# ADR-0008: P0-7a Feature Extraction 体系设计 —— 结构化数值信号层

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-7a / P0-7b）、ADR-0001（只产事实）、ADR-0007（事实事件层 vs 风险语义层）、
  `src/home_perception/analysis/event.py`（P0-6 输出）、ADR-0006（VisitorTrack）

## 背景（Context）

P0-6 完成后，`VisitorEvent` 流已经是干净的"事实事件"。下一步是 P0-7 风险语义层，但 Owner 明确反对
"规则层直接读 event" 模式：

```python
# 反模式：规则层直接读 event，规则变更要回头改 event
if event.duration_seconds > 300 and hour(event.leave_time) < 6:
    emit("high_risk_approach")
```

短期快，长期耦合：阈值变了要改规则、Feature 变了要改 event、新增信号要回头改 event schema。
正确分层是 `event → features → rules`，让 Rule Engine 只消费**结构化数值特征**。

```
VisitorEvent (P0-6 事实事件)
       ↓
Feature Extractor (P0-7a)         ← 本 ADR
  ├── DurationFeature
  ├── VisitFrequencyFeature
  ├── TimeFeature
  └── TrajectoryFeature (占位)
       ↓
RiskFeature (P0-7a 聚合容器)      ← 本 ADR
       ↓
Rule Engine (P0-7b)               ← 后续 ADR
  ├── DwellRule
  ├── RepeatVisitRule
  ├── OddHourRule
  └── ...
       ↓
PerceptionEvent (5 类 + score)    ← ADR-0001 / docs/07_event_schema.md
```

## 决策（Decision）

### 1. Feature 是"被测量的数值"，不是"判断的标签"

继续 Owner P0-6 原则：**领域对象保存测量的数值，不保存系统认为它意味着什么。**

| 字段类型 | 是否允许在 Feature | 例子 |
| --- | --- | --- |
| 数值（int / float） | ✅ 鼓励 | `duration_seconds`、`visits_in_window`、`hour_of_day` |
| 类别（enum / str） | ✅ 可用 | `day_of_week`（0-6）、`weekday_or_weekend`（enum） |
| 派生 bool（如 `is_night`、`is_long_visit`） | ❌ 禁止 | 留给 Rule Engine 算，避免"事实污染" |

> 例外：`enabled: bool` / `source_video: str` / `event_id: str` 等"元数据"类字段允许
> （用于调试 / 追溯，不参与业务判断）。

### 2. Feature 基类 + 4 个具体 Feature

```python
@dataclass
class Feature:
    """基类：单一维度的可测量数值信号。"""
    visitor_id: UUID        # 对应 VisitorEvent.visitor_id
    event_id: str           # 对应 VisitorEvent.event_id
    source_video: str       # 来源视频
    computed_at: datetime   # 提取时刻（UTC）


@dataclass
class DurationFeature(Feature):
    """停留时长（直接取自 VisitorEvent.duration_seconds）。"""
    duration_seconds: float


@dataclass
class VisitFrequencyFeature(Feature):
    """滑动窗口内同 visitor_id 出现次数（含本次）。"""
    visits_in_window: int
    window_seconds: float   # 窗口长度（可配）


@dataclass
class TimeFeature(Feature):
    """时间维度数值信号。"""
    hour_of_day: int        # 0-23
    day_of_week: int        # 0-6 (周一=0)
    is_weekend: bool        # 元数据类别不算判断（基于 day_of_week 派生但本身是事实"周几"）


@dataclass
class TrajectoryFeature(Feature):
    """轨迹模式（MVP 单摄像头无真实轨迹，预留接口位）。"""
    bbox_center_displacement: float = 0.0  # 移动距离（px）；单摄为 0
    segment_count: int = 1                  # 切分段数
```

`is_weekend` 例外解释：周末 / 工作日是**日历事实**（基于日期可严格判定），不是相对阈值判断。
但 `is_long_visit` / `is_suspicious` / `is_odd_hour` 等含"长/可疑/异常"等价值判断的 bool 字段**严禁**
出现在 Feature —— 这些是 Rule Engine 基于 TimeFeature.hour_of_day + VisitFrequencyFeature + 业务阈值算的。

### 3. RiskFeature 聚合容器

`RiskFeature` = 一组 Feature + 触发它的 VisitorEvent 引用，**不含**任何"判断"字段。

```python
@dataclass
class RiskFeature:
    """一组 Feature 的聚合 + 触发源（VisitorEvent），供 Rule Engine 消费。"""
    visitor_id: UUID
    event_id: str
    source_video: str
    computed_at: datetime   # UTC
    duration: DurationFeature | None = None
    frequency: VisitFrequencyFeature | None = None
    time: TimeFeature | None = None
    trajectory: TrajectoryFeature | None = None
```

Rule Engine 拿到 `RiskFeature` 后自行做"duration.duration_seconds > 300?"、"time.hour_of_day in {23, 0, 1, 2, 3, 4}?" 等等判断。
Feature 层**不预设**这些阈值，阈值是 Rule Engine 的责任。

### 4. FeatureExtractor 编排器

```python
class FeatureExtractor:
    """编排器：接收 VisitorEvent 流，输出对应 RiskFeature 流。"""
    def __init__(self, frequency_window_s: float = 1800.0):
        self._frequency_window_s = frequency_window_s
        # 内部维护 visitor_id → 最近 visitor_events 列表（用于 VisitFrequency 滑动窗口）
        self._recent_by_visitor: dict[UUID, list[VisitorEvent]] = {}

    def extract(self, event: VisitorEvent) -> RiskFeature:
        # 调度 4 个具体 Extractor
        duration = DurationFeatureExtractor.extract(event)
        frequency = VisitFrequencyFeatureExtractor.extract(event, self._recent_by_visitor, self._frequency_window_s)
        time_ = TimeFeatureExtractor.extract(event)
        trajectory = TrajectoryFeatureExtractor.extract(event)  # MVP 留空
        return RiskFeature(visitor_id=event.visitor_id, event_id=event.event_id, ...)

    def reset(self):
        """清空滑动窗口（视频源切换 / 多会话）。"""
        self._recent_by_visitor.clear()
```

每个具体 Extractor 是**纯函数**（除 VisitFrequency 需要滑动窗口状态），**职责单一**：
- `DurationFeatureExtractor.extract(event)` → `DurationFeature`（直接抄字段）
- `VisitFrequencyFeatureExtractor.extract(event, history, window_s)` → `VisitFrequencyFeature`（滑动窗口计数）
- `TimeFeatureExtractor.extract(event)` → `TimeFeature`（拆 `leave_time`）
- `TrajectoryFeatureExtractor.extract(event)` → `TrajectoryFeature`（MVP 占位，单摄全 0）

### 5. Trajectory 在 MVP 留空，但接口不能省

MVP 是**单摄像头、单区域**，没有真实轨迹（visitor_id 在单摄下是 1D 时间线）。
但 TrajectoryFeature 仍保留字段定义与 Extractor 占位，原因：
- 避免 P1 多摄时回头改 RiskFeature schema
- 留接口位 = 留扩展点（Feature / RiskFeature 字段增删按 ADR-0005 走 schema_version 评审）

P1 多摄实现时，`TrajectoryFeatureExtractor` 接多摄 bbox 时序，输出跨摄位移 / 速度 / 切分段数。

### 6. 边界测试守"Feature 无判断字段"

照搬 P0-6 的 `test_no_business_judgment_fields` 黑名单策略，扩展 Feature / RiskFeature 的禁用字段集：

```python
FORBIDDEN_FEATURE_FIELDS = {
    "risk_level", "score", "visit_type", "is_suspicious",
    "is_long_visit", "is_odd_hour", "is_repeat",
    "warning", "verdict", "event_type",  # event_type 留 Rule Engine
    # ... 与 P0-6 一致 + Feature 特化
}
```

`test_no_business_judgment_fields` 跑所有 Feature 子类 + RiskFeature 的 `to_dict()`，任何禁用字段越界立即报警。

## 动机（Rationale）

- **事实 → 数值 → 判断** 三层分明：Feature 是"被测量的数值"，Rule Engine 才做"判断"。
  这条原则在 P0-6 ADR-0007 第一次确立，Feature 层继续守住。
- **规则变更不动 event**：阈值变了、规则变了、Feature 字段变了，都是 Rule Engine / Feature 层内部的事。
  VisitorEvent 是稳定的"事实事件契约"（ADR-0005）。
- **可解释**：Feature 都是可测量的数值（`duration=480s` / `visits=3` / `hour=3`），不黑箱；
  Rule Engine 的判断（"是 high_risk"）基于可枚举的 Feature 值，可审查。
- **可扩展**：新增 Feature 不改 event 也不改现有 Feature；新增 Rule 不改 Feature；
  P1 多摄时 TrajectoryFeatureExtractor 直接接数据源。
- **测试简化**：每个 Feature Extractor 纯函数单测；RiskFeature 单测只验证聚合逻辑；
  不需要为每条规则维护 fixture。
- **架构一致性**：DetectionResult / VisitorTrack / VisitorEvent 三个对象属于"事实/状态"层；
  DurationFeature / VisitFrequencyFeature / TimeFeature / TrajectoryFeature / RiskFeature 属于
  "数值信号"层；PerceptionEvent / WarningEvent 属于"决策"层。层与层之间通过稳定接口连接，
  与 ADR-0001/0006/0007 同构。

## 后果（Consequences）

- ✅ Feature 层职责清晰：测量数值，不做判断。
- ✅ Rule Engine 后续可以自由设计（基于规则 / 基于 ML / 基于 LLM 解释），不影响 Feature 层稳定接口。
- ✅ P0-6 VisitorEvent 契约稳定，不被 Feature / Rule 变化波及。
- ✅ CAVIAR 真实链路已能端到端走通（P0-6 验证），Feature 层复用同一 fixture 验证数值提取正确性。
- ⚠️ Team 成员可能误以为"Feature 等同于 Rule 的输入参数"——Feature 不仅是 Rule 输入，
  也是 ML 输入（v2 阶段）、可解释性输出（向家属展示"为什么这事件被标 high_risk"）。
- ⚠️ P1 多摄时 TrajectoryFeatureExtractor 字段会扩展（如 displacement / velocity），
  须走 schema_version 评审（ADR-0005）。
- 📌 约束后续：Feature 字段增删按 ADR-0005 走 schema_version 评审；新增"派生 bool 字段"
  必须经 Owner 评审（避免"事实污染"破口）。

## 替代方案（Alternatives）

- **Feature 直接含 is_long_visit / is_suspicious 等 bool 字段**：否决。等同于把 Rule Engine 的工作
  提前到 Feature 层做，破坏 ADR-0007 的"事实 vs 语义"边界。
- **Feature 改成 dict 不入 dataclass**：否决。结构化 dataclass 字段可枚举、可契约测试（`to_dict` 黑名单）、
  静态类型检查；dict 会让边界测试无法穷举字段。
- **跳过 Feature 层，Rule Engine 直接读 VisitorEvent**：否决。短期快，规则变更要回头改 event，
  违反 ADR-0005 的"契约稳定"。
- **RiskFeature 改为 4 个独立 Feature 不聚合**：否决。聚合容器让 Rule Engine 一次拿全所有相关信号；
  独立 Feature 流会让 Rule Engine 维护"当前 visitor_id 处于什么 feature 状态"的复杂状态机。
  聚合 + 留单字段可空 = 简单且灵活。
- **VisitFrequencyFeature 不维护滑动窗口，按"全部历史"计**：否决。无界滑动窗口会无限增长，
  边缘设备内存受限；按窗口（默认 30 分钟可配）是 O(N) 截断。

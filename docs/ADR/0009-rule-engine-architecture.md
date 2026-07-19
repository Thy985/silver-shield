# ADR-0009: P0-7b Rule Engine 架构 —— 风险语义层与五类规则

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-7b）、`docs/07_event_schema.md`（5 类 PerceptionEvent 终态）、
  `src/home_perception/analysis/{event,event_builder,feature,feature_extractor}.py`（P0-6 / P0-7a）、
  ADR-0001（只产事实）、ADR-0007（事实事件层 vs 风险语义层）、ADR-0008（Feature 数值信号层）

## 背景（Context）

P0-7a 完成 `Feature` 体系后，下一步是 Rule Engine：消费 `RiskFeature`，按业务阈值生成
`PerceptionEvent` + `score`，最终对中心上报 5 类风险标签。

P0-7b 是最容易"破坏边界"的一层 —— 容易把规则逻辑塞回 Feature、把 PerceptionEvent 跳到
WarningEvent、硬编码业务阈值。Owner P0-7a review 明确指出 5 个核心原则：

1. **规则层不要重新污染 Feature 层**（不回头改 Feature schema）
2. **Rule 输出 PerceptionEvent，不直接输出 WarningEvent**（不跳级）
3. **Score 表示规则命中强度，不是诈骗概率**（避免中心误解）
4. **规则组合用 Composite Rule 消费已有 RuleResult**（不复算）
5. **Cooldown 防止重复触发**（30fps 下单次停留可能产生数百条事件）

```
DetectionResult → VisitorTrack → VisitorEvent (P0-6 事实)
                                    ↓
                          Feature Extractor (P0-7a)
                                    ↓
                          RiskFeature (P0-7a 数值特征)
                                    ↓
                          Rule Engine (P0-7b)  ← 本 ADR
                              ├── LongDurationRule   → abnormal_dwell
                              ├── RepeatVisitRule     → repeat_visit
                              ├── OddHourRule         → visit_normal + is_odd_hour
                              ├── PendingVerifyRule   → visit_pending_verify (留接口，未实现)
                              └── HighRiskApproachRule (Composite) → high_risk_approach
                                    ↓
                          CooldownGate (P0-7b)   ← 本 ADR
                                    ↓
                          PerceptionEvent (§7.2 5 类 + score)
                                    ↓
                          WarningEvent (P0-8/P0-9 决策)  ← 后续 ADR
```

## 决策（Decision）

### Decision 1: 规则层消费 Feature，不直接读取 Event

继续 ADR-0007 / ADR-0008 边界：Rule 输入 = `RiskFeature`，**绝不** 直接读 `VisitorEvent`。
- 反模式：`if event.duration_seconds > 300` —— 阈值变了要回头改 event 契约（违反 ADR-0005）
- 正模式：`if feature.duration.duration_seconds > threshold` —— 阈值变了只改 ThresholdConfig

### Decision 2: Rule 输出 PerceptionEvent，不直接输出 WarningEvent

继续分层不跳级（避免把"是否干预"的决策提前到 Rule 层）：
- Rule 输出 `PerceptionEvent`（含 `event_type` / `score` / `is_odd_hour` / `repeat_count` / `evidence`）
- **不**输出 `WarningEvent`（"是否需要报警 / 通知家属"是 P0-8 决策层）
- **不**直接调 MQTT 上报（是 P0-9 传输层）
- **不**直接取证（是 P0-8 取证层）

### Decision 3: score 是规则强度，不是诈骗概率

P0-7b 输出的 `score` 字段命名 + 语义需明确：
- 命名：`perception_score`（P0-7b 内部用）/ `score`（§7.2 对外契约字段）
- 语义：**规则命中强度**（0-1 浮点），不是诈骗概率
- 计算：基于 Rule 命中条件 + Rule 自身权重（每条 Rule 一个权重）
- 例：
  ```
  LongDurationRule matched      → +0.50
  RepeatVisitRule matched        → +0.30
  OddHourRule matched            → +0.10
  → perception_score = 0.90
  ```
  这是"这 3 条规则都触发了"，**不是**"这人是诈骗犯 90%"。

中心侧（AI Understand / Predict 层）会基于 PerceptionEvent 流做综合判断，**不**直接读
`score` 当诈骗概率。docstring 强制说明此边界。

### Decision 4: 规则组合采用 Composite Rule

`HighRiskApproachRule` 不重新算 Feature，而是**消费已有 RuleResult 列表**：
```python
class HighRiskApproachRule(CompositeRule):
    """组合规则：长停留 + 重复 + 异常时段 → high_risk_approach。"""
    required_rule_names = {"LongDurationRule", "RepeatVisitRule", "OddHourRule"}

    def evaluate(self, ctx: RuleContext, prior_results: list[RuleResult]) -> list[RuleResult]:
        matched_names = {r.rule_name for r in prior_results if r.matched}
        if self.required_rule_names.issubset(matched_names):
            return [RuleResult(matched=True, event_type="high_risk_approach", score=self.weight, ...)]
        return []
```

优点：替换为 ML 模型时**不**改 Rule 接口（Composite 仍消费 RuleResult，只是 RuleResult 来源变了）。

### Decision 5: CooldownGate 防止重复触发

30fps 摄像头下，单次停留 480s 可产生 14400 帧，每帧可能触发同 Rule → 数百条重复 PerceptionEvent。
CooldownGate 状态机防刷：

```
State machine per (visitor_id, rule_name):
    INACTIVE
       ↓ (first trigger)
    ACTIVE        ← trigger now, emit PerceptionEvent
       ↓ (within cooldown_seconds of last trigger)
    COOLDOWN      ← suppress trigger
       ↓ (after cooldown_seconds elapsed)
    ACTIVE        ← next trigger allowed
       ↓ (visitor gone for long time)
    INACTIVE      ← reset
```

参数：
- `cooldown_seconds`：默认 600s（10 分钟）
- `reset_gap_seconds`：超过该秒数无该 visitor 事件，状态机重置（默认 1800s = 30 分钟）

**CompositeRule 不走 Cooldown**（它消费的是其他 Rule 的"已冷却"结果，自身不产生重复触发）。

### Decision 6: 阈值配置化，不硬编码

```python
@dataclass
class ThresholdConfig:
    long_duration_seconds: float = 300.0    # LongDurationRule
    repeat_visit_count: int = 3            # RepeatVisitRule
    repeat_visit_window_s: float = 1800.0  # VisitFrequencyFeature 默认窗口
    odd_hour_set: set[int] = field(default_factory=lambda: {23, 0, 1, 2, 3, 4})
    cooldown_seconds: float = 600.0
    reset_gap_seconds: float = 1800.0
    high_risk_required_rules: set[str] = field(default_factory=lambda: {
        "LongDurationRule", "RepeatVisitRule", "OddHourRule"
    })
    rule_weights: dict[str, float] = field(default_factory=lambda: {
        "LongDurationRule": 0.50,
        "RepeatVisitRule": 0.30,
        "OddHourRule": 0.10,
        "HighRiskApproachRule": 0.90,
    })
```

**Rule 不直接 import ThresholdConfig**，而是 RuleEngine 注入。这样：
- 单元测试可构造不同 ThresholdConfig 测边界
- 运行时配置可热更新（v2）
- 默认值是 conservative（"宁可漏报不误报"），业务侧按家庭习惯调

### Decision 7: 5 条 Rule + 1 条 CompositeRule（按 §7.2 5 类 PerceptionEvent 对齐）

| Rule 名称 | 消费 | 输出 event_type | 权重 | 状态 |
| --- | --- | --- | --- | --- |
| `LongDurationRule` | `DurationFeature.duration_seconds` | `abnormal_dwell` | 0.50 | ✅ 实现 |
| `RepeatVisitRule` | `VisitFrequencyFeature.visits_in_window` | `repeat_visit` | 0.30 | ✅ 实现 |
| `OddHourRule` | `TimeFeature.hour_of_day` | `visit_normal`（叠加 `is_odd_hour=true`）| 0.10 | ✅ 实现 |
| `PendingVerifyRule` | `WhitelistProvider`（外部接口）| `visit_pending_verify` | TBD | ⏸ **留接口不实现**（Owner 明确不要硬编码白名单）|
| `HighRiskApproachRule` (Composite) | `RuleResult[]` | `high_risk_approach` | 0.90 | ✅ 实现 |

**PendingVerifyRule 不实现的原因**：
- "非白名单"判断需要 `WhitelistProvider` 抽象（数据库 / 配置文件 / 中心回写）
- P0-7b 第一版无白名单数据源，硬编码 = 假数据 = 误报风险
- 留 `WhitelistProvider` protocol 接口 + `PendingVerifyRule.evaluate()` 抛 `NotImplementedError` 模板
- v2 接入实际白名单数据源时实现该 Rule

## 动机（Rationale）

- **守 ADR-0007 / ADR-0008 边界**：Rule 不回头改 Feature，Feature 不回头改 Event。三层职责分明。
- **可替换**：未来 Rule 可由 ML 模型替换（v2），`RiskFeature` 输入接口稳定，Rule 数量增减不影响其他层。
- **可解释**：每条 PerceptionEvent 都带 `meta.rule_name` 标识"哪条 Rule 触发" + `evidence` 包含输入 Feature 值，
  中心或家属可审计"为什么这事件被标 X"（关键合规需求）。
- **工程鲁棒**：CooldownGate 解决 30fps 重复触发的工程问题，避免下游 MQ 拥塞。
- **可配置**：阈值与权重在 `ThresholdConfig` 集中管理，家庭习惯 / 不同设备可独立调；不硬编码 = 不需改代码。
- **不留尾巴**：PendingVerifyRule 留接口不实现，避免假数据；ADR 写明后续 v2 路径。

## 后果（Consequences）

- ✅ 5 类 PerceptionEvent 严格按 §7.2 输出（`visit_normal` / `visit_pending_verify` / `abnormal_dwell` / `repeat_visit` / `high_risk_approach`）
- ✅ P0-7b 范围清晰：Rule / RuleResult / PerceptionEvent / CooldownGate / RuleEngine 五件套 + 4 条基础 + 1 Composite
- ✅ P0-7a `RiskFeature` 契约稳定，Rule 增减不需改 Feature
- ✅ CooldownGate 解决工程上 30fps 重复触发的核心痛点
- ✅ 阈值与权重可配置（`ThresholdConfig`）
- ⚠️ P0-7b 范围较大（5+1 Rule、Cooldown 状态机、RuleEngine 编排），需充分单元测试
- ⚠️ PendingVerifyRule 留接口不实现 → 第一版 PerceptionEvent **不**会输出 `visit_pending_verify` 类型
  （v2 接入白名单数据源后补全）
- 📌 约束后续：Rule 增删须更新 ADR-0009 与 ThresholdConfig；PerceptionEvent 字段增删按 ADR-0005
  走 schema_version 评审；WhitelistProvider 接口 v2 须新开 ADR 评审。

## 替代方案（Alternatives）

- **Rule 直接读 VisitorEvent**（`if event.duration_seconds > 300`）：否决。短期快，规则变更要回头改 event，违反 ADR-0005 契约稳定 + ADR-0007 边界。
- **Rule 输出 WarningEvent 跳级**：否决。WarningEvent 是 P0-8 决策层，Rule 只该出 PerceptionEvent。
- **CompositeRule 重新算 Feature**：否决。重复计算 + 难以审计（"为什么这事件被标 high_risk" 不可追溯）。
- **CooldownGate 在 Builder 层而非 Rule 层**：否决。Builder 是 P0-6 事实层，不应感知"规则"概念。
- **阈值硬编码在 Rule 类里**：否决。不可配置、不可测试边界、不可家庭个性化。
- **用 ML 模型直接出 PerceptionEvent**：否决。ML 不可解释、不确定性高、与"5 类稳定标签"契约冲突。ML 留 v2。
- **PendingVerifyRule 硬编码"非白名单"= true**：否决。假数据 = 误报 = 误推送家属 = 信任崩盘。留接口不实现。

# ADR-0021: 实时风险流 · 具体设计（Concrete Design for ADR-0018）

- **状态**：Proposed
- **日期**：2026-07-26
- **范围**：v2 / 后 MVP 的**实时风险路径具体接口与模块设计**；把 ADR-0018（实时/历史分流）的"方向"落为可实现的类型 / 模块 / 数据流 / 契约影响。当前 MVP 不实现。
- **决策者**：Owner
- **相关**：ADR-0018（实时/历史分流方向）、ADR-0010（WarningEvent 决策架构）、ADR-0014（三级冻结治理）、ADR-0005（Schema 稳定性）、ADR-0006（track_id 会话级）、ADR-0022（证据链，Phase 2）、ADR-0023（身份，Phase 4）

---

## 1. 背景（基于已核实的现状事实）

当前已冻结的线性管道（历史事件流，`runtime/pipeline.py:285-323` `process_frame`）：

```
DetectionResult
   │  Detector.detect                       (detection/detector.py:88-94)
   ▼
VisitorTrack(track_id, ACTIVE/LEFT)          (detection/schemas.py:22-69)
   │  VisitorTracker.update
   ▼
VisitorEvent          ← 仅 track ACTIVE→LEFT 翻转时生成 (analysis/event_builder.py:125-137)
   │
   ▼
RiskFeature           ← 频率窗口锚定 leave_time (analysis/feature_extractor.py:64-71)
   │
   ▼
PerceptionEvent[]     ← RuleEngine.evaluate (analysis/rule_engine.py:301-326)
   │
   ▼
WarningEvent          ← DecisionPolicy.decide (analysis/decision_policy.py:155-230)
   │
   ▼
ActionCommand[]       ← executor (runtime/pipeline.py:325-349)
```

已核实缺口（ADR-0018）：`WarningEvent` 只能在访客离场后产生（`VisitorEvent` 离场才生成；`RiskFeature` 频率窗口基于 `leave_time`）。访问进行中（蹲守 / 异常停留 / 重复徘徊）无法触发预警。这是**历史完整性 vs 实时性**的张力，不是设计错误。

设计约束（AGENTS.md / ADR-0014）：改造必须 **增量（MINOR）**，不破坏 L1/L2/L3 冻结；新消息 `RiskSignal` 走 ADR-0005 评审。

---

## 2. 目标与非目标

**目标**
- 在访问**进行中**产出实时信号 → 早于离场触发 `WarningEvent` → Action（事中干预）。
- 新增一条**旁路实时路径**，与既有历史事件流并存，不破坏决策层 / 装配入口 / 5 类标签 / 离场语义。

**非目标（本 ADR 不做）**
- 不改写 `VisitorEvent` 离场语义、不改 5 类 `EventType`、不输出"诈骗"判定（模块边界铁律）。
- 不实现音频模态（`RiskSignal.source=audio` 仅预留，检测在 ADR-0022 / Phase 3）。
- 不实现身份解析（见 ADR-0023，Phase 4）；本 ADR 仅透传已有 `visitor_instance_id`。
- 不新增 `DecisionPolicy.decide_realtime`（见 §6 替代方案）。

---

## 3. 决策（具体设计）

### 3.1 三层模型（事实 / 信号 / 决策）

```
事实层（历史，保留）:   VisitorTrack → VisitorEvent（离场生成）
信号层（实时，新增）:   VisitorTrack → BehaviorState → RiskSignal（进行中异常）
决策层（共用，保留）:   → PerceptionEvent(adapter) → WarningEvent → ActionCommand
```

核心判断：**`VisitorEvent` 离场生成没有错，只是不够实时**。历史路径解决"访问完整性"，实时路径解决"事中干预"，二者并存。

### 3.2 `BehaviorState`（NEW，`analysis/behavior_state.py`，**内部运行时态、不对外发布、不进冻结契约**）

每帧从 `VisitorTrack` 计算进行中行为态：

```python
@dataclass
class BehaviorState:
    track_id: int
    visitor_instance_id: str     # 复用 event_builder 分配会话级 UUID（命名见 ADR-0023）
    phase: str                   # "ONGOING" | "LEFT"
    first_seen: float
    last_seen: float
    dwell_seconds: float
    visits_in_window: int        # 含当前进行中的这次访问
    is_odd_hour: bool
    proximity_score: float = 0.0
    def to_dict(self) -> Dict[str, Any]: ...   # 仅内部/演示使用，不入冻结契约
```

> `BehaviorState` 是**运行时内部态**，不入 MQTT / 冻结契约；仅经 `FrameResult` 传给演示层观察。

### 3.3 `RiskSignal`（NEW，`analysis/risk_signal.py`，**抽象信号层，MINOR 增量消息**）— 关键：不在 `RiskSignal` 上枚举具体行为

`RiskSignal` 是**信号层**而非"万能事件"。它描述"某一模态检测到一个异常"，**不**固化成 `ongoing_dwell` / `ongoing_repeat` / `audio_threat` 这类具体行为枚举（否则会膨胀成新的万能 Event）。异常语义由下游 adapter 复用既有冻结标签解释。

```python
class SignalType(str, Enum):
    BEHAVIOR_ANOMALY = "behavior_anomaly"   # 视觉行为异常（停留/重复/接近）
    AUDIO_ANOMALY     = "audio_anomaly"      # 音频异常（预留，Phase 3）
    IDENTITY_ANOMALY  = "identity_anomaly"   # 身份异常（预留，Phase 4）

class Modality(str, Enum):
    VISION   = "vision"
    AUDIO    = "audio"
    IDENTITY = "identity"

@dataclass
class RiskSignal:
    signal_id: str                    # uuid4
    track_id: int
    visitor_instance_id: str
    signal_type: SignalType           # 三类异常，不展开具体行为
    source: Modality                  # 产出该信号的模态
    features: Dict[str, Any]          # 原始异常证据（如 {"dwell_seconds":350} / {"visits_in_window":3}），供 adapter 解释
    severity_hint: Optional[float] = None
    state: str = "RAISED"             # "RAISED" | "CLEARED"
    created_at: datetime = field(default_factory=_utc_now)
    def to_dict(self) -> Dict[str, Any]: ...
```

> 扩展不会污染：`future` 的 `face_expression_abnormal` / `weapon_detected` / `fall_detected` 都收进 `BEHAVIOR_ANOMALY` + `features`，不会新增枚举值、不会新增 `EventType`。

### 3.4 实时评估 + 适配（关键：单一决策中心）

1. `PerceptionPipeline.process_frame` 在 `event_builder.update` 之后，每帧计算 `BehaviorState`（消费当前 `VisitorTrack`）。
2. `RealTimeRiskEvaluator.evaluate(state) -> Optional[RiskSignal]`：当事中阈值触发（`dwell_seconds` 超 `long_duration_seconds` → `BEHAVIOR_ANOMALY`；`visits_in_window` 达 `repeat_visit_count` → 同类型），产出 `RiskSignal(state=RAISED)`；回落产出 `CLEARED`。
3. `risk_signal_to_perception(sig) -> PerceptionEvent`：**adapter 复用冻结的 `PerceptionEvent` + 既有 5 类 `EventType` 标签**，标签选择**复用 RuleEngine 共享的阈值/标签映射**（同一份定义，实时与历史在标签层也汇合），**零新增枚举值**。例如 `features.dwell_seconds > long_duration_seconds → abnormal_dwell`。
4. 适配后的 `PerceptionEvent` 经**现有 `DecisionPolicy.decide()`** 产出 `WarningEvent` → `ActionCommand[]`。**实时与历史在决策层汇合于同一冻结对象**，无第二套决策栈。
5. `FrameResult`（`runtime/pipeline.py:108-117`）追加 `risk_signals: List[RiskSignal]` 与 `behavior_state: Optional[BehaviorState]`；演示 `DemoGateway.run_loop` 广播 `RiskSignal` 与随之产生的 `WarningEvent`（早于离场）。

> **双源去重**：`DecisionPolicy` 消费 `PerceptionEvent` 不区分来源（实时适配 vs 历史 RiskFeature），由 `CooldownGate`（`rule_engine.py`）天然防抖，避免实时/历史重复触发。

### 3.5 冻结契约影响（SemVer 映射，对齐 ADR-0014）

| 改动 | 契约层级 | SemVer | 说明 |
| --- | --- | --- | --- |
| 新增 `RiskSignal` 实时消息 | L1 新对象 | MINOR | 独立消息，不入 5 类 `EventType`；ADR-0005 评审 |
| 新增 `SignalType` / `Modality` 枚举 | L1 枚举 | MINOR | 增量枚举，不触碰 5 类 `EventType` |
| `BehaviorState`（内部态） | 不入契约 | — | 仅 `FrameResult`→演示层，不对外发布 |
| `DecisionPolicy.decide` 签名 | L2 接口 | 不变 | 经 adapter 复用，零签名改动 |
| `PerceptionPipeline.from_settings` 入口 | L3 装配 | 不变 | 仅 `process_frame` 增加分支 |
| 5 类 `EventType` / `VisitorEvent` 离场语义 / `RiskFeature` leave_time 窗口 | L1/L3 | 不变 | 历史事件流完整保留 |

**红线**：不得改 `EventType` 标签、不得新增 `decide_realtime`、不得把音频逻辑混入、不得改 `from_settings` 签名。

### 3.6 分阶段（Phase 1：实时风险 MVP）

Phase 1 只做：`VisitorTrack → BehaviorState → RiskSignal → WarningEvent`。**不做**音频 / ReID / Agent。目标：把"离场报警"升级为"访问过程中报警"。

1. `BehaviorState` 每帧计算（接入 `process_frame`）。
2. `RealTimeRiskEvaluator` + 共享标签映射 adapter（`RiskSignal → PerceptionEvent`）。
3. `DemoGateway.run_loop` 广播 `RiskSignal` / 事中 `WarningEvent`；`DemoAggregateState` 接收 `risk_signals`。
4. contract test：`test_risksignal_contract.py`（字段/SemVer、RAISED/CLEARED 翻转、不依赖离场）。

---

## 4. 动机（Rationale）

- **事中干预**：`RiskSignal` 在访问进行中触发，预警价值从"事后报告"转为"实时干预"，契合产品"诈骗风险前置防控"定位。
- **历史路径零破坏**：`VisitorEvent` 离场语义、下游 Memory/Profile 完整保留，符合"离场事件没有错，只是不够实时"的判断。
- **单一决策中心**：adapter 复用既有 `PerceptionEvent`/`DecisionPolicy`，避免双决策栈（阈值/行动/解释/测试各一套）失控。
- **信号层抽象**：`RiskSignal` 不枚举具体行为，未来模态扩展不污染契约。
- **全部增量**：上表均为 MINOR，不破坏 ADR-0014 冻结边界。

---

## 5. 后果（Consequences）

**正面**
- ✅ 访问进行中即可出 `WarningEvent` → Action（实时干预）。
- ✅ 决策层 / 装配入口 / 5 类标签 / 离场语义全部零破坏。
- ✅ 信号层可扩展（音频/身份异常即插即用）。

**负面 / 约束**
- ⚠️ 新增 2 个对象（`BehaviorState`/`RiskSignal`）+ 2 枚举 + 评估器/适配器，组件数上升。
- ⚠️ 实时/历史双路径需在 `CooldownGate` 协同防抖（已具备）。
- ⚠️ 音频/身份异常为预留（`source`/`features` 预留，检测在 Phase 3/4）。

---

## 6. 替代方案（Alternatives）

- **实时路径直接改 `VisitorEvent` 在场触发**：否决。破坏离场语义 + 下游期望完整生命周期（ADR-0005/0010）。
- **实时路径新增 `DecisionPolicy.decide_realtime`**：否决。`decide()` 签名冻结（L2）；双决策栈导致阈值/行动/解释/测试分裂。adapter 复用更省且合规。
- **`RiskSignal.behavior_type` 枚举 `ongoing_dwell/ongoing_repeat/audio_threat`**：否决。会膨胀成新的万能 Event，未来扩展不断加枚举。改为抽象 `signal_type`(3 类) + `features`(dict)。

---

## 7. 与既有 ADR 的关系

- **ADR-0018**：本 ADR 是其**具体实现**——`BehaviorState`/`RiskSignal`/`RealTimeRiskEvaluator`/adapter 把"实时/历史分流"落为接口与数据流。
- **ADR-0010**：`WarningEvent` 仍是唯一决策对象，实时/历史均经 `PerceptionEvent` 汇入。
- **ADR-0014**：全部改动映射为 MINOR（§3.5），不破坏三级冻结；`RiskSignal` 走 ADR-0005 评审。
- **ADR-0022 / ADR-0023**：本 ADR 的实时信号可携带 `source=audio`（ADR-0022 Phase 3）与 `source=identity`（ADR-0023 Phase 4）的异常，接口已预留，不在本 ADR 实现。

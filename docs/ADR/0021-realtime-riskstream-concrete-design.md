# ADR-0021: 实时风险状态流与信号生成层 · 具体设计（Concrete Design for ADR-0018）

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
- 不实现音频模态（`RiskSignal.source=AUDIO` 仅预留，检测在 ADR-0022 / Phase 3）。
- 不实现身份解析（见 ADR-0023，Phase 4）；本 ADR 仅透传已有 `visitor_instance_id`。
- 不新增 `DecisionPolicy.decide_realtime`（见 §6 替代方案）。

---

## 3. 决策（具体设计）

### 3.1 分层模型（Reality → State → Signal → Decision）

不要把它理解为"历史路径 vs 实时路径"两条平行支线。更准确的模型是：**同一条现实流(Reality Stream)先形成实时状态，历史事件是实时状态的一次投影(projection)**——现实世界里先发生"人在门口→停留→徘徊→离开"，"离场事件/访问记录"是这段过程结束后的投影，而不是先有事件再倒推过程。当前 MVP 工程上做了简化(track→leave→event→feature→risk)，本 ADR 在其旁边补回**实时状态流**。

```
                    Reality Stream（现实流）
                          │
                     VisitorTrack           ← Reality Layer（每帧事实）
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
  实时状态流（新增）                     历史投影流（保留）
        │                                   │
  BehaviorState        ← State Layer    VisitorEvent（离场投影）
        │                                   │
  RiskSignal           ← Signal Layer   RiskFeature（历史窗口统计）
        │                                   │
        └─────────────────┬─────────────────┘
                          ▼
              PerceptionEvent(adapter)       ← Decision Layer（共用）
                          ▼
              WarningEvent → ActionCommand
                （未来 → Memory / Agent，见 §7.1）
```

四层语义：
- **Reality Layer**：`VisitorTrack`，每帧的现实事实，两条流共同的源头。
- **State Layer**：`BehaviorState`，"当前正在发生什么"的实时工作状态（未来 Agent/Memory 的短期状态输入，见 §3.2、§7.1）。
- **Signal Layer**：`RiskSignal`（实时）/ `RiskFeature`（历史）——两者都是**信号生成器的产物**，不是决策。
- **Decision Layer**：`PerceptionEvent → WarningEvent`，实时与历史唯一汇合的决策中心。

核心判断：**`VisitorEvent` 离场生成没有错，它是现实流的历史投影；只是投影不够实时，需要在其旁补回实时状态流**。历史投影解决"访问完整性"，实时状态解决"事中干预",二者同源而非对立。

### 3.2 `BehaviorState`（NEW，`analysis/behavior_state.py`，**实时感知域的工作状态 / working state**）

`BehaviorState` 不是一个普通的中间计算变量，而是**实时感知域的工作状态(working state)**——它回答"此刻门口正在发生什么"，是未来 Agent 理解现况、Memory 系统沉淀短期记忆的天然输入。**当前**它不作为外部消息契约发布(不入 MQTT / 冻结契约)，但其定位应按"短期状态"来设计，避免未来 Memory ADR 推翻重做。

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
    schema_version: int = 1      # 内部态演进标记：未来加 trajectory_pattern/distance_to_door/body_orientation 时递增，便于 AI 系统长期演进与序列化兼容
    def to_dict(self) -> Dict[str, Any]: ...   # 仅内部/演示使用，不入冻结契约
```

> `BehaviorState` 是**实时感知域的工作状态(working state)**，当前不作为外部消息契约发布（不入 MQTT / 冻结契约），仅经 `FrameResult` 传给演示层观察；但**未来可作为 Memory 系统的短期状态输入**（§7.1）。
> `schema_version` 虽非契约字段（不受 ADR-0014 约束），但保留它使未来扩展行为特征（轨迹 / 门距 / 朝向）时演示层与序列化可平滑兼容。

### 3.3 `RiskSignal`（NEW，`analysis/risk_signal.py`，**抽象信号层，MINOR 增量消息**）— 关键：不在 `RiskSignal` 上枚举具体行为

`RiskSignal` 是**信号层**而非"万能事件"。它描述"某一来源检测到一个异常迹象"，**不**固化成 `ongoing_dwell` / `ongoing_repeat` / `audio_threat` 这类具体行为枚举（否则会膨胀成新的万能 Event）。异常语义由下游 adapter 复用既有冻结标签解释。

**语义定位：`RiskSignal` 是「瞬时信号」，是状态机的一次「状态跃迁(transition)」，不是「长期状态」。** `RAISED` 与 `CLEARED` 不是同一个对象的生命周期两端，而是**两条独立发射的消息**（各有自己的 `signal_id` / `created_at`），分别描述"此刻升起了一个异常"和"此刻解除了这个异常"。因此字段用 `transition`（跃迁类型）而非 `state`（状态）——**持续态(ACTIVE_RISK)由 `RealTimeRiskEvaluator` 的内部状态机持有**（见 §3.3.1），下游只依据 `RAISED→CLEARED` 两次跃迁推导展示态，切忌把 `RiskSignal` 当成常驻对象缓存。

**`category`（异常类别）与 `source`（物理来源）正交**——两者是不同维度，不应混在一个枚举里：`category` 回答"这是哪一类异常"，`source` 回答"由哪个模态检测到"。同一类异常可由不同模态产生（如 `IDENTITY` 既可来自 `VISION` 人脸，也可来自 `AUDIO` 声纹）。

```python
class SignalCategory(str, Enum):
    BEHAVIOR     = "behavior"       # 行为异常（停留/重复/接近）
    CONVERSATION = "conversation"   # 对话/语音异常（预留，Phase 3）
    IDENTITY     = "identity"       # 身份异常（预留，Phase 4）
    ENVIRONMENT  = "environment"    # 环境异常（预留）

class Modality(str, Enum):
    VISION = "vision"
    AUDIO  = "audio"
    SENSOR = "sensor"

class SignalTransition(str, Enum):
    RAISED  = "raised"    # 异常升起的一次跃迁
    CLEARED = "cleared"   # 异常解除的一次跃迁

@dataclass
class RiskSignal:
    signal_id: str                    # uuid4，本次发射的唯一标识（瞬时，非会话常驻）
    track_id: int
    visitor_instance_id: str
    category: SignalCategory          # 异常类别（与来源正交，不展开具体行为）
    source: Modality                  # 产出该信号的物理来源
    transition: SignalTransition      # 本次发射是升起还是解除（非长期状态；持续态在评估器状态机）
    features: Dict[str, Any]          # 原始异常证据（如 {"dwell_seconds":350} / {"visits_in_window":3}），供 adapter 解释
    severity_hint: Optional[float] = None
    created_at: datetime = field(default_factory=_utc_now)
    def to_dict(self) -> Dict[str, Any]: ...
```

正交组合示例：

| 场景 | `source` | `category` |
| --- | --- | --- |
| 视觉发现异常停留/重复 | `VISION` | `BEHAVIOR` |
| 语音威胁 / 诱导话术（Phase 3） | `AUDIO` | `CONVERSATION` |
| 人脸判为陌生身份（Phase 4） | `VISION` | `IDENTITY` |
| 声纹判为陌生身份（未来） | `AUDIO` | `IDENTITY` |

> 扩展不会污染：`future` 的 `face_expression_abnormal` / `weapon_detected` / `fall_detected` 都收进 `category=BEHAVIOR` + `features`，不会新增枚举值、不会新增 `EventType`。

### 3.3.1 评估器状态机（持续态归评估器，`RiskSignal` 只发跃迁）

`RiskSignal` 是瞬时跃迁，"某 track 当前是否处于异常"这个**持续态(`ACTIVE_RISK`)由 `RealTimeRiskEvaluator` 内部持有**，它是一台**按 `track_id` 维护的状态机**。这是必须提前钉死的一环——否则会重演之前 Demo 的"已确认状态混乱 / 风险卡一直红"问题（同类病因：只有进入、没有退出）。

```
每个 track_id 一台状态机（持续态在机内，RiskSignal 只在跃迁边上发射）：

   NONE ──trigger（阈值达成）──▶ ACTIVE_RISK ──recover（回落/离场）──▶ NONE
                │  emit                          │  emit
                ▼                                ▼
      RiskSignal(transition=RAISED)   RiskSignal(transition=CLEARED)
```

- **RAISED 跃迁**：`evaluate(state)` 检测到阈值达成且该 track 当前为 `NONE` → 转入 `ACTIVE_RISK` 并发射 `RiskSignal(transition=RAISED)`。已在 `ACTIVE_RISK` 则不重复发（去抖交给 `CooldownGate`）。
- **CLEARED 跃迁（明确职责）**：**由 `RealTimeRiskEvaluator` 负责，不是离场事件**。触发条件二选一：① 异常特征回落到阈值以下（如 `dwell_seconds` 因人离开门口区域而停止增长 / 徘徊结束）；② track 从 `ACTIVE→LEFT`（`BehaviorState.phase="LEFT"`）。满足任一且该 track 处于 `ACTIVE_RISK` → 回到 `NONE` 并发射 `RiskSignal(transition=CLEARED)`。
- **展示语义**：演示层 / `DemoAggregateState` 依据 `RAISED→CLEARED` 两次跃迁驱动风险卡亮/灭，绝不把单个 `RiskSignal` 当常驻实体缓存，从根上杜绝"卡片不熄灭"。

> 持续态 `ACTIVE_RISK` 是内部运行时态（随 `RealTimeRiskEvaluator` 存活），不入冻结契约；`RiskSignal.transition` 的两个跃迁值属于契约字段（§3.3），需 contract test 覆盖 `RAISED→CLEARED` 成对完整性（有升必有解）。

### 3.4 实时评估 + 适配（关键：单一决策中心）

1. `PerceptionPipeline.process_frame` 在 `event_builder.update` 之后，每帧计算 `BehaviorState`（消费当前 `VisitorTrack`）。
2. `RealTimeRiskEvaluator.evaluate(state) -> Optional[RiskSignal]`：作为 §3.3.1 的状态机——当事中阈值触发（`dwell_seconds` 超 `long_duration_seconds`；`visits_in_window` 达 `repeat_visit_count`）且该 track 为 `NONE`，转入 `ACTIVE_RISK` 并产出 `RiskSignal(category=BEHAVIOR, source=VISION, transition=RAISED)`；回落 / 离场产出对应 `transition=CLEARED`。
3. `risk_signal_to_perception(sig) -> PerceptionEvent`：**adapter 复用冻结的 `PerceptionEvent` + 既有 5 类 `EventType` 标签**，标签选择**复用 RuleEngine 共享的阈值/标签映射**（同一份定义，实时与历史在标签层也汇合），**零新增枚举值**。例如 `features.dwell_seconds > long_duration_seconds → abnormal_dwell`。`transition=CLEARED` 信号不适配为 `PerceptionEvent`（不触发新 Warning），仅用于驱动展示态退出。
4. 适配后的 `PerceptionEvent` 经**现有 `DecisionPolicy.decide()`** 产出 `WarningEvent` → `ActionCommand[]`。**实时与历史在决策层汇合于同一冻结对象**，无第二套决策栈。
5. `FrameResult`（`runtime/pipeline.py:108-117`）追加 `risk_signals: List[RiskSignal]` 与 `behavior_state: Optional[BehaviorState]`；演示 `DemoGateway.run_loop` 广播 `RiskSignal` 与随之产生的 `WarningEvent`（早于离场）。

> **双源去重**：`DecisionPolicy` 消费 `PerceptionEvent` 不区分来源（实时适配 vs 历史 RiskFeature），由 `CooldownGate`（`rule_engine.py`）天然防抖，避免实时/历史重复触发。

### 3.5 冻结契约影响（SemVer 映射，对齐 ADR-0014）

| 改动 | 契约层级 | SemVer | 说明 |
| --- | --- | --- | --- |
| 新增 `RiskSignal` 实时消息 | L1 新对象 | MINOR | 独立消息，不入 5 类 `EventType`；ADR-0005 评审 |
| 新增 `SignalCategory` / `Modality` / `SignalTransition` 枚举 | L1 枚举 | MINOR | 正交增量枚举（类别×来源×跃迁），不触碰 5 类 `EventType` |
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
4. contract test：`test_risksignal_contract.py`（字段/SemVer、`category`×`source` 正交、`transition` 成对性即 `RAISED→CLEARED` 有升必有解、`CLEARED` 由回落/离场触发而非遗漏、不依赖离场即可 `RAISED`）。

---

## 4. 动机（Rationale）

- **事中干预**：`RiskSignal` 在访问进行中触发，预警价值从"事后报告"转为"实时干预"，契合产品"诈骗风险前置防控"定位。
- **历史路径零破坏**：`VisitorEvent` 离场语义、下游 Memory/Profile 完整保留，符合"离场事件没有错，只是不够实时"的判断。
- **单一决策中心**：adapter 复用既有 `PerceptionEvent`/`DecisionPolicy`，避免双决策栈（阈值/行动/解释/测试各一套）失控。
- **信号层抽象 + 正交**：`RiskSignal` 不枚举具体行为；`category`（异常类别）与 `source`（物理来源）正交，未来模态扩展（音频/传感器）不污染契约、不制造混合枚举。
- **状态机先行**：`RiskSignal` 定位为瞬时跃迁，持续态 `ACTIVE_RISK` 收敛到 `RealTimeRiskEvaluator` 的 `NONE→ACTIVE_RISK→NONE` 状态机，提前钉死 `CLEARED` 归属，规避"风险卡一直红"这类展示态失控。
- **为状态连续性与 Memory 铺路**：把系统从"事件驱动"抬升为"Reality→State→Signal→Decision"分层（§3.1），`BehaviorState`（短期）/`VisitorEvent`（长期）/`RiskSignal`（Agent 观测）各有长期归宿（§7.1），让未来 Agent/Memory 沿用而非重设——这是本 ADR 比"增加检测能力"更根本的价值。
- **全部增量**：上表均为 MINOR，不破坏 ADR-0014 冻结边界。

---

## 5. 后果（Consequences）

**正面**
- ✅ 访问进行中即可出 `WarningEvent` → Action（实时干预）。
- ✅ 决策层 / 装配入口 / 5 类标签 / 离场语义全部零破坏。
- ✅ 信号层可扩展（音频/身份异常即插即用）。

**负面 / 约束**
- ⚠️ 新增 2 个对象（`BehaviorState`/`RiskSignal`）+ 3 枚举（`SignalCategory`/`Modality`/`SignalTransition`）+ 评估器/适配器，组件数上升。
- ⚠️ 实时/历史双路径需在 `CooldownGate` 协同防抖（已具备）。
- ⚠️ 音频/身份异常为预留（`source`/`features` 预留，检测在 Phase 3/4）。

---

## 6. 替代方案（Alternatives）

- **实时风险直接进入现有 `RuleEngine`（`VisitorTrack → RuleEngine → PerceptionEvent`）**：否决。这里体现本 ADR 最重要的架构原则——**信号生成 ≠ 规则判断 / 状态生成属于不同职责，历史特征与实时状态属于不同时间语义**。`RuleEngine` 的职责是"信号消费后的规则解释"，不是"状态生成"；它当前消费的是 `RiskFeature`——**历史窗口统计**（频率锚定 `leave_time`），而实时输入是 `BehaviorState`——**进行中的瞬时态**。若强行合并，`RuleEngine` 将同时背负「状态/特征生成 + 历史判断 + 实时判断」多重职责，违反单一职责，且历史/实时的阈值与窗口逻辑纠缠后无人敢改。正确分层：`FeatureExtractor`（历史特征）与 `RealTimeRiskEvaluator`（实时信号）**都是 Signal Generator，不是 Decision Maker**，各自产出信号；`RuleEngine`/`DecisionPolicy` 是**统一的 Decision Layer**。未来当来源扩展到音频 / 门磁 / 家属反馈 / 社区数据时，这个"多信号生成器 → 统一决策"的结构让 Agent 能追溯"每个判断由哪个来源产生"（见 §7.1）。产出解耦、决策统一，兼得。
- **实时路径直接改 `VisitorEvent` 在场触发**：否决。破坏离场语义 + 下游期望完整生命周期（ADR-0005/0010）。
- **实时路径新增 `DecisionPolicy.decide_realtime`**：否决。`decide()` 签名冻结（L2）；双决策栈导致阈值/行动/解释/测试分裂。adapter 复用更省且合规。
- **`RiskSignal.behavior_type` 枚举 `ongoing_dwell/ongoing_repeat/audio_threat`**：否决。会膨胀成新的万能 Event，未来扩展不断加枚举。改为正交的 `category`(4 类抽象异常) + `source`(3 类模态) + `features`(dict)。

---

## 7. 与既有 ADR 的关系

- **ADR-0018**：本 ADR 是其**具体实现**——`BehaviorState`/`RiskSignal`/`RealTimeRiskEvaluator`/adapter 把"实时/历史分流"落为接口与数据流。
- **ADR-0010**：`WarningEvent` 仍是唯一决策对象，实时/历史均经 `PerceptionEvent` 汇入。
- **ADR-0014**：全部改动映射为 MINOR（§3.5），不破坏三级冻结；`RiskSignal` 走 ADR-0005 评审。
- **ADR-0022 / ADR-0023**：本 ADR 的实时信号接口已为后续模态预留——音频异常（`source=AUDIO, category=CONVERSATION`，ADR-0022 Phase 3）与身份异常（`category=IDENTITY`，来源可为 `VISION` 人脸或 `AUDIO` 声纹，ADR-0023 Phase 4）均可即插即用，不在本 ADR 实现。

### 7.1 面向未来的演化定位（Memory / Agent 铺路，非本 ADR 实现）

本 ADR 表面是"加实时报警"，实质是一次架构范式迁移：从 **Event-driven system** 迁向 **State + Signal + Decision（未来 + Memory）system**。其价值不止于 `RiskSignal`，而在于确立一个更根本的判断：

> **事件是过去发生的事实，状态是当前正在发生的现实；Agent 真正需要的是状态连续性。**

本 ADR 引入的对象因此各有其长期归宿，后续 Memory / Agent ADR 应**沿用而非重设**：

| 本 ADR 对象 | 时间语义 | 未来演化归宿 |
| --- | --- | --- |
| `BehaviorState` | 当前正在发生 | **Short-term / Working Memory**（Agent"现在发生什么"的输入） |
| `VisitorEvent` | 过去已完成 | **Long-term Episodic Memory**（历史访问档案） |
| `RiskFeature` | 过去的统计 | Long-term Memory 的模式/频率特征 |
| `RiskSignal` | 此刻的跃迁 | **Agent Observation Input**（可观测的风险迹象输入） |

未来 Memory 不应设计成 `Memory → VisitorEvent` 的单层，而应是：`Reality Stream → Short-term State → Event Projection → Long-term Memory → Agent Reasoning`。届时 Agent 回答"为什么今天风险高"= 当前状态异常(`BehaviorState`) + 历史模式匹配(long-term memory) + 家庭/社区规则。

**"实时 350 秒算不算历史？" —— 不算。** 区分实时与历史的**不是时间长度，而是时间语义**：实时状态回答"当前生命周期内正在累计什么"（如本次访问已停留 350 秒 → `ONGOING_DWELL`），历史统计回答"跨多次访问已经发生过什么"（如 30 天来访 8 次、傍晚居多、pattern_score=0.91）。350 秒是**当前生命周期内的累计量**，属实时状态，不因数值大而变成历史。

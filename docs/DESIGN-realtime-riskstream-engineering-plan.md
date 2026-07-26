# 实时风险状态流工程落地方案

> **Engineering Implementation Plan for ADR-0021（实时风险状态流与信号生成层）**
>
> - 状态：Draft（随 ADR-0021 Proposed 评审同步演进）
> - 日期：2026-07-26
> - 关联：ADR-0018（方向）/ ADR-0021（具体设计）/ ADR-0014（冻结契约）/ `docs/08_roadmap.md` §8.4 Phase 1
> - 定位：**比 ADR 更工程化**。ADR 回答"为什么、边界在哪"；本文回答"改哪个文件、每帧执行顺序、状态存哪、怎么测、怎么灰度"。
> - 事实基线：`src/home_perception/runtime/pipeline.py`（P0-10 装配）、**289 测试全绿**（`v0.1.0-mvp-rc` 后基线）、CI = ruff + torch-free 合约测试（每 PR）+ 全栈 runtime（仅 main）。

---

## 1. 当前架构与目标架构差异

### 1.1 当前（真实代码，非理想化伪代码）

`PerceptionPipeline.process_frame`（`runtime/pipeline.py:285`）的真实顺序：

```python
def process_frame(self, frame, frame_index=0) -> FrameResult:
    result = self.detector.detect(frame)          # YOLO + ByteTrack（track 内嵌在 detector）
    dets   = result.detections

    events = self.event_builder.update(dets)      # 注意：tracker 内嵌其中被驱动，
                                                  # 只有"离场"才产出 VisitorEvent
    for ev in events:                             # 历史路径：逐 VisitorEvent 驱动
        percs, warnings, cmds = self._act_on_event(ev)
        # _act_on_event = feature_extractor.extract(ev)
        #               → rule_engine.evaluate(risk)
        #               → decision_engine.evaluate(percs)   # 返回 0/1 个 WarningEvent
        #               → executor.execute(w)
    return FrameResult(...)
```

**与 ADR 伪代码的 3 个关键差异（落地必须对齐真实代码，不得照抄理想图）**：

| # | 理想伪代码 | 真实代码 | 落地含义 |
| --- | --- | --- | --- |
| 1 | `tracks = tracker.update(detection)` 独立一步 | tracker 被 `event_builder.update(dets)` 内部驱动 | 实时路径**不重复驱动 tracker**，只读 `self.tracker.active()`（当前在场 `VisitorTrack` 列表） |
| 2 | `perceptions = []` 两路合并后批量决策 | 历史路径**逐事件**决策（`_act_on_event`），`DecisionEngine.evaluate` 返回单个 warning | 实时路径新增**平行步骤** `_act_on_signals`，不重构既有逐事件循环（0 行为变化） |
| 3 | 隐含墙钟 | 全链路 `now_provider` 注入（DemoClock 模拟时间） | `BehaviorBuilder` / `RealTimeRiskEvaluator` **必须**接同一 `now_provider`，否则 dwell 计算在 Demo 下失真（帧间毫秒级） |

### 1.2 目标（Phase 1 完成后）

```python
def process_frame(self, frame, frame_index=0) -> FrameResult:
    result = self.detector.detect(frame)
    dets   = result.detections

    # —— 历史投影路径（原样保留，0 行为变化）——
    events = self.event_builder.update(dets)
    for ev in events:
        ... = self._act_on_event(ev)

    # —— 实时状态路径（新增，feature flag 控制）——
    behavior_states: List[BehaviorState] = []
    risk_signals:    List[RiskSignal]    = []
    if self._realtime_enabled:
        now = self._now()                                        # 同一 now_provider
        behavior_states = self.behavior_builder.build(self.tracker.active(), now)
        risk_signals    = self.realtime_evaluator.evaluate(behavior_states, now)
        rt_percs = [risk_signal_to_perception(s) for s in risk_signals
                    if s.transition is SignalTransition.RAISED]   # CLEARED 不产 Warning
        if rt_percs:
            ... = self._act_on_perceptions(rt_percs)              # 汇入同一 DecisionEngine

    return FrameResult(..., behavior_states=behavior_states, risk_signals=risk_signals)
```

差异总结：**历史路径一行不改；实时路径是旁路（bypass），由 flag 控制，关闭时行为与今天完全一致。**

---

## 2. 模块新增清单

### 2.1 新增文件

```
src/home_perception/analysis/
├── behavior_state.py            # BehaviorState 数据类（working state，含 schema_version）
├── risk_signal.py               # RiskSignal + SignalCategory/SourceModality/SignalTransition 枚举
├── behavior_builder.py          # BehaviorBuilder：VisitorTrack → BehaviorState（纯函数式，无状态）
├── realtime_risk_evaluator.py   # RealTimeRiskEvaluator：状态机 + 实时判断（有状态）
└── signal_adapter.py            # risk_signal_to_perception：RiskSignal → 既有 PerceptionEvent
```

### 2.2 职责表

| 模块 | 职责 | 有无内部状态 |
| --- | --- | --- |
| `BehaviorState` | 描述当前访问生命周期状态（dwell / visits / odd_hour） | 无（每帧重算的快照） |
| `BehaviorBuilder` | 从 `tracker.active()` + `now` 计算 `BehaviorState` 列表 | 仅进行中访问计数账本（见 §3.3） |
| `RiskSignal` | 描述风险状态**跃迁**（RAISED / CLEARED 两种发射） | 无（瞬时消息） |
| `RealTimeRiskEvaluator` | 每 track 一台 `NONE→ACTIVE_RISK→NONE` 状态机 + 阈值判断 | **有**（`active_states` 字典，见 §4） |
| `signal_adapter` | RAISED 信号 → 冻结 `PerceptionEvent`（复用 5 类 `EventType`，零新增枚举） | 无 |

### 2.3 修改文件（最小侵入）

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `runtime/pipeline.py` | `FrameResult` +2 可选字段；`process_frame` 加旁路块；`from_settings` 装配两个新组件 | 历史路径代码块不动 |
| `core/config.py` | `Settings` 增 `realtime_risk` 配置段（pydantic） | 默认 `enabled=false` |
| `config/default.yaml` | 增 `realtime_risk:` 段 | 见 §5 |
| `runtime/config.py` | `build_threshold_config` 产物**同时**喂给 RuleEngine 与 Evaluator | 单一阈值来源（见 §5） |

**不修改**：`event_builder.py` / `feature_extractor.py` / `rule_engine.py` / `decision_engine.py` / `decision_policy.py` / `dispatcher.py` —— 一行不改。

---

## 3. 数据流详细设计

### 3.1 每帧执行顺序（完整版）

```
帧 i 进入
  │
  ├─ 1. detector.detect(frame)            → DetectionResult（含 track_id）
  │
  ├─ 2. event_builder.update(dets)        → List[VisitorEvent]（离场才产出；内部驱动 tracker）
  │
  ├─ 3. for ev in events:  ← 历史投影路径（原样）
  │       feature_extractor.extract(ev)   → RiskFeature（leave_time 窗口统计）
  │       rule_engine.evaluate(risk)      → List[PerceptionEvent]
  │       decision_engine.evaluate(percs) → Optional[WarningEvent]
  │       executor.execute(w)             → List[ActionCommand]
  │
  ├─ 4. if realtime_enabled:  ← 实时状态路径（新增旁路）
  │       now = now_provider()                          # 与 tracker 同源时钟
  │       states  = behavior_builder.build(tracker.active(), now)
  │       signals = realtime_evaluator.evaluate(states, now)
  │       for s in signals if s.transition == RAISED:
  │           perc = risk_signal_to_perception(s)       # 复用冻结 PerceptionEvent
  │           decision_engine.evaluate([perc])          # 同一决策中心
  │           executor.execute(w)                       # 同一行动执行器
  │       # CLEARED 信号不进决策，仅随 FrameResult 供展示层熄灭风险卡
  │
  └─ 5. return FrameResult(+behavior_states, +risk_signals)
```

### 3.2 时序源约束（Demo 可复现的命门）

- `BehaviorBuilder` / `RealTimeRiskEvaluator` **禁止**调用 `datetime.now()`，必须消费传入的 `now`（来自 `now_provider`，即 DemoClock 或墙钟）。
- 原因：Demo 帧间真实间隔是毫秒级，模拟时间由 `DemoClock.tick()` 推进；不接同源时钟则 `dwell_seconds` 永远 ≈0，实时路径在 Demo 里永不触发（与 E2E 验证发现的 `fps_target` 坑同族）。
- `dwell_seconds = (now - track.enter_time).total_seconds()`，来源与 tracker 判离场的时基一致。

### 3.3 实时 `visits_in_window` 的账本问题

历史 `repeat_visit` 基于 `leave_time` 窗口（`feature_extractor.py`，`frequency_window_s=1800`）——**进行中的访问尚无 leave_time，不在历史账本里**。实时路径需要独立账本：

- `BehaviorBuilder` 内部维护 `Dict[track_key, List[enter_time]]`（仅进入时间，滑窗清理过期项）；
- `visits_in_window = 窗口内该访客 enter 次数（含当前进行中这次）`；
- v1 的 `track_key` = `visitor_instance_id`（会话级，ADR-0023 边界内），**不做跨会话 ReID**；
- 该账本是 volatile working state，进程重启即失（可接受：与 tracker 同生命周期）。

---

## 4. 状态机实现规范

### 4.1 生命周期存储位置

```python
class RealTimeRiskEvaluator:
    """每 track 一台状态机；有状态组件，随 pipeline 存活。"""

    def __init__(self, thresholds: ThresholdConfig, now_provider=None):
        self._active: Dict[int, _TrackRiskState] = {}   # track_id → 状态机记录

@dataclass
class _TrackRiskState:          # 私有，不出模块
    phase: str                  # "NONE" | "ACTIVE_RISK"
    raised_signal_id: str       # 配对：CLEARED 时回填 features["paired_signal_id"]
    raised_at: datetime
```

### 4.2 生命周期（含创建与删除）

```
track_id 首次出现在 states
        ↓
   创建 _TrackRiskState(phase=NONE)
        ↓
   每帧 evaluate(state)
        ↓ trigger（阈值达成 且 phase==NONE）
   emit RiskSignal(transition=RAISED)；phase=ACTIVE_RISK
        ↓ recover（特征回落 或 state.phase=="LEFT"）
   emit RiskSignal(transition=CLEARED)；phase=NONE
        ↓ track 离场且已 CLEARED
   从 self._active 中 **delete**（防泄漏：长时运行不积累已离场 track）
```

**硬性规则**：

1. `phase==ACTIVE_RISK` 时不重复 emit RAISED（去抖第一层；跨 Warning 的节流仍由既有 `CooldownGate` 负责，两层职责不同）；
2. 每个 RAISED **必须**有配对 CLEARED（离场兜底保证）——契约测试断言成对性；
3. 状态机字典只增不删会泄漏 → 离场 + CLEARED 后必须删除条目；
4. 进程重启后状态机清零：不产生虚假 CLEARED（丢失的 RAISED 由展示层超时兜底，Demo 可接受）。

---

## 5. 阈值管理

### 5.1 原则：单一阈值来源，禁止写死

**禁止**：`if dwell_seconds > 300:`（写死后 Agent/运维调参困难，且与历史路径阈值漂移）。

**Phase 1 阈值全部复用既有 `rule` 段**（不新增数值，实时与历史在阈值层就汇合）：

```yaml
# config/default.yaml（现状，实时路径直接消费）
rule:
  long_duration_seconds: 1.5    # Demo 调优值；生产 300s —— 实时 dwell 阈值同源
  repeat_visit_count: 3         # 实时 visits_in_window 阈值同源
  frequency_window_s: 1800.0    # 实时账本滑窗同源
  odd_hour_set: [23, 0, 1, 2, 3, 4]

# 新增段：只放开关与实时特有项，不复制阈值
realtime_risk:
  enabled: false                # Feature Flag（§10），默认关闭
  eval_interval_frames: 1       # 每 N 帧评估一次（性能旋钮，边缘 CPU 可调大）
```

### 5.2 装配路径

`Settings.rule → build_threshold_config() → ThresholdConfig` 现在喂 RuleEngine；同一实例**再喂** `RealTimeRiskEvaluator`。两条路径读同一对象——改一处 YAML，历史与实时同时生效。`ScenarioConfig.rule_overrides`（P0-11.5a 机制）自动同时覆盖两路。

---

## 6. 与 RuleEngine 的边界

**架构原则（ADR-0021 §6）：信号生成 ≠ 规则解释。**

```
禁止（把状态生成塞进 RuleEngine）:            允许（RuleEngine 只做解释）:

RuleEngine:                                   FeatureExtractor ──→ RiskFeature ──┐
    calculate_dwell()        ← ❌                                                ├─→ 解释 → PerceptionEvent
    calculate_repeat()       ← ❌             RealTimeRiskEvaluator → RiskSignal ─┘
    detect_identity()        ← ❌                     （经 signal_adapter 翻译）
```

工程检查清单（PR review 时逐条核对）：

- [ ] `rule_engine.py` diff 为空（本方案不改它一行）；
- [ ] `signal_adapter.py` 不 import `rule_engine`（只 import 冻结的 `PerceptionEvent` + `ThresholdConfig`）；
- [ ] `RealTimeRiskEvaluator` 不产出 `PerceptionEvent` / `WarningEvent`（只产 `RiskSignal`）；
- [ ] `DecisionEngine` / `DecisionPolicy` diff 为空（单一决策中心，adapter 汇入）。

---

## 7. FrameResult 扩展策略

```python
@dataclass
class FrameResult:
    frame_index: int
    n_detections: int = 0
    n_visitor_events: int = 0
    perception_events: List[PerceptionEvent] = field(default_factory=list)
    warnings: List[WarningEvent] = field(default_factory=list)
    commands: List[Any] = field(default_factory=list)
    # —— 新增（默认空列表 = 向后兼容，既有消费方无感知）——
    behavior_states: List[BehaviorState] = field(default_factory=list)
    risk_signals: List[RiskSignal] = field(default_factory=list)
```

- **短期**（Phase 1）：仅 Demo 消费——`silver_demo` 网关经 WS 推给 Dashboard 展示"进行中风险"（RAISED 亮卡 / CLEARED 熄卡）。注意 `FrameResult` 在 ADR-0015 白名单内，字段**新增**为 MINOR，演示层按可选字段消费（缺失容错）。
- **长期**：`behavior_states` / `risk_signals` 是未来 Observation API / Memory Pipeline / Agent Context 的数据源——但**如何持久化、保留多久，由独立 Memory ADR 决策**（ADR-0021 §7.1 边界），本方案不为其设计存储。

---

## 8. 测试方案

### 8.1 Unit Test（torch-free，进 CI 每 PR 合约子集）

| 文件 | 覆盖 |
| --- | --- |
| `tests/analysis/test_behavior_state.py` | 进入→dwell 累计→离开 phase 翻转；`now_provider` 驱动（非墙钟）；`schema_version=1`；`visits_in_window` 账本滑窗（过期清理、含进行中） |
| `tests/analysis/test_realtime_evaluator.py` | 状态机 `NONE→RAISED(emit)→ACTIVE_RISK→CLEARED(emit)→NONE`；ACTIVE_RISK 内不重复 RAISED；离场兜底 CLEARED；离场后条目删除（无泄漏）；阈值来自 `ThresholdConfig` 非硬编码 |
| `tests/analysis/test_signal_adapter.py` | RAISED→`PerceptionEvent` 标签映射（dwell 超阈→`abnormal_dwell`）；CLEARED 不产出；产物过冻结 schema 校验；黑名单字段（fraud/suspect）拒绝 |

### 8.2 Contract Test

`tests/test_risksignal_contract.py`：

- 字段闭合：`signal_id / track_id / visitor_instance_id / category / source / transition / features / created_at`；
- 枚举闭合：`SignalCategory` 5 值 × `SourceModality` 3 值 × `SignalTransition` 2 值；与 ADR-0022 `EvidenceModality` **无交叉 import**；
- **RAISED 必须能 CLEARED**（成对性：注入触发→回落序列，断言配对 signal_id）；
- **CLEARED 不产生 Warning**（adapter 层拦截断言）；
- **重复 RAISED 不刷屏**（同 track 持续超阈 N 帧只 emit 1 次 RAISED）。

### 8.3 Regression Test

- **基线：既有 289 测试全部通过，一个不许改**（AGENTS.md §6.2.6）；
- flag 关闭回归：`realtime_risk.enabled=false` 时，`process_frame` 输出与主线逐字段一致（golden 对比 CAVIAR 固定帧序列的 RunSummary）；
- flag 开启回归：历史路径产出（VisitorEvent / 历史 Warning 计数）与关闭时完全一致——实时是**旁路**，不得改变历史行为。

### 8.4 E2E（系统 Py3.14 全栈，仅 main / 手动）

扩展 `scripts/e2e_validate_demo.py`：CCTV 视频 + flag 开启 → 断言"人未离场即出现 RAISED 信号"（事中干预可验证）+ 离场后收到配对 CLEARED。

---

## 9. Migration Plan（分 4 个 PR，禁止一次重构完成）

| Phase | 内容 | PR 范围 | 验收 |
| --- | --- | --- | --- |
| **Phase 0** | 只加类型：`behavior_state.py` / `risk_signal.py` + 契约测试。**不接入 pipeline** | `feat/realtime-types` | 289+新契约测试全绿；pipeline diff 为空 |
| **Phase 1** | 接实时状态：`BehaviorBuilder` 挂入 `process_frame`，`FrameResult.behavior_states` 可观察。**不产信号** | `feat/realtime-behavior-state` | flag 关闭输出与基线逐字段一致；开启仅多 behavior_states |
| **Phase 2** | 接信号链：Evaluator + Adapter + 决策汇入。**默认关闭**（`enabled=false` 合入 main） | `feat/realtime-signal-chain` | 全部 §8 测试绿；关闭态 golden 回归过 |
| **Phase 3** | 灰度开启：Demo 场景 YAML 里 `realtime_risk.enabled=true`（经 `ScenarioConfig` 覆盖），E2E 验证后再改 default | `chore/enable-realtime-demo` | E2E 断言事中 RAISED；5 分钟剧本（P0-11.5b）可复现 |

每个 Phase 独立 PR、独立可回滚；任何 Phase 出问题，前一 Phase 的 main 状态即回退点。

---

## 10. Feature Flag

```yaml
realtime_risk:
  enabled: false     # 总开关；默认关闭
```

- **为什么必须有**：这是 MVP 冻结后第一次触碰主 pipeline 的架构级变更。出问题时一键关闭实时路径、保留历史路径——与 ADR-0014 冻结治理思想一致（增量可摘除，存量不受损）。
- 实现：`Settings.realtime_risk.enabled` → `PerceptionPipeline.from_settings` 装配期决定是否构造两个新组件（关闭时**不构造**，零运行时开销，边缘 CPU 友好）；
- 场景级覆盖：复用 P0-11.5a 的 `ScenarioConfig.rule_overrides` 机制同型的覆盖通道，Demo 场景可单独开。

---

## 11. 未来 Agent 接入边界（只写接口，不写 Memory）

```
BehaviorState ──→ Observation Builder ──→ Agent Context
RiskSignal    ──→ Agent Observation Input
```

- 本方案交付的 `BehaviorState.to_dict()` / `RiskSignal.to_dict()` 即未来 Observation Builder 的输入契约——**接口在此，消费另议**；
- **不做**：BehaviorState 持久化、Memory Policy、摘要/淘汰策略、Agent 推理——全部属独立 Memory ADR（未来 ADR-0024）决策范围；
- 本文档若与未来 Memory ADR 冲突，以 Memory ADR 为准（本文只保证来源侧字段稳定）。

---

## 附：与 ADR-0021 的对照速查

| ADR-0021 决策 | 本文落地位置 |
| --- | --- |
| Reality → State → Signal → Decision 四层 | §3.1 每帧顺序 |
| BehaviorState = working state（volatile） | §2.2 / §7（不持久化） |
| RiskSignal = 瞬时跃迁（transition） | §4 状态机 emit 语义 |
| 单一决策中心（adapter 汇入 DecisionPolicy） | §3.1 步骤 4 / §6 检查清单 |
| Signal Generation Layer 显式命名 | §6 边界图 |
| 不改 RuleEngine（Alternative） | §6 检查清单第 1 条 |
| MINOR 增量 / 冻结不破 | §7 FrameResult 可选字段 / §9 分 PR |

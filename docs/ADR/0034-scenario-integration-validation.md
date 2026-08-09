# ADR-0034: 闭环集成验证（Scenario Integration Validation · 完整 Runtime 链路端到端验证）

- **Status**: Accepted（2026-08-09 起草，同日 Owner 评审冻结）
- **Date**: 2026-08-09
- **Owner**: SilverShield 技术负责人
- **Implementation Plan**: [`0034-implementation-plan.md`](0034-implementation-plan.md)（类型草案 / 注入伪码 / 分阶段 MUST 清单 / 测试编号明细；**非冻结件**，可随实现演进，不需 Owner 重审）
- **Related**:
  - ADR-0032（场景仿真层 · 上游输入源：`Scenario` / `ScenarioCompiler` / `ScenarioRunner` / `ScenarioValidator` / `generator.fingerprint`）
  - ADR-0033（Benchmark Harness · 同层"感知级"打分与门控；本 ADR 把验证面从"感知"扩展到"完整闭环"）
  - ADR-0031（决策审计血缘 · 复用 `DecisionTraceRecorder` 三件套范式 + `assert_desensitized` + `policy.fingerprint`）
  - ADR-0030（决策边界契约 · `DecisionInput` 收敛载体；本 ADR **不拥有**决策逻辑）
  - ADR-0028 D6 / ADR-0029（跨模态运行时接线 + 只读检索 `CrossModalRetrieval.get_links_for_episode`）
  - ADR-0014（三级冻结治理 · L2 Interface 接缝）/ AGENTS.md §3（模块边界）/ §6.3（ADR Owner 专属）
- **Phase**: v2 · ADR-0030 → ADR-0031 → ADR-0032 → ADR-0033 → **ADR-0034（本 ADR）**

---

## 0. 背景与动机（Why）

> **引用约定**：引用代码以**符号名 + 简短引文**为准（行号会漂移）。基线 = `main` @ `455de5b`（含 ADR-0032 全切片 + ADR-0033 全阶段）。

ADR-0032 解决"能不能用可复现、隐私安全的输入喂 pipeline"；ADR-0033 解决"喂完之后感知行为怎么打分、怎么防回归"。**两者都明确止于感知层**：`ScenarioRunner.run` 逐帧只取 `fr.perception_events` 与 `fr.warnings`、**丢弃 `fr.commands`**；`RunResult` 无 `commands`/`episodes`/`cross_modal_links`/`suppress_traces`；`ScenarioValidator` 校验深度"到 `WarningEvent` 为止"；`BenchmarkReport` 不含 Memory 落库、Decision 审计、Notification 产物。

**结论：今天没有任何一层验证过"完整闭环真的跑通"。**

```
Scenario (ADR-0032 合成) → Runtime (PerceptionPipeline)
  → Memory (MemoryHook.upsert_episodic)      ← 落库是否真发生？
  → Decision (DecisionEngine → WARN/SUPPRESS) ← 是否真触发？SuppressReason 能否观测？
  → Notification (ActionExecutor → ActionCommand) ← 是否真发出？command_type 对不对？
```

### 0.1 关键发现：闭环"接线"已通，缺的是"观测 + 断言"

代码实测（`runtime/pipeline.py`）：`_act_on_event` 对每个 `VisitorEvent` **无条件**调用 `decision_engine.evaluate(percs)`，并对返回的 `WarningEvent` **无条件**调用 `executor.execute(w)`。即：**只要有感知事件，Decision + Notification 必然真实执行**。各组件各自有单测，但**跨组件接线**今天靠人工推断，**没有机器断言**。

ADR-0034 不是"再接一遍线"，而是给每个阶段装**确定性、可读回的探针**，并把"漏报式退化"（某阶段静默丢弃）钉死为 fail-closed。

### 0.2 探索实测揭示的 8 个真实缺口（G1–G8）

| # | 缺口 | 对本 ADR 的含义 |
|---|---|---|
| **G1** | **Notification 层无可注入 sink**（`ActionExecutor` 无 `sink=`、无 `all_commands()`；`MockNotifier` 只记 message dict 非 `ActionCommand`） | **最关键**。须镜像 `DecisionTraceRecorder` 新建接缝 |
| **G2** | `CrossModalEvidence` 不是代码符号（仅 ADR-0026 §6 概念） | 必须改引 `CrossModalLink` / `CrossModalRetrieval` |
| **G3** | 手工构造的 pipeline **无** `cross_modal_runtime`（仅 `from_settings` 装配），而所有场景驱动路径都是手工构造 | 必须显式注入 `memory_hook=MemoryHook(..., cross_modal_runtime=...)` |
| **G4** | 音频/视觉 `Scenario` schema 完全割裂；`audio.tts.scenario_runner.run()` 走独立 `AudioPipeline`，**不经** `process_audio_session`；同名符号三处冲突 | 需**薄桥接层** + 全限定命名 |
| **G5** | `ScenarioRunner.run` 丢弃 `fr.commands`；`RunResult` 无闭环字段 | 核心扩展点：**并列新增 `IntegrationRunner`/`IntegrationRunResult`，不扩 `RunResult`** |
| **G6** | `BenchmarkHarness.run(build_pipeline)` 回调只返回 pipeline，无观测句柄 | 用独立 harness/契约，不污染 ADR-0033 |
| **G7** | `FINGERPRINT_COMPONENT_FIELDS` 是 `harness`/`ab_runner` 守恒双向依赖的单一权威源 | 新增**独立第二枚**指纹，不碰该常量 |
| **G8** | T2 契约 allowlist 仅 `evaluation/` + `run_benchmark.py` | 新包位置须定死并**同步扩展 allowlist** |

### 0.3 现状小结

| 需求 | 今天能否满足 |
| --- | --- |
| 已知场景驱动完整 Runtime → 断言 Memory 落库发生 | **不能**（`RunResult` 无 episodes，仅 `EpisodicRecord.actions` 弱投影） |
| 断言 Decision 真触发 + WARN/SUPPRESS + SuppressReason 可观测 | **部分**（`DecisionTraceRecorder` 能观测，但无编排把它与 Scenario 绑定） |
| 断言 Notification 真发出 + `command_type`/`payload` 正确 | **不能**（无 ActionCommand recorder，G1） |
| 断言跨模态 link 在闭环中真形成 | **不能**（手工 pipeline 无 `cross_modal_runtime`，G3） |
| 闭环级确定性 + 指纹归因 + 脱敏 | **不能**（无 `loop_fingerprint`，G7） |

### 0.4 失败模型（Integration Failure Taxonomy · F1–F6）

本 ADR 的核心目标是**防止静默丢弃**，因此必须先把"静默丢弃"定义成可机检的类型，而不是只定义"成功长什么样"。

> **静默丢弃的判定式**：`上游产物存在` ∧ `下游产物缺失` ∧ `无显式拒绝理由`。
> **反例（合法，不算丢弃）**：`DecisionEngine` 返回 `SUPPRESS` 且带 `SuppressReason` —— 这是**已解释的抑制**，链路健康。ADR-0031 已把"为什么没报警"钉成事实链，本 ADR 复用该区分：**有 reason = 显式抑制；无 reason = F 类丢弃**。

| 代码 | 名称 | 可机检触发条件 | 观测点 | 默认严重度 |
|---|---|---|---|---|
| **F1** | Perception Drop | Scenario 声明了输入，但 `perception_events` 为空 / `ScenarioScore.outcome == FN` | `ScenarioScore`（复用 ADR-0033） | blocking |
| **F2** | Decision Drop | 存在 `PerceptionEvent`，但 `decision_traces` 为空（既无 WARN 也无 SUPPRESS） | `DecisionTraceRecorder` | blocking |
| **F3** | Action Drop | Decision `outcome == WARN`（期望动作），但 `ActionSink` 无任何 `ActionCommand` | `ActionSink`（D3 新建） | blocking |
| **F4** | Memory Drop | Runtime 已执行且 `memory_hook` 启用，但 `store.all_episodic()` 为空或低于 `min_records` | `InMemoryStore.all_episodic()` | warning¹ |
| **F5** | CrossModal Drop | 多模态输入存在（同 `device_id` + 时间窗重叠），但 `CrossModalLink` 为空 | `CrossModalRetrieval.get_links_for_episode` | warning¹ |
| **F6** | **Observability Drop** | 组件**确实执行**（下游有产物）但探针未捕获——即**探针本身失效** | **双通道交叉校验**（见下） | **blocking（不可降级）** |

¹ 严重度（`severity` 字段）仅在 Phase C 引入后生效（D5）；Phase A/B 期间全部按 blocking 处理。

**F6 的检测机制（本 ADR 的自我校验）**：探针失效会让所有结论"看起来通过"，是最危险的一类。因此每个探针都必须有**独立第二通道**做交叉校验：

| 探针 | 独立第二通道 | F6 判定 |
|---|---|---|
| `ActionSink.commands()` | `FrameResult.commands`（既有弱观测） | 两者数量/`command_type` 集合不一致 → F6 |
| `trace_recorder` 采集的 WARN | `IntegrationRunResult.warnings`（`fr.warnings`） | 有 warning 无对应 trace → F6 |
| `store.all_episodic()` | `EpisodicRecord.actions` 投影 vs `ActionSink` | 有 command 但无任何 episode 关联 → F6 |

**归类铁律（fail-closed）**：`LoopValidator` 报出的每一次 stage 失败**必须**归类到 F1–F6 之一；**无法归类的失败一律记为 F6 并使整体不通过**（宁可误判为探针失效，也不放过不可解释的链路缺口）。`IntegrationReport` 的每个 `StageResult` 携带 `failure_code: F1..F6 | None`。

---

## 1. 决策（Decision）

### D1 · 单一 `integration/` 包 + 断环

新增 `src/home_perception/integration/`（与 `evaluation/` 并列的**评估类**包），承载闭环编排、校验、报告、探针、指纹。

- **断环**：`__init__.py` 用 `_PUBLIC_MODULES` + `importlib` **惰性转发**（mirror `evaluation/__init__.py`），import 本包不触发任何子模块加载。
- **T2 边界（G8）**：新包**必须** import `evaluation`（复用 `ScenarioScore`/`BenchmarkReport`/`harness_fingerprint`），故须把 ADR-0033 T2 契约的 allowlist 扩展为 `("src/home_perception/evaluation/", "src/home_perception/integration/", "scripts/run_benchmark.py", "scripts/run_integration_validation.py")`，并新增 `test_adr0034_t2_integration_not_wired_into_production` 守护。

### D2 · 复用不重写 + `LoopRunner` 生命周期契约（G5）

**复用**：ADR-0032 `ScenarioCompiler`/`ScenarioRunner`/`ScenarioValidator`；ADR-0033 `ScenarioScore`/`BenchmarkReport`/`compute_harness_fingerprint`；ADR-0031 `DecisionTraceRecorder`(Protocol)/`InMemoryRecorder`/`JsonlTraceRecorder`/`assert_desensitized`/`compute_policy_fingerprint`；`InMemoryStore.all_episodic()`；`CrossModalRetrieval.get_links_for_episode`。

**新增**：`IntegrationRunner`（**并列**于 `ScenarioRunner`，不扩 `RunResult`）、`IntegrationContext`、`IntegrationValidator`、`IntegrationReport`、`IntegrationExpectationSuite`、`ActionSink` 三件套、两枚指纹。

**`IntegrationContext`（探针唯一容器）**：把 `memory_store` / `trace_recorder` / `action_sink` / `cross_modal_runtime` / `cross_modal_retrieval` / `clock` 收进单一 frozen 容器，由 `IntegrationContext.build(config)` **集中创建**——这是探针的**唯一创建点**。

**`IntegrationRunner` 生命周期（L1–L5，职责边界）**：

| 步 | 职责 | 约束 |
|---|---|---|
| **L1 建探针** | 调 `IntegrationContext.build(config)` | 唯一创建点；调用方不得逐个 new 探针 |
| **L2 注入 runtime** | 把 context 探针装配进 `PerceptionPipeline`（`decision_engine(trace_recorder=)` / `executor(sink=)` / `memory_hook(cross_modal_runtime=)`） | **唯一注入点**；注入点集中才能让 F6 交叉校验有意义 |
| **L3 执行 Scenario** | 复用 ADR-0032 编译产物逐帧推进（视觉）+ 经桥接喂音频事件（Phase B） | 不重新生成、不重校验 schema、不调 generator/renderer |
| **L4 收集 artifacts** | 从 context 探针**读回**六类产物：`perception_events`/`warnings`/`commands`/`episodes`/`cross_modal_links`/`decision_traces` | 只读；不加工、不判定 |
| **L5 产出** | `IntegrationRunResult`（含只读 context 句柄 + 六类 artifacts） | 判定归 `LoopValidator`，Runner 不做断言 |

**签名契约（防"参数搬运器"退化）**：`IntegrationRunner.run(scenario, context: IntegrationContext | None = None) -> IntegrationRunResult`。入参**只有** `scenario` 与可选 `context`（自定义后端时传入），**明令禁止**接受已装配的 pipeline / 散装 recorder / 散装 store。反模式 `runner.run(pipeline_with_some_hooks, some_recorder, another_store)` 会使注入点分散、探针漏接无法被 F6 检出。

### D3 · 可观测 seam：`ActionSink`（核心缺口 G1）

镜像 ADR-0031 `DecisionTraceRecorder` 三件套，新增 Notification 侧探针：

- `ActionSink(Protocol)`（`record(command: ActionCommand)` / `flush()`）+ `InMemoryActionRecorder` + 可选 `JsonlActionRecorder`。
- **注入点**：`ActionExecutor.__init__(..., sink: ActionSink | None = None)`；缺省 `None` → **零行为变化**（既有 action 测试路径完全不受影响）。
- Notification 阶段断言 **`ActionCommand`**（`command_type` / `payload` / `status`），**不**断言 `MockNotifier` 的 message dict（粒度不对，G1）。

> 派发时机与失败隔离的实现形态见 Implementation Plan §2。

### D4 · `IntegrationExpectationSuite`：分层 + 下界 + 结构断言（契约）

定义在 `validation/contracts.py`（**中立子包**，避免 validation 反向 import evaluation），并在 ADR-0032 `Scenario` 增加可选 `integration` 字段（向后兼容、opt-in、`ScenarioValidator` 不消费）。

**顶层容器 `IntegrationExpectationSuite`** 以**命名子期望**承载各关注点，避免单一 God Object 随阶段膨胀——未来 `human_feedback` / `privacy` / `security` 等直接加为**同级可选字段**，不污染现有结构：

| 顶层字段 | 类型 | 说明 |
|---|---|---|
| `perception` | `PerceptionExpectation \| None` | 感知阶段期望（F1 判据，复用 ADR-0033 `ScenarioScore`） |
| `memory` | `MemoryExpectation \| None` | Memory 阶段期望（下界 + 结构） |
| `decision` | `DecisionExpectation \| None` | Decision 阶段期望（结构化 outcome） |
| `action` | `ActionExpectation \| None` | Notification 阶段期望 |
| `cross_modal` | `CrossModalExpectation \| None` | 跨模态阶段期望（Phase B） |

每个子期望**独立可选、互相正交**；未声明的字段不参与校验。

**设计原则**：期望值**只用下界 + 结构断言，绝不用精确 `==` 计数**。闭环内部的 Memory 落库数、跨模态 link 数会随实现演进（一次诈骗流程可能拆成 `visitor` / `suspicious call` / `money transfer` 1~3 个 episode），精确计数会把测试**绑死到某一版实现**，催生"为过测而凑数"的反模式。

| 子结构 | 字段 | 语义（全部可选；未声明的字段不参与校验） |
|---|---|---|
| `PerceptionExpectation` | `min_perception_events` | 产出的 `PerceptionEvent` 条数**下界**（空 → F1） |
| `MemoryExpectation` | `min_records` | `EpisodicRecord` 条数**下界** |
| | `min_risk_episodes` | `risk_level is not None` 的条数下界 |
| | `min_actionable_episodes` | `actions` 非空的条数下界 |
| | `required_modalities` | 每个 modality 须在 ≥1 条 `record.modalities` 出现 |
| `DecisionExpectation` | `outcome` | `WARN` \| `SUPPRESS` |
| | `risk_level` | `LOW`\|`MEDIUM`\|`HIGH` —— **区分 `WARN_LOW` 与 `WARN_HIGH`** |
| | `recommended_action` | `MONITOR`\|`NOTIFY_FAMILY`\|`ESCALATE_COMMUNITY` |
| | `reason_code` | `SUPPRESS`→`SuppressReason.value`；WARN 侧为开放项（§5） |
| | `confidence` | 映射 `WarningEvent.perception_score`（0–1 规则命中强度，非决策置信度） |
| `ActionExpectation` | `expected_command_types` | 元素取自 `COMMAND_TYPES`（`LOG_ONLY`/`SEND_FAMILY_MESSAGE`/`CREATE_COMMUNITY_TASK`） |
| | `expected_notification` | 期望是否真发出通知；与 `expected_command_types` 非空一致 |
| `CrossModalExpectation` | `min_links` / `required_link_kinds` | `CrossModalLink` 条数下界 / 类型命中（G2 真实符号） |

**severity 字段（Phase C 生效）**：`perception` / `memory` / `decision` / `action` / `cross_modal` 每个子期望均内置 `severity: Literal["blocking","warning"] = "blocking"`。详见 D5——severity 属于**期望**而非 stage，随场景类别浮动。

> `EpisodicRecord` **无** `record_type` 枚举，故"类型"一律以真实内容判别（`risk_level` 非空 = 风险 episode；`modalities` 含 audio = 跨模态证据），不引入虚构枚举。
> 与 `BenchmarkExpectation`（`benchmark.expected_alarm`）**语义分离**：前者测"感知该不该报警"，本 suite 测"报警后整条链该不该真落库/真发出通知"。

### D5 · `IntegrationValidator` + Expectation Severity（分阶段收紧）

`IntegrationValidator.validate(run_result, scenario) -> IntegrationReport`，逐阶段对照 `IntegrationExpectationSuite`，每个阶段产出一个 `StageResult(name, passed, failure_code, severity, detail)`。

**阶段与判据**：

| Stage | 判据 | 失败归类 |
|---|---|---|
| perception | 复用 `build_scenario_score` → `outcome ∈ {TP, TN}` | F1 |
| memory | `all_episodic()` 满足 `MemoryExpectation` 各下界/结构谓词 | F4 |
| cross_modal | `CrossModalLink` 数与类型满足 `CrossModalExpectation` 下界 | F5 |
| decision | `trace_recorder` 采集的 `outcome`/`risk_level`/`recommended_action`/`reason_code`/`confidence` 逐项匹配 | F2 |
| notification | `ActionSink` 收到的 `ActionCommand` 集合匹配 `action.expected_command_types`/`action.expected_notification` | F3 |
| （全局） | 双通道交叉校验（§0.4 表） | F6 |

**Phase A/B：全阶段 AND**（忽略 per-expectation `severity`，所有 stage 按 `blocking` 处理；severity 字段在 Phase C 才生效）。理由：闭环尚未收敛时提前给出"可降级"逃生舱，会让阶段一诞生就被降级、失去建设意义。

**Phase C：severity 属于 Expectation（随场景类别浮动）**（Owner 工程反馈 2026-08-09 冻结前定稿）。并非所有阶段同等关键——诈骗场景下 Decision 错、Notification 错**必须** fail；Memory 少记一条对"老人摔倒"通常是**质量下降**而非安全事故，但对"诈骗"会影响后续阶段判断、必须 fail。若全 AND，未来"模型升级 → Memory 结构微调 → link 数量变化"会让整个 Integration Gate 变红、最终被绕过。

**关键决策：severity 不是 stage 级配置，而是每个子期望的内置字段**（默认 `blocking`）。因为 `IntegrationExpectationSuite` 是**按场景类别声明**的——每个场景（或场景组）绑定一份 suite——severity 自然随场景类别浮动，这正是旧 Open Question「Stage Severity 是否随场景类别浮动」的答案：**是，通过把 severity 写进 per-category suite 实现**。

```yaml
# 诈骗老人场景 —— memory 缺失影响后续诈骗阶段判断 ⇒ blocking
fraud_elderly_scam:
  memory:      {min_risk_episodes: 1, severity: blocking}
  decision:    {outcome: WARN, risk_level: HIGH, severity: blocking}
  action:      {expected_command_types: [SEND_FAMILY_MESSAGE], severity: blocking}
  cross_modal: {min_links: 1, severity: warning}

# 老人摔倒场景 —— memory 缺失是质量下降 ⇒ warning
elderly_fall:
  memory:      {min_records: 1, severity: warning}
  decision:    {outcome: WARN, risk_level: MEDIUM, severity: blocking}
  cross_modal: {min_links: 1, severity: warning}
```

- 判定：`IntegrationValidator` 对每个 stage 取**对应子期望的 `severity`** 字段：`passed = all(stage.passed for stage if stage.severity == "blocking")`；任一 `warning` 期望失败 → `degraded = True` + 报告显式标注 + `failure_code` 保留，**不红 CI**。
- **F6（observability）恒 blocking**：它不是期望字段，而是 `gate.py` 内硬编码的常量，配置/期望无法覆盖（对 severity 表做键过滤，`observability` 键被忽略并 warn）。
- **防降级滥用铁律**：severity 是 suite 一部分，**进 `expectation_fingerprint`**（D7），改 severity = 改指纹 = 基线须显式 bump（类比 ADR-0033 `benchmark-baseline-bump`）；severity 随 suite 版本化演进，**禁止运行时临时改 `StageResult.severity`**（frozen，t17 守护）。

### D6 · 跨模态闭环与 Bridge 职责边界（G3 / G4）

- **pipeline 装配（G3）**：驱动路径**显式注入** `memory_hook=MemoryHook(..., cross_modal_runtime=CrossModalLinkRuntime(...))`——手工构造默认无此运行时，不显式注入则 link 永远为空（会被误报成 F5）。
- **跨模态断言**：用 `CrossModalRetrieval.get_links_for_episode(episode_id)`（真实符号，`memory/cross_modal_explainer.py`）。

**Bridge 职责边界（Owner 工程反馈 2026-08-09）**：`integration/bridge.py` 是**纯 Adapter**，唯一职责为

```
AudioScenario（声明式） ──Adapter──▶ list[AudioPerceptionEvent]
```

**明令禁止**（否则 integration 会侵入生产架构，D6 从"验证层"退化为"第二套 runtime orchestration"）：

- ❌ Bridge 调用 `PerceptionPipeline.process_audio_session` —— **谁调 pipeline？`IntegrationRunner`（L2/L3）**，与视觉帧驱动同一编排点；
- ❌ Bridge 生成 episode / 触发 `MemoryHook` —— **谁生成？生产 runtime**，验证层只读回；
- ❌ Bridge 生成或断言 `CrossModalLink` —— **谁生成？`CrossModalLinkRuntime`**；断言归 `LoopValidator`。

**守护**：`bridge.py` 为**纯函数模块**（`build_audio_events(spec) -> list[AudioPerceptionEvent]`），契约测试以 AST 断言其不 import `runtime` / 不出现 `PerceptionPipeline`·`MemoryHook`·`CrossModalLink*` 符号。

### D7 · 两枚指纹：`expectation_fingerprint` + `loop_fingerprint`（G7）

**不扩** `FINGERPRINT_COMPONENT_FIELDS`（该常量是 `harness`/`ab_runner` 守恒双向依赖的单一权威源，扩它会让 `test_adr0033_t4_fingerprint_missing_field_fails_closed` 变红）。新建 `integration/fingerprint.py`，**分层两枚**：

| 指纹 | 成分 | 回答的问题 |
|---|---|---|
| `expectation_fingerprint` | `IntegrationExpectationSuite` 规范化内容（**含每个子期望的 `severity` 字段**）+ `SCENARIO_INTEGRATION_VERSION` | **"用什么标准判"** |
| `loop_fingerprint` | `harness_fingerprint`（含 `scenario_set_id`/`policy_fingerprint`/`model_fingerprint`/`runtime_dependencies`）+ `decision_policy_fingerprint` + `notification_sink_type` + `memory_backend_type` + `cross_modal_enabled` + **`expectation_fingerprint`** | **"什么输入 + 什么装配 + 什么标准 → 这份报告"** |

**为什么必须有 `expectation_fingerprint`**（Owner 工程反馈 2026-08-09）：同一个"诈骗老人"场景、同一策略、同一运行时，期望从"至少一个 warning"改成"必须 `WARN_HIGH` 且必须通知家属"，结论完全不同、但旧口径下指纹一致 —— **测试标准的变化将无法追踪**，回归基线会静默错配。把标准纳入指纹后，改期望 = 改指纹 = 基线需显式 bump。同理，同一场景类别在 fraud 下把 `memory.severity` 设为 `blocking`、在 `elderly_fall` 下设为 `warning`，这种**按场景类别浮动的 severity** 也通过 suite 内容（含 severity 字段）进入指纹，同样可追踪。

**为什么含 `SCENARIO_INTEGRATION_VERSION`**：闭环验证**自身**会演进——同样输入在 **Phase A（单模态最小闭环）** 与 **Phase C（Memory 深度 + 跨模态 + Gate）** 下产出的报告结构/覆盖度不同，无版本则两阶段产物无法区分。该常量随 ADR-0034 切片 bump（类比 `generator.fingerprint`/`harness_fingerprint` 的版本纪律），与 `scenario_set_id`/`policy_fingerprint` 正交。

两枚指纹均**缺字段 fail-closed**。脱敏复用 `analysis/decision_sink.assert_desensitized`（**非 evaluation**，不触 T2），`IntegrationReport` 落盘前必过。

### D8 · 零生产行为变化 + 边界

- 所有新增 seam（`ActionSink`、`trace_recorder`、`integration/` 包）默认 `None`/不启用；`integration/` 仅被 `scripts/run_integration_validation.py` 与 `tests/` 引用，**不进 runtime/demo/gateway**。
- 扩展 T2 allowlist（D1），并用 `tests/validation/_ast_contract.py` 的 `assert_no_dependency` 守护：`integration` 不被 `runtime`/`silver_demo`/`action`（除 sink 协议类型标注）反向 import。

---

## 2. 定位（与既有评估资产的关系）

| 层 | 资产 | 输入 | 评什么 |
| --- | --- | --- | --- |
| 感知级 | ADR-0033 `evaluation/` | ADR-0032 `Scenario` | pipeline 感知行为有没有退化（TP/FN/FP/TN） |
| Memory 级 | `memory/evaluation/`（E-1） | `MemoryReplayDataset` | Memory/Reasoning 有没有用（Shadow 模式） |
| **闭环级** | **ADR-0034 `integration/`** | **ADR-0032 `Scenario` + 完整 Runtime** | **闭环（Memory→Decision→Notification）是否真按契约运转** |

三层并列、互不重叠。本 ADR 把"感知打分"的上游结论作为**感知 Stage** 输入，向下延伸到 Memory/Decision/Notification 的可观测断言。

---

## 3. 非目标（Non-Goals）

1. ❌ 不验证决策**正确性**（归 ADR-0030/0031；本 ADR 只验"循环是否按契约触发 + outcome 是否匹配期望"）。
2. ❌ 不新增决策逻辑 / 路由表条目 / 通知渠道。
3. ❌ 不真实外发（SMS/email）——用注入的 `ActionSink` 收集，绝不触碰真实 publisher/notifier 网络。
4. ❌ 不统一视觉/音频 `Scenario` schema（仅薄 Adapter，D6）。
5. ❌ 不扩展 `RunResult` / `BenchmarkReport` / `FINGERPRINT_COMPONENT_FIELDS`（G5/G7）。
6. ❌ 不引新重依赖（cv2/numpy/pydantic/pyyaml/structlog 已在列）。
7. ❌ 不含原始媒体 / PII / 设备 / 家庭 / 用户标识（报告过 `assert_desensitized`）。
8. ❌ 不做"闭环级 A/B 回归"——留待积累数据后（类比 ADR-0033 Phase 2）。
9. ❌ `integration/` 不承担 runtime orchestration（D6 Bridge 边界）。

---

## 4. 代价与备选方案（已否决）

**代价**：新增 `integration/` 包与一层编排/校验/报告/探针；`ActionExecutor` 增加一个 `sink=None` 默认参数（零行为变化）；T2 allowlist 扩展；多模态薄 Adapter（Phase B）。

| 备选方案 | 否决理由 |
| --- | --- |
| 仅用 `FrameResult.commands` + `EpisodicRecord.actions` 现成弱观测，不建 `ActionSink` | 粒度不对（message dict 非 `ActionCommand`）；且失去 F6 交叉校验的**第二通道** |
| 扩展 `RunResult` 加 `commands`/`episodes`/`links` | 破坏 ADR-0032 `ScenarioValidator` "止于 WarningEvent" 契约，污染 ADR-0033 语义 |
| 扩 `FINGERPRINT_COMPONENT_FIELDS` 加 loop 成分 | 破坏 `harness`/`ab_runner` 守恒（G7），T4 契约必红 |
| 改走 `from_settings` 装配 cross-modal | 不如显式注入 `memory_hook=` 可控可测；`from_settings` 隐含 `episodic_shadow` 等门控，测试意图不直白 |
| Phase A 即引入 Stage Severity | 闭环未收敛时提前开降级口子，阶段会一诞生就被降级（D5） |

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **`ActionSink` 是否落盘**：默认 `InMemoryActionRecorder`，`JsonlActionRecorder` 作为 opt-in（类比 `DecisionTraceRecorder`）。
- **多模态桥接的声明 schema**："视觉 `Scenario` + 音频 scenario 引用"的字段形态，留待 Phase B 定。
- **闭环级回归（loop-level A/B）**：是否纳入、基线形态，留待积累数据后。
- **`reason_code` 的 WARN 侧粒度**：`SUPPRESS` 直映 `SuppressReason.value`（枚举确定）；`WARN` 侧目前仅有 `WarningEvent.reason_summary`（人话文本，无结构化 code），是否引入结构化 WARN reason code 留待实现定。
- **`confidence` 字段来源**：当前映射 `WarningEvent.perception_score`（规则命中强度），非"决策置信度"；若未来决策侧引入真 confidence 再迁移。
- ~~Stage Severity 是否随场景类别浮动~~：**已答**——severity 属于 per-category `IntegrationExpectationSuite` 的子期望字段，fraud 与 elderly_fall 通过各自 suite 声明不同 `memory.severity`（D5 Phase C）。

---

## 6. 实施切片（概要）

> **分阶段铁律**（沿用 ADR-0033）：设计虽完整（D1–D8），**实施**严格分三阶段，每阶段独立 PR + Owner 评审 + 零行为变化。严禁 Phase A 一次做全。
> **详细 MUST / MUST NOT 清单、测试编号明细见 [Implementation Plan](0034-implementation-plan.md) §4。**

| 阶段 | 目标 | 达成的决策 | 明令不做 |
|---|---|---|---|
| **Phase A** | 最小闭环（单模态）：`Scenario → Runtime → Decision → Notification` + Memory 落库基础断言 | D1 / D2 / D3 / D4(单模态子集) / D5(全 AND) / D8 | ❌跨模态 ❌两枚指纹 ❌Stage Severity ❌CI 门禁 ❌闭环回归 |
| **Phase B** | Memory 深度 + 多模态闭环 + 指纹 | D4(全) / D6 / D7 | ❌Hard Gate ❌CI 门禁 |
| **Phase C** | 生产门控：Stage Severity + Gate + CI job | D5(Severity + Gate) | ❌闭环级 A/B（另议） |

---

## 7. 验收标准（Acceptance Criteria）

1. **D1 边界清晰**：`integration/` 与 `evaluation/` 命名区分、Lazy `__init__` 断环；T2 allowlist 已扩展并有契约守护。
2. **D2 复用不重写 + 生命周期**：`IntegrationRunner` 不调 generator/renderer、不重校验 schema、复用 `ScenarioScore`；`run()` 签名不接受已装配 pipeline；探针创建点唯一（`IntegrationContext.build`）。
3. **D3 可观测 seam**：`ActionExecutor(sink=None)` 默认路径零行为变化（既有 action 测试不红）；Notification 断言落在 `ActionCommand`。
4. **D4 契约**：`IntegrationExpectationSuite` 分层 + 各子期望独立可选、`Scenario.integration` 可选向后兼容；`action` 收敛为 `ActionExpectation`；Memory/CrossModal 期望**全为下界 + 结构谓词**（无精确 `==`）；Decision 可区分 `WARN_LOW`/`WARN_HIGH`。
5. **D5 闭环断言**：Phase A/B 全阶段 AND；Phase C **per-expectation severity** 生效且随场景类别浮动（fraud vs elderly_fall 示例）、**只能随 suite 版本化演进**；F6 不可降级；任一静默丢弃 → 整体不通过。
6. **§0.4 失败模型**：每个 `StageResult` 失败均带 `failure_code ∈ {F1..F6}`；**无法归类 → 记 F6 并不通过**；F6 双通道交叉校验有对应测试。
7. **D6 跨模态真实**：用 `CrossModalLink`/`CrossModalRetrieval` 真实符号；`bridge.py` AST 守护通过（不 import runtime、不出现 pipeline/hook/link 符号）。
8. **D7 指纹归因**：两枚指纹缺字段 fail-closed；改期望 → `expectation_fingerprint` 必变；未污染 `FINGERPRINT_COMPONENT_FIELDS`。
9. **D8 零行为 + 脱敏**：`integration/` 不进 runtime/demo/gateway；报告过 `assert_desensitized`。
10. **边界铁律**：全量 `ruff check src tests` + `pytest` 全绿（无回归）；§3 九条非目标逐条确认。

---

## 8. 修订记录（Changelog）

> **修订权属（AGENTS.md §6.3）**：Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-09**：初稿（Proposed）。承接 ADR-0032 + ADR-0033，建立**闭环集成验证**（`src/home_perception/integration/`，与 `evaluation/`/`memory/evaluation/` 并列三层评估）。核心洞察：闭环"接线"已通（`_act_on_event` 无条件执行 decision+notification），缺的是"观测 + 断言"。D1 单一包 + Lazy 断环 + 扩 T2 allowlist（G8）；D2 复用 + 并列 `LoopRunner`（不扩 `RunResult`，G5）；D3 `ActionSink` 三件套 + `ActionExecutor(sink=None)`（G1）；D4 `IntegrationExpectation`（放 `validation/contracts.py`）；D5 `LoopValidator` 逐阶段 AND + fail-closed；D6 显式注入 `cross_modal_runtime`（G3）+ 薄桥接（G4）+ `CrossModalLink` 真实符号（G2）；D7 独立第二枚指纹（G7）；D8 零行为变化。探索实测 8 缺口 G1–G8 逐一定决策。分 Phase A/B/C，纪律同 ADR-0033。
- **2026-08-09（工程微调一 · Owner 反馈）**：收紧 `IntegrationExpectation` 表达力，三项均落到真实符号：(1) **不精确绑计数**——`expected_memory_writes`/`expected_cross_modal_links` 改为 `MemoryExpectation`（`min_records`/`min_risk_episodes`/`min_actionable_episodes`/`required_modalities`）+ `CrossModalExpectation`（`min_links`/`required_link_kinds`）；理由：`EpisodicRecord` 无 `record_type` 枚举，且精确 `==` 会绑死某一版 episode 拆分粒度。(2) **Decision 结构化**——新增 `DecisionExpectation`（`outcome`/`risk_level`/`recommended_action`/`reason_code`/`confidence`），区分 `WARN_LOW`/`WARN_HIGH`。(3) **`loop_fingerprint` 加 `scenario_integration_version`**——闭环验证自身随 Phase 演进，无版本则同输入两阶段产物无法区分。
- **2026-08-09（工程微调二 · Owner 反馈，冻结前定稿）**：五项工程细节 + 一节新增。(1) **§0.4 失败模型（新增，核心）**——定义 Integration Failure Taxonomy F1–F6（Perception/Decision/Action/Memory/CrossModal/Observability Drop）+ 静默丢弃判定式（上游存在 ∧ 下游缺失 ∧ 无显式理由；带 `SuppressReason` 的 SUPPRESS 属合法解释非丢弃）+ **F6 双通道交叉校验机制**（`ActionSink`↔`FrameResult.commands`、`trace_recorder`↔`fr.warnings`、`all_episodic`↔`actions` 投影）+ 归类铁律（无法归类 → 记 F6 且不通过）。(2) **D5 Stage Severity**——Phase A/B 维持全 AND（不提前开降级口子），**Phase C** 引入 `blocking`(perception/decision/notification)/`warning`(memory/cross_modal)，防"模型升级 → Memory 微调 → link 数变化 → 整个 Gate 变红 → 门禁被绕过"；severity 只能来自版本化声明式配置、降级走 `benchmark-baseline-bump` 类治理、且因进 `expectation_fingerprint` 而可追溯；**F6 永不可降级**。(3) **D2 `LoopRunner` 生命周期**——新增 `IntegrationContext`（探针唯一容器 + `build()` 唯一创建点）与 L1–L5 职责表（建探针/注入 runtime/执行 Scenario/收集 artifacts/产出结果），签名冻结为 `run(scenario, context=None)`，**明令禁止**接受已装配 pipeline，防退化为参数搬运器。(4) **D6 Bridge 边界收窄**——`bridge.py` 为纯 Adapter（`AudioScenario → list[AudioPerceptionEvent]`），禁止调 pipeline / 生成 episode / 生成或断言 link（分别归 `LoopRunner`/生产 runtime/`LoopValidator`），AST 契约守护，防 integration 侵入生产 orchestration。(5) **D7 增 `expectation_fingerprint`**——与 `loop_fingerprint` 分层：前者答"用什么标准判"（期望内容 + severity 表 + `SCENARIO_INTEGRATION_VERSION`），后者答"什么输入+装配+标准 → 这份报告"并**包含**前者；理由：同场景同策略下期望从"至少一个 warning"变为"必须 WARN_HIGH + 通知家属"结论不同，旧口径指纹一致会使标准变化不可追踪。(6) **文档瘦身**——实现级内容（类型草案、注入伪码、分阶段 MUST 清单、测试编号明细、DRY 债）迁出至 `0034-implementation-plan.md`，ADR 正文只保留 Why / Decision / Boundary / Contract / Failure Model / Acceptance Criteria。仍 Proposed，待 Owner 冻结。
- **2026-08-09（工程微调三 · Owner 反馈，冻结前定稿）**：采纳三项冻结前调整。(1) **D4 拆层 → `IntegrationExpectationSuite`（建议1）**——新增顶层容器，以命名子期望 `perception`/`memory`/`decision`/`action`/`cross_modal` 承载各关注点，规避 God Object 随阶段膨胀；`action` 拆为独立 `ActionExpectation`（含 `expected_command_types`/`expected_notification`），新增 `PerceptionExpectation`（`min_perception_events` 下界）。(2) **`LoopRunner`→`IntegrationRunner`、`LoopValidator`→`IntegrationValidator`（建议2，命名与 `integration/` 包及 `IntegrationExpectationSuite` 对齐；非强制但提升新人可读性，可逆）。**(3) **severity 归属 Expectation 而非 Stage（建议3，关键）**——severity 作为每个子期望的内置 `severity` 字段（默认 blocking），因 `IntegrationExpectationSuite` 按场景类别声明，severity 自然随场景浮动：诈骗把 `memory.severity` 设 blocking（缺失影响后续判断），老人摔倒设 warning（仅质量下降）；F6 仍恒 blocking 不可降级；severity 随 suite 进 `expectation_fingerprint` 可追溯。原 Stage Severity「是否随场景浮动」Open Question 已答。仍 Proposed，待 Owner 冻结。
- **2026-08-09（冻结）**：Owner 评审通过，状态 `Proposed → Accepted`。冻结范围仅限于本 ADR 正文（`Why / Decision / Boundary / Contract / Failure Model / Acceptance Criteria`）与 §0.4 失败模型；配套 `0034-implementation-plan.md` 为非冻结件，随实现演进不需重审。后续修订由 Owner 追加新条目。本 PR 仅提交文档，不含代码；实现按 Phase A/B/C 独立 PR 推进（纪律同 ADR-0033）。

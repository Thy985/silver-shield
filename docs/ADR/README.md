# 架构决策记录（ADR · Architecture Decision Records）

> 本目录记录 **Home 感知模块** 已发生的、有长期影响的架构与选型决策。
> 目的：让"为什么这样设计"可追溯，避免后人（含 AI 协作者）在无背景下推翻既定约束或重复讨论。

## 什么时候必须写 ADR

依据 [`../../AGENTS.md`](../../AGENTS.md) §6.3 / §7 / §9：

- 跨模块 / 对外契约的改动（事件 Schema、MQTT topic、与中心的数据对象对齐）；
- 影响边界的决策（本模块产出什么、不产出什么）；
- 关键技术选型（模型、协议、引擎分层、部署形态）及其被**实测数据推翻/确认**时；
- 任何"以后有人会问为什么"的决策。

> 不需要 ADR 的：局部实现细节、变量命名、可随时无痛回退的小改动。

## 编写规则（对齐 AGENTS.md §7）

- **文件名**：`NNNN-<kebab-case-title>.md`，`NNNN` 从 `0001` 递增，**编号不复用**（即使 ADR 被废弃，编号也保留）。
- **状态机**：`Proposed → Accepted → Superseded by ADR-NNNN`（被取代）/ `Deprecated`（废弃不再适用）。
- **必含小节**：背景（Context）/ 决策（Decision）/ 动机（Rationale）/ 后果（Consequences）/ 替代方案（Alternatives）。
- **不可变原则**：ADR 一旦 `Accepted`，**不原地重写**；决策变化时新开一篇 ADR 并在旧篇标注 `Superseded by ADR-NNNN`。
- **归属**：`docs/ADR/*` 为 Owner 专属受保护路径；AI 可提 PR，但**不得自行 merge / 直推 main**。

## ADR 模板

新建 ADR 时复制以下骨架：

```markdown
# ADR-NNNN: <标题>

- 状态：Proposed | Accepted | Superseded by ADR-NNNN | Deprecated
- 日期：YYYY-MM-DD
- 决策者：Owner
- 相关：docs/xx、ADR-NNNN、PR #N

## 背景（Context）
（问题是什么、约束条件、触发这次决策的场景。）

## 决策（Decision）
（我们决定做什么，一句话可陈述。）

## 动机（Rationale）
（为什么这样选，依据是什么——实测数据 / 边界约束 / 比赛目标。）

## 后果（Consequences）
（正面 + 负面 + 需要承担的技术债 / 后续动作。）

## 替代方案（Alternatives）
（考虑过但未采用的方案，及否决原因。）
```

## ADR 清单

| 编号 | 标题 | 状态 | 日期 |
| --- | --- | --- | --- |
| [0001](0001-perceive-outputs-facts-not-verdicts.md) | 感知模块只产"事实/标签"，不裁决"诈骗人员" | Accepted | 2026-07-18 |
| [0002](0002-rule-ml-two-layer-engine-defer-llm.md) | 风险引擎采用 Rule + ML 两层，LLM 解释推迟到 v2 | Accepted | 2026-07-18 |
| [0003](0003-yolo11n-explicit-resize-imgsz-profiles.md) | 检测采用 YOLO11n + 显式 resize，imgsz 配置化（默认 480） | Accepted | 2026-07-18 |
| [0004](0004-rtsp-over-hls-for-realtime-stream.md) | 实时取流 RTSP 优先、HLS 回退 | Accepted | 2026-07-18 |
| [0005](0005-event-schema-mqtt-contract-stability.md) | 事件 Schema 与 MQTT 契约作为稳定对外接口 | Accepted | 2026-07-18 |
| [0006](0006-yolo-trackid-wrapped-as-visitor-track.md) | YOLO track_id 封装为银龄盾自己的 VisitorTrack 领域对象（P0-5） | Accepted | 2026-07-19 |
| [0007](0007-p0-6-facts-vs-p0-7-semantics.md) | P0-6 事实事件层 vs P0-7 风险语义层 —— 领域对象边界固化 | Accepted | 2026-07-19 |
| [0008](0008-feature-extraction-architecture.md) | P0-7a Feature Extraction 体系：结构化数值信号层 | Accepted | 2026-07-19 |
| [0009](0009-rule-engine-architecture.md) | P0-7b Rule Engine 架构：风险语义层与五类规则 | Accepted | 2026-07-19 |
| [0010](0010-warning-event-decision-architecture.md) | P0-8 WarningEvent 决策架构：决策层与执行层分离 | Accepted | 2026-07-19 |
| [0011](0011-action-layer-architecture.md) | P0-9 ActionLayer 行动层架构：决策的执行与外部通道 | Accepted | 2026-07-19 |
| [0012](0012-p0-integration-validation.md) | P0 Integration Validation 系统级冻结前验收：6 Golden Scenarios + 状态机独立 + 故障注入 + CAVIAR 端到端 | Accepted | 2026-07-19 |
| [0013](0013-p0-10-assembly-integration.md) | P0-10 装配联调：runtime/ 包 / DemoClock 模拟时序 / Demo 专用配置覆盖 / 保持 Mock | Accepted | 2026-07-20 |
| [0014](0014-freeze-governance-three-levels.md) | 契约冻结治理：三级冻结（Schema/Interface/Runtime Assembly）+ Contract Test + 版本策略 | Proposed | 2026-07-20 |
| [0015](0015-p0-11-demo-architecture.md) | P0-11 MVP Demo 架构（多角色协同闭环展示层；术语见 ADR-0017） | Proposed | 2026-07-20 |
| [0016](0016-p0-11-3-5-demo-runtime-lifecycle.md) | P0-11.3.5 Demo Runtime Lifecycle：服务端聚合状态（DemoAggregateState）单一事实源 + 首连 Snapshot + Reset + 状态面板 | Approved | 2026-07-21 |
| [0017](0017-p0-11-role-based-workflow-demo.md) | P0-11 协同闭环 Demo 范围收敛：多角色协同闭环模拟（Role-based Workflow Demo）· 单 Dashboard 三视图非三产品 · 阶段重编号 | Accepted | 2026-07-22 |
| [0018](0018-separate-realtime-risk-signal-and-historical-visitor-event.md) | 实时风险信号与历史事件流分离：新增 Behavior State 双下游（RiskSignal 实时 / VisitorEvent 历史）+ 支持事中干预 | Proposed | 2026-07-26 |
| [0019](0019-multimodal-evidence-fusion-architecture.md) | 多模态证据融合架构：Vision / Audio 双独立感知链 + Evidence Fusion 阶段，WarningEvent.evidence 升为类型化列表 | Proposed | 2026-07-26 |
| [0020](0020-decouple-short-term-tracking-identity-and-long-term-visitor-identity.md) | 短期追踪身份（track_id）与长期访客身份（person_id）分离：新增 Identity Resolver 阶段 | Proposed | 2026-07-26 |
| [0021](0021-realtime-riskstream-concrete-design.md) | 实时风险状态流与信号生成层·具体设计：把 ADR-0018 落为 Reality→State→Signal→Decision 四层，BehaviorState/RiskSignal/RealTimeRiskEvaluator/adapter，复用单一 DecisionPolicy（Phase 1） | Proposed | 2026-07-26 |
| [0022](0022-evidence-chain-multimodal-interface.md) | 证据链与多模态接口·具体设计：EvidenceItem(类型化证据)+EvidenceAggregator(只整理不重推)+WarningEvent.evidence_items（Phase 2） | Proposed | 2026-07-26 |
| [0023](0023-identity-continuity-system.md) | 身份连续性系统·具体设计：track_id/visitor_instance_id/person_identity_id 三层 + IdentityResolver（Phase 4，v1 不冒充真实身份） | Proposed | 2026-07-26 |
| [0024](0024-memory-architecture.md) | Memory 架构·三类记忆模型与 Memory Policy：Short-term / Episodic / Semantic（Environment+Identity）+ Episode Builder + Invariants + Trust Layer + Snapshot 原则（Phase 4-5，v1 不实现） | Accepted | 2026-07-28 |
| [0025](0025-memory-consumer-architecture.md) | Memory Consumer 架构·让记忆反哺理解：Retrieval/Aggregation/Context Builder/Reasoning Interface 四组件 + 严格单向管道 + 三数据契约（ReasoningInput/ReasoningResult/DecisionRequest）+ 执行模型（触发时机）；不直接决策、不改 Risk Score（v2，承接 ADR-0024） | Accepted | 2026-08-02 |
| [0026](0026-audio-perception-chain-concrete-design.md) | 音频感知链路·具体设计（review-ready）：独立 Audio 管道 + Tier0 VAD/Prosody（零模型，常驻）+ Tier1 YAMNet（目标预算 <20ms，config 可选）+ AudioSegmentEvent/AudioPerceptionEvent(5 类声学感知/AudioPerceptionKind, score+confidence) + AudioAdapter(integration layer) 接入 RiskSignal(AUDIO,COMMUNICATION)/EvidenceItem(AUDIO)；跨模态关联移至 CrossModalEvidence，新增 AudioEvidencePolicy 与 Phase 3.0 MVP Scope（不投重模型/LLM/ASR，承接 ADR-0019/0022，全 MINOR 不破 ADR-0014） | Proposed (review-ready) | 2026-08-04 |
| [0027](0027-audio-memory-integration.md) | 音频记忆集成：音频经统一决策门（DecisionPolicy）汇入 Memory，不新增写入链；D1 modalities 多模态标记 + D2 统一 EvidenceItem（独立存储、`evidence_refs` 以 ID 引用，废弃双 EvidenceRef）+ D3 不新增写入链（基石）+ D4 AudioSessionId 不强绑 visitor + D5 CrossModalLink 关系/边 + D6 Consumer audio-aware（仅标签不分数）+ D7 EvidenceModality 枚举契约（继承 ADR-0022，不增 SENSOR/POSE/UNKNOWN）+ D8 Schema Evolution 向后兼容（v1/v2 双形状、字段对照表）+ D9 Audio Evidence 分层留存 + 可变 EvidenceAssetState 生命周期（修复悬空 URI/不可审计删除）（承接 ADR-0024/0025/0026/0022/0021，守 ADR-0001/0002） | Proposed (review-ready) | 2026-08-06 |
| [0028](0028-cross-modal-runtime-wiring.md) | 跨模态运行时接线：MemoryHook 落库后触发 CrossModalLinker 自动建边，让「视觉事件 + 声音事件」首次在 Memory 中形成关联；D1 EpisodicRecord 增可选 device_id（same_device 载体）+ D2 linker 同源判定扩展（身份键 或 设备键，时间重叠硬 gate）+ D3 min_overlap_seconds 阈值可配 + D4 CrossModalLinkRuntime 可选注入零行为变化 + D5 MemoryStore.all_episodic 全量扫描 + D6 最小 Synthetic Episode Fixture（声明式 scenario → 直接验证 Memory Graph）；第一版只做 same_device + time_overlap，不做身份/语义判定（承接 ADR-0027 D5 / Slice C，守 ADR-0001/0002） | Proposed (review-ready) | 2026-08-06 |
| [0029](0029-cross-modal-memory-retrieval-explanation.md) | 跨模态记忆检索与解释：ADR-0028 建好的跨模态边「谁来读懂」——纯只读检索 + 解释层，不是判断；D1 `get_links_for_episode` episode 主路径唯一对外 API（visitor/window 降内部能力）+ D2/D3 抽离 `ExplanationRenderer`（`CrossModalContext` 为纯结构化事实、`link_confidence` 非风险分）+ D4 `ReasoningInput.cross_modal_contexts` + Consumer 可选注入（零行为变化）；§2.1 C6 硬约束禁止风险语义字段、因果不暗示（support≠cause）、无判断词，契约测试钉死（承接 ADR-0028，守 ADR-0001/0002/0010/0025 C1） | Proposed (review-ready) | 2026-08-07 |
| [0030](0030-decision-boundary-contract.md) | 决策边界契约：四段链路在 Decision 前断成「感知直连决策」与「Memory 仅供 Shadow 观测」两截，本 ADR 冻结 `DecisionInput` 为唯一收敛载体；D1 四类型角色确权（RiskSignal/ReasoningInput/ReasoningResult 均非决策）+ D2 `DecisionInput` 契约字段 + D3 C1–C7 不变式（C1 上游无决策语义/hint 仅排序、C4 link_confidence 非阈值、C5 决策对 device_id 不变、C6 prior_warning 迟滞 seam、C7 一级聚合防 God Object）+ D4 `decide` 单入参签名演进（调用方兼容/实现方破坏性）+ D5 Memory→Decision 受控验证门（默认 Shadow、action lattice max-only、需 Owner 放行）；真正价值是建立 Future Decision Experiment Boundary——收益由受控验证回答而非契约存在（承接 ADR-0029，守 ADR-0010/0001/0002） | Proposed (review-ready) | 2026-08-07 |
| [0031](0031-decision-audit-trace-contract.md) | 决策审计血缘契约：把「为什么报警」与「为什么**没**报警」钉死为可审计事实链，是 ADR-0030 Slice C 受控验证门的硬性前置；先以代码实情校正 ADR-0030 §5.1 草案四处（空决策不可观测 / 无稳定事件引用 / `rejected_actions` 不可计算 / `policy_version` 硬编码 v1）；D1 `outcome` 带标签联合 WARN \| SUPPRESS + `SuppressReason` 严格派生自三条真实 `return None`（漏报首次留痕）+ D2 五具名 Bundle（identity/provenance/policy/rationale/outcome）+ 字段白名单 fail-closed + `arm`/`correlation_id` + D3 `considered_candidates` 记录事实不构造反事实 + D4 引用不复制（`TriggerRef` 以 C3 规范化后下标+三元组绕行、`MemoryRefs` 仅存 ID）+ D5 `policy.fingerprint` 反映实际生效路由表、`WarningEvent.meta` 四键冻结为 legacy + D6 `DecisionTraceRecorder` 可选注入零行为变化（不走 DecisionInput）+ D7 `DecisionABRun` 双轨载体与四条唯一变量守恒断言；T1–T8 不变式（只写不读 / 不改决策 / 失败隔离 / 无判定字段 / 无原始媒体 / 确定性 / 抑制必留痕 / 不重复真相）（承接 ADR-0030 §5.1，守 ADR-0014 `meta` 晋升条款 / ADR-0001/0002/0010） | Proposed (review-ready) | 2026-08-08 |
| [0032](0032-scenario-simulation-layer.md) | 场景仿真层（Perception Validation Infrastructure）：把 ADR-0028 D6 划出的"程序化视频/合成视频生成"落地为**声明式 `Scenario` schema → 两通道生成**（`detections`=`list[Detection]`(复用现有类型, 按 actor 确定性回填 track_id, 非 None 否则 VisitorTracker 丢弃) 零模型 / `frames`=OpenCV 程序化 BGR 帧，二者同源 `actors.tracks`→T8 单一真相源）；schema 拆 `environment`(命名 `regions` 抽象标签+`static_objects`)/`camera`(resolution/fps/viewpoint)、`ActorSpec.actor_type∈{human,vehicle,pet,object}` 解耦 person、`meta` 三层版本化(`schema_version`格式/`scenario_id`资产/`version`内容, T10 三字段加载期必填)；`frames` 通道验证边界明示（✅frame摄入/detector接口/tracking/temporal ❌语义准确率/外观/光照/domain gap）；D4 **三组件预拆** `ScenarioCompiler`/`ScenarioRunner`/`ScenarioValidator`（为 ADR-0033 并行100 scenario+聚合让路）；确定性（seed/仅`frame_index`/跨进程可复现, T1 纳入 numpy/opencv 版本）/ 隐私（替代真实素材, T2 场景YAML不进公共PR+`regions`抽象标签）/ 零生产行为变化（仅经 ADR-0014 L2 接缝；Slice E 改依赖倒置注册钩子，绕过 ADR-0015 §5 白名单）；**D5 采纳 `validation/` 父包变体**（`src/home_perception/validation/` 含 scenario/simulation/runner/fixtures，本质 Perception Test Infrastructure，否决扁平 `simulation/` 与 `silver_demo/simulation/`）；`Detection` 复用现有类型(不新增 RawDetection)、track_id 由 generator 按 actor 确定性回填、仍只产感知原语不越权业务判定；D6 迁移且新 `detections.json` 与旧缓存**字段级等价**(均含 track_id)；D7 `generator.fingerprint`(含 numpy/opencv 版本, 不含设备/家庭ID) 类比 ADR-0031 `policy.fingerprint`；**D8 Scenario Registry**（`validation/fixtures/scenarios/{perception,regression,benchmark}/` + `meta` 预留 owner/tags/difficulty/category，供 ADR-0033 直消费不回改 schema）；T1–T11（测试名带 `adr0032` 前缀）；非目标：不训练YOLO/不生成照片级视频/不加fraud类/不做实时流源/不做Benchmark Harness/不直注Memory Graph/不入库MP4/不引新重依赖；并**记录** `ActorSpec→EntitySpec` 语义演化（本ADR不做）（承接 ADR-0028 D6 / ADR-0031，守 AGENTS.md §3 / §6.1） | Proposed | 2026-08-08 |
| [0033](0033-benchmark-harness.md) | 基准测试框架（Benchmark Harness · 感知级仿真场景回归打分）：消费 ADR-0032 仿真场景做 pipeline 行为回归，闭合 AI 验证基础设施闭环。D1 单一 `evaluation/` 包，与 `benchmark/yolo_speed.py`(性能)/`memory/evaluation/`(Memory E-1) 三块评估资产边界清晰；D2 仅编排 ADR-0032 三组件（不重生成/不重校验 Schema）；D3 把 ADR-0031「漏报/误报可观测」**下沉到场景级混淆矩阵**（TP/FN/FP/TN + `suppression_rate`/`false_alarm_rate`），引入独立 `BenchmarkExpectation`(`benchmark.expected_alarm`) 与 ADR-0032 `expects` 语义分离；D4 `harness_fingerprint` 升级为 `(code, scenario_set, environment)` 三元组（含 `scenario_set_id`/`model_fingerprint`/`runtime_dependencies`）；D5 门控与复合分**推迟到 Phase 3**，且 `BenchmarkScore` 加权分须 `calibrated=False`/experimental、不得门控（安全指标非线性，不早于数学化）；D6 `BenchmarkABRun`+`assert_conserved` 七条守恒（默认 vary=代码版本、可切模型权重轴，与 ADR-0031 `DecisionABRun` 轴正交）；D7 基线**推迟到 Phase 2**；D8 零生产行为变化（`evaluation/` 仅被 `scripts/run_benchmark.py` 与 `tests/` 引用）+ 全程脱敏。实施严格三阶段 Phase 1(最小闭环:Scenario→Harness→ScenarioScore→BenchmarkReport, 只报离散指标)/Phase 2(回归)/Phase 3(生产门控)，每阶段独立 PR+零行为变化，防范围膨胀；T1–T10（测试名带 `adr0033` 前缀）；非目标：不重生成/不重校验Schema/不改规则行为/不拥有决策级A/B/不做性能基准/不做Memory价值评估/不产出未标定单一加权分/不入库原始媒体/不引新重依赖（承接 ADR-0032，守 AGENTS.md §3 / §6.1 / ADR-0014） | Accepted | 2026-08-09 |
| [0034](0034-scenario-integration-validation.md) | 闭环集成验证（Scenario Integration Validation · 完整 Runtime 链路端到端验证）：在 ADR-0032(可复现输入)+ADR-0033(感知级打分) 之上，把验证面从"感知"扩展到"完整闭环"——证明 `Scenario→Runtime→Memory→Decision→Notification` 在已知场景上**真按契约运转**（各组件单测俱全，但跨组件接线今天无机器断言）。核心洞察：闭环"接线已通"(`_act_on_event` 无条件执行 decision+notification)，缺的是"观测+断言"，故给每阶段装确定性可读回探针。**§0.4 失败模型（Integration Failure Taxonomy F1–F6）**：Perception/Decision/Action/Memory/CrossModal/**Observability** Drop + 静默丢弃判定式(上游存在∧下游缺失∧无显式理由；带 `SuppressReason` 的 SUPPRESS 属合法解释非丢弃) + **F6 双通道交叉校验**(`ActionSink`↔`FrameResult.commands`、`trace_recorder`↔`fr.warnings`、`all_episodic`↔`actions` 投影) + 归类铁律(无法归类→记 F6 且不通过)。D1 单一 `integration/` 包+Lazy `__init__` 断环+**扩展 T2 allowlist**(G8)；D2 复用 ADR-0032/0033/0031 符号，新增并列 `IntegrationRunner`/`IntegrationValidator`/`IntegrationReport`(**不扩** `RunResult`，G5)+`IntegrationContext`(探针唯一容器/`build()` 唯一创建点)+**L1–L5 生命周期**(建探针/注入runtime/执行Scenario/收集artifacts/产出)，签名冻结 `run(scenario, context=None)` **禁收已装配 pipeline**(防参数搬运器退化)；D3 镜像 `DecisionTraceRecorder` 新增 `ActionSink` 三件套+`ActionExecutor(sink=None)` 注入(**G1 关键缺口**，断言 `ActionCommand` 而非 MockNotifier message dict)；D4 `IntegrationExpectationSuite`(放 `validation/contracts.py` 避免反向 import)+`Scenario.integration` 可选字段，与 `BenchmarkExpectation` 语义分离；D5 `IntegrationValidator` 逐阶段 AND + `failure_code` 归类 + fail-closed(漏报式退化钉死)，**Phase C 引入 per-expectation Severity**(severity 是各子期望内置字段、随场景类别浮动：fraud 把 `memory.severity` 设 blocking、elderly_fall 设 warning；blocking=perception/decision/notification，warning=memory/cross_modal；防"模型升级→Memory微调→link数变化→整个 Gate 变红→门禁被绕过"；severity 随 suite 版本化演进、改则必改指纹可追溯；**F6 永不可降级**)；D6 显式注入 `memory_hook=(...cross_modal_runtime=...)`(G3)+多模态**纯 Adapter** `bridge.py`(`AudioScenario → list[AudioPerceptionEvent]`，**禁**调 pipeline/生成 episode/生成或断言 link——分别归 `IntegrationRunner`/生产 runtime/`IntegrationValidator`，AST 契约守护，防 integration 侵入生产 orchestration)，用 `CrossModalLink` 真实符号(**G2**：`CrossModalEvidence` 非代码符号)；D7 **两枚分层指纹**——`expectation_fingerprint`(suite 内容含 per-expectation severity+`SCENARIO_INTEGRATION_VERSION`，答"用什么标准判")被 `loop_fingerprint`(harness+policy+sink+memory backend+cross_modal+expectation，答"什么输入+装配+标准→这份报告")包含，均**不扩** `FINGERPRINT_COMPONENT_FIELDS`(G7)+复用 `assert_desensitized`；D8 零生产行为变化+边界守护。实现级细节（类型草案/注入伪码/分阶段 MUST 清单/T1–T19 测试明细/DRY 债）拆至配套 [0034-implementation-plan.md](0034-implementation-plan.md)（非冻结件）。探索实测揭示 8 缺口(G1–G8) 逐一定决策。实施严格三阶段 Phase A(最小闭环:Runtime→Decision→Notification+Memory落库断言,单模态)/Phase B(Memory+跨模态深度+loop_fingerprint)/Phase C(生产门禁)，纪律同 ADR-0033；非目标：不验决策正确性/不新增决策逻辑/不真实外发/不统一双 Scenario schema/不扩 RunResult·BenchmarkReport·FINGERPRINT_COMPONENT_FIELDS/不引新重依赖/不含 PII/不做闭环级 A/B（承接 ADR-0032/0033/0031/0030/0028/0029，守 AGENTS.md §3 / §6.1 / ADR-0014） | Accepted | 2026-08-09 |
| [0035](0035-runtime-evidence-explorer.md) | 运行证据探索器（Runtime Evidence Explorer · Evidence Presentation Layer）：把 ADR-0031/0032/0034 已落盘的可信 artifact 转为人类可理解的时间轴/因果链/关系图，回答「为什么系统认为这里有风险」。**定位**：第四层**可信工程资产（呈现层）**——不参与运行、不参与判断、不改变系统行为（与 evaluation「验证感知」/ integration「验证闭环」职责互补但**无验证能力**，杜绝「visualizer 验证系统」语义）。D1 `visualizer/` 落点 + 命名刻意避开 frontend/dashboard（回放运行证据非业务指标）；D2 **数据投影契约**（`EvidenceProjection → EvidenceTimelineArtifact`：loader 唯一入口、`ref` 必填、缺失粒度降级 stage 摘要、**禁 synthetic node**、`provenance_kind`(REAL_SENSOR/SIMULATED/FIXTURE) 防合成当真实）+ **D2b Schema Evolution Fail-Closed**（artifact 关键字段演化缺失 → 投影抛错拒绝，绝不产出 undefined 空白页）；D3 消费协议**禁 import 生产类**（AST 死胡同叶子），但 `visualizer/schema/` 自建 TypedDict 保类型安全；D4 技术栈分层（D1 Python stdlib 静态自包含单页 HTML + ECharts 单框架零服务器 → D2 FastAPI+D3.js Replay → D3 复用 ADR-0032 frames+audio tts 程序化视频）；D5 **Evidence Graph 统一抽象**（节点 Scenario/Detection/Event/Decision/Action/Episode/Link × 边 caused_by/supports/derived_from/triggered/stored_as；Timeline/Decision Trace/CrossModal/程序化视频均为投影视角；**属展示层派生模型，非 runtime 领域模型/状态交换协议**）；D6 四视图（Scenario Replay Timeline 最高价值 / Decision Trace / Cross Modal Graph / Fingerprint-Gate）；D7 脱敏 + **D7b Presentation Identity Policy**（身份角色化：Resident-A/Visitor-B/Device-01 可，真实姓名/手机号/家庭地址/设备序列号禁）；D8 确定性（同 artifact 两次生成逐字节一致）+ Evidence provenance（节点 ref 可溯源）+ schema 兼容；D9 零行为变化（不接 CI 门禁，是「给人看」的资产）。实施 D1(Timeline→Decision Explanation→Graph 顺序)/D2/D3 独立 PR；非目标：不实时前端/不大而全/不新增采集存储判定/不捏造节点/不 LLM 解释/不穿透 ADR-0015（承接 ADR-0031/0032/0034/0024/0027/28/29，守 AGENTS.md §3 / §6.1 / ADR-0014） | Proposed (owner review) | 2026-08-12 |
| [0036](0036-unified-case-viewer.md) | 统一 SilverShield Case Viewer（**展示语义统一层** · 单一 View Model）：四块资产经三类 Adapter 收敛到唯一 `EvidenceProjection` 而非再造第五块前端；VM-1~VM-13（唯一 View Model / 禁 synthetic / 不依赖 silver_demo / 同源 schema / 零行为 / 只读派生非状态总线 / 字段可缺失禁伪造 / Live 增量幂等 / 不生成证据 / 双时间轴分离 / CasePresentationDescriptor 仅展示编排 / Case Video≠Analysis Video / Live 音频分阶段）；**D-CaseVideo** 定义 Case Video 主线（Context→Incident→Risk Escalation→AI Perception→Intervention→Outcome）并划清与 Analysis Video 边界、**D-Audio** 音频第一等证据（统一时间轴、audio_evidence 锚定真实符号、字段来源表见附录）、**D-MediaMode** 三媒体模式 + 双时间轴、**D-CasePresentation/D-SyncClock** 展示编排 + Case Time 同步；首切片先 Artifact Case Video 后 Live 后音频（承接 ADR-0015/0016/0017 + ADR-0035，守 ADR-0001/0002/0035 D5/D9） | Accepted | 2026-08-16 |
| [0038](0038-phone-detection-capability-boundary.md) | Phone Detection 能力边界确认与 Evidence Contract 调整：30 帧 Benchmark 证实 YOLO11n/s COCO 预训练对 <0.05% 面积目标 Recall=0%，降级 `phone_interaction` 为 `optional_supporting`；Audio → Risk 独立路径完整可用（AUDIO_TELEPHONE_PERSISTENT + AUDIO_DISTRESS_CRY → RISK_SIGNAL → LOW → LOG_ONLY）；Cross-modal Links=0 为已知限制，Phase 0 MVP 允许；不重新训练 Phone Detector（边缘 CPU 预算 + ROI 低）（承接 ADR-0001/ADR-0026，守 AGENTS.md §4.1） | Proposed | 2026-08-21 |
| [0039](0039-runtime-frame-context-contract.md) | Runtime Entry Contract（RuntimeFrameContext 单容器进给）：`process_frame(ctx)` 唯一入参与 `FrameResult` 对称（in/out dataclass 对）；四字段冻结（video_frame 允许 None / frame_index / case_time 显式化消除 gateway 三处重复计算 / audio_events）；**不预留 thermal/imu/door 占位字段**——Context 是扩展边界不是字段垃圾桶，第二模态进入时再走 ADR；旧签名保留一版过渡（Owner 拍板 Option B；承接 ADR-0021/0026，前置 ADR-0041 时钟统一锚点） | Accepted | 2026-08-22 |
| [0040](0040-decision-input-risk-signals-first-class.md) | DecisionInput 引入 risk_signals 一等输入（多模态决策契约）：彻底结束"Audio RiskSignal 伪装成 PerceptionEvent 进决策"路径（signal_adapter 不识别 audio_kind 必落幻觉兜底）；C7 白名单**临时扩展 5→6、6 是硬顶**（非长期目标，后续字段必须 Bundle 化）；语义边界 risk_signals = Runtime 输入 ≠ Decision Result，features 禁决策语义键（C1 不失效）；构造期 (created_at, signal_id) 排序规范化；**policy 升级前 gateway 不接通 audio→risk 链**（防假通电）（Owner 拍板 Option C；修订/扩展 ADR-0030 C7，承接 ADR-0019/0021） | Accepted | 2026-08-22 |
| [0041](0041-signal-level-temporal-alignment.md) | Signal 级跨模态时间对齐机制（SignalTemporalLinker）：**冻结机制不冻结窗口数值**——新增 analysis 层纯函数组件产出 LinkedSignalPair（SAME_FRAME 强关联 / NEAR_WINDOW 可配置弱关联 / UNLINKED）；`window_s = configurable, default = TBD by acceptance data`（2.0s 等候选档由真实 telephone_risk Δt 分布决定，不预设）；前置统一 Runtime 时钟语义（episode_start_unix 锚定，audio Unix 秒 → case_time）；与 episode 级 CrossModalLinker（ADR-0028）职责显式分离；依赖方向 Q3 → Q4（Temporal Alignment 在 Evidence Strength 之前）（Owner 拍板；ADR-0019 Evidence Fusion Phase 1 落地件） | Accepted | 2026-08-22 |
| [0042](0042-audio-evidence-strength-grading.md) | Audio Evidence Strength 分级与升级契约（telephone_risk）：**冻结五档等级不冻结阈值参数**——INSUFFICIENT/MONITOR/RAISE/NOTIFY/ESCALATE 语义与判定维度固定，N/T/score/confidence 全部 TBD；参数确定流程不可跳步（修 class_map → 真实 AudioKind 分布 → E2E → precision/recall/false escalation → 回填 config）；**MONITOR ceiling 硬门控**（class_map 修复 + YAMNet 验证前音频证据封顶 MONITOR，链路可先接通）；新建 RealTimeAudioRiskEvaluator（CLEARED = 同 kind 静默超时 ≠ 视觉主体离场）；ESCALATE 须经 ADR-0041 关联验证、禁 audio 单方推断视觉事实（承 ADR-0038 A4 否决精神）（Owner 拍板） | Accepted | 2026-08-22 |
| [0043](0043-risk-signal-dual-track-projection.md) | RiskSignal 双轨投影契约（覆盖式当前态 + 累积式事件历史）：核心契约冻结「Projection 必须同时支持状态与事件」（CURRENT STATE / HISTORY 同构既有分层）；实测实证单覆盖语义致 0 RAISED 存留（frame0 RAISED 被 frame1 CLEARED 覆盖）破坏叙事与 paired_signal_id 配对；底层幂等契约冻结（signal_id 主键 + seq 序列，VM-8）；**payload 字段名/形状留给实现设计**（增量推送约束保留）；服务端权威状态机零改动（PR-B 红线）；长会话上限登记开放项（Owner 拍板双轨制；承接 ADR-0021/0035/0036） | Accepted | 2026-08-22 |
| [0044](0044-audio-data-asset-tri-split-story-timeline.md) | 音频数据资产三层解耦与 StoryTimeline 单一真相源契约：qualification/runtime/product_story 分域（模型能力域冻结收口，产品叙事域独立建库）+ StoryTimeline 唯一真相源（时间轴固定、产生方式次级）+ provenance 三态（synthetic_replay/runtime_generated/real_sensor 同一 oracle 重跑）+ benign/risk 双 fixture 强制成对（验证「识别到电话≠诈骗风险」）+ B-hard 登记为 limitation 不升格 PASS（语义真实性≠信道真实性）；防「为 E2E 通过反向设计 Runtime」——Story Contract 固定，Runtime 是实现者（Owner 六项决策 D1~D6；承接 RUN1 报告/ADR-0032/0028，守 ADR-0001） | Proposed | 2026-08-23 |

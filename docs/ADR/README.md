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
| [0032](0032-scenario-simulation-layer.md) | 场景仿真层（Perception Validation Infrastructure）：把 ADR-0028 D6 划出的"程序化视频/合成视频生成"落地为**声明式 `Scenario` schema → 两通道生成**（`detections`=`list[RawDetection]` 零模型 / `frames`=OpenCV 程序化 BGR 帧，二者同源 `actors.tracks`→T8 单一真相源）；schema 拆 `environment`(命名 `regions` 集合+`static_objects`)/`camera`(resolution/fps/viewpoint)、`ActorSpec.actor_type∈{human,vehicle,pet,object}` 解耦 person、`meta` 三层版本化(`schema_version`格式/`scenario_id`资产/`version`内容)；`frames` 通道验证边界明示（✅frame摄入/detector接口/tracking/temporal ❌语义准确率/外观/光照/domain gap）；D4 **三组件预拆** `ScenarioCompiler`/`ScenarioRunner`/`ScenarioValidator`（为 ADR-0033 并行100 scenario+聚合让路）；确定性（seed/仅`frame_index`/跨进程可复现）、隐私（替代真实素材）、零生产行为变化（仅经 ADR-0014 L2 接缝）；**D5 采纳 `validation/` 父包变体**（`src/home_perception/validation/` 含 scenario/simulation/runner/fixtures，本质 Perception Test Infrastructure，否决扁平 `simulation/` 与 `silver_demo/simulation/`）；`RawDetection` 无 `track_id`、与 `DetectionResult` 严格分离（呼应 ADR-0031 Non-goal #8）；D6 迁移 `CachedDetectionDetector`/`detections.json`/`_make_synthetic_mp4`；D7 `generator.fingerprint` 类比 ADR-0031 `policy.fingerprint`；**D8 Scenario Registry**（`validation/fixtures/scenarios/{perception,regression,benchmark}/` + `meta` 预留 owner/tags/difficulty/category，供 ADR-0033 直消费不回改 schema）；T1–T11 钉死确定性/隐私/无真实媒体/不破Schema/零行为变化/可校验/成本有界/两通道几何一致/三组件单一职责/三层版本化/产出血缘可溯源；非目标：不训练YOLO、不生成照片级视频、不加fraud类、不做实时流源、不做Benchmark Harness（归ADR-0033）、不直注Memory Graph、不入库MP4、不引新重依赖；并**记录** `ActorSpec→EntitySpec` 语义演化路线（本ADR不做）（承接 ADR-0028 D6 / ADR-0031，守 AGENTS.md §3 / §6.1） | Proposed | 2026-08-08 |

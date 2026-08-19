# SilverShield · Home 感知模块 — 设计文档索引

> 本目录为 **Home 感知模块**（家庭入口区域实时感知与风险证据采集）的工程与架构设计文档。
> 模块在 SilverShield 全局中对应 **Perceive 感知** 逻辑模块 + **门前时空异常与蹲守识别** 子系统，
> 部署于 **Home 端**，是风险数字孪生（RiskTwin）的前端事实采集器。

## 文档清单

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| AGENTS | [`../AGENTS.md`](../AGENTS.md) | **AI 协作开发强制规范（顶层）** —— 所有 PR 须满足；Git 约定见其 §5 |
| 00 | `00_README.md` | 本索引 |
| API | `API_REFERENCE.md` | **团队第一入口**：稳定公共 API 表面（入口 / 可替换接口 / 禁止依赖） |
| CONTRACT | `CONTRACTS.md` | 冻结契约（三级冻结 + Freeze Gate + 黑名单字段） |
| ARCH | `ARCHITECTURE.md` | 系统架构总览（数据流图 + 分层映射 + 红线摘要） |
| CONTRIB | `CONTRIBUTING.md` | 贡献指南（分支 / 提交 / 测试 / 冻结纪律） |
| 01 | `01_module_positioning.md` | 模块在系统中的定位、边界、上下游 |
| 02 | `02_architecture.md` | 模块内部架构、数据流、与三层引擎的关系 |
| 03 | `03_directory_layout.md` | 目录树与每个目录的职责 |
| 04 | `04_development_standards.md` | 编码、测试、日志、配置、提 PR 规范 |
| 05 | `05_git_workflow.md` | 分支策略、提交规范、发布 |
| 06 | `06_api_contract.md` | 与中心（AI 分析服务 / 业务服务）的接口契约 |
| 07 | `07_event_schema.md` | 感知事件（VisitorEvent）字段与取值说明 |
| 08 | `08_roadmap.md` | 分阶段研发路线与第一阶段任务拆解（§8.4 含 v2 演进路线） |
| 09 | `09_risks.md` | 技术 / 项目风险与缓解 |
| ENV | `DEVELOPMENT_ENV.md` | 开发/运行双环境说明（managed venv 跑 ruff/pytest · system Py3.14 跑 AI 栈 + E2E） |
| HPC | `HPC-USAGE-GUIDE.md` | 高性能计算公共平台（傲飞）使用指南（GPU 推理 + Demo 部署操作手册） |
| ASSETS | [`../silver-engineering-assets/`](../silver-engineering-assets/) | **工程资产库**（长期可复制能力：架构/代码/测试/调试/Demo/前端/后端/ADR 模板/失败案例 10 类）；母项目沉淀，新项目套结构；统一索引见 [`HANDBOOK.md`](../silver-engineering-assets/HANDBOOK.md) |
| ADR | [`ADR/`](ADR/) | **架构决策记录**（为什么这样设计，可追溯）；编写规则见 [`ADR/README.md`](ADR/README.md) |

### P0-11 多角色协同闭环展示层（Demo）文档

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| DESIGN-11.4 | `DESIGN-p0-11-4-role-based-workflow.md` | P0-11.4 三视图（① 风险发现 / ② 家属确认 / ③ 社区处置）设计：阶段叙事、共享 `DemoAggregateState`、方案 A 单按钮 |
| DEMO-SCRIPT | `DEMO-SCRIPT-P0-11-5b.md` | P0-11.5b 5 分钟演示剧本（口播 + 切 Tab + 点按钮 SOP，与 E2E 对齐） |

> Demo 展示层的架构决策见 [`ADR/0015`](ADR/0015-p0-11-demo-architecture.md)（技术选型）、
> [`ADR/0016`](ADR/0016-p0-11-3-5-demo-runtime-lifecycle.md)（运行时生命周期）、
> [`ADR/0017`](ADR/0017-p0-11-role-based-workflow-demo.md)（多角色协同闭环范围收敛）。

### v2 演进设计文档（后 MVP · Stage A 已落地类型 + 契约测试）

> 详见 [`08_roadmap.md`](08_roadmap.md) §8.4「v2 架构演进路线」。**命名约定**：Roadmap 的 **Phase** = 产品演进时间线；工程方案的 **Stage** = 代码迁移步骤（Phase 1 内部分 Stage A-D）。当前 **Stage A**（类型 + 契约测试）已在工作区落地，未接入 pipeline；Stage B（BehaviorState 接入）/ Stage C（RiskSignal 链路）/ Stage D（灰度开启）为后续迁移步骤。产品 Phase 2（证据链）/ Phase 4（身份系统化）为后续演进阶段。

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| DESIGN-RR | `DESIGN-realtime-riskstream-engineering-plan.md` | **实时风险状态流工程落地方案**：把 ADR-0021 落为文件清单 / 每帧执行顺序 / 状态机规范 / 测试矩阵 / Migration Plan（State→Signal→Observe→Decision→Memory→Agent） |
| ADR-0018 | [`ADR/0018-separate-realtime-risk-signal-and-historical-visitor-event.md`](ADR/0018-separate-realtime-risk-signal-and-historical-visitor-event.md) | **方向**：实时风险信号与历史事件流分离（新增 Behavior State 双下游） |
| ADR-0019 | [`ADR/0019-multimodal-evidence-fusion-architecture.md`](ADR/0019-multimodal-evidence-fusion-architecture.md) | **方向**：多模态证据融合架构（Vision / Audio 双独立感知链 + Evidence Fusion） |
| ADR-0020 | [`ADR/0020-decouple-short-term-tracking-identity-and-long-term-visitor-identity.md`](ADR/0020-decouple-short-term-tracking-identity-and-long-term-visitor-identity.md) | **方向**：短期追踪身份（track_id）与长期访客身份（person_id）分离 |
| ADR-0021 | [`ADR/0021-realtime-riskstream-concrete-design.md`](ADR/0021-realtime-riskstream-concrete-design.md) | **具体设计（Phase 1）**：Reality→State→Signal→Decision 四层 + `BehaviorState`/`RiskSignal`/`RealTimeRiskEvaluator`/adapter |
| ADR-0022 | [`ADR/0022-evidence-chain-multimodal-interface.md`](ADR/0022-evidence-chain-multimodal-interface.md) | **具体设计（Phase 2）**：`EvidenceItem`(类型化证据) + `EvidenceAggregator`(只整理不重推) + `WarningEvent.evidence_items` |
| ADR-0023 | [`ADR/0023-identity-continuity-system.md`](ADR/0023-identity-continuity-system.md) | **具体设计（Phase 4）**：track_id/visitor_instance_id/person_identity_id 三层 + `IdentityResolver`（v1 不冒充真实身份） |
| ADR-0024 | [`ADR/0024-memory-architecture.md`](ADR/0024-memory-architecture.md) | **具体设计（Phase 4-5）**：三类记忆模型（Short-term / Episodic / Semantic）+ Memory Policy 转换边界 + Episode Builder + 不变量 I1–I4 + Trust Layer + Snapshot 原则；Slices 1–6 + Stage F + Integration Closure（B/C/A/D）已合入 main；详见 `08_roadmap.md` §8.5 |
| ADR-0025 | [`ADR/0025-memory-consumer-architecture.md`](ADR/0025-memory-consumer-architecture.md) | **Memory Consumer 架构**：Retrieval/Aggregation/Context Builder/Reasoning Interface 四组件 + 单向管道 + 三数据契约；不直接决策、不改 Risk Score（Accepted） |
| ADR-0026 | [`ADR/0026-audio-perception-chain-concrete-design.md`](ADR/0026-audio-perception-chain-concrete-design.md) | **音频感知链路具体设计**：独立 Audio 管道 + Tier0 VAD/Prosody + Tier1 YAMNet + AudioPerceptionEvent(5 类) + AudioAdapter；配套见 [`design/audio/`](design/audio/)（Proposed review-ready） |
| ADR-0027 | [`ADR/0027-audio-memory-integration.md`](ADR/0027-audio-memory-integration.md) | **音频记忆集成**：音频经统一决策门汇入 Memory，不新增写入链；D1~D9 含 modalities 标记 / 统一 EvidenceItem / CrossModalLink / EvidenceModality 枚举契约（Proposed review-ready） |
| ADR-0028 | [`ADR/0028-cross-modal-runtime-wiring.md`](ADR/0028-cross-modal-runtime-wiring.md) | **跨模态运行时接线**：MemoryHook 触发 CrossModalLinker 自动建边；D1~D6 含 same_device + time_overlap 判定（Proposed review-ready） |
| ADR-0029 | [`ADR/0029-cross-modal-memory-retrieval-explanation.md`](ADR/0029-cross-modal-memory-retrieval-explanation.md) | **跨模态记忆检索与解释**：纯只读检索 + 解释层，非判断；`get_links_for_episode` + `ExplanationRenderer` + `ReasoningInput.cross_modal_contexts`（Proposed review-ready） |
| ADR-0030 | [`ADR/0030-decision-boundary-contract.md`](ADR/0030-decision-boundary-contract.md) | **决策边界契约**：`DecisionInput` 唯一收敛载体 + C1–C7 不变式 + Memory→Decision 受控验证门（Proposed review-ready） |
| ADR-0031 | [`ADR/0031-decision-audit-trace-contract.md`](ADR/0031-decision-audit-trace-contract.md) | **决策审计血缘契约**：`outcome` WARN\|SUPPRESS + 五具名 Bundle + `DecisionTraceRecorder` + `DecisionABRun` 双轨载体 + T1–T8 不变式（Proposed review-ready） |
| ADR-0032 | [`ADR/0032-scenario-simulation-layer.md`](ADR/0032-scenario-simulation-layer.md) | **场景仿真层**：声明式 `Scenario` schema → 两通道生成（detections + frames）；`ScenarioCompiler`/`ScenarioRunner`/`ScenarioValidator` 三组件预拆（Proposed） |
| ADR-0033 | [`ADR/0033-benchmark-harness.md`](ADR/0033-benchmark-harness.md) | **基准测试框架**：消费 ADR-0032 仿真场景做 pipeline 行为回归；场景级混淆矩阵 + `BenchmarkExpectation` + `harness_fingerprint`（Accepted） |
| ADR-0034 | [`ADR/0034-scenario-integration-validation.md`](ADR/0034-scenario-integration-validation.md) | **闭环集成验证**：`Scenario→Runtime→Memory→Decision→Notification` 端到端契约验证；失败模型 F1–F6 + `IntegrationRunner`/`IntegrationValidator` + 分层指纹（Accepted v1.0 冻结） |
| ADR-0035 | [`ADR/0035-runtime-evidence-explorer.md`](ADR/0035-runtime-evidence-explorer.md) | **运行证据探索器**：把可信 artifact 转为人类可理解时间轴/因果链/关系图；`visualizer/` 展示层 + `EvidenceProjection` + 四视图（Proposed owner review） |
| ADR-0036 | [`ADR/0036-unified-case-viewer.md`](ADR/0036-unified-case-viewer.md) | **统一 Case Viewer**：四块资产经三类 Adapter 收敛到唯一 `EvidenceProjection`；VM-1~VM-13 + D-CaseVideo + D-Audio + D-MediaMode（Accepted） |

### Memory 模块文档（Integration Closure 产出）

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| Memory 架构说明 | [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) | 模块地图 + runtime 接线图 + 访客生命周期 + 接线契约（门控/输入/输出/失败隔离）+ 领域对象 + Decision–Memory 边界守护 |
| Memory 运维手册 | [`MEMORY_OPERATION_GUIDE.md`](MEMORY_OPERATION_GUIDE.md) | 开关（memory.enabled / episodic_shadow）/ 冷启动恢复 / 失败隔离语义 / 已知限制 / 边界守护 |
| Memory 测试报告 | [`MEMORY_TEST_REPORT.md`](MEMORY_TEST_REPORT.md) | 测试矩阵（模块内 + E2E 4 类 + Slice B + Slice C）+ 回放稳定性 + 信息损失评估 + Product Closure 验收样例 |
| Memory 集成收口设计 | [`DESIGN-memory-integration-closure.md`](DESIGN-memory-integration-closure.md) | System × Memory 外部闭环设计（B→C→A→D）+ `compose_context` V0 边界冻结 + 历史差距表 |
| 未来 Observation 契约 | [`DESIGN-observation-contract.md`](DESIGN-observation-contract.md) | 模态无关 `Observation` 协议 + Multimodal Evidence Fusion 接入（本阶段不改代码） |
| Memory Pipeline 工程方案 | [`design/memory/DESIGN-memory-pipeline.md`](design/memory/DESIGN-memory-pipeline.md) | ADR-0024 工程落地：Stage A–H 拆分 + 文件清单 + 每帧执行顺序（被 ADR-0024 + README 引用） |
| Memory Consumer 工程方案 | [`design/memory/DESIGN-memory-consumer.md`](design/memory/DESIGN-memory-consumer.md) | ADR-0025 工程落地：Retrieval → Aggregation → Context Builder → Reasoning 拆分 Slices（C-0~C-6 已合） |
| Memory Value Evaluation | [`design/memory/DESIGN-memory-evaluation.md`](design/memory/DESIGN-memory-evaluation.md) | E-1 Memory 价值评估设计：A/B 对照 + 四指标 + Memory Value Score（Implementation Ready） |
| Memory Replay Dataset | [`design/memory/DESIGN-memory-replay-dataset.md`](design/memory/DESIGN-memory-replay-dataset.md) | Memory Consumer 验证数据集：case 语义 + fixture 结构 + 回放执行（M0 先行） |

### 音频感知文档（v2 · Phase 3，配套 ADR-0026）

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| 音频技术栈调研 | [`design/audio/audio_stack_survey.md`](design/audio/audio_stack_survey.md) | 候选技术 / 为什么选这个 / Spike 结论如何落地（ADR-0026 选型附录） |
| 音频 Spike 实证报告 | [`design/audio/audio_spike_report.md`](design/audio/audio_spike_report.md) | Spike #1~#5 实测：环境 / 命令 / 数据 / 结论（CCTV 无音轨实证） |
| 音频 Fixture 生成基础设施 | [`design/audio/audio_fixture_generation.md`](design/audio/audio_fixture_generation.md) | TTS 生成可控音频 fixture：CI 可测、可复现、零硬件依赖（Proposed） |

### Golden Case 产品化文档（v2 Demo · 配套 ADR-0034/0035/0036）

> 详见 [`design/golden-case/`](design/golden-case/)。该文档族形成自包含引用闭包，配套 ADR-0034（闭环集成验证）/ ADR-0035（运行证据探索器）/ ADR-0036（统一 Case Viewer）。

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| Demo v2 产品恢复总蓝图 | [`design/golden-case/DESIGN-demo-v2-product-restore.md`](design/golden-case/DESIGN-demo-v2-product-restore.md) | 第二代 Demo 产品能力恢复方案（评委三问 Q3 闭环能力恢复） |
| Golden Scenario Set | [`design/golden-case/DESIGN-golden-scenario-set.md`](design/golden-case/DESIGN-golden-scenario-set.md) | 黄金案例集数据准备 v2（六 Case 命题 + ambiguous 三幕） |
| Golden Case Viewer 设计 | [`design/golden-case/DESIGN-golden-case-viewer.md`](design/golden-case/DESIGN-golden-case-viewer.md) | 黄金案例展示层设计 v2（组件级） |
| Case Viewer 产品化总原则 | [`design/golden-case/DESIGN-case-viewer-productization.md`](design/golden-case/DESIGN-case-viewer-productization.md) | Case 呈现模型总原则（冻结，Owner 2026-08-16 评审确认） |
| Golden Case 接 Live 设计 | [`design/golden-case/DESIGN-golden-case-live-product.md`](design/golden-case/DESIGN-golden-case-live-product.md) | Golden Case 接 Live 产品化 UX 设计（v0.1，设计中） |
| Live 产品 UI 恢复设计 | [`design/golden-case/DESIGN-live-product-ui-restore.md`](design/golden-case/DESIGN-live-product-ui-restore.md) | Live 产品 UI 完整恢复（6 区域 + 阶段叙事 tabs + toast） |
| Golden Cases 接入清单 | [`design/golden-case/GOLDEN-CASES-USAGE.md`](design/golden-case/GOLDEN-CASES-USAGE.md) | 黄金案例资产接入清单（G0-1 完成，G0-3 进行中） |
| ADR-0036 补遗 | [`design/golden-case/ADR-0036-supplement-golden-case-adapter.md`](design/golden-case/ADR-0036-supplement-golden-case-adapter.md) | ADR-0036 补遗：Golden Case 接入 Live 架构决策（v0.1 待 Owner 评审） |
| Golden Case 数据诊断 | [`design/golden-case/DIAGNOSIS-golden-case-data-validity.md`](design/golden-case/DIAGNOSIS-golden-case-data-validity.md) | Golden Case 数据有效性诊断（2026-08-17 实测两 scenario） |
| Golden Case 接 Live 任务清单 | [`tasks/TASKS-golden-case-live-product.md`](tasks/TASKS-golden-case-live-product.md) | Golden Case 接 Live 任务清单（Phase 1 T1.1 已落地，Phase 1.5+ 待执行） |

### 工程治理与运维

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| 技术债台账 | [`TECH-DEBT.md`](TECH-DEBT.md) | TD-NNNN 编号追踪技术债（被 ADR-0024 引用，活跃台账） |
| Git 可靠性 SOP | [`ops/GIT_RELIABILITY.md`](ops/GIT_RELIABILITY.md) | Git 可靠性与沙箱约束 SOP（2026-07-31 落地） |

### 验证报告（ADR 验证留痕）

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| P0 集成验证报告 | [`reports/P0-integration-validation.md`](reports/P0-integration-validation.md) | P0 集成验证系统级端到端验收（274 测试全绿，被 ADR-0012 引用） |
| ADR-0021 验证报告 | [`reports/ADR-0021-validation-report.md`](reports/ADR-0021-validation-report.md) | ADR-0021 实时风险状态流 Stage A-E 验证（被 TECH-DEBT 引用） |
| ADR-0026 YAMNet 验证报告 | [`reports/ADR-0026-yamnet-real-weight-validation.md`](reports/ADR-0026-yamnet-real-weight-validation.md) | ADR-0026 YAMNet 真实权重接入验证（四要素确认，被 ADR-0026 引用） |

### 实现计划（非冻结件，配套 ADR）

| 文档 | 文件 | 职责 |
| --- | --- | --- |
| ADR-0034 实现计划 | [`design/0034-implementation-plan.md`](design/0034-implementation-plan.md) | ADR-0034 类型草案 / 注入伪码 / 分阶段 MUST 清单 / 测试编号明细（非冻结件，随实现演进） |
| ADR-0035 D3 实现计划 | [`design/0035-d3-implementation-plan.md`](design/0035-d3-implementation-plan.md) | ADR-0035 D3 Evidence Story Compiler 落地实现设计 v7（Owner Decision Record，非冻结件） |

### 开发手册（跨项目原则沉淀）

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| PLAYBOOK | `PLAYBOOK-silver-shield-development.md` | 从本项目提炼、可复用于所有复杂 AI 系统的开发原则（被验证正确 / 验证阶段新增 / 被证伪 / 七阶段流程），含真实证据 |
| PLAYBOOK-G | `PLAYBOOK-generic-ai-system-development.md` | 通用模板版：去除 SilverShield 专有名词，纯原则，可原样套用到任何「感知 → 判断 → 行动」类 AI 系统 |

## 设计依据

本模块设计对齐团队《银龄盾 架构设计完善版（V2.0）》以及《IRMS 工程定稿版》，核心约束摘录：

- **边界**：本模块只输出"标签/事件"（普通来访 / 待核验来访 / 异常停留 / 重复来访 / 高风险接近），
  **不直接输出"诈骗人员"结论**；是否诈骗由中心结合入户语音、物品、历史记录综合分析。
- **归属**：网络工程同学负责萤石平台接入、设备接入、视频/事件流、部署、安全与**门前采集**（即本模块）。
- **引擎分层**：风险引擎为 **Rule + ML 两层**，LLM 解释推迟到第二版；本模块负责 Rule + ML 侧的
  门前信号抽取与评分输入，**不负责 LLM 解释**。
- **隐私**：范围仅覆盖自家门前，敏感区域遮挡，高风险才存片段，片段设自动删除期。

## 与 `.doc` 设计稿的关系

`../.doc/设计思路研究/` 下为比赛前期的多版思路草稿（九层/八层/四层体系、IRMS、三方博弈、六维机理等）。
其中 `银龄盾_老年诈骗风险数字孪生系统_架构设计完善版(1).docx` 为**统一终稿**，本目录所有结论以该终稿为准；
其余草稿仅作理论背景参考（如"诈骗成功方程式-六维机理"支撑门前信号选取，"三方博弈"支撑协同升级策略）。

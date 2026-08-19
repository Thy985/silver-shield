# 03 · 目录结构（Repository Layout）

> **新人第一站**：先看本文档了解仓库结构，再看 [`API_REFERENCE.md`](API_REFERENCE.md)（接口入口）与 [`CONTRACTS.md`](CONTRACTS.md)（冻结契约）。
> 本文档描述 `origin/main` 的当前结构（含 MVP RC + v2 增量模块）。v2 增量标记 `[v2]`，Stage A 类型标记 `[Stage A]`（已合入 main，未接入 pipeline）。

## 顶层结构

```
silver-shield/
├── README.md                  # 项目入口 + 架构图 + "团队第一入口"链接
├── AGENTS.md                  # AI 协作开发规范（所有协作者强制）
├── pyproject.toml             # 包元数据 + ruff/pytest 配置
├── requirements.txt           # 运行时依赖
├── requirements-dev.txt       # 开发/测试依赖
├── .gitignore                 # 忽略 .env / .workbuddy / .doc / 模型权重 / 证据
├── .env.example               # 环境变量模板（凭证占位，真实值不入库）
├── .pre-commit-config.yaml    # 提交前 lint/format
├── Dockerfile                 # 边缘部署镜像
├── docker-compose.yml         # 本地 MQTT + 模块一键起
├── config/                    # 可调参数与设备注册（改配置不改代码）
├── src/                       # 源码（home_perception 主包 + silver_demo Demo 网关）
├── tests/                     # 单元测试 + 契约测试（159 文件，2295 用例）
├── scripts/                   # 运行 / Demo 脚本
├── benchmark/                 # 性能基准
├── docs/                      # 文档体系（00-09 + ADR + design/reports/tasks/ops）
├── data/                      # 本地运行数据（models/evidence/cache gitignore；demo/ 验证报告入库）
├── silver-engineering-assets/ # 工程资产库（母项目沉淀，可复制能力）
├── artifacts/                 # 运行产物（trace/report，gitignore）
├── generated/                 # 生成产物（gitignore）
├── out/                       # 输出产物（gitignore）
├── reports/                   # 报告产物（gitignore）
├── scenarios/                 # 场景资产
└── var/                       # 运行时变量数据（gitignore）
```

## `src/home_perception` 包结构

```
home_perception/
├── main.py              # CLI 入口：调用 runtime.from_settings 装配并运行
├── core/                # 中枢契约：配置、事件枚举、工具（被所有层依赖，不依赖业务）
│   ├── config.py        #   Settings 加载 + ${ENV} 展开 + pydantic 校验（含 AudioConfig/MemoryConfig/RealtimeRiskConfig/RuntimeConfig 子配置类）
│   └── event.py         #   EventType 枚举 + EvidenceRef（PerceptionEvent 唯一权威在 analysis/perception.py）
├── ingestion/           # 取流与帧源（唯一允许持有流地址/凭证逻辑的地方）
│   ├── frame_source.py  #   FrameSource(ABC) + CaviarFrameSource（比赛 Demo 实现）
│   └── ezviz_client.py  #   EZVIZ token/流地址获取
├── detection/           # 目标检测与跟踪
│   ├── detector.py      #   Detector(ABC) + YOLODetector
│   ├── tracker.py       #   Tracker（多目标跟踪，输出 track_id）
│   └── schemas.py       #   DetectionResult 等数据结构
├── analysis/            # 领域推理层（见"三层语义"）
│   ├── event.py         #   VisitorEvent（事实层：进入/离开/停留）
│   ├── event_builder.py #   DetectionResult → VisitorTrack → VisitorEvent
│   ├── feature.py       #   RiskFeature 数值特征
│   ├── feature_extractor.py # FeatureExtractor：VisitorEvent → RiskFeature
│   ├── perception.py    #   PerceptionEvent（规则发现的异常感知事件，唯一权威）
│   ├── rule.py          #   Rule 抽象基类 + CompositeRule + RuleResult/RuleContext（可组合规则）
│   ├── cooldown.py      #   CooldownGate 状态机（防同 visitor+rule 短时重复触发，被 rule_engine 使用）
│   ├── rule_engine.py   #   RuleEngine：编排 4 基础 Rule + 1 复合 + CooldownGate
│   ├── warning.py       #   WarningEvent（决策层状态机）
│   ├── decision_engine.py   # DecisionEngine：PerceptionEvent → WarningEvent
│   ├── decision_policy.py   # DecisionPolicy（可替换决策策略）
│   ├── behavior_state.py        # [Stage A] BehaviorState + RealtimeContext（ADR-0021 State Layer，已合入 main，未接入 pipeline）
│   ├── recent_behavior_store.py # [Stage A] RecentBehaviorStore：跨访问近期行为账本
│   ├── risk_signal.py           # [Stage A] RiskSignal + 4 枚举（SignalCategory/SourceModality/SignalTransition/SubjectType）
│   ├── behavior_builder.py      # [v2] BehaviorBuilder：Reality → BehaviorState 构造
│   ├── realtime_risk_evaluator.py # [v2] RealTimeRiskEvaluator：BehaviorState → RiskSignal 评估
│   ├── signal_adapter.py        # [v2] SignalAdapter：RiskSignal → DecisionPolicy 适配
│   ├── decision_trace.py        # [v2] DecisionTrace Recorder（ADR-0031 决策审计血缘）
│   ├── decision_sink.py         # [v2] ActionSink（ADR-0034 闭环探针）
│   └── decision_contract.py     # [v2] DecisionInput/DecisionTrace 等契约类型（ADR-0030/0031）
├── evidence/            # 风险证据采集（副作用）
│   ├── clip_collector.py #  触发式快照/片段采集
│   └── storage.py       #  本地/COS 存储
├── output/              # 事件上报（副作用）
│   ├── publisher.py     #   Publisher(ABC) + MQTTPublisher：上报感知事件到中心
│   └── schemas.py       #  事件 schema 再导出
├── action/              # 行动层（副作用执行）
│   ├── executor.py      #   ActionExecutor：WarningEvent → List[ActionCommand]
│   ├── command.py       #   ActionCommand（行动指令，含幂等 id）
│   ├── dispatcher.py    #   ActionDispatcher：分发执行
│   ├── publisher.py     #   MQTTPublisher（下发社区工单等）
│   ├── notifier.py      #   通知适配器（家属通知）
│   └── sink.py          # [v2] ActionSink（ADR-0034 闭环验证探针，可选注入）
├── runtime/             # 装配层（Assembly）
│   ├── pipeline.py      #   PerceptionPipeline.from_settings()：唯一装配入口
│   ├── config.py        #   运行时 Settings 组装
│   ├── lifecycle.py     #   DemoClock / run_demo（演示与生命周期）
│   ├── memory_hook.py   # [v2] MemoryHook：Memory Shadow Mode 接线（ADR-0024 Stage F）
│   ├── memory_consumer_hook.py # [v2] MemoryConsumerHook：Consumer 反哺理解接线（ADR-0025）
│   ├── audio_session_recorder.py # [v2] AudioSessionRecorder：音频会话记录
│   └── observability.py # [v2] Observability：运行时可观测探针
├── audio/               # [v2] 音频感知链路（ADR-0026，独立 Audio 管道）
│   ├── source.py        #   AudioSource(ABC) + FileAudioSource（可插拔音源）
│   ├── vad.py           #   VadBackend(ABC) + EnergyVadBackend / WebRtcVadBackend（Tier0 VAD）
│   ├── tagging.py       #   AcousticTagger(ABC) + YamNetTagger（Tier1 声学标签）
│   ├── features.py      #   AudioFeatures + AudioFeatureExtractor
│   ├── detector.py      #   AudioDetector：音频检测编排
│   ├── rule.py          #   AudioRule + RuleThresholds（音频规则）
│   ├── pipeline.py      #   AudioPipeline：音频感知流水线
│   ├── event.py         #   AudioSegmentEvent / AudioPerceptionEvent / AudioPerceptionKind(5 类)
│   └── tts/             #   TTS fixture 生成基础设施（ADR-0026 配套）
├── memory/              # [v2] 记忆系统（ADR-0024/0025，三类记忆 + Consumer 反哺）
│   ├── store.py         #   MemoryStore(ABC) + InMemoryStore
│   ├── policy.py        #   MemoryPolicy(ABC)
│   ├── episode_builder.py # DefaultEpisodeBuilder
│   ├── records.py       #   ShortTermRecord / EpisodicRecord / SemanticAggregate
│   ├── query.py         #   MemoryQuery + compose_context
│   ├── snapshot.py      #   RuntimeSnapshot / SnapshotStore
│   ├── cold_start.py    #   ColdStartCoordinator（冷启动恢复）
│   ├── cross_modal_link.py # CrossModalLink / CrossModalLinker / CrossModalLinkStore（ADR-0028）
│   ├── cross_modal_runtime.py # CrossModalLinkRuntime（运行时接线）
│   ├── cross_modal_explainer.py # CrossModalRetrieval / CrossModalExplainer / ExplanationRenderer（ADR-0029）
│   ├── short_term_policy.py # DefaultShortTermPolicy
│   ├── consumer/        #   Memory Consumer Layer（ADR-0025：Retrieval/Aggregation/ContextBuilder/Reasoning）
│   │   ├── interfaces.py    # Retrieval/Aggregation/ContextBuilder/MemoryConsumer/ReasoningEngine (ABC)
│   │   ├── retrieval.py     # RuleBasedRetrieval
│   │   ├── aggregation.py   # RuleBasedAggregation
│   │   ├── context.py       # RuleBasedContextBuilder
│   │   ├── reasoning.py     # RuleBasedReasoningEngine
│   │   ├── orchestrator.py  # RuleBasedMemoryConsumer
│   │   ├── contracts.py     # ReasoningInput / ReasoningResult / VisitorProfile / RiskPattern
│   │   ├── replay_dataset.py # MemoryReplayDataset（验证数据集）
│   │   └── replay_layer.py  # EpisodeReplayLayer（回放层）
│   └── evaluation/      #   Memory Value Evaluation（E-1，ADR-0025 配套）
├── validation/          # [v2] 场景仿真层（ADR-0032，声明式 Scenario → 两通道生成）
│   ├── scenario.py      #   Scenario / ActorSpec / EnvironmentSpec / CameraSpec（pydantic schema）
│   ├── compiler.py      #   ScenarioCompiler：YAML → Scenario 对象
│   ├── runner.py        #   ScenarioRunner / ScenarioValidator
│   ├── synthetic_input.py # SyntheticInput（合成输入）
│   ├── generator.py     #   场景生成器（detections + frames 两通道）
│   ├── contracts.py     #   BenchmarkExpectation / PerceptionExpectation / IntegrationExpectationSuite
│   ├── demo_adapter.py  #   SyntheticFrameSource
│   ├── fixtures/        #   场景 fixture（scenarios/{perception,regression,benchmark}/）
│   ├── runner/          #   运行器子包
│   ├── scenario/        #   场景定义子包
│   └── simulation/      #   仿真子包
├── evaluation/          # [v2] 基准测试框架（ADR-0033，感知级场景回归打分）
│   ├── harness.py       #   BenchmarkHarness：场景 + pipeline → 回归
│   ├── metrics.py       #   ScenarioScore（混淆矩阵 TP/FN/FP/TN）
│   ├── report.py        #   BenchmarkReport
│   ├── gate.py          #   BenchmarkScore / GateResult / ThresholdCheck
│   ├── ab_runner.py     #   BenchmarkABRun（A/B 对比 + 守恒断言）
│   ├── schema.py        #   评估 schema
│   └── fingerprint_fields.py # 指纹字段定义
├── integration/         # [v2] 闭环集成验证（ADR-0034，完整 Runtime 链路端到端）
│   ├── runner.py        #   IntegrationRunner：Scenario → Runtime 全链路执行
│   ├── validator.py     #   IntegrationValidator：逐阶段 AND + F1–F6 失败归类
│   ├── report.py        #   IntegrationReport
│   ├── context.py       #   IntegrationContext（探针唯一容器）
│   ├── gate.py          #   IntegrationGateResult / StageVerdict
│   ├── fingerprint.py   #   闭环指纹（expectation + loop）
│   ├── audio_adapter.py #   AudioAdapter（多模态纯 Adapter）
│   └── contracts/       #   契约子包
├── visualizer/          # [v2] 运行证据探索器（ADR-0035/0036，展示层，不参与运行/判断）
│   ├── loader.py        #   EvidenceProjection loader（唯一入口）
│   ├── evidence.py      #   EvidenceProjection / TimelineNode / ScenarioEvidence（TypedDict）
│   ├── graph.py         #   EvidenceGraph / EvidenceGraphNode / Edge
│   ├── compiler.py      #   CaseVideoResult（D3 Evidence Story Compiler）
│   ├── spec.py          #   CaseVideoSpec
│   ├── schema/          #   自建 TypedDict（类型安全，禁 import 生产类）
│   ├── viewer/          #   视图子包（Timeline / Decision Trace / Graph）
│   ├── video/           #   程序化视频子包（D3）
│   └── assets/          #   静态资源（字体等）
└── common/              # 横切能力
    ├── logging.py       #   structlog 配置
    └── timeutil.py      #   时间戳工具
```

## `src/silver_demo` 包结构（Demo 网关）

> P0-11 多角色协同闭环 Demo 的后端网关（FastAPI + WebSocket），展示层零穿透冻结契约。

```
silver_demo/
├── gateway.py           # FastAPI 网关入口
├── ws.py                # WebSocket 推送（实时状态）
├── bridge.py            # Demo ↔ Perception 桥接
├── state.py             # DemoAggregateState（三视图共享状态）
├── config.py            # Demo 配置
├── scenarios.py         # Demo 场景定义
├── sources.py           # Demo 数据源
├── golden_adapter.py    # Golden Case 适配器（ADR-0036）
├── golden_evidence.py   # Golden Case 证据
└── golden_evidence_injector.py # Golden Case 证据注入
```

## 三层语义（避免职责错位）

| 层 | 角色 | 典型误用（禁止） |
|----|------|----------------|
| `runtime/` | **装配层**：用 `from_settings()` 把各层连成流水线；不写业务逻辑 | 在 Pipeline 里直接调 RuleEngine 做决策、绕过装配 |
| `analysis/` | **领域推理层**：事实 → 特征 → 异常事件 → 告警事件；纯函数/规则，无副作用 | 在 RuleEngine 里发 MQTT / 调外部服务 / 产生最终判定 |
| `action/` | **副作用执行层**：把 WarningEvent 变成对外行动（MQTT/通知/工单） | 把 MQTT 写进 RuleEngine；把决策逻辑放进 Executor |

数据流向（自上而下，禁止反向依赖）：

```
ingestion → detection → analysis → evidence/output → action → runtime(组装)
core  被所有层依赖，不依赖业务
common 横切
[v2] audio → analysis(适配) / memory → consumer → reasoning(Shadow)
[v2] validation → evaluation → integration（验证基础设施，不接生产 pipeline）
[v2] visualizer（展示层，只读 artifact，零 import 生产决策）
```

## `tests/` 结构

```
tests/
├── conftest.py            # 公共 fixture
├── test_*.py              # 顶层单元/集成测试（20 文件：config/event/feature/detector/tracker/benchmark/runtime/warning 等）
├── analysis/              # analysis 单元测试（behavior_state/recent_behavior_store/decision_trace 等）
├── contract/              # 契约测试（冻结门禁，攻击性测试）：schema / interface / state-machine / input-attack / config
├── runtime/               # runtime 单元测试（pipeline/memory_hook/audio_session 等）
├── memory/                # [v2] Memory 单元 + E2E 测试（store/episode/consumer/cross_modal/cold_start）
├── evaluation/            # [v2] Evaluation 单元测试（harness/metrics/gate/ab_runner）
├── integration/           # [v2] Integration 单元测试（runner/validator/gate/fingerprint）
├── validation/            # [v2] Validation 单元测试（scenario/compiler/runner/generator）
├── visualizer/            # [v2] Visualizer 单元测试（loader/evidence/graph/compiler）
├── demo/                  # Demo 网关测试（gateway/ws/state/bridge）
├── silver_demo/           # silver_demo 包测试
└── fixtures/              # 测试数据（CAVIAR 真实场景 + memory_replay + audio）+ 下载脚本
```

## 被忽略、不应入库的目录

以下目录/文件已被 `.gitignore` 排除，**禁止 `git add -f`**：

- `.env` / `config/devices.yaml` / `prototypes/`：密钥与真实凭证
- `.doc/`：团队研究草稿（终稿已沉淀到 `docs/`）
- `.workbuddy/`：本地 Agent 工作记忆（仅本地，不共享）
- `data/models/*.pt`：模型权重；`data/evidence/**`：取证片段
- `artifacts/` / `generated/` / `out/` / `reports/` / `var/`：运行/构建产物
- `__pycache__/`、`.venv/`、`.pytest_cache/`、构建产物

## 各目录职责一句话

- `config/`：一切可调参数与设备注册，"改配置不改代码"。
- `core/`：模块的中枢契约（配置、事件枚举、工具），稳定且少变。
- `ingestion/`：与外部视频源打交道，唯一允许持有流地址/凭证逻辑。
- `detection/` / `analysis/`：算法与规则，迭代最快、最可能替换实现。
- `evidence/` / `output/` / `action/`：对外副作用（落盘、上报、行动），便于测试时替换为 fake。
- `runtime/`：装配入口，稳定契约，新成员接入从 `PerceptionPipeline.from_settings()` 开始。
- `audio/` [v2]：音频感知独立链路（VAD + 声学标签 + 事件），不穿透视觉管道。
- `memory/` [v2]：三类记忆 + Consumer 反哺理解，只读检索 + 解释，不决策、不改 Risk Score。
- `validation/` [v2]：声明式场景仿真，零模型可复现，验证基础设施上游。
- `evaluation/` [v2]：感知级场景回归打分，混淆矩阵 + 门禁。
- `integration/` [v2]：完整闭环端到端验证，F1–F6 失败模型 + 探针 + 闭环指纹。
- `visualizer/` [v2]：运行证据展示层，artifact → 时间轴/因果链/关系图，不参与运行/判断。
- `silver_demo/`：Demo 网关（FastAPI + WS），展示层零穿透冻结契约。
- `tests/`：契约与规则单测，保证"改算法不破坏对外事件格式"（159 文件，2295 用例）。
- `scripts/` / `benchmark/`：运行 / Demo / 性能基准，非核心链路。

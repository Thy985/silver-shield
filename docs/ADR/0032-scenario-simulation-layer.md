# ADR-0032: 场景仿真层 · 程序化视频 / 场景生成（Scenario Simulation Layer）

- **Status**: Proposed（Owner 评审中；冻结后修订权属 Owner）
- **Date**: 2026-08-08
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0028（跨模态运行时接线·D6「Synthetic Episode Fixture」明确把"程序化视频/合成视频生成"划出本 ADR 范围、归 Perception Validation Infrastructure——即本 ADR）
  - ADR-0031（决策审计血缘契约·本 ADR 产出的场景是 Slice C / ADR-0033 Benchmark Harness 的可复现输入源）
  - ADR-0033（Benchmark Harness·消费本 ADR 产出的场景对 pipeline 打分——本 ADR 不负责评分）
  - ADR-0014（三级冻结治理·L2 Interface 可替换帧源 / 检测器接缝）
  - AGENTS.md §3（模块边界铁律·仅产标签事件、不产 fraud/suspect）/ §6.3（架构决策文件 Owner 专属）
- **Phase**: v2 · Phase 3 → 决策边界契约（ADR-0030）→ 决策审计血缘（ADR-0031）→ **场景仿真层（Perception Validation Infrastructure）** → Benchmark Harness（ADR-0033）

---

## 0. 背景与动机（Context）

> **引用约定（可维护性）**：本 ADR 引用代码以**符号名 + 简短引文**为准（行号会漂移，仅作辅助定位）。文中行号均截至本 ADR 起草时（`main` 含 ADR-0031 Slice E，commit `f268597`）。若行号与实际不符，以符号名与引文为准。

SilverShield 的 Home 感知模块需要**可复现、隐私安全、确定性**的方式验证整条感知链路（`frame → detector → tracker → VisitorEvent → FeatureExtractor → RuleEngine → PerceptionEvent → DecisionEngine → WarningEvent`）。今天这件事做不到，原因有三层：

### 0.1 真实素材不可用：隐私 + 不可复现

家庭入口视频含人脸、室内布局、可能的对话片段，受 AGENTS.md §3.3「视频帧 / 人脸默认不离开 Home 端」与 §6.1「默认不存全量视频」约束——**真实素材绝不能入库**，且外部 `data/demo/` 不入版本控制（`silver_demo` 的 `ScenarioConfig.media_path` 指向本地未入库素材）。结果是：任何依赖真实视频的回归测试都**不可复现、不可 CI、不可被他人复跑**。

### 0.2 现有两条合成路径都"半截"，且未打通

| 路径 | 现状 | 缺口 |
| --- | --- | --- |
| **(A) 声明式 YAML → 直注 Memory Graph** | ADR-0028 D6 的 fixture 是声明式 episode 数据，翻译器只存在于 `tests/memory/test_cross_modal_runtime.py`（`_scenario_to_episodes` 等），**绕过全部感知层** | 只验证 Memory，不验证"帧 → 事件"链路；翻译器在测试里、无 `src` 级 generator |
| **(B) 声明式 `ScenarioConfig` → `build_frame_source` → 真实 MP4/CAVIAR jpg → `process_frame`** | `silver_demo/scenarios.py` + `sources.py` 已支持，但 `media_path` 指向**外部真实素材** | 依赖未入库素材，CI 不可跑；且无"程序化生成素材"的能力 |

两条路径之间有一个**结构性空白**：没有任何组件能从「声明式场景描述」生成**带语义**（人 / 轨迹 / 时序）的视频帧或 `DetectionResult` 序列。现有的最像样雏形是：

- `tests/runtime/_closed_loop_helpers.py` 的 `CachedDetectionDetector` + `tests/fixtures/detections/stranger_visit_short.detections.json`——手工录制的**合成检测回放**，文件头诚实声明为合成 schema fixture；
- `tests/demo/test_sources.py:36` 的 `_make_synthetic_mp4()`（`cv2.VideoWriter` + `np.zeros` 纯色帧）——**只有颜色、没有语义**，仅验证解码路径。

二者都是"测试内联 hack"，没有 schema、没有确定性保证、没有"期望结果（ground truth）"声明，无法支撑 ADR-0033 的系统性打分。

### 0.3 音频侧已有完整的对称范本，视觉侧却空白

`src/home_perception/audio/tts/` 已经落地了**一模一样的范式**，且验证过可行：

- `generator.py`：`Scenario` / `load_scenarios` / `generate_scenario` / `generate_all`——声明式 YAML → 确定性合成；
- `effects.py`：确定性效果链（`apply_speech_rate` / `volume` / `noise` / `reverb` / `distance`），固定 seed；
- `scenario_runner.py`：`PerceptionScenario` / `synthesize` / `run` / `validate_scenario` / `ValidationResult`——**合成 → 跑 → 对照期望断言**；
- `scenario.yaml` + `scenarios/audio/*.yaml` + `scripts/gen_audio_fixtures.py` / `run_audio_scenarios.py` + `docs/audio_fixture_generation.md`。

这证明"声明式场景 → 确定性合成 → 对照期望校验"在 SilverShield 里是**已被接受且工程可行的模式**。视觉侧的 ADR-0032 应当**对称复制**这一架构，而非另起炉灶。

### 0.4 本 ADR 要解决的，与 ADR-0028 D6 的边界

ADR-0028 D6 的 fixture 直接构造 `EpisodicRecord` 喂 Memory Graph（**绕过感知层**），解决"Memory 层关联"的验证。本 ADR 解决的是**它的前置一层**——"帧 / 检测 → 事件"这一段的验证。二者都是"声明式场景"，但在**不同层**：

```
声明式 Scenario
   ├── ADR-0032（本 ADR）──────→ 生成 frames / RawDetection ──→ 喂整条感知 pipeline（detector→…→WarningEvent）
   └── ADR-0028 D6（既有）──────→ 直注 EpisodicRecord ───────────→ 喂 Memory Graph（绕过感知层）
```

> 正交、不重叠：本 ADR **不**碰 Memory Graph 直接构造；ADR-0028 D6 **不**碰帧/检测生成。后续若需"端到端合成场景 → 同时驱动感知层与 Memory 层"，由 ADR-0033 或单独的编排 ADR 串联二者，本 ADR 只负责"产出感知层输入"。

### 0.5 现状小结

| 验证需求 | 今天能否满足 |
| --- | --- |
| 不依赖真实视频跑通整条感知链路 | **不能**（真实素材不入库） |
| 确定性（同输入 → 同输出，可 CI） | **不能**（现有合成均为测试内联、无 seed/无 schema） |
| 声明 ground truth 并自动校验 | **不能**（无 `expects` / `validate`） |
| 同时支持"结构化场景描述"与"合成视频帧" | **不能**（只有 `detections.json` 回放 或 纯色 mp4，二者皆无语义） |
| 视觉侧对齐音频 `tts/` 成熟范式 | **不能**（视觉侧空白） |

### 0.6 战略定位：本 ADR 是「AI 验证基础设施（AI Validation Infrastructure）」的一环

安全系统最难的两个问题是"**为什么报警**"和"**为什么没报警**"——而模型效果不好时，正确路径不是"找更多数据调模型"，而是"**我能证明系统在某种场景下为什么做出这个决策吗**"。三个 ADR 合起来补的就是这条链：

```
Scenario Simulation (ADR-0032)   ← 本 ADR：事件到底是怎么产生的？（可复现输入）
        |
        v
Perception Pipeline              ← frame → detector → tracker → event → decision
        |
        v
Decision Trace (ADR-0031)        ← 这个决策为什么做 / 为什么没做？（可审计血缘）
        |
        v
Benchmark Harness (ADR-0033)     ← 这个版本到底提升还是下降？（回归/打分）
        |
        v
Regression / Improvement
```

这已经不是普通项目的"测试"，而是在构建一个**面向多模态 / Agent AI 系统的 Evaluation Infrastructure**——与机器人仿真（`World Model → Simulation → Policy → Trace → Benchmark`）、自动驾驶（`Scenario → Simulator → Perception → Planner → Metrics`）、Agent evaluation（`Task Scenario → Environment → Agent → Trace → Evaluation`）是同一方向。SilverShield 正从"一个检测系统"升级为"一个**可验证的 AI 系统**"。本 ADR 负责链条最上游的"可复现、隐私安全输入源"，是 ADR-0031 / ADR-0033 的前置与硬约束。

---

## 1. 决策（Decision）

### D1 · 单一声明式 Scenario Schema，派生两条生成通道（两层语义）

**一个 YAML 场景描述，同时可编译为两种上游输入**，由 `mode` 选择，二者从**同一份 actor / timeline 声明**派生，杜绝"画出来的"与"期望的"漂移：

```
Scenario
├── meta:        { schema_version:"1.0", scenario_id:str, version:int, description, seed, duration_frames }
├── environment: { scene_type, regions:{name:normalized_bbox}, static_objects:[{type,bbox}] }
├── camera:      { resolution:[w,h], fps, viewpoint }
├── actors:      [ ActorSpec { id, actor_type:human|vehicle|pet|object, tracks:[{frame,pos,size}], objects:[str], appearance } ]
├── timeline:    [ EventGroundTruth { frame, type } ]      # 期望 emit 的事件（ground truth）
└── expects:     { emitted_event_types:[...], min_risk_level, max_suppress_rate? }
```

> **三层版本化（资产化 + 历史 benchmark 可复现）**：
> - `meta.schema_version`：**Schema 格式**版本（v1/v2…），加载器校验，未知版本拒绝加载（fail-closed）——防"旧场景被新 renderer 静默误读"；
> - `meta.scenario_id`：**稳定资产标识**，跨版本去重与追踪主键；
> - `meta.version`：**场景内容**修订号（当轨迹从 `track` 改为 `trajectory: spline`、或增删 region 时 bump）——历史 benchmark 必须能锁定"当时跑的是哪个内容版本"，否则分数不可解释。
> 三者缺一不可（T10）。

> **`environment` 与 `camera` 分离**：`camera`（resolution/fps/viewpoint）只描述"镜头怎么拍"；`environment`（scene_type / `regions` / `static_objects`）描述"拍的是哪类空间"——`regions` 是**命名区域集合**（`entrance` / `living_room` / `safe_zone` / `corridor` / `elevator`…），不再是单一 `door_region`。这是为"家庭态势感知平台"预留的抽象：SilverShield 未来不只有门口，客厅 / 卧室 / 楼道 / 电梯都需要独立区域语义，单一 `door_region` 会限制模型。

> **`ActorSpec.actor_type` 解耦 person**：actor 是**实体**而非"人"，`actor_type ∈ {human, vehicle, pet, object}`。例：老人跌倒=`human`+`state=fallen`、诈骗递物=`human`、物体遗留=`object`。这避免将来为"非人实体"改 schema；`actor_type` 仅作生成 `RawDetection.label` 的语义提示，**不参与任何业务规则判定**（呼应 D3 边界）。

**通道一 · 结构化场景描述（`mode: detections`）**——产出 `list[RawDetection]`，直接喂现有 `Detector` 接缝（替换 `CachedDetectionDetector`）。成本最低、最快、最确定性，**不跑任何模型**。这是 ADR-0033 批量回归的主力通道。

> **`RawDetection` 所有权（producer，不是 judge）**：generator 只产**输入层原始检测**——仅有 `bbox` / `label` / `frame_index` 等感知原语，**不含 `track_id`、不做后处理聚合、不含任何业务判定**。它与 `DetectionResult`（`VisitorTracker` 输出、含 `track_id`、跨帧关联后的产物）严格区分，正如 ADR-0031 中 trace 不产出 verdict。generator 是**事实生产者**，不调用 `RuleEngine`、不"替"下游算出期望（期望由 `expects` 声明 + `runner.validate` 比对，呼应 ADR-0031 Non-goal #8「不混杂判断」）。`actor_type`（human/vehicle/pet/object）仅作生成 `RawDetection.label` 的语义提示，不影响业务判定。

**通道二 · 程序化视频帧（`mode: frames`）**——产出 `list[np.ndarray]` BGR 帧（OpenCV **程序化**渲染：在画布上按 `actors[].tracks` 画实心矩形/圆代表实体、小矩形代表 backpack 等），喂 `PerceptionPipeline.process_frame(raw_frame)`，跑**真实** detector→tracker→event 全链路。用于验证"真实检测层在受控输入下"的行为。可选 `export_mp4` 仅作人工检视用，**不入库**（§3 非目标、AGENTS.md §6.1）。

> ⚠️ **`frames` 通道的验证边界（必须明示，防误解）**：它验证的是**链路逻辑**，不是**视觉能力**。
> - ✅ **验证**：`frame` 摄入与解码链路；`detector` **接口兼容性**（真实 YOLO 能否吃下生成的帧并产出 Detection）；`tracking` 跨帧连续性；`temporal` 推理（轨迹 → 停留 → 重复来访 → 决策）。
> - ❌ **不验证**：语义识别**准确率**（人/物分得准不准）、**外观鲁棒性**（角度/尺度/遮挡）、**光照鲁棒性**（白天/夜视/逆光）、**真实世界 domain gap**（合成帧与真实摄像头分布的差异）。
>
> 即：本通道**不能**用"生成视频 + 跑 YOLO"来声称验证了视觉能力——那是真实 YOLO 权重与真实素材的职责。本 ADR 测的是**链路逻辑**，非"人长得像不像人"。

> 两条通道共享 `actors` / `timeline` 声明 → `detections` 通道的 box 与 `frames` 通道画的矩形**必然一致**（单一真相源），这是 T8 不变式的基础。

### D2 · 确定性是契约，不是约定（Deterministic-by-Construction）

- 所有随机性 MUST 由 `seed` 驱动；禁止无 seed 的 `random` / `np.random` 默认状态；
- 帧序列的**唯一时间轴是 `frame_index`**（整数），不允许 wall-clock / `time.time` 进入生成逻辑；
- 同一 `Scenario` 文件（含 `schema_version`）+ 同一代码版本 → **字节级（或内容级）可复现**：`frames` 通道逐帧 `np.array_equal` 相等；`detections` 通道逐帧 `RawDetection` 相等（顺序敏感）；
- 效果/抖动（如 appearance jitter）是**确定性函数**（`f(frame_index, seed)`，非随机游走）；
- 该约束由 T1 契约测试钉死（跨进程复跑一致）。

### D3 · 仅经既有接缝注入，零生产行为变化

生成物**只通过 ADR-0014 L2 既有可替换接缝**进入系统，绝不改动生产路径：

- `frames` 通道 → 经 `silver_demo` 的 `build_frame_source` 新增 `source_type="synthetic"`（在 `silver_demo` 内，不碰 `home_perception` 生产代码）；
- `detections` 通道 → 经现有 `Detector` ABC 注入（替换 `CachedDetectionDetector`，返回生成的 `RawDetection`）；
- 生产 RTSP / 真实 `VideoFileFrameSource` 路径**逐字节不变**；generator 默认不启用。

> 即：generator 是**独立的评估关注点**，生产 pipeline 不知道它的存在（呼应 ADR-0031 D6「可选注入、零行为变化」范式）。

### D4 · 三组件职责分离（Compiler / Runner / Validator），对标音频 `audio/tts/scenario_runner.py`

音频侧把"合成 → 跑 → 校验"放在一个 `scenario_runner.py`；视觉侧**主动预拆为三个单一职责组件**——因为 ADR-0033 Benchmark Harness 需要"100 个 scenario → 并行执行 → 聚合打分"，一个胖 Runner 会与并行/聚合逻辑冲突：

```
ScenarioCompiler   YAML             ──▶ SyntheticInput              # D1 两通道之一（detections / frames）
ScenarioRunner     SyntheticInput   ──▶ Pipeline ──▶ RunResult      # 喂 pipeline，收回事件序列
ScenarioValidator  RunResult + expects ──▶ ValidationResult         # 对照 ground truth
```

- `ScenarioCompiler`：YAML → `SyntheticInput`（调 D1 两通道生成器；对应 `validation/scenario/` 子包）；
- `ScenarioRunner`：**只**负责"输入 → pipeline → 结果"的执行编排（对应 `validation/runner/`），**不**含对比报告、可视化、结果存储、跨场景聚合（归 ADR-0033）；
- `ScenarioValidator`：`RunResult` 与 `expects`/`timeline` 比较（事件类型集合、时序窗口、risk level 下界），产出 `ValidationResult`（含"期望 vs 实际"差异）。**这正是 `audio/tts/scenario_runner.py` 的 `validate_scenario` → `ValidationResult` 模式**，使"期望"可机器校验、不靠人重新看视频。

> **三组件从设计起就分离**（非"膨胀后再拆"）：T9 钉死任一组件不得内嵌其余两组件职责；跨场景并行/聚合是 ADR-0033 `BenchmarkHarness` 的职责，不在本 ADR。

### D5 · 代码落点：采纳方案 A 的「`validation/` 父包」变体（Owner 评审决议）

**采纳**：`src/home_perception/validation/`（与 `audio/tts/`、`analysis/`、`detection/` 平级的**核心**包）。其本质**不是"生产仿真模块"，而是 Perception Test Infrastructure**——类比 audio fixture generator / robotics simulation environment / ML benchmark dataset generator，因此放在 `validation/` 父包下，而非 `simulation/`（避免被误解为运行时仿真）。推荐内部子包：

```
src/home_perception/validation/
├── scenario/      # ScenarioCompiler：YAML + schema + 编译为 SyntheticInput（D1/D4）
├── simulation/    # generator（detections / RawDetection）+ renderer（frames）+ effects（确定性）
├── runner/        # ScenarioRunner（执行编排）+ ScenarioValidator（对照 expects）
└── fixtures/      # 声明式 scenario YAML + 由 Scenario 生成的 detections.json（D6/D8）
```

它属于 `home_perception` 核心，因为产出的不是 demo 玩具，而是 pipeline 的**输入契约**（frames / `RawDetection`），与 `audio/tts` 产出音频输入同层；未来 ADR-0033 Benchmark Harness（`evaluation/`）与音频侧对称消费，包层级清晰、不跨 `silver_demo` 反向依赖。这与自动驾驶 / 机器人仿真 / Agent evaluation 的通用分层一致（`perception` / `detection` / `analysis` / `audio` / `validation` 平级）。

**被否决的方案**：
- **B（`silver_demo/simulation/`）**：放 demo 看似隔离更彻底，但 generator 产的是"pipeline 输入契约"而非 demo 附属物；归 demo 会让 ADR-0033 `evaluation/` 跨包消费、与音频 `audio/tts`（在核心）不对称，长期包面混乱；
- **扁平 `src/home_perception/simulation/`**：方向正确但名实不符——`simulation` 易被读作"生产仿真运行时"，而本组件是测试/验证基础设施，故升一级到 `validation/` 父包。

> generator 仍**不反向依赖**业务规则层（`analysis/rule_engine` 等），只依赖 `detection` 结果类型与核心配置。决策（D1–D4）与不变式（T1–T8）不受落点影响。

### D6 · 迁移既有 hack，不破坏现有测试

- `CachedDetectionDetector` + `tests/fixtures/detections/*.detections.json`：**源真相改为 Scenario YAML**；保留 `export_detections_json(scenario)` 能力（将 `RawDetection` 序列序列化，重新生成等价 `detections.json`，向后兼容既有测试）；旧 `detections.json` 标记为"由 ADR-0032 生成物派生"，逐步退役；
- `tests/demo/test_sources.py:_make_synthetic_mp4`：保留（仅验证解码路径），但新增的 `frames` 通道取代其"语义合成"职责；
- `silver_demo/scenarios.py` 的 `ScenarioConfig`：**不破坏**，新增 `mode` / `seed` / `actors` 字段并标记 optional，旧 `media_path` 驱动的 `VideoFileFrameSource` 路径保留（真实素材仍可用）。

### D7 · generator.fingerprint（产出血缘，类比 ADR-0031 D5 `policy.fingerprint`）

`SynthesizedInput`（及 `RunSummary`）**MUST** 携带 `generator.fingerprint`——对 `{schema_version, renderer_version, seed, code_version}` 的稳定哈希。原因：同一 `Scenario`（`scenario_id` + `seed` 相同）经不同 renderer 版本产出的帧/检测可能不同（如外观基元从"矩形"升级为"圆+矩形"）；下游 ADR-0033 Benchmark Harness 必须能区分"v1 渲染结果"与"v2 渲染结果"，否则跨版本回归不可解释（"分数变了"到底是 renderer 变了还是逻辑变了？）。这与 ADR-0031 D5 用 `policy.fingerprint` 反映"实际生效路由表"是同一思想：**产物必须可溯源到生成它的确切代码与配置**。fingerprint 由纯函数计算，写时 fail-closed（缺字段即报错，不静默）。

### D8 · Scenario Registry（资产编目，为 ADR-0033 Benchmark 前置）

当 ADR-0033 做 benchmark，scenario 资产会从"几个手写文件"膨胀到"几十上百个"，**必须提前编目**，否则半年后混乱。Registry 两层：

1. **目录分层**（落在 `validation/fixtures/`）：`scenarios/{perception,regression,benchmark}/`——`perception`（单点链路验证）/ `regression`（回归守护）/ `benchmark`（横向打分集）；
2. **每场景注册元数据**（写入 `meta`，与 D1 三层版本化同源）：`owner` / `tags` / `difficulty` / `category`——例 `meta: { scenario_id: stranger_repeated_visit, version: 1, owner: perception-team, category: visitor, difficulty: medium, tags: [stranger, repeat] }`。

> Registry **不是**本 ADR 的实现交付：YAML 加载器不强制 `owner` / `difficulty`（缺省即可跑），但 **schema 现在就预留这些字段**，使 ADR-0033 可直接消费而**不回改 schema**。目录约定 + 一个可选的 `registry.yaml` 索引由 ADR-0033 或 Slice E 落地，本 ADR 只定"字段存在、目录分层"。

---

## 2. 不变式（Invariants，契约测试钉死）

| # | 不变式 | 契约测试 |
| - | --- | --- |
| **T1** | **确定性**：同 `Scenario`（同文件含 `schema_version` + 同代码版本）→ `frames` 通道逐帧 `np.array_equal` 相等、`detections` 通道逐帧 `RawDetection` 相等（顺序敏感）；跨进程复跑一致 | `test_scenario_deterministic_reproducible` |
| **T2** | **隐私**：生成帧/检测**绝不含真实人脸 / PII / 真实场景**；全部为程序化基元（矩形/圆 + 噪声纹理）；本 ADR 是"替代真实素材"的手段，自身 MUST NOT 引入真实数据 | `test_synthetic_has_no_real_media` |
| **T3** | **无真实媒体依赖**：generator 不 `cv2.VideoCapture` 真实文件、不读 `data/demo/`；纯内存程序化生成（T2 的强化） | `test_generator_loads_no_external_media` |
| **T4** | **不破坏事件 Schema**：generator **只产出上游输入**（frames / `RawDetection`），**不修改** `PerceptionEvent` / `VisitorEvent` / `WarningEvent` 任何字段；新增事件类型仍须走 `docs/07` + Owner 评审 | `test_generator_does_not_alter_event_schema` |
| **T5** | **零生产行为变化**：`recorder=None` 类比——不启用 generator 时，生产 RTSP / `VideoFileFrameSource` 路径逐字节不变；generator 默认不注入 | `test_production_pipeline_unchanged_without_generator` |
| **T6** | **可机器校验的期望**：`expects` / `timeline` 可被 `ScenarioValidator` 自动比对，无需人工重看视频；校验失败给出"期望 vs 实际"的差异报告 | `test_runner_validates_against_expects` |
| **T7** | **成本有界**：`frames` 通道仅用 OpenCV 程序化绘制（无模型推理）；CPU 轻量；可选"跑真实 detector"为 **opt-in**，不默认发生 | `test_frame_rendering_is_model_free` |
| **T8** | **单一真相源**：`detections` 通道 box 与 `frames` 通道绘制矩形**同源**于 `actors.tracks`；两通道对同一 `Scenario` 在几何上等价（允许像素级渲染容差，但语义位置一致） | `test_two_channels_geometry_consistent` |
| **T9** | **三组件职责分离（编排边界）**：`ScenarioCompiler` / `ScenarioRunner` / `ScenarioValidator` 从设计起分离，各自单一职责；任一组件**不内嵌**其余两组件职责（尤其 Runner 不含对比报告/可视化/结果存储/跨场景聚合，归 ADR-0033）；跨场景并行/聚合是 ADR-0033 `BenchmarkHarness` 职责 | `test_components_single_responsibility` |
| **T10** | **场景资产三层版本化**：每个 `Scenario` 的 `meta` **MUST** 含 `schema_version`（Schema 格式）+ `scenario_id`（稳定资产标识）+ `version`（场景内容修订）；加载器对未知 `schema_version` 拒绝加载（fail-closed），不静默降级；`scenario_id` + `version` 作为历史 benchmark 可复现的锁定主键 | `test_scenario_requires_3level_versioning` |
| **T11** | **产出血缘可溯源**：`SynthesizedInput` / `RunSummary` **MUST** 携带 `generator.fingerprint`（对 `{schema_version, renderer_version, seed, code_version}` 的稳定哈希）；缺字段即报错（fail-closed），不静默；下游 ADR-0033 据此区分不同 renderer 版本的产物 | `test_synthesized_input_carries_fingerprint` |

> **T2/T3 与"替代真实素材"不矛盾**：本 ADR 的存在理由就是消除对真实家庭视频的依赖。generator 自身若引入真实数据，就违背了它要解决的问题。

---

## 3. 范围与非目标（Scope / Non-Goals）

**在范围内**：声明式 `Scenario` schema + YAML 加载；两通道 generator（`detections` / `frames`）；确定性效果；三组件 `ScenarioCompiler` / `ScenarioRunner` / `ScenarioValidator`；`silver_demo` 的 `synthetic` 帧源接缝；既有 hack 迁移；Scenario Registry 字段预留（D8）。

**明确不做**：

1. ❌ **不训练 / 不替代 YOLO**：我们不合成图像去"训练"检测器；我们合成**输入**去测试**既有** pipeline 逻辑。检测器仍是真实 YOLO（或注入的 stub）。
2. ❌ **不生成照片级 / 扩散模型视频**：只用 OpenCV 程序化基元（矩形/圆/噪声）。加 Remotion / moviepy / manim / 扩散视频生成属**重依赖 + 边缘 CPU 不可行**，明确排除（呼应 AGENTS.md §4 资源约束、§6.1）。
3. ❌ **不改变事件语义 / 不加 fraud 类**：严守模块边界铁律，只产 5 类标签事件。
4. ❌ **不做实时流源**：generator 仅评估用途，不接入生产 RTSP 链路。
5. ❌ **不做 Benchmark Harness 本身**：评分 / 聚合 / 报告归 **ADR-0033**；本 ADR 只负责"产出场景 + 跑出结果 + 对照期望"，不负责跨场景打分与回归阈值管理。
6. ❌ **不直注 Memory Graph**：与 ADR-0028 D6 正交（见 §0.4）。
7. ❌ **不把生成的 MP4 入库**：`export_mp4` 仅本地人工检视，遵循 AGENTS.md §6.1「默认不存全量视频」；JSONL/帧产物按 `.gitignore` 排除。
8. ❌ **不引入新重依赖**：仅用已存在的 `opencv-python` / `numpy` / `pydantic` / `pyyaml`；不新增 torch 外依赖。

**已记录但本 ADR 不做：从 `ActorSpec` 到 `EntitySpec` 的语义演化**（设计意图留存，防丢失）。当前 `actors` 描述的是"**画什么**"（几何：轨迹 + 外观基元），且已通过 `ActorSpec.actor_type ∈ {human, vehicle, pet, object}` 解耦"实体类型"——不再把 actor 绑定为 person；但语义**角色 / 行为**（role / behavior / dwell / approach）仍留待 EntitySpec 演化。评审指出，场景库未来会成为核心资产，且安全场景本质是"**发生什么**"——例如诈骗场景 = `陌生人进入门口 → 停留 → 接近老人 → 递送物品 → 老人接电话`。这需要从"几何场景生成"升级为"**语义场景图 + 时序行为**"：

```yaml
entities:                       # 取代 actors
  - id: visitor_01
    category: person
    attributes: { role: stranger }
    trajectory: { type: keyframe, ... }
    behavior:
      dwell: { duration: 300 }
      approach: { target: elderly_01 }
```

即 `ActorSpec`（几何） → `EntitySpec`（几何 + 角色 + 行为）。**本 ADR 明确不做此演化**——保持 MVP 几何场景生成，避免提前膨胀；但把它作为既定未来方向记录于此，后续若立项"语义场景库"，应另起 ADR（或 ADR-0033 的编排层）承接，而非在本 ADR 内扩展 schema。

## 4. 后果与备选方案（Consequences / Alternatives）

**正面**：
- 感知链路首次获得**可 CI、可复现、隐私安全**的输入源，填补 §0.2 的结构性空白；
- 与音频 `tts/` 范式对称，团队已有成熟心智模型，降低维护成本；
- `ScenarioValidator` 让"期望"机器可校验，为 ADR-0033 Benchmark Harness 提供**标准化输入/输出契约**；
- 把散落的测试 hack（`CachedDetectionDetector` / `_make_synthetic_mp4`）收敛为受契约测试守护的正式组件，消除"测试内联、无 schema、无确定性"的技术债；
- 单一 `Scenario` schema 同时服务 `detections` 与 `frames` 两通道（T8），避免"画"与"期望"漂移。

**代价**：
- 新增 `validation/` 父包（或 `silver_demo/simulation/`）+ 一层声明式 schema + 契约测试；
- 程序化帧是**抽象几何表征**（person=矩形），不测试"像素级检测鲁棒性"——那是真实 YOLO 权重与真实素材的职责；本 ADR 测的是**链路逻辑**（轨迹→停留→重复来访→决策），非"人长得像不像人"；
- `frames` 通道若要跑**真实** detector 验证，会触发 YOLO CPU 推理（opt-in，T7 不默认）。

**备选方案（已否决）**：

| 方案 | 否决理由 |
| --- | --- |
| 继续用手工 `detections.json` 回放 | 无 schema / 无确定性 / 无 ground truth 声明；改一处轨迹要手动改 JSON；不可扩展 |
| 用真实家庭视频做回归 | 违反隐私（§3.3）与"不入库"（§6.1）；不可 CI、不可复现 |
| 引入扩散 / Remotion 生成照片级视频 | 重依赖（torch 外）+ 边缘 CPU 不可行；超出 Phase 0/MVP 资源约束（§4） |
| 只对 Memory 层做声明式 fixture（沿用 ADR-0028 D6，不管感知层） | 不解决"帧 → 事件"链路验证，留下 §0.2 的结构性空白 |
| 把 generator 塞进 `silver_demo` 且不复用音频范式 | 与已验证可行的 `tts/` 模式不对称，重复造轮子 |
| 在 generator 里直接调用 `RuleEngine` 自己"算期望" | 越权：generator 只产输入，期望由 `expects` 声明 + `runner.validate` 比对（单一职责，呼应 ADR-0031 Non-goal #8「不混入判断」） |

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **`frames` 通道的语义保真度上限**：程序化矩形能否表达 `abnormal_dwell`（停留）/ `high_risk_approach`（接近门区）所需的"位置 + 时长 + 轨迹"？——本文认为能（这些是几何/时序属性，非像素属性），但最佳"外观基元"集合（person 用矩形还是带头身的圆+矩形？）留待 Slice C 实现时由测试反验。
- **与 ADR-0028 D6 fixture 的编排**：端到端"合成场景同时驱动感知层 + Memory 层"如何串联，归 ADR-0033 或单独编排 ADR，本 ADR 不定义。
- **`expects` 的校验宽松度**：事件时序窗口容差（±N 帧？）、`min_risk_level` 下界语义——Slice D 实现时由契约测试钉死，本 ADR 只定"必须有可机器校验的期望"。

---

## 6. 实施切片（Slices）与验收清单

> 契约先行、零行为变化优先。A/B/C 零行为变化可先合；D/E 触及 `silver_demo` 接缝与既有 hack 迁移，门控评审。

- **Slice A（Scenario schema + 加载，零行为变化）**：新增 `validation/scenario/scenario.py`——`Scenario` pydantic 模型（meta/environment/camera/actors/timeline/expects）+ YAML 加载器 + 校验（`meta.seed`/`duration_frames`/`schema_version`/`scenario_id`/`version` 必填、`camera.resolution`/`fps` 必填、`actors.tracks` 帧序合法、`actor_type` 枚举合法）。测试：`test_scenario_roundtrip` + `test_scenario_validation_errors`（缺字段 / 帧序倒挂 / 未知 `schema_version` 即报错）。**不接任何运行时。**
- **Slice B（通道一：detections 发射器，零行为变化）**：`validation/simulation/generator.py` 的 `emit_detections(scenario) -> list[RawDetection]`，从 `actors.tracks` 确定性派生每帧 `RawDetection`（human/vehicle/pet/object + backpack 等，仅 bbox/label/frame_index，无 track_id）。取代 `CachedDetectionDetector` 的手工 JSON，提供 `export_detections_json` 向后兼容。测试：T1 / T4 / T5 / T8 + 与既有 `detections.json` 几何等价。
- **Slice C（通道二：frames 渲染器）**：`validation/simulation/renderer.py` 的 `render_frames(scenario) -> list[np.ndarray]`（OpenCV 程序化绘制，确定性）+ 可选 `export_mp4`（本地、不入库）。测试：T1 / T2 / T3 / T7 + T8（与 B 几何一致）。
- **Slice D（Compiler / Runner / Validator 三组件）**：`validation/scenario/` 的 `ScenarioCompiler`（YAML → `SyntheticInput`）、`validation/runner/` 的 `ScenarioRunner`（→ `RunResult`）+ `ScenarioValidator`（→ `ValidationResult`，含"期望 vs 实际"差异），对标 `audio/tts/scenario_runner.py` 的 `validate_scenario` 模式。测试：在 2–3 个场景上跑通端到端（detections 通道喂 stub detector；frames 通道喂真实 detector 为 opt-in）。
- **Slice E（`silver_demo` 接线 + 迁移）**：`build_frame_source` 新增 `source_type="synthetic"`（调 `render_frames`）；`ScenarioConfig` 加 optional `mode`/`seed`/`actors`；既有 `CachedDetectionDetector` + `detections.json` 迁移为 ADR-0032 生成物派生物。零生产行为变化；单独 PR + Owner 评审。

### 验收清单（Acceptance Criteria）

1. **D1 两通道同源**：同一 `Scenario` 的 `detections` box 与 `frames` 绘制矩形几何一致（T8）；
2. **D2 确定性**：T1 通过（跨进程复跑一致）；所有随机性经 `seed`；帧时间轴仅为 `frame_index`；
3. **D3 零行为变化**：T5 通过；生产 RTSP / `VideoFileFrameSource` 路径不变；generator 默认不注入；
4. **D4 Runner 可校验**：T6 通过；`validate` 自动比对 `expects` 并产出差异报告；
5. **边界铁律**：T2 / T3 / T4 / T7 通过；全量 `ruff check src tests` + `pytest` 全绿（AGENTS.md 基线，无回归）；
6. **D5 落点明确**：按 D5 采纳方案 A 的 `validation/` 父包变体落地（`src/home_perception/validation/`），`validation/` 不反向依赖业务规则层；
7. **D6 迁移干净**：既有 `detections.json` 可由 `export_detections_json` 重新生成且等价；`ScenarioConfig` 向后兼容（旧 `media_path` 路径仍可用）；
8. **非目标守住**：无新重依赖、无照片级视频、无 fraud 类、无 MP4 入库（§3 八条非目标逐条确认）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-08**：初稿（Proposed）。承接 ADR-0028 D6「程序化视频生成归 Perception Validation Infrastructure」的明确划定，建立**场景仿真层（Scenario Simulation Layer）**：(1) **D1** 单一声明式 `Scenario` schema 派生两通道——`detections`（`list[DetectionResult]`，零模型、最快）与 `frames`（OpenCV 程序化 BGR 帧，跑真实 detector 全链路），二者同源 `actors.tracks`（T8 单一真相源）；(2) **D2** 把确定性升为契约（seed 驱动、帧时间轴仅 `frame_index`、跨进程可复现）；(3) **D3** 仅经 ADR-0014 L2 既有帧源/检测器接缝注入，零生产行为变化；(4) **D4** `ScenarioRunner.synthesize→run→validate` 对称复制音频 `audio/tts/scenario_runner.py` 范式，使期望可机器校验；(5) **D5** 代码落点提 A（`src/home_perception/simulation/`）/ B（`silver_demo/simulation/`）两方案交 Owner 拍板；(6) **D6** 迁移既有 `CachedDetectionDetector`/`detections.json` 与 `_make_synthetic_mp4` hack，不破坏现有测试。T1–T8 不变式钉死确定性/隐私/无真实媒体/不破 Schema/零行为变化/可校验/成本有界/两通道几何一致。明确非目标：不训练 YOLO、不生成照片级视频、不加 fraud 类、不做实时流源、不做 Benchmark Harness（归 ADR-0033）、不直注 Memory Graph（与 ADR-0028 D6 正交）、不入库 MP4、不引新重依赖。本 ADR 是 ADR-0033 Benchmark Harness 的可复现、隐私安全输入源前置。

- **2026-08-08（二修 · Owner 架构评审收紧）**：(1) **D5 采纳方案 A**（`src/home_perception/simulation/`），否决 B（防 `evaluation/` 跨包消费 + 与音频不对称）；(2) **`RawDetection` 所有权**（D1 + T 系）：generator 只产输入层原始检测（无 `track_id`、无后处理、无业务判定），与 `DetectionResult`（tracker 输出）严格分离，呼应 ADR-0031 Non-goal #8；(3) **`meta.schema_version` + `scenario_id`** 资产化前置（D1 + 新增 **T10**）；(4) **D7 `generator.fingerprint`**（类比 ADR-0031 D5 `policy.fingerprint`，对 `{schema_version, renderer_version, seed, code_version}` 哈希，下游 ADR-0033 区分 renderer 版本产物；新增 **T11**）；(5) **D4 Runner 编排边界**：禁止膨胀为 God Object，既定 `ScenarioCompiler`/`Executor`/`Validator` 拆分出口（新增 **T9**）；(6) **§3 记录 `ActorSpec→EntitySpec` 语义演化路线**（几何→"发生什么"，本 ADR 不做但留存设计意图）；(7) **§0.6 战略定位**：明确本 ADR 是 AI Validation Infrastructure 三 ADR 闭环（Simulation→Pipeline→Trace→Benchmark）的最上游输入源。不变式由 T1–T8 扩至 T1–T11。

- **2026-08-08（三修 · Owner 第二轮架构评审）**：(1) **D1 schema 结构性增强**：`camera` 拆为 `environment`（`scene_type` / 命名 `regions` 集合 / `static_objects`）+ `camera`（`resolution`/`fps`/`viewpoint`），`door_region` 升级为多区域抽象（家庭态势感知平台）；`ActorSpec` 加 `actor_type ∈ {human, vehicle, pet, object}`，解耦"实体=人"；`meta` 增 `version`（场景内容修订），与 `schema_version`（格式）/ `scenario_id`（资产）构成**三层版本化**（T10 扩为三字段）。(2) **`frames` 通道验证边界明示**（D1 通道二）：✅ 验证 frame 摄入 / detector 接口兼容 / tracking 连续性 / temporal 推理；❌ 不验证语义准确率 / 外观鲁棒性 / 光照鲁棒性 / 真实 domain gap——杜绝"生成视频+跑 YOLO=验证视觉能力"的误解。(3) **D5 升级为 `validation/` 父包变体**：`src/home_perception/validation/`（含 `scenario`/`simulation`/`runner`/`fixtures` 子包），本质是 Perception Test Infrastructure 而非生产仿真，否决扁平 `simulation/` 与 `silver_demo/simulation/`。(4) **D4 三组件预拆**：`ScenarioCompiler` / `ScenarioRunner` / `ScenarioValidator` 从设计起分离（非膨胀后再拆），T9 钉死单一职责，为 ADR-0033 并行 100 scenario + 聚合让路。(5) **D8 Scenario Registry**：`validation/fixtures/scenarios/{perception,regression,benchmark}/` 目录分层 + `meta` 预留 `owner`/`tags`/`difficulty`/`category`，使 ADR-0033 可直接消费不回改 schema。切片 A–E 引用同步 `validation/` 路径。

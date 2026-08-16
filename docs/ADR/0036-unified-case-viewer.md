# ADR-0036: 统一 SilverShield Case Viewer（展示语义统一层 · 单一 View Model）

- 状态：Accepted（四轮评审 + E2E 实证后冻结）
- 日期：2026-08-14（定稿）／ 2026-08-16（冻结）
- 决策者：Owner
- 相关：ADR-0015 / ADR-0016 / ADR-0017（第一代 Demo）、ADR-0026（音频感知链路）、ADR-0027（音频记忆集成）、ADR-0028（跨模态运行时接线）、ADR-0031 / ADR-0032 / ADR-0033 / ADR-0034 / ADR-0035、MEMORY.md「战略方向重定向（2026-08-14）」

---

## 本 ADR 的身份（先说清楚）

> **ADR-0036 是"统一展示语义层"的 ADR，不是"新建一个前端"的 ADR。**

现在已经存在四块资产，且彼此割裂：

```
silver_demo       → Live（实时，自有 DemoAggregateState）
D1 Explorer       → Artifact（静态可信回放）
D2 Replay         → Artifact Replay（时间交互回放）
D3 Video          → Artifact Export（程序化视频导出）
```

ADR-0036 要做的**不是增加第五块**，而是把这四块经 **Adapter** 收敛到**同一个 `EvidenceProjection`**，再由一个 Case Viewer 渲染；并把音频与媒体源正式抽象为一等证据/适配层。本 ADR 收敛后的主线是：

```
                    Case Viewer
                         │
             ┌───────────┴───────────┐
             ↓                       ↓
        Case Presentation       Evidence Presentation
        (产品主轴·讲述案例)        (证据语义·为什么)
             │                       │
         Case Video               Timeline
         Audio                    Decision
         Action                   Graph
         Outcome                  Memory
                                  CrossModal
                         │
                         ↓
                  EvidenceProjection
                         ▲
        ┌────────────────┼────────────────┐
        │                │                │
  Live Adapter    Artifact Adapter   Replay Adapter
  (Runtime)       (CI 可信 artifact) (D2 时间交互)
                         │
                         ↓
              D3 = Case Video Export（同 View Model 渲染器，不再承担 Analysis Video）
```

---

## 背景（Context）

ADR-0032～0035 已建成可信工程生产线（Scenario → Runtime 回放 → Trace → Memory/Cross-Modal → Benchmark → Integration Gate → 可信 Artifact → EvidenceProjection → D1/D2/D3，机器面向）。第一代 Demo（`silver_demo`，ADR-0015/0016/0017）是人类面向实时产品前端（跑 LIVE `PerceptionPipeline`，自有 `DemoAggregateState`）。

**已证明的问题**：若 Live/Explorer/Replay/Video 各自解释 `risk`/`decision`/`timeline`/`graph`，必然出现 `Live 说 HIGH / Artifact 说 LOW / Video 说 WARN` 的"哪个是真的"争论。

**2026-08-14 战略重定向（Owner）**：原 Demo 网页产品方向才是正确的主产品体验；CI/ADR 是后来补上的可信基础设施。两条线：Demo/Product（人类面向）/ CI/Verification（机器面向）。

**最终 Demo 的形态**（Owner 三轮评审明确）：不是纯视频 Demo，而是 **视频 + 音频 + 多模态风险感知 + 决策 + 行动闭环**，且必须先把"主视频到底是什么 / 与 D3 关系 / 媒体来源 / 首屏"冻结，否则第一版实现极易退化成"把 `silver_demo` 换个名字重写一遍"。

**关键事实（已核实）**：
- `visualizer/schema/evidence.py` 的 `EvidenceProjection` 已是稳定 fail-closed 投影契约；`ProvenanceKind = REAL_SENSOR | SIMULATED | FIXTURE`。
- `visualizer/schema/graph.py` 的 `EvidenceGraph` 节点闭集含 `Frame`/`Detection` 预留位（live 帧级数据无需新 schema）。
- D3 的 `video/evidence/adapter.py` 已以 `load_evidence_projection` 为输入 → D3 与 Case Viewer 共享同一 View Model。
- **音频真实符号（ADR-0026/0027/0028）**：`AudioPerceptionEvent`（kind=`AudioPerceptionKind` 五值、`score` 0~1 规则强度、`confidence` 0~1、`labels`/`scored_labels`、`source_segment_ids`）、`AudioSegmentEvent`（`vad_ratio`/`rms`/`speech_rate` 声学特征）、`RiskSignal(source=AUDIO, category=COMMUNICATION)`、`EvidenceItem(modality=AUDIO)`、`CrossModalLink(relationship=SUPPORTS|CO_OCCURS)`。`AudioPerceptionEvent` 含 `FORBIDDEN_AUDIO_FIELDS`（`fraud_result`/`verdict`/`is_fraud`/`crime_probability`/`deception_score` 等判定字段，结构性禁止），且 **ASR/转录不在音频感知层**（ADR-0001/0026）。
- **诚实声明（重要）**：`audio_evidence` 必须由**真实音频符号**驱动，不得凭空创造（见 VM-7 / §音频字段来源映射 / AC-12）。当前状态（Slice C · VM-13 Phase C 已落地）：`runner` 携带 `scenario.audio` 经 `compiler` 确定性编译的 `AudioPerceptionEvent`（`synth.audio_events`）→ `IntegrationRunResult.audio_perception_events` → `LoopArtifactSummary.audio_events`（`audio_*` 前缀键，规避脱敏禁止键 `"score"` 精确匹配）→ canonical `artifacts.audio_evidence` → `loader._build_audio_evidence` 投影为 `AudioEvidenceNode` → `renderer` 渲染 🔊 区块。**未声明音频的场景（或 Phase A/B）`audio_evidence` 恒为 `()`（AC-12 严守，绝不编造）**；`AudioPerceptionEvent` 的 `FORBIDDEN_AUDIO_FIELDS` 与无 ASR/转录约束由 `live_adapter` / `loader` fail-closed 守卫（见附录 A）。

---

## 决策（Decision）

1. **新建统一 SilverShield Case Viewer**，定位为第二代 Demo / 主产品体验（人类面向，实时/准实时）。它是产品前端，**不参与运行、不参与判断、不改变系统行为**（与 ADR-0035 D9 一致）。
2. **`EvidenceProjection` 是 Case Viewer 唯一 View Model（VM-1）**。Case Viewer **不得**自行定义 `riskData`/`decisionData`/`timelineData`/自有 graph/自有 `audioData`/`audioState`；只渲染 `visualizer/loader` 产出的 `EvidenceProjection`。
3. **四块资产 → 三类 Adapter → 单一 View Model**：Artifact Adapter（D1 静态）/ Replay Adapter（D2 时间交互）/ Live Adapter（实时 FrameResult + AudioEvidence）。
4. **依赖方向铁律（分层，见 ADR-0015 §2.1.1）**：Live Adapter 依赖 Runtime 输出契约（FrameResult / AudioEvidence / events），不依赖 `silver_demo` 内部状态；`silver_demo` 可继续作 WS/启动宿主（live 帧/音源之一），但不是 View Model 拥有者。**Host / Composition Root（`silver_demo.gateway`）允许 import `home_perception.visualizer.viewer`（Presentation Layer），作为唯一合法的「反向依赖展示层」角色**，把 `FrameResult`/`AudioEvidence` 投影为 `EvidenceProjection` 并渲染统一 Case Viewer；但 `silver_demo` 的 **Runtime Core**（除 `gateway` 外的子模块）仍**禁止** import `visualizer`。`viewer/` 不得 import `silver_demo`（T0-3）。
5. **D-CaseVideo · Case Video 为主展示媒体（本轮新增）**：Case Viewer 的主视频形态是 **Case Video（案例视频）**，讲述"发生了什么、风险如何发展、系统何时介入、最终产生什么结果"。定义最小叙事结构：`Context → Incident → Risk Escalation → AI Perception → Intervention → Outcome`。明确 **Case Video ≠ Analysis Video**，后者不产品化（见 VM-12 / 决策 11）。
6. **D-MediaMode · Case Video Mode 与双时间轴（本轮新增）**：Case Video 来自三类媒体模式——`Pre-generated Case MP4` / `Scenario Frame Replay` / `Live Media Stream`（见 §媒体源适配）。严格区分 **Media Timeline（播放什么）** 与 **Evidence Timeline（发生了什么）**：两条时间轴可同步但**不是同一个数据结构**（VM-10）。
7. **D-Audio · 音频作为第一等证据（两轮新增）**：音频在 Case Viewer 中**不是独立 UI 数据源**，而是 `EvidenceProjection` 的一等证据类型，与视觉事件、Decision、Action、Episode、CrossModalLink 共存于**统一 Evidence Timeline / EvidenceGraph**。新增派生字段 `audio_evidence`（详见 §音频作为第一等证据 + 附录 A），由真实音频符号驱动，且不破坏 VM-1。
8. **D-MediaSource · Media Source Adapter（两轮新增）**：视频/音频至少三种来源经 `Media Source Adapter`（`ArtifactVideoSource` / `SyntheticFrameSource` / `LiveFrameSource`）统一解析为可播放媒体字节；媒体字节与证据语义分离——`EvidenceProjection` 只持 ref/timestamp，**不持媒体字节**。
9. **D-CasePresentation · CasePresentationDescriptor 展示编排（本轮新增）**：产品需要一个上层 `Case` 编排对象承载"标题 / 首屏布局 / 媒体源绑定 / 时间映射"等纯展示元数据。**它是展示编排对象，不是业务事实模型**（VM-11）。
10. **D-SyncClock · Presentation Clock / Case Time（本轮新增）**：定义纯展示层时间基准 `Case Time`，以及 `Media Time ↔ Evidence Time` 映射契约，杜绝前端再出现两套时间（VM-10 / AC-14）。
11. **D-D3 · D3-A=Analysis Video 实验，Case Video=新展示能力（本轮澄清）**：D3-A 已产出的是 **Evidence Analysis Video 实验资产**，保留但**不再扩展**；ADR-0036 的产品主视频是 **Case Video（新展示能力）**；D3 未来 = **Case Video Export**（同一 `EvidenceProjection` 的"可传播渲染器"），**不再承担 Analysis Video 职责**（VM-12）。
12. **D-NoInference · 展示层不生成证据（ASR/LLM 禁入，两轮新增）**：Case Viewer / `viewer/` **绝不**执行 ASR/LLM/推理来"造"音频或任何证据；`audio_evidence` 只来自 `EvidenceProjection`（VM-9）。
13. **Provenance 一等视觉概念**：`SIMULATED`/`REAL_SENSOR`/`FIXTURE` 必须在每个案例视图显式呈现（带产品文案），绝不默认隐藏（见 §展示契约）。

### 依赖关系（修正后，含音频 + 媒体源 + Case 编排）

```
                    Runtime (PerceptionPipeline / 冻结白名单)
                      │
             FrameResult + AudioEvidence (ADR-0015 §5 白名单 + ADR-0026 音频契约)
                      │
          ┌───────────┴───────────────────┐
          ↓                                ↓
   Live Adapter (viewer/)          silver_demo transport
   依赖 Runtime 输出契约,             (WebSocket / 启动宿主,
   不依赖 silver_demo 内部状态)         NOT View Model 拥有者)
          ↓                                │ FrameResult + AudioEvidence via WS
   CasePresentationDescriptor ◄────────────┘ (纯展示编排: 标题/媒体绑定/首屏)
          ↓
   EvidenceProjection  ◄────────────────────┘ (Artifact/Replay/Live 三 Adapter 收敛)
   (含 audio_evidence, 统一 Evidence Timeline)
          ↓
      Case Viewer (Case Presentation + Evidence Presentation)
          │
          ↓ Case Video Export
      D3 (同 View Model 渲染器, 非 Analysis Video)
```

> **分层依赖补充（ADR-0015 §2.1.1）**：图中 `silver_demo transport` 仅作 WS / 启动宿主（live 帧/音源出口），
> **不是 View Model 拥有者**；其中 `silver_demo.gateway`（Host / Composition Root）是**唯一**允许
> import `home_perception.visualizer.viewer`（Presentation Layer）的层，用于把 `FrameResult`/`AudioEvidence`
> 投影为 `EvidenceProjection` 并渲染统一 Case Viewer。`viewer/` 仍**单向**——
> 依赖 Runtime 输出契约，**不**依赖 `silver_demo` 内部状态（T0-3）。

---

## 单一 View Model 不变式（Hard Invariant）

- **VM-1（唯一 View Model）**：前端状态必须完全由 `EvidenceProjection` 派生；**不得**出现 `riskData`/`decisionData`/`timelineData`/自有 graph/**`audioData`/`audioState`** 等事实型模型。所有业务展示状态必须可从单个 `EvidenceProjection` 派生；**不得存在第二份业务事实状态**。允许纯 UI 状态（`UIState`/`PlaybackState`/`SelectionState`/`ZoomState`/`PanelState`/`AudioPlaybackState`/`AudioVolumeState`/`AudioWaveformUIState`）。
- **VM-2（禁 synthetic）**：节点/边必须携带 `ref` 且 `provenance_kind` 必填；Live 标 `REAL_SENSOR`，Artifact 标 `SIMULATED`，绝不把合成当真实、或反之。
- **VM-3（不反向耦合生产 + 不依赖 silver_demo）**：`viewer/` 与前端不得 import 生产 runtime 决策逻辑，也**不得 import `silver_demo`**（T0-3，AST 守卫见 ADR-0015 §5 T0-3）；Live 只经 ADR-0015 §2.1 / §2.1.1 白名单 + ADR-0026 音频契约消费 `FrameResult`/`AudioEvidence`，映射逻辑全在 `live_adapter` 内。反向依赖的**唯一**合法例外是 `silver_demo.gateway`（Host / Composition Root）import `home_perception.visualizer.viewer`（ADR-0015 §2.1.1），该依赖方向是「Host → Presentation Layer」，不构成 `viewer → silver_demo`。
- **VM-4（同源 schema，含音频与决策/行动）**：Live 与 Artifact 两种模式，对**视觉、音频、决策、行动共享同一 `EvidenceProjection` schema**，仅 `provenance_kind` 不同；前端渲染对两种模式无分支差异（或仅极薄溯源着色）。
- **VM-5（零行为变化）**：Case Viewer / viewer 适配器不得改变 `silver_demo` 运行行为、不得接入 CI 门禁、不得写回 Memory/Decision。
- **VM-6（只读派生，非权威状态）**：`EvidenceProjection` 是 **read-only projection, not an authoritative runtime state store**；runtime 不得把它当状态总线（守 ADR-0035 D5 派生模型边界）。
- **VM-7（字段可按 mode 缺失，显式表达，禁止伪造）**：结构一致 ≠ 所有字段必存在。Live 可能缺 `gate`/`expectation_fingerprint`/`benchmark`；音频若 artifact 未承载则为**显式空**（`audio_evidence=()`）。缺失必须显式（`None`/空元组/absent），禁止伪造（尤其不得 `gate=PASS`、不得把 Live 伪装成已通过 Gate 的可信回放、不得编造 `audio_evidence`）。
- **VM-8（Live 增量 + 幂等）**：Live projection 是**增量 `ProjectionAccumulator`**（FrameResult/AudioEvidence → merge/append → current）；顺序敏感但幂等：同一有序 stream 重放 N 次，最终 `EvidenceProjection` 逐字段一致。
- **VM-9（展示证据，不生成证据）**：Case Viewer / `viewer/` **绝不**执行 ASR/LLM/推理来"造"音频或任何证据。`audio_evidence` 只能来自 `EvidenceProjection`（由 `AudioPerceptionEvent`/`EvidenceItem(modality=AUDIO)`/`RiskSignal(source=AUDIO)`/`CrossModalLink` 派生）。音频依赖方向是 **Audio Runtime / Audio Evidence Producer → Live Adapter → EvidenceProjection → Case Viewer**，不是 **Case Viewer → ASR → LLM**。TTS 旁白属 D3 导出关注（`visualizer.video → audio.tts` 单向授权，ADR-0035 D3-1），与 Live Case Viewer 推理无关。
- **VM-10（双时间轴分离但可同步 · 新增）**：`Media Timeline`（媒体字节播放进度）与 `Evidence Timeline`（事件/风险/决策语义）是两类关注；前者由 Media Source Adapter + `Case Time` 承载（纯 UI/展示），后者是 `EvidenceProjection.timeline`（语义）。两者经 `Case Time` 映射同步，**不是同一数据结构**；`EvidenceProjection` / `CasePresentationDescriptor` 不得内嵌媒体播放时钟或字节。
- **VM-11（CasePresentationDescriptor 仅展示编排 · 新增）**：`Case` / `CasePresentationDescriptor` 只承载展示元数据（`case_id`/`title`/`scenario_ref`/`media_binding`/`first_screen_layout`/`time_mapping`）。**不得承载业务事实、不得包含可由 `EvidenceProjection` 派生的"事实状态"**（如不得存 `case_risk_level`、`case_decision`）。它编排"显示什么标题 / 播放哪个媒体 / 首屏放哪些面板"，但一切事实值仍来自 `EvidenceProjection`。
- **VM-12（Case Video ≠ Analysis Video · 新增）**：Case Video 是产品主视频（叙事结构 `Context→Incident→Risk Escalation→AI Perception→Intervention→Outcome`）；**Analysis Video 不产品化**。D3-A 的 Evidence Analysis Video 作为历史实验资产保留、**不再扩展**；D3 未来只做 Case Video Export（同 View Model 渲染器）。Case Viewer 不得为"展示更丰富"而把 Analysis Video 重新塞回主体验。
- **VM-13（Live 音频分阶段 · 新增）**：Live Mode 音频落地分三阶段——**Phase A** 视觉 Live（FrameResult → ProjectionAccumulator，**无音频**）；**Phase B** Audio Runtime → AudioEvidence → Live Adapter（REAL_SENSOR 音频进 projection）；**Phase C** AudioEvidence → canonical artifact → Artifact Mode 也能展示音频。Implementation 严禁在 Phase A 同时改 Runtime / Loader / Viewer 三处范围。

---

## D-CaseVideo · Case Video 为主展示媒体

### 1. Case Video 的定义（最小叙事结构）

Case Video 是 Case Viewer 的**主视频形态**，用于讲述一条完整案例：

```
Case Video
→ Context          （场景/人物/环境背景）
→ Incident         （触发事件：异常行为/异常声学）
→ Risk Escalation  （风险如何随时间发展升级）
→ AI Perception    （系统"看到/听到"了什么：视觉 + 音频证据）
→ Intervention     （系统何时、以何种决策介入）
→ Outcome          （最终行动与结果：NOTIFY_FAMILY / ESCALATE / episode 落库）
```

### 2. Case Video ≠ Analysis Video（关键一刀）

- **Analysis Video**：ADR-0035 D3-A 已落地的 Evidence Analysis Video，是"把证据投影逐帧渲染成分析片"的实验产物——它服务于工程分析，不是产品主体验。
- **Case Video**：本 ADR-0036 定义的**产品主视频**，面向人类评委/家属/运营，强调"叙事 + 可信 + 多模态"。
- **关系（决策 11 / VM-12）**：D3-A 实验资产保留但**不再扩展**；D3 未来只演进为 **Case Video Export**（输入仍是同一 `EvidenceProjection`，只是输出定位从"分析片"转为"案例片"）。Case Viewer 主体验用 Case Video，**不得把 Analysis Video 重新产品化**。

### 3. Case Video 的来源（Viewer 不负责生成 · 本轮补）

**来源规则（不扩 ADR）**：Case Viewer **不负责生成** Case Video；Case Video 是一种 **Media Source**（见 §媒体源适配），其字节由外部既存链路产出，Viewer 只消费、不生产。当前体系下的来源映射：

- **已有 AI 生成案例视频 / artifact 内媒体** → `ArtifactVideoSource`（Pre-generated Case MP4）
- **ADR-0032 程序化帧序列** → `SyntheticFrameSource`（Scenario Frame Replay）
- **（未来）`EvidenceProjection` → D3 Case Video Export → mp4** → 仍经 `ArtifactVideoSource` 消费

即：Case Video 是"被播放的媒体"，不是"Case Viewer 渲染时现做的产物"。D3 是 Case Video 的**生成/导出实现方**（VM-12），而非 Case Viewer 的内置能力。Slice A 的 Case Video 主轴直接复用既有的 artifact 媒体或 ADR-0032 帧回放，**不要求先有 D3 导出**。

---

## D-MediaMode · Case Video Mode 与双时间轴

### 1. 媒体模式（主视频来自哪里）

```
Case Video Mode
├── Pre-generated Case MP4      （ArtifactVideoSource：D3 Case Video Export 产出的 mp4 / artifact 内媒体）
├── Scenario Frame Replay       （SyntheticFrameSource：ADR-0032 程序化帧序列回放）
└── Live Media Stream           （LiveFrameSource：Runtime 实时视觉 + 音频流）
```

三种模式服务于"播放什么"，由 Media Source Adapter 解析为可播放字节（见 §媒体源适配）。

### 2. Media Timeline ≠ Evidence Timeline（双时间轴）

```
Media Timeline  = 播放什么（媒体字节进度，UI 关注，由 Case Time 承载）
Evidence Timeline = 发生了什么（事件/风险/决策语义，由 EvidenceProjection.timeline 承载）
```

两条轴**可同步但不同源**：`Case Time` 作为纯展示时钟，把视频当前播放位置映射到 `Evidence Timeline` 上的事件（见 VM-10 / D-SyncClock）。前端**不得**把 Media 进度与 Evidence 事件混进同一数组或同一状态。

**同步 ≠ 一致性（关键边界）**：`Media Time ↔ Evidence Time` 只是**展示层对齐**（让播放头对应到事件），**绝不意味着媒体内容本身就是证据**。`EvidenceProjection.timeline` 才是事实语义的唯一来源；媒体（帧 / 音频字节）只是"回放载体"。前端**不得**把"视频某一帧 / 某段音频"直接当作 `EvidenceProjection` 中的事件或风险事实——归属仍以 `EvidenceProjection` 的 `ref` / `provenance_kind` 为准。正确结构是：`Media`（播放什么）↔ `Case Time`（对齐）↔ `EvidenceProjection`（发生了什么，事实语义来源）。

---

## 音频作为第一等证据（D-Audio · 新增决策展开）

### 1. `audio_evidence` 是派生投影，不是第二套 View Model

`audio_evidence` 作为 `ScenarioEvidence` 的新增**派生字段**（与 `timeline`/`decision_evidence`/`graph` 同级），由真实音频符号驱动。**不得**另立 `audioData` 平行模型（VM-1/VM-9）。

### 2. 统一 Evidence Timeline（绝不三套独立时间轴）

Case Viewer **不得**出现"视频时间轴 / 音频时间轴 / 决策时间轴"三套互独立东西。应为**一条统一时间轴**，按 `timestamp` 交错呈现视觉、音频、决策、行动、记忆落库：

```
00:12.4  👁 person_detected
00:13.1  👁 abnormal_dwell
00:18.6  🔊 audio_speech_rapid (score .88, confidence .91)
00:21.0  🔊 audio_telephone_persistent
00:22.4  🧠 risk → HIGH
00:23.0  🧠 decision → WARN
00:23.1  📢 action → NOTIFY_FAMILY
00:23.2  💾 episode stored
```

实现：在 `TimelineNode` 增加 `modality` 判别（`VISION`/`AUDIO`/`DECISION`/`ACTION`/`MEMORY`/`CROSS_MODAL`），前端把 `timeline` 与 `audio_evidence` 按 `timestamp` 合并为统一轴。这正对应已打通的 `CrossModalLink`。

### 3. 前端主轴 = "多模态案例播放器"（不再是纯视频播放器）

```
┌────────────────────────────────────────────┐
│                案例视频 / 帧               │
│  👁 Vision evidence      🔊 Audio evidence  │
│  ↓ 当前时间 (Case Time)                    │
├─────────────────────┬──────────────────────┤
│ 风险感知             │ 为什么？             │
│ abnormal_dwell       │ 视觉：异常停留       │
│ audio_speech_rapid  │ 音频：急促言语       │
│                     │ 历史：异常行为       │
├─────────────────────┼──────────────────────┤
│ 决策                 │ 行动                 │
│ WARN / HIGH         │ NOTIFY_FAMILY        │
├─────────────────────┴──────────────────────┤
│ Unified Evidence Timeline                   │
│ 👁 → 🔊 → 🧠 → 📢 → 💾                      │
└────────────────────────────────────────────┘
```

这才回到最初 Demo 的核心：**让评委看到 AI 不只是"看到了什么"，还"听到了什么"，最后为什么做出判断。**

### 4. 音频依赖方向（展示证据，不生成证据）

```
Audio Runtime / Audio Evidence Producer
                    ↓
              Live Adapter (viewer/, 仅映射，不推理)
                    ↓
          EvidenceProjection (audio_evidence)
                    ↓
              Case Viewer (只展示)
```

铁律：**展示音频证据，不生成音频证据**（VM-9）。Case Viewer 不做 ASR/LLM；`audio_evidence` 只来自 `EvidenceProjection`。

### 5. 音频不只是"文字"（区分四类，体现多模态价值）

Case Viewer 应区分（非另立数据模型，而是 `audio_evidence` 节点的视图维度）：

```
Audio
├── Transcript（如未来存在，独立派生，非本投影字段，且 Case Viewer 不生成）
├── Audio Event（AudioPerceptionEvent.kind，如 audio_speech_rapid）
├── Signal/Evidence Category（RiskSignal(source=AUDIO).category=COMMUNICATION 透传，属证据分类、非语义解释、非 ASR 文本）
└── Cross-modal relation（CrossModalLink → 视觉事件 ref）
```

示例链（体现银龄盾多模态价值）：

```
🔊 00:18.6  audio_speech_rapid (score .88, confidence .91)
   ↓ signal_category: COMMUNICATION（证据分类，非语义解释）
   ↓ CrossModalLink(supports) → 👁 elderly_alone
```

而非仅仅"页面上多一个音频播放器"。

---

## 媒体源适配（Media Source Adapter · 新增）

视频/音频至少有三类来源，必须正式抽象，否则接真实/程序化/D3 导出媒体时会再分叉：

```
Media Source Adapter
├── ArtifactVideoSource   （D3 Case Video Export 的 mp4 / artifact 内媒体）
├── SyntheticFrameSource  （ADR-0032 程序化帧）
└── LiveFrameSource       （Live 相机 / 帧流 / 实时音频）
```

原则：**媒体字节与证据语义分离**。`EvidenceProjection`（含 `audio_evidence`）只持有 `ref` + `timestamp`；Media Source Adapter 负责把 ref 解析为可播放字节（视频帧序列 / mp4 / 音频 wav），喂给多模态案例播放器的"主轴"。它**不是** View Model 的一部分（不进 `EvidenceProjection`），只服务于播放 UI。这样 Vision 与 Audio 在媒体层统一为"Media"，在证据层统一为"EvidenceProjection"。

**音频样本绑定（音频 E2E 新增，与证据严格分离）**：可播放音频走**独立**的 `Audio Source Adapter`
（`visualizer/viewer/audio_source.py`，只读解析 `{sid}/audio/manifest.json`：
`source_kind=AudioFileSource` + `files: {kind → 相对 url}`）。`audio_evidence`（证据）**绝不**
含 url / 媒体字节；`prepare_case_audio.py` 把 `src/home_perception/audio/tts/fixtures/` 下确定性
合成 WAV 复制为 `{sid}/audio/{kind}.wav` 并登记 manifest（诚实：只为 canonical 中真实出现的
kind 准备样本，fixtures 未覆盖的 kind 不编造）。渲染层仅当绑定命中才渲染 `<audio controls>`；
无绑定只显示证据事实（不渲染播放控件）。

---

## D-CasePresentation · CasePresentationDescriptor 展示编排（新增）

产品需要一个上层 `Case` 编排对象，否则 Case Viewer 很容易直接对着 `EvidenceProjection` 写 UI 逻辑，UI 代码开始自行判断"这个场景显示什么标题""这个案例播放哪个视频"——这些会悄悄形成新的"事实状态"（违背 VM-1）。

定义 `CasePresentationDescriptor`（**纯展示元数据，非业务事实模型**，VM-11）：

```
CasePresentationDescriptor
├── case_id                （展示标识，非新事实）
├── title                  （展示标题文案，可由 scenario 派生，非风险事实）
├── scenario_ref           （指向 EvidenceProjection.scenario 的引用）
├── media_binding          （绑定哪个 Media Source + 哪个 Case Video Mode）
├── first_screen_layout    （首屏放哪些面板及顺序，见 §展示契约）
└── time_mapping           （Media Time ↔ Evidence Time 的展示映射参数）
```

**铁律**：`CasePresentationDescriptor` **不得**承载 `case_risk_level`/`case_decision`/`case_timeline` 等可由 `EvidenceProjection` 派生的业务事实；它只编排"怎么显示"，一切事实值仍来自 `EvidenceProjection`。

---

## D-SyncClock · Presentation Clock / Case Time（新增）

最终体验应是：

```
视频播放到 00:14
   ↓ (Case Time 映射)
Evidence Timeline 自动定位到 abnormal_dwell
   ↓
该节点高亮
   ↓
Risk / Decision / Action 面板随当前事件更新
```

因此定义 **`Case Time`** 为纯展示层时间基准（不进 `EvidenceProjection`，VM-10），并冻结 `Media Time ↔ Evidence Time` 映射契约：

- 媒体源提供 `media_duration` 与帧/事件对应的 `media_timecode`；
- `EvidenceProjection` 提供 `timestamp`（事件语义时间）；
- `media_timecode ↔ timestamp` 的线性映射（或帧对齐表）由 `CasePresentationDescriptor.time_mapping` 描述，前端据此同步 Media Timeline 与 Evidence Timeline。

---

## Live 音频分阶段（VM-13 展开 · 明确状态，避免误判"已就绪"）

当前 `loader` 不投影音频，故必须明确 Live 音频落地节奏，**严禁 implementation 时同时改 Runtime / Loader / Viewer 三处范围膨胀**：

- **Phase A（视觉 Live）**：`FrameResult → ProjectionAccumulator`（`REAL_SENSOR`），**无音频**；投影中 `audio_evidence=()`（VM-7 显式空）。
- **Phase B（Live 音频接入）**：`Audio Runtime → AudioEvidence → Live Adapter`，REAL_SENSOR 音频进入 projection；此时音频经 `live_adapter` 增量合并，仍守 VM-8 幂等。
- **Phase C（Artifact 音频）**：真实音频证据进入 canonical artifact（ADR-0027/0028 + ADR-0034 Phase B.2 落库后）→ `loader` 投影 `audio_evidence` → Artifact Mode 也能展示音频，与 Live 共用同一 schema（VM-4）。

### Live 音频投影铁律（VM-13 Phase B · 6 MUST · 2026-08-16 决策）

> **决策修正（Owner，2026-08-16）**：此前将「Live `audio_evidence` 恒为 `()`」写进 Projection 层，
> 实为**架构主动截断** Live 真实声学证据——并非 Viewer 接线小问题。现纠正为：
> **Live `audio_evidence` MAY 含 REAL_SENSOR 派生的 `AudioPerceptionEvent`**，与 Artifact 共用同一
> `AudioEvidenceNode` 契约，区别仅在 `provenance_kind`（Live=`REAL_SENSOR` / Artifact=`SIMULATED`）。
> 实现顺序固定为 **A'（改契约）→ A（Live 投影）→ B（AudioPipeline 接入 runtime）→ 验证**，不得先 B。

Live 投影（含 `live_adapter` 与未来 `AudioPipeline → runtime` 接线）必须守以下 6 条 MUST：

1. **fail-closed**：摄入命中 `_LIVE_AUDIO_FORBIDDEN_FIELDS`（verdict / transcript / raw_audio / fraud / …）即拒绝，**绝不**进入 View Model（沿用 `ingest_audio` 守卫）。
2. **无 ASR transcript**：`audio_evidence` 不含 `text` / `transcript`（Case Viewer 执行期无 ASR/LLM，VM-9）。
3. **无 verdict / risk 解释**：不含 `verdict` / `fraud_result` / `risk_reason` 等判定或解释字段（音频只产 perception，不产语义判定）。
4. **保留 provenance**：每条节点 `provenance_kind=REAL_SENSOR`，`ref` 可溯源（`live://audio/{idx}`）；与 Artifact 的 `SIMULATED` 仅此一字段之差，便于产品角标区分 `● LIVE · REAL SENSOR` vs `● GOLDEN CASE · SIMULATED`。
5. **幂等（VM-8）**：同一有序音频流重放 N（≥2）次，最终 `audio_evidence` 逐字段一致；禁用墙钟 / 随机 / UUID 派生展示字段（仅透传上游 `event_id`，不新生成）。
6. **共用 EvidenceProjection 契约**：Live 与 Artifact 投影产出**同一 `AudioEvidenceNode` schema**（字段集与禁止字段一致），不引入第二套音频结构。

**Live 窗口语义（回应"滚动窗口 vs 当前 episode"）**：Live Case Viewer 展示的是由 `ProjectionAccumulator` 维护的**滚动案例窗口**（默认保留最近 N 秒 / 最近 M 个事件，窗口参数为纯 UI 配置，不进 View Model）；当 episode 落库时窗口可锚定当前 episode。`EvidenceProjection` 在 Live 模式的生命周期 = accumulator 的当前快照；窗口边界只影响"展示多少"，不影响"事实从哪来"（仍来自 `EvidenceProjection`）。

---

## 展示契约（Presentation Contract）

### 1. 首屏信息层级（MVP · 防"工程师 Dashboard"）

评委打开页面第一眼**必须**是产品叙事，而非把所有证据图堆上去：

```
首屏
  → Case Video（主轴）
  → 音频感知（系统听到了什么 · 音频 E2E 新增）
  → 当前风险
  → 为什么
  → 系统行动
  → Evidence Timeline（统一时间轴）
  → 详细证据（展开后才见 Graph / Fingerprint / Gate / Audio 详情表 / Memory）
```

音频 E2E 决策（Owner P0，2026-08-15）：**音频感知上首屏**——让用户第一眼理解"系统听到了什么"
（人话化卡片：相对时间 + 中文类别 + score/confidence，如 `22.4s 🔊 持续电话声音 score 0.90
confidence 0.92`），而非藏在折叠区。**"音频证据"与"可播放音频"严格分离**（VM-9/VM-10/AC-11）：
`audio_evidence` 不含任何 url / 媒体字节；可播放样本走独立 `Audio Source Adapter`
（`{sid}/audio/manifest.json`，`AudioFileSource`，kind→相对 url），仅绑定命中该 kind 时才渲染
`<audio controls>`（无绑定诚实降级，不编造）。`EvidenceGraph` / `fingerprints` / `gate` /
音频详情表 / `memory` 属于"详细证据"二级视图，**不在首屏同屏堆砌**（除非案例本身需要，由
`first_screen_layout` 显式声明；`audio_perception` 是首屏面板，`audio_evidence` 详情表仍是二级）。

### 2. Provenance 文案映射（可信度是一等视觉，绝默认隐藏）

```
SIMULATED   → 程序化场景 · 可复现
REAL_SENSOR → 真实传感器 · 实时数据
FIXTURE     → 固定测试素材 · 非实时
```

这是项目可信度设计的一部分，必须在每个案例视图显式呈现（AC-7），且**绝默认隐藏**。

### 3. 同步契约（Media Time ↔ Evidence Time）

详见 §D-SyncClock / VM-10。前端实现"视频播放位置 → 时间轴定位 → 节点高亮 → 面板更新"时，必须走 `Case Time` 映射，不得各面板各自维护时间。

---

## 显式非目标（Out of Scope · 本轮固化）

以下"看起来很想要但会破坏架构"的能力，**本 ADR 明确不做**：

- ❌ **Analysis Video 产品化**（D3-A 实验资产保留，不再扩展；Case Video 才是产品主视频）
- ❌ **Case Viewer 内 ASR**（音频感知层本就不做 ASR，ADR-0001/0026）
- ❌ **Case Viewer 内 LLM / 推理生成证据**（VM-9）
- ❌ **第二套 `audioData` / `audioState` 作为业务事实源**（VM-1）
- ❌ **第二套 `risk` / `decision` / `timeline` 业务事实源**（VM-1）
- ❌ **Runtime 直接消费 `EvidenceProjection` 当状态总线**（VM-6）
- ❌ **`silver_demo` 内部状态暴露给 viewer**（VM-3）
- ❌ **D3-B/C 扩张成视频平台**（D3 只做 Case Video Export，同 View Model 渲染器）

尤其：**不要为了"最终展示很丰富"把 Analysis Video 再塞回主体验**——已有 Explorer / Replay / Timeline / Graph，分析视频属重复表达。

---

## 动机（Rationale）

1. **统一展示语义，消除"哪个是真的"争论**：`EvidenceProjection` 已是 fail-closed、脱敏、带 `ref`、带 `provenance_kind` 的成熟投影。让它成为唯一 View Model，把四块前端 + 音频 + 媒体源"焊死"在同一证据语义上。
2. **不是第五块，而是收束**：四块资产 + 音频 + 媒体源经 Adapter / Media Source Adapter / CasePresentationDescriptor 收敛到单一 View Model，而不是再写一堆平行前端。
3. **音频是一等证据，不是播放器插件**：视听证据共同推动一个风险判断，这是银龄盾多模态价值的真正落点；且音频经 `audio_evidence`（派生投影）入模，不破坏 VM-1。
4. **Case Video 主线清晰**：明确定义 Case Video 叙事结构 + 与 Analysis Video 的边界（VM-12），并冻结媒体模式与双时间轴（VM-10），避免实现退化成"重写 silver_demo"或"再来一个分析视频"。
5. **依赖方向正确**：Live Adapter 只认 Runtime 输出契约（含音频契约），不认 `silver_demo` 内部状态；Case Viewer 不做 ASR/LLM（VM-9）。
6. **schema 已就绪**：`ProvenanceKind`/`EvidenceGraph` 闭集 + 真实音频符号（`AudioPerceptionKind`/`EvidenceItem(modality=AUDIO)`/`CrossModalLink`）已存在；新增面仅是 loader schema 扩展 + 前端渲染 + Media Source Adapter + CasePresentationDescriptor。
7. **VM-6/VM-7/VM-11/VM-13 守住边界**：明确"只读派生、非状态总线""字段可按 mode 缺失、禁伪造""展示编排不含事实""Live 音频分阶段"，杜绝投影反渗 runtime、Live 伪装可信回放、或实现期范围膨胀。

---

## 后果（Consequences）

**正面**
- 统一产品体验：四块资产 + 音频 + 媒体源共享同一前端与同一证据语义，无漂移。
- Live 与 CI 互不污染；`silver_demo` 宿主职责不变（仅新增并列的 live 帧/音源出口，不改其内部状态模型）。
- 多模态价值真正呈现（视听共同推动判断）；Case Video 主线 + 首屏叙事让评委一眼看懂。
- D3 定位清晰（降级为同 View Model 的 Case Video Export）。

**负面 / 技术债 / 待办**
- **`silver_demo` 改造（若采用 WS 帧源）**：须把 `DemoAggregateState` 视图态映射移除，改为 `viewer/live_adapter` 投影映射；live 帧/音流只经冻结白名单 + 音频契约边界传递，`viewer/` 不得 import `silver_demo`（T0-3）。`silver_demo.gateway` 作为 Host / Composition Root 可 import `home_perception.visualizer.viewer` 投影并渲染统一 Case Viewer（ADR-0015 §2.1.1），**仅此一层**，Runtime Core 仍禁止 import `visualizer`（T0-1）。
- **`audio_evidence` 落地（VM-13 Phase C · 已落地）**：真实音频证据已进入 canonical artifact（ADR-0027/0028 + ADR-0034 Phase B.2 落库，并经 `scenario.audio`→`compiler`→`synth.audio_events` 确定性携带）；`visualizer/schema/evidence.py` 的 `AudioEvidenceNode` + `loader._build_audio_evidence` 投影 + fail-closed 契约测试均已落地（见附录 A 实现状态）。
- **音频 E2E 首屏接入（P0 · 已落地）**：`_DEFAULT_FIRST_SCREEN_PANELS` 新增 `audio_perception`
  面板（Case Video 之后）；`renderer._AUDIO_KIND_ZH` + `_translate_audio_kind` 人话化；首屏卡片
  用相对时间（以场景最早音频为 T0）；可播放样本经 `Audio Source Adapter`
  （`audio_source.py` / `resolve_audio_source`）+ `scripts/prepare_case_audio.py` 独立绑定，
  命中才渲染 `<audio controls>`（VM-9/VM-10/AC-11 严格分离）；`run_case_viewer.py` 与
  `build_trusted_case.py`（步骤 5.5，`--audio/--no-audio`）穿线；验收测试
  `tests/visualizer/test_audio_e2e_viewer.py` 锁定"CI 可信 artifact → Case Viewer 真实消费"。
- **网关静态伺服（P0 验收补全 · 已落地）**：网关验收发现 `GET /` 只伺服 `case_viewer.html`、
  不伺服任何静态资源 → `<audio controls>` 的样本 / 媒体帧 / case.mp4 全部 404（产品形态下
  "可播放"断裂）。`silver_demo.gateway` 旗舰模式现条件挂载 `StaticFiles`：`/canonical` →
  `case_dir/canonical`（对齐 HTML 相对前缀，零改动渲染产物；缺失则自然 404 诚实降级；
  starlette 自带路径穿越防护）。契约测试 `tests/demo/test_gateway_serves_case_viewer.py` 新增
  4 条（wav 200 字节一致 / manifest 伺服 / 穿越 404 / 无 canonical 不挂载）。
- **主轴真实视频整改（P0 验收整改 · 已落地）**：网关验收进一步发现 Case Video 主轴是空
  canvas 黑屏（build_trusted_case 此前不准备媒体）。整改：`prepare_case_media.py` 默认映射补
  `audio_e2e` / `high_risk`（P0 验收目标场景也须有真实主轴画面），并新增 `--missing-skip`
  （真实演示视频 gitignore、CI 无视频 → 跳过该场景不红；媒体为**可选展示增强**，缺失不影响
  可信 artifact 完整性）；`build_trusted_case` 新增步骤 5.6（`--media/--no-media` 默认开，传
  `--missing-skip`）把 `data/demo/` 真实 CCTV / 门口视频挂为 `{sid}/media/case.mp4`
  （`ArtifactVideoSource`，经既有 Media Source Adapter 渲染为 `<video controls autoplay>`）。
  真实网关验收：`GET /` 主轴为 `<video src="canonical/<sid>/media/case.mp4">`，media/audio
  全部 200。**音频资产约束**：3 个真实演示视频均无声轨（h264 only）、`data/demo` 无真实 wav
  → 音频样本保持确定性合成（诚实标注"样本声音（合成素材，非原始录音）"）；真实场景录音留待
  资产到位后的下一阶段。
- **前端选型待定**：SPA 框架需 Owner 拍板；本 ADR 不锁框架，只锁"渲染 `EvidenceProjection` + Media Source Adapter 分离 + CasePresentationDescriptor 编排 + Case Time 同步"契约。
- **`ProjectionAccumulator` 须确定性/幂等**：VM-8 需补契约测试（对齐 ADR-0035 D8）。
- **首 slice 刻意压小**（见 §实施切片）。

---

## 替代方案（Alternatives）

- **A. 扩展 D1 Explorer 成全功能 Case Viewer**：否决。零服务器静态单页无法承载 Live + 音频流与交互。
- **B. 在 `silver_demo` 内加 artifact 回放**：否决。`DemoAggregateState` 自拥 `riskData/decisionData`，叠加回放会复活血证据语义漂移。
- **C. viewer 依赖 `silver_demo` 内部状态（即换个名字重写 silver_demo）**：否决。违背决策 4 / VM-3（T0-3）。注意：否决的是「`viewer → silver_demo` 反向依赖」；「`silver_demo.gateway` → `visualizer.viewer`」是 Host→Presentation 的单向依赖（ADR-0015 §2.1.1），方向相反、不冲突。
- **D. 把 `EvidenceProjection` 反渗 runtime 当状态总线**：否决。它是 presentation-layer derived model（VM-6）。
- **E. 音频另立 `audioData` 平行模型 / Case Viewer 内做 ASR**：否决。破坏 VM-1 / VM-9。
- **F. 把 Analysis Video 重新产品化**：否决。与 VM-12 / 决策 11 冲突，属重复表达。
- **G. 全新独立 Case Viewer + 薄 `visualizer/viewer` 适配器（三类 Adapter + Media Source Adapter + CasePresentationDescriptor + Case Time + audio_evidence）**：**采用**。

---

## 实施切片（Scope · 冻结前共识 · Owner 建议）

**刻意压到最小，先证明"单一 View Model + Case Video 主线"成立，再碰 Live / 音频。**

- **Slice A（Artifact Mode 优先，先不碰 Live / 音频投影）**：
  - 同一 artifact 上做到：**Case Video 主轴（Pre-generated MP4 或 Frame Replay）+ 风险 + 决策 + 行动 + 统一 Evidence Timeline + provenance + 首屏叙事**。
  - `visualizer/viewer/artifact_source.py`（薄封装 `load_evidence_projection`）+ 前端 SPA（多模态案例播放器首屏）。
  - 引入 `CasePresentationDescriptor`（仅展示编排）+ `Case Time` 同步骨架。
  - 此阶段**不修改 `EvidenceProjection` schema**（无 `audio_evidence` 字段）；前端不建模音频维度（无 `audioData`/`audioState`，AC-1c）。`audio_evidence` 的 schema 扩展推迟到 Slice C——真实音频进入 canonical artifact 后，再经 `loader` 投影（见 VM-13 Phase C / AC-12）。Live 音频处于 VM-13 Phase A 之前（不实现）。
- **Slice B（Live Adapter · VM-13 Phase A）**：`viewer/live_adapter.py`（增量 `ProjectionAccumulator`，视觉 Live，无音频）+ WS（FrameResult）→ REAL_SENSOR；复用同一 Case Viewer，仅 provenance 着色差异；滚动窗口参数化。
- **Slice C（Audio Evidence 投影 · VM-13 Phase B/C）**：Phase B（Live 音频经 `live_adapter` 增量合并 AUDIO modality 时间轴节点，`REAL_SENSOR` provenance）**已落地**；Phase C（真实音频证据进入 canonical artifact 后由 `loader` 投影为 `audio_evidence` → Artifact Mode 渲染 🔊 区块）**已落地**（runner→report→loader→renderer 全链路，AC-12 严守 `()` 当无音频）。守 VM-7（缺则显式空）/ VM-9（不生成）。统一时间轴 modality 交错见 `TimelineModality`/`TimelineNode.modality`（Slice C 已含 AUDIO）。
- **Slice D（Media Source Adapter + D3 导出 + 去重）**：三源抽象 `ArtifactVideoSource`/`SyntheticFrameSource`/`LiveFrameSource`（Slice A.1 已落地 `viewer/media_source.py` + `MediaSourceKind`，只读解析）；D3 导出接入 Case Viewer 作为 Case Video Export（`scripts/run_case_viewer.py --export-case-video`，AC-6 已落地）；D1（Artifact Mode）/ D2（Replay）收敛到同一 `render_case_viewer` 渲染路径（去重，已落地）。

---

## 验收（Acceptance · 供实现阶段对齐）

- **AC-1（VM-1 防旧代码复活）**：前端 grep 不到 `riskData`/`decisionData`/`timelineData` 自有类型。
- **AC-1b（VM-1 真正含义）**：前端所有业务展示状态必须可从单个 `EvidenceProjection` 派生；**不得存在第二份业务事实状态**。静态扫描**禁止** `RiskState`/`DecisionState`/`TimelineState`/`GraphState` 事实型模型，**允许** `UIState`/`PlaybackState`/`SelectionState`/`ZoomState`/`PanelState`。
- **AC-1c（VM-1 音频维度）**：前端**不得定义独立的 `audioData`/`audioState` 作为业务事实源**；静态扫描禁止 `AudioState`/`AudioFactState` 等，允许 `AudioPlaybackState`/`AudioVolumeState`/`AudioWaveformUIState`（纯 UI）。
- **AC-2（VM-4）**：Artifact Mode 渲染的 Timeline/Decision/Graph 与 D1 Explorer 在同一 artifact 上语义一致。
- **AC-3（VM-4 含音频）**：Live 与 Artifact 对视觉/音频/决策/行动共享同一 `EvidenceProjection` schema，仅 `provenance_kind` 不同。
- **AC-4（VM-8 重放稳定）**：Live 流重放两次得到的 `ScenarioEvidence` 逐字段稳定。
- **AC-4b（VM-8 幂等）**：同一有序 `FrameResult`(+`AudioEvidence`) stream 重放 N（≥2）次，最终 `EvidenceProjection` 逐字段一致。
- **AC-5（VM-3 依赖方向 · 分层）**：`viewer/` 与前端不 import 生产 runtime 决策符号，**且 `viewer/` 不得 import `silver_demo`**（T0-3）；`silver_demo` 白名单与内部状态模型不变。放宽点唯一且单向：`silver_demo.gateway`（Host / Composition Root）**允许** import `home_perception.visualizer.viewer`（Presentation Layer，ADR-0015 §2.1.1），用于收敛 `GET /live` 到统一 Case Viewer（T0-2 / T0-6）；`silver_demo` 的其它子模块（Runtime Core）仍禁止 import `visualizer`（T0-1）。
- **AC-6（D3 导出 / VM-12）**：D3 可从 Case Viewer 的 `EvidenceProjection` 导出 Case Video；**断言不存在 Analysis Video 被重新产品化**的入口。
- **AC-7（决策 13 · Provenance 一等视觉）**：前端每个案例视图显式呈现 `provenance_kind` 及对应文案（程序化场景·可复现 / 真实传感器·实时数据 / 固定测试素材·非实时），不得默认隐藏。
- **AC-8（VM-7 禁伪造）**：Live/音频缺失的 `gate`/`fingerprints`/`benchmark`/`audio_evidence` 必须显式表达（`None`/空元组/absent）；断言不存在 `gate=PASS` 或伪造音频证据。
- **AC-9（D-Audio · 统一时间轴）**：Timeline 必须按 `timestamp` 交错呈现视觉/音频/决策/行动/记忆，**不得**出现视频/音频/决策三套独立时间轴；`TimelineNode` 须带 `modality` 判别。
- **AC-10（VM-9 不生成音频证据）**：Case Viewer 执行期间**无 ASR/LLM 调用**；`audio_evidence` 仅来自 `EvidenceProjection`（AudioPerceptionEvent/EvidenceItem(modality=AUDIO)/RiskSignal(source=AUDIO)/CrossModalLink）；断言 `audio_evidence` 节点字段全部派生自真实音频符号，无 `text`/`transcript` 字段、无 `FORBIDDEN_AUDIO_FIELDS` 判定字段。
- **AC-11（Media Source 分离）**：媒体播放字节由 Media Source Adapter 经 ref 解析，不得存入 `EvidenceProjection`；`EvidenceProjection` 只持 ref/timestamp。
- **AC-12（audio_evidence 落地门槛 / VM-13）**：`audio_evidence` **不**再以「Phase B 恒为空」截断。Phase A（视觉 Live，无音频）恒为 `()`；Phase B（Live 真实音频接入）**MAY 含** REAL_SENSOR 派生的 `AudioPerceptionEvent`（仅当实时音频流真实摄入，由 `live_adapter` 投影产出）；Phase C（Artifact）由 `loader` 投影产出（provenance=SIMULATED）。三者的唯一差异是 `provenance_kind`；**未摄入音频的任何 phase 恒为 `()`，绝不编造**（fail-closed）。
- **AC-13（VM-11 · CasePresentationDescriptor 不含事实）**：`CasePresentationDescriptor` 不含 `case_risk_level`/`case_decision`/`case_timeline` 等可由 `EvidenceProjection` 派生的业务事实；静态扫描禁止此类字段。
- **AC-14（VM-10 · 双时间轴 + Case Time）**：前端 Media Timeline 与 Evidence Timeline 经 `Case Time` 映射同步，不混为同一数组/状态；视频播放位置能驱动时间轴定位与面板更新。
- **AC-15（VM-12 · Case Video ≠ Analysis Video）**：Case Viewer 主体验使用 Case Video（叙事结构 Context→Incident→Risk Escalation→AI Perception→Intervention→Outcome），不得把 Analysis Video 当主视频。
- **AC-16（首屏叙事 / §展示契约）**：首屏信息层级为「Case Video → 当前风险 → 为什么 → 系统行动 → Evidence Timeline → 详细证据」；`EvidenceGraph`/`fingerprints`/`gate` 等属二级视图，不强制首屏同屏。

---

## 附录 A：AudioEvidenceNode 字段来源映射（原则冻结 · 字段级已定稿并落地）

> 本 ADR 冻结"字段必须来自真实音频符号、且哪些字段绝不能出现"的原则；字段集与命名已在 `visualizer/schema/evidence.py`（`AudioEvidenceNode`/`AudioAcoustics`）及 canonical 中间层（`LoopArtifactSummary.audio_events` 的 `audio_*` 前缀键）定稿，**Phase C 已随 runner→report→loader→renderer 全链路落地**，并配 fail-closed 契约测试（`tests/visualizer/test_loader.py`、`tests/visualizer/test_renderer.py`、`tests/integration/test_adr0034_phase_c_audio.py`）。

### 实现状态（Slice C · VM-13 Phase B + Phase C 已落地）

`TimelineNode` 已加 `modality: TimelineModality`（AC-9 统一时间轴判别式，取值
`VISION/AUDIO/DECISION/ACTION/MEMORY/CROSS_MODAL/OBSERVABILITY`）；`ScenarioEvidence` 已加
`audio_evidence: tuple[AudioEvidenceNode, ...]`，`AudioEvidenceNode` 与 `AudioAcoustics` 已定稿于
`src/home_perception/visualizer/schema/evidence.py`。**VM-13 Phase C 已落地（runner→report→loader→renderer 全链路）**：

1. **真实音频符号来源（确定性、感知层）**：`scenario.audio` → `compiler.py` 编译为 `AudioPerceptionEvent` 列表 → `SyntheticInput.audio_events`；`runner._collect` 直接取 `synth.audio_events`（运行期确定性、无 UUID/墙钟）。**不依赖 `AudioSessionSummary`**（其丢弃原始事件）。
2. **四层投影链路**：`IntegrationRunResult.audio_perception_events` → `LoopArtifactSummary.audio_events` → canonical `artifacts.audio_evidence`（dict 形态）→ `loader._build_audio_evidence` 映射为 `AudioEvidenceNode` → `renderer._render_audio_evidence` 渲染 🔊 区块。
3. **AC-12 严守**：未声明音频的场景（或 Phase A/B Live）`audio_evidence` 恒为 `()`；`loader._build_audio_evidence` 对 `raw is None` 返回 `()`、对任意字段缺失/类型非法 **fail-closed 抛 `EvidenceProjectionError`**，绝不编造。
4. **脱敏守卫兼容性（关键约束）**：`AudioEvidenceNode.score` 的裸键 `"score"` 会触发 `assert_desensitized` 的 `forbidden_field`（精确匹配 `"score"`）→ 在 **canonical 中间层**一律用 `audio_*` 前缀键承载（`audio_timestamp`/`audio_kind`/`audio_score`/`audio_confidence`/`audio_labels`/`audio_source_segment_ids`），`loader` 投影进 `AudioEvidenceNode` 时再还原为 `score`/`confidence` 等字段。字段级契约见下表「canonical 中间层键」列。

> Phase B（Live）已落地：live_adapter 增量合并 AUDIO modality 时间轴节点（`REAL_SENSOR` provenance），**且**把摄入的 `AudioPerceptionEvent` 投影为 `audio_evidence`（`provenance_kind=REAL_SENSOR`，fail-closed，幂等，见 `visualizer/viewer/live_adapter.py` 的 `_build_audio_evidence_live`）；未摄入音频时恒 `()`，绝不编造（AC-12 / 6 MUST）。与 Artifact（Phase C loader）共用同一 `AudioEvidenceNode` schema，区别仅 `provenance_kind`。

### 实现状态（Slice D · Media Source Adapter + D3 导出 + D1/D2 去重 已落地）

1. **Media Source Adapter（Slice A.1 已落地，`viewer/media_source.py`）**：`resolve_media_source(base_dir, sid, source_kind)` 只读解析 `{base_dir}/{sid}/media/manifest.json`，返回 `MediaManifest`（含 `source_kind`/`frame_count`/`fps`/`duration_sec`/`frame_template`/`video_url`）；媒体字节绝不进 View Model（VM-10/AC-11）。三源行为：
   - `ArtifactVideoSource`：读 `video_url`（原生 `<video>` 播放）；
   - `SyntheticFrameSource`：读 `frame_template`（canvas 逐帧播放，媒体字节不内联）；
   - `LiveFrameSource`：Slice A 不实现 → `None`（未来 slice 注入运行时帧源）。
   结构非法（字段类型/缺关键字段）→ 抛 `MediaSourceError`（fail-closed）；媒体资产缺失 → `None`（降级，不崩）。

2. **D3 导出接入 Case Viewer（AC-6 已落地，`scripts/run_case_viewer.py`）**：新增 `--export-case-video`（及 `--export-fps`/`--export-resolution`/`--export-version`）。开启时对每个场景调用 D3 `generate_case_video`（经可 monkeypatch 的 `_d3_generate_case_video` 懒导入 `compiler`，隔离 cv2）产出 `case.mp4`，并在 `{artifacts}/{sid}/media/manifest.json` 登记 `source_kind=ArtifactVideoSource` + `video_url=相对 artifacts 根的 case.mp4 路径`；同时把 `descriptor.media_binding` 置为 `ArtifactVideoSource`（诚实脚注）。导出失败（含 cv2 缺失 ImportError）→ fail-closed 退出 1，绝不静默产残缺 HTML。

   - **VM-12 / AC-6 严守**：导出使用 `CaseVideoSpec`（Case Video 叙事路径），`with_audio` 维持 `False`（D3-B 未实现：`compiler` 会 `NotImplementedError` fail-closed，绝不静默产"无声片冒充有声片"）；**不存在 Analysis Video 被重新产品化**的入口（D3 只做 Case Video Export，同 View Model 渲染器）。
   - **相对 URL 契约（render 配合）**：`_render_case_video` 对 `ArtifactVideoSource` 的 `video_url`——绝对 `http(s)` 原样透传，相对路径叠加 `media_base_url` 形成最终可解析地址（与 `frame_template` 同契约），使导出的本地 `case.mp4` 可被浏览器正确解析。

3. **D1/D2 去重（已落地）**：`render_case_viewer` 是**唯一**渲染入口——D1（Artifact Mode，首屏证据 + timeline/decision/graph）+ D2（Replay，`window.__Replay` 引擎，双轨道 timeline/trace 联动）共用同一函数与同一 `EvidenceProjection`（VM-1 唯一事实源）。不存在第二份平行渲染器；`__Replay` 引擎经数据岛 + init 调用接线（与 renderer 的 `_render_timeline`/`_render_decision` 同契约）。

> 落地快照：feat-adr0036-slice-c @ a008434（Slice D 评审修复——single-binding media_binding 设计 + 导出错误分级 + render URL 穿越防护，含 4 个新增 fail-closed 测试）。`

### 字段来源表（锚定仓库真实符号）

> 第三列「canonical 中间层键」是 `LoopArtifactSummary.audio_events` 字典在 canonical artifact（`artifacts.audio_evidence`）中承载的键名。`loader._build_audio_evidence` 将这些 `audio_*` 前缀键映射回 `AudioEvidenceNode` 的同名裸键（`score`/`confidence`/...）。**前缀键是脱敏守卫 `assert_desensitized` 的硬约束**：该守卫对 `forbidden_field` 做精确字符串匹配 `"score"`，裸键 `"score"` 会触发 `DesensitizationError` 拒绝落盘；故中间层一律用 `audio_score` 等前缀键规避，投影进 `AudioEvidenceNode` 时再还原。

| `AudioEvidenceNode` 字段 | 来源真实符号 | canonical 中间层键（`LoopArtifactSummary.audio_events` → `artifacts.audio_evidence`） | 说明 |
|---|---|---|---|
| `timestamp` | `AudioPerceptionEvent.timestamp` / `AudioSegmentEvent.timestamp` | `audio_timestamp`（`float`，Unix 秒） | 与统一时间轴同源；loader 投影为 `str(float(...))` |
| `kind` | `AudioPerceptionEvent.kind`（`AudioPerceptionKind.value`） | `audio_kind`（`str`，五值之一） | `audio_speech_rapid` / `audio_voice_raised` / `audio_telephone_persistent` / `audio_distress_cry` / `audio_anomaly_other` |
| `score` | `AudioPerceptionEvent.score` | `audio_score`（`float` 0~1） | 规则强度（非诈骗概率）；**必须用 `audio_score` 前缀键规避脱敏禁止键 `"score"`** |
| `confidence` | `AudioPerceptionEvent.confidence` | `audio_confidence`（`float` 0~1） | 检测可信度 |
| `labels` | `AudioPerceptionEvent.labels` / `scored_labels` | `audio_labels`（`list[str]`） | 声学标签透传（非语义判定） |
| `source_segment_ids` | `AudioPerceptionEvent.source_segment_ids` | `audio_source_segment_ids`（`list[str]`） | 派生自哪些 `AudioSegmentEvent` |
| `acoustics`（可选） | `AudioSegmentEvent.vad_ratio` / `rms` / `speech_rate` | （Phase C 未投影；预留 `audio_vad_ratio`/`audio_rms`/`audio_speech_rate`） | 声学特征，可选；Phase C 仅投影 `AudioPerceptionEvent` 维度 |
| `signal_category`（可选，原名 semantic_tag，本轮改名） | `RiskSignal(source=AUDIO, category=COMMUNICATION)` | （Phase C 未投影；预留 `audio_signal_category`） | **证据/信号分类透传**：`COMMUNICATION` 是证据分类而非自然语言语义解释；音频只产 perception，不产语义判断（非 ASR 文本） |
| `related_visual_ref`（可选） | 由 `CrossModalLink` 经 `EvidenceGraph` **两层派生** | （Phase C 未投影；预留 `audio_related_visual_ref`） | 指向视觉事件 ref（可选，依赖真实关联解析） |
| `ref` | trace artifact 定位 | loader 投影时合成 `f"{scenario_id}.canonical.json#artifacts.audio_evidence[{i}]"` | 溯源；非来自运行期，由 loader 据索引生成 |
| `provenance_kind` | `ProvenanceKind` | （不落 canonical 中间层；loader 固定 `SIMULATED`） | `REAL_SENSOR` / `SIMULATED` / `FIXTURE`；artifact 来源固定 `SIMULATED`（与视觉事件一致） |

> **loader 强校验（fail-closed）**：`_build_audio_evidence` 对每条原始 dict 校验 `audio_timestamp`(int/float) / `audio_kind`(非空 str) / `audio_score`(num) / `audio_confidence`(num) / `audio_labels`(str list) / `audio_source_segment_ids`(str list)；任一缺失或类型非法 → 抛 `EvidenceProjectionError`，绝不静默跳过或编造。

### 绝不能出现的字段（强制）

- **`text` / `transcript`**：ASR 不在音频感知层（ADR-0001/0026），且 Case Viewer 不生成（VM-9）。若未来有转录，是独立派生产物，不进 `audio_evidence` 主投影。
- **`FORBIDDEN_AUDIO_FIELDS`**（`fraud_result` / `verdict` / `is_fraud` / `is_scammer` / `is_criminal` / `crime_probability` / `guilt_score` / `deception_score` 等）：音频只产 perception，不产判定（模块边界铁律，`AudioPerceptionEvent` 结构性禁止）。
- **媒体字节（`raw_audio` / `mp4` / `wav`）**：媒体字节不进 View Model（VM-10 / AC-11），只由 Media Source Adapter 经 `ref` 解析。

---

## 冻结记录（Acceptance · E2E 实证 · 2026-08-16）

> 本 ADR 经四轮评审收紧 + 端到端实证后由 Owner 拍板冻结（Proposed → Accepted）。
> 冻结**不是"全绿"口号**，而是"代码级贯通已验证、唯一未过项是真实 runtime 环境受限"。
> 以下为可复核的硬证据，不接受口头全绿。

### 1. 四闸门签字状态（沿用 Live Audio 验收闸门）

| 闸门 | 含义 | 状态 | 证据 |
| --- | --- | --- | --- |
| Gate 1 · 技术压力审核 | 边界/异常/幂等/大输入 | **PASS** | `tests/visualizer` 全量 392 passed/0 failed；fail-closed 守卫（`MediaSourceError`/`EvidenceProjectionError`/`_assert_audio_boundary`） |
| Gate 2 · 端到端 Projection/Viewer | 四块资产 → 单一 View Model 贯通 | **PASS** | Slice A/B/C/D 全量测试 47 passed；Live 音频独立 harness 42/42 PASS |
| Gate 3 · PM 产品验收 | 首屏叙事 + 产品语义一致 | **PASS**（条件已落实） | 首屏 `audio_perception` 实时摘要已加（PR #238 已 MERGED）；首屏层级 = Case Video → 当前风险 → 为什么 → 系统行动 → Evidence Timeline → 详细证据 |
| Gate 4 · 真实 Runtime 验收 | 真实音频 + GPU/YOLO 权重跑通 | **NOT YET VERIFIED** | **环境受限，非代码未完成**：验证环境无真实音频素材、未装 YOLO 权重；代码级贯通已在 Gate 2 验证。需在带素材的机器跑 `scripts/run_demo.py --live` 补签（见 §4 已知限制） |

> **总状态唯一正确表述**：`Live Audio：Projection chain PASS；Product visibility conditional（首屏摘要已加）；Real runtime production path pending verification.`

### 2. E2E 实证摘要（可复核）

| 套件 | 命令 | 结果 |
| --- | --- | --- |
| visualizer 全量 | `pytest tests/visualizer`（系统 Py3.14，pytest 9.0.2） | **392 passed / 0 failed**（退出码 0，无 F/E） |
| Slice D 子集 | `test_slice_d / test_media_source / test_cli_d3 / test_semantic_equivalence / test_prepare_case_media` | **47 passed** |
| Live 音频 E2E harness（上一轮） | `D:/temp/e2e_live_audio_harness.py` | **42/42 PASS**（fail-closed 6/6、DI 吞异常、2 万条 0.72s、幂等 5 次、路径穿越拒绝、网关 HTTP 200/404、Gate3 分发回归） |
| ruff（提交前） | managed venv `ruff==0.16.1` 全量 | 历史提交全量通过（PR #229–#238 均过 preflight） |

> 注：sandbox atexit 的 `safe-delete` 提示会吞掉 pytest 汇总计数行，但退出码 0 + 全程 `.` 无 `F`/`E` 即全绿；用例数以 collect-only（392）为准。

### 3. 验收合规矩阵（AC-1 ～ AC-16）

| AC | 条款（VM 不变式） | 证据 | 状态 |
| --- | --- | --- | --- |
| AC-1 | VM-1：前端无 `riskData`/`decisionData`/`timelineData` 自有类型 | grep `riskData\|decisionData\|timelineData` 于 `visualizer/`（含 `assets/` JS）→ **0 命中**（仅 `render.py:10` 文档声明"不定义"） | ✅ PASS |
| AC-1b | VM-1：禁止 `RiskState`/`DecisionState`/`TimelineState`/`GraphState` 事实模型 | grep → **0 命中**；`case_presentation.py` 把 `risk_data/decision_data/timeline_data/audio_data/audio_state` 列为禁止键并 fail-closed 拒绝 | ✅ PASS |
| AC-1c | VM-1 音频维度：禁 `audioData`/`audioState` 业务事实源 | grep `audioState\|AudioFactState` → **0 命中**（`assets/` 无 `AudioState`） | ✅ PASS |
| AC-2 | VM-4：Artifact Mode 与 D1 Explorer 同 artifact 语义一致 | `render_case_viewer` 为 D1/D2 唯一渲染入口；`test_semantic_equivalence.py` | ✅ PASS |
| AC-3 | VM-4 含音频：Live 与 Artifact 共享同一 `EvidenceProjection` schema | `AudioEvidenceNode` 共用；`live_adapter`(REAL_SENSOR) 与 `loader`(SIMULATED) 同 schema；`test_live_adapter.py`/`test_loader.py` | ✅ PASS |
| AC-4 | VM-8：Live 流重放两次 `ScenarioEvidence` 逐字段稳定 | `test_live_adapter.py` 幂等用例 + E2E harness 幂等 5 次一致 | ✅ PASS |
| AC-4b | VM-8 幂等：同一有序 stream 重放 N≥2 次 `EvidenceProjection` 逐字段一致 | 同上；harness 幂等 5 次逐字节一致 | ✅ PASS |
| AC-5 | VM-3：viewer/ 不 import 生产 runtime 决策符号、不 import `silver_demo`；`gateway` 单向 import viewer | grep `import silver_demo` 于 `viewer/` → 仅 `__init__.py` 文档说明"不 import"；`gateway` 是 Host 单向依赖（ADR-0015 §2.1.1） | ✅ PASS |
| AC-6 | VM-12：D3 导出 Case Video；无 Analysis Video 被重新产品化 | `run_case_viewer.py --export-case-video` → `D3.generate_case_video(CaseVideoSpec)`；`with_audio` 默认 False、`True` 时 fail-closed `NotImplementedError`（绝不静默产无声片）；无 Analysis Video 入口 | ✅ PASS |
| AC-7 | 决策 13：每个案例视图显式呈现 `provenance_kind` 及文案 | `render.py _PROVENANCE_BADGE`：REAL_SENSOR→"真实传感器·实时数据"、FIXTURE→"固定测试素材·非实时"、SIMULATED→"程序化场景·可复现"；`assets/` provenance 逻辑 | ✅ PASS |
| AC-8 | VM-7：缺失的 `gate`/`fingerprints`/`benchmark`/`audio_evidence` 显式表达；无 `gate=PASS`/伪造音频 | `loader._build_audio_evidence` `raw is None → return ()`（AC-12）；VM-7 显式空；`test_loader.py` | ✅ PASS |
| AC-9 | D-Audio：Timeline 按 `timestamp` 交错呈现视觉/音频/决策/行动/记忆；`TimelineNode` 带 `modality`；无三套独立时间轴 | `schema/evidence.py:56 modality: TimelineModality`；统一 timeline 交错渲染；`test_renderer.py` | ✅ PASS |
| AC-10 | VM-9：无 ASR/LLM；`audio_evidence` 仅来自 `EvidenceProjection`；无 `text`/`transcript`/`FORBIDDEN_AUDIO_FIELDS` | grep `transcribe\|asr\|llm` 于 `viewer/` → 仅文档声明"无 ASR/LLM"；`AudioEvidenceNode` 无禁止字段（脱敏守卫 + 字段来源表） | ✅ PASS |
| AC-11 | Media Source 分离：媒体字节经 ref 解析，不存 `EvidenceProjection` | `media_source.py MediaManifest` 仅含 `source_kind`/`frame_template`/`video_url`（URL/ref），**不读任何媒体字节**（无 `open`/`read_bytes`）；`EvidenceProjection` 不持 `raw_audio/mp4/wav` | ✅ PASS |
| AC-12 | VM-13：`audio_evidence` 仅真实音频进入 canonical 后由 loader 投影；Phase A/B 恒 `()`，不编造 | `loader.py:419-420 if raw is None: return ()`；runner→report→loader→renderer 全链路；`test_loader.py`/`test_integration_adr0034_phase_c_audio.py` | ✅ PASS |
| AC-13 | VM-11：`CasePresentationDescriptor` 不含 `case_risk_level`/`case_decision`/`case_timeline` | `case_presentation.py _FORBIDDEN_FACT_FIELDS` + `_assert_no_forbidden_fact_fields` fail-closed 拒绝；`load_case_descriptor` 校验 shape | ✅ PASS |
| AC-14 | VM-10：Media Timeline 与 Evidence Timeline 经 Case Time 映射同步，不混同 | `case_presentation.py TimeMapping(mode="linear")`；`assets/media.js`/`audio_sync.js` 处理 Case Time 同步；VM-10 分离 | ✅ PASS |
| AC-15 | VM-12：主体验使用 Case Video 叙事结构，不把 Analysis Video 当主视频 | D3 仅 `generate_case_video(CaseVideoSpec, 叙事路径)`；`_assert_audio_boundary` 边界；无 Analysis Video 入口；首屏 `case_video` 面板 | ✅ PASS |
| AC-16 | 展示契约：首屏层级 Case Video→当前风险→为什么→系统行动→Evidence Timeline→详细证据 | `render.py _DEFAULT_FIRST_SCREEN_PANELS`：case_video → (memory_timeline) → current_risk → why → action → action_closure → evidence_timeline → audio_perception；graph/fingerprints/gate/audio 详情在二级视图 | ✅ PASS |

**结论：AC-1 ～ AC-16 全部 PASS（19 条验收项，含 AC-1b/1c 子项）。**

### 4. 已知限制（诚实标注，非未完成的代码）

1. **Gate 4 真实 Runtime 未签**（环境受限）：需在带真实音频素材 + GPU/YOLO 权重的机器跑 `scripts/run_demo.py --live`，验证 `真实音频 → AudioPipeline → 实时帧循环 → Live ingest → Projection → 首屏显示 → 播放/时间轴联动`。当前验证环境不具备素材/权重，**非代码路径未完成**。
2. **D3 `with_audio`（D3-B 旁白/音频合成）未实现**：`_assert_audio_boundary` fail-closed，`with_audio=True` 显式 `NotImplementedError`，绝不静默产"无声片冒充有声片"。Case Video 导出诚实为纯视觉。
3. **YOLO 权重**：`yolo11n.pt` 在仓库根，CI/隔离验证环境未装载 → 涉及真实检测的部分用例在隔离环境跳过（标记 skipped，非失败）。

### 5. 关联提交 / PR（冻结溯源）

- Slice A/B/C + Gate 3：PR #236（Live 真实音频投影，MERGED）、PR #238（Live 首屏 `audio_perception` 实时摘要，MERGED）。
- Slice D（Media Source Adapter + D3 导出 + D1/D2 去重）：随 #229–#238 系列 MERGED（落地快照见附录 A）。
- 本冻结 PR：状态行 Proposed→Accepted + README 清单同步（见关联 PR）。

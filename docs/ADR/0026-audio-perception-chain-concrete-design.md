# ADR-0026: 音频感知链路·具体设计（Audio Perception Chain — Concrete Design）

- **Status**: Proposed（review-ready，待 Owner 冻结）
- **Date**: 2026-08-04
- **Owner**: SilverShield 技术负责人
- **Related**: ADR-0019（多模态融合·方向）、ADR-0022（证据链·多模态接口）、ADR-0014（事件 Schema 冻结）、ADR-0001 / ADR-0002（模块边界 / 隐私铁律）
- **Phase**: v2 · Phase 3（音频双通道）
- **配套文档**：技术栈候选与选型理由见 `docs/design/audio/audio_stack_survey.md`；音频测试素材生成基础设施（TTS Fixture，Testing 侧）见 `docs/design/audio/audio_fixture_generation.md`；技术验证 Spike 实证（环境 / 命令 / 数据 / 结论）见 `docs/design/audio/audio_spike_report.md`。

---

## 0. 背景与动机（Context）

MVP 阶段（P0-1 ~ P0-11.5b）已交付，`v0.1.0-mvp-rc` 已打，289 测试全绿。v2 演进设计中，Phase 1（实时风险流，ADR-0021）、Phase 2（证据链，ADR-0022）、Phase 4（身份，ADR-0023）均已出具体设计；**Phase 3 音频双通道是唯一尚未出具体设计的演进阶段**。

类型层已为音频预留位置（核对 `analysis/risk_signal.py`）：

- `SourceModality.AUDIO`
- `SignalCategory.COMMUNICATION`（注释明标 "Phase 3"）
- `EvidenceModality.AUDIO`

本 ADR 的目标不是"设计一个音频模型"，而是**把音频作为一个新的感知模态接入已有 Agent 架构**，复用 `RiskSignal / Evidence / DecisionPolicy` 三件套，且不破坏已建立的分层纪律。

**核心约束（承接 ADR-0001）**：音频只停留在 **Perception Layer**，只告诉系统"发生了什么"（如 `Speech detected` / `High amplitude` / `Rapid speech` / `Telephone sound` / `Crying`），**绝不跨入 Semantic Reasoning Layer**（ASR→LLM→"用户正在被骗"）。是否诈骗由中心风控综合判断。

---

## 1. 设计原则（Principles）

1. **与视觉链同构**：`Audio → AudioSegmentEvent → AudioPerceptionEvent → RiskSignal → DecisionPolicy → WarningEvent`，与 `Frame → VisitorEvent → PerceptionEvent → RiskSignal → DecisionPolicy` 完全镜像。未来红外 / 门磁 / 可穿戴 / WiFi sensing 均可复制此模式，最终形成多模态在 `RiskSignal` 汇聚、`DecisionPolicy` 统一决策的架构。

2. **不把音频变成"第二个大脑"**：禁止 `音频 → 文本(ASR) → LLM 理解 → 诈骗判断` 的链路。那会越过 Perception Layer、违反 ADR-0001，且引入不可控的 CPU / 延迟 / 网络依赖。

3. **不侵入视觉管道**（ADR-0019 铁律）：音频链自成一条独立管道，单职责，不反向依赖 `detection/` / `analysis/` 的视觉实现。

4. **复用既有三件套、零破坏**：`DecisionPolicy` 不改动；仅新增 `AudioAdapter`（integration layer）把音频语义事件翻译为既有 `RiskSignal` / `EvidenceItem`。所有类型层扩展均为 **MINOR**，不破坏 ADR-0014 冻结。

---

## 2. 管道结构（Pipeline）

```
AudioSource ──▶ AudioDetector ──▶ AudioFeatureExtractor ──▶ AudioRule ──▶ AudioPerceptionEvent
   (ingestion)    (VAD 分段)        (Tier0/1 特征)           (规则)        (语义层, 5 类声学感知)
```

镜像视觉链 `FrameSource → Detector → ... → VisitorEvent → PerceptionEvent`。

- **`AudioSource` 与 `VideoSource` 是两条独立的传感器链路，互不依赖**（与 ADR-0019「Vision / Audio 双独立感知链」完全一致）。**Phase 3.0 使用 `LocalMicSource` / `FileAudioSource` 验证闭环**，不依赖摄像头是否带音频码流；`RTSPAudioSource`（未来支持带音频的摄像头）留待设备适配阶段。摄像头有无麦克风 / 音频流，不影响音频管道的存在与正确性。
- 管道失败隔离：音频链异常时降级为"仅视觉"，不拖垮主视觉管道，也不抛未分类异常（遵循 AGENTS.md §2.5）。
- **边界约束（冻结）**：`AudioRule` 只负责从 `AudioFeature` 生成 `AudioPerceptionEvent`，**绝不直接生成 `RiskSignal`**；所有"音频 → `RiskSignal`"的翻译必经 §5 的 `AudioAdapter`。任何 `AudioRule → RiskSignal` 的旁路都属违规（会绕过 `analysis/` 既有的 RiskSignal 消费契约与 `DecisionPolicy` 统一决策点）。

**`AudioSource` 接口与实现（ABC + 可插拔实现，与 Visual `VideoSource` 解耦）**：
```
AudioSource(ABC)
  ├── FileAudioSource        # 测试 / 回放（Phase 3.0 默认验证入口）
  ├── LocalMicSource         # 本机 / 外接麦克风（Phase 3.0）
  ├── USBMicSource           # 独立 USB 麦克风（后续扩展）
  └── RTSPAudioSource        # 带音频的摄像头 RTSP 音轨（设备适配阶段，不在 3.0）
```
海康 / 萤石 / 大华等摄像头音频能力差异极大（有的带麦克风 / 双向语音 / 音频码流，有的纯视频）。把音频抽象为独立的 `AudioSource`、而非 "camera → audio" 的强绑定，才能在设备适配时不被摄像头能力限制架构。

---

## 3. 模型选型（Tier 0 / Tier 1，边缘 CPU·不投重模型/LLM）

### Tier 0 — VAD + Prosody（常驻，零模型）
- **VAD**（WebRTC VAD / Silero-VAD 轻量版）：把连续音轨切分为语音段。
- **ProsodyExtractor**：从语音段提取 `rms`（振幅）、`speech_rate`（语速代理指标）、基频轮廓、能量方差等**廉价声学特征，零模型权重**。
- 覆盖能力：急促言语 / 高声争吵 / 异常通话时长 / 哭诉求助。
- 默认常驻开启。

### Tier 1 — YAMNet（config 可选增强，语言无关）
- ~1M 参数 CNN（MobileNet 主干），AudioSet 521 类，16kHz mono，0.96s 分析窗。
- **目标预算（约束，非 benchmark 声明）**：单 segment CPU 推理 **< 20ms**；具体数值随部署 runtime（TFLite / ONNX Runtime / TF-CPU）实测为准。**ADR 只定义约束上限，不绑定某一 runtime 的 benchmark 结果。**
- 仅作为 `config` 开关的可选增强，**默认关闭**；开启后由 Tier0 触发式拉起，避免常驻占用。

### 明确不做（Hard Exclusions）
- ❌ ASR（Whisper 等）转写 + LLM 话术分析 —— 越界到 Semantic Reasoning Layer（ADR-0001）+ 边缘 CPU / 延迟 / 网络三重风险。
- ❌ 任何产出"诈骗 / fraud / suspect"结论的逻辑 —— 违反 ADR-0001 / ADR-0002 模块边界铁律。

---

## 4. 事件模型（Event Model，MINOR，不破 ADR-0014 冻结）

### 4.1 AudioSegmentEvent（事实层 · 纯音频域）

```python
@dataclass
class AudioSegmentEvent:
    segment_id: str
    timestamp: float
    duration: float
    vad_ratio: float          # 语音占比 0~1
    rms: float                # 均方根振幅（响度代理）
    speech_rate: float        # 语速代理指标
    labels: list[str]         # 来自 Tier0/1 的声学标签，如 ["speech","telephone"]
    # 注：不含任何跨模态字段（见 §6）
```

**架构纯度约束**：`AudioSegmentEvent` 只描述音频自身的事实，不持有视觉域 / 其他传感器域状态。未来门磁 / 蓝牙 / WiFi 的观测，**不会**被塞进这个音频事件。

### 4.2 AudioPerceptionEvent（语义层 · 5 类声学感知枚举）

```python
class AudioPerceptionKind(str, Enum):
    AUDIO_SPEECH_RAPID = "audio_speech_rapid"              # 急促言语
    AUDIO_VOICE_RAISED = "audio_voice_raised"              # 高声 / 争吵
    AUDIO_TELEPHONE_PERSISTENT = "audio_telephone_persistent"  # 异常/持续通话
    AUDIO_DISTRESS_CRY = "audio_distress_cry"              # 哭诉 / 求助声
    AUDIO_ANOMALY_OTHER = "audio_anomaly_other"            # 其他异常声学信号

@dataclass
class AudioPerceptionEvent:
    event_id: str
    timestamp: float
    kind: AudioPerceptionKind
    score: float              # 规则强度（0~1），不是诈骗概率
    confidence: float         # 检测可信度（0~1）：模型/特征对该 segment 判定的把握
    source_segment_ids: list[str]
    labels: list[str]
```

- **`score` vs `confidence` 的语义区分**（评审新增）：
  - `score` = 规则强度 —— "这条声学风险有多强"。例如 `rapid_speech` 语速越快，`score` 越高。
  - `confidence` = 检测可信度 —— "这个判定有多可信"。例如 `raised_voice` 在背景噪声下 `score=0.7` 但 `confidence=0.6`（可能是高声，但不确定）；而干净环境下 `rapid_speech` `score=0.8, confidence=0.95`（确实很快）。
  - 下游 `RiskSignal` / `DecisionPolicy` 可据此区分"强但不可信"与"弱但确凿"，提升融合质量。
- **严格黑名单**：事件不携带 `fraud` / `suspect` 字段（ADR-0001 / ADR-0002）。

---

## 5. 接入既有三件套（Integration Layer）

### 5.1 AudioAdapter（归属：integration layer）
- **位置**：`integration/`（或既有适配器目录），**不属于 `analysis/`，也不属于 `core/`**。它是音频管道输出与既有 `RiskSignal` / `Evidence` 契约之间的**翻译层**——这与视觉侧 `VisitorEvent → RiskSignal` 的适配方式一致，使 `analysis/` 保持对音频具体实现的无知（符合 ADR-0019 不侵入原则）。
- **职责**：`AudioPerceptionEvent → RiskSignal(source=AUDIO, category=COMMUNICATION)`；`AudioEvidenceCollector → EvidenceItem(modality=AUDIO, kind ∈ {segment, clip})`。
- **`DecisionPolicy`：零改动**——它只消费既有的 `RiskSignal`，不感知信号来自视频还是音频。

### 5.2 复用既有时机
- `SourceModality.AUDIO` / `SignalCategory.COMMUNICATION`（"Phase 3"）/ `EvidenceModality.AUDIO` 均已在类型层预留 → 本 ADR 全部为 **MINOR 扩展**，不破坏 ADR-0014 冻结的契约。

---

## 6. 跨模态融合（Cross-Modal Fusion）—— `overlap` 应在此处产生

`overlap_with_visitor` 这类跨模态关联**不属于音频事实层**。先前设计把它放在 `AudioSegmentEvent` 是越界——`visitor` 是视觉域概念，放进音频事件会形成跨模态耦合，且会诱使门磁 / 蓝牙 / WiFi 等后续传感器也往各自事件里塞外部状态。

正确归属：

```
AudioSegmentEvent  +  VisitorState  ──▶  CrossModalEvidence
   (纯音频事实)        (视觉域状态)        .overlap_with_visitor / .correlated_entities
```

即跨模态关联在 **Evidence Fusion 层 / CrossModalEvidence** 中由融合逻辑产出（与 ADR-0022 的证据聚合接口衔接），音频事实事件保持纯净。这为未来任意新模态加入同一融合模式扫清了架构障碍。

---

## 7. 音频证据生命周期与隐私（AudioEvidencePolicy，新增）

音频证据不能永久保存，且涉及老年人家庭隐私，须与 Memory 的 expiration / privacy / governance 体系一致（ADR-0002）。

```python
class AudioEvidencePolicy:
    # 形态
    segment: "metadata only"      # 仅存特征/标签元数据，不落原始音频
    clip:    "temporary buffer"   # 临时环形缓冲（本地 tmp，自动过期）
    # 留存
    retention:
      warning_triggered: "keep ±30s 窗口作为证据引用"
      no_warning:         "立即丢弃"
    # 隐私
    privacy: "音频永不离开 Home 端；仅上传 metadata / 证据引用至中心，不传原音频"
```

- `segment` 默认只保留元数据，零原始音频落盘。
- `clip` 仅在高风险事件触发时短暂留存 `±30s` 窗口作证据引用；无预警即丢。
- 与既有 `EvidenceItem(modality=AUDIO, kind ∈ {segment, clip})` 对齐；策略由 `AudioEvidenceCollector` 在本地执行。

---

## 8. 边缘预算（Edge Budget）

- 采样：**16kHz / 1.0s 分析窗 / hop 0.5s**。
- Tier0 常驻：目标 **< 3% CPU**；Spike #2 实测规则 VAD CPU 占用 < 1ms/帧（numpy 能量代理 ~0.0ms / 8s），相对视频管道 ~300–400ms/帧 可忽略 → **音频不应设计为每视频帧同步调用**，而应以独立 `Audio Loop` 经 event bus 异步消费（呼应 §2 的独立线程/进程模型，与现有 `run_loop` 一致）。
- Tier1 按需触发（由 Tier0 拉起）：目标 **< 5% CPU**（config-gated，默认关闭）。
- 资源释放：`AudioSource` / 音频缓冲须显式释放（`__exit__` / context manager），进程退出前 flush 日志、停止发布、释放音源（AGENTS.md §2.5 / §4.2）。
- 失败隔离：音频链异常 → 降级为仅视觉，主管道不受影响。

---

## 9. Phase 3.0 MVP Scope（最小实现，新增）

设计完整，但**实现边界应当更硬**。建议先落地最小可演示闭环，证明"第二模态可以进入系统闭环"，而非追求音频 AI 的 SOTA。

### 3.0 在范围内（In Scope）
```
# Phase 3.0：音频管道与视频源解耦，用独立音源验证闭环
FileAudioSource ─▶ WebRTC VAD ─▶ ProsodyExtractor ─▶ AudioPerceptionEvent
        │                                                                 │
        └────────────────── AudioAdapter ─▶ RiskSignal ─▶ Dashboard Evidence Card
```
- **P0（必须，冻结验收门槛）**：`FileAudioSource`（测试 WAV：`tests/fixtures/audio/normal_speech.wav`、`rapid_speech.wav`、`raised_voice.wav`、`telephone_conversation.wav`、`crying_voice.wav`，命名围绕 `AudioPerceptionKind`，见 `docs/design/audio/audio_fixture_generation.md`）—— **CI 可测、可复现、零硬件依赖**，是 3.0 验收的硬门槛。**fixture 由 TTS 生成基础设施产出（`docs/design/audio/audio_fixture_generation.md`），不依赖人工录音、不依赖 CCTV 音轨。**
- **P1（增强，非验收门槛）**：`LocalMicSource`（本机 / 外接麦克风）—— 仅用于现场 Demo 展示真实采集；引入 Windows 音频设备权限 / 驱动 / 采样率 / 麦克风占用等非核心风险，故延后至 P1，不阻塞 3.0 验收。
- **输入解耦**：音频来自 `FileAudioSource` 或 `LocalMicSource`，**不依赖 CCTV 是否含音轨**。
- Tier0（WebRTC VAD + Prosody）常驻；`AudioPerceptionEvent → AudioAdapter → RiskSignal → Dashboard 证据卡` 端到端打通。
- **实测佐证（技术 Spike · 2026-08-04）**：现有 `data/demo/*.mp4`（CCTV Demo）经 PyAV 探测**均不含音轨**（仅 h264 / mpeg4 视频轨），证实"复用 CCTV 音轨"路径在当前数据下不存在 → 音频链必须自带独立音源（详见 `docs/design/audio/audio_spike_report.md` §Spike #1）。

### 3.0 暂不在范围内（Out of Scope）
- YAMNet（Tier1，留作后续增强）
- ASR / LLM 话术分析（越界 ADR-0001）
- 模型训练 / 微调
- 独立 RTSP 音轨分离（`RTSPAudioSource`，留待有音频摄像头设备适配阶段）

> 注：`LocalMicSource`（本机 / 外接麦克风）属 Phase 3.0 的 **P1 增强**（非验收门槛）；**不碰 RTSP 音轨分离**（那是 `RTSPAudioSource`，3.1+）。

### 理由
Demo 阶段最需证明的是"**第二模态进入闭环**"这一架构事实；音频分类精度是后续增强项，不是 3.0 的验收门槛。

---

## 10. 后果 / 替代方案 / 开放问题（Consequences）

**正面后果**
- 系统获得新的感知维度，且接入成本极低（adapter 层 + MINOR 类型扩展）。
- 架构范式可被红外 / 门磁 / 可穿戴 / WiFi sensing 复制，形成真正的多模态感知系统。

**负面 / 代价**
- 新增一条管道与一组契约，需配套 schema 测试（契约变更加 `tests/test_event.py`）。
- 融合层需定义 `CrossModalEvidence` 的关联触发策略。

**替代方案（已否决）**
- ASR + LLM 话术分析：越界 ADR-0001，且边缘 CPU / 延迟 / 网络风险不可控。
- 独立音频微服务：边缘场景过度工程，违背 MVP 控制纪律。

**开放问题**
- `CrossModalEvidence` 中 `overlap_with_visitor` / `correlated_entities` 的具体触发与权重策略（留待融合 ADR / 实现期）。
- Dashboard 音频证据卡的信息密度与布局。

---

## 11. 测试策略（Test Strategy，新增）

与 AGENTS.md §6.3（无测试交付属 AI 协作禁区）及 CI / Agent Sandbox 思路一致，音频链须配套以下测试作为合并门槛：

### 11.1 Contract Test（契约测试）
- 验证 `AudioPerceptionEvent → AudioAdapter → RiskSignal` 字段映射正确：
  - `source == SourceModality.AUDIO`、`category == SignalCategory.COMMUNICATION`
  - `score` / `confidence` / `labels` / `kind` 正确透传或转换
  - `AudioEvidenceCollector → EvidenceItem(modality=AUDIO, kind ∈ {segment, clip})` 正确生成
- 契约模型变更加对应 schema 测试（类比 `tests/test_event.py`），防回归破坏 ADR-0014 冻结。

### 11.2 Fixture Test（固定样本测试）
- 固定样本目录 `tests/fixtures/audio/`（canonical 命名，围绕 `AudioPerceptionKind`，由 TTS 生成基础设施产出，详见 `docs/design/audio/audio_fixture_generation.md`）：
  - `normal_speech.wav`（正常言语，负向对照，不触发风险事件）
  - `rapid_speech.wav`（急促言语）→ `AUDIO_SPEECH_RAPID`
  - `raised_voice.wav`（高声 / 争吵）→ `AUDIO_VOICE_RAISED`
  - `telephone_conversation.wav`（持续通话）→ `AUDIO_TELEPHONE_PERSISTENT`
  - `crying_voice.wav`（哭诉 / 求助）→ `AUDIO_DISTRESS_CRY`
- 样本与预期事件由 `tests/fixtures/audio/manifest.yaml` 声明，测试 manifest 驱动；验证 `input .wav → AudioPipeline → AudioPerceptionEvent` 命中预期 `kind` / `score_min` / `labels`；**确定性、可复现**（不依赖随机 / 网络 / 重模型）。
- 样本作为测试资产显式纳入版本控制（非凭证 / 非模型权重）；若体积大可走 Git LFS。`.gitignore` 仅排除运行期证据（`data/evidence/**`）与模型（`*.pt`），不排测试 fixture。

### 11.3 Failure Isolation Test（失败隔离测试）
- 模拟 `AudioSource` / `AudioDetector` 抛异常，确认：
  - **Vision Pipeline 仍 `PASS`**（主管道不受影响）
  - 系统降级为"仅视觉"，不崩溃、不丢帧、不污染 `RiskSignal`
- 与 §8「失败隔离」约束呼应，作为 CI 回归项。

### 11.4 变异 / 属性校验（参考测试有效性铁律）
- Tier0/1 阈值与属性断言做变异验证；顺序无关逻辑穷举 permutations；幂等 / 重试跨调用验证；测试不读私有成员。

---

## 12. 修订记录（Changelog）

- **2026-08-04** 初稿（Proposed）。
- **2026-08-04** 评审后修订（review-ready）：
  - **P0** 删除 `AudioSegmentEvent.overlap_with_visitor`，跨模态关联移至融合层 `CrossModalEvidence`（§6）。
  - **P0** YAMNet 性能描述由固定 benchmark（"<10ms/segment"）改为**目标预算约束（<20ms，随 runtime 实测）**，ADR 只定义约束不绑定 benchmark（§3）。
  - **P1** `AudioPerceptionEvent` 增加 `confidence`（检测可信度），与 `score`（规则强度）区分（§4.2）。
  - **P1** 新增 `AudioEvidencePolicy`：segment 仅元数据 / clip 临时缓冲 / 预警触发留存 ±30s / 无预警即丢 / 音频不出 Home 端（§7）。
  - **P1** 新增 Phase 3.0 MVP Scope：仅 Tier0 + Adapter + Dashboard 证据卡，YAMNet/ASR/独立麦克风等留作后续（§9）。
  - **P2** 明确 `AudioAdapter` 归属 **integration layer**，不属 `analysis/` / `core/`（§5.1）。
  - **P1** 二轮评审修订（建议 1 / 2 / 7）：① 建议1 `AudioRiskKind` → `AudioPerceptionKind`（去除 "risk" 暗示已完成风险判断，契合 ADR-0001 仅产 perception；枚举值 `AUDIO_*` 保留）；② 建议2 冻结边界——`AudioRule` 只产 `AudioPerceptionEvent`、绝不直接产 `RiskSignal`，翻译必经 `AudioAdapter`（§2 / §5.1）；③ 建议7 新增 §11 测试策略（Contract / Fixture / Failure Isolation / 变异校验，对齐 AGENTS.md §6.3 与 CI）。
  - **Spike 驱动修订（冻结前关键修正 · 2026-08-04）**：技术验证 Spike 实测 `data/demo/*.mp4`（CCTV Demo）经 PyAV 探测**均无音轨**（仅 h264/mpeg4 视频轨），证实"复用现有 CCTV 音轨"假设不成立。据此：① §2 删除"从 EZVIZ RTSP 抽取音轨"，改为 `AudioSource` 与 `VideoSource` 双独立链路，Phase 3.0 用 `LocalMicSource`/`FileAudioSource`；② 新增 `AudioSource(ABC)` 可插拔层级（File/Local/USB/RTSP），Phase 3.0 = File+LocalMic，RTSP Audio 留 3.1；③ §9 Phase 3.0 由 `AudioFromVideoSource` 改为 `FileAudioSource`/`LocalMicSource`，Out-of-Scope 移除"独立实时麦克风接入"（已入 3.0）、保留"独立 RTSP 音轨分离"。调研文档 `docs/design/audio/audio_stack_survey.md` 同步调整 PyAV 优先级（Phase 3.0 用 sounddevice/wave，PyAV 仅 3.1 RTSP）。
  - **二轮 Spike 评审修订（2026-08-04）**：① §9 Phase 3.0 拆 **P0（`FileAudioSource` 必须·CI 可测可复现零硬件）/ P1（`LocalMicSource` 增强·现场 Demo，延后以避免 Windows 音频设备非核心风险）**；② §8 补 Spike #2 结论——音频不应每视频帧同步调用，须独立 `Audio Loop` 经 event bus 异步消费；③ 新增配套文档指针：`docs/design/audio/audio_stack_survey.md`（选型）+ `docs/design/audio/audio_spike_report.md`（Spike 实证）；④ 调研文档同步将 YAMNet 运行时表述由"YAMNet ONNX 已确定"收紧为"**ONNX Runtime 为 py3.13 Spike 验证路径，YAMNet ONNX 模型转换与许可证/权重来源已实现阶段确认（2026-08-06 验证：PINTO_model_zoo 097_YAMNet / Apache-2.0，权重 sha256 见 `docs/reports/ADR-0026-yamnet-real-weight-validation.md`）**"。
  - **2026-08-06 真实权重接入验证（validation）**：YAMNet 真实权重闭环验证通过，闭合本 ADR「权重来源 TBD」开放项。四要素确认——① 权重来源：PINTO_model_zoo `097_YAMNet`（Apache-2.0，`tflite2tensorflow` 转换），类映射取自 TF 官方 `yamnet_class_map.csv`（521 类）；② License：Apache-2.0；③ Checksum：canonical `yamnet.onnx` sha256 `6de606bc...`、runtime `yamnet_runtime.onnx` sha256 `3322b9fe...`（权重不入库，gitignored）；④ Runtime 兼容：onnxruntime 1.24.4 / scipy 1.18.0 / numpy 2.4.2（Py3.14）实测推理成功。验证中发现 PINTO 导出输入 `waveform` 为 rank-1（`[samples]`），原 stub 路径喂 rank-2 触发 `INVALID_ARGUMENT`；修复见 PR `fix/audio-tier1-onnx-rank`（rank 自适应 + 动态输入 `yamnet_runtime.onnx` + 新增 rank-1 单测）。完整报告：`docs/reports/ADR-0026-yamnet-real-weight-validation.md`。

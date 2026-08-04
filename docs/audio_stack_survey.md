# 音频技术栈调研与借鉴应用（Audio Stack Survey & Adaptation）

- **性质**：技术调研 / 参考资料 —— **非 ADR，不修改任何事件契约或 MQTT 契约**。本文件是 ADR-0026（音频感知链路·具体设计）的选型附录，为 Phase 3 音频链路的实现提供候选清单与"如何落到本仓库设计"的映射。
- **目的**：在动手写 `AudioSource` / `AudioDetector` 之前，先把"每层有哪些候选、各自代价、哪些契合边缘 CPU + 既定分层纪律"理清楚，避免实现期临时拍脑袋选型。
- **方法论**：本文先定**架构边界**（ADR-0026 已定），再基于**已有生态选型**填入每个盒子的实现，最后回到**系统边界约束**（事件契约透明 / 失败隔离 / 边缘预算）反查。即"设计系统边界 → 基于生态选组件 → 反查约束"的成熟工程流程，而非"先想模型再补系统"。
- **约束基线**（贯穿全文）：
  - 边缘 CPU、单路家庭入口流，**不投重模型/LLM/ASR**（ADR-0001 / ADR-0026 §3）。
  - 音频只产 **perception**，不产风险结论（ADR-0001）。
  - 不引入破坏仓库约束的重依赖；新依赖须进 venv（AGENTS.md §4.1 / §6.2）。
  - **先复用既有依赖**：本仓库已含 `torch`（经 `ultralytics`）、`numpy`、`opencv-python`（通常自带 `libav*`）；选型优先建立在它们之上。
  - ⚠️ **"复用 torch" 不自动等于 "复用 torchaudio"**：`torchaudio` 与 `torch` 版本强绑定，本仓库 torch 经 `ultralytics` 间接引入，二者兼容性须独立验证（见 §2.3 / §5）。

---

## 1. 分层候选总览（Survey）

| 层                         | 候选                                | 定位                               |
| ------------------------- | --------------------------------- | -------------------------------- |
| Audio I/O（取流 / 解封装 / 解码）  | FFmpeg / PyAV / GStreamer         | 从 EZVIZ RTSP 抽取音轨并解码为 PCM        |
| VAD（语音活动检测）               | WebRTC VAD / Silero VAD           | 把连续音轨切分为语音段（Tier0 的两种后端，非 Tier0/1 递进） |
| Feature（声学特征提取）           | numpy(主) / scipy.signal(可选) / torchaudio(研究期待定) | 从语音段提取 RMS/语速/过零率等 Prosody 特征；Phase 3.0 优先 numpy |
| Classification（声学分类）      | YAMNet / PANNs                    | 语言无关声学事件分类（Tier1 可选）             |
| Runtime（推理运行时）            | ONNX Runtime / TFLite             | 承载 YAMNet / Silero 的 CPU 推理      |
| Streaming / Orchestration | GStreamer / asyncio / thread-pool | 流式编排与推理卸载                        |

---

## 2. 各层候选详解（Pros / Cons / 边缘适配）

### 2.1 Audio I/O

> **关键实测（技术 Spike · 2026-08-04）**：现有 `data/demo/*.mp4`（CCTV Demo）经 PyAV 探测**均不含音轨**（仅 h264/mpeg4 视频轨）。因此 **Phase 3.0 不存在"从 CCTV 抽音轨"路径**，音频必须来自独立音源；PyAV（RTSP 音轨）降级为 **Phase 3.1 设备适配项**。

- **sounddevice / PyAudio**：实时麦克风采集（`LocalMicSource`）。← **Phase 3.0 输入之一**（本机/外接麦克风，不碰 RTSP）。
- **wave**（标准库）：读取 WAV 文件（`FileAudioSource`），零依赖解码 PCM。← **Phase 3.0 默认验证入口**（测试 fixture `fixtures/audio/*.wav`）。
- **FFmpeg**（CLI / `libav*`）：万能媒体工具；可从 RTSP 解音轨、`-f wav` 导出 PCM；作为 **Phase 3.1 RTSP 音轨**的兜底层（进程外子进程、日志/管道管理略繁琐）。
- **PyAV**（libavformat / libavcodec 的 Pythonic 封装）：可在进程内打开**带音频的** RTSP URL 解码为 16kHz PCM，与现有 `ingestion/` 层同源复用。← **Phase 3.1 默认实现（仅当有音频摄像头时）**；`opencv-python` 通常已带 `libav*`，PyAV 会带自己的，需注意版本错配（见 §5）。**PyAV 只是 `PyAVSource` 的具体实现，不是 `AudioSource` 契约本身**（见 §3.1）。
- **GStreamer**：管线级低延迟；但 `gi` 绑定重、学习曲线陡，对"边缘单路流"属过度工程。→ **不采用**（未来若上硬件流式再评估）。

### 2.2 VAD

- **WebRTC VAD**：Google 出品，极小（C 扩展，几乎无模型权重），3 档 aggressiveness，要求 8/16/32/48 kHz、10/20/30 ms 帧；极快、零模型依赖。← **Tier0 默认后端（Edge baseline）**。
- **Silero VAD**：ONNX 小模型（约 2.5 MB），支持多采样率、噪声环境下精度通常高于 WebRTC VAD；需 ONNX Runtime。它是 **Tier0 的可选后端（accuracy upgrade）**，仍是 VAD 而非分类器——**不应被误当作 Tier1**。仅当家庭噪声实测拖垮 WebRTC 时启用，不进 3.0 默认栈。

### 2.3 Feature

- **librosa**：功能最全（MFCC / chroma / spectral contrast / pitch），但偏重——拖入 `scipy` / `numba` / `resampy`，CPU 上明显慢，偏研究向。**Phase 3.0 不引入**：当前只需 RMS / 语速 / 过零率等低阶特征，librosa 大量 DSP 能力暂未使用；后续研究阶段（如 MFCC / spectral contrast / pitch tracking）可重新评估，本调研不做永久否决。
- **torchaudio**：torch 生态，能补 spectrogram / MFCC / pitch 变换；**但 `torchaudio` 与 `torch` 版本强绑定**（`torch 2.6` 须配 `torchaudio 2.6`），本仓库的 `torch` 是经 `ultralytics` 间接装好的，二者版本需**独立验证**；若引入成本过高（版本错配 / 体积），Phase 3.0 **优先用 numpy 实现基础 Prosody**，torchaudio 留作研究期可选项。
- **numpy 直接算 + scipy.signal（可选）**：RMS `sqrt(mean(x²))` / 短时能量 / 过零率（ZCR）等 Prosody 代理特征用 `numpy` 即可，最轻，与 `AudioFeatureExtractor` 的廉价特征层契合；需要带通/共振峰等滤波时再引 `scipy.signal`。← **Phase 3.0 Prosody 主路径**。

### 2.4 Classification

- **YAMNet**：TF 模型，AudioSet 521 类，MobileNet 主干，16 kHz mono，约 3 MB；有 ONNX / TFLite 权重；**语言无关（纯声学类）**——契合"音频只报发生了什么"。← **Tier1 默认**。
- **PANNs**（CNN14 等）：在大音频数据集上精度更高，但 CNN14 约 81 MB、32 kHz、更重；未来若 Demo 需要更高分类精度再考虑，不进 3.0。

  **YAMNet 部署路径（Spike 实证后更新）**：
  - **B. ONNX Runtime（当前 py3.13 下的验证路径）**：Spike 实测 `tflite-runtime` 在 py3.13 无 wheel，ONNX Runtime 可装且能跑同构声学分类图（~0.4–4.6ms/segment，≪ 20ms 预算）。**但须明确：Spike 仅验证"ONNX Runtime 可跑声学分类模型"，并非"YAMNet ONNX 已完整闭环"——YAMNet ONNX 模型转换（TF Hub → ONNX）与许可证 / 权重来源须在实现阶段确认。**
  - **A. TF Lite**：官方边缘生态最顺，但 Spike 证实在 py3.13 无 wheel；若坚持用须把音频栈钉到 py3.11/3.12。
  - **C. TF-CPU**：最简单但依赖重，不推荐。

### 2.5 Runtime

- **ONNX Runtime**：跨平台、x86 CPU 上通常快、支持量化；若 YAMNet 走 ONNX 路径则用它跑 YAMNet / Silero。
- **TFLite / tflite-runtime**：TF 轻量，官方边缘生态顺，但 **py3.13 无 wheel（Spike 实证）**；仅在音频栈钉 py3.11/3.12 时方可作为 YAMNet 路径（见 §2.4）。
- **TF-CPU**：最简单但依赖重；避免。
- 最终 Runtime 取决于 §2.4 的 YAMNet 部署路径决策；二者不冲突（例如 Silero 走 ORT、YAMNet 走 TFLite 亦可）。

### 2.6 Streaming / Orchestration

- **asyncio**：Python 原生，I/O 友好；但 **CPU 密集推理受 GIL 限制** → 推理须丢到 thread / process pool。
- **GStreamer**：管线级流式；过度工程，3.0 不用。
- **concurrent.futures / multiprocessing**：推理卸载；与现有 `FrameSource` / `run_loop` 模式一致。← **推荐编排模式**。

---

## 3. 如何应用到我们的开发设计（Adaptation → ADR-0026）

下表把§2 的选型逐层映射到 ADR-0026 的具体章节与落地动作。

### 3.1 取流：AudioSource 与 VideoSource 双独立（§2 ingestion 层）

- `AudioSource` 是 **ABC（契约）**，具体取流实现可插拔，与 Visual `VideoSource` 完全解耦：`FileAudioSource`（Phase 3.0 默认验证入口）/ `LocalMicSource`（Phase 3.0 本机/外接麦克风）/ `USBMicSource`（后续）/ `RTSPAudioSource`（Phase 3.1 带音频摄像头）。**具体实现（PyAVSource/sounddevice/wave）不是 `AudioSource` 契约的一部分**——未来换实现不改变任何事件字段。
- **Phase 3.0 用 `FileAudioSource`（测试 WAV）或 `LocalMicSource`（麦克风）**验证闭环，**不依赖 CCTV 是否含音轨**（Spike 实测当前 demo 无音轨）。
- **Phase 3.1 才引入 `RTSPAudioSource`/`PyAVSource`**：当部署环境出现带音频的摄像头时，用 PyAV 进程内解 RTSP 音轨 → 16 kHz PCM；退化路径回退 **FFmpeg 子进程 pipe**。

### 3.2 Tier 0 = VAD + Prosody（零模型常驻；VAD 后端 WebRTC 默认 / Silero 可选）

- VAD：`webrtcvad` 切语音段（16 kHz、30 ms 帧）；噪声环境实测不足时，可在 **同一 Tier0 内** 切换 `Silero VAD` 作为可选后端（仍属 VAD，非 Tier1）。
- `ProsodyExtractor`：**以 `numpy` 为主**算 `rms` / `speech_rate` / 能量方差 / 过零率；需要带通滤波再引 `scipy.signal`。**基础 Prosody 不依赖 torchaudio**；若研究期需要 MFCC/基频轮廓且版本验证通过，再视情引入 `torchaudio`。
- **全零模型权重**，对应 ADR-0026 §8 预算（常驻 < 3% CPU）。
- **Phase 3.0 不引入 librosa**：当前只需低阶特征，librosa 大量 DSP 能力暂未使用（后续可重评，非永久否决）。

### 3.3 Tier 1 = YAMNet 声学事件分类（config 可选，默认关）

- 部署路径：Spike 实测 **`tflite-runtime` 在 py3.13 无 wheel**（要求 py<3.13）→ TF Lite 在本 Python 版本装不上；**ONNX Runtime 在 py3.13 可装且实测 ~0.4–4.6ms/segment（远低于 20ms 预算，合成图冒烟测试；真实 YAMNet 会更高，但已验证 ORT 可跑）**，是当前 py3.13 下的验证路径（见 §2.4 / 开放问题）。**须明确：Spike 验证的是"ONNX Runtime 可跑声学分类"，不是"YAMNet ONNX 已完整闭环"——YAMNet ONNX 模型转换与许可证/权重来源待实现阶段确认。**若坚持用 TFLite，须把音频栈钉到 **py3.11/3.12**。无论哪条路径，YAMNet ONNX/TFLite 转换的**数值一致性须独立验证**，不默认某 runtime 一定顺滑。
- 对应 ADR-0026 §3 **目标预算 < 20 ms/segment（约束，非 benchmark 声明）**。
- 由 Tier0 触发式拉起，避免常驻（§3）。

### 3.4 事件 / 接入（§4 / §5）

- 特征 → `AudioSegmentEvent`（纯音频域，无跨模态字段）→ `AudioPerceptionEvent`(`AudioPerceptionKind`, `score`+`confidence`) → `AudioAdapter`(integration layer) → `RiskSignal(source=AUDIO, category=COMMUNICATION)`。
- **选型对契约透明**：VAD / 分类只是 `AudioFeatureExtractor` / `AudioRule` 的**内部实现**，事件模型（`AudioSegmentEvent` / `AudioPerceptionEvent`）完全不感知具体库——选 WebRTC 还是 Silero 不改变任何字段（ADR-0014 冻结不受影响）。

### 3.5 流式编排（§8 预算 + §11.3 失败隔离）

- `AudioPipeline` 作为 asyncio 协程 / 线程，喂入与视觉事件**同一总线**；CPU 推理用 thread pool 避开 GIL。
- 失败隔离：`AudioSource` / `AudioDetector` 异常 → 降级为仅视觉，**Vision Pipeline 仍 PASS**（§11.3 Failure Isolation Test 守住）。

### 3.6 隐私（§7）

- 只在内存算特征，**原始 PCM 不持久化、不出 Home 端**；仅 `EvidenceItem(modality=AUDIO, kind=segment)` 的 metadata 落盘/上报。选型天然支持（我们从不存原始音频）。

### 3.7 测试（§11）

- **Fixture Test**：`tests/fixtures/test_audio_fixtures/*.wav`（`normal` / `phone_call` / `raised_voice` / `crying`）直接喂整个音频栈，验证命中预期 `kind`/`labels`。
- **Contract Test（契约黑名单）**：断言 `AudioPerceptionEvent` **不得出现** `fraud` / `scam` / `victim` 等字段——守住 ADR-0001「音频只产 perception 不产风险结论」的红线（继承 ADR-0026 §11.1 的契约校验）。
- **Failure Test（取流崩溃隔离）**：模拟 `FileAudioSource` / `LocalMicSource` / `AudioSource` 异常 → 音频不可用 → 断言 **Vision Pipeline 仍 PASS**、降级为仅视觉、不污染 `RiskSignal`（ADR-0026 §11.3 Failure Isolation Test）。

---

## 4. 推荐栈（按阶段拆分，呼应 ADR-0026 §9）

### 4.1 Phase 3.0（即时验证闭环 · 无 RTSP 音轨）

| 层              | 选用                        | 一句话理由                             |
| -------------- | ------------------------- | --------------------------------- |
| Audio Input    | **sounddevice（麦克风）/ wave（文件，stdlib）** | 独立音源：本机/外接麦克风 + 测试 WAV；零 RTSP 依赖 |
| Decode         | **wave（stdlib）/ FFmpeg（兜底）** | WAV 直接读 PCM；FFmpeg 仅作后续 RTSP 兜底      |
| VAD            | **WebRTC VAD（默认）/ Silero VAD（可选）** | 同属 Tier0 后端：WebRTC 零模型 baseline，Silero 噪声 accuracy upgrade |
| Feature        | **numpy + scipy.signal（可选）** | Phase 3.0 Prosody 主路径；torchaudio 留研究期、须版本验证 |
| Classification | **（Tier1 暂关）YAMNet 预留接口** | 3.0 不启用；类型/枚举已留，启用仅填实现           |
| Runtime        | **（Tier1 启用时）ONNX Runtime** | py3.13 可装；TFLite 在 py3.13 无 wheel（见 §3.3） |
| Streaming      | **asyncio + thread pool** | 与现有 run_loop 一致，推理卸载避 GIL         |

> Phase 3.0 **明确不引入 PyAV**：当前 CCTV Demo（`data/demo/*.mp4`）经 Spike 实测无音轨，无 RTSP 音频可抽；PyAV 留到 Phase 3.1 有音频摄像头时再启用。

**显式排除（Phase 3.0）**：`PyAV`（3.1 才用）/ `GStreamer` / ASR / LLM（过度工程或违反 ADR-0001 或范围外）。`librosa` 仅本阶段不引入（后续可重评）；`torchaudio` 仅研究期权衡、须版本验证。

### 4.2 Phase 3.1（设备适配：带音频摄像头 RTSP）

| 层              | 选用                        | 一句话理由                             |
| -------------- | ------------------------- | --------------------------------- |
| RTSP Audio     | **PyAVSource（AudioSource ABC 实现）** | 进程内解带音频 RTSP 音轨 → 16kHz PCM；与 `ingestion/` 同源复用 |
| 兜底           | **FFmpeg 子进程 pipe**      | PyAV 不可用时兜底                        |
| Classification | **YAMNet（Tier1，config 默认关）** | 语言无关声学类；部署优先 ONNX（py3.13 可装）或 pin py3.11/3.12 用 TFLite |

---

## 5. 依赖增量与风险

- **新增依赖（Phase 3.0 最小集）**：`sounddevice`（麦克风采集）/ `webrtcvad`（VAD）。`wave` 是标准库免装；`numpy` 已在；`scipy` 仅 Prosody 需带通滤波时加入；`torchaudio` 不在 3.0。
  - ⚠️ **环境注意（Spike 实测）**：`webrtcvad` 在本 Windows/py3.13 沙箱**无法编译**（缺 MSVC 14.0）；Linux CI 提供 manylinux wheel，可正常装。本地开发需确保编译工具链或预编译 wheel。
- **Phase 3.1 才加**：`pyav`（RTSP 音轨）；启用 Tier1 时的 `onnxruntime`（py3.13 可装；若坚持 TFLite 须 pin py3.11/3.12，因 `tflite-runtime` 在 py3.13 无 wheel）。
- **`torchaudio` 不在 Phase 3.0 默认依赖**：它与 `torch` 版本强绑定，须先独立验证本仓库 `torch`（ultralytics 间接引入）的版本是否可配到兼容 `torchaudio`；验证不过则沿用 numpy Prosody，不阻塞 3.0。
- **风险 1 — libav 版本错配（仅 Phase 3.1）**：PyAV 自带 `libav*`，与 `opencv-python` 带的 `libav*` 可能版本不一致 → 锁定版本 + 在 CD 已建镜像内验证（呼应 PR#126~129 的容器化验证纪律）。
- **风险 2 — numpy/（若用）torchaudio 特征一致性**：Fixture Test 须用固定 wav，避免不同版本下特征数值漂移导致 `kind` 误判（§3.7）。
- **风险 3 — YAMNet 部署路径未定 / Python 版本约束**：Spike 实测 `tflite-runtime` 在 py3.13 无 wheel，ONNX Runtime 可装且 ~0.4–4.6ms/segment。**当前 py3.13 下 ONNX 是验证路径**；若用 TFLite 须 pin py3.11/3.12。**但 YAMNet ONNX 模型转换与许可证/权重来源尚未确认——Spike 仅证明 ORT 可跑声学分类，未证明 YAMNet 完整闭环**（§2.4 / §3.3）。
- **不引入（Phase 3.0）**：`librosa` / `numba` / `resampy`（重且无必要）；`GStreamer` / 独立麦克风 / ASR / LLM（过度工程或越界）。`librosa` 后续研究期可重评。

---

## 6. 与既有约束对照

| 约束              | 来源                  | 本调研如何遵守                                           |
| --------------- | ------------------- | ------------------------------------------------- |
| 音频只产 perception | ADR-0001            | 栈止于 VAD/Prosody/YAMNet 声学类，无 ASR/LLM              |
| 不投重模型/重依赖       | AGENTS §4.1 / §6.2  | Phase 3.0 排除 librosa/GStreamer；Prosody 以 numpy 为主；torchaudio 仅研究期权衡（须版本验证） |
| 不破事件契约          | ADR-0014            | 选型对 `AudioSegmentEvent`/`AudioPerceptionEvent` 透明 |
| 不侵入视觉管道         | ADR-0019            | AudioSource 同源复用、独立 AudioPipeline                 |
| 接入经 Adapter     | ADR-0026 §5.1       | 库实现塞进 Extractor/Rule，翻译只走 AudioAdapter            |
| 边缘预算 / 失败隔离     | ADR-0026 §8 / §11.3 | WebRTC 零模型常驻、ONNX 约束预算、降级仅视觉                      |

---

## 7. 生态来源 / Reference Implementation

本调研的选型并非凭空设计，下列工业/开源系统为各层提供了可参照的成熟范式（不依赖它们，但用以佐证架构方向）：

| 领域           | 参考系统                  | 借鉴点                                         |
| ------------ | --------------------- | -------------------------------------------- |
| 实时视频管线       | NVIDIA DeepStream     | 多路流 + 推理旁路 + 元数据总线的编排模式                      |
| 机器人传感器融合     | ROS2                  | 多模态（传感器）汇聚到统一消息总线、由决策节点统一消费的范式             |
| 家庭自动化        | Home Assistant        | 本地优先、隐私不出户、组件插件化的设备接入模型                     |
| 音频事件检测       | YAMNet / AudioSet     | 语言无关声学类标签体系（只报"发生了什么声学事件"）                  |
| VAD          | WebRTC                | 工业级、零模型、低延迟语音活动检测的 baseline 实现             |
| 媒体处理         | FFmpeg / PyAV         | 进程内解封装/解码、同源复用的取流范式                         |

> 这些参照共同印证了 ADR-0026 的核心立场：音频应作为**一个新感知模态接入既有 Agent 架构**（Source → SegmentEvent → PerceptionEvent → RiskSignal → DecisionPolicy 同构），而非另起一套"音频大脑"。

---

## 8. 开放问题（留待实现期 / 融合 ADR）

- WebRTC VAD vs Silero VAD 在家庭环境噪声下的精度取舍（实测后决定是否把 Silero 提为 Tier0 可选后端默认）。
- **`torchaudio` 与现有 `torch` 版本绑定验证**：ultralytics 间接引入的 torch 版本能否配到兼容 torchaudio；不过则 Phase 3.0 纯 numpy Prosody。
- **YAMNet 部署路径决策**：TF Lite（推荐）vs ONNX（须验证转换一致性）vs TF-CPU（不推荐）——决定 Runtime 依赖选 `tflite-runtime` 还是 `onnxruntime`。
- PyAV 在部署镜像内的 `libav*` 版本与 opencv 的共存验证。
- `CrossModalEvidence` 中 `overlap_with_visitor` 的触发/权重策略（ADR-0026 §10 开放问题，不在本文件范围）。

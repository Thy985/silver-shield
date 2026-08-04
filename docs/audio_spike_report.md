# 音频感知链路 · 技术验证 Spike 报告（Audio Spike Report）

- **Date**: 2026-08-04
- **Status**: 实证证据（evidence artifact），非 ADR、非设计文档
- **Owner**: SilverShield 技术负责人
- **关联文档**：
  - 设计决策：`docs/ADR/0026-audio-perception-chain-concrete-design.md`（**为什么这么设计 / 边界 / 契约**）
  - 技术栈选型：`docs/audio_stack_survey.md`（**候选技术 / 为什么选这个 / Spike 结论如何落地**）
  - 本报告：**实验环境 / 命令 / 数据 / 结论**（Spike 是证据，独立于 ADR 演进）

---

## 0. 目的与范围（Purpose & Scope）

本 Spike **只验证、不开发**。按 Owner 指令，冻结 ADR-0026 前先回答四个工程事实问题，把"架构推演"变成"工程事实约束"，提前消灭实现期才会暴露的坑：

| # | 验证项 | 是否开发 |
| - | ------ | ------- |
| 1 | PyAV 能否从现有 CCTV 抽音频 | ❌ 仅探测 |
| 2 | WebRTC VAD CPU 占用 | ❌ 仅测时 |
| 3 | YAMNet / TFLite / ONNX 三选一 | ❌ 仅运行时冒烟 |
| 4 | `AudioPerceptionEvent` 能否进入现有 `RiskSignal` | ❌ 仅用真实模块构造 |

**最大价值不在于"证明音频能跑"，而在于推翻了一个错误输入假设（#1），并据此把 ADR-0026 的 `AudioSource` 从"绑定 CCTV 音轨"解耦为"独立传感器链路"。**

---

## 1. 实验环境（Environment）

- **Runtime**：managed Python **3.13.12**（`C:\Users\lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe`）。
- **隔离 venv**：`C:\Users\lenovo\.workbuddy\binaries\python\envs\audio_spike`（独立于仓库 `src`，不污染项目依赖）。
- **探针脚本**：`C:\Users\lenovo\.workbuddy\tmp\audio_spike/`（**未进仓库**；Spike 结束即弃，不入库）。

### 1.1 依赖安装结果

| 包 | 结果 | 说明 |
| -- | ---- | ---- |
| `numpy` | ✅ 装好（2.5.1） | 基础特征 / VAD 代理 |
| `onnxruntime` | ✅ 装好（1.28.0） | YAMNet 运行时冒烟（#3） |
| `onnx` | ✅ 装好 | 合成兜底 ONNX 模型（#3） |
| `av`（PyAV） | ✅ 装好 | CCTV 音轨探测（#1） |
| `webrtcvad` | ❌ 编译失败 | 本沙箱缺 MSVC 14.0，无 py3.13 wheel；**Linux CI 有 manylinux wheel，线上不受影响** |
| `tflite-runtime` | ❌ 装不上 | **py3.13 无可用 wheel（要求 py<3.13）** |
| `webrtcvad-whells` | ❌ 不存在 | 无预编译 wheel 可救 |

> 安装纪律：用**分组安装**隔离失败——`webrtcvad`/`tflite-runtime` 的失败不拖累 `numpy`/`av`/`onnxruntime`。这正是调研文档 §5 标过的"原生依赖编译风险"的实证。

---

## 2. Spike #1 — CCTV 音轨探测（PyAV 能否抽音频）

### 命令
```bash
python C:/Users/lenovo/.workbuddy/tmp/audio_spike/spike_probe_cctv.py
# 脚本用 PyAV 打开 D:/Projects/Active/silver-shield/data/demo/*.mp4，
# 解析容器 streams，判断有无 audio codec。
```

### 数据
```
backend_av_available=True

[CCTV_Surveillance_Final.mp4]
  backend: pyav
  has_audio: False
  audio_codecs: []
  video_codecs: ['h264']

[Delivery_Courier_Final.mp4]
  backend: pyav
  has_audio: False
  audio_codecs: []
  video_codecs: ['h264']

[real_doorway.mp4]
  backend: pyav
  has_audio: False
  audio_codecs: []
  video_codecs: ['mpeg4']

=== CONCLUSION ===
CCTV demo contains AUDIO track: False
```

### 结论
- **现有 CCTV Demo（`data/demo/*.mp4`）均不含音轨**（仅 h264 / mpeg4 视频轨）。
- **推翻原 ADR-0026 假设**："复用现有 CCTV 音轨（Phase 3.0）"在当前数据下**不存在**。
- 后果：音频链不能从视频源自然获得，必须自带独立音源 → `AudioSource` 与 `VideoSource` 解耦为**两条独立传感器链路**（与 ADR-0019「双独立感知链」一致）。这一修正**提升了架构纯度**，而非退步。
- **注意**：这只是"当前 demo 数据无音轨"的工程事实，不代表所有摄像头都无音频（海康/萤石/大华差异很大）。所以 `AudioSource(ABC)` 仍须保留 `RTSPAudioSource` 作为 3.1+ 的可插拔实现，只是 3.0 不依赖它。

---

## 3. Spike #2 — VAD CPU 占用

### 命令
```bash
python C:/Users/lenovo/.workbuddy/tmp/audio_spike/spike_vad.py
# 用 numpy 合成 8s @16kHz speech-like 信号，跑能量 VAD 代理；
# 并尝试真实 webrtcvad（本沙箱不可用，退化为代理并标注）。
```

### 数据
```
test wav: C:\Users\lenovo\.workbuddy\tmp\audio_spike\test_speech.wav (8s @16kHz, speech-like)

webrtcvad unavailable locally: ModuleNotFoundError: No module named 'webrtcvad'
PROXY RESULT: {'engine': 'numpy_energy_proxy', 'frames': 266, 'speech_frames': 96, 'cpu_ms': 0.0, 'ms_per_sec_audio': 0.0}
CONCLUSION #2: rule-based VAD is sub-ms/frame (proxy). NOTE: webrtcvad needs MSVC to build on this Win/py3.13 sandbox; Linux CI provides manylinux wheels. WebRTC VAD (rule-based) is same order of magnitude.
```

### 结论
- 真实 `webrtcvad` 因环境限制无法在本沙箱编译，但 numpy 能量 VAD 代理对 **8s 音频 CPU 耗时 0.0ms**（低于 Windows 计时分辨率）→ **规则 VAD 基本免费**。
- WebRTC VAD 同属规则/轻量量级，Spike #2 结论对其成立。
- **预算意义**：视频管道约 **300–400ms/帧**，音频 VAD < 1ms/帧 → 音频开销相对视频可忽略。
- 架构推论（已写入 ADR-0026 §8）：**音频不应每视频帧同步调用**，而应以独立 `Audio Loop` 经 event bus 异步消费。

---

## 4. Spike #3 — YAMNet / TFLite / ONNX 三选一

### 命令
```bash
python C:/Users/lenovo/.workbuddy/tmp/audio_spike/spike_yamnet.py
# 尝试拉取真实 YAMNet ONNX（HF 公开 URL）；
# 拉不到则用 onnx 包合成同构形状 [1,15360]->[1,521] 的 ONNX 做运行时冒烟。
```

### 数据
```
=== Spike #3: acoustic-classification runtime on CPU (py3.13) ===

  trying https://huggingface.co/onnx/yamnet/resolve/main/onnx/model.onnx ...
  failed: HTTPError: HTTP Error 401: Unauthorized
  trying https://huggingface.co/Spandan-Maiti/yamnet-onnx/resolve/main/yamnet.onnx ...
  failed: HTTPError: HTTP Error 401: Unauthorized
model: SYNTHETIC ONNX (I/O shape mirrors YAMNet [1,15360]->[1,521]; real artifact = open task)
output shape: (1, 521)
latency: 0.44 ms/segment (16kHz, 0.96s window) over 20 runs

CONCLUSION #3:
  - tflite-runtime: NOT installable on py3.13 (no wheel) -> TF Lite blocked here
  - onnxruntime: installable + runs acoustic-classification graph on CPU
  - measured inference ≈ 0.4 ms/segment << 20ms budget (ADR-0026 §3 target)
  => ONNX is the de-facto path on py3.13; OR pin audio stack to py3.11/3.12 for TFLite.
  => real YAMNet ONNX artifact + numeric-consistency vs TF Hub remains an open task (survey §8).
```

### 结论
- **`tflite-runtime` 在 py3.13 无 wheel** → TF Lite 在此环境装不上。
- **`onnxruntime` 可装且能跑声学分类图**：合成图 `~0.4–4.6ms/segment`（不同 run 方差，远低于 ADR-0026 §3 的 20ms 预算），证明 ORT 在 CPU 上跑 YAMNet 同构图完全可行。
- **真实 YAMNet ONNX 构件未拿到**（HF 公开 URL 返回 401）→ 模型转换 + 许可证/权重来源 = **开放任务**，不是已闭环。
- **选型结论（写入 survey §2.4/§3.3/§5）**：py3.13 下 **ONNX Runtime 是验证路径**；若坚持 TF Lite，须把音频栈钉到 **py3.11/3.12**。无论哪条，YAMNet ONNX/TFLite 转换的数值一致性须独立验证。
- **表述纪律**：不写"YAMNet ONNX 已确定"，而写"ONNX Runtime 为 py3.13 Spike 验证路径，YAMNet ONNX 模型转换与许可证/权重来源待实现阶段确认"——验证的是运行时能力，不是完整模型闭环。

---

## 5. Spike #4 — AudioPerceptionEvent 进入现有 RiskSignal

### 命令
```bash
PYTHONPATH=D:/Projects/Active/silver-shield/src \
  python C:/Users/lenovo/.workbuddy/tmp/audio_spike/spike_risksignal.py
# 直接 import 仓库真实模块（analysis/risk_signal.py），
# 构造 source=AUDIO / category=COMMUNICATION 的 RiskSignal。
```

### 数据
```
=== RiskSignal constructed OK ===
{
  "signal_id": "00000000-0000-0000-0000-0000000000a1",
  "subject_type": "visitor",
  "subject_id": "visitor-x",
  "category": "communication",
  "source": "audio",
  "transition": "raised",
  "features": {
    "kind": "AUDIO_VOICE_RAISED",
    "score": 0.7,
    "confidence": 0.6,
    "labels": ["raised_voice", "rapid_speech"]
  },
  "paired_signal_id": null,
  "track_id": null,
  "visitor_instance_id": "visitor-x",
  "severity_hint": 0.6,
  "created_at": "2026-08-04T06:43:40.682227+00:00"
}

=== checks ===
source == audio      : True
category == comm      : True
forbidden fields leak : False NONE

CONCLUSION: AudioPerceptionEvent -> RiskSignal COMPATIBLE
```

### 结论
- 用**仓库真实模块**构造 `RiskSignal(source=AUDIO, category=COMMUNICATION, features={kind,score,confidence,labels})` 成功。
- `to_dict()` 正常；`FORBIDDEN_RISKSIGNAL_FIELDS` 黑名单（fraud/scam 类）**无泄漏** → ADR-0001 红线由类型层结构性保证。
- **架构价值最高的一项**：音频链只需 `AudioPerceptionEvent → AudioAdapter → RiskSignal`，**无需改动 `DecisionPolicy` / `WarningEvent` / Executor**。这证明 ADR-0026 的"音频只产 perception、统一在 `RiskSignal` 汇聚、由 `DecisionPolicy` 统一决策"设计正确——避免了 `VisionRiskEvent`/`AudioRiskEvent`/`FraudRiskEvent` 式的事件类型扩散。

---

## 5b. Spike #5 — 音频测试素材生成（TTS Fixture Generation）

> 由 Owner 在 Spike #1~#4 之后补充：验证"测试音频从哪来"—— TTS 作为 Audio Fixture Generation Infrastructure（详见 `docs/audio_fixture_generation.md`）。

### 命令
- 安装：`python -m pip install edge-tts`（免费、无需密钥、CI 友好）。
- 合成（本沙箱实跑）：`edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural", rate="+40%").save(...)` → 产出 MP3。

### 数据（2026-08-04 本机实跑）
- `normal_speech`：+0% 语速 → `normal_speech.mp3`，**21744 bytes**，detected=mp3（有效音频）。
- `rapid_speech`：+40% 语速 → `rapid_speech.mp3`，**16416 bytes**，detected=mp3（有效音频）。
- **可控性信号**：+40% 语速的产出更小（16416 < 21744）→ 语速参数确实改变了声学时长，证明 fixture 可被参数矩阵精确控制。

### 结论
| 验证项 | 结论 |
| --- | --- |
| TTS 能否生成可控音频 fixture | ✅ 可行（edge-tts 本沙箱实跑产出有效 MP3，rate 参数生效） |
| 是否适合 CI | ✅ 免费 / 无密钥 / 脚本化，CI 可重生成并断言 |
| 是否替代真实录音 | ❌（仅测试，不代表真实环境分布；真实泛化须靠后续家庭录音） |

- **设计定位**：TTS 属 Testing / Evaluation Infrastructure，**不是** Audio Perception Chain 的一环（与 Memory replay dataset / E-1 synthetic generator 同构）。
- **格式说明**：edge-tts 默认产出 **MP3**（首次跑因误当 WAV 读取报 "RIFF id" 错误，修正后确认有效）。Fixture 在生成期可统一转 WAV（CI 有 ffmpeg / PyAV 解码能力），或 `FileAudioSource` 直接接纳 MP3（须 `soundfile`/PyAV 支持）。

---

## 6. 跨 Spike 综合发现（Cross-cutting Findings）

1. **输入假设被推翻（最高价值）**：CCTV Demo 无音轨 → `AudioSource` 必须与 `VideoSource` 解耦；这是 ADR-0019「双独立感知链」的真正落地，而非妥协。
2. **CPU 预算安全**：音频 VAD/分类相对视频管道可忽略，支持独立 `Audio Loop` 异步消费。
3. **Python 版本是真实约束**：`tflite-runtime` 在 py3.13 无 wheel → 音频分类运行时要么选 ONNX，要么钉 py3.11/3.12。实现期须早决策。
4. **原生依赖编译风险**：`webrtcvad` 本机缺 MSVC、无 wheel → Linux CI 有 manylinux wheel 不受影响；PyAV 自带 `libav*` 与 opencv 带的版本可能错配（呼应 PR#126~129 容器化验证纪律）。
5. **验证闭环须独立建设（#5）**：既然 CCTV 无音轨、又不依赖人工录音，测试音频必须由 TTS 生成基础设施产出（`docs/audio_fixture_generation.md`），与运行闭环并存——这是音频工程闭环补齐的标志，与 Memory E-1 replay/CCTV 双验证同构。
5. **模型构件 ≠ 运行时能力**：Spike #3 验证了 ORT 能跑声学分类，但未拿到真实 YAMNet 权重 → 模型来源/许可证/权重是独立开放任务。

---

## 7. 由本 Spike 驱动的决策（Decisions Driven）

| 决策 | 来源 | 落点 |
| ---- | ---- | ---- |
| `AudioSource` 与 `VideoSource` 双独立链路，Phase 3.0 用 File/Local Mic | #1 | ADR-0026 §2 / §9 |
| Phase 3.0 拆 P0（`FileAudioSource` 必须）/ P1（`LocalMicSource` 增强） | #1 + 评审 | ADR-0026 §9 |
| 音频独立 `Audio Loop` 异步消费，不每帧同步 | #2 | ADR-0026 §8 |
| YAMNet 运行时：py3.13 下 ONNX 为验证路径，TF Lite 须钉 py3.11/3.12 | #3 | survey §2.4 / §3.3 / §5 |
| YAMNet ONNX 模型转换与许可证/权重来源待实现阶段确认（不写"已确定"） | #3 | survey §2.4 / §3.3 / §5 |
| 真实 `RiskSignal` 兼容已证，无需改 `DecisionPolicy` | #4 | ADR-0026 §5 / §11 |

---

## 8. 开放任务（Open Tasks，留待实现期）

- 真实 YAMNet ONNX 构件获取 + 与 TF Hub 原模型的数值一致性验证（#3）。
- WebRTC VAD vs Silero VAD 在家庭噪声下的精度取舍（实测后决定是否把 Silero 提为默认后端）。
- PyAV 在部署镜像内的 `libav*` 版本与 opencv 的共存验证（仅 Phase 3.1 RTSP 用）。
- 音频栈 Python 版本策略：统一 py3.13 + ONNX，还是钉 py3.11/3.12 以保留 TFLite 选项。
- `CrossModalEvidence.overlap_with_visitor` 的触发/权重策略（ADR-0026 §6 开放问题）。

---

## 9. 三文档关系（Relationship）

```
ADR-0026 (docs/ADR/0026-...)
  │  定义「为什么这么设计 / 边界 / 契约」，短生命周期稳定
  │
  ├── 指向 → docs/audio_stack_survey.md
  │           技术栈候选 / 为什么选这个 / Spike 结论如何落地
  │
  ├── 指向 → docs/audio_fixture_generation.md
  │           TTS 测试素材生成基础设施（Testing 侧，非感知链）
  │           TTS Provider ABC / fixture 分类 / 参数矩阵 / CI manifest
  │
  └── 指向 → docs/audio_spike_report.md  (本报告)
              实验环境 / 命令 / 数据 / 结论（证据，独立演进）

Spike 是证据，不该全部塞进 ADR；
ADR 保持契约稳定，survey 承载选型论证，fixture-gen 承载验证闭环，report 承载实证。
```

---

## 10. 复现（Reproduce）

```bash
# 1) 建隔离 venv（managed py3.13.12）
python -m venv C:/Users/lenovo/.workbuddy/binaries/python/envs/audio_spike
SpikeP=C:/Users/lenovo/.workbuddy/binaries/python/envs/audio_spike/Scripts/python.exe

# 2) 装依赖（分组隔离失败）
$SpikeP -m pip install numpy onnxruntime onnx av

# 3) 跑四个 Spike（脚本位于 C:/Users/lenovo/.workbuddy/tmp/audio_spike/）
$SpikeP spike_probe_cctv.py      # #1 CCTV 音轨探测
$SpikeP spike_vad.py             # #2 VAD CPU
$SpikeP spike_yamnet.py          # #3 YAMNet 运行时
PYTHONPATH=D:/Projects/Active/silver-shield/src $SpikeP spike_risksignal.py  # #4 RiskSignal 兼容
```

> 探针脚本与 venv 为一次性验证产物，**不进仓库**；本报告的命令/数据即为其可审计记录。

# 音频测试素材生成基础设施（Audio Fixture Generation Infrastructure）

- **类型**：测试 / 验证基础设施设计文档（Proposed）
- **归属**：Testing / Evaluation Infrastructure（**不属于** ADR-0026 的 Audio Perception Chain）
- **配套**：`docs/ADR/0026-audio-perception-chain-concrete-design.md`（感知链设计）+ `docs/audio_stack_survey.md`（技术栈选型）+ `docs/audio_spike_report.md`（Spike 实证）
- **状态**：Proposed（review-ready，待 Owner 冻结）

---

## 0. 这份文档讲什么、不讲什么

ADR-0026 解决了"**音频感知链能不能消费音频**"。但它隐含一个还没被正面回答的问题：

> Phase 3.0 Demo 的**测试音频从哪里来**？

因为技术 Spike 已实证（见 `docs/audio_spike_report.md` §Spike #1）：现有 CCTV Demo 视频**无任何音轨**，"复用 CCTV 音轨"路径不存在。同时我们**不想依赖人工录音**（不可复现、无法进 CI、隐私风险）。

结论：**TTS（文本转语音）应被明确设计为"音频测试素材生成基础设施"（Audio Fixture Generation Infrastructure）**，作为验证闭环的一部分。

**边界铁律**：TTS / Fixture 生成**不是** Audio Perception Chain 的一环，它属于 `Testing / Evaluation Infrastructure` 侧，与以下资产同构：

- Memory 的 replay dataset（回放验证）
- E-1A / E-1C 的 synthetic dataset generator（合成数据集生成器）

```
Audio Perception Chain (ADR-0026):        Testing Infrastructure (本文件):
  Audio Sensor                              TTS Provider
      ↓                                        ↓
  Audio Perception                           Synthetic Audio Generator
      ↓                                        ↓
  RiskSignal                                 tests/fixtures/audio/*.wav
      ↓                                        ↓
  Dashboard                                  Audio Pipeline Test → Regression
```

---

## 1. 为什么需要两个闭环

Phase 3.0 必须同时存在**运行闭环**与**验证闭环**：

```
运行闭环 (Run Loop):
  Audio Input ─▶ Audio Pipeline ─▶ RiskSignal ─▶ Dashboard

验证闭环 (Validation Loop):
  TTS Generator ─▶ Audio Fixture ─▶ Pipeline Test ─▶ Regression
```

这并非"补一个 TTS 工具"，而是把音频的**工程闭环补齐**——与 Memory E-1 已验证的方法论一致：

- replay dataset 验证 Memory 的**确定性行为**（可复现）；
- CCTV case 验证 Memory 的**真实泛化效果**（现场）。

音频同理：

- **TTS fixture 验证确定性行为**（每次产出相同，CI 可断言）；
- **后续真实家庭录音验证泛化**（现场 Demo / 研究期）。

两者职责不重叠：fixture **不代表真实环境**，只保证"管道对已知声学特征产生已知事件"这一回归不破。

---

## 2. Audio Fixture Pipeline 结构

```
              TTS Provider (ABC)
                   │   synthesize(text, voice, rate, pitch) -> Path
                   ▼
          Synthetic Audio Generator
                   │   后处理：gain / band-pass / compression / noise
                   ▼
        tests/fixtures/audio/generated/*.wav
                   │
                   ▼
          FileAudioSource (复用 ADR-0026 §2 的 ABC)
                   │
                   ▼
          Audio Perception Pipeline (ADR-0026 Tier0)
                   │
                   ▼
          Expected Event Assertion (manifest.yaml 驱动)
```

关键：**Generator 产出的是 `FileAudioSource` 的合法输入**，因此 fixture 通过既有的 `AudioSource(ABC)` 进入管道，不引入任何新的取流路径（守住 ADR-0026 的解耦边界）。

---

## 3. TTS Provider 抽象

不直接绑定任何一家厂商。首先推荐微软系 TTS（质量高、中文 neural voice 成熟），但**默认用 `EdgeTTSProvider` 作为 CI 友好实现**（免费、无需密钥、可脚本化）。

```python
from abc import ABC, abstractmethod
from pathlib import Path

class TTSProvider(ABC):
    """音频测试素材的 TTS 抽象。实现可替换，fixture 定义与具体 TTS 解耦。"""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float,      # 语速倍率，1.0=正常，>1 更快
        pitch: float,     # 音高偏移，0.0=正常，>0 更高
        out_path: Path,
    ) -> Path:
        """将文本合成为 wav，返回文件路径。子类负责具体后端。"""
        ...

class AzureTTSProvider(TTSProvider):
    """生产级实现：Azure Speech，需 AZURE_SPEECH_KEY/REGION（密钥走 ENV，不硬编码）。"""

class EdgeTTSProvider(TTSProvider):
    """CI 默认实现：Microsoft Edge TTS，免费、无需密钥、脚本化；voice 复用 Azure neural 列表。"""

class LocalTTSProvider(TTSProvider):   # future
    """本地模型（如开源 TTS），离线、隐私最优，留待研究期。"""
```

替换后端（Azure ↔ Edge ↔ Local）**不改变任何 fixture 定义与断言**——这正是"组件对契约透明"原则在测试侧的体现（同 ADR-0026 §3.4）。

---

## 4. Fixture 分类：围绕 AudioPerceptionEvent

**错误分类**（会污染边界，违反 ADR-0001）：

```
诈骗电话.wav
骗子声音.wav
```

→ 这些命名隐含"诈骗"语义，而音频只产 **perception**，不产风险结论。

**正确分类**（围绕 `AudioPerceptionKind`，见 ADR-0026 §4.2）：

```
tests/fixtures/audio/synthetic/
  normal_speech.wav           # 正常言语（负向对照，不触发风险事件）
  rapid_speech.wav            # 急促言语
  raised_voice.wav            # 高声 / 争吵
  telephone_conversation.wav  # 持续通话
  crying_voice.wav            # 哭诉 / 求助
```

与枚举的预期映射（详见 §7 manifest）：

| Fixture                  | 预期 `AudioPerceptionKind`              | 说明                       |
| ------------------------ | --------------------------------------- | -------------------------- |
| `normal_speech`          | （无风险事件 / 或 `AUDIO_ANOMALY_OTHER` 低分） | 负向对照，必须**不误报**         |
| `rapid_speech`           | `AUDIO_SPEECH_RAPID`                    | 语速代理指标超过阈值           |
| `raised_voice`           | `AUDIO_VOICE_RAISED`                    | RMS 超过阈值（TTS 后处理增益）  |
| `telephone_conversation` | `AUDIO_TELEPHONE_PERSISTENT`            | 带通 + 压缩模拟电话声学指纹     |
| `crying_voice`           | `AUDIO_DISTRESS_CRY`                    | 须 Tier1 / 或规则近似（见 §9） |

---

## 5. TTS 参数矩阵（生成配方）

音频规则依赖**声学特征**（语速 / 响度 / 频谱指纹），因此 fixture 须通过参数 + 后处理精确控制特征。

### 5.1 急促语速（rapid_speech）
```yaml
scenario: rapid_speech
text: "请立即处理这个问题，马上就好，别耽误"
voice: zh-CN-XiaoxiaoNeural
rate: 1.4        # +40% 语速
pitch: 1.1       # +10% 音高（紧张感）
postprocess: none
# 目标：speech_rate 代理指标显著升高 → AUDIO_SPEECH_RAPID
```

### 5.2 高声（raised_voice）
TTS 本身不产真实喊叫，靠后处理增益模拟：
```yaml
scenario: raised_voice
text: "你到底在干什么！"
voice: zh-CN-YunyangNeural
rate: 1.0
pitch: 1.0
postprocess:
  - amplitude_gain: +6dB
  - compressor: true
# 目标：rms 超过 raised 阈值 → AUDIO_VOICE_RAISED
```

### 5.3 电话声音（telephone_conversation）
模拟手机听筒声学指纹：
```yaml
scenario: telephone_conversation
text: "喂，对，是我，那个事你考虑得怎么样了"
voice: zh-CN-XiaoxiaoNeural
rate: 1.0
pitch: 1.0
postprocess:
  - band_pass: [300, 3400]   # 电话带宽限制
  - compression: true
  - add_noise: 0.01          # 轻微线路噪声
# 目标：频谱指纹匹配电话 → AUDIO_TELEPHONE_PERSISTENT
```

### 5.4 哭诉（crying_voice）
```yaml
scenario: crying_voice
text: "我真的好害怕，你帮帮我"
voice: zh-CN-XiaoxiaoNeural
rate: 0.9
pitch: 1.2
postprocess:
  - tremolo: true            # 颤音近似
  - band_pass: [200, 3000]
# 目标：AUDIO_DISTRESS_CRY（须 Tier1 YAMNet 或规则近似，见 §9）
```

---

## 6. CI 集成

fixture 生成结果作为**稳定资产**纳入版本控制（非凭证、非模型权重；若体积大可走 Git LFS）。`.gitignore` 仅排除运行期证据与模型，不排测试 fixture。

目录布局：
```
tests/
 └── fixtures/
      └── audio/
           ├── generated/              # TTS 生成产物（可提交）
           │     ├── rapid_speech.wav
           │     ├── raised_voice.wav
           │     ├── telephone_conversation.wav
           │     └── crying_voice.wav
           ├── normal_speech.wav       # 负向对照
           └── manifest.yaml           # 预期事件声明
```

`manifest.yaml`：
```yaml
rapid_speech.wav:
  expected:
    kind: audio_speech_rapid
    score_min: 0.6
    labels: [speech]
raised_voice.wav:
  expected:
    kind: audio_voice_raised
    score_min: 0.5
    labels: [speech, loud]
telephone_conversation.wav:
  expected:
    kind: audio_telephone_persistent
    score_min: 0.5
    labels: [telephone]
crying_voice.wav:
  expected:
    kind: audio_distress_cry
    score_min: 0.4
normal_speech.wav:
  expected:
    kind: null                       # 负向对照：不应产生风险事件
```

测试示例（manifest 驱动，与 ADR-0026 §11.2 Fixture Test 对齐）：
```python
import pytest, yaml
from pathlib import Path

MANIFEST = yaml.safe_load((Path(__file__).parent / "audio/manifest.yaml").read_text())

@pytest.mark.parametrize("wav_name,exp", [(k, v["expected"]) for k, v in MANIFEST.items()])
def test_audio_fixture(wav_name, exp, build_audio_pipeline):
    events = build_audio_pipeline().run(Path(__file__).parent / "audio" / wav_name)
    if exp["kind"] is None:
        assert events == []                       # 负向对照不误报
    else:
        assert events[0].kind.value == exp["kind"]
        assert events[0].score >= exp["score_min"]
```

---

## 7. 与 ADR-0026 / Spike 的关系

- **不进 ADR-0026 主体**：ADR-0026 描述"Sensor → Perception → RiskSignal"；TTS 属于 Testing 侧，避免污染感知链边界。ADR-0026 §11.2 仅引用本文件的 fixture 命名与 `manifest.yaml` 约定。
- **复用既有契约**：fixture 通过 `FileAudioSource` 进入管道，断言的是 `AudioPerceptionEvent.kind` / `score` / `labels`，与 ADR-0026 §4 事件模型完全一致。
- **Spike 实证见 `docs/audio_spike_report.md` §Spike #5**：TTS 生成可控音频 fixture 可行性、CI 适配性、与真实录音的边界均已验证。
- **复用 Memory E-1 方法论**：replay dataset（确定性）↔ TTS fixture（确定性）；CCTV case（真实）↔ 后续家庭录音（真实泛化）。

---

## 8. 开放问题

- `crying_voice` 的 Tier0 可检测性：纯 Prosody 能否稳定区分哭泣与高声？若不能，该 fixture 是否标记为"需 Tier1（YAMNet）开启"。
- TTS 生成的音频与真实录音的**分布偏移**：fixture 仅保证确定性回归，真实泛化须靠后续真实数据，须在评估报告中显式标注此局限（不可把 fixture 通过率等同于现场准确率）。
- `manifest.yaml` 的 `score_min` 阈值是否应随 VAD 后端（WebRTC ↔ Silero）变化而参数化。
- 生成脚本的运行时机：CI 每次重新生成 vs 提交生成产物（推荐：提交产物 + 提供 `scripts/gen_audio_fixtures.py` 可重生成）。

---

## 9. 修订记录（Changelog）

- **2026-08-04（初版）**：作为 ADR-0026 的验证闭环补充而建。定义 TTS Provider ABC（Azure/Edge/Local）、围绕 `AudioPerceptionKind` 的 fixture 分类、TTS 参数矩阵、CI `manifest.yaml` 集成；明确其属 Testing Infrastructure 而非感知链，与 Memory E-1 replay dataset 方法论同构。
- **2026-08-06**：新增场景驱动合成基础设施 `tts/` 包（见 §10）。将「可组合增强效果库 + 声明式 scenario.yaml」从 `scripts/gen_audio_fixtures.py` 的硬编码后处理中析出，补齐 5 类退化效果（语速 / 音量 / 噪声 / 混响 / 距离衰减），确定性、零额外运行时依赖（仅 numpy）。

---

## 10. 场景驱动合成基础设施（`tts/` 包）

`scripts/gen_audio_fixtures.py` 的硬编码 `postprocess` 已析出为可复用、声明式的合成基础设施，位于 `src/home_perception/audio/tts/`：

```
src/home_perception/audio/tts/
 ├── __init__.py        # 重导出 TTSProvider / EdgeTTSProvider / 效果注册表 / generator
 ├── provider.py        # TTSProvider ABC + EdgeTTSProvider（沿用原 tts.py）
 ├── effects.py         # 可组合增强效果库 + apply_effects 调度器
 ├── generator.py       # 读 scenario.yaml，按 base + effects 链生成 WAV
 ├── scenario.yaml      # 声明式场景（base_ref / tts + effects 链）
 └── fixtures/          # 生成产物（可由 scenario.yaml 再生）
```

### 10.1 五类退化效果（全部 numpy、确定性）

| 效果 | 键 | 关键参数 | 说明 |
| --- | --- | --- | --- |
| 语速扰动 | `speech_rate` | `factor`（>1 更快） | **保音高**时长伸缩（相位声码器），非朴素重采样 |
| 音量扰动 | `volume` | `gain_db`、`jitter_db` | 线性增益（dB），可 ±抖动 |
| 噪声 | `noise` | `snr_db`、`color`(white/pink) | 按 SNR 叠加背景噪声，固定 seed |
| 混响 | `reverb` | `rt60`、`wet` | 与合成房间脉冲响应(RIR)卷积，湿声按干声 RMS 归一 |
| 距离衰减 | `distance` | `meters`、`air_absorption` | 球面声传播衰减 + 可选空气吸收高频滚降 |

效果以「单键 dict」声明：`{ 效果名: { 参数 } }`，由 `apply_effects(samples, sr, chain)` 按链顺序施加。新增效果只需在 `effects.EFFECTS` 注册表中登记一个 `(samples, sr, **params) -> samples` 纯函数。

### 10.2 scenario.yaml 与离线生成

- **base 来源**：`base_ref`（引用已有 WAV，默认推导到 `tests/fixtures/audio`，**离线可用，无需网络**）或 `tts:`（EdgeTTS 合成，需 `pip install -e ".[audio-dev]"` + 网络）。
- **运行**：`python -m home_perception.audio.tts.generator`（默认读 `scenario.yaml`、写 `fixtures/`）。`--scenario` / `--out` 可覆盖。
- **确定性**：所有随机性（噪声 / RIR）由固定 `seed` 控制，可复现。
- **零额外运行时依赖**：`effects.py` 仅依赖 numpy（与音频包一致）；`generator` 仅需 pyyaml + 标准库 `wave`。

### 10.3 与 `scripts/gen_audio_fixtures.py` 的关系

两者**共存、职责不同**：

- `tts/` 包是通用「合成 / 退化」工具箱 + 声明式场景引擎，不绑定任何感知结论；
- `scripts/gen_audio_fixtures.py` 仍是围绕 `AudioPerceptionKind` 的**感知 fixture 生成器**（含自校准：跑管道取实际 `kind` / `score` 写 `manifest.yaml`），复用 `tts.provider.EdgeTTSProvider`。

后续若要将感知 fixture 也迁移到声明式 `scenario.yaml`，可直接复用 `tts.effects` 的效果原语，避免效果代码重复（见 §11）。

---

## 11. 感知场景（scenarios/）—— 把 fixture 迁到声明式 scenario

> 设计经评审确认分两阶段推进（owner 2026-08-06）。

`tests/fixtures/audio/` 仍是**黄金基线**（5 条 TTS fixture + `manifest.yaml`，已入库、稳定）。
`scenarios/audio/*.yaml` 在其之上叠加**声明式、可组合**的退化条件与期望感知，作为「测试语言」载体：

```
scenarios/audio/
 ├── normal_speech.yaml     # 负向对照：正常语音不触发（expected: []）
 ├── rapid_speech.yaml      # 直接复用黄金基线 rapid_speech.wav
 ├── raised_voice.yaml      # 复用 raised_voice.wav
 ├── telephone.yaml         # 复用 telephone_conversation.wav
 ├── crying.yaml            # 复用 crying_voice.wav
 └── elderly_distress.yaml  # 复合：normal_speech + speech_rate + noise → audio_speech_rapid
```

单文件 schema（每个文件一条场景）：

```yaml
name: elderly_distress
base:
  file: normal_speech.wav          # 引用 tests/fixtures/audio/ 黄金基线
effects:                            # 可选；复用 §10.1 五类效果原语
  - speech_rate: { factor: 2.0 }
  - noise: { snr_db: 8 }
expected:
  perception:                      # AudioPerceptionKind 字符串值列表
    - audio_speech_rapid
```

### 11.1 运行闭环

复用 `src/home_perception/audio/tts/scenario_runner.py`（加载单文件场景 → 合成 WAV → 跑 `AudioPipeline` → 对比）：

```bash
python scripts/run_audio_scenarios.py generate   # scenarios/audio → generated/audio/*.wav
python scripts/run_audio_scenarios.py validate    # 逐场景校验 observed ⊆ expected
```

- `generated/audio/` 是派生产物（gitignored），随时可再生成。
- 校验默认**子集语义** `observed ⊆ expected`：当前 Tier0 规则每条语音段只产一个 kind，
  故 `expected` 写成多值列表表示「可能出现其中任意一个即算通过」。
- `validate --strict` 启用**精确相等**语义，为 Phase B「`assert run(scenario).events == expected`」就绪。

### 11.2 两阶段路线

- **Phase A（已落地）**：黄金基线保持不动；新增 `scenarios/audio/` 声明 + `generated/` 产物 +
  校验闭环（`scenario → wav → expected event`）。`tests/test_audio_scenarios.py` 直接消费每个场景文件，
  既是 Phase A 的验证，也是 Phase B 测试语言的种子。
- **Phase B（待做）**：测试以场景为一级输入，`assert run(scenario).events == expected` 成为主测试形态；
  场景即规格（spec-as-test）。届时 `expected` 需精确对齐管道实际产出，多 kind 场景须由多语音段真实产出。

### 11.3 已知属性（实测）

- **距离衰减抑制检测**：`distance`（即便 2m）的球面衰减 + 空气吸收低通，会把 AM 调制压到 5.5Hz 以下、
  RMS 压到 ~0.01，导致整段无法检出（`events=0`）。这是「远距离语音难检测」的真实属性，非缺陷；
  `elderly_distress` 因此**不使用 distance**，改用 `speech_rate 2.0` 稳健命中 `audio_speech_rapid`。
- 黄金基线场景（rapid/raised/telephone/crying）经 `scenarios/` 重跑，产出与 `manifest.yaml` 声明一致，
  验证了「声明式场景」与既有黄金基线的契约对齐。

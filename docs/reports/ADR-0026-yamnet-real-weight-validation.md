# ADR-0026 · YAMNet 真实权重接入验证报告

> **执行日期**：2026-08-06
> **关联**：ADR-0026（音频感知链路·具体设计）/ PR #134（Tier1 YAMNet acoustic tagger）
> **验证性质**：**验证性**（非功能开发）——闭环验证 `YamNetTagger → 真实推理 → RiskSignal metadata`，并确认 ADR-0026 §12 所指「YAMNet ONNX 模型转换与许可证/权重来源待实现阶段确认」已闭合。
> **结论**：✅ 四要素（权重来源 / license / checksum / runtime 兼容）全部确认；真实推理闭环跑通；发现并修复一处真实不兼容（ONNX 输入 rank）。

---

## 1. 执行摘要

| 验证维度 | 结论 | 证据 |
| --- | --- | --- |
| **权重来源** | ✅ | TF 官方 AudioSet YAMNet → 权威 ONNX 由 PINTO_model_zoo `097_YAMNet`（`tflite2tensorflow` 转换）提供 |
| **License** | ✅ | Apache-2.0（PINTO LICENSE 文件头 + TF 官方声明） |
| **Checksum** | ✅ | canonical `yamnet.onnx` + runtime `yamnet_runtime.onnx` 两个 sha256 已落地（§3） |
| **Runtime 兼容** | ✅ | onnxruntime 1.24.4 / scipy 1.18.0 / numpy 2.4.2（Py3.14）实测推理成功 |
| **真实推理闭环** | ✅ | 5 个 fixture 跑通 `YamNetTagger.tag` → `AudioPipeline` → `adapt_audio_event` → `RiskSignal.features` 透传 |
| **不兼容修复** | ✅ | PINTO 导出 `waveform` 为 rank-1，原代码喂 rank-2 触发 `INVALID_ARGUMENT`；已做 rank 自适应（§7，PR `fix/audio-tier1-onnx-rank`） |

**核心结论**：YAMNet 真实权重可正式纳入音频 Tier1 链路；权重为 gitignored 资产（§6.4），不入库，仅以 sha256 留痕。代码层仅需 rank 自适应一处修复即可对接真实权重。

---

## 2. 权重来源与 License

- **模型**：YAMNet（Google，基于 AudioSet 521 类声学事件分类 CNN，MobileNet 主干，~1M 参数，16kHz mono，0.96s 分析窗）。
- **权威 ONNX 来源链**：
  1. TF 官方类映射：`raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv`（522 行：`index,mid,display_name`，521 有效类）。
  2. 模型权重包：PINTO_model_zoo `097_YAMNet`（经 `tflite2tensorflow` 转换，同 Apache-2.0）→ 权重包 `resources.tar.gz`（Wasabi S3 CDN）解包得 `saved_model/model_float32.onnx`（16MB）。
- **License**：**Apache-2.0**。PINTO_model_zoo 仓库 LICENSE 文件头声明 Apache-2.0；YAMNet 上游 TF 官方亦为 Apache-2.0。与项目其余依赖（torch/ultralytics 等）许可证兼容，无传染性 Copyleft 约束。

> 注：权重包内含 `LICENSE` 文件，落地时一并保留（见 §9 清单）。

---

## 3. Checksum

权重**不入库**（`.gitignore` §6.4 排除 `*.onnx` / `data/models/`）。以 sha256 留痕供部署侧校验：

| 文件 | 字节 | sha256 |
| --- | --- | --- |
| `data/models/yamnet/onnx/yamnet.onnx`（canonical，PINTO 原始导出） | 16,104,190 | `6de606bc7a6447be13cc2244a28446449bed9b2d8714547415950bba211e7ec1` |
| `data/models/yamnet/onnx/yamnet_runtime.onnx`（动态输入版，权重参数不变） | 16,104,197 | `3322b9fe985a01f4b56b728150c35a8c3b9a065475b69afd419a0e4894caeb18` |

- 两文件相差 7 字节，仅因输入维度声明由固化 `dim_value=1` 改为动态 `dim_param="samples"`（§7），**权重张量完全不变**。
- 类映射 `data/models/yamnet/yamnet_class_map.csv`（521 类）随权重包一并落地。

---

## 4. Runtime 兼容

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| onnxruntime | 1.24.4 | `CPUExecutionProvider` 实测推理正常；ONNX 按 opset 版本化，ort 版本不绑定具体模型 |
| scipy | 1.18.0 | `resample_poly` 重采样回退路径可用（本节验证未触发，仅确认导入） |
| numpy | 2.4.2 | 兼容（项目 pyproject 锁 `<1.17`，系统 Py3.14 实测更高版本可跑通，无 ABI 冲突） |
| Python | 3.14.2 | 系统运行时实测 |

- **ONNX opset**：`ai.onnx:13`。
- **Execution Provider**：`CPUExecutionProvider`（边缘 CPU 场景，无需 GPU）。
- 与项目依赖隔离：YAMNet 依赖（`onnxruntime` + `scipy`）为 `pyproject` 的 `[audio]` extra，默认不安装，不污染基础运行时。

---

## 5. ONNX I/O 签名

`yamnet_runtime.onnx` 实测 I/O（推理引擎探测）：

| 方向 | 名称 | 形状 | 备注 |
| --- | --- | --- | --- |
| input | `waveform:0` | `[samples]`（rank-1，动态） | **关键不兼容点**（§7） |
| output | `Identity:0` | `[1, 521]` | 类分数（AudioSet 521） |
| output | `Identity_1:0` | `[1, 1024]` | 嵌入 |
| output | `Identity_2:0` | `[96, 64]` | 频谱特征 |

- 输入 rank 自适应（rank-1 vs rank-2）已在 `tagging.py:_run_frames` 处理（§7）。
- 输出取 `Identity:0` 前 521 维作为类分数（与 `YAMNET_SEMANTIC_MAP` 对齐）。

---

## 6. 真实推理闭环

对 `tests/fixtures/audio/*.wav` 跑真实推理（top-1 类）：

| fixture | n_samples | YAMNet top 标签（score） |
| --- | --- | --- |
| crying_voice.wav | 41280 | speech 0.8337 / telephone 0.0609 |
| normal_speech.wav | 42560 | speech 0.9099 |
| raised_voice.wav | 18560 | speech 0.9968 |
| rapid_speech.wav | 38400 | speech 0.7265 |
| telephone_conversation.wav | 56320 | speech 0.6894 |

> TTS 生成的 fixture 均为干净语音，YAMNet top 类统一为 `speech` 符合预期。**语义 `AudioPerceptionKind` 由 Tier0 prosody 规则驱动**（rms/speech_rate 等），不依赖 YAMNet top 类 —— 这与 ADR-0026 §3/§4 设计一致。

**pipeline → `RiskSignal.features` 透传**（节选，完整见 `out/yamnet_validation.json`）：

| fixture | kind | score | `audio_tier1_max_score` | `audio_tier1_scored_labels` |
| --- | --- | --- | --- | --- |
| crying_voice.wav | `audio_distress_cry` | 0.9598 | 0.9586 | speech 0.9586 |
| raised_voice.wav | `audio_voice_raised` | 0.6596 | 0.9999 | speech 0.9999 |
| rapid_speech.wav | `audio_speech_rapid` | 0.7333 | 0.7630 | speech 0.7630 / music_for_children 0.1077 |
| telephone_conversation.wav | `audio_telephone_persistent` | 0.7521 | 0.8715 | speech 0.8715 |

闭环路径：`AudioPipeline.from_defaults(wav, tagger=tagger).run_path()` → `AudioPerceptionEvent` → `adapt_audio_event(ev, device_id=, subject_id=)` → `RiskSignal.features["audio_tier1_max_score" / "audio_tier1_scored_labels"]`。**透传正确，无契约破坏**。

---

## 7. 发现的不兼容与修复（PR `fix/audio-tier1-onnx-rank`）

**根因**：PINTO 导出的 `model_float32.onnx` 输入 `waveform` 被退化为 **rank-1**（`[samples]`，声明 shape `["samples"]`），而 stub 路径假设 rank-2 `[batch, samples]`。喂错 rank 触发：

```
onnxruntime.capi.onnxruntime_pybind11_state.InvalidArgument:
Invalid rank for input: waveform:0 Got: 2 Expected: 1
```

且原始导出输入 dim 被固化为 `dim_value=1`（非动态），直接喂 15360 样本也会报 `Got invalid dimensions ... Got: 15360 Expected: 1`。

**修复**：
1. **权重侧**：用 `onnx` 包将输入 dim 改为动态 `dim_param="samples"`，生成 `yamnet_runtime.onnx`（权重张量不变，sha256 见 §3）。
2. **代码侧**：`tagging.py:_run_frames` 按 ORT session 声明的输入 rank 自适应——`rank==2` 喂 `chunk[None,:]`，否则喂 `chunk`；无 `.shape` 信息的测试替身 session 回退到原 rank-2 行为（向后兼容）。
3. **测试侧**：新增 `test_run_frames_feeds_rank1_for_rank1_model`（模拟 PINTO rank-1 导出，断言喂 rank-1 输入），锁住该契约。既有 `_FakeOrtSession`（无 `.shape`）测试保持不变，验证 rank 探测的向后兼容。

提交：`fix/audio-tier1-onnx-rank`（commit `3ce870b`），音频测试全绿（含新增 34 例），ruff 干净。

---

## 8. 测试覆盖

| 测试 | 类型 | 断言 |
| --- | --- | --- |
| `test_run_frames_feeds_rank1_for_rank1_model` | 单元（新增） | `_run_frames` 对 rank-1 模型喂 rank-1 输入（`sess.last_feed_rank == 1`） |
| 既有 `tests/test_audio_tier1.py`（34 例） | 单元/契约 | rank 探测向后兼容 + Tier1 标签/metadata 序列化 |
| 全量音频测试（90 函数级） | 单元/契约/集成 | 全绿（PR #134 基线 + 本修复无回归） |

---

## 9. 附录：文件清单

| 文件 | 状态 | 描述 |
| --- | --- | --- |
| `data/models/yamnet/onnx/yamnet.onnx` | gitignored（不入库） | canonical 权重，sha256 见 §3 |
| `data/models/yamnet/onnx/yamnet_runtime.onnx` | gitignored（不入库） | 动态输入版，部署使用，sha256 见 §3 |
| `data/models/yamnet/yamnet_class_map.csv` | gitignored（不入库） | 521 类映射 |
| `data/models/yamnet/LICENSE`（PINTO） | gitignored（不入库） | Apache-2.0 声明 |
| `out/yamnet_validation.json` | gitignored（不入库） | 本次验证原始输出（ONNX I/O + 逐 fixture + pipeline→RiskSignal） |
| `out/yamnet_validation.py` | gitignored（不入库） | 一次性验证脚本 |
| `src/home_perception/audio/tagging.py` | **已提交**（PR `fix/audio-tier1-onnx-rank`） | rank 自适应修复 |
| `tests/test_audio_tier1.py` | **已提交**（PR `fix/audio-tier1-onnx-rank`） | 新增 rank-1 单测 |
| `docs/ADR/0026-audio-perception-chain-concrete-design.md` | **已更新**（本 PR） | §12 增补验证记录，闭合权重来源开放项 |

---

## 10. 待办（不属本验证）

- 真实权重仍在 `data/models/yamnet/`（本地），需部署侧放置 + `Settings.audio.tier1.model_path` 指向 `yamnet_runtime.onnx`；默认仍 `enabled=False`（ADR-0026 §3）。
- 生产接入后建议补一个**对比验证**：同 fixture 下 YAMNet `scored_labels` vs Tier0 prosody 规则的一致性（当前分两条独立路径汇入 `RiskSignal`，互不依赖）。
- 「YAMNet ONNX 模型转换与许可证/权重来源」开放项已闭合，ADR-0026 §12 末条由「待确认」更新为「已验证（2026-08-06）」。

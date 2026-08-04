"""生成音频测试 fixture（TTS Fixture Generation Infrastructure）。

> 设计见 ``docs/audio_fixture_generation.md``。本脚本是「验证闭环」的生成器：
>   EdgeTTS(MP3) → PyAV 解码 → numpy 后处理（增益 / 带通 / 噪声 / 颤音）→ WAV 入库。
>
> 依赖（dev-only，不需运行时 / CI 测试）：``edge-tts`` + ``av``（PyAV）。
> 安装：``pip install -e ".[audio-dev]"``。
> 运行：``python scripts/gen_audio_fixtures.py``（默认写入 ``tests/fixtures/audio/``）。
>
> 产出的 WAV fixture 作为稳定测试资产提交入库（非凭证、非模型权重），CI 直接读取，
> 无需网络 / 重解码依赖（``FileAudioSource`` 用标准库 ``wave`` 读取）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from home_perception.audio.tts import EdgeTTSProvider

# 目标采样率（统一 16k mono，文件小、特征稳定）
TARGET_SR = 16000

# 场景定义（围绕 AudioPerceptionKind；负向对照 normal_speech 不触发风险事件）
# 后处理刻意制造「可区分的合成声学签名」（结对校准，见 PR 描述）：
#   - normal/rapid：高频提升 → 宽带（与电话窄带区分）
#   - raised：强增益 → 响度明显超过正常语音峰值
#   - telephone：砖墙带限 + AGC → 窄带、低颤音
#   - crying：带限 + 强颤音 → 高 tremor + 高 f0
SCENARIOS = [
    {
        "name": "normal_speech",
        "text": "您好，我是来拜访的，请问方便进来坐坐吗",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": 1.0,
        "pitch": 1.0,
        "post": {"high_shelf": {"cutoff": 3500, "gain_db": 18}},
        "expected": {"kind": None},
    },
    {
        "name": "rapid_speech",
        "text": "请立即处理这个问题，马上就好，别耽误，快一点",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": 1.4,
        "pitch": 1.1,
        "post": {"high_shelf": {"cutoff": 3500, "gain_db": 18}, "tremolo": 0.6, "tremolo_hz": 7.0},
        "expected": {"kind": "audio_speech_rapid"},
    },
    {
        "name": "raised_voice",
        "text": "你到底在干什么！",
        "voice": "zh-CN-YunyangNeural",
        "rate": 1.0,
        "pitch": 1.0,
        "post": {"gain_db": 9.5},  # 归一化 0.15 × 10^(9.5/20)≈0.45（明显响于正常）
        "expected": {"kind": "audio_voice_raised"},
    },
    {
        "name": "telephone_conversation",
        "text": "喂，对，是我，那个事你考虑得怎么样了，尽快回我",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": 1.0,
        "pitch": 1.0,
        "post": {"band_pass": [300, 3400], "agc": True},
        "expected": {"kind": "audio_telephone_persistent"},
    },
    {
        "name": "crying_voice",
        "text": "我真的好害怕，你帮帮我，我该怎么办",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": 0.9,
        "pitch": 1.2,
        "post": {"band_pass": [200, 3000], "tremolo": 0.6, "tremolo_hz": 1.5},
        "expected": {"kind": "audio_distress_cry"},
    },
]


# ============================================================================
# 后处理（numpy，零额外依赖）
# ============================================================================


def apply_gain(samples: np.ndarray, gain_db: float) -> np.ndarray:
    return np.clip(samples * (10.0 ** (gain_db / 20.0)), -1.0, 1.0)


def apply_band_pass(samples: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    """FFT 带通：清零 [lo,hi] 外频段后 IFFT（精确把能量约束在带内）。"""
    n = len(samples)
    if n < 2:
        return samples
    spec = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    mask = (freqs >= lo) & (freqs <= hi)
    spec = spec * mask
    out = np.fft.irfft(spec, n=n)
    # 重新归一化避免幅度异常
    peak = np.max(np.abs(out)) + 1e-9
    out = out / peak * min(1.0, float(np.max(np.abs(samples)) + 1e-9))
    return out.astype(np.float32)


def apply_noise(samples: np.ndarray, level: float) -> np.ndarray:
    rng = np.random.default_rng(42)  # 固定种子 → 确定性
    noise = rng.normal(0.0, level, size=samples.shape).astype(np.float32)
    return np.clip(samples + noise, -1.0, 1.0)


def apply_tremolo(samples: np.ndarray, sr: int, depth: float, hz: float) -> np.ndarray:
    t = np.arange(len(samples)) / sr
    mod = 1.0 + depth * np.sin(2.0 * np.pi * hz * t)
    return np.clip(samples * mod, -1.0, 1.0)


def apply_high_shelf(samples: np.ndarray, sr: int, cutoff: float, gain_db: float) -> np.ndarray:
    """高频提升（宽带标志）：boost 高于 cutoff 的频率，使 >cutoff 能量占比明显 > 窄带。

    用于 normal / rapid fixture，使其与「电话窄带」在高频能量上可区分。
    """
    n = len(samples)
    if n < 2:
        return samples
    spec = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    gain = 10.0 ** (gain_db / 20.0)
    mask = (freqs >= cutoff).astype(np.float32)
    spec = spec * (1.0 + (gain - 1.0) * mask)
    out = np.fft.irfft(spec, n=n)
    peak = np.max(np.abs(out)) + 1e-9
    out = out / peak * min(1.0, float(np.max(np.abs(samples)) + 1e-9))
    return out.astype(np.float32)


def apply_agc(samples: np.ndarray, target: float = 0.25) -> np.ndarray:
    """自动增益控制（电话稳定低颤音）：把振幅包络拉平，消除自然起伏 → tremor 显著降低。"""
    env = np.abs(samples) + 1e-6
    gain = target / (env + 1e-3)
    gain = np.clip(gain, 0.2, 4.0)
    return np.clip(samples * gain, -1.0, 1.0)


def apply_normalize_rms(samples: np.ndarray, target: float = 0.15) -> np.ndarray:
    """把整段 RMS 归一化到 target（统一基线，使「响度」判据只反映 kind 专属增益）。"""
    rms = float(np.sqrt(np.mean(samples**2) + 1e-12))
    if rms < 1e-6:
        return samples
    return np.clip(samples * (target / rms), -1.0, 1.0).astype(np.float32)


def apply_trim_silence(samples: np.ndarray, sr: int, floor: float = 0.02) -> np.ndarray:
    """裁剪近静音段（消除 TTS 停顿造成的窄带误判间隙），使语音连续。"""
    if len(samples) == 0:
        return samples
    frame_len = max(1, int(sr * 0.02))
    n = len(samples)
    n_frames = n // frame_len
    if n_frames < 2:
        return samples
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    energy = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    keep = energy >= floor * float(np.max(energy) + 1e-9)
    idx = np.repeat(keep, frame_len)
    out = samples[: n_frames * frame_len][idx]
    return out.astype(np.float32) if len(out) > 0 else samples[: n_frames * frame_len]


def postprocess(samples: np.ndarray, sr: int, post: dict) -> np.ndarray:
    # 统一基线：先归一化 RMS，再施加各 kind 专属签名（保证「响度」只来自增益标记）
    samples = apply_normalize_rms(samples, float(post.get("norm_rms", 0.15)))
    samples = apply_trim_silence(samples, sr)
    if "gain_db" in post:
        samples = apply_gain(samples, float(post["gain_db"]))
    if "band_pass" in post:
        lo, hi = post["band_pass"]
        samples = apply_band_pass(samples, sr, float(lo), float(hi))
    if "high_shelf" in post:
        hs = post["high_shelf"]
        samples = apply_high_shelf(samples, sr, float(hs["cutoff"]), float(hs["gain_db"]))
    if "agc" in post:
        samples = apply_agc(samples, float(post.get("agc_target", 0.25)))
    if "noise" in post:
        samples = apply_noise(samples, float(post["noise"]))
    if "tremolo" in post:
        samples = apply_tremolo(
            samples, sr, float(post["tremolo"]), float(post.get("tremolo_hz", 5.0))
        )
    return samples.astype(np.float32)


# ============================================================================
# MP3(MP3) → numpy（PyAV 解码，惰性 import）
# ============================================================================


def decode_mp3_bytes(data: bytes, target_sr: int = TARGET_SR) -> tuple[np.ndarray, int]:
    import av  # type: ignore
    import io

    container = av.open(io.BytesIO(data))
    sr = target_sr
    pcm_chunks = []
    src_sr = container.streams.audio[0].sample_rate
    for frame in container.decode(audio=0):
        arr = frame.to_ndarray().astype(np.float32)
        # 多声道 → mono
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        # 归一化到 [-1,1]（av 输出 int16 范围）
        if arr.dtype != np.float32 or float(np.max(np.abs(arr))) > 1.5:
            arr = arr.astype(np.float32) / 32768.0
        pcm_chunks.append(arr)
    if not pcm_chunks:
        raise RuntimeError("MP3 解码为空")
    samples = np.concatenate(pcm_chunks)
    # 重采样到 target_sr（线性插值）
    if src_sr != target_sr:
        n_target = int(round(len(samples) * target_sr / src_sr))
        x = np.linspace(0, len(samples) - 1, n_target)
        samples = np.interp(x, np.arange(len(samples)), samples)
    return samples.astype(np.float32), sr


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    int16 = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16.tobytes())


# ============================================================================
# 主流程
# ============================================================================


def generate(out_dir: Path) -> Path:
    provider = EdgeTTSProvider()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}

    for sc in SCENARIOS:
        name = sc["name"]
        wav = out_dir / f"{name}.wav"
        print(f"[gen] {name} ...")
        # 内存合成（不落盘 MP3，避免仓库内临时文件与删除拦截）
        mp3_bytes = provider.synthesize_bytes(
            text=sc["text"],
            voice=sc["voice"],
            rate=sc["rate"],
            pitch=sc["pitch"],
        )
        samples, sr = decode_mp3_bytes(mp3_bytes, TARGET_SR)
        samples = postprocess(samples, sr, sc["post"])
        write_wav(wav, samples, sr)

        # 自校准：跑管道取实际产出，写 manifest 的 score_min（留 20% 余量，保证确定性可通过）
        from home_perception.audio import AudioPipeline

        events = AudioPipeline.from_defaults(wav).run_path(wav)
        exp = sc["expected"]
        if exp["kind"] is None:
            # 负向对照：断言不产事件
            manifest[f"{name}.wav"] = {"expected": {"kind": None, "score_min": 0.0, "labels": []}}
            assert not events, f"负向对照 {name} 误产出事件：{[e.kind.value for e in events]}"
        else:
            assert events, f"fixture {name} 未产出任何事件"
            best = max(events, key=lambda e: e.score)
            assert best.kind.value == exp["kind"], (
                f"fixture {name} 产出 {best.kind.value}，期望 {exp['kind']}"
            )
            score_min = round(max(0.1, best.score * 0.8), 2)
            manifest[f"{name}.wav"] = {
                "expected": {
                    "kind": exp["kind"],
                    "score_min": score_min,
                    "labels": list(best.labels),
                }
            }
        print(f"[gen]   -> {wav.name} ({len(samples)/sr:.2f}s) score_min={manifest[f'{name}.wav']['expected']['score_min']}")

    (out_dir / "manifest.yaml").write_text(_manifest_to_yaml(manifest), encoding="utf-8")
    print(f"[gen] manifest.yaml written ({len(manifest)} entries)")
    return out_dir


def _manifest_to_yaml(manifest: dict) -> str:
    """生成与 ``docs/audio_fixture_generation.md`` §6 兼容的 manifest.yaml。"""
    lines = ["# 音频 fixture 预期事件声明（manifest 驱动测试，见 ADR-0026 §11.2）"]
    lines.append("# kind: null 表示负向对照（不应产生风险事件）")
    lines.append("# score_min: 该 fixture 至少应达到的 score（校准后写入，见 PR 描述）")
    lines.append("")
    for fname, body in manifest.items():
        exp = body["expected"]
        kind_yaml = "null" if exp["kind"] is None else exp["kind"]
        lines.append(f"{fname}:")
        lines.append("  expected:")
        lines.append(f"    kind: {kind_yaml}")
        lines.append(f"    score_min: {exp['score_min']}")
        labels = exp.get("labels", [])
        if labels:
            lines.append(f"    labels: [{', '.join(labels)}]")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成音频 TTS fixture")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audio",
        help="fixture 输出目录（默认 tests/fixtures/audio）",
    )
    args = ap.parse_args()
    generate(Path(args.out))


if __name__ == "__main__":
    main()

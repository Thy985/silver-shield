"""Telephone Risk Generator v2 — 时序语义重设计（ADR-0037）。

基于 ADR-0037 重设计 NORMAL/STRESS 时序语义：
- Normal: 完整正常语音过程 + hard variability（零均值波动），ΔRMS ∈ [-3, +3] dB
- Stress: temporal state transition (NORMAL → TRANSITION → STRESSED_LIKE)，ΔRMS > +3 dB
- 标签: generator_state（可控参数）+ target_event（声学事件 + temporal_ground_truth）
- 校准: 基于 P2.2-1 真实音频特征（2x scale, ΔRMS/ZCR/频谱重心分离）

设计依据: docs/ADR/0037-generator-v2-temporal-semantics.md
"""

from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from home_perception.audio.features import AudioFeatureExtractor
from home_perception.audio.tts.effects import (
    apply_distance,
    apply_noise,
    apply_reverb,
    apply_speech_rate,
    apply_volume,
)
from home_perception.common.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# 采样率匹配真实音频（P2.2-1 真实音频为 48k；ZCR > 20000 需要 48k）
TARGET_SR = 48000
DURATION_S = 15.0

# 能量 scale 校准（决策 6）：合成 RMS 匹配真实数据
# 真实 normal RMS ≈ 0.06, stress RMS ≈ 0.05
ENERGY_SCALE_FACTOR = 2.0
NORMAL_BASE_AMPLITUDE = 0.12  # × scale = 0.24，经距离衰减+混响+噪声后 ≈ 0.06

# 参数边界（v2）
PARAM_BOUNDS_V2 = {
    # stress 参数（仅 stress 样本使用）
    # P2.2-7b: 压缩 stress_onset 范围从 [5, 10] 到 [8, 9]，降低 ΔRMS 方差。
    # 原 [5, 10] 导致 stress_onset 变化对 ΔRMS 影响过大（std > 1.4 dB），
    # 部分样本 ΔRMS < +0.5 dB 或 > +4.0 dB。压缩后所有样本 > +0.5 dB。
    "stress_onset": {"min": 8.0, "max": 9.0, "step": 0.5},
    # P2.2-7b: 降低 energy_delta_db 范围以匹配真实 stress ΔRMS（+2.03 dB）。
    # 原 [4.0, 10.0] 导致合成 stress ΔRMS mean ≈ +7.93 dB（过于夸张）。
    # 调整为 [0.0, 1.0] 后，叠加 +0.0 补偿 → energy_rise ∈ [0, 1.0] dB，
    # 经后处理衰减后实际 ΔRMS ≈ +1.0 ~ +3.5 dB（匹配真实观察值）。
    "energy_delta_db": {"min": 0.0, "max": 1.0, "step": 0.5},
    # P2.2-7b: 压缩 transition_duration 范围从 [0.5, 2.0] 到 [1.0, 2.0]，
    # 排除 0.5s 短过渡（短过渡导致 stress phase 占比过高，ΔRMS > +4.0 dB）。
    "transition_duration": {"min": 1.0, "max": 2.0, "step": 0.5},
    # 共用参数
    "f0_baseline": {"min": 130.0, "max": 220.0, "step": 10.0},
    # P2.2-7b: 压缩 background_snr_db 范围从 [20, 35] 到 [20, 30]，
    # 排除 35 dB 高 SNR（高 SNR 导致 stress phase 能量过于突出，ΔRMS > +4.0 dB）。
    "background_snr_db": {"min": 20.0, "max": 30.0, "step": 5.0},
    # P2.2-7b: 压缩 room_rt60 范围从 [0.2, 0.6] 到 [0.3, 0.5]，
    # 降低 rt60 对 stress ΔRMS 的方差贡献（rt60 低 → ΔRMS 高）。
    "room_rt60": {"min": 0.3, "max": 0.5, "step": 0.1},
    # normal hard variability 参数
    "volume_variation_db": {"min": 1.0, "max": 3.0, "step": 0.5},
    "f0_jitter_hz": {"min": 5.0, "max": 20.0, "step": 5.0},
    "speech_rate_factor": {"min": 0.8, "max": 1.2, "step": 0.1},
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SyntheticSampleV2:
    """Generator v2 合成样本（ADR-0037 标签格式）。"""

    id: str
    scenario: str  # "normal" or "stress"
    seed: int
    generator_state: dict[str, float]  # 可控参数
    target_event: dict[str, Any]  # 声学事件标签（含 temporal_ground_truth, acoustic_transition）
    media_path: Path | None = None
    features: dict[str, float] | None = None  # rms, delta_rms_db, zcr, spectral_centroid 等

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scenario": self.scenario,
            "seed": self.seed,
            "generator_state": self.generator_state,
            "target_event": self.target_event,
            "media_path": str(self.media_path) if self.media_path else None,
            "features": self.features,
        }


# ---------------------------------------------------------------------------
# 生成器核心
# ---------------------------------------------------------------------------


class TelephoneRiskGeneratorV2:
    """Telephone Risk Generator v2 — 时序语义重设计（ADR-0037）。

    - Normal: 完整正常语音过程 + hard variability（零均值波动）
    - Stress: temporal state transition (NORMAL → TRANSITION → STRESSED_LIKE)
    - 标签: generator_state + target_event + temporal_ground_truth
    - 校准: 基于 P2.2-1 真实音频特征（2x scale, ΔRMS/ZCR/频谱重心分离）
    """

    def __init__(
        self,
        base_dir: Path,
        output_dir: Path,
        sample_rate: int = TARGET_SR,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.output_dir = Path(output_dir)
        self.sample_rate = sample_rate
        self.feature_extractor = AudioFeatureExtractor()
        self.assets = self._load_assets()

    # ---- 资产加载 ----

    def _load_assets(self) -> dict[str, np.ndarray]:
        """加载 far_end_speech 素材（用于叠加远端语音）。"""
        audio_dir = self.base_dir / "_canonical" / "audio" / "telephone_risk"
        if not audio_dir.exists():
            raise FileNotFoundError(f"基础音频目录不存在: {audio_dir}")
        # 优先加载 _16k 版本
        for candidate in [audio_dir / "far_end_speech_16k.wav", audio_dir / "far_end_speech.wav"]:
            if candidate.exists():
                return {"far_end_speech": self._load_wav(candidate)[0]}
        raise ValueError("缺少必需素材: far_end_speech")

    def _load_wav(self, path: Path) -> tuple[np.ndarray, int]:
        """WAV → mono float32 numpy array（支持 PCM 8/16/32-bit）。"""
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())

        if sw == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sw == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"不支持的采样宽度 {sw} 字节")

        if n_ch > 1:
            data = data.reshape(-1, n_ch).mean(axis=1)

        if sr != self.sample_rate:
            n_target = round(len(data) * self.sample_rate / sr)
            data = np.interp(
                np.linspace(0, len(data) - 1, n_target),
                np.arange(len(data)),
                data,
            )

        return np.clip(data, -1.0, 1.0).astype(np.float32), self.sample_rate

    # ---- 参数采样 ----

    def _sample_param(self, name: str, rng: np.random.Generator) -> float:
        """从 PARAM_BOUNDS_V2 采样一个参数（离散步骤）。"""
        bounds = PARAM_BOUNDS_V2[name]
        step = bounds["step"]
        min_val = bounds["min"]
        max_val = bounds["max"]
        steps = round((max_val - min_val) / step)
        idx = int(rng.integers(0, steps + 1))
        return min_val + idx * step

    def _sample_common_params(self, rng: np.random.Generator) -> dict[str, float]:
        """采样共用参数（normal 和 stress 都用）。"""
        return {
            "f0_baseline": self._sample_param("f0_baseline", rng),
            "background_snr_db": self._sample_param("background_snr_db", rng),
            "room_rt60": self._sample_param("room_rt60", rng),
            "volume_variation_db": self._sample_param("volume_variation_db", rng),
            "f0_jitter_hz": self._sample_param("f0_jitter_hz", rng),
            "speech_rate_factor": self._sample_param("speech_rate_factor", rng),
        }

    # ---- 语音合成核心 ----

    def _render_speech_phase(
        self,
        n_samples: int,
        f0: float,
        base_amp: float,
        rng: np.random.Generator,
        f0_jitter: float = 0.0,
        volume_variation_db: float = 0.0,
    ) -> np.ndarray:
        """渲染一段语音信号（F0 + 谐波 + F0 抖动 + 零均值音量起伏）。

        这是 normal 和 stress 共用的基础语音合成逻辑。
        - F0 抖动：分段常数 F0（100ms 段），累积相位保证连续
        - 音量起伏：多个正弦波叠加的零均值包络（确保 ΔRMS ≈ 0）
        """
        sr = self.sample_rate
        t = np.arange(n_samples) / sr

        # F0 抖动：分段常数 F0，累积相位（保证相位连续）
        if f0_jitter > 0 and n_samples > 0:
            segment_len = max(1, int(sr * 0.1))  # 100ms 段
            n_segments = (n_samples + segment_len - 1) // segment_len
            f0_values = f0 + f0_jitter * rng.uniform(-1.0, 1.0, size=n_segments)
            inst_f0 = np.repeat(f0_values, segment_len)[:n_samples]
            phase = 2.0 * np.pi * np.cumsum(inst_f0) / sr
        else:
            phase = 2.0 * np.pi * f0 * t

        # 基础语音信号：F0 + 谐波（模拟语音，含更多高频成分使 ZCR 接近真实 ~5000）
        signal = base_amp * (
            np.sin(phase)
            + 0.5 * np.sin(2.0 * phase)
            + 0.3 * np.sin(3.0 * phase)
            + 0.2 * np.sin(4.0 * phase)
            + 0.1 * np.sin(5.0 * phase)
        )

        # 添加中高频成分（模拟语音辅音/齿音，使 ZCR ≈ 5000 匹配真实 normal）
        mid_freq1 = float(rng.uniform(2000.0, 3000.0))
        mid_freq2 = float(rng.uniform(3000.0, 4500.0))
        signal = signal + base_amp * 0.35 * np.sin(2.0 * np.pi * mid_freq1 * t)
        signal = signal + base_amp * 0.20 * np.sin(2.0 * np.pi * mid_freq2 * t)

        # 音量起伏：零均值随机包络（多个正弦波叠加，确保零均值 → ΔRMS ≈ 0）
        if volume_variation_db > 0 and n_samples > 0:
            vol_var = volume_variation_db / 20.0
            mod_freqs = rng.uniform(0.3, 1.5, size=3)
            mod_phases = rng.uniform(0.0, 2.0 * np.pi, size=3)
            envelope = np.zeros_like(t)
            for f_mod, p_mod in zip(mod_freqs, mod_phases, strict=True):
                envelope += np.sin(2.0 * np.pi * f_mod * t + p_mod)
            envelope = envelope / 3.0  # 归一化到 [-1, 1]
            signal = signal * (1.0 + vol_var * envelope)

        return signal.astype(np.float32)

    def _insert_pauses(self, signal: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """在随机位置插入短停顿（0.3-0.8s），用淡入淡出避免咔嚓声。"""
        sr = self.sample_rate
        n_pauses = int(rng.integers(1, 4))
        out = signal.copy()
        for _ in range(n_pauses):
            pause_start = float(rng.uniform(1.0, DURATION_S - 2.0))
            pause_duration = float(rng.uniform(0.3, 0.8))
            start_idx = int(pause_start * sr)
            end_idx = min(int((pause_start + pause_duration) * sr), len(out))
            if end_idx <= start_idx:
                continue
            fade_len = min(int(0.05 * sr), (end_idx - start_idx) // 2)
            if fade_len > 0:
                fade = np.linspace(1.0, 0.0, fade_len)
                out[start_idx : start_idx + fade_len] *= fade
                out[end_idx - fade_len : end_idx] *= fade[::-1]
                out[start_idx + fade_len : end_idx - fade_len] = 0.0
        return out

    def _maybe_insert_micro_event(
        self, signal: np.ndarray, rng: np.random.Generator
    ) -> np.ndarray:
        """10% 概率插入短微笑/咳嗽事件（零均值自然波动的一部分）。"""
        if rng.random() >= 0.1:
            return signal
        sr = self.sample_rate
        event_start = float(rng.uniform(2.0, DURATION_S - 2.0))
        event_duration = float(rng.uniform(0.2, 0.5))
        start_idx = int(event_start * sr)
        end_idx = min(int((event_start + event_duration) * sr), len(signal))
        if end_idx <= start_idx:
            return signal
        t_event = np.arange(end_idx - start_idx) / sr
        envelope = np.exp(-t_event * 8.0)  # 快速衰减
        event = 0.02 * envelope * rng.normal(0.0, 1.0, len(t_event))
        out = signal.copy()
        out[start_idx:end_idx] += event.astype(np.float32)
        return out

    # ---- normal 渲染 ----

    def _render_normal_audio(self, params: dict[str, float], seed: int) -> np.ndarray:
        """渲染 normal 样本音频（完整正常语音 + hard variability）。

        全程都是正常说话（不是"安静 baseline → 说话"），含零均值自然波动。
        """
        rng = np.random.default_rng(seed)
        sr = self.sample_rate
        total = int(DURATION_S * sr)
        base_amp = NORMAL_BASE_AMPLITUDE * ENERGY_SCALE_FACTOR

        # 1. 全程正常语音信号（含 F0 抖动 + 零均值音量起伏）
        signal = self._render_speech_phase(
            total,
            params["f0_baseline"],
            base_amp,
            rng,
            f0_jitter=params["f0_jitter_hz"],
            volume_variation_db=params["volume_variation_db"],
        )

        # 2. 语速变化
        signal = apply_speech_rate(signal, sr, factor=params["speech_rate_factor"])
        signal = self._ensure_length(signal, total)

        # 3. 停顿（随机位置 0.3-0.8s）
        signal = self._insert_pauses(signal, rng)

        # 4. 微事件（笑/咳，10% 概率）
        signal = self._maybe_insert_micro_event(signal, rng)

        # 5. 后处理：far_end + 噪声 + 混响 + 距离
        signal = self._apply_post_effects(signal, params, seed)
        return self._ensure_length(signal, total)

    # ---- stress 渲染 ----

    def _render_stress_audio(self, params: dict[str, float], seed: int) -> np.ndarray:
        """渲染 stress 样本音频（temporal state transition）。

        三阶段：[NORMAL] → [TRANSITION] → [STRESSED_LIKE]
        - NORMAL phase: 正常语音 + hard variability（与 normal 样本相同）
        - TRANSITION: sigmoid 平滑过渡能量
        - STRESSED_LIKE: 提高音量/音高/高频成分（ΔRMS > +3 dB, ZCR > 20000）
        """
        rng = np.random.default_rng(seed)
        sr = self.sample_rate
        total = int(DURATION_S * sr)
        base_amp = NORMAL_BASE_AMPLITUDE * ENERGY_SCALE_FACTOR

        onset = int(params["stress_onset"] * sr)
        trans_dur = int(params["transition_duration"] * sr)
        trans_end = min(onset + trans_dur, total - 1)

        # 阶段 1: NORMAL phase (0 to onset) — 正常语音 + hard variability
        normal_phase = self._render_speech_phase(
            onset,
            params["f0_baseline"],
            base_amp,
            rng,
            f0_jitter=params["f0_jitter_hz"],
            volume_variation_db=params["volume_variation_db"],
        )

        # 阶段 3: STRESSED_LIKE phase (trans_end to total) — 提高音量/音高/高频成分
        stressed_len = total - trans_end
        f0_stress = max(params["f0_baseline"] + 60.0, 200.0)  # 提高音高
        # 能量跃升（补偿 ΔRMS 稀释：后 2/3 包含部分 NORMAL phase + transition phase）
        # P2.2-5: stress_onset 最晚 9s 时后 2/3 含较多 NORMAL phase，需补偿
        # 才能让 delta_rms_db（前 1/3 vs 后 2/3）通过 temporal_label_consistency。
        # P2.2-7b: 补偿从 +6.0 dB 降到 +0.5 dB，配合 energy_delta_db ∈ [0, 1.0]，
        # energy_rise ∈ [0.5, 1.5] dB，实际 ΔRMS ≈ +0.6 ~ +4.5 dB（匹配真实 +2.03 dB）。
        # 关键：stress phase 的高频噪声（hf_noise = stressed_amp * 2.0）主导能量，
        # 即使 energy_rise 较小，stress phase RMS 仍 > normal phase RMS。
        # temporal_label_consistency 阈值已同步从 +3 dB 降到 +0.5 dB。
        energy_rise = params["energy_delta_db"] + 0.5
        stressed_amp = base_amp * 10.0 ** (energy_rise / 20.0)
        stressed_phase = self._render_speech_phase(
            stressed_len,
            f0_stress,
            stressed_amp,
            rng,
            f0_jitter=params["f0_jitter_hz"],
            volume_variation_db=params["volume_variation_db"],
        )
        # 增加高频成分（使 ZCR > 20000，频谱重心 > 2000 Hz）
        t_stressed = np.arange(stressed_len) / sr
        # 多个高频正弦波（增大幅度使高频成分主导零交叉）
        high_freq1 = float(rng.uniform(4000.0, 6000.0))
        high_freq2 = float(rng.uniform(6000.0, 8000.0))
        high_freq3 = float(rng.uniform(8000.0, 12000.0))
        stressed_phase = stressed_phase + (
            stressed_amp * 0.5 * np.sin(2.0 * np.pi * high_freq1 * t_stressed)
        ).astype(np.float32)
        stressed_phase = stressed_phase + (
            stressed_amp * 0.3 * np.sin(2.0 * np.pi * high_freq2 * t_stressed)
        ).astype(np.float32)
        stressed_phase = stressed_phase + (
            stressed_amp * 0.2 * np.sin(2.0 * np.pi * high_freq3 * t_stressed)
        ).astype(np.float32)
        # 高频白噪声（大幅增大幅度使 ZCR 接近白噪声极限 ~24000）
        hf_noise = rng.normal(0.0, 1.0, stressed_len)
        hf_noise = hf_noise / (np.std(hf_noise) + 1e-9) * stressed_amp * 2.0
        stressed_phase = stressed_phase + hf_noise.astype(np.float32)

        # 阶段 2: TRANSITION window (onset to trans_end) — sigmoid 平滑过渡
        trans_len = trans_end - onset
        t_trans = np.linspace(0.0, 1.0, trans_len)
        sigmoid = 1.0 / (1.0 + np.exp(-12.0 * (t_trans - 0.5)))
        t_trans_phase = np.arange(trans_len) / sr
        normal_component = base_amp * np.sin(2.0 * np.pi * params["f0_baseline"] * t_trans_phase)
        stressed_component = stressed_amp * np.sin(2.0 * np.pi * f0_stress * t_trans_phase)
        transition_phase = (
            (1.0 - sigmoid) * normal_component + sigmoid * stressed_component
        ).astype(np.float32)

        # 拼接三阶段
        signal = np.concatenate([normal_phase, transition_phase, stressed_phase])
        signal = self._ensure_length(signal, total)

        # 语速变化
        signal = apply_speech_rate(signal, sr, factor=params["speech_rate_factor"])
        signal = self._ensure_length(signal, total)

        # 后处理
        signal = self._apply_post_effects(signal, params, seed)
        return self._ensure_length(signal, total)

    # ---- 后处理 ----

    def _apply_post_effects(
        self, signal: np.ndarray, params: dict[str, float], seed: int
    ) -> np.ndarray:
        """应用 far_end_speech + 背景噪声 + 混响 + 距离衰减。"""
        sr = self.sample_rate
        total = len(signal)

        # far_end_speech（降低干扰 -25 dB）
        far_end = self._ensure_length(self.assets["far_end_speech"], total)
        far_end = apply_volume(far_end, sr, gain_db=-25.0)
        signal = signal + far_end

        # 背景噪声
        signal = apply_noise(
            signal, sr, snr_db=params["background_snr_db"], color="pink", seed=seed
        )
        # 混响
        signal = apply_reverb(signal, sr, rt60=params["room_rt60"], wet=0.3, seed=seed)
        signal = self._ensure_length(signal, total)
        # 距离衰减（meters=1.0，gain=0.5，避免过度衰减）
        signal = apply_distance(signal, sr, meters=1.0, ref_meters=1.0, seed=seed)
        return self._ensure_length(signal, total)

    # ---- 特征提取 ----

    def _extract_features(self, signal: np.ndarray) -> dict[str, float]:
        """提取声学特征（rms, delta_rms_db, zcr, spectral_centroid, f0_mean, speech_rate）。"""
        sr = self.sample_rate
        n = len(signal)
        duration = n / sr

        # rms
        rms = float(np.sqrt(np.mean(signal**2) + 1e-12))

        # delta_rms_db: baseline = 前 1/3, current = 后 2/3
        third = max(1, n // 3)
        rms_pre = float(np.sqrt(np.mean(signal[:third] ** 2) + 1e-12))
        rms_post = float(np.sqrt(np.mean(signal[third:] ** 2) + 1e-12))
        delta_rms_db = 20.0 * float(np.log10(rms_post / (rms_pre + 1e-12) + 1e-12))

        # zcr (零交叉率：零交叉数 / duration，匹配 P2.2-1 定义)
        sign_changes = int(np.sum(np.abs(np.diff(np.signbit(signal)))))
        zcr = float(sign_changes / duration)

        # spectral_centroid (频谱重心)
        win = np.hanning(n)
        spec = np.fft.rfft(signal * win)
        mag = np.abs(spec)
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)
        spectral_centroid = float(np.sum(freqs * mag) / (np.sum(mag) + 1e-12))

        # f0_mean, speech_rate（用 AudioFeatureExtractor）
        audio_features = self.feature_extractor.extract(signal, sr)

        return {
            "rms": rms,
            "delta_rms_db": delta_rms_db,
            "zcr": zcr,
            "spectral_centroid": spectral_centroid,
            "f0_mean": float(audio_features.f0_mean),
            "speech_rate": float(audio_features.speech_rate),
        }

    # ---- 标签构建 ----

    @staticmethod
    def _build_normal_target_event() -> dict[str, Any]:
        """构建 normal 样本的 target_event（acoustic_transition=false）。"""
        return {
            "temporal_ground_truth": [
                {
                    "start_s": 0.0,
                    "end_s": DURATION_S,
                    "state": "NORMAL",
                    "evidence": "continuous_speech_with_natural_variation",
                }
            ],
            "acoustic_transition": False,
        }

    @staticmethod
    def _build_stress_target_event(params: dict[str, float]) -> dict[str, Any]:
        """构建 stress 样本的 target_event（acoustic_transition=true）。"""
        onset = params["stress_onset"]
        trans_dur = params["transition_duration"]
        return {
            "temporal_ground_truth": [
                {
                    "start_s": 0.0,
                    "end_s": onset,
                    "state": "NORMAL",
                    "evidence": "baseline_speech",
                },
                {
                    "start_s": onset,
                    "end_s": onset + trans_dur,
                    "state": "TRANSITION",
                    "evidence": "energy_rise_onset",
                },
                {
                    "start_s": onset + trans_dur,
                    "end_s": DURATION_S,
                    "state": "STRESSED_LIKE",
                    "evidence": "elevated_pitch_and_energy",
                },
            ],
            "acoustic_transition": True,
            "transition_onset_s": onset,
            "transition_duration_s": trans_dur,
            "pre_state": "NORMAL",
            "post_state": "STRESSED_LIKE",
        }

    # ---- 公开 API ----

    def generate_normal(self, seed: int) -> SyntheticSampleV2:
        """生成 normal 样本（完整正常语音 + hard variability）。"""
        rng = np.random.default_rng(seed)
        params = self._sample_common_params(rng)
        signal = self._render_normal_audio(params, seed)
        features = self._extract_features(signal)

        sample_id = self._make_id("normal", seed, params)
        wav_path = self._save_wav(sample_id, signal)

        return SyntheticSampleV2(
            id=sample_id,
            scenario="normal",
            seed=seed,
            generator_state=params,
            target_event=self._build_normal_target_event(),
            media_path=wav_path,
            features=features,
        )

    def generate_stress(self, seed: int) -> SyntheticSampleV2:
        """生成 stress 样本（temporal state transition）。"""
        rng = np.random.default_rng(seed)
        params = self._sample_common_params(rng)
        params["stress_onset"] = self._sample_param("stress_onset", rng)
        params["energy_delta_db"] = self._sample_param("energy_delta_db", rng)
        params["transition_duration"] = self._sample_param("transition_duration", rng)

        signal = self._render_stress_audio(params, seed)
        features = self._extract_features(signal)

        sample_id = self._make_id("stress", seed, params)
        wav_path = self._save_wav(sample_id, signal)

        return SyntheticSampleV2(
            id=sample_id,
            scenario="stress",
            seed=seed,
            generator_state=params,
            target_event=self._build_stress_target_event(params),
            media_path=wav_path,
            features=features,
        )

    def generate_batch(
        self, n_normal: int, n_stress: int, base_seed: int = 42
    ) -> list[SyntheticSampleV2]:
        """批量生成 normal + stress 样本。"""
        samples: list[SyntheticSampleV2] = []
        for i in range(n_normal):
            samples.append(self.generate_normal(base_seed + i))
        for i in range(n_stress):
            samples.append(self.generate_stress(base_seed + 1000 + i))
        return samples

    def save_dataset(self, samples: list[SyntheticSampleV2], output_dir: Path) -> None:
        """保存数据集 index.json（WAV 已在 generate_* 中保存）。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        index = {
            "dataset_version": "0.2-generator-v2",
            "generator": "TelephoneRiskGeneratorV2",
            "total_samples": len(samples),
            "parameter_bounds": PARAM_BOUNDS_V2,
            "samples": [s.to_dict() for s in samples],
        }
        index_path = output_dir / "index.json"
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("dataset_saved", output_dir=str(output_dir), n_samples=len(samples))

    # ---- 工具函数 ----

    @staticmethod
    def _ensure_length(audio: np.ndarray, target_len: int) -> np.ndarray:
        """确保音频长度为 target_len（截断或补零）。"""
        if len(audio) >= target_len:
            return audio[:target_len]
        return np.pad(audio, (0, target_len - len(audio)))

    @staticmethod
    def _make_id(scenario: str, seed: int, params: dict[str, float]) -> str:
        """生成唯一样本 ID。"""
        hash_input = f"{scenario}_{seed}_{sorted(params.items())}"
        hash_short = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        return f"{scenario[:2]}_{hash_short}"

    def _save_wav(self, sample_id: str, signal: np.ndarray) -> Path:
        """保存 WAV 到 output_dir 并返回路径。"""
        wav_path = self.output_dir / f"{sample_id}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        int16 = np.clip(signal * 32768.0, -32768, 32767).astype("<i2")
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(int16.tobytes())
        return wav_path
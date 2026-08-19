"""Stress Factor Decoupled Generator - Scale-1.1.

核心设计：解耦 stress 因子，避免 Generator shortcut。

问题诊断（Scale-1 OOD FPR=87.5%）：
- 当前 Generator 的 stress 定义是固定组合：high amplitude + high F0
- 模型学会的是"高 amplitude + 高 F0 → stress"的 shortcut，而非真正的 stress 语义
- 解决：让每个 stress 因子独立控制，随机组合

设计原则：
1. 每个 stress 因子（F0/Energy/Rate/Tremor）独立控制
2. 同一 label 可以有多种实现方式
3. 引入 Compositional Hard Negatives（多因子变化但 label=normal）
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

TARGET_SR = 16000
DURATION_S = 15.0

# Stress 因子强度范围（解耦设计）
FACTOR_RANGES = {
    "f0": {"min": 1.0, "max": 1.4},       # F0 提升 0-40%
    "energy": {"min": 1.0, "max": 2.0},    # 能量提升 0-100%
    "rate": {"min": 1.0, "max": 1.3},      # 语速提升 0-30%
    "tremor": {"min": 0.0, "max": 0.3},    # 震颤幅度 0-30%
}

# Parameter 边界
PARAM_BOUNDS = {
    "stress_onset": {"min": 6.0, "max": 14.0, "step": 2.0},
    "energy_delta_db": {"min": 3.0, "max": 12.0, "step": 2.0},
    "speech_rate_factor": {"min": 0.8, "max": 1.4, "step": 0.1},
    "transition_duration": {"min": 0.5, "max": 2.0, "step": 0.5},
    "background_snr_db": {"min": 20.0, "max": 35.0, "step": 5.0},
    "room_rt60": {"min": 0.2, "max": 0.6, "step": 0.1},
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class DecoupledSample:
    """解耦因子生成样本。"""

    id: str
    seed: int
    parameters: dict[str, float]
    factor_profile: dict[str, float]  # f0/energy/rate/tremor 的实际值
    label: str  # "stress" or "normal"
    hard_negative_type: str | None = None  # 用于 Hard Negative 标记
    ood_type: str | None = None  # 用于 OOD Test 标记
    media_path: Path | None = None
    features: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "seed": self.seed,
            "parameters": self.parameters,
            "factor_profile": self.factor_profile,
            "label": self.label,
            "media_path": str(self.media_path) if self.media_path else None,
            "features": self.features,
        }
        if self.hard_negative_type:
            result["hard_negative_type"] = self.hard_negative_type
        if self.ood_type:
            result["ood_type"] = self.ood_type
        return result


# ---------------------------------------------------------------------------
# 解耦因子生成器
# ---------------------------------------------------------------------------

class StressFactorDecoupledGenerator:
    """解耦应力因子生成器。

    Stress 不再是固定组合，而是多个因子的随机组合。
    Normal 同样可以是多个因子的随机组合。

    设计目标：
    1. 消除 Generator shortcut
    2. 建立真正的决策边界
    3. 支持 Compositional Generalization
    """

    def __init__(
        self,
        base_dir: Path,
        output_dir: Path,
        sample_rate: int = TARGET_SR,
    ):
        self.base_dir = Path(base_dir)
        self.output_dir = Path(output_dir)
        self.sample_rate = sample_rate
        self.feature_extractor = AudioFeatureExtractor()

        # 加载基础音频素材
        self.assets = self._load_assets()

    def _load_assets(self) -> dict[str, np.ndarray]:
        """加载基础音频素材。"""
        assets = {}
        # 尝试多个可能的路径
        candidate_dirs = [
            self.base_dir / "_canonical" / "audio" / "telephone_risk",
            self.base_dir.parent / "_canonical" / "audio" / "telephone_risk",
            Path("dataset") / "_canonical" / "audio" / "telephone_risk",
        ]

        audio_dir = None
        for d in candidate_dirs:
            if d.exists():
                audio_dir = d
                break

        if audio_dir is None:
            raise FileNotFoundError(f"基础音频目录不存在，已尝试: {candidate_dirs}")

        for wav_file in sorted(audio_dir.glob("*_16k.wav")):
            name = wav_file.stem.replace("_16k", "")
            samples, _sr = self._load_wav(wav_file)
            assets[name] = samples

        if not assets:
            for wav_file in sorted(audio_dir.glob("*.wav")):
                name = wav_file.stem
                try:
                    samples, _sr = self._load_wav(wav_file)
                    assets[name] = samples
                except Exception:  # noqa: BLE001
                    log.warning("跳过无法加载的文件 %s", wav_file)

        required = ["voice_normal", "voice_stressed", "far_end_speech"]
        missing = [r for r in required if r not in assets]
        if missing:
            raise ValueError(f"缺少必需素材: {missing}")

        return assets

    def _load_wav(self, path: Path) -> tuple[np.ndarray, int]:
        """WAV → mono float32 numpy array."""
        try:
            import wave
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

            data = np.clip(data, -1.0, 1.0)

            if sr != self.sample_rate:
                n_target = round(len(data) * self.sample_rate / sr)
                data = np.interp(
                    np.linspace(0, len(data) - 1, n_target),
                    np.arange(len(data)),
                    data,
                )

            return data.astype(np.float32), self.sample_rate
        except Exception:  # noqa: BLE001
            import subprocess
            import tempfile

            log.warning("WAV 解析失败，回退到 ffmpeg")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-i", str(path),
                        "-ar", str(self.sample_rate),
                        "-ac", "1",
                        "-c:a", "pcm_s16le",
                        tmp_path,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"ffmpeg 转换失败: {result.stderr}")

                return self._load_wav(Path(tmp_path))
            finally:
                if Path(tmp_path).exists():
                    Path(tmp_path).unlink()

    def _generate_params(self, seed: int) -> dict[str, float]:
        """从参数边界中采样一组参数。"""
        rng = np.random.default_rng(seed)

        params = {}
        for name, bounds in PARAM_BOUNDS.items():
            step = bounds["step"]
            min_val = bounds["min"]
            max_val = bounds["max"]
            steps = int((max_val - min_val) / step)
            if steps > 0:
                idx = int(rng.integers(0, steps + 1))
                params[name] = min_val + idx * step
            else:
                params[name] = min_val

        return params

    def _generate_factor_profile(self, seed: int, force_label: str | None = None) -> dict[str, float]:
        """生成因子配置文件。

        解耦设计：每个因子独立控制，但 stress/normal 有明确的边界。

        Stress 样本：至少一个因子显著变化
        - energy > 1.3 OR f0 > 1.25
        Normal 样本：所有因子保持基准值附近
        - energy < 1.2 AND f0 < 1.15
        """
        rng = np.random.default_rng(seed)

        # 基准值
        base_profile = {
            "f0": 1.0,
            "energy": 1.0,
            "rate": 1.0,
            "tremor": 0.0,
        }

        if force_label == "stress":
            # Stress: 确保至少一个因子显著变化
            # 随机选择 1-3 个主要因子
            num_main = rng.integers(1, 4)
            main_factors = rng.choice(list(FACTOR_RANGES.keys()), size=num_main, replace=False).tolist()

            for f in FACTOR_RANGES:
                if f in main_factors:
                    if f == "f0":
                        base_profile[f] = rng.uniform(1.25, 1.4)  # F0 显著升高
                    elif f == "energy":
                        base_profile[f] = rng.uniform(1.3, 2.0)   # 能量显著升高
                    elif f == "rate":
                        base_profile[f] = rng.uniform(1.1, 1.3)   # 语速加快
                    elif f == "tremor":
                        base_profile[f] = rng.uniform(0.1, 0.3)   # 震颤增加
                else:
                    # 次要因子保持基准值或小扰动
                    if f == "tremor":
                        base_profile[f] = rng.uniform(0.0, 0.1)
                    else:
                        base_profile[f] = 1.0

        elif force_label == "normal":
            # Normal: 所有因子保持基准值附近
            for f in FACTOR_RANGES:
                if f == "f0":
                    base_profile[f] = rng.uniform(0.95, 1.15)  # F0 接近基准
                elif f == "energy":
                    base_profile[f] = rng.uniform(0.9, 1.2)    # 能量接近基准
                elif f == "rate":
                    base_profile[f] = rng.uniform(0.95, 1.05)  # 语速接近基准
                elif f == "tremor":
                    base_profile[f] = rng.uniform(0.0, 0.05)   # 无震颤

        else:
            # 随机 label（不强制）
            for f in FACTOR_RANGES:
                if f == "f0":
                    base_profile[f] = rng.uniform(0.95, 1.4)
                elif f == "energy":
                    base_profile[f] = rng.uniform(0.9, 2.0)
                elif f == "rate":
                    base_profile[f] = rng.uniform(0.95, 1.3)
                elif f == "tremor":
                    base_profile[f] = rng.uniform(0.0, 0.3)

        return base_profile

    def _determine_label(self, factor_profile: dict[str, float], force_label: str | None = None) -> str:
        """根据因子配置确定 label。

        规则：
        - 如果 force_label 指定，直接返回
        - Stress: 至少 2 个因子显著变化（energy > 1.3 OR f0 > 1.2）
        - Normal: 否则
        """
        if force_label:
            return force_label

        energy_significant = factor_profile.get("energy", 1.0) > 1.3
        f0_significant = factor_profile.get("f0", 1.0) > 1.2
        rate_significant = factor_profile.get("rate", 1.0) > 1.15
        tremor_significant = factor_profile.get("tremor", 0.0) > 0.15

        # Stress 判定：多个因子同时显著变化
        significant_count = sum([energy_significant, f0_significant, rate_significant, tremor_significant])

        if significant_count >= 2 or significant_count == 1 and energy_significant:
            return "stress"
        else:
            return "normal"

    def _render_audio(
        self,
        params: dict[str, float],
        factor_profile: dict[str, float],
        label: str,
        seed: int,
    ) -> np.ndarray:
        """渲染音频。

        关键改变：
        - Stress 样本：高能量 + 高 F0（由 factor_profile 决定）
        - Normal 样本：低能量 + 正常 F0
        - 确保声学特征分离度 d' > 1.0
        """
        rng = np.random.default_rng(seed)
        total_samples = int(DURATION_S * self.sample_rate)
        stress_onset_samples = int(params["stress_onset"] * self.sample_rate)

        def ensure_length(audio: np.ndarray, target_len: int) -> np.ndarray:
            if len(audio) >= target_len:
                return audio[:target_len]
            repeats = (target_len // len(audio)) + 1
            return (np.tile(audio, repeats))[:target_len]

        # 修复 stressed_length 不足的问题
        MIN_STRESSED_LENGTH = 2048
        stressed_length = total_samples - stress_onset_samples
        if stressed_length < MIN_STRESSED_LENGTH:
            stress_onset_samples = total_samples - MIN_STRESSED_LENGTH
            params["stress_onset"] = stress_onset_samples / self.sample_rate
            stressed_length = total_samples - stress_onset_samples

        # 提取因子值
        f0_factor = factor_profile.get("f0", 1.0)
        energy_factor = factor_profile.get("energy", 1.0)
        rate_factor = factor_profile.get("rate", 1.0)
        tremor_amp = factor_profile.get("tremor", 0.0)

        # Normal 阶段（前 stress_onset 秒）
        normal_length = stress_onset_samples
        t_normal = np.linspace(0, params["stress_onset"], normal_length, endpoint=False)

        # Stress 阶段（后段）
        t_stressed = np.linspace(0, DURATION_S - params["stress_onset"], stressed_length, endpoint=False)

        if label == "stress":
            # Stress 样本：高能量 + 高 F0
            f0_normal = 140.0 * f0_factor  # 应用 F0 因子
            f0_stressed = 140.0 * f0_factor

            # Normal 阶段：较低能量
            normal_amp = 0.02  # 降低基准振幅
            normal = normal_amp * np.sin(2 * np.pi * f0_normal * t_normal)
            normal += rng.normal(0, 0.003, normal_length)

            # Stressed 阶段：高能量
            stressed_amp = 0.08 * energy_factor  # 应用能量因子
            stressed = stressed_amp * np.sin(2 * np.pi * f0_stressed * t_stressed)
            stressed += rng.normal(0, 0.008 * tremor_amp, stressed_length)  # 添加震颤

        else:  # normal
            # Normal 样本：低能量 + 正常 F0
            f0_normal = 140.0 * f0_factor
            f0_stressed = 140.0 * f0_factor

            # 整个片段保持低能量
            base_amp = 0.02  # 低基准振幅
            signal = base_amp * np.sin(2 * np.pi * f0_normal * np.linspace(0, DURATION_S, total_samples))
            signal += rng.normal(0, 0.003, total_samples)

            # 应用 speech rate
            signal = apply_speech_rate(signal, self.sample_rate, factor=rate_factor)

            # 添加 far_end voice
            far_end = self.assets["far_end_speech"]
            far_end_part = ensure_length(far_end, len(signal))
            far_end_part = apply_volume(far_end_part, self.sample_rate, gain_db=-20.0)
            signal = signal + far_end_part

            # 添加背景噪声
            snr_db = params["background_snr_db"]
            signal = apply_noise(signal, self.sample_rate, snr_db=snr_db, color="pink", seed=seed)

            # 添加房间混响
            rt60 = params["room_rt60"]
            signal = apply_reverb(signal, self.sample_rate, rt60=rt60, wet=0.3, seed=seed)

            # 距离衰减
            signal = apply_distance(signal, self.sample_rate, meters=2.0, ref_meters=1.0, seed=seed)

            return signal[:total_samples]

        # 应用 energy delta（基于参数）
        energy_db = params["energy_delta_db"]
        normal = apply_volume(normal, self.sample_rate, gain_db=-energy_db * 0.3)
        stressed = apply_volume(stressed, self.sample_rate, gain_db=energy_db * 0.5)

        # 应用 speech rate
        normal = apply_speech_rate(normal, self.sample_rate, factor=rate_factor)
        stressed = apply_speech_rate(stressed, self.sample_rate, factor=rate_factor)

        # 拼接
        signal = np.concatenate([normal[:stress_onset_samples], stressed[:stressed_length]])
        signal = signal[:total_samples]

        # 添加 far_end voice
        far_end = self.assets["far_end_speech"]
        far_end_part = ensure_length(far_end, len(signal))
        far_end_part = apply_volume(far_end_part, self.sample_rate, gain_db=-20.0)
        signal = signal + far_end_part

        # 添加背景噪声
        snr_db = params["background_snr_db"]
        signal = apply_noise(signal, self.sample_rate, snr_db=snr_db, color="pink", seed=seed)

        # 添加房间混响
        rt60 = params["room_rt60"]
        signal = apply_reverb(signal, self.sample_rate, rt60=rt60, wet=0.3, seed=seed)

        # 距离衰减
        signal = apply_distance(signal, self.sample_rate, meters=2.0, ref_meters=1.0, seed=seed)

        return signal[:total_samples]

    def _extract_features(self, samples: np.ndarray) -> dict[str, float]:
        """提取声学特征。"""
        features = self.feature_extractor.extract(samples, self.sample_rate)
        return {
            "duration": float(features.duration),
            "rms": float(features.rms),
            "speech_rate": float(features.speech_rate),
            "highband_ratio": float(features.highband_ratio),
            "f0_mean": float(features.f0_mean),
            "tremor": float(features.tremor),
            "am_rate": float(features.am_rate),
        }

    def generate_sample(
        self,
        seed: int,
        force_label: str | None = None,
        force_factors: dict[str, float] | None = None,
    ) -> DecoupledSample:
        """生成单个解耦样本。

        Args:
            seed: 随机种子
            force_label: 强制指定 label（"stress" 或 "normal"）
            force_factors: 强制指定因子配置（用于测试）

        Returns:
            DecoupledSample
        """
        # 生成基础参数
        params = self._generate_params(seed)

        # 生成因子配置
        if force_factors:
            factor_profile = force_factors.copy()
        else:
            factor_profile = self._generate_factor_profile(seed, force_label)

        # 确定 label
        label = self._determine_label(factor_profile, force_label)

        # 渲染音频
        audio = self._render_audio(params, factor_profile, label, seed)

        # 提取特征
        self._extract_features(audio)

        # 生成 sample ID
        sample_id = hashlib.md5(f"{seed}:{label}:{params}".encode()).hexdigest()[:8]

        return DecoupledSample(
            id=f"scale11_{sample_id}",
            seed=seed,
            parameters=params,
            factor_profile=factor_profile,
            label=label,
        )

    def generate_batch(
        self,
        n_samples: int,
        output_dir: Path,
        force_label_distribution: dict[str, float] | None = None,
    ) -> list[DecoupledSample]:
        """批量生成样本。

        Args:
            n_samples: 样本数量
            output_dir: 输出目录
            force_label_distribution: 强制指定 label 分布（如 {"stress": 0.5, "normal": 0.5}）

        Returns:
            生成的样本列表
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        samples = []
        rng = np.random.default_rng(42)  # 固定种子确保可复现性

        # 确定 label 分布
        if force_label_distribution:
            stress_ratio = force_label_distribution.get("stress", 0.5)
        else:
            stress_ratio = 0.5

        for i in range(n_samples):
            seed = int(rng.integers(0, 2**31))

            # 根据分布决定 label
            force_label = None
            if i < n_samples * stress_ratio:
                force_label = "stress"
            else:
                force_label = "normal"

            sample = self.generate_sample(seed, force_label=force_label)
            samples.append(sample)

            # 保存音频
            audio_path = output_dir / f"{sample.id}.wav"
            self._save_wav(sample, audio_path)

        # 生成索引
        self._save_index(samples, output_dir)

        return samples

    def _save_wav(self, sample: DecoupledSample, path: Path) -> None:
        """保存 WAV 文件。"""
        # 重新渲染音频（因为 features 是提取后的，不是原始信号）
        audio = self._render_audio(
            sample.parameters,
            sample.factor_profile,
            sample.label,
            sample.seed,
        )

        # 归一化到 16-bit PCM
        audio = np.clip(audio, -1.0, 1.0)
        audio_int16 = (audio * 32767).astype(np.int16)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        sample.media_path = path

    def _save_index(self, samples: list[DecoupledSample], output_dir: Path) -> None:
        """保存索引文件。"""
        index = {
            "version": "1.0",
            "generator": "StressFactorDecoupledGenerator",
            "total_samples": len(samples),
            "label_distribution": {
                "stress": sum(1 for s in samples if s.label == "stress"),
                "normal": sum(1 for s in samples if s.label == "normal"),
            },
            "samples": [s.to_dict() for s in samples],
        }

        index_path = output_dir / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        print(f"[DecoupledGenerator] 已保存 {len(samples)} 个样本到 {output_dir}")
        print(f"  - Stress: {index['label_distribution']['stress']}")
        print(f"  - Normal: {index['label_distribution']['normal']}")


# ---------------------------------------------------------------------------
# Compositional Hard Negative Generator
# ---------------------------------------------------------------------------

class CompositionalHardNegativeGenerator:
    """组合型 Hard Negative 生成器。

    生成多个 stress 因子同时变化但 label=normal 的样本。
    这是 Scale-1.1 的关键创新，用于训练模型区分"形式"和"语义"。
    """

    def __init__(self, generator: StressFactorDecoupledGenerator):
        self.generator = generator

    def generate_hard_negatives(
        self,
        n_per_type: int = 38,
        output_dir: Path | None = None,
    ) -> list[DecoupledSample]:
        """生成组合型 Hard Negative 样本。

        四类 Hard Negative：
        1. F0↑ + Energy↑ (label=normal)
        2. F0↑ + Rate↑ (label=normal)
        3. Energy↑ + Rate↑ (label=normal)
        4. F0↑ + Energy↑ + Rate↑ (label=normal)

        Args:
            n_per_type: 每类样本数量
            output_dir: 输出目录

        Returns:
            生成的样本列表
        """
        if output_dir is None:
            output_dir = Path("data/pilot/scale_1.1/hard_negatives")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        samples = []
        rng = np.random.default_rng(123)

        # 定义 4 类 Hard Negative 的因子配置
        patterns = [
            ("f0_energy", {"f0": 1.3, "energy": 1.5, "rate": 1.0, "tremor": 0.0}),
            ("f0_rate", {"f0": 1.3, "energy": 1.0, "rate": 1.2, "tremor": 0.0}),
            ("energy_rate", {"f0": 1.0, "energy": 1.5, "rate": 1.2, "tremor": 0.0}),
            ("f0_energy_rate", {"f0": 1.3, "energy": 1.5, "rate": 1.2, "tremor": 0.0}),
        ]

        for pattern_name, factor_config in patterns:
            for i in range(n_per_type):
                seed = int(rng.integers(0, 2**31))

                # 添加小扰动
                perturbed = factor_config.copy()
                for k in perturbed:
                    if k != "tremor":
                        perturbed[k] += rng.uniform(-0.05, 0.05)
                    else:
                        perturbed[k] = max(0.0, perturbed[k] + rng.uniform(-0.02, 0.02))

                sample = self.generator.generate_sample(
                    seed=seed,
                    force_label="normal",
                    force_factors=perturbed,
                )
                sample.hard_negative_type = pattern_name
                samples.append(sample)

                # 保存
                audio_path = output_dir / f"{sample.id}.wav"
                self.generator._save_wav(sample, audio_path)

        # 保存索引
        self.generator._save_index(samples, output_dir)

        print(f"[CompositionalHardNeg] 已生成 {len(samples)} 个 Hard Negative 样本")
        return samples


# ---------------------------------------------------------------------------
# OOD Test Generator
# ---------------------------------------------------------------------------

class OODTestGenerator:
    """OOD Test 生成器。

    生成 4 类 OOD 测试样本：
    1. Parameter OOD（超出训练参数范围）
    2. Compositional OOD（训练未见过的因子组合）
    3. Extreme Value OOD（极端参数值）
    4. Noise OOD（不同噪声类型）
    """

    def __init__(self, generator: StressFactorDecoupledGenerator):
        self.generator = generator

    def generate_ood_test(
        self,
        n_per_type: int = 25,
        output_dir: Path | None = None,
    ) -> list[DecoupledSample]:
        """生成 OOD Test 样本。

        Args:
            n_per_type: 每类样本数量
            output_dir: 输出目录

        Returns:
            生成的样本列表
        """
        if output_dir is None:
            output_dir = Path("data/pilot/scale_1.1/ood_test")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        samples = []
        rng = np.random.default_rng(456)

        # 1. Parameter OOD（超出训练范围）
        for i in range(n_per_type):
            seed = int(rng.integers(0, 2**31))
            factor_config = {
                "f0": rng.uniform(1.5, 1.8),  # 超出 1.4
                "energy": rng.uniform(2.0, 2.5),  # 超出 2.0
                "rate": 1.0,
                "tremor": 0.0,
            }
            sample = self.generator.generate_sample(
                seed=seed,
                force_label="stress",
                force_factors=factor_config,
            )
            sample.ood_type = "parameter_ood"
            samples.append(sample)
            self.generator._save_wav(sample, output_dir / f"{sample.id}.wav")

        # 2. Compositional OOD（四因子同时激活）
        for i in range(n_per_type):
            seed = int(rng.integers(0, 2**31))
            factor_config = {
                "f0": rng.uniform(1.2, 1.4),
                "energy": rng.uniform(1.3, 1.5),
                "rate": rng.uniform(1.1, 1.3),
                "tremor": rng.uniform(0.1, 0.2),
            }
            sample = self.generator.generate_sample(
                seed=seed,
                force_label="stress",
                force_factors=factor_config,
            )
            sample.ood_type = "compositional_ood"
            samples.append(sample)
            self.generator._save_wav(sample, output_dir / f"{sample.id}.wav")

        # 3. Extreme Value OOD
        for i in range(n_per_type):
            seed = int(rng.integers(0, 2**31))
            factor_config = {
                "f0": rng.uniform(0.8, 0.9),  # 极低 F0
                "energy": rng.uniform(0.7, 0.9),  # 降低能量
                "rate": rng.uniform(0.7, 0.9),  # 极慢语速
                "tremor": 0.0,
            }
            sample = self.generator.generate_sample(
                seed=seed,
                force_label="normal",
                force_factors=factor_config,
            )
            sample.ood_type = "extreme_value_ood"
            samples.append(sample)
            self.generator._save_wav(sample, output_dir / f"{sample.id}.wav")

        # 4. Noise OOD（不同噪声强度）
        for i in range(n_per_type):
            seed = int(rng.integers(0, 2**31))
            factor_config = {
                "f0": 1.0,
                "energy": 1.0,
                "rate": 1.0,
                "tremor": 0.0,
            }
            sample = self.generator.generate_sample(
                seed=seed,
                force_label="normal",
                force_factors=factor_config,
            )
            # 覆盖 noise 参数
            sample.parameters["background_snr_db"] = rng.uniform(5.0, 15.0)  # 极低 SNR
            sample.ood_type = "noise_ood"
            samples.append(sample)
            self.generator._save_wav(sample, output_dir / f"{sample.id}.wav")

        # 保存索引
        self.generator._save_index(samples, output_dir)

        print(f"[OODTest] 已生成 {len(samples)} 个 OOD Test 样本")
        return samples


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Scale-1.1 Decoupled Generator")
    ap.add_argument("--base-dir", type=Path, default=Path("data/_canonical"))
    ap.add_argument("--output-dir", type=Path, default=Path("data/pilot/scale_1.1"))
    ap.add_argument("--n-core", type=int, default=300, help="Core train samples")
    ap.add_argument("--n-compositional", type=int, default=150, help="Compositional hard negatives")
    ap.add_argument("--n-hard-neg", type=int, default=150, help="Hard negatives")
    ap.add_argument("--n-ood", type=int, default=100, help="OOD test samples")
    args = ap.parse_args()

    # 初始化生成器
    gen = StressFactorDecoupledGenerator(
        base_dir=args.base_dir,
        output_dir=args.output_dir / "core",
    )

    # 1. 生成 Core 数据集
    print("\n[Scale-1.1] 生成 Core 数据集...")
    core_samples = gen.generate_batch(
        n_samples=args.n_core,
        output_dir=args.output_dir / "core",
        force_label_distribution={"stress": 0.5, "normal": 0.5},
    )

    # 2. 生成 Compositional Hard Negatives
    print("\n[Scale-1.1] 生成 Compositional Hard Negatives...")
    comp_gen = CompositionalHardNegativeGenerator(gen)
    comp_samples = comp_gen.generate_hard_negatives(
        n_per_type=args.n_compositional // 4,
        output_dir=args.output_dir / "compositional_neg",
    )

    # 3. 生成 Hard Negatives
    print("\n[Scale-1.1] 生成 Hard Negatives...")
    hard_neg_samples = comp_gen.generate_hard_negatives(
        n_per_type=args.n_hard_neg // 4,
        output_dir=args.output_dir / "hard_negatives",
    )

    # 4. 生成 OOD Test
    print("\n[Scale-1.1] 生成 OOD Test...")
    ood_gen = OODTestGenerator(gen)
    ood_samples = ood_gen.generate_ood_test(
        n_per_type=args.n_ood // 4,
        output_dir=args.output_dir / "ood_test",
    )

    print(f"\n[Scale-1.1] 完成！总计 {len(core_samples) + len(comp_samples) + len(hard_neg_samples) + len(ood_samples)} 样本")


if __name__ == "__main__":
    main()

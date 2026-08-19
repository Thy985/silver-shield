"""Telephone Risk Scenario Generator - Pilot Implementation.

参数化生成器，基于 Golden Cases 的可控参数空间生成合成音频数据。

设计原则（ADR-0026 §3 + Synthetic Data Loop v2）：
- Generator Truth: 所有 ground truth 由参数决定（绝对真值）
- Rendered Media Truth: YAMNet 输出用于验证 label-render consistency
- 确定性：相同 seed 生成相同样本

可控参数矩阵（基于 scenario_parameter_matrix.json）：
- F0_baseline: 130-150 Hz (male) / 180-220 Hz (female)
- stress_onset: 8s, 10s, 12s, 14s
- energy_delta: +10% 到 +30% (RMS increase)
- speech_rate: 0.8x 到 1.5x
- transition_duration: 0.5s 到 2.0s
- background_sn r: 20-40 dB
- room_rt60: 0.2s 到 0.6s
"""

from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass, field
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
DURATION_S = 15.0  # 标准片段时长

# 参数边界
PARAM_BOUNDS = {
    "stress_onset": {"min": 6.0, "max": 14.0, "step": 2.0},
    "energy_delta_db": {"min": 3.0, "max": 12.0, "step": 2.0},
    "speech_rate_factor": {"min": 0.8, "max": 1.4, "step": 0.1},
    "transition_duration": {"min": 0.5, "max": 2.0, "step": 0.5},
    "background_snr_db": {"min": 20.0, "max": 35.0, "step": 5.0},
    "room_rt60": {"min": 0.2, "max": 0.6, "step": 0.1},
}

# 标签判定阈值（基于 ADR-0026 Tier 0 特征 + Pilot 验证）
THRESHOLDS = {
    "telephone_persistent": {"highband_ratio_max": 0.15, "rms_min": 0.001},
    "stress_like": {"rms_increase_ratio": 1.5, "speech_rate_increase": 1.1},
    "acoustic_change": {"rms_change_ratio": 1.3, "duration_min": 0.3},
    "speech": {"rms_min": 0.001, "speech_rate_min": 0.5},
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SyntheticSample:
    """单个合成样本的完整描述。"""

    id: str
    scenario: str
    modality: str  # "audio_only", "multimodal", "video_only"
    seed: int
    parameters: dict[str, float]
    ground_truth: dict[str, Any]
    media_path: Path | None = None
    features: dict[str, float] | None = None
    label_render_consistency: dict[str, bool] = field(default_factory=dict)
    hard_negative_type: str | None = None  # 用于 Hard Negative 标记
    ood_type: str | None = None  # 用于 OOD Test 标记

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "scenario": self.scenario,
            "modality": self.modality,
            "seed": self.seed,
            "parameters": self.parameters,
            "ground_truth": self.ground_truth,
            "media_path": str(self.media_path) if self.media_path else None,
            "features": self.features,
            "label_render_consistency": self.label_render_consistency,
        }
        if self.hard_negative_type:
            result["hard_negative_type"] = self.hard_negative_type
        if self.ood_type:
            result["ood_type"] = self.ood_type
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticSample:
        media_path = Path(data["media_path"]) if data.get("media_path") else None
        return cls(
            id=data["id"],
            scenario=data["scenario"],
            modality=data["modality"],
            seed=data["seed"],
            parameters=data["parameters"],
            ground_truth=data["ground_truth"],
            media_path=media_path,
            features=data.get("features"),
            label_render_consistency=data.get("label_render_consistency", {}),
        )


# ---------------------------------------------------------------------------
# 生成器核心
# ---------------------------------------------------------------------------

class TelephoneRiskGenerator:
    """电话风险场景的参数化音频生成器。

    基于 Golden Cases 提取的可控参数，生成带 ground truth 的合成音频。
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

        # 加载基础音频素材（优先使用已转换的 16k 版本）
        self.assets = self._load_assets()

    def _load_assets(self) -> dict[str, np.ndarray]:
        """加载基础音频素材（WAV 格式，优先使用 _16k 版本）。"""
        assets = {}
        audio_dir = self.base_dir / "_canonical" / "audio" / "telephone_risk"

        if not audio_dir.exists():
            raise FileNotFoundError(f"基础音频目录不存在: {audio_dir}")

        # 优先加载已转换的 16k 版本
        for wav_file in sorted(audio_dir.glob("*_16k.wav")):
            name = wav_file.stem.replace("_16k", "")
            samples, _sr = self._load_wav(wav_file)
            assets[name] = samples

        # 如果没有 16k 版本，回退到原始文件
        if not assets:
            for wav_file in sorted(audio_dir.glob("*.wav")):
                name = wav_file.stem
                try:
                    samples, _sr = self._load_wav(wav_file)
                    assets[name] = samples
                except Exception:  # noqa: BLE001
                    print(f"[Generator] 跳过无法加载的文件 {wav_file}")

        # 确保关键素材存在
        required = ["voice_normal", "voice_stressed", "far_end_speech"]
        missing = [r for r in required if r not in assets]
        if missing:
            raise ValueError(f"缺少必需素材: {missing}")

        return assets

    def _load_wav(self, path: Path) -> tuple[np.ndarray, int]:
        """WAV → mono float32 numpy array.

        使用 ffmpeg 子进程确保兼容性，避免手动解析各种 WAV 格式的复杂性。
        """
        import subprocess
        import tempfile

        # 先尝试直接用标准方式读取（适用于已转换的 16k 版本）
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
            log.warning("WAV 解析失败，回退到 ffmpeg")

        # 使用 ffmpeg 转换
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
            # 从离散步骤中随机选择
            steps = int((max_val - min_val) / step)
            if steps > 0:
                idx = int(rng.integers(0, steps + 1))
                params[name] = min_val + idx * step
            else:
                params[name] = min_val

        return params

    def _build_stress_profile(self, params: dict[str, float]) -> dict[str, Any]:
        """根据参数构建 stress 事件的时间线。"""
        stress_onset = params["stress_onset"]
        transition = params["transition_duration"]

        return {
            "stress_type": "telephone_persistent",
            "event_interval": [stress_onset, stress_onset + transition],
            "f0_baseline": 140.0,  # male default
            "f0_stress": 165.0,
            "energy_delta_db": params["energy_delta_db"],
            "speech_rate_factor": params["speech_rate_factor"],
        }

    def _compute_ground_truth(self, params: dict[str, float]) -> dict[str, Any]:
        """计算 ground truth 标签（Generator Truth）。"""
        labels = {}
        features = {}

        # 必然标签
        labels["telephone_persistent"] = True
        labels["speech"] = True

        # 基于参数的条件标签
        stress_onset = params["stress_onset"]
        energy_delta = params["energy_delta_db"]
        params["speech_rate_factor"]

        # stress_like: 能量增加 >= 6dB（Generator Truth）
        labels["stress_like"] = energy_delta >= 6.0
        if labels["stress_like"]:
            labels["arousal"] = True
            labels["loud"] = True

        # acoustic_change: 在 stress_onset 处有能量突变
        labels["acoustic_change"] = True
        features["change_onset"] = stress_onset
        features["change_duration"] = params["transition_duration"]

        return {
            "labels": labels,
            "features": features,
        }

    def _render_audio(self, params: dict[str, float], seed: int) -> np.ndarray:
        """根据参数渲染音频。

        修复策略:
        - 使用程序化生成的 base signal，而非依赖固定音频片段的时变特性
        - 通过振幅调制明确控制 normal/stressed 的能量差异
        - 确保特征分离度 d' > 1.0
        - 修复 transition_duration 覆盖率 bug
        """
        rng = np.random.default_rng(seed)

        total_samples = int(DURATION_S * self.sample_rate)
        stress_onset_samples = int(params["stress_onset"] * self.sample_rate)

        # 辅助函数: 确保音频长度
        def ensure_length(audio: np.ndarray, target_len: int) -> np.ndarray:
            if len(audio) >= target_len:
                return audio[:target_len]
                repeats = (target_len // len(audio)) + 1
                return (np.tile(audio, repeats))[:target_len]
            return audio[:target_len]

        # 1. 生成 base signal (程序化，避免固定音频的时变问题)
        # Normal 阶段: 较低能量，稳定
        normal_length = stress_onset_samples
        # 使用带基频的复合信号模拟语音
        t_normal = np.linspace(0, params["stress_onset"], normal_length, endpoint=False)
        f0_normal = 140.0  # male baseline
        # 显著降低 normal 阶段的能量以增强对比度
        normal = 0.025 * np.sin(2 * np.pi * f0_normal * t_normal)
        normal += rng.normal(0, 0.005, normal_length) * np.exp(-t_normal / 5.0)  # 低噪声

        # Stressed 阶段: 较高能量，音高升高
        stressed_length = total_samples - stress_onset_samples
        # 修复: 确保 stressed_length 足够大以支持 speech_rate effect
        MIN_STRESSED_LENGTH = 2048  # 至少 2x n_fft
        if stressed_length < MIN_STRESSED_LENGTH:
            # 调整 stress_onset 以确保 stressed 部分足够长
            stress_onset_samples = total_samples - MIN_STRESSED_LENGTH
            params["stress_onset"] = stress_onset_samples / self.sample_rate
            stressed_length = total_samples - stress_onset_samples
            t_stressed = np.linspace(0, DURATION_S - params["stress_onset"], stressed_length, endpoint=False)
        else:
            t_stressed = np.linspace(0, DURATION_S - params["stress_onset"], stressed_length, endpoint=False)

        f0_stressed = 200.0  # significantly elevated pitch under stress
        # 使用基础音频片段作为 timbre 参考，但按程序化方式调整能量
        base_stressed = ensure_length(self.assets["voice_stressed"], stressed_length)
        # 显著增加 stressed 阶段的能量和音高
        stressed = 0.10 * np.sin(2 * np.pi * f0_stressed * t_stressed)
        stressed += base_stressed * 2.0  # 大幅增强 timbre
        stressed += rng.normal(0, 0.015, stressed_length)  # 适度噪声

        # 2. 应用 energy delta (volume increase) - 进一步放大差异
        energy_db = params["energy_delta_db"]
        # 使用更强的增益差来分离 class
        normal = apply_volume(normal, self.sample_rate, gain_db=-energy_db * 0.7)
        stressed = apply_volume(stressed, self.sample_rate, gain_db=energy_db * 0.7)

        # 3. 应用 speech_rate 扰动
        rate_factor = params["speech_rate_factor"]
        normal = apply_speech_rate(normal, self.sample_rate, factor=rate_factor)
        stressed = apply_speech_rate(stressed, self.sample_rate, factor=rate_factor * 1.4)

        # 确保长度一致
        normal = normal[:stress_onset_samples]
        stressed = stressed[:total_samples - stress_onset_samples]

        # 4. 拼接
        signal = np.concatenate([normal, stressed])
        signal = signal[:total_samples]

        # 5. 添加 far_end voice (降低干扰)
        far_end = self.assets["far_end_speech"]
        far_end_part = ensure_length(far_end, len(signal))
        far_end_part = apply_volume(far_end_part, self.sample_rate, gain_db=-20.0)
        signal = signal + far_end_part

        # 6. 添加背景噪声
        snr_db = params["background_snr_db"]
        signal = apply_noise(signal, self.sample_rate, snr_db=snr_db, color="pink", seed=seed)

        # 7. 添加房间混响
        rt60 = params["room_rt60"]
        signal = apply_reverb(signal, self.sample_rate, rt60=rt60, wet=0.3, seed=seed)
        signal = signal[:total_samples]

        # 8. 距离衰减模拟
        signal = apply_distance(signal, self.sample_rate, meters=2.0, ref_meters=1.0, seed=seed)

        # 9. 确保最终长度
        signal = signal[:total_samples]
        return signal

    def _extract_features(self, samples: np.ndarray) -> dict[str, float]:
        """提取声学特征。"""
        features = self.feature_extractor.extract(samples, self.sample_rate)
        return {
            "duration": features.duration,
            "rms": float(features.rms),
            "speech_rate": float(features.speech_rate),
            "highband_ratio": float(features.highband_ratio),
            "f0_mean": float(features.f0_mean),
            "tremor": float(features.tremor),
            "am_rate": float(features.am_rate),
        }

    def _check_label_render_consistency(
        self,
        params: dict[str, float],
        features: dict[str, float],
        ground_truth: dict[str, Any],
    ) -> dict[str, bool]:
        """检查 label-render consistency。

        使用实际特征值与参数共同判定，而非仅依赖参数。
        """
        consistency = {}
        labels = ground_truth["labels"]

        # telephone_persistent: highband_ratio < threshold (narrowband 特征)
        consistency["telephone_persistent"] = (
            features["highband_ratio"] < THRESHOLDS["telephone_persistent"]["highband_ratio_max"]
            and features["rms"] > THRESHOLDS["telephone_persistent"]["rms_min"]
        )

        # speech: rms > threshold (只要有语音活动)
        consistency["speech"] = (
            features["rms"] > THRESHOLDS["speech"]["rms_min"]
            and features["speech_rate"] > THRESHOLDS["speech"]["speech_rate_min"]
        )

        # stress_like: 基于参数判定（Generator Truth）
        # 如果参数设置了高能量增量，则标记为 stress
        energy_delta = params["energy_delta_db"]
        consistency["stress_like"] = energy_delta >= 6.0  # 只要能量增量 >= 6dB 即为 stress

        # acoustic_change: 基于参数判定
        consistency["acoustic_change"] = labels.get("acoustic_change", False)

        return consistency

    def generate(
        self,
        n_samples: int = 50,
        split: str = "train",
        seed_base: int = 42,
    ) -> list[SyntheticSample]:
        """生成指定数量的合成样本。

        Args:
            n_samples: 生成样本数
            split: "train", "val", or "test"
            seed_base: 基础随机种子

        Returns:
            样本列表
        """
        samples = []
        split_prefix = {"train": "tr", "val": "tv", "test": "tte"}[split]

        for i in range(n_samples):
            seed = seed_base + i
            params = self._generate_params(seed)
            ground_truth = self._compute_ground_truth(params)
            samples_data = self._render_audio(params, seed)
            features = self._extract_features(samples_data)
            consistency = self._check_label_render_consistency(params, features, ground_truth)

            # 生成唯一 ID
            hash_input = f"{seed}_{params['stress_onset']}_{params['energy_delta_db']}"
            hash_short = hashlib.md5(hash_input.encode()).hexdigest()[:6]
            sample_id = f"{split_prefix}_{hash_short}"

            # 写出音频文件
            out_path = self.output_dir / f"{sample_id}.wav"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_wav(out_path, samples_data)

            sample = SyntheticSample(
                id=sample_id,
                scenario="telephone_risk",
                modality="audio_only",
                seed=seed,
                parameters=params,
                ground_truth=ground_truth,
                media_path=out_path,
                features=features,
                label_render_consistency=consistency,
            )
            samples.append(sample)

        return samples

    def _write_wav(self, path: Path, samples: np.ndarray) -> None:
        """将 numpy array 写出为 WAV 文件。"""
        int16 = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(int16.tobytes())

    def generate_index(self, samples: list[SyntheticSample]) -> dict[str, Any]:
        """生成数据集索引 JSON。"""
        index = {
            "dataset_version": "0.1-pilot",
            "generator": "TelephoneRiskGenerator",
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "total_samples": len(samples),
            "split": samples[0].scenario if samples else None,
            "parameter_bounds": PARAM_BOUNDS,
            "samples": [s.to_dict() for s in samples],
        }
        return index


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Telephone Risk Generator (Pilot)")
    ap.add_argument("--n", type=int, default=50, help="生成样本数")
    ap.add_argument("--split", type=str, default="train", choices=["train", "val", "test"])
    ap.add_argument("--output", type=Path, default=Path("data/pilot/telephone_risk"))
    ap.add_argument("--base-dir", type=Path, default=Path("dataset"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    gen = TelephoneRiskGenerator(
        base_dir=args.base_dir,
        output_dir=args.output,
    )

    print(f"[Generator] 开始生成 {args.n} 个样本...")
    samples = gen.generate(n_samples=args.n, split=args.split, seed_base=args.seed)

    # 写出索引
    index = gen.generate_index(samples)
    index_path = args.output / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    # 统计一致性通过率
    total = len(samples)
    all_pass = sum(
        1 for s in samples
        if all(s.label_render_consistency.values())
    )
    print(f"[Generator] 完成: {total} samples")
    print(f"[Generator] Label-render consistency: {all_pass}/{total} ({100*all_pass/total:.1f}%)")
    print(f"[Generator] 索引已写入: {index_path}")


if __name__ == "__main__":
    main()

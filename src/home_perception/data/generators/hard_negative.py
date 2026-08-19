"""Hard Negative Generator for Scale-1.

生成 8 种类型的 hard negative 样本，每个样本只激活单一 stress 维度。
"""
import sys

sys.path.insert(0, 'src')
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from home_perception.data.generators.telephone_risk import SyntheticSample, TelephoneRiskGenerator

# Hard Negative Taxonomy
HARD_NEG_TYPES = [
    "higher_pitch",        # 只有 F0↑
    "louder_speech",       # 只有 Energy↑
    "faster_speech",       # 只有 Rate↑
    "noisy_phone",         # Phone audio + noise
    "reverberant",         # 强混响
    "far_end_only",        # 只有远端语音
    "elder_normal",        # 老年人正常语音
    "compressed_audio",    # 压缩电话音频
]


@dataclass
class HardNegativeConfig:
    """单个 hard negative 的配置。"""
    sample_type: str
    seed: int
    parameters: dict[str, float]


class HardNegativeGenerator:
    """Hard Negative 生成器。"""

    def __init__(self, base_dir: Path, output_dir: Path):
        self.base_gen = TelephoneRiskGenerator(base_dir=base_dir, output_dir=output_dir)
        self.output_dir = output_dir
        self.n_per_type = 40  # 每种类型 40 个样本

    def generate(self, n_per_type: int = 40) -> list[SyntheticSample]:
        """生成所有 hard negative 样本。"""
        self.n_per_type = n_per_type
        all_samples = []

        for neg_type in HARD_NEG_TYPES:
            print(f"Generating {neg_type} hard negatives...")
            type_samples = self._generate_type(neg_type, n_per_type)
            all_samples.extend(type_samples)

        return all_samples

    def _generate_type(self, neg_type: str, n: int) -> list[SyntheticSample]:
        """生成指定类型的 hard negative。"""
        samples = []
        rng = np.random.default_rng()

        for i in range(n):
            seed = hash(f"{neg_type}_{i}") % (2**32)
            params = self._get_params(neg_type, seed, rng)
            ground_truth = self.base_gen._compute_ground_truth(params)

            # 关键：hard negative 的 label 始终为 normal
            ground_truth["labels"]["stress_like"] = False

            samples_data = self.base_gen._render_audio(params, seed)
            features = self.base_gen._extract_features(samples_data)
            consistency = self.base_gen._check_label_render_consistency(
                params, features, ground_truth
            )

            hash_input = f"{seed}_{neg_type}"
            hash_short = hashlib.md5(hash_input.encode()).hexdigest()[:6]
            sample_id = f"hn_{neg_type[:4]}_{hash_short}"

            out_path = self.output_dir / f"{sample_id}.wav"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self.base_gen._write_wav(out_path, samples_data)

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
                hard_negative_type=neg_type,  # 标记类型
            )
            samples.append(sample)

        return samples

    def _get_params(self, neg_type: str, seed: int, rng: np.random.Generator) -> dict[str, float]:
        """根据类型生成参数。"""
        base_params = {
            "stress_onset": rng.choice([8.0, 10.0, 12.0]),
            "energy_delta_db": 3.0,  # 低能量（normal）
            "speech_rate_factor": 1.0,
            "transition_duration": 1.0,
            "background_snr_db": 30.0,
            "room_rt60": 0.3,
        }

        if neg_type == "higher_pitch":
            # 高 pitch，但其他正常
            base_params["f0_effective"] = 200.0  # 通过修改 generator 实现

        elif neg_type == "louder_speech":
            # 高能量，但非 stress
            base_params["energy_delta_db"] = 5.0  # 中等能量，但未达 stress 阈值

        elif neg_type == "faster_speech":
            # 快语速，但非 stress
            base_params["speech_rate_factor"] = 1.3

        elif neg_type == "noisy_phone":
            # 高噪声环境
            base_params["background_snr_db"] = 15.0

        elif neg_type == "reverberant":
            # 强混响
            base_params["room_rt60"] = 0.6

        elif neg_type == "far_end_only":
            # 只有远端语音
            base_params["far_end_volume_db"] = -6.0

        elif neg_type == "elder_normal":
            # 老年人正常语音（低能量+慢语速）
            base_params["energy_delta_db"] = 2.0
            base_params["speech_rate_factor"] = 0.8

        elif neg_type == "compressed_audio":
            # 压缩电话音频（通过降低 highband 模拟）
            base_params["highband_reduction"] = True

        return base_params

    def save_index(self, samples: list[SyntheticSample], output_path: Path):
        """保存索引。"""
        data = {
            "version": "1.0",
            "generator": "HardNegativeGenerator",
            "n_samples": len(samples),
            "types": HARD_NEG_TYPES,
            "n_per_type": self.n_per_type,
            "samples": [s.to_dict() for s in samples],
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(samples)} hard negatives to {output_path}")


def main():
    base_dir = Path("dataset")
    output_dir = Path("data/pilot/hard_negatives_v2")
    output_dir.mkdir(parents=True, exist_ok=True)

    gen = HardNegativeGenerator(base_dir, output_dir)
    samples = gen.generate(n_per_type=40)

    # Save index
    index_path = output_dir / "index.json"
    gen.save_index(samples, index_path)

    # Stats
    print(f"\nGenerated {len(samples)} hard negative samples:")
    for t in HARD_NEG_TYPES:
        count = sum(1 for s in samples if s.hard_negative_type == t)
        print(f"  {t}: {count}")


if __name__ == "__main__":
    main()

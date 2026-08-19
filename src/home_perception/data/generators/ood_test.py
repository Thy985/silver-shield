"""OOD Test Set Generator for Scale-1.

生成 4 种类型的 OOD 测试样本：
1. Parameter OOD: 超出训练参数范围
2. Combinatorial OOD: 未见参数组合
3. Extreme Values: 极端参数值
4. Noise OOD: 不同噪声类型
"""
import sys

sys.path.insert(0, 'src')
import hashlib
import json
from pathlib import Path

import numpy as np

from home_perception.data.generators.telephone_risk import SyntheticSample, TelephoneRiskGenerator

# OOD Test Types
OOD_TYPES = [
    "parameter_ood",      # 超出训练范围
    "combinatorial_ood",  # 未见组合
    "extreme_values",     # 极端参数
    "noise_ood",          # 不同噪声
]


class OODTestGenerator:
    """OOD Test Set 生成器。"""

    def __init__(self, base_dir: Path, output_dir: Path):
        self.base_gen = TelephoneRiskGenerator(base_dir=base_dir, output_dir=output_dir)
        self.output_dir = output_dir
        self.n_per_type = 15  # 每种类型 15 个样本

    def generate(self, n_per_type: int = 15) -> list[SyntheticSample]:
        """生成所有 OOD 测试样本。"""
        self.n_per_type = n_per_type
        all_samples = []

        for ood_type in OOD_TYPES:
            print(f"Generating {ood_type} OOD samples...")
            type_samples = self._generate_type(ood_type, n_per_type)
            all_samples.extend(type_samples)

        return all_samples

    def _generate_type(self, ood_type: str, n: int) -> list[SyntheticSample]:
        """生成指定类型的 OOD 样本。"""
        samples = []
        rng = np.random.default_rng()

        for i in range(n):
            seed = hash(f"{ood_type}_{i}") % (2**32)
            params = self._get_params(ood_type, seed, rng)
            ground_truth = self.base_gen._compute_ground_truth(params)
            samples_data = self.base_gen._render_audio(params, seed)
            features = self.base_gen._extract_features(samples_data)
            consistency = self.base_gen._check_label_render_consistency(
                params, features, ground_truth
            )

            hash_input = f"{seed}_{ood_type}"
            hash_short = hashlib.md5(hash_input.encode()).hexdigest()[:6]
            sample_id = f"ood_{ood_type[:4]}_{hash_short}"

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
                ood_type=ood_type,  # 标记类型
            )
            samples.append(sample)

        return samples

    def _get_params(self, ood_type: str, seed: int, rng: np.random.Generator) -> dict[str, float]:
        """根据类型生成 OOD 参数。"""
        if ood_type == "parameter_ood":
            # 超出训练范围
            params = {
                "stress_onset": rng.choice([5.0, 15.0]),  # 超出 [6, 14]
                "energy_delta_db": rng.choice([1.0, 15.0]),  # 超出 [3, 12]
                "speech_rate_factor": 1.0,
                "transition_duration": 1.0,
                "background_snr_db": 30.0,
                "room_rt60": 0.3,
            }

        elif ood_type == "combinatorial_ood":
            # 未见组合：高 F0 + 高噪声
            params = {
                "stress_onset": 10.0,
                "energy_delta_db": 9.0,
                "speech_rate_factor": 1.0,
                "transition_duration": 1.0,
                "background_snr_db": 15.0,  # 高噪声 + 高能量
                "room_rt60": 0.3,
            }

        elif ood_type == "extreme_values":
            # 极端参数
            params = {
                "stress_onset": 6.0,  # 最小值
                "energy_delta_db": 12.0,  # 最大值
                "speech_rate_factor": rng.choice([0.8, 1.4]),  # 极端值
                "transition_duration": 0.5,  # 最小值
                "background_snr_db": rng.choice([20.0, 35.0]),  # 极端值
                "room_rt60": rng.choice([0.2, 0.6]),  # 极端值
            }

        elif ood_type == "noise_ood":
            # 不同噪声类型
            params = {
                "stress_onset": 10.0,
                "energy_delta_db": 9.0,
                "speech_rate_factor": 1.0,
                "transition_duration": 1.0,
                "background_snr_db": 25.0,
                "room_rt60": 0.3,
                "noise_color": "white",  # 白噪声而非粉噪
            }

        else:
            # 默认：随机参数
            params = self.base_gen._generate_params(seed)

        return params

    def save_index(self, samples: list[SyntheticSample], output_path: Path):
        """保存索引。"""
        # 转换 numpy 类型为 Python 原生类型
        def convert_types(obj):
            if isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(v) for v in obj]
            elif isinstance(obj, (np.bool_, bool)):
                return bool(obj)
            elif isinstance(obj, (np.integer,)):
                return int(obj)
            elif isinstance(obj, (np.floating,)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj

        data = {
            "version": "1.0",
            "generator": "OODTestGenerator",
            "n_samples": len(samples),
            "types": OOD_TYPES,
            "n_per_type": self.n_per_type,
            "samples": convert_types([s.to_dict() for s in samples]),
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(samples)} OOD test samples to {output_path}")


def main():
    base_dir = Path("dataset")
    output_dir = Path("data/pilot/ood_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    gen = OODTestGenerator(base_dir, output_dir)
    samples = gen.generate(n_per_type=15)

    # Save index
    index_path = output_dir / "index.json"
    gen.save_index(samples, index_path)

    # Stats
    print(f"\nGenerated {len(samples)} OOD test samples:")
    for t in OOD_TYPES:
        count = sum(1 for s in samples if getattr(s, 'ood_type', None) == t)
        print(f"  {t}: {count}")


if __name__ == "__main__":
    main()

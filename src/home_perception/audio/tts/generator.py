"""场景驱动的合成音频生成器（Audio Synthetic Infrastructure / ``tts`` 包子模块）。

读取 ``scenario.yaml``，对每个 scenario 生成一条合成音频写入输出目录：

  - base 来源 1：``tts``（EdgeTTS 合成，需 ``pip install -e ".[audio-dev]"`` + 网络）
  - base 来源 2：``base_ref``（引用已有 WAV，离线可用，无需网络 / 重解码）

每条 scenario 的 ``effects`` 链由 :func:`effects.apply_effects` 按声明顺序施加。
本模块仅依赖 numpy + pyyaml + 标准库 ``wave``（解码 / 编码 WAV），不引入重解码依赖。
"""

from __future__ import annotations

import argparse
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .effects import apply_effects
from .provider import EdgeTTSProvider, TTSProvider

TARGET_SR = 16000


@dataclass
class Scenario:
    """单条合成场景。``base_source()`` 决定 base 音频来源。"""

    id: str
    tts: dict | None = None
    base_ref: str | None = None
    base_wav: str | None = None
    effects: list[dict] = field(default_factory=list)
    expected: dict | None = None
    seed: int = 42

    def base_source(self) -> str:
        if self.base_wav:
            return "wav"
        if self.base_ref:
            return "ref"
        if self.tts:
            return "tts"
        raise ValueError(f"scenario {self.id!r} 缺少 base（tts / base_ref / base_wav）")


def _repo_root(start: Path) -> Path:
    """向上查找含 ``tests/fixtures/audio`` 的仓库根目录，用于解析相对 base 路径。"""
    p = Path(start).resolve()
    for cand in [p, *p.parents]:
        if (cand / "tests" / "fixtures" / "audio").is_dir():
            return cand
    return p


def load_scenarios(path: Path) -> tuple[list[Scenario], str | None]:
    """解析 scenario.yaml。返回 (scenarios, base_dir)。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    base_dir = data.get("base_dir")
    scenarios: list[Scenario] = []
    for raw in data.get("scenarios", []):
        scenarios.append(
            Scenario(
                id=raw["id"],
                tts=raw.get("tts"),
                base_ref=raw.get("base_ref"),
                base_wav=raw.get("base_wav"),
                effects=raw.get("effects", []),
                expected=raw.get("expected"),
                seed=int(raw.get("seed", 42)),
            )
        )
    return scenarios, base_dir


def _decode_mp3_bytes(data: bytes, target_sr: int = TARGET_SR) -> tuple[np.ndarray, int]:
    """MP3 字节 → numpy（PyAV 解码，惰性 import）。仅 TTS base 路径使用。"""
    import io

    import av

    container = av.open(io.BytesIO(data))
    src_sr = container.streams.audio[0].sample_rate
    chunks = []
    for frame in container.decode(audio=0):
        arr = frame.to_ndarray().astype(np.float32)
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        if arr.dtype != np.float32 or float(np.max(np.abs(arr))) > 1.5:
            arr = arr.astype(np.float32) / 32768.0
        chunks.append(arr)
    if not chunks:
        raise RuntimeError("MP3 解码为空")
    samples = np.concatenate(chunks)
    if src_sr != target_sr:
        n_target = round(len(samples) * target_sr / src_sr)
        samples = np.interp(np.linspace(0, len(samples) - 1, n_target), np.arange(len(samples)), samples)
    return samples.astype(np.float32), target_sr


def _load_wav(path: Path, target_sr: int = TARGET_SR) -> tuple[np.ndarray, int]:
    """WAV → mono float32（复用 FileAudioSource 的解码约定，零重依赖）。"""
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
    if sr != target_sr:
        n_target = round(len(data) * target_sr / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n_target), np.arange(len(data)), data)
    return data.astype(np.float32), target_sr


def _write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    int16 = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16.tobytes())


def _resolve_base(sc: Scenario, fixtures_root: Path) -> Path:
    """解析 base 音频路径：绝对 / fixtures_root / 仓库根相对。

    找不到时显式抛出 ``FileNotFoundError``（附带尝试过的路径），便于调试，
    而非静默返回错误路径、等 ``_load_wav`` 才报晦涩的 I/O 错误。
    """
    ref = sc.base_wav or sc.base_ref
    if ref is None:
        raise ValueError(f"scenario {sc.id!r} 无 base 引用")
    p = Path(ref)
    if p.is_absolute():
        if not p.exists():
            raise FileNotFoundError(f"scenario {sc.id!r} 的 base 文件未找到: {p}")
        return p
    cand = Path(fixtures_root) / ref
    root = _repo_root(fixtures_root)
    cand2 = root / ref
    tried = [str(cand), str(cand2)]
    if cand.exists():
        return cand
    if cand2.exists():
        return cand2
    raise FileNotFoundError(
        f"scenario {sc.id!r} 的 base 文件未找到: {ref!r}；尝试路径: {tried}"
    )


def generate_scenario(
    sc: Scenario,
    out_dir: Path,
    provider: TTSProvider | None = None,
    fixtures_root: Path | None = None,
) -> Path:
    """生成单条 scenario，返回写出路径。``fixtures_root`` 用于解析 ``base_ref``。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = sc.base_source()
    if src == "tts":
        if provider is None:
            provider = EdgeTTSProvider()
        tts = sc.tts or {}
        mp3 = provider.synthesize_bytes(
            text=tts.get("text", ""),
            voice=tts.get("voice", "zh-CN-XiaoxiaoNeural"),
            rate=float(tts.get("rate", 1.0)),
            pitch=float(tts.get("pitch", 1.0)),
        )
        samples, sr = _decode_mp3_bytes(mp3, TARGET_SR)
    else:
        base_path = _resolve_base(sc, fixtures_root or Path("fixtures"))
        samples, sr = _load_wav(base_path, TARGET_SR)

    samples = apply_effects(samples, sr, sc.effects)
    out_path = out_dir / f"{sc.id}.wav"
    _write_wav(out_path, samples, sr)
    return out_path


def generate_all(
    scenarios_path: Path,
    out_dir: Path,
    provider: TTSProvider | None = None,
    fixtures_root: Path | None = None,
) -> list[Path]:
    """生成 scenario.yaml 中的全部场景。``fixtures_root`` 缺省时由 base_dir + 仓库根推导。"""
    scenarios_path = Path(scenarios_path)
    scenarios, base_dir = load_scenarios(scenarios_path)
    if fixtures_root is None:
        fixtures_root = _repo_root(scenarios_path)
        if base_dir:
            cand = fixtures_root / base_dir
            fixtures_root = cand if cand.exists() else scenarios_path.parent / base_dir
    out: list[Path] = []
    for sc in scenarios:
        out.append(generate_scenario(sc, out_dir, provider, Path(fixtures_root)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="场景驱动合成音频生成器（tts 包）")
    ap.add_argument(
        "--scenario",
        type=Path,
        default=Path(__file__).resolve().parent / "scenario.yaml",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "fixtures",
    )
    args = ap.parse_args()
    paths = generate_all(Path(args.scenario), Path(args.out))
    for p in paths:
        print(f"[gen] {p.name}")


if __name__ == "__main__":
    main()

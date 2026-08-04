"""音频检测器（ADR-0026 管道：``AudioSource → AudioDetector`` 分段）。

> ``AudioDetector`` 只负责「语音分段」（VAD），产出 [(start_sec, end_sec), ...] 与整段语音占比
> ``vad_ratio``；不提取语义特征（那是 ``AudioFeatureExtractor`` 的职责），不产任何感知事件
> （那是 ``AudioRule`` 的职责）。单一职责，便于变异测试与失败隔离。
"""

from __future__ import annotations

from dataclasses import dataclass

from .source import LoadedAudio
from .vad import EnergyVadBackend, VadBackend


@dataclass
class DetectionResult:
    """分段结果。"""

    segments: list[tuple[float, float]]  # [(start_sec, end_sec), ...]
    vad_ratio: float  # 整段语音占比 0~1
    backend: str


class AudioDetector:
    """语音活动检测器：把音频切成语音段。

    相邻语音段若间隔小于 ``merge_gap_ms`` 则合并为整句级片段——避免能量 VAD 把连续语音
    切成过碎的段（导致语速等跨段特征失真）。

    后端选择：默认使用 ``EnergyVadBackend``（纯 numpy、零模型、跨平台确定），保证本地
    Windows 与 CI(Linux) 用同一后端产出完全一致的 utterance 级片段，使 fixture 测试跨平台
    确定性可复现。``WebRtcVadBackend`` 精度更高但需 ``webrtcvad``（仅 Linux 有 wheel），
    属**可选 opt-in**（显式传入 ``AudioDetector(vad=WebRtcVadBackend())``），不进入默认管道，
    以免影响 CI 确定性。
    """

    def __init__(self, vad: VadBackend | None = None, merge_gap_ms: int = 300) -> None:
        self.vad = vad or EnergyVadBackend()
        self.merge_gap_ms = merge_gap_ms

    def detect(self, audio: LoadedAudio) -> DetectionResult:
        segments = self._merge(self.vad.detect(audio), audio.sample_rate)
        total = len(audio.samples) / audio.sample_rate if audio.sample_rate else 0.0
        speech = sum((e - s) for s, e in segments)
        vad_ratio = min(1.0, speech / total) if total > 0 else 0.0
        return DetectionResult(segments=segments, vad_ratio=vad_ratio, backend=self.vad.name)

    def _merge(
        self, segments: list[tuple[float, float]], sr: int
    ) -> list[tuple[float, float]]:
        if not segments:
            return []
        segs = sorted(segments)
        gap = self.merge_gap_ms / 1000.0
        merged: list[tuple[float, float]] = [segs[0]]
        for s, e in segs[1:]:
            ps, pe = merged[-1]
            if s - pe <= gap:
                merged[-1] = (ps, max(pe, e))
            else:
                merged.append((s, e))
        return merged

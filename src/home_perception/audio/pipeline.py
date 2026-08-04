"""音频感知管道（ADR-0026 管道组装 + 失败隔离）。

> ``AudioSource → AudioDetector → AudioFeatureExtractor → AudioRule → AudioPerceptionEvent``
> 镜像视觉链 ``FrameSource → Detector → ... → VisitorEvent → PerceptionEvent``。
>
> **失败隔离（ADR-0026 §8 / AGENTS §2.5）**：音频链异常时降级为"无音频事件"（返回 []），
> 不抛未分类异常、不拖垮主管道。任何异常均记录后安全返回，符合"事件 / 状态不崩溃"原则。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..common.logging import get_logger
from .detector import AudioDetector
from .event import AudioPerceptionEvent, AudioSegmentEvent, new_event_id
from .features import AudioFeatureExtractor
from .rule import AudioRule
from .source import AudioSource, FileAudioSource, LoadedAudio

log = get_logger(__name__)


class AudioPipeline:
    """音频感知管道（可插拔组件组装）。"""

    def __init__(
        self,
        source: AudioSource,
        detector: AudioDetector | None = None,
        extractor: AudioFeatureExtractor | None = None,
        rule: AudioRule | None = None,
    ) -> None:
        self.source = source
        self.detector = detector or AudioDetector()
        self.extractor = extractor or AudioFeatureExtractor()
        self.rule = rule or AudioRule()

    @classmethod
    def from_defaults(cls, path: str | Path) -> AudioPipeline:
        """用默认组件构建一个针对文件的管道（测试 / 回放入口）。"""
        return cls(source=FileAudioSource(path))

    def run_path(self, path: str | Path) -> list[AudioPerceptionEvent]:
        """从文件路径运行（便捷封装）。"""
        return self.run(FileAudioSource(path))

    def run(self, source: AudioSource) -> list[AudioPerceptionEvent]:
        """运行管道，返回感知事件列表。

        失败隔离：任何阶段异常 → 记录日志并降级返回 []（不抛未分类异常）。
        """
        try:
            audio = source.load()
        except Exception as exc:  # noqa: BLE001  # 取流失败：降级为无音频事件
            log.warning("audio.pipeline.source_failed", error=str(exc))
            return []

        try:
            detection = self.detector.detect(audio)
        except Exception as exc:  # noqa: BLE001  # 分段失败：降级
            log.warning("audio.pipeline.detection_failed", error=str(exc))
            return []

        events: list[AudioPerceptionEvent] = []
        for start, end in detection.segments:
            try:
                ev = self._process_segment(audio, start, end, detection.vad_ratio)
            except Exception as exc:  # noqa: BLE001  # 单段失败：跳过该段，不崩
                log.warning("audio.pipeline.segment_failed", start=start, error=str(exc))
                continue
            if ev is not None:
                events.append(ev)

        log.info(
            "audio.pipeline.done",
            events=len(events),
            segments=len(detection.segments),
            backend=detection.backend,
        )
        return events

    # ---- 内部 ----

    def _process_segment(
        self, audio: LoadedAudio, start: float, end: float, vad_ratio: float
    ) -> AudioPerceptionEvent | None:
        sr = audio.sample_rate
        s0 = max(0, int(start * sr))
        s1 = min(len(audio.samples), max(s0 + 1, int(end * sr)))
        sub = audio.samples[s0:s1]
        if len(sub) == 0:
            return None

        feats = self.extractor.extract(sub, sr)
        seg = AudioSegmentEvent(
            segment_id=new_event_id(),
            timestamp=start,
            duration=feats.duration,
            vad_ratio=1.0,  # 已是 VAD 检出的语音段
            rms=feats.rms,
            speech_rate=feats.speech_rate,
            labels=[],
        )
        return self.rule.evaluate(
            features=feats,
            vad_ratio=vad_ratio,
            timestamp=seg.timestamp,
            segment_id=seg.segment_id,
        )

    @staticmethod
    def _noop() -> np.ndarray:
        return np.array([])

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
from ..core.config import TIER1_TRIGGERS
from .detector import AudioDetector
from .event import AudioPerceptionEvent, AudioSegmentEvent, new_event_id
from .features import AudioFeatureExtractor
from .rule import AudioRule
from .source import AudioSource, FileAudioSource, LoadedAudio
from .tagging import AcousticTagger, AudioTag, build_tagger, tier1_trigger_of

log = get_logger(__name__)

# Tier1 触发策略（白名单单一来源见 ``core.config.TIER1_TRIGGERS``，评审 2.6）：
# - "segment"：对每段 VAD 语音段都跑 Tier1（默认；VAD 段本质已是语音，最贴合"由 Tier0 触发"）
# - "perception"：仅当 Tier0 产出感知事件时才跑（更省算力，但漏掉"无 Tier0 判定但有标签"的段）


class AudioPipeline:
    """音频感知管道（可插拔组件组装）。"""

    def __init__(
        self,
        source: AudioSource,
        detector: AudioDetector | None = None,
        extractor: AudioFeatureExtractor | None = None,
        rule: AudioRule | None = None,
        tagger: AcousticTagger | None = None,
        tier1_trigger: str = "segment",
    ) -> None:
        self.source = source
        self.detector = detector or AudioDetector()
        self.extractor = extractor or AudioFeatureExtractor()
        self.rule = rule or AudioRule()
        self.tagger = tagger
        if tier1_trigger not in TIER1_TRIGGERS:
            raise ValueError(
                f"tier1_trigger 必须是 {TIER1_TRIGGERS}，收到 {tier1_trigger!r}"
            )
        self.tier1_trigger = tier1_trigger

    @classmethod
    def from_defaults(
        cls,
        path: str | Path,
        tagger: AcousticTagger | None = None,
        tier1_trigger: str = "segment",
    ) -> AudioPipeline:
        """用默认组件构建一个针对文件的管道（测试 / 回放入口）。"""
        return cls(
            source=FileAudioSource(path),
            tagger=tagger,
            tier1_trigger=tier1_trigger,
        )

    @classmethod
    def from_audio_config(
        cls, audio_cfg: object, source: AudioSource
    ) -> AudioPipeline:
        """从音频配置（``AudioConfig``）构建管道，含 Tier1 标签器（config-gated）。

        ``audio_cfg`` 为 duck-typed，接受 ``core.config.AudioConfig``；标签器由
        ``build_tagger`` 按 enabled / model_path 决定（None / YamNet / Stub 回退）。
        """
        tagger = build_tagger(getattr(audio_cfg, "tier1", None))
        trigger = tier1_trigger_of(audio_cfg)
        return cls(source=source, tagger=tagger, tier1_trigger=trigger)

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
        ev = self.rule.evaluate(
            features=feats,
            vad_ratio=vad_ratio,
            timestamp=seg.timestamp,
            segment_id=seg.segment_id,
        )

        # Tier1 触发式拉起（ADR-0026 §3：由 Tier0 触发，避免常驻）。
        # 失败隔离：Tier1 异常降级为"无 Tier1 标签"，不影响 Tier0 事件与主管道。
        # ``_run_tier1`` 返回 list[AudioTag]（保留 score，评审 1.5）。
        t1 = self._run_tier1(sub, sr, ev is not None)
        if t1:
            t1_labels = {t.label for t in t1}
            # labels 契约：去重 + 字母序排序的**集合**（顺序不具语义，评审 2.4）。
            seg.labels = sorted(set(seg.labels) | t1_labels)
            if ev is not None:
                ev.labels = sorted(set(ev.labels) | t1_labels)
                # seg.labels 与 ev.labels 语义层不同，不要求逐元素相等（评审 2.5）：
                #   seg.labels 仅承载 Tier1 声学标签（无 Tier1 时为空）；
                #   ev.labels 承载 Tier0 规则标签 + Tier1 声学标签的并集。
                # 保留 score 信息供下游阈值/审计（评审 1.5）。
                ev.scored_labels = sorted(t1, key=lambda x: x.score, reverse=True)
        return ev

    def tier1_should_run(self, has_perception: bool) -> bool:
        """Tier1 触发判定（公开，供测试与未来融合层复用）。

        - ``segment``：始终对 VAD 语音段运行（默认；VAD 段本质已是语音）。
        - ``perception``：仅当 Tier0 已产出感知事件时运行（更省算力）。
        - 无 tagger（Tier1 关闭）→ 永不运行。
        """
        if self.tagger is None:
            return False
        if self.tier1_trigger == "perception":
            return has_perception
        return True

    def _run_tier1(self, sub: np.ndarray, sr: int, has_perception: bool) -> list[AudioTag]:
        """运行 Tier1 标签器，返回 ``list[AudioTag]``（保留 score；空列表=无 / 关闭 / 失败）。"""
        if not self.tier1_should_run(has_perception):
            return []
        try:
            return list(self.tagger.tag(sub, sr))
        except Exception as exc:  # noqa: BLE001  # Tier1 失败降级，不崩主管道
            log.warning("audio.pipeline.tier1_failed", error=str(exc))
            return []

    @staticmethod
    def _noop() -> np.ndarray:
        return np.array([])

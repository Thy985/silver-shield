"""音频适配器（ADR-0026 §5.1 · Integration Layer）。

> **职责**：``AudioPerceptionEvent → RiskSignal(source=AUDIO, category=COMMUNICATION)``；
> ``AudioEvidenceCollector → EvidenceItem(modality=AUDIO)``。
>
> **边界铁律（冻结）**：``AudioRule`` 绝不直接产 ``RiskSignal``；所有"音频 → ``RiskSignal``"
> 翻译必经本适配器。``DecisionPolicy`` 零改动（它只消费既有 ``RiskSignal``，不感知来源是视频还是音频）。
>
> ``EvidenceItem`` / ``EvidenceModality`` 已在 ADR-0027 Slice A 落地于 ``core/event.py``，
> 音频证据直接构造 ``EvidenceItem(modality=AUDIO)``，不再依赖旧 ``EvidenceRef``（已删除）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..analysis.risk_signal import (
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)
from ..audio.event import AudioPerceptionEvent
from ..core.event import EvidenceItem, EvidenceModality, RetentionTier


def adapt_audio_event(
    event: AudioPerceptionEvent,
    device_id: str,
    subject_id: str | UUID,
    *,
    track_id: int | None = None,
    visitor_instance_id: str | UUID | None = None,
    severity_hint: float | None = None,
) -> RiskSignal:
    """把音频感知事件翻译为 RiskSignal（source=AUDIO, category=COMMUNICATION）。

    参数：
    - ``event``：音频管道产出的 ``AudioPerceptionEvent``
    - ``device_id``：设备 ID（透传）
    - ``subject_id``：风险主体 ID（音频当前未绑定具体访客时，可由调用方传入 visitor 或生成）
    - ``severity_hint``：可选严重度；缺省用 ``event.score``

    返回：``RiskSignal(source=AUDIO, category=COMMUNICATION, transition=RAISED)``
    """
    if not isinstance(event, AudioPerceptionEvent):
        raise TypeError(f"event 必须是 AudioPerceptionEvent，收到 {type(event).__name__}")

    subject = str(subject_id)
    visitor = str(visitor_instance_id) if visitor_instance_id is not None else (
        subject if isinstance(subject_id, UUID) or _looks_uuid(subject) else None
    )

    max_tier1_score = max((t.score for t in event.scored_labels), default=0.0)
    features: dict[str, Any] = {
        "audio_kind": event.kind.value,
        "audio_score": round(event.score, 4),
        "audio_confidence": round(event.confidence, 4),
        "labels": list(event.labels),
        "source_segment_ids": list(event.source_segment_ids),
        # Tier1 score 透传（评审 1.5）：下游可据 audio_tier1_max_score 设阈值告警
        "audio_tier1_max_score": round(float(max_tier1_score), 4),
        "audio_tier1_scored_labels": [
            {"label": t.label, "score": round(t.score, 4)} for t in event.scored_labels
        ],
    }

    return RiskSignal(
        signal_id=str(uuid4()),
        subject_type=SubjectType.VISITOR,
        subject_id=subject,
        category=SignalCategory.COMMUNICATION,
        source=SourceModality.AUDIO,
        transition=SignalTransition.RAISED,
        features=features,
        track_id=track_id,
        visitor_instance_id=visitor,
        severity_hint=event.score if severity_hint is None else severity_hint,
        created_at=event.created_at,
    )


class AudioAdapter:
    """音频 → RiskSignal 适配器（封装 ``adapt_audio_event``，便于依赖注入 / 测试）。"""

    def to_risk_signal(
        self,
        event: AudioPerceptionEvent,
        device_id: str,
        subject_id: str | UUID,
        **kwargs: Any,
    ) -> RiskSignal:
        return adapt_audio_event(event, device_id, subject_id, **kwargs)


class AudioEvidenceCollector:
    """音频证据采集（ADR-0026 §5.1）。

    产出与音频感知事件对应的独立 ``EvidenceItem``（ADR-0027 Slice A），
    ``modality=AUDIO``，``kind`` 以 ``audio_segment`` / ``audio_clip`` 标识；
    episode 侧仅以 ``evidence_id`` 引用（ADR-0024 I2 单调性）。
    """

    def collect_segment(self, event: AudioPerceptionEvent, uri: str) -> EvidenceItem:
        """采集分段级证据对象。"""
        return EvidenceItem(
            evidence_id=str(uuid4()),
            modality=EvidenceModality.AUDIO,
            kind="audio_segment",
            uri=uri,
            captured_at=datetime.fromtimestamp(event.timestamp, tz=UTC),
            retention_tier=RetentionTier.SHORT,
        )

    def collect_clip(self, event: AudioPerceptionEvent, uri: str) -> EvidenceItem:
        """采集片段级证据对象（高风险的音频片段，本地留存 + 自动过期）。"""
        return EvidenceItem(
            evidence_id=str(uuid4()),
            modality=EvidenceModality.AUDIO,
            kind="audio_clip",
            uri=uri,
            captured_at=datetime.fromtimestamp(event.timestamp, tz=UTC),
            retention_tier=RetentionTier.SHORT,
        )


def _looks_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False

"""音频适配器（ADR-0026 §5.1 · Integration Layer）。

> **职责**：``AudioPerceptionEvent → RiskSignal(source=AUDIO, category=COMMUNICATION)``；
> ``AudioEvidenceCollector → EvidenceRef(modality=AUDIO)``。
>
> **边界铁律（冻结）**：``AudioRule`` 绝不直接产 ``RiskSignal``；所有"音频 → ``RiskSignal``"
> 翻译必经本适配器。``DecisionPolicy`` 零改动（它只消费既有 ``RiskSignal``，不感知来源是视频还是音频）。
>
> 注：``EvidenceModality.AUDIO`` 枚举尚未在仓库定义（ADR-0026 §5.2 提及但未落地），
> 此处复用通用 ``EvidenceRef``（kind 以 ``audio_*`` 前缀标识），待证据枚举落地后平滑替换。
"""

from __future__ import annotations

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
from ..core.event import EvidenceRef


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

    features: dict[str, Any] = {
        "audio_kind": event.kind.value,
        "audio_score": round(event.score, 4),
        "audio_confidence": round(event.confidence, 4),
        "labels": list(event.labels),
        "source_segment_ids": list(event.source_segment_ids),
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

    产出与音频感知事件对应的证据引用。当前复用通用 ``EvidenceRef``，
    ``kind`` 以 ``audio_segment`` / ``audio_clip`` 标识（待 ``EvidenceModality.AUDIO`` 枚举落地替换）。
    """

    def collect_segment(self, event: AudioPerceptionEvent, uri: str) -> EvidenceRef:
        """采集分段级证据引用。"""
        return EvidenceRef(kind="audio_segment", uri=uri, timestamp=event.timestamp)

    def collect_clip(self, event: AudioPerceptionEvent, uri: str) -> EvidenceRef:
        """采集片段级证据引用（高风险的音频片段，本地留存 + 自动过期）。"""
        return EvidenceRef(kind="audio_clip", uri=uri, timestamp=event.timestamp)


def _looks_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False

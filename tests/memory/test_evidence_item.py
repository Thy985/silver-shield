"""EvidenceItem / EvidenceModality / RetentionTier 契约测试（ADR-0027 Slice A）。

> 统一证据模型落地于 ``core/event.py``，替代旧双 ``EvidenceRef``（core/event.py 与
> memory/records.py 各一份）。``EpisodicRecord.evidence_refs`` 仅持 ``evidence_id``
> 字符串，独立 ``EvidenceItem`` 以 ID 解析（ADR-0024 I2 单调性）。
>
> 覆盖：
> - D7：``EvidenceModality`` 枚举闭合 {VISION, AUDIO, IDENTITY}（继承 ADR-0022，与
>   ``SourceModality`` 是不同限界上下文，值集不共享）
> - D9：``RetentionTier`` 枚举闭合 {SHORT, MEDIUM, LONG}
> - D2：``EvidenceItem`` 不可变事实对象，``__post_init__`` 强制校验
> - D8：``to_dict`` / ``from_dict`` 字段无损往返；``captured_at`` / ``expires_at`` 时区保持
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from home_perception.core.event import (
    EvidenceItem,
    EvidenceModality,
    RetentionTier,
)


# --------------------------------------------------------------------------
# D7：EvidenceModality 枚举闭合
# --------------------------------------------------------------------------
class TestEvidenceModality:
    def test_values_closed(self):
        assert {m.value for m in EvidenceModality} == {"vision", "audio", "identity"}

    def test_no_sensor_or_unknown(self):
        # 与 SourceModality 是不同限界上下文，不共享值集（ADR-0021 §3.3 命名消歧）
        with pytest.raises(ValueError):
            EvidenceModality("sensor")
        with pytest.raises(ValueError):
            EvidenceModality("unknown")


# --------------------------------------------------------------------------
# D9：RetentionTier 枚举闭合
# --------------------------------------------------------------------------
class TestRetentionTier:
    def test_values_closed(self):
        assert {t.value for t in RetentionTier} == {"short", "medium", "long"}


# --------------------------------------------------------------------------
# D2 / D8：EvidenceItem
# --------------------------------------------------------------------------
def _tz_aware(y=2026, mo=7, d=28, h=18, mi=35):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


class TestEvidenceItem:
    def test_construct_minimal(self):
        e = EvidenceItem(
            evidence_id="ev-001",
            modality=EvidenceModality.AUDIO,
            kind="audio_segment",
        )
        assert e.evidence_id == "ev-001"
        assert e.modality is EvidenceModality.AUDIO
        assert e.kind == "audio_segment"
        assert e.uri is None
        assert e.confidence is None
        assert e.metadata == {}
        assert e.retention_tier is RetentionTier.SHORT
        assert e.expires_at is None
        assert e.captured_at.tzinfo is not None  # UTC 默认注入

    def test_roundtrip(self):
        e = EvidenceItem(
            evidence_id="ev-002",
            modality=EvidenceModality.VISION,
            kind="snapshot",
            uri="data/evidence/shot-002.jpg",
            captured_at=_tz_aware(2026, 7, 28, 18, 40),
            confidence=0.87,
            metadata={"frame_id": "f-9"},
            retention_tier=RetentionTier.MEDIUM,
            expires_at=_tz_aware(2026, 8, 27, 18, 40),
        )
        revived = EvidenceItem.from_dict(e.to_dict())
        assert e == revived
        # 时区保持（traceability 前置条件 I3）
        assert revived.captured_at.tzinfo is not None
        assert revived.expires_at.tzinfo is not None
        assert revived.confidence == 0.87
        assert revived.metadata == {"frame_id": "f-9"}

    def test_evidence_id_empty_rejected(self):
        with pytest.raises(ValueError, match="evidence_id 不能为空"):
            EvidenceItem(evidence_id="", modality=EvidenceModality.AUDIO, kind="audio_segment")

    def test_modality_not_enum_rejected(self):
        with pytest.raises(TypeError, match="EvidenceModality"):
            EvidenceItem(evidence_id="ev", modality="audio", kind="audio_segment")

    def test_kind_empty_rejected(self):
        with pytest.raises(ValueError, match="kind 不能为空"):
            EvidenceItem(evidence_id="ev", modality=EvidenceModality.AUDIO, kind="")

    def test_naive_captured_at_rejected(self):
        naive = datetime(2026, 7, 28, 18, 30, 0)  # noqa: DTZ001 (naive test input)
        with pytest.raises(ValueError, match="timezone-aware"):
            EvidenceItem(
                evidence_id="ev",
                modality=EvidenceModality.AUDIO,
                kind="audio_segment",
                captured_at=naive,
            )

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            EvidenceItem(
                evidence_id="ev",
                modality=EvidenceModality.AUDIO,
                kind="audio_segment",
                confidence=1.5,
            )

    def test_metadata_not_dict_rejected(self):
        with pytest.raises(TypeError, match="dict"):
            EvidenceItem(
                evidence_id="ev",
                modality=EvidenceModality.AUDIO,
                kind="audio_segment",
                metadata=["x"],
            )

    def test_retention_tier_not_enum_rejected(self):
        with pytest.raises(TypeError, match="RetentionTier"):
            EvidenceItem(
                evidence_id="ev",
                modality=EvidenceModality.AUDIO,
                kind="audio_segment",
                retention_tier="short",
            )

    def test_confidence_coerced_to_float(self):
        e = EvidenceItem(
            evidence_id="ev",
            modality=EvidenceModality.AUDIO,
            kind="audio_segment",
            confidence=1,
        )
        assert e.confidence == 1.0 and isinstance(e.confidence, float)

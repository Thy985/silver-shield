"""ADR-0027 Slice E：Audio → Memory 契约测试（schema evolution + 枚举闭合 + 身份负例）。

> 本模块是 Slice E 的**契约守门**层：锁定 "Audio → Memory" 的稳定契约，使后续切片
> （C CrossModalLink / D Consumer audio-aware）改动时，破坏契约会被本套测试立刻捕获。
>
> 覆盖（对应 ADR-0027 §6.1 验收清单中**已实现**的部分）：
> - **D8 Schema Evolution**：`EpisodicRecord.from_dict` 同时接受 v1（无 `modalities` /
>   `audio_session_id`）/ v2 两种形状；`EPISODIC_RECORD_DICT_KEYS` 各版本闭合且显式；
>   旧 `modalities` 缺失 → `[]`（不引入 UNKNOWN）、旧 `evidence_refs` 残留 dict 形式
>   coerce 为 ID 字符串列表、`confidence` 缺失 → `None`（绝不伪造 1.0）。
> - **D7 枚举闭合**：`EvidenceModality` 值集恰为 `{VISION, AUDIO, IDENTITY}`；v1
>   旧 `IDENTITY` 证据读为 `IDENTITY` 不变（向后兼容）。
> - **D4 身份负例**：v1 旧数据 `visitor_instance_id=None` 且缺 `audio_session_id` →
>   拒绝写入（I4 溯源链必填其一）；纯音频 episode 经序列化往返**绝不反填** visitor。
> - **EvidenceItem 序列化向后兼容**：`from_dict` 缺 `confidence` → `None`、缺
>   `retention_tier` / `expires_at` → `SHORT` / `None`。
>
> **明确 deferred（§6.1 中依赖未实现组件的部分，非本切片范围）**：
> - **D5 悬空引用**：`CrossModalLink.episode_ids` / `supporting_evidence_ids` 含未知
>   id 的拒绝/隔离 —— 依赖切片 C 的 `CrossModalLink`，本切片不实现，故不在此测。
> - **D9 隐私路径**（24h/30d/LONG 不删/失败重试/幂等/文件已不存在/用户擦除）：依赖
>   Audio Evidence 留存执行器 + 可变 `EvidenceAssetState`（尚未实现），本切片不实现，
>   故不在此测。二者将在各自的后续切片中补齐验收。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.core.event import EvidenceItem, EvidenceModality, RetentionTier
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.records import (
    EPISODIC_RECORD_DICT_KEYS,
    EpisodicRecord,
    records_equal,
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _utc(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _make_visitor(visitor_id=None, enter=None, leave=None, duration=180.0):
    visitor_id = visitor_id or uuid.uuid4()
    enter = enter or _utc(2026, 7, 28, 14, 32)
    leave = leave or _utc(2026, 7, 28, 14, 35)
    return VisitorEvent(
        visitor_id=visitor_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration,
    )


def _make_audio_evidence(audio_kind, evidence_id, captured_at):
    """构造 AUDIO EvidenceItem（模拟 AudioEvidenceCollector 产出，ADR-0027 Slice A/B）。"""
    return EvidenceItem(
        evidence_id=evidence_id,
        modality=EvidenceModality.AUDIO,
        kind="audio_clip",
        uri=f"data/evidence/{evidence_id}.wav",
        captured_at=captured_at,
        metadata={"audio_kind": audio_kind, "audio_score": 0.9},
        retention_tier=RetentionTier.SHORT,
    )


def _base_v1_episode_dict(
    record_id: str = "ep-v1visitor",
    visitor_instance_id: str = "v1-visitor",
) -> dict:
    """v1 形状（音频接入前）：不含 `modalities` / `audio_session_id`，schema_version=1。

    v1 旧 episode 恒有 `visitor_instance_id`（ADR-0027 D8：v1 旧记录自动满足 I4）。
    """
    return {
        "record_id": record_id,
        "visitor_instance_id": visitor_instance_id,
        "person_identity_id": None,
        "enter_time": "2026-07-28T18:32:00+00:00",
        "leave_time": "2026-07-28T18:45:00+00:00",
        "duration_seconds": 780.0,
        "risk_level": None,
        "recommended_action": None,
        "reason_summary": [],
        "actions": [],
        "evidence_refs": [],
        "source_event_ids": ["ev-1"],
        "summary": "18:32-18:45 访问（停留 13 分钟），未触发风险。",
        "model_version": "ep-builder-v1",
        "memory_status": "active",
        "corrections": [],
        "schema_version": 1,
        "created_at": "2026-07-28T18:45:10+00:00",
    }


def _v2_episode_dict() -> dict:
    """v2 形状（本 ADR 后）：含 `modalities` / `audio_session_id`，schema_version=2。"""
    d = _base_v1_episode_dict(record_id="ep-v2composite", visitor_instance_id="v2-visitor")
    d["modalities"] = ["vision", "audio"]
    d["audio_session_id"] = "audio_session_001"
    d["evidence_refs"] = ["ev-audio-001"]
    d["schema_version"] = 2
    return d


# ---------------------------------------------------------------------------
# D8：EpisodicRecord Schema Evolution（v1/v2 双形状）
# ---------------------------------------------------------------------------
class TestEpisodicSchemaEvolution:
    def test_v1_dict_missing_audio_fields_defaults(self):
        """v1 旧 episode（无 modalities/audio_session_id）→ 默认 [] / None / schema_version=1，record_id 不变。"""
        rec = EpisodicRecord.from_dict(_base_v1_episode_dict())
        assert rec.modalities == []
        assert rec.audio_session_id is None
        assert rec.schema_version == 1
        assert rec.record_id == "ep-v1visitor"
        # v1 旧数据仍满足 I4（有 visitor_instance_id）
        assert rec.visitor_instance_id == "v1-visitor"

    def test_v2_dict_audio_fields_preserved(self):
        """v2 新 episode（含 modalities/audio_session_id）→ 字段无损保留。"""
        rec = EpisodicRecord.from_dict(_v2_episode_dict())
        assert rec.modalities == [EvidenceModality.VISION, EvidenceModality.AUDIO]
        assert rec.audio_session_id == "audio_session_001"
        assert rec.schema_version == 2
        assert rec.evidence_refs == ["ev-audio-001"]

    def test_dict_keys_closure_v1_and_v2(self):
        """EPISODIC_RECORD_DICT_KEYS 闭合：`to_dict` 恒发射全集（含 modalities /
        audio_session_id）；v1 输入（缺两键）也被 `from_dict` 接受，且序列化后仍含
        全集——向后兼容在**读取端**，不在写出端（schema-uniform 输出）。"""
        full = set(EPISODIC_RECORD_DICT_KEYS)
        assert "modalities" in full and "audio_session_id" in full

        # v1 输入（缺 modalities/audio_session_id）→ from_dict 接受，默认值补齐
        rec_v1 = EpisodicRecord.from_dict(_base_v1_episode_dict())
        assert rec_v1.modalities == []
        assert rec_v1.audio_session_id is None
        # 序列化仍发射全集（不因为来源是 v1 而省略键）
        assert set(rec_v1.to_dict().keys()) == full

        # v2 输入（含两键）→ from_dict 接受，序列化同样发射全集
        rec_v2 = EpisodicRecord.from_dict(_v2_episode_dict())
        assert set(rec_v2.to_dict().keys()) == full

    def test_roundtrip_identity_preserves_record_id(self):
        """to_dict → from_dict 内容一致（records_equal 忽略 created_at），record_id / 模态 / 音频会话不变。"""
        builder = DefaultEpisodeBuilder()
        visitor = _make_visitor(enter=_utc(2026, 7, 28, 18, 32), leave=_utc(2026, 7, 28, 18, 44))
        audio_ev = _make_audio_evidence("telephone", "ev-audio-001", _utc(2026, 7, 28, 18, 41))
        rec = builder.project_episode(
            visitor, warnings=[], actions=[], evidence=[audio_ev], audio_session_id="audio_session_001"
        )
        assert rec is not None
        restored = EpisodicRecord.from_dict(rec.to_dict())
        assert records_equal(rec, restored)
        assert restored.record_id == rec.record_id
        assert restored.modalities == rec.modalities
        assert restored.audio_session_id == rec.audio_session_id

    def test_missing_modalities_is_empty_not_unknown(self):
        """旧 episode 缺 modalities → `[]`（D7/D8：不引入 UNKNOWN 哨兵）。"""
        rec = EpisodicRecord.from_dict(_base_v1_episode_dict())
        assert rec.modalities == []
        # EvidenceModality 本身没有 UNKNOWN 值（枚举闭合，ADR-0027 D7）
        assert {m.value for m in EvidenceModality} == {"vision", "audio", "identity"}

    def test_v1_legacy_evidence_refs_dict_coerced_to_ids(self):
        """v1 残留 `evidence_refs` 为 dict 列表（旧 EvidenceRef.to_dict）→ coerce 为 ID 字符串列表。"""
        d = _base_v1_episode_dict()
        d["evidence_refs"] = [
            {"evidence_id": "ev_x", "modality": "audio", "captured_at": "2026-07-28T18:41:00+00:00"},
            {"evidence_id": "ev_y"},
        ]
        rec = EpisodicRecord.from_dict(d)
        # 仅提取非空字符串 evidence_id；不构造 EvidenceItem、不伪造 confidence
        assert rec.evidence_refs == ["ev_x", "ev_y"]

    def test_v1_legacy_evidence_refs_empty_id_rejected(self):
        """v1 残留 dict 的 evidence_id 为空 → 显式拒绝（ADR-0024 I2 单调性：非法 ID 不静默进 v2）。"""
        d = _base_v1_episode_dict()
        d["evidence_refs"] = [{"evidence_id": ""}]
        with pytest.raises(ValueError, match="evidence_refs"):
            EpisodicRecord.from_dict(d)

    def test_v1_visitor_none_no_audio_rejected(self):
        """v1 旧数据 visitor_instance_id=None 且缺 audio_session_id → 拒绝（I4 溯源链必填其一）。"""
        d = _base_v1_episode_dict(visitor_instance_id=None)
        # 显式删除音频会话键，模拟纯视觉 v1 却丢失身份的损坏数据
        d.pop("audio_session_id", None)
        with pytest.raises(ValueError, match="至少其一必填"):
            EpisodicRecord.from_dict(d)


# ---------------------------------------------------------------------------
# D8：EvidenceItem 序列化向后兼容
# ---------------------------------------------------------------------------
class TestEvidenceItemBackwardCompat:
    def _minimal_dict(self):
        return {
            "evidence_id": "ev-bc-001",
            "modality": "audio",
            "kind": "audio_segment",
            "captured_at": "2026-07-28T18:40:00+00:00",
        }

    def test_from_dict_without_confidence_is_none_not_one(self):
        """旧证据缺 confidence → None（D8：绝不伪造 1.0）。"""
        e = EvidenceItem.from_dict(self._minimal_dict())
        assert e.confidence is None

    def test_from_dict_without_retention_defaults(self):
        """旧证据缺 retention_tier / expires_at → SHORT / None（D9 默认值）。"""
        e = EvidenceItem.from_dict(self._minimal_dict())
        assert e.retention_tier is RetentionTier.SHORT
        assert e.expires_at is None


# ---------------------------------------------------------------------------
# D7：EvidenceModality 枚举闭合 / IDENTITY 向后兼容
# ---------------------------------------------------------------------------
class TestEvidenceModalityContract:
    def test_identity_evidence_reads_as_identity(self):
        """v1 旧 IDENTITY 证据读为 IDENTITY 不变（向后兼容，ADR-0027 D7）。"""
        e = EvidenceItem(
            evidence_id="ev-id-001",
            modality=EvidenceModality.IDENTITY,
            kind="face_snapshot",
        )
        revived = EvidenceItem.from_dict(e.to_dict())
        assert revived.modality is EvidenceModality.IDENTITY


# ---------------------------------------------------------------------------
# D4：纯音频 episode 经序列化往返绝不反填 visitor
# ---------------------------------------------------------------------------
class TestAudioOnlyNoVisitorBackfill:
    def test_audio_only_roundtrip_no_visitor_backfill(self):
        """纯音频 episode：visitor_instance_id=None 经 to_dict→from_dict 仍 None（D4 禁止反填）。"""
        builder = DefaultEpisodeBuilder()
        audio_warning = WarningEvent(
            elder_id="elder-001",
            device_id="dev-001",
            risk_level="MEDIUM",
            recommended_action="MONITOR",
            trigger_events=[
                {"event_id": "audio_subject:abnormal_audio", "event_type": "abnormal_audio",
                 "score": 0.9, "timestamp": _utc(2026, 7, 28, 23, 5).isoformat()}
            ],
            reason_summary=["异常通话"],
            warning_id=uuid.uuid4(),
            created_at=_utc(2026, 7, 28, 23, 5),
        )
        audio_ev = _make_audio_evidence("crying", "ev-audio-002", _utc(2026, 7, 28, 23, 4))
        rec = builder.project_episode(
            None,
            warnings=[audio_warning],
            actions=[],
            evidence=[audio_ev],
            audio_session_id="audio_session_002",
        )
        assert rec is not None
        assert rec.visitor_instance_id is None
        restored = EpisodicRecord.from_dict(rec.to_dict())
        # 序列化往返后仍是匿名，未引入任何 visitor 归属
        assert restored.visitor_instance_id is None
        assert restored.audio_session_id == "audio_session_002"
        assert records_equal(rec, restored)

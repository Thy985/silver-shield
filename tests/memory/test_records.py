"""Memory records 契约测试（Slice 1 · Stage A）。

> 对齐 DESIGN-memory-pipeline §8.3 验收标准 + ADR-0024 §3.2.3 不变量。
> 本测试 **torch-free**，可进 CI 每 PR 合约子集。

覆盖：
- 对象创建：dataclass 可构造
- 序列化往返：to_dict → from_dict 字段无损
- MemoryStatus 默认值：新建 record 默认 ACTIVE
- schema_version 默认值：新建 record 默认 1
- model_version 必填：EpisodicRecord / SemanticAggregate 不能为空
- record_id 前缀约束：st- / ep- / sem- 前缀校验（I1）
- source_event_ids 不能为空（I4）
- created_at UTC 校验（I3 前置）
- 枚举闭合：MemoryStatus(4) 值不漂移
- 字段闭合：to_dict 键集合恒定
- v1 约束：EpisodicRecord.person_identity_id 恒 None
- 辅助类型：ActionSummary 校验
- evidence_refs：ADR-0027 Slice A 起为 evidence_id 字符串列表（独立 EvidenceItem 以 ID 解析）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from home_perception.memory.records import (
    EPISODIC_RECORD_DICT_KEYS,
    MEMORY_STATUS_VALUES,
    RECORD_ID_PREFIXES,
    SEMANTIC_AGGREGATE_DICT_KEYS,
    SHORT_TERM_RECORD_DICT_KEYS,
    ActionSummary,
    EpisodicRecord,
    MemoryStatus,
    RecordIdPrefix,
    SemanticAggregate,
    ShortTermRecord,
    records_equal,
)

# ============================================================================
# Fixtures
# ============================================================================

T0 = datetime(2026, 7, 28, 18, 30, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 28, 18, 35, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 28, 18, 44, 0, tzinfo=UTC)


def _make_short_term(**overrides) -> ShortTermRecord:
    defaults = {
        "record_id": "st-visitor-001",
        "visitor_instance_id": "visitor-001",
        "phase": "active_risk",
        "first_seen": T0,
        "last_seen_at": T1,
        "source_event_ids": ["signal-001"],
        "raised_signal_id": "signal-001",
        "raised_at": T1,
    }
    defaults.update(overrides)
    return ShortTermRecord(**defaults)


def _make_episodic(**overrides) -> EpisodicRecord:
    defaults = {
        "record_id": "ep-visitor-event-001",
        "visitor_instance_id": "visitor-001",
        "enter_time": T1,
        "leave_time": T2,
        "duration_seconds": 540.0,
        "source_event_ids": ["visitor-event-001", "warning-001"],
        "summary": "18:35-18:44 访问（停留 9 分钟），风险等级 HIGH，已通知家属。",
        "model_version": "ep-builder-v1",
        "risk_level": "HIGH",
        "recommended_action": "NOTIFY_FAMILY",
        "reason_summary": ["abnormal_dwell"],
    }
    defaults.update(overrides)
    return EpisodicRecord(**defaults)


def _make_semantic(**overrides) -> SemanticAggregate:
    defaults = {
        "aggregate_id": "sem-env-2026-07",
        "dimension": "environment",
        "period_key": "2026-07",
        "episode_count": 42,
        "statistics": {"risk_distribution": {"LOW": 30, "MEDIUM": 8, "HIGH": 4}},
        "confidence": 0.85,
        "source_episode_ids": ["ep-001", "ep-002", "ep-003"],
        "model_version": "env-aggregator-v1",
    }
    defaults.update(overrides)
    return SemanticAggregate(**defaults)


# ============================================================================
# 枚举闭合
# ============================================================================


class TestMemoryStatusEnum:
    def test_enum_values_closed(self):
        """MemoryStatus 枚举值不漂移（4 个值，契约测试基线）。"""
        assert MEMORY_STATUS_VALUES == ("active", "deprecated", "archived", "invalid")

    def test_record_id_prefixes_closed(self):
        """record_id 前缀白名单不漂移。"""
        assert RECORD_ID_PREFIXES == ("st-", "ep-", "sem-")

    def test_record_id_prefix_enum_values(self):
        assert RecordIdPrefix.SHORT_TERM.value == "st-"
        assert RecordIdPrefix.EPISODIC.value == "ep-"
        assert RecordIdPrefix.SEMANTIC.value == "sem-"


# ============================================================================
# ShortTermRecord
# ============================================================================


class TestShortTermRecord:
    def test_construct_minimal(self):
        """对象创建：必填字段构造成功。"""
        rec = _make_short_term()
        assert rec.record_id == "st-visitor-001"
        assert rec.phase == "active_risk"
        assert rec.raised_signal_id == "signal-001"

    def test_default_memory_status_active(self):
        """新建 record 默认 ACTIVE。"""
        rec = _make_short_term()
        assert rec.memory_status is MemoryStatus.ACTIVE

    def test_default_schema_version(self):
        """新建 record 默认 schema_version=1。"""
        rec = _make_short_term()
        assert rec.schema_version == 1

    def test_default_created_at_utc(self):
        """created_at 默认 UTC timezone-aware。"""
        rec = _make_short_term()
        # 注意：变量名用 has_tz，避免与 home_perception.common.timeutil.require_utc
        # 函数同名造成语义混淆（本测试文件未 import 该函数，但命名应一致避歧义）。
        has_tz = rec.created_at.tzinfo is not None
        assert has_tz
        assert rec.created_at.utcoffset() == UTC.utcoffset(None)

    def test_record_id_prefix_validation(self):
        """record_id 必须以 st- 开头（I1）。"""
        with pytest.raises(ValueError, match="record_id 必须以 'st-' 开头"):
            _make_short_term(record_id="ep-visitor-001")

    def test_record_id_empty_rejected(self):
        with pytest.raises(ValueError, match="record_id 不能为空"):
            _make_short_term(record_id="")

    def test_source_event_ids_empty_rejected(self):
        """source_event_ids 不能为空（I4）。"""
        with pytest.raises(ValueError, match="source_event_ids 不能为空"):
            _make_short_term(source_event_ids=[])

    def test_source_event_ids_with_empty_string_rejected(self):
        with pytest.raises(ValueError, match="必须是非空 str"):
            _make_short_term(source_event_ids=[""])

    def test_phase_closed(self):
        """phase 必须是 none / active_risk。"""
        with pytest.raises(ValueError, match="phase 必须是"):
            _make_short_term(phase="unknown")

    def test_active_risk_requires_raised_signal_id(self):
        """phase=active_risk 时 raised_signal_id 必填。"""
        with pytest.raises(ValueError, match="raised_signal_id 必填"):
            _make_short_term(phase="active_risk", raised_signal_id=None)

    def test_phase_none_allows_no_raised_signal_id(self):
        """phase=none 时 raised_signal_id 可为 None。"""
        rec = _make_short_term(phase="none", raised_signal_id=None, raised_at=None)
        assert rec.raised_signal_id is None

    def test_last_seen_at_before_first_seen_rejected(self):
        """last_seen_at 不能早于 first_seen（因果性）。"""
        with pytest.raises(ValueError, match="不能早于 first_seen"):
            _make_short_term(first_seen=T1, last_seen_at=T0)

    def test_naive_datetime_rejected(self):
        """naive datetime 拒绝（UTC 校验）。"""
        naive = datetime(2026, 7, 28, 18, 30, 0)  # noqa: DTZ001 (naive test)
        with pytest.raises(ValueError, match="timezone-aware"):
            _make_short_term(first_seen=naive)

    def test_memory_status_str_coercion(self):
        """memory_status 接受 str 自动归一为枚举。"""
        rec = _make_short_term(memory_status="deprecated")
        assert rec.memory_status is MemoryStatus.DEPRECATED

    def test_memory_status_invalid_str_rejected(self):
        with pytest.raises(ValueError, match="memory_status 必须是 MemoryStatus 之一"):
            _make_short_term(memory_status="unknown")

    def test_dict_keys_closed(self):
        """to_dict 键集合恒定 == SHORT_TERM_RECORD_DICT_KEYS。"""
        rec = _make_short_term()
        keys = set(rec.to_dict().keys())
        assert keys == set(SHORT_TERM_RECORD_DICT_KEYS)

    def test_dict_serialization_format(self):
        """to_dict 中 datetime 转 ISO 字符串，枚举转 value。"""
        rec = _make_short_term()
        d = rec.to_dict()
        assert d["first_seen"] == "2026-07-28T18:30:00+00:00"
        assert d["memory_status"] == "active"
        assert d["raised_at"] == "2026-07-28T18:35:00+00:00"
        assert d["source_event_ids"] == ["signal-001"]

    def test_roundtrip_dict(self):
        """to_dict → from_dict 字段无损。"""
        rec = _make_short_term()
        revived = ShortTermRecord.from_dict(rec.to_dict())
        assert records_equal(rec, revived)

    def test_roundtrip_json(self):
        """to_json → from_json 字段无损。"""
        rec = _make_short_term()
        revived = ShortTermRecord.from_json(rec.to_json())
        assert records_equal(rec, revived)


# ============================================================================
# EpisodicRecord
# ============================================================================


class TestEpisodicRecord:
    def test_construct_minimal(self):
        rec = _make_episodic()
        assert rec.record_id == "ep-visitor-event-001"
        assert rec.risk_level == "HIGH"

    def test_default_memory_status_active(self):
        rec = _make_episodic()
        assert rec.memory_status is MemoryStatus.ACTIVE

    def test_default_schema_version(self):
        rec = _make_episodic()
        assert rec.schema_version == 1

    def test_default_corrections_empty(self):
        rec = _make_episodic()
        assert rec.corrections == []

    def test_record_id_prefix_validation(self):
        """record_id 必须以 ep- 开头（I1）。"""
        with pytest.raises(ValueError, match="record_id 必须以 'ep-' 开头"):
            _make_episodic(record_id="st-visitor-event-001")

    def test_summary_required(self):
        """summary 不能为空（ADR-0024 §3.2.1 强制）。"""
        with pytest.raises(ValueError, match="summary 不能为空"):
            _make_episodic(summary="")

    def test_model_version_required(self):
        """model_version 不能为空。"""
        with pytest.raises(ValueError, match="model_version 不能为空"):
            _make_episodic(model_version="")

    def test_source_event_ids_empty_rejected(self):
        """I4 可解释性。"""
        with pytest.raises(ValueError, match="source_event_ids 不能为空"):
            _make_episodic(source_event_ids=[])

    def test_person_identity_id_must_be_none_v1(self):
        """v1 约束：person_identity_id 恒 None（ADR-0023）。"""
        with pytest.raises(ValueError, match="v1 person_identity_id 必须为 None"):
            _make_episodic(person_identity_id="person-001")

    def test_risk_level_closed(self):
        """risk_level 必须是 LOW/MEDIUM/HIGH 或 None。"""
        with pytest.raises(ValueError, match="risk_level 必须是"):
            _make_episodic(risk_level="CRITICAL")

    def test_risk_level_none_allowed(self):
        """无 Warning 的访问 risk_level=None。"""
        rec = _make_episodic(risk_level=None, recommended_action=None)
        assert rec.risk_level is None

    def test_leave_before_enter_rejected(self):
        with pytest.raises(ValueError, match="不能早于 enter_time"):
            _make_episodic(enter_time=T2, leave_time=T1)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="duration_seconds 必须 >= 0"):
            _make_episodic(duration_seconds=-1.0)

    def test_dict_keys_closed(self):
        rec = _make_episodic()
        keys = set(rec.to_dict().keys())
        assert keys == set(EPISODIC_RECORD_DICT_KEYS)

    def test_dict_serialization_format(self):
        rec = _make_episodic()
        d = rec.to_dict()
        assert d["enter_time"] == "2026-07-28T18:35:00+00:00"
        assert d["memory_status"] == "active"
        assert d["person_identity_id"] is None
        assert d["model_version"] == "ep-builder-v1"
        assert d["corrections"] == []

    def test_roundtrip_dict(self):
        rec = _make_episodic()
        revived = EpisodicRecord.from_dict(rec.to_dict())
        assert records_equal(rec, revived)

    def test_roundtrip_json(self):
        rec = _make_episodic()
        revived = EpisodicRecord.from_json(rec.to_json())
        assert records_equal(rec, revived)

    def test_roundtrip_with_actions_and_evidence(self):
        """嵌套 ActionSummary + evidence_refs（evidence_id 字符串列表）也能往返。"""
        rec = _make_episodic(
            actions=[
                ActionSummary(
                    command_type="NOTIFY_FAMILY",
                    command_id="cmd-001",
                    status="CONFIRMED",
                ),
            ],
            evidence_refs=["ev-001", "ev-002"],
        )
        revived = EpisodicRecord.from_dict(rec.to_dict())
        assert records_equal(rec, revived)
        assert revived.actions[0].command_type == "NOTIFY_FAMILY"
        assert revived.evidence_refs == ["ev-001", "ev-002"]


# ============================================================================
# SemanticAggregate
# ============================================================================


class TestSemanticAggregate:
    def test_construct_minimal(self):
        agg = _make_semantic()
        assert agg.aggregate_id == "sem-env-2026-07"
        assert agg.dimension == "environment"

    def test_default_memory_status_active(self):
        agg = _make_semantic()
        assert agg.memory_status is MemoryStatus.ACTIVE

    def test_aggregate_id_prefix_validation(self):
        """aggregate_id 必须以 sem- 开头（I1）。"""
        with pytest.raises(ValueError, match="record_id 必须以 'sem-' 开头"):
            _make_semantic(aggregate_id="env-2026-07")

    def test_dimension_closed(self):
        """dimension 必须是 environment / identity。"""
        with pytest.raises(ValueError, match="dimension 必须是"):
            _make_semantic(dimension="unknown")

    def test_model_version_required(self):
        with pytest.raises(ValueError, match="model_version 不能为空"):
            _make_semantic(model_version="")

    def test_source_episode_ids_empty_rejected(self):
        """I4 可解释性：聚合也必须可追溯。"""
        with pytest.raises(ValueError, match="source_episode_ids 不能为空"):
            _make_semantic(source_episode_ids=[])

    def test_episode_count_negative_rejected(self):
        with pytest.raises(ValueError, match="episode_count 必须是非负 int"):
            _make_semantic(episode_count=-1)

    def test_confidence_range(self):
        """confidence ∈ [0, 1]。"""
        with pytest.raises(ValueError, match="confidence 必须在"):
            _make_semantic(confidence=1.5)
        with pytest.raises(ValueError, match="confidence 必须在"):
            _make_semantic(confidence=-0.1)

    def test_confidence_boundary_zero_and_one(self):
        agg0 = _make_semantic(confidence=0.0)
        agg1 = _make_semantic(confidence=1.0)
        assert agg0.confidence == 0.0
        assert agg1.confidence == 1.0

    def test_statistics_must_be_dict(self):
        with pytest.raises(TypeError, match="statistics 必须是 dict"):
            _make_semantic(statistics=["not", "a", "dict"])

    def test_dict_keys_closed(self):
        agg = _make_semantic()
        keys = set(agg.to_dict().keys())
        assert keys == set(SEMANTIC_AGGREGATE_DICT_KEYS)

    def test_roundtrip_dict(self):
        agg = _make_semantic()
        revived = SemanticAggregate.from_dict(agg.to_dict())
        assert records_equal(agg, revived)

    def test_roundtrip_json(self):
        agg = _make_semantic()
        revived = SemanticAggregate.from_json(agg.to_json())
        assert records_equal(agg, revived)


# ============================================================================
# ActionSummary
# ============================================================================


class TestActionSummary:
    def test_construct(self):
        a = ActionSummary(
            command_type="NOTIFY_FAMILY",
            command_id="cmd-001",
            status="CONFIRMED",
        )
        assert a.command_type == "NOTIFY_FAMILY"
        assert a.error is None

    def test_command_type_empty_rejected(self):
        with pytest.raises(ValueError, match="command_type 不能为空"):
            ActionSummary(command_type="", command_id="c", status="ok")

    def test_command_id_empty_rejected(self):
        with pytest.raises(ValueError, match="command_id 不能为空"):
            ActionSummary(command_type="t", command_id="", status="ok")

    def test_status_empty_rejected(self):
        with pytest.raises(ValueError, match="status 不能为空"):
            ActionSummary(command_type="t", command_id="c", status="")

    def test_roundtrip(self):
        a = ActionSummary(
            command_type="ESCALATE_COMMUNITY",
            command_id="cmd-002",
            status="PENDING",
            error="network timeout",
        )
        revived = ActionSummary.from_dict(a.to_dict())
        assert a == revived


# ============================================================================
# EpisodicRecord.evidence_refs（ADR-0027 Slice A：evidence_id 字符串列表）
# ============================================================================


class TestEpisodicEvidenceRefs:
    """``evidence_refs`` 在 ADR-0027 Slice A 起为 evidence_id 字符串列表。

    独立 ``EvidenceItem`` 以 ID 解析（ADR-0024 I2 单调性）；episode 仅持引用，
    不内联证据对象。v1 持久化可能是 ``EvidenceRef.to_dict()`` 字典列表，
    ``from_dict`` 须向后兼容（D8）。
    """

    def test_evidence_refs_is_str_list_roundtrip(self):
        rec = _make_episodic(evidence_refs=["ev-001", "ev-002"])
        revived = EpisodicRecord.from_dict(rec.to_dict())
        assert records_equal(rec, revived)
        assert revived.evidence_refs == ["ev-001", "ev-002"]

    def test_from_dict_coerces_v1_evidence_ref_dicts(self):
        """D8 向后兼容：v1 字典格式的 evidence_refs 仅提取 evidence_id。"""
        payload = _make_episodic(evidence_refs=[]).to_dict()
        payload["evidence_refs"] = [
            {
                "evidence_id": "ev-001",
                "modality": "vision",
                "captured_at": T1.isoformat(),
                "uri": "data/evidence/clip-001.mp4",
            }
        ]
        revived = EpisodicRecord.from_dict(payload)
        assert revived.evidence_refs == ["ev-001"]

    # ------------------------------------------------------------------
    # ADR-0027 Slice A 审查（P2）：非法 ID 必须显式拒绝，禁止静默进入 v2
    # ------------------------------------------------------------------
    def test_direct_construct_rejects_non_str_id(self):
        with pytest.raises(ValueError, match="evidence_refs"):
            _make_episodic(evidence_refs=["ev-1", 123])  # type: ignore[list-item]

    def test_direct_construct_rejects_empty_id(self):
        with pytest.raises(ValueError, match="evidence_refs"):
            _make_episodic(evidence_refs=["ev-1", ""])

    def test_from_dict_rejects_non_str_dict_id(self):
        payload = _make_episodic(evidence_refs=[]).to_dict()
        payload["evidence_refs"] = [{"evidence_id": 123, "modality": "vision"}]
        with pytest.raises(ValueError, match="evidence_refs"):
            EpisodicRecord.from_dict(payload)

    def test_from_dict_rejects_empty_str_dict_id(self):
        payload = _make_episodic(evidence_refs=[]).to_dict()
        payload["evidence_refs"] = [{"evidence_id": ""}]
        with pytest.raises(ValueError, match="evidence_refs"):
            EpisodicRecord.from_dict(payload)

    def test_from_dict_rejects_non_list(self):
        payload = _make_episodic(evidence_refs=[]).to_dict()
        payload["evidence_refs"] = "ev-1"
        with pytest.raises((TypeError, ValueError), match="evidence_refs"):
            EpisodicRecord.from_dict(payload)


# ============================================================================
# records_equal 工具
# ============================================================================


class TestRecordsEqual:
    def test_equal_same_type(self):
        a = _make_short_term()
        b = _make_short_term()
        assert records_equal(a, b)

    def test_equal_ignores_created_at(self):
        """created_at 是运行时墙钟，records_equal 必须忽略（Replay Test 语义）。"""
        a = _make_short_term()
        b = _make_short_term()
        # 强制设不同的 created_at，断言仍相等
        b.created_at = a.created_at.replace(microsecond=a.created_at.microsecond + 999)
        assert records_equal(a, b)

    def test_not_equal_different_type(self):
        a = _make_short_term()
        b = _make_episodic()
        assert not records_equal(a, b)

    def test_not_equal_different_field(self):
        a = _make_short_term()
        b = _make_short_term(phase="none", raised_signal_id=None, raised_at=None)
        assert not records_equal(a, b)


# ============================================================================
# JSON 序列化稳定性
# ============================================================================


class TestJsonSerialization:
    def test_short_term_json_sortable(self):
        """to_json 产出可 sort_keys（可做 hash baseline）。"""
        rec = _make_short_term()
        data = json.loads(rec.to_json())
        assert data["record_id"] == "st-visitor-001"

    def test_episodic_json_sortable(self):
        rec = _make_episodic()
        data = json.loads(rec.to_json())
        assert data["record_id"] == "ep-visitor-event-001"
        assert data["person_identity_id"] is None

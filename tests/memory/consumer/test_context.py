"""C-3 RuleBasedContextBuilder 组装单测（ADR-0025 §3.3 / DESIGN §3.3 / §5 DoD）。

覆盖：C1 无 score 字段、C3 确定性排序、C5 每项历史带 source_event_ids、纯组装透传、
ContextBuildError 守卫。
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from home_perception.memory.consumer.context import RuleBasedContextBuilder
from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    EvidenceRef,
    ReasoningInput,
)
from home_perception.memory.consumer.exceptions import ContextBuildError
from home_perception.memory.records import EpisodicRecord


def _utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _make_record(
    record_id: str,
    vid: str,
    enter: datetime,
    leave: datetime,
    source_ids: list[str],
    reason_summary: list[str] | None = None,
) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=record_id,
        visitor_instance_id=vid,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=source_ids,
        summary="visit",
        model_version="v1",
        reason_summary=reason_summary or [],
    )


def _make_current_event(
    vid: str = "v-001",
    risk_level: str | None = "MEDIUM",
    markers: tuple[str, ...] = ("night",),
) -> CurrentEvent:
    return CurrentEvent(
        event_id="ev-1",
        event_type="risk_signal",
        visitor_instance_id=vid,
        occurred_at=_utc(2026, 8, 2, 10),
        risk_level=risk_level,
        markers=markers,
    )


def _builder() -> RuleBasedContextBuilder:
    return RuleBasedContextBuilder()


def test_build_returns_reasoning_input() -> None:
    ce = _make_current_event()
    recs = [_make_record("ep-a", "v-001", _utc(2026, 8, 1, 8), _utc(2026, 8, 1, 8, 5), ["s1"])]
    out = _builder().build(ce, recs, None, None, (), (), ())
    assert isinstance(out, ReasoningInput)
    assert out.current_event is ce
    assert out.historical_context == tuple(recs)
    assert out.visitor_profile is None
    assert out.risk_pattern is None
    assert out.evidence_refs == ()
    assert out.previous_actions == ()
    assert out.conflicts == ()


def test_historical_context_sorted_c3() -> None:
    # 乱序输入（按 enter_time 逆序），产出须按 enter_time 升序
    r_late = _make_record("ep-late", "v-001", _utc(2026, 8, 3, 8), _utc(2026, 8, 3, 8, 5), ["s3"])
    r_early = _make_record("ep-early", "v-001", _utc(2026, 8, 1, 8), _utc(2026, 8, 1, 8, 5), ["s1"])
    out = _builder().build(_make_current_event(), [r_late, r_early], None, None, (), (), ())
    assert [ep.record_id for ep in out.historical_context] == ["ep-early", "ep-late"]


def test_empty_records_ok() -> None:
    out = _builder().build(_make_current_event(), [], None, None, (), (), ())
    assert out.historical_context == ()


def test_c1_no_score_fields() -> None:
    # ReasoningInput 不得含 risk_score / decision / warning 字段（硬边界 C1）
    field_names = {f.name for f in fields(ReasoningInput)}
    forbidden = {"risk_score", "decision", "warning", "score", "decision_request"}
    assert forbidden.isdisjoint(field_names), f"ReasoningInput 含禁止字段: {forbidden & field_names}"


def test_c5_source_event_ids_present() -> None:
    recs = [
        _make_record("ep-a", "v-001", _utc(2026, 8, 1, 8), _utc(2026, 8, 1, 8, 5), ["s1"]),
        _make_record("ep-b", "v-001", _utc(2026, 8, 2, 8), _utc(2026, 8, 2, 8, 5), ["s2", "s3"]),
    ]
    out = _builder().build(_make_current_event(), recs, None, None, (), (), ())
    for ep in out.historical_context:
        assert ep.source_event_ids, f"{ep.record_id} 缺失 source_event_ids（C5 溯源）"


def test_conflicts_passthrough() -> None:
    cf = ConflictFlag(type="behavior_shift", historical="normal", current="abnormal", detail="x")
    out = _builder().build(_make_current_event(), [], None, None, (), (), (cf,))
    assert out.conflicts == (cf,)


def test_evidence_and_actions_passthrough() -> None:
    ev = EvidenceRef(evidence_id="e1", modality="vision", captured_at=_utc(2026, 8, 1, 8))
    ac = ActionRecord(command_type="notify_family", command_id="a1", status="done")
    out = _builder().build(_make_current_event(), [], None, None, (ev,), (ac,), ())
    assert out.evidence_refs == (ev,)
    assert out.previous_actions == (ac,)


def test_determinism_c3() -> None:
    recs = [
        _make_record("ep-a", "v-001", _utc(2026, 8, 1, 8), _utc(2026, 8, 1, 8, 5), ["s1"]),
        _make_record("ep-b", "v-001", _utc(2026, 8, 2, 8), _utc(2026, 8, 2, 8, 5), ["s2"]),
    ]
    b = _builder()
    out1 = b.build(_make_current_event(), list(recs), None, None, (), (), ())
    out2 = b.build(_make_current_event(), recs, None, None, (), (), ())
    assert out1 == out2


def test_rejects_none_current_event() -> None:
    with pytest.raises(ContextBuildError):
        _builder().build(None, [], None, None, (), (), ())


def test_rejects_none_records() -> None:
    with pytest.raises(ContextBuildError):
        _builder().build(_make_current_event(), None, None, None, (), (), ())

"""RuleBasedAggregation 单元测试（C-2）。

覆盖：tuple 输出、空记录、画像字段、三档置信度 + 边界、identity_confirmed=False、
模式发现（repeated_visit / escalating_behavior）、样本不足无 RiskPattern、C3 确定性、
C2 不修改输入、异常分层（AggregationError）、配置可配（阈值 / 夜间窗）。

不变量（ADR-0025）：C1 无 score / decision / warning；C2 只读（不修改输入记录）；
C3 确定性（同输入两次产出一致）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from home_perception.memory.consumer.aggregation import RuleBasedAggregation
from home_perception.memory.consumer.config import AggregationConfig
from home_perception.memory.consumer.contracts import RiskPattern, VisitorProfile
from home_perception.memory.consumer.exceptions import AggregationError
from home_perception.memory.records import EpisodicRecord


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _make_record(
    record_id: str,
    enter_hour: int,
    leave_hour: int,
    day: int = 1,
    reason_summary: tuple[str, ...] = (),
    risk_level: str | None = None,
    visitor_instance_id: str = "v1",
    source_event_ids: list[str] | None = None,
) -> EpisodicRecord:
    enter = _utc(2026, 1, day, enter_hour, 0, 0)
    leave = _utc(2026, 1, day, leave_hour, 0, 0)
    return EpisodicRecord(
        record_id=record_id,
        visitor_instance_id=visitor_instance_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=source_event_ids or [f"evt-{record_id}"],
        summary=f"visit {record_id}",
        model_version="ep-builder-v1",
        reason_summary=list(reason_summary),
        risk_level=risk_level,
    )


def _make_records(n: int, hours: tuple[int, int] | None = None) -> list[EpisodicRecord]:
    """造 n 条记录；hour 轮流在 (23, 23) 夜间与 (10, 10) 白天，便于 night 比例测试。"""
    out: list[EpisodicRecord] = []
    for i in range(n):
        if hours is not None:
            eh, lh = hours
        else:
            eh, lh = (23, 23) if i % 2 == 0 else (10, 10)
        out.append(_make_record(f"ep-{i}", eh, lh, day=i + 1))
    return out


# --------------------------------------------------------------------------
# 基础形态
# --------------------------------------------------------------------------
def test_returns_profile_and_pattern_tuple() -> None:
    records = _make_records(3)
    result = RuleBasedAggregation().aggregate(records)
    assert isinstance(result, tuple)
    assert len(result) == 2
    profile, pattern = result
    assert isinstance(profile, VisitorProfile)
    assert isinstance(pattern, RiskPattern)


def test_empty_records_returns_none_none() -> None:
    assert RuleBasedAggregation().aggregate([]) == (None, None)


# --------------------------------------------------------------------------
# 画像字段（C2 只读、统计正确）
# --------------------------------------------------------------------------
def test_profile_fields_from_records() -> None:
    records = [
        _make_record("ep-0", 23, 23, day=1),  # night
        _make_record("ep-1", 23, 23, day=2),  # night
        _make_record("ep-2", 10, 10, day=3),  # day
    ]
    profile = RuleBasedAggregation().aggregate(records)[0]
    assert profile is not None
    assert profile.visitor_instance_id == "v1"
    assert profile.visit_count == 3
    assert abs(profile.night_visit_ratio - (2 / 3)) < 1e-9
    assert profile.first_seen == _utc(2026, 1, 1, 23, 0, 0)
    assert profile.last_seen == _utc(2026, 1, 3, 10, 0, 0)


def test_identity_confirmed_false() -> None:
    profile = RuleBasedAggregation().aggregate(_make_records(4))[0]
    assert profile is not None
    assert profile.identity_confirmed is False


# --------------------------------------------------------------------------
# 置信度三档 + 边界（cold_start<5 / weak 5–29 / stable≥30）
# --------------------------------------------------------------------------
def test_confidence_cold_start() -> None:
    profile = RuleBasedAggregation().aggregate(_make_records(4))[0]
    assert profile is not None
    assert profile.confidence == "cold_start"


def test_confidence_weak_pattern() -> None:
    profile = RuleBasedAggregation().aggregate(_make_records(5))[0]
    assert profile is not None
    assert profile.confidence == "weak_pattern"


def test_confidence_stable_pattern() -> None:
    profile = RuleBasedAggregation().aggregate(_make_records(30))[0]
    assert profile is not None
    assert profile.confidence == "stable_pattern"


def test_confidence_tier_boundaries() -> None:
    # 边界与 M0 _confidence_tier 一致：n<5 cold；5<=n<30 weak；n>=30 stable
    assert RuleBasedAggregation().aggregate(_make_records(4))[0].confidence == "cold_start"
    assert RuleBasedAggregation().aggregate(_make_records(5))[0].confidence == "weak_pattern"
    assert RuleBasedAggregation().aggregate(_make_records(29))[0].confidence == "weak_pattern"
    assert RuleBasedAggregation().aggregate(_make_records(30))[0].confidence == "stable_pattern"


# --------------------------------------------------------------------------
# 风险模式发现
# --------------------------------------------------------------------------
def test_risk_pattern_repeated_visit() -> None:
    pattern = RuleBasedAggregation().aggregate(_make_records(2))[1]
    assert pattern is not None
    assert "repeated_visit" in pattern.tags


def test_risk_pattern_escalating_behavior() -> None:
    records = [
        _make_record("ep-0", 10, 10, reason_summary=("behavior:loiter",)),
        _make_record("ep-1", 11, 11, reason_summary=("behavior:observe_camera",)),
    ]
    pattern = RuleBasedAggregation().aggregate(records)[1]
    assert pattern is not None
    assert "escalating_behavior" in pattern.tags
    assert "loiter" in pattern.escalation_history
    assert "observe_camera" in pattern.escalation_history


def test_risk_pattern_none_when_single_record() -> None:
    pattern = RuleBasedAggregation().aggregate(_make_records(1))[1]
    assert pattern is None


# --------------------------------------------------------------------------
# C3 确定性（与输入排序无关）
# --------------------------------------------------------------------------
def test_determinism_c3() -> None:
    base = _make_records(3)
    shuffled = [base[2], base[0], base[1]]
    p1, pat1 = RuleBasedAggregation().aggregate(base)
    p2, pat2 = RuleBasedAggregation().aggregate(shuffled)
    assert p1 == p2
    assert pat1 == pat2
    # 排序输出：tags 与 escalation_history 与输入序无关
    assert pat1 is not None
    assert pat1.tags == tuple(sorted(pat1.tags))


# --------------------------------------------------------------------------
# C2 不修改输入（只读）
# --------------------------------------------------------------------------
def test_no_mutation_c2() -> None:
    records = _make_records(3)
    before_ids = [r.record_id for r in records]
    before_summary = [tuple(r.reason_summary) for r in records]
    RuleBasedAggregation().aggregate(records)
    after_ids = [r.record_id for r in records]
    after_summary = [tuple(r.reason_summary) for r in records]
    assert after_ids == before_ids  # 不重排输入
    assert after_summary == before_summary  # 不修改记录内容


# --------------------------------------------------------------------------
# 异常分层（不静默）
# --------------------------------------------------------------------------
class _Boom:
    """属性访问即抛，用于验证 aggregate 把意外异常转译为 AggregationError。"""

    def __getattr__(self, _name: str) -> object:
        raise RuntimeError("boom")


def test_aggregation_error_translated() -> None:
    with pytest.raises(AggregationError):
        RuleBasedAggregation().aggregate([_Boom()])  # type: ignore[list-item]


# --------------------------------------------------------------------------
# 配置可配（不写死阈值 / 夜间窗）
# --------------------------------------------------------------------------
def test_configurable_thresholds() -> None:
    cfg = AggregationConfig(cold_start_threshold=3, weak_pattern_threshold=10)
    # n=3：默认阈值下 = cold_start；调小阈值后 = weak_pattern
    assert (
        RuleBasedAggregation().aggregate(_make_records(3))[0].confidence == "cold_start"
    )
    assert (
        RuleBasedAggregation(cfg).aggregate(_make_records(3))[0].confidence
        == "weak_pattern"
    )


def test_night_window_configurable() -> None:
    # 默认夜间窗 22:00–06:00：hour=20 与 10 均非夜间 → 比例 0
    default_ratio = (
        RuleBasedAggregation()
        .aggregate(
            [
                _make_record("ep-0", 20, 20),
                _make_record("ep-1", 10, 10),
            ]
        )[0]
        .night_visit_ratio
    )
    assert default_ratio == 0.0
    # 自定义夜间窗 20:00–08:00：hour=20 落入夜间 → 比例 0.5
    cfg = AggregationConfig(night_start_hour=20, night_end_hour=8)
    cfg_ratio = (
        RuleBasedAggregation(cfg)
        .aggregate(
            [
                _make_record("ep-0", 20, 20),
                _make_record("ep-1", 10, 10),
            ]
        )[0]
        .night_visit_ratio
    )
    assert cfg_ratio == 0.5
# --------------------------------------------------------------------------
# Fix 1 回归：混合访客输入 -> 显式 AggregationError（C2/C3）
# --------------------------------------------------------------------------
def test_rejects_mixed_visitors() -> None:
    mixed = [
        _make_record("ep-0", 23, 23, visitor_instance_id="vA"),
        _make_record("ep-1", 10, 10, visitor_instance_id="vB"),
    ]
    with pytest.raises(AggregationError):
        RuleBasedAggregation().aggregate(mixed)


def test_mixed_visitors_order_independent() -> None:
    # 同一组混合访客，仅改变顺序，均须抛 AggregationError
    # （不产出顺序相关的错乱画像，守 C3 确定性）
    forward = [
        _make_record("ep-0", 23, 23, visitor_instance_id="vA"),
        _make_record("ep-1", 10, 10, visitor_instance_id="vB"),
    ]
    backward = [forward[1], forward[0]]
    with pytest.raises(AggregationError):
        RuleBasedAggregation().aggregate(forward)
    with pytest.raises(AggregationError):
        RuleBasedAggregation().aggregate(backward)


# --------------------------------------------------------------------------
# Fix 2 回归：升级模式须基于"不同（非空）阶段"，重复 / 空标记不算升级
# --------------------------------------------------------------------------
def test_escalation_requires_distinct_markers() -> None:
    # 两次相同 behavior:loiter -> 唯一非空标记仅 1 -> 不判升级（仅 repeated_visit）
    records = [
        _make_record("ep-0", 10, 10, reason_summary=("behavior:loiter",)),
        _make_record("ep-1", 11, 11, reason_summary=("behavior:loiter",)),
    ]
    pattern = RuleBasedAggregation().aggregate(records)[1]
    assert pattern is not None
    assert "repeated_visit" in pattern.tags
    assert "escalating_behavior" not in pattern.tags
    assert pattern.escalation_history is None


def test_empty_behavior_suffix_ignored() -> None:
    # 一条空 behavior: 后缀 + 一条 observe_camera -> 唯一非空标记仅 1 -> 不判升级
    records = [
        _make_record("ep-0", 10, 10, reason_summary=("behavior:",)),
        _make_record("ep-1", 11, 11, reason_summary=("behavior:observe_camera",)),
    ]
    pattern = RuleBasedAggregation().aggregate(records)[1]
    assert pattern is not None
    assert "escalating_behavior" not in pattern.tags
    assert pattern.escalation_history is None

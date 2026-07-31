"""Short-term Memory Policy 测试（ADR-0024 §3.1.1 / §3.2 · Slice 2 验收）。

> 对应 DESIGN §8.4 验收标准：
> - RAISED → ShortTermRecord(phase=active_risk)
> - CLEARED → 更新 record，phase=none
> - 同 visitor_instance_id 多次 RAISED → record_id 一致（幂等键稳定）
> - 周期快照触发 → 覆写当前 record，不新增
> - 无跃迁时不写 → 只传 BehaviorState 无 RiskSignal → 返回 None
> - I1 幂等 → 同 signal 重复投递 3 次，产出 record_id 一致
> - I4 可解释 → source_event_ids 非空
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from home_perception.analysis.behavior_state import BehaviorState, BehaviorPhase
from home_perception.analysis.risk_signal import (
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)
from home_perception.memory import DefaultShortTermPolicy, ShortTermRecord

# 共用时间基线（UTC）
T0 = datetime(2026, 7, 28, 18, 30, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=60)   # RAISED 时刻
T2 = T0 + timedelta(seconds=300)  # 周期快照时刻
T3 = T0 + timedelta(seconds=600)  # CLEARED 时刻

VISITOR_ID = "visitor-001"
SIGNAL_ID_RAISED = "00000000-0000-0000-0000-000000000001"
SIGNAL_ID_CLEARED = "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _make_state(first_seen=T0, last_seen=T1, dwell=60.0) -> BehaviorState:
    """构造 BehaviorState（phase=ONGOING）。"""
    return BehaviorState(
        track_id=1,
        visitor_instance_id=VISITOR_ID,
        phase=BehaviorPhase.ONGOING,
        first_seen=first_seen,
        last_seen=last_seen,
        dwell_seconds=dwell,
        is_odd_hour=True,
        proximity_score=0.0,
    )


def _make_signal(
    transition: SignalTransition,
    signal_id: str,
    paired_signal_id=None,
    created_at=T1,
) -> RiskSignal:
    """构造 RiskSignal。"""
    return RiskSignal(
        signal_id=signal_id,
        subject_type=SubjectType.VISITOR,
        subject_id=VISITOR_ID,
        category=SignalCategory.BEHAVIORAL,
        source=SourceModality.VISION,
        transition=transition,
        features={"dwell_seconds": 300},
        paired_signal_id=paired_signal_id,
        track_id=1,
        visitor_instance_id=VISITOR_ID,
        severity_hint=0.8,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# 验收 1：RAISED → ShortTermRecord(phase=active_risk)
# ---------------------------------------------------------------------------

class TestRaised:
    def test_raised_produces_active_risk_record(self):
        state = _make_state()
        signal = _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED)
        policy = DefaultShortTermPolicy()
        rec = policy.transform_short_term(state, signal)
        assert rec is not None
        assert rec.record_id == f"st-{VISITOR_ID}"
        assert rec.visitor_instance_id == VISITOR_ID
        assert rec.phase == "active_risk"
        assert rec.raised_signal_id == SIGNAL_ID_RAISED
        assert rec.raised_at == T1
        assert rec.first_seen == T0
        assert rec.last_seen_at == T1

    def test_raised_source_event_ids_contains_signal_id(self):
        """I4 可解释性：source_event_ids 必须非空且包含 signal_id。"""
        state = _make_state()
        signal = _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED)
        rec = DefaultShortTermPolicy().transform_short_term(state, signal)
        assert rec is not None
        assert SIGNAL_ID_RAISED in rec.source_event_ids
        assert len(rec.source_event_ids) == 1


# ---------------------------------------------------------------------------
# 验收 2：CLEARED → 更新 record，phase=none
# ---------------------------------------------------------------------------

class TestCleared:
    def test_cleared_with_current_record_inherits_raised_at(self):
        """CLEARED 时 raised_at 从 current_record 继承（transition 不携带此信息）。"""
        state = _make_state(last_seen=T3)
        signal = _make_signal(
            SignalTransition.CLEARED,
            SIGNAL_ID_CLEARED,
            paired_signal_id=SIGNAL_ID_RAISED,
            created_at=T3,
        )
        # 模拟先有 RAISED 记录
        current = ShortTermRecord(
            record_id=f"st-{VISITOR_ID}",
            visitor_instance_id=VISITOR_ID,
            phase="active_risk",
            first_seen=T0,
            last_seen_at=T1,
            source_event_ids=[SIGNAL_ID_RAISED],
            raised_signal_id=SIGNAL_ID_RAISED,
            raised_at=T1,
        )
        rec = DefaultShortTermPolicy().transform_short_term(state, signal, current)
        assert rec is not None
        assert rec.phase == "none"
        # raised_signal_id 从 CLEARED.paired_signal_id 回填
        assert rec.raised_signal_id == SIGNAL_ID_RAISED
        # raised_at 从 current_record 继承
        assert rec.raised_at == T1
        # last_seen_at 更新为 state.last_seen
        assert rec.last_seen_at == T3

    def test_cleared_source_event_ids_is_cleared_signal_id(self):
        """CLEARED 写入触发，source_event_ids 是 CLEARED 的 signal_id。"""
        state = _make_state()
        signal = _make_signal(
            SignalTransition.CLEARED, SIGNAL_ID_CLEARED,
            paired_signal_id=SIGNAL_ID_RAISED,
        )
        current = ShortTermRecord(
            record_id=f"st-{VISITOR_ID}", visitor_instance_id=VISITOR_ID,
            phase="active_risk", first_seen=T0, last_seen_at=T1,
            source_event_ids=[SIGNAL_ID_RAISED], raised_signal_id=SIGNAL_ID_RAISED,
            raised_at=T1,
        )
        rec = DefaultShortTermPolicy().transform_short_term(state, signal, current)
        assert rec is not None
        assert rec.source_event_ids == [SIGNAL_ID_CLEARED]


# ---------------------------------------------------------------------------
# 验收 3：同一 visitor_instance_id 多次 RAISED → record_id 一致（幂等键稳定）
# ---------------------------------------------------------------------------

class TestIdempotentKey:
    def test_same_visitor_same_record_id_across_transitions(self):
        """同一 visitor 多次跃迁，record_id 恒为 st-{visitor_id}。"""
        policy = DefaultShortTermPolicy()
        state1 = _make_state(last_seen=T1)
        signal1 = _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED)
        rec1 = policy.transform_short_term(state1, signal1)

        state2 = _make_state(last_seen=T3)
        signal2 = _make_signal(
            SignalTransition.CLEARED, SIGNAL_ID_CLEARED,
            paired_signal_id=SIGNAL_ID_RAISED, created_at=T3,
        )
        rec2 = policy.transform_short_term(state2, signal2, rec1)

        assert rec1.record_id == rec2.record_id == f"st-{VISITOR_ID}"


# ---------------------------------------------------------------------------
# 验收 4：周期快照触发 → 覆写当前 record，不新增
# ---------------------------------------------------------------------------

class TestPeriodicSnapshot:
    def test_snapshot_updates_last_seen_keeps_phase(self):
        """周期快照：transition=None, current_record 非 None → 覆写 last_seen_at，phase 继承。"""
        # 先有 RAISED 记录
        raised = DefaultShortTermPolicy().transform_short_term(
            _make_state(last_seen=T1),
            _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED),
        )
        # 周期快照：30s 后，state 推进
        snapshot_state = _make_state(last_seen=T2, dwell=120.0)
        rec = DefaultShortTermPolicy().transform_short_term(
            snapshot_state, None, raised
        )
        assert rec is not None
        # record_id 不变（不新增）
        assert rec.record_id == raised.record_id
        # phase 继承
        assert rec.phase == "active_risk"
        assert rec.raised_signal_id == SIGNAL_ID_RAISED
        assert rec.raised_at == T1
        # last_seen_at 更新
        assert rec.last_seen_at == T2
        # source_event_ids 保留原值（周期快照不产生新事件）
        assert rec.source_event_ids == [SIGNAL_ID_RAISED]


# ---------------------------------------------------------------------------
# 验收 5：无跃迁时不写
# ---------------------------------------------------------------------------

class TestNoTransitionNoWrite:
    def test_no_transition_no_current_returns_none(self):
        """无跃迁且无 current_record → 返回 None。"""
        state = _make_state()
        rec = DefaultShortTermPolicy().transform_short_term(state, None, None)
        assert rec is None

    def test_all_none_returns_none(self):
        """全 None → 返回 None。"""
        rec = DefaultShortTermPolicy().transform_short_term(None, None, None)
        assert rec is None


# ---------------------------------------------------------------------------
# 验收 6：I1 幂等 → 同 signal 重复投递 3 次，产出 record_id 一致
# ---------------------------------------------------------------------------

class TestI1Idempotency:
    def test_same_signal_repeated_produces_same_record(self):
        """同 signal_id 重复 3 次，产出 record_id 一致（I1 幂等性前置）。"""
        policy = DefaultShortTermPolicy()
        state = _make_state()
        signal = _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED)
        rec1 = policy.transform_short_term(state, signal)
        rec2 = policy.transform_short_term(state, signal)
        rec3 = policy.transform_short_term(state, signal)
        # record_id 一致
        assert rec1.record_id == rec2.record_id == rec3.record_id
        # 内容一致（除 created_at）
        from home_perception.memory.records import records_equal
        assert records_equal(rec1, rec2)
        assert records_equal(rec2, rec3)


# ---------------------------------------------------------------------------
# 验收 7：I4 可解释 → source_event_ids 非空
# ---------------------------------------------------------------------------

class TestI4Explainability:
    def test_raised_source_event_ids_non_empty(self):
        rec = DefaultShortTermPolicy().transform_short_term(
            _make_state(),
            _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED),
        )
        assert rec is not None
        assert len(rec.source_event_ids) > 0

    def test_cleared_source_event_ids_non_empty(self):
        current = ShortTermRecord(
            record_id=f"st-{VISITOR_ID}", visitor_instance_id=VISITOR_ID,
            phase="active_risk", first_seen=T0, last_seen_at=T1,
            source_event_ids=[SIGNAL_ID_RAISED], raised_signal_id=SIGNAL_ID_RAISED,
            raised_at=T1,
        )
        rec = DefaultShortTermPolicy().transform_short_term(
            _make_state(),
            _make_signal(SignalTransition.CLEARED, SIGNAL_ID_CLEARED,
                          paired_signal_id=SIGNAL_ID_RAISED),
            current,
        )
        assert rec is not None
        assert len(rec.source_event_ids) > 0


# ---------------------------------------------------------------------------
# 边界场景
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_visitor_id_missing_returns_none(self):
        """visitor_instance_id 缺失 → 返回 None（无法构造幂等键）。"""
        # state 无 visitor_id, transition 无 visitor_id, current_record None
        state = BehaviorState(
            track_id=1, visitor_instance_id="", phase=BehaviorPhase.ONGOING,
            first_seen=T0, last_seen=T1, dwell_seconds=60.0, is_odd_hour=True,
        )
        signal = RiskSignal(
            signal_id=SIGNAL_ID_RAISED, subject_type=SubjectType.VISITOR,
            subject_id="", category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION, transition=SignalTransition.RAISED,
            features={}, visitor_instance_id=None,
        )
        rec = DefaultShortTermPolicy().transform_short_term(state, signal, None)
        assert rec is None

    def test_cleared_without_current_record_no_raised_at(self):
        """CLEARED 但无 current_record → raised_at=None（无法继承）。"""
        state = _make_state()
        signal = _make_signal(
            SignalTransition.CLEARED, SIGNAL_ID_CLEARED,
            paired_signal_id=SIGNAL_ID_RAISED,
        )
        rec = DefaultShortTermPolicy().transform_short_term(state, signal, None)
        assert rec is not None
        assert rec.phase == "none"
        assert rec.raised_signal_id == SIGNAL_ID_RAISED  # 从 paired_signal_id 回填
        assert rec.raised_at is None  # 无 current_record 可继承

    def test_snapshot_without_current_returns_none(self):
        """周期快照但无 current_record → 返回 None（无 record 可覆写）。"""
        state = _make_state()
        rec = DefaultShortTermPolicy().transform_short_term(state, None, None)
        assert rec is None

    def test_cleared_unpaired_uses_current_raised_signal_id(self):
        """CLEARED 无 paired_signal_id → 从 current_record.raised_signal_id 继承。"""
        current = ShortTermRecord(
            record_id=f"st-{VISITOR_ID}", visitor_instance_id=VISITOR_ID,
            phase="active_risk", first_seen=T0, last_seen_at=T1,
            source_event_ids=[SIGNAL_ID_RAISED], raised_signal_id=SIGNAL_ID_RAISED,
            raised_at=T1,
        )
        # CLEARED 无 paired_signal_id
        signal = _make_signal(SignalTransition.CLEARED, SIGNAL_ID_CLEARED)
        rec = DefaultShortTermPolicy().transform_short_term(
            _make_state(), signal, current
        )
        assert rec is not None
        assert rec.raised_signal_id == SIGNAL_ID_RAISED  # 从 current 继承


# ---------------------------------------------------------------------------
# 占位方法验证（v1 不实现，return None）
# ---------------------------------------------------------------------------

class TestPlaceholders:
    def test_project_episode_returns_none(self):
        policy = DefaultShortTermPolicy()
        assert policy.project_episode(None, [], []) is None

    def test_aggregate_semantic_returns_none(self):
        policy = DefaultShortTermPolicy()
        assert policy.aggregate_semantic([], "environment", "2026-07") is None


# ---------------------------------------------------------------------------
# 纯函数语义验证
# ---------------------------------------------------------------------------

class TestPureFunction:
    def test_does_not_modify_inputs(self):
        """transform_short_term 不修改输入对象（只读消费）。"""
        state = _make_state()
        signal = _make_signal(SignalTransition.RAISED, SIGNAL_ID_RAISED)
        state_before = state.to_dict()
        signal_before_id = signal.signal_id
        signal_before_paired = signal.paired_signal_id

        _ = DefaultShortTermPolicy().transform_short_term(state, signal)

        # state 未被修改
        assert state.to_dict() == state_before
        # signal 未被修改
        assert signal.signal_id == signal_before_id
        assert signal.paired_signal_id == signal_before_paired

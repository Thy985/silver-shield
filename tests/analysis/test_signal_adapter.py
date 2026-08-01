"""SignalAdapter 单元测试（Migration Stage C · Shadow Mode）。

覆盖工程方案 §8.1 测试清单：
- RAISED → PerceptionEvent 标签映射（dwell 超阈→abnormal_dwell；visits→repeat_visit；odd_hour→visit_pending_verify）
- CLEARED → None（不产出 PerceptionEvent）
- 产物过冻结 schema 校验（EventType 5 类之一，score ∈ [0,1]）
- 黑名单字段（fraud/suspect/verdict 等）结构性拒绝（RiskSignal 已守，adapter 透传）
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from home_perception.analysis.perception import EVENT_TYPES, PerceptionEvent
from home_perception.analysis.risk_signal import (
    FORBIDDEN_RISKSIGNAL_FIELDS,
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)
from home_perception.analysis.signal_adapter import risk_signal_to_perception

# ============================================================================
# 辅助构造
# ============================================================================


def _utc(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _raised_signal(
    vid: str,
    features: dict,
    track_id: int = 1,
    now: datetime | None = None,
) -> RiskSignal:
    return RiskSignal(
        signal_id=str(uuid4()),
        subject_type=SubjectType.VISITOR,
        subject_id=vid,
        category=SignalCategory.BEHAVIORAL,
        source=SourceModality.VISION,
        transition=SignalTransition.RAISED,
        features=features,
        paired_signal_id=None,
        track_id=track_id,
        visitor_instance_id=vid,
        severity_hint=None,
        created_at=now or _utc(2026, 7, 27, 10, 5, 50),
    )


def _cleared_signal(vid: str, raised_id: str) -> RiskSignal:
    return RiskSignal(
        signal_id=str(uuid4()),
        subject_type=SubjectType.VISITOR,
        subject_id=vid,
        category=SignalCategory.BEHAVIORAL,
        source=SourceModality.VISION,
        transition=SignalTransition.CLEARED,
        features={"dwell_seconds": 100.0},
        paired_signal_id=raised_id,
        track_id=1,
        visitor_instance_id=vid,
        severity_hint=None,
        created_at=_utc(2026, 7, 27, 10, 10, 0),
    )


# ============================================================================
# 1. CLEARED → None（不产出）
# ============================================================================


class TestClearedNoOutput:
    def test_cleared_returns_none(self):
        """CLEARED 信号 → None（不产出 PerceptionEvent）。"""
        vid = str(uuid4())
        raised = _raised_signal(vid, {"dwell_seconds": 350.0})
        cleared = _cleared_signal(vid, raised.signal_id)

        result = risk_signal_to_perception(cleared, device_id="dev/test")
        assert result is None


# ============================================================================
# 2. RAISED → PerceptionEvent 标签映射
# ============================================================================


class TestRaisedLabelMapping:
    def test_dwell_over_threshold_maps_to_abnormal_dwell(self):
        """dwell_seconds >= threshold → abnormal_dwell。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 350.0,
                "visits_in_window": 0,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test", location="入户门")

        assert perc is not None
        assert perc.event_type == "abnormal_dwell"
        assert 0.0 <= perc.score <= 1.0
        assert str(perc.visitor_id) == vid  # UUID 转换正确（__post_init__ 归一为 UUID 对象）
        assert perc.location == "入户门"
        assert perc.meta["rule"] == "RealTimeRiskEvaluator"
        assert perc.meta["signal_id"] == sig.signal_id
        assert perc.meta["realtime"] is True

    def test_visits_over_threshold_maps_to_repeat_visit(self):
        """visits_in_window >= threshold → repeat_visit（dwell 不超阈时）。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 10.0,  # 不超阈
                "visits_in_window": 5,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")

        assert perc is not None
        assert perc.event_type == "repeat_visit"
        assert perc.repeat_count == 5  # 透传 visits_in_window

    def test_odd_hour_maps_to_visit_pending_verify(self):
        """is_odd_hour=True → visit_pending_verify（dwell/visits 不超阈时）。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 10.0,
                "visits_in_window": 0,
                "is_odd_hour": True,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")

        assert perc is not None
        assert perc.event_type == "visit_pending_verify"
        assert perc.is_odd_hour is True

    def test_dwell_priority_over_visits_and_odd_hour(self):
        """多条件同时满足：dwell 优先（→ abnormal_dwell）。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 400.0,  # 超阈
                "visits_in_window": 5,  # 也超阈
                "is_odd_hour": True,  # 也满足
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type == "abnormal_dwell"

    def test_visits_priority_over_odd_hour(self):
        """dwell 不超阈、visits + odd_hour 同时满足：visits 优先（→ repeat_visit）。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 10.0,
                "visits_in_window": 5,
                "is_odd_hour": True,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type == "repeat_visit"


# ============================================================================
# 3. Schema 校验（产物过冻结契约）
# ============================================================================


class TestSchemaConformance:
    def test_event_type_in_five_types(self):
        """产物 event_type 必须是 §7.2 5 类之一。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 350.0,
                "visits_in_window": 0,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type in EVENT_TYPES

    def test_score_in_zero_one(self):
        """score ∈ [0, 1]。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 1000.0,  # 远超阈
                "visits_in_window": 0,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert 0.0 <= perc.score <= 1.0

    def test_returns_perception_event_instance(self):
        """产物是 PerceptionEvent 实例。"""
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 350.0,
                "visits_in_window": 0,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert isinstance(perc, PerceptionEvent)


# ============================================================================
# 4. 输入校验 + 黑名单
# ============================================================================


class TestInputValidation:
    def test_non_risksignal_rejected(self):
        """非 RiskSignal 输入抛 TypeError。"""
        with pytest.raises(TypeError, match="signal 必须是 RiskSignal"):
            risk_signal_to_perception("not-a-signal", device_id="dev/test")  # type: ignore

    def test_invalid_uuid_subject_id_rejected(self):
        """subject_id 非合法 UUID 字符串抛 ValueError。"""
        sig = RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id="not-a-uuid",  # 非法
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.RAISED,
            features={"dwell_seconds": 350.0},
        )
        with pytest.raises(ValueError, match="subject_id 必须是合法 UUID"):
            risk_signal_to_perception(sig, device_id="dev/test")

    def test_forbidden_fields结构性拒绝(self):
        """RiskSignal 顶层结构性不含黑名单字段（fraud/suspect/verdict 等）。

        RiskSignal.__post_init__ 已守（features 内也不允许），adapter 透传产物
        同样不含这些字段。本测试断言此约束在 adapter 路径仍成立。
        """
        vid = str(uuid4())
        sig = _raised_signal(
            vid,
            {
                "dwell_seconds": 350.0,
                "visits_in_window": 0,
                "is_odd_hour": False,
                "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
            },
        )
        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc is not None
        # 顶层字段不含黑名单
        for f in FORBIDDEN_RISKSIGNAL_FIELDS:
            assert not hasattr(perc, f), f"PerceptionEvent 含禁止字段 {f}"
        # meta 内也不含
        for f in FORBIDDEN_RISKSIGNAL_FIELDS:
            assert f not in (perc.meta or {}), f"meta 含禁止字段 {f}"


# ============================================================================
# 5. 端到端：RealTimeRiskEvaluator → signal_adapter（防 features 硬编码回归）
# ============================================================================


class TestEvaluatorToAdapterIntegration:
    """端到端：evaluator 产出 RAISED → signal_adapter 映射 PerceptionEvent。

    回归保护：早期 evaluator._emit_raised 把 visits_in_window 硬编码为 0，
    导致 visits 触发的 RAISED 信号经 adapter 映射落入兜底分支返回
    visit_pending_verify（错误）。本组测试通过真实 evaluator 产出信号喂给
    adapter，断言映射正确。
    """

    def _eval_raised(
        self,
        *,
        dwell: float,
        visits: int,
        is_odd: bool = False,
        long_duration: float = 300.0,
        repeat_count: int = 3,
    ) -> RiskSignal:
        """用 RealTimeRiskEvaluator 产出一个 RAISED 信号。"""
        from home_perception.analysis.behavior_state import (
            BehaviorPhase,
            BehaviorState,
            RealtimeContext,
        )
        from home_perception.analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
        from home_perception.analysis.rule_engine import ThresholdConfig

        vid = str(uuid4())
        fs = _utc(2026, 7, 27, 10, 0, 0)
        # last_seen 由 dwell 推导（防秒越界）
        if dwell >= 60:
            ls = fs.replace(minute=fs.minute + int(dwell // 60), second=int(dwell % 60))
        else:
            ls = fs.replace(second=int(dwell))
        state = BehaviorState(
            track_id=1,
            visitor_instance_id=vid,
            phase=BehaviorPhase.ONGOING,
            first_seen=fs,
            last_seen=ls,
            dwell_seconds=dwell,
            is_odd_hour=is_odd,
            proximity_score=0.0,
        )
        ctx = RealtimeContext(
            current_state=state,
            recent_behavior={"visits_in_window": visits},
        )
        ev = RealTimeRiskEvaluator(
            ThresholdConfig(
                long_duration_seconds=long_duration,
                repeat_visit_count=repeat_count,
            )
        )
        signals = ev.evaluate([ctx], _utc(2026, 7, 27, 10, 0, int(dwell) if dwell < 60 else 0))
        assert len(signals) == 1, f"应产 1 个 RAISED，实际 {len(signals)}"
        assert signals[0].transition is SignalTransition.RAISED
        return signals[0]

    def test_visits_triggered_maps_to_repeat_visit(self):
        """visits 触发的 RAISED → adapter → repeat_visit（不是 visit_pending_verify）。"""
        sig = self._eval_raised(dwell=10.0, visits=5, repeat_count=3)
        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc is not None
        assert perc.event_type == "repeat_visit"
        assert perc.repeat_count == 5

    def test_dwell_triggered_maps_to_abnormal_dwell(self):
        """dwell 触发的 RAISED → adapter → abnormal_dwell。"""
        sig = self._eval_raised(dwell=350.0, visits=0, long_duration=300.0)
        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc is not None
        assert perc.event_type == "abnormal_dwell"

    def test_odd_hour_triggered_maps_to_visit_pending_verify(self):
        """odd_hour 触发的 RAISED → adapter → visit_pending_verify。"""
        sig = self._eval_raised(dwell=10.0, visits=0, is_odd=True)
        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc is not None
        assert perc.event_type == "visit_pending_verify"
        assert perc.is_odd_hour is True

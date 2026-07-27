"""SignalAdapter 单元测试（Migration Stage C · Shadow Mode）。

覆盖工程方案 §8.1 测试清单：
- RAISED → PerceptionEvent 标签映射（dwell 超阈→abnormal_dwell；visits→repeat_visit；odd_hour→visit_pending_verify）
- CLEARED → None（不产出 PerceptionEvent）
- 产物过冻结 schema 校验（EventType 5 类之一，score ∈ [0,1]）
- 黑名单字段（fraud/suspect/verdict 等）结构性拒绝（RiskSignal 已守，adapter 透传）
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from home_perception.analysis.perception import EVENT_TYPES, PerceptionEvent
from home_perception.analysis.risk_signal import (
    FORBIDDEN_RISKSIGNAL_FIELDS,
    RiskSignal,
    SignalCategory,
    SourceModality,
    SignalTransition,
    SubjectType,
)
from home_perception.analysis.signal_adapter import risk_signal_to_perception


# ============================================================================
# 辅助构造
# ============================================================================

def _utc(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


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
        sig = _raised_signal(vid, {
            "dwell_seconds": 350.0,
            "visits_in_window": 0,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

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
        sig = _raised_signal(vid, {
            "dwell_seconds": 10.0,  # 不超阈
            "visits_in_window": 5,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")

        assert perc is not None
        assert perc.event_type == "repeat_visit"
        assert perc.repeat_count == 5  # 透传 visits_in_window

    def test_odd_hour_maps_to_visit_pending_verify(self):
        """is_odd_hour=True → visit_pending_verify（dwell/visits 不超阈时）。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 10.0,
            "visits_in_window": 0,
            "is_odd_hour": True,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")

        assert perc is not None
        assert perc.event_type == "visit_pending_verify"
        assert perc.is_odd_hour is True

    def test_dwell_priority_over_visits_and_odd_hour(self):
        """多条件同时满足：dwell 优先（→ abnormal_dwell）。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 400.0,  # 超阈
            "visits_in_window": 5,   # 也超阈
            "is_odd_hour": True,     # 也满足
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type == "abnormal_dwell"

    def test_visits_priority_over_odd_hour(self):
        """dwell 不超阈、visits + odd_hour 同时满足：visits 优先（→ repeat_visit）。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 10.0,
            "visits_in_window": 5,
            "is_odd_hour": True,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type == "repeat_visit"


# ============================================================================
# 3. Schema 校验（产物过冻结契约）
# ============================================================================

class TestSchemaConformance:
    def test_event_type_in_five_types(self):
        """产物 event_type 必须是 §7.2 5 类之一。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 350.0,
            "visits_in_window": 0,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc.event_type in EVENT_TYPES

    def test_score_in_zero_one(self):
        """score ∈ [0, 1]。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 1000.0,  # 远超阈
            "visits_in_window": 0,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert 0.0 <= perc.score <= 1.0

    def test_returns_perception_event_instance(self):
        """产物是 PerceptionEvent 实例。"""
        vid = str(uuid4())
        sig = _raised_signal(vid, {
            "dwell_seconds": 350.0,
            "visits_in_window": 0,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })

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
        sig = _raised_signal(vid, {
            "dwell_seconds": 350.0,
            "visits_in_window": 0,
            "is_odd_hour": False,
            "thresholds": {"long_duration_seconds": 300.0, "repeat_visit_count": 3},
        })
        perc = risk_signal_to_perception(sig, device_id="dev/test")
        assert perc is not None
        # 顶层字段不含黑名单
        for f in FORBIDDEN_RISKSIGNAL_FIELDS:
            assert not hasattr(perc, f), f"PerceptionEvent 含禁止字段 {f}"
        # meta 内也不含
        for f in FORBIDDEN_RISKSIGNAL_FIELDS:
            assert f not in (perc.meta or {}), f"meta 含禁止字段 {f}"

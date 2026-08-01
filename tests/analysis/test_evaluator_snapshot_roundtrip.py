"""RealTimeRiskEvaluator Snapshot/Restore 往返测试（ADR-0024 Slice 3 Stage C/E）。

覆盖 §8.5：snapshot 只导出 reconstructable 字段、restore 重建 _active、confidence 标记、
STALE 恢复后新帧检测到同一 visitor → confidence 升至 1.0（单调上升）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from home_perception.analysis.behavior_state import BehaviorPhase, BehaviorState
from home_perception.analysis.realtime_risk_evaluator import (
    RealTimeRiskEvaluator,
    RiskPhase,
)
from home_perception.analysis.rule_engine import ThresholdConfig
from home_perception.memory.snapshot import ActiveTrackSnapshot


def _utc(sec: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sec)


def _make_evaluator() -> RealTimeRiskEvaluator:
    return RealTimeRiskEvaluator(thresholds=ThresholdConfig(), now_provider=None)


def _behavior_state(vid: str, *, dwell: float = 0.0, odd: bool = False) -> BehaviorState:
    return BehaviorState(
        track_id=1,
        visitor_instance_id=vid,
        phase=BehaviorPhase.ONGOING,
        first_seen=_utc(0),
        last_seen=_utc(0),
        dwell_seconds=dwell,
        is_odd_hour=odd,
    )


def test_snapshot_exports_reconstructable_only():
    """snapshot 只导出 reconstructable 字段（无 dwell/risk_score/track_id）。"""
    ev = _make_evaluator()
    ev._active["V1"] = _track_state(phase=RiskPhase.ACTIVE_RISK, vid="V1")
    snaps = ev.snapshot(now=_utc(100))
    assert len(snaps) == 1
    s = snaps[0]
    # 仅 reconstructable 字段
    assert set(s.__dataclass_fields__.keys()) == {
        "visitor_instance_id",
        "phase",
        "raised_signal_id",
        "raised_at",
        "first_seen",
        "last_seen_at",
    }
    assert s.visitor_instance_id == "V1"
    assert s.phase == "active_risk"
    assert s.last_seen_at == _utc(100)


def test_restore_rebuilds_active_state():
    """restore 重建 _active，保留 phase / raised_signal_id / first_seen。"""
    ev = _make_evaluator()
    snap = ActiveTrackSnapshot(
        visitor_instance_id="V1",
        phase="active_risk",
        raised_signal_id="sig-x",
        raised_at=_utc(90),
        first_seen=_utc(10),
        last_seen_at=_utc(100),
    )
    ev.restore([snap], confidence=1.0)

    assert "V1" in ev._active
    st = ev._active["V1"]
    assert st.phase is RiskPhase.ACTIVE_RISK
    assert st.raised_signal_id == "sig-x"
    assert st.raised_at == _utc(90)
    assert st.first_seen == _utc(10)
    assert st.last_track_id is None  # track_id 不持久化
    assert st.confidence == 1.0


def test_restore_applies_stale_confidence():
    """STALE 恢复：confidence=0.5 标记写入。"""
    ev = _make_evaluator()
    snap = ActiveTrackSnapshot(
        visitor_instance_id="V1",
        phase="none",
        raised_signal_id=None,
        raised_at=None,
        first_seen=_utc(10),
        last_seen_at=_utc(100),
    )
    ev.restore([snap], confidence=0.5)
    assert ev._active["V1"].confidence == 0.5


def test_restore_empty_clears_active():
    """restore 空列表 → _active 清空（等价 reset）。"""
    ev = _make_evaluator()
    ev._active["LEFT"] = _track_state(phase=RiskPhase.NONE, vid="LEFT")
    ev.restore([], confidence=1.0)
    assert ev.active_count == 0


def test_stale_upgrade_on_new_frame():
    """STALE(0.5) 恢复后，新帧检测到同一 visitor → confidence 升至 1.0（单调上升）。"""
    ev = _make_evaluator()
    snap = ActiveTrackSnapshot(
        visitor_instance_id="V1",
        phase="none",
        raised_signal_id=None,
        raised_at=None,
        first_seen=_utc(10),
        last_seen_at=_utc(100),
    )
    ev.restore([snap], confidence=0.5)
    assert ev._active["V1"].confidence == 0.5

    # 新帧（不触发 RAISED：dwell=0, visits=0, not odd）重新见到 V1
    ev.evaluate(
        [
            __import__(
                "home_perception.analysis.behavior_state", fromlist=["RealtimeContext"]
            ).RealtimeContext(
                current_state=_behavior_state("V1"), recent_behavior={"visits_in_window": 0}
            )
        ],
        now=_utc(110),
    )
    assert ev._active["V1"].confidence == 1.0


def test_fresh_recovery_preserves_active_risk():
    """FRESH 恢复：ACTIVE_RISK 状态与 raised_signal_id 保留。"""
    ev = _make_evaluator()
    snap = ActiveTrackSnapshot(
        visitor_instance_id="V1",
        phase="active_risk",
        raised_signal_id="sig-keep",
        raised_at=_utc(90),
        first_seen=_utc(10),
        last_seen_at=_utc(100),
    )
    ev.restore([snap], confidence=1.0)
    assert ev.active_risk_count == 1
    assert ev._active["V1"].raised_signal_id == "sig-keep"


def _track_state(phase: RiskPhase, vid: str):
    """构造私有 _TrackRiskState（绕过 import 私有名，用 evaluator 内部类）。"""
    from home_perception.analysis.realtime_risk_evaluator import _TrackRiskState

    return _TrackRiskState(
        phase=phase,
        raised_signal_id="sig-x" if phase is RiskPhase.ACTIVE_RISK else "",
        raised_at=_utc(90) if phase is RiskPhase.ACTIVE_RISK else None,
        first_seen=_utc(10),
        last_track_id=None,
        confidence=1.0,
    )

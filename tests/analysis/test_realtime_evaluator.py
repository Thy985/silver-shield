"""RealTimeRiskEvaluator 单元测试（Migration Stage C · Shadow Mode）。

覆盖工程方案 §4.2 硬性规则 + §8.1 测试清单：
- 状态机 NONE → RAISED(emit) → ACTIVE_RISK → CLEARED(emit) → NONE
- ACTIVE_RISK 内不重复 RAISED（去抖第一层）
- 离场兜底 CLEARED（phase==LEFT 或评估帧未见）
- 离场后条目删除（无泄漏）
- 重启丢弃 active 不补发 CLEARED（§4.3）
- 阈值来自 ThresholdConfig 非硬编码
- 状态机 key=visitor_instance_id：track_id 重用不串号
- CLEARED.paired_signal_id == 对应 RAISED.signal_id（成对性）
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from home_perception.analysis.behavior_state import (
    BehaviorPhase,
    BehaviorState,
    RealtimeContext,
)
from home_perception.analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
from home_perception.analysis.risk_signal import SignalTransition
from home_perception.analysis.rule_engine import ThresholdConfig


# ============================================================================
# 辅助构造
# ============================================================================

def _utc(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _state(
    vid: str,
    track_id: int = 1,
    dwell: float = 0.0,
    is_odd: bool = False,
    phase: BehaviorPhase = BehaviorPhase.ONGOING,
    first_seen: datetime | None = None,
    now: datetime | None = None,
) -> BehaviorState:
    """构造 BehaviorState（默认 ONGOING，dwell=0，非 odd_hour）。"""
    fs = first_seen or _utc(2026, 7, 27, 10, 0, 0)
    ls = now or _utc(2026, 7, 27, 10, 0, int(dwell) if dwell < 60 else 0)
    # 防 dwell>=60 时秒越界：用 first_seen + 分钟进位
    if dwell >= 60:
        ls = fs.replace(minute=fs.minute + int(dwell // 60), second=int(dwell % 60))
    return BehaviorState(
        track_id=track_id,
        visitor_instance_id=vid,
        phase=phase,
        first_seen=fs,
        last_seen=ls,
        dwell_seconds=dwell,
        is_odd_hour=is_odd,
        proximity_score=0.0,
    )


def _ctx(state: BehaviorState, visits: int = 0) -> RealtimeContext:
    return RealtimeContext(
        current_state=state,
        recent_behavior={"visits_in_window": visits},
    )


def _thresholds(
    long_duration: float = 300.0,
    repeat_count: int = 3,
) -> ThresholdConfig:
    return ThresholdConfig(
        long_duration_seconds=long_duration,
        repeat_visit_count=repeat_count,
    )


# ============================================================================
# 1. 状态机基础：NONE → RAISED → ACTIVE_RISK → CLEARED → NONE
# ============================================================================

class TestStateMachineBasics:
    def test_first_seen_no_trigger_creates_none_no_signal(self):
        """首次见到、未触发：创建 NONE 条目，不产信号。"""
        ev = RealTimeRiskEvaluator(_thresholds())
        vid = str(uuid4())
        ctxs = [_ctx(_state(vid, dwell=10.0))]  # dwell < 300，不触发

        signals = ev.evaluate(ctxs, _utc(2026, 7, 27, 10, 0, 10))

        assert signals == []
        assert ev.active_count == 1
        assert ev.active_risk_count == 0

    def test_first_seen_triggered_emits_raised(self):
        """首次见到且触发：直接 RAISED，phase=ACTIVE_RISK。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())
        ctxs = [_ctx(_state(vid, dwell=350.0))]  # dwell >= 300，触发

        signals = ev.evaluate(ctxs, _utc(2026, 7, 27, 10, 5, 50))

        assert len(signals) == 1
        s = signals[0]
        assert s.transition is SignalTransition.RAISED
        assert s.subject_id == vid
        assert s.paired_signal_id is None  # RAISED 必无配对
        assert s.visitor_instance_id == vid
        assert ev.active_risk_count == 1

    def test_active_risk_holds_no_repeat_raised(self):
        """ACTIVE_RISK 持续触发：不重复 RAISED（去抖第一层）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())
        now = _utc(2026, 7, 27, 10, 5, 50)

        # 帧 1：触发 RAISED
        s1 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now))], now)
        assert len(s1) == 1 and s1[0].transition is SignalTransition.RAISED

        # 帧 2：仍触发，不重复 RAISED
        now2 = _utc(2026, 7, 27, 10, 6, 50)
        s2 = ev.evaluate([_ctx(_state(vid, dwell=410.0, now=now2))], now2)
        assert s2 == []
        assert ev.active_risk_count == 1

    def test_recover_emits_cleared_paired(self):
        """ACTIVE_RISK 回落：emit CLEARED，paired_signal_id 正确。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1：RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        s1 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now1))], now1)
        raised_id = s1[0].signal_id

        # 帧 2：回落（dwell 重置为 0，新会话）
        now2 = _utc(2026, 7, 27, 11, 0, 0)
        s2 = ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now2))], now2)

        assert len(s2) == 1
        cleared = s2[0]
        assert cleared.transition is SignalTransition.CLEARED
        assert cleared.paired_signal_id == raised_id  # 配对性
        assert ev.active_risk_count == 0
        # 主体仍在场（phase=ONGOING），条目保留为 NONE
        assert ev.active_count == 1


# ============================================================================
# 2. 离场兜底
# ============================================================================

class TestLeaveFallback:
    def test_phase_left_emits_cleared_and_deletes(self):
        """phase==LEFT：强制 CLEARED + 删除条目（防泄漏）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1：RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        s1 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now1))], now1)
        raised_id = s1[0].signal_id

        # 帧 2：phase=LEFT（即便阈值仍满足也强制 CLEARED）
        now2 = _utc(2026, 7, 27, 10, 6, 50)
        left_state = _state(vid, dwell=410.0, phase=BehaviorPhase.LEFT, now=now2)
        s2 = ev.evaluate([_ctx(left_state)], now2)

        assert len(s2) == 1
        assert s2[0].transition is SignalTransition.CLEARED
        assert s2[0].paired_signal_id == raised_id
        assert ev.active_count == 0  # 条目已删除

    def test_subject_missing_emits_cleared(self):
        """主体从 ctxs 消失（评估帧未见）但 _active 仍 ACTIVE_RISK：补发 CLEARED。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1：RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        s1 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now1))], now1)
        raised_id = s1[0].signal_id

        # 帧 2：空 ctxs（主体已离场，tracker.active() 不含它）
        now2 = _utc(2026, 7, 27, 10, 6, 50)
        s2 = ev.evaluate([], now2)

        assert len(s2) == 1
        assert s2[0].transition is SignalTransition.CLEARED
        assert s2[0].paired_signal_id == raised_id
        assert s2[0].features.get("reason") == "subject_missing"
        assert ev.active_count == 0  # 条目已删除

    def test_none_subject_missing_silent_delete(self):
        """NONE 状态的未见主体：直接删除，无信号产出。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1：创建 NONE 条目（不触发）
        now1 = _utc(2026, 7, 27, 10, 0, 10)
        ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now1))], now1)
        assert ev.active_count == 1

        # 帧 2：主体消失，NONE 状态直接删除，无信号
        now2 = _utc(2026, 7, 27, 10, 0, 20)
        s2 = ev.evaluate([], now2)
        assert s2 == []
        assert ev.active_count == 0


# ============================================================================
# 3. 重启丢弃语义（§4.3）
# ============================================================================

class TestResetSemantics:
    def test_reset_clears_active_no_cleared_emitted(self):
        """reset() 清空全部状态，不补发 CLEARED（§4.3 volatile 语义）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1：RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now1))], now1)
        assert ev.active_risk_count == 1

        # 模拟进程重启
        ev.reset()

        assert ev.active_count == 0
        assert ev.active_risk_count == 0

        # 重启后同一主体再次出现：当作首次见到，不补发 CLEARED
        now2 = _utc(2026, 7, 27, 10, 6, 0)
        s2 = ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now2))], now2)
        assert s2 == []  # 无 CLEARED，仅创建 NONE 条目


# ============================================================================
# 4. track_id 重用不串号（key=visitor_instance_id）
# ============================================================================

class TestTrackIdReuseNoBleed:
    def test_track_id_reuse_does_not_inherit_active_risk(self):
        """A 离场后 B 复用同 track_id，B 不继承 A 的 ACTIVE_RISK（key=visitor_instance_id）。

        场景（工程方案 §4.1 key 选型）：
        - A (vid=A, track_id=1) 触发 RAISED → ACTIVE_RISK
        - A 离场 → CLEARED + 删除条目
        - B (vid=B, track_id=1) 进场 → 全新 NONE，不继承 A 的状态
        """
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid_a = str(uuid4())
        vid_b = str(uuid4())  # 不同 UUID，但复用 track_id=1

        # 帧 1：A 触发 RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        s1 = ev.evaluate([_ctx(_state(vid_a, track_id=1, dwell=350.0, now=now1))], now1)
        assert len(s1) == 1 and s1[0].transition is SignalTransition.RAISED

        # 帧 2：A 离场（消失），补发 CLEARED
        now2 = _utc(2026, 7, 27, 10, 6, 50)
        s2 = ev.evaluate([], now2)
        assert len(s2) == 1 and s2[0].transition is SignalTransition.CLEARED

        # 帧 3：B 进场（同 track_id=1，不同 vid），dwell 短不触发 → 全新 NONE
        now3 = _utc(2026, 7, 27, 11, 0, 0)
        s3 = ev.evaluate([_ctx(_state(vid_b, track_id=1, dwell=5.0, now=now3))], now3)
        assert s3 == []  # B 不继承 A 的 ACTIVE_RISK，无信号
        assert ev.active_risk_count == 0
        assert ev.active_count == 1  # 仅 B 的 NONE 条目


# ============================================================================
# 5. 阈值来自 ThresholdConfig（非硬编码）
# ============================================================================

class TestThresholdsFromConfig:
    def test_dwell_threshold_from_config(self):
        """dwell 阈值由 ThresholdConfig.long_duration_seconds 决定。"""
        # 阈值 100s：dwell=150 触发
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=100.0))
        vid = str(uuid4())
        s = ev.evaluate([_ctx(_state(vid, dwell=150.0))], _utc(2026, 7, 27, 10, 2, 30))
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED

    def test_visits_threshold_from_config(self):
        """visits_in_window 阈值由 ThresholdConfig.repeat_visit_count 决定。"""
        ev = RealTimeRiskEvaluator(_thresholds(repeat_count=2))
        vid = str(uuid4())
        # dwell 不触发，但 visits=2 >= 2 触发
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=10.0), visits=2)],
            _utc(2026, 7, 27, 10, 0, 10),
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED

    def test_odd_hour_triggers(self):
        """is_odd_hour=True 触发 RAISED（即便 dwell/visits 不超阈）。"""
        ev = RealTimeRiskEvaluator(_thresholds())
        vid = str(uuid4())
        # dwell=10, visits=0，但 is_odd_hour=True（23:00 UTC）
        now = _utc(2026, 7, 27, 23, 0, 10)
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=10.0, is_odd=True, now=now))],
            now,
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED


# ============================================================================
# 5b. features 反映实际触发证据（防 visits_in_window 硬编码 0 回归）
# ============================================================================

class TestRaisedFeaturesReflectTrigger:
    """RAISED 信号的 features 必须反映实际触发证据，不能硬编码。

    回归保护：早期实现把 visits_in_window 硬编码为 0，导致由 visits 触发的
    RAISED 信号经 signal_adapter 映射时落入兜底分支返回 visit_pending_verify
    （错误映射）。本组测试断言 features 字段与输入一致。
    """

    def test_visits_triggered_features_visits_in_window_matches(self):
        """visits 触发：features.visits_in_window 必须等于输入的 visits 值。"""
        ev = RealTimeRiskEvaluator(_thresholds(repeat_count=2))
        vid = str(uuid4())
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=10.0), visits=5)],
            _utc(2026, 7, 27, 10, 0, 10),
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED
        # 关键断言：features.visits_in_window 反映实际 visits=5，不是 0
        assert s[0].features["visits_in_window"] == 5

    def test_dwell_triggered_features_dwell_seconds_matches(self):
        """dwell 触发：features.dwell_seconds 必须等于输入的 dwell 值（rounded）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=350.5))],
            _utc(2026, 7, 27, 10, 5, 50),
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED
        assert s[0].features["dwell_seconds"] == 350.5

    def test_visits_triggered_features_visits_in_window_zero_when_no_visits(self):
        """visits 未触发（=0）：features.visits_in_window=0（与输入一致，非硬编码）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())
        # dwell 触发，visits=0
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=350.0), visits=0)],
            _utc(2026, 7, 27, 10, 5, 50),
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED
        assert s[0].features["visits_in_window"] == 0  # 与输入一致

    def test_odd_hour_triggered_features_is_odd_hour_true(self):
        """odd_hour 触发：features.is_odd_hour=True。"""
        ev = RealTimeRiskEvaluator(_thresholds())
        vid = str(uuid4())
        now = _utc(2026, 7, 27, 23, 0, 10)
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=10.0, is_odd=True, now=now))],
            now,
        )
        assert len(s) == 1 and s[0].transition is SignalTransition.RAISED
        assert s[0].features["is_odd_hour"] is True

    def test_features_thresholds_present(self):
        """features.thresholds 必须含 long_duration_seconds + repeat_visit_count。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0, repeat_count=3))
        vid = str(uuid4())
        s = ev.evaluate(
            [_ctx(_state(vid, dwell=350.0))],
            _utc(2026, 7, 27, 10, 5, 50),
        )
        assert len(s) == 1
        th = s[0].features["thresholds"]
        assert th["long_duration_seconds"] == 300.0
        assert th["repeat_visit_count"] == 3


# ============================================================================
# 6. 输入校验
# ============================================================================

class TestInputValidation:
    def test_naive_now_rejected(self):
        """naive datetime 被 require_utc 拒绝。"""
        ev = RealTimeRiskEvaluator(_thresholds())
        naive_now = datetime(2026, 7, 27, 10, 0, 0)  # 无 tzinfo
        with pytest.raises(ValueError, match="now 必须是 timezone-aware"):
            ev.evaluate([], naive_now)

    def test_empty_ctxs_returns_empty_signals(self):
        """空 ctxs 产出空 signals（无主体评估）。"""
        ev = RealTimeRiskEvaluator(_thresholds())
        s = ev.evaluate([], _utc(2026, 7, 27, 10, 0, 0))
        assert s == []


# ============================================================================
# 7. 端到端状态机序列：NONE→ACTIVE_RISK→NONE（防 Dashboard 闪烁）
# ============================================================================

class TestStateMachineSequence:
    """验证状态机序列：多帧触发只产 1 RAISED + 1 CLEARED。

    防回归：早期实现可能在每帧触发都产 RAISED，导致 Dashboard 风险卡闪烁
    （红卡反复亮起）。正确行为是首次触发 RAISED 后持续 ACTIVE_RISK，
    直到回落或离场才产 CLEARED。
    """

    def test_multi_frame_trigger_only_one_raised_one_cleared(self):
        """5 帧持续触发 + 1 帧回落 → 只产 1 RAISED + 1 CLEARED。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 帧 1-5：持续触发（dwell 递增 350→750）
        raised_count = 0
        for i in range(5):
            now = _utc(2026, 7, 27, 10, 5 + i, 50)
            sigs = ev.evaluate([_ctx(_state(vid, dwell=350.0 + i * 60.0, now=now))], now)
            raised_count += sum(1 for s in sigs if s.transition is SignalTransition.RAISED)
        assert raised_count == 1, f"5 帧触发应只产 1 RAISED，实际 {raised_count}"

        # 帧 6：回落（dwell 重置）
        now6 = _utc(2026, 7, 27, 11, 0, 0)
        sigs6 = ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now6))], now6)
        cleared_count = sum(1 for s in sigs6 if s.transition is SignalTransition.CLEARED)
        assert cleared_count == 1, f"回落应产 1 CLEARED，实际 {cleared_count}"

    def test_raised_cleared_then_raised_again(self):
        """RAISED → CLEARED → 再次 RAISED（新风险周期）。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())

        # 周期 1：RAISED
        now1 = _utc(2026, 7, 27, 10, 5, 50)
        s1 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now1))], now1)
        assert len(s1) == 1 and s1[0].transition is SignalTransition.RAISED

        # 周期 1：CLEARED（回落）
        now2 = _utc(2026, 7, 27, 11, 0, 0)
        s2 = ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now2))], now2)
        assert len(s2) == 1 and s2[0].transition is SignalTransition.CLEARED

        # 周期 2：再次 RAISED（新风险）
        now3 = _utc(2026, 7, 27, 11, 10, 50)
        s3 = ev.evaluate([_ctx(_state(vid, dwell=350.0, now=now3))], now3)
        assert len(s3) == 1 and s3[0].transition is SignalTransition.RAISED
        # 新 RAISED 的 signal_id 不同于周期 1
        assert s3[0].signal_id != s1[0].signal_id

    def test_no_raised_when_never_triggered(self):
        """从未触发的主体：全程无 RAISED 无 CLEARED。"""
        ev = RealTimeRiskEvaluator(_thresholds(long_duration=300.0))
        vid = str(uuid4())
        # 5 帧 dwell=10（远低于阈值 300）
        for i in range(5):
            now = _utc(2026, 7, 27, 10, 0, i * 10)
            sigs = ev.evaluate([_ctx(_state(vid, dwell=10.0, now=now))], now)
            assert sigs == [], f"帧 {i} 不应产信号"

"""BehaviorState / RealtimeContext 单元测试（ADR-0021 §3.2，Migration Stage A）。

只测类型自身的数据契约与不变式（torch-free，进 CI 每 PR 合约子集）。

覆盖（对齐工程方案 §8.1）：
- 进入→dwell 累计→离开 phase 翻转（now_provider 驱动，非墙钟）
- schema_version=1
- **纯态断言：BehaviorState 无 `visits_in_window` 字段**（跨访问统计归 RecentBehaviorStore）
- proximity_score clamp [0,1]
- BehaviorPhase 枚举闭合（4 值，含预留 APPROACHING/DEPARTING）
- is_odd_hour 纯函数
- RealtimeContext 组合体
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from home_perception.analysis.behavior_state import (
    BEHAVIOR_PHASE_VALUES,
    BehaviorPhase,
    BehaviorState,
    RealtimeContext,
    compute_is_odd_hour,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> datetime:
    """测试用注入时钟（now_provider 的等价物，返回 datetime UTC）。"""
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _state_at(phase: BehaviorPhase, enter: datetime, now: datetime, track_id: int = 1,
              vid: str = "vid-1") -> BehaviorState:
    return BehaviorState(
        track_id=track_id,
        visitor_instance_id=vid,
        phase=phase,
        first_seen=enter,
        last_seen=now,
        dwell_seconds=(now - enter).total_seconds(),
        is_odd_hour=compute_is_odd_hour(now),
    )


# ---------------------------------------------------------------------------
# 进入 → dwell 累计 → 离开 phase 翻转（now_provider 驱动）
# ---------------------------------------------------------------------------

def test_lifecycle_dwell_accumulation_and_phase_flip():
    enter = _utc(2026, 7, 26, 10, 0, 0)
    # 帧 1：刚进入，dwell 0，ONGOING
    f1 = _state_at(BehaviorPhase.ONGOING, enter, enter)
    assert f1.dwell_seconds == 0.0
    assert f1.phase is BehaviorPhase.ONGOING
    # 帧 2：60 秒后，dwell 累计 = 60，仍 ONGOING
    f2 = _state_at(BehaviorPhase.ONGOING, enter, enter + timedelta(seconds=60))
    assert f2.dwell_seconds == 60.0
    assert f2.phase is BehaviorPhase.ONGOING
    # 帧 3：300 秒后，dwell = 300，离开 → LEFT
    f3 = _state_at(BehaviorPhase.LEFT, enter, enter + timedelta(seconds=300))
    assert f3.dwell_seconds == 300.0
    assert f3.phase is BehaviorPhase.LEFT


def test_is_odd_hour_changes_with_injected_clock():
    """is_odd_hour 由注入时刻驱动，不依赖墙钟。"""
    day = _utc(2026, 7, 26, 12, 0, 0)   # 中午，非异时
    night = _utc(2026, 7, 26, 23, 30, 0)  # 深夜，异时
    assert _state_at(BehaviorPhase.ONGOING, day, day).is_odd_hour is False
    assert _state_at(BehaviorPhase.ONGOING, night, night).is_odd_hour is True


# ---------------------------------------------------------------------------
# schema_version = 1
# ---------------------------------------------------------------------------

def test_schema_version_default_one():
    s = _state_at(BehaviorPhase.ONGOING, _utc(2026, 7, 26, 10), _utc(2026, 7, 26, 10))
    assert s.schema_version == 1


# ---------------------------------------------------------------------------
# 纯态断言：无 visits_in_window
# ---------------------------------------------------------------------------

def test_no_visits_in_window_field():
    """BehaviorState 是纯当前生命周期态，不含跨访问统计 visits_in_window。"""
    s = _state_at(BehaviorPhase.ONGOING, _utc(2026, 7, 26, 10), _utc(2026, 7, 26, 10, 1))
    assert not hasattr(s, "visits_in_window")
    assert "visits_in_window" not in s.to_dict()


# ---------------------------------------------------------------------------
# proximity_score clamp [0,1]
# ---------------------------------------------------------------------------

def test_proximity_score_clamped():
    enter = _utc(2026, 7, 26, 10)
    now = _utc(2026, 7, 26, 10, 0, 5)
    over = BehaviorState(1, "vid", BehaviorPhase.ONGOING, enter, now, 5.0, False,
                         proximity_score=1.7)
    under = BehaviorState(1, "vid", BehaviorPhase.ONGOING, enter, now, 5.0, False,
                          proximity_score=-0.3)
    assert over.proximity_score == 1.0
    assert under.proximity_score == 0.0


def test_proximity_score_default_zero():
    s = _state_at(BehaviorPhase.ONGOING, _utc(2026, 7, 26, 10), _utc(2026, 7, 26, 10))
    assert s.proximity_score == 0.0


# ---------------------------------------------------------------------------
# BehaviorPhase 枚举闭合（含预留）
# ---------------------------------------------------------------------------

def test_behavior_phase_enum_closed():
    assert set(BEHAVIOR_PHASE_VALUES) == {
        "ongoing",
        "left",
        "approaching",
        "departing",
    }


def test_phase_accepts_str():
    s = BehaviorState(1, "vid", "ongoing", _utc(2026, 7, 26, 10),
                      _utc(2026, 7, 26, 10), 0.0, False)
    assert s.phase is BehaviorPhase.ONGOING


# ---------------------------------------------------------------------------
# 时间不变式
# ---------------------------------------------------------------------------

def test_rejects_naive_datetime():
    with pytest.raises(ValueError):
        BehaviorState(1, "vid", BehaviorPhase.ONGOING, datetime(2026, 7, 26, 10),
                      _utc(2026, 7, 26, 10), 0.0, False)


def test_rejects_last_before_first():
    with pytest.raises(ValueError):
        BehaviorState(1, "vid", BehaviorPhase.ONGOING, _utc(2026, 7, 26, 10, 5),
                      _utc(2026, 7, 26, 10, 0), 0.0, False)


def test_rejects_negative_dwell():
    with pytest.raises(ValueError):
        BehaviorState(1, "vid", BehaviorPhase.ONGOING, _utc(2026, 7, 26, 10),
                      _utc(2026, 7, 26, 10), -1.0, False)


# ---------------------------------------------------------------------------
# RealtimeContext 组合体
# ---------------------------------------------------------------------------

def test_realtime_context_combines_state_and_recent():
    enter = _utc(2026, 7, 26, 10)
    now = _utc(2026, 7, 26, 10, 2)
    state = _state_at(BehaviorPhase.ONGOING, enter, now)
    ctx = RealtimeContext(current_state=state, recent_behavior={"visits_in_window": 3})
    d = ctx.to_dict()
    assert d["current_state"]["visitor_instance_id"] == "vid-1"
    assert d["recent_behavior"] == {"visits_in_window": 3}


def test_realtime_context_requires_behavior_state():
    with pytest.raises(TypeError):
        RealtimeContext(current_state={"phase": "ongoing"})  # 非 BehaviorState


# ---------------------------------------------------------------------------
# from_dict 反序列化（与 to_dict 严格对称）
# ---------------------------------------------------------------------------

def test_from_dict_roundtrip():
    """to_dict → from_dict → to_dict 应产出相同字典（round-trip 对称）。"""
    enter = _utc(2026, 7, 26, 10, 0, 0)
    now = _utc(2026, 7, 26, 10, 5, 30)
    original = BehaviorState(
        track_id=7,
        visitor_instance_id="vid-roundtrip",
        phase=BehaviorPhase.LEFT,
        first_seen=enter,
        last_seen=now,
        dwell_seconds=330.0,
        is_odd_hour=False,
        proximity_score=0.42,
    )
    d1 = original.to_dict()
    restored = BehaviorState.from_dict(d1)
    d2 = restored.to_dict()
    assert d1 == d2


def test_from_dict_accepts_str_phase():
    """from_dict 接受字符串 phase 值（枚举归一）。"""
    enter = _utc(2026, 7, 26, 10, 0, 0)
    now = _utc(2026, 7, 26, 10, 0, 10)
    d = {
        "track_id": 1,
        "visitor_instance_id": "vid",
        "phase": "ongoing",
        "first_seen": enter.isoformat(),
        "last_seen": now.isoformat(),
        "dwell_seconds": 10.0,
        "is_odd_hour": False,
        "proximity_score": 0.0,
        "schema_version": 1,
    }
    s = BehaviorState.from_dict(d)
    assert s.phase is BehaviorPhase.ONGOING
    assert s.first_seen == enter
    assert s.last_seen == now


def test_from_dict_rejects_invalid_phase():
    """from_dict 接受非法 phase 值应抛 ValueError（枚举闭合）。"""
    d = {
        "track_id": 1,
        "visitor_instance_id": "vid",
        "phase": "unknown_phase",
        "first_seen": _utc(2026, 7, 26, 10).isoformat(),
        "last_seen": _utc(2026, 7, 26, 10).isoformat(),
        "dwell_seconds": 0.0,
        "is_odd_hour": False,
        "proximity_score": 0.0,
        "schema_version": 1,
    }
    with pytest.raises(ValueError):
        BehaviorState.from_dict(d)

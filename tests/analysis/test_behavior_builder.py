"""BehaviorBuilder 单元测试（ADR-0021 State Layer，Migration Stage B）。

torch-free，进 CI 每 PR 合约子集。

覆盖：
- active track → BehaviorState(phase=ONGOING) 投影
- visitor_instance_id 来自 event_builder.visitor_id_for（UUID str）
- dwell_seconds = (now - enter_time).total_seconds()
- is_odd_hour 由注入 now 驱动（非墙钟）
- 多 track 同时构建 / 空 tracks
- 跳过 enter_time=None 的 track（防御）
- 跳过 event_builder 未分配 UUID 的 track（时序异常防御）
- proximity_score 恒 0.0（Stage B 占位，工程方案附录 O1）
- naive now 被拒绝（对齐 ADR-0007）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import pytest

from home_perception.analysis.behavior_builder import BehaviorBuilder
from home_perception.analysis.behavior_state import BehaviorPhase
from home_perception.detection.schemas import ACTIVE, VisitorTrack


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _track(
    track_id: int,
    enter: datetime,
    last: Optional[datetime] = None,
) -> VisitorTrack:
    """构造 active VisitorTrack（绕过 tracker，直接建状态对象）。"""
    return VisitorTrack(
        track_id=track_id,
        first_seen=enter,
        last_seen=last or enter,
        frame_count=1,
        bbox=None,
        confidence=0.9,
        status=ACTIVE,
        enter_time=enter,
        leave_time=None,
    )


class _FakeEventBuilder:
    """模拟 VisitorEventBuilder 的 visitor_id_for 接口（仅暴露 BehaviorBuilder 依赖的方法）。"""

    def __init__(self, mapping: dict[int, object]) -> None:
        self._map = mapping

    def visitor_id_for(self, track_id: int) -> Optional[object]:
        return self._map.get(track_id)


# ---------------------------------------------------------------------------
# 基本投影
# ---------------------------------------------------------------------------

def test_build_projects_active_track_to_ongoing_state():
    """active track → BehaviorState(phase=ONGOING)，字段正确投影。"""
    enter = _utc(2026, 7, 27, 10, 0, 0)
    now = _utc(2026, 7, 27, 10, 0, 30)
    vid = uuid4()
    builder = BehaviorBuilder(_FakeEventBuilder({1: vid}))

    states = builder.build([_track(1, enter)], now)

    assert len(states) == 1
    s = states[0]
    assert s.track_id == 1
    assert s.visitor_instance_id == str(vid)
    assert s.phase is BehaviorPhase.ONGOING
    assert s.first_seen == enter
    assert s.last_seen == now
    assert s.dwell_seconds == 30.0
    assert s.proximity_score == 0.0


def test_dwell_accumulates_with_injected_now():
    """dwell_seconds 由注入 now 驱动，非墙钟；随 now 增长。"""
    enter = _utc(2026, 7, 27, 10, 0, 0)
    builder = BehaviorBuilder(_FakeEventBuilder({1: uuid4()}))

    s1 = builder.build([_track(1, enter)], _utc(2026, 7, 27, 10, 0, 10))[0]
    # 注意：_utc 的 s 参数遵循 datetime 秒域 [0,59]；60s 间隔用分钟进位表达
    s2 = builder.build([_track(1, enter)], _utc(2026, 7, 27, 10, 1, 0))[0]
    assert s1.dwell_seconds == 10.0
    assert s2.dwell_seconds == 60.0


def test_is_odd_hour_driven_by_injected_now():
    """is_odd_hour 由注入 now 决定（非墙钟）。"""
    enter = _utc(2026, 7, 27, 10, 0, 0)
    builder = BehaviorBuilder(_FakeEventBuilder({1: uuid4()}))

    day_now = _utc(2026, 7, 27, 12, 0, 0)
    night_now = _utc(2026, 7, 27, 23, 30, 0)
    assert builder.build([_track(1, enter)], day_now)[0].is_odd_hour is False
    assert builder.build([_track(1, enter)], night_now)[0].is_odd_hour is True


# ---------------------------------------------------------------------------
# 多 track / 空
# ---------------------------------------------------------------------------

def test_build_multiple_tracks():
    """多 track 同时构建，各自独立。"""
    enter = _utc(2026, 7, 27, 10, 0, 0)
    now = _utc(2026, 7, 27, 10, 0, 5)
    vid1, vid2 = uuid4(), uuid4()
    builder = BehaviorBuilder(_FakeEventBuilder({1: vid1, 2: vid2}))

    states = builder.build([_track(1, enter), _track(2, enter)], now)
    assert len(states) == 2
    assert {s.track_id for s in states} == {1, 2}
    assert {s.visitor_instance_id for s in states} == {str(vid1), str(vid2)}


def test_empty_tracks_returns_empty_list():
    """空 tracks → 空 list。"""
    builder = BehaviorBuilder(_FakeEventBuilder({}))
    assert builder.build([], _utc(2026, 7, 27, 10)) == []


# ---------------------------------------------------------------------------
# 跳过防御
# ---------------------------------------------------------------------------

def test_skip_track_with_missing_enter_time():
    """enter_time=None 的 track 被跳过（防御，理论不会）。"""
    now = _utc(2026, 7, 27, 10, 0, 0)
    vt = _track(1, now)
    vt.enter_time = None  # 强制置空
    builder = BehaviorBuilder(_FakeEventBuilder({1: uuid4()}))
    assert builder.build([vt], now) == []


def test_skip_track_with_missing_visitor_id():
    """event_builder 未分配 UUID 的 track 被跳过（时序异常防御）。"""
    enter = _utc(2026, 7, 27, 10, 0, 0)
    now = _utc(2026, 7, 27, 10, 0, 5)
    # event_builder 对 track_id=1 返回 None
    builder = BehaviorBuilder(_FakeEventBuilder({}))
    assert builder.build([_track(1, enter)], now) == []


# ---------------------------------------------------------------------------
# 时间校验
# ---------------------------------------------------------------------------

def test_rejects_naive_now():
    """naive now 必须拒绝（对齐 ADR-0007，防跨设备时间漂移）。"""
    builder = BehaviorBuilder(_FakeEventBuilder({}))
    with pytest.raises(ValueError):
        builder.build([], datetime(2026, 7, 27, 10))  # naive


def test_dwell_clamped_non_negative_on_clock_backdrop():
    """时钟回拨（now < enter_time）时 dwell 钳为 0，不产生负值。"""
    enter = _utc(2026, 7, 27, 10, 0, 10)
    now = _utc(2026, 7, 27, 10, 0, 0)  # 比 enter 早 10s
    builder = BehaviorBuilder(_FakeEventBuilder({1: uuid4()}))
    s = builder.build([_track(1, enter)], now)[0]
    assert s.dwell_seconds == 0.0

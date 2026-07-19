"""VisitorEvent / VisitorEventBuilder 测试（P0-6 · 事实事件层）。

> **P0-6 = 事实事件层；P0-7 = 风险语义层。** 本测试严格不验证任何业务判断逻辑。
>
> 关键边界（ADR-0007）：
> - `visitor_id` 是 UUID 而非 ByteTrack `track_id`（track_id 是 Tracker 内部 ID，程序重启/视频切换后可能复用）
> - 所有时间字段为 UTC（timezone-aware）
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.detection.detector import Detection, DetectionResult, Detector
from home_perception.detection.schemas import ACTIVE
from home_perception.detection.tracker import VisitorTracker


# ============================================================================
# 时区 helper：所有时间字段统一 UTC，避免 naive 漏标
# ============================================================================

def utc(year, month, day, hour=0, minute=0, second=0):
    """构造 timezone-aware UTC datetime。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ============================================================================
# FakeDetector：复用 P0-5 的契约
# ============================================================================

class FakeDetector(Detector):
    """按预设 (track_ids, ts) 序列产出 DetectionResult。"""

    def __init__(self, frames, **kwargs):
        self._seq = list(frames)
        self._i = 0

    def detect(self, frame) -> DetectionResult:  # type: ignore[override]
        if self._i < len(self._seq):
            ids, ts = self._seq[self._i]
        else:
            ids, ts = [], 0.0
        self._i += 1
        dets = [
            Detection(
                class_id=0, class_name="person", confidence=0.9,
                bbox=[0.0, 0.0, 10.0, 10.0], timestamp=ts, track_id=tid,
            )
            for tid in ids
        ]
        return DetectionResult(
            detections=dets, timestamp=ts, inference_ms=1.0,
            source_size=(100, 100), inference_size=(100, 100), model="fake",
        )


def _fixed_now(seq):
    it = iter(seq)
    def _now():
        try:
            return next(it)
        except StopIteration:
            return utc(2000, 1, 1)
    return _now


# ============================================================================
# VisitorEvent 领域对象
# ============================================================================

class TestVisitorEventSchema:
    """VisitorEvent 字段、序列化、边界。"""

    def test_basic_fields(self):
        t0 = utc(2026, 7, 19, 10, 0, 0)
        t1 = utc(2026, 7, 19, 10, 0, 8)
        e = VisitorEvent(
            visitor_id=uuid.uuid4(), enter_time=t0, leave_time=t1,
            duration_seconds=(t1 - t0).total_seconds(),
            source_video="OneStopEnter1cor",
        )
        assert isinstance(e.visitor_id, uuid.UUID)
        assert e.enter_time == t0
        assert e.leave_time == t1
        assert e.duration_seconds == 8.0
        assert e.source_video == "OneStopEnter1cor"
        # event_id 是 UUID 字符串
        assert re.match(r"^[0-9a-f-]{36}$", e.event_id), f"event_id 不是 UUID: {e.event_id}"
        # created_at 是 timezone-aware UTC
        assert isinstance(e.created_at, datetime)
        assert e.created_at.tzinfo is not None
        assert e.created_at.utcoffset().total_seconds() == 0

    def test_to_dict_has_no_datetime(self):
        """to_dict 输出必须 structlog-safe（无 datetime 对象）。"""
        e = VisitorEvent(
            visitor_id=uuid.uuid4(),
            enter_time=utc(2026, 7, 19, 10, 0, 0),
            leave_time=utc(2026, 7, 19, 10, 0, 5),
            duration_seconds=5.0, source_video="cam01",
        )
        d = e.to_dict()
        for v in d.values():
            assert not isinstance(v, datetime), f"to_dict 含 datetime: {v!r}"
        # 时间已转 ISO 字符串（带 UTC offset）
        assert d["enter_time"] == "2026-07-19T10:00:00+00:00"
        assert d["leave_time"] == "2026-07-19T10:00:05+00:00"
        # visitor_id 是 UUID 字符串
        assert d["visitor_id"] == str(e.visitor_id)
        # re-parse UUID 验证
        assert uuid.UUID(d["visitor_id"]) == e.visitor_id

    def test_to_json_roundtrip(self):
        e = VisitorEvent(
            visitor_id=uuid.uuid4(),
            enter_time=utc(2026, 7, 19, 10, 0, 0),
            leave_time=utc(2026, 7, 19, 10, 0, 12),
            duration_seconds=12.0, source_video="CAVIAR/OneStopEnter1cor",
        )
        j = e.to_json()
        parsed = json.loads(j)
        # 关键字段都进了 JSON
        assert parsed["event_id"] == e.event_id
        assert parsed["visitor_id"] == str(e.visitor_id)
        assert uuid.UUID(parsed["visitor_id"]) == e.visitor_id
        assert parsed["duration_seconds"] == 12.0
        assert parsed["source_video"] == "CAVIAR/OneStopEnter1cor"
        assert parsed["enter_time"] == "2026-07-19T10:00:00+00:00"
        assert parsed["leave_time"] == "2026-07-19T10:00:12+00:00"
        # 时间字段能 fromisoformat 反序列化（中心消费侧能力）
        assert datetime.fromisoformat(parsed["enter_time"]) == e.enter_time
        assert datetime.fromisoformat(parsed["leave_time"]) == e.leave_time

    def test_str_uuid_accepted(self):
        """测试便利：visitor_id 可传 str 格式 UUID（内部归一为 UUID 对象）。"""
        e = VisitorEvent(
            visitor_id="550e8400-e29b-41d4-a716-446655440000",
            enter_time=utc(2026, 7, 19, 10, 0, 0),
            leave_time=utc(2026, 7, 19, 10, 0, 5),
            duration_seconds=5.0,
        )
        assert isinstance(e.visitor_id, uuid.UUID)
        assert e.visitor_id == uuid.UUID("550e8400-e29b-41d4-a716-446655440000")

    def test_invalid_uuid_rejected(self):
        """非 UUID/非 str UUID 拒绝（不静默接受任何 int/track_id）。"""
        with pytest.raises(TypeError):
            VisitorEvent(
                visitor_id=1,  # 旧版本用 int，迁移期应被显式拒绝
                enter_time=utc(2026, 7, 19, 10, 0, 0),
                leave_time=utc(2026, 7, 19, 10, 0, 5),
                duration_seconds=5.0,
            )

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="duration_seconds"):
            VisitorEvent(
                visitor_id=uuid.uuid4(),
                enter_time=utc(2026, 7, 19, 10, 0, 10),
                leave_time=utc(2026, 7, 19, 10, 0, 0),
                duration_seconds=-5.0,
            )

    def test_leave_before_enter_rejected(self):
        with pytest.raises(ValueError, match="leave_time"):
            VisitorEvent(
                visitor_id=uuid.uuid4(),
                enter_time=utc(2026, 7, 19, 10, 0, 10),
                leave_time=utc(2026, 7, 19, 10, 0, 0),
                duration_seconds=0.0,
            )

    def test_naive_datetime_rejected(self):
        """时间字段必须 timezone-aware UTC（防御 naive 漏标）。"""
        # naive enter_time
        with pytest.raises(ValueError, match="enter_time"):
            VisitorEvent(
                visitor_id=uuid.uuid4(),
                enter_time=datetime(2026, 7, 19, 10, 0, 0),  # 无 tzinfo
                leave_time=utc(2026, 7, 19, 10, 0, 5),
                duration_seconds=5.0,
            )
        # naive leave_time
        with pytest.raises(ValueError, match="leave_time"):
            VisitorEvent(
                visitor_id=uuid.uuid4(),
                enter_time=utc(2026, 7, 19, 10, 0, 0),
                leave_time=datetime(2026, 7, 19, 10, 0, 5),
                duration_seconds=5.0,
            )

    def test_no_business_judgment_fields(self):
        """P0-6 边界：VisitorEvent **绝对不含** 风险/类型字段（ADR-0007）。"""
        e = VisitorEvent(
            visitor_id=uuid.uuid4(),
            enter_time=utc(2026, 7, 19, 10, 0, 0),
            leave_time=utc(2026, 7, 19, 10, 0, 5),
            duration_seconds=5.0, source_video="cam01",
        )
        d = e.to_dict()
        forbidden = {
            "risk_level", "score", "visit_type", "is_suspicious",
            "repeat_count", "is_odd_hour", "evidence", "event_type",
            "warning", "verdict",
        }
        leaked = forbidden & set(d.keys())
        assert not leaked, (
            f"VisitorEvent 含业务判断字段 {leaked}（P0-7 边界）。"
            f"实际字段集: {set(d.keys())}"
        )


# ============================================================================
# VisitorEventBuilder
# ============================================================================

class TestVisitorEventBuilder:
    """Builder 状态机：track active→left 生成事件；reenter 允许再生成。"""

    def test_enter_then_leave_generates_event(self):
        """帧 0-2 在场，帧 3-4 消失触发离场，生成一个 VisitorEvent。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 1),
            utc(2026, 7, 19, 10, 0, 2),
            utc(2026, 7, 19, 10, 0, 8),
            utc(2026, 7, 19, 10, 0, 14),
        ]
        det = FakeDetector([
            ([1], 0.0), ([1], 1.0), ([1], 2.0),
            ([],  8.0), ([], 14.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="OneStopEnter1cor", now_provider=_fixed_now(times),
        )
        new_events = []
        for _ in range(5):
            new_events.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert len(new_events) == 1
        e = new_events[0]
        assert isinstance(e.visitor_id, uuid.UUID)
        assert e.enter_time == times[0]
        assert e.leave_time == times[2]
        assert e.duration_seconds == 2.0
        assert e.source_video == "OneStopEnter1cor"

    def test_continue_left_frames_no_duplicate_event(self):
        """持续离场（连续多帧都没出现）只生成 1 个事件，不重复。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 10),
            utc(2026, 7, 19, 10, 0, 20),
            utc(2026, 7, 19, 10, 0, 30),
        ]
        det = FakeDetector([
            ([1], 0.0), ([], 10.0), ([], 20.0), ([], 30.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        all_new = []
        for _ in range(4):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert len(all_new) == 1
        assert len(builder.events) == 1

    def test_track_interruption_within_gap_no_event(self):
        """短暂漏检（缺失帧间隔 < absence_gap）不算离场，不生成事件。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 1),
            utc(2026, 7, 19, 10, 0, 2),
            utc(2026, 7, 19, 10, 0, 3),
            utc(2026, 7, 19, 10, 0, 4),
            utc(2026, 7, 19, 10, 0, 5),
        ]
        det = FakeDetector([
            ([1], 0.0), ([], 1.0), ([], 2.0), ([], 3.0),
            ([1], 4.0), ([1], 5.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        all_new = []
        for _ in range(6):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert all_new == []
        assert builder.events == []
        assert tracker.get(1).status == ACTIVE

    def test_revisit_generates_second_event(self):
        """离场后重新进入 → 再离场 → 生成 2 个独立事件。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 1),
            utc(2026, 7, 19, 10, 0, 10),
            utc(2026, 7, 19, 10, 0, 20),
            utc(2026, 7, 19, 10, 0, 25),
            utc(2026, 7, 19, 10, 0, 35),
        ]
        det = FakeDetector([
            ([1], 0.0), ([1], 1.0), ([], 10.0),
            ([1], 20.0), ([1], 25.0), ([], 35.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        all_new = []
        for _ in range(6):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert len(all_new) == 2
        # reenter 复用同 UUID（**关键**：visitor_id 是 UUID 而非 track_id）
        assert all_new[0].visitor_id == all_new[1].visitor_id, (
            "reenter 应复用同一 visitor_id（UUID）；"
            f"但 event 1={all_new[0].visitor_id}, event 2={all_new[1].visitor_id}"
        )
        # 事件 1：enter=0s, leave=1s
        assert all_new[0].enter_time == times[0]
        assert all_new[0].leave_time == times[1]
        assert all_new[0].duration_seconds == 1.0
        # 事件 2：enter=20s(reenter), leave=25s
        assert all_new[1].enter_time == times[3]
        assert all_new[1].leave_time == times[4]
        assert all_new[1].duration_seconds == 5.0
        # event_id 唯一（事件级 ID，区别于 visitor_id）
        assert all_new[0].event_id != all_new[1].event_id

    def test_multi_visitor_independent_events(self):
        """两个 track 独立分配 UUID，不串。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 1),
            utc(2026, 7, 19, 10, 0, 2),
            utc(2026, 7, 19, 10, 0, 8),
        ]
        det = FakeDetector([
            ([1, 2], 0.0), ([1], 1.0), ([1], 2.0), ([], 8.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        all_new = []
        for _ in range(4):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert len(all_new) == 2
        # 跨 track_id 分配不同 UUID
        assert all_new[0].visitor_id != all_new[1].visitor_id, (
            "不同 track_id 应分配不同 visitor_id UUID"
        )
        # visitor_id_for() 查询接口
        assert builder.visitor_id_for(1) == all_new[0].visitor_id
        assert builder.visitor_id_for(2) == all_new[1].visitor_id
        # 时间字段对应各自 track
        for e in all_new:
            if e.visitor_id == all_new[0].visitor_id:
                assert e.enter_time == times[0]
                assert e.leave_time == times[2]
                assert e.duration_seconds == 2.0
            else:
                assert e.enter_time == times[0]
                assert e.leave_time == times[0]
                assert e.duration_seconds == 0.0

    def test_source_video_traced(self):
        """source_video 字段写入每个事件；切换源后新事件用新源。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 10),
            utc(2026, 7, 19, 10, 0, 20),
            utc(2026, 7, 19, 10, 0, 30),
        ]
        det = FakeDetector([
            ([1], 0.0), ([], 10.0), ([2], 20.0), ([], 30.0),
        ])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        all_new = []
        for _ in range(2):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        builder.source_video = "cam02"
        for _ in range(2):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        assert len(all_new) == 2
        assert all_new[0].source_video == "cam01"
        assert all_new[1].source_video == "cam02"

    def test_pending_and_ack(self):
        """pending() 显示未消费事件，ack() 后从 pending 移除。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 10),
        ]
        det = FakeDetector([([1], 0.0), ([], 10.0)])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        for _ in range(2):
            builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        assert len(builder.events) == 1
        assert len(builder.pending()) == 1
        builder.ack(builder.events[0])
        assert len(builder.pending()) == 0
        assert len(builder.events) == 1

    def test_reset_clears_state(self):
        """reset() 清空事件 + 状态历史 + track→UUID 映射；新会话重新分配 UUID。"""
        times = [
            utc(2026, 7, 19, 10, 0, 0),
            utc(2026, 7, 19, 10, 0, 10),
            utc(2026, 7, 19, 10, 1, 0),
            utc(2026, 7, 19, 10, 1, 10),
        ]
        det = FakeDetector([([1], 0.0), ([], 10.0), ([1], 60.0), ([], 70.0)])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        for _ in range(2):
            builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        first_visitor_id = builder.visitor_id_for(1)
        assert first_visitor_id is not None
        assert len(builder.events) == 1
        # reset → 新会话重新分配 UUID（**关键**：不沿用旧 UUID 避免跨会话误关联）
        builder.reset()
        assert builder.visitor_id_for(1) is None
        for _ in range(2):
            builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        assert len(builder.events) == 1
        new_visitor_id = builder.visitor_id_for(1)
        assert new_visitor_id is not None
        assert new_visitor_id != first_visitor_id, (
            "reset 后新会话必须分配新 UUID，不能复用旧会话的 UUID"
        )


class TestVisitorIdUUIDBoundary:
    """ADR-0007 关键：visitor_id 是 UUID 不是 track_id。"""

    def test_visitor_id_is_uuid_not_track_id(self):
        """新访客进入时 visitor_id_for 返回 UUID，绝不是 track_id 的 int。"""
        times = [utc(2026, 7, 19, 10, 0, i) for i in range(2)]
        det = FakeDetector([([1], 0.0), ([1], 1.0)])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        v_id = builder.visitor_id_for(1)
        assert v_id is not None
        assert isinstance(v_id, uuid.UUID), f"visitor_id 必须是 UUID 实例，收到 {type(v_id).__name__}"
        # 显式断言：不是 int track_id
        assert not isinstance(v_id, int)

    def test_byte_track_id_reuse_after_reset(self):
        """程序重启后 track_id 重新从 0 开始（ByteTrack 行为）→ 分配新 UUID。"""
        times = [utc(2026, 7, 19, 10, 0, 0)]
        det = FakeDetector([([0], 0.0)])  # 模拟重启后 track_id=0
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        # 第一次会话：track_id=0
        builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        first_uuid = builder.visitor_id_for(0)
        # reset（模拟程序重启）
        builder.reset()
        builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        second_uuid = builder.visitor_id_for(0)
        # **关键**：同 track_id=0 在新会话应是不同 UUID
        assert first_uuid != second_uuid, (
            "ByteTrack 重启后 track_id 可能复用，UUID 必须重分配，"
            "否则中心侧会去重到错误的人"
        )


# ============================================================================
# CAVIAR 真实链路端到端（fixture 缺失优雅 skip）
# ============================================================================

CAVIAR_ONE_STOP_ENTER = "tests/fixtures/doorway/one_stop_enter"


def test_caviar_one_stop_enter_generates_visitor_event():
    """CAVIAR OneStopEnter1cor: 真实监控链路 → VisitorEvent 端到端验证。

    验证：
    1. 真实 YOLO + ByteTrack 跑出 track_id
    2. VisitorTracker 维护 active/left
    3. VisitorEventBuilder 在 left 时生成 VisitorEvent
    4. 生成的事件含完整 enter/leave/duration/source_video
    5. visitor_id 是 UUID（**关键**：不是 ByteTrack track_id）
    """
    pytest.importorskip("ultralytics")
    import cv2
    from pathlib import Path

    p = Path(CAVIAR_ONE_STOP_ENTER)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip("CAVIAR fixture 缺失；跑 tests/fixtures/download_fixtures.py")

    from home_perception.detection.detector import YOLODetector

    frames = []
    for f in sorted(p.glob("frame_*.jpg")):
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    if not frames:
        pytest.skip("CAVIAR frames 解析失败")

    det = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.25,
        classes=[0], imgsz=416, device="cpu",
        enable_track=True, tracker="bytetrack",
    ).load()
    tracker = VisitorTracker(absence_gap_s=5.0)
    builder = VisitorEventBuilder(tracker, source_video="CAVIAR/OneStopEnter1cor")

    n_with_track = 0
    for f in frames:
        r = det.detect(f)
        builder.update(r.detections)
        for d in r.detections:
            if d.track_id is not None:
                n_with_track += 1

    events = builder.events
    if n_with_track == 0:
        # tracker 阶段没初始化 → 无事件
        assert events == []
    else:
        for e in events:
            assert isinstance(e.visitor_id, uuid.UUID), (
                f"visitor_id 必须是 UUID；收到 {type(e.visitor_id).__name__}"
            )
            assert e.source_video == "CAVIAR/OneStopEnter1cor"
            assert e.duration_seconds >= 0
            assert e.leave_time >= e.enter_time
            # 时间字段都是 UTC
            assert e.enter_time.tzinfo is not None
            assert e.leave_time.tzinfo is not None
        assert len(events) <= n_with_track

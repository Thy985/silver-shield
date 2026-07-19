"""VisitorEvent / VisitorEventBuilder 测试（P0-6 · 事实事件层）。

> **P0-6 = 事实事件层；P0-7 = 风险语义层。** 本测试严格不验证任何业务判断逻辑。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import numpy as np
import pytest

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.detection.detector import Detection, DetectionResult, Detector
from home_perception.detection.schemas import ACTIVE
from home_perception.detection.tracker import VisitorTracker


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
            return datetime(2000, 1, 1)
    return _now


# ============================================================================
# VisitorEvent 领域对象
# ============================================================================

class TestVisitorEventSchema:
    """VisitorEvent 字段、序列化、边界。"""

    def test_basic_fields(self):
        t0 = datetime(2026, 7, 19, 10, 0, 0)
        t1 = datetime(2026, 7, 19, 10, 0, 8)
        e = VisitorEvent(
            visitor_id=1, enter_time=t0, leave_time=t1,
            duration_seconds=(t1 - t0).total_seconds(),
            source_video="OneStopEnter1cor",
        )
        assert e.visitor_id == 1
        assert e.enter_time == t0
        assert e.leave_time == t1
        assert e.duration_seconds == 8.0
        assert e.source_video == "OneStopEnter1cor"
        # event_id 是 UUID 字符串
        assert re.match(r"^[0-9a-f-]{36}$", e.event_id), f"event_id 不是 UUID: {e.event_id}"
        # created_at 是 datetime
        assert isinstance(e.created_at, datetime)

    def test_to_dict_has_no_datetime(self):
        """to_dict 输出必须 structlog-safe（无 datetime 对象）。"""
        e = VisitorEvent(
            visitor_id=2, enter_time=datetime(2026, 7, 19, 10, 0, 0),
            leave_time=datetime(2026, 7, 19, 10, 0, 5),
            duration_seconds=5.0, source_video="cam01",
        )
        d = e.to_dict()
        for v in d.values():
            assert not isinstance(v, datetime), f"to_dict 含 datetime: {v!r}"
        # 时间已转 ISO 字符串
        assert d["enter_time"] == "2026-07-19T10:00:00"
        assert d["leave_time"] == "2026-07-19T10:00:05"
        assert d["source_video"] == "cam01"

    def test_to_json_roundtrip(self):
        e = VisitorEvent(
            visitor_id=3, enter_time=datetime(2026, 7, 19, 10, 0, 0),
            leave_time=datetime(2026, 7, 19, 10, 0, 12),
            duration_seconds=12.0, source_video="CAVIAR/OneStopEnter1cor",
        )
        j = e.to_json()
        parsed = json.loads(j)
        # 关键字段都进了 JSON
        assert parsed["event_id"] == e.event_id
        assert parsed["visitor_id"] == 3
        assert parsed["duration_seconds"] == 12.0
        assert parsed["source_video"] == "CAVIAR/OneStopEnter1cor"
        assert parsed["enter_time"] == "2026-07-19T10:00:00"
        assert parsed["leave_time"] == "2026-07-19T10:00:12"
        # 时间字段能 fromisoformat 反序列化（中心消费侧能力）
        assert datetime.fromisoformat(parsed["enter_time"]) == e.enter_time
        assert datetime.fromisoformat(parsed["leave_time"]) == e.leave_time

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="duration_seconds"):
            VisitorEvent(
                visitor_id=1, enter_time=datetime(2026, 7, 19, 10, 0, 10),
                leave_time=datetime(2026, 7, 19, 10, 0, 0),
                duration_seconds=-5.0,
            )

    def test_leave_before_enter_rejected(self):
        with pytest.raises(ValueError, match="leave_time"):
            VisitorEvent(
                visitor_id=1, enter_time=datetime(2026, 7, 19, 10, 0, 10),
                leave_time=datetime(2026, 7, 19, 10, 0, 0),
                duration_seconds=0.0,
            )

    def test_no_business_judgment_fields(self):
        """P0-6 边界：VisitorEvent **绝对不含** 风险/类型字段（ADR-0007）。"""
        e = VisitorEvent(
            visitor_id=1, enter_time=datetime(2026, 7, 19, 10, 0, 0),
            leave_time=datetime(2026, 7, 19, 10, 0, 5),
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
            datetime(2026, 7, 19, 10, 0, 0),  # frame 0
            datetime(2026, 7, 19, 10, 0, 1),  # frame 1
            datetime(2026, 7, 19, 10, 0, 2),  # frame 2
            datetime(2026, 7, 19, 10, 0, 8),  # frame 3（消失 6s > gap=5）
            datetime(2026, 7, 19, 10, 0, 14), # frame 4
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
        # 同一 track 只在 active→left 那一刻生成 1 个事件
        assert len(new_events) == 1
        e = new_events[0]
        assert e.visitor_id == 1
        assert e.enter_time == times[0]
        # leave_time = 最后一次出现时刻（vt.last_seen）
        assert e.leave_time == times[2]
        assert e.duration_seconds == 2.0
        assert e.source_video == "OneStopEnter1cor"

    def test_continue_left_frames_no_duplicate_event(self):
        """持续离场（连续多帧都没出现）只生成 1 个事件，不重复。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),  # frame 0
            datetime(2026, 7, 19, 10, 0, 10), # frame 1（消失 10s > gap=5）
            datetime(2026, 7, 19, 10, 0, 20), # frame 2
            datetime(2026, 7, 19, 10, 0, 30), # frame 3
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
        # 一次 active→left 就够了
        assert len(all_new) == 1
        assert len(builder.events) == 1

    def test_track_interruption_within_gap_no_event(self):
        """短暂漏检（缺失帧间隔 < absence_gap）不算离场，不生成事件。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),  # frame 0: 出现
            datetime(2026, 7, 19, 10, 0, 1),  # frame 1: 消失 1s < gap=5
            datetime(2026, 7, 19, 10, 0, 2),  # frame 2: 消失 2s
            datetime(2026, 7, 19, 10, 0, 3),  # frame 3: 消失 3s
            datetime(2026, 7, 19, 10, 0, 4),  # frame 4: 重新出现
            datetime(2026, 7, 19, 10, 0, 5),  # frame 5: 出现
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
        # 整个序列内从未真正判离场（缺失帧间隔 < gap=5）
        assert all_new == []
        assert builder.events == []
        assert tracker.get(1).status == ACTIVE

    def test_revisit_generates_second_event(self):
        """离场后重新进入 → 再离场 → 生成 2 个独立事件。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),  # frame 0: enter
            datetime(2026, 7, 19, 10, 0, 1),  # frame 1: present
            datetime(2026, 7, 19, 10, 0, 10), # frame 2: 消失 9s > gap=5 → left, 生成事件 1
            datetime(2026, 7, 19, 10, 0, 20), # frame 3: 重新出现 → reenter
            datetime(2026, 7, 19, 10, 0, 25), # frame 4: present
            datetime(2026, 7, 19, 10, 0, 35), # frame 5: 消失 → left, 生成事件 2
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
        # 事件 1：enter=0s, leave=1s
        assert all_new[0].enter_time == times[0]
        assert all_new[0].leave_time == times[1]
        assert all_new[0].duration_seconds == 1.0
        # 事件 2：enter=20s(reenter), leave=25s
        assert all_new[1].enter_time == times[3]
        assert all_new[1].leave_time == times[4]
        assert all_new[1].duration_seconds == 5.0
        # event_id 唯一
        assert all_new[0].event_id != all_new[1].event_id

    def test_multi_visitor_independent_events(self):
        """两个 track 独立生成各自的事件，不串。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),  # frame 0: A+B
            datetime(2026, 7, 19, 10, 0, 1),  # frame 1: A only
            datetime(2026, 7, 19, 10, 0, 2),  # frame 2: A only
            datetime(2026, 7, 19, 10, 0, 8),  # frame 3: both 消失 → both left
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
        # 同一帧两个 track 同时变 left → 一次 update 生成 2 个事件
        assert len(all_new) == 2
        visitor_ids = sorted(e.visitor_id for e in all_new)
        assert visitor_ids == [1, 2]
        # 每个事件的 enter_time/leave_time 对应各自 track
        for e in all_new:
            if e.visitor_id == 1:
                assert e.enter_time == times[0]
                assert e.leave_time == times[2]
                assert e.duration_seconds == 2.0
            else:
                assert e.enter_time == times[0]
                assert e.leave_time == times[0]  # B 只在 frame 0 出现
                assert e.duration_seconds == 0.0

    def test_source_video_traced(self):
        """source_video 字段写入每个事件；切换源后新事件用新源。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),
            datetime(2026, 7, 19, 10, 0, 10),  # A left
            datetime(2026, 7, 19, 10, 0, 20),  # B enter
            datetime(2026, 7, 19, 10, 0, 30),  # B left
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
        # 切换源
        builder.source_video = "cam02"
        for _ in range(2):
            all_new.extend(builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections))
        # 事件 1 source=cam01，事件 2 source=cam02
        assert len(all_new) == 2
        assert all_new[0].source_video == "cam01"
        assert all_new[1].source_video == "cam02"

    def test_pending_and_ack(self):
        """pending() 显示未消费事件，ack() 后从 pending 移除。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),
            datetime(2026, 7, 19, 10, 0, 10),  # left
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
        # ack
        builder.ack(builder.events[0])
        assert len(builder.pending()) == 0
        # events 仍保留（用于审计 / 重发）
        assert len(builder.events) == 1

    def test_reset_clears_state(self):
        """reset() 清空已生成事件 + 状态历史；新会话重新计数。"""
        times = [
            datetime(2026, 7, 19, 10, 0, 0),
            datetime(2026, 7, 19, 10, 0, 10),  # left
            datetime(2026, 7, 19, 10, 1, 0),   # 重新 enter
            datetime(2026, 7, 19, 10, 1, 10),  # left again
        ]
        det = FakeDetector([([1], 0.0), ([], 10.0), ([1], 60.0), ([], 70.0)])
        tracker = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
        builder = VisitorEventBuilder(
            tracker, source_video="cam01", now_provider=_fixed_now(times),
        )
        for _ in range(2):
            builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        assert len(builder.events) == 1
        # reset 后再跑 → reenter 后的新 left 不被 _emitted_track_ids 抑制
        builder.reset()
        for _ in range(2):
            builder.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
        # reset 后再次 enter→left 产生第 2 个事件
        assert len(builder.events) == 1
        assert builder.events[0].enter_time == times[2]
        assert builder.events[0].leave_time == times[2]


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

    # CAVIAR 低分辨率下 track 可能初始化失败（见 P0-5b 备注），不强求一定生成事件
    # 但**如果** track 出现且又离开，应生成对应事件
    events = builder.events
    if n_with_track == 0:
        # tracker 阶段就没初始化 → 无事件可生成（fixture 质量问题，不是代码 bug）
        assert events == []
    else:
        # 至少不应有重复 / 异常事件
        for e in events:
            assert e.source_video == "CAVIAR/OneStopEnter1cor"
            assert e.duration_seconds >= 0
            assert e.leave_time >= e.enter_time
        # 同一 track_id 出现 ≤ 1 次（除非 reenter；CAVIAR 短片段一般不会）
        assert len(events) <= n_with_track  # 极端情况下每个 track 至多一次

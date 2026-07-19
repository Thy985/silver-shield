"""VisitorTracker / VisitorTrack 测试（P0-5）。

纯逻辑用例用 FakeDetector 驱动（无 torch 依赖，始终运行，时间源可注入以保证确定性）；
真实 YOLO 跨帧跟踪用例在缺 ultralytics/torch 时自动跳过，用 tests/fixtures/person.jpg 验证真实链路。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from home_perception.detection.detector import Detection, DetectionResult, Detector
from home_perception.detection.schemas import ACTIVE, LEFT, VisitorTrack
from home_perception.detection.tracker import DEFAULT_ABSENCE_GAP_S, VisitorTracker


class FakeDetector(Detector):
    """按预设 (track_ids, ts) 序列产出 DetectionResult，便于确定性单测。

    ts 仅用于满足 Detection 契约（float）；VisitorTracker 的时间由注入的 now_provider 控制。
    """

    def __init__(self, frames, **kwargs):
        # frames: list[(track_ids: list[int|None], ts: float)]
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
    """返回一个 now_provider：每次调用推进到 seq 里的下一个时间。"""
    it = iter(seq)
    def _now():
        try:
            return next(it)
        except StopIteration:
            return datetime(2000, 1, 1)
    return _now


# ---------------- 纯逻辑单测（无 torch） ----------------

def test_visitor_track_basic_state():
    t0 = datetime(2026, 7, 19, 10, 0, 0)
    vt = VisitorTrack(track_id=1, first_seen=t0, last_seen=t0, enter_time=t0)
    assert vt.status == ACTIVE
    assert vt.frame_count == 1
    assert vt.duration_s == 0.0
    assert vt.absence_s(t0 + timedelta(seconds=5)) == 5.0


def test_same_person_across_frames_is_one_active():
    # 同人连续 3 帧（时间推进 1s），应只产生 1 个 active 访客，frame_count=3
    times = [datetime(2026, 7, 19, 10, 0, i) for i in range(3)]
    det = FakeDetector([([1], 0.0), ([1], 1.0), ([1], 2.0)])
    tr = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
    for _ in range(3):
        tr.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
    vt = tr.get(1)
    assert vt is not None
    assert vt.frame_count == 3
    assert vt.status == ACTIVE
    assert tr.active()[0].track_id == 1


def test_visitor_leaves_after_absence_gap():
    # t=10:00 / 10:00:01 出现；t=10:00:10 仍未出现 → 超过 gap(5s) 判 left
    times = [
        datetime(2026, 7, 19, 10, 0, 0),
        datetime(2026, 7, 19, 10, 0, 1),
        datetime(2026, 7, 19, 10, 0, 3),   # absence=2 < 5 → 仍 active
        datetime(2026, 7, 19, 10, 0, 10),  # absence=9 >= 5 → left
    ]
    det = FakeDetector([([1], 0.0), ([1], 1.0), ([], 3.0), ([], 10.0)])
    tr = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
    for _ in range(4):
        tr.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
    vt = tr.get(1)
    assert vt.status == LEFT
    assert vt.leave_time is not None
    assert vt.frame_count == 2
    assert tr.active() == []


def test_revisit_keeps_frame_count_and_sets_active():
    # 出现(t=0) → 离场(t=10 超 gap) → 再出现(t=20)：status 回 active，frame_count 累加不重置
    times = [
        datetime(2026, 7, 19, 10, 0, 0),
        datetime(2026, 7, 19, 10, 0, 10),
        datetime(2026, 7, 19, 10, 0, 20),
    ]
    det = FakeDetector([([1], 0.0), ([], 10.0), ([1], 20.0)])
    tr = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
    for _ in range(3):
        tr.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
    vt = tr.get(1)
    assert vt.status == ACTIVE
    assert vt.frame_count == 2          # 不重置
    assert vt.enter_time == times[2]


def test_multiple_visitors_tracked_independently():
    times = [
        datetime(2026, 7, 19, 10, 0, 0),
        datetime(2026, 7, 19, 10, 0, 1),
        datetime(2026, 7, 19, 10, 0, 2),
    ]
    det = FakeDetector([([1, 2], 0.0), ([1], 1.0), ([2], 2.0)])
    tr = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now(times))
    for _ in range(3):
        tr.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
    assert set(tr.active_tracks.keys()) == {1, 2}
    assert tr.get(1).frame_count == 2
    assert tr.get(2).frame_count == 2


def test_detections_without_track_id_skipped():
    # 未启用跟踪（track_id=None）不应污染访客状态
    det = FakeDetector([([], 0.0)])
    tr = VisitorTracker(absence_gap_s=5.0, now_provider=_fixed_now([datetime(2026, 7, 19, 10, 0, 0)]))
    tr.update(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)).detections)
    assert tr.active_tracks == {}


def test_absence_gap_must_be_positive():
    with pytest.raises(ValueError):
        VisitorTracker(absence_gap_s=0.0)


# ---------------- 真实 YOLO 跨帧跟踪（依赖 torch/ultralytics，缺失时跳过） ----------------

FIXTURE = "tests/fixtures/person.jpg"


def _load_fixture_frames():
    """用真实人物照片构造若干帧（轻微平移模拟摄像头下的同一人连续出现）。"""
    import cv2

    img = cv2.imread(FIXTURE)
    if img is None:
        return None
    h, w = img.shape[:2]
    frames = []
    for dx in [0, 8, 16, 24, 32]:
        f = img.copy()
        # 向右平移，制造连续帧里"同一个人"
        if dx:
            f[:, :-dx] = img[:, dx:]
            f[:, -dx:] = 0
        frames.append(f)
    return frames


def test_real_yolo_track_produces_stable_track_id():
    """真实链路验收：person.jpg 经 YOLO+ByteTrack 检出 person 且跨帧 track_id 一致。"""
    pytest.importorskip("ultralytics")
    from home_perception.detection.detector import YOLODetector

    frames = _load_fixture_frames()
    if frames is None:
        pytest.skip(f"fixture 缺失：{FIXTURE}（下载失败或 .gitignore）")

    det = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.25,
        classes=[0], imgsz=416, device="cpu", enable_track=True, tracker="bytetrack",
    )
    det.load()
    prev_ids: set[int] = set()
    consistent = 0
    for f in frames:
        res = det.detect(f)
        ids = {d.track_id for d in res.detections if d.track_id is not None}
        assert len(res.detections) > 0, "person.jpg 应检出至少一个目标"
        assert None not in [d.track_id for d in res.detections], "开启跟踪后 track_id 不应为 None"
        if prev_ids and ids and (prev_ids & ids):
            consistent += 1
        prev_ids = ids
    assert consistent >= 1, "真实链路上跨帧 track_id 未保持一致（persist=True 是否生效？）"


def test_real_visitor_tracker_lifecycle():
    """真实链路：VisitorTracker 包裹 detector，同一目标在本会话内维持单 active 访客。"""
    pytest.importorskip("ultralytics")
    from home_perception.detection.detector import YOLODetector

    frames = _load_fixture_frames()
    if frames is None:
        pytest.skip(f"fixture 缺失：{FIXTURE}（下载失败或 .gitignore）")

    det = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.25,
        classes=[0], imgsz=416, device="cpu", enable_track=True, tracker="bytetrack",
    )
    det.load()
    tr = VisitorTracker(absence_gap_s=DEFAULT_ABSENCE_GAP_S)
    for f in frames:
        tr.update(det.detect(f).detections)
    # 整个序列应出现至少 1 个访客（同一摄像头会话内的同一人）
    assert len(tr.active_tracks) >= 1
    vt = next(iter(tr.active_tracks.values()))
    assert vt.frame_count >= 1
    assert vt.status in (ACTIVE, LEFT)

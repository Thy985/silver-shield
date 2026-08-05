"""访客跟踪状态机（Perceive 模块 · 事实采集层）。

职责边界（见 AGENTS.md §3 / ADR-0001 / Owner P0-5 说明）：
- 本类**只维护"当前摄像头生命周期内"的访客在场状态**，不做任何风险/重复/陌生人判断
  （那些是 analysis 层 P0-6 / P1 的事）。
- 输入：单帧 `Detection` 列表（其 `track_id` 来自 YOLO + ByteTrack，`persist=True` 保证跨帧一致）。
- 输出：当前活跃 `VisitorTrack` 列表（状态对象，非事件）。

P0-5 的关键认知：
- `track_id` 只代表"当前摄像头会话里同一个目标"，**不是跨天身份**。
  今天 `track_id=3`、明天 `track_id=8` 是正常的；跨天重识别属于 P0-6/P1（VisitorFeature/History），不在此层引入。
- `YOLODetector` 实例必须在相机循环里**复用**，否则每帧重建模型会使 `track_id` 不断重置
  （`detect()` 已对 `model.track()` 传 `persist=True`，前提是同一实例）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..common.logging import get_logger
from ..common.timeutil import now_dt
from .detector import Detection
from .schemas import ACTIVE, LEFT, VisitorTrack

log = get_logger(__name__)

# 默认"离场判定"宽限：同一 track_id 连续该秒数未出现，视为本次在场 visit 结束。
# 用于容忍检测器/跟踪器偶发漏检造成的 ID 闪烁，避免把一次来访误计成多次。
DEFAULT_ABSENCE_GAP_S: float = 3.0

# COCO person 类 id。VisitorTracker 只应处理「人」实体。
# 见 AGENTS.md §1.5（感知-事件边界原则）；当前为临时收口，未来前移到 EntityProjection 层。
PERSON_CLASS_ID: int = 0


class VisitorTracker:
    """跨帧访客状态维护器（包裹 Detector 之上的一层状态）。

    典型用法：
        det = YOLODetector(enable_track=True, tracker="bytetrack")  # 实例长期复用
        tracker = VisitorTracker()
        for frame in camera_loop:
            result = det.detect(frame)            # Detection 带 track_id
            tracks = tracker.update(result.detections)   # 当前活跃访客状态
            # tracks 里 status=active 的是在场访客；某 track 转 left 即离场
    """

    def __init__(
        self,
        absence_gap_s: float = DEFAULT_ABSENCE_GAP_S,
        now_provider: Callable[[], datetime] = now_dt,
    ):
        if absence_gap_s <= 0:
            raise ValueError(f"absence_gap_s 必须 > 0，收到 {absence_gap_s}")
        self.absence_gap_s = absence_gap_s
        self._now = now_provider or now_dt
        # track_id -> VisitorTrack（持续维护，离场后保留以便 revisit 计数）
        self.active_tracks: dict[int, VisitorTrack] = {}

    def update(self, detections: list[Detection]) -> list[VisitorTrack]:
        """根据当前帧的检测结果更新访客状态，返回当前所有 VisitorTrack 快照列表。

        逻辑：
        - 本帧出现的 track_id：新建/续接 VisitorTrack，标记 active、累加 frame_count、
          更新 bbox/confidence/last_seen。若该 track 此前处于 left，视为一次**新的在场**
          （frame_count 不重置，status 转回 active，不引入跨天身份）。
        - 本帧未出现、但距 last_seen 超过 absence_gap_s 的 track：标记 left，回填 leave_time。
        - 未超宽限的：保持 active 但暂不判离场（容忍漏检闪烁）。
        """
        now = self._now()
        seen_now: set[int] = set()
        for d in detections:
            # TODO(phase-0): 临时收口——本跟踪器只处理「人」实体。
            # 当前检测器白名单含 backpack(24)/handbag(26)/cell phone(67)，这些非人目标
            # 不应生成访客事件/计入停留与重复来访。
            # 正式方案：在 DetectionResult → VisitorTracker 之间引入 EntityProjection
            # （Human Entity Projection）层做业务实体投影，本 guard 届时前移并移除。
            # 见 AGENTS.md §1.5（感知-事件边界原则）。
            if d.class_id != PERSON_CLASS_ID:
                continue
            if d.track_id is None:
                # 未启用跟踪或跟踪器未给 ID：跳过，不污染访客状态
                continue
            seen_now.add(d.track_id)
            vt = self.active_tracks.get(d.track_id)
            if vt is None:
                vt = VisitorTrack(
                    track_id=d.track_id,
                    first_seen=now,
                    last_seen=now,
                    frame_count=1,
                    bbox=tuple(d.bbox) if d.bbox else None,
                    confidence=d.confidence,
                    status=ACTIVE,
                    enter_time=now,
                    leave_time=None,
                )
                self.active_tracks[d.track_id] = vt
                log.info("visitor_track.created", **vt.to_log())
            else:
                was_left = vt.status == LEFT
                vt.last_seen = now
                vt.frame_count += 1
                vt.bbox = tuple(d.bbox) if d.bbox else vt.bbox
                vt.confidence = d.confidence
                if was_left:
                    # 离场后再次出现 → 重新在场（仍是同一摄像头会话内，不引入跨天身份）
                    vt.status = ACTIVE
                    vt.enter_time = now
                    vt.leave_time = None
                    log.info(
                        "visitor_track.reenter",
                        **vt.to_log(),
                    )
                else:
                    vt.status = ACTIVE

        # 处理本帧未出现的 track：超过宽限判离场
        for vid, vt in self.active_tracks.items():
            if vid in seen_now:
                continue
            if vt.status == ACTIVE and vt.absence_s(now) >= self.absence_gap_s:
                vt.status = LEFT
                if vt.leave_time is None:
                    vt.leave_time = vt.last_seen
                log.info("visitor_track.left", **vt.to_log())

        return list(self.active_tracks.values())

    def active(self) -> list[VisitorTrack]:
        """当前在场（status=active）的访客快照列表。"""
        return [vt for vt in self.active_tracks.values() if vt.status == ACTIVE]

    def get(self, track_id: int) -> VisitorTrack | None:
        return self.active_tracks.get(track_id)

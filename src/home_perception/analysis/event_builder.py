"""事件层构建器（P0-6 · 事实事件层）。

> **P0-6 = 事实事件层；P0-7 = 风险语义层。**
> 本类只把 `VisitorTrack` 状态变化转成 `VisitorEvent`，**不做任何风险判断**（见 ADR-0007）。

`VisitorEventBuilder` 是包裹 `VisitorTracker` 的轻量层：
- 接收 `VisitorTracker` 实例（共享其 `active_tracks` 状态）
- 每次 `update(detections)` 时同步 Tracker 后扫描状态变化：
  - track 从 `active` 转 `left` → 生成 `VisitorEvent`（事实，离场事实）
  - track 从 `left` 转回 `active`（重新进入）→ 清除"已生成"标记，允许下次离场再生成
- 已生成过事件的 track 不会重复生成（除非先重新进入）

**职责边界**（与 P0-7 Rule Engine 严格分开）：
- ✅ 生成"什么时候来、什么时候走、停了多久、从哪条视频"
- ❌ 不判断是否"异常停留" / "重复来访" / "夜间访问" / "高风险" —— P0-7 Rule
- ❌ 不加 risk_score / visit_type / repeat_count —— 留给 P0-7 Feature Extraction

**关键设计（ADR-0007）**：`visitor_id` 是 UUID 而非 ByteTrack `track_id`：
- Tracker 内部 `track_id` 仍为 int（YOLO/ByteTrack 直接产出，不做封装）。
- Builder 内部维护 `track_id → UUID` 映射：track 首次 active 时分配 UUID，
  reenter 复用，程序重启/视频切换后新 track 视为新访客分配新 UUID。
- 中心侧用 UUID 严格去重与 RiskTwin 关联，不受 ByteTrack ID 局部复用影响。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Dict, List

from ..common.logging import get_logger
from ..common.timeutil import now_dt
from ..detection.detector import Detection
from ..detection.schemas import ACTIVE, VisitorTrack
from ..detection.tracker import VisitorTracker
from .event import VisitorEvent

log = get_logger(__name__)


class VisitorEventBuilder:
    """事实事件层 Builder：监听 `VisitorTracker` 状态变化，生成 `VisitorEvent`。

    典型用法：
        det = YOLODetector(enable_track=True, tracker="bytetrack")
        tracker = VisitorTracker()
        builder = VisitorEventBuilder(tracker, source_video="home_entry_01")

        for frame in camera_loop:
            detections = det.detect(frame).detections
            builder.update(detections)
            for event in builder.pending():
                publish_to_mqtt(event)        # 留给 P0-9
                builder.ack(event)            # 标记已消费

    注意：`builder.update(detections)` 会**先**调用 `tracker.update(detections)`，
    所以不要绕过 Builder 直接调 `tracker.update`（会丢事件）。

    时间：所有 datetime 字段均为 UTC（`now_dt` 已是时区感知的 UTC）。
    """

    def __init__(
        self,
        tracker: VisitorTracker,
        source_video: str = "unknown",
        now_provider: Callable[[], datetime] = now_dt,
    ):
        if tracker is None:
            raise ValueError("tracker 不能为空")
        self._tracker = tracker
        self._source_video = source_video
        self._now = now_provider or now_dt
        # 已为本 track_id 生成过事件（离场后未重新进入前不再生成）
        self._emitted_track_ids: set[int] = set()
        # 上一轮各 track_id 的 status（用于检测 active→left / left→active 状态翻转）
        self._last_status: Dict[int, str] = {}
        # 已确认消费（ack）的事件 event_id
        self._acked_ids: set[str] = set()
        # 已生成事件列表（顺序：生成时间）
        self._events: List[VisitorEvent] = []
        # track_id → UUID 映射（本会话内稳定；程序重启/视频切换后清空）
        # ADR-0007：visitor_id 是 UUID，不是 ByteTrack track_id。
        self._track_to_visitor: Dict[int, uuid.UUID] = {}

    # ---------------- 公开接口 ----------------

    @property
    def source_video(self) -> str:
        return self._source_video

    @source_video.setter
    def source_video(self, value: str) -> None:
        """切换视频源（多摄切换 / 视频回放重命名等场景）。"""
        self._source_video = value

    @property
    def events(self) -> List[VisitorEvent]:
        """已生成的 VisitorEvent 列表（只读快照；调用方不应原地修改）。"""
        return list(self._events)

    def pending(self) -> List[VisitorEvent]:
        """未确认消费的事件（`ack` 后从待消费移除；用于 MQTT 失败的本地缓冲）。"""
        return [e for e in self._events if e.event_id not in self._acked_ids]

    def ack(self, event: VisitorEvent) -> None:
        """确认事件已被下游消费（MqTT 上报 / 持久化成功后调用）。"""
        self._acked_ids.add(event.event_id)

    def visitor_id_for(self, track_id: int) -> uuid.UUID | None:
        """查询某 track_id 当前对应的 visitor_id（UUID）。track 未出现过则返回 None。"""
        return self._track_to_visitor.get(track_id)

    def update(self, detections: List[Detection]) -> List[VisitorEvent]:
        """同步 Tracker + 扫描状态变化，返回**本轮新生成**的事件列表。

        事件生成规则：
        - track 在上一次 update 是 `active`，本次 update 变 `left` → 生成 VisitorEvent
        - track 在上一次 update 是 `left`，本次 update 转回 `active`（重新进入）
          → 从 `_emitted_track_ids` 移除该 track_id，下次离场时再生成
        - 任何 active 状态的 track 第一次见到 → 分配 UUID（reenter 复用）
        """
        # 1) 同步 Tracker（内部会按 absence_gap 判定 left）
        self._tracker.update(detections)
        # 2) 扫描变 LEFT 的 track，生成事件
        new_events: List[VisitorEvent] = []
        for vid, vt in self._tracker.active_tracks.items():
            prev_status = self._last_status.get(vid)
            if vt.status == ACTIVE:
                # 进入/在场：分配或复用 UUID；清除"已生成"标记（为下次离场做准备）
                self._get_or_assign_visitor_id(vid)
                self._emitted_track_ids.discard(vid)
            elif vt.status == "left":
                if prev_status == ACTIVE and vid not in self._emitted_track_ids:
                    visitor_id = self._get_or_assign_visitor_id(vid)
                    event = self._build_event(vt, visitor_id)
                    self._events.append(event)
                    new_events.append(event)
                    self._emitted_track_ids.add(vid)
                    log.info(
                        "visitor_event.created",
                        event_id=event.event_id,
                        visitor_id=str(visitor_id),
                        track_id=vid,
                        duration_seconds=round(event.duration_seconds, 3),
                        source_video=event.source_video,
                    )
            self._last_status[vid] = vt.status
        return new_events

    def reset(self) -> None:
        """清空已生成事件 + 已消费标记 + 状态历史 + track→UUID 映射。

        用于：视频源切换 / 多会话切换 / 单元测试重置。
        **新会话不复用旧 UUID**（旧 UUID 与历史 visit 绑定，避免跨会话误关联）。
        """
        self._events.clear()
        self._acked_ids.clear()
        self._last_status.clear()
        self._emitted_track_ids.clear()
        self._track_to_visitor.clear()

    # ---------------- 内部 ----------------

    def _get_or_assign_visitor_id(self, track_id: int) -> uuid.UUID:
        """track_id 首次出现 → 分配新 UUID；后续 reenter 复用同一 UUID。

        ADR-0007 关键设计：visitor_id 是 UUID 不是 track_id。
        本会话内同 track_id 稳定 UUID；reset() 后分配新 UUID。
        """
        existing = self._track_to_visitor.get(track_id)
        if existing is not None:
            return existing
        new_id = uuid.uuid4()
        self._track_to_visitor[track_id] = new_id
        return new_id

    def _build_event(self, vt: VisitorTrack, visitor_id: uuid.UUID) -> VisitorEvent:
        """从已 left 的 VisitorTrack 构造 VisitorEvent（不含业务判断字段）。"""
        if vt.enter_time is None or vt.leave_time is None:
            # 防御：理论上 left 时 leave_time 必被回填；但显式校验避免脏数据
            raise ValueError(
                f"track_id={vt.track_id} 状态 left 但 enter_time/leave_time 缺失"
            )
        return VisitorEvent(
            visitor_id=visitor_id,
            enter_time=vt.enter_time,
            leave_time=vt.leave_time,
            duration_seconds=vt.duration_s,
            source_video=self._source_video,
            created_at=self._now(),
        )

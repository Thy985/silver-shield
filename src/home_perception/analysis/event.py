"""事件层领域对象（P0-6 · 事实事件）。

> **P0-6 = 事实事件层；P0-7 = 风险语义层。**
> 本模块只保存"发生了什么"，不保存"系统认为它意味着什么"（见 ADR-0001 / ADR-0007）。

`VisitorEvent` 是从 `VisitorTrack` 转 `LEFT` 状态时**离场即生成**的离散事实事件，
提供完整时间生命周期（enter / leave / duration）和来源可追溯（source_video）。

**不引入** `risk_level` / `visit_type` / `is_suspicious` / `repeat_count` 等业务字段
—— 那些是 P0-7 Rule Engine 的事，混入会污染领域边界（见 ADR-0007）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utc_now() -> datetime:
    """时区感知的 UTC 当前时间（替代 deprecated `datetime.utcnow()`）。"""
    return datetime.now(timezone.utc)


def _new_event_id() -> str:
    """生成事件唯一 ID（UUID4 字符串）。"""
    return str(uuid.uuid4())


@dataclass
class VisitorEvent:
    """访客事实事件：在 `VisitorTrack` 转 `LEFT` 时由 `VisitorEventBuilder` 生成。

    字段集严格按 ADR-0007 收敛（仅"发生了什么"，无业务判断）：
    - `event_id`：全局唯一 ID（UUID4 字符串），用于中心去重与对账
    - `visitor_id`：与 `VisitorTrack.track_id` 同义；只在当前摄像头会话内有效
    - `enter_time` / `leave_time`：本次在场进入/离开时刻
    - `duration_seconds`：停留时长（秒）
    - `source_video`：来源视频元数据（如 CAVIAR 场景名 / 萤石 stream_id），便于证据链追溯
    - `created_at`：事件生成时刻（UTC），用于排序与审计

    严格**不含**：
    - `risk_level` / `score` —— P0-7 Rule Engine
    - `visit_type` / `is_suspicious` —— P0-7 Rule Engine
    - `repeat_count` —— P0-7 Feature Extraction
    - `evidence` / `EvidenceRef` —— P0-8 取证
    """

    visitor_id: int
    enter_time: datetime
    leave_time: datetime
    duration_seconds: float
    source_video: str = "unknown"
    event_id: str = field(default_factory=_new_event_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError(
                f"duration_seconds 必须 >= 0，收到 {self.duration_seconds}"
            )
        if self.leave_time < self.enter_time:
            raise ValueError(
                f"leave_time ({self.leave_time}) 必须 >= enter_time ({self.enter_time})"
            )

    def to_dict(self) -> dict:
        """structlog 安全的扁平字典（时间已转 ISO 字符串，无 datetime 对象）。"""
        return {
            "event_id": self.event_id,
            "visitor_id": self.visitor_id,
            "enter_time": self.enter_time.isoformat(),
            "leave_time": self.leave_time.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "source_video": self.source_video,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        """JSON 序列化（中心消费 / 日志归档用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

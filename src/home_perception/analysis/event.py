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
from datetime import UTC, datetime


def _utc_now() -> datetime:
    """时区感知的 UTC 当前时间（替代 deprecated `datetime.utcnow()`）。"""
    return datetime.now(UTC)


def _new_visitor_id() -> uuid.UUID:
    """生成访客唯一 ID（UUID4）。

    **关键边界（ADR-0007）**：`visitor_id` 是 UUID 而非 ByteTrack 的 `track_id`：
    - ByteTrack ID 是局部、会话内的稳定 ID，但**程序重启/视频切换后可能复用**（从 0/1 重新计数）。
    - UUID 在 `VisitorEventBuilder` 内部按 `track_id` 首次出现时分配，本会话内同 `track_id` 复用；
      程序重启/视频切换后新 `track_id` 视为新访客，分配新 UUID。
    - 中心侧用 UUID 严格去重 / 关联 RiskTwin 时，不受 ByteTrack 内部 ID 复用影响。
    """
    return uuid.uuid4()


# 接受 UUID 或 str（便利：测试/手工构造可直接传 str；内部统一转 UUID）
VisitorId = uuid.UUID | str


def _coerce_uuid(value: VisitorId) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise TypeError(f"visitor_id 必须是 UUID 或 str 格式 UUID，收到 {type(value).__name__}")


def _require_utc(dt: datetime, field_name: str) -> None:
    """校验 datetime 是 timezone-aware 且为 UTC（防御性）。

    **关键边界（ADR-0007）**：本模块所有时间字段必须 UTC；naive datetime 拒绝接受。
    展示层（CLI / Web / 报告）按需转 Asia/Shanghai，不在本模块做。
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            f"{field_name} 必须是 timezone-aware datetime（建议 UTC），收到 naive datetime: {dt!r}"
        )


@dataclass
class VisitorEvent:
    """访客事实事件：在 `VisitorTrack` 转 `LEFT` 时由 `VisitorEventBuilder` 生成。

    字段集严格按 ADR-0007 收敛（仅"发生了什么"，无业务判断）：
    - `event_id`：全局唯一 ID（UUID4 字符串），用于中心去重与对账
    - `visitor_id`：与 `VisitorTrack.track_id` 解耦的稳定 UUID —— 见 ADR-0007 + 下面"关键边界"
    - `enter_time` / `leave_time`：本次在场进入/离开时刻（**UTC**）
    - `duration_seconds`：停留时长（秒）
    - `source_video`：来源视频元数据（如 CAVIAR 场景名 / 萤石 stream_id），便于证据链追溯
    - `created_at`：事件生成时刻（**UTC**），用于排序与审计

    严格**不含**：
    - `risk_level` / `score` —— P0-7 Rule Engine
    - `visit_type` / `is_suspicious` —— P0-7 Rule Engine
    - `repeat_count` —— P0-7 Feature Extraction
    - `evidence` / `EvidenceItem` —— P0-8 取证

    关键边界（写入 __post_init__ 强制）：
    1. `duration_seconds` 必须 >= 0
    2. `leave_time` 必须 >= `enter_time`
    3. `enter_time` / `leave_time` / `created_at` 必须 timezone-aware（建议 UTC）—— 防御 naive 漏标
    4. `visitor_id` 是 UUID 而非 ByteTrack 的 `track_id`（track_id 是 Tracker 内部 ID，可能复用）
    """

    visitor_id: VisitorId
    enter_time: datetime
    leave_time: datetime
    duration_seconds: float
    source_video: str = "unknown"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # 1) visitor_id 归一为 UUID
        self.visitor_id = _coerce_uuid(self.visitor_id)
        # 2) 时间统一 UTC 校验（必须先于 leave>=enter 比较，否则 naive vs aware 抛 TypeError）
        _require_utc(self.enter_time, "enter_time")
        _require_utc(self.leave_time, "leave_time")
        _require_utc(self.created_at, "created_at")
        # 3) duration 非负
        if self.duration_seconds < 0:
            raise ValueError(f"duration_seconds 必须 >= 0，收到 {self.duration_seconds}")
        # 4) leave >= enter（此时 enter/leave 都是 UTC timezone-aware，可比较）
        if self.leave_time < self.enter_time:
            raise ValueError(
                f"leave_time ({self.leave_time}) 必须 >= enter_time ({self.enter_time})"
            )

    def to_dict(self) -> dict:
        """structlog 安全的扁平字典（时间已转 ISO 字符串，无 datetime 对象）。"""
        return {
            "event_id": self.event_id,
            "visitor_id": str(self.visitor_id),
            "enter_time": self.enter_time.isoformat(),
            "leave_time": self.leave_time.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "source_video": self.source_video,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        """JSON 序列化（中心消费 / 日志归档用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

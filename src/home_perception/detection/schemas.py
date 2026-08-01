"""检测/跟踪层数据模型（Perceive 模块 · 事实采集层）。

定义 **领域对象**，不含任何风险语义（见 AGENTS.md §3 / ADR-0001）。

- `VisitorTrack`：同一个人在**当前摄像头生命周期内**的连续性"状态对象"
  （active / left、最近一帧 bbox、累计帧数、进入/最近时间）。
  注意：它**只代表当前摄像头会话内的同一个目标**，**不是跨天身份**。
  跨天重复识别属于 P0-6 / P1（VisitorFeature / VisitorHistory / 外观 embedding / 人工确认），
  不在本层引入（见 Owner P0-5 说明）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# 访客当前状态：在场 / 已离场（离场宽限内未再见）
ACTIVE = "active"
LEFT = "left"


@dataclass
class VisitorTrack:
    """当前摄像头生命周期内、同一个人的连续性状态（**事实**，非事件、非风险结论）。

    与 P0-6 的 `VisitorEvent` 区分：
    - 本类是**运行时的持续状态**（Tracker 每帧维护），
    - `VisitorEvent` 是**离散事件**（离场/异常时由 analysis 生成，供上报）。
    """

    track_id: int  # 来自 YOLO/ByteTrack 的帧间一致 ID（仅本摄像头会话内有效）
    first_seen: datetime  # 首次出现
    last_seen: datetime  # 最近一次出现
    frame_count: int = 1  # 累计被检出的帧数
    bbox: tuple[float, float, float, float] | None = None  # 最近一帧 bbox（原始帧坐标）
    confidence: float = 0.0  # 最近一帧置信度
    status: str = ACTIVE  # active | left
    enter_time: datetime | None = None  # 本次在场进入时刻
    leave_time: datetime | None = None  # 本次在场离开时刻（离场后回填）

    @property
    def duration_s(self) -> float:
        """本次在场时长（秒）；在场中按 last_seen 估算，已离场按 leave_time 估算。"""
        end = self.leave_time if self.leave_time is not None else self.last_seen
        start = self.enter_time if self.enter_time is not None else self.first_seen
        return max(0.0, (end - start).total_seconds())

    def absence_s(self, now: datetime) -> float:
        """距最近一次出现已过去的秒数（用于离场判定）。"""
        return max(0.0, (now - self.last_seen).total_seconds())

    def to_dict(self) -> dict:
        """供日志 / 调试输出的精简字典（不写入隐私内容）。"""
        return {
            "track_id": self.track_id,
            "status": self.status,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "frame_count": self.frame_count,
            "duration_s": round(self.duration_s, 3),
            "confidence": round(self.confidence, 4),
            "enter_time": self.enter_time.isoformat() if self.enter_time else None,
            "leave_time": self.leave_time.isoformat() if self.leave_time else None,
        }

    def to_log(self) -> dict:
        """structlog 安全的字典（时间已转 ISO 字符串，无 datetime 对象）。等价于 to_dict。"""
        return self.to_dict()

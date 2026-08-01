"""特征层领域对象（P0-7a · 结构化数值信号层）。

> **P0-7a = 结构化数值特征；P0-7b = 风险语义层（Rule Engine）。**
> 继续 Owner P0-6 原则（ADR-0007）：Feature 是"被测量的数值"，不是"判断的标签"。

`Feature` 是 Rule Engine 的输入信号层。Rule Engine 消费 Feature 计算 5 类 PerceptionEvent + score。
Feature 层**不预设任何阈值**，所有"是/否"、"长/短"、"高/低"等判断**严禁**出现在 Feature
（留在 Rule Engine 用业务阈值算）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from uuid import UUID


def _utc_now() -> datetime:
    """时区感知的 UTC 当前时间。"""
    return datetime.now(timezone.utc)


def _coerce_uuid(value: Union[UUID, str]) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise TypeError(f"visitor_id 必须是 UUID 或 str 格式 UUID，收到 {type(value).__name__}")


def _require_utc(dt: datetime, field_name: str) -> None:
    """校验 datetime 是 timezone-aware（ADR-0007 / ADR-0008 一致边界）。"""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            f"{field_name} 必须是 timezone-aware datetime（建议 UTC），"
            f"收到 naive datetime: {dt!r}"
        )


# ============================================================================
# Feature 基类
# ============================================================================

@dataclass
class Feature:
    """基类：单一维度的可测量数值信号（不含判断字段）。

    字段：
    - `visitor_id`：对应 VisitorEvent.visitor_id（UUID）
    - `event_id`：对应 VisitorEvent.event_id
    - `source_video`：来源视频元数据（与 VisitorEvent.source_video 一致）
    - `computed_at`：本 Feature 提取时刻（UTC）

    严格**不含**：
    - 任何 `is_*` 派生 bool（`is_long_visit` / `is_suspicious` 等）—— 留给 Rule Engine
    - 任何数值阈值（`risk_score` / `risk_level`）—— 留给 Rule Engine
    - 任何类别标签（`visit_type` / `event_type`）—— 留给 Rule Engine
    """

    visitor_id: UUID
    event_id: str
    source_video: str
    computed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.visitor_id = _coerce_uuid(self.visitor_id)
        _require_utc(self.computed_at, "computed_at")

    def to_dict(self) -> Dict[str, Any]:
        """structlog-safe dict（时间已转 ISO 字符串）。"""
        return {
            "feature_type": self.__class__.__name__,
            "visitor_id": str(self.visitor_id),
            "event_id": self.event_id,
            "source_video": self.source_video,
            "computed_at": self.computed_at.isoformat(),
        }


# ============================================================================
# 4 个具体 Feature
# ============================================================================

@dataclass
class DurationFeature(Feature):
    """停留时长（直接取自 VisitorEvent.duration_seconds）。

    字段：
    - `duration_seconds`：本次在场停留秒数

    严格**不含**：`is_long_visit`（阈值判断留给 Rule Engine）。
    """

    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.duration_seconds < 0:
            raise ValueError(f"duration_seconds 必须 >= 0，收到 {self.duration_seconds}")

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["duration_seconds"] = round(self.duration_seconds, 3)
        return d


@dataclass
class VisitFrequencyFeature(Feature):
    """滑动窗口内同 visitor_id 出现次数（含本次）。

    字段：
    - `visits_in_window`：窗口内该 visitor_id 出现次数（含本次）
    - `window_seconds`：窗口长度（秒，可配，默认 30 分钟 = 1800s）

    严格**不含**：`is_repeat`（阈值判断留给 Rule Engine）。
    """

    visits_in_window: int = 1
    window_seconds: float = 1800.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.visits_in_window < 1:
            raise ValueError(f"visits_in_window 必须 >= 1，收到 {self.visits_in_window}")
        if self.window_seconds <= 0:
            raise ValueError(f"window_seconds 必须 > 0，收到 {self.window_seconds}")

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["visits_in_window"] = self.visits_in_window
        d["window_seconds"] = round(self.window_seconds, 1)
        return d


@dataclass
class TimeFeature(Feature):
    """时间维度数值信号（拆 VisitorEvent.leave_time）。

    字段：
    - `hour_of_day`：0-23（基于 leave_time）
    - `day_of_week`：0-6（周一=0；基于 leave_time）
    - `is_weekend`：周六/周日（**日历事实**，非"判断" —— 可由 day_of_week 严格派生）

    严格**不含**：`is_odd_hour` / `is_night`（"异常时段"判断留给 Rule Engine 配业务阈值）。
    """

    hour_of_day: int = 0
    day_of_week: int = 0
    is_weekend: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0 <= self.hour_of_day <= 23):
            raise ValueError(f"hour_of_day 必须在 0-23，收到 {self.hour_of_day}")
        if not (0 <= self.day_of_week <= 6):
            raise ValueError(f"day_of_week 必须在 0-6，收到 {self.day_of_week}")
        # is_weekend 应该是 day_of_week 派生的"事实"
        expected_weekend = self.day_of_week in (5, 6)
        if self.is_weekend != expected_weekend:
            raise ValueError(
                f"is_weekend={self.is_weekend} 与 day_of_week={self.day_of_week} 不一致"
            )

    @classmethod
    def from_datetime(cls, dt: datetime, **kwargs) -> "TimeFeature":
        """从 timezone-aware datetime 派生 hour_of_day / day_of_week / is_weekend。"""
        if dt.tzinfo is None:
            raise ValueError("dt 必须是 timezone-aware")
        return cls(
            hour_of_day=dt.hour,
            day_of_week=dt.weekday(),
            is_weekend=dt.weekday() in (5, 6),
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["hour_of_day"] = self.hour_of_day
        d["day_of_week"] = self.day_of_week
        d["is_weekend"] = self.is_weekend
        return d


@dataclass
class TrajectoryFeature(Feature):
    """轨迹模式（MVP 单摄像头无真实轨迹，预留接口位）。

    字段：
    - `bbox_center_displacement`：bbox 中心位移（px），单摄全 0
    - `segment_count`：切分段数（单摄通常 1 段）

    P1 多摄时：本 Feature 扩展 displacement / velocity / segment_count 等。
    """

    bbox_center_displacement: float = 0.0
    segment_count: int = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.bbox_center_displacement < 0:
            raise ValueError(
                f"bbox_center_displacement 必须 >= 0，收到 {self.bbox_center_displacement}"
            )
        if self.segment_count < 1:
            raise ValueError(f"segment_count 必须 >= 1，收到 {self.segment_count}")

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["bbox_center_displacement"] = round(self.bbox_center_displacement, 2)
        d["segment_count"] = self.segment_count
        return d


# ============================================================================
# RiskFeature 聚合
# ============================================================================

@dataclass
class RiskFeature:
    """一组 Feature 的聚合 + 触发源（VisitorEvent），供 Rule Engine 消费。

    字段：
    - `visitor_id` / `event_id` / `source_video` / `computed_at`：触发源 + 提取时刻
    - `duration` / `frequency` / `time` / `trajectory`：4 个具体 Feature，可空
      （某 Feature 不可用时保持 None，Rule Engine 按 None 跳过对应规则）

    严格**不含**：`risk_level` / `score` / `visit_type` / `is_suspicious` / `event_type` /
    `is_repeat` / `is_long_visit` / `is_odd_hour` 等任何判断字段（ADR-0007 / ADR-0008 边界）。
    """

    visitor_id: UUID
    event_id: str
    source_video: str
    computed_at: datetime
    duration: Optional[DurationFeature] = None
    frequency: Optional[VisitFrequencyFeature] = None
    time: Optional[TimeFeature] = None
    trajectory: Optional[TrajectoryFeature] = None

    def __post_init__(self) -> None:
        self.visitor_id = _coerce_uuid(self.visitor_id)
        _require_utc(self.computed_at, "computed_at")

    def to_dict(self) -> Dict[str, Any]:
        """structlog-safe 字典；空 Feature 序列化为 null。"""
        d = {
            "visitor_id": str(self.visitor_id),
            "event_id": self.event_id,
            "source_video": self.source_video,
            "computed_at": self.computed_at.isoformat(),
            "duration": self.duration.to_dict() if self.duration else None,
            "frequency": self.frequency.to_dict() if self.frequency else None,
            "time": self.time.to_dict() if self.time else None,
            "trajectory": self.trajectory.to_dict() if self.trajectory else None,
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def has_all_features(self) -> bool:
        """是否 4 个 Feature 都计算了（用于测试 / 调试）。"""
        return all([
            self.duration is not None,
            self.frequency is not None,
            self.time is not None,
            self.trajectory is not None,
        ])

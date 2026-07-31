"""特征提取器（P0-7a · 结构化数值信号层）。

> **P0-7a = 结构化数值特征；P0-7b = 风险语义层（Rule Engine）。**
> 继续 Owner P0-6 原则：Feature 是"被测量的数值"，不是"判断的标签"。

`FeatureExtractor` 把 `VisitorEvent` 流转换成 `RiskFeature` 流。
- 4 个具体 Extractor 职责单一，可独立测试
- `FeatureExtractor` 编排器维护 `VisitFrequencyFeature` 需要的滑动窗口状态
- `reset()` 清空滑动窗口（视频源切换 / 多会话）
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, Optional
from uuid import UUID

from ..common.logging import get_logger
from .event import VisitorEvent
from .feature import (
    DurationFeature,
    RiskFeature,
    TimeFeature,
    TrajectoryFeature,
    VisitFrequencyFeature,
)

log = get_logger(__name__)


# ============================================================================
# 4 个具体 Extractor（纯函数为主）
# ============================================================================

class DurationFeatureExtractor:
    """从 VisitorEvent 提取停留时长（直接抄字段）。"""

    @staticmethod
    def extract(event: VisitorEvent, computed_at=None) -> DurationFeature:
        from .feature import _utc_now
        return DurationFeature(
            visitor_id=event.visitor_id,
            event_id=event.event_id,
            source_video=event.source_video,
            computed_at=computed_at or _utc_now(),
            duration_seconds=event.duration_seconds,
        )


class VisitFrequencyFeatureExtractor:
    """滑动窗口内同 visitor_id 出现次数。

    输入：当前 VisitorEvent + 该 visitor_id 的历史事件列表 + 窗口长度（秒）
    输出：VisitFrequencyFeature
    """

    @staticmethod
    def extract(
        event: VisitorEvent,
        history: Deque[VisitorEvent],
        window_seconds: float,
        computed_at=None,
    ) -> VisitFrequencyFeature:
        from .feature import _utc_now
        # 窗口起点 = 当前事件 leave_time - window_seconds
        # 历史事件中 leave_time >= 起点 的算入窗口（含当前）
        window_start_ts = event.leave_time.timestamp() - window_seconds
        # 历史中落在窗口内的事件 + 当前事件 = 总次数
        count = 1  # 当前事件至少 1 次
        for prev in history:
            if prev.leave_time.timestamp() >= window_start_ts:
                count += 1
        return VisitFrequencyFeature(
            visitor_id=event.visitor_id,
            event_id=event.event_id,
            source_video=event.source_video,
            computed_at=computed_at or _utc_now(),
            visits_in_window=count,
            window_seconds=window_seconds,
        )


class TimeFeatureExtractor:
    """从 VisitorEvent.leave_time 提取时间维度数值。"""

    @staticmethod
    def extract(event: VisitorEvent, computed_at=None) -> TimeFeature:
        from .feature import _utc_now
        return TimeFeature.from_datetime(
            event.leave_time,
            visitor_id=event.visitor_id,
            event_id=event.event_id,
            source_video=event.source_video,
            computed_at=computed_at or _utc_now(),
        )


class TrajectoryFeatureExtractor:
    """轨迹模式（MVP 单摄像头占位，bbox_center_displacement=0, segment_count=1）。

    P1 多摄时接多摄 bbox 时序，扩展 displacement / velocity / segment_count。
    """

    @staticmethod
    def extract(event: VisitorEvent, computed_at=None) -> TrajectoryFeature:
        from .feature import _utc_now
        return TrajectoryFeature(
            visitor_id=event.visitor_id,
            event_id=event.event_id,
            source_video=event.source_video,
            computed_at=computed_at or _utc_now(),
            bbox_center_displacement=0.0,  # MVP 单摄像头无轨迹
            segment_count=1,
        )


# ============================================================================
# FeatureExtractor 编排器
# ============================================================================

class FeatureExtractor:
    """编排器：接收 VisitorEvent 流，输出 RiskFeature 流。

    用法：
        detector = YOLODetector(...)
        tracker = VisitorTracker(...)
        event_builder = VisitorEventBuilder(tracker, source_video="cam01")
        feature_extractor = FeatureExtractor(frequency_window_s=1800.0)

        for frame in camera_loop:
            detections = detector.detect(frame).detections
            for event in event_builder.update(detections):
                risk_feature = feature_extractor.extract(event)
                # risk_feature 喂给 Rule Engine (P0-7b)
                ...

    状态：
    - `_frequency_window_s`：VisitFrequency 窗口长度（默认 30 分钟 = 1800s）
    - `_recent_by_visitor`：每 visitor_id 保留窗口内历史事件（用于 VisitFrequency 计数）
    """

    DEFAULT_FREQUENCY_WINDOW_S: float = 1800.0  # 30 分钟

    def __init__(
        self,
        frequency_window_s: float = DEFAULT_FREQUENCY_WINDOW_S,
        max_history_per_visitor: int = 100,
    ):
        if frequency_window_s <= 0:
            raise ValueError(f"frequency_window_s 必须 > 0，收到 {frequency_window_s}")
        if max_history_per_visitor < 1:
            raise ValueError(f"max_history_per_visitor 必须 >= 1，收到 {max_history_per_visitor}")
        self._frequency_window_s = frequency_window_s
        self._max_history = max_history_per_visitor
        # visitor_id → 历史事件 deque（按 leave_time 顺序追加）
        self._recent_by_visitor: Dict[UUID, Deque[VisitorEvent]] = defaultdict(
            lambda: deque(maxlen=self._max_history)
        )

    @property
    def frequency_window_s(self) -> float:
        return self._frequency_window_s

    def extract(self, event: VisitorEvent) -> RiskFeature:
        """从单个 VisitorEvent 提取 RiskFeature（更新滑动窗口历史）。"""
        # 1) 4 个 Feature 并行提取
        duration = DurationFeatureExtractor.extract(event)
        frequency = VisitFrequencyFeatureExtractor.extract(
            event, self._recent_by_visitor[event.visitor_id], self._frequency_window_s,
        )
        time_ = TimeFeatureExtractor.extract(event)
        trajectory = TrajectoryFeatureExtractor.extract(event)

        # 2) 聚合 RiskFeature
        risk = RiskFeature(
            visitor_id=event.visitor_id,
            event_id=event.event_id,
            source_video=event.source_video,
            computed_at=duration.computed_at,
            duration=duration,
            frequency=frequency,
            time=time_,
            trajectory=trajectory,
        )

        # 3) 更新滑动窗口（追加当前事件，过期的不主动清 —— deque 容量截断）
        self._recent_by_visitor[event.visitor_id].append(event)

        log.info(
            "risk_feature.extracted",
            event_id=event.event_id,
            visitor_id=str(event.visitor_id),
            duration_s=round(duration.duration_seconds, 3),
            visits_in_window=frequency.visits_in_window,
            hour=time_.hour_of_day,
        )
        return risk

    def reset(self) -> None:
        """清空滑动窗口（视频源切换 / 多会话）。"""
        self._recent_by_visitor.clear()

    def history_size(self, visitor_id: Optional[UUID] = None) -> int:
        """查询滑动窗口历史事件数（用于测试 / 调试）。"""
        if visitor_id is None:
            return sum(len(d) for d in self._recent_by_visitor.values())
        return len(self._recent_by_visitor.get(visitor_id, []))

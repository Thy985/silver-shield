"""Feature / RiskFeature / FeatureExtractor 测试（P0-7a · 结构化数值信号层）。

> **P0-7a = 结构化数值特征；P0-7b = 风险语义层（Rule Engine）。**
> 本测试严格验证 Feature 是"被测量的数值"，不验证任何业务判断逻辑（"长停留" / "夜间"等阈值判断）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.feature import (
    DurationFeature,
    Feature,
    RiskFeature,
    TimeFeature,
    TrajectoryFeature,
    VisitFrequencyFeature,
)
from home_perception.analysis.feature_extractor import (
    DurationFeatureExtractor,
    FeatureExtractor,
    TimeFeatureExtractor,
    TrajectoryFeatureExtractor,
    VisitFrequencyFeatureExtractor,
)


# ============================================================================
# 时区 helper
# ============================================================================

def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_event(
    duration_s: float = 5.0,
    leave_hour: int = 10,
    visitor_id: uuid.UUID | None = None,
    source_video: str = "cam01",
) -> VisitorEvent:
    """构造一个用于 Feature 测试的最小 VisitorEvent。

    enter_time = 2026-07-19 09:00:00 UTC
    leave_time = enter + duration_s（按秒累加，不影响 hour_of_day 推算）
    """
    enter = utc(2026, 7, 19, 9, 0, 0)
    leave = enter + timedelta(seconds=duration_s)
    return VisitorEvent(
        visitor_id=visitor_id or uuid.uuid4(),
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration_s,
        source_video=source_video,
    )


# ============================================================================
# Feature 基类与具体 Feature
# ============================================================================

class TestFeatureBase:
    def test_feature_base_fields(self):
        v_id = uuid.uuid4()
        t = utc(2026, 7, 19, 12, 0, 0)
        f = Feature(visitor_id=v_id, event_id="e1", source_video="cam01", computed_at=t)
        assert f.visitor_id == v_id
        assert f.event_id == "e1"
        assert f.source_video == "cam01"
        assert f.computed_at == t

    def test_str_uuid_accepted(self):
        f = Feature(
            visitor_id="550e8400-e29b-41d4-a716-446655440000",
            event_id="e1", source_video="cam01",
        )
        assert isinstance(f.visitor_id, uuid.UUID)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="computed_at"):
            Feature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                computed_at=datetime(2026, 7, 19, 12, 0, 0),  # naive
            )


class TestDurationFeature:
    def test_basic(self):
        f = DurationFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            duration_seconds=480.0,
        )
        assert f.duration_seconds == 480.0
        d = f.to_dict()
        assert d["duration_seconds"] == 480.0
        assert d["feature_type"] == "DurationFeature"

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="duration_seconds"):
            DurationFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                duration_seconds=-1.0,
            )


class TestVisitFrequencyFeature:
    def test_basic(self):
        f = VisitFrequencyFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            visits_in_window=3, window_seconds=1800.0,
        )
        assert f.visits_in_window == 3
        assert f.window_seconds == 1800.0

    def test_zero_visits_rejected(self):
        with pytest.raises(ValueError, match="visits_in_window"):
            VisitFrequencyFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                visits_in_window=0, window_seconds=1800.0,
            )

    def test_zero_window_rejected(self):
        with pytest.raises(ValueError, match="window_seconds"):
            VisitFrequencyFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                visits_in_window=1, window_seconds=0.0,
            )


class TestTimeFeature:
    def test_basic(self):
        f = TimeFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            hour_of_day=14, day_of_week=0, is_weekend=False,
        )
        assert f.hour_of_day == 14
        assert f.day_of_week == 0
        assert f.is_weekend is False

    def test_from_datetime(self):
        # 2026-07-19 是周日（day_of_week=6）
        dt = utc(2026, 7, 19, 23, 30, 0)
        f = TimeFeature.from_datetime(
            dt, visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
        )
        assert f.hour_of_day == 23
        assert f.day_of_week == 6  # 周日
        assert f.is_weekend is True

    def test_weekend_inconsistency_rejected(self):
        """is_weekend 必须与 day_of_week 一致（避免人为误填）。"""
        with pytest.raises(ValueError, match="is_weekend"):
            TimeFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                hour_of_day=10, day_of_week=0,  # 周一
                is_weekend=True,  # 但标为周末 → 矛盾
            )

    def test_invalid_hour_rejected(self):
        with pytest.raises(ValueError, match="hour_of_day"):
            TimeFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                hour_of_day=25, day_of_week=0, is_weekend=False,
            )

    def test_invalid_day_rejected(self):
        with pytest.raises(ValueError, match="day_of_week"):
            TimeFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                hour_of_day=10, day_of_week=7, is_weekend=False,
            )


class TestTrajectoryFeature:
    def test_basic_mvp_default(self):
        """MVP 单摄默认：bbox_center_displacement=0, segment_count=1。"""
        f = TrajectoryFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
        )
        assert f.bbox_center_displacement == 0.0
        assert f.segment_count == 1

    def test_negative_displacement_rejected(self):
        with pytest.raises(ValueError, match="bbox_center_displacement"):
            TrajectoryFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                bbox_center_displacement=-1.0,
            )

    def test_zero_segment_rejected(self):
        with pytest.raises(ValueError, match="segment_count"):
            TrajectoryFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                segment_count=0,
            )


# ============================================================================
# RiskFeature 聚合
# ============================================================================

class TestRiskFeature:
    def test_aggregate_all_features(self):
        v_id = uuid.uuid4()
        t = utc(2026, 7, 19, 12, 0, 0)
        risk = RiskFeature(
            visitor_id=v_id, event_id="e1", source_video="cam01",
            computed_at=t,
            duration=DurationFeature(visitor_id=v_id, event_id="e1", source_video="cam01", duration_seconds=480.0, computed_at=t),
            frequency=VisitFrequencyFeature(visitor_id=v_id, event_id="e1", source_video="cam01", visits_in_window=3, window_seconds=1800.0, computed_at=t),
            time=TimeFeature(visitor_id=v_id, event_id="e1", source_video="cam01", hour_of_day=12, day_of_week=0, is_weekend=False, computed_at=t),
            trajectory=TrajectoryFeature(visitor_id=v_id, event_id="e1", source_video="cam01", computed_at=t),
        )
        assert risk.has_all_features() is True
        d = risk.to_dict()
        assert d["duration"]["duration_seconds"] == 480.0
        assert d["frequency"]["visits_in_window"] == 3
        assert d["time"]["hour_of_day"] == 12
        assert d["trajectory"]["segment_count"] == 1

    def test_optional_features_default_none(self):
        """未指定的具体 Feature 默认为 None（Rule Engine 跳过对应规则）。"""
        v_id = uuid.uuid4()
        risk = RiskFeature(
            visitor_id=v_id, event_id="e1", source_video="cam01",
            computed_at=utc(2026, 7, 19, 12, 0, 0),
        )
        assert risk.duration is None
        assert risk.frequency is None
        assert risk.time is None
        assert risk.trajectory is None
        assert risk.has_all_features() is False
        d = risk.to_dict()
        assert d["duration"] is None
        assert d["frequency"] is None

    def test_to_json_roundtrip(self):
        v_id = uuid.uuid4()
        t = utc(2026, 7, 19, 12, 0, 0)
        risk = RiskFeature(
            visitor_id=v_id, event_id="e1", source_video="cam01", computed_at=t,
            duration=DurationFeature(visitor_id=v_id, event_id="e1", source_video="cam01", duration_seconds=480.0, computed_at=t),
        )
        j = risk.to_json()
        parsed = json.loads(j)
        assert parsed["visitor_id"] == str(v_id)
        assert parsed["duration"]["duration_seconds"] == 480.0

    def test_naive_computed_at_rejected(self):
        with pytest.raises(ValueError, match="computed_at"):
            RiskFeature(
                visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
                computed_at=datetime(2026, 7, 19, 12, 0, 0),
            )


# ============================================================================
# 4 个具体 Extractor
# ============================================================================

class TestDurationFeatureExtractor:
    def test_extract(self):
        event = make_event(duration_s=480.0, leave_hour=10)
        f = DurationFeatureExtractor.extract(event)
        assert f.duration_seconds == 480.0
        assert f.visitor_id == event.visitor_id
        assert f.event_id == event.event_id


class TestVisitFrequencyFeatureExtractor:
    def test_first_visit_count_is_one(self):
        """首次访问窗口内 = 1 次。"""
        from collections import deque
        event = make_event(duration_s=5.0, leave_hour=10)
        f = VisitFrequencyFeatureExtractor.extract(event, deque(), 1800.0)
        assert f.visits_in_window == 1
        assert f.window_seconds == 1800.0

    def test_includes_recent_history(self):
        """窗口内历史事件被计入。"""
        from collections import deque
        event = make_event(duration_s=5.0, leave_hour=10)
        # 2 个历史事件 leave_time 都在窗口内
        hist = deque([
            make_event(leave_hour=9),
            make_event(leave_hour=9, visitor_id=event.visitor_id),
        ])
        f = VisitFrequencyFeatureExtractor.extract(event, hist, 1800.0)
        assert f.visits_in_window == 3  # 2 历史 + 1 当前

    def test_excludes_old_history_outside_window(self):
        """窗口外历史事件不计入。"""
        from collections import deque
        event = make_event(duration_s=5.0, leave_hour=10)
        # 1 个历史事件 leave_time=2:00（远早于 10:00 - 1800s）
        old = VisitorEvent(
            visitor_id=event.visitor_id,
            enter_time=utc(2026, 7, 19, 1, 0, 0),
            leave_time=utc(2026, 7, 19, 2, 0, 0),
            duration_seconds=3600.0,
            source_video="cam01",
        )
        hist = deque([old])
        f = VisitFrequencyFeatureExtractor.extract(event, hist, 1800.0)
        assert f.visits_in_window == 1  # 只算当前


class TestTimeFeatureExtractor:
    def test_extract_from_leave_time(self):
        # 2026-07-19 23:30 是周日
        event = VisitorEvent(
            visitor_id=uuid.uuid4(),
            enter_time=utc(2026, 7, 19, 22, 0, 0),
            leave_time=utc(2026, 7, 19, 23, 30, 0),
            duration_seconds=5400.0,
            source_video="cam01",
        )
        f = TimeFeatureExtractor.extract(event)
        assert f.hour_of_day == 23
        assert f.day_of_week == 6  # 周日
        assert f.is_weekend is True


class TestTrajectoryFeatureExtractor:
    def test_mvp_default(self):
        event = make_event()
        f = TrajectoryFeatureExtractor.extract(event)
        assert f.bbox_center_displacement == 0.0
        assert f.segment_count == 1


# ============================================================================
# FeatureExtractor 编排器
# ============================================================================

class TestFeatureExtractor:
    def test_single_event(self):
        ext = FeatureExtractor(frequency_window_s=1800.0)
        event = make_event(duration_s=480.0)
        risk = ext.extract(event)
        assert risk.visitor_id == event.visitor_id
        assert risk.event_id == event.event_id
        assert risk.has_all_features() is True
        # 4 个 Feature 都被计算
        assert risk.duration.duration_seconds == 480.0
        assert risk.frequency.visits_in_window == 1  # 首次
        # enter=09:00, leave=09:00+480s=09:08, hour_of_day=9
        assert risk.time.hour_of_day == 9
        assert risk.time.day_of_week == 6  # 2026-07-19 是周日
        assert risk.time.is_weekend is True
        assert risk.trajectory.bbox_center_displacement == 0.0

    def test_frequency_window_grows_with_visits(self):
        """同一 visitor_id 多次访问：visits_in_window 累加。"""
        ext = FeatureExtractor(frequency_window_s=1800.0)
        v_id = uuid.uuid4()
        events = [
            make_event(duration_s=5.0, leave_hour=9, visitor_id=v_id),
            make_event(duration_s=5.0, leave_hour=10, visitor_id=v_id),
            make_event(duration_s=5.0, leave_hour=10, visitor_id=v_id),  # 同一小时 reenter
        ]
        risks = [ext.extract(e) for e in events]
        assert risks[0].frequency.visits_in_window == 1
        assert risks[1].frequency.visits_in_window == 2
        assert risks[2].frequency.visits_in_window == 3

    def test_reset_clears_history(self):
        """reset() 清空滑动窗口。"""
        ext = FeatureExtractor(frequency_window_s=1800.0)
        v_id = uuid.uuid4()
        ext.extract(make_event(visitor_id=v_id))
        ext.extract(make_event(visitor_id=v_id))
        assert ext.history_size(v_id) == 2
        ext.reset()
        assert ext.history_size(v_id) == 0

    def test_window_size_validation(self):
        with pytest.raises(ValueError):
            FeatureExtractor(frequency_window_s=0.0)
        with pytest.raises(ValueError):
            FeatureExtractor(frequency_window_s=1800.0, max_history_per_visitor=0)


# ============================================================================
# 契约边界：Feature 严禁包含业务判断字段（ADR-0007 / ADR-0008）
# ============================================================================

class TestFeatureContractBoundary:
    """Feature / RiskFeature 严格不含业务判断字段。"""

    FORBIDDEN = {
        "risk_level", "score", "visit_type", "is_suspicious",
        "is_long_visit", "is_odd_hour", "is_repeat", "is_night",
        "warning", "verdict", "event_type",
    }

    def test_duration_feature_no_business_judgment(self):
        d = DurationFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            duration_seconds=10.0,
        ).to_dict()
        assert not (self.FORBIDDEN & set(d.keys())), (
            f"DurationFeature 含业务字段 {self.FORBIDDEN & set(d.keys())}"
        )

    def test_visit_frequency_feature_no_business_judgment(self):
        d = VisitFrequencyFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            visits_in_window=3, window_seconds=1800.0,
        ).to_dict()
        assert not (self.FORBIDDEN & set(d.keys()))

    def test_time_feature_no_business_judgment(self):
        d = TimeFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
            hour_of_day=3, day_of_week=0, is_weekend=False,
        ).to_dict()
        # is_weekend 是日历事实，不在禁用集
        leaked = self.FORBIDDEN & set(d.keys())
        assert not leaked, f"TimeFeature 含业务字段 {leaked}"
        # 但 is_odd_hour / is_night 等具体判断字段必须没有
        assert "is_odd_hour" not in d
        assert "is_night" not in d

    def test_trajectory_feature_no_business_judgment(self):
        d = TrajectoryFeature(
            visitor_id=uuid.uuid4(), event_id="e1", source_video="cam01",
        ).to_dict()
        assert not (self.FORBIDDEN & set(d.keys()))

    def test_risk_feature_no_business_judgment(self):
        v_id = uuid.uuid4()
        t = utc(2026, 7, 19, 12, 0, 0)
        risk = RiskFeature(
            visitor_id=v_id, event_id="e1", source_video="cam01", computed_at=t,
            duration=DurationFeature(visitor_id=v_id, event_id="e1", source_video="cam01", duration_seconds=480.0, computed_at=t),
        )
        d = risk.to_dict()
        assert not (self.FORBIDDEN & set(d.keys())), (
            f"RiskFeature 含业务字段 {self.FORBIDDEN & set(d.keys())}"
        )


# ============================================================================
# CAVIAR 真实链路端到端（fixture 缺失优雅 skip）
# ============================================================================

CAVIAR_ONE_STOP_ENTER = "tests/fixtures/doorway/one_stop_enter"


def test_caviar_end_to_end_pipeline_yields_risk_features():
    """CAVIAR OneStopEnter1cor: detector → tracker → event → feature 全链路。

    验证：CAVIAR 真实监控上 FeatureExtractor 能产出 RiskFeature 列表，字段全部为数值。
    """
    pytest.importorskip("ultralytics")
    import cv2
    from pathlib import Path
    from home_perception.detection.detector import YOLODetector
    from home_perception.detection.tracker import VisitorTracker
    from home_perception.analysis.event_builder import VisitorEventBuilder

    p = Path(CAVIAR_ONE_STOP_ENTER)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip("CAVIAR fixture 缺失；跑 tests/fixtures/download_fixtures.py")

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
    event_builder = VisitorEventBuilder(tracker, source_video="CAVIAR/OneStopEnter1cor")
    feat_ext = FeatureExtractor(frequency_window_s=1800.0)

    n_with_track = 0
    risk_features = []
    for f in frames:
        r = det.detect(f)
        for d in r.detections:
            if d.track_id is not None:
                n_with_track += 1
        for event in event_builder.update(r.detections):
            risk_features.append(feat_ext.extract(event))

    if n_with_track == 0:
        # tracker 没初始化 → 无事件 → 无 risk_feature
        assert risk_features == []
    else:
        for rf in risk_features:
            # 所有 Feature 都是数值字段，无判断字段
            assert rf.has_all_features() or any([
                rf.duration, rf.frequency, rf.time, rf.trajectory,
            ])
            if rf.duration:
                assert rf.duration.duration_seconds >= 0
            if rf.frequency:
                assert rf.frequency.visits_in_window >= 1
            if rf.time:
                assert 0 <= rf.time.hour_of_day <= 23
                assert 0 <= rf.time.day_of_week <= 6
            if rf.trajectory:
                assert rf.trajectory.segment_count >= 1

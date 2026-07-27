"""Stage B 实时旁路回归测试（ADR-0021 · Migration Stage B）。

torch-free，进 CI 每 PR 合约子集。

验证工程方案 §8.3 硬性合入门：
- **flag 关闭 golden 回归**：``behavior_states == []``，历史五字段正常产出（旁路零泄漏）
- **flag 开启仅多 behavior_states**：同一帧序列下，历史五字段与关闭态**逐字段一致**，
  仅 ``behavior_states`` 可非空（旁路不污染主线）
- **flag 关闭零组件构造**：``from_settings`` 装配后实时组件为 None（零运行时开销）
- **eval_interval_frames 跳帧**：非评估帧 ``behavior_states == []``，评估帧才产出
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.behavior_builder import BehaviorBuilder
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.recent_behavior_store import RecentBehaviorStore
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.core.config import Settings
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.runtime import FrameResult, PerceptionPipeline


# ============================================================================
# 测试辅助（模式复用自 tests/test_runtime.py，保持 Stage B 自包含）
# ============================================================================

class ManualClock:
    """可控时钟：now() 返回当前时间，advance() 推进。"""

    def __init__(self, base: Optional[datetime] = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class StubDetector:
    """按 plan 返回 Detection 列表；可选 clock 每次 detect 推进 1s。"""

    def __init__(self, plan: List[List[Detection]], clock: Optional[ManualClock] = None):
        self.plan = plan
        self.clock = clock
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        if self.clock is not None:
            self.clock.advance(1.0)
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets, timestamp=0.0, inference_ms=0.0,
            source_size=(1, 1), inference_size=(1, 1), model="stub",
        )


def _person(track_id: int = 1) -> List[Detection]:
    return [Detection(
        class_id=0, class_name="person", confidence=0.9,
        bbox=[0, 0, 10, 10], timestamp=0.0, track_id=track_id,
    )]


def _build_pipeline(
    detector,
    clock: ManualClock,
    *,
    realtime_enabled: bool = False,
    eval_interval_frames: int = 1,
) -> PerceptionPipeline:
    """构造 PerceptionPipeline，可选挂入 Stage B 实时旁路组件。"""
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test", location="入户门",
        thresholds=ThresholdConfig(), now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock,
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher, publisher=MockPublisher(),
        notifier=MockNotifier(), max_retries=3,
    )

    behavior_builder: Optional[BehaviorBuilder] = None
    recent_store: Optional[RecentBehaviorStore] = None
    if realtime_enabled:
        behavior_builder = BehaviorBuilder(event_builder=event_builder)
        recent_store = RecentBehaviorStore()

    return PerceptionPipeline(
        detector=detector, tracker=tracker, event_builder=event_builder,
        feature_extractor=feat, rule_engine=rule_engine, decision_engine=decision,
        executor=executor, now_provider=clock,
        behavior_builder=behavior_builder,
        recent_behavior_store=recent_store,
        realtime_enabled=realtime_enabled,
        eval_interval_frames=eval_interval_frames,
    )


def _run_frames(p: PerceptionPipeline, n: int) -> List[FrameResult]:
    """跑 n 帧 None，返回每帧 FrameResult。"""
    return [p.process_frame(None, frame_index=i) for i in range(n)]


def _history_fields(r: FrameResult) -> tuple:
    """提取历史五字段（不含 behavior_states）用于逐字段对比。"""
    return (
        r.frame_index, r.n_detections, r.n_visitor_events,
        len(r.perception_events), len(r.warnings), len(r.commands),
    )


# ============================================================================
# 1. flag 关闭：golden 回归（behavior_states 空 + 历史字段正常）
# ============================================================================

class TestFlagOffGolden:
    def test_flag_off_behavior_states_empty(self):
        """flag 关闭：behavior_states 恒空（旁路零泄漏）。"""
        clock = ManualClock()
        plan = [_person(1), _person(1), []]  # 进场2帧 + 离场1帧
        p = _build_pipeline(StubDetector(plan, clock), clock, realtime_enabled=False)
        results = _run_frames(p, 3)
        for r in results:
            assert r.behavior_states == []

    def test_flag_off_history_fields_normal(self):
        """flag 关闭：历史五字段正常产出（与基线一致，不受 Stage B 影响）。"""
        clock = ManualClock()
        # absence_gap_s=5.0 + StubDetector 每帧推进 1s → 需连续 5 帧空检测后才触发离场
        plan = [_person(1), _person(1)] + [[] for _ in range(6)]
        p = _build_pipeline(StubDetector(plan, clock), clock, realtime_enabled=False)
        results = _run_frames(p, len(plan))
        # 帧 0/1 有检测，其余空
        assert results[0].n_detections == 1
        assert results[1].n_detections == 1
        assert results[2].n_detections == 0
        # 第 6 帧（clock=7s, absence=5s）触发离场 → 产出 VisitorEvent
        total_events = sum(r.n_visitor_events for r in results)
        assert total_events >= 1


# ============================================================================
# 2. flag 开启：仅多 behavior_states，历史字段逐字段一致
# ============================================================================

class TestFlagOnBypassIsolation:
    def test_flag_on_history_unchanged_vs_off(self):
        """flag 开启 vs 关闭：同一帧序列下历史五字段逐字段一致（旁路不污染主线）。

        这是工程方案 §8.3 硬性合入门：旁路不得改变历史行为。
        """
        # 同一 plan + 同一 clock 起点跑两遍（flag off vs on）
        plan = [_person(1), _person(1), _person(1), [], []]

        clock_off = ManualClock()
        p_off = _build_pipeline(StubDetector(plan, clock_off), clock_off, realtime_enabled=False)
        results_off = _run_frames(p_off, 5)

        clock_on = ManualClock()
        p_on = _build_pipeline(StubDetector(plan, clock_on), clock_on, realtime_enabled=True)
        results_on = _run_frames(p_on, 5)

        # 逐帧对比历史五字段
        assert len(results_off) == len(results_on)
        for i, (ro, rn) in enumerate(zip(results_off, results_on)):
            assert _history_fields(ro) == _history_fields(rn), (
                f"frame {i}: 历史字段不一致 (off={_history_fields(ro)} on={_history_fields(rn)})"
            )

    def test_flag_on_behavior_states_nonempty_when_active(self):
        """flag 开启：有 active track 时 behavior_states 非空。"""
        clock = ManualClock()
        plan = [_person(1), _person(1)]  # 持续在场
        p = _build_pipeline(StubDetector(plan, clock), clock, realtime_enabled=True)
        r0 = p.process_frame(None, frame_index=0)
        # 帧 0：track 首次出现，event_builder 分配 UUID，behavior_builder 应产出 state
        assert len(r0.behavior_states) >= 1
        s = r0.behavior_states[0]
        assert s.track_id == 1
        assert s.visitor_instance_id  # UUID 字符串非空
        assert s.phase.value == "ongoing"

    def test_flag_on_dwell_accumulates(self):
        """flag 开启：dwell_seconds 随帧累积（注入时钟驱动）。"""
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1)]
        p = _build_pipeline(StubDetector(plan, clock), clock, realtime_enabled=True)
        r0 = p.process_frame(None, frame_index=0)
        r1 = p.process_frame(None, frame_index=1)
        # clock 每帧推进 1s，dwell 应递增
        assert r1.behavior_states[0].dwell_seconds > r0.behavior_states[0].dwell_seconds


# ============================================================================
# 3. flag 关闭零组件构造（from_settings 装配验证）
# ============================================================================

class TestFromSettingsAssembly:
    def test_flag_off_no_components_constructed(self):
        """flag 关闭：from_settings 不构造实时组件（零运行时开销）。"""
        s = Settings()
        s.realtime_risk.enabled = False
        # 不传 detector → from_settings 会试图构造 YOLODetector，但构造期不触发 torch
        # 这里只验证装配逻辑：用 mock detector 跳过 YOLO
        from unittest.mock import MagicMock
        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is False
        assert p._behavior_builder is None
        assert p._recent_behavior_store is None

    def test_flag_on_components_constructed(self):
        """flag 开启：from_settings 构造实时组件。"""
        s = Settings()
        s.realtime_risk.enabled = True
        from unittest.mock import MagicMock
        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is True
        assert p._behavior_builder is not None
        assert p._recent_behavior_store is not None


# ============================================================================
# 4. eval_interval_frames 跳帧对称
# ============================================================================

class TestEvalIntervalFrames:
    def test_eval_interval_skips_non_eval_frames(self):
        """eval_interval_frames=2：非评估帧 behavior_states 空，评估帧才产出。"""
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock), clock,
            realtime_enabled=True, eval_interval_frames=2,
        )
        results = _run_frames(p, 4)
        # frame_index 0, 2 是评估帧（0%2==0, 2%2==0）；1, 3 非评估帧
        assert len(results[0].behavior_states) >= 1  # 评估帧
        assert results[1].behavior_states == []       # 非评估帧
        assert len(results[2].behavior_states) >= 1   # 评估帧
        assert results[3].behavior_states == []       # 非评估帧

    def test_eval_interval_history_unchanged(self):
        """eval_interval_frames>1 不影响历史字段（跳帧只影响实时旁路）。"""
        plan = [_person(1), _person(1), _person(1), _person(1)]

        clock1 = ManualClock()
        p1 = _build_pipeline(
            StubDetector(plan, clock1), clock1,
            realtime_enabled=True, eval_interval_frames=1,
        )
        r1 = _run_frames(p1, 4)

        clock2 = ManualClock()
        p2 = _build_pipeline(
            StubDetector(plan, clock2), clock2,
            realtime_enabled=True, eval_interval_frames=2,
        )
        r2 = _run_frames(p2, 4)

        for i, (a, b) in enumerate(zip(r1, r2)):
            assert _history_fields(a) == _history_fields(b), f"frame {i} 历史字段不一致"


# ============================================================================
# 5. RealtimeRiskConfig 校验
# ============================================================================

class TestRealtimeRiskConfig:
    def test_defaults(self):
        from home_perception.core.config import RealtimeRiskConfig
        c = RealtimeRiskConfig()
        assert c.enabled is False
        assert c.eval_interval_frames == 1

    def test_rejects_eval_interval_below_one(self):
        from home_perception.core.config import RealtimeRiskConfig
        with pytest.raises(ValueError):
            RealtimeRiskConfig(eval_interval_frames=0)

    def test_rejects_bool_eval_interval(self):
        from home_perception.core.config import RealtimeRiskConfig
        with pytest.raises(ValueError):
            RealtimeRiskConfig(eval_interval_frames=True)

    def test_yaml_loads_realtime_section(self):
        """config/default.yaml 的 realtime_risk 段被正确加载。"""
        s = Settings.load()
        assert s.realtime_risk.enabled is False
        assert s.realtime_risk.eval_interval_frames == 1

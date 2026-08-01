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

from datetime import UTC, datetime, timedelta

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
from home_perception.analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
from home_perception.analysis.recent_behavior_store import RecentBehaviorStore
from home_perception.analysis.risk_signal import SignalTransition
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

    def __init__(self, base: datetime | None = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class StubDetector:
    """按 plan 返回 Detection 列表；可选 clock 每次 detect 推进 1s。"""

    def __init__(self, plan: list[list[Detection]], clock: ManualClock | None = None):
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
            detections=dets,
            timestamp=0.0,
            inference_ms=0.0,
            source_size=(1, 1),
            inference_size=(1, 1),
            model="stub",
        )


def _person(track_id: int = 1) -> list[Detection]:
    return [
        Detection(
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=[0, 0, 10, 10],
            timestamp=0.0,
            track_id=track_id,
        )
    ]


def _build_pipeline(
    detector,
    clock: ManualClock,
    *,
    realtime_enabled: bool = False,
    eval_interval_frames: int = 1,
    thresholds: ThresholdConfig | None = None,
    decision_enabled: bool = False,
) -> PerceptionPipeline:
    """构造 PerceptionPipeline，可选挂入 Stage B/C/D 实时旁路组件。"""
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    th = thresholds or ThresholdConfig()
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test",
        location="入户门",
        thresholds=th,
        now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(),
        now_provider=clock,
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher,
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=3,
    )

    behavior_builder: BehaviorBuilder | None = None
    recent_store: RecentBehaviorStore | None = None
    evaluator: RealTimeRiskEvaluator | None = None
    if realtime_enabled:
        behavior_builder = BehaviorBuilder(event_builder=event_builder)
        recent_store = RecentBehaviorStore()
        evaluator = RealTimeRiskEvaluator(thresholds=th, now_provider=clock)

    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feat,
        rule_engine=rule_engine,
        decision_engine=decision,
        executor=executor,
        now_provider=clock,
        behavior_builder=behavior_builder,
        recent_behavior_store=recent_store,
        realtime_evaluator=evaluator,
        realtime_enabled=realtime_enabled,
        eval_interval_frames=eval_interval_frames,
        decision_enabled=decision_enabled,
    )


def _run_frames(p: PerceptionPipeline, n: int) -> list[FrameResult]:
    """跑 n 帧 None，返回每帧 FrameResult。"""
    return [p.process_frame(None, frame_index=i) for i in range(n)]


def _history_fields(r: FrameResult) -> tuple:
    """提取历史五字段（不含 behavior_states）用于逐字段对比。"""
    return (
        r.frame_index,
        r.n_detections,
        r.n_visitor_events,
        len(r.perception_events),
        len(r.warnings),
        len(r.commands),
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
        assert p._realtime_evaluator is None  # Stage C

    def test_flag_on_components_constructed(self):
        """flag 开启：from_settings 构造实时组件（含 Stage C Evaluator）。"""
        s = Settings()
        s.realtime_risk.enabled = True
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is True
        assert p._behavior_builder is not None
        assert p._recent_behavior_store is not None
        assert p._realtime_evaluator is not None  # Stage C


# ============================================================================
# 4. eval_interval_frames 跳帧对称
# ============================================================================


class TestEvalIntervalFrames:
    def test_eval_interval_skips_non_eval_frames(self):
        """eval_interval_frames=2：非评估帧 behavior_states 空，评估帧才产出。"""
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            eval_interval_frames=2,
        )
        results = _run_frames(p, 4)
        # frame_index 0, 2 是评估帧（0%2==0, 2%2==0）；1, 3 非评估帧
        assert len(results[0].behavior_states) >= 1  # 评估帧
        assert results[1].behavior_states == []  # 非评估帧
        assert len(results[2].behavior_states) >= 1  # 评估帧
        assert results[3].behavior_states == []  # 非评估帧

    def test_eval_interval_history_unchanged(self):
        """eval_interval_frames>1 不影响历史字段（跳帧只影响实时旁路）。"""
        plan = [_person(1), _person(1), _person(1), _person(1)]

        clock1 = ManualClock()
        p1 = _build_pipeline(
            StubDetector(plan, clock1),
            clock1,
            realtime_enabled=True,
            eval_interval_frames=1,
        )
        r1 = _run_frames(p1, 4)

        clock2 = ManualClock()
        p2 = _build_pipeline(
            StubDetector(plan, clock2),
            clock2,
            realtime_enabled=True,
            eval_interval_frames=2,
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
        assert s.realtime_risk.decision_enabled is False  # Stage D

    def test_decision_enabled_default_false(self):
        """Stage D 决策开关默认关闭。"""
        from home_perception.core.config import RealtimeRiskConfig

        c = RealtimeRiskConfig()
        assert c.decision_enabled is False


# ============================================================================
# 6. Stage C：risk_signals 旁路隔离 + Shadow Mode 不接决策
# ============================================================================


class TestStageCFlagOffSignals:
    def test_flag_off_risk_signals_empty(self):
        """flag 关闭：risk_signals 恒空（旁路零泄漏）。"""
        clock = ManualClock()
        plan = [_person(1), _person(1), []]
        p = _build_pipeline(StubDetector(plan, clock), clock, realtime_enabled=False)
        results = _run_frames(p, 3)
        for r in results:
            assert r.risk_signals == []

    def test_flag_off_history_unchanged_vs_on(self):
        """flag 开启 vs 关闭：同一帧序列下历史五字段逐字段一致（Stage C 旁路不污染主线）。

        工程方案 §8.3 硬性合入门：risk_signals 旁路不得改变历史行为。
        """
        plan = [_person(1), _person(1), _person(1), [], []]

        clock_off = ManualClock()
        p_off = _build_pipeline(StubDetector(plan, clock_off), clock_off, realtime_enabled=False)
        results_off = _run_frames(p_off, 5)

        clock_on = ManualClock()
        p_on = _build_pipeline(StubDetector(plan, clock_on), clock_on, realtime_enabled=True)
        results_on = _run_frames(p_on, 5)

        assert len(results_off) == len(results_on)
        for i, (ro, rn) in enumerate(zip(results_off, results_on)):
            assert _history_fields(ro) == _history_fields(rn), (
                f"frame {i}: 历史字段不一致 (off={_history_fields(ro)} on={_history_fields(rn)})"
            )


class TestStageCShadowMode:
    """Shadow Mode 核心断言：信号产出但不接决策（warnings/commands 不因 risk_signals 改变）。"""

    def test_dwell_over_threshold_emits_raised(self):
        """dwell 超阈 → 产出 RAISED 信号（事中可观察）。"""
        # 阈值设小（1.5s），让 dwell 快速超阈
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        # 持续在场 3 帧（clock 每帧推进 1s，dwell=2s 触发）
        plan = [_person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
        )
        r0 = p.process_frame(None, frame_index=0)  # dwell≈0，不触发
        r1 = p.process_frame(None, frame_index=1)  # dwell≈1s，不触发（<1.5）
        r2 = p.process_frame(None, frame_index=2)  # dwell≈2s，触发 RAISED

        assert r0.risk_signals == []
        assert r1.risk_signals == []
        # 帧 2 应产出 RAISED
        raised = [s for s in r2.risk_signals if s.transition is SignalTransition.RAISED]
        assert len(raised) == 1
        assert raised[0].paired_signal_id is None  # RAISED 无配对

    def test_leave_emits_cleared_paired(self):
        """离场后产出配对 CLEARED（paired_signal_id 正确）。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        # 在场 3 帧（触发 RAISED）+ 离场 6 帧（absence_gap_s=5.0，第 5 帧后离场）
        plan = [_person(1), _person(1), _person(1)] + [[] for _ in range(6)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
        )
        results = _run_frames(p, len(plan))

        # 找 RAISED + CLEARED
        all_signals = [s for r in results for s in r.risk_signals]
        raised = [s for s in all_signals if s.transition is SignalTransition.RAISED]
        cleared = [s for s in all_signals if s.transition is SignalTransition.CLEARED]

        assert len(raised) >= 1, "应有 RAISED 信号"
        assert len(cleared) >= 1, "离场后应有 CLEARED 信号"
        # 配对性：CLEARED.paired_signal_id 指向某个 RAISED.signal_id
        raised_ids = {s.signal_id for s in raised}
        for c in cleared:
            assert c.paired_signal_id in raised_ids, (
                f"CLEARED.paired_signal_id {c.paired_signal_id} 未对应任何 RAISED"
            )

    def test_shadow_mode_no_extra_warnings_or_commands(self):
        """Shadow Mode：risk_signals 不接决策 → warnings/commands 与 flag 关闭态一致。

        工程方案 §9 Stage C 验收：'decision_engine diff 为空；影子信号仅进 FrameResult'。
        本测试用同一 plan 跑 flag off vs on，断言 warnings/commands 逐帧一致。
        """
        # 用会触发历史 Warning 的场景：odd_hour + long dwell
        # 但 ThresholdConfig 默认 long_duration=300s，Demo 帧间 1s 不会触发历史路径
        # 这里用小阈值让历史路径也触发，对比 flag on/off 的 warnings 是否一致
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        plan = [_person(1), _person(1), _person(1), []]

        clock_off = ManualClock()
        p_off = _build_pipeline(
            StubDetector(plan, clock_off),
            clock_off,
            realtime_enabled=False,
            thresholds=th,
        )
        results_off = _run_frames(p_off, 4)

        clock_on = ManualClock()
        p_on = _build_pipeline(
            StubDetector(plan, clock_on),
            clock_on,
            realtime_enabled=True,
            thresholds=th,
        )
        results_on = _run_frames(p_on, 4)

        # 逐帧对比 warnings 数量 + commands 数量
        for i, (ro, rn) in enumerate(zip(results_off, results_on)):
            assert len(ro.warnings) == len(rn.warnings), (
                f"frame {i}: warnings 数量不一致 (off={len(ro.warnings)} on={len(rn.warnings)})"
            )
            assert len(ro.commands) == len(rn.commands), (
                f"frame {i}: commands 数量不一致 (off={len(ro.commands)} on={len(rn.commands)})"
            )

    def test_raised_not_repeated_while_active(self):
        """ACTIVE_RISK 持续触发：不重复 RAISED（去抖第一层）。"""
        th = ThresholdConfig(long_duration_seconds=1.0, repeat_visit_count=99)
        clock = ManualClock()
        # 持续在场 5 帧（dwell 持续超阈）
        plan = [_person(1) for _ in range(5)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
        )
        results = _run_frames(p, 5)

        all_raised = [
            s for r in results for s in r.risk_signals if s.transition is SignalTransition.RAISED
        ]
        # 只应有一次 RAISED（首次触发后持续 ACTIVE_RISK 不重复）
        assert len(all_raised) == 1, f"应有 1 次 RAISED，实际 {len(all_raised)}"


# ============================================================================
# 7. Stage D：决策接入（RAISED → adapter → DecisionEngine → Warning）
# ============================================================================


class TestStageDFlagOffDecision:
    """flag 关闭时 decision_enabled 无意义（无论取值，无实时 Warning）。"""

    def test_flag_off_decision_off_no_realtime_warning(self):
        """flag off + decision off：无实时 Warning（基线）。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=False,
            thresholds=th,
            decision_enabled=False,
        )
        results = _run_frames(p, 3)
        # 无实时路径，无 risk_signals，无实时 Warning
        for r in results:
            assert r.risk_signals == []

    def test_flag_off_decision_on_no_effect(self):
        """flag off + decision on：decision_enabled 无意义（无实时路径触发）。

        等价于基线——realtime_enabled=false 时 process_frame 跳过整个旁路块，
        decision_enabled 即使为 true 也不会产生任何实时 Warning。
        """
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=False,
            thresholds=th,
            decision_enabled=True,
        )
        results = _run_frames(p, 3)
        for r in results:
            assert r.risk_signals == []  # 无实时路径


class TestStageDShadowModeNoWarning:
    """flag on + decision off：Shadow Mode（产信号但不接决策，无实时 Warning）。"""

    def test_shadow_mode_raised_no_warning(self):
        """flag on + decision off：RAISED 产出但不产实时 Warning。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        plan = [_person(1), _person(1), _person(1)]  # 帧 2 dwell=2s 触发
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=False,
        )
        results = _run_frames(p, 3)

        # 帧 2 应有 RAISED 信号
        all_signals = [s for r in results for s in r.risk_signals]
        raised = [s for s in all_signals if s.transition is SignalTransition.RAISED]
        assert len(raised) >= 1, "应有 RAISED 信号"

        # 但 Shadow Mode：不接决策 → 无额外 Warning 来自实时路径
        # 注意：历史路径也可能产 Warning（dwell 超阈触发 abnormal_dwell），
        # 这里只断言"实时路径未额外增加 Warning"——与 flag off 基线对比
        clock_base = ManualClock()
        p_base = _build_pipeline(
            StubDetector(plan, clock_base),
            clock_base,
            realtime_enabled=False,
            thresholds=th,
            decision_enabled=False,
        )
        results_base = _run_frames(p_base, 3)

        for i, (r_shadow, r_base) in enumerate(zip(results, results_base)):
            assert len(r_shadow.warnings) == len(r_base.warnings), (
                f"frame {i}: Shadow Mode 应不增加 Warning "
                f"(shadow={len(r_shadow.warnings)} base={len(r_base.warnings)})"
            )


class TestStageDDecisionOn:
    """flag on + decision on：Stage D 决策接入（RAISED → Warning）。"""

    def test_raised_produces_realtime_warning(self):
        """flag on + decision on：RAISED 信号经 adapter 汇入 DecisionEngine 产 Warning。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        # 持续在场 3 帧，帧 2 dwell=2s 触发 RAISED
        plan = [_person(1), _person(1), _person(1)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=True,
        )
        results = _run_frames(p, 3)

        # 帧 2 应有 RAISED 信号
        r2 = results[2]
        raised = [s for s in r2.risk_signals if s.transition is SignalTransition.RAISED]
        assert len(raised) == 1, "帧 2 应有 1 个 RAISED"

        # Stage D：RAISED → adapter → DecisionEngine → Warning
        # 与 Shadow Mode 对比：decision on 应比 decision off 多出实时 Warning
        clock_shadow = ManualClock()
        p_shadow = _build_pipeline(
            StubDetector(plan, clock_shadow),
            clock_shadow,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=False,
        )
        results_shadow = _run_frames(p_shadow, 3)

        # decision on 的 Warning 数应 > decision off（实时路径额外产 Warning）
        total_warnings_on = sum(len(r.warnings) for r in results)
        total_warnings_off = sum(len(r.warnings) for r in results_shadow)
        assert total_warnings_on > total_warnings_off, (
            f"Stage D 应增加 Warning：on={total_warnings_on} off={total_warnings_off}"
        )

        # 实时路径产出的 PerceptionEvent 应含 meta.realtime=True
        rt_percs = [
            p
            for r in results
            for p in r.perception_events
            if (p.meta or {}).get("realtime") is True
        ]
        assert len(rt_percs) >= 1, "应有来自实时路径的 PerceptionEvent"

    def test_cleared_does_not_produce_warning(self):
        """flag on + decision on：CLEARED 信号不进决策（不产 PerceptionEvent / Warning）。

        工程方案 §3.1 步骤 4：CLEARED 仅随 FrameResult 供展示层熄灭风险卡。
        """
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        # 在场 3 帧（触发 RAISED）+ 离场 6 帧（触发 CLEARED）
        plan = [_person(1), _person(1), _person(1)] + [[] for _ in range(6)]
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=True,
        )
        results = _run_frames(p, len(plan))

        # 找 CLEARED 信号所在帧
        cleared_frames = [
            (i, s)
            for i, r in enumerate(results)
            for s in r.risk_signals
            if s.transition is SignalTransition.CLEARED
        ]
        assert len(cleared_frames) >= 1, "应有 CLEARED 信号"

        # CLEARED 帧：不应有来自实时路径的 PerceptionEvent（CLEARED → adapter 返回 None）
        for i, sig in cleared_frames:
            r = results[i]
            rt_percs_in_frame = [
                p for p in r.perception_events if (p.meta or {}).get("realtime") is True
            ]
            # CLEARED 不产 PerceptionEvent；但同帧可能有其他 RAISED（多主体场景）
            # 本测试单主体，CLEARED 帧不应有实时 PerceptionEvent
            assert rt_percs_in_frame == [], (
                f"frame {i}: CLEARED 帧不应有实时 PerceptionEvent，实际 {len(rt_percs_in_frame)} 个"
            )

    def test_history_unchanged_vs_shadow_mode(self):
        """Stage D on vs Shadow Mode：历史五字段逐字段一致（决策接入不污染历史路径）。

        工程方案 §6 检查清单：DecisionEngine / DecisionPolicy diff 为空，
        实时路径是平行步骤，不重构既有逐事件循环。
        """
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        plan = [_person(1), _person(1), _person(1), [], []]

        clock_shadow = ManualClock()
        p_shadow = _build_pipeline(
            StubDetector(plan, clock_shadow),
            clock_shadow,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=False,
        )
        results_shadow = _run_frames(p_shadow, 5)

        clock_dec = ManualClock()
        p_dec = _build_pipeline(
            StubDetector(plan, clock_dec),
            clock_dec,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=True,
        )
        results_dec = _run_frames(p_dec, 5)

        # 逐帧对比历史五字段
        # 注意：_history_fields 不含 perception_events（实时路径会增加），
        # 但含 n_visitor_events / warnings / commands 数量
        # Stage D on 会增加 warnings/commands，所以这里只对比不受实时影响的部分：
        # frame_index / n_detections / n_visitor_events（这些只由历史路径决定）
        for i, (rs, rd) in enumerate(zip(results_shadow, results_dec)):
            assert rs.frame_index == rd.frame_index, f"frame {i}: frame_index 不一致"
            assert rs.n_detections == rd.n_detections, f"frame {i}: n_detections 不一致"
            assert rs.n_visitor_events == rd.n_visitor_events, (
                f"frame {i}: n_visitor_events 不一致 "
                f"(shadow={rs.n_visitor_events} dec={rd.n_visitor_events})"
            )


class TestStageDFromSettingsAssembly:
    """from_settings 装配：decision_enabled 透传。"""

    def test_flag_off_decision_off_no_evaluator(self):
        """flag off：from_settings 不构造实时组件（含 evaluator）。"""
        s = Settings()
        s.realtime_risk.enabled = False
        s.realtime_risk.decision_enabled = False
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is False
        assert p._realtime_evaluator is None
        assert p._decision_enabled is False

    def test_flag_on_decision_off_shadow_mode(self):
        """flag on + decision off：构造实时组件，但 decision_enabled=False（Shadow Mode）。"""
        s = Settings()
        s.realtime_risk.enabled = True
        s.realtime_risk.decision_enabled = False
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is True
        assert p._realtime_evaluator is not None
        assert p._decision_enabled is False

    def test_flag_on_decision_on_stage_d(self):
        """flag on + decision on：构造实时组件，decision_enabled=True（Stage D）。"""
        s = Settings()
        s.realtime_risk.enabled = True
        s.realtime_risk.decision_enabled = True
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._realtime_enabled is True
        assert p._realtime_evaluator is not None
        assert p._decision_enabled is True

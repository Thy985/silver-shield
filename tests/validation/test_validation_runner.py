"""ADR-0032 契约测试（Slice D 端到端 + T6 + T9）。

端到端用 **torch-free** 的 ``PerceptionPipeline``（``ScenarioDetectionDetector`` 替代
``YOLODetector``），证明「detector→tracker→event_builder→feature→rule→decision」整条链路
在受控输入下真实运行（与 ``CachedDetectionDetector`` E2E 同范式）。不拉起任何真实模型。
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
from home_perception.action.executor import ActionExecutor
from home_perception.action.notifier import MockNotifier
from home_perception.action.publisher import MockPublisher
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine
from home_perception.detection.tracker import VisitorTracker
from home_perception.runtime.pipeline import PerceptionPipeline
from home_perception.validation import (
    ScenarioCompiler,
    ScenarioRunner,
    ScenarioValidator,
    load_scenario,
)
from home_perception.validation.runner.runner import (
    RunResult,
    ValidationResult,
)

FIX = (
    pathlib.Path(__import__("home_perception.validation", fromlist=["__file__"]).__file__).parent
    / "fixtures"
    / "scenarios"
)


class SimpleClock:
    """可推进的确定性时钟（替代 DemoClock，避免测试依赖 runtime 内部）。"""

    def __init__(self, start: datetime, interval_s: float = 0.5):
        self._t = start
        self.interval_s = interval_s

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def tick(self, dt: float | None = None) -> None:
        self._t = self._t + timedelta(seconds=dt if dt is not None else self.interval_s)


def build_torchfree_pipeline(detector, clock, frame_interval_s: float = 0.5):
    """构造 torch-free 的 ``PerceptionPipeline``（仅用于 validation 端到端测试）。"""
    tracker = VisitorTracker(now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="scenario", now_provider=clock)
    feature_extractor = FeatureExtractor(frequency_window_s=60.0)
    rule_engine = RuleEngine(device_id="home_entry_01", location="入户门", now_provider=clock)
    decision_engine = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher, publisher, notifier, max_retries=1)
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feature_extractor,
        rule_engine=rule_engine,
        decision_engine=decision_engine,
        executor=executor,
        now_provider=clock,
        frame_interval_s=frame_interval_s,
    )


# ============================================================================
# T6 可机器校验的期望（validate 自动比对 expects + 差异报告）
# ============================================================================


def test_adr0032_t6_validates_against_expects():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    synth = ScenarioCompiler().compile(scn, mode="detections")

    # odd hour 起点 → OddHourRule 触发 visit_normal(+is_odd_hour) → LOW 警告
    clock = SimpleClock(datetime(2026, 8, 8, 3, 0, 0, tzinfo=UTC), interval_s=0.5)
    pipeline = build_torchfree_pipeline(synth.detector, clock, frame_interval_s=0.5)

    run_result = ScenarioRunner().run(synth, pipeline, frame_interval_s=0.5)
    assert "visit_normal" in run_result.event_types
    assert run_result.risk_levels == ["LOW"]

    result = ScenarioValidator().validate(run_result, scn)
    assert result.ok is True
    assert result.missing_event_types == set()
    assert result.risk_level_ok is True
    assert "visit_normal" in result.observed_event_types

    # 负例：期望一个未发生的事件类型 → 校验失败（给出差异报告）
    scn.expects.emitted_event_types = ["abnormal_dwell"]
    neg = ScenarioValidator().validate(run_result, scn)
    assert neg.ok is False
    assert "abnormal_dwell" in neg.missing_event_types
    assert "missing_event_types" in neg.details


# ============================================================================
# T9 三组件职责分离（编排边界）
# ============================================================================


def test_adr0032_t9_components_single_responsibility():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    compiler = ScenarioCompiler()
    runner = ScenarioRunner()
    validator = ScenarioValidator()

    # 各自单一职责 + 返回类型分明
    synth = compiler.compile(scn, mode="detections")
    assert hasattr(compiler, "compile")
    assert isinstance(synth, object)

    clock = SimpleClock(datetime(2026, 8, 8, 3, 0, 0, tzinfo=UTC), interval_s=0.5)
    pipeline = build_torchfree_pipeline(synth.detector, clock, frame_interval_s=0.5)
    run_result = runner.run(synth, pipeline, frame_interval_s=0.5)
    assert isinstance(run_result, RunResult)

    result = validator.validate(run_result, scn)
    assert isinstance(result, ValidationResult)

    # Runner 不内嵌校验逻辑（职责分离，防膨胀为 God Object，归 ADR-0033 聚合）
    assert "validate" not in ScenarioRunner.__dict__
    assert "run" not in ScenarioValidator.__dict__
    assert "compile" not in ScenarioRunner.__dict__
    assert "compile" not in ScenarioValidator.__dict__

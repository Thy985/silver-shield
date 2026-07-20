"""P0-10 装配联调测试（runtime 包）。

> 验证"已验证组件被正确地装配成可运行 Demo"，**不**再验证逻辑正确性
> （逻辑由 tests/test_integration.py 的 6 Golden Scenarios + 状态机 + 故障注入覆盖）。

覆盖：
- 配置扩展：Settings 含 rule/decision/action/runtime；to_threshold_config 映射正确
- 装配：from_settings 产出类型正确、detector 懒加载（构造期不触发 torch）
- 编排：process_frame 经 7 层、VisitorEvent 生成、metrics 累加
- 优雅停止：run() 捕获 KeyboardInterrupt，返回已处理部分的汇总
- CAVIAR 端到端：真实 YOLO + 三个场景从 frame → ActionCommand（缺依赖/缺 fixture 时 skip）
- run_demo：一键复现主流程（3 个场景汇总）
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
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.analysis.warning import WarningEvent
from home_perception.core.config import Settings
from home_perception.detection.detector import Detection, DetectionResult, YOLODetector
from home_perception.detection.tracker import VisitorTracker
from home_perception.runtime import (
    FrameResult,
    PerceptionPipeline,
    PipelineMetrics,
    RunSummary,
    build_threshold_config,
    read_caviar_frames,
    run_demo,
)
from home_perception.runtime.config import build_dispatcher_config

FORBIDDEN_WARNING_FIELDS = (
    "fraud_result", "fraud_probability", "is_fraud", "is_scammer", "is_criminal",
    "verdict", "final_decision", "crime_probability", "guilt_score",
    "arrest_probability", "deception_score",
)


# ============================================================================
# 测试 fixtures / 辅助
# ============================================================================

class ManualClock:
    """可控时钟：now() 返回当前时间，advance() 推进；供 tracker 离场判定用。"""

    def __init__(self, base: Optional[datetime] = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        """使 ManualClock 可作为 now_provider() 直接调用（与组件约定 / NowProvider 协议一致）。"""
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class StubDetector:
    """测试用 detector：按 plan 返回 Detection 列表（支持空列表触发离场）。

    可选 ``clock``：每次 detect 自动推进 1s，使 tracker 离场判定在 run() 内部循环也能生效。
    """

    def __init__(self, plan: List[List[Detection]], clock: Optional[ManualClock] = None):
        self.plan = plan
        self.clock = clock
        self.i = 0
        self.detect_calls = 0

    def detect(self, frame) -> DetectionResult:
        if self.clock is not None:
            self.clock.advance(1.0)
        self.detect_calls += 1
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets, timestamp=0.0, inference_ms=0.0,
            source_size=(1, 1), inference_size=(1, 1), model="stub",
        )


class InterruptingDetector:
    """第 nth 帧抛出 KeyboardInterrupt（验证优雅停止）。"""

    def __init__(self, fail_at: int, plan: List[List[Detection]]):
        self.fail_at = fail_at
        self.plan = plan
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        if self.i == self.fail_at:
            raise KeyboardInterrupt()
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets, timestamp=0.0, inference_ms=0.0,
            source_size=(1, 1), inference_size=(1, 1), model="stub",
        )


def _person_detections(track_id: int = 1, n: int = 1) -> List[Detection]:
    return [
        Detection(
            class_id=0, class_name="person", confidence=0.9,
            bbox=[0, 0, 10, 10], timestamp=0.0, track_id=track_id,
        )
        for _ in range(n)
    ]


def _build_pipeline(detector, now_provider=None, family_contact=None, max_retries=3):
    """用确定性组件构造 PerceptionPipeline（不依赖真实 YOLO）。"""
    clock = now_provider
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock.now if clock else None)
    builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock.now if clock else None)
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test", location="入户门",
        thresholds=ThresholdConfig(), now_provider=clock.now if clock else None,
    )
    decision = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(),
        now_provider=clock.now if clock else None,
    )
    dispatcher = ActionDispatcher(DispatcherConfig(family_contact=family_contact))
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher=dispatcher, publisher=publisher, notifier=notifier, max_retries=max_retries)
    return PerceptionPipeline(
        detector=detector, tracker=tracker, event_builder=builder,
        feature_extractor=feat, rule_engine=rule_engine, decision_engine=decision, executor=executor,
    )


# ============================================================================
# 1. 配置扩展
# ============================================================================

class TestConfigExtension:
    def test_settings_has_runtime_sections(self):
        s = Settings()
        assert isinstance(s.rule, type(s.rule))
        assert s.runtime.mode == "demo"
        assert s.decision.policy == "rule_based"
        assert s.action.mqtt_topic_prefix == "silvershield/home"

    def test_settings_load_reads_yaml(self):
        s = Settings.load()
        assert s.rule.long_duration_seconds == 1.5   # Demo 调优（生产用 300s）
        assert s.rule.repeat_visit_count == 3
        assert s.runtime.demo_scenarios == [
            "one_stop_enter", "one_leave_reenter", "meet_walk_together",
        ]
        assert s.detection.tracking.absence_gap_s == 5.0

    def test_rule_config_to_threshold_config(self):
        s = Settings.load()
        th = build_threshold_config(s.rule)
        assert isinstance(th, ThresholdConfig)
        assert th.long_duration_seconds == 1.5   # Demo 调优（生产用 300s）
        assert th.repeat_visit_count == 3
        assert th.odd_hour_set == {23, 0, 1, 2, 3, 4}
        assert th.rule_weights["HighRiskApproachRule"] == 0.90

    def test_action_config_to_dispatcher_config(self):
        from home_perception.action.notifier import FamilyContact
        from home_perception.core.config import FamilyContactConfig
        s = Settings.load()
        s.action.family_contact = FamilyContactConfig(
            elder_id="elder_001", name="张子女", phone="+8613800001111", relation="子女"
        )
        dc = build_dispatcher_config(s.action)
        assert isinstance(dc, DispatcherConfig)
        assert isinstance(dc.family_contact, FamilyContact)
        assert dc.community_endpoint is not None


# ============================================================================
# 2. 装配：from_settings
# ============================================================================

class TestAssembly:
    def test_from_settings_builds_wired_pipeline(self):
        s = Settings.load()
        s.action.mock_publisher_output = None  # 测试不落盘
        p = PerceptionPipeline.from_settings(s, device_id="CAVIAR/test")
        assert isinstance(p.detector, YOLODetector)
        assert isinstance(p.tracker, VisitorTracker)
        assert isinstance(p.event_builder, VisitorEventBuilder)
        assert isinstance(p.feature_extractor, FeatureExtractor)
        assert isinstance(p.rule_engine, RuleEngine)
        assert isinstance(p.decision_engine, DecisionEngine)
        assert isinstance(p.executor, ActionExecutor)
        # device_id 透传到 event_builder.source_video（PerceptionEvent.source_video）
        assert p.event_builder.source_video == "CAVIAR/test"

    def test_detector_not_loaded_at_construction(self):
        s = Settings.load()
        s.action.mock_publisher_output = None
        p = PerceptionPipeline.from_settings(s)
        # 构造期不加载 YOLO 权重（懒加载，避免无 GPU 环境导入 torch）
        assert p.detector.is_loaded is False

    def test_default_metrics_instance(self):
        s = Settings.load()
        s.action.mock_publisher_output = None
        p = PerceptionPipeline.from_settings(s)
        assert isinstance(p.metrics, PipelineMetrics)


# ============================================================================
# 3. 编排：process_frame / run
# ============================================================================

class TestOrchestration:
    def test_process_frame_generates_visitor_event(self):
        clock = ManualClock()
        # 10 帧有人（track_id=1），6 帧无人 → 第 15 帧离场（absence 5s）
        plan = [_person_detections(1) for _ in range(10)] + [[] for _ in range(6)]
        det = StubDetector(plan, clock=clock)
        p = _build_pipeline(det, now_provider=clock)

        for i in range(len(plan)):
            p.process_frame(None, frame_index=i)

        # 关键断言：track 离场 → 1 个 VisitorEvent
        assert p.metrics.visitor_events == 1, "有人→无人应生成 1 个 VisitorEvent"
        assert p.metrics.frames_processed == len(plan)
        assert p.metrics.detections_total == 10  # 仅有人帧有检测
        # 短停留（~10s）+ 白天（10:00）→ 不触发 Rule/Decision/Action
        assert p.metrics.warnings == 0
        assert p.executor.publisher.publish_count == 0

    def test_run_returns_summary_and_accumulates(self):
        clock = ManualClock()
        plan = [_person_detections(1) for _ in range(8)] + [[] for _ in range(6)]
        det = StubDetector(plan, clock=clock)
        p = _build_pipeline(det, now_provider=clock)
        frames = [None] * len(plan)
        summary = p.run(frames, scenario="demo/test")
        assert isinstance(summary, RunSummary)
        assert summary.scenario == "demo/test"
        assert summary.frames_processed == len(plan)
        assert summary.n_visitor_events == 1
        assert summary.n_warnings == 0

    def test_run_handles_keyboard_interrupt_gracefully(self):
        plan = [_person_detections(1) for _ in range(5)] + [[] for _ in range(15)]
        det = InterruptingDetector(fail_at=5, plan=plan)
        p = _build_pipeline(det)
        # 第 6 次 detect（i=5）抛 KeyboardInterrupt → run() 捕获并停止
        # 注意：process_frame 先 +1 frames_processed 再 detect，故中断帧被计入（=6）
        summary = p.run([None] * len(plan), scenario="demo/interrupt")
        assert summary.interrupted is True
        assert summary.frames_processed == 6, "中断帧被计入，循环随后停止"
        assert p.metrics.errors == 0, "KeyboardInterrupt 不是业务错误"

    def test_process_frame_isolates_detector_failure(self):
        class FailingDetector:
            def detect(self, frame):
                raise RuntimeError("camera disconnected")

        p = _build_pipeline(FailingDetector())
        # 检测器异常被捕获，计入 errors，不崩溃流水线
        fr = p.process_frame(None, frame_index=0)
        assert isinstance(fr, FrameResult)
        assert fr.n_detections == 0
        assert p.metrics.errors == 1
        assert p.metrics.frames_processed == 1


# ============================================================================
# 4. CAVIAR 端到端（真实 YOLO + fixtures）
# ============================================================================

@pytest.mark.parametrize("scenario", [
    "one_stop_enter", "one_leave_reenter", "meet_walk_together",
])
def test_caviar_end_to_end_via_runtime(scenario):
    pytest.importorskip("ultralytics")
    settings = Settings.load()
    settings.action.mock_publisher_output = None  # 测试不落盘
    frames = read_caviar_frames(settings.runtime.caviar_base_dir, scenario, settings.runtime.frame_glob)
    if not frames:
        pytest.skip(f"CAVIAR fixture 缺失: {scenario}")

    # 复用 detector（跨场景同一实例保证 track_id 一致），每场景独立流水线状态
    shared_detector = PerceptionPipeline.from_settings(settings, device_id=scenario).detector
    p = PerceptionPipeline.from_settings(settings, detector=shared_detector, device_id=scenario)
    p.load_detector()
    summary = p.run(frames, scenario=scenario)

    # 1) 无处理异常
    assert summary.errors == 0, f"{scenario}: 处理过程不应抛异常"

    # 2) WarningEvent 无业务判定字段（黑名单）
    warnings = list(p.executor._warnings_by_id.values())
    for w in warnings:
        assert isinstance(w, WarningEvent)
        d = w.to_dict()
        for f in FORBIDDEN_WARNING_FIELDS:
            assert f not in d, f"WarningEvent 含禁止字段 {f}（{scenario}）"
            assert f not in (w.meta or {}), f"meta 含禁止字段 {f}（{scenario}）"

    # 3) 发送次数与 DONE commands 一致（LOG_ONLY 不发送）
    commands = list(p.executor._command_index.values())
    pub = p.executor.publisher.publish_count
    notif = p.executor.notifier.family_count + p.executor.notifier.community_count
    done = sum(1 for c in commands if c.status == "DONE")
    log_only = sum(1 for c in commands if c.command_type == "LOG_ONLY")
    assert pub + notif == done - log_only, (
        f"{scenario}: 发送 {pub + notif} 次 ≠ DONE命令 {done} - LOG_ONLY {log_only}"
    )

    # 4) 无孤儿 command（DONE command 的 warning 来自实际产出）
    produced_ids = {w.warning_id for w in warnings}
    for c in commands:
        if c.status == "DONE":
            assert c.warning_id in produced_ids, "DONE command 引用了未产出的 warning_id"


# ============================================================================
# 5. run_demo 一键复现
# ============================================================================

def test_run_demo_end_to_end():
    pytest.importorskip("ultralytics")
    settings = Settings.load()
    settings.action.mock_publisher_output = None  # 测试不落盘
    frames = read_caviar_frames(
        settings.runtime.caviar_base_dir, settings.runtime.demo_scenarios[0], settings.runtime.frame_glob
    )
    if not frames:
        pytest.skip("CAVIAR fixture 缺失，跳过 run_demo 集成")

    summaries = run_demo(settings)
    assert isinstance(summaries, list)
    assert len(summaries) >= 1, "至少跑通一个场景"
    assert all(isinstance(s, RunSummary) for s in summaries)
    # 每个跑通的场景都应处理了帧
    assert all(s.frames_processed > 0 for s in summaries)

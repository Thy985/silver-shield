"""ADR-0042 运行时接线：实时音频证据旁路 + Stage D 统一决策（Pre-flight R1/R2/R3 补链）。

验收面：
- **装配门控**：``audio_evidence.enabled=false`` 不构造 evaluator（零运行时开销）；
  ``true`` 构造且 ``device_id`` 与 RuleEngine 同源（WarningEvent.device_id 口径一致）；
- **安全默认**：ceiling MONITOR 下 ``ctx.audio_events`` 判级恒 MONITOR →
  ``risk_signals`` 零产出（接线后默认配置零行为变化）；
- **信号通道**：解除 ceiling 后 AUDIO RAISED/CLEARED 与视觉信号同通道进
  ``FrameResult.risk_signals``；
- **Stage D 统一入口**（自视觉评估帧块移出）：decision_enabled 下纯音频帧的信号以
  原生形态到达 policy（``DecisionInput.risk_signals``）；AUDIO 不经视觉翻译
  （硬门控 3：防幻觉兜底 visit_pending_verify）；纯信号无视觉触发零升级动作
  （policy 升级消费前的灰度语义）;
- **adapt_runtime_audio**：dict ↔ 实例 ↔ 非法输入三态（Host 层冻结边界接缝）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.decision_contract import DecisionInput
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.realtime_audio_risk_evaluator import RealTimeAudioRiskEvaluator
from home_perception.analysis.risk_signal import RiskSignal, SignalTransition
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
from home_perception.core.config import AudioEvidenceConfig, Settings
from home_perception.detection.detector import DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.runtime import FrameResult, PerceptionPipeline, RuntimeFrameContext

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


# ============================================================================
# 测试辅助（模式复用 test_pipeline_realtime_bypass.py，保持自包含）
# ============================================================================


class ManualClock:
    def __init__(self, base: datetime | None = None):
        self._t = base or NOW

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class EmptyDetector:
    """恒返回空检测（音频旁路不依赖视觉事实）。"""

    def detect(self, frame) -> DetectionResult:
        return DetectionResult(
            detections=[],
            timestamp=0.0,
            inference_ms=0.0,
            source_size=(1, 1),
            inference_size=(1, 1),
            model="stub",
        )


class CapturingPolicy:
    """捕获 DecisionInput 的最小 policy stub（不产 Warning，只记录输入）。"""

    def __init__(self) -> None:
        self.inputs: list[DecisionInput] = []

    def decide(self, input: DecisionInput) -> Any:
        self.inputs.append(input)
        return None

    def bind_trace_span(self, span: Any) -> None:
        self.span = span


def make_audio_event(
    *,
    event_id: str = "a1",
    kind: AudioPerceptionKind = AudioPerceptionKind.AUDIO_VOICE_RAISED,
    score: float = 0.9,
    confidence: float = 0.9,
) -> AudioPerceptionEvent:
    return AudioPerceptionEvent(
        event_id=event_id,
        timestamp=0.0,
        kind=kind,
        score=score,
        confidence=confidence,
        source_segment_ids=["seg-1"],
    )


def raised_config(**over) -> AudioEvidenceConfig:
    """解除 MONITOR ceiling 且单事件即达 RAISE 的测试配置。"""
    base = {"ceiling_monitor_only": False, "raise_min_count": 1}
    base.update(over)
    return AudioEvidenceConfig(**base)


def _build_pipeline(
    clock: ManualClock,
    *,
    audio_evaluator: RealTimeAudioRiskEvaluator | None = None,
    decision_enabled: bool = False,
    decision_engine: DecisionEngine | None = None,
) -> PerceptionPipeline:
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    engine = decision_engine or DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(),
        now_provider=clock,
    )
    executor = ActionExecutor(
        dispatcher=ActionDispatcher(DispatcherConfig()),
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=3,
    )
    return PerceptionPipeline(
        detector=EmptyDetector(),
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=FeatureExtractor(frequency_window_s=1800.0),
        rule_engine=RuleEngine(
            device_id="demo/test",
            location="入户门",
            thresholds=ThresholdConfig(),
            now_provider=clock,
        ),
        decision_engine=engine,
        executor=executor,
        now_provider=clock,
        realtime_enabled=False,
        decision_enabled=decision_enabled,
        audio_evaluator=audio_evaluator,
    )


def _ctx(case_time: float, events: tuple = ()) -> RuntimeFrameContext:
    return RuntimeFrameContext(
        video_frame=None, frame_index=int(case_time), case_time=case_time, audio_events=events
    )


# ============================================================================
# 装配门控（from_settings）
# ============================================================================


class TestFromSettingsAssembly:
    def test_flag_off_no_evaluator_constructed(self):
        """enabled=false（默认）：不构造 evaluator（零运行时开销）。"""
        p = PerceptionPipeline.from_settings(Settings(), detector=EmptyDetector())
        assert p._audio_evaluator is None

    def test_flag_on_evaluator_built_with_same_device_id(self):
        """enabled=true：构造 evaluator 且 device_id 与 RuleEngine 同源。"""
        s = Settings()
        s.audio_evidence.enabled = True
        p = PerceptionPipeline.from_settings(s, detector=EmptyDetector())
        assert p._audio_evaluator is not None
        assert p._audio_evaluator._device_id == p.rule_engine.device_id


# ============================================================================
# 安全默认：ceiling MONITOR 下零产出
# ============================================================================


class TestCeilingSafeDefault:
    def test_ceiling_default_zero_signal_yield(self):
        """默认配置（ceiling 开）：高分同 kind 事件逐帧进给 → risk_signals 恒空。"""
        clock = ManualClock()
        cfg = AudioEvidenceConfig()  # enabled 由装配侧表达；ceiling 默认 True
        p = _build_pipeline(clock, audio_evaluator=RealTimeAudioRiskEvaluator(
            device_id="demo/test", config=cfg
        ))
        for i in range(3):
            r = p.process_frame(_ctx(float(i), (make_audio_event(event_id=f"a{i}"),)))
            assert r.risk_signals == []
            assert r.warnings == []


# ============================================================================
# 信号通道：RAISED / CLEARED 与视觉信号同通道
# ============================================================================


class TestAudioSignalChannel:
    def test_raised_flows_to_frame_result(self):
        clock = ManualClock()
        p = _build_pipeline(
            clock, audio_evaluator=RealTimeAudioRiskEvaluator(
                device_id="demo/test", config=raised_config()
            )
        )
        r0 = p.process_frame(_ctx(0.0, (make_audio_event(),)))
        raised = [s for s in r0.risk_signals if s.transition is SignalTransition.RAISED]
        assert len(raised) == 1
        assert raised[0].source.value == "audio"

    def test_cleared_paired_after_silence_timeout(self):
        clock = ManualClock()
        p = _build_pipeline(
            clock,
            audio_evaluator=RealTimeAudioRiskEvaluator(
                device_id="demo/test",
                config=raised_config(clear_timeout_s=5.0),
            ),
        )
        r0 = p.process_frame(_ctx(0.0, (make_audio_event(),)))
        raised = next(s for s in r0.risk_signals if s.transition is SignalTransition.RAISED)
        # 静默期（≤ timeout）不出 CLEARED
        for t in (2.0, 4.0):
            r = p.process_frame(_ctx(t))
            assert all(s.transition is not SignalTransition.CLEARED for s in r.risk_signals)
        # 超时后 tick 扫描出成对 CLEARED
        r6 = p.process_frame(_ctx(6.0))
        cleared = [s for s in r6.risk_signals if s.transition is SignalTransition.CLEARED]
        assert len(cleared) == 1
        assert cleared[0].paired_signal_id == raised.signal_id


# ============================================================================
# Stage D 统一入口：原生形态进 policy、无幻觉翻译、零升级动作
# ============================================================================


class TestStageDUnification:
    def test_audio_raised_reaches_policy_natively_without_warning(self):
        """decision_enabled + 纯音频 RAISED：
        - policy 经 DecisionInput.risk_signals 以原生形态收到信号（R3 链路贯通）；
        - trigger_events 为空（AUDIO 不走视觉翻译，硬门控 3）；
        - FrameResult.warnings / perception_events 为空（纯信号零升级动作）。"""
        clock = ManualClock()
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=clock)
        p = _build_pipeline(
            clock,
            audio_evaluator=RealTimeAudioRiskEvaluator(
                device_id="demo/test", config=raised_config()
            ),
            decision_enabled=True,
            decision_engine=engine,
        )
        r0 = p.process_frame(_ctx(0.0, (make_audio_event(),)))
        assert len(policy.inputs) == 1
        sigs = policy.inputs[0].risk_signals
        assert len(sigs) == 1 and sigs[0].source.value == "audio"
        assert policy.inputs[0].trigger_events == ()
        assert r0.warnings == []
        assert r0.perception_events == []

    def test_act_on_signals_carries_full_signal_set(self):
        """_act_on_signals 全量透传（含未翻译的 AUDIO RAISED 与 CLEARED）。"""
        clock = ManualClock()
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=clock)
        p = _build_pipeline(clock, decision_enabled=True, decision_engine=engine)
        raised = RiskSignal(
            signal_id=str(uuid.uuid4()),
            subject_type="visitor",
            subject_id="vi-1",
            category="communication",
            source="audio",
            transition="raised",
            features={"audio_score": 0.8},
            track_id=None,
            visitor_instance_id=None,
            created_at=NOW,
        )
        cleared = RiskSignal(
            signal_id=str(uuid.uuid4()),
            subject_type="visitor",
            subject_id="vi-1",
            category="communication",
            source="audio",
            transition="cleared",
            features={},
            track_id=None,
            visitor_instance_id=None,
            created_at=NOW,
            paired_signal_id=raised.signal_id,
        )
        percs, warnings, _cmds = p._act_on_signals([raised, cleared], NOW)
        assert percs == [] and warnings == []
        assert len(policy.inputs[-1].risk_signals) == 2


# ============================================================================
# adapt_runtime_audio：Host 层接缝三态
# ============================================================================


class TestAdaptRuntimeAudio:
    def setup_method(self):
        self.pipeline = _build_pipeline(ManualClock())

    def test_dict_roundtrip(self):
        ev = make_audio_event()
        adapted = self.pipeline.adapt_runtime_audio(ev.to_dict())
        assert isinstance(adapted, AudioPerceptionEvent)
        assert adapted.event_id == ev.event_id
        assert adapted.kind is ev.kind
        assert adapted.score == ev.score

    def test_instance_passthrough(self):
        ev = make_audio_event()
        assert self.pipeline.adapt_runtime_audio(ev) is ev

    def test_invalid_dict_returns_none(self):
        assert self.pipeline.adapt_runtime_audio({"kind": "not_a_kind"}) is None

    def test_wrong_type_returns_none(self):
        assert self.pipeline.adapt_runtime_audio("not-a-dict") is None


# ============================================================================
# FrameResult 形状回归：音频块关闭时不新增字段内容
# ============================================================================


class TestNoEvaluatorRegression:
    def test_no_evaluator_frame_result_unchanged(self):
        """未装配 evaluator：ctx 带音频事件也不产生任何信号（零行为变化基线）。"""
        clock = ManualClock()
        p = _build_pipeline(clock)  # audio_evaluator=None
        r = p.process_frame(_ctx(0.0, (make_audio_event(),)))
        assert isinstance(r, FrameResult)
        assert r.risk_signals == []
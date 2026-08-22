"""Integration Gate：Audio → Runtime → Risk → Decision → Action 后端业务链贯通。

分层依据（Owner 2026-08-22 测试策略）：
- **Unit 层已由既有文件锁死**：evaluator 五档判级矩阵与 ESCALATE 双门控
  （``test_realtime_audio_risk_evaluator.py``）、temporal linker 配对/窗口
  （``test_signal_temporal_linker.py``）、ceiling/fallback 封顶
  （``test_evidence_strength.py``）；
- 本文件补两类缺口：
  1. ``adapt_audio_event`` features 键级断言（``audio_confidence`` 此前未被锁）；
  2. **数值化端到端 Gate**：telephone_risk（score=0.82 / confidence=0.91）从
     ``AudioPerceptionEvent`` → ``RiskSignal(source=AUDIO)`` →
     ``DecisionInput.risk_signals`` → ``WarningEvent`` → ``ActionCommand`` →
     ``FrameResult`` 的全链贯通，以及 **Gateway → RuntimeFrameContext →
     process_frame** 接缝 Gate——防"代码存在 / 单测通过 / Projection 有数据，
     但 Gateway 没把音频送进 Runtime"类回归（Browser E2E 的前置有效性条件）。

灰度语义同步锁死：纯音频 RAISE 零升级动作（policy 升级消费 risk_signals 参与
判定前，Warning 只能由视觉触发路径产出）；AUDIO 不产生任何视觉 event_type 翻译
（硬门控 3）。
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
from home_perception.analysis.risk_signal import SignalTransition
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
from home_perception.core.config import AudioEvidenceConfig, Settings
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.integration.audio_adapter import adapt_audio_event
from home_perception.runtime import PerceptionPipeline, RuntimeFrameContext
from silver_demo.gateway import DemoGateway
from silver_demo.scenarios import ScenarioConfig

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

# Owner 指定的 telephone_risk 验收数值
TEL_SCORE = 0.82
TEL_CONFIDENCE = 0.91


# ============================================================================
# 公共辅助（自包含；构造模式复用 test_pipeline_realtime_bypass.py）
# ============================================================================


class ManualClock:
    def __init__(self, base: datetime | None = None):
        self._t = base or NOW

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def advance(self, seconds: float = 1.0) -> None:
        self._t += timedelta(seconds=seconds)


class StubDetector:
    """按 plan 返回 person Detection；每次 detect 推进时钟 1s（与 bypass 测试同款）。"""

    def __init__(self, plan: list[list[Detection]], clock: ManualClock):
        self.plan = plan
        self.clock = clock
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        self.clock.advance(1.0)
        dets = self.plan[min(self.i, len(self.plan) - 1)]
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


class EmptyDetector:
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


def make_tel_event(event_id: str) -> AudioPerceptionEvent:
    """Owner 验收数值的 telephone_persistent 事件。"""
    return AudioPerceptionEvent(
        event_id=event_id,
        timestamp=0.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=TEL_SCORE,
        confidence=TEL_CONFIDENCE,
        source_segment_ids=["seg-1"],
    )


def raised_config(**over) -> AudioEvidenceConfig:
    base = {"ceiling_monitor_only": False, "raise_min_count": 2}
    base.update(over)
    return AudioEvidenceConfig(**base)


def _build_pipeline(
    detector,
    clock: ManualClock,
    *,
    realtime_enabled: bool = False,
    thresholds: ThresholdConfig | None = None,
    decision_enabled: bool = False,
    decision_engine: DecisionEngine | None = None,
    audio_evaluator: RealTimeAudioRiskEvaluator | None = None,
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
    behavior_builder = recent_store = realtime_evaluator = None
    if realtime_enabled:
        from home_perception.analysis.behavior_builder import BehaviorBuilder
        from home_perception.analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
        from home_perception.analysis.recent_behavior_store import RecentBehaviorStore

        th = thresholds or ThresholdConfig()
        behavior_builder = BehaviorBuilder(event_builder=event_builder)
        recent_store = RecentBehaviorStore()
        realtime_evaluator = RealTimeRiskEvaluator(thresholds=th, now_provider=clock)
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=FeatureExtractor(frequency_window_s=1800.0),
        rule_engine=RuleEngine(
            device_id="demo/test",
            location="入户门",
            thresholds=thresholds or ThresholdConfig(),
            now_provider=clock,
        ),
        decision_engine=engine,
        executor=executor,
        now_provider=clock,
        behavior_builder=behavior_builder,
        recent_behavior_store=recent_store,
        realtime_evaluator=realtime_evaluator,
        realtime_enabled=realtime_enabled,
        decision_enabled=decision_enabled,
        audio_evaluator=audio_evaluator,
    )


# ============================================================================
# Unit 补缺：adapter features 三元组键级锁死
# ============================================================================


class TestAdapterFeatureKeys:
    def test_telephone_risk_feature_triple_locked(self):
        """AUDIO_TELEPHONE_PERSISTENT → RiskSignal(AUDIO/COMMUNICATION/RAISED)，
        features 携带 audio_kind / audio_score / audio_confidence 完整三元组。"""
        ev = make_tel_event("u1")
        sig = adapt_audio_event(ev, device_id="dev-1", subject_id=str(uuid.uuid4()))
        assert sig.source.value == "audio"
        assert sig.category.value == "communication"
        assert sig.transition.value == "raised"
        assert sig.features["audio_kind"] == "audio_telephone_persistent"
        assert sig.features["audio_score"] == TEL_SCORE
        assert sig.features["audio_confidence"] == TEL_CONFIDENCE


# ============================================================================
# I1：纯音频 telephone_risk 全链（灰度语义锁死）
# ============================================================================


class TestPureAudioEndToEnd:
    def test_telephone_raise_flows_to_decision_input_and_frame_result(self):
        """score=0.82/conf=0.91 × 2 次（持续性 ≥N=2）：
        帧0 单事件 MONITOR（无信号）→ 帧1 RAISE →
        RiskSignal(source=AUDIO) 进 FrameResult.risk_signals 且以原生形态进
        DecisionInput.risk_signals；零升级动作（warnings/commands 空）。"""
        clock = ManualClock()
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=clock)
        p = _build_pipeline(
            EmptyDetector(),
            clock,
            audio_evaluator=RealTimeAudioRiskEvaluator(
                device_id="demo/test", config=raised_config()
            ),
            decision_enabled=True,
            decision_engine=engine,
        )
        results = []
        for i in range(2):
            ctx = RuntimeFrameContext(
                video_frame=None,
                frame_index=i,
                case_time=float(i),
                audio_events=(make_tel_event(f"tel-{i}"),),
            )
            results.append(p.process_frame(ctx))

        # 帧0：单事件永不升级（Evidence Continuity > Event Count）
        assert results[0].risk_signals == []

        # 帧1：RAISE 信号贯通两条通道
        raised = [
            s for s in results[1].risk_signals if s.transition is SignalTransition.RAISED
        ]
        assert len(raised) == 1
        sig = raised[0]
        assert sig.source.value == "audio"
        assert sig.features["audio_kind"] == "audio_telephone_persistent"

        # 一等输入通道：原生形态到达 policy（R3 链路贯通证据）
        last_input = policy.inputs[-1]
        assert any(
            s.signal_id == sig.signal_id for s in last_input.risk_signals
        )
        assert last_input.trigger_events == ()  # AUDIO 不走视觉翻译（硬门控 3）

        # 灰度语义：纯信号零升级动作
        assert results[1].warnings == []
        assert results[1].commands == []


# ============================================================================
# I2：跨模态同帧汇聚 → WarningEvent → ActionCommand（Runtime→Risk→Decision→Action）
# ============================================================================


class TestCrossModalConvergence:
    def test_vision_dwell_plus_audio_raise_same_frame_produces_warning_chain(self):
        """帧2 同时发生：视觉 dwell RAISED（realtime evaluator）+ 音频第 2 次
        telephone 达标 RAISE → 同一 DecisionInput 收到双模态信号 →
        WarningEvent（meta sources 含 vision+audio）→ ActionCommand →
        FrameResult.commands。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        p = _build_pipeline(
            StubDetector([_person(1)] * 3, clock),
            clock,
            realtime_enabled=True,
            thresholds=th,
            decision_enabled=True,
            audio_evaluator=RealTimeAudioRiskEvaluator(
                device_id="demo/test", config=raised_config()
            ),
        )
        # 时序对齐：音频事件在帧 1/2 投递 → 帧 2 第 2 个事件达 RAISE，
        # 与视觉 dwell（帧 2，dwell=2s ≥ 1.5s）同帧汇聚
        results = []
        for i in range(3):
            audio = (make_tel_event(f"tel-{i}"),) if i >= 1 else ()
            ctx = RuntimeFrameContext(
                video_frame=None,
                frame_index=i,
                case_time=float(i),
                audio_events=audio,
            )
            results.append(p.process_frame(ctx))

        r2 = results[2]
        raised = [s for s in r2.risk_signals if s.transition is SignalTransition.RAISED]
        assert {s.source.value for s in raised} == {"vision", "audio"}, (
            f"帧 2 应同时有视觉+音频 RAISED，实际 {[(s.source.value, s.transition.value) for s in raised]}"
        )

        # 决策：视觉触发路径产 Warning，meta 摘要可见双模态信号集
        assert len(r2.warnings) >= 1
        summary = r2.warnings[-1].meta["risk_signals"]
        assert summary["count"] == 2
        assert set(summary["sources"]) == {"vision", "audio"}
        assert len(summary["signal_ids"]) == 2

        # 行动：Warning 经 executor 产出 ActionCommand 并汇入 FrameResult
        assert len(r2.commands) >= 1

        # 翻译面：perception_events 仅含视觉翻译产物（无 audio 幻觉 event_type）
        event_types = {pe.event_type for pe in r2.perception_events}
        assert event_types == {"abnormal_dwell"}

        # meta 摘要由 policy 写入 → 本身即"policy 收到完整双模态信号集"的证据
        # （signal_ids 数量与 sources 覆盖在上方已断言）。


# ============================================================================
# I3：Gateway → RuntimeFrameContext → process_frame 接缝 Gate
# ============================================================================


class TestGatewayToRuntimeGate:
    def test_gateway_audio_injection_reaches_runtime_and_back(self):
        """Gateway 注入 dict → _runtime_audio_events → ctx.audio_events →
        process_frame → RiskSignal 回到 FrameResult。
        直指历史漏洞形态："组件都在，但 Gateway 没把 Audio 送进 Risk Runtime"。"""
        s = Settings()
        s.audio_evidence.enabled = True
        s.audio_evidence.ceiling_monitor_only = False
        s.audio_evidence.raise_min_count = 2
        pipeline = PerceptionPipeline.from_settings(s, detector=EmptyDetector())

        gw = DemoGateway.create_for_test()
        gw.scenario = ScenarioConfig(
            scenario_id="sess-intg-gate",
            source="s",
            start_time=datetime.now(UTC),
        )
        gw.pipeline = pipeline
        gw.set_live_audio_events([
            {
                "event_id": "gw-tel-0",
                "timestamp": 1700000000.0,
                "kind": "audio_telephone_persistent",
                "score": TEL_SCORE,
                "confidence": TEL_CONFIDENCE,
                "source_segment_ids": ["seg-1"],
                "labels": ["speech", "telephone"],
            },
            {
                "event_id": "gw-tel-1",
                "timestamp": 1700000001.0,
                "kind": "audio_telephone_persistent",
                "score": TEL_SCORE,
                "confidence": TEL_CONFIDENCE,
                "source_segment_ids": ["seg-2"],
                "labels": ["speech", "telephone"],
            },
        ])

        # 模拟 run_loop 核心段（ctx 组装方式与 run_loop 逐字段一致）
        results = []
        for k in range(2):
            interval = getattr(gw.scenario, "frame_interval_s", 0.0) or 0.0
            ctx = RuntimeFrameContext(
                video_frame=None,
                frame_index=k,
                case_time=round(k * interval, 3),
                audio_events=gw._runtime_audio_events(k),
            )
            results.append(pipeline.process_frame(ctx))

        raised = [
            sig
            for sig in results[1].risk_signals
            if sig.transition is SignalTransition.RAISED
        ]
        assert len(raised) == 1
        assert raised[0].source.value == "audio"
        assert raised[0].features["audio_kind"] == "audio_telephone_persistent"
        assert raised[0].features["audio_confidence"] == TEL_CONFIDENCE
        # Gateway 灰度配置（decision_enabled 默认 False）：零升级动作
        assert results[1].warnings == []

    def test_gateway_without_audio_injection_yields_zero_signals(self):
        """反向基线：未注入音频时同一接缝零产出（防误注入/状态泄漏）。"""
        s = Settings()
        s.audio_evidence.enabled = True
        pipeline = PerceptionPipeline.from_settings(s, detector=EmptyDetector())
        gw = DemoGateway.create_for_test()
        gw.scenario = ScenarioConfig(
            scenario_id="sess-intg-neg",
            source="s",
            start_time=datetime.now(UTC),
        )
        gw.pipeline = pipeline
        ctx = RuntimeFrameContext(
            video_frame=None,
            frame_index=0,
            case_time=0.0,
            audio_events=gw._runtime_audio_events(0),
        )
        result = pipeline.process_frame(ctx)
        assert result.risk_signals == []
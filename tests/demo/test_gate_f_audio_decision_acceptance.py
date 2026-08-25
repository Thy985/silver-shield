"""Gate F F1/F2 · Multimodal Decision Acceptance（demo 装配级 · torch-free）。

报告 §8 冻结定义（``docs/reports/TELEPHONE-RISK-BROWSER-E2E-GATE-REPORT-2026-08-23.md``,
Owner 定性）：Gate A-E 只证明「浏览器基础设施与事实链真实存在」；Gate F 才验收
**Audio RiskSignal 真正参与 Risk → Decision**。runtime 级链路已由
``tests/runtime/test_pipeline_audio_evidence_wiring.py`` 锁定；本模块补齐 **demo
装配级**增量——真实场景 yaml（``e2e_telephone_risk.yaml``）→ 网关场景覆盖 →
``from_settings`` 装配链下的端到端证明：

- **F1**：Audio → RiskSignal(source=AUDIO) → ``FrameResult.risk_signals``
  （source / category / signal_id / created_at 六项契约 + 无幻觉翻译）；
- **F2**：Audio → RiskSignal → ``DecisionInput.risk_signals``（ADR-0040 核心
  验收点；不是 Audio → PerceptionEvent → visit_pending_verify）；
- **门禁锚点**：MONITOR ceiling 默认开启下零产出（完整四档升级行为归 F4，
  此处仅锁「demo 链不绕过 ceiling」）。

**ceiling 局部解除说明**：生产全局默认 ``ceiling_monitor_only=True``（硬门控 1，
class_map 标签真实性 Owner 拍板前不得解除）；F1/F2 信号通道验证须在**测试内**
对已装配 evaluator 的 config 引用局部解除 ceiling——与 wiring 测试
``raised_config()`` 同一范式，只证明「链路通了」，不改变任何全局默认。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

E2E_YAML = REPO_ROOT / "config" / "demo" / "scenarios" / "e2e_telephone_risk.yaml"
DEFAULT_YAML = REPO_ROOT / "config" / "default.yaml"

NOW = datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC)


# ============================================================================
# 测试辅助（模式复用 tests/runtime/test_pipeline_audio_evidence_wiring.py，
# 保持自包含；torch-free：EmptyDetector 免 YOLO 加载）
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

    def detect(self, frame) -> Any:
        from home_perception.detection.detector import DetectionResult

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
        self.inputs: list[Any] = []

    def decide(self, input):
        self.inputs.append(input)

    def bind_trace_span(self, span: Any) -> None:
        self.span = span


def make_telephone_event(*, event_id: str = "tel-1", score: float = 0.9):
    """telephone_risk 场景语义的音频感知事件（持续异常通话）。"""
    from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind

    return AudioPerceptionEvent(
        event_id=event_id,
        timestamp=0.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=score,
        confidence=0.9,
        source_segment_ids=["seg-1"],
    )


def build_demo_chain(clock: ManualClock):
    """e2e_telephone_risk.yaml → 网关场景覆盖 → from_settings 装配链。

    复刻 ``DemoGateway.assemble`` 的覆盖顺序（realtime_risk → audio_evidence →
    from_settings），但不触发 YOLO 加载（object.__new__ 绕过 __init__）。
    返回 ``(pipeline, hp_settings)``——evaluator 持 config 引用，测试内改
    ``hp_settings.audio_evidence`` 字段即生效。
    """
    from home_perception.core.config import Settings
    from home_perception.runtime import PerceptionPipeline
    from silver_demo.gateway import DemoGateway
    from silver_demo.scenarios import load_scenario

    sc = load_scenario(E2E_YAML)
    hp_settings = Settings.load(str(DEFAULT_YAML))
    gw = object.__new__(DemoGateway)
    gw.scenario = sc
    gw.hp_settings = hp_settings
    # 真实 assemble 覆盖顺序（两段都在 from_settings 之前生效）
    gw._apply_scenario_realtime_overrides()
    gw._apply_scenario_audio_evidence_overrides()
    pipeline = PerceptionPipeline.from_settings(
        hp_settings,
        detector=EmptyDetector(),
        device_id=sc.source,
        now_provider=clock,
        frame_interval_s=sc.frame_interval_s,
    )
    return pipeline, hp_settings


def _ctx(case_time: float, events: tuple = ()):
    from home_perception.runtime import RuntimeFrameContext

    return RuntimeFrameContext(
        video_frame=None, frame_index=int(case_time), case_time=case_time, audio_events=events
    )


def _lift_ceiling(hp_settings) -> None:
    """测试内局部解除 MONITOR ceiling + 单事件达 RAISE（生产默认不受影响）。"""
    hp_settings.audio_evidence.ceiling_monitor_only = False
    hp_settings.audio_evidence.raise_min_count = 1


# ============================================================================
# Demo 装配链（场景覆盖 → from_settings）
# ============================================================================


class TestDemoAssemblyChain:
    def test_global_default_stays_disabled(self):
        """灰度纪律基线：default.yaml 加载后 audio_evidence.enabled 仍为 False。"""
        from home_perception.core.config import Settings

        s = Settings.load(str(DEFAULT_YAML))
        assert s.audio_evidence.enabled is False
        assert s.audio_evidence.ceiling_monitor_only is True

    def test_scenario_override_enables_evaluator_assembly(self):
        """场景覆盖经真实网关方法 → from_settings 装配 evaluator（device_id 同源）。"""
        clock = ManualClock()
        p, hp = build_demo_chain(clock)

        assert p._audio_evaluator is not None, (
            "e2e_telephone_risk.yaml 开启 audio_evidence.enabled 后，"
            "demo 装配链必须构造 RealTimeAudioRiskEvaluator"
        )
        assert p._audio_evaluator._device_id == p.rule_engine.device_id
        # 白名单纪律：场景只开了 enabled，门禁字段保持安全默认
        assert hp.audio_evidence.ceiling_monitor_only is True
        assert hp.audio_evidence.escalate_enabled is False

    def test_ceiling_default_zero_yield_via_demo_chain(self):
        """门禁锚点：demo 链默认配置（ceiling 开）下音频事件零信号产出。

        实现若绕过 ceiling 直接产信号，此处首先失败（F4 完整四档另测）。
        """
        clock = ManualClock()
        p, _hp = build_demo_chain(clock)
        for i in range(3):
            r = p.process_frame(_ctx(float(i), (make_telephone_event(event_id=f"t{i}"),)))
            assert r.risk_signals == []
            assert r.warnings == []


# ============================================================================
# F1：Audio RiskSignal 真进入 Runtime FrameResult
# ============================================================================


class TestF1AudioSignalContract:
    def test_f1_telephone_raised_full_contract(self):
        """解除 ceiling（测试内）→ telephone 音频事件 → RAISED 信号六项契约。"""
        from datetime import datetime as dt

        from home_perception.analysis.risk_signal import SignalCategory, SignalTransition

        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        _lift_ceiling(hp)

        r0 = p.process_frame(_ctx(0.0, (make_telephone_event(),)))

        raised = [s for s in r0.risk_signals if s.transition is SignalTransition.RAISED]
        assert len(raised) == 1, f"恰一条 RAISED，实际 {r0.risk_signals!r}"
        sig = raised[0]
        # ① source = AUDIO
        assert sig.source.value == "audio"
        # ② category 正确（telephone 类恒 COMMUNICATION）
        assert sig.category is SignalCategory.COMMUNICATION
        # ③ signal_id 唯一非空
        assert sig.signal_id
        # ④ created_at 合法（UTC-aware datetime，非 float 戳）
        assert isinstance(sig.created_at, dt)
        assert sig.created_at.tzinfo is not None
        # ⑤ 未被翻译成视觉事件（硬门控 3：无幻觉兜底 visit_pending_verify）
        assert all(pe.event_type != "visit_pending_verify" for pe in r0.perception_events)
        # ⑥ audio-native path（ADR-0040 D6 升级 · ADR-0044）：纯 audio 产出 LOW + MONITOR warning
        assert len(r0.warnings) == 1
        assert r0.warnings[0].risk_level == "LOW"
        assert r0.warnings[0].recommended_action == "MONITOR"

    def test_f1_cleared_paired_after_silence(self):
        """CLEARED 与 RAISED 成对（paired_signal_id 回填）——双轨投影的事件轨完整性。"""
        from home_perception.analysis.risk_signal import SignalTransition

        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        _lift_ceiling(hp)
        hp.audio_evidence.clear_timeout_s = 5.0

        r0 = p.process_frame(_ctx(0.0, (make_telephone_event(),)))
        raised = next(s for s in r0.risk_signals if s.transition is SignalTransition.RAISED)
        for t in (2.0, 4.0):
            r = p.process_frame(_ctx(t))
            assert all(s.transition is not SignalTransition.CLEARED for s in r.risk_signals)
        r6 = p.process_frame(_ctx(6.0))
        cleared = [s for s in r6.risk_signals if s.transition is SignalTransition.CLEARED]
        assert len(cleared) == 1
        assert cleared[0].paired_signal_id == raised.signal_id


# ============================================================================
# F2：Audio RiskSignal 真进入 DecisionInput（ADR-0040 核心验收点）
# ============================================================================


class TestF2DecisionInputPassthrough:
    def test_f2_policy_receives_native_audio_signals(self):
        """纯音频 RAISED 帧：policy 经 DecisionInput.risk_signals 以原生形态收到信号。

        - risk_signals 含 AUDIO 信号（R3 断点消除的直接证明）；
        - trigger_events 为空（AUDIO 不走视觉翻译，硬门控 3）；
        - warnings / perception_events 空（纯信号零升级动作——policy 升级消费前的灰度语义）。
        """
        from home_perception.analysis.decision_engine import DecisionEngine

        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        _lift_ceiling(hp)
        # realtime_risk 已被场景覆盖为 decision_enabled=true；替换 policy 为捕获桩
        p.decision_engine = DecisionEngine(
            elder_id="elder_001", policy=CapturingPolicy(), now_provider=clock
        )

        r0 = p.process_frame(_ctx(0.0, (make_telephone_event(),)))

        policy = p.decision_engine.policy
        assert len(policy.inputs) >= 1, "decision_enabled 下 Stage D 必须调用 policy"
        last = policy.inputs[-1]
        audio_sigs = [s for s in last.risk_signals if s.source.value == "audio"]
        assert len(audio_sigs) == 1
        assert last.trigger_events == ()
        assert r0.warnings == []
        assert r0.perception_events == []

    def test_f2_no_audio_no_signal_no_translation(self):
        """反幻觉基线：无音频帧 → 无 AUDIO 信号进 DecisionInput、零翻译。"""
        from home_perception.analysis.decision_engine import DecisionEngine

        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        _lift_ceiling(hp)
        p.decision_engine = DecisionEngine(
            elder_id="elder_001", policy=CapturingPolicy(), now_provider=clock
        )

        p.process_frame(_ctx(0.0))

        policy = p.decision_engine.policy
        for inp in policy.inputs:
            assert all(s.source.value != "audio" for s in inp.risk_signals)


# ============================================================================
# 时钟卫生（防跨测试状态泄漏）
# ============================================================================


def test_manual_clock_advance_isolated():
    """ManualClock.advance 仅影响实例自身（辅助契约，防止未来重构引入共享时钟）。"""
    a, b = ManualClock(), ManualClock()
    a.advance(seconds=5)
    assert b.now() == NOW
    assert a.now() == NOW + timedelta(seconds=5)
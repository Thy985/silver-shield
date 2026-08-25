"""Gate F F3–F6 · Multimodal Decision Acceptance（torch-free）。

承接 ``test_gate_f_audio_decision_acceptance.py``（F1/F2），按报告 §8 冻结定义
（``docs/reports/TELEPHONE-RISK-BROWSER-E2E-GATE-REPORT-2026-08-23.md``）补齐：

- **F3 Temporal Alignment（ADR-0041）**：SAME_FRAME / NEAR_WINDOW / UNLINKED 三态；
  窗口数值**按配置读取而非写死**（``realtime_risk.signal_temporal_window_s``，
  默认 None 悬空 = NEAR_WINDOW 不可用，SAME_FRAME 不受影响）；缺位置 fail-safe
  UNLINKED；EpisodeClock Unix→case_time 换算与负值钳位。
- **F4 EvidenceStrength 四档（ADR-0042）**：单弱事件→MONITOR / 持续→RAISE /
  多独立证据→NOTIFY / LinkedSignalPair→ESCALATE；**门禁写死**：ceiling 开启时
  一切 ≤ MONITOR 且 routed=("LOW","MONITOR")；fallback kind 恒封顶（即使 ceiling
  解除）；ESCALATE 缺 LinkedSignalPair 验证不可达（D6 双重门控）。
- **F5 Action 贡献链**：Stage D 统一入口下视觉 RAISED（翻译）+ AUDIO RAISED（原生
  透传）同帧汇入 DecisionInput → Warning 的 ``meta.risk_signals`` 与 reason_summary
  捕获 audio 贡献（signal_id 可追溯）→ executor 产 ActionCommand。**边界声明**：
  audio 主导的 action 升级（modality-aware routing 进 policy 判定）属「policy 升级
  消费」后续工作（ADR-0040/0042 灰度纪律），本模块锁定当前架构下的可审计形态。
- **F6 反幻觉 E2E（负例集）**：单次电话声不通知家属 / fallback kind 不升级 /
  时间完全不重叠不产生 combined risk / class_map 缺失（ceiling 态）不允许 RAISE+ /
  无 audio 不伪造 audio-derived risk。

所有升级档验证均在**测试内局部解除 ceiling**（生产默认封顶不变，硬门控 1）。
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
# 测试辅助（自包含，模式复用 F1/F2 与 wiring 测试）
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


def make_audio_event(
    *,
    event_id: str = "a1",
    kind: Any = None,
    score: float = 0.9,
    confidence: float = 0.9,
):
    from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind

    return AudioPerceptionEvent(
        event_id=event_id,
        timestamp=0.0,
        kind=kind or AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=score,
        confidence=confidence,
        source_segment_ids=["seg-1"],
    )


def make_vision_raised_signal(*, signal_id: str | None = None):
    """视觉 RAISED 信号（dwell 主导证据 → 可被 signal_adapter 翻译为 abnormal_dwell）。"""
    import uuid as _uuid

    from home_perception.analysis.risk_signal import RiskSignal

    visitor_uuid = str(_uuid.uuid4())  # signal_adapter 契约：subject_id 须为合法 UUID
    return RiskSignal(
        signal_id=signal_id or str(_uuid.uuid4()),
        subject_type="visitor",
        subject_id=visitor_uuid,
        category="communication",
        source="vision",
        transition="raised",
        features={
            "dwell_seconds": 60.0,
            "thresholds": {"long_duration_seconds": 30.0},
        },
        track_id=1,
        visitor_instance_id=visitor_uuid,
        created_at=NOW,
    )


def build_demo_chain(clock: ManualClock):
    """e2e_telephone_risk.yaml → 网关场景覆盖 → from_settings 装配链（同 F1/F2）。"""
    from home_perception.core.config import Settings
    from home_perception.runtime import PerceptionPipeline
    from silver_demo.gateway import DemoGateway
    from silver_demo.scenarios import load_scenario

    sc = load_scenario(E2E_YAML)
    hp_settings = Settings.load(str(DEFAULT_YAML))
    gw = object.__new__(DemoGateway)
    gw.scenario = sc
    gw.hp_settings = hp_settings
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


def fresh_evaluator(hp, **cfg):
    """demo 链同源 device_id 的新评估器（测试内局部解除 ceiling + 自定义档位参数）。"""
    from home_perception.analysis.realtime_audio_risk_evaluator import (
        RealTimeAudioRiskEvaluator,
    )
    from home_perception.core.config import AudioEvidenceConfig

    base = {"enabled": True, "ceiling_monitor_only": False}
    base.update(cfg)
    return RealTimeAudioRiskEvaluator(
        device_id="CCTV_Surveillance_Final", config=AudioEvidenceConfig(**base)
    )


# ============================================================================
# F3 · Temporal Alignment（ADR-0041）
# ============================================================================


class TestF3TemporalAlignment:
    def _pair(self, *, v_frame, v_case, a_frame, a_case):
        import uuid as _uuid

        from home_perception.analysis.risk_signal import RiskSignal
        from home_perception.analysis.signal_temporal_linker import SignalPosition

        def mk(source: str) -> RiskSignal:
            return RiskSignal(
                signal_id=str(_uuid.uuid4()),
                subject_type="visitor",
                subject_id="vi-1",
                category="communication",
                source=source,
                transition="raised",
                features={},
                track_id=1 if source == "vision" else None,
                visitor_instance_id="vi-1",
                created_at=NOW,
            )


        return (
            SignalPosition(signal=mk("vision"), frame_index=v_frame, case_time=v_case),
            SignalPosition(signal=mk("audio"), frame_index=a_frame, case_time=a_case),
        )

    def test_f3_same_frame_zero_threshold(self):
        """同一 frame_index 共现 → SAME_FRAME（强关联，不受窗口悬空影响）。"""
        from home_perception.analysis.signal_temporal_linker import LinkLevel, link

        vision, audio = self._pair(v_frame=7, v_case=3.5, a_frame=7, a_case=3.5)
        pair = link(vision, audio, window_s=None)
        assert pair is not None
        assert pair.level is LinkLevel.SAME_FRAME
        assert pair.link_strength == 1.0
        assert pair.delta == 0.0

    def test_f3_near_window_reads_value_from_config_not_hardcoded(self):
        """NEAR_WINDOW 边界随配置值变化（窗口数值按配置读取，非写死——冻结要求）。"""
        from home_perception.analysis.signal_temporal_linker import LinkLevel, link

        # 同一对信号（Δcase_time=1.5s）：window=2.0 → NEAR_WINDOW；window=1.0 → UNLINKED
        v1, a1 = self._pair(v_frame=0, v_case=0.0, a_frame=None, a_case=1.5)
        p_in = link(v1, a1, window_s=2.0)
        assert p_in is not None and p_in.level is LinkLevel.NEAR_WINDOW
        assert 0.0 < p_in.link_strength <= 1.0  # 线性衰减 ∈ (0,1]

        v2, a2 = self._pair(v_frame=0, v_case=0.0, a_frame=None, a_case=1.5)
        assert link(v2, a2, window_s=1.0) is None  # Δ > window → UNLINKED

    def test_f3_window_none_suspends_near_window_only(self):
        """悬空期（window_s=None）：NEAR_WINDOW 恒不可用，SAME_FRAME 不受影响。"""
        from home_perception.analysis.signal_temporal_linker import (
            LinkLevel,
            classify,
            link,
        )

        v1, a1 = self._pair(v_frame=0, v_case=0.0, a_frame=None, a_case=0.5)
        assert classify(v1, a1, window_s=None) is LinkLevel.UNLINKED
        assert link(v1, a1, window_s=None) is None  # 「恰好都出现过」≠ 自动认定多模态

    def test_f3_missing_position_fail_safe_unlinked(self):
        """任一方缺位置信息 → UNLINKED（宁可不关联也不猜）。"""
        from home_perception.analysis.signal_temporal_linker import (
            LinkLevel,
            classify,
        )

        v, a = self._pair(v_frame=0, v_case=None, a_frame=None, a_case=0.5)
        assert classify(v, a, window_s=2.0) is LinkLevel.UNLINKED

    def test_f3_episode_clock_unix_conversion_and_clamp(self):
        """EpisodeClock：Unix 墙钟秒 → case_time；早于锚点钳位 0.0（非负语义）。"""
        from home_perception.analysis.signal_temporal_linker import EpisodeClock

        clock = EpisodeClock(1000.0)
        assert clock.unix_to_case(1002.5) == 2.5
        assert clock.unix_to_case(998.0) == 0.0  # 设备时钟漂移钳位

    def test_f3_window_config_channel_wired_via_demo_chain(self):
        """demo 链配置通道：signal_temporal_window_s 默认悬空 None，赋值后可读回。"""
        _, hp = build_demo_chain(ManualClock())
        assert hp.realtime_risk.signal_temporal_window_s is None  # 悬空安全默认
        hp.realtime_risk.signal_temporal_window_s = 2.0
        assert hp.realtime_risk.signal_temporal_window_s == 2.0


# ============================================================================
# F4 · EvidenceStrength 四档升级（ADR-0042）
# ============================================================================


class TestF4EvidenceStrengthLadder:
    def test_f4_single_weak_event_is_monitor(self):
        """单个弱音频事件 → MONITOR（单事件永不升级，Evidence Continuity > Count）。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength

        ev = fresh_evaluator(None, raise_min_count=2)
        out = ev.observe(make_audio_event(event_id="a1"), case_time=0.0)
        assert out.strength is EvidenceStrength.MONITOR
        assert out.signal is None

    def test_f4_persistent_same_kind_reaches_raise(self):
        """持续可信音频（同 kind 窗口计数 ≥ N）→ RAISE + RAISED 信号。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength
        from home_perception.analysis.risk_signal import SignalTransition

        ev = fresh_evaluator(None, raise_min_count=2)
        out1 = ev.observe(make_audio_event(event_id="a1"), case_time=0.0)
        assert out1.strength is EvidenceStrength.MONITOR
        out2 = ev.observe(make_audio_event(event_id="a2"), case_time=0.5)
        assert out2.strength is EvidenceStrength.RAISE
        assert out2.signal is not None
        assert out2.signal.transition is SignalTransition.RAISED

    def test_f4_multi_kind_diversity_reaches_notify(self):
        """多个独立声学证据（跨 kind 独立 kind 数 ≥ M）→ NOTIFY。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength
        from home_perception.audio.event import AudioPerceptionKind

        ev = fresh_evaluator(None, notify_min_kinds=2, raise_min_count=None)
        o1 = ev.observe(
            make_audio_event(
                event_id="a1", kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT
            ),
            case_time=0.0,
        )
        assert o1.strength is EvidenceStrength.MONITOR  # 第一件仅观察
        o2 = ev.observe(
            make_audio_event(
                event_id="a2", kind=AudioPerceptionKind.AUDIO_SPEECH_RAPID
            ),
            case_time=0.5,
        )
        assert o2.strength is EvidenceStrength.NOTIFY
        assert o2.signal is not None  # ≥ RAISE 档均 emit

    def test_f4_escalate_requires_linked_pair_verification(self):
        """ESCALATE 双重门控（D6）：开关开但缺 LinkedSignalPair 验证 → 不可达。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength

        ev = fresh_evaluator(None, escalate_enabled=True, raise_min_count=1)
        out = ev.observe(make_audio_event(), case_time=0.0, linked_pair_verified=False)
        assert out.strength is not EvidenceStrength.ESCALATE
        assert any("LinkedSignalPair" in r for r in out.reasons)

    def test_f4_escalate_with_verified_pair(self):
        """Vision+Audio temporal link 验证通过 + 开关开 → ESCALATE / 高置信路由。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength

        ev = fresh_evaluator(None, escalate_enabled=True, raise_min_count=1)
        out = ev.observe(make_audio_event(), case_time=0.0, linked_pair_verified=True)
        assert out.strength is EvidenceStrength.ESCALATE
        assert out.routed == ("HIGH", "ESCALATE_COMMUNITY")

    def test_f4_gate_ceiling_caps_everything_to_monitor_routing(self):
        """门禁写死：ceiling 开启（class_map 未修复态）下一切判级压回 MONITOR，
        routed=("LOW","MONITOR")，零信号产出——即使 escalate 全开 + 验证通过。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength

        ev = fresh_evaluator(
            None,
            ceiling_monitor_only=True,
            escalate_enabled=True,
            raise_min_count=1,
        )
        for i in range(3):
            out = ev.observe(
                make_audio_event(event_id=f"a{i}"),
                case_time=float(i),
                linked_pair_verified=True,
            )
            assert out.strength is EvidenceStrength.MONITOR
            assert out.pre_ceiling_strength is not EvidenceStrength.MONITOR or i == 0
            assert out.routed == ("LOW", "MONITOR")
            assert out.signal is None

    def test_f4_gate_fallback_kind_capped_even_without_ceiling(self):
        """fallback kind（AUDIO_ANOMALY_OTHER 兜底类）恒封顶 MONITOR——
        即使 ceiling 解除 + 升级参数全开（双保险第二道）。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength
        from home_perception.audio.event import AudioPerceptionKind

        ev = fresh_evaluator(
            None, escalate_enabled=True, raise_min_count=1, notify_min_kinds=1
        )
        for i in range(3):
            out = ev.observe(
                make_audio_event(
                    event_id=f"f{i}", kind=AudioPerceptionKind.AUDIO_ANOMALY_OTHER
                ),
                case_time=float(i),
                linked_pair_verified=True,
            )
            assert out.strength is EvidenceStrength.MONITOR
            assert out.signal is None


# ============================================================================
# F5 · Action 贡献链（Stage D 统一入口）
# ============================================================================


class TestF5ActionAttributionChain:
    def test_f5_warning_meta_and_reasons_capture_audio_contribution(self):
        """视觉 RAISED（翻译）+ AUDIO RAISED（原生透传）同帧 → Warning 捕获 audio 贡献。

        贡献链可追溯：Warning.meta.risk_signals.sources 含 audio、signal_ids 含该
        AUDIO 信号 id、reason_summary 含 audio 人话原因、executor 产出 ActionCommand。
        """

        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        hp.audio_evidence.ceiling_monitor_only = False
        hp.audio_evidence.raise_min_count = 1
        audio_sig = p._audio_evaluator.observe(make_audio_event(), case_time=0.0).signal
        assert audio_sig is not None
        vision_sig = make_vision_raised_signal()

        percs, warnings, cmds = p._act_on_signals([vision_sig, audio_sig], NOW)

        # 视觉候选存在（abnormal_dwell 翻译成功）→ policy 走完整分支产 Warning
        assert any(pe.event_type == "abnormal_dwell" for pe in percs)
        assert len(warnings) == 1
        w = warnings[0]
        meta = w.meta["risk_signals"]
        assert "audio" in meta["sources"], f"meta 必须捕获 audio 贡献源: {meta!r}"
        assert meta["raised"] >= 1
        assert audio_sig.signal_id in meta["signal_ids"]  # ID 级可追溯
        assert any("communication(audio)" in r for r in w.reason_summary), (
            f"reason_summary 必须含 audio 人话贡献: {w.reason_summary!r}"
        )
        # Action ← Warning：executor 已消费 Warning 产指令
        assert len(cmds) >= 1
        assert cmds[0].command_type

    def test_f5_pure_audio_zero_action_grayscale_semantics(self):
        """Audio-native path（ADR-0040 D6 升级 · ADR-0044）：纯音频帧零视觉触发 →
        audio-native Warning(LOW + MONITOR) + LOG_ONLY ActionCommand。

        不经 signal_adapter.risk_signal_to_perception（硬门控 3：percs 恒空）。
        """
        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        hp.audio_evidence.ceiling_monitor_only = False
        hp.audio_evidence.raise_min_count = 1


        audio_sig = p._audio_evaluator.observe(make_audio_event(), case_time=0.0).signal
        percs, warnings, cmds = p._act_on_signals([audio_sig], NOW)

        assert percs == []  # AUDIO 不经视觉翻译（硬门控 3）
        # audio-native path：纯 audio 产出 LOW + MONITOR warning
        assert len(warnings) == 1
        assert warnings[0].risk_level == "LOW"
        assert warnings[0].recommended_action == "MONITOR"
        assert len(cmds) >= 1  # ActionExecutor 消费 MONITOR → LOG_ONLY command


def _ctx(case_time: float, events: tuple = ()):
    from home_perception.runtime import RuntimeFrameContext

    return RuntimeFrameContext(
        video_frame=None, frame_index=int(case_time), case_time=case_time, audio_events=events
    )


# ============================================================================
# F6 · 反幻觉 E2E（负例集）
# ============================================================================


class TestF6AntiHallucination:
    def test_f6_single_phone_ring_no_family_notify(self):
        """❌ 单次电话声 → 不通知家属（未达持续性门槛 → MONITOR → 零信号零动作）。"""
        clock = ManualClock()
        p, hp = build_demo_chain(clock)
        hp.audio_evidence.ceiling_monitor_only = False
        hp.audio_evidence.raise_min_count = 2  # 持续性门槛 N=2

        r = p.process_frame(_ctx(0.0, (make_audio_event(event_id="once"),)))

        assert r.risk_signals == []
        assert r.warnings == []
        assert all(c.command_type != "SEND_FAMILY_MESSAGE" for c in r.commands)

    def test_f6_fallback_kind_never_escalates(self):
        """❌ fallback audio_distress_cry（兜底类）→ 不升级（一切开关全开仍封顶）。"""
        from home_perception.analysis.evidence_strength import EvidenceStrength
        from home_perception.audio.event import AudioPerceptionKind

        ev = fresh_evaluator(
            None, escalate_enabled=True, raise_min_count=1, notify_min_kinds=1
        )
        out = ev.observe(
            make_audio_event(kind=AudioPerceptionKind.AUDIO_ANOMALY_OTHER, score=0.99),
            case_time=0.0,
            linked_pair_verified=True,
        )
        assert out.strength is EvidenceStrength.MONITOR
        assert out.signal is None

    def test_f6_no_temporal_overlap_no_combined_risk(self):
        """❌ Audio 与 Vision 时间完全不重叠 → 不产生 combined risk（UNLINKED 不合并）。"""
        from home_perception.analysis.signal_temporal_linker import link

        v = _position("vision", frame_index=0, case_time=0.0)
        a = _position("audio", frame_index=None, case_time=30.0)
        assert link(v, a, window_s=2.0) is None  # Δ=30s >> 窗口

    def test_f6_class_map_missing_state_disallows_raise_plus(self):
        """❌ class_map 缺失态（ceiling 开启）→ 不允许 RAISE+（任何输入任何配置）。"""
        from home_perception.analysis.evidence_strength import (
            STRENGTH_ORDER,
            EvidenceStrength,
        )
        from home_perception.audio.event import AudioPerceptionKind

        ev = fresh_evaluator(
            None,
            ceiling_monitor_only=True,
            escalate_enabled=True,
            raise_min_count=1,
            notify_min_kinds=1,
        )
        kinds = [
            AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
            AudioPerceptionKind.AUDIO_SPEECH_RAPID,
            AudioPerceptionKind.AUDIO_ANOMALY_OTHER,
        ]
        n = 0
        for round_i in range(3):
            for k in kinds:
                out = ev.observe(
                    make_audio_event(event_id=f"x{n}", kind=k, score=0.99),
                    case_time=float(round_i),
                    linked_pair_verified=True,
                )
                assert STRENGTH_ORDER[out.strength] <= STRENGTH_ORDER[EvidenceStrength.MONITOR]
                assert out.signal is None
                n += 1

    def test_f6_no_audio_no_fabricated_audio_risk(self):
        """❌ 无 audio → 不伪造 audio-derived risk（装配开启 + 零输入）。"""
        clock = ManualClock()
        p, _hp = build_demo_chain(clock)
        assert p._audio_evaluator is not None  # 已装配

        r = p.process_frame(_ctx(0.0))  # ctx.audio_events=()

        assert all(s.source.value != "audio" for s in r.risk_signals)
        assert r.risk_signals == [] or all(
            s.transition.value != "cleared" for s in r.risk_signals if s.source.value == "audio"
        )


def _position(source: str, *, frame_index, case_time):
    """F6 专用 SignalPosition 构造（source: vision/audio）。"""
    import uuid as _uuid

    from home_perception.analysis.risk_signal import RiskSignal
    from home_perception.analysis.signal_temporal_linker import SignalPosition

    sig = RiskSignal(
        signal_id=str(_uuid.uuid4()),
        subject_type="visitor",
        subject_id="vi-1",
        category="communication",
        source=source,
        transition="raised",
        features={},
        track_id=1 if source == "vision" else None,
        visitor_instance_id="vi-1",
        created_at=NOW,
    )
    return SignalPosition(signal=sig, frame_index=frame_index, case_time=case_time)
"""Gate G · True Multimodal Risk Story（G1–G5 · demo 装配级 · torch-free）。

Owner 冻结链（2026-08-23）——本文件验证其 runtime 落点：

```
Vision Evidence + Audio Evidence
    ↓ SignalTemporalLinker.link（pipeline._synthesize_combined_risk）
    ↓ LinkedSignalPair                    ← G1/G2（FrameResult.linked_pairs 观测点）
    ↓ EvidenceStrength（ESCALATE 组合信号）  ← G4/G5
    ↓ Modality-aware Decision / Warning    ← （policy 最小消费，F5 已锁）
    ↓ Action / Browser DOM                 ← G6（独立 E2E，另文件）
```

- **G1**：真实 Runtime（process_frame 帧循环内）生成 ``LinkedSignalPair``；
- **G2**：Δt 使用真实 runtime 时钟域（ctx.case_time 统一域，非墙钟/素材时间戳）；
- **G3**：Vision-only 不误升级（无音频侧 → 结构性无 pair 无 combined）；
- **G4**：Audio-only 按 EvidenceStrength 正确处理（单模态永不 ESCALATE）；
- **G5**：Vision+Audio temporal link → Combined Risk（ESCALATE 组合信号 +
  pair 元数据可追溯）。

**验收态边界**：本文件以编程方式在 demo 装配链上开启验收态参数
（window=2.0s / escalate_enabled / ceiling 解除 / raise_min_count=1）——
等价于 DEMO_HP_CONFIG=config/live_audio_gate_g.yaml；生产默认不变
（MONITOR ceiling + escalate 关闭，硬门控 1/2）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

E2E_YAML = REPO_ROOT / "config" / "demo" / "scenarios" / "e2e_telephone_risk.yaml"
DEFAULT_YAML = REPO_ROOT / "config" / "default.yaml"

NOW = datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC)

# 验收态窗口（ADR-0041 候选档上限；生产悬空 None 待数据回填）
ACCEPT_WINDOW_S = 2.0


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


def make_telephone_event(*, event_id: str = "tel-1", score: float = 0.9):
    from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind

    return AudioPerceptionEvent(
        event_id=event_id,
        timestamp=0.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=score,
        confidence=0.9,
        source_segment_ids=["seg-1"],
    )


def make_vision_raised_signal(*, signal_id: str | None = None):
    """视觉 RAISED（realtime evaluator 同构产物；source=vision 可被 adapter 翻译）。"""
    from home_perception.analysis.risk_signal import RiskSignal

    visitor_uuid = str(uuid.uuid4())
    return RiskSignal(
        signal_id=signal_id or str(uuid.uuid4()),
        subject_type="visitor",
        subject_id=visitor_uuid,
        category="communication",
        source="vision",
        transition="raised",
        features={"dwell_seconds": 60.0, "thresholds": {"long_duration_seconds": 30.0}},
        track_id=1,
        visitor_instance_id=visitor_uuid,
        created_at=NOW,
    )


def make_audio_raised_signal(*, kind: str = "audio_distress_cry"):
    """音频 RAISED（evaluator 同构产物：features.audio_kind 供 synthesis 反解）。"""
    from home_perception.analysis.risk_signal import RiskSignal

    return RiskSignal(
        signal_id=str(uuid.uuid4()),
        subject_type="visitor",
        subject_id=str(uuid.uuid4()),
        category="communication",
        source="audio",
        transition="raised",
        features={"audio_kind": kind},
        created_at=NOW,
    )


def build_gate_g_chain(clock: ManualClock):
    """demo 装配链 + Gate G 验收态（等价 live_audio_gate_g.yaml 的 runtime 效果）。

    覆盖顺序与真实 assemble 一致（场景覆盖 → 编程式验收态 → from_settings），
    from_settings 读 hp.realtime_risk.signal_temporal_window_s 与
    hp.audio_evidence.escalate_enabled 构造 synthesis 接线。
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
    gw._apply_scenario_realtime_overrides()
    gw._apply_scenario_audio_evidence_overrides()
    # —— Gate G 验收态（= live_audio_gate_g.yaml 的关键项）——
    hp_settings.realtime_risk.signal_temporal_window_s = ACCEPT_WINDOW_S
    hp_settings.audio_evidence.ceiling_monitor_only = False
    hp_settings.audio_evidence.escalate_enabled = True
    hp_settings.audio_evidence.raise_min_count = 1
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
        video_frame=None,
        frame_index=int(case_time * 2),  # frame_interval_s=0.5 → 与 case_time 同域
        case_time=case_time,
        audio_events=events,
    )


def _combined_signals(r):
    return [s for s in r.risk_signals if s.features.get("combined_risk")]


# ============================================================================
# G1 · 真实 Runtime 生成 LinkedSignalPair
# ============================================================================


class TestG1RuntimeLinkedPair:
    def test_g1_process_frame_produces_linked_pair(self):
        """近窗视觉 RAISED（上帧缓存）+ 本帧音频 RAISED → process_frame 内部
        真实产出 LinkedSignalPair（FrameResult.linked_pairs 观测点）。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        # 模拟 frame 9（case_time=4.6s）产生的视觉 RAISED（进入近窗缓存）
        p._recent_vision_raised.append((4.6, 9, make_vision_raised_signal()))

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))  # frame 10，Δt=0.4s

        assert len(r.linked_pairs) == 1, f"恰一条关联对，实际 {r.linked_pairs!r}"
        pair = r.linked_pairs[0]
        assert pair.delta == 0.4  # runtime case_time 域差值
        assert 0.0 < pair.link_strength <= 1.0
        # Combined Risk 信号已并入同一 risk_signals 通道（Stage D 完整可见）
        combined = _combined_signals(r)
        assert len(combined) == 1
        assert combined[0].source.value == "audio"
        assert any(s.transition.value == "raised" for s in r.risk_signals)

    def test_g1_vision_anchor_links_recent_audio(self):
        """对称方向：音频先现（上帧入缓存）+ 本帧视觉 RAISED → 视觉锚点回看
        配对成功。Temporal Link 在 ADR-0041 中是无方向 |Δt| 关系，音频先现
        （真实素材常见时序：语音先于人物入画）同样必须可关联。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        p._recent_audio_raised.append((5.0, 10, make_audio_raised_signal()))

        combined, pairs = p._synthesize_combined_risk(
            [make_vision_raised_signal()], frame_index=11, case_time=5.6
        )

        assert len(pairs) == 1
        assert pairs[0].delta == 0.6
        assert len(combined) == 1
        assert combined[0].features["combined_risk"] is True

    def test_g1_combined_signal_carries_pair_metadata(self):
        """combined 信号 features 携带贡献链元数据（pair 级别/强度/双方 signal_id）。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        vision = make_vision_raised_signal()
        p._recent_vision_raised.append((5.0, 10, vision))

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))  # 同帧

        combined = _combined_signals(r)
        assert len(combined) == 1
        f = combined[0].features
        assert f["combined_risk"] is True
        assert f["linked_pair_level"] in ("same_frame", "near_window")
        assert f["vision_signal_id"] == vision.signal_id  # ID 级可追溯
        assert f.get("paired_audio_signal_id")
        assert isinstance(f["link_strength"], float)


# ============================================================================
# G2 · Δt 使用真实 runtime 时钟域
# ============================================================================


class TestG2RealClockDomain:
    def test_g2_delta_from_ctx_case_time_not_wall_clock(self):
        """pair.delta == |Δctx.case_time|（runtime 伪时钟域），与事件 timestamp/
        墙钟无关（demo 投递规则：音频事件 runtime 位置由投递帧决定）。"""
        clock = ManualClock()
        p, _hp = build_gate_chain_with_window(clock, window_s=2.0)
        p._recent_vision_raised.append((10.0, 20, make_vision_raised_signal()))

        r = p.process_frame(_ctx(11.5, (make_telephone_event(),)))  # Δt=1.5s

        assert len(r.linked_pairs) == 1
        assert r.linked_pairs[0].delta == 1.5

    def test_g2_window_config_flips_linkage(self):
        """同一 Δt=1.5s：window=2.0 关联成立；window=1.0 结构性不关联（数值由
        配置驱动，非写死——ADR-0041 D2 冻结要求）。"""
        clock = ManualClock()
        p_on, _ = build_gate_chain_with_window(clock, window_s=2.0)
        p_on._recent_vision_raised.append((10.0, 20, make_vision_raised_signal()))
        r_on = p_on.process_frame(_ctx(11.5, (make_telephone_event(),)))
        assert len(r_on.linked_pairs) == 1

        p_off, _ = build_gate_chain_with_window(ManualClock(), window_s=1.0)
        p_off._recent_vision_raised.append((10.0, 20, make_vision_raised_signal()))
        r_off = p_off.process_frame(_ctx(11.5, (make_telephone_event(),)))
        assert r_off.linked_pairs == []
        assert _combined_signals(r_off) == []


def build_gate_chain_with_window(clock: ManualClock, *, window_s: float):
    """build_gate_g_chain 的显式 window 参数变体（G2 配置翻转验证用）。"""
    p, hp = build_gate_g_chain(clock)
    hp.realtime_risk.signal_temporal_window_s = window_s
    # from_settings 已用 ACCEPT_WINDOW_S 构造；直接改 runtime 侧字段对齐本测语义
    p._temporal_window_s = window_s
    return p, hp


# ============================================================================
# G3 · Vision-only 不误升级
# ============================================================================


class TestG3VisionOnlyNoFalseEscalation:
    def test_g3_vision_only_no_pair_no_combined(self):
        """只有视觉 RAISED（含近窗缓存历史）：无音频侧 → 结构性零 pair 零 combined。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)

        # 仅预置视觉缓存 + 本帧只喂视觉信号（经 synthesis 直调，模拟视觉评估帧）
        p._recent_vision_raised.append((5.0, 10, make_vision_raised_signal()))
        combined, pairs = p._synthesize_combined_risk(
            [make_vision_raised_signal()], frame_index=11, case_time=5.5
        )

        assert pairs == []
        assert combined == []

    def test_g3_escalate_enabled_still_requires_audio_side(self):
        """escalate 开启也不足以单 Vision 升级——多模态验证链必须双侧成立。"""
        clock = ManualClock()
        p, hp = build_gate_g_chain(clock)
        assert hp.audio_evidence.escalate_enabled is True  # 前置：开关已开
        p._recent_vision_raised.append((5.0, 10, make_vision_raised_signal()))
        r = p.process_frame(_ctx(5.5))  # 无音频输入

        assert r.linked_pairs == []
        assert _combined_signals(r) == []


# ============================================================================
# G4 · Audio-only 按 EvidenceStrength 正确处理
# ============================================================================


class TestG4AudioOnlyByEvidenceStrength:
    def test_g4_audio_only_raises_but_never_combines(self):
        """Audio-only：单模态 RAISE 正常升起，但无视觉侧 → 永不产生 combined/ESCALATE。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))  # 无任何视觉缓存

        audio_raised = [
            s for s in r.risk_signals if s.source.value == "audio"
            and s.transition.value == "raised"
        ]
        assert len(audio_raised) >= 1, "单模态持续性证据应正常 RAISE（raise_min_count=1）"
        assert all(not s.features.get("combined_risk") for s in audio_raised)
        assert r.linked_pairs == []

    def test_g4_weak_single_event_stays_below_raise_when_n2(self):
        """持续性门槛 N=2 时单事件 MONITOR（单事件永不升级，Evidence Continuity）。"""
        clock = ManualClock()
        p, hp = build_gate_g_chain(clock)
        hp.audio_evidence.raise_min_count = 2
        p._audio_evaluator._config.raise_min_count = 2  # evaluator 持 config 引用

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))

        raised_audio = [
            s for s in r.risk_signals
            if s.source.value == "audio" and s.transition.value == "raised"
        ]
        assert raised_audio == [], "N=2 时单事件不得 RAISE"
        assert _combined_signals(r) == []
        assert r.linked_pairs == []


# ============================================================================
# G5 · Vision + Audio temporal link → Combined Risk
# ============================================================================


class TestG5CombinedRisk:
    def test_g5_same_frame_link_full_chain(self):
        """同帧共现：SAME_FRAME（strength=1.0）+ ESCALATE 组合信号全链成立。"""
        from home_perception.analysis.evidence_strength import (
            EvidenceStrength,
            route_strength,
        )
        from home_perception.analysis.signal_temporal_linker import LinkLevel

        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        vision = make_vision_raised_signal()
        p._recent_vision_raised.append((5.0, 10, vision))

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))  # 同帧同刻

        assert len(r.linked_pairs) == 1
        pair = r.linked_pairs[0]
        assert pair.level is LinkLevel.SAME_FRAME, (
            f"同帧共现应判 SAME_FRAME，实际 {pair.level}"
        )
        assert pair.link_strength == 1.0 and pair.delta == 0.0
        # ESCALATE 档语义（ADR-0042 候选路由，ceiling 解除态）
        assert route_strength(
            EvidenceStrength.ESCALATE, ceiling_monitor_only=False
        ) == ("HIGH", "ESCALATE_COMMUNITY")
        combined = _combined_signals(r)
        assert len(combined) == 1
        assert combined[0].features["linked_pair_level"] == LinkLevel.SAME_FRAME.value
        # 原单模态 RAISED 保持活跃（状态机不被改写）
        assert any(
            s.source.value == "audio"
            and s.transition.value == "raised"
            and not s.features.get("combined_risk")
            for s in r.risk_signals
        )

    def test_g5_near_window_cross_frame_link(self):
        """跨帧近窗（Δt=0.4s ≤ 2.0s）：NEAR_WINDOW + 线性衰减强度 ∈ (0,1)。"""
        from home_perception.analysis.signal_temporal_linker import LinkLevel

        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        p._recent_vision_raised.append((4.6, 9, make_vision_raised_signal()))

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))

        assert len(r.linked_pairs) == 1
        assert r.linked_pairs[0].level is LinkLevel.NEAR_WINDOW
        assert 0.0 < r.linked_pairs[0].link_strength < 1.0

    def test_g5_out_of_window_never_links(self):
        """Δt > window → UNLINKED：即使双侧都有信号也绝不合并（反幻觉）。"""
        clock = ManualClock()
        p, _hp = build_gate_g_chain(clock)
        p._recent_vision_raised.append((0.0, 0, make_vision_raised_signal()))  # 远帧

        r = p.process_frame(_ctx(5.0, (make_telephone_event(),)))  # Δt=5.0 > 2.0

        assert r.linked_pairs == []
        assert _combined_signals(r) == []
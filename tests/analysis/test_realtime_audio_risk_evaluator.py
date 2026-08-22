"""RealTimeAudioRiskEvaluator 状态机与判级流水线测试（ADR-0042）。

覆盖：
- **D2/D3 参数悬空期**：默认配置下一切事件最多 MONITOR（观察记录），升级零可达；
- **D4 MONITOR ceiling**：全局闸门压制（pre_ceiling 审计字段可证）+ fallback kind
  （AUDIO_ANOMALY_OTHER）恒封顶（双保险，ceiling 解除也不放行）；
- **D5 状态机**：RAISED 去抖、CLEARED 静默超时、成对性（paired_signal_id 回填）、
  active_kinds 清理；
- **D6 ESCALATE 反幻觉双门控**：escalate_enabled **且** LinkedSignalPair 验证缺一不可；
- 判定维度：持续性（N/T）/ 多样性（M）/ 门槛（score/confidence）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from home_perception.analysis.evidence_strength import EvidenceStrength
from home_perception.analysis.realtime_audio_risk_evaluator import (
    EvidenceOutcome,
    RealTimeAudioRiskEvaluator,
)
from home_perception.analysis.risk_signal import SignalTransition, SourceModality
from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
from home_perception.core.config import AudioEvidenceConfig

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def make_event(
    *,
    kind: AudioPerceptionKind = AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
    score: float = 0.8,
    confidence: float = 0.7,
) -> AudioPerceptionEvent:
    return AudioPerceptionEvent(
        event_id=str(uuid4()),
        timestamp=100.0,
        kind=kind,
        score=score,
        confidence=confidence,
        source_segment_ids=["seg-1"],
    )


def make_evaluator(**cfg) -> RealTimeAudioRiskEvaluator:
    return RealTimeAudioRiskEvaluator(device_id="home_entry_01", config=AudioEvidenceConfig(**cfg))


# ============================================================================
# 悬空期安全默认 + ceiling
# ============================================================================


class TestSuspendedDefaults:
    def test_default_config_everything_monitor_no_signal(self):
        """默认悬空配置：单事件 → MONITOR、无信号、路由压回观察记录。"""
        ev = make_evaluator()
        out = ev.observe(make_event(), case_time=1.0)
        assert out.strength is EvidenceStrength.MONITOR
        assert out.signal is None
        assert out.routed == ("LOW", "MONITOR")
        assert ev.active_kinds == ()

    def test_ceiling_blocks_raise_with_pre_ceiling_audit(self):
        """D4：参数满足 RAISE 但 ceiling 开启 → 压回 MONITOR；压制前判级留审计。"""
        ev = make_evaluator(raise_min_count=2, raise_window_s=10.0, ceiling_monitor_only=True)
        ev.observe(make_event(), case_time=1.0)
        out = ev.observe(make_event(), case_time=2.0)
        assert out.pre_ceiling_strength is EvidenceStrength.RAISE
        assert out.strength is EvidenceStrength.MONITOR
        assert out.signal is None  # 封顶期永不产 RAISED
        assert any("ceiling" in r for r in out.reasons)

    def test_fallback_kind_capped_even_when_ceiling_lifted(self):
        """双保险：ceiling 解除后 fallback 兜底 kind 仍恒封顶 MONITOR。"""
        ev = make_evaluator(ceiling_monitor_only=False)
        out = ev.observe(make_event(kind=AudioPerceptionKind.AUDIO_ANOMALY_OTHER), case_time=1.0)
        assert out.strength is EvidenceStrength.MONITOR


# ============================================================================
# 判级维度（ceiling 解除态验证内部逻辑）
# ============================================================================


class TestGradingDimensions:
    def test_insufficient_by_score_threshold(self):
        ev = make_evaluator(monitor_score_threshold=0.9, ceiling_monitor_only=False)
        out = ev.observe(make_event(score=0.5), case_time=1.0)
        assert out.strength is EvidenceStrength.INSUFFICIENT
        assert out.routed is None

    def test_insufficient_by_confidence_threshold(self):
        ev = make_evaluator(monitor_confidence_threshold=0.9, ceiling_monitor_only=False)
        out = ev.observe(make_event(confidence=0.3), case_time=1.0)
        assert out.strength is EvidenceStrength.INSUFFICIENT

    def test_raise_on_persistence_then_debounced(self):
        """持续性维度：窗口计数 ≥ N → RAISED；后续同 kind 去抖不重复 emit。"""
        ev = make_evaluator(
            raise_min_count=2, raise_window_s=10.0, ceiling_monitor_only=False
        )
        out1 = ev.observe(make_event(), case_time=1.0)
        assert out1.strength is EvidenceStrength.MONITOR  # 单事件不升级
        out2 = ev.observe(make_event(), case_time=2.0)
        assert out2.strength is EvidenceStrength.RAISE
        assert out2.signal is not None
        assert out2.signal.transition is SignalTransition.RAISED
        assert SourceModality.AUDIO is out2.signal.source
        out3 = ev.observe(make_event(), case_time=3.0)
        assert out3.strength is EvidenceStrength.RAISE
        assert out3.signal is None  # 去抖
        assert len(ev.active_kinds) == 1

    def test_notify_on_kind_diversity(self):
        """多样性维度：窗口内独立 kind 数 ≥ M → NOTIFY。"""
        ev = make_evaluator(notify_min_kinds=2, raise_window_s=10.0, ceiling_monitor_only=False)
        ev.observe(make_event(kind=AudioPerceptionKind.AUDIO_VOICE_RAISED), case_time=1.0)
        out = ev.observe(make_event(kind=AudioPerceptionKind.AUDIO_SPEECH_RAPID), case_time=1.5)
        assert out.strength is EvidenceStrength.NOTIFY
        assert out.routed == ("MEDIUM", "NOTIFY_FAMILY")

    def test_escalate_requires_both_switch_and_pair_verification(self):
        """D6 双门控：开关与 LinkedSignalPair 验证缺一不可。"""
        off = make_evaluator(escalate_enabled=False, ceiling_monitor_only=False)
        out = off.observe(make_event(), case_time=1.0, linked_pair_verified=True)
        assert out.strength is not EvidenceStrength.ESCALATE

        on = make_evaluator(escalate_enabled=True, ceiling_monitor_only=False)
        out_unverified = on.observe(make_event(), case_time=1.0, linked_pair_verified=False)
        assert out_unverified.strength is not EvidenceStrength.ESCALATE

        on2 = make_evaluator(escalate_enabled=True, ceiling_monitor_only=False)
        out_ok = on2.observe(make_event(), case_time=1.0, linked_pair_verified=True)
        assert out_ok.strength is EvidenceStrength.ESCALATE
        assert out_ok.routed == ("HIGH", "ESCALATE_COMMUNITY")


# ============================================================================
# D5 状态机：CLEARED 成对性
# ============================================================================


class TestStateMachinePairing:
    def _raise_first(self, ev: RealTimeAudioRiskEvaluator, cfg_kwargs: dict) -> str:
        for i in range(cfg_kwargs["raise_min_count"]):
            ev.observe(make_event(), case_time=float(i + 1))
        raised = ev.active_kinds
        assert raised, "前置：应已有活跃 RAISED"
        return raised[0].value

    def test_cleared_after_silence_timeout_paired(self):
        """每个 RAISED 恰配一个 CLEARED（paired_signal_id 回填），状态清理防泄漏。"""
        ev = make_evaluator(
            raise_min_count=2,
            raise_window_s=10.0,
            clear_timeout_s=5.0,
            ceiling_monitor_only=False,
        )
        self._raise_first(ev, {"raise_min_count": 2})
        # RAISED 的 signal_id 经 CLEARED.paired_signal_id 承载（去抖后无法从 observe
        # 返回值再取，配对关系本身即契约断言点）
        cleared = ev.tick(now_case_time=100.0)  # 远超 clear_timeout_s
        assert len(cleared) == 1
        sig = cleared[0]
        assert sig.transition is SignalTransition.CLEARED
        assert sig.paired_signal_id is not None
        assert ev.active_kinds == ()

    def test_no_clear_before_timeout(self):
        ev = make_evaluator(
            raise_min_count=2, raise_window_s=10.0, clear_timeout_s=5.0, ceiling_monitor_only=False
        )
        self._raise_first(ev, {"raise_min_count": 2})
        assert ev.tick(now_case_time=6.0) == []  # 静默 4s < 5s 超时
        assert len(ev.active_kinds) == 1


# ============================================================================
# 输入守卫 / 产物契约
# ============================================================================


class TestGuards:
    def test_non_event_rejected(self):
        ev = make_evaluator()
        with pytest.raises(TypeError):
            ev.observe("not-an-event", case_time=1.0)  # type: ignore[arg-type]

    def test_outcome_forbids_strength_above_pre_ceiling(self):
        with pytest.raises(ValueError, match="不得高于"):
            EvidenceOutcome(
                event_id="e",
                kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
                strength=EvidenceStrength.RAISE,
                pre_ceiling_strength=EvidenceStrength.MONITOR,
                signal=None,
                routed=None,
                reasons=(),
            )
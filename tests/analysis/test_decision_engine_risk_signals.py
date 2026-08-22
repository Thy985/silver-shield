"""DecisionEngine.evaluate risk_signals 透传（ADR-0040 运行时接线 · R3 补链）。

Pre-flight R3 断点修复的验收面：
- ``evaluate(percs, risk_signals=...)`` 把信号以原生形态透传
  ``DecisionInput.risk_signals``（此前 engine 构造 DecisionInput 时恒缺该字段，
  policy 永远读到空元组——"假通电"断点）；
- 缺省空元组 = 向后兼容（视觉规则路径零行为变化）；
- list 输入被归一为 tuple（DecisionInput C2 不可变容器守卫要求）；
- 端到端：真实 RuleBasedDecisionPolicy 经 engine 路径可消费信号
  （meta["risk_signals"] 摘要可见——证明链路贯通而非仅签名存在）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from home_perception.analysis.decision_contract import DecisionInput
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.risk_signal import RiskSignal
from home_perception.analysis.warning import WarningEvent

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


# ============================================================================
# Helpers（与 test_decision_risk_signals.py 同风格，保持自包含）
# ============================================================================


class CapturingPolicy:
    """捕获 DecisionInput 的最小 policy stub（不产 Warning，只记录输入）。"""

    def __init__(self) -> None:
        self.inputs: list[DecisionInput] = []

    def decide(self, input: DecisionInput) -> WarningEvent | None:
        self.inputs.append(input)
        return None

    def bind_trace_span(self, span: Any) -> None:
        # engine 持有 span 生命周期（Slice C），policy 只需接受绑定/解绑
        self.span = span


def make_perception() -> PerceptionEvent:
    return PerceptionEvent(
        device_id="home_entry_01",
        event_type="abnormal_dwell",
        score=0.5,
        visitor_id=uuid.uuid4(),
        source_video="cam01",
        timestamp=1000.0,
        meta={"rule": "TestRule"},
        created_at=NOW,
    )


def make_signal(
    *,
    created_at: datetime = NOW,
    transition: str = "raised",
    source: str = "audio",
) -> RiskSignal:
    return RiskSignal(
        signal_id=str(uuid.uuid4()),
        subject_type="visitor",
        subject_id="vi-1",
        category="communication",
        source=source,
        transition=transition,
        features={"audio_score": 0.8},
        track_id=1,
        visitor_instance_id="vi-1",
        created_at=created_at,
    )


# ============================================================================
# 透传契约
# ============================================================================


class TestSignalPassThrough:
    def test_signals_forwarded_to_decision_input(self):
        """risk_signals 以原生形态进入 DecisionInput（R3 断点消除的直接证明）。"""
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=lambda: NOW)
        sig_a = make_signal()
        sig_b = make_signal(created_at=datetime(2026, 8, 22, 12, 0, 1, tzinfo=UTC))
        w = engine.evaluate([make_perception()], risk_signals=(sig_a, sig_b))
        assert w is None  # CapturingPolicy 不产 Warning
        assert len(policy.inputs) == 1
        assert policy.inputs[0].risk_signals == (sig_a, sig_b)

    def test_default_empty_tuple_backward_compatible(self):
        """缺省无信号：DecisionInput.risk_signals 为空元组（视觉路径零行为变化）。"""
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=lambda: NOW)
        engine.evaluate([make_perception()])
        assert policy.inputs[0].risk_signals == ()

    def test_list_input_normalized_to_tuple(self):
        """list 便捷入参被归一为 tuple（满足 C2 不可变容器守卫，不抛 TypeError）。"""
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=lambda: NOW)
        sig = make_signal()
        engine.evaluate([make_perception()], risk_signals=[sig])
        assert policy.inputs[0].risk_signals == (sig,)
        assert isinstance(policy.inputs[0].risk_signals, tuple)

    def test_signals_do_not_become_trigger_events(self):
        """语义边界：信号不混入 trigger_events（D3 防幻觉路径的结构性隔离）。"""
        policy = CapturingPolicy()
        engine = DecisionEngine(elder_id="elder_001", policy=policy, now_provider=lambda: NOW)
        percs = [make_perception()]
        sig = make_signal()
        engine.evaluate(percs, risk_signals=(sig,))
        assert policy.inputs[0].trigger_events == tuple(percs)
        assert len(policy.inputs[0].trigger_events) == 1


# ============================================================================
# 端到端（真实 policy 消费——假通电防护解除）
# ============================================================================


class TestEndToEndWithRealPolicy:
    def test_real_policy_sees_signals_via_engine_path(self):
        """真实 RuleBasedDecisionPolicy 经 engine 全链路消费信号：
        Warning.meta["risk_signals"] 摘要可见（engine→policy 链路贯通的可观测证据）。"""

        engine = DecisionEngine(elder_id="elder_001", now_provider=lambda: NOW)
        sig = make_signal()
        w = engine.evaluate([make_perception()], risk_signals=(sig,))
        assert w is not None
        summary = w.meta["risk_signals"]
        assert summary["count"] == 1
        assert summary["raised"] == 1
        assert summary["signal_ids"] == [sig.signal_id]

    def test_cleared_signal_counted_not_reasoned(self):
        """CLEARED 仅计数不产生原因（policy D6 语义经 engine 路径保持一致）。"""

        engine = DecisionEngine(elder_id="elder_001", now_provider=lambda: NOW)
        cleared = make_signal(transition="cleared")
        w = engine.evaluate([make_perception()], risk_signals=(cleared,))
        assert w is not None
        assert w.meta["risk_signals"]["raised"] == 0
        assert w.meta["risk_signals"]["count"] == 1
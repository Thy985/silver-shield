"""DecisionInput.risk_signals 一等输入契约 + policy 最小消费（ADR-0040）。

覆盖 ADR-0040 验收面：
- **D1/D2**：C7 白名单临时扩展 5→6（**6 是硬顶**）；``risk_signals`` 默认空元组合法；
  容器与元素类型守卫。
- **D3 语义边界**：RiskSignal 以**原生形态**进入决策输入（不经 signal_adapter 翻译，
  结构性消除 audio→visit_pending_verify 幻觉路径）；决策产物仍只在 WarningEvent
  （C1 回归）。
- **D4 确定性**：按 ``(created_at, signal_id)`` 升序稳定排序；序列化往返稳定；
  旧 payload 缺键向后兼容为 ``()``。
- **D6 最小消费（假通电防护解除）**：RuleBasedDecisionPolicy 非静默消费——
  RAISED 进 ``reason_summary``、全量进 ``meta["risk_signals"]`` 摘要；
  CLEARED 仅计数不产生原因；**不参与 level/action/perception_score 判定**
  （Evidence Strength → Action 的 modality-aware routing 归 ADR-0042）；
  纯信号无视觉触发仍返回 None（语义同现状）。
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.analysis.decision_contract import (
    DECISION_INPUT_FIELD_WHITELIST,
    DECISION_INPUT_FORBIDDEN_FIELDS,
    DecisionInput,
)
from home_perception.analysis.decision_policy import DecisionContext, RuleBasedDecisionPolicy
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.risk_signal import RiskSignal

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


# ============================================================================
# Helpers（与 test_decision_contract.py 同风格，保持本文件自包含）
# ============================================================================


def make_ctx() -> DecisionContext:
    return DecisionContext(elder_id="elder_001", now=NOW, extra={"tenant": "demo"})


def make_perception(event_type: str = "abnormal_dwell") -> PerceptionEvent:
    return PerceptionEvent(
        device_id="home_entry_01",
        event_type=event_type,
        score=0.5,
        visitor_id=uuid.uuid4(),
        source_video="cam01",
        timestamp=1000.0,
        meta={"rule": f"TestRule_{event_type}"},
        created_at=NOW,
    )


def make_signal(
    *,
    created_at: datetime = NOW,
    signal_id: str | None = None,
    source: str = "audio",
    transition: str = "raised",
    category: str = "communication",
) -> RiskSignal:
    return RiskSignal(
        signal_id=signal_id or str(uuid.uuid4()),
        subject_type="visitor",
        subject_id="vi-1",
        category=category,
        source=source,
        transition=transition,
        features={"audio_score": 0.8},
        track_id=1,
        visitor_instance_id="vi-1",
        created_at=created_at,
    )


# ============================================================================
# D1/D2 —— 字段与白名单（6 硬顶）
# ============================================================================


class TestContractField:
    def test_whitelist_contains_risk_signals_and_hard_capped_at_six(self):
        assert "risk_signals" in DECISION_INPUT_FIELD_WHITELIST
        assert len(DECISION_INPUT_FIELD_WHITELIST) == 6
        names = {f.name for f in fields(DecisionInput)}
        assert names == DECISION_INPUT_FIELD_WHITELIST

    def test_default_empty_tuple_is_legal(self):
        """无信号语义 =「本次决策无实时风险信号」（对齐 Memory 可缺席原则）。"""
        di = DecisionInput(trigger_events=(), decision_context=make_ctx())
        assert di.risk_signals == ()

    def test_list_container_rejected(self):
        with pytest.raises(TypeError, match="tuple"):
            DecisionInput(
                trigger_events=(),
                decision_context=make_ctx(),
                risk_signals=[make_signal()],  # type: ignore[arg-type]
            )

    def test_non_risksignal_element_rejected(self):
        with pytest.raises(TypeError, match="RiskSignal"):
            DecisionInput(
                trigger_events=(),
                decision_context=make_ctx(),
                risk_signals=("not-a-signal",),  # type: ignore[arg-type]
            )


# ============================================================================
# D3 —— 原生形态 + 语义边界
# ============================================================================


class TestNativeFormAndBoundary:
    def test_audio_signal_enters_natively(self):
        """audio RiskSignal 以原生形态进入输入（不经 PerceptionEvent 翻译）。"""
        sig = make_signal()
        di = DecisionInput(trigger_events=(), decision_context=make_ctx(), risk_signals=(sig,))
        assert di.risk_signals == (sig,)
        assert di.risk_signals[0].source.value == "audio"

    def test_features_evidence_keys_allowed_decision_keys_blocked(self):
        """D3：features 允许证据强度键；决策语义键在 DecisionInput 边界被结构性拦截。

        分层互补：ADR-0021 黑名单拦「犯罪认定字段」（RiskSignal.__post_init__）；
        ADR-0040 D3 守卫拦「决策语义键」（DecisionInput.__post_init__）——二者不重叠。
        """
        sig = make_signal()
        sig.features["confidence"] = 0.7  # 证据强度描述键：合法
        assert "risk_level" not in sig.features
        smuggled = RiskSignal(
            signal_id=str(uuid.uuid4()),
            subject_type="visitor",
            subject_id="vi-1",
            category="communication",
            source="audio",
            transition="raised",
            features={"recommended_action": "NOTIFY_FAMILY"},
            created_at=NOW,
        )  # ADR-0021 层不拦（非犯罪认定字段）……
        with pytest.raises(ValueError, match="决策语义键"):
            DecisionInput(
                trigger_events=(),
                decision_context=make_ctx(),
                risk_signals=(smuggled,),  # ……但进决策输入即拦（防 C1 失效）
            )

    def test_serialized_input_carries_no_decision_fields(self):
        payload = DecisionInput(
            trigger_events=(make_perception(),),
            decision_context=make_ctx(),
            risk_signals=(make_signal(),),
        ).to_dict()
        assert not (set(payload) & DECISION_INPUT_FORBIDDEN_FIELDS)


# ============================================================================
# D4 —— 确定性（排序 + 往返）
# ============================================================================


class TestDeterminism:
    def test_sorted_by_created_at_then_signal_id(self):
        late = make_signal(created_at=NOW + timedelta(seconds=5), signal_id=str(uuid.uuid4()))
        early_b = make_signal(created_at=NOW, signal_id=str(uuid.uuid4()))
        early_a = make_signal(created_at=NOW, signal_id=str(uuid.uuid4()))
        # 故意乱序传入
        di = DecisionInput(
            trigger_events=(),
            decision_context=make_ctx(),
            risk_signals=(late, early_b, early_a),
        )
        keys = [(s.created_at, s.signal_id) for s in di.risk_signals]
        assert keys == sorted(keys)  # (created_at, signal_id) 升序全序
        assert di.risk_signals[-1].signal_id == late.signal_id  # 最晚的排最后

    def test_roundtrip_stable_with_signals(self):
        original = DecisionInput(
            trigger_events=(make_perception(),),
            decision_context=make_ctx(),
            risk_signals=(make_signal(), make_signal(created_at=NOW + timedelta(seconds=1))),
        )
        once = DecisionInput.from_dict(original.to_dict()).to_dict()
        twice = DecisionInput.from_dict(once).to_dict()
        assert once == twice
        assert len(twice["risk_signals"]) == 2

    def test_from_dict_without_key_is_backward_compatible(self):
        """ADR-0040 之前落库的 payload 缺 risk_signals 键 → 空元组。"""
        legacy = {
            "trigger_events": [],
            "decision_context": {
                "elder_id": "elder_001",
                "now": NOW.isoformat(),
                "extra": {},
            },
        }
        di = DecisionInput.from_dict(legacy)
        assert di.risk_signals == ()


# ============================================================================
# D6 —— policy 最小消费（非静默 + 判定不变）
# ============================================================================


class TestPolicyMinimalConsumption:
    def test_no_signals_meta_has_no_risk_signals_key(self):
        """空信号路径与旧版逐字一致：meta 不新增键（零行为变化基线）。"""
        w = RuleBasedDecisionPolicy().decide(
            DecisionInput(
                trigger_events=(make_perception(),),
                decision_context=make_ctx(),
            )
        )
        assert w is not None
        assert "risk_signals" not in w.meta

    def test_raised_signal_visible_in_reasons_and_meta(self):
        sig = make_signal()
        w = RuleBasedDecisionPolicy().decide(
            DecisionInput(
                trigger_events=(make_perception(),),
                decision_context=make_ctx(),
                risk_signals=(sig,),
            )
        )
        assert w is not None
        # 人话原因追加（RAISED 才有）
        assert any("communication(audio)" in r for r in w.reason_summary)
        # meta 摘要可审计
        summary = w.meta["risk_signals"]
        assert summary["count"] == 1
        assert summary["raised"] == 1
        assert summary["sources"] == ["audio"]
        assert summary["signal_ids"] == [sig.signal_id]

    def test_cleared_only_counted_not_reasoned(self):
        cleared = make_signal(transition="cleared")
        w = RuleBasedDecisionPolicy().decide(
            DecisionInput(
                trigger_events=(make_perception(),),
                decision_context=make_ctx(),
                risk_signals=(cleared,),
            )
        )
        assert w is not None
        assert not any("实时风险信号" in r for r in w.reason_summary)
        assert w.meta["risk_signals"]["raised"] == 0
        assert w.meta["risk_signals"]["count"] == 1

    def test_signals_do_not_change_verdict_fields(self):
        """判定三件套（level/action/score）不受信号影响——modality-aware routing 归 ADR-0042。"""
        events = (make_perception("abnormal_dwell"),)
        base = RuleBasedDecisionPolicy().decide(
            DecisionInput(trigger_events=events, decision_context=make_ctx())
        )
        with_sig = RuleBasedDecisionPolicy().decide(
            DecisionInput(
                trigger_events=events,
                decision_context=make_ctx(),
                risk_signals=(make_signal(),),
            )
        )
        assert base is not None and with_sig is not None
        assert with_sig.risk_level == base.risk_level
        assert with_sig.recommended_action == base.recommended_action
        assert with_sig.perception_score == base.perception_score

    def test_signal_only_without_triggers_returns_none(self):
        """纯信号无视觉触发仍返回 None（现状保持；独立触发归 ADR-0042 routing）。"""
        w = RuleBasedDecisionPolicy().decide(
            DecisionInput(trigger_events=(), decision_context=make_ctx(), risk_signals=(make_signal(),))
        )
        assert w is None
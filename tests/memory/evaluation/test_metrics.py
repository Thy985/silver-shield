"""metrics 纯函数单测（E-1a）。

覆盖四指标 + severity 映射 + Early Detection 定义；含 **变异验证**：故意移除/篡改某
属性后断言指标翻转，证明指标真正绑定到对应契约字段（守测试有效性铁律）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from home_perception.memory.consumer.contracts import ReasoningResult, SourceRef
from home_perception.memory.evaluation.ground_truth import GroundTruthRecord
from home_perception.memory.evaluation.metrics import (
    EarlyDetectionResult,
    compute_lead_time,
    evaluate_case,
    hint_severity,
    metric_fn,
    metric_fp,
    metric_q1_grounded_gain,
    metric_q2_historical_reference,
    metric_q3_pattern_grounding,
)

# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
GT_001 = GroundTruthRecord(
    case_id="case_001_repeat_visitor",
    category="repeat_visitor",
    expected_pattern=("repeated_visit",),
    required_evidence=(
        "historical_context[].record_id = ep-a001-d1",
        "visitor_profile.visit_count = 5",
        "risk_pattern.tags = repeated_visit",
    ),
    acceptable_hint=("MONITOR", "NOTIFY_FAMILY"),
)


def _baseline_result() -> ReasoningResult:
    """无记忆 Baseline 臂（仅 current_event 锚点）。"""
    return ReasoningResult(
        findings=(
            "当前事件 cur-001（类型 risk_signal），实时风险等级 MEDIUM，行为标记 night",
        ),
        explanation=(
            "无可用历史记忆：本次事件孤立，参考推理仅基于当前事实。"
            "建议接入更多历史以建立长期画像后再评估。"
        ),
        suggested_action_hint="NOTIFY_FAMILY",
        source_refs=(SourceRef(source="current_event", ref="cur-001"),),
    )


def _memory_result() -> ReasoningResult:
    """带记忆 Memory 臂（case_001 形状）。"""
    return ReasoningResult(
        findings=(
            "当前事件 cur-001（类型 risk_signal），实时风险等级 MEDIUM，行为标记 night",
            "访客 A001 历史到访 5 次，夜间到访占比 100%，置信度 weak_pattern（身份已确认=False）",
            "发现风险模式：repeated_visit（置信度 weak_pattern）",
            "可召回历史记录 5 条",
        ),
        explanation=(
            "结合该访客历史画像、识别到风险模式、召回 5 条历史记录。"
            " 每条发现均溯源至 ReasoningInput 字段（见 source_refs）。"
        ),
        suggested_action_hint="NOTIFY_FAMILY",
        source_refs=(
            SourceRef(source="current_event", ref="cur-001"),
            SourceRef(
                source="visitor_profile",
                ref="A001",
                detail="visit_count=5,night_visit_ratio=1.0,confidence=weak_pattern",
            ),
            SourceRef(source="risk_pattern", ref="repeated_visit"),
            SourceRef(source="historical_context", ref="ep-a001-d1", detail="n=5"),
        ),
    )


# ---------------------------------------------------------------------------
# Q1 — Grounded Finding Gain
# ---------------------------------------------------------------------------
def test_q1_pass_when_memory_has_grounded_gain():
    assert metric_q1_grounded_gain(_memory_result(), _baseline_result(), GT_001) is True


def test_q1_false_when_no_gain():
    # Memory == Baseline → 无新增发现
    assert metric_q1_grounded_gain(_baseline_result(), _baseline_result(), GT_001) is False


def test_q1_false_without_historical_context_anchor():
    """变异：移除 historical_context 溯源锚点 → Q1 翻 False（杜绝「话多式」膨胀）。"""
    mem = _memory_result()
    stripped = ReasoningResult(
        findings=mem.findings,
        explanation=mem.explanation,
        suggested_action_hint=mem.suggested_action_hint,
        source_refs=tuple(s for s in mem.source_refs if s.source != "historical_context"),
    )
    assert metric_q1_grounded_gain(stripped, _baseline_result(), GT_001) is False


def test_q1_false_when_expected_pattern_uncovered():
    """变异：Memory 臂不覆盖 expected_pattern（findings 文本 + source_ref 均移除）→ Q1 翻 False。"""
    mem = _memory_result()
    no_pattern = ReasoningResult(
        findings=tuple(f for f in mem.findings if "repeated_visit" not in f),
        explanation=mem.explanation,
        suggested_action_hint=mem.suggested_action_hint,
        source_refs=tuple(s for s in mem.source_refs if s.source != "risk_pattern"),
    )
    assert metric_q1_grounded_gain(no_pattern, _baseline_result(), GT_001) is False


# ---------------------------------------------------------------------------
# Q2 — 历史引用
# ---------------------------------------------------------------------------
def test_q2_pass_with_historical_anchor():
    assert metric_q2_historical_reference(_memory_result()) is True


def test_q2_false_without_historical_anchor():
    assert metric_q2_historical_reference(_baseline_result()) is False


# ---------------------------------------------------------------------------
# Q3 — Pattern Grounding（证据链）
# ---------------------------------------------------------------------------
def test_q3_pass_with_grounded_chain():
    assert metric_q3_pattern_grounding(_memory_result()) is True


def test_q3_false_without_historical_anchor():
    assert metric_q3_pattern_grounding(_baseline_result()) is False


def test_q3_false_when_explanation_only_generic_and_no_value():
    """变异：explanation 仅复述套话、锚点无具体值 → Q3 翻 False（§4.1 反例）。"""
    mem = _memory_result()
    hollow = ReasoningResult(
        findings=mem.findings,
        explanation="该访客存在历史行为模式。",
        suggested_action_hint=mem.suggested_action_hint,
        source_refs=tuple(
            SourceRef(source=s.source, ref=None, detail=None) for s in mem.source_refs
        ),
    )
    assert metric_q3_pattern_grounding(hollow) is False


# ---------------------------------------------------------------------------
# FP — False Positive
# ---------------------------------------------------------------------------
def test_fp_pass_when_both_within_acceptable():
    assert metric_fp(_memory_result(), _baseline_result(), GT_001) is True


def test_fp_fail_when_memory_exceeds_acceptable():
    """变异：Memory 臂 hint 越界（ESCALATE_COMMUNITY > 上限 NOTIFY_FAMILY）→ FP 翻 False。"""
    mem = _memory_result()
    over = ReasoningResult(
        findings=mem.findings,
        explanation=mem.explanation,
        suggested_action_hint="ESCALATE_COMMUNITY",
        source_refs=mem.source_refs,
    )
    assert metric_fp(over, _baseline_result(), GT_001) is False


# ---------------------------------------------------------------------------
# FN — False Negative
# ---------------------------------------------------------------------------
def test_fn_memory_zero_baseline_full():
    fn_m, fn_b = metric_fn(_memory_result(), _baseline_result(), GT_001)
    assert fn_m == 0
    assert fn_b == 1  # Baseline 无历史 → 全缺


def test_fn_evidence_grounding_enforced():
    """变异：required_evidence 错配（record_id 不符）→ Memory 臂 evidence 失败 → FN 翻 1。"""
    bad_gt = GroundTruthRecord(
        case_id="case_001_repeat_visitor",
        category="repeat_visitor",
        expected_pattern=("repeated_visit",),
        required_evidence=(
            "historical_context[].record_id = ep-WRONG-id",
            "visitor_profile.visit_count = 5",
            "risk_pattern.tags = repeated_visit",
        ),
        acceptable_hint=("MONITOR", "NOTIFY_FAMILY"),
    )
    fn_m, _ = metric_fn(_memory_result(), _baseline_result(), bad_gt)
    assert fn_m == 1


# ---------------------------------------------------------------------------
# severity 映射（§4.2 冻结）
# ---------------------------------------------------------------------------
def test_severity_mapping():
    assert hint_severity(None) == 0
    assert hint_severity("MONITOR") == 1
    assert hint_severity("NOTIFY_FAMILY") == 2
    assert hint_severity("ESCALATE_COMMUNITY") == 3


def test_severity_rejects_unknown():
    import pytest

    with pytest.raises(ValueError):
        hint_severity("FOO")


# ---------------------------------------------------------------------------
# Early Detection（§4.4）
# ---------------------------------------------------------------------------
def test_compute_lead_time_memory_earlier():
    b = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
    m = datetime(2026, 7, 8, 14, 30, tzinfo=UTC)
    res = compute_lead_time(b, m)
    assert res.status == "computed"
    assert res.lead_time_minutes == 30.0  # >0 提前


def test_compute_lead_time_equal():
    ts = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
    res = compute_lead_time(ts, ts)
    assert res.lead_time_minutes == 0.0


def test_compute_lead_time_memory_later_is_negative():
    b = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
    m = datetime(2026, 7, 8, 15, 20, tzinfo=UTC)
    res = compute_lead_time(b, m)
    assert res.lead_time_minutes == -20.0  # <0 退化


def test_compute_lead_time_missing_detection():
    ts = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
    res = compute_lead_time(None, ts)
    assert res.status == "missing_detection"


def test_evaluate_case_marks_early_detection_na():
    ev = evaluate_case(_memory_result(), _baseline_result(), GT_001)
    assert isinstance(ev.early_detection, EarlyDetectionResult)
    assert ev.early_detection.status == "na"


# ---------------------------------------------------------------------------
# Hard Gate（§9 E-1A）
# ---------------------------------------------------------------------------
def test_evaluate_case_hard_gate_pass():
    ev = evaluate_case(_memory_result(), _baseline_result(), GT_001)
    assert ev.q1 and ev.q2 and ev.q3 and ev.fp
    assert ev.fn_m < ev.fn_b
    assert ev.hard_gate_pass is True

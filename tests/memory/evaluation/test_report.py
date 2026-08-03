"""report 纯函数单测（DESIGN-memory-evaluation.md §8）。

覆盖：t 区间统计汇总、四 term 归一化边界、Early Detection 缺失时的权重重归一化、
Hard Gate 汇总、渲染与序列化。含**变异验证**：故意翻转某属性，确认指标随之翻转，
避免「断言恒真」的无效测试（Slice 6 review 教训）。
"""

from __future__ import annotations

import json
import math

import pytest

from home_perception.memory.evaluation.metrics import CaseEvaluation, EarlyDetectionResult
from home_perception.memory.evaluation.report import (
    BASE_WEIGHTS,
    build_report,
    compute_memory_value_score,
    compute_score_terms,
    early_detection_term,
    explanation_term,
    fn_term,
    fp_term,
    paired_delta_summary,
    render_markdown,
    report_to_dict,
    summarize,
    summarize_hard_gate,
    t_critical_95,
    write_report,
)


def _ev(
    case_id: str = "case_x",
    *,
    q1: bool = True,
    q2: bool = True,
    q3: bool = True,
    fp: bool = True,
    fn_m: int = 0,
    fn_b: int = 1,
    fp_excess: int = 0,
    early: EarlyDetectionResult | None = None,
    hard_gate_pass: bool | None = None,
) -> CaseEvaluation:
    ed = early or EarlyDetectionResult.na()
    gate = hard_gate_pass if hard_gate_pass is not None else (q1 and q2 and q3 and fp and fn_m < fn_b)
    return CaseEvaluation(
        case_id=case_id,
        q1=q1,
        q2=q2,
        q3=q3,
        fp=fp,
        fn_m=fn_m,
        fn_b=fn_b,
        early_detection=ed,
        hard_gate_pass=gate,
        notes=(),
        fp_excess=fp_excess,
    )


# ---------------------------------------------------------------------------
# §8.1 统计汇总
# ---------------------------------------------------------------------------
def test_summarize_empty_is_no_information():
    s = summarize([])
    assert (s.n, s.mean, s.std) == (0, 0.0, 0.0)
    assert s.ci95_low is None and s.ci95_high is None


def test_summarize_single_sample_has_no_ci():
    s = summarize([2.0])
    assert s.n == 1 and s.mean == 2.0 and s.std == 0.0
    assert s.ci95_low is None and s.ci95_high is None


def test_summarize_known_values():
    s = summarize([1.0, 2.0, 3.0])
    assert s.n == 3
    assert s.mean == pytest.approx(2.0)
    assert s.std == pytest.approx(1.0)  # ddof=1
    half = t_critical_95(2) * 1.0 / math.sqrt(3)
    assert s.ci95_low == pytest.approx(2.0 - half)
    assert s.ci95_high == pytest.approx(2.0 + half)


def test_summarize_constant_series_has_zero_width_ci():
    s = summarize([2.0, 2.0, 2.0])
    assert s.std == pytest.approx(0.0)
    assert s.ci95_low == pytest.approx(2.0)
    assert s.ci95_high == pytest.approx(2.0)


def test_t_critical_lookup_and_conservative_fallback():
    assert t_critical_95(2) == pytest.approx(4.303)
    # 表外 df 取最近的较小 df（区间更宽 = 更保守）
    assert t_critical_95(35) == pytest.approx(t_critical_95(30))
    assert t_critical_95(500) == pytest.approx(t_critical_95(100))
    with pytest.raises(ValueError):
        t_critical_95(0)


def test_paired_delta_summary_uses_fn_b_minus_fn_m():
    stat = paired_delta_summary([_ev(fn_m=0, fn_b=2), _ev(fn_m=1, fn_b=2)])
    assert stat.mean == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# §8.2 四 term
# ---------------------------------------------------------------------------
def test_fn_term_full_credit_when_memory_clears_all():
    assert fn_term([_ev(fn_m=0, fn_b=2)]) == pytest.approx(1.0)


def test_fn_term_neutral_zero_when_both_zero():
    """FN_B=0 ∧ FN_M=0 → 0（无信息，中性），不是满分。"""
    assert fn_term([_ev(fn_m=0, fn_b=0)]) == pytest.approx(0.0)


def test_fn_term_clamped_when_memory_worse():
    """变异：Memory 反而更多漏报 → 截 0，绝不给负分奖励。"""
    assert fn_term([_ev(fn_m=3, fn_b=1)]) == pytest.approx(0.0)


def test_fn_term_partial_credit_and_mean():
    assert fn_term([_ev(fn_m=1, fn_b=2), _ev(fn_m=0, fn_b=2)]) == pytest.approx(0.75)


def test_explanation_term_excludes_q1():
    """只含 Q2/Q3；Q1 翻转不应改变 Explanation_term（避免与 FN_term 重复计权）。"""
    base = explanation_term([_ev(q1=True)])
    mutated = explanation_term([_ev(q1=False)])
    assert base == pytest.approx(1.0)
    assert mutated == pytest.approx(base)


def test_explanation_term_half_credit_on_q3_failure():
    assert explanation_term([_ev(q2=True, q3=False)]) == pytest.approx(0.5)
    assert explanation_term([_ev(q2=False, q3=False)]) == pytest.approx(0.0)


def test_fp_term_discounts_by_severity_excess():
    assert fp_term([_ev(fp_excess=0)]) == pytest.approx(1.0)
    assert fp_term([_ev(fp_excess=1)]) == pytest.approx(2 / 3)
    assert fp_term([_ev(fp_excess=3)]) == pytest.approx(0.0)
    # 超出满量程仍截断在 0，不产生负分
    assert fp_term([_ev(fp_excess=9)]) == pytest.approx(0.0)


def test_early_detection_term_is_none_when_all_na():
    assert early_detection_term([_ev(), _ev()]) is None


def test_early_detection_term_scales_by_window():
    ev = _ev(early=EarlyDetectionResult.computed(30.0, 1))
    assert early_detection_term([ev]) == pytest.approx(0.5)


def test_early_detection_term_clamped_both_ends():
    high = _ev(early=EarlyDetectionResult.computed(120.0, 2))
    low = _ev(early=EarlyDetectionResult.computed(-30.0, -1))
    assert early_detection_term([high]) == pytest.approx(1.0)
    assert early_detection_term([low]) == pytest.approx(0.0)


def test_early_detection_term_missing_detection_is_worst():
    ev = _ev(early=EarlyDetectionResult.missing_detection("memory"))
    assert early_detection_term([ev]) == pytest.approx(0.0)


def test_early_detection_term_ignores_na_cases_in_mean():
    """na 从分母剔除（未测量 ≠ 无提前量）。"""
    mixed = [_ev(), _ev(early=EarlyDetectionResult.computed(60.0, 1))]
    assert early_detection_term(mixed) == pytest.approx(1.0)


def test_early_detection_term_baseline_missing_is_positive():
    """评审 issue 1：Baseline 缺失而 Memory 检出 → 最强正向提前量（1.0），非 0。"""
    ev = _ev(early=EarlyDetectionResult.missing_detection("baseline"))
    assert early_detection_term([ev]) == pytest.approx(1.0)


def test_early_detection_term_memory_missing_is_zero():
    """Memory 缺失 → 0（DESIGN §8.2 规定 M 未检测 → 0）。"""
    ev = _ev(early=EarlyDetectionResult.missing_detection("memory"))
    assert early_detection_term([ev]) == pytest.approx(0.0)


def test_early_detection_term_both_missing_excluded():
    """两臂均缺失 → N/A（排除，未测量 ≠ 0），整组无信息时返回 None。"""
    ev = _ev(early=EarlyDetectionResult.missing_detection("both"))
    assert early_detection_term([ev]) is None


def test_early_detection_term_mixed_baseline_and_computed():
    """混合：B 缺失（1.0）与 computed(30min→0.5) 取均值 = 0.75。"""
    a = _ev(early=EarlyDetectionResult.missing_detection("baseline"))
    b = _ev(early=EarlyDetectionResult.computed(30.0, 1))
    assert early_detection_term([a, b]) == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Memory Value Score
# ---------------------------------------------------------------------------
def test_score_renormalises_weights_when_early_detection_na():
    terms = compute_score_terms([_ev(fn_m=0, fn_b=1, fp_excess=0)])
    score = compute_memory_value_score(terms, n_cases=1)
    assert score.partial is True
    assert "early_detection" not in score.weights
    assert sum(score.weights.values()) == pytest.approx(1.0)
    expected = (0.40 * 1.0 + 0.20 * 1.0 + 0.10 * 1.0) / 0.70
    assert score.score == pytest.approx(expected)


def test_score_uses_base_weights_when_all_terms_present():
    ev = _ev(fn_m=0, fn_b=1, early=EarlyDetectionResult.computed(60.0, 1))
    score = compute_memory_value_score(compute_score_terms([ev]), n_cases=1)
    assert score.partial is False
    assert score.weights == pytest.approx(BASE_WEIGHTS)
    assert score.score == pytest.approx(1.0)


def test_score_never_claims_calibration():
    """Score 非 gate：E-1B 前 calibrated 恒 False，且 note 明示。"""
    score = compute_memory_value_score(compute_score_terms([_ev()]), n_cases=1)
    assert score.calibrated is False
    assert "非 Hard Gate" in score.note


def test_score_drops_when_metrics_degrade():
    """变异：FN 恶化 + Q3 失败 → Score 显著下降。"""
    good = compute_memory_value_score(
        compute_score_terms([_ev(fn_m=0, fn_b=2)]), n_cases=1
    ).score
    bad = compute_memory_value_score(
        compute_score_terms([_ev(fn_m=2, fn_b=2, q3=False, fp_excess=1)]), n_cases=1
    ).score
    assert bad < good


def test_compute_score_terms_empty_returns_none_terms():
    """评审 issue 2：空数据集 → 四 term 全 None（未测量），区别于真实样本得 0。"""
    t = compute_score_terms([])
    assert t.fn is None and t.early_detection is None and t.explanation is None and t.fp is None


def test_score_invalid_when_no_cases():
    """评审 issue 2：空数据集 → Score 无效（valid=False、score=None），不写零分。"""
    score = compute_memory_value_score(compute_score_terms([]), n_cases=0)
    assert score.valid is False
    assert score.score is None
    assert score.weights == {}


# ---------------------------------------------------------------------------
# Hard Gate 汇总
# ---------------------------------------------------------------------------
def test_hard_gate_all_pass():
    hg = summarize_hard_gate([_ev("a"), _ev("b")])
    assert hg.all_pass is True and hg.passed == 2 and hg.failed_case_ids == ()


def test_hard_gate_reports_failed_ids():
    hg = summarize_hard_gate([_ev("a"), _ev("b", q3=False)])
    assert hg.all_pass is False
    assert hg.failed_case_ids == ("b",)


def test_hard_gate_empty_dataset_is_not_pass():
    """空集视为不通过：无证据 ≠ 通过。"""
    assert summarize_hard_gate([]).all_pass is False


# ---------------------------------------------------------------------------
# 报告构建 / 渲染 / 落盘
# ---------------------------------------------------------------------------
def test_build_report_is_reproducible_with_injected_timestamp():
    evs = [_ev("a"), _ev("b")]
    r1 = build_report(evs, dataset_id="ds", generated_at="2026-01-01T00:00:00+00:00")
    r2 = build_report(evs, dataset_id="ds", generated_at="2026-01-01T00:00:00+00:00")
    assert report_to_dict(r1) == report_to_dict(r2)


def test_build_report_notes_early_detection_gap():
    r = build_report([_ev()], dataset_id="ds", generated_at="t")
    assert any("Early Detection" in n for n in r.notes)
    assert any("Hard Gate" in n for n in r.notes)


def test_report_to_dict_is_json_serialisable():
    r = build_report([_ev()], dataset_id="ds", generated_at="t")
    payload = json.loads(json.dumps(report_to_dict(r), ensure_ascii=False))
    assert payload["hard_gate"]["all_pass"] is True
    assert payload["score"]["calibrated"] is False
    assert payload["cases"][0]["early_detection"]["status"] == "na"


def test_render_markdown_contains_gate_and_score_sections():
    r = build_report([_ev("case_a")], dataset_id="ds", generated_at="t")
    md = render_markdown(r)
    assert "# E-1 Memory Value Evaluation — E-1A" in md
    assert "✅ PASS" in md
    assert "case_a" in md
    assert "N/A" in md  # Early Detection
    assert "**非 Hard Gate**" in md


def test_render_markdown_marks_failure():
    r = build_report([_ev("case_a", q3=False)], dataset_id="ds", generated_at="t")
    md = render_markdown(r)
    assert "❌ FAIL" in md
    assert "失败 case：case_a" in md


def test_write_report_emits_both_artifacts(tmp_path):
    r = build_report([_ev()], dataset_id="ds", generated_at="t")
    json_path, md_path = write_report(r, tmp_path / "out")
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["stage"] == "E-1A"
    assert md_path.read_text(encoding="utf-8").startswith("# E-1 Memory Value Evaluation")

"""ADR-0033 Phase 3 契约测试：生产门控（D5 Hard Gate 先于复合分 + D7 BenchmarkThresholds）。

T7  Gate 先于 Score：Hard Gate 全过 → 阈值对照 → 回归对照（可选）→ 复合分（仅报告、非门控、
    calibrated=False）；空集视为不通过；逐阈值边界包容（变异验证方向）；复合分绝不参与门禁。

本文件只依赖 ``evaluation`` 内部（``report`` / ``metrics`` / ``gate`` / ``ab_runner``），
经 opencv-headless 即可跑通（**不**拉 torch / 真实 pipeline），归入 PR 级 ``benchmark-gate`` job。
CLI 端到端（含退出码 3）见 ``tests/evaluation/test_benchmark_harness.py``（需完整 AI 栈，仅 main）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from home_perception.evaluation.ab_runner import BenchmarkABConservationError
from home_perception.evaluation.gate import (
    BenchmarkThresholds,
    GateResult,
    evaluate_gate,
)
from home_perception.evaluation.metrics import ScenarioScore
from home_perception.evaluation.report import BenchmarkReport

_SET_ID = "adr0033-phase1"


def _mk_report(
    *,
    scenario_ids=("a", "b"),
    outcomes=("TP", "TN"),
    validation_ok=None,
    risk_shortfalls=None,
    code_version="v1",
    scenario_set_id=_SET_ID,
):
    """构造可控的 ``BenchmarkReport``（不跑 harness，纯聚合）。"""
    if validation_ok is None:
        validation_ok = [True] * len(outcomes)
    if risk_shortfalls is None:
        risk_shortfalls = [0.0] * len(outcomes)
    scores = [
        ScenarioScore(
            scenario_id=sid,
            expected_label=None,
            actual_label="alert",
            outcome=oc,
            validation_ok=vok,
            validation_details="",
            risk_shortfall=rs,
            benchmark_expected_alarm=None,
        )
        for sid, oc, vok, rs in zip(scenario_ids, outcomes, validation_ok, risk_shortfalls)
    ]
    prov = {
        "scenario_set_id": scenario_set_id,
        "code_version": code_version,
        "generator_fingerprint": "gen1",
        "policy_fingerprint": "pol1",
        "model_fingerprint": {"detector": "d", "tracker": "t", "event_extractor": "e"},
        "runtime_dependencies": {"numpy": "2.4.2", "opencv": "4.13.0", "torch": "2.11.0"},
    }
    return BenchmarkReport.aggregate(
        scenario_set_id=scenario_set_id,
        harness_fingerprint="h",
        scores=scores,
        provenance=prov,
    )


# ============================================================================
# T7 基础：完美报告 → 通过
# ============================================================================
def test_adr0033_t7_perfect_report_passes():
    report = _mk_report(outcomes=("TP", "TN"))
    gate = evaluate_gate(report)
    assert isinstance(gate, GateResult)
    assert gate.hard_gate.all_pass is True
    assert gate.passed is True
    # 复合分永远 calibrated=False、且不参与门禁
    assert gate.score.calibrated is False


# ============================================================================
# T7 空集视为不通过（与 summarize_hard_gate 一致）
# ============================================================================
def test_adr0033_t7_empty_set_fails():
    report = _mk_report(scenario_ids=(), outcomes=())
    gate = evaluate_gate(report)
    assert gate.hard_gate.total == 0
    assert gate.hard_gate.all_pass is False  # bool(()) == False
    assert gate.passed is False


# ============================================================================
# T7 Hard Gate 先于一切：即便阈值/复合分「看起来好」，validation_ok=False 即整体失败
# ============================================================================
def test_adr0033_t7_hard_gate_precedence_over_thresholds_and_score():
    # 两场景：一个 TP(ok)、一个 validation_ok=False(但 outcome=TN)
    # → 阈值按 (tp=1,tn=1) 仍全过，但 Hard Gate 失败 → passed 必须 False
    report = _mk_report(
        scenario_ids=("a", "b"),
        outcomes=("TP", "TN"),
        validation_ok=[True, False],
    )
    gate = evaluate_gate(report)
    assert gate.hard_gate.all_pass is False
    assert "b" in gate.hard_gate.failed_case_ids
    # 阈值对照仍算（且与 hard gate 解耦）：此处阈值全过
    assert all(c.ok for c in gate.threshold_checks if not c.skipped)
    # 复合分照常计算，但绝不影响 passed
    assert gate.score.valid is True and gate.score.calibrated is False
    assert gate.passed is False


# ============================================================================
# T7 变异验证：逐阈值「边界包容」方向正确（> / < 反转即失败）
# ============================================================================
@pytest.mark.parametrize(
    ("outcomes", "risk_shortfalls", "thr_field", "at_threshold", "above_threshold"),
    [
        # min_pass_rate：pass_rate=0.5 → 阈值 0.5 通过、0.5001 失败（>= 方向）
        (("TP", "FN"), [0.0, 0.0], "min_pass_rate", 0.5, 0.5001),
        # max_suppression_rate：suppression=0.5 → 阈值 0.5 通过、0.499 失败（<= 方向）
        (("TP", "FN"), [0.0, 0.0], "max_suppression_rate", 0.5, 0.499),
        # max_false_alarm_rate：false_alarm=0.5 → 阈值 0.5 通过、0.499 失败
        (("TN", "FP"), [0.0, 0.0], "max_false_alarm_rate", 0.5, 0.499),
        # max_mean_risk_shortfall：mean=0.5 → 阈值 0.5 通过、0.499 失败
        (("TP", "TN"), [0.5, 0.5], "max_mean_risk_shortfall", 0.5, 0.499),
    ],
)
def test_adr0033_t7_threshold_boundary_inclusive(outcomes, risk_shortfalls, thr_field, at_threshold, above_threshold):
    report = _mk_report(outcomes=outcomes, risk_shortfalls=risk_shortfalls)
    base = {
        "min_pass_rate": 1.0,
        "max_suppression_rate": 0.0,
        "max_false_alarm_rate": 0.05,
        "max_mean_risk_shortfall": 0.0,
    }
    # 边界处：actual == threshold → ok True
    thr_at = BenchmarkThresholds(**{**base, thr_field: at_threshold})
    checks_at = {c.name: c for c in evaluate_gate(report, thr_at).threshold_checks}
    assert checks_at[thr_field].ok is True, f"{thr_field} 边界处应通过：{checks_at[thr_field]}"
    # 越过边界：actual 反方向 → ok False（若方向写反，此断言会失败 = 变异验证）
    thr_above = BenchmarkThresholds(**{**base, thr_field: above_threshold})
    checks_above = {c.name: c for c in evaluate_gate(report, thr_above).threshold_checks}
    assert checks_above[thr_field].ok is False, f"{thr_field} 越界应失败：{checks_above[thr_field]}"


# ============================================================================
# T7 mean_risk_shortfall=None（未标定场景集）→ 跳过该阈值，不判失败
# ============================================================================
def test_adr0033_t7_mean_risk_shortfall_none_skipped():
    report = _mk_report(outcomes=("TP", "TN"), risk_shortfalls=[None, None])
    gate = evaluate_gate(report)
    shortfall_check = next(
        c for c in gate.threshold_checks if c.name == "max_mean_risk_shortfall"
    )
    assert shortfall_check.skipped is True
    assert shortfall_check.ok is True  # 跳过 ≠ 失败
    assert gate.passed is True  # 其余阈值全过 → 整体通过


# ============================================================================
# T7 复合分绝不参与门禁：passed 与 score 解耦（即便 score 不「完美」也不影响判定）
# ============================================================================
def test_adr0033_t7_score_never_gates():
    # 通过场景：passed 由 hard gate + 阈值决定；score.calibrated 恒 False
    good = _mk_report(outcomes=("TP", "TN"))
    g_good = evaluate_gate(good)
    assert g_good.passed is True
    assert g_good.score.calibrated is False
    # 失败场景：passed=False，但 score 仍照常计算（valid、非 None）
    bad = _mk_report(outcomes=("TP", "TN"), validation_ok=[True, False])
    g_bad = evaluate_gate(bad)
    assert g_bad.passed is False
    assert g_bad.score.valid is True and g_bad.score.score is not None
    # 关键不变量：score 的结构（calibrated/score 值）变化不会改变 passed 的判定来源
    assert set(g_good.to_dict()["score"].keys()) == set(g_bad.to_dict()["score"].keys())


# ============================================================================
# T7 回归对照可独立成为门禁（隔离验证：阈值单独通过，但回归超预算 → 失败）
# ============================================================================
def test_adr0033_t7_regression_gate_isolates_failure():
    baseline = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"), code_version="v1")
    # candidate：a=TP, b=FN（pass_rate=0.5, suppression=0.5）；放宽其余阈值使「仅」回归能拦下
    candidate = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "FN"), code_version="v2")
    relaxed = BenchmarkThresholds(
        min_pass_rate=0.5,
        max_suppression_rate=1.0,
        max_false_alarm_rate=1.0,
        max_mean_risk_shortfall=1.0,
        max_regression_delta=0.0,
    )
    gate = evaluate_gate(candidate, relaxed, baseline=baseline)
    # hard gate 与（放宽后的）阈值均过，但回归对照拦截
    assert gate.hard_gate.all_pass is True
    assert all(c.ok for c in gate.threshold_checks if not c.skipped)
    assert gate.regression is not None
    assert gate.regression.regressions_exceeded is True
    assert gate.passed is False


def test_adr0033_t7_regression_not_gating_when_delta_none():
    baseline = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"), code_version="v1")
    candidate = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "FN"), code_version="v2")
    relaxed = BenchmarkThresholds(
        min_pass_rate=0.5,
        max_suppression_rate=1.0,
        max_false_alarm_rate=1.0,
        max_mean_risk_shortfall=1.0,
        max_regression_delta=None,  # 不设回归预算 → 不对照门禁
    )
    gate = evaluate_gate(candidate, relaxed, baseline=baseline)
    # 阈值（放宽后）过 → 整体通过；回归仅信息性（exceeded 恒 False）
    assert gate.regression is not None
    assert gate.regression.regressions_exceeded is False
    assert gate.passed is True


def test_adr0033_t7_regression_conservation_fail_propagates():
    # baseline 与 candidate 在「非 vary 轴」不一致 → evaluate_regression 抛守恒错误，
    # evaluate_gate 不静默放过（fail-closed）
    # 构造一个 generator 指纹不同的 baseline（破坏守恒）
    bad_baseline = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"), code_version="v1")
    bad_prov = dict(bad_baseline.provenance)
    bad_prov["generator_fingerprint"] = "TAMPERED"
    bad_baseline = BenchmarkReport.aggregate(
        scenario_set_id=bad_baseline.scenario_set_id,
        harness_fingerprint=bad_baseline.harness_fingerprint,
        scores=list(bad_baseline.scores),
        provenance=bad_prov,
    )
    candidate = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"), code_version="v2")
    with pytest.raises(BenchmarkABConservationError):
        evaluate_gate(candidate, BenchmarkThresholds(), baseline=bad_baseline)


# ============================================================================
# T9 模块边界：gate.py 不直接 import validation / runtime / analysis 重链
# （mirror ab_runner T9；用 AST 契约助手做变异验证）
# ============================================================================
def test_adr0033_t9_gate_module_boundary():
    import ast

    src = Path(__file__).resolve().parents[1] / ".." / "src" / "home_perception" / "evaluation" / "gate.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    forbidden_modules = {"torch", "ultralytics"}
    forbidden_names = {
        "ScenarioRunner",
        "ScenarioValidator",
        "ScenarioCompiler",
        "PerceptionPipeline",
        "VisitorTracker",
        "DecisionEngine",
        "RuleEngine",
    }
    # 收集顶层 import 的模块名（含 from X import / import X / import X as）
    imported = set()
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imported.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
            for n in node.names:
                referenced.add(n.name)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)

    # 直接 import 重链必须为零
    assert not (imported & forbidden_modules), f"gate.py 直接 import 了禁用模块：{imported & forbidden_modules}"
    # 不得直接引用重链中的具体符号（如 ScenarioRunner / PerceptionPipeline）
    assert not (referenced & forbidden_names), (
        f"gate.py 直接引用了重链符号：{referenced & forbidden_names}"
    )
    # 关键：gate 对 report / ab_runner 的依赖必须是**函数内懒导入**（不在模块顶层 import）
    top_imports = {
        n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    } | {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    assert "home_perception.evaluation.ab_runner" not in top_imports, (
        "gate.py 不得在模块顶层 import ab_runner（应懒导入以守 T9）"
    )

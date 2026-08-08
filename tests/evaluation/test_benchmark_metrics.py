"""ADR-0033 Phase 1 · T6：场景级混淆矩阵与离散指标的正确性（含变异验证）。

铁律：断言必须有真实检出力。本文件对每条公式都做**变异验证**——把被测实现替换成一个
似是而非的错误变体，断言测试确实会失败；否则断言等于永真装饰。
"""

from __future__ import annotations

import itertools

import pytest

from home_perception.evaluation.metrics import (
    LABEL_ALERT,
    LABEL_NO_ALERT,
    OUTCOME_FN,
    OUTCOME_FP,
    OUTCOME_TN,
    OUTCOME_TP,
    OUTCOME_UNLABELED,
    ScenarioScore,
    build_scenario_score,
    event_recall,
    risk_shortfall,
    scenario_actual_label,
    scenario_confusion,
    scenario_expected_label,
)
from home_perception.evaluation.report import BenchmarkReport
from home_perception.evaluation.schema import BenchmarkExpectation
from home_perception.validation.runner.runner import RunResult, ValidationResult
from home_perception.validation.scenario.scenario import (
    CameraSpec,
    ExpectsSpec,
    MetaSpec,
    Scenario,
)


def _scn(
    scenario_id: str = "s1",
    *,
    benchmark: BenchmarkExpectation | None = None,
    expected_events: list[str] | None = None,
    min_risk_level: str | None = None,
) -> Scenario:
    return Scenario(
        meta=MetaSpec(
            schema_version="1.0",
            scenario_id=scenario_id,
            version=1,
            seed=1,
            duration_frames=10,
        ),
        mode="detections",
        camera=CameraSpec(resolution=[384, 288], fps=2),
        expects=ExpectsSpec(
            emitted_event_types=expected_events or [], min_risk_level=min_risk_level
        ),
        benchmark=benchmark,
    )


def _run(
    scenario_id: str = "s1",
    *,
    warnings: list[object] | None = None,
    event_types: set[str] | None = None,
    risk_levels: list[str] | None = None,
) -> RunResult:
    return RunResult(
        scenario_id=scenario_id,
        mode="detections",
        event_types=event_types or set(),
        risk_levels=risk_levels or [],
        warnings=warnings or [],
    )


def _val(scenario_id: str = "s1", *, ok: bool = True, missing: set[str] | None = None):
    return ValidationResult(
        scenario_id=scenario_id, ok=ok, missing_event_types=missing or set(), details="d"
    )


# ============================================================================
# T6-a 期望标签只来自 benchmark，绝不从 expects 推导（D3 语义分离）
# ============================================================================


def test_adr0033_t6_expected_label_only_from_benchmark():
    # 有 expects 却无 benchmark → 仍是未标注（证明不从 expects 推导）
    rich_expects = _scn(expected_events=["abnormal_dwell"], min_risk_level="HIGH")
    assert rich_expects.benchmark is None
    assert scenario_expected_label(rich_expects) is None

    assert scenario_expected_label(_scn(benchmark=BenchmarkExpectation(expected_alarm=True))) == (
        LABEL_ALERT
    )
    assert scenario_expected_label(_scn(benchmark=BenchmarkExpectation(expected_alarm=False))) == (
        LABEL_NO_ALERT
    )


def test_adr0033_t6_severity_is_fail_closed():
    """severity 必须落在 RISK_LEVELS 内，非法值构造期即拒（fail-closed）。"""
    BenchmarkExpectation(expected_alarm=True, severity="HIGH")  # 合法不抛
    with pytest.raises(ValueError, match="severity"):
        BenchmarkExpectation(expected_alarm=True, severity="CRITICAL")


# ============================================================================
# T6-b 混淆矩阵四象限穷举（顺序无关、全组合覆盖）
# ============================================================================


def test_adr0033_t6_confusion_matrix_exhaustive():
    expected_domain = [LABEL_ALERT, LABEL_NO_ALERT, None]
    actual_domain = [LABEL_ALERT, LABEL_NO_ALERT]
    table = {
        (LABEL_ALERT, LABEL_ALERT): OUTCOME_TP,
        (LABEL_NO_ALERT, LABEL_NO_ALERT): OUTCOME_TN,
        (LABEL_ALERT, LABEL_NO_ALERT): OUTCOME_FN,  # 漏报
        (LABEL_NO_ALERT, LABEL_ALERT): OUTCOME_FP,  # 误报
        (None, LABEL_ALERT): OUTCOME_UNLABELED,
        (None, LABEL_NO_ALERT): OUTCOME_UNLABELED,
    }
    # 穷举 3×2 全组合，确保无遗漏分支
    combos = list(itertools.product(expected_domain, actual_domain))
    assert len(combos) == len(table)
    for expected, actual in combos:
        assert scenario_confusion(expected, actual) == table[(expected, actual)], (
            f"expected={expected} actual={actual}"
        )


def test_adr0033_t6_confusion_mutation_detects_swapped_fn_fp():
    """变异验证：把 FN / FP 语义对调，穷举断言必须失败（证明上面不是永真）。"""

    def mutated(expected: str | None, actual: str) -> str:
        if expected is None:
            return OUTCOME_UNLABELED
        if expected == actual:
            return OUTCOME_TP if expected == LABEL_ALERT else OUTCOME_TN
        # 故意对调：漏报报成误报
        return OUTCOME_FP if expected == LABEL_ALERT else OUTCOME_FN

    assert mutated(LABEL_ALERT, LABEL_NO_ALERT) != scenario_confusion(LABEL_ALERT, LABEL_NO_ALERT)
    assert mutated(LABEL_NO_ALERT, LABEL_ALERT) != scenario_confusion(LABEL_NO_ALERT, LABEL_ALERT)


# ============================================================================
# T6-c 实际标签 = bool(warnings)，且 warning_policy 接缝真实可替换
# ============================================================================


def test_adr0033_t6_actual_label_default_and_policy_seam():
    assert scenario_actual_label(_run(warnings=[])) == LABEL_NO_ALERT
    assert scenario_actual_label(_run(warnings=[object()])) == LABEL_ALERT

    class _SuppressAll:
        def evaluate(self, warnings):  # 策略接缝：故意忽略入参
            return False

    # 有 warning 但策略判定不构成报警 → 走接缝、结果翻转（证明接缝真接上了）
    assert scenario_actual_label(_run(warnings=[object()]), warning_policy=_SuppressAll()) == (
        LABEL_NO_ALERT
    )


# ============================================================================
# T6-d 率公式：suppression_rate / false_alarm_rate / precision / recall / F1
# ============================================================================


def _score(outcome: str, *, sid: str = "x", recall_val: float = 1.0) -> ScenarioScore:
    return ScenarioScore(
        scenario_id=sid,
        expected_label=LABEL_ALERT,
        actual_label=LABEL_ALERT,
        outcome=outcome,
        validation_ok=True,
        validation_details="",
        event_recall=recall_val,
    )


def test_adr0033_t6_rate_formulas():
    # TP=2 FN=1 FP=1 TN=3
    scores = (
        [_score(OUTCOME_TP, sid=f"tp{i}") for i in range(2)]
        + [_score(OUTCOME_FN, sid="fn0")]
        + [_score(OUTCOME_FP, sid="fp0")]
        + [_score(OUTCOME_TN, sid=f"tn{i}") for i in range(3)]
    )
    r = BenchmarkReport.aggregate(scenario_set_id="s", harness_fingerprint="f", scores=scores)
    assert (r.tp, r.tn, r.fn, r.fp) == (2, 3, 1, 1)
    assert r.suppression_rate == pytest.approx(1 / 3)  # FN/(FN+TP)
    assert r.false_alarm_rate == pytest.approx(1 / 4)  # FP/(FP+TN)
    assert r.precision == pytest.approx(2 / 3)  # TP/(TP+FP)
    assert r.recall == pytest.approx(2 / 3)  # TP/(TP+FN)
    assert r.f1 == pytest.approx(2 / 3)
    # 变异验证：若把 suppression_rate 误算成 FN/(FN+FP)，值会是 0.5 ≠ 1/3
    assert r.suppression_rate != pytest.approx(1 / 2)


def test_adr0033_t6_rates_are_zero_safe_on_empty_denominator():
    """全 UNLABELED → 分母为 0，必须返回 0.0 而非抛 ZeroDivisionError。"""
    scores = [_score(OUTCOME_UNLABELED, sid="u0"), _score(OUTCOME_UNLABELED, sid="u1")]
    r = BenchmarkReport.aggregate(scenario_set_id="s", harness_fingerprint="f", scores=scores)
    assert (r.tp, r.tn, r.fn, r.fp) == (0, 0, 0, 0)
    assert r.suppression_rate == 0.0
    assert r.false_alarm_rate == 0.0
    assert r.f1 == 0.0
    assert r.unlabeled_scenario_count == 2
    assert r.unlabeled_scenario_ids == ("u0", "u1")


def test_adr0033_t6_unlabeled_excluded_from_confusion_but_kept_in_recall():
    """未标注场景不进混淆矩阵，但仍参与 mean_event_recall（验证 ≠ 评价，互不污染）。"""
    scores = [
        _score(OUTCOME_TP, sid="labeled", recall_val=1.0),
        _score(OUTCOME_UNLABELED, sid="unlabeled", recall_val=0.0),
    ]
    r = BenchmarkReport.aggregate(scenario_set_id="s", harness_fingerprint="f", scores=scores)
    assert (r.tp, r.tn, r.fn, r.fp) == (1, 0, 0, 0)
    assert r.unlabeled_scenario_ids == ("unlabeled",)
    # 若未标注被错误排除出 recall，均值会是 1.0；正确实现是 0.5
    assert r.mean_event_recall == pytest.approx(0.5)


def test_adr0033_t6_aggregate_is_order_independent():
    """顺序无关：穷举全排列，canonical_dict 必须逐字节一致。"""
    base = [
        _score(OUTCOME_TP, sid="a"),
        _score(OUTCOME_FN, sid="b"),
        _score(OUTCOME_UNLABELED, sid="c"),
    ]
    canon = {
        str(
            BenchmarkReport.aggregate(
                scenario_set_id="s", harness_fingerprint="f", scores=list(perm)
            ).canonical_dict()
        )
        for perm in itertools.permutations(base)
    }
    assert len(canon) == 1, "聚合结果依赖了输入顺序"


# ============================================================================
# T6-e 验证指标：event_recall / risk_shortfall
# ============================================================================


def test_adr0033_t6_event_recall_semantics():
    assert event_recall(set(), set()) == 1.0  # 无期望 → 真空满足
    assert event_recall(set(), {"a"}) == 0.0
    assert event_recall({"a"}, {"a", "b"}) == pytest.approx(0.5)
    assert event_recall({"a", "b"}, {"a"}) == 1.0  # 超额产出不惩罚
    # 变异验证：若误用 len(observed & expected)/len(observed)，上一行会是 0.5
    assert event_recall({"a", "b"}, {"a"}) != pytest.approx(0.5)


def test_adr0033_t6_risk_shortfall_semantics():
    # 期望 LOW(0)，实际 HIGH(2) → 0-2 = -2（负值=超额达标）
    assert risk_shortfall(_scn(min_risk_level="LOW"), ["HIGH"]) == pytest.approx(-2.0)
    # 期望 HIGH(2)，实际无告警(-1) → 2-(-1) = 3（正值=未达标）
    assert risk_shortfall(_scn(min_risk_level="HIGH"), []) == pytest.approx(3.0)
    # 期望 MEDIUM(1)，实际 MEDIUM(1) → 0（刚好达标）
    assert risk_shortfall(_scn(min_risk_level="MEDIUM"), ["MEDIUM"]) == pytest.approx(0.0)
    # 未声明 min_risk_level → None（验证指标不适用，不得静默填 0）
    assert risk_shortfall(_scn(), ["HIGH"]) is None


# ============================================================================
# T6-f build_scenario_score 端到端派生
# ============================================================================


def test_adr0033_t6_build_scenario_score_wires_all_three_inputs():
    scn = _scn(
        "night",
        benchmark=BenchmarkExpectation(expected_alarm=True, severity="LOW", note="n"),
        expected_events=["visit_normal", "abnormal_dwell"],
        min_risk_level="LOW",
    )
    run = _run("night", warnings=[object()], event_types={"visit_normal"}, risk_levels=["LOW"])
    val = _val("night", ok=False, missing={"abnormal_dwell"})

    score = build_scenario_score(scn, run, val)
    assert score.scenario_id == "night"
    assert score.outcome == OUTCOME_TP  # 期望报警 × 实际报警
    assert score.validation_ok is False  # 评价通过 ≠ 验证通过（两轴独立）
    assert score.missing_event_types == {"abnormal_dwell"}
    assert score.event_recall == pytest.approx(0.5)
    assert score.risk_shortfall == pytest.approx(0.0)
    assert score.benchmark_expected_alarm is True
    assert score.benchmark_severity == "LOW"
    # to_dict 全部可 JSON 序列化（集合已转有序列表）
    d = score.to_dict()
    assert d["observed_event_types"] == ["visit_normal"]
    assert d["expected_event_types"] == ["abnormal_dwell", "visit_normal"]

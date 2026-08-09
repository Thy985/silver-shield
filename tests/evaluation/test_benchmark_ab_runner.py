"""ADR-0033 Phase 2 契约测试：A/B 守恒 + 基线可回放 + 回归对照（D6 / D7）。

T8  A/B 双轨守恒（七条，vary=code_version 与 vary=model_fingerprint 双轴，各违反抛
    ``BenchmarkABConservationError``）
T9  模块边界（ab_runner 不直接 import validation / runtime / analysis 重链）
T10 基线可回放（committed baseline 经 ``from_dict`` 还原、``BenchmarkDiff`` 正确、
    ``evaluate_regression`` 对照 ``max_regression_delta``、退化场景集捕获）
集成：两次真实 harness 跑出 ``BenchmarkDiff``（ADR §6 验收："两次相邻提交跑出 BenchmarkDiff"）
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from home_perception.evaluation.ab_runner import (
    VARY_CODE,
    VARY_MODEL,
    BenchmarkABConservationError,
    BenchmarkABRun,
    BenchmarkDiff,
    evaluate_regression,
    load_baseline_report,
    load_baseline_report_path,
    write_baseline_report,
)
from home_perception.evaluation.metrics import ScenarioScore
from home_perception.evaluation.report import BenchmarkReport

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

SET_ID = "adr0033-phase1"

_BASE = {
    "code_version": "v1",
    "model_fp": {"detector": "scenario-detection-detector", "tracker": "t", "event_extractor": "e"},
    "gen_fp": "gen1",
    "policy_fp": "pol1",
    "rt": {"numpy": "2.4.2", "opencv": "4.13.0", "torch": "2.11.0"},
}


def _mk_report(
    *,
    code_version=None,
    model_fp=None,
    gen_fp=None,
    policy_fp=None,
    rt=None,
    scenario_ids=("a", "b"),
    outcomes=("TP", "TN"),
    scenario_set_id=SET_ID,
):
    scores = [
        ScenarioScore(
            scenario_id=sid,
            expected_label=None,
            actual_label="alert",
            outcome=oc,
            validation_ok=True,
            validation_details="",
            benchmark_expected_alarm=None,
        )
        for sid, oc in zip(scenario_ids, outcomes)
    ]
    prov = {
        "scenario_set_id": scenario_set_id,
        "code_version": code_version if code_version is not None else _BASE["code_version"],
        "generator_fingerprint": gen_fp if gen_fp is not None else _BASE["gen_fp"],
        "policy_fingerprint": policy_fp if policy_fp is not None else _BASE["policy_fp"],
        "model_fingerprint": dict(model_fp if model_fp is not None else _BASE["model_fp"]),
        "runtime_dependencies": dict(rt if rt is not None else _BASE["rt"]),
    }
    return BenchmarkReport.aggregate(
        scenario_set_id=scenario_set_id,
        harness_fingerprint="h",
        scores=scores,
        provenance=prov,
    )


# ============================================================================
# T8 A/B 双轨守恒（七条，vary 双轴）
# ============================================================================


def test_t8_conserved_ok_vary_code():
    b = _mk_report(code_version="v1")
    c = _mk_report(code_version="v2")  # 仅 vary=code_version 轴不同
    BenchmarkABRun(
        scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_CODE
    ).assert_conserved()


def test_t8_conserved_ok_vary_model():
    b = _mk_report(model_fp={"detector": "d", "tracker": "t", "event_extractor": "e"}, code_version="v1")
    c = _mk_report(
        model_fp={"detector": "d2", "tracker": "t", "event_extractor": "e"}, code_version="v1"
    )  # 仅 vary=model_fingerprint 轴不同，code 守恒
    BenchmarkABRun(
        scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_MODEL
    ).assert_conserved()


def test_t8_law1_scenario_set_id_mismatch():
    b = _mk_report(scenario_set_id="s1")
    c = _mk_report(scenario_set_id="s2")
    with pytest.raises(BenchmarkABConservationError, match="1/7"):
        BenchmarkABRun(
            scenario_set_id="s1", report_baseline=b, report_candidate=c
        ).assert_conserved()


def test_t8_law2_generator_mismatch():
    b = _mk_report(gen_fp="gen1")
    c = _mk_report(gen_fp="gen2")
    with pytest.raises(BenchmarkABConservationError, match="2/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c
        ).assert_conserved()


def test_t8_law3_policy_mismatch():
    b = _mk_report(policy_fp="pol1")
    c = _mk_report(policy_fp="pol2")
    with pytest.raises(BenchmarkABConservationError, match="3/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c
        ).assert_conserved()


def test_t8_law4_runtime_mismatch():
    b = _mk_report(rt={"numpy": "2.4.2", "opencv": "4.13.0", "torch": "2.11.0"})
    c = _mk_report(rt={"numpy": "2.5.0", "opencv": "4.13.0", "torch": "2.11.0"})
    with pytest.raises(BenchmarkABConservationError, match="4/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c
        ).assert_conserved()


def test_t8_law5_vary_code_model_must_conserve():
    # vary=code_version 时 model 必须守恒
    b = _mk_report(model_fp={"detector": "d", "tracker": "t", "event_extractor": "e"}, code_version="v1")
    c = _mk_report(model_fp={"detector": "dX", "tracker": "t", "event_extractor": "e"}, code_version="v2")
    with pytest.raises(BenchmarkABConservationError, match="5/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_CODE
        ).assert_conserved()


def test_t8_law6_vary_code_must_differ():
    # vary=code_version 时两臂 code_version 必须不同（防同报告伪装无差异）
    b = _mk_report(code_version="v1")
    c = _mk_report(code_version="v1")
    with pytest.raises(BenchmarkABConservationError, match="6/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_CODE
        ).assert_conserved()


def test_t8_law5_vary_model_code_must_conserve():
    # vary=model_fingerprint 时 code 必须守恒
    b = _mk_report(model_fp={"detector": "d", "tracker": "t", "event_extractor": "e"}, code_version="v1")
    c = _mk_report(model_fp={"detector": "d2", "tracker": "t", "event_extractor": "e"}, code_version="v2")
    with pytest.raises(BenchmarkABConservationError, match="5/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_MODEL
        ).assert_conserved()


def test_t8_law6_vary_model_must_differ():
    b = _mk_report(model_fp={"detector": "d", "tracker": "t", "event_extractor": "e"}, code_version="v1")
    c = _mk_report(model_fp={"detector": "d", "tracker": "t", "event_extractor": "e"}, code_version="v1")
    with pytest.raises(BenchmarkABConservationError, match="6/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_MODEL
        ).assert_conserved()


def test_t8_law7_scenario_order_mismatch():
    b = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"), code_version="v1")
    c = _mk_report(scenario_ids=("b", "a"), outcomes=("TN", "TP"), code_version="v2")
    with pytest.raises(BenchmarkABConservationError, match="7/7"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c
        ).assert_conserved()


# ============================================================================
# T9 模块边界（ab_runner 不直接 import validation / runtime / analysis）
# ============================================================================


def test_t9_ab_runner_module_boundary():
    import home_perception.evaluation.ab_runner as m

    src = Path(m.__file__).read_text(encoding="utf-8")
    banned = (
        "import home_perception.validation",
        "from home_perception.validation",
        "import home_perception.runtime",
        "from home_perception.runtime",
        "import home_perception.analysis",
        "from home_perception.analysis",
    )
    for b in banned:
        assert b not in src, f"ab_runner.py 不得直接 import {b!r}（T9 模块边界，须经 .metrics/.report 叶子）"


# ============================================================================
# T10 基线可回放（committed baseline）
# ============================================================================


def test_t10_baseline_roundtrip():
    base = load_baseline_report(SET_ID)
    again = BenchmarkReport.from_dict(base.to_dict())
    assert again.harness_fingerprint == base.harness_fingerprint
    assert (again.tp, again.tn, again.fn, again.fp) == (1, 1, 0, 0)
    assert again.scenario_set_id == SET_ID
    assert again.provenance.get("code_version") == "18c3087"


def test_t10_baseline_diff_zero_on_self():
    base = load_baseline_report(SET_ID)
    diff = BenchmarkDiff.from_reports(base, base)
    assert diff.scenario_set_id == SET_ID
    assert all(d.delta == 0.0 for d in diff.deltas)
    assert diff.regressed_scenario_ids == ()


def test_t10_baseline_ab_conserved_and_diff():
    base = load_baseline_report(SET_ID)
    cand = replace(base, provenance={**base.provenance, "code_version": "candidate-v2"})
    # vary=code_version：code 不同、其余守恒 → 守恒通过
    BenchmarkABRun(
        scenario_set_id=SET_ID, report_baseline=base, report_candidate=cand, vary=VARY_CODE
    ).assert_conserved()
    diff = BenchmarkDiff.from_reports(base, cand)
    # 仅 code_version 不同 → 指标全 0（无行为差异）
    assert all(d.delta == 0.0 for d in diff.deltas)
    reg = evaluate_regression(cand, base, max_regression_delta=0.01)
    assert reg.regressions_exceeded is False


def _degrade(scores):
    """把 night_dwell（baseline 为 TP）翻转为 FN（漏报），其余不变。"""
    out = []
    for s in scores:
        if s.scenario_id == "sw_benchmark_night_dwell":
            out.append(
                replace(
                    s,
                    outcome="FN",
                    actual_label="no_alert",
                    expected_label="alert",
                    benchmark_expected_alarm=True,
                )
            )
        else:
            out.append(s)
    return tuple(out)


def _reaggregate(report: BenchmarkReport, scores) -> BenchmarkReport:
    """从（可能退化的）scores 重新聚合 candidate，并切换 code_version（模拟真实代码变更）。

    ``replace`` 只换 ``scores`` 字段、不动预聚合指标，会令 ``BenchmarkDiff`` 指标 Δ 全 0；
    故退化候选必须重新 ``aggregate`` 才能反映指标变化。
    """
    return BenchmarkReport.aggregate(
        scenario_set_id=report.scenario_set_id,
        harness_fingerprint=report.harness_fingerprint,
        scores=list(scores),
        generated_at=report.generated_at,
        provenance={**report.provenance, "code_version": "candidate-v2"},
    )


def test_t10_regression_detected():
    base = load_baseline_report(SET_ID)
    cand = _reaggregate(base, _degrade(base.scores))
    diff = BenchmarkDiff.from_reports(base, cand)
    assert "sw_benchmark_night_dwell" in diff.regressed_scenario_ids
    # suppression_rate: 0.0 → 1.0（漏报 1 例），远超 0.01 预算
    reg = evaluate_regression(cand, base, max_regression_delta=0.01)
    assert reg.regressions_exceeded is True


# ============================================================================
# 审查 round 2 修正验证（C2/C3/M5/M6/M7/M9/M10/L14）
# ============================================================================


def test_m7_good_to_unlabeled_counts_as_regression():
    """TP/TN → UNLABELED 也计入退化（M7：失去对良好结果的断言即退化）。"""
    b = _mk_report(scenario_ids=("a", "b"), outcomes=("TP", "TN"))
    cand_scores = tuple(
        replace(
            s,
            outcome="UNLABELED",
            actual_label="no_alert",
            expected_label=None,
            benchmark_expected_alarm=None,
        )
        if s.scenario_id == "a"
        else s
        for s in b.scores
    )
    cand = _reaggregate(b, cand_scores)
    diff = BenchmarkDiff.from_reports(b, cand)
    assert "a" in diff.regressed_scenario_ids


def test_m9_scenario_set_id_post_init_consistency():
    """BenchmarkABRun.scenario_set_id 不再被静默忽略（M9）。"""
    b = _mk_report(scenario_set_id="s1", code_version="v1")
    c = _mk_report(scenario_set_id="s1", code_version="v2")
    # 与两臂一致 → 构造通过
    BenchmarkABRun(
        scenario_set_id="s1", report_baseline=b, report_candidate=c, vary=VARY_CODE
    )
    # 与两臂均不一致 → fail-closed
    with pytest.raises(BenchmarkABConservationError):
        BenchmarkABRun(
            scenario_set_id="sX", report_baseline=b, report_candidate=c, vary=VARY_CODE
        )


def test_m5_conservation_rejects_missing_provenance():
    """provenance 缺守恒字段 → 守恒失败（M5：防手工编辑基线假绿）。"""
    b = _mk_report(code_version="v1")
    c = _mk_report(code_version="v2")
    bad = dict(c.provenance)
    del bad["generator_fingerprint"]
    c = replace(c, provenance=bad)
    with pytest.raises(BenchmarkABConservationError, match="provenance 缺字段"):
        BenchmarkABRun(
            scenario_set_id=SET_ID, report_baseline=b, report_candidate=c, vary=VARY_CODE
        ).assert_conserved()


def test_c3_negative_max_regression_delta_rejected():
    """max_regression_delta < 0 令语义反转（任何改善判为回归）→ 显式 ValueError（C3）。"""
    base = load_baseline_report(SET_ID)
    cand = replace(base, provenance={**base.provenance, "code_version": "candidate-v2"})
    with pytest.raises(ValueError, match="必须 ≥ 0"):
        evaluate_regression(cand, base, max_regression_delta=-0.1)


def test_m6_from_dict_rejects_top_level_non_dict():
    """from_dict 顶层非对象 → TypeError（M6：不再让裸 KeyError 透到内层；TRY004 类型检查用 TypeError）。"""
    with pytest.raises(TypeError):
        BenchmarkReport.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]


def test_m6_from_dict_rejects_missing_metrics():
    """from_dict 缺 metrics 字段 → ValueError 带上下文（M6 / L11）。"""
    with pytest.raises(TypeError, match="缺字段 metrics"):
        BenchmarkReport.from_dict(
            {"scenario_set_id": "x", "harness_fingerprint": "h", "scores": []}
        )


def test_m6_load_baseline_report_path_rejects_top_level_list(tmp_path):
    """load_baseline_report_path 顶层 JSON 为 list → ValueError（M6）。"""
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(TypeError, match="顶层须为对象"):
        load_baseline_report_path(p)


def test_m10_from_dict_rejects_string_metric():
    """metrics 数值字段塞字符串 → ValueError（M10：拒绝静默塌缩）。"""
    bad = load_baseline_report(SET_ID).to_dict()
    bad["metrics"]["tp"] = "abc"  # type: ignore[index]
    with pytest.raises(TypeError):
        BenchmarkReport.from_dict(bad)


def test_m10_scenario_score_from_dict_rejects_none_validation_ok():
    """ScenarioScore.validation_ok=None → TypeError（M10：bool(None) 静默塌缩已拒；TRY004 类型检查用 TypeError）。"""
    bad = ScenarioScore(
        scenario_id="x",
        expected_label=None,
        actual_label="alert",
        outcome="TP",
        validation_ok=True,
        validation_details="",
    ).to_dict()
    bad["validation_ok"] = None
    with pytest.raises(TypeError):
        ScenarioScore.from_dict(bad)


def test_c2_write_baseline_report_dual_guard(tmp_path):
    """write_baseline_report 复用双守卫（脱敏 + 父目录须存在，不 mkdir）（C2/M8）。"""
    base = load_baseline_report(SET_ID)
    out = tmp_path / "b.json"
    write_baseline_report(out, base)  # 父目录存在 → 写入
    assert out.exists()
    again = BenchmarkReport.from_dict(json.loads(out.read_text(encoding="utf-8")))
    assert (again.tp, again.tn, again.fn, again.fp) == (1, 1, 0, 0)
    # 父目录不存在 → 拒绝自动创建（纵深防路径穿越）
    with pytest.raises(ValueError, match="父目录不存在"):
        write_baseline_report(tmp_path / "nope" / "x.json", base)


def test_l14_baselines_dir_exported():
    """__init__ 转发 BASELINES_DIR（L14：可经包入口取基线目录）。"""
    from home_perception.evaluation import BASELINES_DIR

    assert (BASELINES_DIR / f"{SET_ID}.json").exists()


# ============================================================================
# 集成：两次真实 harness 跑出 BenchmarkDiff（ADR §6 验收）
# ============================================================================


def _build_torchfree_pipeline(synth):
    from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
    from home_perception.action.executor import ActionExecutor
    from home_perception.action.notifier import MockNotifier
    from home_perception.action.publisher import MockPublisher
    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.analysis.feature_extractor import FeatureExtractor
    from home_perception.analysis.rule_engine import RuleEngine
    from home_perception.detection.tracker import VisitorTracker
    from home_perception.runtime.pipeline import PerceptionPipeline

    clock = _Clock(datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC))
    tracker = VisitorTracker(now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="scenario", now_provider=clock)
    feature_extractor = FeatureExtractor(frequency_window_s=60.0)
    rule_engine = RuleEngine(device_id="home_entry_01", location="入户门", now_provider=clock)
    decision_engine = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher, publisher, notifier, max_retries=1)
    return PerceptionPipeline(
        detector=synth.detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feature_extractor,
        rule_engine=rule_engine,
        decision_engine=decision_engine,
        executor=executor,
        now_provider=clock,
        frame_interval_s=0.5,
    )


class _Clock:
    def __init__(self, start, interval_s: float = 0.5):
        self._t = start
        self.interval_s = interval_s

    def now(self):
        return self._t

    def __call__(self):
        return self._t

    def tick(self, dt: float | None = None):
        import datetime as _dt

        self._t = self._t + _dt.timedelta(seconds=dt if dt is not None else self.interval_s)


@pytest.mark.timeout(180)
def test_t10_integration_real_ab_run():
    from home_perception.evaluation.harness import BenchmarkHarness
    from home_perception.validation import load_scenarios_dir

    scenarios = load_scenarios_dir(
        str(_PROJECT_ROOT / "src/home_perception/validation/fixtures/scenarios/benchmark")
    )
    base = BenchmarkHarness().run(
        scenarios,
        _build_torchfree_pipeline,
        scenario_set_id=SET_ID,
        code_version="base-real",
        generated_at="",
    )
    cand = BenchmarkHarness().run(
        scenarios,
        _build_torchfree_pipeline,
        scenario_set_id=SET_ID,
        code_version="cand-real",
        generated_at="",
    )
    # 两次真实运行、同环境、仅 code_version 不同 → 守恒通过
    BenchmarkABRun(
        scenario_set_id=SET_ID, report_baseline=base, report_candidate=cand, vary=VARY_CODE
    ).assert_conserved()
    diff = BenchmarkDiff.from_reports(base, cand)
    assert all(d.delta == 0.0 for d in diff.deltas)  # 指标不受 code_version 文本影响
    reg = evaluate_regression(cand, base, max_regression_delta=0.0)
    assert reg.regressions_exceeded is False

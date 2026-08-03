"""E-1c 时序 step 展开 + LeadTime 校准测试（DESIGN §4.4 / §4.4.1）。"""

from __future__ import annotations

import pytest

from home_perception.memory.consumer.contracts import ReasoningResult
from home_perception.memory.evaluation.report import (
    early_detection_term,
    main,
    run_e1a_report,
    run_e1c_report,
)
from home_perception.memory.evaluation.temporal import (
    TemporalCase,
    TemporalStep,
    evaluate_temporal_case,
    is_detection,
    load_temporal_dataset,
    run_temporal_ab_case,
)

FIXTURE_ROOT = "tests/fixtures/memory_replay"


def _result(hint, findings):
    return ReasoningResult(findings=tuple(findings), explanation="x", suggested_action_hint=hint)


# ---------------------------------------------------------------------------
# is_detection 判定（§4.4.1）
# ---------------------------------------------------------------------------
def test_is_detection_by_hint():
    assert is_detection(_result("NOTIFY_FAMILY", ["当前事件 X"]))
    assert is_detection(_result("ESCALATE_COMMUNITY", ["当前事件 X"]))
    # MONITOR 不属检测阈值（repeat_visitor 仅到 MONITOR）
    assert not is_detection(_result("MONITOR", ["发现风险模式：repeated_visit"]))
    # None hint + 无 escalation/conflict 文本
    assert not is_detection(_result(None, ["发现风险模式：repeated_visit", "可召回历史 1 条"]))


def test_is_detection_by_finding_keyword():
    # escalation 关键词
    assert is_detection(_result(None, ["发现风险模式：escalating_behavior（置信度 cold_start）"]))
    # conflict 关键词
    assert is_detection(_result(None, ["检测到冲突（behavior_shift）：历史=normal，当前=abnormal"]))
    # baseline 清空后（无 pattern / 无 conflict）→ 不检测
    assert not is_detection(_result(None, ["当前事件 X（类型 risk_signal）"]))


def test_is_detection_negation_context():
    """B2：否定语境不应误判为检测（呼应文本扫描脆弱性）。"""
    # 中文否定
    assert not is_detection(_result(None, ["未观察到 escalation 行为"]))
    assert not is_detection(_result(None, ["无 conflict，历史一致"]))
    # 英文否定
    assert not is_detection(_result(None, ["no escalation detected"]))
    # 正向短语仍应命中（不被否定标记误伤）
    assert is_detection(_result(None, ["发现风险模式：escalating_behavior"]))
    assert is_detection(_result(None, ["检测到冲突（behavior_shift）"]))


def test_is_detection_exact_tag_not_substring_glue():
    """B2：behavior_shift 整词匹配，避免 glued / 派生词误命中。"""
    # 派生词不应命中（无整词 "behavior_shift"）
    assert not is_detection(_result(None, ["检测到 behavior_shifted 趋势"]))
    assert not is_detection(_result(None, ["prebehavior_shift 已排除"]))
    # 整词命中
    assert is_detection(_result(None, ["检测到冲突（behavior_shift）"]))


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------
def test_load_temporal_dataset_finds_three_cases():
    cases = load_temporal_dataset(FIXTURE_ROOT)
    ids = [c.case_id for c in cases]
    assert ids == [
        "case_001_repeat_visitor",
        "case_002_behavior_escalation",
        "case_003_conflict_transparency",
    ]
    assert all(c.n_steps == 3 for c in cases)


def test_temporal_dir_does_not_break_e1a_loader():
    """E-1a 报告仍只加载 M0 三 case（temporal/ 子目录被 MemoryReplayDataset 忽略）。"""
    report = run_e1a_report(FIXTURE_ROOT)
    assert report.hard_gate.total == 3
    assert report.hard_gate.all_pass
    # E-1a 无时序数据 → early_detection term 仍为 None
    assert report.score.terms.early_detection is None


# ---------------------------------------------------------------------------
# 逐 case 时序 A/B：三种 Early Detection 结果
# ---------------------------------------------------------------------------
def test_case_002_computed_positive_lead_time():
    cases = {c.case_id: c for c in load_temporal_dataset(FIXTURE_ROOT)}
    ab = run_temporal_ab_case(cases["case_002_behavior_escalation"])
    # Memory 在 step2（LOW + escalating_behavior 模式）检测；Baseline 在 step3（HIGH）才检测
    assert ab.memory_detection_step == 2
    assert ab.baseline_detection_step == 3
    assert ab.early.status == "computed"
    assert ab.early.lead_time_minutes == pytest.approx(45.0)
    assert ab.early.step_delta == 1


def test_case_003_baseline_missing_positive():
    """评审 issue 1 场景：B 缺失且 M 检出 → 正向（+1.0）。"""
    cases = {c.case_id: c for c in load_temporal_dataset(FIXTURE_ROOT)}
    ab = run_temporal_ab_case(cases["case_003_conflict_transparency"])
    # Memory 在 step2（LOW + conflict）检测；Baseline 始终 LOW 且无冲突 → 从不检测
    assert ab.memory_detection_step == 2
    assert ab.baseline_detection_step is None
    assert ab.early.status == "missing_detection"
    assert ab.early.missing_arm == "baseline"


def test_case_001_both_missing_na():
    """repeat_visitor→MONITOR 不跨检测阈值：两臂均不检测 → N/A（诚实，非记 0）。"""
    cases = {c.case_id: c for c in load_temporal_dataset(FIXTURE_ROOT)}
    ab = run_temporal_ab_case(cases["case_001_repeat_visitor"])
    assert ab.memory_detection_step is None
    assert ab.baseline_detection_step is None
    assert ab.early.status == "missing_detection"
    assert ab.early.missing_arm == "both"


def test_run_temporal_ab_case_requires_memory_input_on_last_step():
    """B1：末 step 无 Memory 输入应抛 RuntimeError（避免 final_memory 沿用上一轮旧值/leaky state）。"""
    cases = {c.case_id: c for c in load_temporal_dataset(FIXTURE_ROOT)}
    base = cases["case_001_repeat_visitor"]
    last = base.steps[-1]
    broken_last = TemporalStep(
        step=last.step,
        timestamp=last.timestamp,
        current_event=last.current_event,
        reasoning_input=None,
    )
    broken = TemporalCase(
        case_id=base.case_id,
        steps=(*base.steps[:-1], broken_last),
        ground_truth=base.ground_truth,
    )
    with pytest.raises(RuntimeError):
        run_temporal_ab_case(broken)


# ---------------------------------------------------------------------------
# evaluate_temporal_case：四指标 + Hard Gate 与 E-1a 一致
# ---------------------------------------------------------------------------
def test_evaluate_temporal_case_hard_gate_pass_and_early():
    cases = {c.case_id: c for c in load_temporal_dataset(FIXTURE_ROOT)}
    for cid, expect_status in (
        ("case_001_repeat_visitor", "missing_detection"),
        ("case_002_behavior_escalation", "computed"),
        ("case_003_conflict_transparency", "missing_detection"),
    ):
        ev = evaluate_temporal_case(cases[cid])
        assert ev.hard_gate_pass, f"{cid} Hard Gate 应 PASS"
        assert ev.early_detection.status == expect_status


# ---------------------------------------------------------------------------
# E-1c 报告：Early Detection 不再为 N/A，partial 加权
# ---------------------------------------------------------------------------
def test_run_e1c_report_early_detection_computed_and_partial():
    report = run_e1c_report(FIXTURE_ROOT)
    assert report.hard_gate.total == 3
    assert report.hard_gate.all_pass
    # Early Detection term 现在可计算（case_001 为 both-missing→N/A，从均值剔除）
    term = report.score.terms.early_detection
    assert term is not None
    # case_002=0.75 (45min/60min), case_003=1.0 (baseline-missing) → 均值 0.875
    assert term == pytest.approx(0.875)
    # 四 term 均存在（early_detection=0.875 非 None）→ 非 partial；Score 加权复合
    assert report.score.partial is False
    assert report.score.valid is True
    # 0.40*1 + 0.30*0.875 + 0.20*1 + 0.10*1 = 0.9625
    assert report.score.score == pytest.approx(0.9625)


def test_e1c_early_detection_term_matches_score():
    """early_detection_term 纯函数对三 case 评估的口径与报告一致。"""
    cases = load_temporal_dataset(FIXTURE_ROOT)
    evs = [evaluate_temporal_case(c) for c in cases]
    assert early_detection_term(evs) == pytest.approx(0.875)
    # 验证缺失臂方向：case_003 baseline-missing 贡献 +1.0（评审 issue 1）
    case003 = next(e for e in evs if e.case_id == "case_003_conflict_transparency")
    assert case003.early_detection.missing_arm == "baseline"


def test_cli_stage_e1c_returns_zero(tmp_path):
    code = main(["--stage", "e1c", "--fixtures", FIXTURE_ROOT, "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "e1_report.json").is_file()

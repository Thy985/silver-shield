"""ab_runner 端到端（E-1A 机制验证，DESIGN §2 / §6.1）。

验证：① build_baseline_input 严格清空历史；② A/B 变量隔离正确（Memory 臂有历史锚点、
Baseline 无）；③ 三 case 端到端跑通且 E-1A Hard Gate 全过。Early Detection 标记 N/A。
"""

from __future__ import annotations

from pathlib import Path

from home_perception.memory.consumer.replay_dataset import MemoryReplayDataset
from home_perception.memory.evaluation.ab_runner import (
    build_baseline_input,
    run_ab_case,
    run_ab_dataset,
)
from home_perception.memory.evaluation.ground_truth import e1a_case_ids, get_ground_truth
from home_perception.memory.evaluation.metrics import evaluate_case

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[2] / "fixtures" / "memory_replay"
)


def _dataset() -> MemoryReplayDataset:
    return MemoryReplayDataset(str(_FIXTURE_ROOT))


# ---------------------------------------------------------------------------
# build_baseline_input
# ---------------------------------------------------------------------------
def test_build_baseline_strips_history():
    case = _dataset().load("case_001_repeat_visitor")
    base = build_baseline_input(case.expected)
    assert base.historical_context == ()
    assert base.visitor_profile is None
    assert base.risk_pattern is None
    assert base.conflicts == ()
    assert base.previous_actions == ()
    # current_event 原样保留
    assert base.current_event == case.expected.current_event


# ---------------------------------------------------------------------------
# 变量隔离
# ---------------------------------------------------------------------------
def test_ab_variable_isolation():
    run = run_ab_case(_dataset().load("case_001_repeat_visitor"))
    assert run.result_memory.source_refs  # Memory 臂有溯源
    assert any(sr.source == "historical_context" for sr in run.result_memory.source_refs)
    # Baseline 臂无任何历史锚点
    assert not any(
        sr.source
        in {"visitor_profile", "risk_pattern", "historical_context", "conflicts", "previous_actions"}
        for sr in run.result_baseline.source_refs
    )


def test_run_ab_case_deterministic():
    case = _dataset().load("case_001_repeat_visitor")
    r1 = run_ab_case(case)
    r2 = run_ab_case(case)
    assert r1.result_memory.findings == r2.result_memory.findings
    assert r1.result_baseline.findings == r2.result_baseline.findings


# ---------------------------------------------------------------------------
# E-1A 三 case Hard Gate
# ---------------------------------------------------------------------------
def test_e1a_three_cases_hard_gate_pass():
    ds = _dataset()
    runs = run_ab_dataset(ds)
    assert {r.case_id for r in runs} == set(e1a_case_ids())
    for run in runs:
        gt = get_ground_truth(run.case_id)
        ev = evaluate_case(run.result_memory, run.result_baseline, gt)
        assert ev.hard_gate_pass is True, f"{run.case_id} Hard Gate 失败: {ev.notes}"


def test_e1a_each_case_metrics_detail():
    ds = _dataset()
    for run in run_ab_dataset(ds):
        gt = get_ground_truth(run.case_id)
        ev = evaluate_case(run.result_memory, run.result_baseline, gt)
        # 三 case 均应有历史依据的发现增益 + FP 不恶化 + FN 显著降低
        assert ev.q1 and ev.q2 and ev.q3
        assert ev.fp
        assert ev.fn_m == 0
        assert ev.fn_b >= 1
        assert ev.early_detection.status == "na"

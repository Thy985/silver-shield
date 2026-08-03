"""E-1A 顶层集成：dataset → A/B → 四指标 → 报告（DESIGN-memory-evaluation.md §6.1 / §9 / §11）。

这是 E-1 实验的**入口测试**：它回答「在现有 M0 三 case 上，加入 HistoricalContext
是否通过 Hard Gate」。与 ``test_ab_runner.py``（runner 层）、``test_report.py``
（报告纯函数层）分工不同，本文件只做端到端契约验证与产出物验证。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from home_perception.memory.consumer.replay_dataset import MemoryReplayDataset
from home_perception.memory.evaluation.ground_truth import e1a_case_ids
from home_perception.memory.evaluation.report import (
    BASE_WEIGHTS,
    build_report,
    evaluate_dataset,
    main,
    render_markdown,
    report_to_dict,
    run_e1a_report,
    write_report,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "memory_replay"


def _report(generated_at: str = "2026-08-03T00:00:00+00:00"):
    return run_e1a_report(str(_FIXTURE_ROOT), generated_at=generated_at)


# ---------------------------------------------------------------------------
# Hard Gate（§9）
# ---------------------------------------------------------------------------
def test_e1a_hard_gate_passes_on_all_three_cases():
    report = _report()
    assert report.stage == "E-1A"
    assert report.hard_gate.total == len(e1a_case_ids()) == 3
    assert report.hard_gate.all_pass is True, f"失败 case: {report.hard_gate.failed_case_ids}"
    assert report.hard_gate.failed_case_ids == ()


def test_e1a_every_case_reduces_false_negatives():
    for ev in evaluate_dataset(MemoryReplayDataset(str(_FIXTURE_ROOT))):
        assert ev.fn_m == 0, f"{ev.case_id} Memory 臂仍有漏报"
        assert ev.fn_b >= 1, f"{ev.case_id} Baseline 臂未体现漏报，A/B 失去区分度"
        assert ev.fn_m < ev.fn_b


def test_e1a_no_case_exceeds_acceptable_hint():
    """FP 不恶化：两臂 hint 均 ≤ GT 上限，且 Memory 臂无严重度超额。"""
    for ev in evaluate_dataset(MemoryReplayDataset(str(_FIXTURE_ROOT))):
        assert ev.fp is True
        assert ev.fp_excess == 0


# ---------------------------------------------------------------------------
# Early Detection data gate（§4.4）
# ---------------------------------------------------------------------------
def test_e1a_early_detection_is_na_and_excluded_from_score():
    report = _report()
    assert all(ev.early_detection.status == "na" for ev in report.cases)
    assert report.score.terms.early_detection is None
    assert report.score.partial is True
    assert "early_detection" not in report.score.weights
    assert sum(report.score.weights.values()) == pytest.approx(1.0)
    # 剩余三 term 按原比例重归一化（0.40 / 0.20 / 0.10 → 除以 0.70）
    remaining = BASE_WEIGHTS["fn"] + BASE_WEIGHTS["explanation"] + BASE_WEIGHTS["fp"]
    assert report.score.weights["fn"] == pytest.approx(BASE_WEIGHTS["fn"] / remaining)


def test_e1a_score_is_reported_but_not_calibrated():
    """Score 只做报告：未标定，且绝不参与 Hard Gate 判定。"""
    report = _report()
    assert report.score.calibrated is False
    assert 0.0 <= report.score.score <= 1.0
    assert "非 Hard Gate" in report.score.note


# ---------------------------------------------------------------------------
# 统计汇总（§8.1，E-1A 仅占位）
# ---------------------------------------------------------------------------
def test_e1a_stats_show_positive_fn_delta_with_placeholder_note():
    report = _report()
    assert report.stats.fn_delta.n == 3
    assert report.stats.fn_delta.mean > 0  # Memory 减少漏报
    assert report.stats.explanation_pass.mean == 1.0  # Q2/Q3 全过
    assert report.stats.wilcoxon_p is None  # E-1B 再引入
    assert "E-1B" in report.stats.note


def test_e1a_report_is_deterministic():
    assert _report().score.score == _report().score.score
    assert _report().hard_gate == _report().hard_gate


# ---------------------------------------------------------------------------
# 产出物（§11）
# ---------------------------------------------------------------------------
def test_e1a_writes_json_and_markdown(tmp_path):
    json_path, md_path = write_report(_report(), tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["hard_gate"]["all_pass"] is True
    assert len(payload["cases"]) == 3
    assert payload["score"]["terms"]["early_detection"] is None

    md = md_path.read_text(encoding="utf-8")
    for case_id in e1a_case_ids():
        assert case_id in md
    assert "✅ PASS" in md


def test_cli_returns_zero_when_hard_gate_passes(tmp_path, capsys):
    code = main(["--fixtures", str(_FIXTURE_ROOT), "--out", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Hard Gate: PASS" in out
    assert (tmp_path / "e1_report.json").exists()
    assert (tmp_path / "e1_report.md").exists()


def test_cli_returns_one_when_dataset_empty(tmp_path):
    """空数据集 → Hard Gate 不通过 → 退出码 1（可作 CI gate）。"""
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    code = main(["--fixtures", str(empty_root), "--out", str(tmp_path / "out")])
    assert code == 1


def test_empty_dataset_report_invalid_score_and_json_na():
    """评审 issue 2：空数据集报告必须显式标记 Score 无效，JSON 写 null 而非 0。"""
    report = build_report([], dataset_id="empty", generated_at="2026-08-03T00:00:00+00:00")
    assert report.hard_gate.all_pass is False
    assert report.score.valid is False
    assert report.score.score is None
    payload = report_to_dict(report)
    assert payload["score"]["score"] is None
    assert payload["score"]["valid"] is False
    assert "N/A" in render_markdown(report)

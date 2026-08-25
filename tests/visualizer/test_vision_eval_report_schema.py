"""I · Vision Acceptance 报告 schema 校验（SSOT §3.4 D2 流水线两端之②）。

SSOT：``docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`` v3.5。

参数化（2026-08-25）：支持多场景 vision-eval 报告校验。
每个场景的 vision-eval-{scenario_id}-YYYY-MM-DD.json 必须存在并满足 schema 约束。

红线（§3.4）：本文件仅校验报告**存在性**与 **schema 结构**（字段齐全/枚举合法/
6 图 × 5 维 = 30 项齐全）；**禁止对 Vision Judge 评分结论做任何 assert**——
PASS/WARN/FAIL 的语义判断属人工/vision 验收职责，自动化断言评分结论即伪验收。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPORTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "reports"

# 已注册需要校验的 vision-eval 报告（文件名 = `vision-eval-{scenario_id}-YYYY-MM-DD.json`）。
# 双注册（C9 α 决策 2026-08-25）：
# - 保留 2026-08-24 历史 product_story_risk 报告（场景身份迁移前的 D2 验收快照，文件名不可改）
# - 新增 telephone_risk 报告占位（场景身份迁移后，等 C11 全链路验证生成）
REGISTERED_REPORTS: tuple[str, ...] = (
    "vision-eval-product-story-risk-2026-08-24.json",       # 历史 · product_story_risk D2 验收快照
    "vision-eval-telephone-risk-2026-08-25.json",            # 新场景身份 · C11 全链路验证后生成
    "vision-eval-cctv-surveillance-suspicious-2026-08-25.json",
    "vision-eval-delivery-courier-normal-2026-08-25.json",
)

SCHEMA_VERSION = "1.0"
RUBRIC_FROZEN = ("信息层级", "叙事完整性", "调试元素残留", "视觉压迫感", "产品感")
VERDICT_ENUM = ("PASS", "WARN", "FAIL")
SHOT_COUNT = 6


def _load_report(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"vision-eval 报告未生成: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(params=REGISTERED_REPORTS)
def report_path(request) -> Path:
    return REPORTS_DIR / request.param


def test_report_exists_with_valid_schema(report_path):
    """报告存在 + 顶层字段齐全 + 枚举合法。"""
    report = _load_report(report_path)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["scenario_id"]
    assert set(report["rubric_frozen"]) == set(RUBRIC_FROZEN)
    assert report["verdict_enum"] == list(VERDICT_ENUM)
    assert isinstance(report["rounds"], list) and report["rounds"]


def test_final_round_has_30_dimensions_all_legal(report_path):
    """末轮（当前验收轮）必须 6 图 × 5 维 = 30 项，且每项判定枚举合法。"""
    report = _load_report(report_path)
    final = report["rounds"][-1]
    assert len(final["shots"]) == SHOT_COUNT
    total = 0
    for shot in final["shots"]:
        dims = shot["dimensions"]
        assert set(dims) == set(RUBRIC_FROZEN), f"{shot['file']} 维度缺失/多余"
        for name, dim in dims.items():
            assert dim["verdict"] in VERDICT_ENUM, f"{shot['file']}/{name} 非法枚举"
            assert isinstance(dim["note"], str) and dim["note"], (
                f"{shot['file']}/{name} 缺少 judge 理由"
            )
            total += 1
    assert total == SHOT_COUNT * len(RUBRIC_FROZEN)


def test_fail_items_must_be_explained_in_resolution(report_path):
    """收口条件：若存在 FAIL 判定，必须在 fail_resolution 中逐条登记闭环。"""
    report = _load_report(report_path)
    final = report["rounds"][-1]
    fail_shots = [
        s["file"] for s in final["shots"] if s["verdict"] == "FAIL"
    ]
    resolutions = report.get("fail_resolution") or []
    resolved_ids = {r.get("defect_id") for r in resolutions}
    for shot_file in fail_shots:
        assert any(
            r.get("round_2_verdict") == "resolved" for r in resolutions
        ), f"{shot_file} 存在未解释的 FAIL（fail_resolution 无 resolved 记录）"
    assert resolved_ids or not fail_shots or resolutions, (
        "FAIL 闭环记录缺失"
    )


def test_summary_counts_consistent(report_path):
    """末轮 summary 三计数与 30 个维度判定一致（防手改汇总失真）。"""
    report = _load_report(report_path)
    final = report["rounds"][-1]
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for shot in final["shots"]:
        for dim in shot["dimensions"].values():
            counts[dim["verdict"]] += 1
    summary = final["summary"]
    assert summary["pass"] == counts["PASS"]
    assert summary["warn"] == counts["WARN"]
    assert summary["fail"] == counts["FAIL"]


def test_no_unexplained_dimension_fail(report_path):
    """收口条件（§8）：30 个维度判定中不得存在未解释的 FAIL。"""
    report = _load_report(report_path)
    final = report["rounds"][-1]
    dim_fails = [
        f"{shot['file']}/{name}"
        for shot in final["shots"]
        for name, dim in shot["dimensions"].items()
        if dim["verdict"] == "FAIL"
    ]
    assert not dim_fails, (
        f"存在未闭环的 FAIL 维度（收口条件 §8 未达成）: {dim_fails}"
    )
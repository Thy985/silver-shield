"""ADR-0036 P0-4 · Suppression Reason / Negative Capability 卡（VM-1 纯展示）。

验证「系统为什么没报警」负向能力卡的数据链路与渲染：
- loader：canonical 顶层 ``suppress_reasons`` → ``ScenarioEvidence.suppress_reasons``
  （新增**可选** tuple 读取器，缺键/非法 → 空元组，保持旧 ``()`` 契约绿）；
- render：非空时渲染首屏负向能力卡，空时不渲染（VM-1 绝不伪造）；
- 端到端：注入顶层 ``suppress_reasons`` 的 canonical → 渲染 HTML 含「为什么没有报警」卡。

所有测试 hermetic、不依赖真实闭环 / 模型，CI 与本地一致、快速、可复现。
"""

from __future__ import annotations

import json
from pathlib import Path

from home_perception.visualizer.loader import (
    _str_tuple_field,
    load_evidence_projection,
)
from home_perception.visualizer.viewer import load_case_artifact, render_case_viewer
from home_perception.visualizer.viewer.render import _render_suppression_reason

from .conftest import (
    SUMMARY_FILENAME,
    _canonical,
    _fingerprints,
    _gate,
    make_artifacts,
)

# ---------------------------------------------------------------------------
# 1. 可选 tuple 读取器（_str_tuple_field）契约
# ---------------------------------------------------------------------------


def test_str_tuple_field_missing_key_returns_empty():
    assert _str_tuple_field({}, "suppress_reasons", "owner") == ()


def test_str_tuple_field_non_list_returns_empty():
    assert _str_tuple_field({"suppress_reasons": "nope"}, "suppress_reasons", "owner") == ()


def test_str_tuple_field_list_with_non_str_returns_empty():
    assert _str_tuple_field({"suppress_reasons": ["ok", 1]}, "suppress_reasons", "owner") == ()


def test_str_tuple_field_valid_returns_tuple():
    assert _str_tuple_field(
        {"suppress_reasons": ["no_trigger_events", "all_suppressed_normal"]},
        "suppress_reasons",
        "owner",
    ) == ("no_trigger_events", "all_suppressed_normal")


# ---------------------------------------------------------------------------
# 2. render：负向能力卡
# ---------------------------------------------------------------------------


def test_render_suppression_reason_empty_returns_empty_string():
    assert _render_suppression_reason({"suppress_reasons": ()}) == ""
    assert _render_suppression_reason({}) == ""


def test_render_suppression_reason_card_contains_label_and_tag():
    html = _render_suppression_reason({"suppress_reasons": ("no_trigger_events",)})
    assert "为什么没有报警" in html
    assert "no_trigger_events" in html  # 枚举值原值标签
    assert "真阴性" in html  # 人类可读映射
    assert "suppress-reason" in html
    assert "suppress-list" in html


def test_render_suppression_reason_unknown_reason_falls_back_to_raw():
    html = _render_suppression_reason({"suppress_reasons": ("custom_reason_xyz",)})
    assert "为什么没有报警" in html
    assert "custom_reason_xyz" in html  # 未知值回退为原值，不编造


def test_render_suppression_reason_multiple_reasons_all_rendered():
    html = _render_suppression_reason(
        {"suppress_reasons": ("no_trigger_events", "all_suppressed_normal")}
    )
    assert "no_trigger_events" in html
    assert "all_suppressed_normal" in html
    assert html.count("<li>") == 2


# ---------------------------------------------------------------------------
# 3. loader：canonical 顶层投影（含向后兼容）
# ---------------------------------------------------------------------------


def _write_artifact_dir(
    tmp_path: Path, *, top_level_reasons=None
) -> Path:
    """复用 conftest 的 ``_canonical`` 形状写出合法 artifact 树，可选注入顶层 suppress_reasons。"""
    d = tmp_path / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    sid = "sw_t1"
    canonical = _canonical(sid)
    if top_level_reasons is not None:
        canonical["suppress_reasons"] = top_level_reasons
    (d / f"{sid}.canonical.json").write_text(
        json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
    )
    (d / f"{sid}.gate.json").write_text(
        json.dumps(_gate(sid), ensure_ascii=False), encoding="utf-8"
    )
    (d / f"{sid}.fingerprints.json").write_text(
        json.dumps(_fingerprints(sid), ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "generated_at": "2026-08-16T00:00:00+00:00",
        "scenarios_dir": str(d),
        "scenarios": [
            {
                "scenario_id": sid,
                "path": f"src/.../{sid}.yaml",
                "ok": True,
                "failure_codes": [],
                "canonical_report": f"{d}/{sid}.canonical.json",
            }
        ],
    }
    (d / SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    return d


def test_loader_projects_top_level_suppress_reasons():
    d = _write_artifact_dir(
        Path("/tmp") / "p04_top", top_level_reasons=["no_trigger_events", "all_suppressed_normal"]
    )
    proj = load_evidence_projection(d)
    assert proj["scenarios"][0]["suppress_reasons"] == (
        "no_trigger_events",
        "all_suppressed_normal",
    )


def test_loader_backward_compat_no_top_level_key_is_empty():
    d = _write_artifact_dir(Path("/tmp") / "p04_bc")
    proj = load_evidence_projection(d)
    # 旧 artifact 无顶层键（只有 artifacts.suppress_reasons=[]）→ 空元组，向后兼容
    assert proj["scenarios"][0]["suppress_reasons"] == ()


# ---------------------------------------------------------------------------
# 4. 端到端：渲染 HTML 含负向能力卡
# ---------------------------------------------------------------------------


def test_render_html_contains_suppression_card(tmp_path: Path):
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    # 注入顶层 suppress_reasons（模拟场景 meta 声明）
    canon_path = d / "sw_t1.canonical.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    canon["suppress_reasons"] = ["no_trigger_events"]
    canon_path.write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    html = render_case_viewer(load_case_artifact(d))
    assert "为什么没有报警" in html
    assert "no_trigger_events" in html
    assert "真阴性" in html


def test_render_html_no_card_when_no_reasons(tmp_path: Path):
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = render_case_viewer(load_case_artifact(d))
    # 默认 fixture 无顶层 suppress_reasons → 不渲染负向能力卡
    # （注意：CSS 注释含「为什么没有报警」字样，故断言卡片节点 class 而非子串）
    assert 'class="suppress-reason"' not in html

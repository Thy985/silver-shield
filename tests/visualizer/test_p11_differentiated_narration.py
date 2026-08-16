"""ADR-0036 P1-1 · 场景差异化叙事带（VM-1 纯展示投影，零新数据）。

验证「结果自适应一句话」叙事带：
- 派生逻辑 ``_derive_narrative_kind``：从既有字段
  （recommended_actions / command_types / suppress_reasons）推导主导结果类型，
  优先级 suppressed > high_risk > repeated_visit > monitor > none；
- 渲染 ``_render_narrative_band``：非空时渲染带 severity 修饰的 hero 声明，
  None 时返回空串（VM-1 不编造）；
- 端到端：注入 canonical 信号 → 渲染 HTML 含对应 ``class="narrative-band sev-*"`` 元素。

所有测试 hermetic、不依赖真实闭环 / 模型，CI 与本地一致、快速、可复现。
"""

from __future__ import annotations

import json
from pathlib import Path

from home_perception.visualizer.viewer import load_case_artifact, render_case_viewer
from home_perception.visualizer.viewer.render import (
    _derive_narrative_kind,
    _render_narrative_band,
)

from .conftest import make_artifacts

# ---------------------------------------------------------------------------
# 1. 派生逻辑（_derive_narrative_kind）
# ---------------------------------------------------------------------------


def test_derive_suppressed_when_suppress_reasons_present():
    assert (
        _derive_narrative_kind({"suppress_reasons": ("no_trigger_events",)}) == "suppressed"
    )


def test_derive_high_risk_from_recommended_action():
    assert (
        _derive_narrative_kind({"recommended_actions": ("ESCALATE_COMMUNITY",)})
        == "high_risk"
    )


def test_derive_high_risk_from_command_type():
    assert (
        _derive_narrative_kind({"command_types": ("CREATE_COMMUNITY_TASK",)})
        == "high_risk"
    )


def test_derive_repeated_visit_from_recommended_action():
    assert (
        _derive_narrative_kind({"recommended_actions": ("NOTIFY_FAMILY",)})
        == "repeated_visit"
    )


def test_derive_repeated_visit_from_command_type():
    assert (
        _derive_narrative_kind({"command_types": ("SEND_FAMILY_MESSAGE",)})
        == "repeated_visit"
    )


def test_derive_monitor_from_recommended_action():
    assert _derive_narrative_kind({"recommended_actions": ("MONITOR",)}) == "monitor"


def test_derive_none_when_no_signal():
    assert (
        _derive_narrative_kind(
            {
                "recommended_actions": (),
                "command_types": ("LOG_ONLY",),
                "suppress_reasons": (),
            }
        )
        is None
    )


def test_derive_precedence_suppressed_beats_high_risk():
    # 即便同时含升级动作，负向能力（系统有意沉默）才是主导故事。
    assert (
        _derive_narrative_kind(
            {
                "suppress_reasons": ("no_trigger_events",),
                "recommended_actions": ("ESCALATE_COMMUNITY",),
            }
        )
        == "suppressed"
    )


def test_derive_precedence_high_risk_beats_repeated_visit():
    assert (
        _derive_narrative_kind(
            {
                "recommended_actions": ("ESCALATE_COMMUNITY", "NOTIFY_FAMILY"),
            }
        )
        == "high_risk"
    )


# ---------------------------------------------------------------------------
# 2. 渲染（_render_narrative_band）
# ---------------------------------------------------------------------------


def test_render_suppressed_band():
    html = _render_narrative_band({"suppress_reasons": ("no_trigger_events",)})
    assert 'class="narrative-band sev-suppressed"' in html
    assert "未触发风险（真阴性）" in html
    assert "主动保持沉默" in html


def test_render_high_risk_band():
    html = _render_narrative_band({"recommended_actions": ("ESCALATE_COMMUNITY",)})
    assert 'class="narrative-band sev-high_risk"' in html
    assert "高风险处置" in html
    assert "升级至社区协同处置" in html


def test_render_repeated_visit_band():
    html = _render_narrative_band({"recommended_actions": ("NOTIFY_FAMILY",)})
    assert 'class="narrative-band sev-repeated_visit"' in html
    assert "记忆驱动升级" in html
    assert "通知家属" in html


def test_render_monitor_band():
    html = _render_narrative_band({"recommended_actions": ("MONITOR",)})
    assert 'class="narrative-band sev-monitor"' in html
    assert "持续观察" in html


def test_render_empty_returns_empty_string():
    assert _render_narrative_band({}) == ""
    assert _render_narrative_band({"recommended_actions": (), "command_types": ("LOG_ONLY",)}) == ""


def test_render_precedence_reflected_in_band():
    # suppress_reasons 主导 → 渲染 suppressed 带，而非 high_risk 带。
    html = _render_narrative_band(
        {
            "suppress_reasons": ("no_trigger_events",),
            "recommended_actions": ("ESCALATE_COMMUNITY",),
        }
    )
    assert 'class="narrative-band sev-suppressed"' in html
    assert "sev-high_risk" not in html


# ---------------------------------------------------------------------------
# 3. 端到端：canonical 信号 → 渲染 HTML 含对应叙事带
# ---------------------------------------------------------------------------


def _write_with_signal(
    tmp_path: Path,
    *,
    recommended_actions=None,
    command_types=None,
    suppress_reasons=None,
) -> Path:
    """复用 conftest.make_artifacts 造合法 artifact 树，再注入叙事信号。

    recommended_actions / command_types 注入 canonical['artifacts']（loader 投影位置）；
    suppress_reasons 注入 canonical 顶层（P0-4 同构位置）。
    """
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    canon_path = d / "sw_t1.canonical.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    if recommended_actions is not None:
        canon["artifacts"]["recommended_actions"] = recommended_actions
    if command_types is not None:
        canon["artifacts"]["command_types"] = command_types
    if suppress_reasons is not None:
        canon["suppress_reasons"] = suppress_reasons
    canon_path.write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    return d


def test_e2e_high_risk_band_rendered():
    d = _write_with_signal(
        Path("/tmp") / "p11_hr",
        recommended_actions=["ESCALATE_COMMUNITY"],
        command_types=["CREATE_COMMUNITY_TASK"],
    )
    html = render_case_viewer(load_case_artifact(d))
    assert 'class="narrative-band sev-high_risk"' in html
    assert "高风险处置" in html


def test_e2e_repeated_visit_band_rendered():
    d = _write_with_signal(
        Path("/tmp") / "p11_rv",
        recommended_actions=["NOTIFY_FAMILY"],
    )
    html = render_case_viewer(load_case_artifact(d))
    assert 'class="narrative-band sev-repeated_visit"' in html
    assert "通知家属" in html


def test_e2e_suppressed_band_rendered():
    d = _write_with_signal(
        Path("/tmp") / "p11_sup",
        suppress_reasons=["no_trigger_events"],
    )
    html = render_case_viewer(load_case_artifact(d))
    assert 'class="narrative-band sev-suppressed"' in html
    assert "未触发风险（真阴性）" in html


def test_e2e_monitor_band_rendered():
    d = _write_with_signal(
        Path("/tmp") / "p11_mon",
        recommended_actions=["MONITOR"],
    )
    html = render_case_viewer(load_case_artifact(d))
    assert 'class="narrative-band sev-monitor"' in html


def test_e2e_default_fixture_maps_to_repeated_visit():
    # conftest 默认 canonical：recommended_actions=["NOTIFY_FAMILY"]，
    # command_types=["LOG_ONLY"]，suppress_reasons=[] → 应渲染 repeated_visit 带。
    d = make_artifacts(Path("/tmp") / "p11_def", scenario_ids=("sw_t1",))
    html = render_case_viewer(load_case_artifact(d))
    assert 'class="narrative-band sev-repeated_visit"' in html


def test_e2e_no_band_when_no_signal():
    d = _write_with_signal(
        Path("/tmp") / "p11_none",
        recommended_actions=[],
        command_types=["LOG_ONLY"],
        suppress_reasons=[],
    )
    html = render_case_viewer(load_case_artifact(d))
    # 无结果信号 → 不渲染任何叙事带（VM-1 不编造）。
    assert 'class="narrative-band' not in html

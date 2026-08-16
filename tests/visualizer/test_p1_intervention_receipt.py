"""P1 干预回执 + 闭环可达性（诚实边界）测试。

验收红线（ADR-0036 VM / AC 系列）：
- VM-1：回执卡纯派生自真实 ``command_types``，不新增事实。
- VM-9：不调用 ASR/LLM、不推导；映射是展示派生层（loader 死胡同叶子）。
- AC-12：全仓库无送达/时延/SLA 遥测 → 回执卡**绝不编造**送达时间 / 接收确认 / 时延 /
  60s 送达 / SLA；仅表征「已派发 + 目标接收方 + 待确认闭环」。
- 空派发 → 诚实空卡（不编造回执）。

Golden E2E：golden ``repeated_visit`` 声明的 ``family.required_state`` /
``community.required_state`` 必须等于我方回执卡闭环标签（零编造）；且 golden 决策层
``command_types: [NOTIFY_FAMILY]`` 是**决策词汇**，不得被混入 ActionCommand 映射。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from home_perception.visualizer.loader import (
    _INTERVENTION_CLOSURE,
    _INTERVENTION_TARGET_ROLE,
    _build_intervention_dispatch,
)
from home_perception.visualizer.viewer import (
    load_case_artifact,
    render_case_viewer,
)

from .conftest import make_artifacts

_GOLDEN_MANIFEST = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "golden"
    / "repeated_visit"
    / "manifest.yaml"
)


# ---------------------------------------------------------------------------
# 单元：_build_intervention_dispatch 映射（VM-1 纯派生 / 失败闭合）
# ---------------------------------------------------------------------------


def test_dispatch_maps_send_family_message():
    rows = _build_intervention_dispatch(("SEND_FAMILY_MESSAGE",))
    assert len(rows) == 1
    r = rows[0]
    assert r["command_type"] == "SEND_FAMILY_MESSAGE"
    assert r["target_role"] == "家属"
    assert r["closure_expectation"] == "family_handled"


def test_dispatch_maps_create_community_task():
    rows = _build_intervention_dispatch(("CREATE_COMMUNITY_TASK",))
    assert len(rows) == 1
    r = rows[0]
    assert r["target_role"] == "社区"
    assert r["closure_expectation"] == "community_done"


def test_dispatch_log_only_has_no_closure():
    rows = _build_intervention_dispatch(("LOG_ONLY",))
    assert len(rows) == 1
    r = rows[0]
    assert r["target_role"] == "系统（仅记录）"
    assert r["closure_expectation"] == ""


def test_dispatch_empty_returns_empty():
    assert _build_intervention_dispatch(()) == ()


def test_dispatch_unknown_type_is_fail_closed():
    rows = _build_intervention_dispatch(("MYSTERY_CMD",))
    assert len(rows) == 1
    r = rows[0]
    assert r["command_type"] == "MYSTERY_CMD"  # 仍以原始枚举呈现，不静默丢弃
    assert r["target_role"] == "未知接收方"
    assert r["closure_expectation"] == ""


def test_dispatch_dedup_preserves_first_seen_order():
    rows = _build_intervention_dispatch(
        ("SEND_FAMILY_MESSAGE", "CREATE_COMMUNITY_TASK", "SEND_FAMILY_MESSAGE")
    )
    assert [r["command_type"] for r in rows] == [
        "SEND_FAMILY_MESSAGE",
        "CREATE_COMMUNITY_TASK",
    ]


# ---------------------------------------------------------------------------
# 渲染：真实 loader + renderer 端到端（conftest 注入真实 command_types）
# ---------------------------------------------------------------------------


def _render_with_commands(tmp_path, command_types):
    d = make_artifacts(
        tmp_path / "art", scenario_ids=("sw_t1",), command_types=command_types
    )
    return render_case_viewer(load_case_artifact(d))


def test_render_receipt_card_with_family_and_community(tmp_path):
    html = _render_with_commands(
        tmp_path, ["SEND_FAMILY_MESSAGE", "CREATE_COMMUNITY_TASK"]
    )
    # 卡标题 + 双接收方 + 期望闭环标签
    assert "干预派发回执" in html
    assert "家属" in html
    assert "社区" in html
    assert "family_handled" in html
    assert "community_done" in html
    # 闭环可达性陈述存在
    assert "闭环可达性" in html
    # 诚实边界：明确声明不声称 60s 送达
    assert "不声称 60s 内送达" in html
    # 零编造：不得出现任何正向送达 / 时延确认（AC-12）
    assert "已送达" not in html
    assert "delivered" not in html.lower()
    assert "确认送达" not in html


def test_render_receipt_log_only_has_no_closure_labels(tmp_path):
    html = _render_with_commands(tmp_path, ["LOG_ONLY"])
    assert "干预派发回执" in html
    assert "系统（仅记录）" in html
    assert "无外部接收方（仅系统记录）" in html
    # LOG_ONLY 无外部接收方 → 不得出现 family/community 闭环标签
    assert "family_handled" not in html
    assert "community_done" not in html


def test_render_receipt_empty_shows_honest_empty_card(tmp_path):
    html = _render_with_commands(tmp_path, [])
    assert "本场景未派发任何干预指令（仅感知与记录）" in html
    assert "family_handled" not in html
    assert "community_done" not in html


# ---------------------------------------------------------------------------
# Golden E2E：闭环标签与 golden 声明一致（零编造）+ 决策词汇不入映射
# ---------------------------------------------------------------------------


def test_golden_closure_labels_match_manifest_required_state():
    """回执卡期望闭环标签必须 == golden 声明的 required_state（零编造）。"""
    manifest = yaml.safe_load(_GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    workflow = manifest["expected"]["workflow"]
    golden_family = workflow["family"]["required_state"]
    golden_community = workflow["community"]["required_state"]

    assert golden_family == "family_handled"
    assert golden_community == "community_done"
    # 我方映射产出的闭环标签必须等于 golden 声明值
    assert _INTERVENTION_CLOSURE["SEND_FAMILY_MESSAGE"] == golden_family
    assert _INTERVENTION_CLOSURE["CREATE_COMMUNITY_TASK"] == golden_community


def test_golden_action_uses_decision_vocab_not_actioncommand_type():
    """golden 决策层 command_types 用 NOTIFY_FAMILY（决策词汇），不混入 ActionCommand 映射。"""
    manifest = yaml.safe_load(_GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    golden_cmd_types = manifest["expected"]["action"]["command_types"]
    assert golden_cmd_types == ["NOTIFY_FAMILY"]

    # 决策词汇不是 ActionCommand 类型 → 回执绝不据此编造接收方/闭环（fail-closed）
    assert _INTERVENTION_TARGET_ROLE.get("NOTIFY_FAMILY") is None
    assert _INTERVENTION_CLOSURE.get("NOTIFY_FAMILY", "") == ""


def test_golden_e2e_render_family_community_closure(tmp_path):
    """golden 场景（repeated_visit）若派发 family + community，回执卡应呈现与 golden
    required_state 一致的闭环标签，且不声称 60s 送达。"""
    manifest = yaml.safe_load(_GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    golden_family = manifest["expected"]["workflow"]["family"]["required_state"]
    golden_community = manifest["expected"]["workflow"]["community"]["required_state"]

    html = _render_with_commands(
        tmp_path, ["SEND_FAMILY_MESSAGE", "CREATE_COMMUNITY_TASK"]
    )
    assert golden_family in html
    assert golden_community in html
    assert "不声称 60s 内送达" in html
    assert "已送达" not in html

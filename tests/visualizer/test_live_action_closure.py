"""ADR-0036 P0-1 · 人类处置闭环契约测试（Projection 不回写 / VM-6）。

覆盖验收：
- ``ProjectionAccumulator.ingest_resolution`` 摄入 Resolution 事实（新事件）→
  ``to_evidence_projection()`` 重新构造 → Evidence Timeline 出现 ACTION 节点
  （provenance=REAL_SENSOR、ref=live://resolution/N、人话 summary）——**非 mutate projection**；
- fail-closed：非 dict / 缺 warning_id / status 非 community_done → LiveIngestError；
- ``build_live_presentation``：panels 含 ``action_closure`` + ``live_ws_path`` 纯展示元数据；
- Case Viewer 渲染：Live 模式含行动闭环面板（家属/社区按钮 + 状态徽章 + data-ws-path +
  live_actions.js 注入）；Artifact 模式无此面板、无注入。

不依赖 torch / cv2（纯 stdlib + 投影契约 fixture）。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.viewer.live_adapter import (
    LiveIngestError,
    ProjectionAccumulator,
    build_live_presentation,
)
from home_perception.visualizer.viewer.render import render_case_viewer


def _make_frame(frame_index, *, n_detections=0, risk_levels=(), command_types=()):
    """构造 FrameResult 契约的 dict 形态（鸭子类型摄入）。"""
    return {
        "frame_index": frame_index,
        "n_detections": n_detections,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [
            {"risk_level": rl, "recommended_action": "MONITOR"} for rl in risk_levels
        ],
        "commands": [{"command_type": ct} for ct in command_types],
    }


def _resolution_fact(warning_id="7f3a9c21-0000-4000-8000-000000000001", **overrides):
    fact = {
        "warning_id": warning_id,
        "operator": "community",
        "action": "complete",
        "status": "community_done",
    }
    fact.update(overrides)
    return fact


def _live_projection(acc: ProjectionAccumulator):
    return acc.to_evidence_projection()


# ---------------------------------------------------------------------------
# ingest_resolution：事实事件 → 重新投影出 ACTION 节点（Projection 不回写）
# ---------------------------------------------------------------------------


def test_ingest_resolution_projects_action_node():
    """Resolution 事实摄入 → 重新投影出现 ACTION 节点（REAL_SENSOR / live://resolution/1）。"""
    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0, risk_levels=["HIGH"]))
    proj = _live_projection(acc)
    assert all(n["type"] != "resolution" for n in proj["scenarios"][0]["timeline"])

    acc.ingest_resolution(_resolution_fact())
    proj2 = _live_projection(acc)
    timeline = proj2["scenarios"][0]["timeline"]
    resolution_nodes = [n for n in timeline if n["type"] == "resolution"]
    assert len(resolution_nodes) == 1
    node = resolution_nodes[0]
    assert node["modality"] == "ACTION"
    assert node["provenance_kind"] == "REAL_SENSOR"
    assert node["ref"] == "live://resolution/1"
    assert node["stage"] == "action"
    # 人话 summary 含 warning 前缀与处置方
    assert "处置完成" in node["summary"]
    assert "7f3a9c21" in node["summary"]
    assert "community" in node["summary"]


def test_ingest_resolution_deterministic_reprojection():
    """VM-8 幂等：同一有序流（帧 + resolution）重放 N 次 → 投影逐字段一致。"""
    acc1 = ProjectionAccumulator("sw_live1")
    acc1.ingest(_make_frame(0, risk_levels=["HIGH"]))
    acc1.ingest_resolution(_resolution_fact())

    acc2 = ProjectionAccumulator("sw_live1")
    acc2.ingest(_make_frame(0, risk_levels=["HIGH"]))
    acc2.ingest_resolution(_resolution_fact())

    p1 = _live_projection(acc1)
    p2 = _live_projection(acc2)
    assert p1 == p2


def test_ingest_resolution_does_not_mutate_previous_projection():
    """Projection 不回写（VM-6）：旧 projection 对象不受后续摄入影响（每次重新构造）。"""
    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0))
    old_proj = _live_projection(acc)
    acc.ingest_resolution(_resolution_fact())
    new_proj = _live_projection(acc)
    # 旧投影是独立构造的快照：不含 resolution 节点（未被动过）。
    assert all(n["type"] != "resolution" for n in old_proj["scenarios"][0]["timeline"])
    # 新投影重新构造后才有 resolution 节点。
    assert any(n["type"] == "resolution" for n in new_proj["scenarios"][0]["timeline"])


def test_ingest_resolution_fail_closed():
    """fail-closed：非 dict / 缺 warning_id / status 非 community_done → LiveIngestError。"""
    acc = ProjectionAccumulator("sw_live1")
    with pytest.raises(LiveIngestError):
        acc.ingest_resolution("not-a-dict")
    with pytest.raises(LiveIngestError):
        acc.ingest_resolution(_resolution_fact(warning_id=""))
    with pytest.raises(LiveIngestError):
        acc.ingest_resolution(_resolution_fact(status="family_handled"))
    with pytest.raises(LiveIngestError):
        acc.ingest_resolution(_resolution_fact(operator=123))
    # 合法摄入后无残留错误状态（失败不污染）。
    acc.ingest_resolution(_resolution_fact())
    assert any(
        n["type"] == "resolution"
        for n in _live_projection(acc)["scenarios"][0]["timeline"]
    )


# ---------------------------------------------------------------------------
# build_live_presentation：action_closure 面板 + live_ws_path 纯展示元数据
# ---------------------------------------------------------------------------


def test_build_live_presentation_has_action_closure_panel():
    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0, risk_levels=["HIGH"]))
    proj = _live_projection(acc)
    _proj, desc = build_live_presentation(proj, live_ws_path="/ws")
    assert "action_closure" in desc["first_screen_layout"]["panels"]
    # live_ws_path 是纯展示元数据（非事实字段）。
    assert desc["live_ws_path"] == "/ws"


def test_build_live_presentation_default_ws_path():
    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0))
    _proj, desc = build_live_presentation(_live_projection(acc))
    assert desc.get("live_ws_path", "/ws") == "/ws"


# ---------------------------------------------------------------------------
# Case Viewer 渲染：Live 含行动闭环面板；Artifact 不含
# ---------------------------------------------------------------------------


def test_render_live_has_action_closure_panel():
    """Live 渲染：行动闭环面板（按钮/徽章/data-ws-path）+ live_actions.js 注入。"""
    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0, risk_levels=["HIGH"]))
    proj = _live_projection(acc)
    _proj, desc = build_live_presentation(proj, live_ws_path="/ws")
    html = render_case_viewer(proj, desc)

    assert 'id="fs-action-closure-sw_live1"' in html
    assert "我知道了" in html and "通知社区" in html
    assert "接受任务" in html and "完成处置" in html
    assert 'data-ws-path="/ws"' in html
    # live_actions.js 内联注入（JS 内容特征：__LiveActions 全局）。
    assert "__LiveActions" in html
    # 按钮/徽章为 UI/Workflow 态：不进 EvidenceProjection 事实（timeline 无 resolution 前
    # 面板仍是交互骨架）。
    assert "closure-family-status-sw_live1" in html
    assert "closure-community-status-sw_live1" in html


def test_render_artifact_mode_no_action_closure():
    """Artifact 模式（默认 descriptor 无 action_closure 面板）→ 不渲染交互、不注入 JS。"""
    from home_perception.visualizer.viewer.case_presentation import (
        build_default_case_presentation,
    )

    acc = ProjectionAccumulator("sw_live1")
    acc.ingest(_make_frame(0))
    proj = _live_projection(acc)
    desc = build_default_case_presentation(proj)
    assert "action_closure" not in desc["first_screen_layout"]["panels"]
    html = render_case_viewer(proj, desc)
    assert "行动闭环" not in html
    assert "__LiveActions" not in html

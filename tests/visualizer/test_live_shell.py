"""Live 产品骨架（DESIGN-live-product-ui-restore PR-A）渲染契约测试。

覆盖：
- live_frame_stream=True → 阶段叙事 tabs（3 个，可直切）+ 6 区域 12 列 Grid +
  Live 帧流容器（video-img / LIVE badge / overlay）+ header（真实性声明 + WS pill）；
- tab ②/③ 角色聚焦视图（家属/社区按钮 + closure-tabview 共享状态机钩子）；
- ③.5 实时风险信号 / ⑥ Memory Context 诚实占位（AC-12 不编造）；
- Artifact 模式（live_frame_stream=False）→ 无 tabs / 无 pill / 无 Grid（零影响）；
- 事实架构红线：无旧 {"type":"frame","view":{...}} 协议残留。
"""

from __future__ import annotations

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.live_adapter import (
    ProjectionAccumulator,
    build_live_presentation,
)
from home_perception.visualizer.viewer.render import _render_provenance_banner


def _frame(frame_index: int, *, risk_levels=()) -> dict:
    return {
        "frame_index": frame_index,
        "n_detections": 1,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [
            {"risk_level": rl, "recommended_action": "MONITOR"} for rl in risk_levels
        ],
        "commands": [],
    }


def _live_html() -> str:
    acc = ProjectionAccumulator("live_shell_t")
    acc.ingest(_frame(0, risk_levels=("HIGH",)))
    proj, desc = build_live_presentation(
        acc.to_evidence_projection(), live_ws_path="/ws"
    )
    return render_case_viewer(proj, desc, live_frame_stream=True)


def test_live_shell_has_tabs_and_grid_regions():
    """PR-A：三个 tab 可直切 + 6 区域 12 列 Grid + Live 帧流容器立起来。"""
    html = _live_html()
    # 阶段叙事 tabs（默认① active，②③ 可直切）
    assert 'class="tab active" data-view="discover"' in html
    assert 'class="tab" data-view="family"' in html
    assert 'class="tab" data-view="community"' in html
    assert "① 风险发现" in html and "② 家属确认" in html and "③ 社区处置" in html
    # 三视图容器（②③ 默认 hidden）
    assert 'id="view-discover-live_shell_t"' in html
    assert 'id="view-family-live_shell_t" hidden' in html
    assert 'id="view-community-live_shell_t" hidden' in html
    # 6 区域 Grid
    for cls in ("lv-video", "lv-risk", "lv-timeline", "lv-signal", "lv-closure", "lv-memory"):
        assert f'region {cls}' in html, cls
    # ① Live 帧流容器（LP-1 既有契约不回退）
    assert 'id="video-img-live_shell_t"' in html
    assert 'id="live-badge-live_shell_t"' in html
    assert 'id="ov-frame-live_shell_t"' in html
    assert 'id="ov-det-live_shell_t"' in html
    # ④ 行动闭环面板（P0-1 既有契约）
    assert 'id="fs-action-closure-live_shell_t"' in html
    assert 'data-ws-path="/ws"' in html


def test_live_shell_header_auth_badge_and_ws_pill():
    """PR-A：header 恢复真实性声明角标 + WS 连接 pill（live_stream.js 维护）。"""
    html = _live_html()
    assert "Demo 数据真实性声明" in html
    assert "受控演示输入" in html
    assert 'class="pill offline" id="ws-pill"' in html
    assert 'id="ws-text">未连接' in html
    # live_stream.js pill 维护钩子存在
    assert "ws-pill" in html and "已连接" in html


def test_live_shell_role_views_share_closure_state_machine():
    """tab ②/③ 角色视图：closure-tabview 钩子 + 角色按钮（共享 WS，切 Tab 不重连）。"""
    html = _live_html()
    assert 'class="closure-tabview" data-role="family"' in html
    assert 'class="closure-tabview" data-role="community"' in html
    assert 'id="tabview-family-ack-live_shell_t"' in html
    assert 'id="tabview-family-notify-live_shell_t"' in html
    assert 'id="tabview-community-accept-live_shell_t"' in html
    assert 'id="tabview-community-complete-live_shell_t"' in html
    assert 'id="tabview-family-status-live_shell_t"' in html
    assert 'id="tabview-community-status-live_shell_t"' in html
    # live_actions.js 同步渲染钩子（_renderTabViews）
    assert "_renderTabViews" in html


def test_live_shell_honest_placeholders():
    """③.5 风险信号 / ⑥ Memory 诚实占位（AC-12 不编造 visitor profile / 风险信号）。"""
    html = _live_html()
    assert 'id="live-signals-live_shell_t"' in html
    assert "当前无进行中风险信号" in html
    assert "Memory Context" in html
    assert "当前案例无历史事件可供引用" in html  # 诚实标注未接入，不编造 profile


def test_live_shell_risk_card_skeleton():
    """PR-B：③ 风险解释卡片骨架（✓ 人话原因容器 + 空态"实时观察中"），risk_delta 驱动。"""
    html = _live_html()
    # 卡片骨架（初始隐藏，risk_delta.risk_levels 非空时亮起）
    assert 'id="lrk-card-live_shell_t"' in html
    assert 'id="lrk-level-live_shell_t"' in html
    assert 'id="lrk-reasons-live_shell_t"' in html
    assert 'id="lrk-rec-live_shell_t"' in html
    assert 'id="lrk-frame-live_shell_t"' in html
    # 空态（诚实边界 AC-12：无风险 → 实时观察中，非"无数据"）
    assert 'id="lrk-empty-live_shell_t"' in html
    assert "实时观察中" in html and "风险尚未触发" in html
    # ③.5 信号容器（risk_transition 服务端状态机驱动）
    assert 'id="live-signals-empty-live_shell_t"' in html


def test_live_shell_artifact_mode_unaffected():
    """Artifact 模式（live_frame_stream=False）→ 无 tabs / pill / Grid（零影响）。"""
    acc = ProjectionAccumulator("artifact_t")
    acc.ingest(_frame(0))
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    html = render_case_viewer(proj, desc, live_frame_stream=False)
    assert "data-live-tabs" not in html
    assert 'id="ws-pill"' not in html
    assert 'class="live-grid"' not in html
    assert 'class="closure-tabview"' not in html
    assert "__LiveTabs" not in html
    # 瀑布流面板仍在（fs-panel 路径不变）
    assert 'id="fs-case-video-artifact_t"' in html


def test_live_shell_no_legacy_view_protocol():
    """红线：无旧 {"type":"frame","view":{...}} 协议 / bridge view model 残留。"""
    html = _live_html()
    assert '"view":{' not in html.replace(" ", "")
    assert "frame_result_to_view" not in html
    assert "demo_time" not in html  # Owner 锁死：不新增 demo_time 字段


def test_live_shell_overlay_chips_pr_c():
    """PR-C：① overlay chips 补全 Case Time + 访客事件（DESIGN §4.3）。"""
    html = _live_html()
    assert 'id="ov-frame-live_shell_t"' in html
    assert 'id="ov-time-live_shell_t"' in html   # 新增 Case Time chip
    assert 'id="ov-det-live_shell_t"' in html
    assert 'id="ov-ve-live_shell_t"' in html     # 新增访客事件 chip
    assert "Case Time" in html
    assert "访客事件" in html


def test_live_shell_closure_summary_pr_c():
    """PR-C：④ 行动轻量摘要骨架（cs-family / cs-community hooks，live_actions.js 驱动）。"""
    html = _live_html()
    assert 'id="closure-summary-live_shell_t"' in html
    assert 'id="cs-family-live_shell_t"' in html
    assert 'id="cs-community-live_shell_t"' in html
    assert "家属" in html and "社区" in html


def test_live_shell_sysarch_foldable_pr_c():
    """PR-C：⑤ 系统原理折叠区（SVG 架构图 · 次级模块，默认折叠）。"""
    html = _live_html()
    assert 'class="lv-sysarch"' in html
    assert 'id="lv-sysarch-live_shell_t"' in html
    assert "⑤ 系统原理" in html
    assert "How it works" in html
    assert 'class="sysarch-svg"' in html
    # 三框：Home 端感知内核 / Demo Gateway / Case Viewer
    assert "Home 端感知内核" in html
    assert "Demo Gateway" in html
    assert "Case Viewer" in html


def test_live_shell_memory_msg_element_pr_c():
    """PR-C：⑥ Memory Context 诚实占位（dev 态文案可被前端切换展示态）。"""
    html = _live_html()
    assert 'id="memory-msg-live_shell_t"' in html
    # dev 态默认文案：Not connected（诚实标注未接入）
    assert "当前案例无历史事件可供引用" in html


def _scenario_with_provenance(kind: str) -> dict:
    """最小 ScenarioEvidence：仅含一条带指定 provenance_kind 的 timeline 节点。"""
    return {"timeline": [{"provenance_kind": kind}]}


def test_live_banner_honest_controlled_demo_label():
    """T1.1：Live（REAL_SENSOR）角标诚实标注「受控演示输入」+ 副标题，不再标榜 REAL SENSOR。

    验收（DESIGN-golden-case-live-product P0-1 / TASKS T1.1）：
    - 产物含「受控演示输入」与副标题「非 7×24 真实设备 · 演示素材」；
    - 不再含误导的「REAL SENSOR」字样。
    """
    html = _render_provenance_banner(_scenario_with_provenance("REAL_SENSOR"))
    assert "受控演示输入" in html
    assert "非 7×24 真实设备 · 演示素材" in html
    assert "REAL SENSOR" not in html


def test_artifact_banner_simulated_unchanged():
    """T1.1：Artifact（SIMULATED）角标文案不变（「● GOLDEN CASE · SIMULATED」），且无 Live 副标题。"""
    html = _render_provenance_banner(_scenario_with_provenance("SIMULATED"))
    assert "● GOLDEN CASE · SIMULATED" in html
    assert "非 7×24" not in html

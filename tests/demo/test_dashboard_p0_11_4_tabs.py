"""P0-11.4 阶段叙事 Tab · 结构性合约测试（torch-free · ADR-0017）。

P0-11.4 把 Dashboard 重组为「发现 → 确认 → 处置」三视图 Tab，共享同一
DemoAggregateState（切 Tab 不重订 WS）。本测试守结构性合约：

1. Tab 导航存在且命名是**阶段**（不是角色）。
2. ② 家属确认 / ③ 社区处置 视图区域存在且默认 hidden。
3. ``switchTab`` 不重订 WS（设计 D4：函数体内无 ``new WebSocket(``）。
4. ``bridge.route_commands`` 保留 ``warning_id``（数据流基础：同一 warning_id 流过三视图）。

端到端「同一 warning_id 流过三视图」由集成 + 5b 剧本手测保证。
"""
from __future__ import annotations

import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[2] / "src" / "silver_demo" / "dashboard" / "index.html"


def _read() -> str:
    assert DASHBOARD.is_file(), f"Dashboard 文件不存在: {DASHBOARD}"
    return DASHBOARD.read_text(encoding="utf-8")


def test_tab_nav_has_three_stage_tabs():
    """阶段叙事 Tab 导航：① 风险发现 / ② 家属确认 / ③ 社区处置。"""
    html = _read()
    assert 'id="role-tabs"' in html, "缺少 #role-tabs 导航"
    for label in ("① 风险发现", "② 家属确认", "③ 社区处置"):
        assert label in html, f"Tab 标签缺失: {label!r}"


def test_tab_views_exist_with_hidden_default():
    """② / ③ 视图区域存在且默认 hidden（避免 Tab 1 之前闪现）。"""
    html = _read()
    assert 'id="view-family"' in html, "缺少 #view-family"
    assert 'id="view-community"' in html, "缺少 #view-community"
    # 默认 hidden
    assert re.search(r'<section[^>]*id="view-family"[^>]*\bhidden\b', html), "#view-family 应默认 hidden"
    assert re.search(r'<section[^>]*id="view-community"[^>]*\bhidden\b', html), "#view-community 应默认 hidden"


def test_switchTab_does_not_reconnect_websocket():
    """切 Tab 不重订 WS（设计 D4）：switchTab 函数体内无 ``new WebSocket(`` 调用。"""
    html = _read()
    idx = html.find("function switchTab")
    assert idx > -1, "switchTab 函数未找到"
    # 取函数后 ~800 字符窗口（switchTab 约 15 行，远小于此）
    window = html[idx:idx + 800]
    assert "new WebSocket" not in window, (
        "switchTab 内出现 new WebSocket(，违反「切 Tab 不重订 WS」设计（D4）"
    )


def test_tab_view_button_delegation_targets_real_ids():
    """回归：Tab ②/③ 按钮事件委托必须绑定到真实存在的 list 元素 ID。

    Bug：委托曾写作 view-family-list / view-community-list，而 DOM 中真实 ID 是
    tab-family-list / tab-community-list，导致 $() 返回 null、委托静默跳过、
    按钮点击无响应（sendAction 永不触发）。断言委托数组引用的 ID 在 DOM 中确实存在，
    且旧错误前缀不复存在。
    """
    html = _read()
    # DOM 中定义真实 list 元素 ID
    assert 'id="tab-family-list"' in html
    assert 'id="tab-community-list"' in html
    # 委托数组必须使用真实 ID
    assert '"tab-family-list"' in html, "按钮委托未绑定 tab-family-list"
    assert '"tab-community-list"' in html, "按钮委托未绑定 tab-community-list"
    # 旧错误前缀不得再出现（静默跳过型 bug，必须有测试拦截）
    assert "view-family-list" not in html, "仍存在错误的 view-family-list 委托目标"
    assert "view-community-list" not in html, "仍存在错误的 view-community-list 委托目标"


def test_routed_commands_preserve_warning_id():
    """``route_commands`` 保留 ``warning_id``：同一 warning_id 在三视图中可关联。"""
    from silver_demo.bridge import route_commands

    cmds = [
        {"command_id": "c1", "warning_id": "w-xyz", "command_type": "SEND_FAMILY_MESSAGE"},
        {"command_id": "c2", "warning_id": "w-xyz", "command_type": "CREATE_COMMUNITY_TASK"},
        {"command_id": "c3", "warning_id": "w-abc", "command_type": "LOG_ONLY"},
    ]
    routed = route_commands(cmds)
    # 同一 warning_id w-xyz 同时出现在 family 和 community 桶 → ②/③ 可联动
    assert routed["family"][0]["warning_id"] == "w-xyz"
    assert routed["community"][0]["warning_id"] == "w-xyz"
    assert routed["log_only"][0]["warning_id"] == "w-abc"
    # 三桶都存在
    assert set(routed.keys()) == {"family", "community", "log_only"}

"""P0-11.4 Dashboard 深化契约测试。

锁定「区域③ 风险解释卡片 + 区域④ 三端任务卡联动」所需的数据契约与前端锚点：
1. ``bridge.route_commands`` 必须保留每个命令的 ``warning_id`` 与 ``payload``
   —— Dashboard 据此把 家属/社区 任务卡 与 风险卡片 按 warning_id 联动渲染。
2. ``dashboard/index.html`` 必须含 P0-11.4 深化渲染锚点（人话原因 / 触发规则 /
   规则命中强度 / 三端任务卡 / commandMap 联动），防止回归为骨架。

P0-11.4 是纯前端深化：网关已在每帧广播 ``active_warnings`` 与 ``routed_commands``
（含完整 payload + warning_id），本测试只锁契约，不改动后端。
"""
from __future__ import annotations

from pathlib import Path

SILVER_DEMO = Path(__file__).resolve().parents[2] / "src" / "silver_demo"
DASHBOARD = SILVER_DEMO / "dashboard" / "index.html"


def _sample_commands():
    """贴近 dispatcher.py 真实 payload 的命令样本。"""
    return [
        {
            "command_id": "c-fam-1",
            "warning_id": "w-1",
            "command_type": "SEND_FAMILY_MESSAGE",
            "payload": {
                "topic": "x/notify_family",
                "contact": {
                    "elder_id": "elder_01",
                    "name": "张三",
                    "phone": "13800001111",
                    "relation": "儿子",
                },
                "message": "【银龄盾告警】门前检测到：异常停留。风险等级：LOW。建议核实情况。",
            },
            "status": "PENDING",
        },
        {
            "command_id": "c-com-1",
            "warning_id": "w-1",
            "command_type": "CREATE_COMMUNITY_TASK",
            "payload": {
                "endpoint": "community-mock",
                "elder_id": "elder_01",
                "risk_level": "LOW",
                "reasons": ["异常停留"],
                "perception_score": 0.72,
            },
            "status": "PENDING",
        },
        {
            "command_id": "c-log-1",
            "warning_id": "w-2",
            "command_type": "LOG_ONLY",
            "payload": {"device_id": "d1", "risk_level": "LOW", "reason_summary": ["异常时段访问"]},
            "status": "PENDING",
        },
    ]


def test_route_commands_preserves_linkage_keys() -> None:
    """routed 命令必须保留 warning_id 与 payload，供 Dashboard 按 warning_id 联动渲染。"""
    from silver_demo.bridge import route_commands

    routed = route_commands(_sample_commands())
    for wid in ("w-1", "w-2"):
        found = False
        for grp in ("family", "community", "log_only"):
            for c in routed[grp]:
                assert "warning_id" in c, "routed 命令丢失 warning_id（联动断裂）"
                assert "payload" in c, "routed 命令丢失 payload（任务卡无内容）"
                if c["warning_id"] == wid:
                    found = True
        assert found, f"warning_id={wid!r} 未在任何路由桶中出现"

    # warning_id=w-1 同时驱动 家属 + 社区 两张任务卡（联动核心）
    fam = [c for c in routed["family"] if c["warning_id"] == "w-1"]
    com = [c for c in routed["community"] if c["warning_id"] == "w-1"]
    assert fam and com, "同一 warning 应同时联动 家属 + 社区 任务卡"
    assert fam[0]["payload"]["message"]
    assert com[0]["payload"]["reasons"]


def test_dashboard_contains_p0_11_4_anchors() -> None:
    """dashboard/index.html 必须含 P0-11.4 深化锚点，防止回归为骨架。"""
    html = DASHBOARD.read_text(encoding="utf-8")
    anchors = [
        "rc-reason",        # 区域③ 人话原因
        "rc-triggers",      # 区域③ 触发规则
        "trig-chip",        # 区域③ 触发规则 chip
        "规则命中强度",      # 区域③ perception_score 标注（非诈骗概率）
        "task family",      # 区域④ 家属端任务卡
        "task community",   # 区域④ 社区端任务卡
        "commandMap",       # 区域④ 按 warning_id 联动累积
        "lookupCommands",   # 区域④ 取任务卡
        "cl-reasons",       # 区域④ 风险原因联动
    ]
    missing = [a for a in anchors if a not in html]
    assert not missing, f"dashboard 缺少 P0-11.4 锚点：{missing}"

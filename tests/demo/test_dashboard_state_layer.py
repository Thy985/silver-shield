"""P0-11.3 Dashboard 状态层契约测试（从"事实生产"聚合出"用户认知"）。

背景：网关每帧只广播「当前帧」的 ``active_warnings`` / ``perception_events``，其余
98%+ 的帧为空。若 Dashboard 逐帧覆盖，风险卡（区域③④）会"闪现即消失"、行为
时间线（区域②）几乎全程空转。P0-11.3 在 **展示层** 增加状态聚合层修复：

1. Warning 保活：``warningMap`` 按 ``warning_id`` 跨帧 upsert，终态移除、超上限修剪；
   区域③④ 改读 ``activeWarningList()`` 聚合列表 → 风险卡稳定常驻、③④ 同源联动。
2. AI 行为时间线：``ingestBehavior`` 跨帧累积去重 ``perception_events`` + ``warnings``
   为「访客行为里程碑」（首次出现 / 停留超阈值 / 再次出现 / 高风险逼近 / 生成预警），
   ``visitor_id`` → "访客#N" 友好名，体现 Tracking/Event/Feature 演化价值。

本层纯前端（silver_demo/dashboard），零改动 home_perception 冻结契约，故只锁前端锚点。
"""
from __future__ import annotations

from pathlib import Path

SILVER_DEMO = Path(__file__).resolve().parents[2] / "src" / "silver_demo"
DASHBOARD = SILVER_DEMO / "dashboard" / "index.html"


def test_dashboard_has_warning_keepalive_anchors() -> None:
    """区域③④ 必须经 warningMap 保活 + activeWarningList 聚合，而非逐帧覆盖。"""
    html = DASHBOARD.read_text(encoding="utf-8")
    anchors = [
        "warningMap",           # 按 warning_id 跨帧保活
        "ingestWarnings",       # 每帧 upsert + 终态移除
        "pruneWarnings",        # 超上限按 created_at 修剪
        "activeWarningList",    # 区域③④ 共用的聚合列表
    ]
    missing = [a for a in anchors if a not in html]
    assert not missing, f"dashboard 缺少 Warning 保活锚点：{missing}"

    # 关键回归防线：区域③④ 不得再直接读逐帧 activeWarnings（那正是"闪现"根因）
    assert "state.activeWarnings" not in html, (
        "区域③④ 仍在读逐帧 state.activeWarnings —— 会导致风险卡闪现即消失（P0-11.3 回归）"
    )
    # renderRisks / renderClosure 必须改用聚合列表
    assert html.count("activeWarningList()") >= 2, (
        "renderRisks 与 renderClosure 应各自改读 activeWarningList()（③④ 同源联动）"
    )


def test_dashboard_has_behavior_timeline_anchors() -> None:
    """区域② 必须是累积去重的行为里程碑时间线，而非逐帧 perception_events 快照。"""
    html = DASHBOARD.read_text(encoding="utf-8")
    anchors = [
        "ingestBehavior",       # 跨帧累积行为里程碑
        "behaviorEvents",       # 累积去重后的里程碑列表
        "friendlyVisitor",      # visitor_id → 访客#N 友好名
        "首次出现",              # 行为演化：进入
        "AI 行为时间线",         # 区域② 标题（从 "AI 时间线 / PerceptionEvent 流" 升级）
    ]
    missing = [a for a in anchors if a not in html]
    assert not missing, f"dashboard 缺少 AI 行为时间线锚点：{missing}"

    # 回归防线：旧的"逐帧快照 + 无触发事件"噪声实现应已移除
    assert "无触发事件" not in html, (
        "区域② 仍保留逐帧'无触发事件'噪声行 —— 应改为累积行为里程碑（P0-11.3 回归）"
    )

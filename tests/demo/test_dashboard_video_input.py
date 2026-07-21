"""P0-11.4 视频输入适配层 · Dashboard / 网关契约测试。

验证「场景输入 / 视频源接入」作为入口适配验证（而非普通"上传视频→AI判断"），
守住分层叙事：视频只是传感器 → 冻结架构产出 身份→轨迹→行为→风险→解释→干预。

纯静态锚点检查（与 test_dashboard_state_layer.py 同一手法），不触发 torch / 不装配 pipeline。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "silver_demo" / "dashboard" / "index.html"
GATEWAY = ROOT / "src" / "silver_demo" / "gateway.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_dashboard_has_video_source_panel():
    html = _read(DASHBOARD)
    # 面板标题定位为"入口适配验证"，而非"上传视频"
    assert "场景输入 / 视频源接入" in html
    assert "入口适配验证" in html
    # 三种输入模式（摄像头 Coming Soon，不实现）
    assert "模拟场景" in html
    assert "本地视频测试" in html
    assert "摄像头接入" in html
    # 前端挂载的上传/切换端点
    assert "/demo/upload" in html
    assert "/demo/scenario" in html
    assert "btn-analyze" in html
    assert "video-file" in html


def test_dashboard_has_ai_status_card():
    html = _read(DASHBOARD)
    # AI 状态卡体现工程成熟度，而非准确率承诺
    assert "AI 状态" in html
    assert "Demo-YOLO11n" in html
    assert "工程闭环验证" in html
    # 四段能力链路
    assert "人体检测" in html
    assert "轨迹跟踪" in html
    assert "行为建模" in html
    assert "风险解释" in html


def test_dashboard_resets_on_source_switch():
    html = _read(DASHBOARD)
    # WS 收到 source_switched 时清空跨帧累积（新视频=新会话），避免旧数据串场
    assert "source_switched" in html
    assert "resetSession" in html


def test_gateway_has_upload_and_scenario_endpoints():
    gw = _read(GATEWAY)
    assert '"/demo/upload"' in gw
    assert '"/demo/scenario"' in gw
    # 热切换逻辑（不重建 pipeline，仅替换帧源）
    assert "async def switch_source" in gw
    assert "source_switched" in gw
    # 上传落盘到 upload_dir（最小实现，无用户系统/文件管理/数据库）
    assert "upload_dir" in gw

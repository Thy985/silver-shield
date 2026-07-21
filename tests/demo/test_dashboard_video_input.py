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
    # 热切换逻辑：停旧循环→重建帧源+流水线状态（复用 detector）→重开循环
    assert "async def switch_source" in gw
    assert "source_switched" in gw
    # 上传落盘到 upload_dir（最小实现，无用户系统/文件管理/数据库）
    assert "upload_dir" in gw


def test_gateway_rebuilds_pipeline_state_on_loop_and_switch():
    """回归防线（修复 ②③④ 演示区多循环后变空白的根因）。

    根因：PerceptionPipeline 内部追踪/窗口/规则/决策状态跨 loop 累积、从不重置，
    首轮循环后 warning 不再产生。修复：loop 重放 & 切换源时调用 _rebuild_pipeline
    重建状态组件（复用已加载 YOLO detector 免重载权重）。此测试锁住该调用点，
    防止后续重构静默移除导致回归。
    """
    gw = _read(GATEWAY)
    # 存在重建方法，且复用 detector（不重载 YOLO 权重）
    assert "def _rebuild_pipeline" in gw
    assert "detector=self.pipeline.detector" in gw
    # loop 重放分支必须重建流水线（否则跨循环状态饱和→warning 断流）
    assert gw.count("self._rebuild_pipeline(") >= 2, (
        "应在 loop 重放与 switch_source 两处调用 _rebuild_pipeline"
    )
    # 重建同时重置帧序号，保证 frame_index 从 0 起（不影响冻结契约单调性约定）
    assert "self._frame_index = 0" in gw

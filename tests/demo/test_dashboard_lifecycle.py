"""P0-11.3.5 生命周期集成测试（WS 首连 snapshot + Reset + switch_source 清空）。

沿用 test_gateway_integration.py 的 monkeypatch 隔离 YOLO 模式（assemble/run_loop 置桩），
聚焦验证：
- 新 WS 连接首帧即收到 snapshot（晚连也能恢复历史状态）
- POST /demo/reset 清空服务端聚合 + 帧索引
- 真实的 switch_source 在切换输入源时清空服务端聚合（解决「切换视频源状态残留」）

若运行环境无 httpx（TestClient 依赖），整文件自动跳过。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from silver_demo.config import DemoSettings  # noqa: E402
from silver_demo.gateway import DemoGateway, create_app  # noqa: E402
from silver_demo.scenarios import ScenarioConfig  # noqa: E402
from silver_demo.state import DemoAggregateState  # noqa: E402
from silver_demo.ws import ConnectionHub  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """隔离 YOLO：assemble/run_loop 置桩，switch_source 仅置桩 n_frames=100。"""

    def _noop_assemble(self):
        self.n_frames = 100

    async def _noop_run(self):
        return

    async def _noop_switch(self, scenario):
        self.scenario = scenario
        self.n_frames = 100
        await self.hub.broadcast({"type": "source_switched", "scenario": scenario.scenario_id})

    monkeypatch.setattr(DemoGateway, "assemble", _noop_assemble)
    monkeypatch.setattr(DemoGateway, "run_loop", _noop_run)
    monkeypatch.setattr(DemoGateway, "switch_source", _noop_switch)

    settings = DemoSettings.from_env()
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _seed(gateway: DemoGateway) -> None:
    """手工注入一帧派生数据到服务端聚合状态（模拟已运行一段时间的系统）。"""
    gateway.aggregate_state.ingest(
        [
            {"warning_id": "w1", "risk_level": "LOW", "status": "PENDING",
             "created_at": "2026-01-01T00:00:01", "reason_summary": ["夜间异常"]},
            {"warning_id": "w2", "risk_level": "HIGH", "status": "PENDING",
             "created_at": "2026-01-01T00:00:02"},
        ],
        [{"visitor_id": "v1", "event_type": "abnormal_dwell", "created_at": "t",
          "location": "门口", "score": 0.7, "repeat_count": 1}],
        [],
        {"family": [{"command_id": "c1", "warning_id": "w1",
                      "command_type": "SEND_FAMILY_MESSAGE"}],
         "community": [], "log_only": []},
        7, 2,
    )


def test_ws_first_message_is_snapshot_with_history(client):
    """晚连的浏览器首帧即收到 snapshot，含当前 warning/behavior/command + 运行时元数据。"""
    _seed(client.app.state.gateway)
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert len(msg["warnings"]) == 2
    assert any(b["key"].startswith("enter|v1") for b in msg["behaviors"])
    assert "w1" in msg["commands"]
    assert msg["meta"]["frame_index"] == 7
    assert msg["meta"]["loop_count"] == 2
    assert "visitor_seq" in msg and "behavior_seen" in msg  # 客户端精确恢复所需


def test_reset_endpoint_clears_aggregate(client, monkeypatch):
    """POST /demo/reset 走 clear 逻辑，清空服务端聚合 + 帧索引，返回干净状态。"""
    _seed(client.app.state.gateway)

    # 让 reset 真实走「清空聚合」逻辑（镜像真实 switch_source 的清聚合分支）
    async def _clear_switch(self, scenario):
        self.scenario = scenario
        self.n_frames = 100
        self._frame_index = 0
        self.loop_count = 0
        self.aggregate_state.clear(reset_session=True)
        await self.hub.broadcast({"type": "source_switched", "scenario": scenario.scenario_id})

    monkeypatch.setattr(DemoGateway, "switch_source", _clear_switch)

    resp = client.post("/demo/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["frame_index"] == 0
    assert body["loop_count"] == 0

    agg = client.app.state.gateway.aggregate_state
    assert agg.warnings == {} and agg.behaviors == [] and agg.commands == {}
    assert agg.frame_index == 0 and agg.loop_count == 0


def test_switch_source_clears_aggregate_real(monkeypatch):
    """真实 switch_source 在切换输入源时清空服务端聚合状态（解决切换残留）。

    用 ``DemoGateway.__new__`` 跳过 ``__init__``（避免真实加载 Settings / 装配 pipeline），
    手工补齐 ``switch_source`` 依赖的属性，并隔离 I/O 副作用：
    - ``_validate_frame_source`` 置桩：避免对假 mp4 路径做文件存在性校验
    - ``run_loop`` 置桩：避免后台 task 因 ``pipeline`` 为 None 而抛错
    - ``Source.load`` 置桩：避免真实读视频 / CAVIAR fixture
    其余逻辑（停旧循环 → 清 store → 清聚合 → 重置索引/计时 → 广播）走真实实现。
    """
    gw = DemoGateway.__new__(DemoGateway)
    gw.aggregate_state = DemoAggregateState()
    gw.hub = ConnectionHub()
    gw._task = None          # __init__ 未跑，手工补齐 switch_source 取消分支所需属性
    gw._running = False
    gw.pipeline = None
    gw.clock = None
    gw.source = None
    gw.n_frames = -1
    gw.store = None
    gw.hp_settings = None
    gw._frame_index = 0
    gw.loop_count = 0
    gw.scenario = None

    async def _noop_run(self):
        return

    async def _broadcast(msg):
        return None

    monkeypatch.setattr(DemoGateway, "run_loop", _noop_run)
    monkeypatch.setattr(gw.hub, "broadcast", _broadcast)
    monkeypatch.setattr(gw, "_rebuild_pipeline", lambda scn: None)
    # 跳过文件存在性校验（假路径），聚焦验证清聚合逻辑
    monkeypatch.setattr(gw, "_validate_frame_source", lambda scn: None)
    # Source.load 置桩：避免真实读视频 / CAVIAR fixture
    monkeypatch.setattr(
        "silver_demo.sources.Source.load",
        lambda self, scn, hp: setattr(self, "frame_count", 100),
    )

    scn = ScenarioConfig(
        scenario_id="s", source="x", source_type="video_file",
        media_path="data/demo/x.mp4", start_time=datetime.now(timezone.utc),
    )
    gw.scenario = scn
    gw.aggregate_state.warnings = {"w1": {"warning_id": "w1", "risk_level": "LOW"}}
    gw.aggregate_state.behaviors = [{"key": "k"}]
    gw._frame_index = 10
    gw.loop_count = 3

    async def _run():
        await gw.switch_source(scn)

    asyncio.run(_run())

    assert gw.aggregate_state.warnings == {}
    assert gw.aggregate_state.behaviors == []
    assert gw._frame_index == 0
    assert gw.loop_count == 0
    assert gw.aggregate_state.started_at > 0  # 新会话计时

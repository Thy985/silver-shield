"""P0-11.4 视频输入适配层集成测试（FastAPI TestClient）。

通过 monkeypatch 隔离 ``DemoGateway.assemble`` / ``run_loop`` / ``switch_source``（避免加载 YOLO 权重），
聚焦验证端点接线与边界：扩展名校验 / 文件落盘 / 响应结构 / 场景切换 / 路径穿越防护 / 上传大小限制，
以及 ``_validate_frame_source`` 对 CAVIAR / video_file 的帧源可用性守护（评审 #2）。

若运行环境无 httpx（TestClient 依赖），整文件自动跳过，不阻断契约测试套件收集。
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("httpx")  # 无 TestClient 依赖则跳过

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from silver_demo.config import DemoSettings
from silver_demo.gateway import DemoGateway, create_app
from silver_demo.scenarios import ScenarioConfig

# ----------------------------------------------------------------------
# 单元测试：_validate_frame_source（评审 #2 — caviar_jpg / video_file 帧源守护）
# ----------------------------------------------------------------------


def _bare_gateway() -> DemoGateway:
    """构造未装配的 DemoGateway（仅注入校验所需的 hp_settings / n_frames）。"""
    from home_perception.core.config import Settings

    ds = DemoSettings.from_env()
    hp = Settings.load(ds.home_perception_config)
    gw = DemoGateway.create_for_test()  # 测试工厂：跳过 YOLO / 场景解析，避免 __new__ 绕过 __init__
    gw.hp_settings = hp
    gw.n_frames = 0
    return gw


def test_validate_frame_source_rejects_empty_caviar():
    gw = _bare_gateway()
    gw.n_frames = 0
    scn = ScenarioConfig(
        scenario_id="s",
        source="s",
        source_type="caviar_jpg",
        start_time=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        gw._validate_frame_source(scn)


def test_validate_frame_source_rejects_missing_video():
    gw = _bare_gateway()
    gw.n_frames = 5  # 帧数正常，但文件不存在
    scn = ScenarioConfig(
        scenario_id="s",
        source="s",
        source_type="video_file",
        media_path="/no/such/file.mp4",
        start_time=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        gw._validate_frame_source(scn)


def test_validate_frame_source_rejects_zero_frame_video():
    gw = _bare_gateway()
    gw.n_frames = 0  # 帧数为 0（编码不支持 / 时长为 0）
    scn = ScenarioConfig(
        scenario_id="s",
        source="s",
        source_type="video_file",
        media_path="data/demo/CCTV_Surveillance_Final.mp4",
        start_time=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        gw._validate_frame_source(scn)


# ----------------------------------------------------------------------
# 集成测试：端点接线（TestClient + monkeypatch 隔离 YOLO）
# ----------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """隔离 YOLO：assemble/run_loop 置桩，switch_source 仅置桩 n_frames=100。"""

    def _noop_assemble(self):
        self.n_frames = 100  # 桩：模拟源有 100 帧

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
    settings.live_enabled = True  # 本套测试专测 Live 次级入口（WS + /demo/*），必须开启
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_upload_rejects_bad_extension(client):
    resp = client.post(
        "/demo/upload",
        files={"file": ("evil.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "不支持" in resp.json()["error"]


def test_upload_accepts_video_and_returns_frames(client):
    resp = client.post(
        "/demo/upload",
        files={"file": ("clip.mp4", io.BytesIO(b"\x00\x01fake-mp4-bytes"), "video/mp4")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["filename"] == "clip.mp4"
    assert body["frames"] == 100  # 桩 n_frames
    # 文件确实落盘到 upload_dir（uuid.hex + 原扩展名）
    dest = Path(client.app.state.gateway.demo_settings.upload_dir) / body["source_id"]
    assert dest.is_file()
    assert dest.suffix == ".mp4"


def test_action_closure_workflow_reaches_resolution(client):
    """P0-1 人类处置闭环：WS 上行（家属→社区）→ 状态机翻转 → community_done → /live 重新投影出 Resolution。

    验证：
    - family action → family_handled；community action → community_done（state.py 翻转）；
    - 终态触发 Resolution 事实摄入（Projection 不回写：经 accumulator 新事件 → /live 重新构造）；
    - /live 渲染含"处置完成"ACTION 节点 + 行动闭环面板（action_closure）。
    """
    warning_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001"

    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()  # 首连 snapshot（晚连也能看到历史 action 状态）
        assert snap["type"] == "snapshot"

        # 家属：我知道了 → family_handled
        ws.send_json(
            {"type": "action", "warning_id": warning_id, "operator": "family", "action": "acknowledge"}
        )
        ack = ws.receive_json()
        assert ack["type"] == "action_ack"
        assert ack["updated"]["status"] == "family_handled"
        upd = ws.receive_json()  # 广播 state_update
        assert upd["type"] == "state_update"
        assert upd["state"][warning_id]["status"] == "family_handled"

        # 社区：接受任务 → community_done
        ws.send_json(
            {"type": "action", "warning_id": warning_id, "operator": "community", "action": "accept"}
        )
        ack2 = ws.receive_json()
        assert ack2["updated"]["status"] == "community_done"
        upd2 = ws.receive_json()
        assert upd2["state"][warning_id]["status"] == "community_done"

    # 终态事实已摄入 accumulator → /live 重新投影出 Resolution 节点 + 行动闭环面板。
    resp = client.get("/live")
    assert resp.status_code == 200
    assert "处置完成" in resp.text  # Evidence Timeline 的 Resolution 节点 summary
    assert "aaaaaaaa" in resp.text  # warning 前缀在 summary
    assert "行动闭环" in resp.text  # action_closure 面板
    assert "我知道了" in resp.text
    assert "完成处置" in resp.text


def test_upload_rejects_oversized_file(client):
    # 将软上限压到 0 MB，任何非空文件都应触发 413
    client.app.state.gateway.demo_settings.max_upload_mb = 0.0
    resp = client.post(
        "/demo/upload",
        files={"file": ("big.mp4", io.BytesIO(b"\x00" * 10), "video/mp4")},
    )
    assert resp.status_code == 413
    assert "过大" in resp.json()["error"]


def test_scenario_switch_to_preset(client):
    resp = client.post("/demo/scenario", json={"scenario_id": "cctv_surveillance_suspicious"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["scenario"] == "cctv_surveillance_suspicious"
    assert body["frames"] == 100


def test_scenario_switch_rejects_path_traversal(client):
    resp = client.post("/demo/scenario", json={"scenario_id": "../../etc/passwd"})
    assert resp.status_code == 404  # 解析后落在 scenarios_dir 之外 → 拒绝（评审 #4）


def test_scenario_switch_rejects_empty_id(client):
    resp = client.post("/demo/scenario", json={"scenario_id": ""})
    assert resp.status_code == 400

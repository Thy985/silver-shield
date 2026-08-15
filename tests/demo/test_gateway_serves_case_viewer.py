"""Task 0：旗舰 Case Viewer 托管 + Live 降级的契约测试。

验证：
- 旗舰模式（``live_enabled=False``）下，``GET /`` 静态托管 ``case_artifacts_dir/case_viewer.html``；
  未构建时返回引导提示页（不依赖 runtime、不 import visualizer）。
- ``GET /live`` 在旗舰模式下 404（明确提示 disabled）。
- ``GET /health`` 报告 ``mode=verified`` / ``live_enabled=False`` / ``assembled=False``。
- 旗舰模式 app 启动不装配 runtime（``gateway.pipeline is None``），即不触发 torch / YOLO。
- Live 模式（``live_enabled=True``）下 ``GET /live`` 返回既有 ``dashboard/index.html``。

若运行环境无 httpx（TestClient 依赖），整文件自动跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from silver_demo.config import DemoSettings
from silver_demo.gateway import DemoGateway, create_app

SILVER_DEMO = REPO_ROOT / "src" / "silver_demo"


@pytest.fixture
def built_case_dir(tmp_path: Path) -> Path:
    """造一个含 case_viewer.html 的假 Factory 产物目录。"""
    d = tmp_path / "artifacts"
    d.mkdir()
    (d / "case_viewer.html").write_text(
        "<html><head><title>Verified Case</title></head><body>CASE CONTENT</body></html>",
        encoding="utf-8",
    )
    return d


def test_flagship_get_root_serves_case_viewer(built_case_dir: Path):
    settings = DemoSettings(case_artifacts_dir=str(built_case_dir), live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/")
    assert resp.status_code == 200
    assert "CASE CONTENT" in resp.text
    assert "Verified Case" in resp.text


def test_flagship_get_root_missing_artifact_shows_guide():
    settings = DemoSettings(case_artifacts_dir=None, live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/")
    assert resp.status_code == 200
    assert "case_viewer.html" in resp.text  # 引导提示页
    assert "Verified Cases" in resp.text


def test_flagship_live_route_disabled():
    settings = DemoSettings(case_artifacts_dir=None, live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/live")
    assert resp.status_code == 404
    assert "Live Runtime Preview" in resp.text


def test_flagship_health_reports_verified_mode():
    settings = DemoSettings(case_artifacts_dir=None, live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/health")
    body = resp.json()
    assert body["mode"] == "verified"
    assert body["live_enabled"] is False
    assert body["assembled"] is False


def test_flagship_does_not_assemble_runtime(built_case_dir: Path):
    # 旗舰模式不应装配 runtime（assemble 会触发 torch / YOLO 加载）
    settings = DemoSettings(case_artifacts_dir=str(built_case_dir), live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        # 若 assemble 被调用，gateway.pipeline 非 None；此处应为 None
        assert c.app.state.gateway.pipeline is None


def test_live_mode_serves_dashboard(monkeypatch):
    # dashboard/index.html 存在于仓库（Legacy / Phase-3 Preview）；live_enabled=True 时应被 /live 返回
    dash = SILVER_DEMO / "dashboard" / "index.html"
    if not dash.is_file():
        pytest.skip("dashboard/index.html 不存在")

    # 隔离 YOLO：assemble / run_loop 置桩，避免启动加载权重
    def _noop_assemble(self):
        self.n_frames = 100

    async def _noop_run(self):
        return

    monkeypatch.setattr(DemoGateway, "assemble", _noop_assemble)
    monkeypatch.setattr(DemoGateway, "run_loop", _noop_run)

    settings = DemoSettings(case_artifacts_dir=None, live_enabled=True)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/live")
    assert resp.status_code == 200

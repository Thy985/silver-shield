"""Task 0：旗舰 Case Viewer 托管 + Live 降级的契约测试。

验证：
- 旗舰模式（``live_enabled=False``）下，``GET /`` 静态托管 ``case_artifacts_dir/case_viewer.html``；
  未构建时返回引导提示页（不依赖 runtime、不 import visualizer）。
- ``GET /live`` 在旗舰模式下 404（明确提示 disabled）。
- ``GET /health`` 报告 ``mode=verified`` / ``live_enabled=False`` / ``assembled=False``。
- 旗舰模式 app 启动不装配 runtime（``gateway.pipeline is None``），即不触发 torch / YOLO。
- Live 模式（``live_enabled=True``）下 ``GET /live`` 收敛到统一 Case Viewer（与旗舰同源
  ``render_case_viewer``，同一 ``EvidenceProjection`` View Model，T0-6）。

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


def test_live_mode_serves_case_viewer(monkeypatch):
    # ADR-0036 Phase 3：/live 收敛到统一 Case Viewer（与旗舰同源 render_case_viewer）。
    # 隔离 YOLO：assemble / run_loop 置桩，避免启动加载权重；
    # 即便 run_loop 未累积帧，/live 也须渲染出统一 Case Viewer（空投影 = 初始态）。
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
    # T0-6：与旗舰同源——同一 render_case_viewer 输出的统一 Case Viewer 标题标记
    assert "SilverShield Case Viewer" in resp.text
    # 语义体系统一：不再各自解释 risk/decision/timeline（统一 Evidence Timeline）
    assert "统一 Evidence Timeline" in resp.text


# ----------------------------------------------------------------------
# 音频 E2E（P0 验收补全）：旗舰网关静态伺服 canonical/（音频样本 / 媒体帧 / case.mp4）
# ----------------------------------------------------------------------


@pytest.fixture
def case_dir_with_canonical(tmp_path: Path) -> Path:
    """造一个含 case_viewer.html + canonical/<sid>/audio 样本的假 Factory 产物目录。"""
    d = tmp_path / "artifacts"
    audio_dir = d / "canonical" / "sw_t1" / "audio"
    audio_dir.mkdir(parents=True)
    (d / "case_viewer.html").write_text(
        "<html><body>CASE</body></html>", encoding="utf-8"
    )
    wav = audio_dir / "audio_telephone_persistent.wav"
    wav.write_bytes(b"RIFF\x00fake-wav-bytes-for-test")
    (audio_dir / "manifest.json").write_text(
        '{"source_kind": "AudioFileSource", "files": {}}', encoding="utf-8"
    )
    # canonical 外的"秘密"文件：路径穿越防护验证目标（不得被伺服）。
    (d / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return d


def test_flagship_serves_canonical_audio_sample(case_dir_with_canonical: Path):
    """音频 E2E 验收：/canonical/<sid>/audio/<kind>.wav 可被浏览器真实加载（200 + 字节一致）。"""
    settings = DemoSettings(
        case_artifacts_dir=str(case_dir_with_canonical), live_enabled=False
    )
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/canonical/sw_t1/audio/audio_telephone_persistent.wav")
    assert resp.status_code == 200
    assert resp.content == b"RIFF\x00fake-wav-bytes-for-test"
    assert resp.headers.get("content-type", "").startswith("audio")


def test_flagship_canonical_manifest_json_served(case_dir_with_canonical: Path):
    """canonical 内元数据（manifest.json）同样可伺服（可信 artifact 树只读暴露）。"""
    settings = DemoSettings(
        case_artifacts_dir=str(case_dir_with_canonical), live_enabled=False
    )
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/canonical/sw_t1/audio/manifest.json")
    assert resp.status_code == 200
    assert "AudioFileSource" in resp.text


def test_flagship_canonical_path_traversal_rejected(case_dir_with_canonical: Path):
    """路径穿越防护（fail-closed）：/canonical/.. 不得越界读取 artifacts 根之外文件。"""
    settings = DemoSettings(
        case_artifacts_dir=str(case_dir_with_canonical), live_enabled=False
    )
    app = create_app(settings)
    with TestClient(app) as c:
        # 双重穿越尝试：从 canonical/ 逃逸到 artifacts 根读 secret.txt（StaticFiles 拒绝）。
        resp = c.get("/canonical/../secret.txt")
    assert resp.status_code == 404
    assert "TOP-SECRET" not in resp.text


def test_flagship_no_canonical_dir_no_mount(tmp_path: Path):
    """无 canonical 目录 → 不挂载静态资源（/canonical/* 自然 404，诚实降级不兜底）。"""
    d = tmp_path / "artifacts"
    d.mkdir()
    (d / "case_viewer.html").write_text("<html></html>", encoding="utf-8")
    settings = DemoSettings(case_artifacts_dir=str(d), live_enabled=False)
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/canonical/sw_t1/audio/audio_telephone_persistent.wav")
    assert resp.status_code == 404


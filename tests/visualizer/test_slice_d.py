"""ADR-0036 Slice D · D3 导出接入 Case Viewer（AC-6）+ Media Source 三源 + D1/D2 去重。

全部用例 hermetic（零 cv2）：D3 导出经 ``run_case_viewer._d3_generate_case_video``
monkeypatch 桩替代（真实入口懒导入 compiler，单元测试不拉 cv2/PIL/numpy 渲染栈）。

覆盖：
- AC-6（Slice D）：``--export-case-video`` 调用 D3 generate_case_video，将导出 case.mp4
  登记为 ArtifactVideoSource（manifest.source_kind=ArtifactVideoSource），HTML 出现原生
  <video> 且 src 指向 case.mp4；导出所用 spec 必须是 CaseVideoSpec 且 with_audio=False
  （Case Video 叙事路径，非 Analysis Video 重新产品化；D3-B 未实现 fail-closed）。
- Media Source Adapter 三源抽象：ArtifactVideoSource（读 video_url）/ SyntheticFrameSource
  （读 frames）/ LiveFrameSource（→ None，Slice A 不实现）。
- D1/D2 去重：单次 ``render_case_viewer`` 同时产出 Artifact 证据（场景标题 + Evidence
  Timeline）与 Replay 引擎（window.__Replay.init），证明 D1（Artifact Mode）与 D2（Replay）
  收敛于同一渲染路径，无第二份重复渲染器。
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

from home_perception.visualizer.video.spec import CaseVideoSpec
from home_perception.visualizer.viewer import load_case_artifact, render_case_viewer
from home_perception.visualizer.viewer.media_source import (
    MediaSourceError,
    resolve_media_source,
)

from .conftest import make_d3a_artifact_dir, make_media_asset

_SID = "sw_adr0034_elderly_dwell"


def _load_run_case_viewer():
    """加载 scripts/run_case_viewer.py CLI 模块（非包，按需 importlib 装配）。"""
    cli_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_case_viewer.py"
    spec = importlib.util.spec_from_file_location("run_case_viewer_cli", str(cli_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_d3_result(spec) -> types.SimpleNamespace:
    """桩：复刻 compiler 的产物布局（output_dir/{sid}__v{ver}/case.mp4），写伪文件。"""
    out_dir = Path(spec.output_dir) / f"{spec.scenario_id}__v{spec.version}"
    case_mp4 = out_dir / "case.mp4"
    out_dir.mkdir(parents=True, exist_ok=True)
    case_mp4.write_bytes(b"FAKEMP4")
    (out_dir / "storyboard.yaml").write_text("{}", encoding="utf-8")
    (out_dir / "provenance.json").write_text("{}", encoding="utf-8")
    return types.SimpleNamespace(
        scenario_id=spec.scenario_id,
        n_frames=24,
        duration_s=12.0,
        case_mp4=case_mp4,
        storyboard_yaml=out_dir / "storyboard.yaml",
        provenance_json=out_dir / "provenance.json",
    )


# ---------------------------------------------------------------------------
# AC-6：导出登记为 ArtifactVideoSource + HTML 原生 <video>
# ---------------------------------------------------------------------------


def test_export_case_video_registers_artifact_video_source(tmp_path, monkeypatch):
    artifacts = make_d3a_artifact_dir(tmp_path)
    out = tmp_path / "out.html"
    rc_mod = _load_run_case_viewer()
    monkeypatch.setattr(rc_mod, "_d3_generate_case_video", _fake_d3_result)

    rc = rc_mod.main(
        ["--artifacts", str(artifacts), "--output", str(out), "--export-case-video"]
    )
    assert rc == 0

    manifest_path = artifacts / _SID / "media" / "manifest.json"
    assert manifest_path.exists(), "导出应写入 {sid}/media/manifest.json"
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_kind"] == "ArtifactVideoSource"
    assert manifest["video_url"].endswith("case.mp4")
    # ArtifactVideoSource 必须有 video_url（fail-closed：缺则 MediaSourceError）。
    assert manifest["frame_count"] == 24 and manifest["duration_sec"] == 12.0

    html = out.read_text(encoding="utf-8")
    assert "<video" in html, "应渲染原生 <video>（ArtifactVideoSource）"
    assert "case.mp4" in html, "video src 应指向导出的 case.mp4"
    assert "ArtifactVideoSource" in html, "媒体绑定脚注应诚实标注 ArtifactVideoSource"


def test_export_case_video_spec_is_case_video_with_audio_false(tmp_path, monkeypatch):
    """静默断言：导出走 CaseVideoSpec 且 with_audio=False（Case Video 叙事路径）。"""
    artifacts = make_d3a_artifact_dir(tmp_path)
    out = tmp_path / "out.html"
    rc_mod = _load_run_case_viewer()
    captured: dict = {}

    def _spy(spec):
        captured["spec"] = spec
        return _fake_d3_result(spec)

    monkeypatch.setattr(rc_mod, "_d3_generate_case_video", _spy)

    rc = rc_mod.main(
        [
            "--artifacts", str(artifacts),
            "--output", str(out),
            "--export-case-video",
            "--export-fps", "5",
            "--export-resolution", "640x480",
        ]
    )
    assert rc == 0
    spec = captured["spec"]
    assert isinstance(spec, CaseVideoSpec)
    assert spec.with_audio is False, "D3-B 未实现：导出必须维持 with_audio=False（fail-closed）"
    assert spec.resolution == (640, 480)
    assert spec.fps == 5.0


# ---------------------------------------------------------------------------
# Media Source Adapter 三源抽象
# ---------------------------------------------------------------------------


def test_resolve_media_source_artifact_video_reads_video_url(tmp_path):
    import json

    base = tmp_path / "base"
    media_dir = base / _SID / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_kind": "ArtifactVideoSource",
                "frame_count": 10,
                "fps": 2.0,
                "duration_sec": 5.0,
                "frame_template": "",
                "video_url": f"{_SID}/media/clip.mp4",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    m = resolve_media_source(base, _SID, "ArtifactVideoSource")
    assert m is not None
    assert m["source_kind"] == "ArtifactVideoSource"
    assert m["video_url"] == f"{_SID}/media/clip.mp4"
    assert m["frame_count"] == 10


def test_resolve_media_source_artifact_video_requires_video_url(tmp_path):
    import json

    base = tmp_path / "base"
    media_dir = base / _SID / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_kind": "ArtifactVideoSource",
                "frame_count": 10,
                "fps": 2.0,
                "duration_sec": 5.0,
                "frame_template": "",
                "video_url": "",  # ArtifactVideoSource 缺失 video_url → fail-closed
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(MediaSourceError):
        resolve_media_source(base, _SID, "ArtifactVideoSource")


def test_resolve_media_source_synthetic_reads_frames(tmp_path):
    base = tmp_path / "base"
    make_media_asset(base, _SID, frame_count=12, fps=6.0)
    m = resolve_media_source(base, _SID, "SyntheticFrameSource")
    assert m is not None
    assert m["source_kind"] == "SyntheticFrameSource"
    assert m["frame_template"].endswith(".png")
    assert m["frame_count"] == 12 and m["fps"] == 6.0


def test_resolve_media_source_live_returns_none(tmp_path):
    base = tmp_path / "base"
    # LiveFrameSource 是运行时注入的帧源，Slice A 不实现 → 恒 None（不崩）。
    assert resolve_media_source(base, _SID, "LiveFrameSource") is None


# ---------------------------------------------------------------------------
# D1/D2 去重：单次渲染同时服务 Artifact Mode 与 Replay
# ---------------------------------------------------------------------------


def test_d1_d2_convergence_single_render(tmp_path):
    artifacts = make_d3a_artifact_dir(tmp_path)
    html = render_case_viewer(
        load_case_artifact(artifacts),
        media_base_dir=artifacts,
        media_base_url="",
    )
    # 单一自包含页面（无第二份重复渲染器）。
    assert html.count("<!DOCTYPE html>") == 1
    # D1（Artifact Mode）：场景标题 + Evidence Timeline 证据段出现。
    assert _SID in html
    assert "统一 Evidence Timeline" in html
    # D2（Replay）：同一渲染输出内含 Replay 引擎接入点（window.__Replay.init）。
    assert "window.__Replay.init" in html

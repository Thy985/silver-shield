"""ADR-0036 P4 整改 · prepare_case_media 脚本测试（真实案例媒体准备）。

覆盖验收：
- 幂等：已就绪（manifest 指向 {sid}/media/case.mp4 且文件存在）→ 跳过不重复复制；
- **D3 合成帧不误判**：manifest 指向 {sid}__v<ver>/case.mp4（D3 产物）→ 不算就绪，
  必须覆盖为真实媒体（P4 红线：不得把合成帧当真实案例视频）；
- fail-closed：视频缺失 / cv2 探测失败 → 返回 False / 退出非 0；
- 路径穿越：video_url 含 ".." → 不算就绪；
- --map 覆盖默认映射；--force 强制覆盖。
"""

from __future__ import annotations

from pathlib import Path

from scripts.prepare_case_media import (
    _media_valid,
    _parse_map,
    _prepare_one,
    _probe_video,
    main,
)

# ---------------------------------------------------------------------------
# _media_valid：幂等判定
# ---------------------------------------------------------------------------


def test_media_valid_ok_for_prepare_product(tmp_path: Path):
    """就绪判定：manifest 指向 {sid}/media/case.mp4 且文件存在 → True。"""
    sid = "sw_x"
    media_dir = tmp_path / sid / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "case.mp4").write_bytes(b"fake-video")
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "video_url": f"{sid}/media/case.mp4",
    }
    assert _media_valid(media_dir, manifest) is True


def test_media_valid_false_for_d3_export(tmp_path: Path):
    """D3 合成帧不误判：manifest 指向 {sid}__v1/case.mp4 → False（P4 红线）。"""
    sid = "sw_x"
    media_dir = tmp_path / sid / "media"
    media_dir.mkdir(parents=True)
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "video_url": f"{sid}/media/{sid}__v1/case.mp4",
    }
    assert _media_valid(media_dir, manifest) is False


def test_media_valid_false_on_missing_file(tmp_path: Path):
    """video_url 指向不存在的文件 → False。"""
    sid = "sw_x"
    media_dir = tmp_path / sid / "media"
    media_dir.mkdir(parents=True)
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "video_url": f"{sid}/media/case.mp4",  # 文件不存在
    }
    assert _media_valid(media_dir, manifest) is False


def test_media_valid_false_on_wrong_kind(tmp_path: Path):
    """非 ArtifactVideoSource → False。"""
    sid = "sw_x"
    media_dir = tmp_path / sid / "media"
    media_dir.mkdir(parents=True)
    manifest = {"source_kind": "SyntheticFrameSource", "video_url": ""}
    assert _media_valid(media_dir, manifest) is False


def test_media_valid_false_on_path_traversal(tmp_path: Path):
    """video_url 含 '..' → False（防路径穿越）。"""
    sid = "sw_x"
    media_dir = tmp_path / sid / "media"
    media_dir.mkdir(parents=True)
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "video_url": "../../etc/passwd",
    }
    assert _media_valid(media_dir, manifest) is False


# ---------------------------------------------------------------------------
# _prepare_one：复制 + manifest + 幂等 + fail-closed
# ---------------------------------------------------------------------------


def test_prepare_one_writes_manifest(tmp_path: Path, monkeypatch):
    """首次准备：复制视频 + 写 manifest（video_url 指向 {sid}/media/case.mp4）。"""
    artifacts = tmp_path / "artifacts"
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    src = media_root / "demo.mp4"
    src.write_bytes(b"fake-video-bytes")

    monkeypatch.setattr(
        "scripts.prepare_case_media._probe_video",
        lambda _p: (120, 24.0, 5.0),
    )

    assert _prepare_one(artifacts, media_root, "sw_x", "demo.mp4", force=False) is True

    media_dir = artifacts / "sw_x" / "media"
    assert (media_dir / "case.mp4").read_bytes() == b"fake-video-bytes"
    manifest = _read_json(media_dir / "manifest.json")
    assert manifest["source_kind"] == "ArtifactVideoSource"
    assert manifest["video_url"] == "sw_x/media/case.mp4"
    assert manifest["frame_count"] == 120
    assert manifest["fps"] == 24.0
    assert abs(manifest["duration_sec"] - 5.0) < 1e-6


def test_prepare_one_idempotent_skips_copy(tmp_path: Path, monkeypatch):
    """幂等：已就绪（manifest 指向本脚本产物且文件存在）→ 不重复复制。"""
    artifacts = tmp_path / "artifacts"
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    src = media_root / "demo.mp4"
    src.write_bytes(b"fake-video-bytes")

    calls: list = []
    monkeypatch.setattr(
        "scripts.prepare_case_media._probe_video",
        lambda _p: (calls.append(1) or (120, 24.0, 5.0)),
    )

    assert _prepare_one(artifacts, media_root, "sw_x", "demo.mp4", force=False) is True
    assert _prepare_one(artifacts, media_root, "sw_x", "demo.mp4", force=False) is True
    # 第二次命中幂等 → 不再探测视频（无 copy2 触发探测）。
    assert len(calls) == 1


def test_prepare_one_overwrites_d3_manifest(tmp_path: Path, monkeypatch):
    """D3 合成帧 manifest 存在 → 不视为就绪，覆盖为真实媒体（P4 红线）。"""
    artifacts = tmp_path / "artifacts"
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    src = media_root / "demo.mp4"
    src.write_bytes(b"fake-video-bytes")

    # 预置 D3 产物形态的 manifest（指向 __v1 子目录）
    media_dir = artifacts / "sw_x" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "manifest.json").write_text(
        '{"source_kind": "ArtifactVideoSource", "video_url": "sw_x/media/sw_x__v1/case.mp4"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "scripts.prepare_case_media._probe_video",
        lambda _p: (120, 24.0, 5.0),
    )

    assert _prepare_one(artifacts, media_root, "sw_x", "demo.mp4", force=False) is True
    manifest = _read_json(media_dir / "manifest.json")
    assert manifest["video_url"] == "sw_x/media/case.mp4"  # 覆盖为真实媒体


def test_prepare_one_fail_closed_missing_video(tmp_path: Path):
    """演示视频缺失 → False（fail-closed）。"""
    artifacts = tmp_path / "artifacts"
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    assert _prepare_one(artifacts, media_root, "sw_x", "missing.mp4", force=False) is False


def test_prepare_one_fail_closed_probe_error(tmp_path: Path, monkeypatch):
    """cv2 探测失败 → False 且不产 manifest（fail-closed）。"""
    artifacts = tmp_path / "artifacts"
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    src = media_root / "demo.mp4"
    src.write_bytes(b"fake-video-bytes")

    def _boom(_p):
        raise OSError("moov atom not found")

    monkeypatch.setattr("scripts.prepare_case_media._probe_video", _boom)

    assert _prepare_one(artifacts, media_root, "sw_x", "demo.mp4", force=False) is False
    media_dir = artifacts / "sw_x" / "media"
    assert not (media_dir / "manifest.json").exists()  # 不产残缺 manifest


# ---------------------------------------------------------------------------
# _probe_video / _parse_map / main
# ---------------------------------------------------------------------------


def test_probe_video_roundtrip(tmp_path: Path, monkeypatch):
    """_probe_video 返回 (frames, fps, duration)。"""

    class _FakeCap:
        def __init__(self) -> None:
            self._v = {"frames": 100.0, "fps": 25.0}

        def isOpened(self) -> bool:
            return True

        def get(self, prop: int) -> float:
            return {"frames": 100.0, "fps": 25.0}.get(
                _prop_name(prop), 0.0
            )

        def release(self) -> None:
            return None

    def _prop_name(prop: int) -> str:
        return {0: "frames", 5: "fps"}.get(prop, "other")

    fake = _FakeCap()
    monkeypatch.setattr("cv2.VideoCapture", lambda _p: fake)
    monkeypatch.setattr("cv2.CAP_PROP_FRAME_COUNT", 0)
    monkeypatch.setattr("cv2.CAP_PROP_FPS", 5)

    n, fps, dur = _probe_video(tmp_path / "x.mp4")
    assert n == 100
    assert fps == 25.0
    assert abs(dur - 4.0) < 1e-6


def test_parse_map_ok():
    assert _parse_map("a=1.mp4,b=2.mp4") == {"a": "1.mp4", "b": "2.mp4"}


def test_parse_map_rejects_malformed():
    import argparse

    import pytest

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_map("nofile")


def test_main_missing_media_root_exit_2(tmp_path: Path, monkeypatch):
    """media_root 不存在 → 退出 2（fail-closed）。"""
    monkeypatch.setattr(
        "scripts.prepare_case_media._DEFAULT_ARTIFACTS", tmp_path / "a"
    )
    rc = main(["--artifacts", str(tmp_path / "a"), "--media-root", str(tmp_path / "nope")])
    assert rc == 2


def test_main_success_exit_0(tmp_path: Path, monkeypatch):
    """成功准备 → 退出 0。"""
    media_root = tmp_path / "media_root"
    media_root.mkdir(parents=True)
    (media_root / "demo.mp4").write_bytes(b"fake")
    monkeypatch.setattr(
        "scripts.prepare_case_media._probe_video", lambda _p: (120, 24.0, 5.0)
    )
    rc = main(
        [
            "--artifacts", str(tmp_path / "a"),
            "--media-root", str(media_root),
            "--map", "sw_x=demo.mp4",
        ]
    )
    assert rc == 0
    assert (tmp_path / "a" / "sw_x" / "media" / "case.mp4").is_file()


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))

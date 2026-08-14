"""ADR-0036 Slice A.1 · Media Source Adapter 单元测试（只读解析层铁律）。

覆盖：解析存在 / 缺失 / 非法（fail-closed）/ LiveFrameSource 早返 / ArtifactVideoSource。
核心断言：Adapter **只读**——测试中绝不触发任何帧生成（media asset 由 make_media_asset
"生产者模拟"写入，Adapter 仅读取 manifest）。
"""

from __future__ import annotations

import json

import pytest

from home_perception.visualizer.viewer.media_source import (
    MediaManifest,
    MediaSourceError,
    resolve_media_source,
)

from .conftest import make_media_asset


def test_resolve_present_synthetic(tmp_path):
    """解析存在：SyntheticFrameSource manifest 被正确读出（frame_count/fps/duration/
    frame_template）。"""
    make_media_asset(tmp_path, "sw_t1", frame_count=30, fps=10.0)
    m = resolve_media_source(tmp_path, "sw_t1", "SyntheticFrameSource")
    assert isinstance(m, dict)
    assert m["source_kind"] == "SyntheticFrameSource"
    assert m["frame_count"] == 30
    assert m["fps"] == 10.0
    assert m["duration_sec"] == 3.0
    assert m["frame_template"] == "sw_t1/media/frames/{idx:06d}.png"
    assert m["video_url"] == ""
    # 类型契约（TypedDict 字段齐全）
    assert set(MediaManifest.__annotations__.keys()).issubset(m.keys())


def test_resolve_missing_dir_returns_none(tmp_path):
    """缺失媒体目录 → 降级返回 None（画布留空，不崩）。"""
    # 不调用 make_media_asset：目录不存在
    assert resolve_media_source(tmp_path, "sw_t1", "SyntheticFrameSource") is None


def test_resolve_missing_manifest_returns_none(tmp_path):
    """目录存在但无 manifest.json → 降级返回 None。"""
    (tmp_path / "sw_t1" / "media").mkdir(parents=True)
    assert resolve_media_source(tmp_path, "sw_t1", "SyntheticFrameSource") is None


def test_resolve_live_frame_source_returns_none(tmp_path):
    """LiveFrameSource：Slice A 不实现 → 返回 None（未来 slice 接 runtime）。"""
    assert resolve_media_source(tmp_path, "sw_t1", "LiveFrameSource") is None


def test_resolve_malformed_missing_template_fail_closed(tmp_path):
    """manifest 缺 frame_template（SyntheticFrameSource）→ MediaSourceError（fail-closed）。"""
    media_dir = tmp_path / "sw_t1" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "manifest.json").write_text(
        json.dumps({"source_kind": "SyntheticFrameSource", "frame_count": 5,
                    "fps": 10.0, "duration_sec": 0.5, "video_url": ""}),
        encoding="utf-8",
    )
    with pytest.raises(MediaSourceError):
        resolve_media_source(tmp_path, "sw_t1", "SyntheticFrameSource")


def test_resolve_bad_frame_count_fail_closed(tmp_path):
    """frame_count 非法（负/类型错）→ MediaSourceError（fail-closed）。"""
    media_dir = tmp_path / "sw_t1" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "manifest.json").write_text(
        json.dumps({"source_kind": "SyntheticFrameSource", "frame_count": -1,
                    "fps": 10.0, "duration_sec": 0.5,
                    "frame_template": "sw_t1/media/frames/{idx:06d}.png", "video_url": ""}),
        encoding="utf-8",
    )
    with pytest.raises(MediaSourceError):
        resolve_media_source(tmp_path, "sw_t1", "SyntheticFrameSource")


def test_resolve_artifact_video_missing_url_fail_closed(tmp_path):
    """ArtifactVideoSource 缺 video_url → MediaSourceError（fail-closed）。"""
    media_dir = tmp_path / "sw_t1" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "manifest.json").write_text(
        json.dumps({"source_kind": "ArtifactVideoSource", "frame_count": 0,
                    "fps": 0.0, "duration_sec": 1.0,
                    "frame_template": "", "video_url": ""}),
        encoding="utf-8",
    )
    with pytest.raises(MediaSourceError):
        resolve_media_source(tmp_path, "sw_t1", "ArtifactVideoSource")

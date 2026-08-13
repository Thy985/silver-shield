"""ADR-0035 D3 · VideoMuxer 单测（评审缺口 #1 · D3-A 无声 mp4 + 音频边界）。

with_audio / audio_track 一律 fail-closed（NotImplementedError，绝不静默无声退化）；
write_silent_mp4 空帧 → ValueError；正常帧 → 写出非空 mp4。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from home_perception.visualizer.video.mux.muxer import (
    MuxResult,
    mux,
    write_silent_mp4,
)
from home_perception.visualizer.video.spec import CaseVideoSpec


def _spec(**kw) -> CaseVideoSpec:
    base = {"scenario_id": "sw_x", "artifact_dir": Path("/tmp/a"), "output_dir": Path("/tmp/o")}
    base.update(kw)
    return CaseVideoSpec(**base)


def _frames(n: int = 3, h: int = 32, w: int = 32) -> list[np.ndarray]:
    return [np.zeros((h, w, 3), dtype=np.uint8) for _ in range(n)]


def test_mux_with_audio_true_fail_closed():
    with pytest.raises(NotImplementedError):
        mux(_frames(), Path("/tmp/x.mp4"), _spec(with_audio=True))


def test_mux_audio_track_fail_closed():
    with pytest.raises(NotImplementedError):
        mux(_frames(), Path("/tmp/x.mp4"), _spec(with_audio=False), audio_track=_frames(1))


def test_mux_silent_writes_mp4(tmp_path: Path):
    out = tmp_path / "case.mp4"
    res = mux(_frames(), out, _spec())
    assert isinstance(res, MuxResult)
    assert res.video_mp4 == out
    assert out.exists() and out.stat().st_size > 0


def test_write_silent_mp4_empty_raises():
    with pytest.raises(ValueError):
        write_silent_mp4([], Path("/tmp/x.mp4"))


def test_write_silent_mp4_writes_file(tmp_path: Path):
    out = tmp_path / "s.mp4"
    returned = write_silent_mp4(_frames(2, 16, 16), out, fps=2.0)
    assert returned == out
    assert out.exists() and out.stat().st_size > 0

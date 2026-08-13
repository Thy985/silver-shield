"""ADR-0035 D3 · 视觉确定性测试（§8 验收 5）。

同 scenario 两次生成 → 逐帧 np.array_equal 一致（确定性，指纹版本锁定）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from home_perception.visualizer.video.compiler import render_case_frames
from home_perception.visualizer.video.spec import CaseVideoSpec


def _spec(output_dir: Path, artifact_dir: Path) -> CaseVideoSpec:
    return CaseVideoSpec(
        scenario_id="sw_adr0034_elderly_dwell",
        artifact_dir=artifact_dir,
        output_dir=output_dir,
        fps=2.0,
        resolution=(320, 180),
        version=1,
    )


def test_visual_determinism_frames(tmp_path: Path, built_artifact_dir):
    frames_a = render_case_frames(_spec(tmp_path / "a", built_artifact_dir))
    frames_b = render_case_frames(_spec(tmp_path / "b", built_artifact_dir))
    assert len(frames_a) == len(frames_b) > 0
    for fa, fb in zip(frames_a, frames_b):
        assert np.array_equal(fa, fb)


def test_metadata_determinism(tmp_path: Path, built_artifact_dir):
    from home_perception.visualizer.video.compiler import generate_case_video

    out_a = generate_case_video(_spec(tmp_path / "a", built_artifact_dir))
    out_b = generate_case_video(_spec(tmp_path / "b", built_artifact_dir))
    assert (
        out_a.storyboard_yaml.read_text(encoding="utf-8")
        == out_b.storyboard_yaml.read_text(encoding="utf-8")
    )
    assert (
        out_a.provenance_json.read_text(encoding="utf-8")
        == out_b.provenance_json.read_text(encoding="utf-8")
    )

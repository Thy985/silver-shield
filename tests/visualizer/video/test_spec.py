"""ADR-0035 D3 · CaseVideoSpec schema 单测（评审缺口 #8 · extra="forbid"）。

编排配置模型的字段集硬锁：越界字段必须 ValidationError（CI/review 据此打回），
默认值（with_audio=False / seed=None / background=synthetic）须稳定。
"""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from home_perception.visualizer.video.spec import CaseVideoSpec


def _base() -> dict:
    return {
        "scenario_id": "sw_x",
        "artifact_dir": Path("/tmp/art"),
        "output_dir": Path("/tmp/out"),
    }


def test_spec_rejects_extra_field():
    with pytest.raises(pydantic.ValidationError):
        CaseVideoSpec(**_base(), surprise="oops")


def test_spec_background_literal():
    with pytest.raises(pydantic.ValidationError):
        CaseVideoSpec(**_base(), background="bogus")


def test_spec_defaults():
    spec = CaseVideoSpec(**_base())
    assert spec.with_audio is False
    assert spec.seed is None
    assert spec.background == "synthetic"
    assert spec.fps == 2.0
    assert spec.resolution == (1280, 720)
    assert spec.version == 1


def test_spec_accepts_known_fields():
    spec = CaseVideoSpec(
        **_base(),
        with_audio=False,
        seed=42,
        background="synthetic",
        fps=4.0,
        resolution=(640, 360),
        version=3,
    )
    assert spec.seed == 42
    assert spec.fps == 4.0
    assert spec.resolution == (640, 360)
    assert spec.version == 3

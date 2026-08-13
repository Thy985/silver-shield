"""ADR-0035 D3 · Evidence adapter 单测（评审缺口 #7 · 背景层 + 投影投影）。

SyntheticBackgroundProvider 确定性灰底（非 hash）；_fit_length 循环/裁剪；
BackgroundProvider 协议；ValidationBackgroundProvider 满足协议 + 空渲染回退 synthetic。
"""

from __future__ import annotations

import sys
import types

import numpy as np

from home_perception.visualizer.video.evidence.adapter import (
    BackgroundProvider,
    SyntheticBackgroundProvider,
    ValidationBackgroundProvider,
    _fit_length,
    load_scenario_evidence,
)


def test_synthetic_deterministic_gray():
    p = SyntheticBackgroundProvider()
    frames = p.generate("context", 3, (320, 180))  # (width, height)
    assert len(frames) == 3
    for f in frames:
        assert f.shape == (180, 320, 3)
        assert f.dtype == np.uint8
    gray = 30 + (sum(ord(c) for c in "context") % 18)
    assert frames[0][0, 0, 0] == gray
    # 确定性：两次生成完全一致
    again = SyntheticBackgroundProvider().generate("context", 3, (320, 180))
    for a, b in zip(frames, again):
        assert np.array_equal(a, b)


def test_fit_length_truncate():
    assert _fit_length([1, 2, 3, 4], 2) == [1, 2]


def test_fit_length_extend_cyclic():
    assert _fit_length([1, 2], 4) == [1, 2, 1, 2]


def test_fit_length_empty():
    assert _fit_length([], 5) == []


def test_protocol_synthetic_conforms():
    assert isinstance(SyntheticBackgroundProvider(), BackgroundProvider)


def test_protocol_validation_conforms():
    # 不调用 generate（避免拉入 validation 渲染栈），仅验证契约与构造
    assert isinstance(ValidationBackgroundProvider(scenario=None), BackgroundProvider)


def test_validation_provider_falls_back_to_synthetic(monkeypatch):
    # 注入最小 fake renderer：`render_frames` 返回空列表 → generate 应回退到 Synthetic。
    fake = types.ModuleType("home_perception.validation.simulation.renderer")
    fake.render_frames = staticmethod(lambda scenario: [])
    monkeypatch.setitem(sys.modules, "home_perception.validation.simulation.renderer", fake)
    frames = ValidationBackgroundProvider(scenario=None).generate("detection", 2, (320, 180))
    assert len(frames) == 2
    assert frames[0].shape == (180, 320, 3)


def test_load_scenario_evidence_missing_fail_closed():
    import pytest

    from .conftest import artifact_dir

    # artifact 目录存在但 scenario 不在投影中 → KeyError（fail-closed，不静默返回）
    with pytest.raises(KeyError):
        load_scenario_evidence(artifact_dir(), "sw_no_such_scenario")

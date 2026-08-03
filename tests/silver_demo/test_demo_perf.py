"""Demo 流水线轻量化配置测试（perf/demo-pipeline-fps）。

不依赖 torch / 网络 / 文件系统：
- ``DemoSettings`` 默认值与 ``from_env`` 注入；
- ``DemoGateway._resolve_frame_interval`` 限速解析（纯函数）；
- ``DemoGateway._apply_demo_detector_overrides`` 经 runtime.detector_imgsz 覆盖（不碰生产）；
- ``encode_frame_to_base64_jpeg(max_width=...)`` 缩图降体积（cv2 缺失时跳过）。
"""

from __future__ import annotations

import importlib.util

import pytest

from home_perception.core.config import Settings
from silver_demo.config import DemoSettings
from silver_demo.gateway import DemoGateway


def _has_cv2() -> bool:
    return importlib.util.find_spec("cv2") is not None


# ----------------------------------------------------------------------
# DemoSettings
# ----------------------------------------------------------------------


def test_demo_settings_defaults() -> None:
    s = DemoSettings()
    # 不限速 + 轻量化推理尺寸（realtime 档 416）+ 预览缩图 640
    assert s.frame_loop_interval_s == 0.0
    assert s.detector_imgsz == 416
    assert s.preview_max_width == 640
    assert s.jpeg_quality == 50


def test_demo_settings_from_env_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_DETECTOR_IMGSZ", "320")
    monkeypatch.setenv("DEMO_PREVIEW_MAX_WIDTH", "480")
    monkeypatch.setenv("DEMO_HOST", "0.0.0.0")
    monkeypatch.setenv("DEMO_PORT", "8765")
    s = DemoSettings.from_env()
    assert s.detector_imgsz == 320
    assert s.preview_max_width == 480
    assert s.host == "0.0.0.0"


def test_demo_settings_from_env_bad_imgsz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_DETECTOR_IMGSZ", "not-int")
    with pytest.raises(ValueError):
        DemoSettings.from_env()


# ----------------------------------------------------------------------
# _resolve_frame_interval（纯函数）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "demo_interval,fps_target,expected",
    [
        (0.0, 8, 0.0),
        (-1.0, 8, 0.0),
        (0.0, 0, 0.0),
        (0.125, 8, 0.125),
        (0.5, 24, 0.5),
    ],
)
def test_resolve_frame_interval(demo_interval: float, fps_target: int, expected: float) -> None:
    # frame_loop_interval_s <= 0 → 不限速(0.0)；>0 原样返回（不再回退到 1/fps_target）
    assert DemoGateway._resolve_frame_interval(demo_interval, fps_target) == expected


# ----------------------------------------------------------------------
# _apply_demo_detector_overrides
# ----------------------------------------------------------------------


def test_apply_demo_detector_overrides_sets_imgsz() -> None:
    gw = DemoGateway.create_for_test()
    gw.hp_settings = Settings()
    gw.demo_settings = DemoSettings(detector_imgsz=416)
    gw._apply_demo_detector_overrides()
    assert gw.hp_settings.runtime.detector_imgsz == 416


def test_apply_demo_detector_overrides_none_keeps_default() -> None:
    gw = DemoGateway.create_for_test()
    gw.hp_settings = Settings()
    gw.hp_settings.runtime.detector_imgsz = 480  # 模拟生产默认
    gw.demo_settings = DemoSettings(detector_imgsz=None)
    gw._apply_demo_detector_overrides()
    # None → 不覆盖，保留既有值（生产中由 detection.imgsz 决定）
    assert gw.hp_settings.runtime.detector_imgsz == 480


# ----------------------------------------------------------------------
# encode_frame_to_base64_jpeg(max_width)
# ----------------------------------------------------------------------


@pytest.mark.skipif(not _has_cv2(), reason="cv2 未安装，跳过编码测试")
def test_encode_frame_max_width_shrinks() -> None:
    import cv2
    import numpy as np

    from silver_demo.bridge import encode_frame_to_base64_jpeg

    frame = np.zeros((480, 1280, 3), dtype=np.uint8)
    full = encode_frame_to_base64_jpeg(frame, quality=50)
    small = encode_frame_to_base64_jpeg(frame, quality=50, max_width=640)
    assert full is not None and small is not None
    # 缩图后应更小（宽度 1280 → 640）
    assert len(small) < len(full)
    # 解码验证宽度确实被缩放
    raw = cv2.imdecode(
        np.frombuffer(__import__("base64").b64decode(small), np.uint8), cv2.IMREAD_UNCHANGED
    )
    assert raw.shape[1] == 640


@pytest.mark.skipif(not _has_cv2(), reason="cv2 未安装，跳过编码测试")
def test_encode_frame_max_width_noop_when_smaller() -> None:
    import numpy as np

    from silver_demo.bridge import encode_frame_to_base64_jpeg

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    out = encode_frame_to_base64_jpeg(frame, quality=50, max_width=640)
    assert out is not None
    # 原宽已小于 max_width → 不放大
    assert len(out) > 0

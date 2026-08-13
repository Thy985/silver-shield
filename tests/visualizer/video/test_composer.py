"""ADR-0035 D3 · Composer alpha 合成单测（评审缺口 #5）。

BGR 背景 + 有序 RGBA 图层 → 确定性合成；后叠图层在上；偏移定位正确。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from home_perception.visualizer.video.render.composer import compose_frame


def _rgba(w: int, h: int, color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def test_compose_overlay_opaque_covers_background():
    h, w = 40, 40
    bg = np.zeros((h, w, 3), dtype=np.uint8)  # 黑
    red = _rgba(w, h, (255, 0, 0, 255))  # 不透明红
    out = compose_frame(bg, [(red, (0, 0))])
    # BGR：红 = (0,0,255)
    assert tuple(int(out[20, 20, c]) for c in range(3)) == (0, 0, 255)


def test_compose_z_order_later_on_top():
    h, w = 40, 40
    bg = np.zeros((h, w, 3), dtype=np.uint8)
    green = _rgba(w, h, (0, 255, 0, 255))  # 底层绿
    blue = _rgba(w, h, (0, 0, 255, 255))  # 顶层蓝
    out = compose_frame(bg, [(green, (0, 0)), (blue, (0, 0))])
    assert tuple(int(out[20, 20, c]) for c in range(3)) == (255, 0, 0)  # 蓝胜出


def test_compose_offset_placement():
    h, w = 40, 40
    bg = np.zeros((h, w, 3), dtype=np.uint8)
    red = _rgba(20, 20, (255, 0, 0, 255))  # 仅覆盖左上 20x20
    out = compose_frame(bg, [(red, (0, 0))])
    # 左上被覆盖（红），右下仍黑
    assert tuple(int(out[5, 5, c]) for c in range(3)) == (0, 0, 255)
    assert tuple(int(out[35, 35, c]) for c in range(3)) == (0, 0, 0)


def test_compose_partial_alpha_blends():
    h, w = 40, 40
    bg = np.full((h, w, 3), 100, dtype=np.uint8)  # 灰 100（BGR）
    white_half = _rgba(w, h, (255, 255, 255, 128))  # 半透明白
    out = compose_frame(bg, [(white_half, (0, 0))])
    # 半透明叠加：结果应介于 100 与 255 之间，且 >100
    assert out[20, 20, 0] > 100
    assert out[20, 20, 0] < 255

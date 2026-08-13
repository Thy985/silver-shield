"""ADR-0035 D3 · Rasterizer 箭头顶点单测（评审缺口 #6 · atan2 三角箭头）。

_draw_arrow 按边方向 atan2 计算三角箭头两后顶点；用记录型 draw 验证几何正确、
箭尖落在 (x2,y2)、线段起止无误。
"""

from __future__ import annotations

from math import atan2, cos, sin

import pytest

from home_perception.visualizer.video.render.rasterizer import _draw_arrow, rasterize_scene
from home_perception.visualizer.video.render.svg import VectorScene


class _RecordingDraw:
    def __init__(self):
        self.lines = []
        self.polygons = []

    def rounded_rectangle(self, *a, **k):
        pass

    def text(self, *a, **k):
        pass

    def line(self, xy, *a, **k):
        self.lines.append(tuple(xy))

    def polygon(self, xy, *a, **k):
        self.polygons.append(tuple(xy))


def test_arrow_vertex_geometry():
    d = _RecordingDraw()
    arrow = {"x1": 10, "y1": 10, "x2": 100, "y2": 10, "color": (255, 0, 0), "width": 3}
    _draw_arrow(d, arrow)
    # 仅一支箭头 + 一条线
    assert len(d.lines) == 1
    assert len(d.polygons) == 1
    # 线段起止
    assert d.lines[0][0] == (10, 10) and d.lines[0][1] == (100, 10)
    # 箭尖落在 (x2,y2)
    poly = d.polygons[0]
    assert poly[0] == (100, 10)
    # 两后顶点符合 atan2 公式
    angle = atan2(10 - 10, 100 - 10)
    head = max(10, 3 * 4)
    left = (100 - head * cos(angle - 0.4), 10 - head * sin(angle - 0.4))
    right = (100 - head * cos(angle + 0.4), 10 - head * sin(angle + 0.4))
    assert pytest.approx(poly[1][0], abs=1e-6) == left[0]
    assert pytest.approx(poly[1][1], abs=1e-6) == left[1]
    assert pytest.approx(poly[2][0], abs=1e-6) == right[0]
    assert pytest.approx(poly[2][1], abs=1e-6) == right[1]


def test_rasterize_produces_rgba_with_arrow():
    scene = VectorScene(80, 80)
    scene.add_card(4, 4, 30, 20, (46, 204, 113), "card")
    scene.add_arrow(10, 10, 60, 60, (231, 76, 60), 3)
    from home_perception.visualizer.video.render.font_registry import FontRegistry

    img = rasterize_scene(scene, FontRegistry())
    assert img.mode == "RGBA"
    assert img.size == (80, 80)

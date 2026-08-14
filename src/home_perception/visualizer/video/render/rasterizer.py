"""ADR-0035 D3 · Rasterizer（VectorScene → RGBA · Pillow-first · 确定性）。

用 ``PIL.ImageDraw`` 把矢量场景绘成 RGBA（矩形/线/圆/三角箭头 marker/文本），
再交由 composer alpha 合成到 BGR 背景帧。零新原生依赖、确定性（§4 D3-8）。

字形统一经 ``FontRegistry`` 取（D3-7：业务代码不直接加载字体路径）。

见设计文档 §4（Visual Language / SVG Strategy）、§8 验收 5（视觉确定性）。
"""

from __future__ import annotations

from math import atan2, cos, sin

from PIL import Image, ImageDraw

from home_perception.visualizer.video.render.font_registry import FontRegistry
from home_perception.visualizer.video.render.svg import VectorScene


def rasterize_scene(scene: VectorScene, registry: FontRegistry) -> Image.Image:
    """VectorScene → 透明 RGBA 图层（同帧尺寸）。"""
    img = Image.new("RGBA", (scene.width, scene.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = registry.get(max(14, int(scene.height * 0.028)))
    for card in scene.cards:
        _draw_card(draw, card, font)
    for arrow in scene.arrows:
        _draw_arrow(draw, arrow)
    return img


def _draw_card(draw: ImageDraw.ImageDraw, card: dict, font) -> None:
    x, y, w, h = card["x"], card["y"], card["w"], card["h"]
    border = card["border"]
    alpha = card.get("alpha", 255)
    width = card.get("width", 3)
    border_a = (*border[:3], alpha) if len(border) == 3 else border
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, outline=border_a, width=width)
    draw.text((x + 12, y + 12), card["label"], font=font, fill=(238, 238, 238, alpha))


def _draw_arrow(draw: ImageDraw.ImageDraw, arrow: dict) -> None:
    x1, y1, x2, y2 = arrow["x1"], arrow["y1"], arrow["x2"], arrow["y2"]
    color = arrow["color"]
    width = arrow["width"]
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    # 自绘三角箭头 marker（Pillow 无原生 arrow marker，按边方向计算顶点，§4）。
    angle = atan2(y2 - y1, x2 - x1)
    head = max(10, width * 4)
    left = (x2 - head * cos(angle - 0.4), y2 - head * sin(angle - 0.4))
    right = (x2 - head * cos(angle + 0.4), y2 - head * sin(angle + 0.4))
    draw.polygon([(x2, y2), left, right], fill=color)


__all__ = ["rasterize_scene"]

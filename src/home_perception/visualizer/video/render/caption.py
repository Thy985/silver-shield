"""ADR-0035 D3 · Caption 文本层（Pillow 绘制 · 经 FontRegistry 取 CJK 字形）。

把单条字幕文本渲染为底部字幕条 RGBA（半透明深色底 + 白字）。由 composer 定位合成。

见设计文档 §2.5（VisualComposer 分支）、§9 D3-7（中文叠加）。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from home_perception.visualizer.video.render.font_registry import FontRegistry


def render_caption(text: str, width: int, height: int, registry: FontRegistry) -> Image.Image:
    """单条字幕 → 底部字幕条 RGBA（同帧宽）。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 170))  # 半透明深色底
    draw = ImageDraw.Draw(img)
    font = registry.get(max(16, int(height * 0.55)))
    # 脱敏后渲染（防路径/序列号泄漏，D3-4）。
    safe = _sanitize(text)
    _, _, tw, th = draw.textbbox((0, 0), safe, font=font)
    x = max(12, (width - tw) // 2)
    y = max(4, (height - th) // 2)
    draw.text((x, y), safe, font=font, fill=(255, 255, 255, 255))
    return img


def _sanitize(text: str) -> str:
    """极简脱敏：截断过长文本，避免越界（路径/序列号已在 overlay 层统一处理）。"""
    if len(text) > 80:
        return text[:77] + "..."
    return text


__all__ = ["render_caption"]

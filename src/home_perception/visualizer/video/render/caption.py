"""ADR-0035 D3 · Caption 文本层（Pillow 绘制 · 经 FontRegistry 取 CJK 字形）。

把单条字幕文本渲染为底部字幕条 RGBA（半透明深色底 + 白字）。由 composer 定位合成。

D3-4 双保险：字幕文本在上游（``storyboard/generator._build_narration``）已脱敏，
本层**再脱敏一次**——渲染是「文本进入帧」的最后一道关口，不能假设上游一定干净
（作者 YAML 覆盖、未来新增文案来源都会绕过上游）。脱敏/净化/宽度截断统一调用
``video.text_safety``（唯一实现处）。

见设计文档 §2.5（VisualComposer 分支）、§6（D3-4）、§9 D3-7（中文叠加）。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from home_perception.visualizer.video.render.font_registry import FontRegistry
from home_perception.visualizer.video.text_safety import safe_display_text


def render_caption(text: str, width: int, height: int, registry: FontRegistry) -> Image.Image:
    """单条字幕 → 底部字幕条 RGBA（同帧宽）。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 170))  # 半透明深色底
    draw = ImageDraw.Draw(img)
    font = registry.get(max(16, int(height * 0.55)))
    # 脱敏 + 控制字符净化 + CJK 宽度截断后渲染（防路径/序列号泄漏与破版，D3-4）。
    safe = _sanitize(text)
    _, _, tw, th = draw.textbbox((0, 0), safe, font=font)
    x = max(12, (width - tw) // 2)
    y = max(4, (height - th) // 2)
    draw.text((x, y), safe, font=font, fill=(255, 255, 255, 255))
    return img


def _sanitize(text: str) -> str:
    """脱敏 + 净化 + 按显示宽度截断（CJK 全角计 2 单元，见 text_safety）。

    旧实现按 ``len(text) > 80`` 截断：对中文低估宽度近一倍，且不做二次脱敏、
    不过滤控制字符。现统一委派 ``text_safety.safe_display_text``。
    """
    return safe_display_text(text)


__all__ = ["render_caption"]

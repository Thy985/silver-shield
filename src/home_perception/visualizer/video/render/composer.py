"""ADR-0035 D3 · Composer（帧合成 · alpha 叠加到 BGR 背景帧）。

z-order 五层（§2.5）：background(BGR) → evidence(SVG 矢量) → annotation →
text(字幕条) → provenance(水印/角标)。本模块只做 RGBA→BGR 确定性合成。

见设计文档 §2.5（VisualComposer + Renderer 分支）、§4（确定性）。
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np
from PIL import Image


def compose_frame(
    background_bgr: np.ndarray,
    layers: Sequence[tuple[Image.Image, tuple[int, int]]],
) -> np.ndarray:
    """BGR 背景 + 有序 RGBA 图层 → 合成后 BGR 帧（确定性）。

    ``layers`` 顺序即叠放顺序（先叠在下，后叠在上）。每个图层以 (rgba_image, (x, y)) 定位。
    """
    height, width = background_bgr.shape[:2]
    bg = Image.fromarray(cv2.cvtColor(background_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    acc = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for rgba, (x, y) in layers:
        acc = Image.alpha_composite(acc, _placed(rgba, (width, height), (x, y)))
    out = Image.alpha_composite(bg, acc).convert("RGB")
    return cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)


def _placed(rgba: Image.Image, size: tuple[int, int], offset: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(rgba, offset, rgba)  # 以图层自身 alpha 作掩码
    return canvas


__all__ = ["compose_frame"]

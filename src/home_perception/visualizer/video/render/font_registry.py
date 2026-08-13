"""ADR-0035 D3 · FontRegistry（D3-7 受控字体资源抽象）。

业务代码（caption / overlay / rasterizer）**不直接加载字体路径**，统一向 FontRegistry
取字形；由 Rasterizer 负责光栅化（防散加载字体）。

- 优先：受控字体资源 ``assets/fonts/NotoSansCJK-Regular.ttf``（OFL，可控、可版本化）；
- 回退：系统 CJK 字体（仅读取，不依赖）；
- 最终降级：PIL 默认位图字体（无 CJK 字形，但保证不崩、不阻塞 pipeline）。

见设计文档 §9 D3-7。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import ImageFont

logger = logging.getLogger(__name__)

# 受控字体资产落点（D3-7）。
_ASSET_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansCJK-Regular.ttf"
)

# 系统 CJK 字体候选（仅读取，不强制）。
_SYSTEM_CANDIDATES = [
    r"C:/Windows/Fonts/msyh.ttc",
    r"C:/Windows/Fonts/simhei.ttf",
    r"C:/Windows/Fonts/NotoSansSC-VF.ttf",
    r"C:/Windows/Fonts/Noto Sans SC (TrueType).otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]


class FontRegistry:
    """受控字体注册表（CJK 字形来源 · 业务代码统一入口）。"""

    def __init__(self, asset_path: Path | None = None) -> None:
        self._asset_path = Path(asset_path) if asset_path else _ASSET_PATH
        self._cache: dict[int, ImageFont.FreeTypeFont] = {}

    def has_controlled_font(self) -> bool:
        """受控字体资产是否就绪（用于 provenance / 测试断言）。"""
        return self._asset_path.exists()

    def get(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """取指定字号字体（缓存；缺失则系统字体，再降级默认位图字体）。"""
        if size in self._cache:
            return self._cache[size]
        font = self._load(size)
        self._cache[size] = font
        return font

    def _load(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = [self._asset_path, *_SYSTEM_CANDIDATES]
        for cand in candidates:
            if not cand:
                continue
            path = Path(cand)
            if not path.exists():
                continue
            try:
                return ImageFont.truetype(str(path), size)
            except OSError as exc:
                logger.debug("FontRegistry 字体加载失败，跳过", path=str(path), error=str(exc))
                continue
        return ImageFont.load_default()  # 最终降级（无 CJK，但不阻塞 pipeline）


__all__ = ["FontRegistry"]

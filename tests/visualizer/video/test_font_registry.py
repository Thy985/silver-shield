"""ADR-0035 D3 · FontRegistry 三档回退单测（评审缺口 #2 · D3-7）。

受控字体资产 → 系统 CJK 字体 → PIL 默认位图字体。三档均可单独验证。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import ImageFont

from home_perception.visualizer.video.render import font_registry
from home_perception.visualizer.video.render.font_registry import FontRegistry


def _first_existing() -> str | None:
    for p in font_registry._SYSTEM_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def test_controlled_tier(tmp_path: Path, monkeypatch):
    existing = _first_existing()
    if existing is None:
        pytest.skip("无可用系统字体，无法构造受控档对比")
    # 把「受控资产」指向一个真实存在的字体文件 → 走第一档 FreeType
    reg = FontRegistry(asset_path=Path(existing))
    assert reg.has_controlled_font() is True
    font = reg.get(20)
    assert isinstance(font, ImageFont.FreeTypeFont)


def test_system_fallback_tier(tmp_path: Path, monkeypatch):
    existing = _first_existing()
    if existing is None:
        pytest.skip("无可用系统字体")
    # 受控资产缺失，系统候选指向真实字体 → 第二档 FreeType
    monkeypatch.setattr(font_registry, "_SYSTEM_CANDIDATES", [existing])
    reg = FontRegistry(asset_path=tmp_path / "missing.ttf")
    assert reg.has_controlled_font() is False
    font = reg.get(20)
    assert isinstance(font, ImageFont.FreeTypeFont)


def test_default_bitmap_fallback(tmp_path: Path, monkeypatch):
    # 受控资产缺失 + 系统候选清空 → 第三档 PIL 默认字体（不崩、不阻塞 pipeline）。
    # 注：PIL 11 起 load_default() 返回 FreeTypeFont，故按「可用字体」判定而非具体子类。
    monkeypatch.setattr(font_registry, "_SYSTEM_CANDIDATES", [])
    reg = FontRegistry(asset_path=tmp_path / "missing.ttf")
    assert reg.has_controlled_font() is False
    font = reg.get(20)
    assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))
    assert font.getlength("x") > 0


def test_cache_returns_same_object(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(font_registry, "_SYSTEM_CANDIDATES", [])
    reg = FontRegistry(asset_path=tmp_path / "missing.ttf")
    assert reg.get(24) is reg.get(24)

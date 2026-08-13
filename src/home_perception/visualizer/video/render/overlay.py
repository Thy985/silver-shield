"""ADR-0035 D3 · Overlay 层（provenance + 水印 + 脱敏 · 强制 · 不可关 · D3-4）。

每帧强制（fail-closed，不可配置关闭）：
1. ``程序化合成 · 非真实录像`` 水印（防误当真实监控录像，呼应 ADR-0035 非目标）；
2. provenance 角标（scenario_id + seed + generator.fingerprint）——
   **必须包含 scenario_id**（§8 验收 9 Frame provenance）；
3. 角色标签脱敏（D7b：Visitor-B / Resident-A，禁真实姓名/设备序列号/家庭地址）。

见设计文档 §6（D3-4 脱敏 + provenance 强制叠加）、§8 验收 6 / 9。
"""

from __future__ import annotations

import re

from PIL import Image, ImageDraw

from home_perception.visualizer.video.render.font_registry import FontRegistry

# 真实路径（Windows/Linux）模式——脱敏。
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\"'\s]+|/[^\"'\s]*[\\/][^\"'\s]+")
# 长数字串（疑似设备序列号/身份证）。
_LONG_DIGITS_RE = re.compile(r"\b\d{8,}\b")


def desensitize(text: str) -> str:
    """脱敏：抹除真实路径与长数字序列（D3-4 D7b）。"""
    text = _PATH_RE.sub("[REDACTED_PATH]", text)
    text = _LONG_DIGITS_RE.sub("[REDACTED_ID]", text)
    return text


def shorten_label(node, ref: str) -> str:
    """节点显示标签（脱敏）；无 label 时回退 ref 短形式（# 之后）。"""
    label = ""
    if node is not None:
        label = node.get("label") if isinstance(node, dict) else getattr(node, "label", None) or ""
    if label:
        return desensitize(label)
    short = ref.split("#")[-1] if "#" in ref else ref
    return desensitize(short)


def render_provenance_layer(
    width: int,
    height: int,
    scenario_id: str,
    seed: int,
    fingerprint: str,
    registry: FontRegistry,
) -> Image.Image:
    """强制叠加层（RGBA 全帧）：水印 + provenance 角标。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    wm_font = registry.get(max(14, int(height * 0.03)))
    draw.text((10, 10), "程序化合成 · 非真实录像", font=wm_font, fill=(255, 255, 255, 200))
    prov = f"{scenario_id} · seed={seed} · {fingerprint[:12]}"
    prov_font = registry.get(max(12, int(height * 0.025)))
    bbox = draw.textbbox((0, 0), prov, font=prov_font)
    px = width - bbox[2] - 10
    py = height - bbox[3] - 10
    draw.text((px, py), prov, font=prov_font, fill=(255, 255, 255, 180))
    return img


__all__ = ["desensitize", "render_provenance_layer", "shorten_label"]

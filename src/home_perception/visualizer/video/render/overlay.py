"""ADR-0035 D3 · Overlay 层（provenance + 水印 + 脱敏 · 强制 · 不可关 · D3-4）。

每帧强制（fail-closed，不可配置关闭）：
1. ``程序化合成 · 非真实录像`` 水印（防误当真实监控录像，呼应 ADR-0035 非目标）；
2. provenance 角标（scenario_id + seed + generator.fingerprint）——
   **必须包含 scenario_id**（§8 验收 9 Frame provenance）；
3. 角色标签脱敏（D7b：Visitor-B / Resident-A，禁真实姓名/设备序列号/家庭地址）。

角标文本由 ``provenance_text`` 单点产出：编译器的一致性断言与实际渲染共用同一格式化
函数，格式串一旦丢掉 scenario_id，断言立即失败（避免「自己拼串自己断言」的同义反复）。

脱敏/净化实现统一在 ``video.text_safety``（唯一实现处），本模块仅调用并重导出
``desensitize`` 以保持既有 import 路径兼容。

见设计文档 §6（D3-4 脱敏 + provenance 强制叠加）、§8 验收 6 / 9。
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from home_perception.visualizer.video.render.font_registry import FontRegistry
from home_perception.visualizer.video.text_safety import (
    desensitize,
    sanitize_display_text,
    truncate_display,
)

# 强制水印文案（不可配置关闭 · ADR-0035 非目标：不得被误当真实录像）。
WATERMARK_TEXT = "程序化合成 · 非真实录像"

# 节点标签显示宽度上限（单元数；卡片内单行，超出以 … 收尾）。
_LABEL_CELLS = 28


def provenance_text(scenario_id: str, seed: int, fingerprint: str) -> str:
    """provenance 角标文本（**唯一**格式化处）。

    ``scenario_id`` 必须出现在返回串中（§8 验收 9 Frame provenance 的实质约束）。
    编译器断言与渲染共用本函数，故格式漂移会被一致性断言直接抓到。
    """
    return f"{scenario_id} · seed={seed} · {fingerprint[:12]}"


def shorten_label(node: Any | None, ref: str) -> str:
    """节点显示标签：净化 + 脱敏 + 宽度截断；无 label 时回退 ref 短形式（``#`` 之后）。

    ``node`` 允许为 dict（loader 投影运行时形态）或带 ``label`` 属性的对象，或 None。
    label 来自 artifact（外部数据），故必须过滤换行/控制字符——否则可越出卡片破版。
    """
    label = ""
    if isinstance(node, dict):
        label = node.get("label") or ""
    elif node is not None:
        label = getattr(node, "label", None) or ""
    raw = label or (ref.split("#")[-1] if "#" in ref else ref)
    return truncate_display(sanitize_display_text(desensitize(str(raw))), _LABEL_CELLS)


def render_provenance_layer(
    width: int,
    height: int,
    scenario_id: str,
    seed: int,
    fingerprint: str,
    registry: FontRegistry,
) -> Image.Image:
    """强制叠加层（RGBA 全帧）：水印（左上） + provenance 角标（右下）。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    wm_font = registry.get(max(14, int(height * 0.03)))
    draw.text((10, 10), WATERMARK_TEXT, font=wm_font, fill=(255, 255, 255, 200))
    prov = provenance_text(scenario_id, seed, fingerprint)
    prov_font = registry.get(max(12, int(height * 0.025)))
    bbox = draw.textbbox((0, 0), prov, font=prov_font)
    px = width - bbox[2] - 10
    py = height - bbox[3] - 10
    draw.text((px, py), prov, font=prov_font, fill=(255, 255, 255, 180))
    return img


__all__ = [
    "WATERMARK_TEXT",
    "desensitize",
    "provenance_text",
    "render_provenance_layer",
    "shorten_label",
]

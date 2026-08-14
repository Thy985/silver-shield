"""ADR-0035 D3 · VectorScene 建模（阶段 6 信息图层 · SVG/SVG-like 中间表示）。

把 ``VisualSceneGraph``（表达层）翻译成**带坐标的矢量基元**（卡片 + 箭头），作为
rasterizer 的输入。这是 D3 内部声明式中间表示——与 D2 ECharts 图共享同一份
``evidence.graph``，仅渲染目标不同（屏幕 DOM vs 视频帧 RGBA）。

Mapping 边界（§4）：卡片/箭头的坐标与字形只来自 ``VisualSceneGraph``（最终来自
EvidenceGraph），``VisualSceneGraph`` 是唯一允许把「事实」翻译成「版面」的地方。

glyph → 边框色（确定性）：
  detection_box → 绿（检测框）；warn_badge → 红（风险徽章）；
  message_icon → 蓝（消息/通知）；timeline → 灰（时间轴）；risk_score → 橙（风险分）。

见设计文档 §4（Visual Language / SVG Strategy）、§2.8（render/svg.py）。
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol

from home_perception.visualizer.video.scene.schema import VisualSceneGraph


class LabelFormatter(Protocol):
    """节点 → 显示标签的回调协议（脱敏/净化由实现方负责，见 ``render.overlay``）。

    此前该回调是无类型的裸参数，签名靠约定；一旦调用方与实现方参数顺序漂移，
    只能在渲染出错时才发现。协议化后，类型检查与 review 都能直接看出契约。
    """

    def __call__(self, node: Any | None, ref: str) -> str:
        """``node`` 为图节点（dict 或对象，可能为 None）；``ref`` 为节点 id。"""
        ...

# 区域水平区间（占宽比例）：左-中-右三栏 + 全屏上下文。
_REGION_X: dict[str, tuple[float, float]] = {
    "left": (0.06, 0.36),
    "center": (0.38, 0.62),
    "right": (0.64, 0.94),
    "full": (0.06, 0.94),
}

# glyph → 边框色（RGB）。deterministic。
_GLYPH_COLOR: dict[str, tuple[int, int, int]] = {
    "detection_box": (46, 204, 113),
    "warn_badge": (231, 76, 60),
    "message_icon": (52, 152, 219),
    "timeline": (149, 165, 166),
    "risk_score": (230, 126, 34),
}

# 处理顺序（保证多区域时的稳定布局）。
_REGION_ORDER = ["left", "center", "right", "full"]


class VectorScene:
    """矢量场景中间表示（卡片 + 箭头基元，带绝对像素坐标）。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.cards: list[dict] = []
        self.arrows: list[dict] = []

    def add_card(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        border: tuple[int, int, int, int],
        label: str,
        alpha: int = 255,
        width: int = 3,
    ) -> None:
        self.cards.append(
            {"x": x, "y": y, "w": w, "h": h, "border": border, "label": label, "alpha": alpha, "width": width}
        )

    def add_arrow(self, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int, int], width: int) -> None:
        self.arrows.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color, "width": width})


def build_vector_scene(
    scene_graph: VisualSceneGraph,
    node_by_id: dict,
    width: int,
    height: int,
    shorten_label: LabelFormatter,
    canvas: list | None = None,
    highlight: set[str] | None = None,
    fade: set[str] | None = None,
) -> VectorScene:
    """VisualSceneGraph → VectorScene（确定性坐标计算）。

    - 同区域元素垂直堆叠；
    - 卡片边框色由 glyph 决定；
    - 箭头连接同 shot 内被引用的边两端（坐标取卡片中心）。
    - 当传入 ``canvas``（决策画布节点列表）时，改为渲染决策解释链：节点纵向居中排列、
      相邻节点以箭头相连；``highlight`` 节点高亮（亮蓝粗边），``fade`` 节点淡出（灰低透明）。
      这是「同一张决策图随时间逐步揭示」的空间实现（ADR-0035 D3-A 决策幕）。
    """
    scene = VectorScene(width, height)
    if canvas is not None:
        _render_canvas(scene, canvas, highlight or set(), fade or set(), width, height)
        return scene

    grouped: dict[str, list[dict]] = {r: [] for r in _REGION_ORDER}
    for el in scene_graph.layout:
        grouped.setdefault(el.region, []).append(el)

    card_center: dict[str, tuple[int, int]] = {}
    top_y = int(height * 0.12)
    card_h = int(height * 0.15)
    gap = int(height * 0.02)
    for region in _REGION_ORDER:
        x0, x1 = _REGION_X[region]
        rx = int(x0 * width)
        rw = int((x1 - x0) * width)
        y = top_y
        for el in grouped.get(region, []):
            node = node_by_id.get(el.ref)
            label = shorten_label(node, el.ref)
            border = _GLYPH_COLOR.get(el.glyph, (149, 165, 166))
            scene.add_card(rx, y, rw, card_h, border, label)
            card_center[el.ref] = (rx + rw // 2, y + card_h // 2)
            y += card_h + gap

    for arrow in scene_graph.arrows:
        c_from = card_center.get(arrow["from"])
        c_to = card_center.get(arrow["to"])
        if c_from and c_to:
            scene.add_arrow(
                c_from[0], c_from[1], c_to[0], c_to[1],
                (231, 76, 60, 255), max(2, int(width * 0.004)),
            )
    return scene


# 决策画布节点配色（RGBA）。
_CANVAS_HL = (52, 152, 219, 255)       # 高亮：亮蓝
_CANVAS_NEUTRAL = (149, 165, 166, 200)  # 未达步骤：中性灰（仍可见）
_CANVAS_FADE = (120, 120, 120, 70)      # 已淡出：暗灰低透明
_ARROW_HL = (52, 152, 219, 220)
_ARROW_NEUTRAL = (149, 165, 166, 110)


def _node_attr(node: object, key: str, default: object = None) -> object:
    """决策画布节点取值（兼容 dict 与 pydantic ``DecisionCanvasNode``）。"""
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def _render_canvas(
    scene: VectorScene,
    canvas: list,
    highlight: set[str],
    fade: set[str],
    width: int,
    height: int,
) -> None:
    """把决策画布节点渲染为纵向因果链（逐帧由 highlight/fade 控制明暗）。"""
    n = len(canvas)
    if n == 0:
        return
    rx = int(width * 0.16)
    rw = int(width * 0.68)
    top = int(height * 0.08)
    bottom = int(height * 0.92)
    avail = bottom - top
    gap = int(height * 0.018)
    card_h = max(22, (avail - gap * (n - 1)) // n)
    y = top
    centers: dict[str, tuple[int, int]] = {}
    for node in canvas:
        nid = _node_attr(node, "id")
        if nid in highlight:
            border, alpha, wdt = _CANVAS_HL, 255, max(3, int(width * 0.006))
        elif nid in fade:
            border, alpha, wdt = _CANVAS_FADE, 80, 1
        else:
            border, alpha, wdt = _CANVAS_NEUTRAL, 200, 2
        label = _node_attr(node, "label", nid)
        # 卡片略矮时用单/双行截断，避免溢出（保持确定性）。
        scene.add_card(rx, y, rw, card_h, border, label, alpha=alpha, width=wdt)
        centers[nid] = (rx + rw // 2, y + card_h // 2)
        y += card_h + gap
    for a, b in itertools.pairwise(canvas):
        ca = centers[_node_attr(a, "id")]
        cb = centers[_node_attr(b, "id")]
        connected = (_node_attr(a, "id") in highlight) and (_node_attr(b, "id") in highlight)
        color = _ARROW_HL if connected else _ARROW_NEUTRAL
        scene.add_arrow(ca[0], ca[1], cb[0], cb[1], color, max(2, int(width * 0.003)))


__all__ = ["LabelFormatter", "VectorScene", "build_vector_scene"]

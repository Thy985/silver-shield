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
        nid: str | None = None,
        font_size: int | None = None,
    ) -> None:
        self.cards.append(
            {
                "x": x, "y": y, "w": w, "h": h, "border": border, "label": label,
                "alpha": alpha, "width": width, "nid": nid, "font_size": font_size,
            }
        )

    def add_arrow(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: tuple[int, int, int, int],
        width: int,
        from_id: str | None = None,
        to_id: str | None = None,
    ) -> None:
        self.arrows.append(
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color, "width": width,
             "from_id": from_id, "to_id": to_id}
        )


def build_vector_scene(
    scene_graph: VisualSceneGraph,
    node_by_id: dict,
    width: int,
    height: int,
    shorten_label: LabelFormatter,
    canvas: list | None = None,
    highlight: set[str] | None = None,
    fade: set[str] | None = None,
    caption_reserve: int = 0,
) -> VectorScene:
    """VisualSceneGraph → VectorScene（确定性坐标计算）。

    - 同区域元素垂直堆叠；
    - 卡片边框色由 glyph 决定；
    - 箭头连接同 shot 内被引用的边两端（坐标取卡片中心）。
    - 当传入 ``canvas``（决策画布节点列表）时，改为渲染决策解释链：节点纵向居中排列、
      相邻节点以箭头相连；``highlight`` 节点高亮（亮蓝粗边），``fade`` 节点淡出（灰低透明）。
      这是「同一张决策图随时间逐步揭示」的空间实现（ADR-0035 D3-A 决策幕）。
    - ``caption_reserve``：底部为字幕条预留的像素高度（compiler 在 ``height - caption_h``
      区域叠加字幕），避免决策画布末排卡片与字幕重叠（review 代码质量 #3）。
    """
    scene = VectorScene(width, height)
    if canvas is not None:
        _render_canvas(scene, canvas, highlight or set(), fade or set(), width, height, caption_reserve)
        return scene

    grouped: dict[str, list[dict]] = {r: [] for r in _REGION_ORDER}
    for el in scene_graph.layout:
        grouped.setdefault(el.region, []).append(el)

    card_center: dict[str, tuple[int, int]] = {}
    top_y = int(height * 0.12)
    card_h = int(height * 0.15)
    gap = int(height * 0.02)
    usable_bottom = height - caption_reserve - int(height * 0.02)
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
            # 防止字幕条区域被卡片覆盖（review 代码质量 #3）。
            if y > usable_bottom:
                break

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
    """决策画布节点取值（仅接受 pydantic ``DecisionCanvasNode``；dict 直接 TypeError）。

    渲染层是 ``extra='forbid'`` schema 的下游消费者，绝不接受 dict 旁路——否则绕过 schema
    的测试也能渲染，等于校验空转（review 代码质量 #4）。
    """
    if isinstance(node, dict):
        raise TypeError(
            f"决策画布节点必须是 DecisionCanvasNode，收到 dict（绕过 schema 校验）：{node!r}"
        )
    return getattr(node, key, default)


def _canvas_card_style(
    nid: str, highlight: set[str], fade: set[str], width: int
) -> tuple[tuple[int, int, int, int], int, int]:
    """单卡片的（边框色, alpha, 边框宽）由 highlight/fade 决定（集中规则，供渲染与就地改写复用）。"""
    if nid in highlight:
        return _CANVAS_HL, 255, max(3, int(width * 0.006))
    if nid in fade:
        return _CANVAS_FADE, 80, 1
    return _CANVAS_NEUTRAL, 200, 2


def _canvas_arrow_style(a_id: str, b_id: str, fade: set[str]) -> tuple[int, int, int, int]:
    """箭头颜色：两端均可见（未淡出）即视为因果链已连，否则中性灰。

    与旧实现（要求两端都在 highlight 单点高亮）不同——单步 highlight 仅含一个节点，
    旧逻辑下箭头恒为中性；改为「两端未淡出」后，因果链随逐步揭示自动「画出来」。
    """
    connected = (a_id not in fade) and (b_id not in fade)
    return _ARROW_HL if connected else _ARROW_NEUTRAL


def _render_canvas(
    scene: VectorScene,
    canvas: list,
    highlight: set[str],
    fade: set[str],
    width: int,
    height: int,
    caption_reserve: int = 0,
) -> None:
    """把决策画布节点渲染为纵向因果链（坐标只算一次；明暗由 highlight/fade 控制）。"""
    n = len(canvas)
    if n == 0:
        return
    rx = int(width * 0.16)
    rw = int(width * 0.68)
    top = int(height * 0.06)
    # 预留字幕条：compiler 在 height - caption_h 叠加字幕，决策画布末排不可侵入（review #3）。
    bottom = height - caption_reserve - int(height * 0.02)
    avail = bottom - top
    gap = int(height * 0.014)
    card_h = max(20, (avail - gap * (n - 1)) // n)
    # 字体随卡片高度自适应，防止多行标签（如「选中\nNOTIFY_FAMILY」）裁切（review 潜在 Bug 2）。
    max_lines = max((str(_node_attr(node, "label", "")).count("\n") + 1) for node in canvas)
    font_size = max(11, min(int(height * 0.030), int((card_h - 24) / (1.3 * max_lines))))
    y = top
    centers: dict[str, tuple[int, int]] = {}
    for node in canvas:
        nid = _node_attr(node, "id")
        border, alpha, wdt = _canvas_card_style(nid, highlight, fade, width)
        label = _node_attr(node, "label", nid)
        scene.add_card(rx, y, rw, card_h, border, label, alpha=alpha, width=wdt, nid=nid, font_size=font_size)
        centers[nid] = (rx + rw // 2, y + card_h // 2)
        y += card_h + gap
    for a, b in itertools.pairwise(canvas):
        a_id = _node_attr(a, "id")
        b_id = _node_attr(b, "id")
        color = _canvas_arrow_style(a_id, b_id, fade)
        scene.add_arrow(centers[a_id][0], centers[a_id][1], centers[b_id][0], centers[b_id][1],
                        color, max(2, int(width * 0.003)), from_id=a_id, to_id=b_id)


def apply_canvas_highlight(scene: VectorScene, highlight: set[str] | None, fade: set[str] | None) -> None:
    """对已构建的决策画布 ``VectorScene`` 就地改写高亮/淡出，避免逐帧重建几何（review 潜在 Bug 1）。

    ``build_vector_scene(canvas=...)`` 的卡片坐标不随 highlight/fade 改变（仅边/框 alpha 变），
    故决策幕每帧只需调用本函数改写 alpha，而非重建整张矢量场景——30s×30fps 不再重复 900 次
    全量 add_card/add_arrow。
    """
    hl = highlight or set()
    fd = fade or set()
    for card in scene.cards:
        nid = card.get("nid")
        if nid is None:
            continue
        border, alpha, wdt = _canvas_card_style(nid, hl, fd, scene.width)
        card["border"] = border
        card["alpha"] = alpha
        card["width"] = wdt
    for arrow in scene.arrows:
        a_id = arrow.get("from_id")
        b_id = arrow.get("to_id")
        if a_id is None or b_id is None:
            continue
        arrow["color"] = _canvas_arrow_style(a_id, b_id, fd)


__all__ = ["LabelFormatter", "VectorScene", "apply_canvas_highlight", "build_vector_scene"]

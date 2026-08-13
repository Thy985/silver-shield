"""ADR-0035 D3 · VisualSceneGraph schema（表达层 · 空间维）。

层边界契约（§2.4.1）：本模块是**表达层**，只承载空间排布，**禁止**携带任何解释
语义字段。下列模型均已 `extra="forbid"` 机械锁定字段集——若有人新增
`why`/`purpose`/`audience_need`/`explanation_order` 等语义字段，`ValidationError` 立即打回。

见设计文档 §3（VisualSceneGraph / VisualElement）、§2.4（VisualScene）。

**禁用字段（机械可 enforcement）**：`why` `purpose` `audience_need` `explanation_order`
及任何「为什么/给谁」的解释语义（这些属于 `storyboard/schema.py` 语义层）。

**合法耦合**：`VisualElement.ref` ⊆ `Storyboard.evidence_refs` ⊆ `EvidenceGraph.nodes`
（§8 验收 9 Scene consistency）；表达层不得引用语义层未引用的证据。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# 版面区域闭集（左-中-右三栏 + 全屏，对应 reasoning shot 默认布局）。
Region = Literal["left", "center", "right", "full"]

# 视觉字形闭集（信息图层的图元类别）。
Glyph = Literal[
    "detection_box",
    "warn_badge",
    "message_icon",
    "timeline",
    "risk_score",
]


class VisualElement(BaseModel):
    """表达层单元素（画面怎么摆）。

    ``ref`` 指向 EvidenceGraph 节点（fail-closed 解析）；`region`/`glyph` 是纯视觉编排。
    """

    model_config = ConfigDict(extra="forbid")  # 表达层：禁 why/purpose/audience_need/explanation_order

    ref: str  # 指向 EvidenceGraph 节点 id
    region: Region
    glyph: Glyph


class VisualSceneGraph(BaseModel):
    """单镜头视觉场景图（表达层 · 空间维）。

    ``arrows`` 由 EvidenceGraph 边映射而来（非发明）：`from`/`to` 指向 `evidence_refs`，
    `style` 仅视觉（如 `causal_red`）。
    """

    model_config = ConfigDict(extra="forbid")  # 表达层：禁 why/purpose/audience_need/explanation_order

    shot: str  # 所属 shot name（与 Storyboard.shots[].name 对应）
    layout: list[VisualElement]
    arrows: list[dict] = []  # 每项: {"from": str, "to": str, "style": str}

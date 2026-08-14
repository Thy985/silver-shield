"""ADR-0035 D3 · Storyboard schema（语义层 · 时间维）。

层边界契约（§2.4.1）：本模块是**语义层**，只承载时间/意图/受众，**禁止**携带任何
空间/视觉字段。下列模型均已 `extra="forbid"` 机械锁定字段集——若有人新增
`x`/`y`/`color`/`layout`/`font`/`shape` 等空间字段，`ValidationError` 立即打回。

见设计文档 §3（Storyboard / ShotSpec）、§2.3（StoryboardGenerator）。

**禁用字段（机械可 enforcement）**：`x` `y` `color` `layout` `font` `shape`
及任何空间/像素级属性（这些属于 `scene/schema.py` 表达层）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 镜头类别闭集（与 §2.2 canonical 5-shot 弧线 + cross_modal 一致）。
ShotKind = Literal[
    "environment",
    "detection_overlay",
    "reasoning",
    "decision",
    "cross_modal",
    "closure",
]


class DecisionStep(BaseModel):
    """决策幕单步（语义层 · 决策解释链）。

    仅用于 ``decision`` shot，把「为什么是这个动作而非其他」拆成可逐步揭示的因果步骤。
    每条携带：阶段语义（stage）、字幕（caption，同步进 ``ShotSpec.narration``）、
    该步高亮/淡出的**决策画布**节点 id（``highlight``/``fade``）。空间排版由表达层
    ``VisualSceneGraph.decision_canvas`` 负责，本模型不携带任何坐标/颜色字段。
    """

    model_config = ConfigDict(extra="forbid")  # 语义层：禁 x/y/color/layout/font/shape

    stage: Literal[
        "observation", "risk", "policy", "candidate", "selected", "execution", "closure"
    ]
    caption: str  # 字幕文本（与 narration 同步，确定性脱敏）
    highlight: list[str] = Field(default_factory=list)  # 此步高亮的决策画布节点 id
    fade: list[str] = Field(default_factory=list)  # 此步淡出的决策画布节点 id（其余保持可见）


class ShotSpec(BaseModel):
    """单镜头分镜（语义层 · 时间维）。

    只描述「何时播 / 给谁看 / 引用哪些证据 / 解释意图」，绝不描述「怎么摆」。
    """

    model_config = ConfigDict(extra="forbid")  # 语义层：禁 x/y/color/layout/font/shape

    name: str  # context/detection/reasoning/decision/closure(+cross_modal)
    kind: ShotKind
    duration_s: float  # 该镜头时长（秒）
    purpose: str  # 人类可读叙事意图（可解释性元数据）
    audience_need: str = ""  # 该镜头要满足观众什么信息需求（默认空）
    evidence_refs: list[str] = Field(default_factory=list)  # 指向 EvidenceGraph 节点 id
    narration: list[str] = Field(default_factory=list)  # 字幕逐句（证据值 + 文案常量填充）
    decision_steps: list[DecisionStep] = Field(default_factory=list)  # 仅 decision shot 使用


class Storyboard(BaseModel):
    """完整分镜（语义层 · 时间维 · 可审计）。

    叙事是否忠于证据，看本对象即可：每个 ShotSpec.evidence_refs 必须能在
    ``EvidenceGraph.nodes`` 解析（fail-closed，见 §8 验收 9 Story consistency）。
    """

    model_config = ConfigDict(extra="forbid")  # 语义层：禁 x/y/color/layout/font/shape

    demo_id: str
    title_zh: str
    scenario_ref: str  # 关联 validation Scenario（事实层来源）
    audience: str = "general"  # 受众维度（general/judges/investors/family...）
    shots: list[ShotSpec]
    version: int = 1

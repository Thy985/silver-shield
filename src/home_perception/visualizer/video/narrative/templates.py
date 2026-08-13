"""ADR-0035 D3 · ScenarioTemplate 声明（固定模板 · 非规则引擎）。

本模块只声明**固定**场景类别模板（canonical 5-shot 弧线 + 可选 cross_modal），
不含任何「按证据值决定镜头」的分支逻辑——那会退化成规则引擎（违背 §2.2 / §0.3 红线）。
`instantiate_narrative_template` 只做「按模板填 ref」，本模块的模板是「填什么」的唯一来源。

见设计文档 §2.2（NarrativeTemplateCompiler）、§2.8（narrative/templates.py）、§9 D3-9。
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from home_perception.visualizer.video.storyboard.schema import ShotKind

# ref_kind → 候选 EvidenceGraph 节点类型（按优先级；首个命中的类型优先）。
# 仅数据查找，非叙事决策。与 §2.2 模板示例一致：
#   scenario_meta→Scenario；perception_event→Event；track_id→Detection(回退 Event)；
#   decision_evidence/decision_node→Decision；action_node/command_type/action_landed→Action；
#   episode→Episode；cross_modal→Link。
REF_KIND_TO_NODE_TYPE: dict[str, list[str]] = {
    "scenario_meta": ["Scenario"],
    "perception_event": ["Event"],
    "track_id": ["Detection", "Event"],
    "decision_evidence": ["Decision"],
    "decision_node": ["Decision"],
    "action_node": ["Action"],
    "command_type": ["Action"],
    "action_landed": ["Action"],
    "episode": ["Episode"],
    "cross_modal": ["Link"],
}

TemplateIntent = Literal[
    "explain_risk_decision",
    "explain_false_positive",
    "explain_normal_case",
    "explain_cross_modal",
]


class TemplateShot(TypedDict):
    """模板单镜头骨架（固定）：名称 / 类别 / 引用的证据 ref_kind 集合。"""

    name: str
    kind: ShotKind
    ref_kinds: list[str]


class ScenarioTemplate(BaseModel):
    """固定场景类别模板（声明式 · canonical 5-shot 弧线 + 可选 cross_modal）。

    字段集硬锁：除下列字段外不得出现任何额外字段。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    intent: TemplateIntent
    audience_question: str  # 本片要回答的观众疑问（模板常量，非生成）
    shots: list[TemplateShot]  # 固定 shot 序列（canonical 弧线）
    default_purposes: dict[str, str]  # shot name → 人类可读叙事意图（语义层文案常量）
    default_durations_s: dict[str, float] = {}  # shot name → 默认时长（秒）


# ── canonical 5-shot 弧线（§2.2 表格）──
_ELDERLY_SHOTS: list[TemplateShot] = [
    {"name": "context", "kind": "environment", "ref_kinds": ["scenario_meta"]},
    {"name": "detection", "kind": "detection_overlay", "ref_kinds": ["perception_event", "track_id"]},
    {"name": "reasoning", "kind": "reasoning", "ref_kinds": ["decision_evidence", "decision_node"]},
    {"name": "decision", "kind": "decision", "ref_kinds": ["action_node", "command_type"]},
    {"name": "closure", "kind": "closure", "ref_kinds": ["action_landed", "episode"]},
]
_ELDERLY_PURPOSES: dict[str, str] = {
    "context": "建立环境上下文：家门口·时间窗·脱敏角色标签",
    "detection": "展示异常行为如何被发现（停留/重复来访/接近）",
    "reasoning": "解释系统为何判定为风险（决策追溯）",
    "decision": "展示风险判断与触发的动作",
    "closure": "展示闭环完成（通知家属/社区协同确认）",
}
_ELDERLY_DURATIONS: dict[str, float] = {
    "context": 4.0,
    "detection": 5.0,
    "reasoning": 6.0,
    "decision": 4.0,
    "closure": 4.0,
}

_GENERIC_SHOTS: list[TemplateShot] = list(_ELDERLY_SHOTS)  # 默认复用同一弧线骨架
_GENERIC_PURPOSES: dict[str, str] = dict(_ELDERLY_PURPOSES)
_GENERIC_DURATIONS: dict[str, float] = dict(_ELDERLY_DURATIONS)

# cross_modal 追加镜头（仅当 EvidenceGraph 含 Link 节点时由 template_for_evidence 插入）。
_CROSS_MODAL_SHOT: TemplateShot = {
    "name": "cross_modal",
    "kind": "cross_modal",
    "ref_kinds": ["cross_modal"],
}
_CROSS_MODAL_PURPOSE: str = "展示跨模态关联证据如何互相印证（视觉/音频协同）"
_CROSS_MODAL_DURATION: float = 4.0

_REGISTRY: dict[str, ScenarioTemplate] = {
    "elderly_warning_case_v1": ScenarioTemplate(
        name="elderly_warning_case_v1",
        intent="explain_risk_decision",
        audience_question="为什么系统认为需要关注？",
        shots=_ELDERLY_SHOTS,
        default_purposes=_ELDERLY_PURPOSES,
        default_durations_s=_ELDERLY_DURATIONS,
    ),
    "generic_case_v1": ScenarioTemplate(
        name="generic_case_v1",
        intent="explain_risk_decision",
        audience_question="系统在此场景做了什么判断？",
        shots=_GENERIC_SHOTS,
        default_purposes=_GENERIC_PURPOSES,
        default_durations_s=_GENERIC_DURATIONS,
    ),
}

# 场景类别 → 基础模板名（固定映射，数据驱动选型，非叙事决策）。
_CATEGORY_TO_TEMPLATE: dict[str, str] = {
    "elderly_warning": "elderly_warning_case_v1",
    "generic": "generic_case_v1",
}


def _category_of(evidence: dict) -> str:
    """由 scenario_id / event_types 推导场景类别（固定映射，非按证据值分支叙事）。"""
    sid = (evidence.get("scenario_id") or "").lower()
    events = [e.lower() for e in (evidence.get("event_types") or ())]
    if "elderly" in sid or any(("elderly" in e or "dwell" in e or "fall" in e) for e in events):
        return "elderly_warning"
    return "generic"


def template_for_evidence(evidence: dict, template_name: str | None = None) -> ScenarioTemplate:
    """为给定证据选取固定模板（D3-1 纯消费；不触发验证判定）。

    - 显式 ``template_name`` 优先；否则按场景类别映射。
    - 若 ``EvidenceGraph`` 含 ``Link`` 节点（跨模态关联），追加固定 cross_modal 镜头。

    本函数只做「选哪个固定模板 / 是否含跨模态固定片段」的装配，**不含任何
    按证据值决定镜头内容/顺序的分支**（那属于规则引擎，违背 §2.2）。
    """
    base_name = template_name or _CATEGORY_TO_TEMPLATE.get(_category_of(evidence), "generic_case_v1")
    base = _REGISTRY[base_name]
    nodes = (evidence.get("graph") or {}).get("nodes") or []
    has_cross_modal = any(n["type"] == "Link" for n in nodes)
    if not has_cross_modal:
        return base
    return ScenarioTemplate(
        name=base.name,
        intent=base.intent,
        audience_question=base.audience_question,
        shots=[*base.shots, _CROSS_MODAL_SHOT],
        default_purposes={**base.default_purposes, "cross_modal": _CROSS_MODAL_PURPOSE},
        default_durations_s={**base.default_durations_s, "cross_modal": _CROSS_MODAL_DURATION},
    )


def materialize_shot_refs(evidence: dict, template: ScenarioTemplate) -> dict[str, list[str]]:
    """按模板 ``ref_kinds`` 把 EvidenceGraph 节点 id 填入每个 shot（确定性、无分支）。

    返回 ``{shot_name: [node_id, ...]}``。所有 ref 必来自真实图节点（fail-closed 解析）。
    编译器与分镜生成器共用，避免两处 ref 计算漂移（§8 验收 9 一致性）。
    """
    graph = evidence["graph"]
    nodes = graph["nodes"]
    node_ids_by_type: dict[str, list[str]] = {}
    for n in nodes:
        node_ids_by_type.setdefault(n["type"], []).append(n["id"])
    result: dict[str, list[str]] = {}
    for shot in template.shots:
        refs: list[str] = []
        for rk in shot["ref_kinds"]:
            for nt in REF_KIND_TO_NODE_TYPE[rk]:
                refs.extend(node_ids_by_type.get(nt, []))
        result[shot["name"]] = list(dict.fromkeys(refs))
    return result


__all__ = [
    "REF_KIND_TO_NODE_TYPE",
    "ScenarioTemplate",
    "TemplateShot",
    "materialize_shot_refs",
    "template_for_evidence",
]

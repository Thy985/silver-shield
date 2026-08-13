"""ADR-0035 D3 · NarrativeTemplateCompiler（阶段 3 · 模板实例化 · 非规则引擎）。

``instantiate_narrative_template`` 是**纯函数 · 确定性 · 模板实例化**：从
``evidence.graph`` 取节点，按 ``template.shots`` 的 ``ref_kinds`` 填充每 shot 的
``evidence_refs``；只决定「顺序与意图」（intent / reasoning_chain / audience_question），
**不产出任何自然语言文本、不调用 LLM、不生成图外节点、不包含任何按证据值决定
镜头内容/顺序的 if-else 分支**（否则即退化为规则引擎，违背 §2.2 / §0.3 红线 / §9 D3-9）。

见设计文档 §2.2（NarrativeTemplateCompiler）、§3（NarrativePlan / ReasoningStep）、§9 D3-9。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from home_perception.visualizer.video.narrative.templates import (
    ScenarioTemplate,
    materialize_shot_refs,
)

# step_kind 闭集（来自模板，非自由生成）。
StepKind = Literal[
    "observation",
    "interpretation",
    "policy_match",
    "decision",
    "closure",
    "cross_modal",
]

# NarrativePlan 解释意图闭集。
NarrativeIntent = Literal[
    "explain_risk_decision",
    "explain_false_positive",
    "explain_normal_case",
    "explain_cross_modal",
]

# EvidenceGraph 节点类型 → ReasoningStep.step_kind 映射（仅数据查找，非叙事决策）。
STEP_KIND_BY_NODE_TYPE: dict[str, StepKind] = {
    "Scenario": "observation",
    "Frame": "observation",
    "Detection": "observation",
    "Event": "observation",
    "Decision": "decision",
    "Action": "closure",
    "Episode": "closure",
    "Link": "cross_modal",
}


class ReasoningStep(BaseModel):
    """解释链单步（指向 EvidenceGraph 真实节点/边的 id，fail-closed 解析）。"""

    model_config = ConfigDict(extra="forbid")  # 字段集硬锁：禁越界增字段
    step_kind: StepKind
    ref: str


class NarrativePlan(BaseModel):
    """解释策略（语义层 · 非文本）。

    **严禁** ``text`` / ``sentence`` / ``narration`` 自然语言文本字段（§3 机械锁定）。
    只编排证据节点；句子由下游 ``storyboard/generator`` 从证据值 + 文案常量填充。
    """

    model_config = ConfigDict(extra="forbid")  # 字段集硬锁：严禁 text/sentence/narration
    intent: NarrativeIntent
    reasoning_chain: list[ReasoningStep]
    audience_question: str
    audience: str = "general"  # 受众维度（general/judges/investors/family...）


def instantiate_narrative_template(
    evidence: dict,
    template: ScenarioTemplate,
    audience: str = "general",
) -> NarrativePlan:
    """纯函数 · 确定性 · 模板实例化（**无 if/elif/else/match 分支决策**）。

    从 ``evidence["graph"]`` 取节点，按 ``template.shots`` 的 ``ref_kinds`` 填充每 shot
    的 ``evidence_refs``；再依节点类型派生 ``reasoning_chain``（observation/decision/
    closure/cross_modal）。所有 ref 均来自真实图节点（fail-closed 解析）。

    本函数体刻意仅含循环与数据查找（无分支语句）——可被静态断言「非规则引擎」。
    """
    graph = evidence["graph"]
    nodes = graph["nodes"]
    node_by_id = {n["id"]: n for n in nodes}
    shot_refs = materialize_shot_refs(evidence, template)
    ordered_shot_names = [shot["name"] for shot in template.shots]
    reasoning_chain = [
        ReasoningStep(
            step_kind=STEP_KIND_BY_NODE_TYPE.get(node_by_id[ref]["type"], "observation"),
            ref=ref,
        )
        for shot_name in ordered_shot_names
        for ref in shot_refs[shot_name]
    ]
    return NarrativePlan(
        intent=template.intent,
        reasoning_chain=reasoning_chain,
        audience_question=template.audience_question,
        audience=audience,
    )


__all__ = [
    "NarrativePlan",
    "ReasoningStep",
    "instantiate_narrative_template",
]

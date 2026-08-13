"""ADR-0035 D3 · NarrativeTemplateCompiler 测试（阶段 3）。

覆盖：模板实例化（canonical 5-shot / ref 解析）、非规则引擎（无 if-else 分支决策）、
NarrativePlan 禁文本字段（层边界）、跨模态 shot 插入。
"""

from __future__ import annotations

import ast
import inspect

from home_perception.visualizer.video.narrative.compiler import (
    NarrativePlan,
    instantiate_narrative_template,
)
from home_perception.visualizer.video.narrative.templates import template_for_evidence

from .conftest import make_evidence


def _function_ast(func) -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(func))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func.__name__:
            return node
    raise AssertionError("未找到函数定义")


def test_narrative_no_branch_decision_logic():
    """§8 验收 7：instantiate_narrative_template 不含 if/elif/else/match 分支决策。"""
    fn = _function_ast(instantiate_narrative_template)
    offenders = [
        type(n).__name__
        for n in ast.walk(fn)
        if isinstance(n, (ast.If, ast.IfExp, ast.Match))
    ]
    assert offenders == [], f"编译器含分支决策语句：{offenders}"


def test_narrative_canonical_five_shot():
    evidence = make_evidence()
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)

    assert isinstance(plan, NarrativePlan)
    # canonical 5-shot 弧线
    assert [s["name"] for s in template.shots] == [
        "context", "detection", "reasoning", "decision", "closure",
    ]
    # reasoning_chain 每步 ref 均能在 graph 解析
    node_ids = {n["id"] for n in evidence["graph"]["nodes"]}
    for step in plan.reasoning_chain:
        assert step.ref in node_ids
    # intent 来自模板枚举
    assert plan.intent == "explain_risk_decision"
    assert plan.audience_question


def test_narrative_resolves_real_node_ids():
    evidence = make_evidence()
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    refs = {s.ref for s in plan.reasoning_chain}
    # 至少引用到 Event / Decision / Action 真实节点
    assert "event-0" in refs
    assert "decision-0" in refs
    assert "action-0" in refs


def test_narrative_plan_forbids_text_fields():
    """§3 机械锁定：NarrativePlan 不得含 text/sentence/narration 自然语言字段。"""
    import pydantic

    try:
        NarrativePlan(
            intent="explain_risk_decision",
            reasoning_chain=[],
            audience_question="q",
            text="非法文本字段",  # 应被 extra="forbid" 拒绝
        )
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("NarrativePlan 接受了 text 字段，违反 §3 层边界")


def test_narrative_cross_modal_inserted():
    """含 Link 节点时，固定插入 cross_modal shot（结构性装配，非叙事分支）。"""
    evidence = make_evidence()
    evidence["graph"]["nodes"] = (
        *evidence["graph"]["nodes"],
        {"id": "link-0", "type": "Link", "label": "cross_modal", "ref": "l0", "provenance_kind": "SIMULATED"},
    )
    evidence["graph"]["edges"] = (
        *evidence["graph"]["edges"],
        {"source": "event-0", "target": "link-0", "type": "supports", "ref": "r5"},
    )
    template = template_for_evidence(evidence)
    assert any(s["name"] == "cross_modal" for s in template.shots)
    plan = instantiate_narrative_template(evidence, template)
    cross_refs = {s.ref for s in plan.reasoning_chain if s.step_kind == "cross_modal"}
    assert "link-0" in cross_refs

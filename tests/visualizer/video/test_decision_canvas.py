"""ADR-0035 D3 · Decision Canvas（决策解释链）语义层测试。

覆盖 B+C 决策的语义层规格：
- ``build_decision_steps`` 产出固定 7 步（Observation→Risk→Policy→Candidate→Selected
  →Execution→Closure），且每步 highlight/fade 集合正确；
- 正常场景高亮选中候选；benign（无推荐动作）场景不编造 ``dc:cand:无``，改以「无」承接；
- 前缀标签（真实 loader 产物）与无前缀标签（测试夹具）两套约定都能被解析（双约定兼容）；
- ``assert_decision_canvas`` 对真实 artifact 通过，且对篡改证据 fail-closed；
- 画布节点 id 闭集 = ``CANONICAL_CHAIN``。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.video.compiler import _decision_step_index
from home_perception.visualizer.video.evidence.adapter import load_scenario_evidence
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.scene.designer import (
    _build_decision_canvas,
    design_visual_scene,
)
from home_perception.visualizer.video.storyboard.decision_canvas import (
    CANONICAL_CHAIN,
    COMMAND_CANDIDATES,
    assert_decision_canvas,
    build_decision_steps,
    canvas_node_spec,
)
from home_perception.visualizer.video.storyboard.generator import generate_storyboard

from .conftest import artifact_dir, make_evidence

EXPECTED_STAGES = [
    "observation", "risk", "policy", "candidate",
    "selected", "execution", "closure",
]


def test_build_decision_steps_seven_ordered_stages():
    """决策解释链固定 7 步且顺序恒定（不随证据值漂移）。"""
    ev = make_evidence()
    steps = build_decision_steps(ev)
    assert [s.stage for s in steps] == EXPECTED_STAGES
    # 每步 highlight 必须是单节点（确定性逐步揭示）
    for st in steps:
        assert isinstance(st.highlight, list) and len(st.highlight) >= 1


def test_candidate_step_highlights_selected_action():
    """正常场景：候选步高亮选中动作（dc:cand:<selected>），淡出其余候选。"""
    ev = make_evidence(recommended_actions=("NOTIFY_FAMILY",), command_types=("LOG_ONLY",))
    steps = build_decision_steps(ev)
    cand = steps[3]
    assert cand.stage == "candidate"
    assert cand.highlight == ["dc:cand:NOTIFY_FAMILY"]
    # 其余候选动作须被淡出（不能凭空出现 dc:cand:无）
    assert "dc:cand:MONITOR" in cand.fade
    assert "dc:cand:ESCALATE_COMMUNITY" in cand.fade
    assert all(not n.startswith("dc:cand:无") for n in cand.highlight + cand.fade)


def test_execution_caption_exposes_recommended_vs_actual_divergence():
    """推荐动作 ≠ 实际指令 时显式解释（增强可信度，不掩盖差异）。"""
    ev = make_evidence(recommended_actions=("NOTIFY_FAMILY",), command_types=("LOG_ONLY",))
    steps = build_decision_steps(ev)
    exec_cap = steps[5].caption
    assert "≠" in exec_cap
    assert "NOTIFY_FAMILY" in exec_cap and "LOG_ONLY" in exec_cap


def test_benign_no_recommended_action_does_not_invent_candidate():
    """benign（recommended_actions 为空）不编造 dc:cand:无，候选步以选中节点承接『无』。"""
    ev = make_evidence(
        event_types=(),
        recommended_actions=(),
        command_types=(),
        decision_evidence=[],
    )
    steps = build_decision_steps(ev)
    assert [s.stage for s in steps] == EXPECTED_STAGES
    cand = steps[3]
    assert cand.highlight == ["dc:selected"]
    # 三个候选一并淡出（不编造 dc:cand:无）
    for a in COMMAND_CANDIDATES:
        assert f"dc:cand:{a}" in cand.fade
    assert not any(n.startswith("dc:cand:无") for n in cand.highlight + cand.fade)
    # 执行步：两者皆为『无』，不应套用 『≠』 框架
    assert "≠" not in steps[5].caption


def test_dual_label_convention_prefixed_loader_output():
    """真实 loader 输出的**前缀标签**能被正确解析（观测/风险/推荐/指令）。"""
    ev = {
        "scenario_id": "x",
        "event_types": ("abnormal_dwell",),
        "recommended_actions": ("NOTIFY_FAMILY",),
        "command_types": ("LOG_ONLY",),
        "risk_levels": ("LOW",),
        "trace_outcome_kinds": ("WARN",),
        "decision_evidence": (
            {"kind": "evidence", "label": "Observation · 检测证据（事件类型）", "value": "abnormal_dwell", "ref": "r1"},
            {"kind": "reasoning", "label": "Reasoning · 风险级别", "value": "LOW", "ref": "r2"},
            {"kind": "reasoning", "label": "Reasoning · 决策结果（trace outcome）", "value": "WARN", "ref": "r3"},
            {"kind": "outcome", "label": "Outcome · 推荐动作", "value": "NOTIFY_FAMILY", "ref": "r4"},
            {"kind": "outcome", "label": "Outcome · 已执行命令", "value": "LOG_ONLY", "ref": "r5"},
        ),
    }
    steps = build_decision_steps(ev)
    assert "abnormal_dwell" in steps[0].caption
    assert "LOW" in steps[1].caption
    assert "NOTIFY_FAMILY" in steps[3].caption


def test_dual_label_convention_unprefixed_fixture():
    """测试夹具的**无前缀标签**同样被解析（双约定兼容，避免漂移）。"""
    ev = make_evidence()  # conftest 用无前缀标签
    steps = build_decision_steps(ev)
    # make_evidence 默认观测=abnormal_dwell，风险=LOW
    assert "abnormal_dwell" in steps[0].caption
    assert "LOW" in steps[1].caption


def test_canvas_node_ids_equal_canonical_chain():
    """决策画布节点 id 闭集必须等于 CANONICAL_CHAIN（受控常量，不随输入膨胀）。"""
    ev = make_evidence()
    shot = type("Shot", (), {"decision_steps": build_decision_steps(ev)})()
    from home_perception.visualizer.video.scene.designer import _build_decision_canvas

    nodes = _build_decision_canvas(shot, ev)
    ids = [n.id for n in nodes]
    assert ids == list(CANONICAL_CHAIN)
    assert all(c in COMMAND_CANDIDATES for c in ("MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY"))


def test_assert_decision_canvas_passes_on_real_artifact():
    """对真实 ADR-0034 artifact 投影，assert_decision_canvas 必须通过（端到端锚定）。"""
    evidence = load_scenario_evidence(artifact_dir(), "sw_adr0034_elderly_dwell")
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    sb = generate_storyboard(plan, evidence, template)
    scenes = design_visual_scene(sb, evidence)
    canvas = scenes["decision"].decision_canvas
    # 必须不抛异常；节点 id 闭集且 label 与证据锚定一致
    assert_decision_canvas(evidence, canvas)


def test_assert_decision_canvas_failclosed_on_tampered_evidence():
    """篡改证据（推荐动作）后，画布锚定必 fail-closed 报错（防止漂移）。"""
    evidence = load_scenario_evidence(artifact_dir(), "sw_adr0034_elderly_dwell")
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    sb = generate_storyboard(plan, evidence, template)
    scenes = design_visual_scene(sb, evidence)
    canvas = scenes["decision"].decision_canvas
    # 原始证据通过
    assert_decision_canvas(evidence, canvas)
    # 篡改 recommended_actions（原 NOTIFY_FAMILY → MONITOR），画布未重建 → 锚定不一致
    tampered = dict(evidence)
    tampered["recommended_actions"] = ("MONITOR",)
    with pytest.raises(AssertionError):
        assert_decision_canvas(tampered, canvas)


def test_benign_real_artifact_canvas_consistent():
    """benign 真实 artifact（全空证据）的画布也通过锚定校验。"""
    evidence = load_scenario_evidence(artifact_dir(), "sw_adr0034_benign")
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    sb = generate_storyboard(plan, evidence, template)
    scenes = design_visual_scene(sb, evidence)
    canvas = scenes["decision"].decision_canvas
    assert_decision_canvas(evidence, canvas)
    # benign 仍产出 7 步、9 节点
    dec_shot = next(s for s in sb.shots if s.name == "decision")
    assert len(dec_shot.decision_steps) == 7
    assert len(canvas) == 9


def test_gap1_decision_step_index_deterministic():
    """缺口 1：决策幕逐帧→步骤映射的确定性序列必须被锁定（fps=1, duration_s=9, steps=7）。

    n_frames = round(9*1) = 9，lvl = max(1, 8) = 8，step_idx = min(6, i*7//8)。
    """
    n_frames = 9
    seq = [_decision_step_index(i, n_frames, 7) for i in range(n_frames)]
    assert seq == [0, 0, 1, 2, 3, 4, 5, 6, 6]
    # 单调性 + 末帧必命中末步
    assert seq == sorted(seq)
    assert seq[-1] == 6


def test_gap2_real_scenarioevidence_roundtrip():
    """缺口 2：build_decision_steps + assert_decision_canvas 对真实 ScenarioEvidence 形态（TypedDict）生效。

    落盘前一致性校验接收的是 designer 产出的 pydantic DecisionCanvasNode 列表，而
    build_decision_steps 接收的是 loader 投影的 ScenarioEvidence（TypedDict，运行期即 dict）。
    此前所有夹具都走简化 dict；本测试用贴合 loader 实况的字段形态（元组 + 嵌套 DecisionEvidence）
    跑完整「语义层规格 → 表达层节点 → 锚定校验」闭环，确保 canvas_node_spec 的
    recommended_actions/command_types 取值在生产数据形态下不 AttributeError（review #2）。
    """
    evidence = make_evidence(
        scenario_id="sw_adr0034_elderly_dwell",
        event_types=("abnormal_dwell",),
        recommended_actions=("NOTIFY_FAMILY",),
        command_types=("LOG_ONLY",),
        decision_evidence=[
            {"kind": "evidence", "label": "检测证据（事件类型）", "value": "abnormal_dwell", "ref": "d1"},
            {"kind": "reasoning", "label": "风险级别", "value": "LOW", "ref": "d2"},
            {"kind": "reasoning", "label": "决策结果（trace outcome）", "value": "WARN", "ref": "d3"},
        ],
    )
    steps = build_decision_steps(evidence)
    assert [s.stage for s in steps] == EXPECTED_STAGES
    # 语义层规格 → 表达层 DecisionCanvasNode（designer 真实路径）
    shot = type("Shot", (), {"decision_steps": steps})()
    nodes = _build_decision_canvas(shot, evidence)
    assert [n.id for n in nodes] == list(CANONICAL_CHAIN)
    # 表达层节点 → 锚定校验（生产落盘前复校）
    assert_decision_canvas(evidence, nodes)
    # 规格锚定：选中节点 label 必须含真实推荐动作
    selected = next(n for n in nodes if n.id == "dc:selected")
    assert selected.label == "选中\nNOTIFY_FAMILY"
    # canvas_node_spec 在 pydantic/真实数据形态下不抛 AttributeError
    assert canvas_node_spec(evidence, "dc:execution")["label"] == "执行\nLOG_ONLY"


def test_gap3_candidate_step_keeps_non_candidates_visible():
    """缺口 3：候选步只淡出其余候选 + 尚未抵达的后续节点，决策上下文保持中性可见（review #5）。

    候选步（正常场景）只淡出：① 其余两个候选；② 尚未抵达的 execution/closure（渐进揭示，
    在各自步骤才点亮，避免 visible→faded→visible 抖动）。已揭示的决策上下文
    observation/risk/policy/selected 保持中性可见——画布不再近乎全黑。
    """
    ev = make_evidence(recommended_actions=("NOTIFY_FAMILY",), command_types=("LOG_ONLY",))
    steps = build_decision_steps(ev)
    cand = steps[3]
    # 决策上下文（已揭示）不得被淡出
    context_visible = ["dc:observation", "dc:risk", "dc:policy", "dc:selected"]
    for nid in context_visible:
        assert nid not in cand.fade, f"决策上下文节点 {nid} 不应被淡出"
    # 选中候选高亮、其余两个候选淡出
    assert cand.highlight == ["dc:cand:NOTIFY_FAMILY"]
    assert "dc:cand:MONITOR" in cand.fade
    assert "dc:cand:ESCALATE_COMMUNITY" in cand.fade
    # execution/closure 为渐进揭示的后续节点（候选步尚未抵达），按设计淡出
    assert "dc:execution" in cand.fade
    assert "dc:closure" in cand.fade

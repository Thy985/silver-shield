"""ADR-0035 D3 · VisualSceneDesigner 测试（阶段 5 · 表达层）。"""

from __future__ import annotations

import pydantic

from home_perception.visualizer.video.compiler import _assert_consistency
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.scene.designer import design_visual_scene
from home_perception.visualizer.video.scene.schema import VisualElement, VisualSceneGraph
from home_perception.visualizer.video.spec import CaseVideoSpec
from home_perception.visualizer.video.storyboard.generator import generate_storyboard
from home_perception.visualizer.video.storyboard.schema import Storyboard

from .conftest import make_evidence


def _storyboard(evidence):
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    return generate_storyboard(plan, evidence, template)


def test_scene_refs_subset_of_storyboard():
    """§8 验收 9 Scene consistency：VisualSceneGraph.ref ⊆ Storyboard.evidence_refs。"""
    evidence = make_evidence()
    sb = _storyboard(evidence)
    scenes = design_visual_scene(sb, evidence)
    for shot in sb.shots:
        allowed = set(shot.evidence_refs)
        for el in scenes[shot.name].layout:
            assert el.ref in allowed


def test_scene_arrows_only_when_both_endpoints_present():
    """箭头仅当边的两端都 ∈ 本 shot 的 evidence_refs 才出现（含 closure 把 action+episode 同镜）。"""
    evidence = make_evidence()
    sb = _storyboard(evidence)
    scenes = design_visual_scene(sb, evidence)
    # 确定性复算「期望箭头数」：遍历每条边，仅当两端都 ∈ 同一 shot 的 evidence_refs 才计数。
    # 默认模板中 closure 镜头同时含 action-0 + episodes，且二者存在 stored_as 边 → 期望 1 支箭头。
    expected = 0
    for shot in sb.shots:
        ref_set = set(shot.evidence_refs)
        for e in evidence["graph"]["edges"]:
            if e["source"] in ref_set and e["target"] in ref_set:
                expected += 1
    total_arrows = sum(len(s.arrows) for s in scenes.values())
    assert total_arrows == expected
    # 反向不变量：任何真实出现的箭头，其两端必 ∈ 所属 shot 的 evidence_refs
    for shot in sb.shots:
        ref_set = set(shot.evidence_refs)
        for arrow in scenes[shot.name].arrows:
            assert arrow["from"] in ref_set and arrow["to"] in ref_set

    # 构造单 shot 含两端点相连的自定义场景 → 应出现箭头
    from home_perception.visualizer.video.storyboard.schema import ShotSpec

    custom_evidence = make_evidence(
        nodes=[
            {"id": "A", "type": "Event", "label": "abnormal_dwell", "ref": "a", "provenance_kind": "SIMULATED"},
            {"id": "B", "type": "Decision", "label": "WARN", "ref": "b", "provenance_kind": "SIMULATED"},
        ],
        edges=[{"source": "A", "target": "B", "type": "caused_by", "ref": "e1"}],
    )
    custom_sb = Storyboard(
        demo_id="d", title_zh="t", scenario_ref="s",
        shots=[ShotSpec(name="reasoning", kind="reasoning", duration_s=1.0,
                       purpose="p", evidence_refs=["A", "B"], narration=["x"])],
        version=1,
    )
    scenes2 = design_visual_scene(custom_sb, custom_evidence)
    assert len(scenes2["reasoning"].arrows) == 1
    arrow = scenes2["reasoning"].arrows[0]
    assert arrow["from"] == "A" and arrow["to"] == "B"
    # 两端都 ∈ evidence_refs
    assert arrow["from"] in custom_sb.shots[0].evidence_refs
    assert arrow["to"] in custom_sb.shots[0].evidence_refs


def test_scene_layer_boundary_forbids_semantic_fields():
    """§2.4.1 / §8 验收 3：VisualElement/VisualSceneGraph（表达层）不得含语义字段。"""
    for field in ("why", "purpose", "audience_need", "explanation_order"):
        try:
            VisualElement(ref="r", region="left", glyph="detection_box", **{field: "x"})
        except pydantic.ValidationError:
            pass
        else:
            raise AssertionError(f"VisualElement 接受了语义字段 {field!r}，违反 §2.4.1")
    try:
        VisualSceneGraph(shot="s", layout=[], **{field: "x"})
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError(f"VisualSceneGraph 接受了语义字段 {field!r}，违反 §2.4.1")


def test_scene_failclosed_on_orphan_ref(tmp_path):
    """§8 验收 9：VisualSceneGraph 含超出语义层 evidence_refs 的 ref → 一致性断言失败。

    传入**真实 spec**（而非 None）：靠「后续断言尚未用到 spec」来通过，是隐式依赖
    断言顺序的脆弱写法——一旦 Duration/Frame-provenance 段前移就会变成 AttributeError
    而非预期的 AssertionError。同时校验错误信息确实来自 Scene consistency 段。
    """
    evidence = make_evidence()
    sb = _storyboard(evidence)
    scenes = design_visual_scene(sb, evidence)
    # 手动植入一个图外 ref
    scenes["context"].layout.append(VisualElement(ref="ghost", region="left", glyph="timeline"))
    spec = CaseVideoSpec(
        scenario_id=evidence["scenario_id"],
        artifact_dir=tmp_path / "artifacts",
        output_dir=tmp_path / "out",
        resolution=(320, 180),
    )
    try:
        _assert_consistency(sb, scenes, evidence, frames=[], spec=spec, duration_s=1.0)
    except AssertionError as exc:
        assert "Scene consistency" in str(exc), f"应由 Scene consistency 段拒绝，实际：{exc}"
        assert "ghost" in str(exc)
    else:
        raise AssertionError("Scene consistency 未拒绝图外 ref")


def test_scene_failclosed_on_missing_node():
    """节点缺失不得静默回退成 "Scenario"：证据图与分镜不一致必须 fail-closed。"""
    evidence = make_evidence()
    sb = _storyboard(evidence)
    # 从图中抹掉 decision-0，但分镜仍引用它 → designer 必须报错而非画一张时间轴卡片
    evidence["graph"]["nodes"] = tuple(
        n for n in evidence["graph"]["nodes"] if n["id"] != "decision-0"
    )
    try:
        design_visual_scene(sb, evidence)
    except KeyError as exc:
        assert "decision-0" in str(exc)
    else:
        raise AssertionError("节点缺失未 fail-closed（静默回退会画出误导性卡片）")

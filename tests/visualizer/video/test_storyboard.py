"""ADR-0035 D3 · StoryboardGenerator 测试（阶段 4 · 语义层）。"""

from __future__ import annotations

import pydantic

from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.storyboard.generator import (
    generate_storyboard,
    storyboard_from_yaml,
    storyboard_to_yaml,
)
from home_perception.visualizer.video.storyboard.schema import ShotSpec, Storyboard

from .conftest import make_evidence


def _plan_and_template(evidence):
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    return plan, template


def test_storyboard_roundtrip():
    evidence = make_evidence()
    plan, template = _plan_and_template(evidence)
    sb = generate_storyboard(plan, evidence, template)
    text = storyboard_to_yaml(sb)
    restored = storyboard_from_yaml(text)
    assert restored.model_dump() == sb.model_dump()


def test_storyboard_layer_boundary_forbids_spatial_fields():
    """§2.4.1 / §8 验收 3：Storyboard/ShotSpec（语义层）不得含空间字段。"""
    for field in ("x", "y", "color", "layout", "font", "shape"):
        try:
            ShotSpec(name="x", kind="environment", duration_s=1.0, **{field: 1})
        except pydantic.ValidationError:
            pass
        else:
            raise AssertionError(f"ShotSpec 接受了空间字段 {field!r}，违反 §2.4.1")
    try:
        Storyboard(demo_id="d", title_zh="t", scenario_ref="s", shots=[], layout=1)
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("Storyboard 接受了 layout 字段，违反 §2.4.1")


def test_storyboard_audience_dimension():
    evidence = make_evidence()
    plan, template = _plan_and_template(evidence)
    sb_general = generate_storyboard(plan, evidence, template, audience="general")
    sb_judges = generate_storyboard(plan, evidence, template, audience="judges")
    assert sb_general.audience == "general"
    assert sb_judges.audience == "judges"
    # 同图不同受众 → 不同 Storyboard，但 evidence_refs 始终来自同一图
    for a, b in zip(sb_general.shots, sb_judges.shots):
        assert a.evidence_refs == b.evidence_refs


def test_storyboard_failclosed_on_fake_ref():
    """§2.3 解析纪律：覆盖的 evidence_refs 必须能在 graph 解析，否则 fail-closed。"""
    evidence = make_evidence()
    plan, template = _plan_and_template(evidence)
    override = {"shots": [{"name": "detection", "evidence_refs": ["does_not_exist"]}]}
    try:
        generate_storyboard(plan, evidence, template, override=override)
    except ValueError:
        pass
    else:
        raise AssertionError("伪造 ref 未被拒，违反 D3-12 证据所有权边界")


def test_storyboard_every_shot_has_purpose_and_refs():
    """§8 验收 2：每个 shot 有 purpose + evidence_refs（无裸 JSON 字段镜头）。"""
    evidence = make_evidence()
    plan, template = _plan_and_template(evidence)
    sb = generate_storyboard(plan, evidence, template)
    node_ids = {n["id"] for n in evidence["graph"]["nodes"]}
    for shot in sb.shots:
        assert shot.purpose
        for ref in shot.evidence_refs:
            assert ref in node_ids

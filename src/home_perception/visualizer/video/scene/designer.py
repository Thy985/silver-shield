"""ADR-0035 D3 · VisualSceneDesigner（阶段 5 · 表达层 / 空间维）。

``design_visual_scene``：Storyboard（语义层）+ evidence → ``{shot_name: VisualSceneGraph}``。
- 把每个 shot 的 ``evidence_refs`` 翻译成空间排布（region / glyph），由**节点类型**确定性派生；
- 箭头（arrows）由 EvidenceGraph 边映射而来（**非发明**）：仅当边的两端都 ∈ 本 shot 的
  evidence_refs 才出现；
- 作者可经 ``visual_override`` 覆盖单个 ref 的 region/glyph（仅调版面，不得引入图外 ref）。

层边界契约（§2.4.1）：本模块只产表达层（空间），**不**产 why/purpose/audience_need。

见设计文档 §2.4（VisualScene）、§3（VisualSceneGraph / VisualElement）、§9 D3-11。
"""

from __future__ import annotations

from home_perception.visualizer.video.scene.schema import VisualElement, VisualSceneGraph
from home_perception.visualizer.video.storyboard.schema import Storyboard

# 节点类型 → 默认版面区域（reasoning shot 默认左-中-右三栏 + 全屏上下文）。
REGION_BY_NODE_TYPE: dict[str, str] = {
    "Scenario": "full",
    "Frame": "left",
    "Detection": "left",
    "Event": "left",
    "Decision": "center",
    "Link": "center",
    "Action": "right",
    "Episode": "right",
}

# 节点类型 → 默认视觉字形。
GLYPH_BY_NODE_TYPE: dict[str, str] = {
    "Scenario": "timeline",
    "Frame": "detection_box",
    "Detection": "detection_box",
    "Event": "detection_box",
    "Decision": "warn_badge",
    "Link": "timeline",
    "Action": "message_icon",
    "Episode": "message_icon",
}


def design_visual_scene(
    storyboard: Storyboard,
    evidence: dict,
    visual_override: dict | None = None,
) -> dict[str, VisualSceneGraph]:
    """产出每 shot 的 VisualSceneGraph（表达层 · 空间维 · 确定性）。

    ``visual_override`` 结构：``{shot_name: [{"ref", "region"?, "glyph"?}, ...]}``。
    每个 ``VisualElement.ref`` ⊆ 本 shot 的 ``Storyboard.evidence_refs``（§8 验收 9 Scene consistency）。
    """
    graph = evidence["graph"]
    node_by_id = {n["id"]: n for n in graph["nodes"]}
    edge_pairs = [(e["source"], e["target"]) for e in graph["edges"]]

    override_all = visual_override or {}
    result: dict[str, VisualSceneGraph] = {}
    for shot in storyboard.shots:
        refs = shot.evidence_refs
        ref_set = set(refs)
        shot_ov = {ov["ref"]: ov for ov in (override_all.get(shot.name, []) or [])}
        layout: list[VisualElement] = []
        for ref in refs:
            node = node_by_id.get(ref)
            ntype = (node or {}).get("type") or "Scenario"
            region = shot_ov.get(ref, {}).get("region", REGION_BY_NODE_TYPE.get(ntype, "center"))
            glyph = shot_ov.get(ref, {}).get("glyph", GLYPH_BY_NODE_TYPE.get(ntype, "timeline"))
            layout.append(VisualElement(ref=ref, region=region, glyph=glyph))
        arrows = [
            {"from": s, "to": t, "style": "causal_red"}
            for s, t in edge_pairs
            if s in ref_set and t in ref_set
        ]
        result[shot.name] = VisualSceneGraph(shot=shot.name, layout=layout, arrows=arrows)
    return result


__all__ = ["design_visual_scene"]

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
    _assert_override_shots_known(storyboard, override_all)
    result: dict[str, VisualSceneGraph] = {}
    for shot in storyboard.shots:
        refs = shot.evidence_refs
        ref_set = set(refs)
        shot_ov = _parse_shot_override(shot.name, override_all, ref_set)
        layout: list[VisualElement] = []
        for ref in refs:
            node = node_by_id.get(ref)
            if node is None:
                # fail-closed：ref 必来自 EvidenceGraph（上游 generator 已断言）。
                # 静默回退成 "Scenario" 会把「图/分镜不一致」画成一张看似正常的时间轴卡片。
                raise KeyError(
                    f"VisualSceneDesigner: shot={shot.name!r} 的 ref={ref!r} 不在 EvidenceGraph 节点中；"
                    "证据图与分镜已不一致（违反 D3-12 证据所有权边界）"
                )
            ntype = node.get("type")
            if not ntype:
                raise KeyError(
                    f"VisualSceneDesigner: 节点 {ref!r} 缺少 'type' 字段，无法确定性派生版面"
                )
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


def _assert_override_shots_known(storyboard: Storyboard, override_all: dict) -> None:
    """作者 ``visual_override`` 的 shot 名必须真实存在（fail-closed）。

    拼错 shot 名此前会被静默忽略——精修覆盖悄悄失效，却仍能产出一版默认片子。
    """
    known = {shot.name for shot in storyboard.shots}
    unknown = sorted(set(override_all) - known)
    if unknown:
        raise ValueError(
            f"visual_override 引用了不存在的 shot：{unknown}；可用 shot：{sorted(known)}"
        )


def _parse_shot_override(shot_name: str, override_all: dict, ref_set: set[str]) -> dict[str, dict]:
    """解析单 shot 的版面覆盖（fail-closed：条目非映射 / 缺 ``ref`` 键 / 图外 ref 均报错）。

    ``visual_override`` 契约（见 scenarios/*.yaml 注释）：仅调版面，**不得**引入本 shot
    ``evidence_refs`` 之外的 ref。此前缺 ``ref`` 键会抛裸 ``KeyError``（无上下文），
    而图外 ref 则被静默忽略——两者都改为带定位信息的显式错误。
    """
    entries = override_all.get(shot_name) or []
    if not isinstance(entries, list):
        raise TypeError(
            f"visual_override[{shot_name!r}] 须为条目列表，实际为 {type(entries).__name__}"
        )
    parsed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "ref" not in entry:
            raise ValueError(
                f"visual_override[{shot_name!r}] 条目缺少必需键 'ref'：{entry!r}"
            )
        ref = entry["ref"]
        if ref not in ref_set:
            raise ValueError(
                f"visual_override[{shot_name!r}] 引入了本 shot evidence_refs 之外的 ref={ref!r}；"
                f"作者覆盖仅可调版面（可用 ref：{sorted(ref_set)}）"
            )
        parsed[ref] = entry
    return parsed


__all__ = ["design_visual_scene"]

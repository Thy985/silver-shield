"""P1-B · Story Replay 分幕派生（Artifact-only · Owner 2026-08-17 拍板）。

从 ``EvidenceProjection`` 派生叙事分幕（StoryChapter）——**纯展示编排，事实驱动**：
- 只绑定 EvidenceProjection 里**已存在**的事实节点 ref（timeline / decision_evidence /
  audio_evidence / memory_episodes），绝不凭空编造"这一幕代表风险升级"（VM-1）；
- 固定叙事序 Context → Incident → Risk → Decision → Closure，但**某幕无对应事实即省略**
  （AC-12：无事实不编造空幕）；
- ``display_copy`` 只陈述可观测事实（"检测到 N 个感知事件"），不做风险/语义判定。

与叙事带（``_derive_narrative_kind`` 单结果 hero）分工：叙事带=结果类型一行总览，
本模块=沿证据时间轴的分幕结构（章节 + 聚焦 ref）。二者互补不重复。

不 import ``silver_demo`` / 生产 runtime（VM-3，import 图死胡同叶子）。
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class StoryChapter(TypedDict):
    """叙事分幕（纯展示编排，ref 引用语义——非业务事实值，对齐 media_binding.ref）。

    - ``chapter_id``：稳定幕标识（context/incident/risk/decision/closure）；
    - ``label``：展示幕名（如「② 事件」）；
    - ``display_copy``：叙述文案（只陈述可观测事实，不判定风险/语义）；
    - ``start_idx`` / ``end_idx``：对应 timeline 节点索引区间（含两端）；
    - ``focus_refs``：绑定的事实节点 ref（timeline/decision/audio 节点 ref，驱动前端聚焦）。
    """

    chapter_id: str
    label: str
    display_copy: str
    start_idx: int
    end_idx: int
    focus_refs: NotRequired[tuple[str, ...]]


# 叙事幕固定顺序（事实驱动：无对应事实的幕省略，不编造）。
_STORY_CHAPTER_ORDER: tuple[str, ...] = (
    "context",
    "incident",
    "risk",
    "decision",
    "closure",
)

# 幕 id → 中文幕名（纯展示文案）。
_CHAPTER_LABELS: dict[str, str] = {
    "context": "① 开场",
    "incident": "② 事件",
    "risk": "③ 风险",
    "decision": "④ 决策",
    "closure": "⑤ 闭环",
}

# stage 名 → 叙事幕（Artifact timeline 是 stage 级；事实驱动映射）。
# - perception → incident（检测到事件）
# - decision → decision（决策）
# - notification → closure（通知/处置闭环）
# - memory → risk（历史记忆叠加 → 风险升级）
# - cross_modal / observability → 无对应叙事幕（工程验证阶段，不进产品叙事）
_STAGE_TO_CHAPTER: dict[str, str] = {
    "perception": "incident",
    "decision": "decision",
    "notification": "closure",
    "memory": "risk",
}


def _count_scalar(value: object) -> int:
    """安全取整数计数（非 int → 0，不抛；投影已强校验，此处防御）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def build_story_chapters(scenario: object) -> tuple[StoryChapter, ...]:
    """从单个场景 ``ScenarioEvidence`` 派生叙事分幕（事实驱动，省略空幕）。

    Args:
        scenario: 已投影的场景对象（dict 形态，含 timeline/decision_evidence/
            audio_evidence/memory_episodes/counts 等契约字段）。

    Returns:
        按叙事序排列的 StoryChapter 元组（无对应事实的幕已省略；空元组=无叙事）。
    """
    if not isinstance(scenario, dict):
        return ()
    timeline = scenario.get("timeline") or ()
    if not timeline:
        return ()

    # 按 stage 分组 timeline 节点（保持原顺序；索引区间用于前端 seek）。
    chapter_indexes: dict[str, list[int]] = {}
    for idx, node in enumerate(timeline):
        chapter_id = _STAGE_TO_CHAPTER.get(str(node.get("stage", "")))
        if chapter_id:
            chapter_indexes.setdefault(chapter_id, []).append(idx)

    # 各幕的事实锚点 ref（绑定已存在节点，绝不编造）。
    decision_evidence = scenario.get("decision_evidence") or ()
    audio_evidence = scenario.get("audio_evidence") or ()
    memory_episodes = scenario.get("memory_episodes") or ()
    counts = scenario.get("counts") or {}

    def _focus_refs(*sources: tuple[object, ...]) -> tuple[str, ...]:
        refs: list[str] = []
        for src in sources:
            for item in src:
                if isinstance(item, dict) and isinstance(item.get("ref"), str):
                    refs.append(item["ref"])
        return tuple(refs)

    chapters: list[StoryChapter] = []
    # context：无 stage 节点映射 → 仅当 timeline 首个节点非上述四 stage 时作为"开场"锚。
    first_stage = str(timeline[0].get("stage", "")) if timeline else ""
    if first_stage and first_stage not in _STAGE_TO_CHAPTER:
        chapters.append(
            StoryChapter(
                chapter_id="context",
                label=_CHAPTER_LABELS["context"],
                display_copy=f"场景 {scenario.get('scenario_id', '')} 开场（证据时间轴起点）。",
                start_idx=0,
                end_idx=0,
                focus_refs=tuple(
                    n["ref"] for n in timeline[:1] if isinstance(n.get("ref"), str)
                ),
            )
        )

    # incident：perception stage 节点 + audio evidence（真实感知）。
    if "incident" in chapter_indexes:
        idxs = chapter_indexes["incident"]
        chapters.append(
            StoryChapter(
                chapter_id="incident",
                label=_CHAPTER_LABELS["incident"],
                display_copy=(
                    f"系统检测到 {_count_scalar(counts.get('perception_events'))} 个感知事件"
                    + (f"，{len(audio_evidence)} 条音频证据" if audio_evidence else "")
                    + "。"
                ),
                start_idx=idxs[0],
                end_idx=idxs[-1],
                focus_refs=tuple(
                    timeline[i]["ref"] for i in idxs if isinstance(timeline[i].get("ref"), str)
                )
                + _focus_refs(audio_evidence),
            )
        )

    # risk：memory stage（历史叠加）+ memory_episodes。
    if "risk" in chapter_indexes:
        idxs = chapter_indexes["risk"]
        chapters.append(
            StoryChapter(
                chapter_id="risk",
                label=_CHAPTER_LABELS["risk"],
                display_copy=(
                    f"历史记忆叠加：{len(memory_episodes)} 条记忆片段参与重新评估。"
                ),
                start_idx=idxs[0],
                end_idx=idxs[-1],
                focus_refs=tuple(
                    timeline[i]["ref"] for i in idxs if isinstance(timeline[i].get("ref"), str)
                )
                + _focus_refs(memory_episodes),
            )
        )

    # decision：decision stage + decision_evidence（reasoning/outcome）。
    if "decision" in chapter_indexes:
        idxs = chapter_indexes["decision"]
        chapters.append(
            StoryChapter(
                chapter_id="decision",
                label=_CHAPTER_LABELS["decision"],
                display_copy=(
                    f"系统给出决策证据（{_count_scalar(counts.get('decision_traces'))} 条决策轨迹）。"
                ),
                start_idx=idxs[0],
                end_idx=idxs[-1],
                focus_refs=tuple(
                    timeline[i]["ref"] for i in idxs if isinstance(timeline[i].get("ref"), str)
                )
                + _focus_refs(decision_evidence),
            )
        )

    # closure：notification stage（通知/处置）+ intervention_dispatch（行动闭环）。
    if "closure" in chapter_indexes:
        idxs = chapter_indexes["closure"]
        dispatch = scenario.get("intervention_dispatch") or ()
        chapters.append(
            StoryChapter(
                chapter_id="closure",
                label=_CHAPTER_LABELS["closure"],
                display_copy=(
                    f"系统通知家属/社区（{_count_scalar(counts.get('commands'))} 条指令）。"
                ),
                start_idx=idxs[0],
                end_idx=idxs[-1],
                focus_refs=tuple(
                    timeline[i]["ref"] for i in idxs if isinstance(timeline[i].get("ref"), str)
                )
                + _focus_refs(dispatch),
            )
        )

    return tuple(chapters)


__all__ = ["StoryChapter", "build_story_chapters"]

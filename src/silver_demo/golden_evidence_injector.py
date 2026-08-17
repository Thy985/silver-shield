"""Golden Case EvidenceProjection 注入器（ADR-0036 补遗 · Phase 2）。

## 设计原则

- **不**修改 LiveAdapter（冻结契约）。
- **不**修改 schema Literal（``ProvenanceKind`` 闭集保持）。
- **不**修改 ``build_live_presentation`` 签名。
- **不**修改 ``EvidenceProjection`` schema。

唯一动作：在 ``build_live_presentation`` 返回后，对 ``projection`` 做 **post-process**：
- 追加 ``scenarios[0].timeline`` 节点（manifest 派生的 pre-event 节点）
- 追加 ``scenarios[0].graph.nodes``（manifest 派生的 memory episode 节点）
- 填充 ``scenarios[0].memory_episodes`` 字段（real-time Live 之前恒 ``()``）
- 同步追加 ``scenarios[0].refs``（schema: refs 在 scenario 层，不在 projection 顶层）

## VM-1 / VM-9 守护

- pre-event 节点 ``provenance_kind="SIMULATED"``（不动 schema Literal，SIMULATED 是闭集内的合法值）
- 渲染层靠节点来源（timeline vs audio_evidence vs memory_episodes）判别"预期 vs 实测"
- 不污染 ``audio_evidence``（这是 REAL_SENSOR 专属）
- 不污染 ``memory_episodes`` 已有逻辑（仅填充，**不**触发任何 runtime 路径）

## 4 case 共用同一个 ``inject_golden_evidence``

不写 case-specific 分支：所有 manifest 字段都走 ``golden_evidence_projection()`` 统一派生。
"""
from __future__ import annotations

from typing import Any

from .golden_evidence import golden_evidence_projection


def inject_golden_evidence(
    projection: dict[str, Any],
    case: str,
) -> dict[str, Any]:
    """对 EvidenceProjection 注入 golden manifest 派生的 pre-event 节点（post-process）。

    **不**修改 ``projection`` 内部结构，**不**修改 runtime 真实事件。
    只追加 manifest 派生的 pre-event 节点（provenance_kind=SIMULATED，渲染层用其他方式
    区分"预期 vs 实测"）。

    Args:
        projection: ``build_live_presentation`` 返回的 EvidenceProjection dict
        case: golden case 名（已与 ``projection.scenarios[0].scenario_id`` 一致）

    Returns:
        同一个 projection dict（in-place 修改，但返回引用便于链式调用）

    Side effects:
        - 追加 ``scenarios[0].timeline`` 节点
        - 追加 ``scenarios[0].graph.nodes`` 节点
        - 填充 ``scenarios[0].memory_episodes`` 字段
        - 同步追加 ``scenarios[0].refs``

    Failure modes:
        - projection 结构不合法 → 抛 ValueError（fail-closed）
        - golden_evidence_projection 抛错 → 透传（fail-closed）
    """
    scenarios = projection.get("scenarios")
    if not isinstance(scenarios, tuple) or not scenarios:
        raise ValueError(
            "EvidenceProjection 无 scenarios，无法注入 golden pre-event"
        )

    ev = golden_evidence_projection(case)
    scenario = scenarios[0]  # mutable_dict 类型，可 in-place 修改

    # 1. 追加 timeline 节点（pre-event）
    existing_timeline = scenario.get("timeline") or ()
    if ev["timeline_nodes"]:
        scenario["timeline"] = tuple(existing_timeline) + tuple(ev["timeline_nodes"])

    # 2. 追加 graph.nodes（memory episode 节点）
    if ev["memory_episode_nodes"]:
        graph = scenario.get("graph")
        if isinstance(graph, dict):
            existing_nodes = graph.get("nodes") or ()
            graph["nodes"] = tuple(existing_nodes) + tuple(ev["memory_episode_nodes"])

    # 3. 填充 memory_episodes（用于 ⑥ 跨日叙事）
    if ev["memory_episode_nodes"]:
        scenario["memory_episodes"] = tuple(ev["memory_episode_nodes"])

    # 4. 同步追加 refs（schema: refs 在 scenario 层）
    new_refs = (
        [n["ref"] for n in ev["timeline_nodes"]]
        + [n["ref"] for n in ev["memory_episode_nodes"]]
    )
    if new_refs:
        existing_refs = scenario.get("refs") or ()
        scenario["refs"] = tuple(existing_refs) + tuple(new_refs)

    return projection


def golden_session_metadata(case: str) -> dict[str, Any]:
    """从 manifest 提取展示层 metadata（用于前端 banner / description 增强）。

    Returns:
        dict with:
        - ``expected_decision_outcome``: ``manifest.expected.decision.outcome``（文案补全）
        - ``case``: case 名
    """
    ev = golden_evidence_projection(case)
    return {
        "case": case,
        "expected_decision_outcome": ev["expected_decision_outcome"],
    }


__all__ = [
    "golden_session_metadata",
    "inject_golden_evidence",
]

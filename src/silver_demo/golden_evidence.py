"""Golden Case 统一接入适配层（ADR-0036 补遗 · Phase 2）。

## Phase 2: M1-Evidence（manifest → EvidenceProjection 增强）

**核心原则**：

- **不**改 schema Literal（`ProvenanceKind = ["REAL_SENSOR", "SIMULATED", "FIXTURE"]`），
  避免契约变更。pre-event 节点用 ``SIMULATED`` 标注（广义仿真），但**独立字段**隔离，
  渲染层用字段来源（``golden_evidence`` / ``golden_memory_refs``）判别"预期 vs 实测"。
- **不**import home_perception.*（freeze boundary 约束：silver_demo 只能通过 gateway import
  visualizer.viewer）。本模块只产生 **dict 结构** 的 pre-event 节点，类型形状与
  ``TimelineNode`` / ``EvidenceGraphNode`` schema 字段一致（duck-typing），但不在 import
  graph 中显式 import TypedDict。
- **不**编造事实：所有 pre-event 节点字段都来自 manifest 已有字段，adapter 只翻译。
- **不**解析数组（episodes/acts/variants）：只用 manifest 顶层字段（acoustic_progression 等）。

## Pre-Event 节点类型

| 节点 | 数据源 manifest 字段 | 节点类型（type） | modality |
|------|------------------|------------|----------|
| 声学状态阶段 | ``audio.voice_stressed.acoustic_progression[].phase`` | ``"golden_audio_state"`` | ``"AUDIO"`` |
| 跨日记忆引用 | ``episodes[].memory_ref`` | ``"golden_memory_ref"`` | ``"MEMORY"`` |
| A/B variant | ``variants[].id`` | ``"golden_variant"`` | ``"OBSERVABILITY"`` |
| 跨模态 link（声明的） | ``variants[].cross_modal`` | ``"golden_cross_modal"`` | ``"CROSS_MODAL"`` |
| 期望 outcome | ``expected.decision.outcome`` | （不进 timeline，进 decision_evidence）|  |

## 4 case 共用同一个 ``golden_evidence_projection``

- 走同一规则：``manifest 字段 → 节点类型 → dict 节点``，不写 case-specific 分支
- 字段缺失 → 跳过该类型（不抛错，诚实的空节点）
- 字段异常 → fail-closed（抛 ValueError）
"""
from __future__ import annotations

from typing import Any

from .golden_adapter import _load_manifest


def _audio_state_nodes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 manifest 派生"声学状态阶段"时间轴节点（telephone_risk 的核心叙事）。

    数据源：``audio.{layer_name}.acoustic_progression[].{phase,time,f0_mean,energy}``
    仅用 manifest 已有字段，不生成。
    """
    nodes: list[dict[str, Any]] = []
    audio_cfg = manifest.get("audio") or {}
    for layer in audio_cfg.values():
        if not isinstance(layer, dict):
            continue
        progression = layer.get("acoustic_progression")
        if not isinstance(progression, list):
            continue
        for phase in progression:
            if not isinstance(phase, dict):
                continue
            phase_name = phase.get("phase", "")
            if not phase_name:
                continue
            nodes.append({
                "timestamp": str(phase.get("time", "")),
                "stage": "perception",
                "type": "golden_audio_state",
                "summary": f"声学状态 {phase_name}",
                "verdict": "INFO",
                "modality": "AUDIO",
                "provenance_kind": "SIMULATED",
                "ref": f"golden://{manifest.get('case', '?')}/audio/{phase_name}",
            })
    return nodes


def _memory_ref_nodes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 manifest 派生"跨日记忆引用"时间轴节点（repeated_visit 的核心叙事）。"""
    nodes: list[dict[str, Any]] = []
    episodes = manifest.get("episodes") or []
    case = manifest.get("case", "?")
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        mem_refs = ep.get("memory_ref") or []
        ep_id = ep.get("id", "")
        for mem_ref in mem_refs:
            nodes.append({
                "timestamp": str(ep.get("timestamp", "")),
                "stage": "memory",
                "type": "golden_memory_ref",
                "summary": f"引用历史 {mem_ref}（{ep_id}）",
                "verdict": "INFO",
                "modality": "MEMORY",
                "provenance_kind": "SIMULATED",
                "ref": f"golden://{case}/episodes/{mem_ref}",
            })
    return nodes


def _variant_nodes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 manifest 派生"A/B variant 切换"时间轴节点（telephone_risk case_a/b）。"""
    nodes: list[dict[str, Any]] = []
    variants = manifest.get("variants") or []
    case = manifest.get("case", "?")
    for v in variants:
        if not isinstance(v, dict):
            continue
        v_id = v.get("id", "")
        if not v_id:
            continue
        nodes.append({
            "timestamp": str(v.get("timestamp", "")),
            "stage": "observability",
            "type": "golden_variant",
            "summary": f"Variant {v_id}: {v.get('label', '')}",
            "verdict": "INFO",
            "modality": "OBSERVABILITY",
            "provenance_kind": "SIMULATED",
            "ref": f"golden://{case}/variants/{v_id}",
        })
    return nodes


def _cross_modal_nodes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 manifest 派生"跨模态 link"时间轴节点（telephone_risk case_b 的 SUPPORTS）。"""
    nodes: list[dict[str, Any]] = []
    variants = manifest.get("variants") or []
    case = manifest.get("case", "?")
    for v in variants:
        if not isinstance(v, dict):
            continue
        cm = v.get("cross_modal")
        if not isinstance(cm, dict):
            continue
        v_id = v.get("id", "")
        nodes.append({
            "timestamp": str(v.get("timestamp", "")),
            "stage": "cross_modal",
            "type": "golden_cross_modal",
            "summary": f"跨模态 {cm.get('type', '')}：{cm.get('vision_ref', '')} + {cm.get('audio_ref', '')}",
            "verdict": "INFO",
            "modality": "CROSS_MODAL",
            "provenance_kind": "SIMULATED",
            "ref": f"golden://{case}/variants/{v_id}/cross_modal",
        })
    return nodes


def golden_evidence_projection(case: str) -> dict[str, Any]:
    """从 manifest 派生 pre-event 证据节点（不解析 episodes/acts/variants 数组作为启动参数）。

    Returns:
        dict with:
        - ``timeline_nodes``: 追加到 LiveAdapter timeline 的节点
        - ``memory_episode_nodes``: 追加到 memory_episodes 的节点（用于 ⑥ 跨日叙事）
        - ``expected_decision_outcome``: manifest.expected.decision.outcome（用于文案补全）
        - ``case``: case 名

    节点结构 duck-type 为 ``TimelineNode`` / ``EvidenceGraphNode``（字段名一致），
    渲染层按 dict 消费（不 import TypedDict）。
    """
    manifest = _load_manifest(case)

    # Timeline 节点：4 类（acoustic / memory_ref / variant / cross_modal）
    timeline_nodes: list[dict[str, Any]] = []
    timeline_nodes.extend(_audio_state_nodes(manifest))
    timeline_nodes.extend(_memory_ref_nodes(manifest))
    timeline_nodes.extend(_variant_nodes(manifest))
    timeline_nodes.extend(_cross_modal_nodes(manifest))

    # Memory episode 节点：跨日引用（用于 memory_episodes 字段）
    memory_episode_nodes: list[dict[str, Any]] = []
    episodes = manifest.get("episodes") or []
    case_name = manifest.get("case", case)
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        for mem_ref in ep.get("memory_ref") or []:
            memory_episode_nodes.append({
                "id": f"golden-mem-{ep.get('id', '?')}-{mem_ref}",
                "type": "Memory",
                "label": f"历史 {mem_ref}（golden case 引用）",
                "ref": f"golden://{case_name}/episodes/{mem_ref}",
                "provenance_kind": "SIMULATED",
            })

    # Expected decision outcome
    expected = manifest.get("expected") or {}
    expected_decision = expected.get("decision") or {}
    expected_outcome = expected_decision.get("outcome")

    return {
        "case": case,
        "timeline_nodes": timeline_nodes,
        "memory_episode_nodes": memory_episode_nodes,
        "expected_decision_outcome": expected_outcome,
    }


__all__ = [
    "golden_evidence_projection",
]

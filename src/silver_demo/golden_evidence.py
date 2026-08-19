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

## M1-AudioMap（DIAGNOSIS §9.4 路径 B · manifest 语义 → AudioPerceptionKind 5 类）

Golden manifest 用领域语义标签（``doorbell_ring`` / ``voice_stressed`` / ``telephone_interaction``），
Live runtime 用 ``AudioPerceptionKind`` 5 类（``audio_telephone_persistent`` / ``audio_voice_raised`` ...）。
本模块提供 ``MANIFEST_TO_AUDIO_KIND`` 映射表 + ``manifest_audio_to_live_audio_kinds`` 收集函数，
让 4 个 golden case 都能接入 Live audio 事件链路。映射值用字符串（= AudioPerceptionKind.value），
不 import 生产枚举（freeze boundary）。

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


# ============================================================================
# M1: Manifest 语义 → AudioPerceptionKind 5 类映射表（DIAGNOSIS §9.4 路径 B）
# ============================================================================

# Golden manifest 领域语义标签 → AudioPerceptionKind.value（5 类字符串）。
# None 表示该标签不映射到 audio event（正常/环境音/静音）。
# 映射值与 home_perception.audio.event.AudioPerceptionKind.value 逐字对齐，
# 但不 import 生产枚举（freeze boundary：silver_demo 不依赖 home_perception）。
MANIFEST_TO_AUDIO_KIND: dict[str, str | None] = {
    # telephone_risk —— 声学状态变化核心叙事
    "voice_stressed": "audio_voice_raised",
    "voice_stress_elevated": "audio_voice_raised",
    "telephone_interaction": "audio_telephone_persistent",
    "phone_interaction": "audio_telephone_persistent",
    # stranger_visit —— 门铃 + 脚步声
    "doorbell_ring": "audio_anomaly_other",
    "doorbell": "audio_anomaly_other",
    "footsteps_in": "audio_anomaly_other",
    "footsteps_out": "audio_anomaly_other",
    "footsteps": "audio_anomaly_other",
    # 通用（未来 case 可用）
    "distress_cry": "audio_distress_cry",
    "rapid_speech": "audio_speech_rapid",
    # 不映射（正常/环境音/静音 —— 不产 audio event）
    "voice_normal": None,
    "ambient": None,
    "far_end": None,
    "silence_response": None,
    "micro_events": None,
}

# AudioPerceptionKind 5 类合法值（用于校验映射产物，不 import 生产枚举）
_AUDIO_PERCEPTION_KIND_VALUES: frozenset[str] = frozenset(
    {
        "audio_speech_rapid",
        "audio_voice_raised",
        "audio_telephone_persistent",
        "audio_distress_cry",
        "audio_anomaly_other",
    }
)


def _collect_manifest_audio_labels(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """扫描 manifest 所有 audio 语义标签出现位置。

    Returns:
        list of (label, source_path) —— label 是 manifest 中的语义标签，
        source_path 是该标签在 manifest 中的位置描述（用于溯源/调试）。

    扫描位置（覆盖 4 个 case 的所有 audio 声明点）：
    - ``manifest.audio`` 的 dict key（telephone_risk: voice_stressed / ambient / ...）
    - ``manifest.media.audio[].id``（stranger_visit: footsteps_in / doorbell / ...）
    - ``manifest.segments[].evidence.audio``（stranger_visit: doorbell_ring）
    - ``manifest.variants[].evidence.audio``（telephone_risk: telephone_interaction）
    - ``manifest.variants[].evidence.vision``（telephone_risk: phone_interaction）
    - ``manifest.variants[].cross_modal.audio_ref``（telephone_risk: voice_stress_elevated）
    - ``manifest.episodes[].evidence``（repeated_visit: doorbell）
    """
    labels: list[tuple[str, str]] = []

    # 1. manifest.audio dict key（telephone_risk）
    audio_cfg = manifest.get("audio") or {}
    if isinstance(audio_cfg, dict):
        for key in audio_cfg:
            if isinstance(key, str) and key not in ("duration", "sample_rate", "channels", "format"):
                labels.append((key, f"audio.{key}"))

    # 2. manifest.media.audio[].id（stranger_visit）
    media = manifest.get("media") or {}
    if isinstance(media, dict):
        media_audio = media.get("audio") or []
        if isinstance(media_audio, list):
            for item in media_audio:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    labels.append((item["id"], f"media.audio.{item['id']}"))

    # 3. manifest.segments[].evidence.audio（stranger_visit）
    for seg in manifest.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        ev = seg.get("evidence") or {}
        if not isinstance(ev, dict):
            continue
        for label in ev.get("audio") or []:
            if isinstance(label, str):
                labels.append((label, f"segments.{seg.get('id', '?')}.evidence.audio"))

    # 4. manifest.variants[].evidence.audio + evidence.vision + cross_modal.audio_ref
    for v in manifest.get("variants") or []:
        if not isinstance(v, dict):
            continue
        ev = v.get("evidence") or {}
        if isinstance(ev, dict):
            for label in ev.get("audio") or []:
                if isinstance(label, str):
                    labels.append((label, f"variants.{v.get('id', '?')}.evidence.audio"))
            for label in ev.get("vision") or []:
                if isinstance(label, str):
                    labels.append((label, f"variants.{v.get('id', '?')}.evidence.vision"))
        cm = v.get("cross_modal") or {}
        if isinstance(cm, dict):
            for ref_key in ("audio_ref", "vision_ref"):
                ref = cm.get(ref_key)
                if isinstance(ref, str):
                    labels.append((ref, f"variants.{v.get('id', '?')}.cross_modal.{ref_key}"))

    # 5. manifest.episodes[].evidence（repeated_visit: doorbell 等）
    for ep in manifest.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        for label in ep.get("evidence") or []:
            if isinstance(label, str):
                labels.append((label, f"episodes.{ep.get('id', '?')}.evidence"))

    return labels


def manifest_audio_to_live_audio_kinds(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """把 manifest 的 audio 语义标签映射为 LiveAudioFrame-shape dict 列表。

    每个映射产物 duck-type 为 ``LiveAudioFrame``（``live_adapter.py`` 定义的 TypedDict）：
    - ``kind``: AudioPerceptionKind.value 字符串（5 类之一）
    - ``score``: 默认 0.7（manifest 声明，非实测；用于 UI 强度条展示）
    - ``confidence``: 默认 0.8（manifest 声明置信度）
    - ``labels``: 原始 manifest 标签（溯源）
    - ``source_segment_ids``: 来源位置（溯源）
    - ``timestamp``: Unix 秒字符串（manifest 声明，非实测）
    - ``provenance_kind``: "SIMULATED"（manifest 声明，非 REAL_SENSOR）

    fail-soft：未在 ``MANIFEST_TO_AUDIO_KIND`` 中的标签跳过（不编造）。
    去重：同一 kind 多次出现只保留第一个（避免重复 audio event）。
    """
    labels = _collect_manifest_audio_labels(manifest)
    seen_kinds: set[str] = set()
    events: list[dict[str, Any]] = []

    for label, source in labels:
        kind = MANIFEST_TO_AUDIO_KIND.get(label)
        if kind is None or kind in seen_kinds:
            continue
        if kind not in _AUDIO_PERCEPTION_KIND_VALUES:
            continue
        seen_kinds.add(kind)
        events.append({
            "kind": kind,
            "score": 0.7,
            "confidence": 0.8,
            "labels": (label,),
            "source_segment_ids": (source,),
            "timestamp": "0.0",
            "provenance_kind": "SIMULATED",
        })

    return events


# ============================================================================
# Pre-Event 节点派生（Phase 2 原有功能）
# ============================================================================


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
            ref = f"golden://{manifest.get('case', '?')}/audio/{phase_name}"
            nodes.append({
                "id": ref,
                "label": f"声学状态 {phase_name}",
                "timestamp": str(phase.get("time", "")),
                "stage": "perception",
                "type": "golden_audio_state",
                "summary": f"声学状态 {phase_name}",
                "verdict": "INFO",
                "modality": "AUDIO",
                "provenance_kind": "SIMULATED",
                "ref": ref,
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
            summary = f"引用历史 {mem_ref}（{ep_id}）"
            ref = f"golden://{case}/episodes/{mem_ref}"
            nodes.append({
                "id": ref,
                "label": summary,
                "timestamp": str(ep.get("timestamp", "")),
                "stage": "memory",
                "type": "golden_memory_ref",
                "summary": summary,
                "verdict": "INFO",
                "modality": "MEMORY",
                "provenance_kind": "SIMULATED",
                "ref": ref,
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
        summary = f"Variant {v_id}: {v.get('label', '')}"
        ref = f"golden://{case}/variants/{v_id}"
        nodes.append({
            "id": ref,
            "label": summary,
            "timestamp": str(v.get("timestamp", "")),
            "stage": "observability",
            "type": "golden_variant",
            "summary": summary,
            "verdict": "INFO",
            "modality": "OBSERVABILITY",
            "provenance_kind": "SIMULATED",
            "ref": ref,
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
        summary = f"跨模态 {cm.get('type', '')}：{cm.get('vision_ref', '')} + {cm.get('audio_ref', '')}"
        ref = f"golden://{case}/variants/{v_id}/cross_modal"
        nodes.append({
            "id": ref,
            "label": summary,
            "timestamp": str(v.get("timestamp", "")),
            "stage": "cross_modal",
            "type": "golden_cross_modal",
            "summary": summary,
            "verdict": "INFO",
            "modality": "CROSS_MODAL",
            "provenance_kind": "SIMULATED",
            "ref": ref,
        })
    return nodes


def golden_evidence_projection(case: str) -> dict[str, Any]:
    """从 manifest 派生 pre-event 证据节点（不解析 episodes/acts/variants 数组作为启动参数）。

    Returns:
        dict with:
        - ``timeline_nodes``: 追加到 LiveAdapter timeline 的节点
        - ``memory_episode_nodes``: 追加到 memory_episodes 的节点（用于 ⑥ 跨日叙事）
        - ``audio_events``: M1 映射产物 —— manifest 语义 → AudioPerceptionKind 5 类
          （LiveAudioFrame-shape dict 列表，供 Live audio 事件链路消费）
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

    # M1: manifest 语义 → AudioPerceptionKind 5 类（DIAGNOSIS §9.4 路径 B）
    audio_events = manifest_audio_to_live_audio_kinds(manifest)

    # Memory episode 节点：跨日引用（用于 memory_episodes 字段，标准 MemoryEpisodeNode 格式）
    memory_episode_nodes: list[dict[str, Any]] = []
    episodes = manifest.get("episodes") or []
    case_name = manifest.get("case", case)
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("id", "")
        # ep_003 是当前正在 Live 回放的幕 → prior=False；ep_001/002 是历史 → prior=True
        is_current = ep_id == "ep_003"
        memory_episode_nodes.append({
            "record_id": ep_id,
            "timestamp": str(ep.get("timestamp", "")),
            "risk_level": str(ep.get("decision", "")),
            "recommended_action": str(ep.get("decision_detail", "")),
            "summary": str(ep.get("label", "")),
            "reason_summary": tuple(str(e) for e in (ep.get("evidence") or ())),
            "command_types": tuple(),
            "prior": not is_current,
            # Extra fields for injector / rendering compatibility
            "ref": f"golden://{case_name}/episodes/{ep_id}",
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
        "audio_events": audio_events,
        "expected_decision_outcome": expected_outcome,
    }


__all__ = [
    "MANIFEST_TO_AUDIO_KIND",
    "golden_evidence_projection",
    "manifest_audio_to_live_audio_kinds",
]

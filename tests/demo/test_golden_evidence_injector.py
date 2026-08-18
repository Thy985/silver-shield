"""Unit tests for golden_evidence_injector (Phase 2 of M1).

验证：
1. inject_golden_evidence 不破坏 LiveAdapter 内部
2. timeline 节点追加成功（不覆盖 runtime 节点）
3. graph.nodes 追加成功
4. memory_episodes 字段被填充
5. 4 case 都能 inject 成功
6. refs 同步追加
7. 没有 golden pre-event 字段的 case 也不报错
8. 不修改 source 节点（runtime REAL_SENSOR 不被污染）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silver_demo.golden_evidence_injector import (
    golden_session_metadata,
    inject_golden_evidence,
)


def _make_minimal_projection(case: str) -> dict:
    """构造最小 EvidenceProjection dict（用于测试 inject 行为）。"""
    return {
        "meta": {"generated_at": "live", "scenario_count": 1},
        "scenarios": (
            {
                "scenario_id": case,
                "ok": True,
                "mode": "live",
                "n_frames": 0,
                "scenario_fingerprint": case,
                "counts": {
                    "perception_events": 0, "warnings": 0, "commands": 0,
                    "sink_commands": 0, "decision_traces": 0,
                    "episodes": 0, "cross_modal_links": 0,
                },
                "event_types": (),
                "risk_levels": (),
                "recommended_actions": (),
                "command_types": (),
                "trace_outcome_kinds": (),
                "suppress_reasons": (),
                "episode_action_command_types": (),
                "intervention_dispatch": (),
                "timeline": (),  # 真实 runtime 节点（空）
                "decision_evidence": (),
                "audio_evidence": (),
                "case_time_tracks": (),
                "memory_episodes": (),  # 真实 runtime 节点（空）
                "gate": (),
                "gate_passed": False,
                "gate_degraded": False,
                "fingerprints": None,
                "refs": (),
                "graph": {
                    "scenario_id": case,
                    "nodes": (),  # 真实 runtime graph（空）
                    "edges": (),
                },
            },
        ),
    }


def test_telephone_risk_inject_acoustic_nodes():
    """telephone_risk 注入 4 个声学状态节点 + 0 memory（无 episodes）。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    timeline = p["scenarios"][0]["timeline"]
    audio_nodes = [n for n in timeline if n["type"] == "golden_audio_state"]
    assert len(audio_nodes) == 4, f"期望 4 阶段，实际 {len(audio_nodes)}"
    # 全部 SIMULATED
    for n in audio_nodes:
        assert n["provenance_kind"] == "SIMULATED"


def test_repeated_visit_inject_memory_nodes():
    """repeated_visit 注入 3 个 memory_ref timeline 节点 + 3 个 memory_episode 节点。"""
    case = "repeated_visit"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    timeline = p["scenarios"][0]["timeline"]
    mem_refs = [n for n in timeline if n["type"] == "golden_memory_ref"]
    assert len(mem_refs) == 3
    # memory_episodes 字段被填充
    mem_eps = p["scenarios"][0]["memory_episodes"]
    assert len(mem_eps) == 3
    for n in mem_eps:
        assert n["provenance_kind"] == "SIMULATED"
    # graph.nodes 也被追加
    graph_nodes = p["scenarios"][0]["graph"]["nodes"]
    assert len(graph_nodes) == 3


def test_evidence_insufficient_inject_empty():
    """evidence_insufficient 无 pre-event 字段 → 注入后所有字段仍为空。"""
    case = "evidence_insufficient"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    assert p["scenarios"][0]["timeline"] == ()
    assert p["scenarios"][0]["memory_episodes"] == ()
    assert p["scenarios"][0]["graph"]["nodes"] == ()


def test_stranger_visit_inject_empty():
    """stranger_visit 无 pre-event 字段 → 同 evidence_insufficient。"""
    case = "stranger_visit"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    assert p["scenarios"][0]["timeline"] == ()


def test_does_not_modify_runtime_nodes():
    """注入不能修改/删除已有的 runtime 节点。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    # 模拟 runtime 已有的节点
    p["scenarios"][0]["timeline"] = (
        {
            "timestamp": "F1",
            "stage": "perception",
            "type": "frame",
            "summary": "runtime frame",
            "verdict": "INFO",
            "modality": "VISION",
            "provenance_kind": "REAL_SENSOR",  # runtime 真实节点
            "ref": "live://frame/1",
        },
    )
    p["scenarios"][0]["refs"] = ("live://frame/1",)
    # 注入
    inject_golden_evidence(p, case)
    timeline = p["scenarios"][0]["timeline"]
    # runtime 节点必须保留
    assert timeline[0]["ref"] == "live://frame/1"
    assert timeline[0]["provenance_kind"] == "REAL_SENSOR"
    # golden 节点在尾部
    assert any(n["provenance_kind"] == "SIMULATED" for n in timeline)
    # refs 同步（schema: refs 在 scenario 层）
    assert "live://frame/1" in p["scenarios"][0]["refs"]
    assert any("golden://" in r for r in p["scenarios"][0]["refs"])


def test_inject_preserves_provenance():
    """VM-1 守护：注入不能把 runtime REAL_SENSOR 改成其他。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    p["scenarios"][0]["timeline"] = (
        {
            "timestamp": "LIVE",
            "stage": "live",
            "type": "session",
            "summary": "session",
            "verdict": "INFO",
            "modality": "VISION",
            "provenance_kind": "REAL_SENSOR",
            "ref": "live://session/stranger_visit",
        },
    )
    inject_golden_evidence(p, case)
    # session 节点必须仍是 REAL_SENSOR
    session = p["scenarios"][0]["timeline"][0]
    assert session["provenance_kind"] == "REAL_SENSOR"


def test_refs_synced():
    """refs 列表必须与 timeline 节点 ref 同步。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    timeline = p["scenarios"][0]["timeline"]
    refs = p["scenarios"][0]["refs"]
    # 每个 timeline 节点的 ref 必须在 refs 里
    timeline_refs = {n["ref"] for n in timeline}
    refs_set = set(refs)
    assert timeline_refs.issubset(refs_set), (
        f"timeline refs 不全在 refs 里：missing={timeline_refs - refs_set}"
    )


def test_session_metadata():
    """golden_session_metadata 返回 case + expected_decision_outcome。"""
    m = golden_session_metadata("telephone_risk")
    assert m["case"] == "telephone_risk"
    assert m["expected_decision_outcome"] == "RISK_SIGNAL"

    m = golden_session_metadata("stranger_visit")
    assert m["expected_decision_outcome"] == "LOW"

    m = golden_session_metadata("repeated_visit")
    assert m["expected_decision_outcome"] == "NOTIFY_FAMILY"

    m = golden_session_metadata("evidence_insufficient")
    assert m["expected_decision_outcome"] == "NOT_TRIGGERED"


def test_inject_returns_same_projection():
    """inject 返回的就是 input projection（in-place 修改 + 返回引用）。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    result = inject_golden_evidence(p, case)
    assert result is p  # 同一对象


def test_inject_preserves_existing_graph_edges():
    """注入不能删除已有 graph edges。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    p["scenarios"][0]["graph"]["edges"] = (
        {"source": "scn", "target": "event-0", "type": "observed_from", "ref": "live://event[0]"},
    )
    inject_golden_evidence(p, case)
    edges = p["scenarios"][0]["graph"]["edges"]
    assert len(edges) == 1
    assert edges[0]["ref"] == "live://event[0]"


# ===========================================================================
# M1-AudioMap: audio_events 注入测试
# ===========================================================================


def test_telephone_risk_inject_audio_events():
    """telephone_risk → audio_evidence 注入 audio_telephone_persistent + audio_voice_raised。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    audio_ev = p["scenarios"][0]["audio_evidence"]
    assert len(audio_ev) == 2, f"期望 2 个 audio events，实际 {len(audio_ev)}"
    kinds = {e["kind"] for e in audio_ev}
    assert "audio_telephone_persistent" in kinds
    assert "audio_voice_raised" in kinds
    # provenance 必须是 SIMULATED
    for e in audio_ev:
        assert e["provenance_kind"] == "SIMULATED"
    # ref 格式
    for e in audio_ev:
        assert e["ref"].startswith("live://audio/golden/")


def test_stranger_visit_inject_audio_events():
    """stranger_visit → audio_evidence 注入 audio_anomaly_other（doorbell）。"""
    case = "stranger_visit"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    audio_ev = p["scenarios"][0]["audio_evidence"]
    assert len(audio_ev) == 1
    assert audio_ev[0]["kind"] == "audio_anomaly_other"
    assert audio_ev[0]["provenance_kind"] == "SIMULATED"


def test_repeated_visit_inject_audio_events():
    """repeated_visit → audio_evidence 注入 audio_anomaly_other（episodes.evidence.doorbell）。"""
    case = "repeated_visit"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    audio_ev = p["scenarios"][0]["audio_evidence"]
    assert len(audio_ev) == 1
    assert audio_ev[0]["kind"] == "audio_anomaly_other"
    assert audio_ev[0]["provenance_kind"] == "SIMULATED"


def test_evidence_insufficient_no_audio_events():
    """evidence_insufficient 无 audio 声明 → audio_evidence 保持空。"""
    case = "evidence_insufficient"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    assert p["scenarios"][0]["audio_evidence"] == ()


def test_audio_events_refs_synced():
    """audio_evidence 的 ref 必须同步到 scenario.refs。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    inject_golden_evidence(p, case)
    audio_ev = p["scenarios"][0]["audio_evidence"]
    refs = p["scenarios"][0]["refs"]
    audio_refs = {e["ref"] for e in audio_ev}
    assert audio_refs.issubset(set(refs))


def test_audio_events_do_not_overwrite_real_audio():
    """若 runtime 已有 REAL_SENSOR audio_evidence，注入不覆盖（保护真实数据）。"""
    case = "telephone_risk"
    p = _make_minimal_projection(case)
    # 模拟 runtime 已摄入真实音频
    p["scenarios"][0]["audio_evidence"] = (
        {
            "timestamp": "1.0",
            "kind": "audio_telephone_persistent",
            "score": 0.9,
            "confidence": 0.95,
            "labels": ("telephone_interaction",),
            "source_segment_ids": ("live://audio/0",),
            "ref": "live://audio/0",
            "provenance_kind": "REAL_SENSOR",
        },
    )
    inject_golden_evidence(p, case)
    audio_ev = p["scenarios"][0]["audio_evidence"]
    # 必须保留原 REAL_SENSOR 节点
    assert len(audio_ev) == 1
    assert audio_ev[0]["provenance_kind"] == "REAL_SENSOR"
    assert audio_ev[0]["ref"] == "live://audio/0"

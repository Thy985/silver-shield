"""ADR-0035 D3 · 测试夹具（visualizer/video/）。

提供手填 ``ScenarioEvidence``（dict，符合 loader 投影形态）以隔离 loader/artifact，
并暴露真实 artifact 目录（端到端测试使用）。同时防御性将 ``src`` 加入 sys.path，
保证在任意 pytest 环境下 ``home_perception`` 可导入。
"""

from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parents[3] / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from home_perception.visualizer.video.narrative.templates import template_for_evidence

REPO_ROOT = Path(__file__).resolve().parents[3]


def make_evidence(
    scenario_id: str = "sw_adr0034_elderly_dwell",
    event_types: tuple[str, ...] = ("abnormal_dwell",),
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    counts: dict | None = None,
    decision_evidence: list[dict] | None = None,
    recommended_actions: tuple[str, ...] = ("NOTIFY_FAMILY",),
    command_types: tuple[str, ...] = ("LOG_ONLY",),
    episode_action_command_types: tuple[str, ...] = ("LOG_ONLY",),
    ok: bool = True,
    gate_passed: bool = True,
    scenario_fingerprint: str = "fp_elderly_001",
) -> dict:
    """构造手填 ScenarioEvidence（dict 形态，与 loader 投影一致）。"""
    if nodes is None:
        nodes = [
            {"id": "scn", "type": "Scenario", "label": scenario_id, "ref": f"{scenario_id}.canonical.json#scenario_id", "provenance_kind": "SIMULATED"},
            {"id": "event-0", "type": "Event", "label": "abnormal_dwell", "ref": f"{scenario_id}.canonical.json#event0", "provenance_kind": "SIMULATED"},
            {"id": "decision-0", "type": "Decision", "label": "WARN", "ref": f"{scenario_id}.canonical.json#decision0", "provenance_kind": "SIMULATED"},
            {"id": "action-0", "type": "Action", "label": "NOTIFY_FAMILY", "ref": f"{scenario_id}.canonical.json#action0", "provenance_kind": "SIMULATED"},
            {"id": "episodes", "type": "Episode", "label": "episode-1", "ref": f"{scenario_id}.canonical.json#episodes", "provenance_kind": "SIMULATED"},
        ]
    if edges is None:
        edges = [
            {"source": "scn", "target": "event-0", "type": "observed_from", "ref": "r1"},
            {"source": "event-0", "target": "decision-0", "type": "caused_by", "ref": "r2"},
            {"source": "decision-0", "target": "action-0", "type": "triggered", "ref": "r3"},
            {"source": "action-0", "target": "episodes", "type": "stored_as", "ref": "r4"},
        ]
    if counts is None:
        counts = {
            "perception_events": 1,
            "warnings": 1,
            "commands": 1,
            "sink_commands": 1,
            "decision_traces": 1,
            "episodes": 1,
            "cross_modal_links": 0,
        }
    if decision_evidence is None:
        decision_evidence = [
            {"kind": "evidence", "label": "检测证据（事件类型）", "value": "abnormal_dwell", "ref": "d1"},
            {"kind": "reasoning", "label": "决策结果（trace outcome）", "value": "WARN", "ref": "d2"},
            {"kind": "outcome", "label": "风险级别", "value": "LOW", "ref": "d3"},
        ]
    return {
        "scenario_id": scenario_id,
        "ok": ok,
        "mode": "frames",
        "n_frames": 10,
        "scenario_fingerprint": scenario_fingerprint,
        "counts": counts,
        "event_types": event_types,
        "risk_levels": ("LOW",),
        "recommended_actions": recommended_actions,
        "command_types": command_types,
        "trace_outcome_kinds": ("WARN",),
        "suppress_reasons": (),
        "episode_action_command_types": episode_action_command_types,
        "timeline": (),
        "decision_evidence": tuple(decision_evidence),
        "gate": (),
        "gate_passed": gate_passed,
        "gate_degraded": False,
        "fingerprints": {"expectation_fingerprint": "e1", "loop_fingerprint": "l1"},
        "refs": tuple(n["ref"] for n in nodes),
        "graph": {"scenario_id": scenario_id, "nodes": tuple(nodes), "edges": tuple(edges)},
    }


def artifact_dir() -> Path:
    return REPO_ROOT / "artifacts" / "adr0034_integration"


__all__ = ["artifact_dir", "make_evidence", "template_for_evidence"]

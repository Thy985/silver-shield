"""ADR-0035 D1 · 测试夹具：构造合法/异常 artifact 集（真实结构形状）。

所有测试通过 ``make_artifacts`` 在 tmp_path 生成 artifact 目录——不依赖仓库
``artifacts/``（CI 上为产物可能不存在），且可精确注入异常（删文件/删字段）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 单一来源（评审 R2-#7）：文件名常量复用 loader 定义，防双源漂移。
from home_perception.visualizer.loader import SUMMARY_FILENAME

SUMMARY = SUMMARY_FILENAME


def _canonical(scenario_id: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "ok": True,
        "mode": "detections",
        "n_frames": 660,
        "scenario_fingerprint": "a" * 64,
        "failure_codes": [],
        "stages": [
            {"name": "perception", "passed": True, "failure_code": None, "severity": "blocking"},
            {"name": "decision", "passed": True, "failure_code": None, "severity": "blocking"},
            {"name": "notification", "passed": True, "failure_code": None, "severity": "blocking"},
            {"name": "memory", "passed": True, "failure_code": None, "severity": "blocking"},
            {"name": "cross_modal", "passed": True, "failure_code": None, "severity": "blocking"},
            {"name": "observability", "passed": True, "failure_code": None, "severity": "blocking"},
        ],
        "artifacts": {
            "counts": {
                "perception_events": 1,
                "warnings": 1,
                "commands": 1,
                "sink_commands": 1,
                "decision_traces": 1,
                "episodes": 2,
                "cross_modal_links": 1,
            },
            "event_types": ["abnormal_dwell"],
            "risk_levels": ["LOW"],
            "recommended_actions": ["NOTIFY_FAMILY"],
            "command_types": ["LOG_ONLY"],
            "trace_outcome_kinds": ["WARN"],
            "suppress_reasons": [],
            "episode_action_command_types": ["LOG_ONLY"],
        },
        "perception_score": {
            "scenario_id": scenario_id,
            "expected_label": "alert",
            "actual_label": "alert",
            "outcome": "TP",
            "validation_ok": True,
        },
    }


def _gate(scenario_id: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "passed": True,
        "degraded": False,
        "verdicts": [
            {"name": "perception", "passed": True, "severity": "blocking", "failure_code": None},
            {"name": "decision", "passed": True, "severity": "blocking", "failure_code": None},
            {"name": "notification", "passed": True, "severity": "blocking", "failure_code": None},
            {"name": "memory", "passed": True, "severity": "blocking", "failure_code": None},
            {"name": "cross_modal", "passed": True, "severity": "blocking", "failure_code": None},
            {"name": "observability", "passed": True, "severity": "blocking", "failure_code": None},
        ],
        "notices": [],
    }


def _fingerprints(scenario_id: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "expectation_fingerprint": "e" * 64,
        "loop_fingerprint": "f" * 64,
    }


def make_artifacts(
    directory: Path,
    scenario_ids: tuple[str, ...] = ("sw_t1",),
    *,
    drop_file: str | None = None,
    drop_field: tuple[str, str] | None = None,
) -> Path:
    """在 ``directory`` 生成合法 artifact 集；``drop_*`` 注入异常（fail-closed 测试）。

    Args:
        drop_file: 删除的 artifact 文件名（如 ``sw_t1.gate.json``）。
        drop_field: 从 canonical 中删除的字段（(owner, key) 如 ("sw_t1", "stages")）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for sid in scenario_ids:
        canonical = _canonical(sid)
        if drop_field is not None and drop_field[0] == sid:
            canonical.pop(drop_field[1], None)
        files = {
            f"{sid}.canonical.json": canonical,
            f"{sid}.gate.json": _gate(sid),
            f"{sid}.fingerprints.json": _fingerprints(sid),
        }
        for name, payload in files.items():
            if drop_file == name:
                continue
            (directory / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        entries.append(
            {
                "scenario_id": sid,
                "path": f"src/.../{sid}.yaml",
                "ok": True,
                "failure_codes": [],
                "canonical_report": f"{directory}/{sid}.canonical.json",
            }
        )
    summary = {
        "generated_at": "2026-08-12T00:00:00+00:00",
        "scenarios_dir": str(directory),
        "scenarios": entries,
    }
    if drop_file != SUMMARY:
        (directory / SUMMARY).write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8"
        )
    return directory


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return make_artifacts(tmp_path / "artifacts")

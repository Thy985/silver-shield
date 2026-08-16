"""G0-3/G0-2 · Memory Timeline 契约测试（记忆时间线：prior 历史 + 本次会话）。

覆盖验收（docs/DESIGN-golden-case-viewer.md §3.1）：
- report 投影：EpisodicRecord → canonical memory_episodes（prior 标记、确定性排序）；
- loader 投影：canonical memory_episodes → MemoryEpisodeNode（字段白名单、fail-closed）；
- render：memory_timeline 面板（历史/本次卡片 + 引用脚注）；
- descriptor：memory_episodes >= 2 → 首屏注入 memory_timeline（VM-11 展示编排）；
- AC-12：无 memory 明细恒 ()（绝不编造）。

不依赖 cv2 / torch（纯契约测试）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore


def _episode(record_id: str, visitor: str, t: datetime, *, prior: bool) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=record_id,
        visitor_instance_id=visitor,
        enter_time=t,
        leave_time=t + timedelta(seconds=30),
        duration_seconds=30.0,
        source_event_ids=[f"prior:{record_id}" if prior else record_id],
        summary=f"历史访问 {record_id}" if prior else f"本次访问 {record_id}",
        model_version="test",
        reason_summary=["abnormal_dwell"],
        risk_level="LOW",
        recommended_action="MONITOR",
        device_id="home_entry",
    )


def _store_with(records: list[EpisodicRecord]) -> InMemoryStore:
    store = InMemoryStore()
    for r in records:
        store.upsert_episodic(r)
    return store


def _episodes() -> list[EpisodicRecord]:
    t0 = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)
    return [
        _episode("ep-prior-historical_001", "V-017", t0, prior=True),
        _episode("ep-prior-historical_002", "V-017", t0 + timedelta(days=1), prior=True),
        _episode("ep-eb214cb8-0000", "V-017", t0 + timedelta(days=2), prior=False),
    ]


# ---------------------------------------------------------------------------
# 1. report 投影（canonical memory_episodes）
# ---------------------------------------------------------------------------


def test_report_projects_memory_episodes():
    """EpisodicRecord → canonical memory_episodes（prior 标记 + 确定性排序 + 前缀键）。"""
    from home_perception.integration.loop.report import _project_memory_episodes

    class _R:
        episodes = tuple(_episodes())

    out = _project_memory_episodes(_R())
    assert len(out) == 3
    # 确定性排序：enter_time 升序 → prior 在前
    assert [e["memory_record_id"] for e in out] == [
        "ep-prior-historical_001",
        "ep-prior-historical_002",
        "ep-eb214cb8-0000",
    ]
    assert out[0]["memory_prior"] is True
    assert out[2]["memory_prior"] is False
    # 前缀键（脱敏安全：无裸 score/decision 键）
    first = out[0]
    for key in ("memory_record_id", "memory_timestamp", "memory_risk_level",
                "memory_recommended_action", "memory_summary", "memory_reason_summary",
                "memory_command_types", "memory_prior"):
        assert key in first


def test_report_projects_no_episodes_empty():
    """无 episode → 恒 ()（AC-12 不编造）。"""
    from home_perception.integration.loop.report import _project_memory_episodes

    class _R:
        episodes = ()

    assert _project_memory_episodes(_R()) == ()


# ---------------------------------------------------------------------------
# 2. loader 投影（canonical → MemoryEpisodeNode）
# ---------------------------------------------------------------------------


def _artifacts_with_memory() -> dict:
    return {
        "memory_episodes": [
            {
                "memory_record_id": "ep-prior-historical_001",
                "memory_timestamp": "2026-08-13T15:30:00+00:00",
                "memory_risk_level": "LOW",
                "memory_recommended_action": "MONITOR",
                "memory_summary": "3 days ago",
                "memory_reason_summary": ["abnormal_dwell"],
                "memory_command_types": [],
                "memory_prior": True,
            },
            {
                "memory_record_id": "ep-prior-historical_002",
                "memory_timestamp": "2026-08-14T15:30:00+00:00",
                "memory_risk_level": "LOW",
                "memory_recommended_action": "MONITOR",
                "memory_summary": "yesterday",
                "memory_reason_summary": ["abnormal_dwell"],
                "memory_command_types": [],
                "memory_prior": True,
            },
        ]
    }


def test_loader_projects_memory_episodes():
    """canonical memory_episodes → MemoryEpisodeNode（字段白名单、prior 透传）。"""
    from home_perception.visualizer.loader import _build_memory_episodes

    nodes = _build_memory_episodes(_artifacts_with_memory(), "sw_g", "owner")
    assert len(nodes) == 2
    n0 = nodes[0]
    assert n0["record_id"] == "ep-prior-historical_001"
    assert n0["prior"] is True
    assert n0["reason_summary"] == ("abnormal_dwell",)
    assert n0["command_types"] == ()


def test_loader_memory_absent_empty():
    """旧 artifact 无 memory_episodes 键 → ()（AC-12 不编造）。"""
    from home_perception.visualizer.loader import _build_memory_episodes

    assert _build_memory_episodes({}, "sw_g", "owner") == ()


def test_loader_memory_fail_closed():
    """缺 memory_record_id → EvidenceProjectionError（fail-closed，不兜底占位）。"""
    from home_perception.visualizer.loader import EvidenceProjectionError, _build_memory_episodes

    bad = _artifacts_with_memory()
    del bad["memory_episodes"][0]["memory_record_id"]
    with pytest.raises(EvidenceProjectionError):
        _build_memory_episodes(bad, "sw_g", "owner")


# ---------------------------------------------------------------------------
# 3. render：memory_timeline 面板
# ---------------------------------------------------------------------------


def _scenario_with_memory() -> dict:
    return {
        "scenario_id": "sw_golden_repeated_visit",
        "memory_episodes": (
            {
                "record_id": "ep-prior-historical_001",
                "timestamp": "2026-08-13T15:30:00+00:00",
                "risk_level": "LOW",
                "recommended_action": "MONITOR",
                "summary": "3 days ago: abnormal dwell",
                "reason_summary": ("abnormal_dwell",),
                "command_types": (),
                "prior": True,
            },
            {
                "record_id": "ep-prior-historical_002",
                "timestamp": "2026-08-14T15:30:00+00:00",
                "risk_level": "LOW",
                "recommended_action": "MONITOR",
                "summary": "yesterday: abnormal dwell",
                "reason_summary": ("abnormal_dwell",),
                "command_types": (),
                "prior": True,
            },
        ),
    }


def test_render_memory_timeline_panel():
    """memory_timeline 渲染：历史卡片 + prior 标记 + 引用脚注。"""
    from home_perception.visualizer.viewer.render import _render_memory_timeline

    html = _render_memory_timeline(_scenario_with_memory())
    assert "记忆时间线" in html
    assert "ep-prior-historical_001" in html
    assert "历史预置" in html
    assert "Decision Trace.historical_record_ids" in html


def test_render_memory_timeline_empty_returns_empty():
    """无 memory_episodes → 空串（不渲染空面板）。"""
    from home_perception.visualizer.viewer.render import _render_memory_timeline

    assert _render_memory_timeline({"scenario_id": "sw_g"}) == ""


# ---------------------------------------------------------------------------
# 4. descriptor：memory_episodes >= 2 → 注入 memory_timeline（VM-11）
# ---------------------------------------------------------------------------


def _projection_with(memory_count: int):
    mem = tuple(
        {
            "record_id": f"ep-prior-historical_{i:03d}",
            "timestamp": "2026-08-13T15:30:00+00:00",
            "risk_level": "LOW",
            "recommended_action": "MONITOR",
            "summary": "h",
            "reason_summary": (),
            "command_types": (),
            "prior": True,
        }
        for i in range(memory_count)
    )
    return {
        "meta": {"generated_at": "t", "scenario_count": 1},
        "scenarios": (
            {
                "scenario_id": "sw_golden_repeated_visit",
                "ok": True,
                "mode": "detections",
                "n_frames": 660,
                "scenario_fingerprint": "fp",
                "counts": {
                    "perception_events": 1,
                    "warnings": 1,
                    "commands": 1,
                    "sink_commands": 1,
                    "decision_traces": 1,
                    "episodes": memory_count,
                    "cross_modal_links": 0,
                },
                "event_types": (),
                "risk_levels": (),
                "recommended_actions": (),
                "command_types": (),
                "trace_outcome_kinds": (),
                "suppress_reasons": (),
                "episode_action_command_types": (),
                "timeline": (),
                "decision_evidence": (),
                "audio_evidence": (),
                "memory_episodes": mem,
                "gate": (),
                "gate_passed": True,
                "gate_degraded": False,
                "fingerprints": {"expectation_fingerprint": "expect-fp", "loop_fingerprint": "loop-fp"},
                "refs": (),
                "graph": {"nodes": (), "edges": ()},
            },
        ),
    }


def test_descriptor_injects_memory_timeline_when_history():
    """memory_episodes >= 2 → 首屏 panels 注入 memory_timeline。"""
    from home_perception.visualizer.viewer.case_presentation import (
        build_default_case_presentation,
    )

    desc = build_default_case_presentation(_projection_with(3))
    panels = desc["first_screen_layout"]["panels"]
    assert "memory_timeline" in panels
    # 注入位置：current_risk 之前（先看历史背景，再看当前风险）
    assert panels.index("memory_timeline") < panels.index("current_risk")


def test_descriptor_no_memory_timeline_without_history():
    """memory_episodes < 2 → 不注入（普通场景零变化）。"""
    from home_perception.visualizer.viewer.case_presentation import (
        build_default_case_presentation,
    )

    desc = build_default_case_presentation(_projection_with(1))
    assert "memory_timeline" not in desc["first_screen_layout"]["panels"]


def test_descriptor_any_scenario_triggers_global_panel():
    """G0-4：descriptor 是全局单例——任一场景（非 scenario_index 场景）有记忆也注入。"""
    from home_perception.visualizer.viewer.case_presentation import (
        build_default_case_presentation,
    )

    # 多场景投影：index 0 = 无记忆（benign 语义），index 1 = 有 3 条记忆（repeated 语义）。
    proj = _projection_with(0)
    proj["scenarios"] = proj["scenarios"] + _projection_with(3)["scenarios"]
    desc = build_default_case_presentation(proj, scenario_index=0)
    panels = desc["first_screen_layout"]["panels"]
    assert "memory_timeline" in panels
    assert panels.index("memory_timeline") < panels.index("current_risk")


def test_load_case_presentation_ci_descriptor_derives_panels(tmp_path):
    """G0-4：CI descriptor（无 first_screen_layout）→ 派生感知场景默认面板（含注入）。"""
    from home_perception.visualizer.viewer.artifact_source import (
        load_case_presentation,
    )
    from home_perception.visualizer.viewer.case_presentation import (
        _DEFAULT_FIRST_SCREEN_PANELS,
    )

    from .conftest import make_artifacts

    # canonical 形态：memory_* 前缀键（loader 从 artifacts.memory_episodes 读）。
    mem_eps = [
        {
            "memory_record_id": f"ep-prior-historical_{i:03d}",
            "memory_timestamp": f"2026-08-{13 + i:02d}T15:30:00+00:00",
            "memory_risk_level": "LOW",
            "memory_recommended_action": "MONITOR",
            "memory_summary": "h",
            "memory_reason_summary": [],
            "memory_prior": True,
        }
        for i in range(3)
    ]
    # 注入 memory_episodes 到 canonical.artifacts（make_artifacts 生成合法 artifact 集）。
    canon_dir = make_artifacts(
        tmp_path / "canonical", memory_episodes=mem_eps, audio_evidence=[]
    )
    # CI descriptor：只有元数据，无 first_screen_layout（G0-4 关键场景）。
    desc_path = tmp_path / "ci_desc.json"
    desc_path.write_text(
        json.dumps(
            {"generated_by": "ci", "renderer_version": "1.0", "provenance_ref": "provenance.json"}
        ),
        encoding="utf-8",
    )
    _proj, desc = load_case_presentation(str(canon_dir), descriptor_path=str(desc_path))
    panels = desc["first_screen_layout"]["panels"]
    assert "memory_timeline" in panels  # 派生注入：ci 模式也能拿到 Memory Timeline
    for p in _DEFAULT_FIRST_SCREEN_PANELS:  # 缺省基底不变
        assert p in panels


def test_render_case_viewer_memory_timeline_end_to_end():
    """render_case_viewer：记忆场景 → HTML 含记忆时间线；普通场景 → 不含。"""
    from home_perception.visualizer.viewer.case_presentation import (
        build_default_case_presentation,
    )
    from home_perception.visualizer.viewer.render import render_case_viewer

    proj = _projection_with(2)
    desc = build_default_case_presentation(proj)
    html = render_case_viewer(proj, desc)
    assert "记忆时间线" in html
    assert "ep-prior-historical_000" in html

    proj2 = _projection_with(0)
    desc2 = build_default_case_presentation(proj2)
    html2 = render_case_viewer(proj2, desc2)
    assert "记忆时间线" not in html2

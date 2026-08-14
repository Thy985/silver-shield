"""ADR-0036 Slice B · Live Adapter 契约测试（VM-13 Phase A）。

覆盖验收：AC-3（共享 EvidenceProjection schema）/ AC-4b（幂等重放）/ AC-5（VM-3 不 import
runtime/silver_demo）/ AC-7（provenance=REAL_SENSOR 一等视觉）/ AC-8（gate/fingerprints 等
缺失显式表达，禁伪造）/ VM-8（ProjectionAccumulator 确定性 + 滚动窗口）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from home_perception.visualizer.viewer.live_adapter import (
    LiveIngestError,
    ProjectionAccumulator,
    build_live_presentation,
    frame_result_to_live_frame,
)
from home_perception.visualizer.viewer.render import render_case_viewer


def _make_frame(frame_index, *, n_detections=0, n_visitor_events=0, event_types=(),
                risk_levels=(), recommended_actions=(), command_types=()):
    """构造 FrameResult 契约的 dict 形态（鸭子类型摄入，不依赖生产对象）。"""
    return {
        "frame_index": frame_index,
        "n_detections": n_detections,
        "n_visitor_events": n_visitor_events,
        "perception_events": [{"event_type": et} for et in event_types],
        "warnings": [
            {"risk_level": rl, "recommended_action": ra}
            for rl, ra in zip(risk_levels, recommended_actions)
        ],
        "commands": [{"command_type": ct} for ct in command_types],
    }


def _sample_frames():
    return [
        _make_frame(0, n_detections=2, n_visitor_events=1, event_types=["stranger_loiter"]),
        _make_frame(
            1, n_detections=3, n_visitor_events=1,
            event_types=["stranger_loiter", "visit_normal"],
            risk_levels=["HIGH"], recommended_actions=["ESCALATE_COMMUNITY"],
            command_types=["CREATE_COMMUNITY_TASK"],
        ),
        _make_frame(2, n_detections=1, n_visitor_events=0, command_types=["LOG_ONLY"]),
    ]


def _projection_json(proj):
    return json.dumps(proj, sort_keys=True, default=str)


# —— AC-5 / VM-3：依赖方向（不 import 生产 runtime / silver_demo） ——

def test_live_adapter_no_production_imports():
    """live_adapter.py 不得 import runtime/evaluation/integration/memory/silver_demo（AC-5）。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "home_perception" / "visualizer" / "viewer" / "live_adapter.py"
    )
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    forbidden = (
        "home_perception.runtime",
        "home_perception.evaluation",
        "home_perception.integration",
        "home_perception.memory",
        "silver_demo",
    )
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for m in modules:
            if m.startswith(forbidden):
                offenders.append(m)
    assert offenders == [], f"live_adapter 不得 import 生产包：{offenders}"


def test_frame_result_to_live_frame_duck_typed_no_runtime_objects():
    """frame_result_to_live_frame 接受纯 dict（不要求生产对象），VM-3 鸭子类型映射。"""
    lf = frame_result_to_live_frame(_sample_frames()[1])
    assert lf["frame_index"] == 1
    assert lf["event_types"] == ("stranger_loiter", "visit_normal")
    assert lf["risk_levels"] == ("HIGH",)
    assert lf["recommended_actions"] == ("ESCALATE_COMMUNITY",)
    assert lf["command_types"] == ("CREATE_COMMUNITY_TASK",)


# —— AC-4b / VM-8：幂等重放（同有序流重放 N 次逐字段一致） ——

@pytest.mark.parametrize("replays", [2, 3, 5])
def test_idempotent_replay(replays):
    """同一有序帧流重放 N(≥2) 次，最终 EvidenceProjection 逐字段一致（AC-4b）。"""
    baselines = None
    for _ in range(replays):
        acc = ProjectionAccumulator("sess-x", window_size=64)
        for f in _sample_frames():
            acc.ingest(f)
        proj = acc.to_evidence_projection()
        dumped = _projection_json(proj)
        if baselines is None:
            baselines = dumped
        else:
            assert dumped == baselines, "同帧流重放结果不一致（破坏 AC-4b 幂等）"


# —— AC-8：Live 缺失字段显式表达，禁伪造 ——

def test_live_absent_fields_explicit():
    """Live 投影必须显式表达缺失（gate=()/fingerprints=None/无 audio 等），不得伪造。"""
    acc = ProjectionAccumulator("sess-y")
    for f in _sample_frames():
        acc.ingest(f)
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert scn["gate"] == ()
    assert scn["gate_passed"] is False
    assert scn["gate_degraded"] is False
    assert scn["fingerprints"] is None
    assert scn["trace_outcome_kinds"] == ()
    assert scn["suppress_reasons"] == ()
    assert scn["episode_action_command_types"] == ()
    assert scn["counts"]["episodes"] == 0
    assert scn["counts"]["cross_modal_links"] == 0
    # 关键守卫：不得出现伪造的 gate=PASS 或假指纹。
    assert "PASS" not in scn["gate"]  # 空元组本就不含
    # 断言 Live 无 audio_evidence 维度（Phase A 天然 absent，schema 无该字段）。


# —— AC-7：provenance_kind=REAL_SENSOR 一等视觉 ——

def test_real_sensor_provenance():
    """Live 所有节点 provenance_kind=REAL_SENSOR（AC-7 一等视觉，不得默认隐藏）。"""
    acc = ProjectionAccumulator("sess-z")
    for f in _sample_frames():
        acc.ingest(f)
    proj, _ = build_live_presentation(acc.to_evidence_projection())
    scn = proj["scenarios"][0]
    timeline_kinds = {n["provenance_kind"] for n in scn["timeline"]}
    graph_kinds = {n["provenance_kind"] for n in scn["graph"]["nodes"]}
    assert timeline_kinds == {"REAL_SENSOR"}
    assert graph_kinds == {"REAL_SENSOR"}


# —— VM-8：滚动窗口（累积计数独立于窗口裁剪） ——

def test_rolling_window_trims_timeline_but_counts_cumulative():
    """滚动窗口裁剪逐帧时间轴细节，但累计计数跨全量帧（VM-8 确定性）。"""
    acc = ProjectionAccumulator("sess-w", window_size=2)
    frames = [_make_frame(i, n_detections=1, event_types=["visit_normal"]) for i in range(5)]
    for f in frames:
        acc.ingest(f)
    scn = acc.to_evidence_projection()["scenarios"][0]
    # 累计帧数 = 5（独立于窗口裁剪）
    assert acc.n_frames == 5
    assert scn["n_frames"] == 5
    # 时间轴：1 会话锚点 + 窗口内 2 帧（最近 frame_index 3,4），frame 0..2 被裁剪。
    assert len(scn["timeline"]) == 3
    frame_ts = [n["timestamp"] for n in scn["timeline"] if n["type"] == "frame"]
    assert frame_ts == ["F3", "F4"], "滚动窗口应只保留最近 2 帧"
    # 累计计数仍覆盖全量 5 帧
    assert scn["counts"]["perception_events"] == 5
    assert scn["event_types"] == ("visit_normal",)


def test_empty_session_still_valid_and_renders():
    """零帧实时会话：时间轴含会话锚点（provenance 非空），benign 降级，渲染不崩。"""
    acc = ProjectionAccumulator("sess-empty")
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert len(scn["timeline"]) == 1  # 仅会话锚点
    assert scn["timeline"][0]["provenance_kind"] == "REAL_SENSOR"
    assert acc.n_frames == 0
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    html = render_case_viewer(proj, desc)
    assert "真实传感器" in html


# —— AC-5 / VM-3：fail-closed 摄入（缺字段/类型非法） ——

def test_ingest_fail_closed_on_missing_field():
    with pytest.raises(LiveIngestError):
        frame_result_to_live_frame({"frame_index": 0})  # 缺 n_detections 等


def test_ingest_fail_closed_on_bad_type():
    bad = {"frame_index": "x", "n_detections": 0, "n_visitor_events": 0,
           "perception_events": [], "warnings": [], "commands": []}
    with pytest.raises(LiveIngestError):
        frame_result_to_live_frame(bad)


def test_ingest_fail_closed_on_empty_str():
    bad = {"frame_index": 0, "n_detections": 0, "n_visitor_events": 0,
           "perception_events": [{"event_type": ""}], "warnings": [], "commands": []}
    with pytest.raises(LiveIngestError):
        frame_result_to_live_frame(bad)


def test_accumulator_invalid_params_fail_closed():
    with pytest.raises(LiveIngestError):
        ProjectionAccumulator("")
    with pytest.raises(LiveIngestError):
        ProjectionAccumulator("s", window_size=0)


# —— 复用 Case Viewer（renderer 守卫，AC-8/AC-7 渲染层） ——

def test_render_case_viewer_reuses_live_projection():
    """Live 投影经 render_case_viewer 复用，渲染显式表达实时模式（守卫生效）。"""
    acc = ProjectionAccumulator("sess-render")
    for f in _sample_frames():
        acc.ingest(f)
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    # 展示编排绑定 LiveFrameSource（媒体字节不进 View Model）
    assert desc["media_binding"]["source_kind"] == "LiveFrameSource"
    html = render_case_viewer(proj, desc)
    assert "真实传感器" in html            # AC-7 provenance 一等视觉
    assert "无 Gate 评估" in html          # AC-8 gate absent 显式表达
    assert "无（实时模式" in html          # AC-8 fingerprints absent 显式表达
    # Live 无媒体资产（resolve_media_source 对 LiveFrameSource 返回 None）→ 诚实呈现
    # 「无媒体绑定」脚注（媒体字节不进 View Model，VM-10/AC-11）。
    assert "无媒体绑定" in html


def test_build_live_presentation_rejects_empty_projection():
    from home_perception.visualizer.schema.evidence import EvidenceProjection, ProjectionMeta
    empty = EvidenceProjection(meta=ProjectionMeta(generated_at="live", scenario_count=0), scenarios=())
    with pytest.raises(LiveIngestError):
        build_live_presentation(empty)

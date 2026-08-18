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
                risk_levels=(), recommended_actions=(), reason_summary=(),
                command_types=(), detections=()):
    """构造 FrameResult 契约的 dict 形态（鸭子类型摄入，不依赖生产对象）。"""
    warnings = [
        {"risk_level": rl, "recommended_action": ra}
        for rl, ra in zip(risk_levels, recommended_actions)
    ]
    if reason_summary and warnings:
        warnings[0]["reason_summary"] = list(reason_summary)
    return {
        "frame_index": frame_index,
        "n_detections": n_detections,
        "n_visitor_events": n_visitor_events,
        "perception_events": [{"event_type": et} for et in event_types],
        "warnings": warnings,
        "commands": [{"command_type": ct} for ct in command_types],
        "detections": list(detections),
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


def _make_audio(timestamp, kind, *, score=0.8, confidence=0.9,
                source_segment_ids=("seg-1",), labels=("raised",), event_id=None):
    """构造 AudioPerceptionEvent 契约的 dict 形态（鸭子类型摄入，不依赖生产对象）。

    timestamp 数值型（对齐 AudioPerceptionEvent.to_dict() 的 Unix 秒 float），
    与 JSONL 人工条目的字符串形态都兼容。event_id 可选（透传上游事件 ID）。
    """
    d = {
        "timestamp": timestamp,
        "kind": kind,
        "score": score,
        "confidence": confidence,
        "source_segment_ids": list(source_segment_ids),
        "labels": list(labels),
    }
    if event_id is not None:
        d["event_id"] = event_id
    return d


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
        "home_perception.audio",
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
    # AC-12：Live 音频证据维度恒 ()（Phase B 真实音频证据尚未进入 canonical，不编造）。
    assert scn["audio_evidence"] == ()


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
    assert "受控演示输入" in html            # AC-7 诚实标注：Live=受控演示输入（非 REAL SENSOR 标榜）


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
    assert "受控演示输入" in html            # AC-7 provenance 一等视觉（诚实标注受控演示输入）
    assert "无 Gate 评估" in html          # AC-8 gate absent 显式表达
    assert "无（实时模式" in html          # AC-8 fingerprints absent 显式表达
    # Live 无媒体资产（resolve_media_source 对 LiveFrameSource 返回 None）→ 诚实呈现
    # 「无媒体绑定」脚注（媒体字节不进 View Model，VM-10/AC-11）。
    assert "无媒体绑定" in html


def test_live_omits_negative_capability_card_by_design():
    """Live 路径**刻意不显示**负向能力卡——与旗舰 Canonical 路径的有意分歧（ADR-0036）。

    - 负向能力卡（"为什么没有报警"）只在**已终结的 Canonical case** 有意义：它解释
      "系统观察到正常状态故未报警（TN）"或"证据不足保持克制（MONITOR）"。
    - ``/live`` 是**进行中的实时流**，case 尚未终结，此刻声称"系统决定不报警"是
      **不诚实**的（数秒后可能就升级报警）。故 Live 投影 ``suppress_reasons`` 恒为 ``()``，
      渲染层不出现该卡。
    - 此分歧是 ADR-0036 的有意设计（非遗漏）：被本测试 + ``test_live_absent_fields_explicit``
      双重锁死，防止将来有人"好心"给 live 补上负向能力卡。
    """
    acc = ProjectionAccumulator("sess-negcap")
    for f in _sample_frames():
        acc.ingest(f)
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert scn["suppress_reasons"] == ()  # 数据层：刻意空
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    html = render_case_viewer(proj, desc)
    # 用户可见：Live 渲染绝不能出现负向能力卡（与旗舰路径的分歧被钉死）。
    # 注意：CSS 注释里虽含"为什么没有报警"字样，但真实卡片元素用 class="suppress-reason"
    # 标记（CSS 定义为 .suppress-reason {，带点号）；故以 class="suppress-reason" 判定实际卡片。
    assert 'class="suppress-reason"' not in html


def test_build_live_presentation_rejects_empty_projection():
    from home_perception.visualizer.schema.evidence import EvidenceProjection, ProjectionMeta
    empty = EvidenceProjection(meta=ProjectionMeta(generated_at="live", scenario_count=0), scenarios=())
    with pytest.raises(LiveIngestError):
        build_live_presentation(empty)


# —— ADR-0036 Slice C（VM-13 Phase B）：音频增量合并 + AC-9 统一时间轴 + AC-12 ——


def test_ingest_audio_emits_audio_timeline_nodes():
    """摄入音频 → 时间轴含 AUDIO modality 节点，且 audio_kinds 累积（AC-9 / Slice C）。"""
    acc = ProjectionAccumulator("sess-audio")
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))
    acc.ingest_audio(_make_audio(1700000001.0, "audio_distress_cry"))
    scn = acc.to_evidence_projection()["scenarios"][0]
    audio_nodes = [n for n in scn["timeline"] if n["type"] == "audio"]
    assert len(audio_nodes) == 2
    assert all(n["modality"] == "AUDIO" for n in audio_nodes)
    # 音频节点与视觉节点按摄入顺序交错（AC-9：统一时间轴，非三套独立时间轴）。
    types_in_order = [n["type"] for n in scn["timeline"]]
    assert types_in_order[0] == "session"
    assert "audio" in types_in_order and "frame" in types_in_order
    # audio_kinds 累积（去重排序）
    assert acc.audio_kinds == ("audio_distress_cry", "audio_voice_raised")
    assert acc.n_audio == 2


def test_ingest_audio_idempotent():
    """含音频的同一有序流重放 N(≥2) 次，最终 EvidenceProjection 逐字段一致（AC-4b）。"""
    def build():
        a = ProjectionAccumulator("sess-idem", window_size=64)
        a.ingest(_make_frame(0, n_detections=2, event_types=["stranger_loiter"]))
        a.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))
        a.ingest(_make_frame(1, risk_levels=["HIGH"], recommended_actions=["ESCALATE_COMMUNITY"]))
        a.ingest_audio(_make_audio(1700000002.0, "audio_telephone_persistent"))
        return a.to_evidence_projection()
    baseline = _projection_json(build())
    for _ in range(4):
        assert _projection_json(build()) == baseline, "含音频流重放结果不一致（破坏 AC-4b 幂等）"


@pytest.mark.parametrize("replays", [2, 3, 5])
def test_idempotent_replay_with_audio(replays):
    """含音频流重放参数化（对齐 test_idempotent_replay，覆盖音频增量合并路径）。"""
    baselines = None
    for _ in range(replays):
        acc = ProjectionAccumulator("sess-p", window_size=64)
        acc.ingest(_make_frame(0, event_types=["visit_normal"]))
        acc.ingest_audio(_make_audio(1700000000.0, "audio_speech_rapid"))
        acc.ingest_audio(_make_audio(1700000001.0, "audio_anomaly_other"))
        proj = acc.to_evidence_projection()
        dumped = _projection_json(proj)
        if baselines is None:
            baselines = dumped
        else:
            assert dumped == baselines


def test_all_timeline_nodes_carry_modality():
    """AC-9：所有时间轴节点（session/frame/audio）均带 modality 判别，且取值合法。"""
    from home_perception.visualizer.schema.evidence import TimelineModality
    acc = ProjectionAccumulator("sess-mod")
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))
    scn = acc.to_evidence_projection()["scenarios"][0]
    valid = set(TimelineModality.__args__)
    for n in scn["timeline"]:
        assert "modality" in n, "时间轴节点必须带 modality（AC-9）"
        assert n["modality"] in valid, f"modality 越界：{n['modality']}"
    # session + frame 节点 modality=VISION；audio 节点 modality=AUDIO
    by_type = {n["type"]: n["modality"] for n in scn["timeline"]}
    assert by_type["session"] == "VISION"
    assert by_type["frame"] == "VISION"
    assert by_type["audio"] == "AUDIO"


def test_ingest_audio_fail_closed_on_forbidden_fields():
    """VM-9 / AC-10：音频摄入含语义判定/ASR 文本/媒体字节字段 → fail-closed。"""
    acc = ProjectionAccumulator("sess-bad")
    # 语义判定字段
    with pytest.raises(LiveIngestError):
        acc.ingest_audio({**_make_audio(1700000000.0, "audio_voice_raised"), "verdict": "FRAUD"})
    # ASR 文本字段
    with pytest.raises(LiveIngestError):
        acc.ingest_audio({**_make_audio(1700000000.0, "audio_voice_raised"), "transcript": "通话内容"})
    # 媒体字节字段
    with pytest.raises(LiveIngestError):
        acc.ingest_audio({**_make_audio(1700000000.0, "audio_voice_raised"), "raw_audio": "base64..."})


def test_ingest_audio_fail_closed_on_bad_type():
    """音频字段类型非法（score 越界 / timestamp 非法）→ fail-closed。"""
    with pytest.raises(LiveIngestError):
        acc = ProjectionAccumulator("sess-bad2")
        acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", score=1.7))
    with pytest.raises(LiveIngestError):
        acc = ProjectionAccumulator("sess-bad3")
        acc.ingest_audio({"timestamp": {"x": 1}, "kind": "audio_voice_raised",
                          "score": 0.8, "confidence": 0.9, "source_segment_ids": [], "labels": []})


def test_render_live_with_audio_marker():
    """含音频的 Live 投影经 render_case_viewer 复用，时间轴渲染 AUDIO 徽章（AC-9）。"""
    acc = ProjectionAccumulator("sess-render-audio")
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    html = render_case_viewer(proj, desc)
    assert "🔊" in html            # AUDIO modality 徽章
    assert "AUDIO" in html          # modality 标签
    assert "受控演示输入" in html      # AC-7 provenance 一等视觉（音频节点同为 REAL_SENSOR，诚实标注）


# —— ADR-0036 VM-13 Phase B（#509 验证）：Live audio_evidence 投影字段契约 ——

def test_ingest_audio_populates_audio_evidence():
    """摄入真实音频 → audio_evidence 非空、provenance=REAL_SENSOR、ref=live://audio/{idx}。

    这是「Active 截断 Live 音频证据」修正后的核心验收：Live 投影不再恒 ``()``，
    而是把摄入的 REAL_SENSOR 音频感知投影为与 Artifact 共享 schema 的节点（区别仅
    provenance_kind）。
    """
    acc = ProjectionAccumulator("sess-ev")
    acc.ingest_audio(_make_audio(
        1700000000.0, "audio_voice_raised",
        score=0.83, confidence=0.91,
        source_segment_ids=("seg-1",), labels=("raised",),
        event_id="ev-1",
    ))
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert scn["audio_evidence"], "摄入音频后 audio_evidence 必须非空（VM-13 Phase B）"
    node = scn["audio_evidence"][0]
    assert node["provenance_kind"] == "REAL_SENSOR"
    assert node["ref"] == "live://audio/1"
    assert node["kind"] == "audio_voice_raised"
    assert node["score"] == 0.83
    assert node["confidence"] == 0.91
    assert node["labels"] == ("raised",)
    assert node["source_segment_ids"] == ("seg-1",)
    assert node["event_id"] == "ev-1"          # 可选 event_id 透传（溯源/幂等）


def test_audio_evidence_event_id_omitted_when_absent():
    """未提供 event_id → 节点不含 event_id 键（NotRequired，绝不占位编造）。"""
    acc = ProjectionAccumulator("sess-ev2")
    acc.ingest_audio(_make_audio(1700000000.0, "audio_speech_rapid"))
    node = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"][0]
    assert "event_id" not in node


def test_audio_evidence_empty_without_audio():
    """未摄入音频 → audio_evidence 恒 ()（AC-12 / VM-13 6 MUST，绝不编造）。"""
    acc = ProjectionAccumulator("sess-ev3")
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    assert acc.to_evidence_projection()["scenarios"][0]["audio_evidence"] == ()


def test_audio_evidence_idempotent_replay_explicit():
    """含音频流重放 N 次 → audio_evidence 逐字段一致（VM-8 幂等，显式断言节点内容）。"""
    def build():
        a = ProjectionAccumulator("sess-evidem", window_size=64)
        a.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e1"))
        a.ingest_audio(_make_audio(1700000001.0, "audio_telephone_persistent", event_id="e2"))
        return a.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    baseline = build()
    assert len(baseline) == 2
    for _ in range(4):
        cur = build()
        assert cur == baseline, "audio_evidence 重放不一致（破坏 VM-8 幂等）"


def test_live_audio_case_time_track_audio_lane():
    """Step 6：Live 摄入音频 → Case Time 主轴含音频 Lane 标记（相对 T0 确定性排序）。"""
    acc = ProjectionAccumulator("sess-ct", window_size=64)
    acc.ingest_audio(_make_audio(1700000002.0, "audio_speech_rapid", event_id="e2"))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e1"))
    scn = acc.to_evidence_projection()["scenarios"][0]
    tracks = scn["case_time_tracks"]
    assert len(tracks) == 2
    # 按时间确定性排序：e1(t=0) 在前、e2(t=2) 在后
    assert tracks[0]["kind"] == "audio"
    assert tracks[0]["time"] == 0.0
    assert tracks[1]["time"] == 2.0
    assert tracks[0]["ref"] == "live://audio/2"   # ref 用的是（逆序）摄入序号
    # 音频 Lane 标签来自本地映射（不编造）
    assert tracks[0]["label"] == "音高升高"
    # 实时会话无 memory episodes → 显式 absent
    assert scn["memory_episodes"] == ()


def test_render_live_case_time_audio_lane():
    """含音频的 Live 投影渲染出 Case Time 音频 Lane 标记（🔊 · 相对时间）。"""
    acc = ProjectionAccumulator("sess-render-ct")
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e1"))
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    html = render_case_viewer(proj, desc)
    assert "Case Time" in html
    assert "🔊" in html
    assert "音高升高" in html


# —— Gate 4 真实缺陷 #2 回归：audio_evidence 跨滚动窗口持久（不被帧窗口裁掉） ——

def test_audio_evidence_persists_beyond_rolling_window():
    """Gate 4 缺陷 #2 回归：音频证据须跨整个会话持久，不被帧滚动窗口裁掉。

    模拟长循环：先摄入若干音频事件（帧 0..k），再灌入远超 window_size 的帧事件；
    早期音频事件若只存于 _recent_events 滚动窗口会被裁空（audio_evidence 首屏不持久）。
    正确实现应将音频存入独立持久列表，audio_evidence 始终非空；且时间轴 AUDIO 节点同源一致。
    """
    acc = ProjectionAccumulator("sess-persist", window_size=4)
    # 早段摄入 3 条音频（模拟 live_audio_builder 在帧 0..2 喂入）
    for i in range(3):
        acc.ingest_audio(_make_audio(1700000000.0 + i, f"audio_kind_{i}", event_id=f"e{i}"))
    # 灌入远超窗口的帧事件（模拟长循环跑到几百帧）
    for i in range(50):
        acc.ingest(_make_frame(i, n_detections=1, event_types=["visit_normal"]))
    scn = acc.to_evidence_projection()["scenarios"][0]
    # 核心断言：即便滚动窗口早已滚过早期音频，audio_evidence 仍含全部 3 条
    assert len(scn["audio_evidence"]) == 3, scn["audio_evidence"]
    # 时间轴 AUDIO 节点也须一致（不变式：时间轴 ↔ audio_evidence 同源）
    audio_nodes = [n for n in scn["timeline"] if n["type"] == "audio"]
    assert len(audio_nodes) == 3
    # 帧窗口只保留最近 4 帧（独立裁剪），但音频不受影响
    frame_nodes = [n for n in scn["timeline"] if n["type"] == "frame"]
    assert len(frame_nodes) == 4


def test_audio_evidence_dedup_on_repeat_feed():
    """同一音频事件被重复喂入（live 循环重启 frame_index 归零重新喂入）→ 去重，不重复累积（VM-8）。"""
    acc = ProjectionAccumulator("sess-dedup", window_size=64)
    # 模拟两次循环各喂一次相同音频（event_id 相同 → 去重）
    for _ in range(2):
        for i in range(3):
            acc.ingest_audio(_make_audio(1700000000.0 + i, f"audio_kind_{i}", event_id=f"e{i}"))
    scn = acc.to_evidence_projection()["scenarios"][0]
    # 去重后音频证据仍为 3 条，不随重放翻倍
    assert len(scn["audio_evidence"]) == 3
    # 缺 event_id 时回退组合键去重同样生效
    acc2 = ProjectionAccumulator("sess-dedup2", window_size=64)
    for _ in range(2):
        acc2.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))
    assert len(acc2.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1


# ---------------------------------------------------------------------------
# P0 evidence_delta 增量投影（Owner 2026-08-17 拍板）
# ---------------------------------------------------------------------------


def test_evidence_delta_none_prev_returns_full():
    """无基线（服务端首帧）→ 增量 = 全量；浏览器以快照 ref 幂等去重，不重复渲染（VM-8）。"""
    acc = ProjectionAccumulator("sess-delta0", window_size=64)
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e0"))
    delta = acc.extract_evidence_delta(None)
    assert delta["type"] == "evidence_delta"
    # 全量：frame + audio 两类 timeline 节点、1 条 audio 证据
    assert {n["type"] for n in delta["timeline"]} == {"frame", "audio"}
    assert {a["event_id"] for a in delta["audio"]} == {"e0"}
    assert delta["counts"]["n_frames"] == 1


def test_evidence_delta_includes_new_nodes_and_tracks():
    """摄入新帧/音频 → 增量含对应 timeline 节点、audio 证据与 Case Time 标记。"""
    acc = ProjectionAccumulator("sess-delta1", window_size=64)
    acc.ingest(_make_frame(3, n_detections=2, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e1"))
    prev = acc.projection_fingerprint()
    delta = acc.extract_evidence_delta(prev)
    assert delta["timeline"] == []
    assert delta["audio"] == []
    # 再摄入新内容 → 增量出现
    acc.ingest(_make_frame(4, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000001.0, "audio_distress_cry", event_id="e2"))
    delta2 = acc.extract_evidence_delta(prev)
    tl_types = {n["type"] for n in delta2["timeline"]}
    assert "frame" in tl_types and "audio" in tl_types
    audio_ids = {a["event_id"] for a in delta2["audio"]}
    assert audio_ids == {"e2"}
    assert delta2["case_time"] and delta2["case_time"][0]["kind"] == "audio"
    assert delta2["counts"]["n_frames"] == 2
    assert delta2["counts"]["n_audio"] == 2


def test_evidence_delta_idempotent_second_extract_empty():
    """同一指纹连续提取 → 第二次增量全空（去重/幂等，VM-8）。"""
    acc = ProjectionAccumulator("sess-delta2", window_size=64)
    acc.ingest(_make_frame(0, n_detections=1, event_types=["visit_normal"]))
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", event_id="e0"))
    fp0 = acc.projection_fingerprint()
    # fp0 已含全部 → 提取无新增
    d0 = acc.extract_evidence_delta(fp0)
    assert d0["timeline"] == [] and d0["audio"] == [] and d0["case_time"] == []
    # 无基线（None）→ 全量；随后以新指纹提取 → 空
    d1 = acc.extract_evidence_delta(None)
    assert d1["timeline"] and d1["audio"]
    d2 = acc.extract_evidence_delta(acc.projection_fingerprint())
    assert d2["timeline"] == [] and d2["audio"] == [] and d2["case_time"] == []


def test_evidence_delta_audio_node_fields_aligned():
    """delta 的 audio 节点字段与 _build_audio_evidence_live 对齐（防漂移 DRY 债）。"""
    acc = ProjectionAccumulator("sess-delta3", window_size=64)
    acc.ingest_audio(
        _make_audio(
            1700000000.0, "audio_voice_raised", score=0.7, confidence=0.8,
            source_segment_ids=("seg-9",), labels=("raised",), event_id="evt_009",
        )
    )
    d = acc.extract_evidence_delta(None)  # 无基线 → 全量（含该条）
    assert len(d["audio"]) == 1
    a = d["audio"][0]
    assert a["event_id"] == "evt_009"
    assert a["kind"] == "audio_voice_raised"
    assert abs(a["score"] - 0.7) < 1e-6
    assert abs(a["confidence"] - 0.8) < 1e-6
    assert a["source_segment_ids"] == ["seg-9"]
    assert a["labels"] == ["raised"]
    assert a["provenance_kind"] == "REAL_SENSOR"
    assert a["ref"].startswith("live://audio/")
    # 与全量投影同源一致
    full = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"][0]
    assert full["event_id"] == a["event_id"]
    assert full["ref"] == a["ref"]


# ---------------------------------------------------------------------------
# P1-A 实时感知状态流（Live Perception Delta · Owner 2026-08-17 拍板）
# ---------------------------------------------------------------------------


def test_frame_result_to_live_frame_extracts_detections():
    """detections 鸭子类型提取结构化子集（class/bbox/confidence，round 3 位，裁剪上限）。"""
    from home_perception.visualizer.viewer.live_adapter import frame_result_to_live_frame

    dets = [
        {"class_name": "person", "bbox": [1.2345, 2.3456, 100.9999, 200.0001], "confidence": 0.9134},
        {"class_name": "car", "bbox": [0.0, 0.0, 50.0, 50.0], "confidence": 0.55},
    ]
    lf = frame_result_to_live_frame(_make_frame(0, detections=dets))
    assert lf["detections"] == (
        {"class": "person", "bbox": [1.234, 2.346, 101.0, 200.0], "confidence": 0.913},
        {"class": "car", "bbox": [0.0, 0.0, 50.0, 50.0], "confidence": 0.55},
    )


def test_frame_result_to_live_frame_caps_detections():
    """检测数超 _MAX_DETECTIONS → 裁剪（事实投影，非原始 detector 仓库）。"""
    from home_perception.visualizer.viewer.live_adapter import frame_result_to_live_frame

    dets = [
        {"class_name": f"c{i}", "bbox": [0.0, 0.0, 1.0, 1.0], "confidence": 0.9}
        for i in range(20)
    ]
    lf = frame_result_to_live_frame(_make_frame(0, detections=dets))
    assert len(lf["detections"]) <= 8


def test_perception_delta_change_and_idempotent():
    """perception_delta：None→全量；指纹变化→推；未变→空（避免 8fps 全量刷原始框）。"""
    acc = ProjectionAccumulator("sess-pd", window_size=64)
    acc.ingest(_make_frame(0, detections=[{"class_name": "person", "bbox": [0, 0, 10, 10], "confidence": 0.9}]))
    # None（首连）→ 携带当前检测
    d0 = acc.extract_perception_delta(None)
    assert d0["type"] == "perception_delta"
    assert d0["detections"] == [{"class": "person", "bbox": [0.0, 0.0, 10.0, 10.0], "confidence": 0.9}]
    fp = acc.perception_fingerprint()
    # 指纹未变 → 空（不推）
    d1 = acc.extract_perception_delta(fp)
    assert d1["detections"] == []
    # 摄入变化 → 推新检测
    acc.ingest(_make_frame(1, detections=[{"class_name": "car", "bbox": [0, 0, 20, 20], "confidence": 0.8}]))
    d2 = acc.extract_perception_delta(fp)
    assert d2["detections"] == [{"class": "car", "bbox": [0.0, 0.0, 20.0, 20.0], "confidence": 0.8}]
    assert d2["frame_index"] == 1


def test_perception_delta_empty_detections():
    """无检测帧：指纹（空）一致 → 不推；首连 None → 空列表（非报错）。"""
    acc = ProjectionAccumulator("sess-pd2", window_size=64)
    acc.ingest(_make_frame(0))
    d = acc.extract_perception_delta(None)
    assert d["detections"] == []
    assert d["frame_index"] == 0


def test_risk_delta_change_and_idempotent():
    """LP-3 risk_delta：None→全量；指纹变化→推；未变→空（覆盖式"当前 AI 判断"）。

    PR-B：risk_transition 服务端状态机（Owner 锁死 §4.6）——
    空→非空=raised；非空→空=cleared；指纹未变=None（不推信号）。
    """
    acc = ProjectionAccumulator("sess-rd", window_size=64)
    acc.ingest(_make_frame(
        0, risk_levels=["HIGH"], recommended_actions=["ESCALATE_COMMUNITY"],
        reason_summary=["夜间访问", "长时间停留"], command_types=["CREATE_COMMUNITY_TASK"],
    ))
    # None（首连）→ 携带当前风险状态；空→非空 = raised
    d0 = acc.extract_risk_delta(None)
    assert d0["type"] == "risk_delta"
    assert d0["risk_levels"] == ["HIGH"]
    assert d0["reason_summary"] == ["夜间访问", "长时间停留"]
    assert d0["recommended_actions"] == ["ESCALATE_COMMUNITY"]
    assert d0["command_types"] == ["CREATE_COMMUNITY_TASK"]
    assert d0["risk_transition"] == "raised"
    fp = acc.risk_fingerprint()
    # 指纹未变 → 空（不推）；无 transition
    d1 = acc.extract_risk_delta(fp)
    assert d1["risk_levels"] == []
    assert d1["reason_summary"] == []
    assert d1["risk_transition"] is None
    # 风险清除（无 warning 帧）→ 变化 → 推空风险 + cleared（前端只渲染，不猜空=CLEARED）
    acc.ingest(_make_frame(1))
    d2 = acc.extract_risk_delta(fp)
    assert d2["risk_levels"] == []
    assert d2["reason_summary"] == []
    assert d2["frame_index"] == 1
    assert d2["risk_transition"] == "cleared"


def test_risk_delta_transition_active_on_content_change():
    """PR-B：持续非空但内容变化 → active（更新风险内容，非重新亮卡）。"""
    acc = ProjectionAccumulator("sess-rd3", window_size=64)
    acc.ingest(_make_frame(0, risk_levels=["MEDIUM"], recommended_actions=["MONITOR"]))
    fp0 = acc.risk_fingerprint()
    assert acc.extract_risk_delta(None)["risk_transition"] == "raised"
    acc.ingest(_make_frame(1, risk_levels=["HIGH"], recommended_actions=["ESCALATE_COMMUNITY"]))
    d = acc.extract_risk_delta(fp0)
    assert d["risk_levels"] == ["HIGH"]
    assert d["risk_transition"] == "active"


def test_risk_delta_no_warning_initial_monitor():
    """无 warning 帧：首连 risk_delta 为空列表（前端据此显示 MONITOR 继续观察）。

    PR-B：首连无风险 → risk_transition=None（无 transition 不推信号，§4.6 契约表）。
    """
    acc = ProjectionAccumulator("sess-rd2", window_size=64)
    acc.ingest(_make_frame(0))
    d = acc.extract_risk_delta(None)
    assert d["risk_levels"] == []
    assert d["reason_summary"] == []
    assert d["frame_index"] == 0
    assert d["risk_transition"] is None

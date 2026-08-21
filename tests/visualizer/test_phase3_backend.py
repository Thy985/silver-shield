"""Phase 3 · Live Adapter 后端能力补齐（person_present / rms_window / memory_timeline）。

覆盖验收：
- person_present 状态机：持续在场语义（count + duration_s）
- rms_window 连续波形：滑动窗口 20 采样
- memory_timeline 占位：🟡 Partial 阻塞于 Memory API，返回空列表
- extract_perception_delta 含 person_present 字段
- extract_evidence_delta 含 rms_window 字段
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.viewer.live_adapter import ProjectionAccumulator


def _make_frame(frame_index, *, n_detections=0, detections=()):
    return {
        "frame_index": frame_index,
        "n_detections": n_detections,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [],
        "commands": [],
        "detections": list(detections),
    }


def _make_audio(timestamp, kind, *, rms=None):
    d = {
        "timestamp": timestamp,
        "kind": kind,
        "score": 0.8,
        "confidence": 0.9,
        "source_segment_ids": ("seg-1",),
        "labels": ("raised",),
    }
    if rms is not None:
        d["rms"] = rms
    return d


# ------------------------------------------------------------------
# Phase 3.1: person_present 状态机
# ------------------------------------------------------------------

def test_person_present_count_and_duration():
    """有人检测 → count > 0 且 duration_s > 0（持续在场语义）。"""
    acc = ProjectionAccumulator("sess-pp", window_size=64)
    # 无人帧
    d0 = acc.extract_perception_delta(None)
    assert d0["person_present"]["count"] == 0
    assert d0["person_present"]["duration_s"] == 0.0
    # 摄入有人帧
    acc.ingest(_make_frame(0, n_detections=1, detections=[
        {"class_name": "person", "bbox": [0, 0, 10, 10], "confidence": 0.9}
    ]))
    d1 = acc.extract_perception_delta(None)
    assert d1["person_present"]["count"] == 1
    assert d1["person_present"]["duration_s"] >= 0.0
    # 持续在场 → duration 随帧增长
    acc.ingest(_make_frame(1, n_detections=1, detections=[
        {"class_name": "person", "bbox": [0, 0, 10, 10], "confidence": 0.9}
    ]))
    d2 = acc.extract_perception_delta(None)
    assert d2["person_present"]["count"] == 1
    assert d2["person_present"]["duration_s"] > d1["person_present"]["duration_s"]


def test_person_present_zero_when_absent():
    """无人检测 → count == 0，duration_s == 0.0。"""
    acc = ProjectionAccumulator("sess-pp-absent", window_size=64)
    acc.ingest(_make_frame(0, n_detections=1, detections=[
        {"class_name": "car", "bbox": [0, 0, 20, 20], "confidence": 0.7}
    ]))
    d = acc.extract_perception_delta(None)
    assert d["person_present"]["count"] == 0
    assert d["person_present"]["duration_s"] == 0.0


def test_person_present_transition_count():
    """有人→无人跃迁 → count 归零；无人→有人跃迁 → count 重新计数。"""
    acc = ProjectionAccumulator("sess-pp-trans", window_size=64)
    # 有人
    acc.ingest(_make_frame(0, n_detections=1, detections=[
        {"class_name": "person", "bbox": [0, 0, 10, 10], "confidence": 0.9}
    ]))
    d_has = acc.extract_perception_delta(None)
    assert d_has["person_present"]["count"] == 1
    # 无人
    acc.ingest(_make_frame(1, n_detections=0))
    d_none = acc.extract_perception_delta(None)
    assert d_none["person_present"]["count"] == 0
    # 再次有人
    acc.ingest(_make_frame(2, n_detections=1, detections=[
        {"class_name": "person", "bbox": [0, 0, 10, 10], "confidence": 0.85}
    ]))
    d_has2 = acc.extract_perception_delta(None)
    assert d_has2["person_present"]["count"] == 1
    # duration 应从第二次出现开始计算（start_ts 重置）
    assert d_has2["person_present"]["duration_s"] < d_has["person_present"]["duration_s"] + 1.0


def test_person_present_in_perception_delta():
    """extract_perception_delta 返回值必须含 person_present 字段。"""
    acc = ProjectionAccumulator("sess-pp-field", window_size=64)
    acc.ingest(_make_frame(0, n_detections=0))
    d = acc.extract_perception_delta(None)
    assert "person_present" in d
    assert isinstance(d["person_present"], dict)
    assert "count" in d["person_present"]
    assert "duration_s" in d["person_present"]


# ------------------------------------------------------------------
# Phase 3.2: rms_window 连续波形
# ------------------------------------------------------------------

def test_rms_window_accumulates_from_audio():
    """ingest_audio 含 rms → _rms_window 非空，长度等于音频数。"""
    acc = ProjectionAccumulator("sess-rms", window_size=64)
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised", rms=0.45))
    acc.ingest_audio(_make_audio(1700000001.0, "audio_distress_cry", rms=0.72))
    delta = acc.extract_evidence_delta(None)
    assert "rms_window" in delta
    assert len(delta["rms_window"]) == 2
    assert delta["rms_window"][0] == pytest.approx(0.45)
    assert delta["rms_window"][1] == pytest.approx(0.72)


def test_rms_window_sliding_window():
    """超过 _rms_window_size(20) 个采样 → 只保留最近 20 个。"""
    acc = ProjectionAccumulator("sess-rms-sw", window_size=64)
    for i in range(25):
        acc.ingest_audio(_make_audio(1700000000.0 + i, f"audio_kind_{i}", rms=float(i)))
    delta = acc.extract_evidence_delta(None)
    assert len(delta["rms_window"]) == 20
    assert delta["rms_window"][0] == pytest.approx(5.0)   # 25-20=5
    assert delta["rms_window"][-1] == pytest.approx(24.0)


def test_rms_window_in_evidence_delta():
    """extract_evidence_delta 返回值必须含 rms_window 字段（即使为空）。"""
    acc = ProjectionAccumulator("sess-rms-field", window_size=64)
    acc.ingest(_make_frame(0, n_detections=0))
    delta = acc.extract_evidence_delta(None)
    assert "rms_window" in delta
    assert delta["rms_window"] == []


def test_rms_window_ignores_missing_rms():
    """音频不含 rms 字段 → 不追加（窗口不膨胀）。"""
    acc = ProjectionAccumulator("sess-rms-no-rms", window_size=64)
    acc.ingest_audio(_make_audio(1700000000.0, "audio_voice_raised"))  # 无 rms
    acc.ingest_audio(_make_audio(1700000001.0, "audio_distress_cry", rms=0.5))
    delta = acc.extract_evidence_delta(None)
    assert len(delta["rms_window"]) == 1
    assert delta["rms_window"][0] == pytest.approx(0.5)


# ------------------------------------------------------------------
# Phase 3.3: memory_timeline
# ------------------------------------------------------------------

def test_memory_timeline_returns_empty_placeholder():
    """当前阻塞于 Memory API → extract_memory_timeline 返回空 episodes 列表。"""
    acc = ProjectionAccumulator("sess-mem", window_size=64)
    result = acc.extract_memory_timeline()
    assert result["type"] == "memory_timeline"
    assert result["episodes"] == []


def test_memory_timeline_no_production_imports():
    """extract_memory_timeline 不得 import memory/runtime/silver_demo（AC-5）。"""
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / \
        "src" / "home_perception" / "visualizer" / "viewer" / "live_adapter.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = (
        "home_perception.memory",
        "home_perception.runtime",
        "silver_demo",
    )
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for m in modules:
            for f in forbidden:
                assert not m.startswith(f), f"禁止 import {f}，发现 {m}"
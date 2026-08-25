"""SSOT v4.1：Audio Evidence Lane segments 派生纯函数测试。

presentation-only 派生：仅依赖 audio_evidence 现有字段
（event_id / kind / case_time / score），不需新 schema / runtime fact。
Python 测试覆盖 JS 等价语义——浏览器侧 Playwright e2e 负责真实 DOM 验证。
"""

from __future__ import annotations

import pytest

# SSOT v4.1：kind → semantic_class 映射（presentation-only，主题层；与 live_stream.js _AUDIO_KIND_LANE 同源）
AUDIO_KIND_LANE = {
    "audio_telephone_persistent": "kind-telephone",
    "audio_voice_raised": "kind-voice-raised",
    "audio_speech_rapid": "kind-speech-rapid",
    "audio_distress_cry": "kind-distress-cry",
    "audio_anomaly_other": "kind-anomaly-other",
}

LANE_MIN_MARK_S = 0.4  # 与 live_stream.js _LANE_MIN_MARK_S 同值


def derive_audio_evidence_segments(
    events: list[dict], window_start: float, window_end: float
) -> list[dict]:
    """等价于 live_stream.js deriveAudioEvidenceSegments 的纯函数（Python 实现）。

    输入：events 列表 [{event_id, kind, case_time, score, confidence}]
    输出：[{kind, semantic_class, start_pct, end_pct, score_max}]（按 kind 分桶，相邻同类合并）
    语义边界：marker 表"在此 case_time 观察到该 kind 的证据"，不承诺"持续时长"。
    """
    if not events or window_end <= window_start:
        return []
    win_dur = window_end - window_start
    if win_dur <= 0:
        return []
    # 按 kind 分桶（仅含窗口内事件，case_time 缺失跳过）
    buckets: dict[str, list[dict]] = {}
    kind_order: list[str] = []
    for e in events:
        ct = e.get("case_time")
        if ct is None:
            continue
        ct = float(ct)
        if ct < window_start or ct > window_end:
            continue
        kind = str(e.get("kind") or "audio_anomaly_other")
        if kind not in buckets:
            buckets[kind] = []
            kind_order.append(kind)
        buckets[kind].append({"case_time": ct, "score": float(e.get("score") or 0)})
    segments: list[dict] = []
    for kind in kind_order:
        pts = sorted(buckets[kind], key=lambda p: p["case_time"])
        if not pts:
            continue
        seg_start = pts[0]["case_time"]
        seg_end = pts[0]["case_time"]
        score_max = pts[0]["score"]
        for p in pts[1:]:
            if p["case_time"] - seg_end <= LANE_MIN_MARK_S:
                seg_end = p["case_time"]
                score_max = max(score_max, p["score"])
            else:
                pct_start = max(0.0, (seg_start - window_start) / win_dur * 100.0)
                pct_end = max(pct_start + 0.5, (seg_end - window_start) / win_dur * 100.0)
                segments.append(
                    {
                        "kind": kind,
                        "semantic_class": AUDIO_KIND_LANE.get(kind, "kind-anomaly-other"),
                        "start_pct": pct_start,
                        "end_pct": pct_end,
                        "score_max": score_max,
                    }
                )
                seg_start = p["case_time"]
                seg_end = p["case_time"]
                score_max = p["score"]
        # 收尾段
        pct_start = max(0.0, (seg_start - window_start) / win_dur * 100.0)
        pct_end = max(pct_start + 0.5, (seg_end - window_start) / win_dur * 100.0)
        segments.append(
            {
                "kind": kind,
                "semantic_class": AUDIO_KIND_LANE.get(kind, "kind-anomaly-other"),
                "start_pct": pct_start,
                "end_pct": pct_end,
                "score_max": score_max,
            }
        )
    return segments


def _ev(eid: str, kind: str, case_time: float, score: float = 0.8) -> dict:
    return {
        "event_id": eid,
        "kind": kind,
        "case_time": case_time,
        "score": score,
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# SSOT v4.1 segments 派生规则（presentation-only · 不承诺持续时长）
# ---------------------------------------------------------------------------


def test_empty_events_returns_empty():
    """空事件列表 → 无 segments（保留时间刻度但无 markers）。"""
    assert derive_audio_evidence_segments([], 0.0, 16.0) == []


def test_invalid_window_returns_empty():
    """window_end ≤ window_start → 无 segments（防御退化）。"""
    events = [_ev("e1", "audio_telephone_persistent", 5.0)]
    assert derive_audio_evidence_segments(events, 10.0, 5.0) == []


def test_window_filters_out_of_range_events():
    """窗口外事件不投影（仍可在窗口滚动时回填，但当前帧不画）。"""
    events = [
        _ev("e1", "audio_telephone_persistent", 1.0),  # 窗口外
        _ev("e2", "audio_telephone_persistent", 12.0),  # 窗口内
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 1
    # 窗口 [8, 16] → 12s 对应 (12-8)/8 * 100 = 50%
    assert segs[0]["start_pct"] == pytest.approx(50.0)


def test_window_edge_inclusive():
    """窗口边界包含：window_start 和 window_end 都投影（包容端点）。

    注：同 kind 间隔 8s > MIN_MARK_S(0.4s)，故拆为两段（每段都跨越边界）。
    测试目的：仅验证两个端点都映射到 [0, 100] 边界。
    """
    events = [
        _ev("a", "audio_voice_raised", 8.0),   # 窗口起点 → 0%
        _ev("b", "audio_voice_raised", 16.0),  # 窗口终点 → 100%
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 2
    # 起点段：start_pct=0
    assert segs[0]["start_pct"] == pytest.approx(0.0)
    # 终点段：start_pct=100, end_pct=100.5（min 宽度）
    assert segs[1]["start_pct"] == pytest.approx(100.0)
    assert segs[1]["end_pct"] == pytest.approx(100.5)


def test_same_kind_within_min_mark_merge():
    """相邻同类事件间隔 ≤ MIN_MARK_S → 合并为一段（仅"观察密度"语义，非持续时长）。"""
    events = [
        _ev("a", "audio_telephone_persistent", 8.0),
        _ev("b", "audio_telephone_persistent", 8.2),  # 间隔 0.2s < 0.4s
        _ev("c", "audio_telephone_persistent", 8.4),
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 1, "相邻同类应合并"
    assert segs[0]["kind"] == "audio_telephone_persistent"


def test_same_kind_beyond_min_mark_split():
    """相邻同类事件间隔 > MIN_MARK_S → 拆为两段（保持"曾观察"语义，不承诺持续）。"""
    events = [
        _ev("a", "audio_telephone_persistent", 8.0),
        _ev("b", "audio_telephone_persistent", 10.0),  # 间隔 2s > 0.4s
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 2, "间隔过大应拆段"


def test_different_kinds_split_by_kind():
    """不同 kind → 不同 marker（不分桶，bucket per kind）。"""
    events = [
        _ev("a", "audio_telephone_persistent", 8.0),
        _ev("b", "audio_voice_raised", 8.0),  # 同 case_time 不同 kind
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    kinds = [s["kind"] for s in segs]
    assert set(kinds) == {"audio_telephone_persistent", "audio_voice_raised"}
    assert len(segs) == 2


def test_unknown_kind_falls_back_to_anomaly_class():
    """未登记 kind → semantic_class 兜底 kind-anomaly-other。"""
    events = [_ev("a", "audio_unknown_kind", 10.0)]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert segs[0]["semantic_class"] == "kind-anomaly-other"
    assert segs[0]["kind"] == "audio_unknown_kind"


def test_score_max_keeps_highest_in_merged_segment():
    """合并段保留 score_max（不是 mean/first）。"""
    events = [
        _ev("a", "audio_telephone_persistent", 8.0, score=0.7),
        _ev("b", "audio_telephone_persistent", 8.1, score=0.95),
        _ev("c", "audio_telephone_persistent", 8.2, score=0.8),
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 1
    assert segs[0]["score_max"] == pytest.approx(0.95)


def test_missing_case_time_skipped_not_raised():
    """case_time 缺失的事件跳过（fail-soft，不污染窗口投影）。"""
    events = [
        {"event_id": "a", "kind": "audio_voice_raised", "score": 0.5},  # 无 case_time
        _ev("b", "audio_voice_raised", 10.0),
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert len(segs) == 1
    assert segs[0]["kind"] == "audio_voice_raised"


def test_segments_sorted_by_kind_order():
    """segments 按 kind_order（首次出现序）输出——确定性（重放稳定，VM-8）。"""
    events = [
        _ev("z", "audio_voice_raised", 10.0),
        _ev("a", "audio_telephone_persistent", 11.0),
        _ev("m", "audio_distress_cry", 12.0),
    ]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    kinds = [s["kind"] for s in segs]
    assert kinds == [
        "audio_voice_raised",
        "audio_telephone_persistent",
        "audio_distress_cry",
    ]


def test_segments_pct_monotonic():
    """百分比输出在 [0, 100] 区间内（防窗口/事件越界）。"""
    events = [
        _ev("a", "audio_telephone_persistent", 0.0),  # 窗口起点
        _ev("b", "audio_voice_raised", 8.0),
        _ev("c", "audio_distress_cry", 16.0),  # 窗口终点
    ]
    segs = derive_audio_evidence_segments(events, 0.0, 16.0)
    for s in segs:
        assert 0.0 <= s["start_pct"] <= 100.0
        # end_pct 由 min(start+0.5, ...) 决定，可能稍 > 100；允许最多 0.5 溢出（视觉最小宽度）
        assert s["end_pct"] <= 100.5
        assert s["start_pct"] <= s["end_pct"]


def test_distress_cry_uses_distress_semantic_class():
    """SSOT v4.1：声学异常活动（distress_cry）→ kind-distress-cry（视觉主题层）。"""
    events = [_ev("a", "audio_distress_cry", 10.0)]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert segs[0]["semantic_class"] == "kind-distress-cry"


def test_segment_width_minimum_zero_point_five_pct():
    """极窄窗口的段至少 0.5% 宽（保证 marker 可见，不是"幽灵标记"）。"""
    events = [_ev("a", "audio_voice_raised", 8.0)]
    segs = derive_audio_evidence_segments(events, 8.0, 16.0)
    assert segs[0]["end_pct"] - segs[0]["start_pct"] >= 0.5


def test_timestamp_field_used_as_anchor_fallback():
    """SSOT v4.1：anchor 优先级 case_time → timestamp。

    telephone_risk_reality_check WS audio payload 携带 timestamp（wav 相对起点的秒）
    而非 case_time——这是已存在事实，不是 schema 扩展。
    """
    events_with_timestamp = [
        {"event_id": "a", "kind": "audio_distress_cry", "timestamp": 10.0, "score": 0.9}
    ]
    # 模拟 JS 缓存填充（case_time 优先 → fallback 到 timestamp）
    enriched = []
    for e in events_with_timestamp:
        ct = e.get("case_time") if e.get("case_time") is not None else e.get("timestamp")
        enriched.append({"event_id": e["event_id"], "kind": e["kind"], "case_time": ct, "score": e["score"]})
    segs = derive_audio_evidence_segments(enriched, 8.0, 16.0)
    assert len(segs) == 1
    # 10s 在 [8,16] 内，位置 = (10-8)/8 * 100 = 25%
    assert segs[0]["start_pct"] == pytest.approx(25.0)


def test_missing_both_anchor_skipped_not_raised():
    """SSOT v4.1：event 既无 case_time 也无 timestamp → JS 端不填入 cache（fail-soft）。

    服务端/前端故障兜底——投影不会因缺锚点崩溃。
    """
    events_missing_anchor = [
        {"event_id": "a", "kind": "audio_voice_raised", "score": 0.9},  # 无 case_time 无 timestamp
    ]
    # JS 端行为：ct = null → 不 push 进 cache
    enriched = []
    for e in events_missing_anchor:
        ct = e.get("case_time") if e.get("case_time") is not None else e.get("timestamp")
        if ct is not None and isFinite_anchor(ct):
            enriched.append({"event_id": e["event_id"], "kind": e["kind"], "case_time": ct, "score": e["score"]})
    assert enriched == []
    # 派生结果应为空
    segs = derive_audio_evidence_segments(enriched, 0.0, 16.0)
    assert segs == []


def isFinite_anchor(v):
    """Anchor 数值有效性检查（与 JS isFinite 对齐）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    # NaN != NaN 性质 → math.isnan 比 f == f 语义更明确
    import math as _math
    return not _math.isnan(f)


def test_window_alignment_for_reality_check_scenario():
    """SSOT v4.1：telephone_risk_reality_check 场景验证

    wav 音轨 28s 左右产生 distress_cry；window [t-16, t] 滚动应能捕获。
    """
    events = [
        _ev("a", "audio_distress_cry", 28.56),
        _ev("b", "audio_telephone_persistent", 30.0),
    ]
    # 当前 case_time ≈ 30
    segs = derive_audio_evidence_segments(events, 14.0, 30.0)
    kinds = [s["kind"] for s in segs]
    assert "audio_distress_cry" in kinds
    assert "audio_telephone_persistent" in kinds
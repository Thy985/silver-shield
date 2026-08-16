"""ADR-0036 P1 · Memory Timeline 嵌入 Case Time（音频 + 记忆 双 Lane，VM-1 纯展示）。

验证「记忆自然嵌进统一时间轴」的展示层实现：
- 双 Lane 结构：``.case-time-axis`` 内 ``音频 Lane（🔊，上）`` 与 ``记忆 Lane（🧠，下）``
  共享同一游标；记忆因此成为统一时间轴的一部分，而非孤立面板；
- P0-3 回放 JS 契约不变：所有标记仍在单一 ``#case-time-track-{sid}`` 内、
  ``#case-time-cursor-{sid}`` 唯一、``querySelectorAll('.case-time-mark')`` 拿全；
- Lane 标签仅在对应事件存在时显示（避免空行噪音）；零新数据（仅用既有
  ``case_time_tracks`` 字段，VM-1 不编造）。

所有测试 hermetic、不依赖真实闭环 / 模型，CI 与本地一致、快速、可复现。
"""

from __future__ import annotations

from home_perception.visualizer.viewer.render import _render_case_time_tracks


def _scenario(sid: str, tracks):
    return {"scenario_id": sid, "case_time_tracks": tuple(tracks)}


def _track(time, kind, label="x"):
    return {"time": float(time), "kind": kind, "ref": "", "label": label}


# ---------------------------------------------------------------------------
# 1. 空 / 契约
# ---------------------------------------------------------------------------


def test_empty_tracks_returns_empty_string():
    assert _render_case_time_tracks(_scenario("S1", [])) == ""


def test_single_track_and_cursor_ids_preserved():
    # P0-3 回放 JS 契约：单 #case-time-track-{sid} + 单 #case-time-cursor-{sid}。
    html = _render_case_time_tracks(
        _scenario("S1", [_track(0.0, "audio", "a1"), _track(1.0, "memory", "m1")])
    )
    assert html.count('id="case-time-track-S1"') == 1
    assert html.count('id="case-time-cursor-S1"') == 1


def test_all_marks_in_single_track_for_replay():
    # 回放遍历 querySelectorAll('.case-time-mark') 须拿到全部标记（含 memory）。
    html = _render_case_time_tracks(
        _scenario(
            "S1",
            [_track(0.0, "audio", "a1"), _track(1.0, "memory", "m1"), _track(2.0, "audio", "a2")],
        )
    )
    assert html.count('class="case-time-mark') == 3
    assert html.count('id="case-time-track-') == 1


# ---------------------------------------------------------------------------
# 2. 双 Lane 结构
# ---------------------------------------------------------------------------


def test_audio_and_memory_mark_classes_present():
    html = _render_case_time_tracks(
        _scenario("S1", [_track(0.0, "audio", "a1"), _track(1.0, "memory", "m1")])
    )
    assert 'class="case-time-mark mark-audio"' in html
    assert 'class="case-time-mark mark-memory"' in html


def test_lane_tags_for_both_kinds():
    html = _render_case_time_tracks(
        _scenario("S1", [_track(0.0, "audio", "a1"), _track(1.0, "memory", "m1")])
    )
    assert "lane-tag-audio" in html
    assert "lane-tag-memory" in html


def test_lane_tag_audio_only():
    html = _render_case_time_tracks(_scenario("S1", [_track(0.0, "audio", "a1")]))
    assert "lane-tag-audio" in html
    assert "lane-tag-memory" not in html


def test_lane_tag_memory_only():
    html = _render_case_time_tracks(_scenario("S1", [_track(0.0, "memory", "m1")]))
    assert "lane-tag-memory" in html
    assert "lane-tag-audio" not in html


# ---------------------------------------------------------------------------
# 3. VM-1：零新数据（标记 data-label 来自既有 case_time_tracks[].label）
# ---------------------------------------------------------------------------


def test_memory_label_not_fabricated_from_other_fields():
    html = _render_case_time_tracks(_scenario("S1", [_track(1.0, "memory", "历史模式重复")]))
    assert "历史模式重复" in html
    assert "mark-memory" in html

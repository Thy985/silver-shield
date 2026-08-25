"""P0-2 Case Time 统一主轴契约测试（产品化总原则 §3）。

覆盖：
- loader ``_build_case_time_tracks``：音频/记忆相对最早证据 T0、prior 排除、确定性排序、
  负时间（历史背景）丢弃、无事件恒 ()；
- render Case Time 组件：事件标记渲染（mark-audio/mark-memory）、无事件空、
  __caseTime 引擎注入（media.js 全局）；
- media.js ``__caseTime`` 行为（node vm）：点击音频标记 → 游标移动 + play 样本 +
  高亮卡片；记忆标记 → 滚动记忆面板；无目标 no-op。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from home_perception.visualizer.loader import _build_case_time_tracks
from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation

from .conftest import make_artifacts

_AUDIO = [
    {
        "audio_timestamp": 1752952800.0,
        "audio_kind": "audio_telephone_persistent",
        "audio_score": 0.9,
        "audio_confidence": 0.9,
        "audio_labels": ["telephone"],
        "audio_source_segment_ids": ["seg-0"],
    },
    {
        "audio_timestamp": 1752952800.0 + 150.0,
        "audio_kind": "audio_voice_raised",
        "audio_score": 0.85,
        "audio_confidence": 0.88,
        "audio_labels": ["speech"],
        "audio_source_segment_ids": ["seg-1"],
    },
]

# 记忆：2 条非 prior（本次会话）+ 1 条 prior（历史，不应进主轴）。
_MEMORY = [
    {
        "memory_record_id": "ep-curr-001",
        "memory_timestamp": "2025-07-19T19:22:30+00:00",  # T0+150s
        "memory_risk_level": "LOW",
        "memory_recommended_action": "MONITOR",
        "memory_summary": "本次会话 episode A",
        "memory_reason_summary": [],
        "memory_command_types": [],
        "memory_prior": False,
    },
    {
        "memory_record_id": "ep-prior-historical_001",
        "memory_timestamp": "2025-07-16T19:20:00+00:00",  # 3 天前（历史）
        "memory_risk_level": "LOW",
        "memory_recommended_action": "MONITOR",
        "memory_summary": "历史 episode",
        "memory_reason_summary": [],
        "memory_command_types": [],
        "memory_prior": True,
    },
    {
        "memory_record_id": "ep-curr-002",
        "memory_timestamp": "2025-07-19T19:21:00+00:00",  # T0+60s
        "memory_risk_level": "MEDIUM",
        "memory_recommended_action": "NOTIFY_FAMILY",
        "memory_summary": "本次会话 episode B",
        "memory_reason_summary": [],
        "memory_command_types": ["LOG_ONLY"],
        "memory_prior": False,
    },
]


def _audio_nodes():
    """直接构造 AudioEvidenceNode（timestamp float Unix 秒）。"""
    return (
        {
            "timestamp": "1752952800.0",
            "kind": "audio_telephone_persistent",
            "score": 0.9,
            "confidence": 0.9,
            "labels": (),
            "source_segment_ids": (),
            "ref": "sw_t1.canonical.json#artifacts.audio_evidence[0]",
            "provenance_kind": "SIMULATED",
        },
        {
            "timestamp": "1752952950.0",  # +150s
            "kind": "audio_voice_raised",
            "score": 0.85,
            "confidence": 0.88,
            "labels": (),
            "source_segment_ids": (),
            "ref": "sw_t1.canonical.json#artifacts.audio_evidence[1]",
            "provenance_kind": "SIMULATED",
        },
    )


# ---------------------------------------------------------------------------
# 1. loader：_build_case_time_tracks
# ---------------------------------------------------------------------------


def test_tracks_audio_and_current_memory_relative_to_t0():
    """音频 + 本次会话记忆 → 相对最早音频 T0；prior 历史排除；确定性排序。"""
    audio = _audio_nodes()
    memory = [
        {
            "record_id": "ep-curr-001",
            "timestamp": "2025-07-19T19:22:30+00:00",
            "risk_level": "LOW",
            "recommended_action": "MONITOR",
            "summary": "本次会话 episode A",
            "reason_summary": (),
            "command_types": (),
            "prior": False,
        },
        {
            "record_id": "ep-prior-historical_001",
            "timestamp": "2025-07-16T19:20:00+00:00",
            "risk_level": "LOW",
            "recommended_action": "MONITOR",
            "summary": "历史 episode",
            "reason_summary": (),
            "command_types": (),
            "prior": True,
        },
        {
            "record_id": "ep-curr-002",
            "timestamp": "2025-07-19T19:21:00+00:00",
            "risk_level": "MEDIUM",
            "recommended_action": "NOTIFY_FAMILY",
            "summary": "本次会话 episode B",
            "reason_summary": (),
            "command_types": (),
            "prior": False,
        },
    ]
    tracks = _build_case_time_tracks(tuple(audio), tuple(memory), "sw_t1")
    # T0 = 最早音频（0s）；事件：audio(0,150) + memory curr-002(60) + curr-001(150)
    assert [t["time"] for t in tracks] == [0.0, 60.0, 150.0, 150.0]
    assert all(t["kind"] in ("audio", "memory") for t in tracks)
    # prior 历史不进主轴（ep-prior-* 无对应标记）。
    assert not any("prior" in t["label"] for t in tracks)
    # 排序确定性：(time, kind, ref, label)。tuple vs sorted 结果（list）逐元素比较。
    assert list(tracks) == sorted(
        tracks, key=lambda t: (t["time"], t["kind"], t["ref"], t["label"])
    )


def test_tracks_no_events_empty():
    """无音频无记忆 → 恒 ()（AC-12 不编造）。"""
    assert _build_case_time_tracks((), (), "sw_t1") == ()


def test_tracks_only_prior_memory_empty():
    """只有 prior 历史 → 恒 ()（历史不进当前 Case Time 主轴）。"""
    memory = (
        {
            "record_id": "ep-prior-historical_001",
            "timestamp": "2025-07-16T19:20:00+00:00",
            "risk_level": "LOW",
            "recommended_action": "MONITOR",
            "summary": "历史 episode",
            "reason_summary": (),
            "command_types": (),
            "prior": True,
        },
    )
    assert _build_case_time_tracks((), memory, "sw_t1") == ()


# ---------------------------------------------------------------------------
# 2. render：Case Time 组件
# ---------------------------------------------------------------------------


def test_render_case_time_tracks_marks(tmp_path):
    """有事件 → 渲染 mark-audio/mark-memory 标记 + case-time 主轴 + 游标。"""
    canon = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO, memory_episodes=_MEMORY)
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    assert 'class="case-time-track"' in html
    assert "mark-audio" in html
    assert "mark-memory" in html
    assert "case-time-cursor" in html
    assert "Case Time（事件时间" in html
    assert "__caseTime" in html  # media.js 引擎


def test_render_no_events_no_case_time(tmp_path):
    """无音频无记忆 → 不渲染 Case Time 主轴（零成本；media.js 引擎字符串不算组件）。"""
    canon = make_artifacts(tmp_path / "a")
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    assert 'id="case-time-track-' not in html  # 无 case-time 组件（媒体引擎字符串另计）
    assert "Case Time（事件时间" not in html


def test_render_case_time_mark_no_quote_corruption(tmp_path):
    """缺陷 #3 回归：data-label 用 HTML 属性层转义（无 JSON 引号）；onclick 不内联 label
    （data-driven：this.getAttribute('data-label')），不再被裸引号截断 → 点击不再抛 SyntaxError。"""
    import re

    canon = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO, memory_episodes=_MEMORY)
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    # 提取所有 mark 的 onclick / data-label（正则防误命中 media.js 引擎字符串）
    marks = re.findall(
        r'<span class="case-time-mark (?:mark-audio|mark-memory)"[^>]*>',
        html,
    )
    assert marks, "应渲染 case-time 标记"
    for m in marks:
        # 1) data-label 值必须干净（不含多余引号：旧 bug 是 data-label=""哭腔/求助""）
        dl = re.search(r'data-label="([^"]*)"', m)
        assert dl, f"mark 缺 data-label: {m[:120]}"
        assert not dl.group(1).startswith('"'), f"data-label 被引号污染: {dl.group(1)!r}"
        # 2) onclick 必须 data-driven（含 this.getAttribute('data-label')），不得内联裸 label
        oc = re.search(r'onclick="([^"]*)"', m)
        assert oc, f"mark 缺 onclick: {m[:120]}"
        assert "this.getAttribute('data-label')" in oc.group(1), f"onclick 未 data-driven: {oc.group(1)!r}"
        assert "window.__caseTime(" in oc.group(1)


# ---------------------------------------------------------------------------
# 3. media.js __caseTime 行为（node vm）
# ---------------------------------------------------------------------------


def _media_source() -> str:
    from home_perception.visualizer.renderer import _media_inline

    src = _media_inline()
    assert src, "media.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_case_time_js_audio_mark_plays_and_moves_cursor():
    """Node vm：点击音频标记 → 游标移动 + play 样本 + 高亮卡片；记忆 → 滚动面板。"""
    import os
    import subprocess
    import tempfile
    import textwrap

    src = _media_source()
    harness = textwrap.dedent(
        """
        const fs = require('fs');
        const cursor = { style: {} };
        const track = { getAttribute: function(k) { return k === 'data-max' ? '150.0' : null; } };
        const audioEl = { _played: false, play: function() { this._played = true; } };
        const card = { classList: { add: function(c){ card._added = c; }, remove: function(){} } };
        const memPanel = { _scrolled: false, scrollIntoView: function() { this._scrolled = true; },
                          querySelectorAll: function() { return []; } };
        const doc = {
          getElementById: function(id) {
            if (id === 'case-time-track-sw_t1') return track;
            if (id === 'case-time-cursor-sw_t1') return cursor;
            if (id === 'audio-audio_telephone_persistent') return audioEl;
            if (id === 'fs-memory-timeline-sw_t1') return memPanel;
            return null;
          },
          querySelectorAll: function(sel) {
            // 缺陷 #3 修复后：卡片高亮改遍历 .audio-card 比较 data-kind（不再拼接选择器）。
            if (sel === '.audio-card') return [card];
            return [];
          },
        };
        const cardEl = card;
        cardEl.getAttribute = function(k) { return k === 'data-kind' ? 'audio_telephone_persistent' : null; };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        // 音频标记：游标移动 + play + 高亮（data-kind 命中 label）
        global.__caseTime('sw_t1', 'audio', 'audio_telephone_persistent', 0.0);
        const audioOk = cursor.style.left === '0%' && audioEl._played && card._added === 'audio-card-active';
        // 记忆标记：滚动面板（无 mem 卡 no-op 不崩）
        global.__caseTime('sw_t1', 'memory', '', 60.0);
        const memOk = memPanel._scrolled && cursor.style.left !== '';
        // 未知 kind no-op 不崩
        let noopOk = true;
        try { global.__caseTime('sw_t1', 'unknown', 'x', 1.0); } catch (e) { noopOk = false; }
        console.log(audioOk && memOk && noopOk ? 'CASE_TIME_OK' : 'CASE_TIME_FAIL');
        process.exit(audioOk && memOk && noopOk ? 0 : 1);
        """
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        harness_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        src_path = f.name
    r = subprocess.run(
        ["node", harness_path, src_path],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        check=False,
    )
    os.unlink(harness_path)
    os.unlink(src_path)
    assert r.returncode == 0, f"__caseTime 行为失败: {r.stdout} {r.stderr}"
    assert "CASE_TIME_OK" in r.stdout

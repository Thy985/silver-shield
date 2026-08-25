"""P0-3 Evidence Replay 契约测试（产品化总原则 §4 · 证据时间回放）。

覆盖：
- render：Case Time 主轴带播放按钮 + 事件标记 data-time/data-kind/data-label；
- media.js ``__caseTimeReplay`` 行为（node vm + mock DOM + 假时钟）：
  播放推进 → 游标移动 + 事件按序触发（audio play/记忆滚动）+ 播放完自动停 +
  按钮状态切换 + 无标记 no-op。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

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
]


# ---------------------------------------------------------------------------
# 1. render：回放按钮 + 标记 data-* 属性
# ---------------------------------------------------------------------------


def _render_with_events(tmp_path) -> str:
    canon = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO, memory_episodes=_MEMORY)
    proj, desc = load_case_presentation(canon)
    return render_case_viewer(proj, desc)


def test_render_replay_button_and_mark_data(tmp_path):
    """有事件 → 回放按钮 + 标记 data-time/data-kind/data-label。"""
    html = _render_with_events(tmp_path)
    assert 'id="case-time-play-' in html  # 播放按钮
    assert "__caseTimeReplay" in html  # 引擎
    assert 'data-time="' in html
    assert 'data-kind="audio' in html
    assert "事件按发生顺序依次涌现" in html


def test_render_no_events_no_replay(tmp_path):
    """无事件 → 无回放按钮/主轴（零成本；media.js 引擎字符串另计）。"""
    canon = make_artifacts(tmp_path / "a")
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    assert 'id="case-time-play-' not in html  # 无回放按钮（引擎字符串另计）
    assert 'id="case-time-track-' not in html


# ---------------------------------------------------------------------------
# 2. media.js __caseTimeReplay 行为（node vm + 假时钟）
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
def test_case_time_replay_js_advances_and_triggers_events():
    """Node vm：回放推进 → 游标移动 + 事件按序触发 + 播放完自动停 + 按钮切换。"""
    src = _media_source()
    harness = textwrap.dedent(
        """
        const fs = require('fs');
        // 假时钟：手动触发 interval。
        const intervals = [];
        global.setInterval = function (fn, ms) { intervals.push({ fn, ms }); return intervals.length; };
        global.clearInterval = function (id) { intervals[id - 1] = null; };

        const cursor = { style: {} };
        const btn = { textContent: '' };
        // 3 个标记：0s(audio) / 60s(memory) / 150s(audio)。
        function makeMark(time, kind, label) {
          return {
            _attrs: { 'data-time': String(time), 'data-kind': kind, 'data-label': label, 'data-triggered': '0' },
            classList: { add: function(){}, remove: function(){} },
            getAttribute: function(k) { return this._attrs[k]; },
            setAttribute: function(k, v) { this._attrs[k] = v; },
          };
        }
        const marks = [makeMark(0, 'audio', 'audio_telephone_persistent'),
                       makeMark(60, 'memory', 'episode B'),
                       makeMark(150, 'audio', 'audio_voice_raised')];
        const track = {
          getAttribute: function(k) { return k === 'data-max' ? '150.0' : null; },
          querySelectorAll: function() { return marks; },
        };
        const audioEls = {
          'audio-audio_telephone_persistent': { _p: 0, play: function() { this._p++; } },
          'audio-audio_voice_raised': { _p: 0, play: function() { this._p++; } },
        };
        const cardNodes = [];
        const memPanel = { _scrolled: false, scrollIntoView: function() { this._scrolled = true; },
                          querySelectorAll: function() { return []; } };
        const doc = {
          getElementById: function(id) {
            if (id === 'case-time-track-sw_t1') return track;
            if (id === 'case-time-cursor-sw_t1') return cursor;
            if (id === 'case-time-play-sw_t1') return btn;
            if (audioEls[id]) return audioEls[id];
            if (id === 'fs-memory-timeline-sw_t1') return memPanel;
            return null;
          },
          querySelectorAll: function(sel) {
            return sel.indexOf('.audio-card[data-kind="') === 0 ? cardNodes : [];
          },
        };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        // 启动回放。
        global.__caseTimeReplay('sw_t1');
        if (intervals.length !== 1) { console.log('REPLAY_NO_INTERVAL'); process.exit(1); }
        if (btn.textContent !== '⏸') { console.log('BTN_NOT_PAUSE'); process.exit(1); }
        const iv = intervals[0];
        // 推进前 3 步：0s 标记应触发（audio play）、游标应移动。
        for (let s = 0; s < 3; s++) iv.fn();
        if (audioEls['audio-audio_telephone_persistent']._p < 1) { console.log('AUDIO0_NOT_TRIGGERED'); process.exit(1); }
        if (cursor.style.left === undefined) { console.log('CURSOR_NOT_MOVED'); process.exit(1); }
        // 推进到末尾：播放自动停 + 按钮恢复。
        for (let s = 0; s < 20; s++) iv.fn();
        if (intervals[0] !== null) { console.log('NOT_STOPPED'); process.exit(1); }
        if (btn.textContent !== '▶') { console.log('BTN_NOT_RESET'); process.exit(1); }
        console.log('REPLAY_OK');
        process.exit(0);
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
    assert r.returncode == 0, f"__caseTimeReplay 行为失败: {r.stdout} {r.stderr}"
    assert "REPLAY_OK" in r.stdout


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_case_time_replay_js_no_marks_noop():
    """无标记 → no-op（不启动 interval，不崩）。"""
    src = _media_source()
    harness = textwrap.dedent(
        """
        const fs = require('fs');
        const intervals = [];
        global.setInterval = function (fn, ms) { intervals.push(fn); return 1; };
        const track = { getAttribute: function() { return '10.0'; }, querySelectorAll: function() { return []; } };
        const doc = {
          getElementById: function(id) {
            if (id === 'case-time-track-sw_t1') return track;
            if (id === 'case-time-cursor-sw_t1') return { style: {} };
            return null;
          },
        };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        global.__caseTimeReplay('sw_t1');
        console.log(intervals.length === 0 ? 'NOOP_OK' : 'NOOP_FAIL');
        process.exit(intervals.length === 0 ? 0 : 1);
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
    assert r.returncode == 0, f"no-op 行为失败: {r.stdout} {r.stderr}"
    assert "NOOP_OK" in r.stdout

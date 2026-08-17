"""P1-B story_replay.js（叙事分幕点击聚焦）运行时契约测试。

对齐 html-inline-js 纪律：Node vm + mock DOM 真实运行引擎源码（引擎经 argv 传真实路径）。

覆盖：
- 点击幕按钮 → 叙述文案更新 + 按钮激活 + __Replay.seek(start) + focus refs 节点高亮；
- 无 .story-chapters → no-op 不崩。

不依赖 torch/cv2；CI 无 node 则 skip。
"""

from __future__ import annotations

import textwrap

import pytest


def _story_replay_source() -> str:
    from home_perception.visualizer.viewer import render

    src = render._story_replay_inline()
    assert src, "story_replay.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_story_replay_js_click_focuses_chapter():
    """Node vm：点击幕按钮 → 文案更新 + seek + focus refs 高亮（只读渲染，不推理）。"""
    import subprocess
    import tempfile

    src = _story_replay_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        const clicked = {};
        const copyEl = { textContent: '' };
        const btn = {
          _classes: [],
          classList: { add: function (c) { btn._classes.push(c); }, remove: function () {} },
          addEventListener: function (evt, fn) { clicked.fn = fn; },
          getAttribute: function (k) {
            return { 'data-copy': '系统检测到 2 个感知事件。',
                     'data-start': '3', 'data-end': '5',
                     'data-refs': 'sw_t1.canonical.json#stages[0]|sw_t1.canonical.json#audio[0]' }[k] || null;
          },
        };
        const btn2 = { _classes: [], classList: { add: function(){}, remove: function(){} },
          addEventListener: function(){}, getAttribute: function(){ return null; } };
        const nav = {
          getAttribute: function (k) { return k === 'data-scenario' ? 'sw_t1' : null; },
          querySelectorAll: function () { return [btn, btn2]; },
        };
        const seekLog = [];
        const replay = { seek: function (i) { seekLog.push(i); } };
        global.__Replay = { get: function () { return replay; } };
        const li = { _classes: [], classList: { add: function (c) { li._classes.push(c); }, remove: function(){} },
          getAttribute: function (k) { return k === 'data-ref' ? 'sw_t1.canonical.json#stages[0]' : null; } };
        const doc = {
          querySelectorAll: function (sel) {
            if (sel === '.story-chapters') return [nav];
            if (sel === '.tl-item[data-ref]') return [li];
            return [];
          },
          getElementById: function (id) { return id === 'story-copy-sw_t1' ? copyEl : null; },
        };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        clicked.fn();
        const copyOk = copyEl.textContent === '系统检测到 2 个感知事件。';
        const seekOk = seekLog.length === 1 && seekLog[0] === 3;
        const focusOk = li._classes.indexOf('story-focus') !== -1;
        const activeOk = btn._classes.indexOf('active') !== -1;
        console.log(copyOk && seekOk && focusOk && activeOk ? 'STORY_REPLAY_OK' : JSON.stringify({ copyOk: copyOk, seekOk: seekOk, focusOk: focusOk, activeOk: activeOk }));
        process.exit(copyOk && seekOk && focusOk && activeOk ? 0 : 1);
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
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, f"story_replay.js 运行时断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "STORY_REPLAY_OK" in r.stdout


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_story_replay_js_no_nav_noop():
    """无 .story-chapters → no-op 不崩（fail-open 于 UI 层）。"""
    import subprocess
    import tempfile

    src = _story_replay_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        const doc = { querySelectorAll: function () { return []; }, getElementById: function () { return null; } };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        console.log('STORY_REPLAY_NOOP_OK');
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
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, f"story_replay.js no-op 断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "STORY_REPLAY_NOOP_OK" in r.stdout

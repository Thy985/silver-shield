"""P0 live_stream.js（EvidenceProjection delta stream）运行时契约测试。

对齐 html-inline-js-behavioral-test 纪律：Node vm + mock DOM 真实运行引擎源码
（引擎经 argv 传真实文件路径，mock 只提供 document/WebSocket/location）。

覆盖：
- 收 evidence_delta → timeline/audio/case_time 增量渲染 + frames 计数更新；
- 首屏快照基线：已渲染 ref 被跳过（不重放，VM-8）；
- 幂等：同 ref/event_id 重复 delta 不重复渲染；
- 降级：目标容器缺失 → no-op 不崩。

不依赖 torch/cv2（纯 stdlib + Node），可在 torch-free 环境跑；CI 无 node 则 skip。
"""

from __future__ import annotations

import textwrap

import pytest


def _live_stream_source() -> str:
    from home_perception.visualizer.viewer import render

    src = render._live_stream_inline()
    assert src, "live_stream.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_delta_renders_and_dedups():
    """Node vm 真实运行：收 evidence_delta → 追加 timeline/audio/case_time + 更新 frames；
    重复 delta 幂等不重复；首屏基线 ref 被跳过。"""
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = {
            attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); },
            insertAdjacentHTML: function (pos, h) { this.html += h; },
          };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); },
            get: function () { return this.text; },
          });
          return el;
        }
        const ul = makeEl();                       // .timeline
        const table = makeEl();                    // table.audio-table
        const track = makeEl(); track.attrs['data-max'] = '10';  // #case-time-track-*
        const codeEl = makeEl(); codeEl.text = 'live_telephone_risk';  // .scenario-title code
        const timelineItem = makeEl(); timelineItem.attrs['data-ref'] = 'live://frame/5';  // 首屏基线
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const framesMuted = makeEl(); framesMuted.text = 'mode=live · frames=253';  // .scenario-title .muted

        const doc = {
          querySelector: function (sel) {
            if (sel === '.scenario-title code') return codeEl;
            if (sel === '.timeline') return ul;
            if (sel === 'table.audio-table') return table;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function (sel) {
            if (sel === '.tl-item[data-ref]') return [timelineItem];
            if (sel === '.muted') return [framesMuted];
            return [];
          },
          getElementById: function (id) { return id === 'case-time-track-live_telephone_risk' ? track : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function (url) { this.url = url; global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        const DELTA = {
          type: 'evidence_delta',
          timeline: [{ ref: 'live://frame/6', timestamp: 'F6', stage: 'perception', type: 'frame',
                       summary: 'frame 6: 1 检测, 0 警告', verdict: 'INFO', modality: 'VISION',
                       provenance_kind: 'REAL_SENSOR' }],
          audio: [{ event_id: 'evt_1', ref: 'live://audio/2', timestamp: '1700000000',
                    kind: 'audio_voice_raised', score: 0.7, confidence: 0.8,
                    labels: ['raised'], source_segment_ids: ['seg-1'] }],
          case_time: [{ time: 1.2, kind: 'audio', label: '音高升高' }],
          counts: { n_frames: 300, n_audio: 3, warnings: 1, commands: 0 },
        };
        // 首帧：新 ref 渲染；基线 ref（frame/5）不在 delta 里故无需测跳过——用重复 delta 测幂等。
        global._ws.onmessage({ data: JSON.stringify(DELTA) });
        const once = {
          tl: (ul.html.match(/data-ref="live:\/\/frame\/6"/g) || []).length,
          audio: (table.html.match(/audio_voice_raised/g) || []).length,  // event_id 是去重键，不渲染进行
          mark: (track.html.match(/mark-audio/g) || []).length,
          frames: framesMuted.text.indexOf('frames=300') !== -1,
        };
        // 幂等：重复同 delta → 不重复追加
        global._ws.onmessage({ data: JSON.stringify(DELTA) });
        const twice = {
          tl: (ul.html.match(/data-ref="live:\/\/frame\/6"/g) || []).length,
          audio: (table.html.match(/audio_voice_raised/g) || []).length,
          mark: (track.html.match(/mark-audio/g) || []).length,
        };
        // 首屏基线 ref 出现在 delta 中 → 被 seenRefs 跳过（不重放，VM-8）
        const DELTA2 = JSON.parse(JSON.stringify(DELTA));
        DELTA2.timeline[0].ref = 'live://frame/5';  // 与预填基线相同
        global._ws.onmessage({ data: JSON.stringify(DELTA2) });
        const baselineSkip = (ul.html.match(/data-ref="live:\/\/frame\/5"/g) || []).length;

        const ok = once.tl === 1 && once.audio === 1 && once.mark === 1 && once.frames
          && twice.tl === 1 && twice.audio === 1 && twice.mark === 1
          && baselineSkip === 0;
        console.log(ok ? 'LIVE_STREAM_OK' : JSON.stringify({ once: once, twice: twice, baselineSkip: baselineSkip }));
        process.exit(ok ? 0 : 1);
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
    assert r.returncode == 0, f"live_stream.js 运行时断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "LIVE_STREAM_OK" in r.stdout


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_missing_containers_noop():
    """目标容器缺失（如无 timeline / 无 audio-table）→ no-op 不崩（fail-open 于 UI 层）。"""
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        const closurePanel = { getAttribute: function () { return '/ws'; } };
        const codeEl = { text: 'live_telephone_risk' };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.scenario-title code') return codeEl;
            if (sel === '.closure-panel') return closurePanel;
            return null;  // .timeline / table.audio-table 缺失
          },
          querySelectorAll: function () { return []; },
          getElementById: function () { return null; },  // case-time-track 缺失
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        // 无任何容器 → 必须 no-op 不抛
        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta',
          timeline: [{ ref: 'live://frame/7', timestamp: 'F7', stage: 'perception', type: 'frame',
                       summary: 'frame 7', verdict: 'INFO', modality: 'VISION', provenance_kind: 'REAL_SENSOR' }],
          audio: [{ event_id: 'evt_9', ref: 'live://audio/9', timestamp: '1700000000',
                    kind: 'audio_voice_raised', score: 0.7, confidence: 0.8, labels: [], source_segment_ids: [] }],
          case_time: [{ time: 2.2, kind: 'audio', label: '音高升高' }],
          counts: { n_frames: 310 },
        }) });
        console.log('LIVE_STREAM_NOOP_OK');
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
    assert r.returncode == 0, f"live_stream.js no-op 断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "LIVE_STREAM_NOOP_OK" in r.stdout

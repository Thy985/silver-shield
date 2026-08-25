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
            attrs: {}, html: '', text: '', className: '', style: {}, onclick: null, _children: [],
            classList: { add: function () {}, remove: function () {} },
            parentNode: { querySelector: function () { return null; }, insertBefore: function () {}, removeChild: function () {}, nextSibling: null },
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); },
            insertAdjacentHTML: function (pos, h) { this.html += h; },
            appendChild: function (child) {
              var h = (child && child.outerHTML != null) ? child.outerHTML
                    : (child && child.html != null) ? child.html
                    : (child != null ? String(child) : '');
              this.html += h;
              this._children.push(child);
              if (child) child.parentNode = this;
              return child;
            },
            querySelector: function (sel) {
              // Surface flush 判重：li.tl-item[data-ref="X"] → 从自身 html 查 data-ref 是否已存在
              var m = sel && sel.match(/^li\.tl-item\[data-ref="([^"]*)"\]$/);
              if (m) {
                return this.html.indexOf('data-ref="' + m[1] + '"') !== -1
                  ? { getAttribute: function (k) { return k === 'data-ref' ? m[1] : null; }, parentNode: null }
                  : null;
              }
              return null;
            },
            querySelectorAll: function (sel) {
              if (sel === 'li.tl-item[data-ref]') {
                var re = /data-ref="([^"]*)"/g, m, out = [];
                while ((m = re.exec(this.html)) !== null) {
                  (function (ref) {
                    out.push({ getAttribute: function (k) { return k === 'data-ref' ? ref : null; }, parentNode: null });
                  })(m[1]);
                }
                return out;
              }
              return [];
            },
            removeChild: function (child) {
              this._children = this._children.filter(function (c) { return c !== child; });
              if (child && child.outerHTML != null) this.html = this.html.split(child.outerHTML).join('');
            },
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
          createElement: function () {
            // 最小 DOM 解析：innerHTML 设入 → firstChild.outerHTML 透出节点 HTML（_applyDelta 用其重建/追加）
            var e = makeEl();
            var _inner = '';
            Object.defineProperty(e, 'innerHTML', {
              set: function (v) {
                _inner = String(v);
                e.firstChild = { outerHTML: _inner, getAttribute: function () { return null; },
                  parentNode: null, className: '', style: {}, textContent: '', onclick: null };
              },
              get: function () { return _inner; },
              configurable: true,
            });
            return e;
          },
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
          body: makeEl(),
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
          createElement: function () {
            // P0-B 数据层：_applyDelta 先经 tmp.innerHTML 构建 timeline 节点（与 Surface 是否存在无关）
            var e = { attrs: {}, html: '', text: '', style: {}, className: '', onclick: null,
                      getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
                      setAttribute: function (k, v) { this.attrs[k] = String(v); } };
            Object.defineProperty(e, 'innerHTML', {
              set: function (v) {
                e.html = String(v);
                e.firstChild = { outerHTML: e.html, getAttribute: function () { return null; }, parentNode: null };
              },
              get: function () { return e.html; },
              configurable: true,
            });
            return e;
          },
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


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_perception_delta_renders():
    """Node vm：收 perception_delta → 渲染感知状态 + 记录延迟样本（零推理，只渲染）。"""
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '', innerHTML: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); },
            insertAdjacentHTML: function (pos, h) { this.html += h; } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          return el;
        }
        const lp = makeEl();                       // #live-perception-live_telephone_risk
        lp.attrs['data-scenario'] = 'live_telephone_risk';
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            // 回退路径：.scenario-title code 缺失（render.py live 页无 <code>），
            // sid 从 .live-perception 容器 data-scenario 取。
            if (sel === '.scenario-title code') return null;
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-live_telephone_risk' ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        // 发 perception_delta
        const nowMs = Date.now();
        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta', frame_index: 42, case_time: 5.25, server_ts: nowMs / 1000,
          detections: [
            { class: 'person', bbox: [10.5, 20.6, 300.0, 400.0], confidence: 0.91 },
            { class: 'car', bbox: [0, 0, 50, 50], confidence: 0.55 },
          ],
        })});
        const rendered = lp.innerHTML.indexOf('person') !== -1 && lp.innerHTML.indexOf('car') !== -1
          && lp.innerHTML.indexOf('0.91') !== -1 && lp.innerHTML.indexOf('5.3s') !== -1;
        const latencyRecorded = typeof window.__LiveStream.lastLatencyMs === 'number';
        // 幂等：同检测再发一次 → innerHTML 覆盖式（当前状态），不累积
        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta', frame_index: 43, case_time: 5.38, server_ts: nowMs / 1000,
          detections: [{ class: 'person', bbox: [11, 21, 301, 401], confidence: 0.92 }],
        })});
        const overwrite = lp.innerHTML.indexOf('5.4s') !== -1 && lp.innerHTML.indexOf('car') === -1;
        console.log(rendered && latencyRecorded && overwrite ? 'PERCEPTION_OK' : JSON.stringify({ rendered: rendered, latencyRecorded: latencyRecorded, overwrite: overwrite, html: lp.innerHTML }));
        process.exit(rendered && latencyRecorded && overwrite ? 0 : 1);
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
    assert r.returncode == 0, f"perception_delta 渲染失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "PERCEPTION_OK" in r.stdout


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_risk_card_and_signal():
    """PR-B（Node vm）：risk_delta → ③ ✓ 人话风险卡 + ③.5 RAISED/CLEARED 信号。

    - raised：卡亮起（✓ 人话原因 + 建议动作人话映射）+ 信号卡 RAISED；
    - cleared（risk_levels 空 + 服务端 transition）：风险卡熄灭回空态 + 信号 CLEARED 徽章；
      且风险信号卡 DOM **保留**（T1.2：不再 setTimeout 清空，避免 RAISED→CLEARED 闪烁，
      新 RAISED 由 raised 分支整体覆盖旧卡）。
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '', style: {},
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_t1';
        const els = {};
        ['lrk-card', 'lrk-empty', 'lrk-level', 'lrk-frame', 'lrk-reasons', 'lrk-rec',
         'live-signals-empty'].forEach(function (k) { els[k + '-' + sid] = makeEl(); });
        // 信号容器：innerHTML 写入含 rt-card 时同步提供 querySelector('.rt-card') mock。
        const sigBox = { html: '', style: {}, _card: null,
          querySelector: function (sel) { return sel === '.rt-card' ? this._card : null; } };
        Object.defineProperty(sigBox, 'innerHTML', {
          configurable: true,
          set: function (v) {
            this.html = String(v);
            if (this.html.indexOf('rt-card') !== -1) {
              const card = { classes: {},
                badge: { textContent: 'RAISED', classList: { add: function () {}, remove: function () {} } } };
              card.classList = { add: function (c) { card.classes[c] = true; },
                                 remove: function (c) { delete card.classes[c]; } };
              card.querySelector = function (sel) { return sel === '.rt-badge' ? card.badge : null; };
              this._card = card;
            } else { this._card = null; }
          },
          get: function () { return this.html; },
        });
        els['live-signals-' + sid] = sigBox;
        const lp = makeEl(); lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return els[id] || null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        // ① raised：风险卡亮起（✓ 人话原因 + 建议人话映射）+ 信号 RAISED
        global._ws.onmessage({ data: JSON.stringify({
          type: 'risk_delta', frame_index: 23, case_time: 2.875, risk_transition: 'raised',
          risk_levels: ['HIGH'], reason_summary: ['夜间访问', 'repeated_visit_detected'],
          recommended_actions: ['ESCALATE_COMMUNITY'], command_types: ['CREATE_COMMUNITY_TASK'],
        })});
        const raised = {
          cardShown: els['lrk-card-' + sid].style.display === '',
          emptyHidden: els['lrk-empty-' + sid].style.display === 'none',
          level: els['lrk-level-' + sid].textContent === 'HIGH 风险',
          frame: els['lrk-frame-' + sid].textContent === '2.9s',  // 仅 case_time，无 frame_index
          reasons: els['lrk-reasons-' + sid].innerHTML.indexOf('✓ 夜间访问') !== -1
            && els['lrk-reasons-' + sid].innerHTML.indexOf('✓ 检测到重复访问') !== -1,
          rec: els['lrk-rec-' + sid].innerHTML.indexOf('升级社区') !== -1,
          sigRaised: sigBox.html.indexOf('RAISED') !== -1 && sigBox.html.indexOf('2.9s') !== -1,
          sigEmptyHidden: els['live-signals-empty-' + sid].style.display === 'none',
        };
        // ② cleared：risk_levels 空 + 服务端 transition=cleared → 熄卡 + 回空态
        global._ws.onmessage({ data: JSON.stringify({
          type: 'risk_delta', frame_index: 40, case_time: 5.0, risk_transition: 'cleared',
          risk_levels: [], reason_summary: [], recommended_actions: [], command_types: [],
        })});
        const cleared = {
          cardHidden: els['lrk-card-' + sid].style.display === 'none',
          emptyShown: els['lrk-empty-' + sid].style.display === '',
          emptyText: els['lrk-empty-' + sid].textContent.indexOf('风险尚未触发') !== -1,
          sigCleared: sigBox._card && sigBox._card.badge.textContent === 'CLEARED',
          // T1.2 回归：cleared 后信号卡 DOM 保留（rt-card 仍在），不闪烁消失
          sigRetained: sigBox.html.indexOf('rt-card') !== -1,
        };
        const ok = Object.keys(raised).every(function (k) { return raised[k]; })
          && Object.keys(cleared).every(function (k) { return cleared[k]; });
        console.log(ok ? 'RISK_CARD_OK' : JSON.stringify({ raised: raised, cleared: cleared }));
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
    assert r.returncode == 0, f"risk card/signal 断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_draw_waveform():
    """Node vm 真实运行：_drawWaveform 绘制 RMS 波形（含空数据降级文案）。"""
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl(querySelectorFn, getAttrFn) {
          var el = { attrs: {}, html: '', text: '', style: {}, _drawn: [] };
          var _ctx = {
            fillStyle: '',
            fillRect: function (x, y, w, h) { this._rects.push({x: x, y: y, w: w, h: h}); },
            _rects: [],
            font: '',
            textAlign: '',
            fillText: function (t) { this._texts.push(t); },
            _texts: [],
            clearRect: function () { this._rects.length = 0; },
          };
          el.getContext = function () { return _ctx; };
          el.querySelector = querySelectorFn || function () { return null; };
          el.getAttribute = getAttrFn || function () { return null; };
          return el;
        }
        const sid = 'live_wave';
        var canvas = makeEl();
        canvas.width = 400; canvas.height = 60;
        var canvasId = 'waveform-canvas-' + sid;
        var els = {};
        els[canvasId] = canvas;
        var lpEl = makeEl(function () { return null; }, function (k) { return k === 'data-scenario' ? sid : null; });
        var doc = {
          getElementById: function (id) { return els[id] || null; },
          querySelector: function (sel) {
            if (sel === '.live-perception') return lpEl;
            return null;
          },
          querySelectorAll: function () { return []; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; this.onmessage = null; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        // 通过 _applyDelta 间接测试 _drawWaveform（不暴露内部函数，符合 VM-9 纪律）
        // 发送含 rms_window 的 evidence_delta → 触发 _drawWaveform
        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta',
          rms_window: [0.1, 0.5, 0.8, 0.3, 0.6],
          timeline: [], audio: [], case_time: [], counts: { n_frames: 5 },
          perception_events: [], warnings: [], commands: [],
        })});
        // 调试输出：检查 rects 和 texts
        var ctx = canvas.getContext();
        console.log(JSON.stringify({
          rectsLen: ctx._rects.length,
          texts: ctx._texts,
          hasBars: ctx._rects.length >= 5,
          barsVisible: ctx._rects.slice(-5).every(function (r) { return r.h > 0 && r.w >= 1; }),
        }));
        process.exit(ctx._rects.length >= 5 && ctx._rects.slice(-5).every(function (r) { return r.h > 0 && r.w >= 1; }) ? 0 : 1);
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
    assert r.returncode == 0, f"waveform 断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


# ---------------------------------------------------------------------------
# SSOT v4.0（Owner 裁决 2026-08-24）：T2 loop 重播 / T5 Debug Mode / epoch 显示
# ---------------------------------------------------------------------------


def _run_node(harness: str, src: str) -> str:
    """写临时 harness + 引擎源码 → node 运行 → 返回 stdout（非零退出即断言失败）。"""
    import subprocess
    import tempfile

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
    assert r.returncode == 0, f"live_stream.js 断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    return r.stdout


_MOCK_DOM_JS = """
const fs = require('fs');
function makeEl() {
  var el = { attrs: {}, html: '', text: '', className: '', style: {}, onclick: null,
    _children: [],
    classList: { add: function () {}, remove: function () {} },
    parentNode: { querySelector: function () { return null; }, insertBefore: function () {},
      removeChild: function () {}, nextSibling: null },
    getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
    setAttribute: function (k, v) { this.attrs[k] = String(v); },
    insertAdjacentHTML: function (pos, h) { this.html += h; },
    appendChild: function (c) { this._children.push(c); if (c) c.parentNode = el; return c; },
    remove: function () { this._removed = true; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
  };
  Object.defineProperty(el, 'textContent', {
    set: function (v) { this.text = String(v); },
    get: function () { return this.text; },
  });
  return el;
}
const table = makeEl();   // table.audio-table
const ul = makeEl();      // .timeline
const codeEl = makeEl(); codeEl.text = 'live_telephone_risk';
const closurePanel = makeEl();
closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
var locatorRemoved = false;
const locatorEl = { remove: function () { locatorRemoved = true; } };
const doc = {
  querySelector: function (sel) {
    if (sel === '.scenario-title code') return codeEl;
    if (sel === '.timeline') return ul;
    if (sel === 'table.audio-table') return table;
    if (sel === '.closure-panel') return closurePanel;
    return null;
  },
  querySelectorAll: function (sel) {
    if (sel === 'table.audio-table') return [table];
    if (sel === '.audio-event-locator') return locatorRemoved ? [] : [locatorEl];
    return [];
  },
  getElementById: function () { return null; },
  // 最小 DOM 解析：innerHTML 设入 → firstChild.outerHTML 透出（_applyDelta 节点重建）。
  createElement: function () {
    var e = makeEl();
    var _inner = '';
    Object.defineProperty(e, 'innerHTML', {
      set: function (v) {
        _inner = String(v);
        e.firstChild = { outerHTML: _inner, getAttribute: function () { return null; },
          parentNode: e, className: '', style: {}, textContent: '', onclick: null,
          querySelector: function () { return null; }, querySelectorAll: function () { return []; } };
      },
      get: function () { return _inner; },
      configurable: true,
    });
    return e;
  },
  body: makeEl(),
};
global.document = doc;
global.location = { protocol: 'http:', host: '127.0.0.1:8765', search: __SEARCH__ };
global.WebSocket = function () { global._ws = this; };
global.window = global;
eval(fs.readFileSync(process.argv[2], 'utf-8'));
"""


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_loop_wrap_resets_audio_dedup_and_replays():
    """SSOT v4.0 T2：frame_tick.loop_count 回绕 → 清空音频判重集合 + 移除定位条；
    同一 event_id 的证据在轮内仍幂等去重、跨轮回绕后重新涌现（重播语义）。
    晚开页面因此可在新一轮观察到事件按时间轴逐条流入。"""
    harness = _MOCK_DOM_JS.replace("__SEARCH__", "''") + textwrap.dedent(
        r"""
        const AUDIO = { event_id: 'evt_loop_1', ref: 'live://audio/1', timestamp: '1756036800',
          kind: 'audio_telephone_persistent', score: 0.9, confidence: 0.95,
          labels: ['telephone'], source_segment_ids: ['seg-1'] };
        const mkDelta = () => ({ type: 'evidence_delta', timeline: [],
          audio: [JSON.parse(JSON.stringify(AUDIO))], case_time: [], counts: {} });
        const tick = (i, lc) => global._ws.onmessage({ data: JSON.stringify(
          { type: 'frame_tick', frame_index: i, case_time: i * 30, loop_count: lc }) });
        const count = () => (table.html.match(/audio_telephone_persistent/g) || []).length;

        tick(0, 0);                       // 初始化 loop 基线（首轮）
        global._ws.onmessage({ data: JSON.stringify(mkDelta()) });
        const first = count();
        global._ws.onmessage({ data: JSON.stringify(mkDelta()) });   // 轮内重放同 delta
        const dup = count();
        tick(1, 1);                       // loop 回绕：0 → 1
        global._ws.onmessage({ data: JSON.stringify(mkDelta()) });   // 同 event_id 再流一次
        const replay = count();

        const ok = first === 1 && dup === 1 && replay === 2 && locatorRemoved === true;
        console.log(ok ? 'LOOP_REPLAY_OK'
          : JSON.stringify({ first: first, dup: dup, replay: replay, locatorRemoved: locatorRemoved }));
        process.exit(ok ? 0 : 1);
        """
    )
    out = _run_node(harness, _live_stream_source())
    assert "LOOP_REPLAY_OK" in out


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_debug_mode_metadata_annotations():
    """SSOT v4.0 T5：``?debug=1`` 显式调试意图下，audio-table 行的 score/conf
    data-* 元数据以 .debug-meta 标注显形；Product Mode（默认无参数）零标注——
    工程数值不回灌产品界面，且不引入新数据通道。"""
    delta_js = textwrap.dedent(
        r"""
        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta', timeline: [],
          audio: [{ event_id: 'evt_dbg_1', ref: 'live://audio/1', timestamp: '1756036800',
                    kind: 'audio_voice_raised', score: 0.7, confidence: 0.8,
                    labels: ['raised'], source_segment_ids: ['seg-1'] }],
          case_time: [], counts: {},
        }) });
        """
    )
    # 表 mock 需支持 td[data-score] 解析（_applyDebugAnnotations 的查询面）。
    # 关键：按匹配序号注册表复用同一 td 对象——引擎标注与探针读取必须是同一实例
    # （querySelectorAll 每次调用都重解析 html，若每次新建对象，_note 会丢失）。
    td_mock = """
var _tdReg = [];
table.querySelectorAll = function (sel) {
  if (sel !== 'td[data-score]') return [];
  var out = [], re = /<td data-score="([\\d.]+)" data-confidence="([\\d.]+)">/g, m, i = 0;
  while ((m = re.exec(table.html)) !== null) {
    var td = _tdReg[i];
    if (!td) {
      td = makeEl();
      td.attrs['data-score'] = m[1];
      td.attrs['data-confidence'] = m[2];
      td.querySelector = function (sel2) { return this._note != null ? this._note : null; };
      td.appendChild = function (c) { this._note = c; return c; };
      _tdReg[i] = td;
    }
    out.push(td);
    i++;
  }
  return out;
};
"""
    probe = """
var tds = table.querySelectorAll('td[data-score]');
console.log(JSON.stringify({
  rows: tds.length,
  annotated: tds.filter(function (t) { return t._note != null; }).length,
  sample: tds.length && tds[0]._note ? tds[0]._note.text : '',
  cls: tds.length && tds[0]._note ? tds[0]._note.className : '',
}));
process.exit(0);   // 引擎内存在常驻定时器，必须显式退出
"""
    # Product Mode：行存在但零标注。
    out_p = _run_node(
        _MOCK_DOM_JS.replace("__SEARCH__", "''") + td_mock + delta_js + probe,
        _live_stream_source(),
    )
    import json as _json

    info_p = _json.loads(out_p.strip().splitlines()[-1])
    assert info_p["rows"] == 1 and info_p["annotated"] == 0, (
        f"Product Mode 出现调试标注或行缺失：{info_p}"
    )
    # Debug Mode（?debug=1）：同一数据通道，标注显形。
    out_d = _run_node(
        _MOCK_DOM_JS.replace("__SEARCH__", "'?debug=1'") + td_mock + delta_js + probe,
        _live_stream_source(),
    )
    info_d = _json.loads(out_d.strip().splitlines()[-1])
    assert info_d["rows"] == 1 and info_d["annotated"] == 1, (
        f"Debug Mode 标注未显形：{info_d}"
    )
    assert "score=0.70" in info_d["sample"] and "conf=0.80" in info_d["sample"]
    assert "debug-meta" in info_d["cls"]


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_stream_js_epoch_timestamp_relative_display():
    """SSOT v4.0：epoch 绝对秒时间戳 → 行显示会话相对秒；原始值保留 data-ts。
    与 renderer._audio_ts_display_labels 同规则（双端防漂移）。"""
    harness = _MOCK_DOM_JS.replace("__SEARCH__", "''") + textwrap.dedent(
        r"""
        const mkA = (id, ts) => ({ event_id: id, ref: 'live://audio/' + id, timestamp: ts,
          kind: 'audio_telephone_persistent', score: 0.9, confidence: 0.95,
          labels: ['telephone'], source_segment_ids: ['seg-1'] });
        const send = (a) => global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta', timeline: [], audio: [a], case_time: [], counts: {} }) });
        send(mkA('e1', '1756036800'));
        send(mkA('e2', '1756036804'));
        send(mkA('e3', '12.5'));   // REAL pipeline 相对秒路径
        const has = (s) => table.html.indexOf(s) !== -1;
        const ok = has('@ 0.0s') && has('@ 4.0s') && has('@ 12.5s')
          && has('data-ts="1756036800"')
          && table.html.match(/@ 17\d{8}/) === null;
        console.log(ok ? 'EPOCH_DISPLAY_OK' : table.html);
        process.exit(ok ? 0 : 1);
        """
    )
    out = _run_node(harness, _live_stream_source())
    assert "EPOCH_DISPLAY_OK" in out


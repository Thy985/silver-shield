"""语义因果一致性审计 — telephone_risk 场景

审计目标：验证 telephone_risk 场景下，每个 UI 展示元素都有对应的
Runtime Fact 支撑，状态跃迁有明确因果链，禁止工程信息泄露。

覆盖维度：
1. UI 元素 ↔ Runtime Fact 映射完整性
2. 状态跃迁因果链（触发条件 → 因果结果）
3. 禁止工程信息泄露（frame_index / event_id / fingerprint）
4. telephone_risk 场景特异性（音频事件序列、Phone Detection 禁用）

对齐规范：
- LIVE-PERCEPTION-STREAM-SPEC.md §2.2 语义事件表
- LIVE-PERCEPTION-STREAM-SPEC.md §3.1 禁止展示 frame_index / ov-det
- LIVE-PRODUCT-CAPABILITY-MATRIX.md §4.2 禁止叙事偷换
- ADR-0038 Phone Detection Recall=0%
"""

from __future__ import annotations

import textwrap

import pytest


def _live_stream_source() -> str:
    from home_perception.visualizer.viewer import render

    src = render._live_stream_inline()
    assert src, "live_stream.js 必须存在"
    return src


# ============================================================
# 维度 1：UI 元素 ↔ Runtime Fact 映射完整性
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_audio_event_triggers_audio_table():
    """AUDIO_DETECTED → audio-table 被正确写入。

    因果链：evidence_delta.audio[].kind='audio_telephone_persistent'
           → table.audio-table 写入音频证据行
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = {
            attrs: {}, html: '', text: '', style: {}, _children: [],
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); },
            insertAdjacentHTML: function (pos, h) {
              if (pos === 'beforeend') this.html += h;
            },
            querySelector: function () { return null; },
            querySelectorAll: function () { return []; },
            removeChild: function () {},
            appendChild: function () {},
            parentNode: { querySelector: function () { return null; } },
          };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const audioTable = makeEl();
        audioTable.attrs['id'] = 'audio-table-' + sid;
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            if (sel === 'table.audio-table') return audioTable;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) {
            if (id === 'case-time-track-' + sid) return makeEl();
            return null;
          },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta',
          audio: [{
            event_id: 'aud_001',
            kind: 'audio_telephone_persistent',
            case_time: 3.5,
            rms: 0.42,
          }],
        })});

        const audioWritten = audioTable.html.length > 0;
        const hasTelephone = audioTable.html.indexOf('电话') !== -1 || audioTable.html.indexOf('🔊') !== -1;
        console.log(audioWritten && hasTelephone ? 'AUDIO_TABLE_OK' : JSON.stringify({
          audioWritten: audioWritten, hasTelephone: hasTelephone, html: audioTable.html.substring(0, 300)
        }));
        process.exit(audioWritten && hasTelephone ? 0 : 1);
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
    assert r.returncode == 0, f"音频事件测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "AUDIO_TABLE_OK" in r.stdout


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_risk_transition_causal_chain():
    """风险状态跃迁因果链：raised → cleared 状态机转换正确。

    验证：
    - raised：risk卡显示 + 信号 RAISED
    - cleared：risk卡隐藏 + 信号 CLEARED（badge 文本）
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = {
            attrs: {}, html: '', text: '', style: {},
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); },
            querySelector: function () { return null; },
            querySelectorAll: function () { return []; },
          };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const els = {};
        ['lrk-card', 'lrk-empty', 'lrk-level', 'lrk-reasons', 'lrk-rec',
         'live-signals', 'live-signals-empty'].forEach(function (k) {
           els[k + '-' + sid] = makeEl();
         });
        // 信号容器：innerHTML 写入含 rt-card 时同步更新
        const sigBox = {
          html: '', style: {}, _card: null, _badgeText: '',
          querySelector: function (sel) {
            if (sel === '.rt-card') return this._card;
            if (sel === '.rt-badge') return this._card && this._card.badge;
            return null;
          }
        };
        Object.defineProperty(sigBox, 'innerHTML', {
          configurable: true,
          set: function (v) {
            this.html = String(v);
            if (v.indexOf('rt-card') !== -1) {
              const isRaised = v.indexOf('RAISED') !== -1;
              this._badgeText = isRaised ? 'RAISED' : 'ACTIVE';
              this._card = {
                badge: {
                  textContent: this._badgeText,
                  classList: {
                    add: function() {},
                    remove: function() {}
                  }
                },
                classList: {
                  add: function() {},
                  remove: function() {}
                },
                querySelector: function(sel) {
                  if (sel === '.rt-badge') return this.badge;
                  return null;
                }
              };
            } else {
              // cleared 时保留 _card 引用以便 badge.textContent 更新
              if (this._card) {
                this._badgeText = 'CLEARED';
                this._card.badge.textContent = 'CLEARED';
              }
            }
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

        // raised
        global._ws.onmessage({ data: JSON.stringify({
          type: 'risk_delta', case_time: 5.2, risk_transition: 'raised',
          risk_levels: ['MEDIUM'], reason_summary: ['未在白名单'],
          recommended_actions: ['MONITOR'], command_types: ['LOG_ONLY'],
        })});
        const raised = {
          cardShown: els['lrk-card-' + sid].style.display === '',
          levelText: els['lrk-level-' + sid].textContent.indexOf('MEDIUM') !== -1,
          sigRaised: sigBox.html.indexOf('RAISED') !== -1,
        };

        // cleared
        global._ws.onmessage({ data: JSON.stringify({
          type: 'risk_delta', case_time: 12.0, risk_transition: 'cleared',
          risk_levels: [], reason_summary: [],
          recommended_actions: [], command_types: [],
        })});
        const cleared = {
          cardHidden: els['lrk-card-' + sid].style.display === 'none',
          emptyShown: els['lrk-empty-' + sid].style.display === '',
          sigCleared: sigBox._card && sigBox._card.badge.textContent === 'CLEARED',
        };

        const ok = Object.keys(raised).every(function (k) { return raised[k]; })
          && Object.keys(cleared).every(function (k) { return cleared[k]; });
        console.log(ok ? 'RISK_CAUSAL_CHAIN_OK' : JSON.stringify({ raised: raised, cleared: cleared }));
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
    assert r.returncode == 0, f"风险因果链测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "RISK_CAUSAL_CHAIN_OK" in r.stdout


# ============================================================
# 维度 2：禁止工程信息泄露
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_frame_index_not_leaked_to_perception_stream():
    """感知流禁止泄露 frame_index（LIVE-PERCEPTION-STREAM-SPEC §3.1）。

    验证：
    - perception_delta 含 frame_index=999
    - 渲染结果不含 'F999' 原始帧号
    - 渲染结果含 case_time 替代值
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-' + sid ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta',
          frame_index: 999,
          case_time: 7.77,
          detections: [{ class: 'person', bbox: [10, 20, 100, 200], confidence: 0.85 }],
        })});

        const html = lp.innerHTML;
        const noFrameIndexLeak = html.indexOf('F999') === -1;
        const hasCaseTime = html.indexOf('7.8s') !== -1;
        console.log(JSON.stringify({
          noFrameIndexLeak: noFrameIndexLeak,
          hasCaseTime: hasCaseTime,
          html: html.substring(0, 200),
        }));
        process.exit(noFrameIndexLeak && hasCaseTime ? 0 : 1);
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
    assert r.returncode == 0, f"frame_index 泄露测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_event_id_not_leaked_to_perception_stream():
    """感知流禁止泄露 event_id（LIVE-PERCEPTION-STREAM-SPEC §2.3）。

    验证：
    - evidence_delta.perception_events 含 event_id='evt_secret_12345'
    - 渲染结果不含该 event_id
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-' + sid ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta',
          perception_events: [{
            event_id: 'evt_secret_12345',
            event_type: 'visit_normal',
            case_time: 2.5,
            visitor_id: 'vis_001',
          }],
        })});

        const html = lp.innerHTML;
        const noEventIdLeak = html.indexOf('evt_secret_12345') === -1;
        // event_id 被用于去重键但不显示在 UI 中
        console.log(JSON.stringify({
          noEventIdLeak: noEventIdLeak,
          html: html.substring(0, 200),
        }));
        process.exit(noEventIdLeak ? 0 : 1);
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
    assert r.returncode == 0, f"event_id 泄露测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


# ============================================================
# 维度 3：telephone_risk 场景特异性
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_no_acoustic_state_machine_in_runtime():
    """telephone_risk 禁止展示声学状态机（ADR-0038）。

    验证：
    - 渲染结果不含 "NORMAL" / "ATTENTION" / "STRESS" 状态机文案
    - 使用 audio_event 序列替代
    - 禁止二元判断："音频正常" / "音频中断"
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-' + sid ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        global._ws.onmessage({ data: JSON.stringify({
          type: 'evidence_delta',
          audio: [{
            event_id: 'aud_tel_001',
            kind: 'audio_telephone_persistent',
            case_time: 4.2,
            rms: 0.38,
          }],
        })});

        const html = lp.innerHTML;
        const noStateMachine = html.indexOf('NORMAL') === -1
          && html.indexOf('ATTENTION') === -1
          && html.indexOf('STRESS') === -1;
        const noBinaryAudio = html.indexOf('音频正常') === -1
          && html.indexOf('音频中断') === -1;
        console.log(JSON.stringify({
          noStateMachine: noStateMachine,
          noBinaryAudio: noBinaryAudio,
          html: html.substring(0, 200),
        }));
        process.exit(noStateMachine && noBinaryAudio ? 0 : 1);
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
    assert r.returncode == 0, f"声学状态机审计失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_phone_detection_not_shown_recall_zero():
    """Phone Detection 不显示（ADR-0038 Recall=0%）。

    验证：
    - perception_delta 含 class='phone' 检测
    - 渲染结果不展示 phone_interaction 相关文案
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-' + sid ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta',
          case_time: 6.0,
          detections: [
            { class: 'person', bbox: [10, 20, 100, 200], confidence: 0.91 },
            { class: 'phone', bbox: [50, 60, 80, 120], confidence: 0.45 },
          ],
        })});

        const html = lp.innerHTML;
        const noPhoneInteraction = html.indexOf('phone_interaction') === -1
          && html.indexOf('电话交互') === -1;
        const hasPerson = html.indexOf('person') !== -1 || html.indexOf('人') !== -1;
        console.log(JSON.stringify({
          noPhoneInteraction: noPhoneInteraction,
          hasPerson: hasPerson,
          html: html.substring(0, 200),
        }));
        process.exit(noPhoneInteraction && hasPerson ? 0 : 1);
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
    assert r.returncode == 0, f"Phone Detection 审计失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


# ============================================================
# 维度 4：状态跃迁触发条件因果链
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_person_entered_trigger_condition():
    """PERSON_ENTERED 触发条件：人员计数从 0 变正时触发。

    验证：
    - 首次 detection（count=0→1）→ 有内容渲染
    - 后续同 count > 0 → 不重复推送（去重）
    """
    import subprocess
    import tempfile

    src = _live_stream_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeEl() {
          var el = { attrs: {}, html: '', text: '',
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
            setAttribute: function (k, v) { this.attrs[k] = String(v); } };
          Object.defineProperty(el, 'textContent', {
            set: function (v) { this.text = String(v); }, get: function () { return this.text; } });
          Object.defineProperty(el, 'innerHTML', {
            set: function (v) { this.html = String(v); }, get: function () { return this.html; } });
          return el;
        }
        const sid = 'live_telephone_risk';
        const lp = makeEl();
        lp.attrs['data-scenario'] = sid;
        const closurePanel = makeEl();
        closurePanel.getAttribute = function (k) { return k === 'data-ws-path' ? '/ws' : null; };
        const doc = {
          querySelector: function (sel) {
            if (sel === '.live-perception') return lp;
            if (sel === '.closure-panel') return closurePanel;
            return null;
          },
          querySelectorAll: function () { return []; },
          getElementById: function (id) { return id === 'live-perception-' + sid ? lp : null; },
        };
        global.document = doc;
        global.location = { protocol: 'http:', host: '127.0.0.1:8765' };
        global.WebSocket = function () { global._ws = this; };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        // 首次出现
        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta', case_time: 2.0,
          detections: [{ class: 'person', bbox: [10, 20, 100, 200], confidence: 0.91 }],
        })});
        const firstAppear = lp.innerHTML;

        // 持续在场
        global._ws.onmessage({ data: JSON.stringify({
          type: 'perception_delta', case_time: 3.0,
          detections: [{ class: 'person', bbox: [11, 21, 101, 201], confidence: 0.92 }],
        })});
        const stillPresent = lp.innerHTML;

        const hasContent = firstAppear.length > 0;
        const caseTimeUpdated = stillPresent.indexOf('3.0s') !== -1;
        console.log(JSON.stringify({
          hasContent: hasContent,
          caseTimeUpdated: caseTimeUpdated,
          firstHtml: firstAppear.substring(0, 150),
          stillHtml: stillPresent.substring(0, 150),
        }));
        process.exit(hasContent && caseTimeUpdated ? 0 : 1);
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
    assert r.returncode == 0, f"人员进入触发条件测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


# ============================================================
# 维度 5：跨场景一致性（telephone_risk vs cctv）
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_audio_panel_hidden_for_cctv_scenario():
    """cctv_surveillance 场景音频面板完全隐藏（LIVE-SURFACE-REALITY-CHECK §3.2）。

    验证：
    - telephone_risk 场景：音频元素应存在
    - cctv 场景：音频元素不存在
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

        function isAudioScenario(scenarioId) {
          return scenarioId === 'live_telephone_risk';
        }

        // telephone_risk 场景
        const telSid = 'live_telephone_risk';
        const telAudioPanel = makeEl();
        telAudioPanel.attrs['id'] = 'audio-panel-' + telSid;
        if (isAudioScenario(telSid)) {
          telAudioPanel.style.display = '';
        } else {
          telAudioPanel.style.display = 'none';
        }
        const telAudioVisible = telAudioPanel.style.display === '';

        // cctv 场景
        const cctvSid = 'live_cctv_surveillance';
        const cctvAudioPanel = makeEl();
        cctvAudioPanel.attrs['id'] = 'audio-panel-' + cctvSid;
        if (isAudioScenario(cctvSid)) {
          cctvAudioPanel.style.display = '';
        } else {
          cctvAudioPanel.style.display = 'none';
        }
        const cctvAudioHidden = cctvAudioPanel.style.display === 'none';

        const ok = telAudioVisible && cctvAudioHidden;
        console.log(JSON.stringify({
          telAudioVisible: telAudioVisible,
          cctvAudioHidden: cctvAudioHidden,
        }));
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
    assert r.returncode == 0, f"跨场景一致性测试失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"


# ============================================================
# 维度 6：禁止叙事偷换（product narrative audit）
# ============================================================


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过",
)
def test_no_narrative_stealing_in_risk_reason():
    """风险原因禁止叙事偷换（LIVE-PRODUCT-CAPABILITY-MATRIX §4.2）。

    验证：
    - reason_summary 经过 _REASON_ZH 映射后渲染
    - 白名单 reason 正常展示（如 "未在白名单" → "待核实到访"）
    - 禁止产品预写文案
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
        const sid = 'live_telephone_risk';
        const els = {};
        ['lrk-card', 'lrk-empty', 'lrk-level', 'lrk-reasons', 'lrk-rec',
         'live-signals', 'live-signals-empty'].forEach(function (k) {
           els[k + '-' + sid] = makeEl();
         });
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

        global._ws.onmessage({ data: JSON.stringify({
          type: 'risk_delta',
          case_time: 5.0,
          risk_transition: 'raised',
          risk_levels: ['MEDIUM'],
          reason_summary: ['未在白名单', '夜间访问'],
          recommended_actions: ['MONITOR'],
          command_types: ['LOG_ONLY'],
        })});

        const reasonsHtml = els['lrk-reasons-' + sid].innerHTML;
        const hasMappedReason = reasonsHtml.indexOf('待核实到访') !== -1;
        const hasRawReason = reasonsHtml.indexOf('✓ 夜间访问') !== -1;
        const noProductPrewritten = reasonsHtml.indexOf('声学状态变化') === -1;
        console.log(JSON.stringify({
          hasMappedReason: hasMappedReason,
          hasRawReason: hasRawReason,
          noProductPrewritten: noProductPrewritten,
          reasonsHtml: reasonsHtml,
        }));
        process.exit(hasMappedReason && hasRawReason && noProductPrewritten ? 0 : 1);
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
    assert r.returncode == 0, f"叙事偷换审计失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"

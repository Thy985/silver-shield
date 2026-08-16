/*
 * P0 EvidenceProjection delta stream（Owner 2026-08-17 拍板）· live_stream.js
 * 职责（边界严守，VM-1 / VM-9）：
 * - 连接 gateway WS（路径取自 closure-panel data-ws-path，缺省 /ws）；
 * - 收 ``evidence_delta``（服务端对 EvidenceProjection 的只读增量）→ **只渲染**：
 *   timeline 追加节点 / audio 证据追加行 / Case Time 追加标记 / frames 计数更新；
 * - **不创造 Evidence、不跑规则、不判断风险**：一切来自服务端投影；
 * - 幂等（VM-8）：以 ref / event_id / (kind@time) 去重，重复 delta 不重复渲染；
 * - 首屏快照即基线：预填已渲染 refs，绝不重放重复；
 * - 降级：WS 不可达 / 元素缺失 → no-op 不崩（fail-open 于 UI 层）。
 */
(function (global) {
  'use strict';

  var _REF_PREFIX = 'live://';
  var _MARKERS = { VISION: '👁', AUDIO: '🔊', ACTION: '⚡' };
  var _COLORS = { VISION: '#4a90d9', AUDIO: '#9b59b6', ACTION: '#d68910' };
  var seenRefs = new Set();
  var seenAudio = new Set();
  var seenCaseTime = new Set();
  var ws = null;
  var sid = '';

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _buildTimelineNode(n) {
    var marker = _MARKERS[n.modality] || '•';
    var color = _COLORS[n.modality] || '#3b4a5a';
    var verdictCls = 'node-neutral';
    return '<li class="tl-item" data-step="' + _esc(n.timestamp) + '" data-ref="' + _esc(n.ref) + '">' +
      '<span class="tl-dot" style="background:' + color + '"></span>' +
      '<div class="tl-body">' +
      '<div class="tl-head">' +
      '<span class="tl-step">' + _esc(n.timestamp) + '</span>' +
      '<span class="tl-modality" style="color:' + color + '">' + marker + ' ' + _esc(n.modality) + '</span>' +
      '<span class="tl-stage" style="color:' + color + '">' + _esc(n.stage) + '</span>' +
      '<span class="tl-kind">' + _esc(n.type) + '</span>' +
      '<span class="tl-verdict ' + verdictCls + '">' + _esc(n.summary) + '</span>' +
      '</div>' +
      '<div class="tl-meta muted">provenance: ' + _esc(n.provenance_kind) + ' · source: ' + _esc(n.ref) + '</div>' +
      '</div></li>';
  }

  function _buildAudioRow(a) {
    var ts = _esc(a.timestamp);
    var labels = (a.labels || []).map(_esc).join(', ');
    var segs = (a.source_segment_ids || []).map(_esc).join(', ');
    return '<tr>' +
      '<td>🔊</td>' +
      '<td>' + _esc(a.kind) + ' <span class="muted">@ ' + ts + '</span></td>' +
      '<td>score=' + Number(a.score).toFixed(2) + ' · conf=' + Number(a.confidence).toFixed(2) + '</td>' +
      '<td>' + labels + '</td>' +
      '<td>' + segs + '</td>' +
      '</tr>';
  }

  function _buildCaseTimeMark(m, maxTime) {
    var max = maxTime > 0 ? maxTime : 1;
    var pct = Math.min(Math.max((Number(m.time) || 0) / max * 100, 0), 100);
    return '<span class="case-time-mark mark-audio" style="left:' + pct.toFixed(1) + '%" ' +
      'data-time="' + Number(m.time).toFixed(3) + '" data-kind="audio" data-label="' + _esc(m.label) + '" ' +
      'title="' + Number(m.time).toFixed(1) + 's · audio · ' + _esc(m.label) + '">🔊</span>';
  }

  function _updateFrames(nFrames) {
    // frames 计数文本位于 .muted 元素（详情面板"场景标识：… mode=live · frames=N"），
    // 通用匹配所有含 "frames=N" 的 .muted（结构解耦，避免硬编码场景标题选择器）。
    var els = global.document.querySelectorAll('.muted');
    for (var i = 0; i < els.length; i++) {
      var t = els[i].textContent || '';
      if (/frames=\d+/.test(t)) els[i].textContent = t.replace(/frames=\d+/, 'frames=' + nFrames);
    }
  }

  function _applyDelta(msg) {
    // timeline 追加（幂等：ref 去重）
    (msg.timeline || []).forEach(function (n) {
      if (!n.ref || seenRefs.has(n.ref)) return;
      seenRefs.add(n.ref);
      var ul = global.document.querySelector('.timeline');
      if (!ul) return;
      ul.insertAdjacentHTML('beforeend', _buildTimelineNode(n));
    });
    // audio 证据行追加（幂等：event_id 去重）
    (msg.audio || []).forEach(function (a) {
      var id = a.event_id || a.ref;
      if (!id || seenAudio.has(id)) return;
      seenAudio.add(id);
      var table = global.document.querySelector('table.audio-table');
      if (!table) return;
      table.insertAdjacentHTML('beforeend', _buildAudioRow(a));
    });
    // Case Time 标记追加（幂等：kind@time 去重）
    (msg.case_time || []).forEach(function (m) {
      var key = m.kind + '@' + m.time;
      if (seenCaseTime.has(key)) return;
      seenCaseTime.add(key);
      var track = global.document.getElementById('case-time-track-' + sid);
      if (!track) return;
      var max = parseFloat(track.getAttribute('data-max') || '0') || 0;
      track.insertAdjacentHTML('beforeend', _buildCaseTimeMark(m, max));
    });
    // counts 更新（frames 计数实时推进）
    if (msg.counts && msg.counts.n_frames != null) _updateFrames(msg.counts.n_frames);
  }

  function _init() {
    if (typeof global.document === 'undefined' || typeof WebSocket === 'undefined') return;
    var code = global.document.querySelector('.scenario-title code');
    if (code) sid = (code.textContent || '').trim();
    // 首屏快照即基线：预填已渲染 refs，绝不重放重复（VM-8）。
    var items = global.document.querySelectorAll('.tl-item[data-ref]');
    for (var i = 0; i < items.length; i++) seenRefs.add(items[i].getAttribute('data-ref'));
    var panel = global.document.querySelector('.closure-panel');
    var wsPath = (panel && panel.getAttribute('data-ws-path')) || '/ws';
    try {
      ws = new WebSocket(
        (global.location.protocol === 'https:' ? 'wss://' : 'ws://') +
        global.location.host + wsPath
      );
    } catch (e) { return; }
    ws.onmessage = function (evt) {
      var msg;
      try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (msg && msg.type === 'evidence_delta') _applyDelta(msg);
    };
  }

  _init();

  global.__LiveStream = { applyDelta: _applyDelta, seenRefs: seenRefs };
})(window);

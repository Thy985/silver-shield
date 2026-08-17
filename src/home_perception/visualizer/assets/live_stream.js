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
  // LP-2：检测 class → 人话标签（仅视觉类别，非语义判定 VM-9）。
  var _CLASS_ZH = { person: '人', car: '车辆', dog: '动物', cat: '动物' };
  // LP-2：音频 kind → 人话标签（与 live_adapter._AUDIO_KIND_ZH 语义对齐，回落原枚举）。
  var _AUDIO_KIND_ZH = {
    audio_voice_raised: '音高升高',
    audio_speech_rapid: '语速加快',
    audio_distress_cry: '哭腔/求助',
    audio_telephone_persistent: '持续电话声',
    audio_anomaly_other: '其他声学异常'
  };
  // LP-2：实时感知聚合状态（视觉 + 音频），perception_delta 更新 vision、evidence_delta 更新 audio。
  var seeState = { vision: [], audio: [] };
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

  // LP-4：Toast 事件涌现（右下角飞入，3.2s 后淡出）。新证据/风险变化的视觉反馈。
  function _toast(text) {
    if (typeof global.document === 'undefined') return;
    if (typeof global.document.createElement !== 'function') return;  // 测试/极端环境降级 no-op
    var host = global.document.getElementById('live-toasts');
    if (!host) {
      host = global.document.createElement('div');
      host.id = 'live-toasts';
      global.document.body.appendChild(host);
    }
    var t = global.document.createElement('div');
    t.className = 'live-toast';
    t.textContent = text;
    host.appendChild(t);
    setTimeout(function () {
      t.classList.add('out');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 400);
    }, 3200);
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

  // P1-A：实时感知状态（perception_delta → 结构化渲染）。浏览器只渲染服务端投影的
  // 检测子集（class/bbox/confidence），零推理、不判断风险（VM-9）。
  function _applyPerceptionDelta(msg) {
    var el = global.document.getElementById('live-perception-' + sid);
    if (!el) return;
    var rows = (msg.detections || []).map(function (d) {
      var b = (d.bbox || []).map(function (v) { return Math.round(v); }).join(',');
      return '<li><span class="lp-class">' + _esc(d.class) + '</span>' +
        '<span class="muted"> conf ' + Number(d.confidence).toFixed(2) + '</span>' +
        '<span class="muted"> · bbox [' + b + ']</span></li>';
    }).join('');
    var head = 'F' + msg.frame_index +
      (msg.case_time != null ? ' · ' + Number(msg.case_time).toFixed(1) + 's' : '');
    el.innerHTML = '<div class="lp-head">Visual perception <span class="muted">' + head + '</span></div>' +
      (rows ? '<ul>' + rows + '</ul>' : '<span class="muted">（当前无检测）</span>');
    // 帧 overlay 检测数联动（LP-1：检测数与画面同帧同步）。
    var od = global.document.getElementById('ov-det-' + sid);
    if (od) od.textContent = (msg.detections || []).length;
    // LP-2：更新"AI 看到了"视觉部分（人话 class，非裸 bbox）。
    seeState.vision = (msg.detections || []).map(function (d) {
      return { class: _CLASS_ZH[d.class] || d.class };
    });
    _renderSee();
    // 端到端延迟样本（server_ts 为网关 time.time()，仅延迟度量，不进 EvidenceProjection）。
    if (msg.server_ts != null) {
      var now = Date.now();
      global.__LiveStream.lastLatencyMs = now - (msg.server_ts * 1000);
      global.__LiveStream.latencySamples = global.__LiveStream.latencySamples || [];
      global.__LiveStream.latencySamples.push(global.__LiveStream.lastLatencyMs);
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
      // LP-4：新事件涌现 toast（非 session 根节点才提示，避免首连刷屏）。
      if (n.type !== 'session' && n.summary) _toast(n.summary);
    });
    // audio 证据行追加（幂等：event_id 去重）
    (msg.audio || []).forEach(function (a) {
      var id = a.event_id || a.ref;
      if (!id || seenAudio.has(id)) return;
      seenAudio.add(id);
      var table = global.document.querySelector('table.audio-table');
      if (!table) return;
      table.insertAdjacentHTML('beforeend', _buildAudioRow(a));
      // LP-2：更新"AI 听到了"音频部分（人话 kind）。
      var kz = _AUDIO_KIND_ZH[a.kind] || a.kind;
      if (seeState.audio.indexOf(kz) < 0) seeState.audio.push(kz);
      _renderSee();
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

  // LP-1：真实同步帧流（frame_tick → <img> 显示 base64 JPEG，浏览器零推理 VM-9）。
  // Frame N 的画面与同帧 detection（perception_delta，case_time = N×interval）天然同步。
  function _applyFrame(msg) {
    var img = global.document.getElementById('video-img-' + sid);
    if (img && msg.frame_base64) {
      img.src = 'data:image/jpeg;base64,' + msg.frame_base64;
    }
    var badge = global.document.getElementById('live-badge-' + sid);
    if (badge) badge.style.display = 'inline-flex';
    var f = global.document.getElementById('ov-frame-' + sid);
    if (f) f.textContent = msg.frame_index;
  }

  // LP-2：渲染"AI 看到了"（视觉 + 音频聚合的人话感知，非裸 bbox 数字）。
  function _renderSee() {
    var el = global.document.getElementById('ai-see-' + sid);
    if (!el) return;
    var parts = [];
    if (seeState.vision.length) {
      parts.push(seeState.vision.map(function (d) {
        return '👤 ' + _esc(d.class);
      }).join(' · '));
    } else {
      parts.push('画面中暂无人物');
    }
    if (seeState.audio.length) {
      parts.push('🔊 ' + seeState.audio.join(' · '));
    }
    el.textContent = parts.join('　');
  }

  // LP-3：实时风险与决策（risk_delta → CURRENT STATE / 为什么关注 / 下一步）。
  function _applyRiskDelta(msg) {
    var riskEl = global.document.getElementById('ai-risk-' + sid);
    var whyEl = global.document.getElementById('ai-why-' + sid);
    var nextEl = global.document.getElementById('ai-next-' + sid);
    var levels = msg.risk_levels || [];
    var reasons = msg.reason_summary || [];
    var actions = msg.recommended_actions || [];
    var commands = msg.command_types || [];
    // CURRENT STATE：有风险级别 → 展示之；无 → MONITOR（继续观察）。
    if (riskEl) {
      var state = levels.length ? levels.join(' / ') : 'MONITOR';
      riskEl.textContent = state;
      var cls = 'ai-v ai-risk';
      if (levels.indexOf('HIGH') >= 0) cls += ' high';
      else if (levels.indexOf('MEDIUM') >= 0) cls += ' medium';
      riskEl.className = cls;
    }
    // LP-4：风险变化 toast（仅当有风险级别时，避免无风险帧刷屏）。
    if (levels.length) _toast('风险变化：' + levels.join(' / '));
    // 为什么关注：人话 reason_summary（无"诈骗/犯罪"字样）；空 → 诚实"—"。
    if (whyEl) whyEl.textContent = reasons.length ? reasons.join(' · ') : '—';
    // 下一步：优先 recommended_actions（策略建议），其次 command_types（实际命令）。
    if (nextEl) {
      var next = actions.length ? actions : commands;
      nextEl.textContent = next.length ? next.join(' · ') : '继续观察';
    }
  }

  // PR-A：WS 连接状态 pill（header 实时反馈 未连接/已连接；元素缺失 → no-op）。
  function _setWsPill(online) {
    var pill = global.document.getElementById('ws-pill');
    if (!pill) return;
    pill.className = 'pill ' + (online ? 'online' : 'offline');
    var t = global.document.getElementById('ws-text');
    if (t) t.textContent = online ? '已连接' : '未连接';
  }

  function _init() {
    if (typeof global.document === 'undefined' || typeof WebSocket === 'undefined') return;
    var code = global.document.querySelector('.scenario-title code');
    if (code) sid = (code.textContent || '').trim();
    // 回退：live 页 scenario-title 无 <code>（render.py 用 _scenario_headline），改从
    // live-perception 容器的 data-scenario 属性取 sid（容器本身带 sid，最可靠）。
    if (!sid) {
      var lp = global.document.querySelector('.live-perception');
      if (lp) sid = lp.getAttribute('data-scenario') || '';
    }
    // 首屏快照即基线：预填已渲染 refs，绝不重放重复（VM-8）。
    var items = global.document.querySelectorAll('.tl-item[data-ref]');
    for (var i = 0; i < items.length; i++) seenRefs.add(items[i].getAttribute('data-ref'));
    var panel = global.document.querySelector('.closure-panel');
    var wsPath = (panel && panel.getAttribute('data-ws-path')) || '/ws';
    var reconnectTimer = null;
    function _scheduleReconnect() {
      if (reconnectTimer) return;
      reconnectTimer = setTimeout(function () { reconnectTimer = null; _connect(); }, 2500);
    }
    function _connect() {
      try {
        ws = new WebSocket(
          (global.location.protocol === 'https:' ? 'wss://' : 'ws://') +
          global.location.host + wsPath
        );
      } catch (e) { _scheduleReconnect(); return; }
      ws.onopen = function () { _setWsPill(true); };
      ws.onclose = function () { _setWsPill(false); _scheduleReconnect(); };
      ws.onerror = function () { _setWsPill(false); };
      ws.onmessage = function (evt) {
        var msg;
        try { msg = JSON.parse(evt.data); } catch (e) { return; }
        if (!msg) return;
        if (msg.type === 'evidence_delta') _applyDelta(msg);
        else if (msg.type === 'perception_delta') _applyPerceptionDelta(msg);
        else if (msg.type === 'frame_tick') _applyFrame(msg);
        else if (msg.type === 'risk_delta') _applyRiskDelta(msg);
      };
    }
    _connect();
  }

  _init();

  global.__LiveStream = { applyDelta: _applyDelta, seenRefs: seenRefs };
})(window);

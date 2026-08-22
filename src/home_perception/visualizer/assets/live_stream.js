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
  // ============================================================
  // P0-1: 前端状态层（跨帧保活 · 行为叙事 · 风险信号 TTL）
  // 对齐原 Demo b593a01 state 对象；VM-9：只消费 delta 流，零推理。
  // ============================================================
  var __LiveState = {
    // 跨帧风险保活（warning_id → warning 最新快照，防闪现）
    warningMap: {},
    warningMax: 30,
    // 行为里程碑（visitor 首次出现 → 停留 → 再现 → 风险升级）
    behaviorEvents: [],
    behaviorSeen: {},
    behaviorMax: 120,
    behaviorN: 0,
    visitorSeq: {},
    visitorSeqN: 0,
    visitorFirst: {},
    // 风险信号实体追踪（signal_id → { ..., expiresAt }）
    riskSignalMap: new Map(),
    rtTtlMs: 20000,  // 20s TTL 兜底（服务端 volatile 语义展示层补充）
    // 三端命令累积（warning_id → { family, community, log_only: Map }）
    commandMap: {},
    // Session 元数据
    sessionStart: null,
    sessionTimer: null,
    lastFrameIndex: -1,
    lastLoopCount: 0,
  };
  // ============================================================

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
  // Phase 1 L0：Audio Health 三值状态机（见 LIVE-PERCEPTION-STREAM-SPEC §2.4）
  // 铁律：非二元健康度；RECENT_EVENT 仅表"最近有事件"；NO_RECENT_EVENT 仅表"5s 内无事件"
  // 仅前端推断（无后端 audio_input_tick / audio_last_seen 字段，🟡 Partial）
  // Phase 2 L2：声学状态实时更新（telephone_risk 专属，🟡 Partial 依赖后端 acoustic_state_delta）
  var _acousticStateHistory = [];           // 声学状态变化历史（NORMAL → ... → STRESS）
  var _acousticStatePanelEl = null;         // acoustic-state-panel DOM 元素引用
  var _audioHealthState = null;             // 'RECENT_EVENT' / 'NO_RECENT_EVENT' / 'UNAVAILABLE'
  var _lastAudioEventMs = null;             // 最近 audio event 时间戳（Unix ms）
  var _audioStaleThresholdMs = 5000;        // 5s 无事件 → NO_RECENT_EVENT
  var _audioStaleTimer = null;              // 周期检查定时器 handle

  function computeAudioHealth(lastEventMs, nowMs, scenarioHasAudio) {
    if (!scenarioHasAudio) return 'UNAVAILABLE';
    if (lastEventMs === null || lastEventMs === undefined) return 'NO_RECENT_EVENT';
    if (nowMs - lastEventMs > _audioStaleThresholdMs) return 'NO_RECENT_EVENT';
    return 'RECENT_EVENT';
  }

  function _scenarioHasAudioTrack() {
    var card = global.document.getElementById('audio-sensor-' + sid);
    if (!card) return false;
    var initial = card.getAttribute('data-audio-health');
    return initial !== 'UNAVAILABLE';
  }

  function _updateAudioHealthDOM(newState) {
    if (_audioHealthState === newState) return;
    _audioHealthState = newState;
    var card = global.document.getElementById('audio-sensor-' + sid);
    if (!card) return;
    card.setAttribute('data-audio-health', newState);
    var labelMap = {
      'RECENT_EVENT': '🔊 RECENT_EVENT',
      'NO_RECENT_EVENT': '⏸ NO_RECENT_EVENT',
      'UNAVAILABLE': '🔇 UNAVAILABLE'
    };
    var classMap = {
      'RECENT_EVENT': 'audio-active',
      'NO_RECENT_EVENT': 'audio-stale',
      'UNAVAILABLE': 'audio-na'
    };
    var badge = card.querySelector('.sensor-card-status');
    if (badge) {
      badge.textContent = labelMap[newState];
      badge.className = 'sensor-card-status ' + classMap[newState];
    }
  }

  function _startAudioStaleTimer() {
    if (_audioStaleTimer) return;
    _audioStaleTimer = setInterval(function () {
      if (!_scenarioHasAudioTrack()) return;
      var newState = computeAudioHealth(_lastAudioEventMs, Date.now(), true);
      _updateAudioHealthDOM(newState);
    }, 1000);
  }
  // Phase 2 L2：声学状态实时更新（telephone_risk 专属）
  function _updateAcousticState(msg) {
    // 从 evidence_delta 提取声学状态变化（golden_audio_state / acoustic_state_change）
    var stateNodes = msg.timeline || [];
    var audioStates = msg.audio || [];
    var stateChanges = [];

    // 收集 timeline 中的 golden_audio_state 节点
    stateNodes.forEach(function (n) {
      if (n.type === 'golden_audio_state' && n.summary) {
        stateChanges.push({
          timestamp: n.timestamp,
          summary: n.summary,
          phase: n.phase || ''
        });
      }
    });

    // 收集 audio 中的声学状态变化（acoustic_state_change 字段）
    audioStates.forEach(function (a) {
      if (a.acoustic_state_change) {
        stateChanges.push({
          timestamp: a.timestamp || Date.now(),
          summary: a.acoustic_state_change,
          phase: ''
        });
      }
    });

    if (stateChanges.length === 0) return;

    // 更新历史记录（去重）
    stateChanges.forEach(function (sc) {
      var key = sc.timestamp + '@' + sc.summary;
      if (!_acousticStateHistory.some(function (h) { return h.key === key; })) {
        _acousticStateHistory.push({ key: key, ...sc });
      }
    });

    // 渲染到面板
    _renderAcousticStatePanel();
  }

  function _renderAcousticStatePanel() {
    var panel = global.document.getElementById('acoustic-state-panel-' + sid);
    if (!panel) return;

    if (_acousticStateHistory.length === 0) {
      panel.innerHTML = '<p class="acoustic-state-note muted">声学状态：观察中...</p>';
      return;
    }

    var phasesHtml = _acousticStateHistory.map(function (h) {
      var phase = '';
      ['NORMAL', 'ATTENTION', 'AROUSAL', 'STRESS'].forEach(function (p) {
        if (h.summary.toUpperCase().indexOf(p) >= 0) phase = p;
      });
      var phaseClass = phase ? 'phase-' + phase.toLowerCase() : 'phase-unknown';
      return '<li class="acoustic-phase ' + phaseClass + '">' +
        '<span class="phase-time">' + h.timestamp + 's</span>' +
        '<span class="phase-label">' + (phase || 'STATE') + '</span>' +
        '<span class="phase-desc">' + h.summary + '</span>' +
        '</li>';
    }).join('');

    panel.innerHTML =
      '<h3 class="acoustic-state-title">🔊 声学状态变化</h3>' +
      '<ol class="acoustic-timeline">' + phasesHtml + '</ol>' +
      '<p class="acoustic-state-note muted">数据来源：Golden Case manifest 声明式声学状态机（provenance=SIMULATED）；系统不调用 ASR / LLM，不推导当事人心理或诈骗判定（VM-9）</p>';
  }

  // Phase 3 Waveform：RMS 连续波形绘制（telephone_risk 专属，VM-9 零推理）
  // 数据源：evidence_delta.rms_window（list[float]，最近 N 个 RMS 采样）
  // Canvas 暗底 + 渐变紫色柱状图，bar 高度 = rms * 50%（最大值归一化到 canvas 高度）
  function _drawWaveform(sid, rmsWindow) {
    var canvas = global.document.getElementById('waveform-canvas-' + sid);
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var w = canvas.width || canvas.clientWidth;
    var h = canvas.height || canvas.clientHeight;
    // 清屏（暗底 #1e293b）
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(0, 0, w, h);
    if (!rmsWindow || rmsWindow.length === 0) {
      ctx.fillStyle = '#475569';
      ctx.font = '11px monospace';
      ctx.textAlign = 'center';
      ctx.fillText('等待音频采样…', w / 2, h / 2 + 4);
      return;
    }
    var n = rmsWindow.length;
    var barW = Math.max(1, Math.floor(w / n));
    var gap = Math.max(1, Math.ceil(w / n) - barW);
    // RMS 范围 [0,1]，映射到 [h*0.1, h]（底部留 10% padding）
    var maxH = h * 0.9;
    for (var i = 0; i < n; i++) {
      var rms = Math.min(1, Math.max(0, parseFloat(rmsWindow[i]) || 0));
      var barH = rms * maxH;
      var x = i * (barW + gap);
      // 渐变色：低 RMS 蓝色 → 高 RMS 紫色
      var r = Math.round(139 + (168 - 139) * rms);
      var g = Math.round(92 + (48 - 92) * rms);
      var b = Math.round(198 + (207 - 198) * rms);
      ctx.fillStyle = 'rgb(' + r + ',' + g + ',' + b + ')';
      ctx.fillRect(x, h - barH, barW, barH);
    }
  }


  // Phase 1 L5：Provenance 快捷入口降级处理（浏览器原生 href 已可展开 details）
  function _bindWhyBelieveLinks() {
    var links = global.document.querySelectorAll('.why-believe-link');
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener('click', function () {
        var targetId = this.getAttribute('data-target');
        if (!targetId) return;
        var details = global.document.getElementById(targetId);
        if (details && details.tagName === 'DETAILS' && !details.open) {
          details.open = true;
        }
      });
    }
  }
  // ============================================================
  // PR-B：建议动作 → 人话映射（DESIGN §4.5）。
  var _ACTION_ZH = {
    MONITOR: '继续观察',
    NOTIFY_FAMILY: '通知家属',
    ESCALATE_COMMUNITY: '升级社区'
  };
  // PR-B：reason_summary 语义整理（枚举→人话同义映射，Owner 红线 §7.9）。
  // 来源：RuleBasedDecisionPolicy.routing_table 的 human_reason（第三元素）+ 实时风险评估器补充。
  // risk_delta.reason_summary 直接包含这些人话字符串，映射做同义润色/兜底。
  var _REASON_ZH = {
    // Routing table 原始 reason → 显示文案（同义润色）
    '异常停留': '停留超过阈值',
    '重复访问': '检测到重复访问',
    '未在白名单': '待核实到访',
    '异常时段访问': '夜间异常访问',
    '多风险规则同时命中': '高风险逼近（多规则命中）',
    // 实时风险评估器 / 声学风险补充
    'acoustic_state_change': '声学状态变化',
    'voice_stress_elevated': '语音应激升高',
    'telephone_interaction': '电话交互进行中',
    // 兼容旧 event_type 键（去重兜底）。
    // 注意：visit_normal → "异常时段访问"并非笔误——本系统 visit_normal 仅在
    // is_odd_hour 叠加时产生并进入决策（decision_policy.routing_table 该行 reason
    // 即"异常时段访问"），此映射忠实镜像服务端语义，勿改。
    repeated_visit_detected: '检测到重复访问',
    abnormal_dwell: '停留超过阈值',
    visit_normal: '异常时段访问',
    visit_pending_verify: '待核实到访',
    high_risk_approach: '高风险逼近',
    odd_hour_visit: '夜间访问'
  };
  // P0-3: BEHAV 映射（event_type → 行为里程碑 icon/color/label）。
  // 对齐原 Demo b593a01 BEHAV 表，枚举→人话同义映射，不扩展语义。
  var _BEHAV = {
    visit_normal:         { icon: '👤', label: '首次出现',   color: '#0891b2' },
    visit_pending_verify: { icon: '🔍', label: '待核实到访', color: '#0ea5e9' },
    abnormal_dwell:       { icon: '⏱',  label: '停留超过阈值', color: '#d97706' },
    repeat_visit:         { icon: '🔁', label: '再次出现',   color: '#7c3aed' },
    high_risk_approach:   { icon: '⚠',  label: '高风险逼近', color: '#dc2626' },
  };
  // LP-2：实时感知聚合状态（视觉 + 音频），perception_delta 更新 vision、evidence_delta 更新 audio。
  var seeState = { vision: [], audio: [] };
  // 空态"当前 N 人在场"：perception_delta 跟踪（只渲染服务端计数，零推理）。
  var lastPersons = 0;
  var seenRefs = new Set();
  var seenAudio = new Set();
  var seenCaseTime = new Set();
  var ws = null;
  var sid = '';
  var _narrativeMode = 'neutral';
  // （_MARKERS / _COLORS / _CLASS_ZH / _AUDIO_KIND_ZH / _ACTION_ZH / _REASON_ZH / _BEHAV /
  //   seeState 声明见上方，勿在此重复声明——重复 var 声明会静默覆盖且掩盖漂移）
  var _sessionStart = null;
  var _sessionTimer = null;

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // LP-4：Toast 事件涌现（右下角飞入，3.2s 后淡出）。新证据/风险变化的视觉反馈。
  var _lastToastTs = 0;
  var _lastToastText = '';
  function _toast(text, opts) {
    if (typeof global.document === 'undefined') return;
    if (typeof global.document.createElement !== 'function') return;
    // 节流：同类 toast 5 秒内不重复弹（opts.throttle=true 时启用）。
    var now = Date.now();
    if (opts && opts.throttle) {
      if (text === _lastToastText && now - _lastToastTs < 5000) return;
    }
    _lastToastTs = now;
    _lastToastText = text;
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

  // PR-B：时间线节点 summary → 人话（DESIGN §4.4：只改前端渲染文案映射，不新增事实）。
  // 支持类型：frame / audio / resolution / golden_audio_state / golden_memory_ref / golden_variant / golden_cross_modal
  function _humanSummary(n) {
    var s = n.summary || '';
    var t = n.type || '';

    if (t === 'frame') {
      var m = /frame \d+: (\d+) 检测, (\d+) 警告/.exec(s);
      if (m) {
        if (Number(m[2]) > 0) return '检测到 ' + m[1] + ' 个目标 · 风险升级（' + m[2] + ' 项警告）';
        if (Number(m[1]) > 0) return '检测到 ' + m[1] + ' 个目标';
        return '画面中暂无目标';
      }
    } else if (t === 'audio') {
      var am = /audio \d+: (\S+)/.exec(s);
      if (am) return '听到 ' + (_AUDIO_KIND_ZH[am[1]] || am[1]);
    } else if (t === 'resolution') {
      // "处置完成：warning abc123 由 family「NOTIFY_FAMILY」（community_done）"
      return s.replace(/处置完成：warning ([a-f0-9]+) 由 (\w+)「(\w+)」.*/,
        '处置完成：预警 $1 由 $2「$3」');
    } else if (t === 'golden_audio_state') {
      // "声学状态 ATTENTION" → "声学状态变化：ATTENTION（关注态）"
      var phaseMatch = /声学状态\s+(\w+)/.exec(s);
      if (phaseMatch) {
        var phase = phaseMatch[1];
        var phaseZh = { NORMAL: '平静态', ATTENTION: '关注态', AROUSAL: '唤起态', STRESS: '应激态' }[phase] || phase;
        return '声学状态变化：' + phase + '（' + phaseZh + '）';
      }
      return s;
    } else if (t === 'golden_memory_ref') {
      // "引用历史 ep_001（ep_002）" → "跨日记忆关联：引用 ep_001（当前 ep_002）"
      return s.replace(/引用历史\s+(\S+)（(\S+)）/, '跨日记忆关联：引用 $1（当前 $2）');
    } else if (t === 'golden_variant') {
      // "A/B variant: case_a (Normal telephone conversation - baseline)"
      var varMatch = /case_([ab])/.exec(s);
      if (varMatch) {
        var v = varMatch[1];
        var label = v === 'a' ? '基线对话（正常通话）' : '声学应激升级（风险信号）';
        return '场景切换：Case ' + v.toUpperCase() + ' — ' + label;
      }
      return s;
    } else if (t === 'golden_cross_modal') {
      // "Cross-modal: phone_interaction ↔ voice_stress_elevated (SUPPORTS)"
      return s.replace(/Cross-modal:\s*(\S+)\s*↔\s*(\S+)\s*\((\w+)\)/,
        '跨模态佐证：$1 ↔ $2 （$3）');
    }
    // 兜底：直接返回 summary（golden 节点的 summary 已是人话）
    return s;
  }

  // P0-3: 友好访客名（visitor_id → "访客#1"）
  function _friendlyVisitor(vid) {
    if (!vid) return '访客';
    var ls = __LiveState.visitorSeq;
    if (!ls[vid]) { __LiveState.visitorSeqN += 1; ls[vid] = '访客#' + __LiveState.visitorSeqN; }
    return ls[vid];
  }
  // P0-3: 添加行为里程碑（去重 + 限长）
  function _addBehavior(ev) {
    if (!ev || !ev.key || __LiveState.behaviorSeen[ev.key]) return;
    __LiveState.behaviorSeen[ev.key] = true;
    ev.seq = (++__LiveState.behaviorN);
    __LiveState.behaviorEvents.unshift(ev);
    if (__LiveState.behaviorEvents.length > __LiveState.behaviorMax)
      __LiveState.behaviorEvents.length = __LiveState.behaviorMax;
  }
  // P0-3: 从 perception_events + warnings 累积行为里程碑
  function _ingestBehavior(perceptionEvents, warnings) {
    if (!perceptionEvents) return;
    for (var i = 0; i < perceptionEvents.length; i++) {
      var pe = perceptionEvents[i];
      if (!pe || !pe.event_type) continue;
      var vid = pe.visitor_id || '';
      var who = _friendlyVisitor(vid);
      if (vid && !__LiveState.visitorFirst[vid]) {
        __LiveState.visitorFirst[vid] = true;
        _addBehavior({ key: 'enter|' + vid, icon: '👤', label: '首次出现', color: '#0891b2',
          who: who, detail: '进入' + (pe.location || '门口') + '画面' });
      }
      var bm = _BEHAV[pe.event_type] || { icon: '•', label: pe.event_type, color: '#64748b' };
      _addBehavior({
        key: 'pe|' + vid + '|' + pe.event_type + '|' + (pe.repeat_count || 0),
        icon: bm.icon, label: bm.label, color: bm.color,
        who: who, score: typeof pe.score === 'number' ? pe.score : null,
        repeat: pe.repeat_count != null ? pe.repeat_count : null,
        detail: pe.location ? ('位置 ' + pe.location) : ''
      });
    }
    if (!warnings) return;
    for (var j = 0; j < warnings.length; j++) {
      var w = warnings[j];
      if (!w || !w.warning_id) continue;
      var wk = 'warn|' + w.warning_id;
      if (__LiveState.behaviorSeen[wk]) continue;
      var rl = w.risk_level || 'UNKNOWN';
      _addBehavior({
        key: wk, icon: '⚠', label: '生成风险预警（' + rl + '）',
        color: rl === 'HIGH' ? '#dc2626' : rl === 'MEDIUM' ? '#d97706' : '#64748b',
        who: '', detail: (w.reason_summary || []).join('、')
      });
    }
  }
  // P0-2: 晚连恢复 —— 从 snapshot 消息重建跨帧 state
  function _applySnapshot(msg) {
    if (!msg || typeof msg !== 'object') return;
    var ls = __LiveState;
    // 警告保活
    if (msg.state_map) { ls.warningMap = msg.state_map; }
    if (msg.warnings) {
      for (var i = 0; i < msg.warnings.length; i++) {
        var w = msg.warnings[i];
        if (w && w.warning_id) ls.warningMap[w.warning_id] = w;
      }
    }
    // 行为事件（首连快照含历史里程碑）
    if (msg.behavior_events) {
      ls.behaviorEvents = msg.behavior_events;
      ls.behaviorN = msg.behavior_events.length;
      ls.visitorSeqN = msg.visitor_seq_n || 0;
    }
    // 三端命令累积
    if (msg.routed_commands) {
      var types = ['family', 'community', 'log_only'];
      for (var t = 0; t < types.length; t++) {
        var arr = msg.routed_commands[types[t]] || [];
        for (var k = 0; k < arr.length; k++) {
          var c = arr[k];
          var wid = c && c.warning_id;
          if (!wid) continue;
          if (!ls.commandMap[wid]) ls.commandMap[wid] = { family: new Map(), community: new Map(), log_only: new Map() };
          var map = ls.commandMap[wid][types[t]];
          var cid = c.command_id || '__null__';
          if (!map.has(cid)) { map.set(cid, c); if (map.size > 24) { var old = map.keys().next().value; map.delete(old); } }
        }
      }
    }
    // 风险信号
    if (msg.risk_signals) {
      for (var s = 0; s < msg.risk_signals.length; s++) {
        var sig = msg.risk_signals[s];
        if (!sig || !sig.signal_id) continue;
        if (sig.transition === 'cleared') {
          ls.riskSignalMap.delete(sig.signal_id);
          if (sig.paired_signal_id) ls.riskSignalMap.delete(sig.paired_signal_id);
        } else {
          ls.riskSignalMap.set(sig.signal_id, Object.assign({}, sig, { expiresAt: Date.now() + ls.rtTtlMs }));
        }
      }
    }
    // 帧信息
    if (msg.frame_index != null) ls.lastFrameIndex = msg.frame_index;
    if (msg.loop_count != null) ls.lastLoopCount = msg.loop_count;
    // P0: 首连快照也更新头部 overlay（帧号/循环/Case Time），避免连上时显示 '–'
    // 注意：snapshot 的帧信息在 meta 中，不在顶层
    var snapMeta = msg.meta || {};
    var of = global.document.getElementById('ov-frame-' + sid);
    if (of && snapMeta.frame_index != null) of.textContent = snapMeta.frame_index;
    var ol = global.document.getElementById('ds-loop-' + sid);
    if (ol && snapMeta.loop_count != null) ol.textContent = snapMeta.loop_count;
    var oc = global.document.getElementById('ov-time-' + sid);
    if (oc) oc.textContent = '00:00';
    var os = global.document.getElementById('demo-stat-' + sid);
    if (os) os.style.display = 'flex';
    var odsf = global.document.getElementById('ds-frame-' + sid);
    if (odsf && snapMeta.frame_index != null) odsf.textContent = snapMeta.frame_index;
    // 触发全量重渲染
    _renderAllFromSnapshot();
  }
  function _renderAllFromSnapshot() {
    // 用最后收到的 risk_delta / perception_delta 数据重渲染
    // （snapshot 本身不含完整 delta，依赖后续帧 delta 触发渲染；此处仅恢复 state）
    _renderBehaviorTimeline();
    _renderRiskSignals();
    // LP-3：快照中的行为事件 → 感知流（首连时补推，确保感知流有初始内容）。
    var snapBehaviors = __LiveState.behaviorEvents || [];
    if (snapBehaviors.length) {
      snapBehaviors.forEach(function (ev) {
        if (!ev || !ev.key) return;
        var dedupKey = 'snap|' + ev.key;
        if (_perceptionStream._seenRiskTransitions.has(dedupKey)) return;
        _perceptionStream._seenRiskTransitions.add(dedupKey);
        _perceptionStream.push({
          timestamp: '--:--:--',
          icon: ev.icon || '•',
          label: ev.label || '',
          detail: ev.who || ev.detail || '',
          type: 'behavior'
        });
      });
      _renderPerceptionStream();
    }
  }

  // T1.3：时间轴节点「人话优先」——首字段为人话 summary，技术信息收进 meta 灰字（不新增事实，仅改渲染）。
  // P0-11.x-3：AUDIO 节点加 data-kind + data-time（case_time 秒），供 audio_sync.js 点击 → seek/play。
  function _buildTimelineNode(n) {
    var marker = _MARKERS[n.modality] || '•';
    var color = _COLORS[n.modality] || '#3b4a5a';
    var summary = _humanSummary(n) || _esc(n.type);
    var metaParts = [ _esc(n.timestamp), marker + ' ' + _esc(n.modality), _esc(n.stage) ];
    // P0-11.x-3：AUDIO 节点附加 data-kind（= n.type，AudioPerceptionKind 枚举）+ data-time（case_time 秒）。
    // n.case_time 由 live_adapter._build_timeline 在 audio 节点注入（相对最早证据 T0 的秒）；
    // 缺失 → 不加 data-time（audio_sync.js 回退到 0，即从头播放）。
    var audioAttrs = '';
    if (n.modality === 'AUDIO') {
      audioAttrs = ' data-kind="' + _esc(n.type || '') + '"';
      if (n.case_time != null && isFinite(Number(n.case_time))) {
        audioAttrs += ' data-time="' + Number(n.case_time).toFixed(3) + '"';
      }
    }
    return '<li class="tl-item" data-step="' + _esc(n.timestamp) + '" data-ref="' + _esc(n.ref) + '"' + audioAttrs + '>' +
      '<span class="tl-dot" style="background:' + color + '"></span>' +
      '<div class="tl-body">' +
      '<div class="tl-summary">' + _esc(summary) + '</div>' +
      '<div class="tl-meta muted">' + metaParts.join(' · ') + '</div>' +
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
  // P0-3: 渲染行为时间线（里程碑列表，最新在上）
  function _renderBehaviorTimeline() {
    var body = global.document.getElementById('behavior-timeline-' + sid);
    if (!body) return;
    var events = __LiveState.behaviorEvents || [];
    if (!events.length) {
      body.innerHTML = '<div class="tl-empty">等待访客行为...</div>';
      return;
    }
    body.innerHTML = events.slice(0, 20).map(function (ev) {
      var who = ev.who ? (' <span class="muted">. ' + _esc(ev.who) + '</span>') : '';
      var detail = ev.detail ? (' <span class="tl-meta">' + _esc(ev.detail) + '</span>') : '';
      var score = ev.score != null ? (' <span class="muted">' + Number(ev.score).toFixed(2) + '</span>') : '';
      var repeat = ev.repeat != null ? (' <span class="muted">第' + ev.repeat + '次</span>') : '';
      // Use template literal to avoid quote-escaping hell
      var html = '<div class="tl-item" style="border-left:3px solid '
        + ev.color + ';padding-left:10px;margin:4px 0">';
      html += '<span class="tl-type" style="color:' + ev.color + '">'
        + _esc(ev.icon) + ' ' + _esc(ev.label) + '</span>';
      html += who + score + repeat + detail;
      html += '</div>';
      return html;
    }).join('');
  }

  // LIVE-PERCEPTION-STREAM-SPEC：右侧感知流（CURRENT STATE + RECENT CHANGES + HISTORY）
  var _perceptionStream = {
    entries: [],        // 当前显示的条目（最多 10 条）
    history: [],        // 折叠的历史条目
    maxVisible: 10,
    maxHistory: 50,
    // 持续状态追踪
    _lastPersons: 0,
    _lastPersonMs: null,
    _lastAudioKind: '',
    _lastRiskLevel: '',
    _lastRiskTrans: '',
    _lastCaseTime: null,
    // 去重标记
    _seenPersonCount: -1,
    _seenAudioEventIds: new Set(),
    _seenRiskTransitions: new Set(),
    push: function(entry) {
      this.entries.unshift(entry);
      if (this.entries.length > this.maxVisible) {
        this.history.push(this.entries.pop());
      }
      if (this.history.length > this.maxHistory) this.history.shift();
    },
    clear: function() {
      this.entries = [];
      this.history = [];
      this._lastPersons = 0;
      this._lastPersonMs = null;
      this._lastAudioKind = '';
      this._lastRiskLevel = '';
      this._lastRiskTrans = '';
      this._lastCaseTime = null;
      this._seenPersonCount = -1;
      this._seenAudioEventIds = new Set();
      this._seenRiskTransitions = new Set();
    }
  };

  function _formatCaseTime(s) {
    if (s == null) return '--:--:--';
    var total = Math.floor(Number(s));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var sec = total % 60;
    return (h > 0 ? h + ':' : '') +
      String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
  }

  function _renderPerceptionStream() {
    var ps = global.document.getElementById('perception-stream-' + sid);
    if (!ps) return;
    var stateEl = global.document.getElementById('ps-state-' + sid);
    var recentEl = global.document.getElementById('ps-recent-' + sid);
    var historyEl = global.document.getElementById('ps-history-' + sid);
    var historyListEl = global.document.getElementById('ps-history-list-' + sid);
    var historyCountEl = global.document.getElementById('ps-history-count-' + sid);
    var personEl = global.document.getElementById('ps-person-' + sid);
    var audioEl = global.document.getElementById('ps-audio-' + sid);
    var riskEl = global.document.getElementById('ps-risk-' + sid);
    if (!stateEl || !recentEl) return;

    // CURRENT STATE
    var personDuration = '';
    if (_perceptionStream._lastPersonMs != null) {
      var dur = ((Date.now() - _perceptionStream._lastPersonMs) / 1000).toFixed(1);
      personDuration = ' 持续在场 ' + dur + 's';
    }
    if (personEl) {
      if (_perceptionStream._lastPersons > 0) {
        personEl.style.display = '';
        personEl.querySelector('.ps-label').textContent =
          '👤 ' + _perceptionStream._lastPersons + ' 人' + personDuration;
      } else {
        personEl.style.display = 'none';
      }
    }
    if (audioEl) {
      if (_perceptionStream._lastAudioKind) {
        audioEl.style.display = '';
        audioEl.querySelector('.ps-label').textContent =
          '🔊 最近检测到 ' + _perceptionStream._lastAudioKind;
      } else {
        audioEl.style.display = 'none';
      }
    }
    if (riskEl) {
      var riskLevel = _perceptionStream._lastRiskLevel;
      if (riskLevel) {
        riskEl.style.display = '';
        var riskTrans = _perceptionStream._lastRiskTrans === 'raised' ? '观察 → 关注' :
                         _perceptionStream._lastRiskTrans === 'cleared' ? '关注 → 解除' : '—';
        riskEl.querySelector('.ps-label').textContent =
          '⚠ 风险：' + riskLevel + ' · ' + riskTrans;
        riskEl.classList.toggle('active', _perceptionStream._lastRiskTrans === 'raised');
      } else {
        riskEl.style.display = 'none';
        riskEl.classList.remove('active');
      }
    }

    // RECENT CHANGES
    var entries = _perceptionStream.entries;
    if (entries.length === 0) {
      var emptyEl = global.document.getElementById('ps-recent-empty-' + sid);
      if (emptyEl) emptyEl.style.display = '';
      recentEl.innerHTML = '';
    } else {
      var emptyEl2 = global.document.getElementById('ps-recent-empty-' + sid);
      if (emptyEl2) emptyEl2.style.display = 'none';
      recentEl.innerHTML = entries.slice(0, 10).map(function(e) {
        return '<div class="ps-entry" data-type="' + _esc(e.type) + '">' +
          '<span class="ps-time">' + _esc(e.time || e.timestamp) + '</span>' +
          '<span class="ps-icon">' + _esc(e.icon) + '</span>' +
          '<span class="ps-label">' + _esc(e.label) + '</span>' +
          (e.detail ? '<div class="ps-detail">' + _esc(e.detail) + '</div>' : '') +
          '</div>';
      }).join('');
    }

    // HISTORY
    if (historyEl && historyListEl && historyCountEl) {
      if (_perceptionStream.history.length > 0) {
        historyEl.style.display = '';
        historyCountEl.textContent = _perceptionStream.history.length;
        historyListEl.innerHTML = _perceptionStream.history.slice(0, 10).reverse().map(function(e) {
          return '<div class="ps-entry" data-type="' + _esc(e.type) + '">' +
       '<span class="ps-time">' + _esc(e.time || e.timestamp) + '</span>' +
            '<span class="ps-icon">' + _esc(e.icon) + '</span>' +
            '<span class="ps-label">' + _esc(e.label) + '</span>' +
            (e.detail ? '<div class="ps-detail">' + _esc(e.detail) + '</div>' : '') +
            '</div>';
        }).join('');
      } else {
        historyEl.style.display = 'none';
      }
    }
  }
  // P0-1/P1-2: 通知 live_actions 三端命令已更新（通过自定义事件）
  function _renderCommandMap() {
    if (typeof global.document === 'undefined') return;
    if (typeof global.document.dispatchEvent !== 'function') return;  // mock DOM guard
    var evt = new global.CustomEvent('__liveCommandsUpdated', {
      detail: { commandMap: __LiveState.commandMap }
    });
    global.document.dispatchEvent(evt);
  }
  // P2-2: 风险信号 TTL 检查 + 渲染
  function _tickRiskSignals() {
    var ls = __LiveState;
    var now = Date.now();
    var keys = Array.from(ls.riskSignalMap.keys());
    for (var i = 0; i < keys.length; i++) {
      var sig = ls.riskSignalMap.get(keys[i]);
      if (sig && sig.expiresAt && now > sig.expiresAt) {
        ls.riskSignalMap.delete(keys[i]);
      }
    }
    _renderRiskSignals();
  }
  // P2-2: 渲染实时风险信号卡
  function _renderRiskSignals() {
    var body = global.document.getElementById('live-signals-' + sid);
    if (!body) return;
    var signals = Array.from(__LiveState.riskSignalMap.values());
    if (!signals.length) {
      body.innerHTML = '<div class="tl-empty">当前无进行中风险信号</div>';
      return;
    }
    body.innerHTML = signals.map(function (s) {
      var catLabel = s.category || '?';
      var sevLabel = s.severity_hint != null ? Number(s.severity_hint).toFixed(2) : '?';
      var subj = s.subject_type === 'VISITOR' ? '访客' : (s.subject_type || '?');
      return '<div class="rt-sig" style="border-left-color:'
        + (s.transition === 'raised' ? '#dc2626' : '#64748b') + '">'
        + '<span class="rt-sid">' + (s.signal_id || '?').slice(0, 8) + '</span>'
        + '<span class="rt-cat">' + _esc(catLabel) + '</span>'
        + '<span class="rt-sev">强度 ' + sevLabel + '</span>'
        + '<span class="rt-subj">' + _esc(subj) + '</span>'
        + '</div>';
    }).join('');
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
    var head = (msg.case_time != null ? Number(msg.case_time).toFixed(1) + 's' : '—');
    el.innerHTML = '<div class="lp-head">Visual perception <span class="muted">' + head + '</span></div>' +
      (rows ? '<ul>' + rows + '</ul>' : '<span class="muted">（当前无检测）</span>');
    // PR-B：空态"当前 N 人在场"跟踪（③ 风险卡空态文案同源）。
    lastPersons = (msg.detections || []).length;
    _perceptionStream._lastPersons = lastPersons;
    _perceptionStream._lastCaseTime = msg.case_time != null ? Number(msg.case_time) : null;
    if (lastPersons > 0 && _perceptionStream._lastPersonMs == null) {
      _perceptionStream._lastPersonMs = Date.now();
    } else if (lastPersons === 0) {
      _perceptionStream._lastPersonMs = null;
    }
    // LP-3：首次出现 → 推感知流条目（去重：仅当 count 从 0 变正时触发）。
    var prevPersonCount = _perceptionStream._seenPersonCount;
    _perceptionStream._seenPersonCount = lastPersons;
    if (lastPersons > 0 && prevPersonCount < 0) {
      _perceptionStream.push({
        timestamp: _perceptionStream._lastCaseTime != null
          ? _formatCaseTime(_perceptionStream._lastCaseTime) : '--:--:--',
        icon: '👤',
        label: '首次出现',
        detail: '检测到 ' + lastPersons + ' 人进入画面',
        type: 'behavior'
      });
    }
    _renderPerceptionStream();
    var empty = global.document.getElementById('lrk-empty-' + sid);
    if (empty && empty.style.display !== 'none') {
      empty.textContent = '🔴 实时观察中 · 当前 ' + lastPersons + ' 人在场，风险尚未触发';
    }
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

  // P2-1：timeline 折叠（用户反馈"打开就 210+ 节点塞满"）
  // 设计：seenRefs 始终记录所有曾见过的 ref；DOM 只保留最新 TIMELINE_MAX_VISIBLE 个 runtime 节点。
  // 折叠时裁剪最早节点；展开时**从 seenRefs 重建全量**（避免重连 WS 的 1.5s 抖动）。
  // 保留 golden_ 节点（manifest 派生的预期，**不**裁剪）。
  var TIMELINE_MAX_VISIBLE = 30;  // 默认折叠态最多展示 30 个
  var _timelineExpanded = false;  // 当前是否展开全量
  var _timelineNodesByRef = {};  // ref → node（用于展开时重建 DOM）
  function _trimTimelineDom(ul) {
    if (!ul) return;
    var items = ul.querySelectorAll('li.tl-item[data-ref]');
    var runtimeItems = [];
    for (var i = 0; i < items.length; i++) {
      var ref = items[i].getAttribute('data-ref') || '';
      if (ref.indexOf('golden://') !== 0) runtimeItems.push(items[i]);
    }
    // 折叠态：裁剪到最新 N 个
    if (!_timelineExpanded && runtimeItems.length > TIMELINE_MAX_VISIBLE) {
      var toRemove = runtimeItems.length - TIMELINE_MAX_VISIBLE;
      for (var j = 0; j < toRemove; j++) {
        var node = runtimeItems[j];
        if (node && node.parentNode) node.parentNode.removeChild(node);
        // 注意：seenRefs 和 _timelineNodesByRef **不删**（保留以便展开时重建）
      }
    }
  }
  function _expandTimelineDom(ul) {
    // 展开：把 _timelineNodesByRef 中所有 runtime 节点按 ref 顺序重建到 DOM
    if (!ul) return;
    var refs = Object.keys(_timelineNodesByRef).filter(function (r) {
      return r.indexOf('golden://') !== 0;
    });
    // 按 frame_index 数值升序（仅 runtime 节点用 live://frame/N 命名）
    refs.sort(function (a, b) {
      var na = parseInt((a.match(/frame\/(\d+)/) || [0, 0])[1], 10);
      var nb = parseInt((b.match(/frame\/(\d+)/) || [0, 0])[1], 10);
      return na - nb;
    });
    // 找到 golden 节点（插在它们之前，避免覆盖）
    var firstGolden = ul.querySelector('li.tl-item-golden');
    for (var i = 0; i < refs.length; i++) {
      var ref = refs[i];
      if (ul.querySelector('li.tl-item[data-ref="' + ref + '"]')) continue;  // 已在 DOM
      var node = _timelineNodesByRef[ref];
      if (!node) continue;
      if (firstGolden && firstGolden.parentNode) {
        firstGolden.parentNode.insertBefore(node, firstGolden);
      } else {
        ul.appendChild(node);
      }
    }
  }
  function _renderTimelineMoreButton(ul) {
    if (!ul) return;
    var existing = ul.parentNode.querySelector('.tl-more-toggle');
    if (existing) existing.parentNode.removeChild(existing);
    var items = ul.querySelectorAll('li.tl-item[data-ref]');
    // 统计"已折叠的"（seenRefs 总数 - DOM 显示数；不含 golden 裁剪）
    var totalRuntime = 0;
    for (var r in _timelineNodesByRef) {
      if (r.indexOf('golden://') !== 0) totalRuntime++;
    }
    var shownRuntime = 0;
    for (var k = 0; k < items.length; k++) {
      var r2 = items[k].getAttribute('data-ref') || '';
      if (r2.indexOf('golden://') !== 0) shownRuntime++;
    }
    var hidden = totalRuntime - shownRuntime;
    if (hidden <= 0) return;
    var btn = document.createElement('div');
    btn.className = 'tl-more-toggle';
    btn.style.cssText = 'padding:8px 12px; text-align:center; cursor:pointer; color:#4a90d9; font-size:13px; border-top:1px solid #e3e8ee;';
    btn.textContent = _timelineExpanded
      ? '收起（共 ' + totalRuntime + ' 条）'
      : '查看更多（已折叠 ' + hidden + ' 条 / 共 ' + totalRuntime + ' 条）';
    btn.onclick = function () {
      _timelineExpanded = !_timelineExpanded;
      if (_timelineExpanded) {
        _expandTimelineDom(ul);
      } else {
        _trimTimelineDom(ul);
      }
      _renderTimelineMoreButton(ul);
    };
    ul.parentNode.insertBefore(btn, ul.nextSibling);
  }

  // ============================================================
  // Surface Independence（P0 修复）：语义事件处理 与 Surface 渲染解耦。
  // 原则：Surface 缺失 ≠ Runtime Fact 丢失。
  // - eventSeen（seenRefs/seenAudio/seenCaseTime）= 事件已处理（语义层），与 DOM 无关；
  //   事件不因某个 Surface 当前不存在而重新触发 Semantic Event；
  // - 各 Surface（.timeline / table.audio-table / case-time-track）独立降级渲染；
  //   【单场景契约】querySelector 取页面首个匹配——Live Viewer 当前为单场景页
  //   （.live-perception 同样取首个，见 _init），多场景同页前必须先 per-sid 化这些 selector。
  // - Surface 后现时由 _flushPendingSurfaces 从已保存 state 补渲染。
  // ============================================================
  var _pendingAudioRows = [];      // audio-table 缺失期间挂起的行 HTML
  var _pendingCaseTimeMarks = [];  // case-time-track 缺失期间挂起的标记 {sid, m}

  function _renderAudioRow(a) {
    var html = _buildAudioRow(a);
    var table = global.document.querySelector('table.audio-table');
    if (table) {
      table.insertAdjacentHTML('beforeend', html);
    } else {
      _pendingAudioRows.push(html);
    }
  }

  // timeline Surface 后现 → 从节点缓存补插（折叠态由 _trimTimelineDom 裁剪到最新 N 个）
  function _flushTimelineIntoDom(ul) {
    if (!ul) return;
    for (var ref in _timelineNodesByRef) {
      if (ref.indexOf('golden://') === 0) continue;
      if (ul.querySelector('li.tl-item[data-ref="' + ref + '"]')) continue;  // 已在 DOM
      var node = _timelineNodesByRef[ref];
      if (!node) continue;
      var firstGolden = ul.querySelector('li.tl-item-golden');
      if (firstGolden && firstGolden.parentNode) {
        firstGolden.parentNode.insertBefore(node, firstGolden);
      } else {
        ul.appendChild(node);
      }
    }
    _trimTimelineDom(ul);
    _renderTimelineMoreButton(ul);
  }

  // 每条 delta 处理完毕后调用：尝试将此前因 Surface 缺失而挂起的渲染项补上。
  // 只重放"渲染"，不重放语义事件——语义去重在入队前已完成（VM-8 幂等不破坏）。
  function _flushPendingSurfaces() {
    if (_pendingAudioRows.length) {
      var table = global.document.querySelector('table.audio-table');
      if (table) {
        for (var i = 0; i < _pendingAudioRows.length; i++) {
          table.insertAdjacentHTML('beforeend', _pendingAudioRows[i]);
        }
        _pendingAudioRows = [];
      }
    }
    var ul = global.document.querySelector('.timeline');
    if (ul) _flushTimelineIntoDom(ul);
    if (_pendingCaseTimeMarks.length) {
      var remaining = [];
      for (var j = 0; j < _pendingCaseTimeMarks.length; j++) {
        var item = _pendingCaseTimeMarks[j];
        var track = global.document.getElementById('case-time-track-' + item.sid);
        if (track) {
          var max = parseFloat(track.getAttribute('data-max') || '0') || 0;
          track.insertAdjacentHTML('beforeend', _buildCaseTimeMark(item.m, max));
        } else {
          remaining.push(item);
        }
      }
      _pendingCaseTimeMarks = remaining;
    }
  }

  function _applyDelta(msg) {
    // timeline 追加（幂等：ref 去重；P0-B 修复：数据缓存与 DOM 插入解耦——
    // 原实现 .timeline 缺失即 return，但 seenRefs 已标记 → 节点永久丢失。
    // 现改为：先构建节点并缓存 _timelineNodesByRef（数据层，供展开重建/Surface 后现补插），
    // DOM 插入独立降级）。
    (msg.timeline || []).forEach(function (n) {
      if (!n.ref || seenRefs.has(n.ref)) return;
      seenRefs.add(n.ref);
      // 数据层：构建节点 + 缓存（不依赖任何 Surface 存在）
      var nodeHtml = _buildTimelineNode(n);
      // 用临时容器解析为 DOM 节点
      var tmp = global.document.createElement('div');
      tmp.innerHTML = nodeHtml;
      var node = tmp.firstChild;
      if (node) _timelineNodesByRef[n.ref] = node;
      // 渲染层：.timeline 存在才操作 DOM
      var ul = global.document.querySelector('.timeline');
      if (!ul || !node) return;
      // 折叠态：直接 append（_trimTimelineDom 会裁剪旧的）
      // 展开态：插入到 golden 节点之前（避免覆盖）
      if (_timelineExpanded) {
        var firstGolden = ul.querySelector('li.tl-item-golden');
        if (firstGolden && firstGolden.parentNode) {
          firstGolden.parentNode.insertBefore(node, firstGolden);
        } else {
          ul.appendChild(node);
        }
      } else {
        ul.appendChild(node);
      }
      // P2-1：裁剪 + "查看更多"按钮（用户反馈"210+ 节点塞满"）
      _trimTimelineDom(ul);
      _renderTimelineMoreButton(ul);
      // LP-4：新事件涌现 toast（非 session 根节点才提示，避免首连刷屏）。
      // frame 节点节流（5 秒内同类不重复）；非 frame 节点立即弹。
      if (n.type !== 'session' && n.summary) {
        var summary = _humanSummary(n);
        var isFrame = n.type === 'frame';
        // 非风险帧 → 静态"目标检测中"，不逐帧弹"检测到 N 个目标"。
        if (isFrame && summary.indexOf('风险升级') < 0) {
          _toast('目标检测中', { throttle: true });
        } else if (isFrame) {
          _toast(summary, { throttle: false });
        } else {
          _toast(summary, { throttle: true });
        }
      }
    });
    // P0-3: 行为里程碑累积（从 perception_events + warnings 推导）
    _ingestBehavior(msg.perception_events, msg.warnings || []);
    _renderBehaviorTimeline();
    // LP-3：行为事件 → 感知流条目（去重：按 event_id + visitor_id 组合键）。
    (msg.perception_events || []).forEach(function (pe) {
      if (!pe || !pe.event_type) return;
      var vid = pe.visitor_id || '';
      var who = _friendlyVisitor(vid);
      var bm = _BEHAV[pe.event_type];
      if (!bm) return;
      var dedupKey = 'pe|' + (pe.event_id || pe.frame_index) + '|' + pe.event_type + '|' + vid;
      if (_perceptionStream._seenRiskTransitions.has(dedupKey)) return;
      _perceptionStream._seenRiskTransitions.add(dedupKey);
      var caseTime = pe.case_time != null
        ? _formatCaseTime(Number(pe.case_time))
        : (_perceptionStream._lastCaseTime != null ? _formatCaseTime(_perceptionStream._lastCaseTime) : '--:--:--');
      var label = bm.label;
      var detail = who;
      if (pe.repeat_count != null && pe.repeat_count > 1) {
        label = '再次出现';
        detail = who + '（第' + pe.repeat_count + '次）';
      } else if (pe.location) {
        detail += (detail ? ' · ' : '') + '位置 ' + pe.location;
      }
      _perceptionStream.push({
        timestamp: caseTime,
        icon: bm.icon,
        label: label,
        detail: detail,
        type: 'behavior'
      });
    });
    _renderPerceptionStream();
    // 更新三端命令显示
    _renderCommandMap();
    // audio 证据处理（幂等：event_id 去重；P0-A 修复：语义层与渲染层解耦——
    // 原实现 table 缺失即 return，导致 seeState/Audio Health/感知流全部不更新，
    // 且 seenAudio 先标记造成事件在本次 session 内永久丢失。
    // 现改为：语义状态无条件先行（纯内存，不依赖任何 DOM Surface），
    // audio-table 行渲染独立降级 + 挂起，Surface 后现由 _flushPendingSurfaces 补渲染）。
    (msg.audio || []).forEach(function (a) {
      var id = a.event_id || a.ref;
      if (!id || seenAudio.has(id)) return;
      seenAudio.add(id);
      // ── 语义层：Runtime Fact → Semantic State（无条件执行）──
      // LP-2：人话 kind 聚合（"AI 听到了"数据源）。
      var kz = _AUDIO_KIND_ZH[a.kind] || a.kind;
      if (seeState.audio.indexOf(kz) < 0) seeState.audio.push(kz);
      // Phase 1 L0：最近音频事件时间戳（Audio Health 三值状态机输入）
      _lastAudioEventMs = Date.now();
      // LP-3：音频事件 → 感知流条目（去重：按 event_id）。
      if (!_perceptionStream._seenAudioEventIds.has(id)) {
        _perceptionStream._seenAudioEventIds.add(id);
        _perceptionStream._lastAudioKind = kz;
        var at = a.case_time != null
          ? _formatCaseTime(Number(a.case_time))
          : '--:--:--';
        _perceptionStream.push({
          timestamp: at,
          icon: '🔊',
          label: '检测到' + kz,
          detail: '',
          type: 'audio'
        });
      }
      // ── 渲染层：各 Surface 独立降级（缺失由 _flushPendingSurfaces 补渲染）──
      _renderAudioRow(a);
      _renderSee();
      _updateAudioHealthDOM('RECENT_EVENT');
      _renderPerceptionStream();
    });
    // Case Time 标记追加（幂等：kind@time 去重；P1-C 修复：track 缺失不再丢标记，
    // 挂起 {sid, m} 由 _flushPendingSurfaces 按 track 当时的 data-max 重算补插）。
    (msg.case_time || []).forEach(function (m) {
      var key = m.kind + '@' + m.time;
      if (seenCaseTime.has(key)) return;
      seenCaseTime.add(key);
      var track = global.document.getElementById('case-time-track-' + sid);
      if (track) {
        var max = parseFloat(track.getAttribute('data-max') || '0') || 0;
        track.insertAdjacentHTML('beforeend', _buildCaseTimeMark(m, max));
      } else {
        _pendingCaseTimeMarks.push({ sid: sid, m: m });
      }
    });
    // counts 更新（frames 计数实时推进）
    if (msg.counts && msg.counts.n_frames != null) _updateFrames(msg.counts.n_frames);
    // PR-C：访客事件 chip（evidence_delta.counts.perception_events）。
    var ve = global.document.getElementById('ov-ve-' + sid);
    if (ve && msg.counts && msg.counts.perception_events != null) {
      ve.textContent = msg.counts.perception_events;
    }
    // Phase 2 L2：声学状态实时更新（telephone_risk 专属）
    _updateAcousticState(msg);
    // Phase 3 Waveform：RMS 连续波形绘制（telephone_risk 专属）
    _drawWaveform(sid, msg.rms_window);
    // Surface 补渲染：本条 delta 处理完毕后，尝试补上此前因 Surface 缺失而挂起的渲染项
    _flushPendingSurfaces();
  }

  // LP-1：真实同步帧流（frame_tick 心跳：frame_index / case_time / loop_count）。
  // 真实帧画面由 MJPEG 端点 (/mjpeg/{scenario_id}) 流式传输，浏览器原生解码 <img src="...">，
  // 无 Base64 over WS 开销。Frame N 的 case_time 与同帧 perception_delta 天然同步（VM-9）。
  // P2：Demo 状态面板实时更新（帧计数 / 循环次数 / 延迟 / Session 计时）。
  // 同步节流 overlay chips（帧号/Case Time），避免"画面不动、数字狂跳"体验差。
  var _lastFrameTs = 0;
  var FRAME_DISPLAY_INTERVAL_MS = 150;
  function _applyFrame(msg) {
    var img = global.document.getElementById('video-img-' + sid);
    // 首帧心跳到达：隐藏 placeholder（MJPEG 已在加载视频）
    if (img) {
      img.style.display = 'block';
      var ph = global.document.getElementById('video-ph-' + sid);
      if (ph) ph.style.display = 'none';
    }
    var badge = global.document.getElementById('live-badge-' + sid);
    if (badge) badge.style.display = 'inline-flex';

    // 节流：仅每 150ms 刷新一次 overlay chips，避免数字狂跳
    var now = (typeof performance !== 'undefined' && performance.now)
      ? performance.now() : Date.now();
    var shouldUpdateChips = (now - _lastFrameTs >= FRAME_DISPLAY_INTERVAL_MS);
    if (shouldUpdateChips) {
      _lastFrameTs = now;
      var f = global.document.getElementById('ov-frame-' + sid);
      if (f) f.textContent = msg.frame_index;
      // PR-C：Case Time chip（frame_tick.case_time，红线 §7.7 双标识）。
      // 显示为 MM:SS 格式（如 00:15），更符合人类阅读习惯。
      var ct = global.document.getElementById('ov-time-' + sid);
      if (ct && msg.case_time != null) {
        var totalSec = Math.floor(Number(msg.case_time));
        var mm = String(Math.floor(totalSec / 60)).padStart(2, '0');
        var ss = String(totalSec % 60).padStart(2, '0');
        ct.textContent = mm + ':' + ss;
      }
    }
    // P2：Demo 状态面板（首帧时显示，后续每秒刷新 Session 计时）。
    var stat = global.document.getElementById('demo-stat-' + sid);
    if (stat) {
      stat.style.display = 'flex';
      var df = global.document.getElementById('ds-frame-' + sid);
      if (df) df.textContent = msg.frame_index;
      var dl = global.document.getElementById('ds-loop-' + sid);
      if (dl) dl.textContent = msg.loop_count || 0;
      if (_sessionStart === null) { _sessionStart = Date.now(); _startSessionTimer(); }
    }
    // 延迟样本（server_ts 为网关 time.time()）。
    if (msg.server_ts != null) {
      var lat = global.document.getElementById('ds-latency-' + sid);
      if (lat) {
        var ms = Date.now() - (msg.server_ts * 1000);
        lat.textContent = ms + 'ms';
      }
    }
  }
  function _startSessionTimer() {
    if (_sessionTimer) return;
    _sessionTimer = setInterval(function() {
      if (!_sessionStart) return;
      var el = global.document.getElementById('ds-session-' + sid);
      if (!el) { clearInterval(_sessionTimer); _sessionTimer = null; return; }
      var sec = Math.floor((Date.now() - _sessionStart) / 1000);
      el.textContent = String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
    }, 1000);
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

  // PR-B：③ 风险解释卡片（✓ 人话原因格式）+ ③.5 实时风险信号（risk_transition）。
  // 数据源：risk_delta（risk_levels/reason_summary/recommended_actions/risk_transition）。
  // 红线：risk_transition 由服务端状态机判定，前端只渲染，绝不自行解释"空=CLEARED"（§7.8）。
  function _applyRiskDelta(msg) {
    var levels = msg.risk_levels || [];
    var reasons = msg.reason_summary || [];
    var actions = msg.recommended_actions || [];
    var card = global.document.getElementById('lrk-card-' + sid);
    var empty = global.document.getElementById('lrk-empty-' + sid);
    if (levels.length) {
      if (card) card.style.display = '';
      if (empty) empty.style.display = 'none';
      var lvlEl = global.document.getElementById('lrk-level-' + sid);
      if (lvlEl) {
        lvlEl.textContent = levels.join(' / ') + ' 风险';
        lvlEl.className = 'lrk-level' +
          (levels.indexOf('HIGH') >= 0 ? '' : levels.indexOf('MEDIUM') >= 0 ? ' medium' : ' low');
      }
      var frameEl = global.document.getElementById('lrk-frame-' + sid);
      if (frameEl && msg.case_time != null) {
        frameEl.textContent = Number(msg.case_time).toFixed(1) + 's';
      }
      var reasonsEl = global.document.getElementById('lrk-reasons-' + sid);
      if (reasonsEl) {
        reasonsEl.innerHTML = reasons.map(function (r) {
          return '<li>✓ ' + _esc(_REASON_ZH[r] || r) + '</li>';
        }).join('');
      }
      // P0-1: trigger chips（证据引用链：触发规则 chips + source_ref）
      var triggersEl = global.document.getElementById('lrk-triggers-' + sid);
      if (triggersEl) {
        var te = msg.trigger_events || [];
        if (te.length) {
          triggersEl.style.display = '';
          global.document.getElementById('lrk-trig-' + sid).innerHTML = te.map(function (t) {
            var label = _REASON_ZH[t.event_type] || t.event_type || '规则命中';
            // P1-1: 按 event_type 着色（对齐原 Demo TRIG_COLOR）
            var chipColor = '#d97706';
            if (t.event_type === 'high_risk_approach') chipColor = '#dc2626';
            else if (t.event_type === 'abnormal_dwell') chipColor = '#d97706';
            else if (t.event_type === 'repeat_visit') chipColor = '#7c3aed';
            else if (t.event_type === 'visit_normal') chipColor = '#0891b2';
            return '<span class="trig-chip" data-ref="live://rule/' + _esc(t.event_type)
              + '" style="border-color:' + chipColor + ';color:' + chipColor + '">'
              + _esc(label) + ' <span class="muted">' + Number(t.score).toFixed(2) + '</span></span>';
          }).join('');
        } else {
          triggersEl.style.display = 'none';
        }
      }
      // P0-1: 强度条（perception_score）
      var barWrap = global.document.getElementById('lrk-bar-wrap-' + sid);
      if (barWrap) {
        var ps = msg.perception_scores || [];
        if (ps.length) {
          barWrap.style.display = '';
          var maxScore = Math.max.apply(null, ps.map(Number));
          var pct = Math.round(maxScore * 100);
          global.document.getElementById('lrk-bar-' + sid).style.width = pct + '%';
          var scoreEl = global.document.getElementById('lrk-score-' + sid);
          if (scoreEl) scoreEl.textContent = '命中强度 ' + maxScore.toFixed(2);
        } else {
          barWrap.style.display = 'none';
        }
      }
      // P0-1: warning_id + device/elder 元数据
      var widEl = global.document.getElementById('lrk-wid-' + sid);
      if (widEl) widEl.textContent = (msg.warning_ids || []).slice(0, 1).map(function (w) { return w.slice(0, 8); }).join(',');
      var metaEl = global.document.getElementById('lrk-meta-' + sid);
      if (metaEl) {
        var parts = [];
        if (msg.device_id) parts.push('<span>📷 ' + _esc(msg.device_id) + '</span>');
        if (msg.elder_id) parts.push('<span>👤 ' + _esc(msg.elder_id) + '</span>');
        metaEl.innerHTML = parts.join('');
      }
      // P0-1: 跨帧保活（warningMap 防止风险卡随帧刷新生灭）
      var ls = __LiveState;
      (msg.warning_ids || []).forEach(function (wid) {
        ls.warningMap[wid] = levels[0] || 'UNKNOWN';
      });
      var recEl = global.document.getElementById('lrk-rec-' + sid);
      if (recEl) {
        var rec = actions.length
          ? actions.map(function (a) { return _ACTION_ZH[a] || a; }).join(' / ')
          : '继续观察';
        recEl.innerHTML = '建议：<b>' + _esc(rec) + '</b>';
      }
    } else {
      // 无风险 → 保留"观察中"状态卡（含 lastPersons 计数），不彻底消失（P1-1 超越改进）。
      if (card) card.style.display = 'none';
      if (empty) {
        empty.style.display = '';
        empty.textContent = '🔴 实时观察中 · 当前 ' + lastPersons + ' 人在场，风险尚未触发';
      }
    }
    // ③.5 实时风险信号：risk_transition 服务端状态机驱动（前端零推断）。
    _applyRiskSignal(msg);
    // P0-1 修复（P0-3 audit bug）：risk_delta 也会累积三端命令（按 recommended_action 路由）。
    _renderCommandMap();
    // LP-4 toast：仅在服务端判定 raised 跃迁时提示（无风险帧不刷屏）。
    if (msg.risk_transition === 'raised') _toast('风险升级：' + levels.join(' / '));
    // LP-3：风险跃迁 → 感知流条目（去重：按 transition 类型）。
    if (msg.risk_transition) {
      var rtKey = 'risk|' + msg.risk_transition + '|' + (levels.join(',') || '');
      if (!_perceptionStream._seenRiskTransitions.has(rtKey)) {
        _perceptionStream._seenRiskTransitions.add(rtKey);
        _perceptionStream._lastRiskLevel = levels.join(' / ') || '';
        _perceptionStream._lastRiskTrans = msg.risk_transition;
        var rtCaseTime = msg.case_time != null
          ? _formatCaseTime(Number(msg.case_time))
          : '--:--:--';
        var rtLabel = msg.risk_transition === 'raised'
          ? '风险状态：观察 → 关注'
          : msg.risk_transition === 'cleared'
            ? '风险状态：关注 → 解除'
            : '风险状态更新';
        _perceptionStream.push({
          timestamp: rtCaseTime,
          icon: msg.risk_transition === 'raised' ? '⚠' : '✓',
          label: rtLabel,
          detail: (msg.reason_summary || []).join(' · '),
          type: 'risk'
        });
        _renderPerceptionStream();
      }
    }
  }

  // ③.5 实时风险信号（RAISED 亮卡 / CLEARED 熄卡 / active 更新内容）。
  // P0-1：risk_signals 内嵌于 risk_delta（Owner Q1 方案 B），信号实体带 signal_id/subject/severity。
  function _applyRiskSignal(msg) {
    var t = msg.risk_transition;
    if (!t) return;  // 无 transition（指纹未变 / 首连无风险）→ 无信号（§4.6 契约表）
    var box = global.document.getElementById('live-signals-' + sid);
    var empty = global.document.getElementById('live-signals-empty-' + sid);
    if (!box) return;
    if (t === 'raised' || t === 'active') {
      var levels = (msg.risk_levels || []).join(' / ');
      var time = msg.case_time != null
        ? Number(msg.case_time).toFixed(1) + 's'
        : '—';
      // P0-1：信号实体（signal_id / subject / severity）
      var signals = msg.risk_signals || [];
      var sigHtml = '';
      if (signals.length) {
        var sig = signals[0];
        var sid_val = sig.signal_id || 'sig-unknown';
        var subject = sig.subject_id || sig.visitor_instance_id || '访客';
        var sev = sig.severity_hint != null ? Number(sig.severity_hint).toFixed(2) : '';
        var category = sig.category || '';
        sigHtml = '<div class="rt-sig"><span class="rt-sid">信号 ' + _esc(sid_val.slice(0, 12)) + '</span>'
          + (category ? '<span class="rt-cat">' + _esc(category) + '</span>' : '')
          + (sev ? '<span class="rt-sev">严重度 ' + _esc(sev) + '</span>' : '')
          + '<span class="rt-subj">主体：' + _esc(subject) + '</span></div>';
        // TTL 续期：记录最后出现帧（P0-2 audit fix → __LiveState.riskSignalMap.set 已 TTL）
      }
      box.innerHTML = '<div class="rt-card' + (t === 'raised' ? ' live' : '') + '"><div class="rt-head">' +
        '<span class="rt-badge' + (t === 'raised' ? ' live' : '') + '">' + (t === 'raised' ? 'RAISED' : 'ACTIVE') + '</span>' +
        '<span class="rt-id">' + _esc(levels || 'RISK') + '</span>' +
        '<span class="rt-time">' + _esc(time) + '</span>' +
        '</div>' + sigHtml + '</div>';
      if (empty) empty.style.display = 'none';
    } else if (t === 'cleared') {
      var cur = box.querySelector('.rt-card');
      if (cur) {
        // T1.2 修复（P0-5）：保留「已解除」卡在 DOM 中，加 cleared class，不自动清空。
        // 避免 RAISED→CLEARED 时整卡闪烁消失；新 RAISED 触发时由上方 raised 分支
        // 以 box.innerHTML 整体覆盖（替换而非堆叠），满足"覆盖旧卡"。
        cur.classList.remove('live');
        cur.classList.add('cleared');
        var badge = cur.querySelector('.rt-badge');
        if (badge) {
          badge.textContent = 'CLEARED';
          badge.classList.remove('live');
          badge.classList.add('cleared');
        }
      } else if (empty) {
        empty.style.display = '';
      }
    }
    // P0-1 修复（P0-3 audit bug）：三端命令累积。
    // 把 active_warnings（每个 warning 含 recommended_action）按 recommended_action
    // 拆成 family / community / log_only 三个 Map（live_actions.js _renderTaskCards 消费）。
    // 这是事实投影（VM-9）：仅按 recommended_action 分桶，不创造 payload 字段。
    var aw = msg.active_warnings || [];
    for (var k = 0; k < aw.length; k++) {
      var w = aw[k];
      if (!w || !w.warning_id) continue;
      var wid = String(w.warning_id);
      var ls = __LiveState;
      if (!ls.commandMap[wid]) {
        ls.commandMap[wid] = { family: new Map(), community: new Map(), log_only: new Map() };
      }
      var act = w.recommended_action || 'LOG_ONLY';
      var cmdId = 'cmd-' + wid + '-' + act;
      var payload = {
        warning_id: wid,
        reason_summary: w.reason_summary || [],
        risk_level: w.risk_level,
        perception_score: w.perception_score,
        device_id: w.device_id,
        elder_id: w.elder_id,
      };
      if (act === 'NOTIFY_FAMILY' || act === 'MONITOR_FAMILY') {
        ls.commandMap[wid].family.set(cmdId, {
          command_id: cmdId, command_type: act, payload: payload,
          warning_id: wid, status: 'SENT',
        });
      } else if (act === 'ESCALATE_COMMUNITY' || act === 'CREATE_COMMUNITY_TASK') {
        ls.commandMap[wid].community.set(cmdId, {
          command_id: cmdId, command_type: act, payload: payload,
          warning_id: wid, status: 'SENT',
        });
      } else {
        // LOG_ONLY / MONITOR / 其他 → 仅记录
        ls.commandMap[wid].log_only.set(cmdId, {
          command_id: cmdId, command_type: act, payload: payload,
          warning_id: wid, status: 'SENT',
        });
      }
    }
  }

  // Phase 3.3: memory_timeline 消息处理（🟡 Partial · 阻塞于 Memory API）
  // 历史访问记录时间线：每 episode 卡片（record_id / prior 标记 / timestamp /
  // summary / risk_level / recommended_action / reason_summary），VM-9 只渲染，不推理。
  function _applyMemoryTimeline(msg) {
    var el = global.document.getElementById('live-memory-timeline-' + sid);
    if (!el) return;
    var episodes = msg.episodes || [];
    if (!episodes.length) {
      el.innerHTML = '<span class="muted">暂无历史访问记录（Memory API 待接入）</span>';
      return;
    }
    var cards = episodes.map(function (ep) {
      var tag = ep.prior ? '历史预置' : '本次会话';
      var risk = ep.risk_level || '—';
      var action = ep.recommended_action || '—';
      var summary = _esc(ep.summary || '');
      var reasons = ((ep.reason_summary || []).map(function (r) {
        return _esc(_REASON_ZH[r] || r);
      })).join('、') || '—';
      return '<div class="mem-ep">' +
        '<div class="mem-ep-head">' +
          '<span class="mem-ep-id">' + _esc(ep.record_id || '') + '</span>' +
          '<span class="mem-ep-tag">' + tag + '</span>' +
          '<span class="mem-ep-time">' + _esc(ep.timestamp || '') + '</span>' +
        '</div>' +
        '<div class="mem-ep-body">' + summary + ' · 风险 ' + _esc(risk) + ' · 建议 ' + _esc(action) + '</div>' +
        '<div class="mem-ep-reasons">依据：' + reasons + '</div>' +
      '</div>';
    }).join('');
    el.innerHTML = cards;
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
    // P1-5 修复：直接用 .live-perception 的 data-scenario 属性取 sid（最可靠）。
    // 原代码先查 .scenario-title code（Live 页走 render_case_viewer._scenario_headline，
    // 不输出 <code>）→ 取空 → 回回退。这两步 sid 初始化导致 _applyFrame 第一帧
    // getElementById 全部失败。改成直接读 data-scenario。
    var lp = global.document.querySelector('.live-perception');
    if (lp) {
      sid = lp.getAttribute('data-scenario') || '';
      // LIVE Scenario Controller：读 narrative_mode 属性（由 render.py 注入，前端只消费）
      _narrativeMode = lp.getAttribute('data-narrative-mode') || 'neutral';
    }
    // 兼容：如果页面有 <code>（非 live 模式），也读一下。
    if (!sid) {
      var code = global.document.querySelector('.scenario-title code');
      if (code) sid = (code.textContent || '').trim();
    }
    // 首屏快照即基线：预填已渲染 refs，绝不重放重复（VM-8）。
    var items = global.document.querySelectorAll('.tl-item[data-ref]');
    for (var i = 0; i < items.length; i++) seenRefs.add(items[i].getAttribute('data-ref'));
    // Phase 1 L0：启动 Audio Health 三值轮询定时器（无后端字段，前端推断 🟡 Partial）。
    _startAudioStaleTimer();
    // Phase 1 L5：绑定 Provenance 快捷入口（无 JS 也可展开，降级体验）。
    _bindWhyBelieveLinks();
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
        if (msg.type === 'snapshot') _applySnapshot(msg);
        else if (msg.type === 'evidence_delta') _applyDelta(msg);
        else if (msg.type === 'perception_delta') _applyPerceptionDelta(msg);
        else if (msg.type === 'frame_tick') _applyFrame(msg);
        else if (msg.type === 'risk_delta') _applyRiskDelta(msg);
        // Phase 3.3: memory_timeline 消息类型（🟡 Partial · 阻塞于 Memory API）
        else if (msg.type === 'memory_timeline') _applyMemoryTimeline(msg);
        // P0-11.3.5 场景切换：source_switched 广播（切换视频源 / POST /demo/reset 共用此通道）
        // 触发前端 resetSession() 清空跨帧累积状态，避免旧数据串场。
        else if (msg.type === 'source_switched') resetSession();
      };
    }
    // P2-2: TTL 风险信号兜底计时器（每 5s 检查过期）
    setInterval(_tickRiskSignals, 5000);
    _connect();
  }

  _init();

  // P0-11.3.5 会话重置：清空跨帧累积状态（新视频 = 新会话）。
  // 由 source_switched 消息或 POST /demo/reset 广播触发；
  // sid 不自动更新——由 _init() 在页面加载时从 .live-perception data-scenario 读取。
  function resetSession() {
    // 停止旧 audio stale timer（避免新会话计时器与旧场景混用）
    if (_audioStaleTimer) { clearInterval(_audioStaleTimer); _audioStaleTimer = null; }
    // 去重 Set：跨会话事件不再被跳过
    seenRefs.clear();
    seenAudio.clear();
    seenCaseTime.clear();
    // Surface 挂起渲染队列（新会话无历史挂起）
    _pendingAudioRows = [];
    _pendingCaseTimeMarks = [];
    // PerceptionStream：条目、历史、last* 状态、去重集合全清
    _perceptionStream.clear();
    // 语义状态层：seeState.vision / seeState.audio
    seeState.vision = [];
    seeState.audio = [];
    // Audio Health：最近事件时间戳 + 健康态重置
    _lastAudioEventMs = null;
    _audioHealthState = null;
    // LiveState 聚合层：warningMap / behaviorEvents / commandMap / riskSignalMap
    __LiveState.warningMap = {};
    __LiveState.behaviorEvents = [];
    __LiveState.behaviorN = 0;
    __LiveState.visitorSeq = {};
    __LiveState.visitorSeqN = 0;
    __LiveState.visitorFirst = {};
    __LiveState.commandMap = {};
    __LiveState.riskSignalMap = new Map();
    // 声学状态历史（新会话从零开始记录）
    _acousticStateHistory = [];
    // 重新开启 audio stale timer（新会话初始 NO_RECENT_EVENT → 下次音频事件后刷新）
    _startAudioStaleTimer();
  }

  global.__LiveState = __LiveState;
  // __LiveStream 测试/调试导出口：seeState / perceptionStream 为语义层只读引用，
  // 供 Surface Independence 回归测试直接断言"Surface 缺失 ≠ Runtime Fact 丢失"。
  global.__LiveStream = {
    applyDelta: _applyDelta,
    seenRefs: seenRefs,
    seenAudio: seenAudio,
    seenCaseTime: seenCaseTime,
    renderBehaviorTimeline: _renderBehaviorTimeline,
    renderRiskSignals: _renderRiskSignals,
    seeState: seeState,
    perceptionStream: _perceptionStream,
    pendingSurfaces: function () {
      return { audioRows: _pendingAudioRows.length, caseTimeMarks: _pendingCaseTimeMarks.length };
    },
    // P0-11.3.5 场景切换：供测试直接调用，验证 resetSession 清空跨帧累积状态。
    resetSession: resetSession,
  };
  // 默认暴露 resetSession 到全局，供 inline script 直接调用（非模块环境兼容）。
  global.resetSession = resetSession;
})(window);

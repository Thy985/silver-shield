/*
 * ADR-0036 P0-3 · AudioSync（音频轨 ↔ 证据时间线联动 · P0-11.x 升级 2026-08-18）。
 *
 * vanilla JS，零依赖，由 render.py 内联进自包含 HTML（对齐 replay.js / media.js 纪律）。
 *
 * 设计（P0-3 media_tracks + P0-11.x-3 Case Time → audio seek/play）：
 * - 点击源（两类，互补）：
 *   ① .tl-item[data-kind]  —— Evidence Timeline 的 AUDIO 节点（Artifact 模式 renderer
 *     已注入 data-kind；Live 模式 live_stream.js _buildTimelineNode 也已注入）。
 *   ② .case-time-mark[data-kind="audio"] —— Case Time 主轴上的音频事件标记
 *     （render.py _render_case_time, _buildCaseTimeMark 已注入 data-time + data-kind）。
 * - 点击 → 播放对应样本轨（#audio-<kind>）+ 高亮音频感知卡片（.audio-card[data-kind]）；
 * - P0-11.x-3（用户拍板 2026-08-18）：Case Time → audio seek/play 双边 clamp：
 *     audio_local_time = max(0, min(case_time - track.start_time, audio.duration))
 *   仅当 audio.readyState >= 1 且 isFinite(audio.duration) 才 seek/play；
 *   否则等 loadedmetadata 事件触发后再 seek/play（避免 NaN / Infinity / play rejected）。
 * - 反向（Media → Evidence）：<audio> 播放不驱动 timeline（音频样本是独立媒体轨，
 *   无逐帧证据映射；不形成闭环抖动）；
 * - 降级安全：无样本（无 #audio-<kind>）→ no-op；无 audio-card → no-op；不崩。
 * - 证据与媒体严格分离（VM-9 / AC-11）：本脚本只按 data-kind 查找已渲染的 <audio>
 *   元素，绝不拼接/注入任何 url。
 */
(function (global) {
  'use strict';

  function byId(id) { return global.document.getElementById(id); }

  function AudioSync() {
    this._bound = false;
  }

  // P0-11.x-3：从 <script type="application/json" id="audio-manifest-{sid}"> 读 manifest。
  // manifest 结构：{source_kind, files: {kind: url}, tracks: [{id, kind, url, start_time, end_time, provenance_kind}]}。
  // 无数据岛 → 返回 null（降级：不 seek，只 play 从头）。
  AudioSync.prototype._readManifest = function () {
    if (typeof global.document === 'undefined') return null;
    if (typeof global.document.querySelector !== 'function') return null;
    var el = global.document.querySelector('script[type="application/json"][id^="audio-manifest-"]');
    if (!el || !el.textContent) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  };

  // P0-11.x-3：查 track.start_time（按 kind）。无 manifest / 无 track → 0。
  AudioSync.prototype._trackStartTime = function (manifest, kind) {
    if (!manifest || !manifest.tracks) return 0;
    for (var i = 0; i < manifest.tracks.length; i++) {
      if (manifest.tracks[i].kind === kind) {
        var st = manifest.tracks[i].start_time;
        return (typeof st === 'number' && isFinite(st)) ? st : 0;
      }
    }
    return 0;
  };

  // P0-11.x-3：loadedmetadata 后 seek + play（用户硬约束：readyState >= 1 + finite duration）。
  AudioSync.prototype._seekAndPlay = function (audioEl, targetLocalTime) {
    if (!audioEl || typeof audioEl.play !== 'function') return;
    function doSeek() {
      var dur = audioEl.duration;
      if (!isFinite(dur) || dur <= 0) {
        try { audioEl.play(); } catch (e) { /* 降级 */ }
        return;
      }
      var clamped = Math.max(0, Math.min(targetLocalTime, dur));
      try {
        audioEl.currentTime = clamped;
        audioEl.play();
      } catch (e) { /* 降级 */ }
    }
    // 防御：无 readyState / 无 addEventListener → 直接 play 从头（降级，不崩）。
    if (typeof audioEl.readyState === 'undefined' ||
        typeof audioEl.addEventListener !== 'function') {
      try { audioEl.play(); } catch (e) { /* 降级 */ }
      return;
    }
    if (audioEl.readyState >= 1 && isFinite(audioEl.duration)) {
      doSeek();
    } else {
      var done = false;
      var onReady = function () {
        if (done) return;
        done = true;
        if (typeof audioEl.removeEventListener === 'function') {
          audioEl.removeEventListener('loadedmetadata', onReady);
        }
        doSeek();
      };
      audioEl.addEventListener('loadedmetadata', onReady);
      try { audioEl.load(); } catch (e) { /* 降级 */ }
      setTimeout(function () {
        if (done) return;
        done = true;
        if (typeof audioEl.removeEventListener === 'function') {
          audioEl.removeEventListener('loadedmetadata', onReady);
        }
        try { audioEl.play(); } catch (e) { /* 降级 */ }
      }, 3000);
    }
  };

  // P0-11.x-3：中文标签 → kind 枚举（case-time-mark 的 data-label 是中文）。
  AudioSync.prototype._resolveKindFromLabel = function (manifest, label) {
    if (!manifest || !manifest.tracks) return null;
    for (var i = 0; i < manifest.tracks.length; i++) {
      if (manifest.tracks[i].kind === label) return manifest.tracks[i].kind;
    }
    if (manifest.tracks.length > 0) return manifest.tracks[0].kind;
    return null;
  };

  // P0-11.x-3：统一激活逻辑（高亮 card + seek/play audio）。
  AudioSync.prototype._activate = function (kind, caseTimeStr) {
    if (!kind) return;
    var cards = global.document.querySelectorAll('.audio-card[data-kind="' + kind + '"]');
    for (var c = 0; c < cards.length; c++) {
      cards[c].classList.add('audio-card-active');
      (function (card) {
        setTimeout(function () { card.classList.remove('audio-card-active'); }, 3000);
      })(cards[c]);
    }
    var audioEl = byId('audio-' + kind);
    if (!audioEl) return;
    var manifest = this._readManifest();
    var trackStart = this._trackStartTime(manifest, kind);
    var caseTime = parseFloat(caseTimeStr);
    if (!isFinite(caseTime)) caseTime = 0;
    var targetLocal = caseTime - trackStart;
    this._seekAndPlay(audioEl, targetLocal);
  };

  AudioSync.prototype.bind = function () {
    if (this._bound) return;
    this._bound = true;
    if (typeof global.document === 'undefined') return;
    var self = this;
    var manifest = self._readManifest();

    var items = global.document.querySelectorAll('.tl-item[data-kind]');
    for (var i = 0; i < items.length; i++) {
      (function (el) {
        el.addEventListener('click', function () {
          var kind = el.getAttribute('data-kind');
          if (!kind) return;
          self._activate(kind, el.getAttribute('data-time'));
        });
      })(items[i]);
    }

    var marks = global.document.querySelectorAll('.case-time-mark[data-kind="audio"]');
    for (var j = 0; j < marks.length; j++) {
      (function (el) {
        el.addEventListener('click', function () {
          var label = el.getAttribute('data-label') || 'audio';
          var resolvedKind = self._resolveKindFromLabel(manifest, label) || label;
          self._activate(resolvedKind, el.getAttribute('data-time'));
        });
      })(marks[j]);
    }
  };

  global.__AudioSync = new AudioSync();
  if (typeof global.document !== 'undefined') {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', function () {
        global.__AudioSync.bind();
      });
    } else {
      global.__AudioSync.bind();
    }
  }
})(typeof window !== 'undefined' ? window : this);

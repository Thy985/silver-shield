/*
 * ADR-0036 P0-3 · AudioSync（音频轨 ↔ 证据时间线联动）。
 *
 * vanilla JS，零依赖，由 render.py 内联进自包含 HTML（对齐 replay.js / media.js 纪律）。
 *
 * 设计（P0-3 media_tracks · Case Time 对齐）：
 * - 音频作为与视频并行的 Case Media Track：Evidence Timeline 的 AUDIO 节点（data-kind）
 *   点击 → 播放对应样本轨（#audio-<kind>）+ 高亮音频感知卡片（.audio-card[data-kind]）；
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

  AudioSync.prototype.bind = function () {
    if (this._bound) return;
    this._bound = true;
    if (typeof global.document === 'undefined') return;
    var items = global.document.querySelectorAll('.tl-item[data-kind]');
    for (var i = 0; i < items.length; i++) {
      (function (el) {
        el.addEventListener('click', function () {
          var kind = el.getAttribute('data-kind');
          if (!kind) return;
          // 高亮对应音频感知卡片（无卡片 no-op）。
          var cards = global.document.querySelectorAll('.audio-card[data-kind="' + kind + '"]');
          for (var c = 0; c < cards.length; c++) {
            cards[c].classList.add('audio-card-active');
            // 点击另一节点时清理其他高亮（保持单一高亮）。
            (function (card) {
              setTimeout(function () { card.classList.remove('audio-card-active'); }, 3000);
            })(cards[c]);
          }
          // 播放对应样本轨（无样本 no-op；播放失败降级不崩）。
          var audioEl = byId('audio-' + kind);
          if (audioEl && typeof audioEl.play === 'function') {
            try { audioEl.play(); } catch (e) { /* 降级 */ }
          }
        });
      })(items[i]);
    }
  };

  global.__AudioSync = new AudioSync();
  // DOM 就绪后自动绑定（replay.js 同款：脚本在 body 尾内联，此时 DOM 已可用）。
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

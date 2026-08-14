/*
 * ADR-0036 Slice A.1 · MediaPlayer（canvas 播放 + 双向同步）。
 *
 * vanilla JS，零依赖，由 render.py 内联进自包含 HTML（对齐 replay.js 纪律）。
 *
 * 设计（严格对齐用户决策）：
 * - MediaPlayer 是前端**主时钟**：驱动 canvas 帧绘制 + Evidence Timeline
 *   （window.__Replay）同步；与 replay 双轨道（timeline/trace）解耦。
 * - 媒体字节不进 View Model（VM-10 / AC-11）：manifest 只持 ref/template/count，
 *   HTML 不 base64 内联帧（避免 660 帧 → 几十 MB 膨胀）。
 * - 双向同步（Case Time 线性映射）：
 *   - 正向（Media → Evidence）：play / 进度条跳转 → seekByTime(t, echo=true)
 *     → 画帧 + __Replay.get(sid).seek(evidenceIdx)（驱动统一 Evidence Timeline）；
 *   - 反向（Evidence → Media）：replay.js timeline 节点点击 → window.__MediaSync
 *     .onEvidenceSeek(idx) → seekByEvidence(idx) 只画对应帧（echo=false，**不回写**
 *     __Replay，避免闭环抖动）。
 * - 单一 MediaPlayer 实例（按 sid 注册）；降级安全：manifest 缺失 → 画布留空、控制条
 *   仍可点（纯 UI 进度），不崩。
 */
(function (global) {
  'use strict';

  var players = {};

  function MediaPlayer(sid, canvas, manifest, opts) {
    this.sid = sid;
    this.canvas = canvas;
    this.manifest = manifest || null;
    this.ctx = (canvas && canvas.getContext) ? canvas.getContext('2d') : null;
    opts = opts || {};
    // 时长优先级：opts 显式 > manifest > 0（无媒体时仍允许纯 UI 进度驱动 Evidence）。
    this.duration = (opts.duration != null) ? opts.duration
      : (manifest && manifest.duration_sec) || 0;
    this.fps = (opts.fps != null) ? opts.fps
      : (manifest && manifest.fps) || 0;
    this.frameCount = (manifest && manifest.frame_count) || 0;
    this.frameTemplate = (manifest && manifest.frame_template) || '';
    this.currentFrame = -1;
    this.time = 0;
    this.playing = false;
    this.timer = null;
    this.draws = 0;          // 调试/测试可观测：drawImage 调用计数
    this._progress = null;
    this._label = null;
  }

  MediaPlayer.prototype._evidenceNodeCount = function () {
    var rp = global.__Replay && global.__Replay.get(this.sid);
    return (rp && rp.nodes) ? rp.nodes.length : 0;
  };

  MediaPlayer.prototype._drawFrame = function (frameIdx) {
    if (frameIdx === this.currentFrame) return;  // 幂等：同帧不重绘
    this.currentFrame = frameIdx;
    if (!this.ctx || !this.frameTemplate) return;
    var url = this.frameTemplate.replace('{idx:06d}', ('000000' + frameIdx).slice(-6));
    var self = this;
    if (typeof Image === 'undefined') return;  // 极端降级：无 Image API 不崩
    var img = new Image();
    img.onload = function () {
      try { self.ctx.drawImage(img, 0, 0); self.draws++; } catch (e) { /* 降级 */ }
    };
    img.onerror = function () { /* 帧缺失降级：画布留空 */ };
    img.src = url;
  };

  // 正向：时间 → 帧 + Evidence（echo=true 时驱动 __Replay.seek，正向同步）。
  MediaPlayer.prototype.seekByTime = function (t, echo) {
    if (!this.duration) return;
    if (t < 0) t = 0;
    if (t > this.duration) t = this.duration;
    this.time = t;
    var fi = this.frameCount > 1
      ? Math.round(t / this.duration * (this.frameCount - 1)) : 0;
    this._drawFrame(fi);
    if (echo !== false) {
      var n = this._evidenceNodeCount();
      if (n > 1) {
        var ei = Math.round(t / this.duration * (n - 1));
        var rp = global.__Replay && global.__Replay.get(this.sid);
        if (rp) rp.seek(ei);
      }
    }
    this._renderProgress();
  };

  // 反向：Evidence 节点 idx → 帧（echo=false，不回写 __Replay）。
  MediaPlayer.prototype.seekByEvidence = function (evidenceIdx) {
    var n = this._evidenceNodeCount();
    if (n < 1 || this.frameCount < 1) return;
    var fi = this.frameCount > 1
      ? Math.round((evidenceIdx || 0) / (n - 1) * (this.frameCount - 1)) : 0;
    this._drawFrame(fi);
    this._renderProgress();
  };

  MediaPlayer.prototype.play = function () {
    if (this.playing || !this.duration) return;
    this.playing = true;
    var self = this;
    if (this.time >= this.duration) this.time = 0;
    this.timer = setInterval(function () {
      self.time += self.duration / 50;  // 50 步到尾
      if (self.time >= self.duration) { self.time = self.duration; self.pause(); }
      self.seekByTime(self.time, true);
    }, 100);
  };

  MediaPlayer.prototype.pause = function () {
    this.playing = false;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  };

  MediaPlayer.prototype.toggle = function () { this.playing ? this.pause() : this.play(); };

  MediaPlayer.prototype._bindControls = function () {
    var self = this;
    var playBtn = document.getElementById('media-play-' + this.sid);
    var progress = document.getElementById('media-progress-' + this.sid);
    var label = document.getElementById('media-time-label-' + this.sid);
    if (playBtn) playBtn.onclick = function () { self.toggle(); };
    // 进度条包裹层点击 → 跳到对应时间（正向同步）。
    var wrap = progress && progress.parentElement;
    if (wrap) {
      wrap.onclick = function (ev) {
        var rect = (wrap.getBoundingClientRect)
          ? wrap.getBoundingClientRect() : { width: 100, left: 0 };
        var ratio = (ev && ev.clientX != null)
          ? (ev.clientX - (rect.left || 0)) / (rect.width || 1) : 0;
        if (ratio < 0) ratio = 0;
        if (ratio > 1) ratio = 1;
        self.seekByTime(ratio * self.duration, true);
      };
    }
    this._progress = progress;
    this._label = label;
  };

  MediaPlayer.prototype._renderProgress = function () {
    if (this._progress) {
      var pct = this.duration ? Math.round(this.time / this.duration * 100) : 0;
      this._progress.style.width = pct + '%';
    }
    if (this._label) {
      this._label.textContent =
        this.time.toFixed(1) + 's / ' + this.duration.toFixed(1) + 's';
    }
  };

  MediaPlayer.prototype.start = function () {
    this._bindControls();
    // 反向同步钩子（Evidence → Media）：仅注册一次；replay.js bindTimeline 点击时回调。
    if (global.__Replay && typeof global.__Replay.linkHighlight === 'function') {
      global.__MediaSync = global.__MediaSync || {};
      var self = this;
      global.__MediaSync.onEvidenceSeek = function (evidenceIdx) {
        self.seekByEvidence(evidenceIdx == null ? 0 : evidenceIdx);
      };
    }
    this.seekByTime(0, false);  // 初始画首帧（不驱动 Evidence，避免回环）
    this._renderProgress();
  };

  global.__MediaPlayer = {
    init: function (sid, canvas, manifest, opts) {
      var p = new MediaPlayer(sid, canvas, manifest, opts);
      players[sid] = p;
      p.start();
      return p;
    },
    get: function (sid) { return players[sid] || null; }
  };
})(window);

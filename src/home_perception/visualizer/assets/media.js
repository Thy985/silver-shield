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

  // 帧 URL 协议黑名单（评审 R2-#3 / R2-#11）：拒 javascript:/data:/file: 等伪协议
  // （XSS / 本地文件读取）；放行 http(s) 与**任何相对路径**（含裸相对路径
  // ``sw_t1/media/frames/000000.png``、``/abs``、``./``、``../``）——相对路径是 frame_template
  // 的合法形态（manifest 只持相对 media base 的模板，渲染层再叠加 media_base_url）。
  // 用"拒危险 scheme"而非"白名单 scheme"，避免误伤裸相对路径导致帧不绘制。
  function _safeUrl(url) {
    if (!url) return '';
    // 带 scheme 但非 http(s)（如 javascript:/data:/file:/vbscript:）→ 拒绝。
    if (/^[a-z][a-z0-9+.\-]*:/i.test(url) && !/^https?:/i.test(url)) return '';
    return url;
  }

  MediaPlayer.prototype._drawFrame = function (frameIdx) {
    if (frameIdx === this.currentFrame) return;  // 幂等：同帧不重绘
    this.currentFrame = frameIdx;
    if (!this.ctx || !this.frameTemplate) return;
    var raw = this.frameTemplate.replace('{idx:06d}', ('000000' + frameIdx).slice(-6));
    var url = _safeUrl(raw);
    if (!url) return;  // 伪协议 / 非法 URL → 不加载，画布留空（fail-closed）
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
    // P5：有 <video>（ArtifactVideoSource）→ 反向定位 video（进度条点击 / replay 联动）。
    if (this.videoEl && typeof this.videoEl.currentTime !== 'undefined') {
      if (Math.abs(this.videoEl.currentTime - t) > 0.05) {
        try { this.videoEl.currentTime = t; } catch (e) { /* seek 失败降级 */ }
      }
    }
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
    // P5：有 <video> → 按节点比例定位视频。
    if (this.videoEl && n > 1 && typeof this.videoEl.currentTime !== 'undefined') {
      var t = (evidenceIdx || 0) / (n - 1) * this.duration;
      try { this.videoEl.currentTime = t; } catch (e) { /* seek 失败降级 */ }
    }
    this._drawFrame(fi);
    this._renderProgress();
  };

  MediaPlayer.prototype._bindVideoSync = function () {
    // P5（评审整改）：ArtifactVideoSource 用原生 <video> 播放，须把 video 时钟桥接进
    // MediaPlayer —— timeupdate → seekByTime（驱动 Evidence Timeline 定位）。
    // 无 <video>（canvas 帧源 / 无媒体）→ 不绑定，保持原纯 UI 进度行为。
    // 仅当元素是真实 <video>（含 play/pause 方法）才设 videoEl —— 否则 mock/缺失
    // 元素会把 play() 错误导向 video 分支（评审：前端行为测试 mock 无 play 方法）。
    var self = this;
    if (typeof document === 'undefined') return;
    var ve = document.getElementById('case-video-el-' + this.sid);
    if (!ve) return;
    if (typeof ve.play !== 'function' || typeof ve.addEventListener !== 'function') return;
    this.videoEl = ve;
    ve.addEventListener('timeupdate', function () {
      if (!self.playing) self.playing = true;  // video 原生播放 → 标记播放态
      if (ve.duration && isFinite(ve.duration)) self.duration = ve.duration;
      self.seekByTime(ve.currentTime, true);
    });
    ve.addEventListener('play', function () { self.playing = true; });
    ve.addEventListener('pause', function () { self.playing = false; });
  };

  MediaPlayer.prototype.play = function () {
    // 有 <video>（ArtifactVideoSource）→ 交给原生控件播放（timeupdate 驱动 Evidence 同步）；
    // 无 <video>（canvas 帧源）→ 维持原 setInterval 帧播放。
    if (this.videoEl) {
      if (typeof this.videoEl.play === 'function') this.videoEl.play();
      return;
    }
    if (this.playing || !this.duration) return;
    this.playing = true;
    var self = this;
    if (this.time >= this.duration) this.time = 0;
    // 步长按真实帧率 1x 同步（评审 R2-#11）：每步推进 1/fps 秒、间隔 1000/fps 毫秒；
    // fps 缺失（无媒体）→ 回退 10fps（步长 0.1s），避免 duration 很小（如 0.5s）时
    // 固定 50 步 → 步长 10ms 触发高频 setInterval 多步累计跳帧 / 同步抖动。
    var fps = this.fps || 10;
    var stepSec = 1 / fps;
    var stepMs = 1000 / fps;
    this.timer = setInterval(function () {
      self.time += stepSec;
      if (self.time >= self.duration) { self.time = self.duration; self.pause(); }
      self.seekByTime(self.time, true);
    }, stepMs);
  };

  MediaPlayer.prototype.pause = function () {
    if (this.videoEl) {
      if (typeof this.videoEl.pause === 'function') this.videoEl.pause();
      return;
    }
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
    // P5：真实 <video> 桥接（ArtifactVideoSource：timeupdate → Evidence Timeline）。
    this._bindVideoSync();
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
    get: function (sid) { return players[sid] || null; },
    // 暴露给测试：帧 URL 协议白名单（评审 R2-#3 / R2-#11）。
    _safeUrl: _safeUrl
  };

  // P0-1 Case Header ▶ Play Case：滚动到 Case Video 面板 + 触发 MediaPlayer 播放。
  // 无播放器 / 无元素 → no-op（降级不崩）。
  global.__playCase = function (sid) {
    if (typeof global.document === 'undefined') return;
    var el = global.document.getElementById('fs-case-video-' + sid);
    if (el && el.scrollIntoView) {
      try { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      catch (e) { el.scrollIntoView(); }
    }
    var p = global.__MediaPlayer && global.__MediaPlayer.get(sid);
    if (p && typeof p.play === 'function') {
      try { p.play(); } catch (e) { /* 降级 */ }
    }
  };
})(window);

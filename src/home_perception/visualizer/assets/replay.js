/*
 * ADR-0035 D2.1 Replay Cursor —— vanilla JS（无任何外部依赖）。
 *
 * 由 renderer 内联进自包含 HTML，运行时调用：
 *     window.__Replay.init(sid)
 * 把静态 Timeline 升级为可交互重放：播放 / 暂停 / 单步前进后退 / 进度条 / 速度。
 * timeline 数据来自 renderer 内联的 <script type="application/json" id="replay-data-<sid>">
 * 数据岛（客户端 JSON.parse），与 <script> 终结隔离（评审 R4-安全）。
 *
 * 设计纪律（对齐 ADR-0035 D4 路线锁定）：
 * - 纯前端、零服务器；交互全部在浏览器内完成，不改 artifact、不联网；
 * - 不引 D3.js / FastAPI；时间轴重放原生 DOM + CSS 即可达成；
 * - 数据来自 projection（确定性），初始态固定（index=0 / 暂停），
 *   保证同 artifact 两次渲染逐字节一致（D8 确定性）；
 * - onStep() 暴露当前 step 钩子，供 D2.2 Causal Highlight 订阅（step↔graph 联动），
 *   返回 { off() } 句柄以支持取消订阅（避免热重载/多次 init 时订阅指数累积）。
 */
(function (global) {
  'use strict';

  function Replay(sid, nodes) {
    this.sid = sid;
    this.nodes = nodes || [];
    this.index = 0;
    this.playing = false;
    this.timer = null;
    this.speed = 1;            // 1x / 2x / 4x
    this.baseInterval = 900;   // 每步毫秒（1x 基准）
    this.listEl = null;
    this.bar = null;
    this._subs = [];           // D2.2 联动订阅
  }

  // 订阅当前 step 变更；返回 { off() } 句柄（幂等取消），避免重复订阅累积。
  Replay.prototype.onStep = function (fn) {
    var self = this;
    this._subs.push(fn);
    return {
      off: function () {
        var i = self._subs.indexOf(fn);
        if (i >= 0) self._subs.splice(i, 1);
      }
    };
  };

  // 清空全部订阅（如视图热重载时调用，防内存泄漏 + 重复回调）。
  Replay.prototype.clearSubs = function () { this._subs = []; };

  Replay.prototype._notify = function () {
    var cur = this.nodes[this.index];
    for (var i = 0; i < this._subs.length; i++) this._subs[i](this.index, cur);
  };

  Replay.prototype._render = function () {
    var items = this.listEl ? this.listEl.querySelectorAll('.tl-item') : [];
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle('active', i === this.index);
      items[i].classList.toggle('played', i <= this.index);
    }
    if (this.bar) {
      var pct = this.nodes.length
        ? Math.round(((this.index + 1) / this.nodes.length) * 100)
        : 0;
      this.bar.progress.style.width = pct + '%';
      this.bar.label.textContent = (this.index + 1) + ' / ' + this.nodes.length;
    }
    this._notify();
  };

  Replay.prototype.seek = function (i) {
    if (i < 0) i = 0;
    if (i > this.nodes.length - 1) i = this.nodes.length - 1;
    this.index = i;
    this._render();
  };

  Replay.prototype.stepNext = function () {
    if (this.index < this.nodes.length - 1) { this.index++; this._render(); }
    else this.pause();
  };

  Replay.prototype.stepPrev = function () {
    if (this.index > 0) { this.index--; this._render(); }
  };

  Replay.prototype._schedule = function () {
    var self = this;
    // 复用前先清旧 timer，避免切速度时出现两个心跳叠加。
    if (self.timer) { clearTimeout(self.timer); self.timer = null; }
    self.timer = setTimeout(function () { self._tick(); }, self.baseInterval / self.speed);
  };

  Replay.prototype._tick = function () {
    var self = this;
    if (self.index >= self.nodes.length - 1) { self.pause(); return; }
    self.stepNext();
    if (self.playing) self._schedule();
  };

  Replay.prototype.play = function () {
    if (this.playing) return;
    // 末位再播：回到开头重放（产品约定——▶ 即「从头播放」）。
    if (this.index >= this.nodes.length - 1) this.index = 0;
    this.playing = true;
    if (this.bar) this.bar.toggle.textContent = '⏸';
    this._schedule();
  };

  Replay.prototype.pause = function () {
    this.playing = false;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.bar) this.bar.toggle.textContent = '▶';
  };

  Replay.prototype.toggle = function () { this.playing ? this.pause() : this.play(); };

  Replay.prototype.setSpeed = function (s) {
    this.speed = s;
    // 立即按新速度重排下一次心跳：不重置 index、不 pause+play 重建 timer，
    // 避免「切到 4x 立刻跳到 1/4 处」的视觉错位。
    if (this.playing) this._schedule();
  };

  Replay.prototype.bind = function () {
    var self = this;
    // 控制条缺件（降级路径）→ 不绑定（静态时间轴仍可读，不崩）。
    if (!this.bar) return;
    this.bar.reset.onclick = function () { self.pause(); self.seek(0); };
    this.bar.toggle.onclick = function () { self.toggle(); };
    this.bar.next.onclick = function () { self.pause(); self.stepNext(); };
    this.bar.prev.onclick = function () { self.pause(); self.stepPrev(); };
    this.bar.speed.onchange = function () { self.setSpeed(parseFloat(this.value) || 1); };
    this._render();
  };

  function byId(id) { return document.getElementById(id); }

  var registry = {};
  global.__Replay = {
    init: function (sid) {
      var r = new Replay(sid, []);
      registry[sid] = r;
      // 数据来自 application/json 数据岛（renderer 内联，已做 </ 安全清洗）。
      var dataEl = byId('replay-data-' + sid);
      if (dataEl) {
        try { r.nodes = JSON.parse(dataEl.textContent); }
        catch (e) { r.nodes = []; }
      }
      // 必须是 <ul>（重放目标），而非视图锚点 H3（评审 R4-Bug：此前 id 撞名）。
      r.listEl = byId('timeline-list-' + sid);
      var reset = byId('rp-reset-' + sid),
          toggle = byId('rp-toggle-' + sid),
          next = byId('rp-next-' + sid),
          prev = byId('rp-prev-' + sid),
          speed = byId('rp-speed-' + sid),
          progress = byId('rp-progress-' + sid),
          label = byId('rp-progress-label-' + sid);
      // 控制条任一控件缺失 → 整体不绑定（防 null 解引用）。
      r.bar = (reset && toggle && next && prev && speed && progress && label)
        ? { reset: reset, toggle: toggle, next: next, prev: prev,
            speed: speed, progress: progress, label: label }
        : null;
      // 既无 timeline 列表又无控制条 → 无重放对象，直接返回（不 bind）。
      if (!r.listEl && !r.bar) return r;
      r.bind();
      return r;
    },
    get: function (sid) { return registry[sid]; }
  };
})(window);

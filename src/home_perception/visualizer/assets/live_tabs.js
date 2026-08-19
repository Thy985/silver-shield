/*
 * Live 产品骨架 · 阶段叙事 tabs（DESIGN-live-product-ui-restore PR-A）· live_tabs.js
 * 职责（边界严守）：
 * - ① 风险发现 / ② 家属确认 / ③ 社区处置 三视图切换（纯 [hidden] 展示编排，VM-11）；
 * - 默认停①，②/③ 可直切，不强制左→右顺序（Owner 收紧：叙事引导但不强迫剧本）；
 * - 切 Tab 不重订 WS：视图与主视图共享同一 EvidenceProjection delta 流与 closure 状态机；
 * - 纯 UI 切换，不创造事实、不推理（VM-1 / VM-9）。
 * 降级：无 tabs / 无目标视图 → no-op 不崩（fail-open 于 UI 层）。
 */
(function (global) {
  'use strict';

  var _VIEWS = ['discover', 'family', 'community'];

  function _switch(nav, view) {
    var sid = nav.getAttribute('data-scenario') || '';
    var tabs = nav.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (t.getAttribute('data-view') === view) t.classList.add('active');
      else t.classList.remove('active');
    }
    for (var k = 0; k < _VIEWS.length; k++) {
      var el = global.document.getElementById('view-' + _VIEWS[k] + '-' + sid);
      if (el) el.hidden = (_VIEWS[k] !== view);
    }
  }

  function _init() {
    if (typeof global.document === 'undefined') return;
    var navs = global.document.querySelectorAll('.tabs[data-live-tabs]');
    for (var i = 0; i < navs.length; i++) {
      (function (nav) {
        nav.addEventListener('click', function (ev) {
          var target = ev.target || ev.srcElement;
          // 兼容无 Element.closest 的极简 DOM（测试 mock）：向上找 .tab。
          while (target && target !== nav) {
            if (target.classList && target.classList.contains('tab')) break;
            target = target.parentNode;
          }
          if (!target || target === nav) return;
          var view = target.getAttribute('data-view');
          if (view) _switch(nav, view);
        });
      })(navs[i]);
    }
  }

  _init();

  global.__LiveTabs = { switch: _switch, init: _init };
})(window);

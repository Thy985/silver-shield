/*
 * P1-B · story_replay.js：叙事分幕点击聚焦（Artifact-only Story Replay）。
 *
 * 边界（严守 VM-1 / VM-9）：浏览器**只读**服务端派生的 data-start/data-end/data-refs/
 * data-copy 属性做渲染与聚焦，**绝不自己生成"这一幕代表风险升级"**——分幕结构由
 * ``build_story_chapters``（服务端，事实驱动）产出，本文件仅呈现与联动。
 *
 * 点击幕按钮：
 *   1. 更新叙述文案（data-copy）；
 *   2. 按钮激活态；
 *   3. ``__Replay.seek(start_idx)`` 把统一 Evidence Timeline 跳到该幕起点；
 *   4. 高亮 focus refs 对应的 timeline 节点（短暂 story-focus 类，3s 消退）。
 */
(function (global) {
  'use strict';

  function _bind() {
    if (typeof global.document === 'undefined') return;
    var navs = global.document.querySelectorAll('.story-chapters');
    for (var n = 0; n < navs.length; n++) {
      (function (nav) {
        var sid = nav.getAttribute('data-scenario') || '';
        var copyEl = global.document.getElementById('story-copy-' + sid);
        var btns = nav.querySelectorAll('.story-chapter');
        for (var i = 0; i < btns.length; i++) {
          (function (btn) {
            btn.addEventListener('click', function () {
              if (copyEl) copyEl.textContent = btn.getAttribute('data-copy') || '';
              for (var j = 0; j < btns.length; j++) btns[j].classList.remove('active');
              btn.classList.add('active');
              var rp = global.__Replay && global.__Replay.get(sid);
              var start = parseInt(btn.getAttribute('data-start') || '0', 10);
              if (rp && typeof rp.seek === 'function') rp.seek(start);
              var refs = (btn.getAttribute('data-refs') || '').split('|').filter(Boolean);
              global.document.querySelectorAll('.tl-item[data-ref]').forEach(function (li) {
                if (refs.indexOf(li.getAttribute('data-ref')) !== -1) {
                  li.classList.add('story-focus');
                  setTimeout(function () { li.classList.remove('story-focus'); }, 3000);
                }
              });
            });
          })(btns[i]);
        }
      })(navs[n]);
    }
  }

  _bind();

  global.__StoryReplay = { bind: _bind };
})(window);

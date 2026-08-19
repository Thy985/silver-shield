"""live_tabs.js（阶段叙事 tabs）运行时契约测试。

对齐 html-inline-js-behavioral-test 纪律：Node vm + mock DOM 真实运行引擎源码。

覆盖：
- 点击 tab → active 类切换 + 三视图 [hidden] 切换（可直切，不强制顺序）；
- 降级：目标视图缺失 → no-op 不崩。

不依赖 torch/cv2（纯 stdlib + Node），可在 torch-free 环境跑；CI 无 node 则 skip。
"""

from __future__ import annotations

import textwrap

import pytest


def _live_tabs_source() -> str:
    from home_perception.visualizer.viewer import render

    src = render._live_tabs_inline()
    assert src, "live_tabs.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_live_tabs_js_switches_views():
    """Node vm 真实运行：点击 ③ → ③ active + community 视图显示、其余 hidden。"""
    import subprocess
    import tempfile

    src = _live_tabs_source()
    harness = textwrap.dedent(
        r"""
        const fs = require('fs');
        function makeClassList(initial) {
          var set = new Set(initial || []);
          return {
            add: function (c) { set.add(c); },
            remove: function (c) { set.delete(c); },
            contains: function (c) { return set.has(c); },
          };
        }
        function makeTab(view) {
          return {
            attrs: { 'data-view': view },
            classList: makeClassList(['tab'].concat(view === 'discover' ? ['active'] : [])),
            getAttribute: function (k) { return this.attrs[k] != null ? this.attrs[k] : null; },
          };
        }
        const tabs = ['discover', 'family', 'community'].map(makeTab);
        const nav = {
          attrs: { 'data-scenario': 'live_t1' },
          listeners: {},
          getAttribute: function (k) { return this.attrs[k]; },
          querySelectorAll: function (sel) { return sel === '.tab' ? tabs : []; },
          addEventListener: function (ev, fn) { this.listeners[ev] = fn; },
        };
        const views = {};
        ['discover', 'family', 'community'].forEach(function (k) {
          views['view-' + k + '-live_t1'] = { hidden: k !== 'discover' };
        });
        global.document = {
          querySelectorAll: function (sel) { return sel === '.tabs[data-live-tabs]' ? [nav] : []; },
          getElementById: function (id) { return views[id] || null; },
        };
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));

        // 直切 ③ 社区处置（不经过 ② —— 可直切，不强制顺序）
        nav.listeners.click({ target: tabs[2] });
        const afterCommunity = {
          active: tabs[2].classList.contains('active'),
          discoverHidden: views['view-discover-live_t1'].hidden === true,
          familyHidden: views['view-family-live_t1'].hidden === true,
          communityShown: views['view-community-live_t1'].hidden === false,
          discoverInactive: !tabs[0].classList.contains('active'),
        };
        // 再直切回 ①
        nav.listeners.click({ target: tabs[0] });
        const backHome = {
          active: tabs[0].classList.contains('active'),
          discoverShown: views['view-discover-live_t1'].hidden === false,
          communityHidden: views['view-community-live_t1'].hidden === true,
        };
        const ok = afterCommunity.active && afterCommunity.discoverHidden
          && afterCommunity.familyHidden && afterCommunity.communityShown
          && afterCommunity.discoverInactive
          && backHome.active && backHome.discoverShown && backHome.communityHidden;
        console.log(ok ? 'LIVE_TABS_OK' : JSON.stringify({ a: afterCommunity, b: backHome }));
        process.exit(ok ? 0 : 1);
        """
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        harness_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        src_path = f.name
    r = subprocess.run(
        ["node", harness_path, src_path],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, f"live_tabs.js 运行时断言失败\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
    assert "LIVE_TABS_OK" in r.stdout

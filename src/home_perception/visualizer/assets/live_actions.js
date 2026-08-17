/* ADR-0036 P0-1 · live_actions.js：行动闭环面板的 Live WS 客户端（Case Viewer Live 模式）。

职责（边界严守）：
- 连接 gateway WS（路径来自面板 data-ws-path，由 descriptor.live_ws_path 注入）；
- 收 ``snapshot`` / ``state_update`` → 渲染处置状态徽章（pending / family_handled / community_done）；
- 按钮点击 → 上行 ``{type:action, warning_id, operator, action}``（复用 ws.py 协议）。

边界铁律（P0-1 设计）：
- 本文件只处理 **UI / Workflow 态**（浏览器 + gateway 会话态），**绝不写 EvidenceProjection**；
- 「完成处置」的 Resolution 事实由后端（state.py 状态机 → ProjectionAccumulator）投影为
  只读证据节点，前端不宣布行动成功；
- 无 ASR / LLM；无墙钟 / 随机数参与证据（仅 UI 反馈）。

降级：WS 不可达 / 无待处置警告 → 面板显示占位文案、按钮禁用，不崩（fail-open 于 UI 层）。
*/

(function (global) {
  'use strict';

  var _STATUS_ZH = {
    pending: '待家属确认',
    family_handled: '家属已确认 · 待社区处置',
    community_done: '社区已完成处置'
  };

  function _byId(panel, sid, key) {
    return panel.querySelector('#closure-' + key + '-' + sid);
  }

  function _render(panel, sid, state) {
    var warnEl = panel.querySelector('.closure-warning');
    var famSt = _byId(panel, sid, 'family-status');
    var comSt = _byId(panel, sid, 'community-status');
    var famAck = _byId(panel, sid, 'family-ack');
    var famNot = _byId(panel, sid, 'family-notify');
    var comAcc = _byId(panel, sid, 'community-accept');
    var comCom = _byId(panel, sid, 'community-complete');
    var buttons = [famAck, famNot, comAcc, comCom].filter(Boolean);

    // 取第一个待处置/进行中的警告作为操作目标（第一版：单警告闭环）。
    var keys = Object.keys(state || {});
    var active = null;
    for (var i = 0; i < keys.length; i++) {
      var st = state[keys[i]] && state[keys[i]].status;
      if (st === 'pending' || st === 'family_handled') { active = keys[i]; break; }
    }
    if (!active) {
      panel.removeAttribute('data-warning-id');
      if (warnEl) { warnEl.textContent = '暂无待处置警告（实时会话未触发风险）'; }
      buttons.forEach(function (b) { b.disabled = true; });
      if (famSt) { famSt.textContent = '—'; }
      if (comSt) { comSt.textContent = '—'; }
      // PR-A：tab 角色视图同步空态（共享状态机）。
      _renderTabViews(panel, null, 'pending');
      return;
    }

    panel.setAttribute('data-warning-id', active);
    if (warnEl) { warnEl.textContent = '待处置警告：' + active.slice(0, 8) + '…'; }
    var cur = state[active].status || 'pending';
    if (famSt) { famSt.textContent = _STATUS_ZH[cur] || cur; }
    if (comSt) { comSt.textContent = _STATUS_ZH[cur] || cur; }
    // 家属按钮仅 pending 可点；社区按钮仅 family_handled 可点；终态全部禁用。
    if (famAck) { famAck.disabled = (cur !== 'pending'); }
    if (famNot) { famNot.disabled = (cur !== 'pending'); }
    if (comAcc) { comAcc.disabled = (cur !== 'family_handled'); }
    if (comCom) { comCom.disabled = (cur !== 'family_handled'); }
    // PR-A：同步渲染 tab ②/③ 角色聚焦视图（共享同一 WS 会话与状态机，切 Tab 不重连）。
    _renderTabViews(panel, active, cur);
  }

  // PR-A：tab 角色视图渲染（家属确认 / 社区处置）。与主面板同一 active warning、同一
  // 状态机映射；视图元素缺失 → no-op（非 Live 骨架页零成本）。
  function _renderTabViews(panel, active, cur) {
    var views = panel._tabViews || [];
    for (var i = 0; i < views.length; i++) {
      var v = views[i];
      var role = v.getAttribute('data-role');
      var warn = v.querySelector('.closure-warning');
      var st = v.querySelector('.closure-status');
      var btns = v.querySelectorAll('.closure-btn');
      if (!active) {
        if (warn) { warn.textContent = '暂无待处置警告（实时会话未触发风险）'; }
        if (st) { st.textContent = '—'; }
        for (var b0 = 0; b0 < btns.length; b0++) { btns[b0].disabled = true; }
        continue;
      }
      if (warn) { warn.textContent = '待处置警告：' + active.slice(0, 8) + '…'; }
      if (st) { st.textContent = _STATUS_ZH[cur] || cur; }
      for (var b = 0; b < btns.length; b++) {
        // 家属视图按钮仅 pending 可点；社区视图按钮仅 family_handled 可点。
        btns[b].disabled = (role === 'family') ? (cur !== 'pending') : (cur !== 'family_handled');
      }
    }
  }

  function _init(panel) {
    var wsPath = panel.getAttribute('data-ws-path') || '/ws';
    var sid = panel.getAttribute('data-scenario') || '';
    var protocol = (global.location.protocol === 'https:') ? 'wss://' : 'ws://';
    var ws;
    try { ws = new WebSocket(protocol + global.location.host + wsPath); } catch (e) { return; }
    panel._liveWs = ws;

    ws.onmessage = function (evt) {
      var msg;
      try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (msg.type === 'snapshot' || msg.type === 'state_update') {
        _render(panel, sid, msg.state || {});
      }
    };

    panel.querySelectorAll('.closure-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!ws || ws.readyState !== 1) { return; }
        var wid = panel.getAttribute('data-warning-id');
        if (!wid) { return; }
        ws.send(JSON.stringify({
          type: 'action',
          warning_id: wid,
          operator: btn.getAttribute('data-operator'),
          action: btn.getAttribute('data-action')
        }));
      });
    });

    // PR-A：绑定 tab ②/③ 角色聚焦视图（.closure-tabview[data-scenario=<sid>]）——
    // 与主面板共享同一 WS 连接（切 Tab 不重订）与同一 active warning（data-warning-id
    // 由主面板 _render 写入）。
    var views = global.document.querySelectorAll('.closure-tabview');
    panel._tabViews = [];
    for (var vi = 0; vi < views.length; vi++) {
      if (views[vi].getAttribute('data-scenario') !== sid) continue;
      panel._tabViews.push(views[vi]);
      (function (view) {
        view.querySelectorAll('.closure-btn').forEach(function (btn) {
          btn.addEventListener('click', function () {
            if (!ws || ws.readyState !== 1) { return; }
            var wid = panel.getAttribute('data-warning-id');
            if (!wid) { return; }
            ws.send(JSON.stringify({
              type: 'action',
              warning_id: wid,
              operator: btn.getAttribute('data-operator'),
              action: btn.getAttribute('data-action')
            }));
          });
        });
      })(views[vi]);
    }
  }

  // 页面加载后初始化所有行动闭环面板（body 末尾注入，DOM 已就绪）。
  document.querySelectorAll('.closure-panel').forEach(function (p) {
    _init(p);
  });

  global.__LiveActions = { init: _init, render: _render };
})(window);

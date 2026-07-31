// State-Driven Dashboard · 可复用片段（示例）
// 来源：Silver Shield dashboard/index.html 提炼。仅展示核心模式，非完整文件。

// ---- 1. 单一前端状态对象（所有渲染的来源）----
var state = {
  frame: null,
  stateMap: {},            // warning_id -> {status, operator}（人工闭环状态）
  warningMap: {},          // 跨帧 upsert 保活
  commandMap: {},          // 跨帧累积三端命令
  behaviorEvents: [],      // 行为里程碑（跨帧去重）
  behaviorSeen: {},
  meta: {}
};

// ---- 2. WS 消息 → 更新 state（Event → State）----
function handle(msg) {
  if (msg.type === "frame") {
    state.frame = msg.view;
    if (msg.meta) state.meta = msg.meta;
    if (msg.state) Object.assign(state.stateMap, msg.state);
    ingestWarnings(msg.active_warnings || []);   // 跨帧保活
    mergeCommands(msg.routed_commands || {});
    ingestBehavior(msg.view, msg.active_warnings || []);
    renderAll();                                 // State → Render
  } else if (msg.type === "snapshot") {
    applySnapshot(msg);                          // 晚连恢复历史
  } else if (msg.type === "state_update") {
    if (msg.state) Object.assign(state.stateMap, msg.state);
    renderClosure(); renderRisks();              // 点击确认后即时刷新
  } else if (msg.type === "source_switched") {
    resetSession(msg.scenario, msg.source, msg.source_type, msg.frames);
  }
}

// ---- 3. 晚连恢复：snapshot → 重建 state → 渲染 ----
function applySnapshot(s) {
  if (!s) return;
  state.warningMap = {};
  (s.warnings || []).forEach(function (w) { if (w && w.warning_id) state.warningMap[w.warning_id] = w; });
  state.commandMap = s.commands || {};
  state.behaviorEvents = (s.behaviors || []).slice();
  renderAll();
  toast("已恢复当前系统状态");
}

// ---- 4. 切换 / 重置：清空本地累积（新视频 = 新会话）----
function resetSession() {
  state.frame = null; state.stateMap = {}; state.warningMap = {};
  state.commandMap = {}; state.behaviorEvents = []; state.behaviorSeen = {};
  renderAll();
}

// ---- 5. 单一渲染入口：state → 各区域 DOM ----
function renderAll() {
  renderVideo();
  renderTimeline();
  renderRisks();
  renderClosure();
  renderRouting();
  renderStatus();
}

// ---- 6. 跨帧保活（不逐帧覆盖，避免闪现）----
function ingestWarnings(list) {
  for (var i = 0; i < list.length; i++) {
    var w = list[i];
    if (w && w.warning_id) state.warningMap[w.warning_id] = w;   // upsert
  }
  for (var wid in state.warningMap) {
    if (state.warningMap[wid].status === "RESOLVED") delete state.warningMap[wid];
  }
}

// WebSocket 绑定
ws.onmessage = function (ev) {
  var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
  handle(msg);
};

# Pattern · State-Driven Dashboard（状态驱动 Dashboard）

> 核心思想：**Event → State → Render**，而不是 **Event → 直接改 DOM**。

- 来源：Silver Shield `dashboard/index.html`（`ws.onmessage` → `handle` → `state` → `renderAll`）
- 类别：[06-Frontend-Patterns](../README.md)
- 阶段：一

---

## 错误做法

```javascript
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  // 直接把 msg 塞进 DOM
  document.getElementById("risk").innerHTML = ...;
};
```

每来一条消息就直接改 DOM → 状态散落、难恢复、难调试、晚连空白、切换串场。

---

## 正确做法

```
ws.onmessage(ev)
   ↓ handle(msg)
   ↓ 更新 state（单一前端状态对象）
   ↓ renderAll()（state → 各区域 DOM）
```

**关键结构**

```javascript
var state = {
  frame: null,
  stateMap: {},          // warning_id -> {status, operator}（人工闭环状态）
  warningMap: {},        // 跨帧 upsert 保活
  commandMap: {},        // 跨帧累积三端命令
  behaviorEvents: [],    // 行为里程碑（跨帧去重）
  meta: {}               // 运行时元数据
};

function handle(msg) {
  if (msg.type === "frame") {
    Object.assign(state, ...);     // Event → State
    ingestWarnings(...); mergeCommands(...); ingestBehavior(...);
    renderAll();                   // State → Render
  } else if (msg.type === "snapshot") {
    applySnapshot(msg);           // 晚连恢复历史
  } else if (msg.type === "state_update") {
    Object.assign(state.stateMap, msg.state);
    renderClosure(); renderRisks();
  } else if (msg.type === "source_switched") {
    resetSession(...);             // 切换/重置清空本地累积
  }
}

function renderAll() {            // 单一渲染入口
  renderVideo(); renderTimeline(); renderRisks();
  renderClosure(); renderRouting(); renderStatus();
}
```

**晚连恢复（snapshot）**

```javascript
function applySnapshot(s) {
  state.warningMap = {}; (s.warnings||[]).forEach(w => state.warningMap[w.warning_id]=w);
  state.commandMap = ...; state.behaviorEvents = (s.behaviors||[]).slice();
  renderAll();                    // 恢复后纯渲染
}
```

**切换/重置（resetSession）**

```javascript
function resetSession() {         // 新视频 = 新会话
  state.frame=null; state.stateMap={}; state.warningMap={};
  state.commandMap={}; state.behaviorEvents=[];
  renderAll();
}
```

> 真实完整片段见 [example/dashboard-state.js](example/dashboard-state.js)。

---

## 为什么这样设计

- **状态是一等公民**：所有渲染从 `state` 派生，UI 永远可预测。
- **可恢复**：snapshot 恢复 state，而非重新累积。
- **可重置**：resetSession 清空 state，不串场。
- **好调试**：所有状态在一个对象里，出问题时打印 `state` 即可。

---

## 相关资产

- 多角色投影：[multi-role-projection](multi-role-projection/pattern.md)
- 状态聚合：[../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md](../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- 调试：[../../04-Debug-Patterns/runtime-data-flow-debug/pattern.md](../../04-Debug-Patterns/runtime-data-flow-debug/pattern.md)

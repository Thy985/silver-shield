# Pattern · Snapshot Recovery（快照恢复）

> 解决：**用户晚进入系统，为什么看不到之前发生的事情？**

- 来源：Silver Shield ADR-0016 能力 2（首次连接 Snapshot 恢复）
- 类别：[05-Demo-Engineering](../README.md)
- 阶段：二

---

## 问题

演示进行到一半，评委才打开页面 → 连上了，但历史上下文全丢，看到的是空白。

根因：聚合状态只在客户端、且逐帧覆盖，无服务端快照。

---

## 方案

服务端维护**全量状态快照**（聚合状态），新连接建立时第一帧推送 snapshot。

```
新 WS 连接
   → 服务端立即推送 snapshot（warnings / commands / behaviors / 运行时元数据）
   → 之后增量推送 frame
```

**契约**
- snapshot 结构与增量 `frame` 状态结构**一致**，前端无需区分「首帧」与「后续帧」。
- snapshot 含行为去重键与访客映射，便于客户端**精确恢复**累积状态（而非重新累积）。
- 客户端 `applySnapshot()`：恢复本地 maps → 继续接收增量 → `renderAll()`。

---

## 为什么这样设计

- 表达真实系统语义：「设备端不断运行、用户随时打开 App」。
- 与聚合状态（单一事实来源）同源：snapshot 是聚合状态的投影。
- 不增加新消息类型负担：复用既有 frame 状态结构。

---

## 相关资产

- 状态聚合：[../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md](../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- 状态驱动 UI：[../../06-Frontend-Patterns/state-driven-dashboard/pattern.md](../../06-Frontend-Patterns/state-driven-dashboard/pattern.md)
- 生命周期：[lifecycle](lifecycle/pattern.md)
- 失败案例：[../../09-Failure-Cases/pipeline-state-pollution.md](../../09-Failure-Cases/pipeline-state-pollution.md)（无快照的真实代价）

# Pattern · Cross-Frame State Aggregation（跨帧状态聚合）

> 解决：实时系统中，**跨帧累积的状态**（风险卡、行为时间线、命令）如何不丢、不串、不闪。

- 来源：Silver Shield `state.py`（`DemoAggregateState`：warningMap / commandMap / behaviorEvents 上移到服务端）
- 类别：[02-Code-Patterns](../README.md)
- 阶段：一

---

## 问题

实时视觉 / Agent 事件流 / IoT 系统中，单帧数据不足以叙事。需要把多帧信息**累积**成「当前有哪些风险 / 行为如何演化 / 命令是否到位」。
朴素做法：状态只在客户端、逐帧覆盖 → 出现三类问题：
- **闪现即消失**：网关每帧只广播「当前帧」数据，风险卡一帧出现下一帧被覆盖。
- **晚连无历史**：后来连接的用户看不到之前累积的状态。
- **串场**：切换输入源，旧数据残留在新会话。

---

## 原始方案

```
每帧 WS 消息 → 前端直接覆盖 DOM
```

状态活在 DOM 里，无服务端权威源，无快照，无重置。

---

## 最终方案（模式）

把跨帧累积状态提升为**服务端单一事实来源（Single Source of Truth）**，客户端退化为「快照渲染器 + 增量消费者」。

```
服务端 DemoAggregateState (权威)
   ├─ warnings:  dict[warning_id, WarningView]   # 跨帧 upsert 保活，终态移除，超上限 prune
   ├─ behaviors: list[BehaviorEvent]             # 跨帧去重里程碑（enter|vid / pe|vid|type / warn|wid）
   ├─ commands:  dict[warning_id, dict[type, CommandView]]  # 按 warning_id 跨帧累积
   ↓ 每帧 ingest(active_warnings, routed_commands, perception_events, warnings)
   ↓ WS 下行：每帧 frame（含状态）+ 新连接先发 snapshot
客户端
   └─ 接收 snapshot 恢复本地 maps；接收 frame 增量更新；纯渲染，不再自管累积
```

**三个关键机制**
1. **保活（upsert）**：按 `warning_id` 跨帧 upsert，不逐帧覆盖 → 不闪现。
2. **快照（snapshot）**：新连接先推全量聚合状态 → 晚连有历史。
3. **清空（clear）**：reset / 切换源时清空聚合 → 不串场。

---

## 为什么这样设计

- **状态是一等公民**：跨时间运行后，状态管理优先级超过模型。
- **单一事实来源**：多消费者（AI 中心 / 家属 / 社区）共享同一状态，避免各自累积不一致。
- **未来产品雏形**：聚合状态即真实产品（家属 App / 社区 Web）的第一个服务端状态源。

---

## 适用范围

实时视觉、Agent 事件流、IoT 流处理、任何「单帧不足、需跨帧叙事」的系统。

---

## 相关资产

- 代码：[aggregate.py](aggregate.py)（抽取骨架）
- 生命周期：[../lifecycle-management/pattern.md](../lifecycle-management/pattern.md)
- 状态驱动 UI：[../../06-Frontend-Patterns/state-driven-dashboard/pattern.md](../../06-Frontend-Patterns/state-driven-dashboard/pattern.md)
- 快照恢复：[../../05-Demo-Engineering/snapshot-recovery/pattern.md](../../05-Demo-Engineering/snapshot-recovery/pattern.md)
- ADR：[../../08-ADR-Templates/state-separation/pattern.md](../../08-ADR-Templates/state-separation/pattern.md)

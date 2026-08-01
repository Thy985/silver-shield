# Pattern · Multi-Role Projection（多角色投影）

> 多个角色消费同一个实时状态，该怎么设计？

- 来源：Silver Shield ADR-0017（三角色视角）+ Dashboard 三视图
- 类别：[06-Frontend-Patterns](../README.md)
- 阶段：二

---

## 错误做法

```
Family Page   自己请求一份状态
Community Page 自己请求一份状态
AI Page       自己请求一份状态
```

每个角色一个独立应用 / 独立数据源 → 多份状态各自累积、易不一致、接口碎片化、工程量指数增加。

---

## 正确做法

```
WebSocket
   ↓
Shared State（服务端权威聚合状态）
   ↓ 投影
Views:  AI中心 / 家属端 / 社区端
```

- 一个风险事件，被三个消费者共享（单一事实来源 + 角色视图投影）。
- 新增一个消费者 ≈ 加一个视图，零成本；而不是加一套系统。
- 切换视图**不重连 / 不重订** WS——共享同一连接与同一 state。

---

## 银龄盾实例

```
单一事实源（DemoAggregateState）
   ↓ 投影
视图① 风险发现 / 视图② 家属确认 / 视图③ 社区处置
```

一个 `warning_id` 流过三个视图 = 一个事实被多角色共享。评委三问（AI 理解行为？风险有解释？发现后产生行动？）全部落在一条故事线上。

---

## 为什么这样设计

- 多角色需求 ≠ 多系统需求。先冻结事实源，再用视图投影满足多角色，避免重复建设。
- 切换视图只是「换投影」，不动连接、不动状态——稳定且廉价。

---

## 相关资产

- 状态驱动：[state-driven-dashboard](state-driven-dashboard/pattern.md)
- 状态聚合：[../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md](../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- 代码：[state-driven-dashboard/example/dashboard-state.js](state-driven-dashboard/example/dashboard-state.js)
- ADR：[../../08-ADR-Templates/state-separation/pattern.md](../../08-ADR-Templates/state-separation/pattern.md)

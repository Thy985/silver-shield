# Pattern · Runtime Data-Flow Debug（实时数据流调试）

> 记录：**从症状到根因的定位方法**，而非单个 bug。

- 来源：Silver Shield 调试实践（Dashboard 空白 / 状态污染 / 晚连丢失）
- 类别：[04-Debug-Patterns](../README.md)
- 阶段：一

---

## 错误做法（本能但致命）

```
Dashboard 没有数据
   ↓
修改前端
```

看到 UI 异常就直接改前端——可能在改一个根本没问题的展示层，而真病根在更底层。

---

## 正确做法（自下而上，5 层定位）

用户看到异常 → 按以下顺序逐层追问，**哪一层断了，就从哪一层修**：

```
用户看到异常
   ↓
① 数据是否产生？   （Fact Debug：感知层真的产出事实事件了吗？）
   ↓
② 事件是否传输？   （Pipeline Debug：事实→特征→规则→决策，链路通了吗？）
   ↓
③ 协议是否收发？   （Protocol Debug：WS / MQTT 帧结构、topic、字段对吗？）
   ↓
④ 状态是否保存？   （State Debug：映射表 / 会话 / 快照 / 循环 / 重置语义对吗？）
   ↓
⑤ 展示是否消费？   （UI Debug：前端真的读了正确状态源吗？）
```

> 银龄盾实证：大部分「展示层问题」（时间线空白、风险卡闪烁、视图无内容）根因在 ①~④ 层，而非 ⑤。修前端只是掩盖。

---

## 关键陷阱（状态双源语义）

风险卡显示「已确认」但闭环没人确认——根因是前端误读了**系统下发状态**（`CONFIRMED` = MQTT 已送达）当成了**人工闭环状态**（pending / family_handled / community_done）。状态有两套语义，展示层必须读对那一套。
→ 详见 [09-Failure-Cases/risk-card-false-confirmed](../09-Failure-Cases/risk-card-false-confirmed.md)

---

## 决策树

见 [decision-tree.md](decision-tree.md)。

---

## 相关资产

- 失败案例：[../09-Failure-Cases/README.md](../09-Failure-Cases/README.md)
- 状态聚合：[../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md](../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- 状态驱动 UI：[../../06-Frontend-Patterns/state-driven-dashboard/pattern.md](../../06-Frontend-Patterns/state-driven-dashboard/pattern.md)

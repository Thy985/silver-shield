# 06 · Frontend Patterns（前端架构资产）

> 前端不是「调接口画图」，而是**消费单一事实来源的投影层**。

## 本目录资产

| 资产 | 阶段 | 说明 |
|------|------|------|
| [multi-role-projection](multi-role-projection/pattern.md) | 二 | 多角色消费同一实时状态：WS→Shared State→Views |
| [state-driven-dashboard](state-driven-dashboard/pattern.md) | 一 | Event→State→Render，而非 Event→DOM |

## 核心思想

```
错误： Family Page 自己请求 / Community Page 自己请求   → 多份状态、各自累积、易不一致
正确： WebSocket → Shared State → Views (AI / Family / Community)  → 单一事实源投影
```

展示层只经**白名单 + 只读**消费核心（冻结边界），不得反向改核心。

## 参见

- 状态聚合：[../02-Code-Patterns/cross-frame-state-aggregation](../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- 调试：[../04-Debug-Patterns/runtime-data-flow-debug](../04-Debug-Patterns/runtime-data-flow-debug/pattern.md)

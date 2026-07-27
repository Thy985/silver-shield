# Pattern · Dispatcher Routing（动作路由）

> 风险语义 → 动作指令 → **1:1 路由到正确接收方**，且路由可配置。

- 来源：Silver Shield `action/dispatcher.py`（Dispatcher 1:1 路由）
- 类别：[07-Backend-Patterns](../README.md)
- 阶段：二

---

## 问题

决策层产出风险后，若把「发给谁 / 怎么发」硬编码进决策或展示层，会出现：
- 动作层偷偷再做一次风险决策（职责混乱）；
- 路由逻辑散落在多端，难以一致与演进。

---

## 方案

动作层**只翻译，不重新判断**；路由规则**可配置、1:1**。

```
DecisionEngine → ActionCommand(recommended_action)
   ↓
Dispatcher（1:1 路由，可配置）
   ├─ ESCALATE_COMMUNITY → 仅 CREATE_COMMUNITY_TASK
   ├─ NOTIFY_FAMILY      → SEND_FAMILY_MESSAGE（family_contact 非空时）
   └─ MONITOR            → LOG_ONLY
```

**银龄盾实证路由**
- `ESCALATE_COMMUNITY` → 仅社区任务（1:1，不污染家属端）
- `family_contact` 为 null 时，`NOTIFY_FAMILY` 降级 `LOG_ONLY`（不静默丢，也不误发）

**桥接（bridge.route_commands）**按 `command_type` 把命令分到 `family / community / log_only` 三桶，供三视图消费——单一事实源投影。

---

## 为什么这样设计

- 动作层职责单一：翻译风险语义为指令类型，不重新决策。
- 路由可配置：加接收方 / 调阈值不影响决策核心。
- 1:1 路由保证「一个风险只走一条正确路径」，避免多端重复或漏发。

---

## 相关资产

- 事件契约：[event-contract](event-contract/pattern.md)
- 多角色投影：[../../06-Frontend-Patterns/multi-role-projection/pattern.md](../../06-Frontend-Patterns/multi-role-projection/pattern.md)
- 架构分层：[../../01-Architecture-Patterns/AI-Pipeline-Separation/pattern.md](../../01-Architecture-Patterns/AI-Pipeline-Separation/pattern.md)

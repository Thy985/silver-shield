# Pattern · Event Contract（事件契约）

> 事件 Schema / 消息契约**必须稳定**，下游才能放心消费。

- 来源：Silver Shield ADR-0005（事件 Schema + MQTT 契约稳定）+ ADR-0014 L1
- 类别：[07-Backend-Patterns](../README.md)
- 阶段：二

---

## 问题

下游（中心风控 / 家属端 / 社区端）直接消费事件 JSON。一旦上游改字段名 / 类型 / 语义，下游全断，且难以察觉。

---

## 方案

把事件 Schema 与消息契约当作**冻结接口**管理：

**冻结（改动 = BREAKING，须 Owner 评审 + 升 MAJOR）**
- 字段名称（如 `visitor_id` 不得改名 `person_id`）
- 字段类型 / 语义
- 时间格式：UTC timezone-aware，ISO 8601
- 枚举取值（EventType / risk_level / command_type 等字面量）

**允许（向后兼容，升 MINOR）**
- 新增 optional 字段（带默认值）
- 经 `meta` 逃生舱承载实验性信息（但被 ≥2 消费方稳定依赖时须晋升为正式字段）

**MQTT 契约**
- topic 固定：`silvershield/home/{device_id}/events`
- payload 为事件 JSON
- 离线 ring buffer；publish 失败**不丢事件**

---

## 为什么这样设计

- 契约是下游信任的基础；不稳定 = 系统不可维护。
- 用 SemVer 映射三级冻结，变更有明确红线。
- `meta` 逃生舱 + 晋升条款，防止「事实上的不稳定 Schema」。

---

## 相关资产

- 冻结边界：[../../08-ADR-Templates/freeze-boundary/pattern.md](../../08-ADR-Templates/freeze-boundary/pattern.md)
- 契约测试：[../03-Test-Patterns/contract-test/pattern.md](../03-Test-Patterns/contract-test/pattern.md)
- 路由：[dispatcher-routing](dispatcher-routing/pattern.md)

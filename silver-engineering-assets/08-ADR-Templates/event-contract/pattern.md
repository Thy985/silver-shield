# ADR Template · Event Contract（事件契约稳定）

> 当系统有**下游消费事件 / 消息**（MQTT / Kafka / WS）时写这个 ADR。

## 模板（复制填写）

```markdown
# ADR-NNNN: 事件 Schema / 消息契约稳定
- 状态：Proposed
- 日期：

## 背景（Context）
（下游（中心/多端）直接消费事件 JSON；上游改字段名/类型/语义 → 下游全断且难察觉。）

## 决策（Decision）
### 冻结（改动 = BREAKING，须 Owner 评审 + 升 MAJOR）
- 字段名称 / 类型 / 语义
- 时间格式：UTC tz-aware，ISO 8601
- 枚举取值（EventType / risk_level / command_type 等字面量）

### 允许（向后兼容，升 MINOR）
- 新增 optional 字段（带默认值）
- meta 逃生舱承载实验性信息（被 ≥2 消费方稳定依赖时须晋升正式字段）

### 消息契约
- topic 固定
- payload 为事件 JSON
- 离线缓冲；发布失败不丢事件

## 版本策略
（SemVer 映射：破坏数据契约 = MAJOR；新增可选字段 = MINOR；实现变化 = PATCH）

## 后果（Consequences）
- 正面：下游可信赖；变更有红线
- 负面：演进需走版本流程（刻意摩擦）

## 替代方案（Alternatives）
- 不冻结靠文档约定：否决（口头约定易被"能跑就行"破坏）
```

## 银龄盾实例（参照）

- ADR-0005 事件 Schema + MQTT 契约稳定；ADR-0014 L1（真实版本见 `docs/ADR/0005-*`、`docs/ADR/0014-*`）。
- 关联模式：[../../07-Backend-Patterns/event-contract](../07-Backend-Patterns/event-contract/pattern.md) · [../../03-Test-Patterns/contract-test](../03-Test-Patterns/contract-test/pattern.md) · [../../08-ADR-Templates/freeze-boundary](freeze-boundary/pattern.md)

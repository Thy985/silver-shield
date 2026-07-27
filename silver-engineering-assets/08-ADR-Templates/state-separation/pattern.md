# ADR Template · State Separation（状态分离 / 单一事实来源）

> 当**状态跨时间累积**且被多消费者共享时写这个 ADR。

## 模板（复制填写）

```markdown
# ADR-NNNN: 状态作为一等公民（State Separation）
- 状态：Proposed
- 日期：

## 背景（Context）
（展示层 / 多消费者各自累积状态 → 不一致、晚连空白、串场。
 最大 Bug 来自状态，而非模型：映射表跳变 / 晚连丢失 / 多轮崩 / reset 不净。）

## 决策（Decision）
- 把跨帧累积状态提升为**服务端单一事实来源（SSOT）**。
- 客户端退化为「快照渲染器 + 增量消费者」。
- 状态层有独立可验证契约，不散落各模块临时变量。
- 区分「模型加载」与「状态初始化」：reset 清状态不重载模型。

## 状态分类（须显式列出）
- 聚合状态：warnings / commands / behaviors / session
- 映射表：warningMap / commandMap / behaviorMap
- 会话 / 快照 / 循环计数 / 重置语义

## 后果（Consequences）
- 正面：多消费者共享一致状态；可恢复（snapshot）；可重置（clear）
- 负面：服务端需持有并 prune 状态（防无限增长）

## 替代方案（Alternatives）
- 状态留客户端：否决（晚连空白、串场）
- 每消费者独立状态：否决（不一致、接口碎片化）
```

## 银龄盾实例（参照）

- ADR-0016 §4「单一事实来源」（`DemoAggregateState` 上移服务端）。
- 关键洞察：**状态管理优先级超过模型本身**——很多 AI 项目最后失败不是模型不行，而是状态管理崩了。
- 关联模式：[../../02-Code-Patterns/cross-frame-state-aggregation](../02-Code-Patterns/cross-frame-state-aggregation/pattern.md) · [../../06-Frontend-Patterns/state-driven-dashboard](../06-Frontend-Patterns/state-driven-dashboard/pattern.md) · [../../06-Frontend-Patterns/multi-role-projection](../06-Frontend-Patterns/multi-role-projection/pattern.md)

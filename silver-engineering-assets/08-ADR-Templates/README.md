# 08 · ADR Templates（架构决策模板）

> 把**架构决策本身**升级为可套模板。以后新项目直接抄，不重想结构。

## 什么是 ADR

Architecture Decision Record——记录「为什么这么定」，而非「定成什么样」。
文件名 `NNNN-<kebab-title>.md`，NNNN 从 0001 递增不复用。
状态：`Proposed → Accepted → Superseded by ADR-NNNN / Deprecated`。
必含：**背景 / 决策 / 动机 / 后果 / 替代方案**。

## 本目录资产（4 个高频模板）

| 模板 | 阶段 | 何时写 |
|------|------|--------|
| [runtime-lifecycle](runtime-lifecycle/pattern.md) | 一 | 系统要长时运行 / 可重复演示 |
| [freeze-boundary](freeze-boundary/pattern.md) | 一 | 核心被多处消费，要防腐化 |
| [state-separation](state-separation/pattern.md) | 一 | 状态跨时间累积，需单一事实来源 |
| [event-contract](event-contract/pattern.md) | 二 | 有下游消费事件 / 消息 |

## 用法

1. 复制对应模板到新项目 `docs/ADR/`。
2. 填背景 / 决策 / 动机 / 后果 / 替代方案。
3. 标状态（Proposed → Owner 评审 → Accepted）。
4. 关联引用其他 ADR。

## 银龄盾实例（已沉淀，可作参照）

- ADR-0014 三级冻结治理 → 本库 freeze-boundary 模板的来源
- ADR-0016 Demo 运行时生命周期 → runtime-lifecycle 模板的来源
- ADR-0015 演示架构与冻结边界 → 对应 freeze-boundary + state-separation
- ADR-0005 事件 Schema/MQTT 契约 → event-contract 模板的来源

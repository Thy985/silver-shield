# 05 · Demo Engineering（Demo 工程资产）

> **银龄盾最独特的价值之一。** 很多团队：模型完成 → 做个网页 → 结束。
> 你验证了：**Demo 其实是一种产品入口。**

## 本目录资产

| 资产 | 阶段 | 说明 |
|------|------|------|
| [lifecycle](lifecycle/pattern.md) | 二 | Demo 生命周期：CREATED→…→STOPPED |
| [snapshot-recovery](snapshot-recovery/pattern.md) | 二 | 用户晚进入为何看不到历史 |
| [scenario-management](scenario-management/pattern.md) | 二 | 演示必须确定性触发，而非碰运气 |

## 认知转变

Demo = 第一个产品入口。它必须具备产品级能力：
**真实输入上传、生命周期管理、状态快照、Session 管理、Scenario 管理**。

> 银龄盾实证：正是把 Demo 当「产品入口」而非「幻灯片」，才推动了真实视频输入 + 生命周期、`reset` 接口、WS snapshot 等原本「产品」才有的能力。

## 参见

- 代码模式：[../02-Code-Patterns/lifecycle-management](../02-Code-Patterns/lifecycle-management/pattern.md)
- 状态聚合：[../02-Code-Patterns/cross-frame-state-aggregation](../02-Code-Patterns/cross-frame-state-aggregation/pattern.md)
- ADR：[../08-ADR-Templates/runtime-lifecycle](../08-ADR-Templates/runtime-lifecycle/pattern.md)

# AI System Engineering Handbook

> 统一索引。把分散的资产，组织成「理论 → 代码 → 实践」一条主线。
>
> 目标：让任何一个新 AI 系统项目，能在这里找到**起点**与**对照**。

```
                    AI System Engineering Handbook
                              |
                    ----------------------
                    |          |          |
                 Theory      Code      Practice
                    |          |          |
          Architecture   Code Patterns   Failure Cases
            Patterns      Test Patterns   Debug Patterns
           ADR Templates  Frontend/      Demo Engineering
                         Backend Pat.
                    |          |          |
                    ----------------------
                              |
                     Silver Shield Assets
                    （本仓库即第一个实例）
```

---

## 一、Theory（为什么这么设计）

| 主题 | 资产位置 |
|------|----------|
| AI 流水线该如何分层 | [01-Architecture-Patterns/AI-Pipeline-Separation](01-Architecture-Patterns/AI-Pipeline-Separation/pattern.md) |
| 冻结边界治理（三级） | [08-ADR-Templates/freeze-boundary](08-ADR-Templates/freeze-boundary/pattern.md) |
| 运行时生命周期 | [08-ADR-Templates/runtime-lifecycle](08-ADR-Templates/runtime-lifecycle/pattern.md) |
| 状态分离（单一事实来源） | [08-ADR-Templates/state-separation](08-ADR-Templates/state-separation/pattern.md) |
| 事件 / Schema 契约 | [08-ADR-Templates/event-contract](08-ADR-Templates/event-contract/pattern.md) |
| 银龄盾踩过的坑与教训 | [00-Overview/SilverShield-Lessons](00-Overview/SilverShield-Lessons.md) |

---

## 二、Code（可复用的代码骨架）

| 主题 | 资产位置 |
|------|----------|
| 生命周期管理（reset / 重建 / 会话隔离） | [02-Code-Patterns/lifecycle-management](02-Code-Patterns/lifecycle-management/pattern.md) |
| 跨帧状态聚合（warningMap / commandMap / 行为时间线） | [02-Code-Patterns/cross-frame-state-aggregation](02-Code-Patterns/cross-frame-state-aggregation/pattern.md) |
| 状态驱动 Dashboard（Event→State→Render） | [06-Frontend-Patterns/state-driven-dashboard](06-Frontend-Patterns/state-driven-dashboard/pattern.md) |
| 多角色投影（单一事实源 → 多视图） | [06-Frontend-Patterns/multi-role-projection](06-Frontend-Patterns/multi-role-projection/pattern.md) |
| 事件契约 + 1:1 路由 | [07-Backend-Patterns/event-contract](07-Backend-Patterns/event-contract/pattern.md) · [dispatcher-routing](07-Backend-Patterns/dispatcher-routing/pattern.md) |

---

## 三、Practice（验证与排错）

| 主题 | 资产位置 |
|------|----------|
| E2E 验证模板（真实链路闭环） | [03-Test-Patterns/e2e-test](03-Test-Patterns/e2e-test/pattern.md) |
| 契约测试（攻击性边界守护） | [03-Test-Patterns/contract-test](03-Test-Patterns/contract-test/pattern.md) |
| 单元测试 / 集成测试 | [03-Test-Patterns/unit-test](03-Test-Patterns/unit-test/README.md) · [integration-test](03-Test-Patterns/integration-test/README.md) |
| 实时系统空白 → 根因定位 | [04-Debug-Patterns/runtime-data-flow-debug](04-Debug-Patterns/runtime-data-flow-debug/pattern.md) |
| Demo 工程（生命周期 / 快照 / 场景） | [05-Demo-Engineering](05-Demo-Engineering/README.md) |
| **失败案例库（最高价值）** | [09-Failure-Cases](09-Failure-Cases/README.md) |

---

## 一句话导航

- 新项目立项 → 先看 Theory（架构 + ADR 模板）→ 抄 Code 骨架 → 用 Practice 验证。
- 系统出 bug → 直奔 09-Failure-Cases 对照，再走 04-Debug-Patterns 流程。
- 被评委 / 客户问「这系统真的成立吗」→ 用 03-Test-Patterns/e2e-test 证明闭环。

# Asset Map（资产地图）

> 本库全部资产的清单。标记「阶段」对应推荐的沉淀顺序（一/二/三）。

## 01 · Architecture Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [AI-Pipeline-Separation](../../01-Architecture-Patterns/AI-Pipeline-Separation/pattern.md) | 三 | 复杂 AI 系统怎么拆：感知→事实→特征→规则→决策→动作→展示 |

## 02 · Code Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [lifecycle-management](../../02-Code-Patterns/lifecycle-management/pattern.md) | 一 | 状态污染 / 多次运行 / reset / 会话隔离 |
| [cross-frame-state-aggregation](../../02-Code-Patterns/cross-frame-state-aggregation/pattern.md) | 一 | warningMap / commandMap / 行为时间线：实时视觉 / Agent / IoT 通用 |

## 03 · Test Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [unit-test](../../03-Test-Patterns/unit-test/README.md) | 二 | 单模块测试 |
| [contract-test](../../03-Test-Patterns/contract-test/pattern.md) | 一 | 攻击性边界守护（冻结契约） |
| [integration-test](../../03-Test-Patterns/integration-test/README.md) | 二 | 模块组合测试 |
| [e2e-test](../../03-Test-Patterns/e2e-test/pattern.md) | 一 | 真实链路闭环验证（最高价值测试资产） |

## 04 · Debug Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [runtime-data-flow-debug](../../04-Debug-Patterns/runtime-data-flow-debug/pattern.md) | 一 | 从症状到根因：数据是否产生→传输→保存→渲染 |

## 05 · Demo Engineering
| 资产 | 阶段 | 说明 |
|------|------|------|
| [lifecycle](../../05-Demo-Engineering/lifecycle/pattern.md) | 二 | CREATED→…→STOPPED 状态机 |
| [snapshot-recovery](../../05-Demo-Engineering/snapshot-recovery/pattern.md) | 二 | 用户晚进入为何看不到历史 |
| [scenario-management](../../05-Demo-Engineering/scenario-management/pattern.md) | 二 | 演示必须确定性触发，而非碰运气 |

## 06 · Frontend Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [multi-role-projection](../../06-Frontend-Patterns/multi-role-projection/pattern.md) | 二 | 多角色消费同一实时状态：WS→Shared State→Views |
| [state-driven-dashboard](../../06-Frontend-Patterns/state-driven-dashboard/pattern.md) | 一 | Event→State→Render，而非 Event→DOM |

## 07 · Backend Patterns
| 资产 | 阶段 | 说明 |
|------|------|------|
| [event-contract](../../07-Backend-Patterns/event-contract/pattern.md) | 二 | 事件 Schema / MQTT 契约稳定 |
| [dispatcher-routing](../../07-Backend-Patterns/dispatcher-routing/pattern.md) | 二 | 动作 1:1 路由、可配置 |

## 08 · ADR Templates
| 资产 | 阶段 | 说明 |
|------|------|------|
| [runtime-lifecycle](../../08-ADR-Templates/runtime-lifecycle/pattern.md) | 一 | 把「运行时生命周期」决策升级为可套模板 |
| [freeze-boundary](../../08-ADR-Templates/freeze-boundary/pattern.md) | 一 | 三级冻结治理模板 |
| [state-separation](../../08-ADR-Templates/state-separation/pattern.md) | 一 | 状态作为一等公民 / 单一事实来源 |
| [event-contract](../../08-ADR-Templates/event-contract/pattern.md) | 二 | 事件契约稳定性决策 |

## 09 · Failure Cases
| 资产 | 阶段 | 说明 |
|------|------|------|
| [pipeline-state-pollution](../../09-Failure-Cases/pipeline-state-pollution.md) | 一 | 循环播放后无 warning：Tracker 状态未 reset |
| [high-not-appearing](../../09-Failure-Cases/high-not-appearing.md) | 一 | HIGH 不出现：不是模型问题，是时间尺度错配 |
| [click-confirm-ineffective](../../09-Failure-Cases/click-confirm-ineffective.md) | 一 | 点击确认无效：状态机设计错误 |
| [risk-card-false-confirmed](../../09-Failure-Cases/risk-card-false-confirmed.md) | 一 | 风险卡误显「已确认」：状态双源语义混淆 |

---

## 统计

- 10 个类别目录
- 4 个失败案例（最高价值，建议持续扩充）
- 第一阶段（最高价值）资产已全部覆盖

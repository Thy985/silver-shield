---
name: risk-card-false-confirmed
title: 风险卡误显「已确认」：状态双源语义混淆
category: Failure Case
phase: 1
root_cause: 状态语义分轴
refs:
  - src/silver_demo/dashboard/index.html  (SYS_STATUS / renderRisks L981-998)
  - src/silver_demo/state.py::DemoStateStore  (人工闭环 status)
  - 07-Backend-Patterns/event-contract
---

# 案例：风险卡误显「已确认」（状态双源语义混淆）

## 现象

- 风险卡上，明明**没有任何人**点击「已确认」，却显示「已确认 / 已处理」。
- 实际只是系统把指令（通知家属、建社区任务）**下发成功**了，状态却被渲染成「人已确认」。
- 家属/社区角色看到「已确认」，以为对方已处理，闭环假象，真实风险可能被漏掉。
- 第一直觉：「状态机 bug」「谁偷偷点了」「store 写串了」——根因在「两个 status 被当成同一个」。

## 根因

系统里存在**两条正交的状态轴**，早期被混用同一个 `status` 字段：

1. **系统侧指令下发状态**（`WarningEvent.status`）：`CREATED → PENDING → CONFIRMED → RESOLVED/REJECTED`。
   `CONFIRMED` 的含义是「指令已送达下游（如 MQTT 投递成功）」，**与「人是否确认」无关**。
2. **人工闭环状态**（`DemoStateStore`）：`pending → family_handled → community_done`。
   这才是「家属/社区是否真的处理」。

前端曾直接用 `WarningEvent.status == CONFIRMED` 来渲染「已确认」徽标，导致「系统送达」被误读为「人已确认」。

## 错误假设

> 「warning 上的 status 就代表它有没有被处理完。」

错。一个 warning 同时有两层进度：
- **机器进度**：指令生成了没、发出去了没（系统轴）；
- **人进度**：家属确认了没、社区处置了没（人工轴）。
把系统轴的 `CONFIRMED` 当成人轴终点，是最典型的「状态语义合并陷阱」。

## 修复

前端**显式分轴**，两轴用不同来源、不同标签、不同配色，且人工轴永不从系统轴读取：

```javascript
// 风险卡主状态 = 人工闭环状态（来自 state.stateMap，与 ②③ 共享）
var closure = state.stateMap[w.warning_id] ? state.stateMap[w.warning_id].status : "pending";
// 系统轴只作附属小标，且文案明确「非人工确认」
var sysStat = SYS_STATUS[w.status] || w.status || "";
// SYS_STATUS: CONFIRMED -> "系统·已送达"（而非「已确认」）
```

- 主状态标签（`cl-status`）渲染 `closure`（pending/family_handled/community_done）。
- 附属小标（`sys-status`）渲染 `SYS_STATUS[w.status]`，title 写明「系统指令下发状态（非人工确认）」，颜色刻意弱化。
- `WarningEvent.status`（冻结对象，只读消费）**只**用于系统轴，绝不回写人工轴。

契约层面（见 `07-Backend-Patterns/event-contract`）：两条轴在事件 schema 里就是两个独立字段，物理隔离，从协议上杜绝混淆。

## 适用

任何「系统自动动作」与「人审批动作」并存的系统：

- 告警平台、审批流、自动化运维（自动修复 vs 人工确认）、工单系统。
- 抽象原则：**「机器做完了」和「人处理完了」是两条独立状态轴，必须分字段、分标签、分颜色。**
  任一条轴的状态变化，都不能被另一条轴的等值（哪怕字面相同）误代表。
  在事件契约（ADR）里就应当把它们定义为不同的字段，而非共用一个 enum。
- 关联：`08-ADR-Templates/state-separation`、`07-Backend-Patterns/event-contract`。

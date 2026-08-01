---
name: click-confirm-ineffective
title: 点击确认无效：状态机设计错误
category: Failure Case
phase: 1
root_cause: 状态机初值
refs:
  - src/silver_demo/state.py::DemoStateStore.upsert  (L45-82)
  - 06-Frontend-Patterns/state-driven-dashboard
---

# 案例：点击「已确认」无效（状态机初值错误）

## 现象

- 前端风险卡上有「已确认」按钮。点击后：WS 上行 `action` 成功返回，但**风险卡状态不变**，过一会儿还被后端「重置」回 pending。
- 用户再次点击，依旧无效；控制台无报错。
- 第一直觉：「前端没收到回写」「WS 上行格式错」「状态没 render」——但后端确实收到了、状态机也「接受了」。

## 根因

状态存储 `DemoStateStore` 在**首次见到某 warning_id 时，强制初始化为 `pending`**，无视上行请求里带的状态：

```python
# 修复前的错误写法（示意）
if entry is None:
    entry = {"warning_id": warning_id, "status": "pending", ...}  # 永远 pending
```

而交互设计的本意是「**单次点击即确认**」：用户在三视图里点一次，就应从 `pending` 直接跳到 `family_handled`（或 `community_done`）。
由于首见即被钉死成 `pending`，随后单次上行的 `family_handled` 被当成「从 pending 翻转」——

- 若恰好时序上后端先发 snapshot（pending）被前端当作权威、再叠加用户点击，就会出现「点击被静默丢弃 / 被 snapshot 覆盖」的现象。
- 更隐蔽：某些路径下首见请求本身就是带 `family_handled` 的，却被强制降级成 `pending`，闭环永远走不完。

本质：**状态机的「初值」写死成了唯一入口态，剥夺了「首次即非初始态」的合法路径。**

## 错误假设

> 「warning 第一次出现时，一定处于最初始的 pending；任何非 pending 状态都来自后续翻转。」

错。在「上行即闭环」的交互里，**首次写入完全可能直接携带终态或半终态**（用户点得快、或 snapshot 与点击并发）。
状态机的初值应当是「请求所声明的合法状态」，而不是被代码强制覆盖的常量。

## 修复

`upsert` 在首见时**尊重请求的合法非 pending 状态**，仅当请求非法/为空时才回退 `pending`；后续翻转仍受 `TRANSITIONS` 单向约束：

```python
if entry is None:
    # 首次：尊重请求的状态（演示交互「单次点击即确认」需要）。
    # 合法非 pending 状态（family_handled / community_done）直接作为初值，
    # 否则回退 pending。后续翻转仍受 TRANSITIONS 单向约束。
    init_status = status if status in VALID_STATUSES and status != "pending" else "pending"
    entry = {"warning_id": warning_id, "status": init_status, "operator": operator}
    self._state[warning_id] = entry
    return dict(entry)
# 已存在：校验翻转合法性（略）
```

配套回归测试：`test_state_store_first_seen_direct_non_pending`——首见即 `family_handled`，断言返回状态就是 `family_handled` 而非 `pending`。

## 适用

任何「用户动作直接驱动状态机」且「首见与上行可能并发/乱序」的系统：

- 工单闭环、审批流、告警确认、IoT 设备状态上报。
- 抽象原则：**状态机的初值 ≠ 强制入口态。首次写入必须允许携带调用方声明的合法状态；「初始态」只是「未声明时的默认值」，不是「唯一合法首值」。** 翻转约束（不可逆、单向）仍独立生效。
- 关联：`06-Frontend-Patterns/state-driven-dashboard`（上行→State→Render，初值错了整条链路都错）。

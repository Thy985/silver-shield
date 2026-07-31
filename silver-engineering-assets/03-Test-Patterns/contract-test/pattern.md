# Pattern · Contract Test（契约测试 · 攻击性边界守护）

> 守护**冻结边界**不被「能跑就行」的改动悄悄破坏。

- 来源：Silver Shield ADR-0014（三级冻结治理）+ `tests/contract/`
- 类别：[03-Test-Patterns](../README.md)
- 阶段：一

---

## 问题

核心事实层冻结后，仍有「为赶进度快速修改」的退化路径：前端直连模型 / 后端绕过接口 / 字段乱加。普通测试只验证正常路径，抓不到这类腐化。

---

## 原始方案

```
普通测试：输入 A → 输出 B，全绿即「没问题」
```

无法发现「字段改名 / 接口签名悄悄变 / 状态机非法翻转」这类边界破坏。

---

## 最终方案（模式）

**Contract Test = 攻击性测试**：验证系统面对现实世界异常输入时是否保持边界。更接近生产。

**与实现解耦**：只断言契约（字段 / 枚举 / 状态机 / 异常语义 / 装配边界），不依赖具体算法。因此替换模型 / 实现时，Contract Test 应继续通过。

**契约测试矩阵（参考 ADR-0014）**

| 类别 | 攻击输入 | 期望边界行为 | 守护等级 |
|------|----------|--------------|----------|
| 时间异常 | Frame1=10:00:10, Frame2=10:00:05（倒流） | **不得**产生 `duration<0` | L1 |
| 脏输入 | `visitor_id=""` / `duration="abc"` | **不得**进入特征层；schema 拒绝 | L1 |
| 高频压力 | 1 秒 100 帧 enter | **不得**生成 100 个事件；状态机去重 | L2/L3 |
| 状态机攻击 | `CREATED→RESOLVED`（跳步） | **必须拒绝**非法翻转 | L1 |
| 配置攻击 | `long_duration_seconds: -100` | **必须明确报错**，不得静默运行 | L1/L3 |
| 通道失败 | Publisher 返回 `False` | 事件保持 PENDING 等待重试，不丢 | L2 |

三级冻结：L1 Schema 契约 / L2 接口契约 / L3 运行时装配契约。破坏即 BREAKING，须 Owner 评审 + 升版本。

---

## 为什么这样设计

- 变更有明确红线：AI / 人工都能自检「这个改动破坏了哪一级、该升哪位版本」。
- 生产级异常前置到 CI，杜绝「能跑就行」式腐化。
- 三段式装配契约保证 Demo 源 / 生产源可无痛切换。

---

## 相关资产

- 范例：[example/contract_test.py](example/contract_test.py)
- ADR 模板：[../../08-ADR-Templates/freeze-boundary/pattern.md](../../08-ADR-Templates/freeze-boundary/pattern.md)
- E2E：[../e2e-test/pattern.md](../e2e-test/pattern.md)
- 失败案例：[../../09-Failure-Cases/risk-card-false-confirmed.md](../../09-Failure-Cases/risk-card-false-confirmed.md)（边界混淆的真实代价）

# ADR Template · Freeze Boundary（三级冻结治理）

> 当**核心事实层被多处消费**，需要防腐化时写这个 ADR。

## 模板（复制填写）

```markdown
# ADR-NNNN: 契约冻结治理 —— 分级定义 + Contract Test + 版本策略
- 状态：Proposed
- 日期：
- 决策者：Owner
- 相关：

## 背景（Context）
（当前最大风险不是功能不足，而是"进入展示/多消费者开发后为赶进度快速修改导致腐化"。
 描述退化路径：架构清晰 → 前端直连模型/后端绕过接口 → 系统失去一致性。）

## 决策（Decision）
不采用"冻结/不冻结"二元，定义三个冻结等级：

### Frozen Level 1 · Schema Contract（数据契约 —— 必须冻结）
- 冻结对象（下游直接消费的数据对象）
- 冻结：字段名 / 类型 / 语义 / 时间格式 / 枚举取值
- 允许：新增 optional 字段（带默认值）/ meta 逃生舱
- 反例（禁止）：改名 / 改类型 / 删枚举

### Frozen Level 2 · Interface Contract（接口契约 —— 冻结）
- 核心原则：实现可变化，接口不能随意变
- 冻结：方法名 / 输入输出类型 / 异常语义（用 bool 还是抛异常）
- 不冻结：接口背后的实现（可替换）

### Frozen Level 3 · Runtime Assembly Contract（运行时装配 —— 冻结）
- 装配入口语义不变；Source → Pipeline → Consumer 三段解耦

### Contract Test（攻击性测试）
（列出攻击输入与期望边界行为，见 03-Test-Patterns/contract-test）

### 版本策略
（SemVer 映射到三级冻结：破坏 L1/L2 = MAJOR；新增可选字段 = MINOR；实现变化 = PATCH）

## 冻结前置条件（打 RC 前必须清零）
（列出当前代码与契约不一致之处，先修复再宣称冻结）

## 后果（Consequences）
- 正面：变更有红线，Contract Test 守边界
- 负面：维护成本，演进需走版本流程（刻意摩擦）

## 替代方案（Alternatives）
- 简单二元冻结：否决（太粗）
- 不冻结靠自觉：否决（正是要防的腐化）
```

## 银龄盾实例（参照）

- ADR-0014 三级冻结治理（真实版本见仓库 `docs/ADR/0014-freeze-governance-three-levels.md`）。
- 关键创新：**诚实前置条件**——先把代码与契约不一致处清零，再宣称冻结，否则「冻结的是两个互相冲突的定义」。
- 关联模式：[../../03-Test-Patterns/contract-test](../03-Test-Patterns/contract-test/pattern.md) · [../../07-Backend-Patterns/event-contract](../07-Backend-Patterns/event-contract/pattern.md)

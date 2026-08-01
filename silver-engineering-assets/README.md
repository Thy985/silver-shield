# Silver Shield Engineering Asset Library v1.0

> 从一次 **Silver Shield（银龄盾）** 项目经验，提炼出的**可复制 AI 系统工程能力库**。
>
> **开发手册沉淀「认知」，工程资产沉淀「模式」，代码仓库沉淀「实现」。**
> 本库是第 2 类——把已被验证正确的工程范式，抽成可套用到任何「感知 → 判断 → 行动」类 AI 系统的资产。

---

## 这是什么，不是什么

- ✅ **是**：可复制能力（架构模式 / 代码模式 / 测试模式 / 调试模式 / Demo 工程 / 失败案例 / ADR 模板）。
- ❌ **不是**：项目归档（`代码 + README + 文档`）。归档只证明「做过」，资产库证明「能复用」。
- ❌ **不是**：银龄盾代码本身。本库刻意去专有名词、抽取**模式**，新项目直接抄结构而非复制业务代码。

---

## 目录（10 类资产）

```
silver-engineering-assets/
├── 00-Overview/             总览：README / Asset-Map / SilverShield-Lessons
├── 01-Architecture-Patterns/ 架构模式（AI 流水线分层）
├── 02-Code-Patterns/        代码模式（生命周期 / 状态聚合）
├── 03-Test-Patterns/        测试模式（单测 / 契约 / 集成 / E2E）
├── 04-Debug-Patterns/       调试模式（数据流分层定位）
├── 05-Demo-Engineering/     Demo 工程（生命周期 / 快照 / 场景）
├── 06-Frontend-Patterns/    前端模式（多角色投影 / 状态驱动）
├── 07-Backend-Patterns/     后端模式（事件契约 / 路由）
├── 08-ADR-Templates/        ADR 模板（运行时生命周期 / 冻结 / 状态分离 / 事件契约）
└── 09-Failure-Cases/       失败案例库（最高价值）
```

统一索引见 [**HANDBOOK.md**](HANDBOOK.md)。

---

## 推荐沉淀顺序（按价值）

### 第一阶段（最高价值，本库已重点建设）
1. E2E 验证模式（03-Test-Patterns/e2e-test）
2. 生命周期管理（02-Code-Patterns/lifecycle-management）
3. 状态驱动架构（02-Code-Patterns/cross-frame-state-aggregation · 06-Frontend-Patterns/state-driven-dashboard）
4. 冻结边界 + 契约测试（08-ADR-Templates/freeze-boundary · 03-Test-Patterns/contract-test）
5. Debug 流程（04-Debug-Patterns/runtime-data-flow-debug）

### 第二阶段
6. 多角色 Dashboard（06-Frontend-Patterns/multi-role-projection）
7. Demo 工程（05-Demo-Engineering）
8. 事件系统设计（07-Backend-Patterns/event-contract）

### 第三阶段
9. AI Pipeline 模板（01-Architecture-Patterns）
10. 多模态扩展模板
11. Agent Runtime 模板

---

## 如何使用（把银龄盾当母项目）

1. 开新项目，从本库复制对应模式文件夹到新仓库。
2. 改领域名词即可套（见 01 的映射表）。
3. 不复制业务代码——只取模式骨架 + example。
4. 每踩一个新坑，回写 `09-Failure-Cases/`；每验证一个新范式，补进对应类别。母项目越用越厚。

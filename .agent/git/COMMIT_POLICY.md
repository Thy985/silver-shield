# Commit Policy

> Commit 规范与格式管理。
> 继承自 `REPOSITORY_GOVERNANCE.md`。

---

## 1. 核心原则

### 1.1 Commit 是恢复单位

每个 commit 应满足：

- **单一目的**：一个 commit 做一件事
- **可理解**：通过 message 能理解变更内容
- **可回滚**：能独立回滚到之前状态

### 1.2 Commit 粒度

**推荐**：小步提交，每完成一个逻辑单元提交一次

**禁止**：
- 把半成品代码提交
- 把无关改动混入同一 commit
- 累积大量修改后一次性提交

---

## 2. Commit 格式

### 2.1 标准格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 2.2 Type 类型

| Type | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | 接入 YOLO 检测 |
| `fix` | Bug 修复 | 修复断流重连 |
| `refactor` | 重构 | 提取公共方法 |
| `test` | 测试 | 添加单元测试 |
| `docs` | 文档 | 更新 API 文档 |
| `chore` | 工程维护 | 更新依赖 |
| `perf` | 性能优化 | 优化推理速度 |
| `style` | 代码格式 | 格式化代码 |
| `ci` | CI 配置 | 添加 GitHub Actions |
| `build` | 构建系统 | 更新构建配置 |

### 2.3 Scope 范围（本项目）

| Scope | 对应模块 |
|-------|----------|
| `ingestion` | `src/home_perception/ingestion/` |
| `detection` | `src/home_perception/detection/` |
| `analysis` | `src/home_perception/analysis/` |
| `evidence` | `src/home_perception/evidence/` |
| `output` | `src/home_perception/output/` |
| `core` | `src/home_perception/core/` |
| `memory` | `src/home_perception/memory/` |
| `config` | `config/` |
| `deps` | 依赖配置 |
| `ci` | CI 配置 |
| `docs` | 文档 |

### 2.4 Subject 规范

- **中文**，祈使句
- ≤ 50 字符
- 不加句号
- 描述做了什么，不描述怎么做的

**正确**：
```
feat(memory): 实现 InMemoryStore 存储后端
fix(analysis): 修复风险评估空指针异常
```

**错误**：
```
feat: 添加了新功能
update: 修改了代码
fix bug: 修复问题
```

### 2.5 Body 规范

解释 **what** + **why**，不解释 how。

每行 ≤ 72 字符。

**必须包含**：
```
Task scope: <ROADMAP P0-x | issue #n | governance>
```

### 2.6 Footer 规范

```
Closes #n       - 关闭 Issue
Refs #n         - 关联 Issue
ROADMAP P0-x    - 关联任务
BREAKING CHANGE - 破坏性变更
```

---

## 3. Commit 示例

### 3.1 功能开发

```
feat(memory): 实现 Episodic Storage 存储后端

新增 InMemoryStore 实现，支持：
- I1 幂等：record_id 去重
- I2 单调：字段变化检测
- I4 查询：按 visitor 检索

Task scope: ROADMAP P0-11 / ADR-0024 Slice 5
```

### 3.2 Bug 修复

```
fix(realtime): 修复断流重连后帧序号重置

断流重连后帧缓冲未清空，导致序号连续递增。
已在 _open 成功后重置缓冲。

Task scope: ROADMAP P0-2
```

### 3.3 重构

```
refactor(analysis): 提取 RiskEvaluator 状态机

将风险评估状态机从 Pipeline 提取为独立类，
提高可测试性和可复用性。

Task scope: refactor
```

### 3.4 测试

```
test(memory): 添加 InMemoryStore 单元测试

覆盖：
- upsert_episodic 幂等性
- I2 字段变化检测
- get_episodic_by_visitor 查询
- get_active_episodic 过滤

Task scope: ROADMAP P0-11 / ADR-0024 Slice 5
```

---

## 4. Commit 规范检查

### 4.1 提交前自检

- [ ] 格式符合 `<type>(<scope>): <subject>`
- [ ] Subject ≤ 50 字符，不加句号
- [ ] Body 解释 what + why
- [ ] 包含 `Task scope:`
- [ ] 无敏感信息（密钥/token/凭证）
- [ ] `.gitignore` 已覆盖新增产物

### 4.2 禁止的 Commit

```
update / modify / fix bug / changes / wip / misc
```

**原因**：无法从历史中理解变更内容。

---

## 5. Commit 修改

### 5.1 未 Push 的 Commit

```bash
git commit --amend
git rebase -i HEAD~N
```

### 5.2 已 Push 的 Commit

**必须使用 force-with-lease**：

```bash
git commit --amend
git push --force-with-lease origin <branch>
```

---

## 6. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-29 | 初始版本 |

# PR Policy

> Pull Request 创建与合并流程。
> 继承自 `REPOSITORY_GOVERNANCE.md`。

---

## 1. 核心原则

### 1.1 所有变更必须通过 PR

**禁止**：
- 直接 push 到 main
- 绕过 PR 合并代码
- 在 main 分支直接开发

### 1.2 PR 生命周期

```
创建 -> 自检 -> Review -> CI -> 合并 -> 清理
```

---

## 2. PR 创建

### 2.1 创建前检查

- [ ] 从 main 或最新代码创建分支
- [ ] 变更范围明确，无无关改动
- [ ] Commit 符合规范
- [ ] 测试覆盖完整
- [ ] 文档已同步

### 2.2 PR 信息要求

```
标题：<type>(<scope>): <简短描述>

正文：
- 变更内容（what）
- 变更原因（why）
- 测试验证（how to verify）
- Task scope: <ROADMAP | issue #>
```

---

## 3. PR 自检清单

### 3.1 Code Check

- [ ] `ruff check src tests` 无 error
- [ ] `pytest tests/ -q` 全部通过
- [ ] 代码格式正确
- [ ] 类型检查通过

### 3.2 Repository Check

- [ ] 分支名称符合规范
- [ ] Commit 符合规范
- [ ] Diff 范围合理
- [ ] 无敏感文件
- [ ] `.gitignore` 覆盖新增产物

### 3.3 Architecture Check

涉及以下领域时，**必须创建 ADR**：

- 数据模型变更
- 存储架构变更
- API 契约变更
- 核心架构决策

---

## 4. Code Review

### 4.1 Review 要求

| 分支类型 | Review 要求 |
|----------|------------|
| main | 必须 1 人 review |
| feature | 建议 review |
| hotfix | 必须 review |

### 4.2 Review 检查点

- 功能正确性
- 代码质量
- 测试覆盖
- 文档同步

---

## 5. CI 检查

### 5.1 必须通过的检查

| 检查 | 说明 |
|------|------|
| Lint | `ruff check` 无 error |
| Format | `ruff format` 通过 |
| Test | `pytest` 全部通过 |
| Build | 无构建错误 |

### 5.2 禁止的行为

```
禁止忽略 CI 失败 / 跳过检查合并 / 使用 "skip CI" 注释绕过
```

---

## 6. PR 合并

### 6.1 合并策略

| 策略 | 适用场景 |
|------|----------|
| Squash and merge | Feature 分支（推荐） |
| Rebase and merge | 大重构、需要保留历史 |

### 6.2 合并条件

**必须满足**：
- CI 全部通过
- Review 已批准
- 无冲突

### 6.3 合并后

- [ ] 删除 feature 分支
- [ ] 更新相关文档
- [ ] 通知相关人员

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-29 | 初始版本 |

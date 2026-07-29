# Branch Policy

> 分支命名规范与生命周期管理。
> 继承自 `REPOSITORY_GOVERNANCE.md`。

---

## 1. 核心原则

### 1.1 分支用途

分支是**隔离变化的工具**，不是项目管理工具。

**禁止**：
- 用分支代替 ROADMAP/ADR
- 用分支代替 CHANGELOG
- 长期维护阶段分支

### 1.2 分支生命周期

```
创建 → 开发 → 测试 → PR → 合并 → 删除
```

**硬性要求**：合并后必须删除分支

---

## 2. 分支类型

### 2.1 允许的分支类型

| 类型 | 格式 | 用途 | 生命周期 |
|------|------|------|----------|
| 功能 | `feat/<name>` | 新功能开发 | < 1 周 |
| 修复 | `fix/<name>` | Bug 修复 | < 3 天 |
| 重构 | `refactor/<name>` | 代码重构 | < 1 周 |
| 实验 | `experiment/<name>` | 探索性开发 | 明确截止 |
| 工程 | `chore/<name>` | 工程维护 | < 1 天 |
| 文档 | `docs/<name>` | 文档更新 | < 1 天 |
| 测试 | `test/<name>` | 测试用例 | < 1 天 |
| 发布 | `release/<version>` | 发布准备 | 按需 |

### 2.2 命名规范

**格式**：`type/scope-name`

**规则**：
- 全小写
- 单词用 `-` 连接
- 不含 issue 编号
- 长度 ≤ 40 字符

**示例**：
```
feat/memory-episodic-storage
fix/realtime-risk-evaluator
chore/add-precommit-hook
docs/update-api-contract
experiment/new-detection-model
```

### 2.3 禁止的分支类型

```
❌ phase-1
❌ phase-2
❌ phase-final
❌ backup-main
❌ temp-test
❌ old-version
❌ development（用 feat/ 代替）
❌ feature/<name>（用 feat/ 代替）
```

**原因**：这些是项目阶段，不是代码隔离需求。

---

## 3. 主分支

### 3.1 main 分支

**要求**：
- 永远保持可运行
- CI 必须通过
- 禁止直接 push
- 禁止 force push

**保护规则**：
```yaml
main:
  require_pr: true
  require_ci: true
  require_review: 1
  force_push: false
```

### 3.2 发布分支（按需）

```
release/v0.1.0
release/v0.2.0
```

用于发布准备，发布完成后可归档或删除。

---

## 4. Feature 分支管理

### 4.1 创建分支

```bash
# 从 main 创建
git checkout main
git pull origin main
git checkout -b feat/<name>
```

### 4.2 同步 main

```bash
# 方法 1：rebase（推荐）
git fetch origin main
git rebase origin/main

# 方法 2：merge（保留历史）
git fetch origin main
git merge origin/main
```

### 4.3 删除分支

```bash
# 合并后删除本地
git branch -d <branch-name>

# 合并后删除远程
git push origin --delete <branch-name>
```

---

## 5. 特殊分支场景

### 5.1 Hotfix

```bash
# 从 main 创建
git checkout main
git checkout -b hotfix/<name>

# 修复后 PR 到 main
# 标记为 bugfix
```

### 5.2 Experiment

实验分支必须有明确截止：

```bash
# 创建时注明
git checkout -b experiment/<name>

# 实验结束
# - 合并到功能分支
# - 或废弃（删除）
# - 或保留（明确标记为废弃）
```

### 5.3 Stale 分支清理

**超过 30 天未更新的分支**视为 stale：

```bash
# 列出 stale 分支
git branch -vv | grep ': gone]'

# 删除本地 stale 分支
git branch -vv | grep ': gone]' | awk '{print $1}' | xargs git branch -d
```

---

## 6. 相关文档

| 文档 | 内容 |
|------|------|
| `REPOSITORY_GOVERNANCE.md` | 核心治理原则 |
| `PR_POLICY.md` | PR 创建与合并流程 |
| `RECOVERY_POLICY.md` | 分支损坏恢复 |

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-29 | 初始版本 |

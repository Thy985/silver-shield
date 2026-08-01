# Repository Governance Policy

> 仓库治理核心策略手册。
> 定位：**防止 AI 和快速迭代导致 Git 仓库状态失控**。
> 生效日期：2026-07-29

---

## 1. 核心原则

### 1.1 Git 是系统状态管理工具，不只是代码备份工具

仓库状态包括：

- 源代码
- Commit 历史
- Branch 引用
- Tag
- CI 配置
- Release 状态
- Dependency 状态

**任何修改 Git 状态的操作，都属于系统变更。**

### 1.2 单一可信主线优先

```
main ─ 永远保持可运行、CI 通过
  │
  ├── feat/<name> ─ 功能分支（短期存在）
  ├── fix/<name>  ─ 修复分支（短期存在）
  └── experiment/<name> ─ 实验分支（明确生命周期）
```

**禁止**：长期维护 `phase-1`、`backup-main`、`temp-test` 等分支。

阶段信息通过 **ROADMAP / ADR / CHANGELOG / TAG** 管理。

### 1.3 所有变化必须可追踪、可恢复

```
Change → Record → Validate → Recover
```

**禁止**：

```
修改 → 未知状态 → 无法恢复
```

### 1.4 分工原则

| 角色 | 权限 |
|------|------|
| Human Owner | 合并 PR、修改 main ref、删除分支、执行 force push |
| AI Agent | 创建分支、提交 commit、创建 PR、push feature 分支 |

**AI 禁止**：直推 main、执行 force push、修改 main ref

---

## 2. 仓库健康检查（定期执行）

### 2.1 每周检查清单

```bash
# 1. 对象完整性
git fsck --full

# 2. 丢失的提交
git fsck --lost-found

# 3. 远程 ref 状态
git remote prune origin
git branch -r | head -20

# 4. 过期分支清理
git fetch --prune
```

### 2.2 Push 前检查

```bash
git status        # 确认分支正确
git branch -vv    # 确认 upstream
git log -3        # 确认提交内容
```

---

## 3. 仓库损坏处理流程

### 3.1 发现问题

```
missing commit / broken ref / invalid object
```

### 3.2 处理流程

```
Freeze ─ 停止 push
  ↓
Inspect ─ 定位问题
  ↓
Find valid history ─ 找有效提交
  ↓
Restore ref ─ 恢复引用
  ↓
Verify ─ 验证 CI
  ↓
Resume ─ 恢复开发
```

### 3.3 预防措施

- 重大修改前创建 tag/checkpoint
- 使用 `--force-with-lease` 而非 `--force`
- 定期 `git remote prune origin`

---

## 4. 分支保护规则

### 4.1 main 分支

| 规则 | 要求 |
|------|------|
| 直接 push | ❌ 禁止 |
| PR 要求 | ✅ 必须 |
| CI 检查 | ✅ 必须通过 |
| Review | ✅ 至少 1 人 |
| Force push | ❌ 禁止 |

### 4.2 Feature 分支生命周期

```
创建 → 开发 → 测试 → PR → 合并 → 删除
```

**禁止**：合并后长期保留 feature 分支

---

## 5. 相关文档

| 文档 | 内容 |
|------|------|
| `.agent/git/BRANCH_POLICY.md` | 分支命名与生命周期 |
| `.agent/git/COMMIT_POLICY.md` | Commit 规范与格式 |
| `.agent/git/PR_POLICY.md` | PR 创建与合并流程 |
| `.agent/git/RECOVERY_POLICY.md` | 仓库恢复与健康检查 |
| `.github/workflows/` | CI 配置 |

---

## 6. 核心原则总结

> **分支负责隔离变化，Commit 负责记录变化，ADR 负责解释变化，CI 负责验证变化，Tag 负责标记阶段。**

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-29 | 初始版本，吸取 ADR-0024 开发教训 |

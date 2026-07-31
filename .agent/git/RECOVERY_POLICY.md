# Recovery Policy

> 仓库恢复与健康检查策略。
> 继承自 `REPOSITORY_GOVERNANCE.md`。

---

## 1. 核心原则

### 1.1 预防优先

```
预防 -> 检测 -> 恢复 -> 验证
```

**预防措施**：
- 定期健康检查
- 重大修改前创建恢复点
- 使用安全的 Git 操作

### 1.2 恢复目标

任何损坏都应能在 **30 分钟内**恢复。

---

## 2. 定期健康检查

### 2.1 每周检查清单

```bash
# 1. 对象完整性检查
git fsck --full

# 2. 丢失的提交
git fsck --lost-found

# 3. 远程 ref 状态
git remote prune origin

# 4. 过期分支清理
git fetch --prune

# 5. 查看远程分支
git branch -r | head -20
```

### 2.2 检查频率

| 环境 | 频率 |
|------|------|
| 开发中 | 每周 |
| 生产 | 每天 |

---

## 3. 常见问题与解决方案

### 3.1 Remote Ref 损坏

**症状**：
```
fatal: bad object <sha>
```

**解决方案**：

```bash
# 1. 查看远程分支状态
git ls-remote origin

# 2. 找到有效 SHA
git rev-parse origin/main

# 3. 重写本地 ref
printf '<valid-sha>\n' > .git/refs/remotes/origin/main

# 4. 验证
git log origin/main --oneline -3
```

### 3.2 Missing Objects

**症状**：
```
missing <sha>
```

**解决方案**：

```bash
# 1. 查看丢失的对象
git fsck --lost-found

# 2. 尝试恢复
git reflog
git reset --hard HEAD@{n}

# 3. 如果无法恢复
# - 从远程重新 clone
# - 或使用 git clone --reference
```

### 3.3 Stale Remote Tracking

**症状**：
```
<remote-branch> - [gone]
```

**解决方案**：

```bash
# 1. 清理过期追踪分支
git remote prune origin

# 2. 删除本地已消失的追踪分支
git branch -vv | grep ': gone]' | awk '{print $1}' | xargs git branch -d
```

---

## 4. 重大修改前恢复点

### 4.1 创建恢复点

```bash
# 创建 tag 作为恢复点
git tag before-refactor-<name>
```

## 4. 分支清理规范

### 4.1 本地分支限制

**最多维护 2 个本地分支**：

```
main              # 主分支
feat/<current>    # 当前开发分支（可选）
```

**规则**：
- 完成任务后立即删除本地 feature 分支
- 不保留已合并的分支
- 不创建长期维护的功能分支

### 4.2 远程分支清理

**每周执行一次**：

```bash
# 1. 删除已合并的远程分支
git branch -r --merged origin/main | grep -v "main" | sed 's/origin\///' | xargs -I {} git push origin --delete {}

# 2. 清理远程追踪分支
git remote prune origin

# 3. 删除本地已合并的分支
git branch --merged main | grep -v "main" | xargs git branch -d
```

### 4.3 一次性分支

**定义**：用于单次任务，完成后不再使用

```
feat/<short-task>
fix/<specific-bug>
chore/<one-time-task>
```

**清理时机**：
- 合并后立即删除
- 放弃后立即删除
- 超过 7 天未合并的一律删除

### 4.4 恢复点场景

| 场景 | 恢复点 |
|------|--------|
| 大重构 | `before-refactor-<name>` |
| 依赖升级 | `before-deps-upgrade` |
| 架构变更 | `before-architecture` |

---

## 5. 仓库损坏处理流程

### 5.1 立即响应

```
Step 1: Freeze - 停止所有 push 操作
Step 2: Inspect - 运行 git fsck 检查
Step 3: Diagnose - 确定损坏类型
Step 4: Backup - 备份当前状态
Step 5: Fix - 应用对应修复方案
Step 6: Verify - 验证完整性
Step 7: Resume - 恢复开发
```

### 5.2 损坏类型分类

| 类型 | 严重度 | 恢复时间 | 方案 |
|------|--------|----------|------|
| Remote ref 损坏 | 低 | < 5 min | 重写 ref |
| Missing object | 中 | < 30 min | 重新 fetch / clone |
| Pack 损坏 | 高 | < 1 hour | 重新 pack / clone |
| 完整损坏 | 严重 | < 2 hour | 完整恢复 |

---

## 6. Force Push 规范

### 6.1 安全使用

**必须使用** `--force-with-lease`：

```bash
git push --force-with-lease origin <branch>
```

**禁止使用** `--force`

### 6.2 Force Push 场景

| 场景 | 允许 | 条件 |
|------|------|------|
| Rebase 分支 | Yes | 已通知协作者 |
| 清理 commit | Yes | 在自己的 feature 分支 |
| 修复损坏 | Yes | 必须，有完整备份 |
| main 分支 | No | 禁止 |

---

## 7. AI Agent 操作约束

### 7.1 低风险操作（可自动执行）

```
git status / git diff / git log / git branch
git add / git commit / git checkout -b
```

### 7.2 中风险操作（需确认）

```
git merge / git rebase / git cherry-pick
```

### 7.3 高风险操作（禁止自动执行）

```
git push --force / git reset --hard
git branch -D / git push origin --delete
git push origin main / 修改 main ref
```

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-29 | 初始版本，吸取远程 ref 损坏教训 |

#!/usr/bin/env bash
# ============================================================
# git-safety-guard.sh
# ------------------------------------------------------------
# 在 WorkBuddy 沙箱中包裹 `git`，拦截会触发「沙箱 FS 重定向地雷」的操作。
#
# 背景：本沙箱里 `git gc` / `git repack` / `git clone` / `git prune` 会把数据
# 写入一个 git 独占、ls/cp/python 都看不到的隐藏文件系统，从而静默损坏工作
# 仓库（这正是 2026-07-30 silver-shield 对象库损坏的根因）。其余 git 命令
# 原样透传。
#
# 用法（二选一）：
#   1) alias git=/path/to/scripts/git-safety-guard.sh
#   2) 直接调用：./scripts/git-safety-guard.sh status
#
# 安全命令（透传）：add / commit(走 plumbing) / push / fetch / pull /
#   status / diff / log / show / branch(只读) / checkout(仅文件) 等。
# ============================================================
set -euo pipefail

# 提取第一个非选项参数作为 git 子命令（-C <dir> / --verbose 等跳过）
subcmd=""
for arg in "$@"; do
  case "$arg" in
    -*) continue ;;
    *)  subcmd="$arg"; break ;;
  esac
done

# 会写入沙箱隐藏 FS、从而损坏仓库的操作
case "$subcmd" in
  gc|repack|clone|prune|fast-export|bundle|pack-objects)
    echo "🚫 BLOCKED by git-safety-guard: 'git $subcmd' 在本沙箱中被禁止。" >&2
    echo "   原因：触发 FS 重定向，会静默损坏本地仓库。" >&2
    echo "   替代：使用 fetch / push / pull / commit(plumbing) / status / diff。" >&2
    echo "   若必须执行，请在沙箱外的真机上进行。" >&2
    exit 1
    ;;
esac

exec git "$@"

#!/usr/bin/env bash
# 启用本地 git hooks（pre-push 守门）。
# 每个 clone / worktree 运行一次：git config core.hooksPath .githooks。
# 之后每次 git push 自动运行 .githooks/pre-push（调用 scripts/preflight.sh）。
#
# 用法：bash scripts/setup-hooks.sh
#
# 紧急绕过：SKIP_PREFLIGHT=1 git push（仅限本地确实无法跑测试的场景，如离线）。

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" config core.hooksPath .githooks
echo "[setup-hooks] core.hooksPath = .githooks 已设置"
echo "[setup-hooks] 下次 git push 将自动运行 pre-push 守门（scripts/preflight.sh）"
echo "[setup-hooks] 紧急绕过：SKIP_PREFLIGHT=1 git push"

#!/usr/bin/env bash
# preflight.sh —— push 前本地复刻 CI 守门（与 .github/workflows/ci.yml 的 lint + test-contracts 一致）
#
# 用途：把事故挡在 push 之前，杜绝「到 CI 才发现」的 ruff 警告 / 契约测试回归。
# 由 .githooks/pre-push 在每次 git push 前自动调用。
#
# 用法：
#   bash scripts/preflight.sh          # ruff + torch-free 契约/仪表盘测试（与 CI test-contracts 一致）
#   bash scripts/preflight.sh --quick  # 仅 ruff（秒级）
#
# 注：
# 1. 完整运行时测试（需 torch/ultralytics/opencv 的 AI 栈）不在本脚本范围——
#    由 CI 的 test-runtime job 在 main 合入时运行。本地守门只覆盖快速、无外部依赖的子集。
# 2. ruff / pytest 需在 PATH 中（CI 用 ruff==0.15.22，本地建议版本一致）。
# 3. 不要用 `ruff check | tail` 吞退出码——本脚本直接取 $?。

set -u
cd "$(dirname "$0")/.."   # -> repo root

FAIL=0

echo "==================================================="
echo "[1/2] ruff check src tests"
echo "==================================================="
ruff check src tests
RUFF_EXIT=$?
if [ $RUFF_EXIT -ne 0 ]; then
  echo ""
  echo "❌ RUFF FAILED (exit=$RUFF_EXIT) —— CI 的 Lint job 会挂，请先修复上面的 warning/error"
  FAIL=1
else
  echo "✅ ruff passed"
fi

if [ "${1:-}" = "--quick" ]; then
  if [ $FAIL -eq 0 ]; then
    echo "🟢 PREFLIGHT PASSED (quick) —— 可以 push"
  else
    echo "🔴 PREFLIGHT FAILED —— 不要 push，先修复"
  fi
  exit $FAIL
fi

echo ""
echo "==================================================="
echo "[2/2] pytest（torch-free 契约/仪表盘子集，与 CI test-contracts 一致）"
echo "==================================================="
pytest \
  tests/contract/test_config_contract.py \
  tests/contract/test_state_machine_contract.py \
  tests/contract/test_schema_contract.py \
  tests/demo/test_dashboard_p0_11_4.py \
  tests/demo/test_dashboard_state_layer.py \
  tests/demo/test_dashboard_video_input.py \
  tests/demo/test_freeze_boundary.py
PYTEST_EXIT=$?
if [ $PYTEST_EXIT -ne 0 ]; then
  echo ""
  echo "❌ TEST FAILED (exit=$PYTEST_EXIT)"
  FAIL=1
else
  echo "✅ tests passed"
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "🟢 PREFLIGHT PASSED —— 可以 push"
else
  echo "🔴 PREFLIGHT FAILED —— 不要 push，先修复"
fi
exit $FAIL

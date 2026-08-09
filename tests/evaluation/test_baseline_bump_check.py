"""ADR-0033 Phase 3 基线 bump 治理测试（D7 + §6 基线治理）。

纯函数（requires_bump_marker / has_bump_marker / check_bump_policy）做变异验证；CLI 经
subprocess 跑真实 ``scripts/check_baseline_bump.py`` 验证退出码（0=合规 / 1=基线变更缺标记）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
import check_baseline_bump as bump

_BASELINES = "src/home_perception/evaluation/fixtures/baselines"
_BASELINE_FILE = f"{_BASELINES}/adr0033-phase1.json"
_MARKER = "benchmark-baseline-bump"


# ============================================================================
# 纯函数：路径判定
# ============================================================================
def test_requires_bump_marker_detects_baseline_paths():
    assert bump.requires_bump_marker([_BASELINE_FILE]) is True
    # 子目录下的基线文件也算
    assert bump.requires_bump_marker([f"{_BASELINES}/sub/x.json"]) is True
    # 恰好等于目录本身也算（极端情形）
    assert bump.requires_bump_marker([_BASELINES]) is True
    # 反例：其他路径不算
    assert bump.requires_bump_marker(["src/foo/other.py", "tests/x.py"]) is False
    # 反例：仅前缀相似但不同目录（如 baselines2）不算（防误判）
    assert bump.requires_bump_marker(["src/home_perception/evaluation/fixtures/baselines2/x.json"]) is False
    # 反例：删除基线（路径仍落目录内）同样危险 → 算
    assert bump.requires_bump_marker([f"{_BASELINES}/adr0033-phase1.json"]) is True


def test_requires_bump_marker_windows_separator():
    # 跨平台路径分隔符归一化
    assert bump.requires_bump_marker([_BASELINES.replace("/", "\\") + "\\x.json"]) is True


def test_has_bump_marker_case_insensitive():
    assert bump.has_bump_marker("we bumped baseline: BENCHMARK-BASELINE-BUMP") is True
    assert bump.has_bump_marker("benchmark-baseline-bump") is True
    assert bump.has_bump_marker("no marker here") is False
    assert bump.has_bump_marker("") is False


# ============================================================================
# 纯函数：策略裁决（变异验证三态）
# ============================================================================
def test_check_bump_policy_three_states():
    # 1) 无基线变更 → 合规
    ok, _ = bump.check_bump_policy(["src/foo.py"], "anything")
    assert ok is True
    # 2) 有基线变更 + 标记 → 合规
    ok, _ = bump.check_bump_policy([_BASELINE_FILE], _MARKER)
    assert ok is True
    # 3) 有基线变更 + 缺标记 → 拦截（CI 非零退出）
    ok, hint = bump.check_bump_policy([_BASELINE_FILE], "no marker")
    assert ok is False
    assert _MARKER in hint


# ============================================================================
# CLI：退出码（真实脚本）
# ============================================================================
def _run_cli(changed_files, marker_text):
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts/check_baseline_bump.py"),
         "--changed-files", *changed_files, "--marker-text", marker_text],
        capture_output=True, text=True, check=False,
    )


def test_cli_no_baseline_change_exits_0():
    r = _run_cli(["src/foo/other.py"], "")
    assert r.returncode == 0, r.stderr + r.stdout


def test_cli_baseline_changed_with_marker_exits_0():
    r = _run_cli([_BASELINE_FILE], f"bump baseline {_MARKER}")
    assert r.returncode == 0, r.stderr + r.stdout


def test_cli_baseline_changed_without_marker_exits_1():
    r = _run_cli([_BASELINE_FILE], "just a normal refactor")
    assert r.returncode == 1, r.stderr + r.stdout

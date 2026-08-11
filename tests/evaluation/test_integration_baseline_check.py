"""ADR-0034 Phase C · loop 指纹基线漂移治理单测（DoD C4）。

对齐 ADR-0033 ``tests/evaluation/test_baseline_bump_check.py`` 惯例：
只测 ``check_integration_baseline.py`` 的**纯函数**（不依赖 git / 网络），CLI 包装
由 CI job 集成验证。

关键断言（变异验证）：
- 无漂移 → 通过；漂移 + 无标记 → 拦截；漂移 + 标记 → 通过；无基线 → 通过；
- 基线文件变更（增/删/改）缺标记 → 拦截（防绕过漂移判定）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

from check_integration_baseline import (  # sys.path 注入后导入 scripts 模块
    BASELINE_FILENAME,
    BUMP_MARKER,
    baselines_changed,
    check_baseline_file_policy,
    check_drift_policy,
    drift_details,
    load_fingerprints,
)

_MARKER = BUMP_MARKER


def _fp(expectation: str = "E" * 64, loop: str = "L" * 64) -> dict[str, str]:
    return {"expectation_fingerprint": expectation, "loop_fingerprint": loop}


def _current() -> dict[str, dict[str, str]]:
    return {"scn_a": _fp(), "scn_b": _fp(expectation="X" * 64)}


# ---------------------------------------------------------------------------
# load_fingerprints
# ---------------------------------------------------------------------------


def test_load_fingerprints_missing_file():
    with pytest.raises(FileNotFoundError):
        load_fingerprints("/nonexistent/fingerprints.json")


def test_load_fingerprints_invalid_structure(tmp_path):
    p = tmp_path / "fp.json"
    p.write_text('{"scenarios": [1, 2]}', encoding="utf-8")
    with pytest.raises(TypeError, match="scenarios"):
        load_fingerprints(p)


def test_load_fingerprints_roundtrip(tmp_path):
    p = tmp_path / "fp.json"
    p.write_text(
        '{"scenarios": {"scn_a": {"expectation_fingerprint": "E", "loop_fingerprint": "L"}}}',
        encoding="utf-8",
    )
    assert load_fingerprints(p) == {"scn_a": {"expectation_fingerprint": "E", "loop_fingerprint": "L"}}


# ---------------------------------------------------------------------------
# drift_details / check_drift_policy
# ---------------------------------------------------------------------------


def test_drift_details_identical_is_empty():
    cur = _current()
    assert drift_details(cur, dict(cur)) == {}


def test_drift_details_detects_changes_and_additions():
    cur = _current()
    base = dict(cur)
    base["scn_a"] = _fp(loop="0" * 64)  # 改了 loop_fp
    base.pop("scn_b")  # 场景消失
    cur["scn_c"] = _fp()  # 新增场景
    details = drift_details(cur, base)
    assert details["scn_a"] == ["loop_fingerprint"]
    assert "scn_c" in details
    assert "scn_b" in details


def test_check_drift_no_drift_passes():
    cur = _current()
    ok, hint = check_drift_policy(cur, dict(cur), "")
    assert ok is True and "无漂移" in hint


def test_check_drift_without_marker_fails():
    cur = _current()
    base = dict(cur)
    base["scn_a"] = _fp(loop="0" * 64)
    ok, hint = check_drift_policy(cur, base, "")
    assert ok is False
    assert "Fingerprint drift without baseline update" in hint
    assert "scn_a" in hint


def test_check_drift_with_marker_passes():
    cur = _current()
    base = dict(cur)
    base["scn_a"] = _fp(loop="0" * 64)
    ok, hint = check_drift_policy(cur, base, f"fix: {_MARKER}")
    assert ok is True and _MARKER in hint


def test_check_drift_no_baseline_passes_first_run():
    ok, hint = check_drift_policy(_current(), None, "")
    assert ok is True and "首次运行无基线" in hint


# ---------------------------------------------------------------------------
# baselines_changed / check_baseline_file_policy（防绕过）
# ---------------------------------------------------------------------------


def test_baselines_changed_prefix_match():
    assert baselines_changed(["src/home_perception/integration/fixtures/baselines/loop_fingerprints.json"]) is True
    assert baselines_changed(["scripts/run_integration_validation.py"]) is False


def test_check_baseline_file_policy_requires_marker():
    changed = ["src/home_perception/integration/fixtures/baselines/loop_fingerprints.json"]
    ok, _ = check_baseline_file_policy(changed, "")
    assert ok is False
    ok, _ = check_baseline_file_policy(changed, f"bump {_MARKER}")
    assert ok is True
    ok, _ = check_baseline_file_policy(["scripts/other.py"], "")
    assert ok is True


def test_marker_value_constant():
    """标记常量不得漂移（CI job 与脚本同源消费）。"""
    assert _MARKER == "integration-baseline-bump"
    assert BASELINE_FILENAME == "loop_fingerprints.json"

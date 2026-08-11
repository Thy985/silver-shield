"""ADR-0034 Phase C · loop 指纹基线漂移治理单测（DoD C4）。

对齐 ADR-0033 ``tests/evaluation/test_baseline_bump_check.py`` 惯例：
纯函数 + CLI 包装两层都测（评审 D1/D2：CLI 主流程与 ``_write_baseline``
不再由 CI 集成隐式兜底）。

关键断言（变异验证）：
- 无漂移 → 通过；漂移 + 无标记 → 拦截；漂移 + 标记 → 通过；无基线 → 通过；
- 基线文件变更（增/删/改）缺标记 → 拦截（防绕过漂移判定）；
- CLI：exit 2（输入错误）、--init-baseline、--marker-text、git 不可用显式 WARN。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import check_integration_baseline as mod  # sys.path 注入后导入 scripts 模块
from check_integration_baseline import (
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


# ============================================================================
# D2 · _write_baseline（首次生成基线）
# ============================================================================


def test_write_baseline_refuses_missing_parent(tmp_path):
    """父目录不存在 → ValueError（拒绝自动创建，防路径穿越）。"""
    target = tmp_path / "no" / "such" / "dir" / BASELINE_FILENAME
    with pytest.raises(ValueError, match="父目录不存在"):
        mod._write_baseline({"s": _fp()}, target)


def test_write_baseline_sorted_and_deterministic(tmp_path):
    """乱序输入 → 落盘按 scenario_id 排序；两次写入字节一致（确定性）。"""
    target = tmp_path / "baselines" / BASELINE_FILENAME
    target.parent.mkdir()
    mod._write_baseline({"z": _fp(loop="Z"), "a": _fp(loop="A"), "m": _fp(loop="M")}, target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert list(data["scenarios"]) == ["a", "m", "z"]  # 排序
    assert data["generated_at"] == ""  # 易变时间不落盘（确定性）
    assert "note" in data

    mod._write_baseline({"z": _fp(loop="Z"), "a": _fp(loop="A"), "m": _fp(loop="M")}, target)
    assert target.read_bytes() == target.read_bytes()  # 幂等


# ============================================================================
# D1 · CLI 主流程（monkeypatch subprocess / _changed_files_since）
# ============================================================================


def test_cli_current_missing_exit_2(capsys):
    """--current 文件缺失 → 退出码 2（输入错误，fail-closed）。"""
    rc = mod.main(
        ["--current", "/nonexistent/fp.json", "--baseline", "/tmp/b.json"]
    )
    assert rc == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_init_baseline_writes_and_exits_0(tmp_path, capsys):
    """--init-baseline：生成基线文件 + 退出 0（跳过漂移判定）。"""
    cur = tmp_path / "current.json"
    cur.write_text(
        json.dumps({"scenarios": {"b": _fp(loop="B"), "a": _fp(loop="A")}}),
        encoding="utf-8",
    )
    base = tmp_path / "baselines" / BASELINE_FILENAME
    base.parent.mkdir()
    rc = mod.main(
        ["--current", str(cur), "--baseline", str(base), "--init-baseline"]
    )
    assert rc == 0
    assert base.exists()
    assert "已生成基线" in capsys.readouterr().out


def test_cli_init_baseline_missing_parent_exit_2(tmp_path):
    """--init-baseline 且基线父目录不存在 → 退出码 2。"""
    cur = tmp_path / "current.json"
    cur.write_text(json.dumps({"scenarios": {"a": _fp()}}), encoding="utf-8")
    rc = mod.main(
        [
            "--current",
            str(cur),
            "--baseline",
            str(tmp_path / "no" / "dir" / BASELINE_FILENAME),
            "--init-baseline",
        ]
    )
    assert rc == 2


def _write_pair(tmp_path, *, drift: bool) -> tuple[Path, Path]:
    """写 current + baseline 指纹对（drift=True 时 baseline 的 loop_fp 被篡改）。"""
    cur = tmp_path / "current.json"
    cur.write_text(
        json.dumps({"scenarios": {"s": _fp()}}), encoding="utf-8"
    )
    base = tmp_path / "baseline.json"
    base_fp = _fp(loop="0" * 64) if drift else _fp()
    base.write_text(json.dumps({"scenarios": {"s": base_fp}}), encoding="utf-8")
    return cur, base


def test_cli_drift_with_marker_text_passes(tmp_path, monkeypatch, capsys):
    """漂移 + --marker-text 含标记 → 退出 0（评审 D1：--marker-text 路径）。"""
    cur, base = _write_pair(tmp_path, drift=True)
    monkeypatch.setattr(mod, "_changed_files_since", lambda base_ref: [])
    rc = mod.main(
        [
            "--current", str(cur),
            "--baseline", str(base),
            "--marker-text", f"fix: {_MARKER}",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "[DRIFT]" in out and "OK" in out


def test_cli_drift_without_marker_fails(tmp_path, monkeypatch):
    """漂移 + 无标记 → 退出 1（拦截）。"""
    cur, base = _write_pair(tmp_path, drift=True)
    monkeypatch.setattr(mod, "_changed_files_since", lambda base_ref: [])
    rc = mod.main(["--current", str(cur), "--baseline", str(base)])
    assert rc == 1


def test_cli_git_unavailable_warns_but_drift_governs(tmp_path, monkeypatch, capsys):
    """git 不可用 → 显式 WARN（基线文件检测跳过），漂移判定仍是最终裁决（评审 A3/C1）。"""
    cur, base = _write_pair(tmp_path, drift=True)
    monkeypatch.setattr(mod, "_changed_files_since", lambda base_ref: None)
    rc = mod.main(
        [
            "--current", str(cur),
            "--baseline", str(base),
            "--marker-text", f"fix: {_MARKER}",
        ]
    )
    out = capsys.readouterr().out
    assert "WARN" in out and "git 不可用" in out
    # 漂移有标记 → 仍 PASS（git 不可用不误伤合法漂移）
    assert rc == 0


def test_cli_baseline_file_change_without_marker_fails(tmp_path, monkeypatch):
    """基线文件变更缺标记 → 退出 1（防绕过，评审 B2 前缀路径）。"""
    cur, base = _write_pair(tmp_path, drift=False)  # 无指纹漂移
    monkeypatch.setattr(
        mod,
        "_changed_files_since",
        lambda base_ref: [f"{mod.BASELINES_REL}/{BASELINE_FILENAME}"],
    )
    rc = mod.main(["--current", str(cur), "--baseline", str(base)])
    assert rc == 1


def test_cli_skip_file_policy_push_event(tmp_path, monkeypatch, capsys):
    """push 到 main 场景（--skip-file-policy）：文件治理跳过，漂移判定仍生效。

    复现 2026-08-11 线上事故：main push 时 git 检测到合并引入的基线文件变更 +
    marker 为空（无 PR number + git log 为空）→ 文件治理误拦。--skip-file-policy
    后：无漂移 → PASS（漂移判定仍是最终裁决）。
    """
    cur, base = _write_pair(tmp_path, drift=False)  # 指纹一致
    # 即使 git 会报告基线文件变更，--skip-file-policy 也不拦截
    monkeypatch.setattr(
        mod,
        "_changed_files_since",
        lambda base_ref: [f"{mod.BASELINES_REL}/{BASELINE_FILENAME}"],
    )
    rc = mod.main(
        ["--current", str(cur), "--baseline", str(base), "--skip-file-policy"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKIP" in out and "skip-file-policy" in out
    assert "[DRIFT]" in out and "OK" in out

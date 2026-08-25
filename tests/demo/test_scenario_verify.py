"""PR-A verify_scenarios 单元测试（torch-free · 纯逻辑校验）。

锁定 3 类合约：
  1. ``_verify_expected`` 校验逻辑（RAISED/WARN/MONITOR × 各种 warning 组合）；
  2. ``VerifyReport.all_passed`` 聚合属性；
  3. ``print_report`` 退出码（0=全过，1=有失败）。

不测 ``verify_all`` / ``_run_n_frames``（需完整 gateway 装配，属集成测试范畴）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_verify():
    """通过 sys.path + import 加载 scripts/verify_scenarios.py。"""
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import scenario_verify as vs

    return vs


# ---------------------------------------------------------------------------
# ① _verify_expected 校验逻辑
# ---------------------------------------------------------------------------


def _w(level: str, action: str = "MONITOR") -> SimpleNamespace:
    """构造 mock WarningEvent。"""
    return SimpleNamespace(risk_level=level, recommended_action=action)


def test_verify_expected_raised_with_high_passes():
    vs = _load_verify()
    warnings = [_w("HIGH", "ESCALATE_COMMUNITY"), _w("LOW", "MONITOR")]
    passed, detail = vs._verify_expected("RAISED", warnings)
    assert passed is True
    assert "HIGH" in detail


def test_verify_expected_raised_without_high_fails():
    vs = _load_verify()
    warnings = [_w("LOW", "NOTIFY_FAMILY"), _w("LOW", "MONITOR")]
    passed, detail = vs._verify_expected("RAISED", warnings)
    assert passed is False
    assert "RAISED" in detail


def test_verify_expected_raised_with_zero_warnings_fails():
    vs = _load_verify()
    passed, detail = vs._verify_expected("RAISED", [])
    assert passed is False
    assert "∅" in detail


def test_verify_expected_warn_with_low_only_passes():
    vs = _load_verify()
    warnings = [_w("LOW", "MONITOR"), _w("MEDIUM", "NOTIFY_FAMILY")]
    passed, detail = vs._verify_expected("WARN", warnings)
    assert passed is True
    assert "WARN" in detail


def test_verify_expected_warn_with_zero_warnings_fails():
    vs = _load_verify()
    passed, detail = vs._verify_expected("WARN", [])
    assert passed is False
    assert "0 warnings" in detail


def test_verify_expected_warn_with_high_fails():
    vs = _load_verify()
    warnings = [_w("HIGH", "ESCALATE_COMMUNITY"), _w("LOW", "MONITOR")]
    passed, detail = vs._verify_expected("WARN", warnings)
    assert passed is False
    assert "HIGH" in detail


def test_verify_expected_monitor_with_zero_warnings_passes():
    vs = _load_verify()
    passed, detail = vs._verify_expected("MONITOR", [])
    assert passed is True
    assert "克制" in detail


def test_verify_expected_monitor_with_warnings_fails():
    vs = _load_verify()
    warnings = [_w("LOW", "NOTIFY_FAMILY")]
    passed, detail = vs._verify_expected("MONITOR", warnings)
    assert passed is False
    assert "MONITOR" in detail


def test_verify_expected_unknown_label_fails():
    vs = _load_verify()
    passed, detail = vs._verify_expected("UNKNOWN", [])
    assert passed is False
    assert "未知" in detail


# ---------------------------------------------------------------------------
# ② VerifyReport.all_passed 聚合属性
# ---------------------------------------------------------------------------


def test_verify_report_all_passed_true():
    vs = _load_verify()
    r1 = vs.ScenarioVerifyResult(
        scenario_id="a", expected="MONITOR", actual_warnings=0,
        actual_levels=(), actual_actions=(), passed=True, elapsed_s=1.0,
    )
    r2 = vs.ScenarioVerifyResult(
        scenario_id="b", expected="RAISED", actual_warnings=1,
        actual_levels=("HIGH",), actual_actions=("ESCALATE_COMMUNITY",),
        passed=True, elapsed_s=2.0,
    )
    report = vs.VerifyReport(results=[r1, r2], assemble_s=3.0, total_s=6.0)
    assert report.all_passed is True


def test_verify_report_all_passed_false_when_any_failed():
    vs = _load_verify()
    r1 = vs.ScenarioVerifyResult(
        scenario_id="a", expected="MONITOR", actual_warnings=0,
        actual_levels=(), actual_actions=(), passed=True, elapsed_s=1.0,
    )
    r2 = vs.ScenarioVerifyResult(
        scenario_id="b", expected="RAISED", actual_warnings=0,
        actual_levels=(), actual_actions=(), passed=False, elapsed_s=2.0,
    )
    report = vs.VerifyReport(results=[r1, r2], assemble_s=3.0, total_s=6.0)
    assert report.all_passed is False


def test_verify_report_all_passed_true_when_empty():
    vs = _load_verify()
    report = vs.VerifyReport()
    assert report.all_passed is True


# ---------------------------------------------------------------------------
# ③ print_report 退出码
# ---------------------------------------------------------------------------


def test_print_report_returns_0_when_all_passed(capsys):
    vs = _load_verify()
    r = vs.ScenarioVerifyResult(
        scenario_id="telephone_risk", expected="RAISED", actual_warnings=1,
        actual_levels=("HIGH",), actual_actions=("ESCALATE_COMMUNITY",),
        passed=True, elapsed_s=45.0,
    )
    report = vs.VerifyReport(results=[r], assemble_s=5.0, total_s=50.0)
    exit_code = vs.print_report(report)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "全部达成" in out


def test_print_report_returns_1_when_any_failed(capsys):
    vs = _load_verify()
    r1 = vs.ScenarioVerifyResult(
        scenario_id="telephone_risk", expected="RAISED", actual_warnings=0,
        actual_levels=(), actual_actions=(), passed=False, elapsed_s=45.0,
        detail="期望 RAISED（HIGH）但最高 level=∅",
    )
    r2 = vs.ScenarioVerifyResult(
        scenario_id="delivery_courier_normal", expected="MONITOR",
        actual_warnings=0, actual_levels=(), actual_actions=(),
        passed=True, elapsed_s=22.0,
    )
    report = vs.VerifyReport(results=[r1, r2], assemble_s=5.0, total_s=67.0)
    exit_code = vs.print_report(report)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "未达成" in out
    assert "telephone_risk" in out
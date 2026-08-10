"""CI 集成测试入口（ci-runtime 使用，为 ADR-0034 真实闭环验证预留接入点）。

设计原则（对应本次 CI 治理「测试入口脚本 + Artifact + 真实闭环」要求）：
- YAML 只调本入口；测试发现 / 产物生成全在脚本内。
- 运行**完整运行时套件**（含需要 AI 栈 / 真实 YOLO 的测试，ci-test 的 torch-free
  分层不覆盖的部分），ci-runtime 仅在 main 合入或手动触发时运行，避免每 PR 强行装全栈。
- 产出三类证据（落 ``artifacts/``，CI 上传）：
    IntegrationReport.json  —— 结构化汇总（失败可下载溯源，对应「CI 必须有 Artifact」）
    junit.xml                —— JUnit 标准报告
    coverage.xml             —— 覆盖率

产物语义（非伪造）：
- ``IntegrationReport.json`` 由**真实 pytest 结果解析**而来（通过/失败用例、耗时、失败信息），
  不是手工构造的 ``result == expected``。满足「benchmark/集成不能是假跑」。
- ADR-0034 接入点：待 Scenario→Runtime→Memory→Decision→Notification 闭环管线就位后，
  本脚本可扩展 ``--mode scenario``，对每个 scenario 记录 ``decision`` / ``reason`` 等字段，
  形成用户示例中的 ``{scenario, decision, reason}`` 证据。当前先以 pytest-summary 形式落地，
  保证 CI 底座稳定、可追溯，ADR-0034 接进来即可直接产出真实验证证据。

用法：
    python scripts/run_integration.py                       # 全量运行时套件
    python scripts/run_integration.py -k test_pipeline     # 透传 pytest 选择
    python scripts/run_integration.py --artifacts-dir artifacts
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPORT_VERSION = "1.0"


def _default_artifacts_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "artifacts"


def _run_pytest(artifacts_dir: Path, extra: list[str]) -> int:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "-m",
        "pytest",
        f"--junitxml={artifacts_dir / 'junit.xml'}",
        "--cov=home_perception",
        f"--cov-report=xml:{artifacts_dir / 'coverage.xml'}",
        "--cov-report=term-missing",
        *extra,
    ]
    print(f"[run_integration] {' '.join(args)}", flush=True)
    proc = subprocess.run(args, check=False)
    return proc.returncode


def _parse_junit(junit_path: Path) -> dict:
    """从 pytest 生成的 JUnit XML 解析真实结果（非伪造）。"""
    if not junit_path.exists():
        return {"available": False, "reason": "junit xml 未生成（pytest 可能早期崩溃）"}
    tree = ET.parse(junit_path)
    root = tree.getroot()
    # pytest 可能把属性放在 <testsuite> 或嵌套 <testsuite> 下
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {"available": False, "reason": "junit xml 无 testsuite 节点"}

    def attr(name: str, default: int = 0) -> int:
        return int(suite.get(name, default))

    totals = {
        "tests": attr("tests"),
        "failures": attr("failures"),
        "errors": attr("errors"),
        "skipped": attr("skipped"),
        "time_sec": float(suite.get("time", 0.0)),
    }

    failed: list[dict] = []
    for case in suite.iter("testcase"):
        outcome = "passed"
        detail = None
        for child_tag in ("failure", "error", "skipped"):
            child = case.find(child_tag)
            if child is not None:
                outcome = child_tag
                detail = child.get("message") or child.text
                break
        if outcome in ("failure", "error"):
            failed.append(
                {
                    "nodeid": f"{case.get('classname', '')}::{case.get('name', '')}".strip(
                        ":"
                    ).strip(),
                    "outcome": outcome,
                    "message": (detail or "")[:2000],
                }
            )
    return {"available": True, "totals": totals, "failed_tests": failed}


def _build_report(pytest_rc: int, junit_path: Path) -> dict:
    parsed = _parse_junit(junit_path)
    passed = 0
    if parsed.get("available"):
        t = parsed["totals"]
        passed = t["tests"] - t["failures"] - t["errors"] - t["skipped"]
    report = {
        "report_version": REPORT_VERSION,
        "report_kind": "pytest-summary",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "python_version": platform.python_version(),
        "pytest_returncode": pytest_rc,
        "summary": {
            "total": parsed.get("totals", {}).get("tests", 0),
            "passed": passed,
            "failed": parsed.get("totals", {}).get("failures", 0)
            + parsed.get("totals", {}).get("errors", 0),
            "skipped": parsed.get("totals", {}).get("skipped", 0),
            "duration_sec": parsed.get("totals", {}).get("time_sec", 0.0),
        },
        "status": "passed" if pytest_rc == 0 else "failed",
        # ADR-0034 接入点：未来在此追加 scenarios: [{scenario, decision, reason, ...}]
        "scenarios": [],
    }
    if parsed.get("available"):
        report["failed_tests"] = parsed["failed_tests"]
    else:
        report["note"] = parsed.get("reason")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SilverShield CI 集成测试入口")
    parser.add_argument("--artifacts-dir", default=str(_default_artifacts_dir()))
    parser.add_argument(
        "--report-out",
        default=None,
        help="IntegrationReport.json 输出路径（默认 <artifacts-dir>/IntegrationReport.json）",
    )
    parsed, extra = parser.parse_known_args(argv)

    artifacts_dir = Path(parsed.artifacts_dir)
    rc = _run_pytest(artifacts_dir, extra)

    report = _build_report(rc, artifacts_dir / "junit.xml")
    report_out = (
        Path(parsed.report_out) if parsed.report_out else (artifacts_dir / "IntegrationReport.json")
    )
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"[run_integration] IntegrationReport -> {report_out} (status={report['status']})",
        flush=True,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

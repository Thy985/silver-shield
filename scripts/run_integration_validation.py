"""ADR-0034 Phase A · 闭环集成验证入口（手动 / 后续 CI 接入点）。

对 ``integration`` fixture 目录下的每个 scenario 跑完整闭环：

    Scenario → IntegrationRunner.run → IntegrationValidator.validate
             → IntegrationReport.build → write_canonical_report

产出物（落 ``artifacts/adr0034_integration/``，**刻意避开** CI 的
``IntegrationReport.json`` 同名异义产物）：

    <scenario_id>.canonical.json  —— 单场景确定性报告（canonical_dict，t1 比对用）
    adr0034_summary.json          —— 汇总（每场景 ok / failure_codes / markdown）

设计纪律（ADR-0034 Phase A MUST）：

- **零行为变化**：本脚本只调用已存在的 ``IntegrationRunner`` / ``IntegrationValidator`` /
  ``IntegrationReport``，不新增任何决策/感知行为；
- **不进自动门禁**：默认退出码恒 0，报告给人读、人工判断（与 ADR-0033 D8 同口径）。
  仅当显式 ``--strict`` 时才在任一场景失败时退出码 1——供本地预检，不接 CI 非零门禁；
- **确定性比对友好**：canonical 报告剔除 UUID / 时间戳（见 ``report.canonical_dict``），
  同 seed 两次运行逐字节一致（t1）。

依赖延迟导入：仅在 ``main`` 内 import 运行时 / 验证链，避免加载即拉起重链。

用法：
    python scripts/run_integration_validation.py
    python scripts/run_integration_validation.py --scenarios <dir> --out-dir <dir>
    python scripts/run_integration_validation.py --strict
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

# 默认 fixture 目录：ADR-0034 Phase A 自洽 fixtures（带 integration: 期望块）。
_DEFAULT_SCENARIOS = (
    Path(__file__).resolve().parent.parent
    / "src/home_perception/validation/fixtures/scenarios/integration"
)
# 输出目录：与 CI 的 artifacts/integration/（IntegrationReport.json）刻意分开。
_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "artifacts" / "adr0034_integration"


def _iter_scenario_paths(directory: Path) -> list[Path]:
    """目录内所有 *.yaml（按文件名排序，保证报告可复现）。"""
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.yaml"))


def _write_guarded(path: str | Path, payload: dict[str, object], *, who: str) -> None:
    """gate 结果落盘守卫（与 report 模块同口径：父目录存在 + 脱敏 fail-closed）。"""
    from home_perception.analysis.decision_sink import assert_desensitized

    p = Path(path).resolve()
    if not p.parent.exists():
        raise ValueError(
            f"{who} 父目录不存在，拒绝自动创建以防路径穿越：{p.parent}"
        )
    assert_desensitized(payload)
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0034 Phase A 闭环集成验证")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=_DEFAULT_SCENARIOS,
        help="含 integration fixture 的目录（默认 src/.../fixtures/scenarios/integration）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT,
        help="报告输出目录（默认 artifacts/adr0034_integration）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="任一场景验证失败时退出码 1（本地预检用，不接 CI 门禁）",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="跑 Phase C 门禁判定（severity 语义：blocking 拦 / warning 降级）并落盘 gate 报告；"
        "与 --strict 组合时退出码按 gate.passed（warning 降级不退出非零）",
    )
    args = parser.parse_args(argv)

    # 延迟导入：避免在 --help 或导入期就拉起运行时重链。
    from home_perception.integration.loop.context import IntegrationRunnerConfig
    from home_perception.integration.loop.gate import evaluate_integration_gate
    from home_perception.integration.loop.report import IntegrationReport
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)  # 调用方负责建目录；report 模块拒绝自建父目录

    paths = _iter_scenario_paths(args.scenarios)
    if not paths:
        logger.warning("no_scenarios_found", directory=str(args.scenarios))
        print(f"[WARN] 未找到任何 scenario：{args.scenarios}")
        return 0

    # Phase B.2 起跨模态是闭环标配能力：fixtures 里的 cross_modal 场景（如
    # sw_adr0034_cross_modal.yaml）声明了 F5 期望，默认关闭跨模态会让它恒 F5 失败
    # （t8：声明了期望却没注入 runtime = 必须暴露）。其余场景无 cross_modal 期望，
    # 启用后恒通过（validator 未声明期望即不校验），无副作用。
    runner = IntegrationRunner(
        config=IntegrationRunnerConfig(cross_modal_enabled=True)
    )
    validator = IntegrationValidator()

    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scenarios_dir": str(args.scenarios),
        "scenarios": [],
    }
    any_failed = False

    for path in paths:
        scn = load_scenario(path)
        result = runner.run(scn)
        validation = validator.validate(result, scn)
        report = IntegrationReport.build(result, validation)

        canonical_path = out_dir / f"{scn.meta.scenario_id}.canonical.json"
        report.write_canonical_report(canonical_path)  # 过双重守卫（脱敏 + 父目录存在）

        entry: dict[str, object] = {
            "scenario_id": scn.meta.scenario_id,
            "path": str(path),
            "ok": validation.ok,
            "failure_codes": list(validation.failure_codes()),
            "canonical_report": str(canonical_path),
        }
        summary["scenarios"].append(entry)  # type: ignore[arg-type]
        any_failed = any_failed or (not validation.ok)

        status = "PASS" if validation.ok else "FAIL"
        print(f"[{status}] {scn.meta.scenario_id}  codes={list(validation.failure_codes())}")
        print(report.render_markdown())

        if args.gate:
            gate_result = evaluate_integration_gate(report, scn.integration)
            gate_path = out_dir / f"{scn.meta.scenario_id}.gate.json"
            _write_guarded(gate_path, gate_result.canonical_dict(), who="gate")
            entry["gate"] = {
                "passed": gate_result.passed,
                "degraded": gate_result.degraded,
                "path": str(gate_path),
            }
            print(gate_result.render_markdown())
        print()

    summary_path = out_dir / "adr0034_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"汇总已写入：{summary_path}")
    print(f"逐场景 canonical 报告目录：{out_dir}")

    # 退出码语义（Phase C）：
    # - 仅 --strict：按 validator.ok（全 AND，warning 失败也算失败）——本地预检口径；
    # - --gate --strict：按 gate.passed（**blocking 语义**，warning 失败仅 degraded）——
    #   CI integration-gate job 口径：标准被降级的失败不拦合并，但必须可见。
    if args.gate and args.strict:
        entries = summary["scenarios"]  # type: ignore[arg-type]
        gate_failed = any(
            not bool(e.get("gate", {}).get("passed", False)) for e in entries
        )
        gate_degraded = any(bool(e.get("gate", {}).get("degraded", False)) for e in entries)
        if gate_failed:
            print("[GATE-FAIL] 存在 blocking 失败场景（详见 gate 报告）")
            return 1
        if gate_degraded:
            print("[GATE-PASS] 门禁通过，但存在 warning 降级场景（degraded=True，需人工复核）")
            return 0
        print("[GATE-PASS] 全部场景门禁通过（无失败、无降级）")
        return 0

    if any_failed:
        print("[FAIL] 存在未通过的场景（详见上方报告）")
        return 1 if args.strict else 0
    print("[PASS] 全部场景闭环验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

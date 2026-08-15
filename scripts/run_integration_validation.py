"""ADR-0034 Phase A–C · 闭环集成验证入口（手动 / CI integration-gate 接入点）。

对 ``integration`` fixture 目录下的每个 scenario 跑完整闭环：

    Scenario → IntegrationRunner.run → IntegrationValidator.validate
             → IntegrationReport.build → write_canonical_report

产出物（落 ``artifacts/adr0034_integration/``，**刻意避开** CI 的
``IntegrationReport.json`` 同名异义产物）：

    <scenario_id>.canonical.json  —— 单场景确定性报告（canonical_dict，t1 比对用；
                                     含两枚闭环指纹 + runtime provenance）
    <scenario_id>.gate.json       —— Phase C 门禁判定（--gate；blocking/warning 语义）
    <scenario_id>.fingerprints.json —— 两枚闭环指纹（DoD C5 独立指纹 artifact）
    adr0034_fingerprints.json     —— 全部场景指纹汇总（DoD C4 baseline 漂移比较的输入）
    adr0034_summary.json          —— 汇总（每场景 ok / gate / fingerprints）

设计纪律（ADR-0034 Phase A MUST + Phase C 扩展）：

- **零行为变化**：本脚本只调用已存在的 ``IntegrationRunner`` / ``IntegrationValidator`` /
  ``IntegrationReport``，不新增任何决策/感知行为；
- **不进自动门禁**：默认退出码恒 0，报告给人读、人工判断（与 ADR-0033 D8 同口径）。
  ``--strict`` 按 validator.ok（全 AND）；``--gate --strict`` 按 gate.passed
  （**blocking 语义**，warning 失败仅 degraded 不拦）——CI integration-gate 用后者；
- **确定性比对友好**：canonical 报告剔除 UUID / 时间戳（见 ``report.canonical_dict``），
  同 seed 两次运行逐字节一致（t1）；
- **指纹可溯源（DoD C2/C5/C7）**：报告含两枚闭环指纹；provenance 填 code_version +
  python/numpy/opencv/torch 版本——回答"失败发生在哪次提交、哪套运行时"。

**summary 结构约定**（评审 B4/B5）：
- ``scenarios[].gate`` 键**仅 --gate 分支存在**（非 gate 路径无此键），消费方须
  ``entry.get("gate")`` 而非直接下标；
- ``provenance`` 出现在两处：summary 顶层（**汇总元数据**）与每场景 canonical 报告内
  （**单场景溯源**）——同源不同用途，命名刻意一致（都是"这次运行的运行血缘"）。

依赖延迟导入：仅在 ``main`` 内 import 运行时 / 验证链，避免加载即拉起重链。

用法：
    python scripts/run_integration_validation.py
    python scripts/run_integration_validation.py --scenarios <dir> --out-dir <dir>
    python scripts/run_integration_validation.py --gate --strict
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

# 默认 fixture 目录：ADR-0034 Phase A 自洽 fixtures（带 integration: 期望块）。
_DEFAULT_SCENARIOS = (
    Path(__file__).resolve().parent.parent
    / "src/home_perception/validation/fixtures/scenarios/integration"
)
# 输出目录：与 CI 的 artifacts/integration/（IntegrationReport.json）刻意分开。
_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "artifacts" / "adr0034_integration"

# 报告 provenance 里记录的运行时依赖（缺失记 "n/a"，不因可选依赖缺失而崩）。
_RUNTIME_PKG_NAMES: tuple[str, ...] = ("numpy", "opencv-python", "torch")


class FingerprintArtifact(TypedDict):
    """单场景指纹 artifact 结构（``<id>.fingerprints.json``，DoD C5）。

    用 TypedDict 显式约束落盘键集（评审 A2）：未来扩展必须改类型定义，
    防止内层 dict 静默引入未知键破坏下游消费（baseline 比较 / 审计）。
    """

    scenario_id: str
    expectation_fingerprint: str
    loop_fingerprint: str


def _code_version() -> str:
    """code_version：git 短哈希优先，回退 ``home_perception.__version__``（DoD C7）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parent.parent,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    try:
        import home_perception

        return getattr(home_perception, "__version__", "unknown")
    except ImportError:
        return "unknown"


def _normalize_version(version: str) -> str:
    """归一化构建后缀（跨 OS / 跨构建可比，对齐 ADR-0033 harness.normalize_version）。"""
    return version.split("+", 1)[0]


def _runtime_provenance() -> dict[str, str]:
    """运行血缘（DoD C7）：code_version + python/numpy/opencv/torch 版本。"""
    deps: dict[str, str] = {}
    for pkg in _RUNTIME_PKG_NAMES:
        try:
            deps[pkg] = _normalize_version(importlib.metadata.version(pkg))
        except importlib.metadata.PackageNotFoundError:
            deps[pkg] = "n/a"
    return {
        "code_version": _code_version(),
        "python": platform.python_version(),
        **deps,
    }


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


def _build_runner(scn: Any) -> Any:
    """按场景声明构建 ``IntegrationRunner``（ADR-0036 P0-2：确定性运行时覆盖）。

    缺省场景（未声明 ``meta.clock_start`` / ``meta.rule_overrides``）→ 与历史行为
    逐字节一致（默认 18:00 UTC 时钟 + 默认阈值）；声明后覆盖为确定性自定义值。
    loop_fingerprint 成分**不含** clock_start / thresholds（见 runner 的
    ``_policy_fingerprint``），故覆盖不影响两枚闭环指纹的可复现性。

    规则（fail-closed）：
    - ``meta.clock_start`` 为正 Unix 秒（UTC）→ ``IntegrationRunnerConfig.clock_start``；
    - ``meta.rule_overrides`` 键必须是 ``ThresholdConfig`` 既有字段，未知键直接拒绝，
      绝不静默忽略（对齐 ADR-0034 "静默丢弃 = 失败" 的姿态）。
    """
    from home_perception.analysis.rule_engine import ThresholdConfig
    from home_perception.integration.loop.context import IntegrationRunnerConfig
    from home_perception.integration.loop.runner import IntegrationRunner

    config = IntegrationRunnerConfig(cross_modal_enabled=True)
    if scn.meta.clock_start is not None:
        config = IntegrationRunnerConfig(
            cross_modal_enabled=True,
            clock_start=datetime.fromtimestamp(scn.meta.clock_start, tz=UTC),
        )
    thresholds: ThresholdConfig | None = None
    if scn.meta.rule_overrides:
        thresholds = ThresholdConfig()
        for key, value in scn.meta.rule_overrides.items():
            if not hasattr(thresholds, key):
                raise ValueError(
                    f"场景 {scn.meta.scenario_id!r} meta.rule_overrides 含未知阈值键 "
                    f"{key!r}（ThresholdConfig 无此字段，fail-closed）"
                )
            setattr(thresholds, key, value)
    return IntegrationRunner(config=config, thresholds=thresholds)


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
    # （IntegrationRunner / IntegrationRunnerConfig 由 _build_runner 按场景懒加载，
    # 本层不直接引用，保持主流程只依赖 validator/report/gate。）
    from home_perception.integration.loop.gate import evaluate_integration_gate
    from home_perception.integration.loop.report import IntegrationReport
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
    # 每场景按 meta 声明构建 runner（ADR-0036 P0-2）：缺省 = 默认配置，行为与历史
    # 逐字节一致；声明 clock_start / rule_overrides 的场景用确定性自定义值。
    validator = IntegrationValidator()

    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scenarios_dir": str(args.scenarios),
        "provenance": _runtime_provenance(),  # DoD C7：失败可溯源到提交 + 运行时
        "scenarios": [],
    }
    any_failed = False
    all_fingerprints: dict[str, dict[str, str]] = {}

    for path in paths:
        scn = load_scenario(path)
        runner = _build_runner(scn)
        result = runner.run(scn)
        validation = validator.validate(result, scn)
        report = IntegrationReport.build(
            result, validation, provenance=_runtime_provenance()
        )

        canonical_path = out_dir / f"{scn.meta.scenario_id}.canonical.json"
        report.write_canonical_report(canonical_path)  # 过双重守卫（脱敏 + 父目录存在）

        fingerprints = {
            "expectation_fingerprint": report.expectation_fingerprint,
            "loop_fingerprint": report.loop_fingerprint,
        }
        all_fingerprints[scn.meta.scenario_id] = fingerprints
        fp_path = out_dir / f"{scn.meta.scenario_id}.fingerprints.json"
        _write_guarded(
            fp_path,
            FingerprintArtifact(
                scenario_id=scn.meta.scenario_id, **fingerprints  # type: ignore[arg-type]
            ),
            who="fingerprints",
        )

        entry: dict[str, object] = {
            "scenario_id": scn.meta.scenario_id,
            "path": str(path),
            "ok": validation.ok,
            "failure_codes": list(validation.failure_codes()),
            "canonical_report": str(canonical_path),
            "fingerprints": fingerprints,
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
    # 评审 A1 实测结论：summary **不**走 _write_guarded（脱敏守卫）。
    # summary 的排障价值恰恰在路径字段（scenarios_dir / entry.path /
    # canonical_report / gate.path），而 assert_desensitized 的 path_or_url 规则
    # 拒绝一切"像路径的值"→ 过守卫必炸（2026-08-11 实测 DesensitizationError）。
    # 当前 provenance 仅含版本号（无 PII），风险低（评审 C2 同判）；若未来 summary
    # 需承载 PII，须先剥离路径字段再引入守卫，而非原样过守卫。
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # DoD C4：全部场景指纹汇总（check_integration_baseline.py 的 current 输入）。
    fingerprints_path = out_dir / "adr0034_fingerprints.json"
    _write_guarded(
        fingerprints_path,
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "provenance": _runtime_provenance(),
            "scenarios": all_fingerprints,
        },
        who="fingerprints",
    )
    print(f"汇总已写入：{summary_path}")
    print(f"指纹汇总已写入：{fingerprints_path}")
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

"""ADR-0036 P0-4.1 · Trusted Case Factory（CI → Demo 真正闭环）。

把「ADR-0034 闭环集成验证」产出的可信 artifact，封装为**自描述、可溯源**的 ``demo/`` 包，
供 Case Viewer 直接消费并渲染「本案例由 CI 受控生成」可信徽章（P0-4.2）。

闭环链（单一生成路径，对齐 ADR-0036 不变式）：

    Scenario fixtures
      → run_integration_validation.main（闭环 runner→validator→report→canonical）
      → demo/canonical/<sid>.canonical.json   （单场景确定性报告，t1 比对）
      → demo/canonical/<sid>.{fingerprints,gate}.json
      → demo/manifest.json / fingerprints.json / gate.json / provenance.json
      → CI descriptor（generated_by="ci"）注入
      → run_case_viewer.main  → demo/case_viewer.html（可信徽章）

设计纪律（对齐 ADR-0035 D4 + ADR-0036 不变式）：

- **单一生成路径**：本脚本**只编排**两个既有入口（``run_integration_validation.main``
  / ``run_case_viewer.main``），不复制任何闭环逻辑 / 渲染逻辑（零行为变化）；
- **自描述（P0-4.1）**：``manifest.json`` 绑定 ``case_id 列表 + artifact_bundle_hash +
  renderer_version + generated_by``，并列出包内每个逻辑产物的相对路径，人 / 机器皆可读；
- **可溯源（P0-4.1）**：``provenance.json`` 记录 code_version + python/numpy/opencv/torch
  运行血缘 + 每场景 canonical 报告定位，回答「这份 demo 由哪次提交、哪套运行时生成」；
- **VM-1（唯一事实源）**：demo 包内不引入第二份风险/决策/时间轴事实；Case Viewer 仍只
  从 ``<sid>.canonical.json`` 投影；
- **确定性（t1）**：渲染管线 ``render_case_viewer`` 是投影的纯函数——**相同投影输入 → 逐字节
  一致 HTML**（已验证：非跨模态场景 canonical 两次运行逐字节相同）；工厂本身不引入墙钟/随机。
  唯一的非确定性来自上游闭环的「运行时生成 ID」：生产 ``EpisodeBuilder`` 用 uuid4 生成
  episode_id → 跨模态场景的 ``cross_modal_links.link_id`` 含 uuid → 该场景 canonical 与 HTML
  非逐字节复现（属 ADR-0034 闭环确定性待办，与本工厂无关）；``manifest.json`` / ``provenance.json``
  作为「交付信封」含 generated_at（构建时间戳，预期非确定性，非事实字段）；
- **fail-closed**：任一场景门禁 blocking 失败（``--gate --strict``）→ 仍产出 demo 包供排障，
  但工厂退出码非 0，CI 据此拦合并；绝不静默产出「残缺可信 artifact」。

依赖延迟导入：仅在 ``main`` 内 import 运行时 / 既有入口，避免导入即拉起重链。

用法：
    python scripts/build_trusted_case.py
    python scripts/build_trusted_case.py --scenarios <dir> --out-dir demo
    python scripts/build_trusted_case.py --no-gate        # 不跑 Phase C 门禁
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

_CANONICAL_DIRNAME = "canonical"
_MANIFEST_NAME = "manifest.json"
_FINGERPRINTS_NAME = "fingerprints.json"
_GATE_NAME = "gate.json"
_PROVENANCE_NAME = "provenance.json"
_CASE_VIEWER_NAME = "case_viewer.html"
_CI_DESCRIPTOR_NAME = "case_descriptor.ci.json"


def _dt_now() -> str:
    """UTC ISO 时间戳（仅用于交付信封 manifest/provenance，非事实字段）。"""
    return datetime.now(UTC).isoformat()


def _artifact_bundle_hash(canonical_dir: Path) -> str:
    """对 demo/canonical 下全部 artifact 计算稳定 sha256（t1：按路径排序后拼接字节）。

    作为「交付信封」的防篡改指纹，区别于 canonical 内的 loop/expectation 指纹
    （那是 case 语义指纹，本哈希是包完整性指纹）。
    """
    h = hashlib.sha256()
    paths: list[Path] = []
    for suffix in (".canonical.json", ".fingerprints.json", ".gate.json"):
        paths.extend(sorted(canonical_dir.glob(f"*{suffix}")))
    if not paths:
        return "0" * 64
    for p in paths:
        h.update(p.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00\x00")
    return h.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _build_ci_descriptor(renderer_version: str, out_dir: Path) -> Path:
    """生成 CI 受控生成 descriptor（generated_by="ci"），供 Case Viewer 渲染可信徽章。"""
    desc_path = out_dir / _CI_DESCRIPTOR_NAME
    _write_json(
        desc_path,
        {
            "generated_by": "ci",
            "renderer_version": renderer_version,
            "provenance_ref": _PROVENANCE_NAME,
        },
    )
    return desc_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0036 P0-4.1 Trusted Case Factory")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=None,
        help="含 integration fixture 的目录（默认=run_integration_validation 默认目录）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("demo"),
        help="Trusted Case 自描述包输出目录（默认 ./demo）",
    )
    parser.add_argument(
        "--gate/--no-gate",
        dest="gate",
        default=True,
        help="是否跑 Phase C 门禁判定（默认开；CI 受控生成须过门禁才可信）",
    )
    parser.add_argument(
        "--strict/--no-strict",
        dest="strict",
        default=True,
        help="门禁 blocking 失败时工厂退出码非 0（默认开，CI 据此拦合并）",
    )
    args = parser.parse_args(argv)

    # 确保仓库根目录在 sys.path 上，使 ``scripts`` 可作为命名空间包导入
    # （直接 ``python scripts/build_trusted_case.py`` 时，脚本目录被置为 sys.path[0]，
    # 但 ``scripts`` 自身未必是包；显式插入仓库根以兼容 ``from scripts.X import``）。
    import sys

    _repo_root = Path(__file__).resolve().parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    # 延迟导入既有入口（编排而非复制逻辑）。
    from home_perception.visualizer.viewer.case_presentation import RENDERER_VERSION
    from scripts.run_case_viewer import main as run_case_viewer_main
    from scripts.run_integration_validation import (
        _DEFAULT_SCENARIOS as _IV_DEFAULT_SCENARIOS,
    )
    from scripts.run_integration_validation import (
        _runtime_provenance,
    )
    from scripts.run_integration_validation import (
        main as run_integration_validation_main_real,
    )

    out_dir: Path = args.out_dir.resolve()
    canonical_dir = out_dir / _CANONICAL_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    canonical_dir.mkdir(parents=True, exist_ok=True)

    scenarios_dir = args.scenarios or _IV_DEFAULT_SCENARIOS

    # —— 步骤 1：闭环集成验证 → demo/canonical ——
    iv_argv = [
        "--scenarios", str(scenarios_dir),
        "--out-dir", str(canonical_dir),
    ]
    if args.gate:
        iv_argv.append("--gate")
    if args.strict:
        iv_argv.append("--strict")
    logger.info("Trusted Case Factory · 步骤1 闭环集成验证", scenarios=str(scenarios_dir))
    iv_rc = run_integration_validation_main_real(iv_argv)
    # 注意：即便 iv_rc != 0（门禁失败），仍继续产出 demo 包供排障；最终退出码在末尾传播。

    # —— 步骤 2：交付信封 provenance.json ——
    provenance = _runtime_provenance()
    provenance_payload: dict[str, object] = {
        "generated_at": _dt_now(),
        "generated_by": "ci",
        "pipeline": "Trusted Case Factory (ADR-0036 P0-4.1)",
        "renderer_version": RENDERER_VERSION,
        **provenance,
    }
    _write_json(out_dir / _PROVENANCE_NAME, provenance_payload)

    # —— 步骤 3：指纹汇总 fingerprints.json ——
    src_fp = canonical_dir / "adr0034_fingerprints.json"
    if src_fp.exists():
        shutil.copyfile(src_fp, out_dir / _FINGERPRINTS_NAME)

    # —— 步骤 4：门禁汇总 gate.json（仅 --gate 时存在）——
    summary_path = canonical_dir / "adr0034_summary.json"
    gate_overall: dict[str, object] | None = None
    if args.gate and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        scenarios_gate: list[dict[str, object]] = []
        all_passed = True
        any_degraded = False
        for entry in summary.get("scenarios", []):
            g = entry.get("gate")
            if g is None:
                continue
            passed = bool(g.get("passed", False))
            degraded = bool(g.get("degraded", False))
            all_passed = all_passed and passed
            any_degraded = any_degraded or degraded
            scenarios_gate.append(
                {
                    "scenario_id": entry["scenario_id"],
                    "passed": passed,
                    "degraded": degraded,
                    "path": g.get("path"),
                }
            )
        gate_overall = {
            "generated_at": _dt_now(),
            "passed": all_passed and not any_degraded,
            "degraded": any_degraded,
            "scenarios": scenarios_gate,
        }
        _write_json(out_dir / _GATE_NAME, gate_overall)

    # —— 步骤 5：CI descriptor（驱动可信徽章）——
    ci_desc_path = _build_ci_descriptor(RENDERER_VERSION, out_dir)

    # —— 步骤 6：Case Viewer 渲染 → demo/case_viewer.html（含 CI 受控生成徽章）——
    cv_argv = [
        "--artifacts", str(canonical_dir),
        "--descriptor", str(ci_desc_path),
        "--output", str(out_dir / _CASE_VIEWER_NAME),
    ]
    logger.info("Trusted Case Factory · 步骤6 渲染 Case Viewer", output=str(out_dir / _CASE_VIEWER_NAME))
    cv_rc = run_case_viewer_main(cv_argv)
    if cv_rc != 0:
        logger.error("Case Viewer 渲染失败（fail-closed）", rc=cv_rc)
        return cv_rc

    # —— 步骤 7：自描述 manifest.json ——
    # 收集 demo/canonical 下每个场景的 canonical 报告，列出可溯源产物清单。
    case_ids: list[str] = []
    canonical_index: dict[str, str] = {}
    for cpath in sorted(canonical_dir.glob("*.canonical.json")):
        sid = cpath.name[: -len(".canonical.json")]
        case_ids.append(sid)
        canonical_index[sid] = f"{_CANONICAL_DIRNAME}/{cpath.name}"

    bundle_hash = _artifact_bundle_hash(canonical_dir)
    manifest: dict[str, object] = {
        "package": "silvershield-trusted-case",
        "spec": "ADR-0036 P0-4.1 Trusted Case Factory",
        "generated_at": _dt_now(),
        "generated_by": "ci",
        "renderer_version": RENDERER_VERSION,
        "code_version": provenance.get("code_version", "unknown"),
        "artifact_bundle_hash": bundle_hash,
        "case_ids": case_ids,
        "scenario_count": len(case_ids),
        "gate_passed": (gate_overall or {}).get("passed", None),
        "contents": {
            "case_viewer": _CASE_VIEWER_NAME,
            "manifest": _MANIFEST_NAME,
            "provenance": _PROVENANCE_NAME,
            "fingerprints": _FINGERPRINTS_NAME,
            "gate": _GATE_NAME if gate_overall is not None else None,
            "ci_descriptor": _CI_DESCRIPTOR_NAME,
            "canonical_reports": canonical_index,
        },
    }
    _write_json(out_dir / _MANIFEST_NAME, manifest)

    logger.info(
        "Trusted Case Factory 完成",
        out_dir=str(out_dir),
        case_ids=case_ids,
        bundle_hash=bundle_hash[:16],
        iv_rc=iv_rc,
    )
    print(f"[OK] Trusted Case 自描述包已生成：{out_dir}")
    print(f"     案例：{', '.join(case_ids) or '(无)'}")
    print(f"     包完整性哈希：{bundle_hash}")
    if iv_rc != 0:
        print("[GATE-FAIL] 闭环集成验证未通过（demo 包已生成供排障，工厂退出码非 0）")
    elif gate_overall is not None:
        verdict = "PASS" if gate_overall["passed"] else "DEGRADED"
        print(f"[GATE-{verdict}] 门禁结论：passed={gate_overall['passed']} degraded={gate_overall['degraded']}")
    return iv_rc


if __name__ == "__main__":
    raise SystemExit(main())

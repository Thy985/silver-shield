"""感知场景 CLI（scenarios/audio → generated/audio → 管道校验）。

> Phase A 验证闭环的命令行入口，复用 ``home_perception.audio.tts.scenario_runner``。
>
> 子命令：
>   generate  把 scenarios/audio/*.yaml 合成为 generated/audio/<name>.wav（base + effects）
>   validate  对每个场景跑 AudioPipeline，对比 expected.perception（默认子集语义）
>
> 依赖：仅 numpy + pyyaml（与 tts 包一致），不引入重解码依赖；管道为离线 EnergyVAD，无需权重。

用法：
    python scripts/run_audio_scenarios.py generate
    python scripts/run_audio_scenarios.py validate [--strict]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from home_perception.audio.tts.scenario_runner import (
    load_scenarios_dir,
    validate_scenario,
)
from home_perception.common.logging import get_logger

log = get_logger(__name__)


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    # scripts/run_audio_scenarios.py -> repo root
    return p.parent.parent


def _default_scenarios() -> Path:
    return _repo_root() / "scenarios" / "audio"


def _default_fixtures() -> Path:
    return _repo_root() / "tests" / "fixtures" / "audio"


def _default_generated() -> Path:
    return _repo_root() / "generated" / "audio"


def cmd_generate(args: argparse.Namespace) -> int:
    from home_perception.audio.tts.scenario_runner import synthesize

    scenarios_dir = Path(args.scenarios_dir)
    out_dir = Path(args.generated_dir)
    fixtures_root = Path(args.fixtures_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    scns = load_scenarios_dir(scenarios_dir)
    log.info("scenarios.generate", count=len(scns), out=str(out_dir))
    for scn in scns:
        path = synthesize(scn, out_dir, fixtures_root)
        log.info("scenarios.generate.one", name=scn.name, file=path.name)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    scenarios_dir = Path(args.scenarios_dir)
    fixtures_root = Path(args.fixtures_root)
    scns = load_scenarios_dir(scenarios_dir)
    results = [validate_scenario(scn, fixtures_root, strict=args.strict) for scn in scns]
    for r in results:
        log.info(
            "scenarios.validate.result",
            name=r.name,
            status="PASS" if r.ok else "FAIL",
            mode="strict" if r.strict else "subset",
            observed=r.observed,
            expected=r.expected,
        )
    failed = [r for r in results if not r.ok]
    if failed:
        log.error("scenarios.validate.failed", n=len(failed), total=len(results))
        return 1
    log.info("scenarios.validate.ok", total=len(results))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="感知场景 generate/validate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="合成 scenarios/audio → generated/audio")
    g.add_argument("--scenarios-dir", default=str(_default_scenarios()))
    g.add_argument("--generated-dir", default=str(_default_generated()))
    g.add_argument("--fixtures-root", default=str(_default_fixtures()))
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="校验 observed ⊆ expected（或 --strict 精确相等）")
    v.add_argument("--scenarios-dir", default=str(_default_scenarios()))
    v.add_argument("--fixtures-root", default=str(_default_fixtures()))
    v.add_argument("--strict", action="store_true", help="精确相等校验（Phase B 就绪）")
    v.set_defaults(func=cmd_validate)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

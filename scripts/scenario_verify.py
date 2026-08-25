"""PR-A：一键产品结论回归（--verify-all）。

对 Product Scenario Registry 的 3 个场景各跑 N 帧（含音频注入），
校验 ``expected_product_result``（RAISED / WARN / MONITOR）是否达成。

设计：
  - 复用 ``DemoGateway.assemble``（一次加载 YOLO，~7s）
  - 复用 ``scripts/run_demo._build_live_audio_events``（音频注入，含 synthetic_replay fixture）
  - 复用 ``DemoGateway.switch_source``（场景切换，~50ms）
  - 同步跑帧（复刻 ``run_loop`` 的 ``RuntimeFrameContext`` 构造，不开 WS / 浏览器 / MJPEG）
  - 校验 ``WarningEvent`` 集合是否符合 ``expected_product_result`` 语义

校验规则：
  - RAISED  → 至少 1 个 ``risk_level=HIGH`` 的 WarningEvent
  - WARN    → 至少 1 个 WarningEvent 且无 HIGH（不升级到通知家属 + 社区上报）
  - MONITOR → 0 个 WarningEvent（系统克制，看到人 ≠ 报警）

用法：
  python scripts/run_demo.py --verify-all           # 默认 480 帧/场景
  python scripts/run_demo.py --verify-all --frames 240  # 快速模式
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Windows PowerShell 中文输出兜底（避免 mojibake）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# 复用 run_demo.py 的音频注入逻辑（依赖倒置接缝）
from run_demo import _build_live_audio_events, _register_synthetic_source

from home_perception.core.config import Settings
from home_perception.runtime.pipeline import RuntimeFrameContext
from silver_demo.config import DemoSettings
from silver_demo.gateway import DemoGateway
from silver_demo.product_scenarios import PRODUCT_SCENARIOS
from silver_demo.scenarios import load_scenario

DEFAULT_FRAMES = 480


@dataclass(frozen=True)
class ScenarioVerifyResult:
    """单个场景的验证结果。"""

    scenario_id: str
    expected: str
    actual_warnings: int
    actual_levels: tuple[str, ...]
    actual_actions: tuple[str, ...]
    passed: bool
    elapsed_s: float
    detail: str = ""


@dataclass
class VerifyReport:
    """整体验证报告。"""

    results: list[ScenarioVerifyResult] = field(default_factory=list)
    assemble_s: float = 0.0
    total_s: float = 0.0

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)


def _verify_expected(expected: str, warnings: list[Any]) -> tuple[bool, str]:
    """校验 warnings 是否符合 expected_product_result 语义。

    返回 (passed, detail)。
    """
    levels = sorted({getattr(w, "risk_level", "?") for w in warnings})
    actions = sorted({getattr(w, "recommended_action", "?") for w in warnings})
    n = len(warnings)

    if expected == "RAISED":
        if "HIGH" in levels:
            return True, f"HIGH 触发（{n} warnings, levels={levels}, actions={actions}）"
        return False, f"期望 RAISED（HIGH）但最高 level={levels or '∅'}（{n} warnings）"

    if expected == "WARN":
        if n == 0:
            return False, "期望 WARN 但 0 warnings"
        if "HIGH" in levels:
            return False, f"期望 WARN（不升级）但有 HIGH（{n} warnings, levels={levels}）"
        return True, f"WARN 级别达成（{n} warnings, levels={levels}, actions={actions}）"

    if expected == "MONITOR":
        if n == 0:
            return True, "0 warnings（系统克制，看到人 ≠ 报警）"
        return False, f"期望 MONITOR（0 warnings）但产出 {n} warnings, levels={levels}, actions={actions}"

    return False, f"未知 expected_product_result={expected!r}"


async def _run_n_frames(gw: DemoGateway, n: int) -> list[Any]:
    """同步跑 n 帧，返回 WarningEvent 列表（复刻 run_loop 的 ctx 构造，不开 WS）。"""
    warnings: list[Any] = []
    interval = gw.scenario.frame_interval_s
    frame_iter = iter(gw.source)
    for i in range(n):
        try:
            _, frame = next(frame_iter)
        except StopIteration:
            break
        gw.clock.tick(interval)
        ctx = RuntimeFrameContext(
            video_frame=frame,
            frame_index=i,
            case_time=round(i * interval, 3),
            audio_events=gw._runtime_audio_events(i),
        )
        result = gw.pipeline.process_frame(ctx)
        warnings.extend(getattr(result, "warnings", None) or ())
    return warnings


async def _stop_run_loop(gw: DemoGateway) -> None:
    """取消 switch_source 启动的后台 run_loop task（verify 场景手动跑帧，不需要后台循环）。"""
    if gw._task is not None:
        gw._task.cancel()
        try:
            await gw._task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] run_loop 取消异常：{exc}", file=sys.stderr)
        gw._task = None


async def verify_all(n_frames: int = DEFAULT_FRAMES) -> VerifyReport:
    """跑 3 场景产品结论回归，返回 VerifyReport。"""
    # 确保 audio.enabled=true（复用 live_audio.yaml 覆盖）
    if not os.environ.get("DEMO_HP_CONFIG"):
        live_hp = ROOT / "config" / "live_audio.yaml"
        if live_hp.is_file():
            os.environ["DEMO_HP_CONFIG"] = str(live_hp)
    os.environ["DEMO_LIVE"] = "1"

    _register_synthetic_source()

    ds = DemoSettings.from_env()
    hp = Settings.load(ds.home_perception_config)

    # 用第一个场景装配 gateway（含 YOLO 加载，~7s）
    first_ps = PRODUCT_SCENARIOS[0]
    first_sc = load_scenario(ROOT / first_ps.scenario_yaml)
    gw = DemoGateway(ds, hp, first_sc)

    t_assemble = time.monotonic()
    gw.assemble()
    assemble_s = time.monotonic() - t_assemble

    report = VerifyReport(assemble_s=assemble_s)
    t_total = time.monotonic()

    for ps in PRODUCT_SCENARIOS:
        sc = load_scenario(ROOT / ps.scenario_yaml)
        # 音频注入（依赖倒置接缝）
        try:
            events = _build_live_audio_events(hp, sc)
            gw.set_live_audio_events(events)
        except Exception as exc:  # noqa: BLE001
            # 音频注入失败不阻断验证（降级为无音频，记日志）
            gw.set_live_audio_events([])
            print(f"[warn] {ps.scenario_id} 音频注入失败：{exc}", file=sys.stderr)

        await gw.switch_source(sc)
        await _stop_run_loop(gw)

        t0 = time.monotonic()
        warnings = await _run_n_frames(gw, n_frames)
        elapsed = time.monotonic() - t0

        passed, detail = _verify_expected(ps.expected_product_result, warnings)
        levels = tuple(sorted({getattr(w, "risk_level", "?") for w in warnings}))
        actions = tuple(sorted({getattr(w, "recommended_action", "?") for w in warnings}))

        report.results.append(
            ScenarioVerifyResult(
                scenario_id=ps.scenario_id,
                expected=ps.expected_product_result,
                actual_warnings=len(warnings),
                actual_levels=levels,
                actual_actions=actions,
                passed=passed,
                elapsed_s=elapsed,
                detail=detail,
            )
        )

    report.total_s = time.monotonic() - t_total
    await _stop_run_loop(gw)
    gw.close()
    return report


def print_report(report: VerifyReport) -> int:
    """打印验证报告，返回退出码（0=全过，1=有失败）。"""
    print()
    print("=" * 72)
    print("  产品结论回归报告（--verify-all）")
    print("=" * 72)
    print(f"  assemble: {report.assemble_s:.2f}s  |  总耗时: {report.total_s:.2f}s")
    print()

    for r in report.results:
        mark = "✓" if r.passed else "✗"
        print(
            f"  [{mark}] {r.scenario_id:32s}  期望={r.expected:8s}  "
            f"实际={r.actual_warnings}w {list(r.actual_levels)}  "
            f"{r.elapsed_s:.1f}s"
        )
        if not r.passed:
            print(f"      → {r.detail}")

    print()
    if report.all_passed:
        print("  ✓ 3 场景产品结论全部达成")
    else:
        failed = [r for r in report.results if not r.passed]
        print(f"  ✗ {len(failed)}/{len(report.results)} 场景未达成：")
        for r in failed:
            print(f"      - {r.scenario_id}: {r.detail}")

    print("=" * 72)
    return 0 if report.all_passed else 1


def main(frames: int = DEFAULT_FRAMES) -> int:
    """CLI 入口：跑验证 + 打印报告 + 返回退出码。"""
    report = asyncio.run(verify_all(frames))
    return print_report(report)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FRAMES
    sys.exit(main(n))
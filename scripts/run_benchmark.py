"""ADR-0033 Phase 1 手动 / CI 入口（不接线 demo / gateway，D8）。

用法：
    python scripts/run_benchmark.py --scenarios <dir> --set-id <id> [--out report.json]

默认构造 torch-free 的 ``PerceptionPipeline``（detections 通道零模型），不拉起真实
YOLO；``--frames`` 可切换 frames 通道（需真实 detector，opt-in）。报告落盘 JSON +
stdout Markdown。Phase 1 报告**给人读、人工判断**，不进任何自动门禁。

依赖延迟导入：脚本仅在 ``main`` 内 import 运行时 / 评估链，避免加载即拉起重链。
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from home_perception.common.logging import get_logger

logger = get_logger(__name__)


class _SimpleClock:
    """可推进的确定性时钟（替代 DemoClock，避免测试 / 脚本依赖 runtime 内部）。"""

    def __init__(self, start: datetime, interval_s: float = 0.5) -> None:
        self._t = start
        self.interval_s = interval_s

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def tick(self, dt: float | None = None) -> None:
        self._t = self._t + timedelta(seconds=dt if dt is not None else self.interval_s)


def _build_torchfree_pipeline(detector, clock, frame_interval_s: float):
    """构造 torch-free 的 ``PerceptionPipeline``（与 ADR-0032 端到端测试同款装配）。"""
    from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
    from home_perception.action.executor import ActionExecutor
    from home_perception.action.notifier import MockNotifier
    from home_perception.action.publisher import MockPublisher
    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.analysis.feature_extractor import FeatureExtractor
    from home_perception.analysis.rule_engine import RuleEngine
    from home_perception.detection.tracker import VisitorTracker
    from home_perception.runtime.pipeline import PerceptionPipeline

    tracker = VisitorTracker(now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="scenario", now_provider=clock)
    feature_extractor = FeatureExtractor(frequency_window_s=60.0)
    rule_engine = RuleEngine(device_id="home_entry_01", location="入户门", now_provider=clock)
    decision_engine = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher, publisher, notifier, max_retries=1)
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feature_extractor,
        rule_engine=rule_engine,
        decision_engine=decision_engine,
        executor=executor,
        now_provider=clock,
        frame_interval_s=frame_interval_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0033 Phase 1 Benchmark Harness")
    parser.add_argument("--scenarios", required=True, help="场景 YAML 目录")
    parser.add_argument("--set-id", required=True, help="scenario_set_id（标识测的是哪批案例）")
    parser.add_argument("--out", default=None, help="报告 JSON 输出路径")
    parser.add_argument("--mode", default="detections", choices=["detections", "frames"])
    parser.add_argument("--frame-interval-s", type=float, default=0.5)
    parser.add_argument(
        "--code-version", default=None, help="显式注入 code_version（否则取 git 短哈希）"
    )
    # —— Phase 2 基线对照 / bump（D7，报告性、非门禁）——
    parser.add_argument(
        "--baseline",
        default=None,
        metavar="PATH",
        help="对照基线 JSON 路径；提供则跑 evaluate_regression 并打印 diff "
        "（退出码恒 0，Phase 2 不触发 CI 非零；candidate 须与基线在 vary 轴不同）",
    )
    parser.add_argument(
        "--max-regression-delta",
        type=float,
        default=None,
        help="回归预算 Δ（仅信息性对照，超过置 regressions_exceeded=True，不阻断）",
    )
    parser.add_argument(
        "--write-baseline",
        default=None,
        metavar="PATH",
        help="写基线 JSON（bump 工作流）：canonical_dict 落到指定路径；"
        "若填 'auto' 则落到 <baselines_dir>/<set-id>.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="与 --write-baseline 配合：覆盖已存在的基线文件（D7 显式 bump 动作，"
        "须 Owner 评审；默认不覆盖以防误操作）",
    )
    args = parser.parse_args(argv)

    from home_perception.evaluation.harness import BenchmarkHarness
    from home_perception.validation import load_scenarios_dir
    from home_perception.validation.synthetic_input import SyntheticInput

    scenarios = load_scenarios_dir(args.scenarios)
    if not scenarios:
        logger.warning("benchmark_scenarios_empty", dir=args.scenarios)
        return 2

    odd_start = datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)  # odd hour 起点（与 ADR-0032 同款）

    def build_pipeline(synth: SyntheticInput):
        clock = _SimpleClock(odd_start, interval_s=args.frame_interval_s)
        return _build_torchfree_pipeline(synth.detector, clock, args.frame_interval_s)

    report = BenchmarkHarness().run(
        scenarios,
        build_pipeline,
        scenario_set_id=args.set_id,
        frame_interval_s=args.frame_interval_s,
        code_version=args.code_version,
        generated_at=datetime.now(UTC).isoformat(),
    )
    if args.out:
        # write_report 拒绝自动创建父目录（防路径穿越），故此处显式建目录
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        report.write_report(args.out)
        logger.info("benchmark_report_written", path=args.out)

    # —— Phase 2：写基线（bump 工作流）——
    if args.write_baseline:
        from home_perception.evaluation.ab_runner import (
            BASELINES_DIR,
            baseline_path,
            write_baseline_report,
        )

        if args.write_baseline == "auto":
            target = baseline_path(args.set_id, BASELINES_DIR)
        else:
            target = Path(args.write_baseline)
            # Critical 3：与 --out 对称——CLI 层显式建父目录（用户显式指定路径即视为其落盘意图）。
            # 函数层 ``write_canonical_report`` 仍拒自动建目录（守程序化调用方路径穿越），故此处
            # 由 CLI 预先建好父目录；'auto' 落点 BASELINES_DIR 随 commit 已存在，无需建。
            target.parent.mkdir(parents=True, exist_ok=True)
        # C1：已提交基线不可被静默覆盖（D7 显式 bump 必须 --force）；
        # 显式路径同理需 --force 才允许覆盖，防止误操作抹掉 reference。
        if target.exists() and not args.force:
            logger.warning(
                "benchmark_baseline_overwrite_blocked",
                path=str(target),
                hint="加 --force 显式覆盖（须 Owner 评审，注明 benchmark-baseline-bump）",
            )
            print(
                f"[baseline] 拒绝覆盖已存在文件（防误操作）：{target}\n"
                f"          若确要 bump 基线，请加 --force（并务必在 PR 注明 benchmark-baseline-bump）"
            )
            return 2
        # C2/M8：走 write_baseline_report 双守卫（脱敏 + 父目录须存在，不 mkdir parents）
        write_baseline_report(target, report)
        logger.info("benchmark_baseline_written", path=str(target))

    # —— Phase 2：对照基线（报告性、非门禁）——
    if args.baseline:
        from home_perception.evaluation.ab_runner import (
            BenchmarkABConservationError,
            evaluate_regression,
            load_baseline_report_path,
        )

        # C3：max_regression_delta 必须 ≥ 0，负值令语义反转，入口即拦
        if args.max_regression_delta is not None and args.max_regression_delta < 0:
            print(
                "[regression] --max-regression-delta 必须 ≥ 0（表达可容忍退化预算），"
                f"收到 {args.max_regression_delta}"
            )
            return 2

        baseline = load_baseline_report_path(args.baseline)
        try:
            reg = evaluate_regression(
                report,
                baseline,
                max_regression_delta=args.max_regression_delta,
            )
        except BenchmarkABConservationError as exc:  # 守恒/装配错误，清晰报错，退出码 1
            logger.warning("benchmark_regression_conservation_failed", error=str(exc))
            print(f"[regression] 守恒校验失败（基线对照装配错误）：{exc}")
            return 1
        # 注意：基线 JSON 解析错误（KeyError / JSONDecodeError / TypeError）不在此捕获，
        # 直接透传给调用方，暴露真实根因（C4：不再裸 except Exception 伪装成守恒失败）。
        print(reg.render_markdown())

    # 最终报告为人类可读产物，输出到 stdout（命令主产物，非日志）
    print(report.render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

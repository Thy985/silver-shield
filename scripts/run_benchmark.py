"""ADR-0033 Phase 1 + 2 手动 / CI 入口（不接线 demo / gateway，D8）。

用法：
    python scripts/run_benchmark.py --scenarios <dir> --set-id <id> [--out report.json]
    # Phase 2 基线对照 / bump（报告性、非门禁）：
    python scripts/run_benchmark.py ... --baseline <path> --max-regression-delta 0.01
    python scripts/run_benchmark.py ... --write-baseline auto --force

默认构造 torch-free 的 ``PerceptionPipeline``（detections 通道零模型），不拉起真实
YOLO；``--frames`` 可切换 frames 通道（需真实 detector，opt-in）。报告落盘 JSON +
stdout Markdown。Phase 1 报告**给人读、人工判断**，不进任何自动门禁；Phase 2 基线对照
**不**触发 CI 非零退出、**不**做 Hard Gate / 复合分门控（MUST NOT，见 ADR-0033 §6）。
Phase 3 生产门控为**独立开关**：``--gate`` 启用 Hard Gate + 阈值 + 可选基线回归门禁，
未通过 → 退出码 3；默认关闭，保持 Phase 1/2 行为零变化（D8）。

依赖延迟导入：脚本仅在 ``main`` 内 import 运行时 / 评估链，避免加载即拉起重链。
"""

from __future__ import annotations

import argparse
import math
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
        "若填 'auto' 则落到 <baselines_dir>/<set-id>.json。"
        "显式 PATH 默认仅允许 baselines 目录子树内（自动建父目录，与 --out 同规约）；"
        "子树外路径须加 --write-baseline-allow-anywhere 显式放行（注意路径安全，round 5）",
    )
    parser.add_argument(
        "--write-baseline-allow-anywhere",
        action="store_true",
        help="与 --write-baseline 配合：允许显式路径落在 baselines 目录子树之外并自动创建"
        "父目录（默认拒绝，防路径穿越/误写任意位置；请自行确认路径安全）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="与 --write-baseline 配合：覆盖已存在的基线文件（D7 显式 bump 动作，"
        "须 Owner 评审；默认不覆盖以防误操作）",
    )
    # —— Phase 3 生产门控（独立 --gate 开关，默认关闭，零行为变化）——
    parser.add_argument(
        "--gate",
        action="store_true",
        help="启用生产门禁（ADR-0033 Phase 3，D5）：Hard Gate（全部场景 MUST 通过 "
        "ScenarioValidator 且达阈值）→ 阈值对照 → 可选基线回归 → 复合分（仅报告）。"
        "门禁未通过 → 退出码 3（非零，CI 拦截）；默认不启用，保持 Phase 1/2 行为。",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="门控：标注场景最低通过率（默认 1.0，须全部 TP/TN）",
    )
    parser.add_argument(
        "--max-suppression-rate",
        type=float,
        default=None,
        help="门控：漏报率上限（默认 0.0）",
    )
    parser.add_argument(
        "--max-false-alarm-rate",
        type=float,
        default=None,
        help="门控：误报率上限（默认 0.05）",
    )
    parser.add_argument(
        "--max-mean-risk-shortfall",
        type=float,
        default=None,
        help="门控：平均风险缺口上限（默认 0.0；场景集未标定时该阈值自动跳过）",
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
            baseline_write_gate,
            write_baseline_report,
        )

        if args.write_baseline == "auto":
            target = baseline_path(args.set_id, BASELINES_DIR)
        else:
            target = Path(args.write_baseline)
        # round 5：落盘门（纯策略）——防静默覆盖（C1）+ 防子树外路径自动建父目录（路径安全）。
        # auto 落点恒在 BASELINES_DIR 内；显式子树内路径经门后安全 mkdir；子树外路径须
        # --write-baseline-allow-anywhere 显式放行。
        ok, hint = baseline_write_gate(
            target,
            force=args.force,
            allow_anywhere=args.write_baseline_allow_anywhere,
            baselines_dir=BASELINES_DIR,
        )
        if not ok:
            logger.warning("benchmark_baseline_write_blocked", path=str(target))
            print(hint)
            return 2
        # 函数层 ``write_canonical_report`` 仍拒自动建目录（守程序化调用方路径穿越），
        # 故由 CLI 在门通过后预先建好父目录（auto 落点 BASELINES_DIR 已存在，no-op）。
        target.parent.mkdir(parents=True, exist_ok=True)
        write_baseline_report(target, report)
        logger.info("benchmark_baseline_written", path=str(target))

    # —— Phase 2/3：基线对照（Phase 2 报告性 / Phase 3 可选门禁）——
    # 先把基线加载出来，供后续 Phase 2 回归展示 或 Phase 3 门禁共用（避免重复加载）。
    baseline = None
    if args.baseline:
        from home_perception.evaluation.ab_runner import (
            BenchmarkABConservationError,
            load_baseline_report_path,
        )

        # C3：max_regression_delta 必须 ≥ 0 且非 NaN，负值令语义反转、NaN 静默吞判定，入口即拦
        if args.max_regression_delta is not None and (
            args.max_regression_delta < 0 or math.isnan(args.max_regression_delta)
        ):
            print(
                "[regression] --max-regression-delta 必须 ≥ 0 且非 NaN（表达可容忍退化预算），"
                f"收到 {args.max_regression_delta}"
            )
            return 2

        # Medium 11：基线加载失败（文件不存在 / JSON 损坏 / 顶层非对象 / 字段非法）给友好
        # 错误并退出码 1，而非 raw 异常栈；JSONDecodeError 是 ValueError 子类，一并覆盖。
        try:
            baseline = load_baseline_report_path(args.baseline)
        except (FileNotFoundError, ValueError, TypeError) as exc:
            logger.warning("benchmark_baseline_load_failed", error=str(exc))
            print(
                f"[regression] 基线加载失败：{exc}\n"
                "          提示：检查路径是否存在、基线 JSON 是否完整（可用 "
                "--write-baseline 重新生成，并确认 PR 注明 benchmark-baseline-bump）"
            )
            return 1

    # —— Phase 3 生产门控（仅 --gate 启用，默认关闭）——
    if args.gate:
        from home_perception.evaluation.ab_runner import BenchmarkABConservationError
        from home_perception.evaluation.gate import (
            BenchmarkThresholds,
            evaluate_gate,
        )

        gate_thr: dict[str, object] = {}
        # 仅当用户显式覆盖时才传入，否则用 BenchmarkThresholds D7 默认值
        if args.min_pass_rate is not None:
            gate_thr["min_pass_rate"] = args.min_pass_rate
        if args.max_suppression_rate is not None:
            gate_thr["max_suppression_rate"] = args.max_suppression_rate
        if args.max_false_alarm_rate is not None:
            gate_thr["max_false_alarm_rate"] = args.max_false_alarm_rate
        if args.max_mean_risk_shortfall is not None:
            gate_thr["max_mean_risk_shortfall"] = args.max_mean_risk_shortfall
        # max_regression_delta 交由 thresholds（None = 不对照回归门禁）
        gate_thr["max_regression_delta"] = args.max_regression_delta
        thresholds = BenchmarkThresholds(**gate_thr)

        try:
            gate = evaluate_gate(report, thresholds, baseline=baseline)
        except BenchmarkABConservationError as exc:  # 基线对照装配/守恒错误，退出码 1
            logger.warning("benchmark_gate_conservation_failed", error=str(exc))
            print(f"[gate] 基线对照守恒校验失败（装配错误）：{exc}")
            return 1
        print(gate.render_markdown())
        if not gate.passed:
            logger.warning("benchmark_gate_failed", set_id=args.set_id)
            # 退出码 3：Hard Gate / 阈值 / 回归未通过（区别于 1=加载/装配错误、2=输入错误）
            return 3
    elif baseline is not None:
        # Phase 2 行为（--baseline 且无 --gate）：报告性回归对照，退出码恒 0
        from home_perception.evaluation.ab_runner import evaluate_regression

        try:
            reg = evaluate_regression(
                report, baseline, max_regression_delta=args.max_regression_delta
            )
        except BenchmarkABConservationError as exc:  # 守恒/装配错误，清晰报错，退出码 1
            logger.warning("benchmark_regression_conservation_failed", error=str(exc))
            print(f"[regression] 守恒校验失败（基线对照装配错误）：{exc}")
            return 1
        print(reg.render_markdown())

    # 最终报告为人类可读产物，输出到 stdout（命令主产物，非日志）
    print(report.render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import sys
from datetime import UTC, datetime, timedelta


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
    args = parser.parse_args(argv)

    from home_perception.evaluation.harness import BenchmarkHarness
    from home_perception.validation import load_scenarios_dir
    from home_perception.validation.synthetic_input import SyntheticInput

    scenarios = load_scenarios_dir(args.scenarios)
    if not scenarios:
        print(f"[!] 目录 {args.scenarios} 无场景 YAML", file=sys.stderr)
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
        report.write_report(args.out)
        print(f"[i] 报告已写入 {args.out}")
    print(report.render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

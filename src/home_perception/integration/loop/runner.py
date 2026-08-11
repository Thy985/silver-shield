"""ADR-0034 Phase A · D2：``IntegrationRunner`` —— 闭环编排（L1–L5）。

与 ADR-0032 ``ScenarioRunner`` 的分工（G5：**不扩 ``RunResult``，并列一个 Runner**）：

| | ``ScenarioRunner`` | ``IntegrationRunner`` |
|---|---|---|
| 观测深度 | 感知层（``perception_events`` / ``warnings``） | 全闭环（+ ``commands`` / ``traces`` / ``episodes``） |
| 探针 | 无 | ``IntegrationContext`` 三通道 |
| 产物 | ``RunResult`` | ``IntegrationRunResult``（**内含**一枚等价 ``RunResult``） |

内含 ``RunResult`` 而非另起炉灶，是为了让感知层判据继续走 ADR-0033
``build_scenario_score``（复用不重写，t3），闭环只在其上**叠加**下游 stage。

生命周期（ADR-0034 §D2 L1–L5）：

1. **L1 建探针** —— ``IntegrationContext.build(config)``，唯一创建点；
2. **L2 注入 runtime** —— ``_assemble()``，唯一注入点（``decision_engine(trace_recorder=)`` /
   ``executor(sink=)`` / ``memory_hook=``）；
3. **L3 执行 Scenario** —— 复用 ADR-0032 编译产物逐帧推进；
4. **L4 收集 artifacts** —— 从探针**只读**读回，不加工不判定；
5. **L5 产出** —— ``IntegrationRunResult``（含只读 context 句柄）。

> 顺序细节：``ScenarioCompiler.compile`` 在 L2 之前调用，因为 ``detections`` 通道的
> ``detector`` 是 pipeline 的**构造依赖**。这不破坏 L2/L3 职责边界——编译只产输入、
> 不驱动执行；L2 仍只装配，L3 仍只驱动。

签名冻结（t11）：``run(self, scenario, context=None)``。**禁止**出现 ``pipeline`` /
``recorder`` / ``store`` 形参——一旦允许传入已装配 pipeline，Runner 就退化为参数搬运器，
"探针唯一创建点 + 唯一注入点"随之失效，F6 交叉校验不再可信。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from home_perception.validation.runner.runner import RunResult
from home_perception.validation.scenario.compiler import ScenarioCompiler

from .context import IntegrationConfigError, IntegrationContext, IntegrationRunnerConfig

if TYPE_CHECKING:  # 仅注解，避免加载期拉起重链
    from home_perception.analysis.rule_engine import ThresholdConfig
    from home_perception.validation.scenario.scenario import Scenario

__all__ = ["IntegrationRunResult", "IntegrationRunner"]

# 装配缺省身份（与 scripts/run_benchmark._build_torchfree_pipeline 保持同款，便于对照）
DEFAULT_ELDER_ID = "elder_001"
DEFAULT_DEVICE_ID = "home_entry_01"
DEFAULT_LOCATION = "入户门"
DEFAULT_SOURCE_VIDEO = "scenario"


@dataclass(frozen=True, slots=True)
class IntegrationRunResult:
    """闭环一次运行的全部只读产物（L5）。

    ``commands`` 与 ``sink_commands`` 是**两条独立通道**，刻意不合并：

    - ``commands``      ：``FrameResult.commands`` 汇总 —— 生产链路自报的执行结果；
    - ``sink_commands`` ：``ActionSink`` 读回 —— 旁路探针独立观测到的执行结果。

    二者不一致即 F6（Observability Drop）：要么生产链路漏报，要么探针漏采，
    无论哪种，"我们对系统行为的观测"都不可信，必须整体判不通过。

    ``run_result`` 为 ADR-0032 形态的感知层结果，字段语义与 ``ScenarioRunner.run``
    逐字段对齐（唯一差异是本 Runner 额外收集 ``commands``），供
    ``ScenarioValidator`` / ``build_scenario_score`` 直接消费。
    """

    scenario_id: str
    mode: str
    n_frames: int
    run_result: RunResult
    perception_events: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()
    commands: tuple[Any, ...] = ()
    sink_commands: tuple[Any, ...] = ()
    decision_traces: tuple[Any, ...] = ()
    episodes: tuple[Any, ...] = ()
    cross_modal_links: tuple[Any, ...] = ()  # Phase A 恒空（Phase B 填充）
    fingerprint: str = ""
    context: IntegrationContext | None = None


def _read_sink_commands(sink: Any) -> tuple[Any, ...]:
    """从 ``ActionSink`` 读回已记录命令（鸭子读取，fail-closed）。

    ``ActionSink`` 协议只约定写侧（``record`` / ``flush``），读回属实现细节。
    读不到就**报错**而不是返回空元组——返回空会被下游误判为"Action Drop（F3）"，
    把"探针不支持回读"这一配置问题伪装成"系统丢了命令"这一行为问题。
    """
    reader = getattr(sink, "commands", None)
    if not callable(reader):
        raise IntegrationConfigError(
            f"action_sink={type(sink).__name__} 不支持 L4 读回（缺 commands()）；"
            "Phase A 判定通道请用 InMemoryActionRecorder（jsonl 仅作审计落盘）"
        )
    return tuple(reader())


def _read_traces(recorder: Any) -> tuple[Any, ...]:
    """从 ``DecisionTraceRecorder`` 读回已记录 trace（鸭子读取，fail-closed）。

    理由同 ``_read_sink_commands``：读不回就报错，不伪装成 Decision Drop（F2）。
    """
    traces = getattr(recorder, "traces", None)
    if traces is None:
        raise IntegrationConfigError(
            f"trace_recorder={type(recorder).__name__} 不支持 L4 读回（缺 traces）；"
            "Phase A 判定通道请用 InMemoryRecorder（jsonl 仅作审计落盘）"
        )
    return tuple(traces)


@dataclass
class IntegrationRunner:
    """Scenario → Runtime → Memory → Decision → Notification 闭环编排器。

    构造参数承载**装配形态**（阈值 / 身份 / 配置），``run()`` 只接场景与可选 context——
    这样签名才能冻结（t11）。
    """

    config: IntegrationRunnerConfig = field(default_factory=IntegrationRunnerConfig)
    thresholds: ThresholdConfig | None = None
    elder_id: str = DEFAULT_ELDER_ID
    device_id: str = DEFAULT_DEVICE_ID
    location: str = DEFAULT_LOCATION
    source_video: str = DEFAULT_SOURCE_VIDEO
    compiler: ScenarioCompiler = field(default_factory=ScenarioCompiler)

    # ------------------------------------------------------------------ L1–L5
    def run(self, scenario: Scenario, context: IntegrationContext | None = None):
        """跑通一次闭环并返回只读产物（**签名冻结**：参数名集合 == {scenario, context}）。

        Args:
            scenario: 已加载 + 校验的 ADR-0032 ``Scenario``。
            context: 可选的既有探针容器（复用同一批探针跨场景累积时传入）；
                ``None`` 时由 ``IntegrationContext.build(self.config)`` 现建（L1）。

        Returns:
            ``IntegrationRunResult``（L5）。判定归 ``IntegrationValidator``，本方法不断言。
        """
        ctx = context or IntegrationContext.build(self.config)  # L1 建探针
        synth = self.compiler.compile(scenario, mode=scenario.mode)  # L3 输入（编译产物）
        pipeline = self._assemble(ctx, synth.detector)  # L2 注入 runtime
        frame_results = self._drive(pipeline, synth, ctx)  # L3 执行 Scenario
        return self._collect(ctx, synth, frame_results)  # L4 + L5

    # ------------------------------------------------------------------ L2
    def _assemble(self, ctx: IntegrationContext, detector: Any) -> Any:
        """装配注入了三枚探针的 ``PerceptionPipeline``（**唯一注入点**）。

        与 ``scripts/run_benchmark._build_torchfree_pipeline`` 同款 torch-free 装配，
        额外接三处观测接缝：

        - ``DecisionEngine(trace_recorder=ctx.trace_recorder)`` —— ADR-0031 既有接缝；
        - ``ActionExecutor(sink=ctx.action_sink)`` —— ADR-0034 D3 新增接缝；
        - ``memory_hook=MemoryHook(...)`` —— 显式注入（G3：手工装配的 pipeline 不会
          自带 ``cross_modal_runtime``，Phase B 要在此处接跨模态，故 Phase A 就把
          注入点固定下来，避免届时改动扩散）。

        依赖全部**延迟 import**：本包被 ``scripts`` / ``tests`` 加载，不应在 import
        期就拉起 runtime 重链（与 ``scripts/run_benchmark`` 同纪律）。
        """
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
        from home_perception.memory.episode_builder import DefaultEpisodeBuilder
        from home_perception.runtime.memory_hook import MemoryHook
        from home_perception.runtime.observability import PipelineMetrics
        from home_perception.runtime.pipeline import PerceptionPipeline

        clock = ctx.clock
        tracker = VisitorTracker(now_provider=clock)
        event_builder = VisitorEventBuilder(
            tracker, source_video=self.source_video, now_provider=clock
        )
        feature_extractor = FeatureExtractor(frequency_window_s=60.0)
        rule_engine = RuleEngine(
            device_id=self.device_id,
            location=self.location,
            thresholds=self.thresholds,
            now_provider=clock,
        )
        decision_engine = DecisionEngine(
            elder_id=self.elder_id,
            policy=RuleBasedDecisionPolicy(),
            now_provider=clock,
            trace_recorder=ctx.trace_recorder,  # 探针①：Decision
        )
        executor = ActionExecutor(
            dispatcher=ActionDispatcher(DispatcherConfig()),
            publisher=MockPublisher(),
            notifier=MockNotifier(),
            max_retries=1,
            sink=ctx.action_sink,  # 探针②：Action
        )
        # metrics 必须与 pipeline 共享同一实例：MemoryHook 在外部先建，若各持一份
        # metrics，落库计数会分裂到两个对象，审计口径不一致。
        metrics = PipelineMetrics()
        episode_builder = DefaultEpisodeBuilder()
        memory_hook = MemoryHook(
            episode_builder,
            ctx.memory_store,  # 探针③：Memory
            True,  # enabled：闭环验证必须落库，否则 Memory Stage 无从判定
            metrics,
            cross_modal_runtime=ctx.cross_modal_runtime,  # Phase A 恒 None
        )
        return PerceptionPipeline(
            detector=detector,
            tracker=tracker,
            event_builder=event_builder,
            feature_extractor=feature_extractor,
            rule_engine=rule_engine,
            decision_engine=decision_engine,
            executor=executor,
            metrics=metrics,
            now_provider=clock,
            frame_interval_s=self.config.frame_interval_s,
            memory_store=ctx.memory_store,
            episode_builder=episode_builder,
            episodic_shadow=True,
            memory_hook=memory_hook,
        )

    # ------------------------------------------------------------------ L3
    @staticmethod
    def _drive(pipeline: Any, synth: Any, ctx: IntegrationContext) -> list[Any]:
        """逐帧推进 pipeline，返回 ``FrameResult`` 序列。

        帧驱动语义与 ``ScenarioRunner.run`` **逐行对齐**（占位帧 / 时钟推进 / 帧序），
        唯一差异是本方法返回完整 ``FrameResult``（含 ``commands``）而非只抽事件。
        ``ScenarioRunner`` 若变更驱动语义，此处须同步（契约测试守护等价性）。
        """
        import numpy as np

        dummy = np.zeros((1, 1, 3), dtype=np.uint8)
        if synth.frames is not None:
            frames: list[Any] = list(synth.frames)
        else:
            frames = [dummy] * synth.n_frames

        clock = ctx.clock
        interval = getattr(pipeline, "_frame_interval_s", 0.0)
        tickable = clock is not None and hasattr(clock, "tick")

        results: list[Any] = []
        for i, frame in enumerate(frames):
            if interval > 0 and tickable:
                clock.tick(interval)
            results.append(pipeline.process_frame(frame, frame_index=i))
        return results

    # ------------------------------------------------------------------ L4 + L5
    def _collect(
        self, ctx: IntegrationContext, synth: Any, frame_results: list[Any]
    ) -> IntegrationRunResult:
        """从探针**只读**读回六类 artifacts 并封装为 ``IntegrationRunResult``。

        本方法不做任何判定 / 加工 / 兜底填充——"缺了什么"是 ``IntegrationValidator``
        的判断，Runner 只如实呈现（含"确实为空"这一事实）。
        """
        perception_events: list[Any] = []
        warnings: list[Any] = []
        commands: list[Any] = []
        for fr in frame_results:
            perception_events.extend(fr.perception_events)
            warnings.extend(fr.warnings)
            commands.extend(fr.commands)

        # 探针读回（fail-closed：读不回即报错，不伪装成下游 Drop）
        ctx.action_sink.flush()
        ctx.trace_recorder.flush()
        sink_commands = _read_sink_commands(ctx.action_sink)
        traces = _read_traces(ctx.trace_recorder)
        episodes = tuple(ctx.memory_store.all_episodic())

        run_result = RunResult(
            scenario_id=synth.scenario_id,
            mode=synth.mode,
            event_types={e.event_type for e in perception_events},
            risk_levels=[w.risk_level for w in warnings],
            perception_events=list(perception_events),
            warnings=list(warnings),
            fingerprint=synth.fingerprint,
        )
        return IntegrationRunResult(
            scenario_id=synth.scenario_id,
            mode=synth.mode,
            n_frames=len(frame_results),
            run_result=run_result,
            perception_events=tuple(perception_events),
            warnings=tuple(warnings),
            commands=tuple(commands),
            sink_commands=sink_commands,
            decision_traces=traces,
            episodes=episodes,
            cross_modal_links=(),  # Phase A 恒空
            fingerprint=synth.fingerprint,
            context=ctx,
        )

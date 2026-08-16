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
from datetime import UTC
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
    # P0-1：产品命题一句话（场景声明静态元数据，经 canonical → loader → Case Header）。
    product_question: str = ""
    perception_events: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()
    commands: tuple[Any, ...] = ()
    sink_commands: tuple[Any, ...] = ()
    decision_traces: tuple[Any, ...] = ()
    episodes: tuple[Any, ...] = ()
    cross_modal_links: tuple[Any, ...] = ()  # Phase A 恒空（Phase B 填充）
    # ADR-0036 VM-13 Phase C：真实音频感知符号（来自 Scenario.audio 经 compiler 编译的
    # AudioPerceptionEvent，确定性、感知层、无 UUID/墙钟）。Phase C 由 loader 投影进
    # canonical artifact 的 audio_evidence；此处仅如实携带，不做判定 / 兜底。
    audio_perception_events: tuple[Any, ...] = ()
    fingerprint: str = ""
    # ADR-0034 Phase B.3（D7）：两枚闭环指纹。默认空 = 向后兼容（Phase A/B.1/B.2
    # 构造不传时恒 ""，字段语义不变）。
    # - expectation_fingerprint：评价标准（IntegrationExpectationSuite）指纹，
    #   回答"用什么标准评价"；
    # - loop_fingerprint：本次闭环的输入+标准+装配六成分指纹，回答"这次怎么跑的"。
    expectation_fingerprint: str = ""
    loop_fingerprint: str = ""
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
        # G0-3：历史感知决策开关（golden opt-in）——仅当场景声明 prior 或 memory_aware 时
        # 注入 memory_store 并启用历史感知 policy；否则 DecisionEngine 无 memory_store
        # （Memory 可缺席，ADR-0030 D2），现有场景决策逐字不变。
        memory_aware = bool(getattr(scenario, "memory_aware", False))
        pipeline = self._assemble(
            ctx, synth.detector, memory_aware=memory_aware
        )  # L2 注入 runtime
        # G0-3：确定性身份桥——memory_aware 场景把 actor.id 预置为确定性 visitor_id
        # （uuid5(NS, actor.id)），使运行时 visitor 身份可预测、prior 历史可跨日匹配。
        if memory_aware:
            self._preset_visitor_identities(scenario, pipeline)
        # G0-3：历史记忆预置（prior_episodes → MemoryStore）。必须在驱动前完成，
        # 决策检索（get_episodic_by_visitor）才能命中历史；幂等（record_id 前缀防冲突）。
        if scenario.prior_episodes:
            self._seed_prior_episodes(ctx, scenario)
        # ADR-0034 Phase B.3（D7）：两枚闭环指纹。期望指纹只依赖场景声明的评价标准；
        # loop 指纹依赖 输入指纹(synth) + 策略指纹(pipeline) + 装配(config)，故须在
        # _assemble 之后计算（routing_table 已就位）。任一成分缺失即 raise（fail-closed）。
        from .fingerprint import (
            compute_expectation_fingerprint,
            compute_loop_fingerprint,
        )

        expectation_fp = compute_expectation_fingerprint(scenario.integration)
        loop_fp = compute_loop_fingerprint(
            synth.fingerprint,
            policy_fp=self._policy_fingerprint(pipeline),
            sink_type=self.config.sink_kind,
            memory_backend=self.config.memory_backend,
            cross_modal_enabled=self.config.cross_modal_enabled,
            expectation_fp=expectation_fp,
        )
        frame_results, audio_summary = self._drive(pipeline, synth, ctx)  # L3 执行 Scenario
        result = self._collect(  # L4 + L5
            ctx,
            synth,
            frame_results,
            audio_summary,
            expectation_fingerprint=expectation_fp,
            loop_fingerprint=loop_fp,
            # P0-1：产品命题一句话（场景声明静态元数据，随 canonical 投影到 Case Header）。
            product_question=getattr(scenario.meta, "product_question", ""),
        )
        return result

    # ------------------------------------------------------------------ G0-3
    @staticmethod
    def _golden_visitor_id(actor_id: str) -> str:
        """actor.id → 确定性 visitor UUID（uuid5，固定命名空间；golden 身份桥）。"""
        import uuid as _uuid

        return str(
            _uuid.uuid5(_uuid.UUID("9c5edd75-983c-fa66-df3f-4863e70cce38"), actor_id)
        )

    @staticmethod
    def _preset_visitor_identities(scenario: Scenario, pipeline: Any) -> None:
        """G0-3：memory_aware 场景预置确定性 visitor 身份（actor.id → uuid5 映射）。

        - track_id 规则与 ``validation.simulation.generator._assign_track_ids`` 一致
          （actor.id 排序序位 + 1），保证与编译产物 detections 的 track_id 对齐；
        - 预置后运行时该 actor 的 visitor_id == ``_golden_visitor_id(actor.id)``，
          prior_episodes 用同一 ID 声明即可跨日匹配（"同一访客"身份）。
        """
        if not scenario.actors:
            return
        import uuid as _uuid

        from home_perception.validation.simulation.generator import (
            _assign_track_ids,
        )

        track_map = _assign_track_ids(scenario.actors)
        event_builder = pipeline.event_builder
        for actor in scenario.actors:
            event_builder.preset_visitor_id(
                track_map[actor.id],
                _uuid.UUID(IntegrationRunner._golden_visitor_id(actor.id)),
            )

    def _seed_prior_episodes(self, ctx: IntegrationContext, scenario: Scenario) -> None:
        """G0-3：把 ``scenario.prior_episodes`` 预置进 MemoryStore（EpisodicRecord 形态）。

        - record_id = ``ep-prior-<episode_id>``（前缀防与运行时 episode 冲突，I1 幂等）；
        - enter/leave_time 由 ``event_time`` 与 ``duration_seconds`` 推导（确定性）；
        - prior ``visitor_id`` 若匹配场景 actor.id → 归一为 ``_golden_visitor_id``
          （与 ``_preset_visitor_identities`` 同一确定性身份，保证决策检索命中）；
        - 预置后决策检索（``get_episodic_by_visitor``）即可命中历史。
        """
        from datetime import datetime, timedelta

        from home_perception.core.event import EvidenceModality
        from home_perception.memory.records import EpisodicRecord

        actor_ids = {a.id for a in scenario.actors}
        for pe in scenario.prior_episodes:
            vid = (
                self._golden_visitor_id(pe.visitor_id)
                if pe.visitor_id in actor_ids
                else pe.visitor_id
            )
            enter = datetime.fromtimestamp(pe.event_time, tz=UTC)
            leave = enter + timedelta(seconds=max(pe.duration_seconds, 1.0))
            record = EpisodicRecord(
                record_id=f"ep-prior-{pe.episode_id}",
                visitor_instance_id=vid,
                enter_time=enter,
                leave_time=leave,
                duration_seconds=max(pe.duration_seconds, 1.0),
                source_event_ids=[f"prior:{pe.episode_id}"],
                summary=pe.summary or f"历史访问 {pe.episode_id}",
                model_version="prior-seed-v1",
                reason_summary=list(pe.reason_summary),
                risk_level=pe.risk_level,
                recommended_action=pe.recommended_action,
                device_id=pe.device_id,
                modalities=[EvidenceModality(str(m).lower()) for m in pe.modalities],
            )
            ctx.memory_store.upsert_episodic(record)

    # ------------------------------------------------------------------ L2
    @staticmethod
    def _policy_fingerprint(pipeline: Any) -> str:
        """决策策略指纹（loop_fingerprint 成分②，经 ``PolicyFingerprintProvider`` 协议）。

        ADR-0034 **不绑定** ``decision_engine.policy.routing_table`` 内部结构（评审 #4）：
        只依赖引擎公开的 ``policy_fingerprint()`` 协议方法（生产侧
        ``DecisionEngine`` 已实现）；协议不满足或返回空即 raise
        （fail-closed：指纹缺成分 = 无法复述"这次怎么跑的"，不静默降级）。
        """
        from .fingerprint import PolicyFingerprintProvider

        engine = getattr(pipeline, "decision_engine", None)
        if not isinstance(engine, PolicyFingerprintProvider):
            raise IntegrationConfigError(
                "decision_engine 不满足 PolicyFingerprintProvider 协议（缺 "
                "policy_fingerprint() 方法），无法计算 loop_fingerprint（fail-closed）"
            )
        fingerprint = engine.policy_fingerprint()
        if not fingerprint:
            raise IntegrationConfigError(
                "policy_fingerprint() 返回空，无法计算 loop_fingerprint（fail-closed）"
            )
        return fingerprint

    def _assemble(
        self, ctx: IntegrationContext, detector: Any, *, memory_aware: bool = False
    ) -> Any:
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
            policy=RuleBasedDecisionPolicy(memory_aware=memory_aware),
            now_provider=clock,
            trace_recorder=ctx.trace_recorder,  # 探针①：Decision
            # G0-3：仅 golden（memory_aware）场景注入 memory_store——历史检索装配
            # ReasoningInput（historical_context）；否则 None（Memory 可缺席，零行为变化）。
            memory_store=ctx.memory_store if memory_aware else None,
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
        # device_id（ADR-0028 D1）不在此传：MemoryHook 不接受构造期 device_id，
        # 而是在 record() 时透传——视觉路径由 pipeline.process_frame 取
        # rule_engine.device_id（本装配中 == self.device_id），音频路径由
        # AudioSessionRecorder 内部 _device_id 透传（见下）。两路同值，保证
        # 跨模态同设备关联（CrossModalLinker 共享 device_id 前置条件）。
        memory_hook = MemoryHook(
            episode_builder,
            ctx.memory_store,  # 探针③：Memory
            True,  # enabled：闭环验证必须落库，否则 Memory Stage 无从判定
            metrics,
            cross_modal_runtime=ctx.cross_modal_runtime,  # Phase B.2：启用时真实注入
        )

        # ADR-0034 Phase B.2：跨模态启用时，构造 AudioSessionRecorder 并挂到 pipeline。
        # 复用同一 decision_engine / executor / memory_hook（与视觉共享 MemoryStore +
        # cross_modal_runtime，落库后自动建边）；device_id 与视觉一致，保证同设备关联。
        # 未启用（cross_modal_enabled=False）则不挂，pipeline 行为与 Phase A 完全一致
        # （process_audio_session 因 _audio_recorder is None 直接返回 None，零行为变化）。
        if self.config.cross_modal_enabled:
            from home_perception.runtime.audio_session_recorder import (
                AudioSessionRecorder,
            )

            audio_recorder = AudioSessionRecorder(
                decision_engine=decision_engine,
                executor=executor,
                memory_hook=memory_hook,
                device_id=self.device_id,
                session_id_factory=lambda: "integration_audio_session",
                enabled=True,
            )
            pipeline = PerceptionPipeline(  # 占位，下行立即覆盖 _audio_recorder
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
            pipeline._audio_recorder = audio_recorder  # 与 from_settings 同款接线
            return pipeline

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
    def _drive(
        pipeline: Any, synth: Any, ctx: IntegrationContext
    ) -> tuple[list[Any], Any]:
        """逐帧推进 pipeline，返回 ``(FrameResult 序列, audio_summary)``。

        帧驱动语义与 ``ScenarioRunner.run`` **逐行对齐**（占位帧 / 时钟推进 / 帧序），
        唯一差异是本方法返回完整 ``FrameResult``（含 ``commands``）而非只抽事件。
        ``ScenarioRunner`` 若变更驱动语义，此处须同步（契约测试守护等价性）。

        ``audio_summary``（ADR-0034 Phase B.2）：音频会话结束后
        ``process_audio_session`` 的返回（未装配 / 无音频声明时为 ``None``）。
        音频是独立 Loop（ADR-0026 §8），其产物只出现在 summary、不进 FrameResult；
        ``_collect`` 据此把音频 warning/command 并入生产侧自报通道（F6 交叉校验）。
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

        # ADR-0034 Phase B.2：帧循环结束后，若存在音频会话声明且已装配 AudioSessionRecorder，
        # 驱动一次音频会话（独立 Audio Loop，不随视频帧同步，ADR-0026 §8）。音频事件经决策链
        # 产出纯音频 EpisodicRecord，与视觉 episode 共享 device_id + 时间窗重叠 → 自动建跨模态边。
        # 未装配（_audio_recorder is None，即 cross_modal_enabled=False）或无音频声明时跳过，
        # 与 Phase A 行为完全一致（零行为变化）。
        audio_summary: Any = None
        audio_recorder = getattr(pipeline, "_audio_recorder", None)
        if audio_recorder is not None and getattr(synth, "audio_events", None):
            audio_summary = pipeline.process_audio_session(
                list(synth.audio_events),
                audio_session_id="integration_audio_session",
            )
        return results, audio_summary

    # ------------------------------------------------------------------ L4 + L5
    def _collect(
        self,
        ctx: IntegrationContext,
        synth: Any,
        frame_results: list[Any],
        audio_summary: Any = None,
        *,
        expectation_fingerprint: str = "",
        loop_fingerprint: str = "",
        product_question: str = "",
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

        # ADR-0034 Phase B.2：音频 Loop 的产出并入**生产侧自报通道**。音频是独立通道
        # （ADR-0026 §8），其 warning/command 只出现在 AudioSessionSummary、不进
        # FrameResult；若不并入，F6 会把「探针（sink/trace）观测到、生产通道缺失」
        # 误判为 Observability Drop——尽管命令真实执行了、告警真实产出了。
        # 并入后 F6 三通道（sink↔commands、WARN trace↔warnings、episode↔commands）
        # 两侧同源，交叉校验恢复自洽。
        if audio_summary is not None:
            warnings.extend(getattr(audio_summary, "warnings", ()) or ())
            commands.extend(getattr(audio_summary, "commands", ()) or ())

        # 探针读回（fail-closed：读不回即报错，不伪装成下游 Drop）
        ctx.action_sink.flush()
        ctx.trace_recorder.flush()
        sink_commands = _read_sink_commands(ctx.action_sink)
        traces = _read_traces(ctx.trace_recorder)
        episodes = tuple(ctx.memory_store.all_episodic())

        # Phase B.2：cross_modal 关联边。启用时从 CrossModalLinkRuntime 只读读回真实
        # CrossModalLink（落库后由 MemoryHook 触发建边）；未启用则恒空（零行为变化）。
        cross_modal_links = (
            tuple(ctx.cross_modal_runtime.all_links())
            if ctx.cross_modal_runtime is not None
            else ()
        )

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
            cross_modal_links=cross_modal_links,  # Phase B.2：启用时真实读回
            # ADR-0036 VM-13 Phase C：携带真实音频符号（compiler 已确定性编译，感知层）。
            # 不依赖 AudioSessionSummary（其丢弃原始事件）；直接取 synth.audio_events，
            # 运行期确定性、无 UUID/墙钟，符合 canonical 的确定性约束。
            audio_perception_events=tuple(getattr(synth, "audio_events", ()) or ()),
            fingerprint=synth.fingerprint,
            expectation_fingerprint=expectation_fingerprint,  # Phase B.3（D7）
            loop_fingerprint=loop_fingerprint,  # Phase B.3（D7）
            product_question=product_question,  # P0-1 产品命题一句话
            context=ctx,
        )

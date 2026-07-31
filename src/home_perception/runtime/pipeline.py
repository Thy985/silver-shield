"""感知流水线装配器（P0-10 · 装配联调）。

> **P0-10 = 工程层问题（"怎么启动系统"），不验证逻辑正确性**
> —— 逻辑已由 P0 Integration Validation 充分验证（274 测试全绿）。
> 本文件只把已验证正确的组件装配成可运行 Demo。

链路（7 层，自上而下严格依赖，每层只读上一层）：
```
YOLODetector (P0-3)        → DetectionResult（视觉事实，带 track_id）
    ↓
VisitorTracker (P0-5)      → VisitorTrack（在场状态）
    ↓
VisitorEventBuilder (P0-6) → VisitorEvent（事实事件：谁/何时来走/停多久）
    ↓
FeatureExtractor (P0-7a)   → RiskFeature（数值特征）
    ↓
RuleEngine (P0-7b)         → PerceptionEvent（§7.2 5 类标签 + score）
    ↓
DecisionEngine (P0-8)      → WarningEvent（决策严重度 + 建议动作）
    ↓
ActionExecutor (P0-9)      → ActionCommand（MQTT / 通知 / 社区，MVP Mock）
```

装配边界（严守 ADR-0007~0012 分层铁律）：
- `PerceptionPipeline` 只做"调用顺序编排"，**不**在层间跳级、不读越层对象；
- 阈值/权重来自 `RuleConfig`（YAML），不在本文件硬编码；
- 行动层 MVP 用 `MockPublisher` / `MockNotifier`（Owner 决策：保持 Mock）；
- 每层**不**做最终判定（黑名单字段由各自领域对象 __post_init__ 守）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable
from uuid import uuid4

from ..action.dispatcher import ActionDispatcher
from ..action.executor import ActionExecutor
from ..action.notifier import MockNotifier
from ..action.publisher import MockPublisher
from ..analysis.behavior_builder import BehaviorBuilder
from ..analysis.behavior_state import BehaviorState, RealtimeContext
from ..analysis.decision_engine import DecisionEngine
from ..analysis.decision_policy import RuleBasedDecisionPolicy
from ..analysis.event import VisitorEvent
from ..analysis.event_builder import VisitorEventBuilder
from ..analysis.feature_extractor import FeatureExtractor
from ..analysis.perception import PerceptionEvent
from ..analysis.recent_behavior_store import RecentBehaviorStore
from ..analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
from ..analysis.risk_signal import RiskSignal, SignalTransition
from ..analysis.rule_engine import RuleEngine
from ..analysis.signal_adapter import risk_signal_to_perception
from ..analysis.warning import WarningEvent
from ..common.logging import get_logger
from ..core.config import MemoryConfig, Settings
from ..detection.detector import Detection, DetectionResult, YOLODetector
from ..detection.tracker import VisitorTracker
from ..memory.cold_start import ColdStartCoordinator
from ..memory.snapshot import RuntimeSnapshot, SnapshotStore
from ..memory import (
    DefaultEpisodeBuilder,
    InMemoryStore,
    InvariantViolationError,
    MemoryStore,
)
from .config import build_dispatcher_config, build_threshold_config
from .observability import PipelineMetrics

log = get_logger(__name__)


@runtime_checkable
class NowProvider(Protocol):
    """时序源协议：返回当前（模拟/真实）时间，供 tracker / event_builder / rule / decision 读取。

    仅要求可被调用 `()` 并返回 datetime；不要求可推进（真实墙钟、pytest 用 bound method 都满足）。
    """

    def __call__(self) -> datetime: ...


@runtime_checkable
class TickableNowProvider(NowProvider, Protocol):
    """可推进的时序源：在 NowProvider 基础上增加 ``tick(dt)``，供 run() 每帧推进模拟时间。

    Demo 用 DemoClock 实现此协议（复现视频帧率驱动 tracker 离场判定）；
    传入不可推进的 now_provider（如墙钟 / bound method）时 run() 静默跳过推进，不报错。
    """

    def tick(self, dt: Optional[float] = None) -> None: ...


class DemoClock:
    """Demo 用可控时钟：模拟视频帧率（如 2fps → 0.5s/帧），驱动 tracker 离场判定。

    真实视频有固定帧率与时间线；CAVIAR fixtures 是抽帧静态图，无真实帧时间戳。
    若不注入时钟，tracker 用墙钟，Demo 因处理太快（帧间毫秒级）几乎不会触发离场
    → 不生成 VisitorEvent、Demo 看起来"空跑"。注入 DemoClock 让每帧推进固定模拟时间，
    复现真实视频时序，使 Demo 确定可复现且能产出事件（与 P0-9 验证的链路一致）。

    仅作 Demo 时序源，不影响任何风险判定逻辑（组件仍只读 now_provider 返回的时间）。
    """

    def __init__(self, start: Optional[datetime] = None, interval_s: float = 0.5):
        if start is None:
            # 未显式传入起点 → 静默回退墙钟会破坏 Demo 确定性；告警以便发现配置问题（审查 #5）
            log.warning("demo_clock.start_unset_fallback_wallclock")
        self._t = start or datetime.now(timezone.utc)
        self.interval_s = interval_s

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        """使 DemoClock 可作为 `now_provider()` 直接调用（与组件约定一致）。"""
        return self.now()

    def tick(self, dt: Optional[float] = None) -> None:
        self._t = self._t + timedelta(seconds=dt if dt is not None else self.interval_s)


@dataclass
class FrameResult:
    """单帧处理结果（供编排层/监控消费，不跨层传递）。"""

    frame_index: int
    n_detections: int = 0
    n_visitor_events: int = 0
    perception_events: List[PerceptionEvent] = field(default_factory=list)
    warnings: List[WarningEvent] = field(default_factory=list)
    commands: List[Any] = field(default_factory=list)
    # —— Stage B 新增（默认空列表 = 向后兼容，flag 关闭时与基线逐字段一致）——
    # behavior_states 即实时观察（Observation）：每帧在场访客的纯实时快照，
    # 供 Demo Dashboard / 调试层展示"此刻门口正在发生什么"。Stage B 不产信号。
    behavior_states: List[BehaviorState] = field(default_factory=list)
    # —— Stage C 新增（默认空列表 = 向后兼容，flag 关闭时与基线逐字段一致）——
    # risk_signals 即实时风险跃迁（RAISED/CLEARED）：Shadow Mode 只进 FrameResult 供
    # Dashboard 展示"进行中风险"（RAISED 亮卡 / CLEARED 熄卡），**不接决策、不产 Warning**。
    # 接决策是 Stage D 的职责（经 signal_adapter 翻译为 PerceptionEvent 汇入 DecisionEngine）。
    risk_signals: List[RiskSignal] = field(default_factory=list)


@dataclass
class RunSummary:
    """一次运行的汇总（单场景 / 单视频源）。"""

    scenario: str
    interrupted: bool = False
    frames_processed: int = 0
    n_detections: int = 0
    n_visitor_events: int = 0
    n_perception: int = 0
    perception_by_type: Dict[str, int] = field(default_factory=dict)
    n_warnings: int = 0
    warnings_by_level: Dict[str, int] = field(default_factory=dict)
    n_commands: int = 0
    commands_by_type: Dict[str, int] = field(default_factory=dict)
    episodes_recorded: int = 0  # ADR-0024 Slice 5 · Stage F 影子写入落库计数
    publish_count: int = 0
    notify_family: int = 0
    notify_community: int = 0
    errors: int = 0
    duration_s: float = 0.0

    def to_log(self) -> Dict[str, Any]:
        """structlog-safe 字典。"""
        return {
            "scenario": self.scenario,
            "interrupted": self.interrupted,
            "frames_processed": self.frames_processed,
            "n_detections": self.n_detections,
            "n_visitor_events": self.n_visitor_events,
            "n_perception": self.n_perception,
            "perception_by_type": dict(self.perception_by_type),
            "n_warnings": self.n_warnings,
            "warnings_by_level": dict(self.warnings_by_level),
            "n_commands": self.n_commands,
            "commands_by_type": dict(self.commands_by_type),
            "episodes_recorded": self.episodes_recorded,
            "publish_count": self.publish_count,
            "notify_family": self.notify_family,
            "notify_community": self.notify_community,
            "errors": self.errors,
            "duration_s": self.duration_s,
        }


class PerceptionPipeline:
    """7 层感知链路装配器（P0-10 主对象）。

    用法：
        # 从配置装配（推荐）
        pipeline = PerceptionPipeline.from_settings(settings, device_id="CAVIAR/OneStopEnter1cor")
        pipeline.load_detector()                      # 懒加载 YOLO 权重
        summary = pipeline.run(frames, scenario=...)  # frames: List[np.ndarray]
        pipeline.close()

        # 或直接提供已构造的 detector（复用同一实例保证 track_id 跨帧一致）
        pipeline = PerceptionPipeline(
            detector=det, tracker=..., event_builder=..., feature_extractor=...,
            rule_engine=..., decision_engine=..., executor=...,
        )
    """

    def __init__(
        self,
        *,
        detector: YOLODetector,
        tracker: VisitorTracker,
        event_builder: VisitorEventBuilder,
        feature_extractor: FeatureExtractor,
        rule_engine: RuleEngine,
        decision_engine: DecisionEngine,
        executor: ActionExecutor,
        metrics: Optional[PipelineMetrics] = None,
        now_provider: Optional[NowProvider] = None,
        frame_interval_s: float = 0.0,
        # —— Stage B 实时旁路组件（可选；flag 关闭时全 None，零运行时开销）——
        behavior_builder: Optional[BehaviorBuilder] = None,
        recent_behavior_store: Optional[RecentBehaviorStore] = None,
        # —— Stage C 实时评估器（可选；flag 关闭时 None，Shadow Mode 不接决策）——
        realtime_evaluator: Optional[RealTimeRiskEvaluator] = None,
        realtime_enabled: bool = False,
        eval_interval_frames: int = 1,
        # —— Stage D 决策接入开关（可选；默认 false，Shadow Mode 不产 Warning）——
        decision_enabled: bool = False,
        # —— ADR-0024 Slice 3：Snapshot Recovery（Stage C + Stage E，解 TD-0027）——
        # 可选；memory_config=None 或 enabled=False 时不触发，行为与基线逐字段一致。
        # 开启时需要 realtime 旁路组件（evaluator/store）已存在，否则降级为纯冷启动无效。
        memory_config: Optional[MemoryConfig] = None,
        # —— ADR-0024 Slice 5：Episodic Memory Store + Stage F 影子写入（可选；
        #    默认全 None/false，零运行时开销；仅 memory.enabled + episodic_shadow 时激活）——
        memory_store: Optional[MemoryStore] = None,
        episode_builder: Optional[DefaultEpisodeBuilder] = None,
        episodic_shadow: bool = False,
    ):
        self.detector = detector
        self.tracker = tracker
        self.event_builder = event_builder
        self.feature_extractor = feature_extractor
        self.rule_engine = rule_engine
        self.decision_engine = decision_engine
        self.executor = executor
        self.metrics = metrics or PipelineMetrics()
        # Demo 时序源（now_provider 即 tick 对象）；frame_interval_s>0 时 run() 每帧推进
        self._clock = now_provider
        self._frame_interval_s = frame_interval_s
        # 实时旁路（Stage B/C）：flag 关闭时组件为 None，process_frame 跳过旁路块
        self._behavior_builder = behavior_builder
        self._recent_behavior_store = recent_behavior_store
        self._realtime_evaluator = realtime_evaluator
        self._realtime_enabled = realtime_enabled
        self._eval_interval_frames = max(1, int(eval_interval_frames))
        # Stage D 决策接入：true 时 RAISED 信号经 adapter 汇入 DecisionEngine 产 Warning。
        # 灰度策略：先 enabled=true, decision_enabled=false 观察误报率，再开决策。
        # decision_enabled=true 隐含要求 realtime_enabled=true（关闭态下此开关无意义）。
        self._decision_enabled = bool(decision_enabled)
        if realtime_enabled and eval_interval_frames > 1:
            log.warning(
                "pipeline.realtime_eval_throttled",
                eval_interval_frames=eval_interval_frames,
                note="RAISED/CLEARED 最坏延迟约 N×帧间隔",
            )
        if decision_enabled and not realtime_enabled:
            log.warning(
                "pipeline.decision_enabled_without_realtime",
                note="decision_enabled=true 但 realtime_enabled=false，决策开关无意义",
            )

        # —— ADR-0024 Slice 3：Snapshot Recovery 接线（Stage C + Stage E）——
        # memory_config 由 Settings.memory 注入。effective 激活需 enabled 且 realtime
        # 旁路组件已就位（from_settings 在 memory 开启时会连带开启 realtime 装配）。
        self._memory_config = memory_config
        self._snapshot_store: Optional[SnapshotStore] = None
        self._cold_start_coordinator: Optional[ColdStartCoordinator] = None
        self._last_snapshot_at: Optional[datetime] = None
        if memory_config is not None and memory_config.enabled:
            if self._realtime_evaluator is not None and self._recent_behavior_store is not None:
                self._snapshot_store = SnapshotStore(Path(memory_config.snapshot_path))
                self._cold_start_coordinator = ColdStartCoordinator(
                    self._snapshot_store,
                    self._realtime_evaluator,
                    self._recent_behavior_store,
                    memory_config,
                )
                # 启动期立即冷启动恢复（解 TD-0027 进程重启状态恢复）
                result = self._cold_start_coordinator.recover(self._now())
                log.info("pipeline.cold_start_recover", **result.as_log_fields())
            else:
                log.warning(
                    "pipeline.memory_enabled_without_realtime",
                    note="memory.enabled=true 但 realtime 旁路组件缺失，已跳过 Snapshot/恢复",
                )

        # —— ADR-0024 Slice 5：Episodic Memory Store（Stage F 影子写入后端）——
        # memory_store 由 from_settings 在 episodic_shadow 激活时注入 InMemoryStore；
        # episode_builder 投影 VisitorEvent → EpisodicRecord；episodic_shadow 为运行期开关。
        self._memory_store = memory_store
        self._episode_builder = episode_builder
        self._episodic_shadow = bool(episodic_shadow)
        if self._episodic_shadow and (
            self._memory_store is None or self._episode_builder is None
        ):
            log.warning(
                "pipeline.episodic_shadow_without_store",
                note="episodic_shadow=true 但 store/episode_builder 缺失，影子写入静默跳过",
            )

    # ------------------------------------------------------------------
    # 从配置装配
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        detector: Optional[YOLODetector] = None,
        device_id: str = "home_entry_01",
        location: str = "入户门",
        elder_id: str = "elder_001",
        now_provider: Optional[NowProvider] = None,
        frame_interval_s: float = 0.0,
    ) -> "PerceptionPipeline":
        """从 `Settings` 构造完整流水线（各组件按 YAML 配置装配）。

        `detector` 可复用同一 `YOLODetector` 实例跨场景（保证 track_id 跨帧一致，
        `model.track(persist=True)` 要求同一实例）。`device_id` 在 demo 模式传入场景名。
        """
        det = detector or YOLODetector(
            model=settings.runtime.detector_model or settings.detection.model,
            conf_threshold=settings.runtime.detector_conf or settings.detection.conf_threshold,
            classes=settings.detection.classes,
            device=settings.detection.device,
            imgsz=settings.runtime.detector_imgsz or settings.detection.imgsz,
            profile=settings.detection.imgsz_profile,
            enable_track=settings.detection.enable_track,
            tracker=settings.detection.tracker,
        )
        tracker = VisitorTracker(
            absence_gap_s=settings.detection.tracking.absence_gap_s,
            now_provider=now_provider,
        )
        event_builder = VisitorEventBuilder(tracker, source_video=device_id, now_provider=now_provider)
        feature_extractor = FeatureExtractor(frequency_window_s=settings.rule.frequency_window_s)
        rule_engine = RuleEngine(
            device_id=device_id,
            location=location,
            thresholds=build_threshold_config(settings.rule),
            now_provider=now_provider,
        )
        decision_engine = DecisionEngine(
            elder_id=elder_id,
            policy=RuleBasedDecisionPolicy(),
            now_provider=now_provider,
        )
        dispatcher_config = build_dispatcher_config(settings.action)
        dispatcher = ActionDispatcher(dispatcher_config)
        publisher = MockPublisher(output_path=settings.action.mock_publisher_output)
        notifier = MockNotifier()
        executor = ActionExecutor(
            dispatcher=dispatcher,
            publisher=publisher,
            notifier=notifier,
            max_retries=settings.action.max_retries,
        )
        # —— Stage B/C 实时旁路装配 ——
        # flag 关闭时不构造组件（零运行时开销）；开启时构造 BehaviorBuilder +
        # RecentBehaviorStore + RealTimeRiskEvaluator，挂入 process_frame 旁路块。
        # 阈值复用 settings.rule（单一阈值来源，工程方案 §5.1）：同一 ThresholdConfig
        # 实例喂 RuleEngine 与 RealTimeRiskEvaluator，改一处 YAML 两路同时生效。
        # Memory（ADR-0024 Slice 3）需要实时旁路组件才能持久化/恢复，故 memory 开启时
        # 连带开启 realtime 装配（Shadow Mode，decision_enabled 仍按配置，默认关闭）。
        realtime_enabled = settings.realtime_risk.enabled or settings.memory.enabled
        eval_interval = settings.realtime_risk.eval_interval_frames
        decision_enabled = settings.realtime_risk.decision_enabled
        behavior_builder: Optional[BehaviorBuilder] = None
        recent_behavior_store: Optional[RecentBehaviorStore] = None
        realtime_evaluator: Optional[RealTimeRiskEvaluator] = None
        if realtime_enabled:
            thresholds = build_threshold_config(settings.rule)
            behavior_builder = BehaviorBuilder(event_builder=event_builder)
            recent_behavior_store = RecentBehaviorStore()
            realtime_evaluator = RealTimeRiskEvaluator(
                thresholds=thresholds,
                now_provider=now_provider,
            )
        if settings.memory.enabled and not settings.realtime_risk.enabled:
            log.info(
                "pipeline.memory_implicitly_enables_realtime",
                note="memory.enabled=true 但 realtime_risk.enabled=false，已连带开启实时旁路（Shadow Mode）",
            )
        # —— ADR-0024 Slice 5：Episodic Memory Store + Stage F 影子写入接线 ——
        # memory.enabled 为 Memory 子系统总开关（含 Slice 3 Snapshot Recovery）；
        # episodic_shadow 为 Stage F 独立子开关（默认 off）：仅当二者同时为真才构造
        # InMemoryStore + DefaultEpisodeBuilder 并开启影子写入。Shadow Mode 只记录
        # EpisodicRecord，不接决策、不产 Warning。
        memory_store: Optional[MemoryStore] = None
        episode_builder: Optional[DefaultEpisodeBuilder] = None
        episodic_shadow = False
        if settings.memory.enabled:
            if settings.memory.episodic_shadow:
                memory_store = InMemoryStore()
                episode_builder = DefaultEpisodeBuilder()
                episodic_shadow = True
            else:
                log.info(
                    "pipeline.memory_snapshot_only",
                    note="memory.enabled=true 但 episodic_shadow=false，仅 Snapshot Recovery 激活",
                )
        elif settings.memory.episodic_shadow:
            log.warning(
                "pipeline.episodic_shadow_requires_memory",
                note="episodic_shadow=true 但 memory.enabled=false，影子模式未激活",
            )
        return cls(
            detector=det,
            tracker=tracker,
            event_builder=event_builder,
            feature_extractor=feature_extractor,
            rule_engine=rule_engine,
            decision_engine=decision_engine,
            executor=executor,
            metrics=PipelineMetrics(),
            now_provider=now_provider,
            frame_interval_s=frame_interval_s,
            behavior_builder=behavior_builder,
            recent_behavior_store=recent_behavior_store,
            realtime_evaluator=realtime_evaluator,
            realtime_enabled=realtime_enabled,
            eval_interval_frames=eval_interval,
            decision_enabled=decision_enabled,
            memory_config=settings.memory,
            memory_store=memory_store,
            episode_builder=episode_builder,
            episodic_shadow=episodic_shadow,
        )

    # ------------------------------------------------------------------
    # 运行期
    # ------------------------------------------------------------------

    def load_detector(self) -> None:
        """加载 YOLO 权重（懒加载，构造期不触发 torch 导入）。"""
        if hasattr(self.detector, "load"):
            self.detector.load()

    # ------------------------------------------------------------------
    # ADR-0024 Slice 3：Snapshot Recovery 辅助（Stage C 写入路径）
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """当前时刻：优先 now_provider（Demo 模拟时钟），否则墙钟 UTC。"""
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc)

    def _save_snapshot(self, now: datetime) -> None:
        """把当前实时状态写入 JSON snapshot（原子写，解 TD-0027 重启恢复）。"""
        if (
            self._snapshot_store is None
            or self._realtime_evaluator is None
            or self._recent_behavior_store is None
        ):
            return
        snap = RuntimeSnapshot(
            snapshot_id=str(uuid4()),
            snapshot_at=now,
            active_tracks=self._realtime_evaluator.snapshot(now),
            recent_behavior=self._recent_behavior_store.snapshot(),
        )
        self._snapshot_store.save(snap)

    def _maybe_save_snapshot(self, now: datetime) -> None:
        """周期快照：距上次写入 >= snapshot_interval_seconds 才写（默认 30s）。"""
        if self._snapshot_store is None or self._memory_config is None:
            return
        interval = self._memory_config.snapshot_interval_seconds
        if (
            self._last_snapshot_at is None
            or (now - self._last_snapshot_at).total_seconds() >= interval
        ):
            self._save_snapshot(now)
            self._last_snapshot_at = now

    # ------------------------------------------------------------------
    # ADR-0024 Slice 5：Stage F Episodic Memory 影子写入（Shadow Mode）
    # ------------------------------------------------------------------

    def _record_episode(
        self,
        ev: "VisitorEvent",
        warnings: List["WarningEvent"],
        actions: List[Any],
    ) -> None:
        """把一次访客离场投影为 EpisodicRecord 并写入 MemoryStore（Shadow Mode）。

        触发时机：``process_frame`` 中每个 ``VisitorEvent`` 产出后立即调用（含其关联的
        ``warnings`` / ``actions``）。影子写入**只记录、不接决策、不产 Warning**，
        因此开启 ``episodic_shadow`` 不会改变流水线任何历史行为（工程方案 §8.3 合入门）。

        容错（AGENTS.md §2.5：记忆写入失败不崩溃主链路）：
        - 投影异常 / 落库未知异常 → 计 ``errors`` + 记日志，跳过本 episode；
        - ``InvariantViolationError``（I2 单调性：字段冲突）→ 防御性告警，不计入 errors。
        """
        if self._episode_builder is None or self._memory_store is None:
            return
        try:
            record = self._episode_builder.project_episode(ev, warnings, actions)
        except Exception:  # 投影失败（理论上 DefaultEpisodeBuilder 为纯函数不应抛）
            self.metrics.errors += 1
            log.exception(
                "pipeline.episode_build_failed",
                event_id=getattr(ev, "event_id", None),
            )
            return
        if record is None:
            return
        try:
            self._memory_store.upsert_episodic(record)
        except InvariantViolationError as exc:
            # I2 单调保护：字段冲突属防御性告警，不计入 errors（不崩溃流水线）
            log.warning("pipeline.episode_invariant_violation", error=str(exc))
            return
        except Exception:
            self.metrics.errors += 1
            log.exception(
                "pipeline.episode_store_failed", record_id=record.record_id
            )
            return
        self.metrics.episodes_recorded += 1

    def process_frame(self, frame: "object", frame_index: int = 0) -> FrameResult:
        """处理单帧：detector → tracker → builder → feature → rule → decision → action。

        任何阶段的异常（除 KeyboardInterrupt）都被捕获并计入 metrics.errors，
        不中断整条流水线（AGENTS.md §2.5：业务失败走事件/状态，不崩溃进程）。
        """
        self.metrics.frames_processed += 1
        self.metrics.detection_calls += 1
        try:
            result: DetectionResult = self.detector.detect(frame)
        except Exception:  # 检测器异常：记日志（保留 traceback）+ 计数，跳过本帧
            self.metrics.errors += 1
            log.exception("pipeline.detect_failed", frame_index=frame_index)
            return FrameResult(frame_index=frame_index, n_detections=0, n_visitor_events=0)

        dets: List[Detection] = result.detections
        self.metrics.detections_total += len(dets)

        events: List[VisitorEvent] = self.event_builder.update(dets)
        perception_events: List[PerceptionEvent] = []
        warnings: List[WarningEvent] = []
        commands: List[Any] = []

        for ev in events:
            self.metrics.visitor_events += 1
            # 抽出的下游链路（feature→rule→decision→action）见 _act_on_event，降低本函数嵌套层级
            percs, ev_warnings, cmds = self._act_on_event(ev)
            perception_events.extend(percs)
            warnings.extend(ev_warnings)
            commands.extend(cmds)
            # —— ADR-0024 Slice 5：Stage F Episodic Memory 影子写入 ——
            # 仅记录本次访客离场的投影（含其已产出的 warning/action），
            # 不接决策、不产 Warning，开启影子开关不改变任何历史行为。
            if self._episodic_shadow:
                self._record_episode(ev, ev_warnings, cmds)

        # —— Stage B/C 实时旁路（feature flag 控制；关闭时零开销，行为与基线逐字段一致）——
        # Stage B：BehaviorBuilder 纯函数 + RecentBehaviorStore 跨访问账本 → behavior_states（观察）
        # Stage C：RealTimeRiskEvaluator 状态机 → risk_signals（Shadow Mode：只进 FrameResult，
        #          不接 DecisionPolicy、不产 Warning；接决策是 Stage D 的职责）
        # 工程方案 §3.1 步骤 4 + §5.3 跳帧对称：RAISED/CLEARED 仅在评估帧发生，延迟对称
        behavior_states: List[BehaviorState] = []
        risk_signals: List[RiskSignal] = []
        if self._realtime_enabled and self._behavior_builder is not None:
            is_eval_frame = (frame_index % self._eval_interval_frames) == 0
            if is_eval_frame:
                now = self._clock() if self._clock is not None else datetime.now(timezone.utc)
                active_tracks = self.tracker.active()
                behavior_states = self._behavior_builder.build(active_tracks, now)

                # 构造 RealtimeContext 列表（BehaviorState + recent_behavior）喂给评估器
                # 工程方案 §3.3：State 与 History 在此组合，评估器只读不写
                # 注意：即使 behavior_states 为空（主体全部离场），也要调用 evaluate([])
                # 让评估器走 missing_ids 路径补发 CLEARED（离场兜底，工程方案 §4.2 规则 2）
                if (
                    self._recent_behavior_store is not None
                    and self._realtime_evaluator is not None
                ):
                    window_s = self.feature_extractor.frequency_window_s
                    # 建立 track_id → enter_time 映射（active_tracks 与 behavior_states 同序）
                    enter_time_map = {
                        vt.track_id: vt.enter_time
                        for vt in active_tracks
                        if vt.enter_time is not None
                    }
                    ctxs: List[RealtimeContext] = []
                    for state in behavior_states:
                        enter_time = enter_time_map.get(state.track_id)
                        if enter_time is None:
                            continue
                        # 先更新账本（记录本次进入），再取只读快照组合进 ctx
                        recent = self._recent_behavior_store.update(
                            state.visitor_instance_id, enter_time, now, window_s
                        )
                        ctxs.append(RealtimeContext(
                            current_state=state,
                            recent_behavior=dict(recent),  # 解 MappingProxyType 为普通 dict
                        ))
                    # 评估器消费 ctxs 产出 signals（RAISED + CLEARED）
                    # ctxs 为空时评估器走 missing_ids 路径补发 CLEARED（离场兜底）
                    risk_signals = self._realtime_evaluator.evaluate(ctxs, now)

                    # —— Stage D：决策接入（feature flag 控制；默认关闭 = Shadow Mode）——
                    # RAISED 信号经 signal_adapter 翻译为 PerceptionEvent 汇入同一
                    # DecisionEngine 产 WarningEvent；CLEARED 不进决策（仅随 FrameResult
                    # 供展示层熄灭风险卡）。工程方案 §3.1 步骤 4 + §6 单一决策中心。
                    if self._decision_enabled and risk_signals:
                        rt_percs, rt_warnings, rt_cmds = self._act_on_signals(
                            risk_signals, now
                        )
                        perception_events.extend(rt_percs)
                        warnings.extend(rt_warnings)
                        commands.extend(rt_cmds)

                    # 周期快照（Stage C 持久化；冷启动恢复用）。仅当 memory 激活时。
                    if self._snapshot_store is not None:
                        self._maybe_save_snapshot(now)

        return FrameResult(
            frame_index=frame_index,
            n_detections=len(dets),
            n_visitor_events=len(events),
            perception_events=perception_events,
            warnings=warnings,
            commands=commands,
            behavior_states=behavior_states,
            risk_signals=risk_signals,
        )

    def _act_on_event(
        self, ev: VisitorEvent
    ) -> Tuple[List[PerceptionEvent], List[WarningEvent], List[Any]]:
        """处理单个 VisitorEvent 的下游链路（feature → rule → decision → action）。

        从 process_frame 抽取，避免 4 层嵌套；返回本事件产出的感知事件 / 告警 / 行动指令。
        无感知（规则未命中）或（命中但）决策层不产出告警时返回对应空列表，调用方安全 extend。
        """
        risk = self.feature_extractor.extract(ev)
        percs = self.rule_engine.evaluate(risk)
        if not percs:
            return [], [], []
        for p in percs:
            self.metrics.record_perception(p.event_type)
        warnings: List[WarningEvent] = []
        commands: List[Any] = []
        w = self.decision_engine.evaluate(percs)
        if w is not None:
            self.metrics.record_warning(w.risk_level)
            warnings.append(w)
            cmds = self.executor.execute(w)
            commands.extend(cmds)
            for c in cmds:
                self.metrics.record_command(c.command_type)
        return percs, warnings, commands

    def _act_on_signals(
        self, signals: List[RiskSignal], now: datetime
    ) -> Tuple[List[PerceptionEvent], List[WarningEvent], List[Any]]:
        """Stage D：处理实时信号的下游链路（adapter → decision → action）。

        与 ``_act_on_event`` 平行（不重构既有逐事件循环，0 行为变化）：
        - ``RAISED`` 信号 → ``signal_adapter`` 翻译为 ``PerceptionEvent`` →
          汇入同一 ``DecisionEngine.evaluate`` → 产 ``WarningEvent`` →
          ``executor.execute`` 产 ``ActionCommand``；
        - ``CLEARED`` 信号 → 不产出 ``PerceptionEvent``（``signal_adapter`` 返回 None），
          仅随 ``FrameResult.risk_signals`` 供展示层熄灭风险卡（工程方案 §3.1 步骤 4）。

        单一决策中心（工程方案 §6 检查清单第 4 条）：本方法**不**新建决策器，
        复用 ``self.decision_engine``——同一 ``DecisionPolicy`` 解释历史与实时两路
        PerceptionEvent，避免双决策中心漂移。

        参数：
        - ``signals``：评估器本帧产出的 RiskSignal 列表（含 RAISED + CLEARED）
        - ``now``：当前时刻（用于 device_id 透传，与历史路径同源）

        返回：(perception_events, warnings, commands)，调用方安全 extend。
        """
        # 1) 翻译：RAISED → PerceptionEvent；CLEARED → None（跳过）
        rt_percs: List[PerceptionEvent] = []
        for sig in signals:
            if sig.transition is not SignalTransition.RAISED:
                continue  # CLEARED 不进决策
            perc = risk_signal_to_perception(
                sig,
                device_id=self.rule_engine.device_id,
                location=self.rule_engine.location,
            )
            if perc is not None:
                rt_percs.append(perc)
        if not rt_percs:
            return [], [], []

        for p in rt_percs:
            self.metrics.record_perception(p.event_type)

        # 2) 决策：复用同一 DecisionEngine（单一决策中心）
        warnings: List[WarningEvent] = []
        commands: List[Any] = []
        w = self.decision_engine.evaluate(rt_percs)
        if w is not None:
            self.metrics.record_warning(w.risk_level)
            warnings.append(w)
            cmds = self.executor.execute(w)
            commands.extend(cmds)
            for c in cmds:
                self.metrics.record_command(c.command_type)
        return rt_percs, warnings, commands

    def run(self, frames: List["object"], scenario: str = "unknown") -> RunSummary:
        """处理一整个帧序列（单场景 / 单视频源），返回汇总。

        支持优雅中断：捕获 KeyboardInterrupt，停止处理并仍返回已处理部分的汇总。
        """
        self.metrics.start()
        interrupted = False
        for i, frame in enumerate(frames):
            # Demo：每帧推进模拟时间（复现视频帧率，驱动 tracker 离场判定）
            # 仅对实现 TickableNowProvider 的时钟推进；不可推进的 now_provider 静默跳过
            if self._frame_interval_s > 0 and isinstance(self._clock, TickableNowProvider):
                self._clock.tick(self._frame_interval_s)
            try:
                self.process_frame(frame, frame_index=i)
            except KeyboardInterrupt:
                interrupted = True
                log.info("pipeline.interrupted", frame_index=i, scenario=scenario)
                break
        self.metrics.stop()
        return RunSummary(
            scenario=scenario,
            interrupted=interrupted,
            frames_processed=self.metrics.frames_processed,
            n_detections=self.metrics.detections_total,
            n_visitor_events=self.metrics.visitor_events,
            n_perception=self.metrics.perception_events,
            perception_by_type=dict(self.metrics.perception_by_type),
            n_warnings=self.metrics.warnings,
            warnings_by_level=dict(self.metrics.warnings_by_level),
            n_commands=self.metrics.commands,
            commands_by_type=dict(self.metrics.commands_by_type),
            episodes_recorded=self.metrics.episodes_recorded,
            publish_count=self.executor.publisher.publish_count,
            notify_family=self.executor.notifier.family_count,
            notify_community=self.executor.notifier.community_count,
            errors=self.metrics.errors,
            duration_s=self.metrics.elapsed_s,
        )

    def close(self) -> None:
        """释放资源（模型显存 / 内存）。detector 实例可复用，这里只置空内部模型。"""
        # 优雅退出：flush 最终 snapshot（Stage C 持久化，冷启动恢复用）
        if self._snapshot_store is not None:
            self._save_snapshot(self._now())
        det = self.detector
        if hasattr(det, "unload"):
            det.unload()

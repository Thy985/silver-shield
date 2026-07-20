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
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from ..action.dispatcher import ActionDispatcher
from ..action.executor import ActionExecutor
from ..action.notifier import MockNotifier
from ..action.publisher import MockPublisher
from ..analysis.decision_engine import DecisionEngine
from ..analysis.decision_policy import RuleBasedDecisionPolicy
from ..analysis.event import VisitorEvent
from ..analysis.event_builder import VisitorEventBuilder
from ..analysis.feature_extractor import FeatureExtractor
from ..analysis.perception import PerceptionEvent
from ..analysis.rule_engine import RuleEngine
from ..analysis.warning import WarningEvent
from ..common.logging import get_logger
from ..core.config import Settings
from ..detection.detector import Detection, DetectionResult, YOLODetector
from ..detection.tracker import VisitorTracker
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
        )

    # ------------------------------------------------------------------
    # 运行期
    # ------------------------------------------------------------------

    def load_detector(self) -> None:
        """加载 YOLO 权重（懒加载，构造期不触发 torch 导入）。"""
        if hasattr(self.detector, "load"):
            self.detector.load()

    def process_frame(self, frame: "object", frame_index: int = 0) -> FrameResult:
        """处理单帧：detector → tracker → builder → feature → rule → decision → action。

        任何阶段的异常（除 KeyboardInterrupt）都被捕获并计入 metrics.errors，
        不中断整条流水线（AGENTS.md §2.5：业务失败走事件/状态，不崩溃进程）。
        """
        self.metrics.frames_processed += 1
        self.metrics.detection_calls += 1
        try:
            result: DetectionResult = self.detector.detect(frame)
        except Exception as exc:  # 检测器异常：记日志 + 计数，跳过本帧
            self.metrics.errors += 1
            log.error("pipeline.detect_failed", frame_index=frame_index, error=str(exc))
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

        return FrameResult(
            frame_index=frame_index,
            n_detections=len(dets),
            n_visitor_events=len(events),
            perception_events=perception_events,
            warnings=warnings,
            commands=commands,
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
            publish_count=self.executor.publisher.publish_count,
            notify_family=self.executor.notifier.family_count,
            notify_community=self.executor.notifier.community_count,
            errors=self.metrics.errors,
            duration_s=self.metrics.elapsed_s,
        )

    def close(self) -> None:
        """释放资源（模型显存 / 内存）。detector 实例可复用，这里只置空内部模型。"""
        det = self.detector
        if hasattr(det, "unload"):
            det.unload()

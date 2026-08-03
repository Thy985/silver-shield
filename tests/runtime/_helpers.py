"""Memory / 闭环测试公共辅助件（DRY 单一来源）。

被 ``test_memory_e2e_closed_loop.py`` 与 ``test_memory_closure_slice_b.py`` 共同 import，
避免两套逐行相同的 ``ManualClock`` / ``TH_HIGH`` / ``memory_config`` / ``build_full_pipeline``
/ ``drive`` 漂移。两个测试各自保留其 detector 专属件：

- E2E：``SteppingStubDetector``（内联 plan，绕过 detection/tracking）
- Slice B：``CachedDetectionDetector`` / ``load_cached_detections``（见 ``_closed_loop_helpers.py``）

> 注意：本模块依赖 pytest 默认 ``--import-mode=prepend``（测试文件所在目录 ``tests/runtime``
> 被加入 ``sys.path``），故 ``from _helpers import ...`` 可用。若日后切到 ``importlib`` 模式
> 需改 rootdir / conftest 暴露。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.behavior_builder import BehaviorBuilder
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
from home_perception.analysis.recent_behavior_store import RecentBehaviorStore
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.core.config import MemoryConfig
from home_perception.detection.tracker import VisitorTracker
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.memory.consumer import MemoryConsumer
from home_perception.runtime import PerceptionPipeline


class ManualClock:
    """可控时钟：now() 返回当前时间，advance() 推进。"""

    def __init__(self, base: datetime | None = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


def TH_HIGH() -> ThresholdConfig:
    """让一次访问同时命中三条基础规则 → 组合规则产 high_risk_approach → HIGH。

    - long_duration_seconds=60：停留 >=60s 即触发（生命周期里远超限）；
    - repeat_visit_count=1：单次访问 visits_in_window=1 即命中；
    - odd_hour_set={18}：访问发生在 18 点（UTC），命中 OddHourRule。
    """
    return ThresholdConfig(
        long_duration_seconds=60.0,
        repeat_visit_count=1,
        odd_hour_set={18},
    )


def memory_config(path, **over) -> MemoryConfig:
    base = {
        "enabled": True,
        "snapshot_path": str(path),
        "snapshot_interval_seconds": 1.0,
        "snapshot_fresh_threshold_seconds": 30.0,
        "snapshot_ttl_seconds": 300.0,
        "recent_behavior_retention_seconds": 3600.0,
        "eviction_interval_frames": 60,
        "cold_start_stale_confidence": 0.5,
    }
    base.update(over)
    return MemoryConfig(**base)


def build_full_pipeline(
    detector,
    clock: ManualClock,
    *,
    thresholds: ThresholdConfig | None = None,
    memory_store: InMemoryStore | None = None,
    episode_builder: DefaultEpisodeBuilder | None = None,
    episodic_shadow: bool = False,
    memory_config: MemoryConfig | None = None,
    decision_enabled: bool = False,
    eval_interval_frames: int = 1,
    realtime_enabled: bool = True,
    memory_consumer: MemoryConsumer | None = None,
    consumer_enabled: bool = False,
    reasoning_engine: object | None = None,
    reasoning_enabled: bool = False,
):
    """构造完整 PerceptionPipeline（实时旁路 + 可选 Memory 影子写入 + 可选快照恢复）。

    统一入口：E2E 与 Slice B 共用，避免构造签名变更要改两处。
    """
    th = thresholds or ThresholdConfig()
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test",
        location="入户门",
        thresholds=th,
        now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(),
        now_provider=clock,
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher,
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=3,
    )
    behavior_builder = BehaviorBuilder(event_builder=event_builder)
    recent_store = RecentBehaviorStore()
    evaluator = RealTimeRiskEvaluator(thresholds=th, now_provider=clock)
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feat,
        rule_engine=rule_engine,
        decision_engine=decision,
        executor=executor,
        now_provider=clock,
        behavior_builder=behavior_builder,
        recent_behavior_store=recent_store,
        realtime_evaluator=evaluator,
        realtime_enabled=realtime_enabled,
        eval_interval_frames=eval_interval_frames,
        decision_enabled=decision_enabled,
        memory_store=memory_store,
        episode_builder=episode_builder,
        episodic_shadow=episodic_shadow,
        memory_config=memory_config,
        memory_consumer=memory_consumer,
        consumer_enabled=consumer_enabled,
        reasoning_engine=reasoning_engine,
        reasoning_enabled=reasoning_enabled,
    )


def drive(pipeline: PerceptionPipeline, clock: ManualClock, items, step_s: float):
    """逐帧推进时钟并 process_frame（frame 传 None），返回 FrameResult 列表。

    时间**完全由 ``clock`` 驱动**（每帧 advance(step_s)）；fixture 的 per-frame
    ``timestamp`` 仅作 schema 保真，不参与计时（与 clock 存在 +30s 偏移，属预期）。
    ``items`` 仅用于确定帧数；两种 detector（Stub / Cached）的 detect 各自消费其内容。
    """
    results = []
    for i, _ in enumerate(items):
        clock.advance(step_s)
        results.append(pipeline.process_frame(None, frame_index=i))
    return results

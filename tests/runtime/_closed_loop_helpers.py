"""Slice B 闭环测试共享件：真实 detector 路径（cached detection replay）。

与 ``test_memory_e2e_closed_loop.py`` 的区别：这里的 detector 不内联 plan，而是**重放
真实 YOLO+ByteTrack 预跑得到的检测缓存** (``tests/fixtures/detections/*.json``)，
从而证明「事件经 detector→tracker→event_builder→memory 真实进入 Memory」，
而非 ``StubDetector`` 绕过 detection/tracking。

设计铁律（与 E2E 一致）：Memory 是旁路（Shadow Mode），绝不接决策、不产 Warning、
异常不崩主链路。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.runtime import PerceptionPipeline


class ManualClock:
    """可控时钟：now() 返回当前时间，advance() 推进。"""

    def __init__(self, base: Optional[datetime] = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class CachedDetectionDetector:
    """重放真实 YOLO+ByteTrack 预跑得到的检测缓存（torch-free，CI 合约）。

    仅实现 pipeline 依赖的 ``detect(frame)`` 鸭子接口；frame 透传忽略（与 ``YOLODetector``
    不同，不消费 frame 像素，只回放缓存）。这证明「事件经
    detector→tracker→event_builder→memory」真实进入 Memory，而非 ``StubDetector``
    绕过 detection/tracking。

    缓存必须包含 ``track_id``（tracker 会丢弃 ``track_id is None`` 的检测）。
    """

    def __init__(self, frames: List[Dict[str, Any]]):
        self._frames = frames
        self._i = 0

    def detect(self, frame) -> DetectionResult:
        idx = min(self._i, len(self._frames) - 1)
        f = self._frames[idx]
        dets = [Detection(**d) for d in f.get("detections", [])]
        ts = float(f.get("timestamp", 0.0))
        self._i += 1
        return DetectionResult(
            detections=dets,
            timestamp=ts,
            inference_ms=0.0,
            source_size=(288, 384),
            inference_size=(288, 384),
            model="cached",
        )


def load_cached_detections(path: Path) -> Dict[str, Any]:
    """加载检测缓存 JSON（schema 见 tests/fixtures/detections/）。"""
    return json.loads(Path(path).read_text())


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


def _memory_config(path: Path, **over) -> MemoryConfig:
    base = dict(
        enabled=True,
        snapshot_path=str(path),
        snapshot_interval_seconds=1.0,
        snapshot_fresh_threshold_seconds=30.0,
        snapshot_ttl_seconds=300.0,
        recent_behavior_retention_seconds=3600.0,
        eviction_interval_frames=60,
        cold_start_stale_confidence=0.5,
    )
    base.update(over)
    return MemoryConfig(**base)


def build_full_pipeline(
    detector,
    clock: ManualClock,
    *,
    thresholds: Optional[ThresholdConfig] = None,
    memory_store: Optional[InMemoryStore] = None,
    episode_builder: Optional[DefaultEpisodeBuilder] = None,
    episodic_shadow: bool = False,
    memory_config: Optional[MemoryConfig] = None,
    decision_enabled: bool = False,
    eval_interval_frames: int = 1,
    realtime_enabled: bool = True,
):
    """构造完整 PerceptionPipeline（实时旁路 + 可选 Memory 影子写入 + 可选快照恢复）。"""
    th = thresholds or ThresholdConfig()
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test", location="入户门",
        thresholds=th, now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock,
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher, publisher=MockPublisher(),
        notifier=MockNotifier(), max_retries=3,
    )
    behavior_builder = BehaviorBuilder(event_builder=event_builder)
    recent_store = RecentBehaviorStore()
    evaluator = RealTimeRiskEvaluator(thresholds=th, now_provider=clock)
    return PerceptionPipeline(
        detector=detector, tracker=tracker, event_builder=event_builder,
        feature_extractor=feat, rule_engine=rule_engine, decision_engine=decision,
        executor=executor, now_provider=clock,
        behavior_builder=behavior_builder, recent_behavior_store=recent_store,
        realtime_evaluator=evaluator, realtime_enabled=realtime_enabled,
        eval_interval_frames=eval_interval_frames, decision_enabled=decision_enabled,
        memory_store=memory_store, episode_builder=episode_builder,
        episodic_shadow=episodic_shadow, memory_config=memory_config,
    )


def drive_cached(p: PerceptionPipeline, clock: ManualClock, frames: List[Dict[str, Any]], step_s: float):
    """逐帧推进时钟并 process_frame（frame 传 None），返回 FrameResult 列表。"""
    results = []
    for i, _f in enumerate(frames):
        clock.advance(step_s)
        results.append(p.process_frame(None, frame_index=i))
    return results

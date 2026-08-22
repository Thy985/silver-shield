"""Pipeline Snapshot Recovery 集成测试（ADR-0024 Slice 3 Stage C + E，解 TD-0027）。

torch-free，进 CI 每 PR 合约子集。验证：
- memory 未启用 → 无 snapshot store，process_frame 不崩溃（向后兼容）
- memory 启用 + realtime 启用 → 启动期 recover() 冷启动 / 从 snapshot 恢复
- 周期快照：评估帧触发写文件；close() 兜底 flush 最终 snapshot
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from home_perception.analysis.realtime_risk_evaluator import (
    RealTimeRiskEvaluator,
)
from home_perception.analysis.recent_behavior_store import RecentBehaviorStore
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.core.config import MemoryConfig
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.memory.snapshot import (
    ActiveTrackSnapshot,
    RuntimeSnapshot,
    SnapshotStore,
)
from home_perception.runtime import PerceptionPipeline, RuntimeFrameContext


class ManualClock:
    def __init__(self, base: datetime | None = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class StubDetector:
    def __init__(self, plan: list[list[Detection]], clock: ManualClock | None = None):
        self.plan = plan
        self.clock = clock
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        if self.clock is not None:
            self.clock.advance(1.0)
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets,
            timestamp=0.0,
            inference_ms=0.0,
            source_size=(1, 1),
            inference_size=(1, 1),
            model="stub",
        )


def _person(track_id: int = 1) -> list[Detection]:
    return [
        Detection(
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=[0, 0, 10, 10],
            timestamp=0.0,
            track_id=track_id,
        )
    ]


def _build_pipeline(
    detector,
    clock: ManualClock,
    *,
    realtime_enabled: bool = True,
    memory_config: MemoryConfig | None = None,
) -> PerceptionPipeline:
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    th = ThresholdConfig()
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test", location="入户门", thresholds=th, now_provider=clock
    )
    decision = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher, publisher=MockPublisher(), notifier=MockNotifier(), max_retries=3
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
        eval_interval_frames=1,
        decision_enabled=False,
        memory_config=memory_config,
    )


def _memory_config(path: Path, **over) -> MemoryConfig:
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


def test_memory_disabled_no_snapshot_store():
    """memory_config=None → 无 snapshot store，process_frame 不崩溃（向后兼容）。"""
    clock = ManualClock()
    p = _build_pipeline(
        StubDetector([_person(1), _person(1), []], clock), clock, memory_config=None
    )
    assert p._snapshot_store is None
    assert p._cold_start_coordinator is None
    # 跑帧不抛异常
    for _ in range(3):
        p.process_frame(
            RuntimeFrameContext(video_frame=None, frame_index=0, case_time=0.0)
        )
    p.close()


def test_cold_start_on_init_no_snapshot(tmp_path: Path):
    """memory 启用但无 snapshot 文件 → 启动期冷启动，组件为空。"""
    clock = ManualClock()
    cfg = _memory_config(tmp_path / "snapshot.json")
    p = _build_pipeline(StubDetector([_person(1)], clock), clock, memory_config=cfg)
    assert p._snapshot_store is not None
    assert p._cold_start_coordinator is not None
    # 冷启动：评估器/账本为空
    assert p._realtime_evaluator.active_count == 0
    assert p._recent_behavior_store.is_empty
    p.close()


def test_periodic_snapshot_written_on_first_eval_frame(tmp_path: Path):
    """评估帧触发周期快照：首帧即写文件（_last_snapshot_at=None）。"""
    clock = ManualClock()
    snap_path = tmp_path / "snapshot.json"
    cfg = _memory_config(snap_path)
    p = _build_pipeline(StubDetector([_person(1)], clock), clock, memory_config=cfg)
    assert not snap_path.exists()  # 启动期冷启动不写文件
    p.process_frame(
        RuntimeFrameContext(video_frame=None, frame_index=0, case_time=0.0)
    )  # 评估帧 → 周期快照
    assert snap_path.exists()
    loaded = SnapshotStore(snap_path).load()
    assert loaded is not None
    assert loaded.schema_version == 1
    p.close()


def test_close_flushes_final_snapshot(tmp_path: Path):
    """close() 兜底 flush 最终 snapshot。"""
    clock = ManualClock()
    snap_path = tmp_path / "snapshot.json"
    cfg = _memory_config(snap_path)
    p = _build_pipeline(StubDetector([_person(1)], clock), clock, memory_config=cfg)
    p.process_frame(
        RuntimeFrameContext(video_frame=None, frame_index=0, case_time=0.0)
    )
    p.close()
    assert snap_path.exists()
    loaded = SnapshotStore(snap_path).load()
    assert loaded is not None


def test_recover_restores_active_state_on_init(tmp_path: Path):
    """启动期从既有 snapshot 恢复 ACTIVE_RISK 状态（Stage E 端到端）。"""
    clock = ManualClock()
    snap_path = tmp_path / "snapshot.json"
    # 预置一个 FRESH 的 snapshot（age=5s < fresh 阈值 30s）
    pre = RuntimeSnapshot(
        snapshot_id="pre-1",
        snapshot_at=clock.now() - timedelta(seconds=5),
        active_tracks=[
            ActiveTrackSnapshot(
                visitor_instance_id="V1",
                phase="active_risk",
                raised_signal_id="sig-restore",
                raised_at=clock.now() - timedelta(seconds=5),
                first_seen=clock.now() - timedelta(seconds=95),
                last_seen_at=clock.now() - timedelta(seconds=5),
            )
        ],
        recent_behavior=[],
    )
    SnapshotStore(snap_path).save(pre)

    cfg = _memory_config(snap_path)
    p = _build_pipeline(StubDetector([_person(1)], clock), clock, memory_config=cfg)
    # FRESH 恢复：ACTIVE_RISK 状态保留
    assert p._realtime_evaluator.active_risk_count == 1
    assert p._realtime_evaluator._active["V1"].raised_signal_id == "sig-restore"
    assert p._realtime_evaluator._active["V1"].confidence == 1.0
    p.close()

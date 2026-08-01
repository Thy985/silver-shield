"""冷启动恢复测试（ADR-0024 Slice 3 Stage E，解 TD-0027）。

覆盖 §8.5：FRESH/STALE/DISCARD 三档、缺失/损坏视为冷启动、恢复后 evict、
只恢复 active visitor（避免 TD-0024 重现）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from home_perception.analysis.realtime_risk_evaluator import (
    RealTimeRiskEvaluator,
    RiskPhase,
)
from home_perception.analysis.recent_behavior_store import (
    BehaviorHistory,
    RecentBehaviorStore,
)
from home_perception.analysis.rule_engine import ThresholdConfig
from home_perception.core.config import MemoryConfig
from home_perception.memory.cold_start import (
    ColdStartConfidence,
    ColdStartCoordinator,
    RecoveryResult,
)
from home_perception.memory.snapshot import (
    ActiveTrackSnapshot,
    RecentBehaviorSnapshot,
    RuntimeSnapshot,
    SnapshotStore,
)


def _utc(sec: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sec)


def _config(**overrides) -> MemoryConfig:
    base = dict(
        enabled=True,
        snapshot_path="data/memory/snapshot.json",
        snapshot_interval_seconds=30.0,
        snapshot_fresh_threshold_seconds=30.0,
        snapshot_ttl_seconds=300.0,
        recent_behavior_retention_seconds=3600.0,
        eviction_interval_frames=60,
        cold_start_stale_confidence=0.5,
    )
    base.update(overrides)
    return MemoryConfig(**base)


def _coordinator(
    path: Path, config: MemoryConfig
) -> tuple[ColdStartCoordinator, RealTimeRiskEvaluator, RecentBehaviorStore]:
    ev = RealTimeRiskEvaluator(thresholds=ThresholdConfig(), now_provider=None)
    store = RecentBehaviorStore()
    coord = ColdStartCoordinator(
        snapshot_store=SnapshotStore(path),
        evaluator=ev,
        recent_store=store,
        config=config,
    )
    return coord, ev, store


def _save_snapshot(path: Path, snapshot_at: datetime):
    snap = RuntimeSnapshot(
        snapshot_id="snap-1",
        snapshot_at=snapshot_at,
        schema_version=1,
        active_tracks=[
            ActiveTrackSnapshot(
                visitor_instance_id="V1",
                phase="active_risk",
                raised_signal_id="sig-keep",
                raised_at=snapshot_at,
                first_seen=snapshot_at - timedelta(seconds=90),
                last_seen_at=snapshot_at,
            )
        ],
        recent_behavior=[
            RecentBehaviorSnapshot(
                visitor_instance_id="V1",
                enter_times=[snapshot_at - timedelta(seconds=80)],
                last_seen_at=snapshot_at,
            )
        ],
    )
    SnapshotStore(path).save(snap)


# ---------------------------------------------------------------------------
# 三档恢复
# ---------------------------------------------------------------------------


def test_fresh_recovery(tmp_path: Path):
    """FRESH（age<30s）：完全恢复，ACTIVE_RISK 状态与 raised_signal_id 保留。"""
    now = _utc(1000)
    _save_snapshot(tmp_path / "snapshot.json", now - timedelta(seconds=10))
    coord, ev, store = _coordinator(tmp_path / "snapshot.json", _config())
    res = coord.recover(now)

    assert isinstance(res, RecoveryResult)
    assert res.recovered is True
    assert res.confidence is ColdStartConfidence.FRESH
    assert res.restored_tracks == 1
    assert res.restored_visitors == 1
    # evaluator 状态保留
    assert ev.active_risk_count == 1
    assert ev._active["V1"].raised_signal_id == "sig-keep"
    assert ev._active["V1"].confidence == 1.0
    # recent_behavior 保留
    assert "V1" in store._entries


def test_stale_recovery(tmp_path: Path):
    """STALE（30s<age<=5min）：降级恢复，confidence=0.5，不发警报。"""
    now = _utc(1000)
    _save_snapshot(tmp_path / "snapshot.json", now - timedelta(seconds=100))
    coord, ev, store = _coordinator(tmp_path / "snapshot.json", _config())
    res = coord.recover(now)

    assert res.recovered is True
    assert res.confidence is ColdStartConfidence.STALE
    assert ev.active_risk_count == 1
    # 恢复 confidence=0.5（后续新帧升级）
    assert ev._active["V1"].confidence == 0.5
    assert store._entries["V1"].last_seen_at == now - timedelta(seconds=100)


def test_discard_recovery_on_ttl(tmp_path: Path):
    """DISCARD（age>5min）：冷启动，评估器 reset()。"""
    now = _utc(1000)
    _save_snapshot(tmp_path / "snapshot.json", now - timedelta(seconds=1000))
    coord, ev, store = _coordinator(tmp_path / "snapshot.json", _config())
    # 预置一些状态
    ev._active["PRE"] = _make_state()
    store._entries["PRE"] = BehaviorHistory(enter_times=[now], last_seen_at=now)

    res = coord.recover(now)
    assert res.recovered is False
    assert res.confidence is ColdStartConfidence.DISCARD
    # 冷启动：状态清零
    assert ev.active_count == 0
    assert store.is_empty


# ---------------------------------------------------------------------------
# 缺失 / 损坏
# ---------------------------------------------------------------------------


def test_missing_snapshot_cold_start(tmp_path: Path):
    """缺失 snapshot → 视为冷启动，不抛异常。"""
    coord, ev, store = _coordinator(tmp_path / "missing.json", _config())
    res = coord.recover(_utc(1000))
    assert res.recovered is False
    assert res.confidence is ColdStartConfidence.DISCARD
    assert res.reason == "snapshot_missing"
    assert ev.active_count == 0
    assert store.is_empty


def test_corrupted_snapshot_cold_start(tmp_path: Path):
    """损坏 snapshot（非法 JSON）→ 视为冷启动，不抛异常。"""
    p = tmp_path / "snapshot.json"
    p.write_text("not json{{{", encoding="utf-8")
    coord, ev, store = _coordinator(p, _config())
    res = coord.recover(_utc(1000))
    assert res.recovered is False
    assert res.confidence is ColdStartConfidence.DISCARD
    assert ev.active_count == 0


# ---------------------------------------------------------------------------
# 只恢复 active visitor + 恢复后 evict（避免 TD-0024 重现）
# ---------------------------------------------------------------------------


def test_only_active_visitors_restored(tmp_path: Path):
    """inactive visitor（last_seen 超 retention）不被恢复。"""
    now = _utc(1000)
    snap = RuntimeSnapshot(
        snapshot_id="snap-1",
        snapshot_at=now - timedelta(seconds=10),  # FRESH
        schema_version=1,
        active_tracks=[
            ActiveTrackSnapshot(
                visitor_instance_id="ACTIVE_V",
                phase="none",
                raised_signal_id=None,
                raised_at=None,
                first_seen=now - timedelta(seconds=100),
                last_seen_at=now - timedelta(seconds=10),
            )
        ],
        recent_behavior=[
            RecentBehaviorSnapshot(
                visitor_instance_id="ACTIVE_V",
                enter_times=[now - timedelta(seconds=100)],
                last_seen_at=now - timedelta(seconds=10),  # 在 retention(3600) 内
            ),
            RecentBehaviorSnapshot(
                visitor_instance_id="STALE_V",
                enter_times=[now - timedelta(seconds=5000)],
                last_seen_at=now - timedelta(seconds=5000),  # 超 retention(3600)
            ),
        ],
    )
    SnapshotStore(tmp_path / "snapshot.json").save(snap)
    coord, ev, store = _coordinator(
        tmp_path / "snapshot.json", _config(recent_behavior_retention_seconds=3600.0)
    )
    res = coord.recover(now)

    # STALE_V 被过滤，不恢复
    assert res.restored_visitors == 1
    assert "ACTIVE_V" in store._entries
    assert "STALE_V" not in store._entries
    # 恢复后 evict 双保险：所有条目都在 retention 内
    cutoff = now - timedelta(seconds=3600)
    assert all(h.last_seen_at >= cutoff for h in store._entries.values())


def _make_state():
    from home_perception.analysis.realtime_risk_evaluator import _TrackRiskState

    return _TrackRiskState(
        phase=RiskPhase.NONE,
        raised_signal_id="",
        raised_at=None,
        first_seen=_utc(0),
        last_track_id=None,
        confidence=1.0,
    )

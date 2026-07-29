"""Snapshot 持久化测试（ADR-0024 Slice 3 Stage C，解 TD-0027）。

覆盖工程方案 §8.5：save→load 往返字段无损、缺失/损坏视为冷启动（load→None）、
原子写不残留 .tmp。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from home_perception.memory.snapshot import (
    ActiveTrackSnapshot,
    RecentBehaviorSnapshot,
    RuntimeSnapshot,
    SnapshotStore,
)


def _utc(sec: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + __import__("datetime").timedelta(
        seconds=sec
    )


def _build_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        snapshot_id="snap-1",
        snapshot_at=_utc(100),
        schema_version=1,
        active_tracks=[
            ActiveTrackSnapshot(
                visitor_instance_id="V1",
                phase="active_risk",
                raised_signal_id="sig-abc",
                raised_at=_utc(90),
                first_seen=_utc(10),
                last_seen_at=_utc(100),
            ),
            ActiveTrackSnapshot(
                visitor_instance_id="V2",
                phase="none",
                raised_signal_id=None,
                raised_at=None,
                first_seen=_utc(50),
                last_seen_at=_utc(100),
            ),
        ],
        recent_behavior=[
            RecentBehaviorSnapshot(
                visitor_instance_id="V1",
                enter_times=[_utc(10), _utc(40), _utc(80)],
                last_seen_at=_utc(100),
            ),
        ],
    )


def test_save_load_roundtrip_field_lossless(tmp_path: Path):
    """save→load 往返所有 reconstructable 字段无损。"""
    store = SnapshotStore(tmp_path / "snapshot.json")
    snap = _build_snapshot()
    store.save(snap)

    loaded = store.load()
    assert loaded is not None
    assert loaded.snapshot_id == "snap-1"
    assert loaded.snapshot_at == _utc(100)
    assert loaded.schema_version == 1

    # active_tracks
    assert len(loaded.active_tracks) == 2
    t0 = loaded.active_tracks[0]
    assert t0.visitor_instance_id == "V1"
    assert t0.phase == "active_risk"
    assert t0.raised_signal_id == "sig-abc"
    assert t0.raised_at == _utc(90)
    assert t0.first_seen == _utc(10)
    assert t0.last_seen_at == _utc(100)
    # raised_at=None 也须正确往返
    t1 = loaded.active_tracks[1]
    assert t1.raised_signal_id is None
    assert t1.raised_at is None

    # recent_behavior
    assert len(loaded.recent_behavior) == 1
    rb = loaded.recent_behavior[0]
    assert rb.visitor_instance_id == "V1"
    assert rb.enter_times == [_utc(10), _utc(40), _utc(80)]
    assert rb.last_seen_at == _utc(100)


def test_load_missing_returns_none(tmp_path: Path):
    """缺失 snapshot → load() 返回 None（视为冷启动）。"""
    store = SnapshotStore(tmp_path / "does_not_exist.json")
    assert store.load() is None


def test_load_corrupted_returns_none(tmp_path: Path):
    """损坏的 snapshot（非法 JSON）→ load() 返回 None，不抛异常。"""
    p = tmp_path / "snapshot.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    store = SnapshotStore(p)
    assert store.load() is None


def test_atomic_write_no_tmp_leftover(tmp_path: Path):
    """原子写：完成后目标文件存在，.tmp 临时文件已被 replace 消费。"""
    target = tmp_path / "snapshot.json"
    store = SnapshotStore(target)
    store.save(_build_snapshot())
    assert target.exists()
    assert not target.with_suffix(".tmp").exists()


def test_atomic_write_idempotent_overwrite(tmp_path: Path):
    """多次 save 不损坏文件，最后一次内容生效。"""
    store = SnapshotStore(tmp_path / "snapshot.json")
    store.save(_build_snapshot())
    snap2 = _build_snapshot()
    snap2.snapshot_id = "snap-2"
    store.save(snap2)
    loaded = store.load()
    assert loaded is not None
    assert loaded.snapshot_id == "snap-2"

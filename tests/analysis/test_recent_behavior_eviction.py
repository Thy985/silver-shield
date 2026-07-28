"""RecentBehaviorStore Eviction 测试（ADR-0024 Slice 3 Stage D，解 TD-0024）。

覆盖工程方案 §6.4：Eviction 只清理"超过 retention 未再出现的 visitor 条目"，
不影响滑窗计数语义（window_seconds 与 retention_seconds 两个独立时间尺度）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from home_perception.analysis.recent_behavior_store import (
    BehaviorHistory,
    RecentBehaviorStore,
)


def _utc(sec: int) -> datetime:
    """测试时钟：以 epoch 秒表达，返回 datetime UTC。"""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sec)


# ---------------------------------------------------------------------------
# Eviction 基础语义
# ---------------------------------------------------------------------------


def test_evict_expired_removes_old_entries():
    """last_seen_at < cutoff 的条目被清。"""
    store = RecentBehaviorStore()
    store._entries["OLD"] = BehaviorHistory(
        enter_times=[_utc(0)], last_seen_at=_utc(0)
    )
    store._entries["NEW"] = BehaviorHistory(
        enter_times=[_utc(100)], last_seen_at=_utc(100)
    )
    # retention=60：cutoff = now(200) - 60 = 140；OLD(last_seen=0) < 140 被清，NEW(100<140?) 100<140 → 也被清
    # 调整：让 NEW 在窗口内
    store._entries["NEW"] = BehaviorHistory(enter_times=[_utc(150)], last_seen_at=_utc(150))
    removed = store.evict_expired(_utc(200), retention_seconds=60)
    assert removed == 1
    assert "OLD" not in store._entries
    assert "NEW" in store._entries


def test_evict_expired_keeps_recent_entries():
    """last_seen_at >= cutoff 的条目保留。"""
    store = RecentBehaviorStore()
    store._entries["A"] = BehaviorHistory(enter_times=[_utc(190)], last_seen_at=_utc(190))
    store._entries["B"] = BehaviorHistory(enter_times=[_utc(195)], last_seen_at=_utc(195))
    removed = store.evict_expired(_utc(200), retention_seconds=60)  # cutoff=140
    assert removed == 0
    assert "A" in store._entries and "B" in store._entries


def test_evict_expired_returns_count():
    """返回被清理条目数。"""
    store = RecentBehaviorStore()
    for i in range(3):
        store._entries[f"V{i}"] = BehaviorHistory(
            enter_times=[_utc(0)], last_seen_at=_utc(0)
        )
    assert store.evict_expired(_utc(200), retention_seconds=60) == 3
    assert len(store._entries) == 0


def test_evict_expired_empty_store():
    """空 store 不抛异常，返回 0。"""
    store = RecentBehaviorStore()
    assert store.evict_expired(_utc(200), retention_seconds=60) == 0
    assert store.is_empty


# ---------------------------------------------------------------------------
# Soak：大批量不爆内存
# ---------------------------------------------------------------------------


def test_evict_soak_1000_visitors():
    """模拟 1000 个 visitor 依次进入，evict 后 _entries 不超过 retention 上限。"""
    store = RecentBehaviorStore()
    now = _utc(0)
    # 让每个 visitor 的 last_seen_at 跨度大，retention=60 时只保留最近 60s 内的
    for i in range(1000):
        # 每 i 秒一个 visitor 进入（last_seen = i）
        t = _utc(i)
        store._entries[f"V{i}"] = BehaviorHistory(enter_times=[t], last_seen_at=t)
    now = _utc(1000)
    removed = store.evict_expired(now, retention_seconds=60)  # cutoff=940
    # 应清掉 940 个（last_seen < 940），保留 60 个（940..999）
    assert removed == 940
    assert len(store._entries) == 60
    assert all(f"V{i}" in store._entries for i in range(940, 1000))


# ---------------------------------------------------------------------------
# 与 update() / 滑窗计数的协同
# ---------------------------------------------------------------------------


def test_last_seen_at_updated_on_every_update():
    """同 visitor 多次 update，last_seen_at 始终刷新为本次 now。"""
    store = RecentBehaviorStore()
    store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    store.update("V", _utc(10), now=_utc(100), window_seconds=600)
    assert store._entries["V"].last_seen_at == _utc(100)
    store.update("V", _utc(20), now=_utc(200), window_seconds=600)
    assert store._entries["V"].last_seen_at == _utc(200)


def test_visits_in_window_unchanged_after_eviction():
    """eviction 不破坏滑窗计数语义（retention 远大于窗口时，evict 不影响 counts）。"""
    store = RecentBehaviorStore()
    store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    store.update("V", _utc(100), now=_utc(100), window_seconds=600)
    # 大 retention evict，不应清掉当前 visitor
    removed = store.evict_expired(_utc(200), retention_seconds=3600)
    assert removed == 0
    assert store.query_window("V", now=_utc(200), window_seconds=600)["visits_in_window"] == 2

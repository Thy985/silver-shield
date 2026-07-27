"""RecentBehaviorStore 单元测试（ADR-0021 §3.2，Migration Stage A）。

只测类型自身的数据契约与不变式（torch-free，进 CI 每 PR 合约子集）。

覆盖（对齐工程方案 §8.1）：
- visits_in_window 账本滑窗（过期清理、含当前进行中这次）
- track_key = visitor_instance_id（稳定主键，非会复用的 track_id）
- 重启即空（volatile）
- 只读返回：改动产出的 recent_behavior 不影响 store 下一帧产出（引用隔离）
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest

from home_perception.analysis.recent_behavior_store import RecentBehaviorStore


def _utc(sec: int) -> datetime:
    """测试时钟：以 epoch 秒表达，返回 datetime UTC（便于滑窗加减）。"""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sec)


# ---------------------------------------------------------------------------
# 滑窗：含当前 + 过期清理
# ---------------------------------------------------------------------------

def test_window_includes_current_ongoing_visit():
    """进入瞬间即计入窗口（含当前进行中这次）。"""
    store = RecentBehaviorStore()
    t0 = _utc(0)
    res = store.update("V", t0, now=t0, window_seconds=600)
    assert res["visits_in_window"] == 1


def test_window_expiry_and_dedup():
    """窗口外的旧进入被清理；同帧重处理不重复计数。"""
    store = RecentBehaviorStore()
    # V 在 t=0 进入
    store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    # 同帧重处理（now=0）不应重复计数
    assert store.update("V", _utc(0), now=_utc(0), window_seconds=600)["visits_in_window"] == 1
    # t=300 仍在区间内（窗口 [−300, 300]? 不，cutoff=now-600），V 计 1
    assert store.update("V", _utc(0), now=_utc(300), window_seconds=600)["visits_in_window"] == 1
    # t=700：cutoff = 100，V 的 t=0 已过期 → 0
    assert store.update("V", _utc(0), now=_utc(700), window_seconds=600)["visits_in_window"] == 0


def test_multiple_visitors_sliding_window():
    """不同访客各自计数；窗口滑动后各自独立过期。"""
    store = RecentBehaviorStore()
    store.update("V", _utc(0), now=_utc(0), window_seconds=600)   # V=1
    store.update("U", _utc(300), now=_utc(300), window_seconds=600)  # U=1
    # now=700：V 过期、U 在窗内
    snap = store.update("U", _utc(300), now=_utc(700), window_seconds=600)
    assert snap["visits_in_window"] == 1
    # V 此时单独查询应为 0
    assert store.snapshot("V", now=_utc(700), window_seconds=600)["visits_in_window"] == 0


def test_window_length_edge():
    """窗口边界：enter_time == cutoff 仍计入（>=）。"""
    store = RecentBehaviorStore()
    store.update("V", _utc(100), now=_utc(700), window_seconds=600)  # cutoff=100
    assert store.snapshot("V", now=_utc(700), window_seconds=600)["visits_in_window"] == 1


# ---------------------------------------------------------------------------
# track_key = visitor_instance_id
# ---------------------------------------------------------------------------

def test_keyed_by_visitor_instance_id():
    """store 以 visitor_instance_id 为主键（稳定），同一访客多次进入合并计数。"""
    store = RecentBehaviorStore()
    store.update("vid-ABC", _utc(0), now=_utc(0), window_seconds=600)
    store.update("vid-ABC", _utc(100), now=_utc(100), window_seconds=600)
    store.update("vid-ABC", _utc(200), now=_utc(200), window_seconds=600)
    assert store.snapshot("vid-ABC", now=_utc(200), window_seconds=600)["visits_in_window"] == 3


def test_distinct_instance_ids_separate():
    """不同 visitor_instance_id 互不串号（即便来自同一 track_id 复用场景）。"""
    store = RecentBehaviorStore()
    store.update("vid-A", _utc(0), now=_utc(0), window_seconds=600)
    store.update("vid-B", _utc(0), now=_utc(0), window_seconds=600)
    assert store.snapshot("vid-A", now=_utc(0), window_seconds=600)["visits_in_window"] == 1
    assert store.snapshot("vid-B", now=_utc(0), window_seconds=600)["visits_in_window"] == 1


# ---------------------------------------------------------------------------
# 重启即空（volatile）
# ---------------------------------------------------------------------------

def test_volatile_restart_empty():
    """新 store 即空；reset() 模拟重启丢弃。"""
    store = RecentBehaviorStore()
    assert store.is_empty
    store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    assert not store.is_empty
    store.reset()
    assert store.is_empty
    assert store.snapshot("V", now=_utc(0), window_seconds=600)["visits_in_window"] == 0


# ---------------------------------------------------------------------------
# 只读返回 / 引用隔离
# ---------------------------------------------------------------------------

def test_return_is_readonly_proxy():
    """update() 返回 MappingProxyType（只读），防止调用方意外改写内部状态。"""
    store = RecentBehaviorStore()
    res = store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    assert isinstance(res, types.MappingProxyType)
    with pytest.raises(TypeError):
        res["visits_in_window"] = 999  # 只读代理拒绝改写


def test_return_isolation_across_frames():
    """改动产出的 recent_behavior 不影响 store 下一帧产出（引用隔离）。"""
    store = RecentBehaviorStore()
    r1 = store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    r1_copy = dict(r1)
    r1_copy["visits_in_window"] = 42  # 改副本
    # 下一帧重新统计，不受副本改动影响
    r2 = store.update("V", _utc(0), now=_utc(0), window_seconds=600)
    assert r2["visits_in_window"] == 1


def test_negative_window_rejected():
    with pytest.raises(ValueError):
        RecentBehaviorStore().update("V", _utc(0), now=_utc(0), window_seconds=-1)


def test_enter_after_now_rejected():
    with pytest.raises(ValueError):
        RecentBehaviorStore().update("V", _utc(100), now=_utc(0), window_seconds=600)

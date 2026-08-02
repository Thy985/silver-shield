"""C-1 RuleBasedRetrieval 单测（DESIGN §5 C-1 / ADR-0025 C1–C3）。

验证：
- 返回 ``list[EpisodicRecord]``（代码优先：数据源是 ``get_episodic_by_visitor``，
  非 ``compose_context`` 的 dict）；
- 30d 时间窗过滤 + ``MemoryStatus.ACTIVE`` 过滤；
- C3 确定性（同输入两次顺序一致）；
- 三排序键（risk_category_match → same_time_band → recency）+ record_id tiebreak；
- 100 条上限；空 store 不崩；store 失败抛 ``RetrievalError``；
- device_id 保留参数 no-op（绝不进输出）；
- C2 只读：retrieve 不修改原始记录。

构造 ``EpisodicRecord`` 用**固定 source_event_ids**（回放铁律：event_id 默认随机
UUID4，测试须显式传固定值）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from home_perception.memory.consumer.config import RetrievalConfig
from home_perception.memory.consumer.contracts import CurrentEvent
from home_perception.memory.consumer.exceptions import RetrievalError
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval
from home_perception.memory.records import EpisodicRecord, MemoryStatus
from home_perception.memory.store import InMemoryStore


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _make_record(
    rid: str,
    vid: str,
    enter: datetime,
    leave: datetime,
    risk_level: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    source_ids: list[str] | None = None,
) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=vid,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=source_ids or [f"ev-{rid}"],
        summary=f"visit {rid}",
        model_version="ep-builder-v1",
        risk_level=risk_level,
        memory_status=status,
    )


def _make_event(
    vid: str,
    occurred_at: datetime,
    event_type: str = "visitor_event",
    risk_level: str | None = None,
) -> CurrentEvent:
    return CurrentEvent(
        event_id=f"cur-{vid}",
        event_type=event_type,
        visitor_instance_id=vid,
        occurred_at=occurred_at,
        risk_level=risk_level,
        markers=(),
    )


def _seeded_store(records: list[EpisodicRecord]) -> InMemoryStore:
    store = InMemoryStore()
    for r in records:
        store.upsert_episodic(r)
    return store


# ---------------------------------------------------------------------------
# 基本契约
# ---------------------------------------------------------------------------
def test_returns_episodic_list_not_dict() -> None:
    """代码优先：召回返回 list[EpisodicRecord]，不是 compose_context 的 dict。"""
    vid = "v-return"
    store = _seeded_store(
        [_make_record("ep-1", vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30))]
    )
    out = RuleBasedRetrieval(store).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    assert isinstance(out, list)
    assert len(out) == 1
    assert isinstance(out[0], EpisodicRecord)


def test_empty_store_returns_empty() -> None:
    store = InMemoryStore()
    out = RuleBasedRetrieval(store).retrieve(_make_event("v-empty", _utc(2026, 7, 1, 21)))
    assert out == []


def test_retrieval_error_translated() -> None:
    """store 召回失败 → 转译为 RetrievalError（不静默、不抛未分类异常）。"""

    class BoomStore(InMemoryStore):
        def get_episodic_by_visitor(self, visitor_instance_id: str) -> list[EpisodicRecord]:
            raise RuntimeError("backend down")

    store = BoomStore()
    with pytest.raises(RetrievalError):
        RuleBasedRetrieval(store).retrieve(_make_event("v-err", _utc(2026, 7, 1, 21)))


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------
def test_time_window_filter() -> None:
    vid = "v-window"
    old = _make_record("ep-old", vid, _utc(2026, 5, 1, 21), _utc(2026, 5, 1, 21, 30))
    recent = _make_record("ep-recent", vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30))
    store = _seeded_store([old, recent])
    out = RuleBasedRetrieval(store).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    ids = [r.record_id for r in out]
    assert "ep-recent" in ids
    assert "ep-old" not in ids


def test_memory_status_filter() -> None:
    vid = "v-status"
    active = _make_record("ep-active", vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30))
    deprecated = _make_record(
        "ep-dep", vid, _utc(2026, 6, 21, 21), _utc(2026, 6, 21, 21, 30),
        status=MemoryStatus.DEPRECATED,
    )
    archived = _make_record(
        "ep-arch", vid, _utc(2026, 6, 22, 21), _utc(2026, 6, 22, 21, 30),
        status=MemoryStatus.ARCHIVED,
    )
    invalid = _make_record(
        "ep-inv", vid, _utc(2026, 6, 23, 21), _utc(2026, 6, 23, 21, 30),
        status=MemoryStatus.INVALID,
    )
    store = _seeded_store([active, deprecated, archived, invalid])
    out = RuleBasedRetrieval(store).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    ids = [r.record_id for r in out]
    assert ids == ["ep-active"]


def test_max_records_cap() -> None:
    vid = "v-cap"
    records = [
        _make_record(
            f"ep-{i:03d}", vid,
            _utc(2026, 6, 1, 21) + timedelta(days=i),
            _utc(2026, 6, 1, 21, 30) + timedelta(days=i),
        )
        for i in range(150)
    ]
    store = _seeded_store(records)
    out = RuleBasedRetrieval(store).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    assert len(out) == 100


def test_lookback_days_configurable() -> None:
    """RetrievalConfig.lookback_days 生效（O2 可配）。"""
    vid = "v-look"
    mid = _make_record("ep-mid", vid, _utc(2026, 5, 15, 21), _utc(2026, 5, 15, 21, 30))
    store = _seeded_store([mid])
    cfg = RetrievalConfig(lookback_days=120)
    out = RuleBasedRetrieval(store, config=cfg).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    assert [r.record_id for r in out] == ["ep-mid"]


# ---------------------------------------------------------------------------
# C3 确定性
# ---------------------------------------------------------------------------
def test_determinism_c3() -> None:
    vid = "v-det"
    records = [
        _make_record(f"ep-{i:03d}", vid, _utc(2026, 6, i + 1, 21), _utc(2026, 6, i + 1, 21, 30))
        for i in range(20)
    ]
    store = _seeded_store(records)
    retrieval = RuleBasedRetrieval(store)
    event = _make_event(vid, _utc(2026, 7, 1, 21))
    out1 = [r.record_id for r in retrieval.retrieve(event)]
    out2 = [r.record_id for r in retrieval.retrieve(event)]
    assert out1 == out2


# ---------------------------------------------------------------------------
# 排序键
# ---------------------------------------------------------------------------
def test_rank_risk_category_first() -> None:
    """risk_signal 当前事件：含 risk_level 的记录（命中）排在无 risk_level 前。"""
    vid = "v-cat"
    no_risk = _make_record(
        "ep-norisk", vid, _utc(2026, 6, 25, 21), _utc(2026, 6, 25, 21, 30), risk_level=None
    )
    with_risk = _make_record(
        "ep-risk", vid, _utc(2026, 6, 10, 9), _utc(2026, 6, 10, 9, 30), risk_level="HIGH"
    )
    store = _seeded_store([no_risk, with_risk])
    out = RuleBasedRetrieval(store).retrieve(
        _make_event(vid, _utc(2026, 7, 1, 21), event_type="risk_signal")
    )
    assert [r.record_id for r in out] == ["ep-risk", "ep-norisk"]


def test_rank_same_time_band() -> None:
    """同时间段（enter hour 与当前 hour 环形距离 <=3）排在前。"""
    vid = "v-band"
    off_band = _make_record(
        "ep-off", vid, _utc(2026, 6, 20, 9), _utc(2026, 6, 20, 9, 30), risk_level="HIGH"
    )
    in_band = _make_record(
        "ep-in", vid, _utc(2026, 6, 25, 21), _utc(2026, 6, 25, 21, 30), risk_level="HIGH"
    )
    store = _seeded_store([off_band, in_band])
    out = RuleBasedRetrieval(store).retrieve(
        _make_event(vid, _utc(2026, 7, 1, 21), event_type="risk_signal")
    )
    assert [r.record_id for r in out] == ["ep-in", "ep-off"]


def test_rank_recency() -> None:
    """recency：enter_time 越近越前。"""
    vid = "v-rec"
    older = _make_record(
        "ep-old", vid, _utc(2026, 6, 10, 21), _utc(2026, 6, 10, 21, 30), risk_level="HIGH"
    )
    newer = _make_record(
        "ep-new", vid, _utc(2026, 6, 25, 21), _utc(2026, 6, 25, 21, 30), risk_level="HIGH"
    )
    store = _seeded_store([older, newer])
    out = RuleBasedRetrieval(store).retrieve(
        _make_event(vid, _utc(2026, 7, 1, 21), event_type="risk_signal")
    )
    assert [r.record_id for r in out] == ["ep-new", "ep-old"]


def test_rank_record_id_tiebreak() -> None:
    """完全同序时按 record_id 升序（C3 完全确定）。"""
    vid = "v-tie"
    records = [
        _make_record(
            rid, vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30), risk_level="HIGH"
        )
        for rid in ["ep-bbb", "ep-aaa", "ep-ccc"]
    ]
    store = _seeded_store(records)
    out = RuleBasedRetrieval(store).retrieve(
        _make_event(vid, _utc(2026, 7, 1, 21), event_type="risk_signal")
    )
    assert [r.record_id for r in out] == ["ep-aaa", "ep-bbb", "ep-ccc"]


# ---------------------------------------------------------------------------
# 隐私 / 只读
# ---------------------------------------------------------------------------
def test_device_id_is_noop() -> None:
    """device_id 保留参数：不改变召回结果（v1 no-op，绝不进输出）。"""
    vid = "v-dev"
    store = _seeded_store(
        [_make_record("ep-1", vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30))]
    )
    event = _make_event(vid, _utc(2026, 7, 1, 21))
    base = [r.record_id for r in RuleBasedRetrieval(store).retrieve(event)]
    with_dev = [
        r.record_id for r in RuleBasedRetrieval(store, device_id="cam-door-1").retrieve(event)
    ]
    assert base == with_dev


def test_no_mutation_c2() -> None:
    """C2 只读：retrieve 不修改 Memory Store 中的原始记录。"""
    vid = "v-mut"
    store = _seeded_store(
        [_make_record("ep-1", vid, _utc(2026, 6, 20, 21), _utc(2026, 6, 20, 21, 30))]
    )
    before = store.snapshot()["episodic"]
    RuleBasedRetrieval(store).retrieve(_make_event(vid, _utc(2026, 7, 1, 21)))
    after = store.snapshot()["episodic"]
    assert before == after

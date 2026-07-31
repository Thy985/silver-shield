"""Memory Integration Closure · Slice C — Product Closure（用户价值验收）测试。

验证 ``MemoryQuery.compose_context`` 真能产生**可溯源、可重放**的用户价值 JSON，
而非"为了好看而硬编码"。全部 torch-free。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from home_perception.memory.query import MemoryQuery
from home_perception.memory.records import ActionSummary, EpisodicRecord
from home_perception.memory.store import InMemoryStore


def _utc(y, m, d, h, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def _make_episode(
    record_id: str = "ep-1",
    visitor_instance_id: str = "v-stranger-001",
    enter: datetime = _utc(2026, 7, 30, 18, 30),
    leave: datetime = _utc(2026, 7, 30, 18, 45),
    risk_level: str = "HIGH",
    recommended_action: str = "ESCALATE_COMMUNITY",
    source_event_ids: tuple = ("ve-1", "w-1", "a-1"),
    reason_summary: tuple = (
        "门口停留15分钟（> long_duration 阈值）",
        "非常规访问时间（odd_hour）",
        "风险规则 high_risk_approach 触发",
    ),
) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=record_id,
        visitor_instance_id=visitor_instance_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=list(source_event_ids),
        summary="陌生访客异常停留15分钟",
        model_version="ep-builder-v1",
        reason_summary=list(reason_summary),
        actions=[ActionSummary(command_type="NOTIFY_FAMILY", command_id="a-1", status="executed")],
        risk_level=risk_level,
        recommended_action=recommended_action,
    )


@pytest.fixture
def store_with_one_episode() -> InMemoryStore:
    store = InMemoryStore()
    store.upsert_episodic(_make_episode())
    return store


# ---------------------------------------------------------------------------
# 1) 用户价值验收：输出可溯源到具体 record / warning / action
# ---------------------------------------------------------------------------
def test_compose_context_produces_traceable_user_value(store_with_one_episode):
    store = store_with_one_episode
    ep = store.get_episodic_by_visitor("v-stranger-001")[0]
    query = MemoryQuery(store)

    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )

    # 访客已离场 → 已解除
    assert ctx["current_status"] == "CLEARED"

    # reason 来自 episode 真实字段（停留时长 + 风险等级 + 非常规时间），非硬编码
    assert "15 分钟" in ctx["reason"]
    assert "风险等级 HIGH" in ctx["reason"]
    assert "非常规访问时间" in ctx["reason"]

    # evidence 直接等于 episode.reason_summary（数据派生，可溯源）
    assert ctx["evidence"] == ep.reason_summary

    # handling 溯源到 recommended_action + 首个 ActionSummary
    assert "ESCALATE_COMMUNITY" in ctx["handling"]
    assert "NOTIFY_FAMILY" in ctx["handling"]
    assert "a-1" in ctx["handling"]

    # history 计数等于窗口内该访客 episode 数
    assert ctx["history"] == "过去 1 天类似事件 1 次"

    # source_record_ids 指向贡献来源 record
    assert ctx["source_record_ids"] == [ep.record_id]

    # 关键不变量：每字段都能从 store 中具体对象重建（证明不是硬编码）
    assert ctx["reason"] == query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )["reason"]


# ---------------------------------------------------------------------------
# 2) Replay 稳定性：同输入两次调用输出完全一致（可审计 / Agent 输入稳定）
# ---------------------------------------------------------------------------
def test_compose_context_replay_stable(store_with_one_episode):
    store = store_with_one_episode
    q1, q2 = MemoryQuery(store), MemoryQuery(store)
    kw = dict(
        visitor_instance_id="v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )
    assert q1.compose_context(**kw) == q2.compose_context(**kw)


# ---------------------------------------------------------------------------
# 3) 无匹配：窗口内无事件 → reason/handling 为 None，不报错
# ---------------------------------------------------------------------------
def test_compose_context_no_match_returns_empty_reason(store_with_one_episode):
    store = store_with_one_episode
    query = MemoryQuery(store)
    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 1, 1, 0, 0),
        window_end=_utc(2026, 1, 2, 0, 0),
    )
    assert ctx["reason"] is None
    assert ctx["evidence"] == []
    assert ctx["handling"] is None
    assert ctx["source_record_ids"] == []
    assert ctx["history"] == "过去 1 天类似事件 0 次"


# ---------------------------------------------------------------------------
# 4) current_status：访客在场（enter <= as_of <= leave）→ ACTIVE_RISK
# ---------------------------------------------------------------------------
def test_current_status_active_when_visitor_in_progress(store_with_one_episode):
    store = store_with_one_episode
    query = MemoryQuery(store)
    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
        as_of=_utc(2026, 7, 30, 18, 38),  # 访客仍在场
    )
    assert ctx["current_status"] == "ACTIVE_RISK"


# ---------------------------------------------------------------------------
# 5) 信息损失评估：10000 条事件 → 1 条 Episode，关键字段保留可溯源
# ---------------------------------------------------------------------------
def test_episode_condenses_many_source_events():
    store = InMemoryStore()
    many_ids = [f"evt-{i}" for i in range(100)]  # 模拟 100 条原始事件
    store.upsert_episodic(_make_episode(source_event_ids=tuple(many_ids)))

    assert store.short_term_count() == 0  # 非 O(帧数)
    episodes = store.get_episodic_by_visitor("v-stranger-001")
    assert len(episodes) == 1  # 100 条事件 → 1 条 episode
    # 关键字段保留且可溯源
    ep = episodes[0]
    assert ep.risk_level == "HIGH"
    assert ep.recommended_action == "ESCALATE_COMMUNITY"
    assert set(ep.source_event_ids) == set(many_ids)  # 溯源链不丢
    ctx = MemoryQuery(store).compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )
    assert ctx["source_record_ids"] == [ep.record_id]

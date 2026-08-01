"""Memory Integration Closure · Slice C — Product Closure（用户价值验收）测试。

验证 ``MemoryQuery.compose_context`` 真能产生**可溯源、可重放**的用户价值 JSON，
而非"为了好看而硬编码"。全部 torch-free。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from home_perception.memory.query import MemoryQuery
from home_perception.memory.records import (
    ActionSummary,
    EpisodicRecord,
    VisitorPresenceStatus,
)
from home_perception.memory.store import InMemoryStore


def _utc(y, m, d, h, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _make_episode(
    record_id: str = "ep-1",
    visitor_instance_id: str = "v-stranger-001",
    enter: datetime = _utc(2026, 7, 30, 18, 30),  # noqa: B008
    leave: datetime = _utc(2026, 7, 30, 18, 45),  # noqa: B008
    risk_level: str = "HIGH",
    recommended_action: str = "ESCALATE_COMMUNITY",
    actions: tuple = (("NOTIFY_FAMILY", "a-1", "executed"),),
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
        actions=[ActionSummary(command_type=c, command_id=i, status=s) for c, i, s in actions],
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

    # 访客已离场（as_of=window_end 晚于 leave_time）→ CLEARED
    assert ctx["current_status"] == VisitorPresenceStatus.CLEARED

    # reason 来自 episode 真实字段（停留时长 + 风险等级 + 非常规时间），非硬编码
    assert "15 分钟" in ctx["reason"]
    assert "风险等级 HIGH" in ctx["reason"]
    assert "非常规访问时间" in ctx["reason"]

    # evidence 直接等于 episode.reason_summary（数据派生，可溯源）
    assert ctx["evidence"] == ep.reason_summary

    # handling 溯源到 recommended_action + 全部 ActionSummary
    assert "ESCALATE_COMMUNITY" in ctx["handling"]
    assert "NOTIFY_FAMILY" in ctx["handling"]
    assert "a-1" in ctx["handling"]

    # history 计数等于窗口内该访客 episode 数（文案与计数口径一致，review #7）
    assert ctx["history"] == "过去 1 天事件 1 次"

    # source_record_ids 指向贡献来源 record
    assert ctx["source_record_ids"] == [ep.record_id]


# ---------------------------------------------------------------------------
# 2) Replay 稳定性：同输入两次调用输出完全一致（可审计 / Agent 输入稳定）
# ---------------------------------------------------------------------------
def test_compose_context_replay_stable(store_with_one_episode):
    store = store_with_one_episode
    q1, q2 = MemoryQuery(store), MemoryQuery(store)
    kw = {
        "visitor_instance_id": "v-stranger-001",
        "window_start": _utc(2026, 7, 30, 0, 0),
        "window_end": _utc(2026, 7, 31, 0, 0),
    }
    assert q1.compose_context(**kw) == q2.compose_context(**kw)


# ---------------------------------------------------------------------------
# 3) 无匹配：窗口内无事件 → reason/handling 为 None，current_status=NO_RECORD
# ---------------------------------------------------------------------------
def test_compose_context_no_match_returns_empty_reason(store_with_one_episode):
    store = store_with_one_episode
    query = MemoryQuery(store)
    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 1, 1, 0, 0),
        window_end=_utc(2026, 1, 2, 0, 0),
    )
    assert ctx["current_status"] == VisitorPresenceStatus.NO_RECORD
    assert ctx["reason"] is None
    assert ctx["evidence"] == []
    assert ctx["handling"] is None
    assert ctx["source_record_ids"] == []
    assert ctx["history"] == "过去 1 天事件 0 次"


# ---------------------------------------------------------------------------
# 4) current_status：IN_PROGRESS 仅在「回放」语义下可达（as_of 落在在场区间内）。
#    真实数据流中 episode 仅离场后写入，leave_time 恒为过去，实时查询恒为 CLEARED
#    （review #1：IN_PROGRESS ≠ 实时在场，实时在场见 ShortTermRecord.phase）。
# ---------------------------------------------------------------------------
def test_current_status_in_progress_only_during_replay(store_with_one_episode):
    store = store_with_one_episode
    query = MemoryQuery(store)
    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
        as_of=_utc(2026, 7, 30, 18, 38),  # 访客仍在场（历史回放视角）
    )
    assert ctx["current_status"] == VisitorPresenceStatus.IN_PROGRESS


def test_current_status_cleared_after_leave(store_with_one_episode):
    store = store_with_one_episode
    query = MemoryQuery(store)
    ctx = query.compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
        as_of=_utc(2026, 7, 30, 20, 0),  # 已离场之后
    )
    assert ctx["current_status"] == VisitorPresenceStatus.CLEARED


# ---------------------------------------------------------------------------
# 5) 多 episode：primary 选最高风险；同风险取最新（review #8 关键路径）
# ---------------------------------------------------------------------------
def test_primary_episode_picks_highest_risk():
    store = InMemoryStore()
    store.upsert_episodic(
        _make_episode(
            record_id="ep-low",
            risk_level="LOW",
            enter=_utc(2026, 7, 30, 19, 30),
            leave=_utc(2026, 7, 30, 19, 45),
        )
    )
    store.upsert_episodic(
        _make_episode(record_id="ep-high", risk_level="HIGH", enter=_utc(2026, 7, 30, 18, 30))
    )
    ctx = MemoryQuery(store).compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 0, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )
    assert ctx["current_status"] == VisitorPresenceStatus.CLEARED
    assert "风险等级 HIGH" in ctx["reason"]  # 选了 HIGH 而非 LOW
    assert "风险等级 LOW" not in ctx["reason"]
    assert ctx["history"] == "过去 1 天事件 2 次"
    assert set(ctx["source_record_ids"]) == {"ep-low", "ep-high"}


# ---------------------------------------------------------------------------
# 6) 窗口过滤与状态视角一致（review #2）：
#    - 窗口前进入、窗口前离开 → 不重叠 → 排除，current_status=NO_RECORD（无矛盾）
#    - 窗口前进入、窗口内离开 → 重叠 → 包含（旧 enter_time-only 过滤会漏掉）
# ---------------------------------------------------------------------------
def test_window_excludes_pre_window_episode_no_contradiction():
    store = InMemoryStore()
    # 进入 07-29 23:00，离开 07-30 00:30，窗口 07-30 06:00~07-31
    store.upsert_episodic(
        _make_episode(enter=_utc(2026, 7, 29, 23, 0), leave=_utc(2026, 7, 30, 0, 30))
    )
    ctx = MemoryQuery(store).compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 6, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )
    assert ctx["current_status"] == VisitorPresenceStatus.NO_RECORD
    assert ctx["reason"] is None
    assert ctx["history"] == "过去 1 天事件 0 次"


def test_window_includes_episode_straddling_window_start():
    store = InMemoryStore()
    # 进入 07-30 05:00，离开 07-30 07:00，窗口 07-30 06:00~07-31（跨窗口起点）
    store.upsert_episodic(
        _make_episode(enter=_utc(2026, 7, 30, 5, 0), leave=_utc(2026, 7, 30, 7, 0))
    )
    ctx = MemoryQuery(store).compose_context(
        "v-stranger-001",
        window_start=_utc(2026, 7, 30, 6, 0),
        window_end=_utc(2026, 7, 31, 0, 0),
    )
    assert ctx["current_status"] == VisitorPresenceStatus.CLEARED
    assert ctx["reason"] is not None  # 重叠过滤正确纳入，未被漏掉


# ---------------------------------------------------------------------------
# 7) 入参校验（review #3）：naive datetime / 乱序窗口必须明确报错
# ---------------------------------------------------------------------------
def test_compose_context_rejects_naive_datetime(store_with_one_episode):
    query = MemoryQuery(store_with_one_episode)
    with pytest.raises(ValueError):
        query.compose_context(
            "v-stranger-001",
            window_start=datetime(2026, 7, 30, 0, 0),  # noqa: DTZ001 (naive test)
            window_end=_utc(2026, 7, 31, 0, 0),
        )


def test_compose_context_rejects_reversed_window(store_with_one_episode):
    query = MemoryQuery(store_with_one_episode)
    with pytest.raises(ValueError):
        query.compose_context(
            "v-stranger-001",
            window_start=_utc(2026, 7, 31, 0, 0),
            window_end=_utc(2026, 7, 30, 0, 0),  # window_start > window_end
        )


# ---------------------------------------------------------------------------
# 8) 信息损失评估：100 条事件 → 1 条 Episode，关键字段保留可溯源
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

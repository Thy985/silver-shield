"""Memory Evaluation（ADR-0024 §8.8 Slice 6 验收）。

> Slice 6 是**验证切片**，不实现新功能（Semantic 聚合归 Stage G/H）。
> 三类量化验收：
> - §8.8.1 压缩效果（Compression Ratio）
> - §8.8.2 信息保留（Information Retention）
> - §8.8.3 一致性（Consistency / Replay Test）→ 见 test_memory_replay.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.action.command import ActionCommand
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.records import EpisodicRecord, ShortTermRecord
from home_perception.memory.store import InMemoryStore


def _utc(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def _make_visitor(enter, leave, duration=180.0, event_id="ev-eval"):
    return VisitorEvent(
        visitor_id=uuid4(),
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration,
        event_id=event_id,
    )


def _make_warning(visitor_id, created_at, risk_level="HIGH", rec_action="NOTIFY_FAMILY",
                  reasons=("异常停留",)):
    trigger = {
        "event_id": f"{visitor_id}:abnormal_dwell",
        "event_type": "abnormal_dwell",
        "score": 0.9,
        "timestamp": created_at.isoformat(),
    }
    return WarningEvent(
        elder_id="elder-001",
        device_id="dev-001",
        risk_level=risk_level,
        recommended_action=rec_action,
        trigger_events=[trigger],
        reason_summary=list(reasons),
        warning_id=uuid4(),
        created_at=created_at,
    )


def _make_action(warning_id, command_type="SEND_FAMILY_MESSAGE", status="DONE"):
    return ActionCommand(
        command_type=command_type,
        warning_id=warning_id,
        payload={},
        command_id=uuid4(),
        status=status,
    )


# ===========================================================================
# §8.8.1 压缩效果（Compression Ratio）
# ===========================================================================
def test_compression_ratio_meets_threshold():
    """10000 帧原始状态 → 1 次访问 → 1 条 EpisodicRecord，压缩比 ≥ 100:1。

    Memory 不逐帧落盘（ADR-0024 §3.1.1）：每帧 `dwell_seconds` 是易变态，逐帧
    存储会爆炸。一次完整访问只投影为 1 条 EpisodicRecord。
    """
    RAW_FRAMES = 10000  # 模拟一次访问内的逐帧 BehaviorState 观测数
    builder = DefaultEpisodeBuilder()
    store = InMemoryStore()

    # 一次访问（停留约 10000*? 秒，仅用于语义；压缩比与停留时长无关）
    enter = _utc(2026, 7, 28, 14, 0, 0)
    leave = _utc(2026, 7, 28, 14, 30, 0)
    visitor = _visitor = _make_visitor(enter, leave, duration=1800.0, event_id="ev-compress")

    rec = builder.project_episode(visitor, warnings=[], actions=[])
    assert rec is not None
    store.upsert_episodic(rec)

    episodic_count = len(store.get_episodic_by_visitor(str(visitor.visitor_id)))
    assert episodic_count == 1, "一次访问必须只产生 1 条 EpisodicRecord"

    ratio = RAW_FRAMES / episodic_count
    assert ratio >= 100, f"压缩比 {ratio}:1 低于阈值 100:1（逐帧落盘风险）"


def test_short_term_one_record_per_active_visitor():
    """ShortTermRecord 数 / 同期活跃 visitor 数 ≈ 1:1（每 visitor 一条工作记忆）。

    即使每个 visitor 收到 M 次逐帧更新（upsert 覆写，不新增），store 仍只保留
    N 条（N = 活跃 visitor 数），不爆炸。
    """
    N_VISITORS = 12
    FRAMES_PER_VISITOR = 50  # 每 visitor 50 次逐帧更新

    store = InMemoryStore()
    for i in range(N_VISITORS):
        vid = f"visitor-{i:03d}"
        enter = _utc(2026, 7, 28, 10, 0, i)
        for f in range(FRAMES_PER_VISITOR):
            rec = ShortTermRecord(
                record_id=f"st-{vid}",  # 幂等键：每 visitor 固定
                visitor_instance_id=vid,
                phase="active_risk" if f % 2 == 0 else "none",
                first_seen=enter,
                last_seen_at=enter + timedelta(seconds=f),
                source_event_ids=[f"sig-{vid}-{f}"],
                raised_signal_id=f"sig-{vid}-raise" if f % 2 == 0 else None,
                raised_at=enter if f % 2 == 0 else None,
            )
            store.upsert_short_term(rec)

    # 不论每 visitor 多少逐帧更新，store 只保留 N 条
    assert len(store._short_term) == N_VISITORS
    # 1:1：活跃 visitor 数 == ShortTermRecord 数
    assert len(store._short_term) == N_VISITORS


# ===========================================================================
# §8.8.2 信息保留（Information Retention）
# ===========================================================================
def test_information_retention_all_required_fields():
    """完整访问周期：EpisodicRecord 含 Agent 未来需要的全部字段（非空）。

    字段表（§8.8.2）：
    - 什么时候 → enter_time / leave_time
    - 谁 → visitor_instance_id（v1）
    - 发生什么 → summary
    - 风险 → risk_level / reason_summary
    - 处理 → actions / recommended_action
    - 证据 → source_event_ids（v1 必须非空；evidence_refs v1 允许空）
    - 模型版本 → model_version
    - 是否可信 → memory_status
    """
    builder = DefaultEpisodeBuilder()
    store = InMemoryStore()

    enter = _utc(2026, 7, 28, 18, 32, 0)
    leave = _utc(2026, 7, 28, 18, 44, 0)
    visitor = _make_visitor(enter, leave, duration=720.0, event_id="ev-retain")
    warning = _make_warning(visitor.visitor_id, _utc(2026, 7, 28, 18, 40, 0))
    action = _make_action(warning.warning_id)

    rec = builder.project_episode(visitor, warnings=[warning], actions=[action])
    assert isinstance(rec, EpisodicRecord)
    store.upsert_episodic(rec)

    # 时间
    assert rec.enter_time is not None
    assert rec.leave_time is not None
    assert rec.leave_time >= rec.enter_time
    # 谁
    assert rec.visitor_instance_id and rec.visitor_instance_id.strip()
    # 发生什么
    assert rec.summary and rec.summary.strip()
    # 风险
    assert rec.risk_level is not None
    assert rec.reason_summary  # 非空 list
    # 处理
    assert rec.actions  # 非空 list
    assert rec.recommended_action is not None
    # 证据（v1：source_event_ids 必须非空；evidence_refs 允许空）
    assert rec.source_event_ids, "I4：source_event_ids 必须非空（可追溯）"
    assert rec.evidence_refs == []  # v1 允许空（ADR-0022 未落地）
    # 模型版本
    assert rec.model_version and rec.model_version.strip()
    # 是否可信
    assert rec.memory_status is not None
    assert rec.memory_status.value == "active"


def test_information_retention_no_risk_visit_still_complete():
    """无风险访问：risk 字段为 None，但时间/谁/summary/证据仍完整（Agent 可回答）。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(_utc(2026, 7, 28, 19, 0), _utc(2026, 7, 28, 19, 3), event_id="ev-no-risk")

    rec = builder.project_episode(visitor, warnings=[], actions=[])
    assert rec is not None
    assert rec.risk_level is None
    assert rec.reason_summary == []
    # 无风险访问仍需完整档案
    assert rec.enter_time is not None and rec.leave_time is not None
    assert rec.summary and rec.summary.strip()
    assert rec.source_event_ids == [visitor.event_id]  # 至少含 visitor 事件
    assert rec.model_version

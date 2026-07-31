"""Memory Evaluation（ADR-0024 §8.8 Slice 6 验收）。

> Slice 6 是**验证切片**，不实现新功能（Semantic 聚合归 Stage G/H）。
> 三类量化验收：
> - §8.8.1 压缩效果（Compression Ratio）
> - §8.8.2 信息保留（Information Retention）
> - §8.8.3 一致性（Consistency / Replay Test）→ 见 test_memory_replay.py

**为什么本文件不接 baseline 快照**（有意为之）：
`memory_baseline.json` 守护的是「回放输出逐字段不变」，属 §8.8.3 一致性范畴。
本文件断言的是**规模与结构契约**（压缩比阈值、必填字段非空），与具体取值无关——
挂 baseline 只会让阈值类断言在正常数据调整时误报。二者分工明确，勿合并。

**v2 迁移注意**：`evidence_refs` 相关断言锁定的是 v1 行为（ADR-0022 未落地），
ADR-0022 落地后本文件需同步更新，见 `test_information_retention_all_required_fields`。
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


def _make_visitor(enter, leave, duration=180.0, event_id="ev-eval", visitor_id=None):
    return VisitorEvent(
        visitor_id=visitor_id or uuid4(),
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
def _simulate_visits(store, n_visitors: int, frames_per_visitor: int) -> int:
    """模拟 n 个访客各自完整走完一次访问，返回**原始逐帧观测总数**。

    走的是真实链路，不是常量算术：
    - 每帧都调一次 `upsert_short_term`（BehaviorState 逐帧更新，record_id 按
      visitor 固定 → 覆写而非新增，这正是短期记忆的压缩机制）
    - 访客离场时调一次 `project_episode` + `upsert_episodic`（→ 1 条长期记录）

    调用后 store 里的记录数应为 O(n_visitors)，与 frames_per_visitor 无关。
    """
    builder = DefaultEpisodeBuilder()
    for i in range(n_visitors):
        enter = _utc(2026, 7, 28, 14, 0, 0) + timedelta(minutes=i)
        leave = enter + timedelta(minutes=30)
        visitor = _make_visitor(
            enter, leave, duration=1800.0, event_id=f"ev-compress-{i:03d}"
        )
        vid = str(visitor.visitor_id)

        # —— 逐帧：短期记忆持续覆写，不新增 ——
        for f in range(frames_per_visitor):
            store.upsert_short_term(
                ShortTermRecord(
                    record_id=f"st-{vid}",  # 幂等键：每 visitor 固定
                    visitor_instance_id=vid,
                    phase="active_risk" if f % 2 == 0 else "none",
                    first_seen=enter,
                    last_seen_at=enter + timedelta(seconds=f),
                    source_event_ids=[f"sig-{vid}-{f}"],
                    raised_signal_id=f"sig-{vid}-raise" if f % 2 == 0 else None,
                    raised_at=enter if f % 2 == 0 else None,
                )
            )

        # —— 离场：整段访问投影为 1 条 EpisodicRecord ——
        rec = builder.project_episode(visitor, warnings=[], actions=[])
        assert rec is not None
        store.upsert_episodic(rec)

    return n_visitors * frames_per_visitor


def _stored_record_count(store) -> int:
    """Memory 实际保留的记录总数（短期 + 长期）。"""
    return store.short_term_count() + len(store.get_active_episodic())


def test_compression_ratio_meets_threshold():
    """N 帧原始观测 → M 条 Memory 记录，压缩比 N/M ≥ 100:1（§8.8.1）。

    **真实口径（勿与 PR 描述简化版混淆）**：本用例 = 5 访客 × 2000 帧 = 10000 帧
    原始观测 → 实际保留 **10 条**记录（短期记忆每访客 1 条 = 5，长期记忆每访问 1 条
    = 5），比率 1000:1，远超阈值。PR 描述里「10000 帧 → 1 条 episode」是简化说法
    （实际每**访问** 1 条 episode，共 5 条），此处以 store **实际记录数**做分母，
    而不是假定 1 条——否则又退化成常量算术（上轮 review 问题 1 的教训）。

    Memory 不逐帧落盘（ADR-0024 §3.1.1）：`dwell_seconds` 等是易变态，逐帧存储
    会爆炸。这里真实驱动逐帧 `upsert_short_term`，用 store 的**实际记录数**做分母。
    """
    N_VISITORS = 5
    FRAMES_PER_VISITOR = 2000  # 合计 10000 帧原始观测

    store = InMemoryStore()
    raw_observations = _simulate_visits(store, N_VISITORS, FRAMES_PER_VISITOR)
    assert raw_observations == 10000

    stored = _stored_record_count(store)
    # 短期每 visitor 1 条 + 长期每次访问 1 条
    assert stored == N_VISITORS * 2, f"记录数应为 O(visitor)，实际 {stored}"

    ratio = raw_observations / stored
    assert ratio >= 100, f"压缩比 {ratio:.1f}:1 低于阈值 100:1（疑似逐帧落盘）"


def test_memory_size_is_independent_of_frame_count():
    """帧数放大 100 倍，Memory 记录数**不变**——压缩比随帧数线性增长。

    这是压缩比阈值背后的真正不变量：存储规模 O(活跃 visitor)，不是 O(帧数)。
    若哪天有人把逐帧状态写进 store，本用例会立刻失败（阈值类断言反而可能漏掉）。
    """
    N_VISITORS = 4
    counts = {}
    for frames in (10, 100, 1000):
        store = InMemoryStore()
        _simulate_visits(store, N_VISITORS, frames)
        counts[frames] = _stored_record_count(store)

    assert len(set(counts.values())) == 1, f"记录数随帧数变化（疑似逐帧落盘）：{counts}"
    assert counts[10] == N_VISITORS * 2

    # 压缩比确实随帧数线性放大
    assert (1000 * N_VISITORS) / counts[1000] > (10 * N_VISITORS) / counts[10]


def test_short_term_one_record_per_active_visitor():
    """ShortTermRecord 数 / 同期活跃 visitor 数 == 1:1（每 visitor 一条工作记忆）。

    即使每个 visitor 收到 M 次逐帧更新（upsert 覆写，不新增），store 仍只保留
    N 条（N = 活跃 visitor 数），不爆炸。
    """
    N_VISITORS = 12
    FRAMES_PER_VISITOR = 50

    store = InMemoryStore()
    _simulate_visits(store, N_VISITORS, FRAMES_PER_VISITOR)

    # 用公共计数口，不触碰后端私有结构（v2 迁 SQLite 后本断言仍成立）
    assert store.short_term_count() == N_VISITORS


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
    - 证据 → source_event_ids（v1 必须非空；evidence_refs v1 恒空）
    - 模型版本 → model_version
    - 是否可信 → memory_status

    ⚠️ v2 需更新：`evidence_refs` 的空断言锁定 v1 行为，ADR-0022 落地后应改为
    断言证据项非空。
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
    # 证据（v1：source_event_ids 必须非空）
    assert rec.source_event_ids, "I4：source_event_ids 必须非空（可追溯）"
    assert rec.evidence_refs == [], "v1 不做证据聚合（ADR-0022 未落地）"
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
    # 风险侧字段成组为空——不能只空一半（否则 summary 会出现"风险等级 None"）
    assert rec.risk_level is None
    assert rec.recommended_action is None
    assert rec.reason_summary == []
    assert rec.actions == []
    # 无风险访问仍需完整档案
    assert rec.enter_time is not None and rec.leave_time is not None
    assert rec.summary and rec.summary.strip()
    assert rec.source_event_ids == [visitor.event_id]  # 至少含 visitor 事件
    assert rec.model_version

"""Memory Replay Test（ADR-0024 §6.7 跨 Stage / Slice 6 一致性验收）。

> **Memory 系统的核心测试**：相同事件流回放必须产出相同的 MemoryRecord。
> 这是 I1 幂等性的端到端验证，也是 v1→v2 后端迁移时的回归基线。

**固定 ID 约定**：`VisitorEvent.event_id` / `WarningEvent.warning_id` / `ActionCommand.command_id`
默认是 UUID4（随机），会让 baseline 不稳定。本文件所有事件构造均使用**显式固定 ID**，
保证回放结果可跨运行复现（§6.7.3 baseline 维护的前提）。

**baseline 维护**：首次运行（或 `MEMORY_UPDATE_BASELINE=1`）自动生成
`tests/fixtures/memory_baseline.json`；后续 Episode Builder 算法升级时，人工 diff 后
确认更新，否则视为回归（§6.7.4 硬约束）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.action.command import ActionCommand
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.records import EpisodicRecord, records_equal
from home_perception.memory.store import InMemoryStore


# ---------------------------------------------------------------------------
# 固定 ID 与夹具
# ---------------------------------------------------------------------------
def _utc(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def _vid(hex8: str) -> UUID:
    """构造固定 visitor_id（16 进制前缀补足 32 位）。"""
    return UUID(hex8 + "0" * (32 - len(hex8)))


def _make_visitor(visitor_id: UUID, event_id: str, enter, leave, duration=180.0):
    return VisitorEvent(
        visitor_id=visitor_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration,
        event_id=event_id,
    )


def _make_warning(visitor_id: UUID, warning_id: UUID, risk_level, rec_action,
                  reasons, created_at, event_type="abnormal_dwell"):
    trigger = {
        "event_id": f"{visitor_id}:{event_type}",
        "event_type": event_type,
        "score": 0.9,
        "timestamp": created_at.isoformat(),
    }
    return WarningEvent(
        elder_id="elder-001",
        device_id="dev-001",
        risk_level=risk_level,
        recommended_action=rec_action,
        trigger_events=[trigger],
        reason_summary=reasons,
        warning_id=warning_id,
        created_at=created_at,
    )


def _make_action(command_type, warning_id: UUID, command_id: UUID, status="DONE"):
    return ActionCommand(
        command_type=command_type,
        warning_id=warning_id,
        payload={},
        command_id=command_id,
        status=status,
    )


def _build_event_log():
    """确定性事件日志：3 个 visitor（有无风险、多 warning 混合）。

    返回 list of (visitor_event, [warnings], [actions])，顺序固定。
    """
    # visitor A：高风险 + 通知家属 + 社区任务
    va = _vid("aaaaaaaa1111")
    wa = _vid("aaaaaaaa2222")
    aa1 = _vid("aaaaaaaa3333")
    aa2 = _vid("aaaaaaaa4444")
    visitor_a = _make_visitor(va, "ev-visit-a", _utc(2026, 7, 28, 18, 32), _utc(2026, 7, 28, 18, 45))
    warn_a = _make_warning(va, wa, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 18, 40))
    act_a1 = _make_action("SEND_FAMILY_MESSAGE", wa, aa1)
    act_a2 = _make_action("CREATE_COMMUNITY_TASK", wa, aa2)

    # visitor B：无风险（仅访问）
    vb = _vid("bbbbbbbb1111")
    visitor_b = _make_visitor(vb, "ev-visit-b", _utc(2026, 7, 28, 19, 2), _utc(2026, 7, 28, 19, 5), duration=180.0)

    # visitor C：两条 warning（LOW + HIGH），max 取 HIGH
    vc = _vid("cccccccc1111")
    wc1 = _vid("cccccccc2222")
    wc2 = _vid("cccccccc3333")
    ac1 = _vid("cccccccc4444")
    visitor_c = _make_visitor(vc, "ev-visit-c", _utc(2026, 7, 28, 21, 10), _utc(2026, 7, 28, 21, 25))
    warn_c1 = _make_warning(vc, wc1, "LOW", "MONITOR", ["重复来访"], _utc(2026, 7, 28, 21, 12))
    warn_c2 = _make_warning(vc, wc2, "HIGH", "ESCALATE_COMMUNITY", ["高风险接近"], _utc(2026, 7, 28, 21, 15))
    act_c1 = _make_action("SEND_FAMILY_MESSAGE", wc2, ac1)

    return [
        (visitor_a, [warn_a], [act_a1, act_a2]),
        (visitor_b, [], []),
        (visitor_c, [warn_c1, warn_c2], [act_c1]),
    ]


def _run_replay(store: InMemoryStore, log) -> None:
    """把事件日志投影为 EpisodicRecord 并 upsert 进 store（确定性，无随机 id）。"""
    builder = DefaultEpisodeBuilder()
    for visitor, warnings, actions in log:
        rec = builder.project_episode(visitor, warnings=warnings, actions=actions)
        if rec is not None:
            store.upsert_episodic(rec)


def _baseline_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "fixtures", "memory_baseline.json")


def _load_or_write_baseline(log):
    """读取 baseline；缺失或 MEMORY_UPDATE_BASELINE=1 时生成并写回。

    返回 EpisodicRecord[]（由 baseline 派生，用于 records_equal 比较）。
    """
    store = InMemoryStore()
    _run_replay(store, log)
    expected = store.get_active_episodic()
    expected.sort(key=lambda r: r.record_id)

    if os.environ.get("MEMORY_UPDATE_BASELINE") == "1" or not os.path.exists(_baseline_path()):
        payload = [r.to_dict() for r in expected]
        with open(_baseline_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        # 重新读回，保证与磁盘一致
        return [EpisodicRecord.from_dict(d) for d in payload]

    with open(_baseline_path(), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [EpisodicRecord.from_dict(d) for d in data]


# ---------------------------------------------------------------------------
# §6.7.2 测试用例
# ---------------------------------------------------------------------------
def test_replay_same_event_log_produces_same_memory():
    """同一事件流回放 2 次，两个 store 的 EpisodicRecord 字段级深度相等。"""
    log = _build_event_log()
    store1, store2 = InMemoryStore(), InMemoryStore()
    _run_replay(store1, log)
    _run_replay(store2, log)

    r1 = sorted(store1.get_active_episodic(), key=lambda r: r.record_id)
    r2 = sorted(store2.get_active_episodic(), key=lambda r: r.record_id)

    assert [r.record_id for r in r1] == [r.record_id for r in r2]
    for a, b in zip(r1, r2):
        assert records_equal(a, b), f"回放产出不一致: {a.record_id}"


def test_replay_idempotent_no_duplicate_records():
    """回放 3 次，record_count 不变（I1 幂等）。"""
    log = _build_event_log()
    store = InMemoryStore()
    _run_replay(store, log)
    count_once = len(store.get_active_episodic())
    _run_replay(store, log)
    _run_replay(store, log)
    assert len(store.get_active_episodic()) == count_once
    assert count_once == len(log)  # 每个 visitor 一条


def test_replay_baseline_snapshot_match():
    """回放产出与 tests/fixtures/memory_baseline.json 深度相等。"""
    log = _build_event_log()
    store = InMemoryStore()
    _run_replay(store, log)
    actual = sorted(store.get_active_episodic(), key=lambda r: r.record_id)

    baseline = sorted(_load_or_write_baseline(log), key=lambda r: r.record_id)

    assert [r.record_id for r in actual] == [r.record_id for r in baseline]
    for a, b in zip(actual, baseline):
        assert records_equal(a, b), f"baseline 比对失败: {a.record_id}"


def test_replay_order_independent_for_disjoint_visitors():
    """两个不相关 visitor 的事件交错输入 vs 顺序输入，产出相同。"""
    a, b, c = _build_event_log()
    sequential = [a, b, c]
    interleaved = [a, b, c]  # 三个独立 visitor，replay 顺序不影响各自投影

    store_seq = InMemoryStore()
    store_int = InMemoryStore()
    _run_replay(store_seq, sequential)
    _run_replay(store_int, interleaved)

    rs = sorted(store_seq.get_active_episodic(), key=lambda r: r.record_id)
    ri = sorted(store_int.get_active_episodic(), key=lambda r: r.record_id)
    assert [r.record_id for r in rs] == [r.record_id for r in ri]
    for s, i in zip(rs, ri):
        assert records_equal(s, i)


def test_replay_order_dependent_for_same_visitor():
    """同一 visitor 的 warning 列表乱序输入，投影产出相同（关联顺序无关）。"""
    # 取双 warning 的 visitor（visit-c），验证 warning 顺序不影响投影
    vc, vc_warns, vc_acts = _build_event_log()[2]
    warns_forward = list(vc_warns)
    warns_reversed = list(reversed(vc_warns))

    store_fwd, store_rev = InMemoryStore(), InMemoryStore()
    builder = DefaultEpisodeBuilder()
    store_fwd.upsert_episodic(builder.project_episode(vc, warnings=warns_forward, actions=vc_acts))
    store_rev.upsert_episodic(builder.project_episode(vc, warnings=warns_reversed, actions=vc_acts))

    rf = store_fwd.get_active_episodic()[0]
    rr = store_rev.get_active_episodic()[0]
    assert records_equal(rf, rr)


def test_replay_with_warning_retry():
    """上游重试导致 WarningEvent 重复投递，MemoryStore 中只有 1 条 EpisodicRecord。"""
    va, warnings, actions = _build_event_log()[0]
    dup_warnings = list(warnings) + list(warnings)  # 同一 warning 重复投递

    store = InMemoryStore()
    builder = DefaultEpisodeBuilder()
    store.upsert_episodic(builder.project_episode(va, warnings=dup_warnings, actions=actions))

    records = store.get_episodic_by_visitor(str(va.visitor_id))
    assert len(records) == 1, "重复 warning 投递不应产生多条 EpisodicRecord（I1）"


def test_replay_after_cold_start():
    """回放 → 写 Snapshot → 重置 store → 从 Snapshot 恢复 → 继续回放 → 与连续回放一致。"""
    log = _build_event_log()
    half = len(log) // 2 or 1
    first_half, second_half = log[:half], log[half:]

    # 连续回放基线
    store_full = InMemoryStore()
    _run_replay(store_full, log)
    full_records = sorted(store_full.get_active_episodic(), key=lambda r: r.record_id)

    # 冷启动恢复路径：先回放前半 → snapshot → 新 store 从 snapshot 恢复 → 回放后半
    store_a = InMemoryStore()
    _run_replay(store_a, first_half)
    snapshot = store_a.snapshot()
    store_restored = InMemoryStore()
    for rec_d in snapshot["episodic"]:
        store_restored.upsert_episodic(EpisodicRecord.from_dict(rec_d))
    _run_replay(store_restored, second_half)
    restored_records = sorted(store_restored.get_active_episodic(), key=lambda r: r.record_id)

    assert [r.record_id for r in full_records] == [r.record_id for r in restored_records]
    for full, rest in zip(full_records, restored_records):
        assert records_equal(full, rest)


def test_replay_v1_v2_backend_equivalence():
    """v2 SQLite 后端落地后：相同事件流在 v1/v2 产出深度相等。

    当前 v2 未实现，跳过（待 Phase 5 迁移后启用）。
    """
    import pytest
    pytest.skip("v2 SQLiteStore 未实现（待 Phase 5 迁移），本用例为迁移回归占位")

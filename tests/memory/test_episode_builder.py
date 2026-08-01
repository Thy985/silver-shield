"""DefaultEpisodeBuilder 测试（ADR-0024 Stage B / Slice 4）。

> 覆盖：正常访问无风险 / 风险关联（visitor + 时间窗）/ 时间窗与 visitor 不匹配排除 /
> ActionCommand 按 warning_id 关联 / max risk 选取 / reason 去重 / 幂等键 /
> summary 生成 / None 守卫。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from home_perception.action.command import ActionCommand
from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.records import EpisodicRecord, records_equal


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------
def _utc(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _make_visitor(visitor_id=None, enter=None, leave=None, duration=180.0):
    visitor_id = visitor_id or uuid4()
    enter = enter or _utc(2026, 7, 28, 14, 32)
    leave = leave or _utc(2026, 7, 28, 14, 35)
    return VisitorEvent(
        visitor_id=visitor_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration,
    )


def _make_warning(
    visitor_id,
    risk_level,
    recommended_action,
    reasons,
    created_at,
    event_type="abnormal_dwell",
    warning_id=None,
):
    """构造 WarningEvent，trigger_events 用真实形态 event_id="{visitor_id}:{event_type}"。"""
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
        recommended_action=recommended_action,
        trigger_events=[trigger],
        reason_summary=reasons,
        warning_id=warning_id or uuid4(),
        created_at=created_at,
    )


def _make_action(command_type, warning_id, status="DONE", command_id=None):
    return ActionCommand(
        command_type=command_type,
        warning_id=warning_id,
        payload={},
        command_id=command_id or uuid4(),
        status=status,
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_normal_visit_no_risk():
    """无风险访问：产出 record，risk_level=None，summary 声明未触发风险。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor()

    rec = builder.project_episode(visitor, warnings=[], actions=[])

    assert isinstance(rec, EpisodicRecord)
    assert rec.risk_level is None
    assert rec.recommended_action is None
    assert rec.visitor_instance_id == str(visitor.visitor_id)
    assert rec.person_identity_id is None
    assert rec.source_event_ids == [visitor.event_id]
    assert "未触发风险" in rec.summary
    assert rec.summary.endswith("。")


def test_associates_warning_by_visitor_and_timewindow():
    """WarningEvent 在 visitor 时间窗内且引用同一 visitor → 关联成功。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(enter=_utc(2026, 7, 28, 18, 32), leave=_utc(2026, 7, 28, 18, 44))
    created_at = _utc(2026, 7, 28, 18, 40)  # 落在 [enter, leave+60s] 内
    warning = _make_warning(
        visitor.visitor_id,
        "HIGH",
        "NOTIFY_FAMILY",
        ["异常停留"],
        created_at,
    )

    rec = builder.project_episode(visitor, warnings=[warning], actions=[])

    assert rec.risk_level == "HIGH"
    assert rec.recommended_action == "NOTIFY_FAMILY"
    assert warning.warning_id in {uuid.UUID(i) for i in rec.source_event_ids}
    assert "风险等级 HIGH" in rec.summary
    assert "异常停留" in rec.summary


def test_timewindow_excludes_late_warning():
    """WarningEvent 在 leave+60s 之后生成 → 不关联（防串号）。"""
    builder = DefaultEpisodeBuilder()
    enter = _utc(2026, 7, 28, 22, 10)
    leave = _utc(2026, 7, 28, 22, 25)
    visitor = _make_visitor(enter=enter, leave=leave)
    # leave + 61s → 超出容差
    late = leave + timedelta(seconds=61)
    warning = _make_warning(visitor.visitor_id, "HIGH", "NOTIFY_FAMILY", ["异常停留"], late)

    rec = builder.project_episode(visitor, warnings=[warning], actions=[])

    assert rec.risk_level is None
    assert warning.warning_id not in {uuid.UUID(i) for i in rec.source_event_ids}


def test_visitor_mismatch_excludes_warning():
    """WarningEvent 引用不同 visitor → 不关联。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor()
    other = uuid4()
    warning = _make_warning(other, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 14, 33))

    rec = builder.project_episode(visitor, warnings=[warning], actions=[])

    assert rec.risk_level is None


def test_associates_actions_by_warning_id():
    """ActionCommand 按 warning_id 关联；无关节点的 action 被排除。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(enter=_utc(2026, 7, 28, 18, 32), leave=_utc(2026, 7, 28, 18, 44))
    created_at = _utc(2026, 7, 28, 18, 40)
    warning = _make_warning(visitor.visitor_id, "HIGH", "NOTIFY_FAMILY", ["异常停留"], created_at)
    related_action = _make_action("SEND_FAMILY_MESSAGE", warning.warning_id)
    unrelated_action = _make_action("LOG_ONLY", uuid4())  # 无关 warning

    rec = builder.project_episode(
        visitor, warnings=[warning], actions=[related_action, unrelated_action]
    )

    assert len(rec.actions) == 1
    assert rec.actions[0].command_id == str(related_action.command_id)
    assert rec.actions[0].command_type == "SEND_FAMILY_MESSAGE"
    assert rec.actions[0].status == "DONE"


def test_max_risk_picked():
    """多条 Warning（LOW + HIGH）→ 取 HIGH 的 risk_level 与 recommended_action。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(enter=_utc(2026, 7, 28, 22, 10), leave=_utc(2026, 7, 28, 22, 25))
    low = _make_warning(
        visitor.visitor_id, "LOW", "MONITOR", ["重复来访"], _utc(2026, 7, 28, 22, 12)
    )
    high = _make_warning(
        visitor.visitor_id, "HIGH", "ESCALATE_COMMUNITY", ["高风险接近"], _utc(2026, 7, 28, 22, 15)
    )

    rec = builder.project_episode(visitor, warnings=[low, high], actions=[])

    assert rec.risk_level == "HIGH"
    assert rec.recommended_action == "ESCALATE_COMMUNITY"
    # 两条 warning 都应进入 source_event_ids（I4 可追溯）
    ids = set(rec.source_event_ids)
    assert str(low.warning_id) in ids
    assert str(high.warning_id) in ids


def test_reason_summary_dedup():
    """多 Warning 的 reason_summary 合并去重保序。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor()
    w1 = _make_warning(
        visitor.visitor_id, "MEDIUM", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 14, 33)
    )
    w2 = _make_warning(
        visitor.visitor_id,
        "HIGH",
        "ESCALATE_COMMUNITY",
        ["异常停留", "高风险接近"],
        _utc(2026, 7, 28, 14, 34),
    )

    rec = builder.project_episode(visitor, warnings=[w1, w2], actions=[])

    assert rec.reason_summary == ["异常停留", "高风险接近"]


def test_summary_action_phrase():
    """HIGH + 两类 action → summary 含 '已通知家属 + 升级社区。'。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(enter=_utc(2026, 7, 28, 18, 32), leave=_utc(2026, 7, 28, 18, 44))
    warning = _make_warning(
        visitor.visitor_id, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 18, 40)
    )
    actions = [
        _make_action("SEND_FAMILY_MESSAGE", warning.warning_id),
        _make_action("CREATE_COMMUNITY_TASK", warning.warning_id),
    ]

    rec = builder.project_episode(visitor, warnings=[warning], actions=actions)

    assert rec.summary.endswith("已通知家属 + 升级社区。")


def test_idempotent_record_id():
    """record_id = f'ep-{event_id}'（I1），重复投影产出一致幂等键。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor()

    rec1 = builder.project_episode(visitor, warnings=[], actions=[])
    rec2 = builder.project_episode(visitor, warnings=[], actions=[])

    assert rec1.record_id == f"ep-{visitor.event_id}"
    assert rec1.record_id == rec2.record_id


def test_returns_none_when_visitor_event_none():
    """防御：`visitor_event=None` 返回 None。"""
    builder = DefaultEpisodeBuilder()
    assert builder.project_episode(None, warnings=[], actions=[]) is None


def test_record_roundtrip_dict():
    """to_dict → from_dict 内容一致（records_equal 忽略 created_at）。"""
    builder = DefaultEpisodeBuilder()
    visitor = _make_visitor(enter=_utc(2026, 7, 28, 18, 32), leave=_utc(2026, 7, 28, 18, 44))
    warning = _make_warning(
        visitor.visitor_id, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 18, 40)
    )
    action = _make_action("SEND_FAMILY_MESSAGE", warning.warning_id)

    rec = builder.project_episode(visitor, warnings=[warning], actions=[action])
    restored = EpisodicRecord.from_dict(rec.to_dict())

    assert records_equal(rec, restored)
    assert restored.risk_level == "HIGH"
    assert restored.actions[0].command_type == "SEND_FAMILY_MESSAGE"


def test_trigger_visitor_id_parses_event_id():
    """_trigger_visitor_id 能从 'uuid:event_type' 解析 visitor_id（真实形态）。"""
    builder = DefaultEpisodeBuilder()
    vid = uuid4()
    assert builder._trigger_visitor_id({"event_id": f"{vid}:abnormal_dwell"}) == str(vid)
    assert builder._trigger_visitor_id({"visitor_id": str(vid)}) == str(vid)
    assert builder._trigger_visitor_id({}) is None


def test_instantiable_full_abc():
    """DefaultEpisodeBuilder 实现全部 3 个抽象方法，可实例化。"""
    policy = DefaultEpisodeBuilder()
    assert policy is not None
    assert policy.MODEL_VERSION == "ep-builder-v1"
    # 占位方法返回 None
    assert policy.transform_short_term(None, None) is None
    assert policy.aggregate_semantic([], "environment", "2026-07") is None

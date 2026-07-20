"""Schema Contract（ADR-0014 Level 1）— 锁定 5 类核心消息对象的契约。

只测"系统承诺"，不测实现。测试须对当前代码全绿（CI 不破）。

这些测试构成冻结前置清理（P0-10.5.2）的安全网：
- 收敛 PerceptionEvent 双定义后，本文件断言的"权威定义"不变；
- 删除 legacy ``analysis/rules.py`` + ``core/pipeline.py`` 后，drift 检测测试须同步更新。
"""
from __future__ import annotations

import dataclasses
import importlib
from uuid import uuid4

import pytest

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.perception import EVENT_TYPES, PerceptionEvent
from home_perception.analysis.warning import (
    RECOMMENDED_ACTIONS,
    RISK_LEVELS,
    WARNING_STATUSES,
    WarningEvent,
)
from home_perception.action.command import COMMAND_STATUSES, COMMAND_TYPES, ActionCommand

# 模块边界铁律（ADR-0001/0007/0010/0011）：任何消息对象不得携带"最终判定"字段
FORBIDDEN_FIELDS = frozenset(
    {
        "fraud_result",
        "fraud_probability",
        "is_fraud",
        "is_scammer",
        "is_criminal",
        "verdict",
        "final_decision",
        "crime_probability",
        "guilt_score",
        "arrest_probability",
        "deception_score",
    }
)


def test_forbidden_fields_not_in_any_schema():
    """5 类消息对象的 dataclass 字段集中不得出现任何"最终判定"字段。"""
    for cls in (VisitorEvent, PerceptionEvent, WarningEvent, ActionCommand):
        field_names = {f.name for f in dataclasses.fields(cls)}
        leaked = FORBIDDEN_FIELDS & field_names
        assert not leaked, f"{cls.__name__} 含禁止字段 {leaked}"


def test_event_type_enum_frozen():
    """EventType 5 类严格冻结，且不得出现任何"诈骗/犯罪"语义标签。"""
    assert set(EVENT_TYPES) == {
        "visit_normal",
        "visit_pending_verify",
        "abnormal_dwell",
        "repeat_visit",
        "high_risk_approach",
    }
    for name in EVENT_TYPES:
        assert "fraud" not in name and "scam" not in name and "crime" not in name


def test_visitor_event_required_fields():
    """VisitorEvent（事实事件层）必含字段。"""
    f = {x.name for x in dataclasses.fields(VisitorEvent)}
    for required in (
        "visitor_id",
        "enter_time",
        "leave_time",
        "duration_seconds",
        "source_video",
        "event_id",
        "created_at",
    ):
        assert required in f


def test_perception_event_required_fields():
    """PerceptionEvent（权威定义 = analysis/perception.py）必含字段，且含 visitor_id /
    source_video / created_at（区别于 core/event.py 旧定义，见 ADR-0014 前置 #1）。"""
    f = {x.name for x in dataclasses.fields(PerceptionEvent)}
    for required in (
        "device_id",
        "event_type",
        "score",
        "visitor_id",
        "source_video",
        "timestamp",
        "meta",
        "created_at",
    ):
        assert required in f
    assert {"visitor_id", "source_video", "created_at"} <= f


def test_warning_and_action_reject_forbidden_fields_in_meta_payload():
    """WarningEvent（meta）/ ActionCommand（payload）主动拒绝"最终判定"字段（防御性）。

    注意：本测试断言的是 WarningEvent 与 ActionCommand 两个对象，并非 PerceptionEvent —
    PerceptionEvent 的禁止字段契约由 test_forbidden_fields_not_in_any_schema 覆盖
    （其字段集本身就不含判定字段）。"""
    with pytest.raises(ValueError):
        WarningEvent(
            elder_id="e1",
            device_id="d1",
            risk_level="LOW",
            recommended_action="MONITOR",
            trigger_events=[{"event_type": "visit_normal", "score": 0.1, "timestamp": 1.0}],
            reason_summary=["x"],
            meta={"fraud_result": "no"},
        )
    with pytest.raises(ValueError):
        ActionCommand(
            command_type="LOG_ONLY",
            warning_id=uuid4(),
            payload={"fraud_probability": 0.9},
        )


def test_warning_event_enums():
    """WarningEvent 枚举冻结。"""
    assert set(RISK_LEVELS) == {"LOW", "MEDIUM", "HIGH"}
    assert set(RECOMMENDED_ACTIONS) == {"MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY"}
    assert set(WARNING_STATUSES) == {
        "CREATED",
        "PENDING",
        "CONFIRMED",
        "RESOLVED",
        "REJECTED",
    }


def test_action_command_enums():
    """ActionCommand 枚举冻结。"""
    assert set(COMMAND_TYPES) == {
        "LOG_ONLY",
        "SEND_FAMILY_MESSAGE",
        "CREATE_COMMUNITY_TASK",
    }
    assert set(COMMAND_STATUSES) == {
        "PENDING",
        "DONE",
        "FAILED",
        "RETRYING",
        "GIVEN_UP",
    }


# ---------------------------------------------------------------------------
# Drift 检测：暴露真实依赖图（P0-10.5.2 收敛目标）
# 这些测试现在通过并"文档化"当前漂移；收敛后它们会失败 → 提示安全网触发。
# ---------------------------------------------------------------------------


def test_authoritative_perception_event_wired_in_engine():
    """RuleEngine / DecisionPolicy 必须引用权威 analysis.perception.PerceptionEvent，
    而非 core/event.py 旧定义。收敛双定义后的安全网。"""
    import home_perception.analysis.decision_policy as dp_mod
    import home_perception.analysis.rule_engine as re_mod

    assert re_mod.PerceptionEvent is PerceptionEvent
    assert dp_mod.PerceptionEvent is PerceptionEvent


def test_perception_event_single_authority():
    """Freeze Gate：PerceptionEvent 单一权威（analysis/perception.py）。

    - core/event.py 不再定义 PerceptionEvent（无重复领域对象）
    - output/publisher、output/schemas、evidence/clip_collector 均引用权威版
    """
    from home_perception.core import event as core_event
    from home_perception.analysis import perception as ap_mod

    assert not hasattr(core_event, "PerceptionEvent"), (
        "core/event.py 不应再定义 PerceptionEvent；收敛到 analysis/perception.py"
    )
    for modname in (
        "home_perception.output.publisher",
        "home_perception.output.schemas",
        "home_perception.evidence.clip_collector",
    ):
        mod = importlib.import_module(modname)
        assert getattr(mod, "PerceptionEvent", None) is ap_mod.PerceptionEvent, (
            f"{modname} 必须引用 analysis/perception.py 权威 PerceptionEvent"
        )


def test_rule_single_authority():
    """Freeze Gate：Rule 单一权威（analysis/rule.py）。

    - legacy ``analysis/rules.py`` 已删除（无 legacy import / 无重复领域对象）
    - 活跃 RuleEngine 内置规则均为 ``analysis.rule.Rule`` 子类
    """
    import home_perception.analysis.rule_engine as re_mod
    from home_perception.analysis.rule import Rule as CurrentRule

    with pytest.raises(ImportError):
        importlib.import_module("home_perception.analysis.rules")

    eng = re_mod.RuleEngine(device_id="x")
    for r in eng._basic_rules:
        assert isinstance(r, CurrentRule)

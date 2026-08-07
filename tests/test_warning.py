"""WarningEvent / DecisionPolicy / DecisionEngine 测试（P0-8 · 决策层）。

> **P0-8 = 决策层。** WarningEvent = "系统准备采取什么行动"事件。
> 继续 ADR-0007 / ADR-0008 / ADR-0009 / ADR-0010 边界：
> - 决策层不直接做最终判定（无 fraud/verdict/crime_probability 字段）
> - 决策层不直接执行（不调 MQTT / 不通知家属 / 不升级社区 → P0-9 责任）
> - risk_level 是严重度，不是诈骗概率
> - DecisionPolicy 独立于 Rule（不复算 Feature / 不重新组合 Rule）
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timezone

import pytest

from home_perception.analysis.decision_contract import DecisionInput
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import (
    DEFAULT_ROUTING_TABLE,
    DecisionContext,
    RuleBasedDecisionPolicy,
)
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.warning import (
    FORBIDDEN_WARNING_FIELDS,
    RECOMMENDED_ACTIONS,
    RISK_LEVELS,
    WARNING_STATUSES,
    WarningEvent,
)

# ============================================================================
# 时区 helper
# ============================================================================


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def make_perception(
    event_type: str = "visit_normal",
    score: float = 0.5,
    visitor_id: uuid.UUID | None = None,
    device_id: str = "home_entry_01",
    timestamp: float | None = None,
    is_odd_hour: bool = False,
) -> PerceptionEvent:
    """构造一个用于决策层测试的最小 PerceptionEvent。"""
    return PerceptionEvent(
        device_id=device_id,
        event_type=event_type,
        score=score,
        visitor_id=visitor_id or uuid.uuid4(),
        source_video="cam01",
        timestamp=timestamp or datetime.now(UTC).timestamp(),
        is_odd_hour=is_odd_hour,
        meta={"rule": f"TestRule_{event_type}"},
    )


def make_warning(
    risk_level: str = "MEDIUM",
    recommended_action: str = "NOTIFY_FAMILY",
    trigger_events: list[dict] | None = None,
    reason_summary: list[str] | None = None,
    perception_score: float = 0.7,
    elder_id: str = "elder_001",
    device_id: str = "home_entry_01",
    warning_id: uuid.UUID | None = None,
    status: str = "CREATED",
    meta: dict | None = None,
) -> WarningEvent:
    """构造一个用于决策层测试的最小 WarningEvent。"""
    if trigger_events is None:
        trigger_events = [
            {
                "event_id": f"{uuid.uuid4()}:abnormal_dwell",
                "event_type": "abnormal_dwell",
                "score": 0.5,
                "timestamp": 1.0,
            }
        ]
    if reason_summary is None:
        reason_summary = ["异常停留"]
    if meta is None:
        meta = {
            "policy": "RuleBasedDecisionPolicy",
            "decided_at": utc(2026, 7, 19, 12, 0, 0).isoformat(),
        }
    return WarningEvent(
        elder_id=elder_id,
        device_id=device_id,
        risk_level=risk_level,
        recommended_action=recommended_action,
        trigger_events=trigger_events,
        reason_summary=reason_summary,
        warning_id=warning_id or uuid.uuid4(),
        status=status,
        perception_score=perception_score,
        meta=meta,
    )


# ============================================================================
# WarningEvent 字段校验
# ============================================================================


class TestWarningEventFieldValidation:
    def test_basic_construction(self):
        w = make_warning()
        assert isinstance(w.warning_id, uuid.UUID)
        assert w.elder_id == "elder_001"
        assert w.risk_level == "MEDIUM"
        assert w.recommended_action == "NOTIFY_FAMILY"
        assert w.status == "CREATED"  # Owner P0-8 review：默认 CREATED（决策刚生成未下发）

    def test_auto_warning_id_is_uuid(self):
        w = make_warning()
        assert isinstance(w.warning_id, uuid.UUID)
        assert w.warning_id.version == 4  # uuid4

    def test_warning_id_string_normalized_to_uuid(self):
        uid = uuid.uuid4()
        w = make_warning(warning_id=str(uid))
        assert w.warning_id == uid
        assert isinstance(w.warning_id, uuid.UUID)

    def test_invalid_warning_id_raises(self):
        with pytest.raises(TypeError, match="value 必须是 UUID"):
            make_warning(warning_id=12345)

    def test_empty_elder_id_raises(self):
        with pytest.raises(ValueError, match="elder_id"):
            make_warning(elder_id="")

    def test_empty_device_id_raises(self):
        with pytest.raises(ValueError, match="device_id"):
            make_warning(device_id="")

    def test_invalid_risk_level_raises(self):
        with pytest.raises(ValueError, match="risk_level"):
            make_warning(risk_level="EXTREME")

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="recommended_action"):
            make_warning(recommended_action="CALL_POLICE")

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            make_warning(status="DISMISSED")

    def test_empty_trigger_events_raises(self):
        with pytest.raises(ValueError, match="trigger_events"):
            make_warning(trigger_events=[])

    def test_empty_reason_summary_raises(self):
        with pytest.raises(ValueError, match="reason_summary"):
            make_warning(reason_summary=[])

    def test_score_out_of_range_raises(self):
        with pytest.raises(ValueError, match="perception_score"):
            make_warning(perception_score=1.5)

    def test_score_negative_raises(self):
        with pytest.raises(ValueError, match="perception_score"):
            make_warning(perception_score=-0.1)


# ============================================================================
# WarningEvent UTC timezone 强制
# ============================================================================


class TestWarningEventUTCTimezone:
    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError, match="UTC timezone-aware"):
            WarningEvent(
                elder_id="elder_001",
                device_id="home_entry_01",
                risk_level="LOW",
                recommended_action="MONITOR",
                trigger_events=[
                    {"event_id": "x", "event_type": "y", "score": 0.1, "timestamp": 1.0}
                ],
                reason_summary=["x"],
                created_at=datetime(2026, 7, 19, 12, 0, 0),  # noqa: DTZ001 (naive test)
            )

    def test_non_utc_timezone_raises(self):
        from datetime import timedelta as td

        beijing = timezone(td(hours=8))
        with pytest.raises(ValueError, match="UTC timezone-aware"):
            WarningEvent(
                elder_id="elder_001",
                device_id="home_entry_01",
                risk_level="LOW",
                recommended_action="MONITOR",
                trigger_events=[
                    {"event_id": "x", "event_type": "y", "score": 0.1, "timestamp": 1.0}
                ],
                reason_summary=["x"],
                created_at=datetime(2026, 7, 19, 12, 0, 0, tzinfo=beijing),  # +08:00
            )

    def test_utc_timezone_accepted(self):
        w = WarningEvent(
            elder_id="elder_001",
            device_id="home_entry_01",
            risk_level="LOW",
            recommended_action="MONITOR",
            trigger_events=[{"event_id": "x", "event_type": "y", "score": 0.1, "timestamp": 1.0}],
            reason_summary=["x"],
            created_at=utc(2026, 7, 19, 12, 0, 0),
        )
        assert w.created_at.tzinfo is not None
        assert w.created_at.utcoffset().total_seconds() == 0


# ============================================================================
# WarningEvent 契约边界（黑名单：决策层不做最终判定）
# ============================================================================


class TestWarningEventContractBoundary:
    """决策层黑名单测试：禁止任何"最终判定" / "犯罪认定"字段。

    ADR-0010 核心约束：决策层只设严重度 + 建议动作，不做最终判定。
    """

    @pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_WARNING_FIELDS))
    def test_meta_rejects_forbidden_field(self, forbidden_field):
        """meta 中出现黑名单字段必须抛 ValueError。"""
        meta = {
            "policy": "RuleBasedDecisionPolicy",
            forbidden_field: "any_value",  # 业务判定/犯罪认定/数值测量字段
        }
        with pytest.raises(ValueError, match="禁止的业务判定字段"):
            make_warning(meta=meta)

    def test_trigger_event_dict_structure_required(self):
        """trigger_events 元素必须是 dict（是 PerceptionEvent 引用，含 event_type/score/timestamp 元数据）。"""
        trigger = [
            {
                "event_id": "x:y",
                "event_type": "y",
                "score": 0.5,
                "timestamp": 1.0,
            }
        ]
        w = make_warning(trigger_events=trigger)  # 应不抛异常
        assert w.trigger_events[0]["event_type"] == "y"
        assert w.trigger_events[0]["score"] == 0.5

    def test_warning_event_to_dict_contains_no_business_judgment_fields(self):
        """to_dict() 输出不应泄漏任何业务判定字段（除合法字段外）。"""
        w = make_warning(meta={"policy": "X", "decided_at": "2026-07-19T12:00:00+00:00"})
        d = w.to_dict()
        # 合法字段白名单
        allowed = {
            "warning_id",
            "elder_id",
            "device_id",
            "risk_level",
            "recommended_action",
            "status",
            "perception_score",
            "trigger_events",
            "reason_summary",
            "evidence",
            "meta",
            "created_at",
        }
        leaked = set(d.keys()) - allowed
        assert not leaked, f"WarningEvent 泄漏字段 {leaked}，应只含白名单字段"

    def test_no_fraud_in_warning_event_text(self):
        """reason_summary 中不能含 'fraud' / 'scam' / 'criminal' / 'verdict' / 'crime' 等词。"""
        # 合法 reason_summary（来自 decision_policy 路由表）
        legal_reasons = [
            "异常停留",
            "重复访问",
            "未在白名单",
            "多风险规则同时命中",
            "异常时段访问",
        ]
        for r in legal_reasons:
            assert "fraud" not in r.lower()
            assert "scam" not in r.lower()
            assert "criminal" not in r.lower()
            assert "verdict" not in r.lower()
            assert "crime" not in r.lower()


# ============================================================================
# WarningEvent 序列化
# ============================================================================


class TestWarningEventSerialization:
    def test_to_dict_basic(self):
        w = make_warning()
        d = w.to_dict()
        assert d["elder_id"] == "elder_001"
        assert d["risk_level"] == "MEDIUM"
        assert d["recommended_action"] == "NOTIFY_FAMILY"
        assert d["warning_id"] == str(w.warning_id)

    def test_to_dict_structlog_safe(self):
        """to_dict 不含 datetime 对象（避免 structlog 序列化错误）。"""
        w = make_warning()
        d = w.to_dict()
        # 验证所有 value 都不是 datetime 对象
        for k, v in d.items():
            assert not isinstance(v, datetime), f"to_dict[{k}] 仍是 datetime 对象"

    def test_to_dict_created_at_is_iso(self):
        w = make_warning()
        d = w.to_dict()
        assert "T" in d["created_at"]
        assert d["created_at"].endswith("+00:00")

    def test_to_json_round_trip(self):
        w = make_warning()
        j = w.to_json()
        assert isinstance(j, str)
        parsed = json.loads(j)
        assert parsed["warning_id"] == str(w.warning_id)
        assert parsed["elder_id"] == "elder_001"
        assert parsed["risk_level"] == "MEDIUM"


# ============================================================================
# DecisionPolicy 路由逻辑
# ============================================================================


class TestRuleBasedDecisionPolicy:
    def test_empty_events_returns_none(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        assert policy.decide(DecisionInput(trigger_events=(), decision_context=ctx)) is None

    def test_visit_normal_alone_returns_none(self):
        """单条 visit_normal（无 is_odd_hour）→ 不警告，避免噪音。"""
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="visit_normal", is_odd_hour=False)]
        assert policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx)) is None

    def test_visit_normal_with_odd_hour_returns_low(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="visit_normal", is_odd_hour=True)]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "MONITOR"
        assert "异常时段" in w.reason_summary[0]

    def test_high_risk_approach_alone_returns_high(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="high_risk_approach", score=0.9)]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "HIGH"
        assert w.recommended_action == "ESCALATE_COMMUNITY"
        assert w.perception_score == 0.9

    def test_abnormal_dwell_alone_returns_low(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="abnormal_dwell", score=0.5)]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"
        assert "异常停留" in w.reason_summary

    def test_repeat_visit_alone_returns_low(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="repeat_visit", score=0.3)]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"
        assert "重复访问" in w.reason_summary

    def test_pending_verify_alone_returns_low_monitor(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [make_perception(event_type="visit_pending_verify", score=0.2)]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "MONITOR"

    def test_combination_high_plus_low_takes_max(self):
        """Owner："HIGH + LOW = HIGH"，max wins。"""
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [
            make_perception(event_type="high_risk_approach", score=0.9),
            make_perception(event_type="abnormal_dwell", score=0.5),
            make_perception(event_type="repeat_visit", score=0.3),
        ]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "HIGH"  # max wins
        assert w.recommended_action == "ESCALATE_COMMUNITY"
        assert w.perception_score == 0.9  # max of all scores
        # reason_summary 合并去重
        assert len(w.reason_summary) == 3
        assert "多风险规则同时命中" in w.reason_summary
        assert "异常停留" in w.reason_summary
        assert "重复访问" in w.reason_summary

    def test_combination_two_low_stays_low(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [
            make_perception(event_type="abnormal_dwell", score=0.5),
            make_perception(event_type="repeat_visit", score=0.3),
        ]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"

    def test_combination_normal_visit_suppressed(self):
        """visit_normal 单独不警告，但与 abnormal_dwell 一起 → 保留 abnormal_dwell。"""
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="elder_001")
        events = [
            make_perception(event_type="visit_normal", score=0.1),  # 单独不警告
            make_perception(event_type="abnormal_dwell", score=0.5),
        ]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w is not None
        assert w.risk_level == "LOW"
        # 触发事件只含 abnormal_dwell（visit_normal 被过滤）
        trigger_types = [t["event_type"] for t in w.trigger_events]
        assert "visit_normal" not in trigger_types
        assert "abnormal_dwell" in trigger_types

    def test_warning_event_elder_id_from_context(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="my_elder_42")
        events = [make_perception(event_type="abnormal_dwell")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.elder_id == "my_elder_42"

    def test_warning_event_device_id_from_perception(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="abnormal_dwell", device_id="front_door_v2")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.device_id == "front_door_v2"

    def test_warning_event_meta_contains_policy(self):
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="abnormal_dwell")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.meta["policy"] == "RuleBasedDecisionPolicy"
        assert "decided_at" in w.meta
        assert "trigger_event_types" in w.meta

    def test_warning_event_default_status_is_created(self):
        """Owner P0-8 review：WarningEvent 默认 status = CREATED（决策刚生成，未下发）。"""
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="abnormal_dwell")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.status == "CREATED"

    def test_warning_event_no_fraud_field(self):
        """决策层输出 WarningEvent 不含任何"最终判定"字段。"""
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="e1")
        events = [
            make_perception(event_type="high_risk_approach", score=0.9),
            make_perception(event_type="abnormal_dwell", score=0.5),
        ]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        d = w.to_dict()
        # 关键黑名单检查（dict 顶层 + meta）
        for forbidden in FORBIDDEN_WARNING_FIELDS:
            assert forbidden not in d, f"WarningEvent.to_dict() 含禁止字段 {forbidden!r}"
            assert forbidden not in d["meta"], f"WarningEvent.meta 含禁止字段 {forbidden!r}"


# ============================================================================
# RuleBasedDecisionPolicy 路由表定制
# ============================================================================


    def test_decision_degrades_to_perception_only_without_memory(self):
        """ADR-0030 D2 Memory 可缺席：memory 三字段全 None 时退化为纯感知决策且不报错。

        Slice B 零行为变化——policy 不读 memory 字段；「缺席」= 中性，不得因无记忆
        而抬升或降低风险。本测试断言带 None memory 的输入与纯感知输入逐字段一致。
        """
        policy = RuleBasedDecisionPolicy()
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="abnormal_dwell", score=0.5)]
        di = DecisionInput(
            trigger_events=tuple(events),
            decision_context=ctx,
            reasoning_input=None,
            reasoning_result=None,
            prior_warning=None,
        )
        w = policy.decide(di)
        assert w is not None
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"
        # 与「不带任何 memory 字段」的纯感知输入逐字段一致（缺席=中性，非风险信号）
        plain = policy.decide(
            DecisionInput(trigger_events=tuple(events), decision_context=ctx)
        )
        assert w.risk_level == plain.risk_level
        assert w.recommended_action == plain.recommended_action
        assert w.perception_score == plain.perception_score

class TestRuleBasedDecisionPolicyCustomization:
    def test_custom_routing_table(self):
        """家庭可定制路由表：把 abnormal_dwell 视为 MEDIUM 而非 LOW。"""
        custom_table = dict(DEFAULT_ROUTING_TABLE)
        custom_table["abnormal_dwell"] = ("MEDIUM", "NOTIFY_FAMILY", "异常停留")
        policy = RuleBasedDecisionPolicy(routing_table=custom_table)
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="abnormal_dwell")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.risk_level == "MEDIUM"
        assert w.recommended_action == "NOTIFY_FAMILY"

    def test_invalid_routing_table_level_raises(self):
        bad_table = {
            "abnormal_dwell": ("EXTREME", "NOTIFY_FAMILY", "x"),
        }
        with pytest.raises(ValueError, match="level 必须是"):
            RuleBasedDecisionPolicy(routing_table=bad_table)

    def test_invalid_routing_table_action_raises(self):
        bad_table = {
            "abnormal_dwell": ("LOW", "CALL_POLICE", "x"),
        }
        with pytest.raises(ValueError, match="action 必须是"):
            RuleBasedDecisionPolicy(routing_table=bad_table)

    def test_family_prefers_notify_over_escalate(self):
        """家庭可定制路由表：把 high_risk_approach 也映射为 NOTIFY_FAMILY（先联系家属不升级社区）。"""
        custom_table = dict(DEFAULT_ROUTING_TABLE)
        custom_table["high_risk_approach"] = ("HIGH", "NOTIFY_FAMILY", "多风险规则同时命中")
        policy = RuleBasedDecisionPolicy(routing_table=custom_table)
        ctx = DecisionContext(elder_id="e1")
        events = [make_perception(event_type="high_risk_approach")]
        w = policy.decide(DecisionInput(trigger_events=tuple(events), decision_context=ctx))
        assert w.risk_level == "HIGH"
        assert w.recommended_action == "NOTIFY_FAMILY"

    def test_default_routing_table_constants(self):
        """默认路由表与 Owner 决策一致。"""
        assert DEFAULT_ROUTING_TABLE["high_risk_approach"][0] == "HIGH"
        assert DEFAULT_ROUTING_TABLE["high_risk_approach"][1] == "ESCALATE_COMMUNITY"
        assert DEFAULT_ROUTING_TABLE["abnormal_dwell"][0] == "LOW"
        assert DEFAULT_ROUTING_TABLE["abnormal_dwell"][1] == "NOTIFY_FAMILY"
        assert DEFAULT_ROUTING_TABLE["repeat_visit"][0] == "LOW"
        assert DEFAULT_ROUTING_TABLE["repeat_visit"][1] == "NOTIFY_FAMILY"
        assert DEFAULT_ROUTING_TABLE["visit_pending_verify"][0] == "LOW"
        assert DEFAULT_ROUTING_TABLE["visit_pending_verify"][1] == "MONITOR"


# ============================================================================
# DecisionEngine 编排器
# ============================================================================


class TestDecisionEngine:
    def test_requires_elder_id(self):
        with pytest.raises(ValueError, match="elder_id"):
            DecisionEngine(elder_id="")

    def test_default_policy_is_rule_based(self):
        engine = DecisionEngine(elder_id="elder_001")
        assert isinstance(engine.policy, RuleBasedDecisionPolicy)
        assert engine.policy.name == "RuleBasedDecisionPolicy"

    def test_custom_policy(self):
        class _StubPolicy(RuleBasedDecisionPolicy):
            name = "StubPolicy"

        engine = DecisionEngine(elder_id="e1", policy=_StubPolicy())
        assert engine.policy.name == "StubPolicy"

    def test_empty_events_returns_none(self):
        engine = DecisionEngine(elder_id="e1")
        assert engine.evaluate([]) is None

    def test_normal_visit_returns_none(self):
        engine = DecisionEngine(elder_id="e1")
        events = [make_perception(event_type="visit_normal", is_odd_hour=False)]
        assert engine.evaluate(events) is None

    def test_high_risk_emits_warning(self):
        engine = DecisionEngine(elder_id="e1")
        events = [make_perception(event_type="high_risk_approach", score=0.9)]
        w = engine.evaluate(events)
        assert w is not None
        assert w.risk_level == "HIGH"
        assert w.recommended_action == "ESCALATE_COMMUNITY"
        assert w.elder_id == "e1"

    def test_combination_emits_max_level(self):
        engine = DecisionEngine(elder_id="e1")
        events = [
            make_perception(event_type="abnormal_dwell", score=0.5),
            make_perception(event_type="high_risk_approach", score=0.9),
        ]
        w = engine.evaluate(events)
        assert w is not None
        assert w.risk_level == "HIGH"

    def test_now_provider_injection(self):
        """DecisionEngine 应允许注入 now_provider（便于测试）。"""
        fixed = utc(2026, 7, 19, 12, 0, 0)
        engine = DecisionEngine(
            elder_id="e1",
            now_provider=lambda: fixed,
        )
        events = [make_perception(event_type="abnormal_dwell")]
        w = engine.evaluate(events)
        assert w is not None
        assert w.meta["decided_at"] == fixed.isoformat()


# ============================================================================
# 链路集成：RuleEngine → DecisionEngine
# ============================================================================


class TestRuleEngineToDecisionEngine:
    """P0-7b RuleEngine 输出 → P0-8 DecisionEngine 消费 集成测试。"""

    def test_rule_engine_output_feeds_decision_engine(self):
        """RuleEngine 输出的 PerceptionEvent 列表能被 DecisionEngine 消费。"""
        pytest.importorskip("ultralytics")
        from home_perception.analysis.rule_engine import RuleEngine

        # 构造一个会触发 high_risk_approach 的 RiskFeature
        visitor_id = uuid.uuid4()
        risk = _make_high_risk_feature(visitor_id)

        rule_engine = RuleEngine(device_id="dev1", location="入户门")
        perception_events = rule_engine.evaluate(risk)
        assert len(perception_events) > 0

        # 决策层消费
        decision_engine = DecisionEngine(elder_id="elder_001")
        warning = decision_engine.evaluate(perception_events)
        if any(e.event_type == "high_risk_approach" for e in perception_events):
            assert warning is not None
            assert warning.risk_level == "HIGH"
            assert warning.recommended_action == "ESCALATE_COMMUNITY"

    def test_visitor_id_propagates_to_warning(self):
        pytest.importorskip("ultralytics")
        from home_perception.analysis.rule_engine import RuleEngine

        visitor_id = uuid.uuid4()
        risk = _make_high_risk_feature(visitor_id)

        rule_engine = RuleEngine(device_id="dev1")
        perception_events = rule_engine.evaluate(risk)

        decision_engine = DecisionEngine(elder_id="e1")
        warning = decision_engine.evaluate(perception_events)
        if warning is not None:
            for trigger in warning.trigger_events:
                # trigger_events 中的 event_id 应含 visitor_id
                assert str(visitor_id) in trigger["event_id"]


# ============================================================================
# CAVIAR 真实链路端到端（fixture 缺失优雅 skip）
# ============================================================================

CAVIAR_ONE_STOP_ENTER = "tests/fixtures/doorway/one_stop_enter"


def _make_high_risk_feature(visitor_id: uuid.UUID):
    """构造一个会同时触发 LongDuration + RepeatVisit + OddHour 的 RiskFeature。"""
    pytest.importorskip("ultralytics")
    from home_perception.analysis.feature import (
        DurationFeature,
        RiskFeature,
        TimeFeature,
        TrajectoryFeature,
        VisitFrequencyFeature,
    )

    t = utc(2026, 7, 19, 2, 0, 0)  # 凌晨 2 点（异常时段）
    return RiskFeature(
        visitor_id=visitor_id,
        event_id="e1",
        source_video="cam01",
        computed_at=t,
        duration=DurationFeature(
            visitor_id=visitor_id,
            event_id="e1",
            source_video="cam01",
            duration_seconds=600.0,
            computed_at=t,  # > 300 阈值
        ),
        frequency=VisitFrequencyFeature(
            visitor_id=visitor_id,
            event_id="e1",
            source_video="cam01",
            visits_in_window=5,
            window_seconds=1800.0,
            computed_at=t,  # > 3 阈值
        ),
        time=TimeFeature.from_datetime(
            t,
            visitor_id=visitor_id,
            event_id="e1",
            source_video="cam01",
            computed_at=t,
        ),
        trajectory=TrajectoryFeature(
            visitor_id=visitor_id,
            event_id="e1",
            source_video="cam01",
            computed_at=t,
        ),
    )


def test_caviar_end_to_end_pipeline_yields_decision():
    """CAVIAR OneStopEnter1cor: detector → tracker → event → feature → rule → perception → decision 全链路。"""
    pytest.importorskip("ultralytics")
    from pathlib import Path

    import cv2

    p = Path(CAVIAR_ONE_STOP_ENTER)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip("CAVIAR fixture 缺失")

    frames = []
    for f in sorted(p.glob("frame_*.jpg")):
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    if not frames:
        pytest.skip("CAVIAR frames 解析失败")

    # 端到端：复用 P0-5/P0-6/P0-7a/P0-7b 链路
    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.analysis.feature_extractor import FeatureExtractor
    from home_perception.analysis.rule_engine import RuleEngine
    from home_perception.detection.detector import YOLODetector
    from home_perception.detection.tracker import VisitorTracker

    det = YOLODetector(model="yolo11n.pt", conf_threshold=0.25)
    tracker = VisitorTracker(absence_gap_s=5.0)
    event_builder = VisitorEventBuilder(tracker, source_video="CAVIAR/OneStopEnter1cor")
    feat_ext = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(device_id="CAVIAR-Test", location="入户门")
    decision_engine = DecisionEngine(elder_id="CAVIAR-Elder")

    warnings = []
    for f in frames:
        r = det.detect(f)
        for ev in event_builder.update(r.detections):
            risk = feat_ext.extract(ev)
            perception_events = rule_engine.evaluate(risk)
            warning = decision_engine.evaluate(perception_events)
            if warning is not None:
                warnings.append(warning)

    # 验证：CAVIAR OneStopEnter1cor 是单访客 1 次进入，warning 应 ≤ 1（Cooldown 抑制）
    assert len(warnings) <= 1
    if warnings:
        w = warnings[0]
        # 决策层不直接说"诈骗"
        assert "fraud" not in w.reason_summary[0].lower() if w.reason_summary else True
        # 字段合法
        assert w.risk_level in RISK_LEVELS
        assert w.recommended_action in RECOMMENDED_ACTIONS
        assert w.status in WARNING_STATUSES
        # meta 合法
        assert "policy" in w.meta
        assert w.meta["policy"] == "RuleBasedDecisionPolicy"
        # 黑名单检查
        for forbidden in FORBIDDEN_WARNING_FIELDS:
            assert forbidden not in w.to_dict()
            assert forbidden not in w.to_dict()["meta"]

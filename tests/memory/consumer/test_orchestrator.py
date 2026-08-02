"""C-4 RuleBasedMemoryConsumer 单测（DESIGN §4.2 / ADR-0025 C1–C5）。

验证：
- 单向管道：Retrieval → Aggregation → ContextBuilder，严格一次、严格有序，三组件互不调用；
- C1 不决策：产物字段白名单不含 score / decision / warning；
- C4 冲突**只标记不解决**：risk_escalation / behavior_shift 的正反例（含"持平不标记"
  这一可变异验证的负例）；冷启动无历史时不产伪冲突；
- C2 只读：召回记录与 store 在 consume 前后逐字段不变；
- C3 确定性：同输入两次逐字段一致，且与 Retrieval 返回顺序无关（穷举全排列）；
- evidence / previous_actions 汇总与去重；
- 异常分层：子层已分类异常原样上抛，未分类异常转译为 ConsumerError。

构造 ``EpisodicRecord`` 用**固定 source_event_ids**（回放铁律：event_id 默认随机
UUID4，测试须显式传固定值）。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.memory.consumer.aggregation import RuleBasedAggregation
from home_perception.memory.consumer.context import RuleBasedContextBuilder
from home_perception.memory.consumer.contracts import CurrentEvent, ReasoningInput
from home_perception.memory.consumer.exceptions import (
    AggregationError,
    ConsumerError,
    ContextBuildError,
    RetrievalError,
)
from home_perception.memory.consumer.interfaces import Aggregation, ContextBuilder, Retrieval
from home_perception.memory.consumer.orchestrator import RuleBasedMemoryConsumer
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval
from home_perception.memory.records import ActionSummary, EpisodicRecord, EvidenceRef
from home_perception.memory.store import InMemoryStore

VISITOR = "visitor-c4"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _make_record(
    rid: str,
    enter: datetime,
    *,
    vid: str = VISITOR,
    risk_level: str | None = None,
    reasons: list[str] | None = None,
    actions: list[ActionSummary] | None = None,
    evidence: list[EvidenceRef] | None = None,
) -> EpisodicRecord:
    leave = enter + timedelta(minutes=5)
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=vid,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=[f"ev-{rid}"],
        summary=f"visit {rid}",
        model_version="ep-builder-v1",
        risk_level=risk_level,
        reason_summary=reasons or [],
        actions=actions or [],
        evidence_refs=evidence or [],
    )


def _make_event(
    *,
    vid: str = VISITOR,
    risk_level: str | None = None,
    markers: tuple[str, ...] = (),
    occurred_at: datetime | None = None,
) -> CurrentEvent:
    return CurrentEvent(
        event_id=f"cur-{vid}",
        event_type="visitor_event",
        visitor_instance_id=vid,
        occurred_at=occurred_at or _utc(2026, 7, 20, 21, 0, 0),
        risk_level=risk_level,
        markers=markers,
    )


def _default_consumer(records: list[EpisodicRecord]) -> RuleBasedMemoryConsumer:
    """默认三组件组合 + 已注入历史的真实 InMemoryStore（尽量少 mock）。"""
    store = InMemoryStore()
    for r in records:
        store.upsert_episodic(r)
    return RuleBasedMemoryConsumer(
        RuleBasedRetrieval(store),
        RuleBasedAggregation(),
        RuleBasedContextBuilder(),
    )


# 历史基线：当前事件设在 2026-07-20 21:00，落在 30d 召回窗内
_H1 = _utc(2026, 7, 18, 10, 0, 0)
_H2 = _utc(2026, 7, 19, 10, 0, 0)


# ============================================================================
# 1. 单向管道与产物形态
# ============================================================================


class _SpyRetrieval(Retrieval):
    def __init__(self, records: list[EpisodicRecord], trace: list[str]):
        self._records = records
        self._trace = trace

    def retrieve(self, current_event, device_id=None):
        self._trace.append("retrieve")
        return list(self._records)


class _SpyAggregation(Aggregation):
    def __init__(self, trace: list[str]):
        self._trace = trace
        self.seen: list[EpisodicRecord] | None = None

    def aggregate(self, records):
        self._trace.append("aggregate")
        self.seen = records
        return (None, None)


class _SpyContextBuilder(ContextBuilder):
    def __init__(self, trace: list[str]):
        self._trace = trace
        self.kwargs: dict = {}

    def build(
        self,
        current_event,
        records,
        profile,
        pattern,
        evidence_refs,
        previous_actions,
        conflicts,
    ):
        self._trace.append("build")
        self.kwargs = {
            "records": records,
            "profile": profile,
            "pattern": pattern,
            "evidence_refs": evidence_refs,
            "previous_actions": previous_actions,
            "conflicts": conflicts,
        }
        return ReasoningInput(
            current_event=current_event,
            historical_context=tuple(records),
            visitor_profile=profile,
            risk_pattern=pattern,
            evidence_refs=evidence_refs,
            previous_actions=previous_actions,
            conflicts=conflicts,
        )


class TestPipelineShape:
    def test_consume_returns_reasoning_input_with_history(self):
        """happy path：产出 ReasoningInput，historical_context 含召回记录。"""
        consumer = _default_consumer([_make_record("ep-1", _H1), _make_record("ep-2", _H2)])
        out = consumer.consume(_make_event())
        assert isinstance(out, ReasoningInput)
        assert [ep.record_id for ep in out.historical_context] == ["ep-1", "ep-2"]

    def test_strict_one_way_order_each_called_once(self):
        """严格单向：retrieve → aggregate → build，各恰好一次（组件互不调用）。"""
        trace: list[str] = []
        agg = _SpyAggregation(trace)
        builder = _SpyContextBuilder(trace)
        records = [_make_record("ep-1", _H1)]
        consumer = RuleBasedMemoryConsumer(_SpyRetrieval(records, trace), agg, builder)
        consumer.consume(_make_event())
        assert trace == ["retrieve", "aggregate", "build"]
        # Aggregation 收到的正是 Retrieval 的产物；Builder 收到的正是同一批记录
        assert [ep.record_id for ep in agg.seen] == ["ep-1"]
        assert [ep.record_id for ep in builder.kwargs["records"]] == ["ep-1"]

    def test_empty_history_still_produces_input(self):
        """无历史：仍产出 ReasoningInput（profile/pattern 为 None，非报错）。"""
        out = _default_consumer([]).consume(_make_event())
        assert out.historical_context == ()
        assert out.visitor_profile is None
        assert out.risk_pattern is None


# ============================================================================
# 2. C1 不决策
# ============================================================================


class TestC1NoDecision:
    def test_no_score_or_decision_fields(self):
        """产物字段白名单：不含任何 score / decision / warning 字段。"""
        out = _default_consumer([_make_record("ep-1", _H1)]).consume(_make_event(risk_level="HIGH"))
        names = set(out.to_dict().keys())
        assert names == {
            "current_event",
            "historical_context",
            "visitor_profile",
            "risk_pattern",
            "evidence_refs",
            "previous_actions",
            "conflicts",
        }
        forbidden = {"risk_score", "score", "decision", "warning", "recommended_action"}
        assert not (names & forbidden)

    def test_current_risk_level_is_passed_through_not_computed(self):
        """current_event.risk_level 原样透传（是输入事实，不是 Consumer 算出来的）。"""
        event = _make_event(risk_level="MEDIUM")
        out = _default_consumer([_make_record("ep-1", _H1, risk_level="HIGH")]).consume(event)
        assert out.current_event.risk_level == "MEDIUM"


# ============================================================================
# 3. C4 冲突透明（只标记不解决）
# ============================================================================


def _conflict_types(out: ReasoningInput) -> list[str]:
    return [c.type for c in out.conflicts]


class TestC4Conflicts:
    def test_risk_escalation_marked_when_strictly_higher(self):
        """历史最高 LOW、当前 HIGH → 标记 risk_escalation，并保留新旧双方。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, risk_level="LOW")])
        out = consumer.consume(_make_event(risk_level="HIGH"))
        flags = [c for c in out.conflicts if c.type == "risk_escalation"]
        assert len(flags) == 1
        assert flags[0].historical == "LOW"
        assert flags[0].current == "HIGH"

    def test_no_escalation_when_level_equal(self):
        """持平不标记（若把 `<=` 误写成 `<`，本例会多出一条冲突 → 变异可检出）。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, risk_level="MEDIUM")])
        out = consumer.consume(_make_event(risk_level="MEDIUM"))
        assert "risk_escalation" not in _conflict_types(out)

    def test_no_escalation_when_level_lower(self):
        """回落不标记：C4 关注"历史解释不了当前"，等级下降不构成需裁决的冲突。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, risk_level="HIGH")])
        out = consumer.consume(_make_event(risk_level="LOW"))
        assert "risk_escalation" not in _conflict_types(out)

    def test_escalation_from_no_risk_history_uses_none_placeholder(self):
        """历史全无风险等级 → historical 用显式 "none" 占位（ConflictFlag 四字段非空）。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, risk_level=None)])
        out = consumer.consume(_make_event(risk_level="MEDIUM"))
        flags = [c for c in out.conflicts if c.type == "risk_escalation"]
        assert len(flags) == 1
        assert flags[0].historical == "none"

    def test_behavior_shift_marked_for_unseen_marker(self):
        """当前出现历史未见的行为标记 → 逐个标记 behavior_shift。"""
        consumer = _default_consumer(
            [_make_record("ep-1", _H1, reasons=["behavior:daytime_visit"])]
        )
        out = consumer.consume(_make_event(markers=("night", "observe_camera")))
        shifts = [c for c in out.conflicts if c.type == "behavior_shift"]
        assert [c.current for c in shifts] == ["night", "observe_camera"]  # 名称升序，C3

    def test_no_behavior_shift_for_known_marker(self):
        """标记在历史中出现过 → 不标记（口径一致性的关键负例）。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, reasons=["behavior:night"])])
        out = consumer.consume(_make_event(markers=("night",)))
        assert "behavior_shift" not in _conflict_types(out)

    def test_empty_marker_ignored(self):
        """空标记不产生冲突（与 RuleBasedAggregation 同口径）。"""
        consumer = _default_consumer([_make_record("ep-1", _H1, reasons=["behavior:"])])
        out = consumer.consume(_make_event(markers=("",)))
        assert out.conflicts == ()

    def test_cold_start_produces_no_conflicts(self):
        """无历史 = 无冲突：首次来访不得被标记为升级/突变（伪冲突防线）。"""
        out = _default_consumer([]).consume(_make_event(risk_level="HIGH", markers=("night",)))
        assert out.conflicts == ()

    def test_conflicts_are_marked_not_resolved(self):
        """冲突同时保留 historical 与 current 两侧，不做取舍（ADR-0025 §3.6）。"""
        consumer = _default_consumer(
            [_make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"])]
        )
        out = consumer.consume(_make_event(risk_level="HIGH", markers=("night",)))
        assert _conflict_types(out) == ["risk_escalation", "behavior_shift"]
        for flag in out.conflicts:
            assert flag.historical and flag.current
            assert flag.historical != flag.current


# ============================================================================
# 4. C2 只读
# ============================================================================


class TestC2ReadOnly:
    def test_inputs_and_store_unmodified(self):
        """consume 前后：store 内记录与当前事件逐字段不变，且未新增记录。"""
        store = InMemoryStore()
        record = _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"])
        store.upsert_episodic(record)
        before = record.to_dict()
        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        event = _make_event(risk_level="HIGH", markers=("night",))
        event_before = event.to_dict()

        consumer.consume(event)

        assert record.to_dict() == before
        assert event.to_dict() == event_before
        assert len(store.get_episodic_by_visitor(VISITOR)) == 1

    def test_no_cross_request_state(self):
        """无跨请求状态：先消费一次高风险事件，不影响后续低风险事件的产物。

        对照组是**同一 store 上的全新编排器**（复用同一批 record 实例，避免
        ``created_at`` 自动时间戳造成的伪差异）。若编排器缓存了上次的 conflicts /
        records，两者会不等。
        """
        store = InMemoryStore()
        store.upsert_episodic(_make_record("ep-1", _H1, risk_level="LOW"))

        def _new() -> RuleBasedMemoryConsumer:
            return RuleBasedMemoryConsumer(
                RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
            )

        stateful = _new()
        stateful.consume(_make_event(risk_level="HIGH", markers=("night",)))
        second = stateful.consume(_make_event(risk_level="LOW"))
        fresh = _new().consume(_make_event(risk_level="LOW"))
        assert second.to_dict() == fresh.to_dict()
        assert second.conflicts == ()  # 上一次的 risk_escalation / behavior_shift 未残留


# ============================================================================
# 5. C3 确定性
# ============================================================================


class TestC3Determinism:
    def test_same_input_twice_identical(self):
        consumer = _default_consumer(
            [
                _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"]),
                _make_record("ep-2", _H2, risk_level="MEDIUM", reasons=["behavior:loiter"]),
            ]
        )
        event = _make_event(risk_level="HIGH", markers=("night", "observe_camera"))
        assert consumer.consume(event).to_dict() == consumer.consume(event).to_dict()

    def test_output_independent_of_retrieval_order(self):
        """穷举 Retrieval 返回顺序的全排列，产物逐字段一致（顺序无关铁律）。"""
        records = [
            _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"]),
            _make_record("ep-2", _H2, risk_level="MEDIUM", reasons=["behavior:loiter"]),
            _make_record("ep-3", _H2, risk_level=None, reasons=["behavior:loiter"]),
        ]
        event = _make_event(risk_level="HIGH", markers=("night",))
        outputs = []
        for perm in itertools.permutations(records):
            trace: list[str] = []
            consumer = RuleBasedMemoryConsumer(
                _SpyRetrieval(list(perm), trace),
                RuleBasedAggregation(),
                RuleBasedContextBuilder(),
            )
            outputs.append(consumer.consume(event).to_dict())
        assert all(o == outputs[0] for o in outputs)

    def test_marker_order_in_event_does_not_change_conflicts(self):
        """current_event.markers 输入顺序不影响冲突产出顺序。"""
        consumer = _default_consumer([_make_record("ep-1", _H1)])
        a = consumer.consume(_make_event(markers=("night", "observe_camera")))
        b = consumer.consume(_make_event(markers=("observe_camera", "night")))
        assert [c.to_dict() for c in a.conflicts] == [c.to_dict() for c in b.conflicts]


# ============================================================================
# 6. 证据 / 既往动作汇总
# ============================================================================


def _evidence(eid: str) -> EvidenceRef:
    return EvidenceRef(evidence_id=eid, modality="vision", captured_at=_H1, uri=f"file://{eid}")


def _action(cid: str, ctype: str = "SEND_FAMILY_MESSAGE") -> ActionSummary:
    return ActionSummary(command_type=ctype, command_id=cid, status="SUCCESS")


class TestEvidenceAndActions:
    def test_actions_collected_and_deduped_by_command_id(self):
        consumer = _default_consumer(
            [
                _make_record("ep-1", _H1, actions=[_action("cmd-1"), _action("cmd-2")]),
                _make_record("ep-2", _H2, actions=[_action("cmd-2"), _action("cmd-3")]),
            ]
        )
        out = consumer.consume(_make_event())
        assert [a.command_id for a in out.previous_actions] == ["cmd-1", "cmd-2", "cmd-3"]

    def test_action_projection_keeps_all_four_fields(self):
        summary = ActionSummary(
            command_type="CREATE_COMMUNITY_TASK",
            command_id="cmd-9",
            status="FAILED",
            error="timeout",
        )
        out = _default_consumer([_make_record("ep-1", _H1, actions=[summary])]).consume(
            _make_event()
        )
        assert out.previous_actions[0].to_dict() == {
            "command_type": "CREATE_COMMUNITY_TASK",
            "command_id": "cmd-9",
            "status": "FAILED",
            "error": "timeout",
        }

    def test_evidence_collected_and_deduped(self):
        consumer = _default_consumer(
            [
                _make_record("ep-1", _H1, evidence=[_evidence("ev-a")]),
                _make_record("ep-2", _H2, evidence=[_evidence("ev-a"), _evidence("ev-b")]),
            ]
        )
        out = consumer.consume(_make_event())
        assert [e.evidence_id for e in out.evidence_refs] == ["ev-a", "ev-b"]

    def test_evidence_empty_for_v1_records(self):
        """v1 Episode Builder 不填证据 → evidence_refs 为空（现状事实断言）。"""
        out = _default_consumer([_make_record("ep-1", _H1)]).consume(_make_event())
        assert out.evidence_refs == ()


# ============================================================================
# 7. 异常分层
# ============================================================================


class _BoomRetrieval(Retrieval):
    def __init__(self, exc: Exception):
        self._exc = exc

    def retrieve(self, current_event, device_id=None):
        raise self._exc


class TestErrorLayering:
    @pytest.mark.parametrize(
        "exc",
        [RetrievalError("boom"), AggregationError("boom"), ContextBuildError("boom")],
    )
    def test_classified_errors_propagate_unwrapped(self, exc):
        """子层已分类异常原样上抛，保留失败阶段信息（供 hook 日志区分）。"""
        consumer = RuleBasedMemoryConsumer(
            _BoomRetrieval(exc), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        with pytest.raises(type(exc)):
            consumer.consume(_make_event())

    def test_unclassified_error_wrapped_as_consumer_error(self):
        """未分类异常统一转译为 ConsumerError，绝不向上抛裸异常。"""
        consumer = RuleBasedMemoryConsumer(
            _BoomRetrieval(RuntimeError("driver down")),
            RuleBasedAggregation(),
            RuleBasedContextBuilder(),
        )
        with pytest.raises(ConsumerError) as ei:
            consumer.consume(_make_event())
        assert isinstance(ei.value.__cause__, RuntimeError)

    def test_none_event_raises_consumer_error(self):
        with pytest.raises(ConsumerError):
            _default_consumer([]).consume(None)

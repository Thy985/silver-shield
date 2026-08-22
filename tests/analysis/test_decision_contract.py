"""`DecisionInput` 契约测试（ADR-0030 · Slice A）。

覆盖 ADR-0030 验收清单第 2/3 条中属于 Slice A（零行为变化）的部分：

- **C1**：`DecisionInput` 不含任何决策语义字段（决策语义是 `WarningEvent` 的输出属性）；
- **C2**：frozen + tuple 容器；
- **C3**：`from_dict` ↔ `to_dict` 往返稳定 + `trigger_events` 规范化排序；
- **C7**：一级聚合字段白名单（防 God Object 横向膨胀）；
- **D2 Memory 可缺席原则**：`reasoning_input` / `reasoning_result` 为 `None` 时仍可合法构造。

本文件**不**测试 `DecisionPolicy` 行为——Slice A 不改变任何决策行为。
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from home_perception.analysis import decision_contract as dc
from home_perception.analysis.decision_contract import (
    DECISION_INPUT_FIELD_WHITELIST,
    DECISION_INPUT_FORBIDDEN_FIELDS,
    DecisionInput,
)
from home_perception.analysis.decision_policy import DecisionContext, RuleBasedDecisionPolicy
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.memory.consumer.contracts import (
    CurrentEvent,
    ReasoningInput,
    ReasoningResult,
)

# ============================================================================
# Fixtures / helpers
# ============================================================================

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)


def make_perception(
    event_type: str = "abnormal_dwell",
    score: float = 0.5,
    device_id: str = "home_entry_01",
    timestamp: float = 1000.0,
) -> PerceptionEvent:
    return PerceptionEvent(
        device_id=device_id,
        event_type=event_type,
        score=score,
        visitor_id=uuid.uuid4(),
        source_video="cam01",
        timestamp=timestamp,
        meta={"rule": f"TestRule_{event_type}"},
        created_at=NOW,
    )


def make_ctx(elder_id: str = "elder_001") -> DecisionContext:
    return DecisionContext(elder_id=elder_id, now=NOW, extra={"tenant": "demo"})


def make_reasoning_input() -> ReasoningInput:
    return ReasoningInput(
        current_event=CurrentEvent(
            event_id="evt-1",
            event_type="visitor_event",
            visitor_instance_id="vi-1",
            occurred_at=NOW,
            risk_level="LOW",
            markers=("night",),
        ),
        historical_context=(),
        visitor_profile=None,
        risk_pattern=None,
    )


def make_reasoning_result() -> ReasoningResult:
    return ReasoningResult(
        findings=("历史无异常",),
        explanation="该访客过去一月均为日间到访。",
        suggested_action_hint="MONITOR",
    )


def make_prior_warning() -> WarningEvent:
    return WarningEvent(
        elder_id="elder_001",
        device_id="home_entry_01",
        risk_level="LOW",
        recommended_action="MONITOR",
        trigger_events=[
            {
                "event_id": "prev:abnormal_dwell",
                "event_type": "abnormal_dwell",
                "score": 0.5,
                "timestamp": 900.0,
            }
        ],
        reason_summary=["异常停留"],
        perception_score=0.5,
        meta={"policy": "RuleBasedDecisionPolicy"},
        created_at=NOW,
    )


def make_full_input() -> DecisionInput:
    return DecisionInput(
        trigger_events=(make_perception(),),
        decision_context=make_ctx(),
        reasoning_input=make_reasoning_input(),
        reasoning_result=make_reasoning_result(),
        prior_warning=make_prior_warning(),
    )


# ============================================================================
# C1 —— 无决策语义字段（ADR-0010 单一决策中心）
# ============================================================================


class TestC1NoDecisionFields:
    def test_decision_input_has_no_decision_fields(self):
        """C1：决策语义字段是 WarningEvent 的**输出**属性，不得内嵌进决策**输入**。"""
        names = {f.name for f in fields(DecisionInput)}
        assert not (names & DECISION_INPUT_FORBIDDEN_FIELDS)

    def test_forbidden_set_covers_the_real_decision_vocabulary(self):
        """防止禁用集被悄悄削空导致上一条测试空转。"""
        for word in ("risk_score", "risk_level", "recommended_action", "verdict", "decision"):
            assert word in DECISION_INPUT_FORBIDDEN_FIELDS

    def test_decision_input_carries_no_verdict_in_serialized_form(self):
        """序列化产物顶层同样不得出现决策语义键。"""
        payload = make_full_input().to_dict()
        assert not (set(payload) & DECISION_INPUT_FORBIDDEN_FIELDS)


# ============================================================================
# C7 —— 一级聚合白名单（防 God Object）
# ============================================================================


class TestC7FlatFieldWhitelist:
    def test_field_names_match_whitelist_exactly(self):
        names = {f.name for f in fields(DecisionInput)}
        assert names == DECISION_INPUT_FIELD_WHITELIST

    def test_whitelist_is_six_fields_hard_capped(self):
        """ADR-0040 D2：临时扩展 5→6，**6 是硬顶**（不构成 5→6→7→8 演进先例）。"""
        assert len(DECISION_INPUT_FIELD_WHITELIST) == 6

    def test_import_time_guard_actually_fires(self, monkeypatch):
        """反向变异验证：把白名单改掉后守卫**必须**抛错。

        若不做这一条，`_assert_contract_shape()` 可能是一个永远不会失败的摆设。
        """
        monkeypatch.setattr(
            dc,
            "DECISION_INPUT_FIELD_WHITELIST",
            frozenset({"trigger_events"}),
        )
        with pytest.raises(RuntimeError, match="C7"):
            dc._assert_contract_shape()

    def test_import_time_guard_rejects_forbidden_field(self, monkeypatch):
        """反向变异验证：若 DecisionInput 真的多出 risk_score，守卫必须拦下。"""
        monkeypatch.setattr(
            dc,
            "DECISION_INPUT_FORBIDDEN_FIELDS",
            frozenset({"trigger_events"}),  # 伪装成「已存在字段是禁用字段」
        )
        with pytest.raises(RuntimeError, match="ADR-0010"):
            dc._assert_contract_shape()


# ============================================================================
# C2 —— frozen + tuple 容器
# ============================================================================


class TestC2Immutability:
    def test_is_frozen(self):
        di = make_full_input()
        with pytest.raises(FrozenInstanceError):
            di.decision_context = make_ctx("other")  # type: ignore[misc]

    def test_trigger_events_is_tuple(self):
        di = make_full_input()
        assert isinstance(di.trigger_events, tuple)

    def test_list_trigger_events_rejected(self):
        """C2：容器必须是不可变 tuple —— 传 list 直接拒绝，不做静默转换。"""
        with pytest.raises(TypeError, match="tuple"):
            DecisionInput(
                trigger_events=[make_perception()],  # type: ignore[arg-type]
                decision_context=make_ctx(),
            )

    def test_none_trigger_events_rejected(self):
        with pytest.raises(ValueError, match="不能为 None"):
            DecisionInput(trigger_events=None, decision_context=make_ctx())  # type: ignore[arg-type]

    def test_wrong_element_type_rejected(self):
        with pytest.raises(TypeError, match="PerceptionEvent"):
            DecisionInput(
                trigger_events=({"event_type": "abnormal_dwell"},),  # type: ignore[arg-type]
                decision_context=make_ctx(),
            )

    def test_wrong_context_type_rejected(self):
        with pytest.raises(TypeError, match="DecisionContext"):
            DecisionInput(
                trigger_events=(make_perception(),),
                decision_context={"elder_id": "e1"},  # type: ignore[arg-type]
            )


# ============================================================================
# C3 —— 序列化往返 + 规范化排序
# ============================================================================


class TestC3Determinism:
    def test_decision_input_roundtrip(self):
        """C3：dict → obj → dict 逐键稳定（回放 / 审计一致）。"""
        original = make_full_input()
        payload = original.to_dict()
        restored = DecisionInput.from_dict(payload)
        assert restored.to_dict() == payload

    def test_roundtrip_preserves_semantic_fields(self):
        original = make_full_input()
        restored = DecisionInput.from_dict(original.to_dict())

        assert len(restored.trigger_events) == len(original.trigger_events)
        assert restored.trigger_events[0].event_type == original.trigger_events[0].event_type
        assert restored.trigger_events[0].device_id == original.trigger_events[0].device_id
        assert restored.trigger_events[0].visitor_id == original.trigger_events[0].visitor_id
        assert restored.trigger_events[0].source_video == original.trigger_events[0].source_video
        assert restored.decision_context.elder_id == original.decision_context.elder_id
        assert restored.decision_context.now == original.decision_context.now
        assert restored.decision_context.extra == original.decision_context.extra
        assert restored.reasoning_result is not None
        assert restored.reasoning_result.findings == original.reasoning_result.findings
        assert restored.prior_warning is not None
        assert restored.prior_warning.warning_id == original.prior_warning.warning_id

    def test_double_roundtrip_is_stable(self):
        payload = make_full_input().to_dict()
        once = DecisionInput.from_dict(payload).to_dict()
        twice = DecisionInput.from_dict(once).to_dict()
        assert once == twice

    def test_trigger_events_canonically_sorted_by_timestamp(self):
        """C3 规范化：构造时按 timestamp 升序，与传入次序无关。"""
        early = make_perception(timestamp=100.0, device_id="cam-early")
        late = make_perception(timestamp=200.0, device_id="cam-late")

        di = DecisionInput(trigger_events=(late, early), decision_context=make_ctx())
        assert [ev.timestamp for ev in di.trigger_events] == [100.0, 200.0]
        assert di.trigger_events[0].device_id == "cam-early"

    def test_permutations_produce_identical_ordering(self):
        """同一组事件的任意排列 → 同一规范化次序（审计一致的实际含义）。"""
        a = make_perception(timestamp=100.0, device_id="cam-a")
        b = make_perception(timestamp=200.0, device_id="cam-b")
        c = make_perception(timestamp=300.0, device_id="cam-c")

        orderings = [(a, b, c), (c, b, a), (b, a, c), (c, a, b)]
        canonical = [
            [ev.device_id for ev in DecisionInput(trigger_events=o, decision_context=make_ctx()).trigger_events]
            for o in orderings
        ]
        assert canonical == [["cam-a", "cam-b", "cam-c"]] * len(orderings)

    def test_equal_timestamps_keep_insertion_order(self):
        """同 timestamp 使用稳定排序 —— 保留传入相对次序（确定性、不引入随机）。"""
        first = make_perception(timestamp=100.0, device_id="cam-1")
        second = make_perception(timestamp=100.0, device_id="cam-2")

        di = DecisionInput(trigger_events=(second, first), decision_context=make_ctx())
        assert [ev.device_id for ev in di.trigger_events] == ["cam-2", "cam-1"]


# ============================================================================
# D2 —— Memory 可缺席原则
# ============================================================================


class TestD2MemoryMayBeAbsent:
    def test_decision_input_valid_without_memory(self):
        """D2：Memory 未启用 / 未接线 / 检索失败三态均须能合法构造且往返稳定。

        这是 D4 适配层与 Slice B「零行为变化」路径成立的前提——
        `DecisionEngine.evaluate` 装配时并无 Memory 上下文可填。
        """
        di = DecisionInput(
            trigger_events=(make_perception(),),
            decision_context=make_ctx(),
        )
        assert di.reasoning_input is None
        assert di.reasoning_result is None
        assert di.prior_warning is None

        payload = di.to_dict()
        assert payload["reasoning_input"] is None
        assert payload["reasoning_result"] is None
        assert DecisionInput.from_dict(payload).to_dict() == payload

    def test_decision_input_backward_compatible_without_reasoning_result(self):
        """护旧序列化：Slice A 之前落库的 payload 完全没有这些键。

        Agent 系统长期运行必然遇到 checkpoint / cache / replay log / historical trace
        的旧数据；缺键必须退化为 None，而不是 KeyError。
        """
        payload = make_full_input().to_dict()
        del payload["reasoning_result"]
        del payload["reasoning_input"]
        del payload["prior_warning"]

        restored = DecisionInput.from_dict(payload)
        assert restored.reasoning_result is None
        assert restored.reasoning_input is None
        assert restored.prior_warning is None
        # 未受影响的字段照常还原
        assert restored.decision_context.elder_id == "elder_001"
        assert len(restored.trigger_events) == 1

        # 向前兼容：新代码重序列化会把三个键补出来（值为 None）
        assert restored.to_dict()["reasoning_result"] is None

    def test_empty_trigger_events_is_legal(self):
        """`DecisionPolicy` 抽象基类明确「空列表 → 返回 None」。

        若 `DecisionInput` 强制非空，`DecisionEngine.evaluate([])` 会从「返回 None」
        变成「抛异常」——那是行为回归，与 Slice B 的零行为变化承诺冲突。
        """
        di = DecisionInput(trigger_events=(), decision_context=make_ctx())
        assert di.trigger_events == ()
        assert DecisionInput.from_dict(di.to_dict()).trigger_events == ()


# ============================================================================
# C2（浅层）/ 可选字段校验 / 消费者只读 —— 来自评审反馈 #3 / #5
# ============================================================================


class TestShallowImmutabilityAndValidation:
    def test_container_rebinding_is_forbidden(self):
        """C2 浅层 frozen：字段重绑被拒（嵌套对象可变是 caller-owned，不在此层冻结）。"""
        di = DecisionInput(trigger_events=(make_perception(),), decision_context=make_ctx())
        with pytest.raises(FrozenInstanceError):
            di.trigger_events = ()  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            di.decision_context = make_ctx("other")  # type: ignore[misc]

    def test_optional_fields_reject_wrong_type(self):
        """#5：可选字段传错类型应在构造处明确报错，而非在 to_dict 才 AttributeError。"""
        ctx = make_ctx()
        events = (make_perception(),)
        with pytest.raises(TypeError, match="reasoning_input"):
            DecisionInput(trigger_events=events, decision_context=ctx, reasoning_input="bad")
        with pytest.raises(TypeError, match="reasoning_result"):
            DecisionInput(trigger_events=events, decision_context=ctx, reasoning_result=123)
        with pytest.raises(TypeError, match="prior_warning"):
            DecisionInput(trigger_events=events, decision_context=ctx, prior_warning={"x": 1})

    def test_policy_does_not_mutate_inputs(self):
        """#3：消费者只读——构造后修改输入不影响决策输出（实践层不可变保证）。

        `DecisionInput` 是浅层 frozen：嵌套 `PerceptionEvent` / `DecisionContext` 由调用方
        所有、未结构冻结。本测试验证 `RuleBasedDecisionPolicy` 确实不改它们，使「浅层
        frozen 容器 + 消费者只读」足以支撑回放确定性（无需深拷贝/冻结嵌套对象）。
        """
        policy = RuleBasedDecisionPolicy()
        ctx = make_ctx()
        ev = make_perception(event_type="abnormal_dwell", score=0.5)
        di = DecisionInput(trigger_events=(ev,), decision_context=ctx)

        snapshot_score = ev.score
        snapshot_extra = dict(ctx.extra)
        w = policy.decide(di)

        assert w is not None
        assert ev.score == snapshot_score  # 未被策略改动
        assert dict(ctx.extra) == snapshot_extra  # 未被策略改动

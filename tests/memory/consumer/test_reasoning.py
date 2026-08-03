"""C-6 RuleBasedReasoningEngine 单测（ADR-0025 / DESIGN §4.3）。

torch-free。聚焦四件事——**参考推理、不决策、只读、确定性**：

- **参考推理**：从 ``ReasoningInput``（画像 / 模式 / 冲突 / 既往动作 / 历史规模）合成
  人类可读 ``findings`` + ``explanation`` + 非绑定 ``suggested_action_hint`` + 可溯源
  ``source_refs``。
- **C1 无分数（契约层）**：``ReasoningResult`` dataclass 字段集本身不含
  ``risk_score`` / ``score`` / ``decision`` / ``warning``（与 ``ReasoningInput`` 同款
  铁律，守 ADR-0010 单一决策中心）。
- **C2 只读**：``infer`` 是纯函数，只读取不可变的 ``ReasoningInput``，不改任何字段、
  不写任何外部状态（可变异验证：同输入两次产出一致 + 输入前后 to_dict 不变）。
- **C3 确定性**：同输入两次 ``infer`` 产物逐字段一致（审计 / 回放一致）。
- **hint 对齐**：``suggested_action_hint`` 仅把已观测模式翻译成与 DecisionPolicy 同词汇
  的提示（MONITOR / NOTIFY_FAMILY / ESCALATE_COMMUNITY），绝不提升风险等级。

构造 ``EpisodicRecord`` 用固定 ``source_event_ids``（回放铁律：event_id 默认随机 UUID4，
测试须显式传固定值）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
    ReasoningResult,
    RiskPattern,
    SourceRef,
    VisitorProfile,
)
from home_perception.memory.consumer.reasoning import RuleBasedReasoningEngine
from home_perception.memory.records import EpisodicRecord

ENGINE = RuleBasedReasoningEngine()
VISITOR = "visitor-reason"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _make_event(
    *,
    vid: str = VISITOR,
    risk_level: str | None = None,
    markers: tuple[str, ...] = (),
) -> CurrentEvent:
    return CurrentEvent(
        event_id=f"cur-{vid}",
        event_type="visitor_event",
        visitor_instance_id=vid,
        occurred_at=_utc(2026, 7, 20, 21, 0, 0),
        risk_level=risk_level,
        markers=markers,
    )


def _make_record(rid: str, vid: str = VISITOR, *, risk_level: str | None = None) -> EpisodicRecord:
    enter = _utc(2026, 7, 18, 10, 0, 0)
    leave = enter + timedelta(minutes=5)
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=vid,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=300.0,
        source_event_ids=[f"ev-{rid}"],
        summary=f"visit {rid}",
        model_version="ep-builder-v1",
        risk_level=risk_level,
    )


def _full_input() -> ReasoningInput:
    """一个"Memory 改变了理解"的富上下文：画像 + 模式 + 冲突 + 既往动作 + 历史。"""
    profile = VisitorProfile(
        visitor_instance_id=VISITOR,
        visit_count=5,
        night_visit_ratio=1.0,
        confidence="stable_pattern",
        identity_confirmed=False,
        first_seen=_utc(2026, 7, 10, 22, 0, 0),
        last_seen=_utc(2026, 7, 19, 23, 0, 0),
    )
    pattern = RiskPattern(
        tags=("repeated_visit", "escalating_behavior"),
        escalation_history=("night", "observe_camera"),
        confidence="stable_pattern",
    )
    conflicts = (
        ConflictFlag(
            type="risk_escalation",
            historical="LOW",
            current="HIGH",
            detail="当前风险等级严格高于历史最高",
        ),
        ConflictFlag(
            type="behavior_shift",
            historical="daytime_visit",
            current="observe_camera",
            detail="新增行为标记 observe_camera 历史未见",
        ),
    )
    previous = (
        ActionRecord(command_type="SEND_FAMILY_MESSAGE", command_id="cmd-1", status="done"),
        ActionRecord(command_type="LOG_ONLY", command_id="cmd-2", status="done"),
    )
    history = (_make_record("ep-1"), _make_record("ep-2", risk_level="MEDIUM"))
    return ReasoningInput(
        current_event=_make_event(risk_level="HIGH", markers=("night", "observe_camera")),
        historical_context=history,
        visitor_profile=profile,
        risk_pattern=pattern,
        evidence_refs=(),
        previous_actions=previous,
        conflicts=conflicts,
    )


# ============================================================================
# 1. 参考推理：产出形态与内容
# ============================================================================


class TestReasoningOutput:
    def test_produces_reasoning_result_with_all_sections(self):
        out = ENGINE.infer(_full_input())
        assert isinstance(out, ReasoningResult)
        assert out.findings, "findings 不能为空"
        assert out.explanation
        # 当前事件 / 画像 / 模式 / 冲突 / 既往动作 / 历史 六段都应有对应 finding
        joined = "\n".join(out.findings)
        assert "当前事件" in joined
        assert "历史到访 5 次" in joined
        assert "发现风险模式" in joined
        assert "检测到冲突" in joined
        assert "既往动作" in joined
        assert "可召回历史记录 2 条" in joined

    def test_source_refs_cover_every_input_field(self):
        out = ENGINE.infer(_full_input())
        sources = {s.source for s in out.source_refs}
        # 每个非空输入字段都应有溯源锚点
        assert {"current_event", "visitor_profile", "risk_pattern", "conflicts",
                "previous_actions", "historical_context"} <= sources
        for s in out.source_refs:
            assert s.source  # SourceRef.source 必填

    def test_explanation_mentions_pattern_and_conflict(self):
        out = ENGINE.infer(_full_input())
        assert "风险模式" in out.explanation
        assert "冲突" in out.explanation
        assert "source_refs" in out.explanation


# ============================================================================
# 2. C1 无分数（契约层，独立于运行期行为）
# ============================================================================


class TestContractC1NoScore:
    def test_reasoning_result_has_no_decision_fields(self):
        """C1（契约层）：``ReasoningResult`` dataclass 字段集本身不含 score/decision/warning。

        与 ReasoningInput 同款铁律——把"推理不决策"从运行期产物提升为数据结构约束。
        """
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ReasoningResult)}
        forbidden = {"risk_score", "score", "decision", "warning", "recommended_action"}
        assert not (field_names & forbidden), (
            f"ReasoningResult 含禁止字段: {field_names & forbidden}"
        )
        # 字段集正是契约声明的 4 个，无漂移
        assert field_names == {
            "findings",
            "explanation",
            "suggested_action_hint",
            "source_refs",
        }

    def test_reasoning_input_still_has_no_decision_fields(self):
        """回归：ReasoningInput 的 C1 白名单不被本次改动破坏。"""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ReasoningInput)}
        forbidden = {"risk_score", "score", "decision", "warning", "recommended_action"}
        assert not (field_names & forbidden)


# ============================================================================
# 3. C2 只读（纯函数，不写外部状态）
# ============================================================================


class TestReadOnly:
    def test_input_unchanged_after_infer(self):
        """C2：infer 前后 ReasoningInput.to_dict() 逐字段不变（只读铁律）。"""
        inp = _full_input()
        before = inp.to_dict()
        ENGINE.infer(inp)
        ENGINE.infer(inp)
        assert inp.to_dict() == before

    def test_does_not_accept_unknown_action_hint(self):
        """构造非法 hint 应被 ReasoningResult.__post_init__ 拒绝（防污染单一决策中心词汇）。"""
        import pytest

        from home_perception.memory.consumer.contracts import RECOMMENDED_ACTION_HINTS

        with pytest.raises(ValueError, match="suggested_action_hint"):
            ReasoningResult(
                findings=("x",),
                explanation="y",
                suggested_action_hint="LAUNCH_NUKES",
            )
        # 合法词汇放行
        ok = ReasoningResult(findings=("x",), explanation="y",
                              suggested_action_hint=RECOMMENDED_ACTION_HINTS[0])
        assert ok.suggested_action_hint == RECOMMENDED_ACTION_HINTS[0]


# ============================================================================
# 4. C3 确定性
# ============================================================================


class TestC3Determinism:
    def test_same_input_same_output(self):
        """C3：同 ReasoningInput 两次 infer 产物逐字段一致（审计 / 回放一致）。"""
        inp = _full_input()
        a = ENGINE.infer(inp).to_dict()
        b = ENGINE.infer(inp).to_dict()
        assert a == b

    def test_to_dict_roundtrip(self):
        out = ENGINE.infer(_full_input())
        restored = ReasoningResult.from_dict(out.to_dict())
        assert restored.to_dict() == out.to_dict()
        assert all(isinstance(s, SourceRef) for s in restored.source_refs)


# ============================================================================
# 5. suggested_action_hint 对齐（仅 advisory，不提升风险等级）
# ============================================================================


class TestActionHintAdvisory:
    @staticmethod
    def _hint_for(*, risk_level=None, tags=(), conflicts=()):
        inp = ReasoningInput(
            current_event=_make_event(risk_level=risk_level),
            historical_context=(),
            visitor_profile=None,
            risk_pattern=RiskPattern(tags=tags) if tags else None,
            conflicts=conflicts,
        )
        return ENGINE.infer(inp).suggested_action_hint

    def test_high_risk_maps_to_escalate(self):
        assert self._hint_for(risk_level="HIGH") == "ESCALATE_COMMUNITY"

    def test_medium_risk_maps_to_notify_family(self):
        assert self._hint_for(risk_level="MEDIUM") == "NOTIFY_FAMILY"

    def test_low_with_repeated_visit_maps_to_monitor(self):
        assert self._hint_for(risk_level="LOW", tags=("repeated_visit",)) == "MONITOR"

    def test_escalating_behavior_maps_to_notify_family(self):
        assert (
            self._hint_for(risk_level="LOW", tags=("escalating_behavior",))
            == "NOTIFY_FAMILY"
        )

    def test_conflict_maps_to_notify_family(self):
        conflict = ConflictFlag(type="behavior_shift", historical="a", current="b", detail="d")
        assert self._hint_for(risk_level="LOW", conflicts=(conflict,)) == "NOTIFY_FAMILY"

    def test_no_signal_no_pattern_no_conflict_is_none(self):
        assert self._hint_for() is None

    def test_single_visit_no_history_is_none(self):
        # 孤立事件（无风险 / 无模式 / 无冲突）→ 不强行提示
        assert self._hint_for(risk_level="LOW") is None

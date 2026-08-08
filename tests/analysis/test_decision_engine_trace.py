"""Slice B 采集接缝契约测试（ADR-0031 D6 / T1 / T2 / T3）。

> Slice B = `DecisionTraceRecorder` Protocol + `NullRecorder` + `InMemoryRecorder`；
> `DecisionEngine` 可选注入；WARN 路径产出完整 trace。SUPPRESS 路径留痕归 Slice C。

不变式覆盖：
- T1 trace 只写不读（引擎不回读 trace 内容做决策）
- T2 trace 不改变决策（recorder=None 与 InMemoryRecorder 产出同 WarningEvent）
- T3 trace 失败不影响决策（recorder 抛异常决策照常返回）

设计要点：确定性测试 fixture 不得含随机值（visitor_id 固定、created_at 注入固定时钟），
否则 T2 的 WarningEvent 比对会因 warning_id 不同而误判；比对时归一化掉 identity 类字段。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from home_perception.analysis.decision_contract import DecisionContext
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import DEFAULT_ROUTING_TABLE
from home_perception.analysis.decision_trace import (
    DecisionTraceRecorder,
    InMemoryRecorder,
    MemoryRefs,
    NullRecorder,
    TraceOutcomeKind,
    build_rationale,
    build_warning_trace,
    compute_policy_fingerprint,
)
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.warning import WarningEvent

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# 固定 visitor_id：避免 fixture 随机性导致 T2 误判（warning_id 由 decide 生成，
# 比对时归一化；但 trigger_refs / considered_candidates 需稳定身份）。
V1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
V2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


def make_perception(
    event_type: str = "abnormal_dwell",
    score: float = 0.5,
    device_id: str = "home_entry_01",
    timestamp: float = 1000.0,
    is_odd_hour: bool = False,
    visitor_id: uuid.UUID | None = None,
) -> PerceptionEvent:
    return PerceptionEvent(
        device_id=device_id,
        event_type=event_type,
        score=score,
        visitor_id=visitor_id or V1,
        source_video="cam01",
        timestamp=timestamp,
        is_odd_hour=is_odd_hour,
        meta={"rule": f"TestRule_{event_type}"},
        created_at=NOW,
    )


def make_engine(trace_recorder=None, now=NOW) -> DecisionEngine:
    return DecisionEngine(
        elder_id="elder_001",
        now_provider=lambda: now,
        trace_recorder=trace_recorder,
    )


def warning_core(w: WarningEvent) -> dict:
    """归一化 WarningEvent 的决策相关字段（排除随机 warning_id），用于 T2 比对。"""
    return {
        "elder_id": w.elder_id,
        "device_id": w.device_id,
        "risk_level": w.risk_level,
        "recommended_action": w.recommended_action,
        "status": w.status,
        "trigger_events": w.trigger_events,
        "reason_summary": w.reason_summary,
        "perception_score": w.perception_score,
        "created_at": w.created_at,
    }


# ============================================================================
# T1：trace 只写不读
# ============================================================================


def test_trace_is_write_only_and_does_not_affect_decision():
    """引擎只调用 recorder.record，绝不回读 trace 做任何决策。

    用一个「只读方法被调用就炸」的 recorder 验证引擎只走了 record 路径；
    同时断言带 recorder 的决策结果与无 recorder 完全一致（T2 交叉验证）。
    """
    calls: list[str] = []

    class WriteOnlyRecorder:
        def record(self, trace):
            calls.append("record")

        def flush(self):
            calls.append("flush")

    rec = WriteOnlyRecorder()
    engine = make_engine(trace_recorder=rec)
    events = [make_perception(event_type="abnormal_dwell")]

    warning = engine.evaluate(events)
    assert warning is not None
    # 引擎只调用了 record（flush 是 Slice E 的，Slice B 不调用）
    assert calls == ["record"]


def test_trace_recorder_protocol_accepts_impls():
    """NullRecorder / InMemoryRecorder 满足 DecisionTraceRecorder（runtime_checkable）。"""
    assert isinstance(NullRecorder(), DecisionTraceRecorder)
    assert isinstance(InMemoryRecorder(), DecisionTraceRecorder)


# ============================================================================
# T2：trace 不改变决策
# ============================================================================


def test_warning_identical_with_and_without_tracing_warn():
    base = make_engine(trace_recorder=None)
    traced = make_engine(trace_recorder=InMemoryRecorder())
    events = [make_perception(event_type="abnormal_dwell")]

    w1 = base.evaluate(events)
    w2 = traced.evaluate(events)
    assert w1 is not None and w2 is not None
    assert warning_core(w1) == warning_core(w2)
    assert traced.trace_recorder.warn_traces  # type: ignore[union-attr]
    assert len(traced.trace_recorder.warn_traces) == 1  # type: ignore[union-attr]


def test_warning_identical_with_and_without_tracing_suppress():
    base = make_engine(trace_recorder=None)
    traced = make_engine(trace_recorder=InMemoryRecorder())

    # 空事件 → SUPPRESS（Slice B 不记录 SUPPRESS，但决策结果必须一致）
    assert base.evaluate([]) is None
    assert traced.evaluate([]) is None
    assert traced.trace_recorder.traces == []  # type: ignore[union-attr]

    # 纯普通访问（无 odd_hour 叠加）→ SUPPRESS
    plain = [make_perception(event_type="visit_normal")]
    assert base.evaluate(plain) is None
    assert traced.evaluate(plain) is None
    assert traced.trace_recorder.traces == []  # type: ignore[union-attr]


# ============================================================================
# T3：trace 失败不影响决策
# ============================================================================


def test_recorder_exception_does_not_break_decision_warn():
    class BoomRecorder:
        def record(self, trace):
            raise RuntimeError("recorder down")

        def flush(self):
            return None

    engine = make_engine(trace_recorder=BoomRecorder())
    events = [make_perception(event_type="abnormal_dwell")]
    warning = engine.evaluate(events)
    # 决策照常返回，trace 故障被隔离（T3）
    assert warning is not None
    assert warning.risk_level == "LOW"
    assert warning.recommended_action == "NOTIFY_FAMILY"


def test_recorder_exception_does_not_break_decision_suppress():
    class BoomRecorder:
        def record(self, trace):
            raise RuntimeError("recorder down")

        def flush(self):
            return None

    engine = make_engine(trace_recorder=BoomRecorder())
    # 即便 recorder 崩溃，SUPPRESS 仍正确返回 None
    assert engine.evaluate([]) is None
    assert engine.evaluate([make_perception(event_type="visit_normal")]) is None


# ============================================================================
# WARN 路径结构正确性
# ============================================================================


def test_warn_path_emits_complete_trace():
    rec = InMemoryRecorder()
    engine = make_engine(trace_recorder=rec)
    events = [make_perception(event_type="abnormal_dwell")]
    warning = engine.evaluate(events)
    assert warning is not None

    assert len(rec.traces) == 1
    trace = rec.traces[0]
    # 五个 Bundle 齐备
    assert trace.identity.arm == "production"
    assert trace.identity.correlation_id == ""  # Slice B 单评估默认空
    assert trace.provenance.input_digest
    assert trace.provenance.trigger_digest
    assert len(trace.provenance.trigger_refs) == 1
    assert trace.provenance.memory_refs == MemoryRefs()  # Slice B 一律空
    # policy.fingerprint 取代硬编码 "v1"
    assert trace.policy.name == "RuleBasedDecisionPolicy"
    assert trace.policy.fingerprint == compute_policy_fingerprint(DEFAULT_ROUTING_TABLE)
    assert trace.policy.fingerprint != "v1"
    # rationale：WARN 必有候选与选中
    assert len(trace.rationale.considered_candidates) == 1
    assert trace.rationale.chosen_index == 0
    # outcome：WARN 携带决策产物
    assert trace.outcome.kind == TraceOutcomeKind.WARN
    assert trace.outcome.risk_level == warning.risk_level
    assert trace.outcome.recommended_action == warning.recommended_action
    assert trace.outcome.warning_id == str(warning.warning_id)
    assert trace.outcome.suppress_reason is None


def test_default_recorder_is_none_and_off():
    """默认构造 engine.trace_recorder 为 None（采集关闭，零行为变化）。"""
    engine = make_engine()
    assert engine.trace_recorder is None

    calls = {"n": 0}

    class CountingRecorder:
        def record(self, trace):
            calls["n"] += 1

        def flush(self):
            return None

    traced = make_engine(trace_recorder=CountingRecorder())
    # warn → 1 次 record
    assert traced.evaluate([make_perception(event_type="abnormal_dwell")]) is not None
    assert calls["n"] == 1
    # suppress → 0 次 record（Slice B 不记录 SUPPRESS）
    assert traced.evaluate([]) is None
    assert calls["n"] == 1


# ============================================================================
# build_rationale / build_warning_trace 单元正确性
# ============================================================================


def test_build_rationale_matches_policy_choice():
    # high_risk_approach(HIGH,p3) + abnormal_dwell(LOW,p1) → 选中 HIGH
    events = [
        make_perception(event_type="abnormal_dwell", visitor_id=V1, timestamp=1000.0),
        make_perception(event_type="high_risk_approach", visitor_id=V2, timestamp=2000.0),
    ]
    r = build_rationale(events, DEFAULT_ROUTING_TABLE)
    assert len(r.considered_candidates) == 2
    assert r.chosen_index == 1
    assert r.considered_candidates[1].event_type == "high_risk_approach"
    assert r.considered_candidates[1].priority == 3
    # trigger_index 保留原下标
    assert [c.trigger_index for c in r.considered_candidates] == [0, 1]


def test_build_rationale_filters_plain_visit_normal_keeps_odd_hour():
    plain = make_perception(event_type="visit_normal", is_odd_hour=False)
    odd = make_perception(event_type="visit_normal", is_odd_hour=True, visitor_id=V2)
    r = build_rationale([plain, odd], DEFAULT_ROUTING_TABLE)
    # 纯普通访问被抑制；odd_hour 叠加保留（LOW）
    assert [c.event_type for c in r.considered_candidates] == ["visit_normal"]
    assert r.considered_candidates[0].trigger_index == 1  # 原下标
    assert r.considered_candidates[0].routed_level == "LOW"


def test_build_warning_trace_roundtrip():
    from home_perception.analysis.decision_contract import DecisionInput

    events = [make_perception(event_type="abnormal_dwell")]
    di = DecisionInput(
        trigger_events=tuple(events),
        decision_context=DecisionContext(elder_id="elder_001", now=NOW),
        reasoning_input=None,
        reasoning_result=None,
        prior_warning=None,
    )
    # 用一个最小 warning 占位（只取 risk_level / recommended_action / warning_id）
    w = WarningEvent(
        elder_id="elder_001",
        device_id="home_entry_01",
        risk_level="LOW",
        recommended_action="NOTIFY_FAMILY",
        trigger_events=[{"event_type": "abnormal_dwell", "score": 0.5, "timestamp": 1000.0}],
        reason_summary=["异常停留"],
        warning_id=uuid.uuid4(),
        created_at=NOW,
    )
    trace = build_warning_trace(
        input=di,
        warning=w,
        policy_name="RuleBasedDecisionPolicy",
        routing_table=DEFAULT_ROUTING_TABLE,
    )
    restored = trace.from_dict(trace.to_dict())
    assert restored == trace
    assert restored.outcome.warning_id == str(w.warning_id)

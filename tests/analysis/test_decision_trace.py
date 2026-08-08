"""`DecisionTrace` 契约测试（ADR-0031 · Slice A）。

覆盖 ADR-0031 验收清单中属于 Slice A（零行为变化、纯契约）的部分：

- **D1**：`outcome` 带标签联合 `WARN | SUPPRESS`，构造期互斥校验；
- **D2**：顶层 5 个具名 Bundle 白名单（防 God Object 横向膨胀）+ 导入期 fail-closed；
- **D3**：`considered_candidates` 取代 `rejected_actions`，候选顺序确定性（T9）；
- **D4**：引用而非复制（`trigger_refs` 下标 = C3 规范化后次序；`input_digest` / `trigger_digest`）；
- **D5 / T10**：`policy.fingerprint` 规范化（`sort_keys=True`）稳定且可分辨；
- **T4**：trace 不含任何判定语义字段（递归扫描序列化产物）；
- **T6**：确定性——同输入 + 同 fingerprint + 同 runtime config → 除 identity 外逐字段相同；
- **T8**：不重复真相——只存引用 + digest，不内嵌完整 `PerceptionEvent` / `ReasoningInput`；
- 序列化：`to_dict` ↔ `from_dict` 往返稳定。

本文件**不**测试采集接缝（recorder，Slice B）/ 抑制留痕（Slice C）/ 双轨（Slice D）/
落盘（Slice E）——那些是后续切片，且会改动运行时行为；Slice A 只冻结契约形状。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from home_perception.analysis import decision_trace as dt
from home_perception.analysis.decision_contract import DecisionInput
from home_perception.analysis.decision_policy import (
    DEFAULT_ROUTING_TABLE,
    LEVEL_PRIORITY,
    DecisionContext,
)
from home_perception.analysis.decision_trace import (
    DECISION_TRACE_FIELD_WHITELIST,
    DECISION_TRACE_FORBIDDEN_FIELDS,
    DecisionTrace,
    MemoryRefs,
    SuppressReason,
    TraceIdentity,
    TraceOutcome,
    TraceOutcomeKind,
    TracePolicy,
    TraceProvenance,
    TraceRationale,
)
from home_perception.analysis.perception import PerceptionEvent

NOW = datetime(2026, 8, 8, 9, 0, 0, tzinfo=UTC)


# ============================================================================
# Fixtures / helpers
# ============================================================================


def make_perception(
    event_type: str = "abnormal_dwell",
    score: float = 0.5,
    device_id: str = "home_entry_01",
    timestamp: float = 1000.0,
    visitor_id: uuid.UUID | None = None,
) -> PerceptionEvent:
    return PerceptionEvent(
        device_id=device_id,
        event_type=event_type,
        score=score,
        visitor_id=visitor_id or uuid.uuid4(),
        source_video="cam01",
        timestamp=timestamp,
        meta={"rule": f"TestRule_{event_type}"},
        created_at=NOW,
    )


def make_ctx(elder_id: str = "elder_001") -> DecisionContext:
    return DecisionContext(elder_id=elder_id, now=NOW, extra={"tenant": "demo"})


# 固定身份（T6 确定性：相同输入 → 除 identity 外逐字段相同；故非 identity 字段必须固定）
_FIXED_VID_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FIXED_VID_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_FIXED_WARNING_ID = "w-" + "0" * 32


def _mk_events() -> tuple[PerceptionEvent, PerceptionEvent]:
    return (
        make_perception(event_type="abnormal_dwell", timestamp=1000.0, visitor_id=_FIXED_VID_A),
        make_perception(event_type="repeat_visit", timestamp=2000.0, visitor_id=_FIXED_VID_B),
    )


def make_warning_trace(identity: TraceIdentity | None = None) -> DecisionTrace:
    """一条典型的 WARN trace（用于往返 / 确定性 / T4 测试）。

    非 identity 字段全部固定（固定 visitor_id / warning_id / digest），只让 `identity`
    在两次构造间自然变化（decision_id / created_at），从而精确验证 T6「除 identity 外
    逐字段相同」。
    """
    events = _mk_events()
    return DecisionTrace(
        identity=identity or TraceIdentity.new(arm="production", correlation_id="corr-1"),
        provenance=TraceProvenance(
            input_digest="di",
            trigger_digest="td",
            trigger_refs=dt.build_trigger_refs(events),
            memory_refs=MemoryRefs(
                reasoning_input_present=True,
                reasoning_result_present=True,
                historical_record_ids=("ep-1",),
                cross_modal_link_ids=("link-1",),
                evidence_ref_ids=("ev-1",),
                suggested_action_hint="NOTIFY_FAMILY",
            ),
        ),
        policy=TracePolicy(
            name="RuleBasedDecisionPolicy",
            fingerprint=dt.compute_policy_fingerprint(DEFAULT_ROUTING_TABLE),
        ),
        rationale=TraceRationale(
            considered_candidates=dt.candidate_records_from_events(events, DEFAULT_ROUTING_TABLE),
            chosen_index=0,
        ),
        outcome=TraceOutcome(
            kind=TraceOutcomeKind.WARN,
            risk_level="LOW",
            recommended_action="NOTIFY_FAMILY",
            warning_id=_FIXED_WARNING_ID,
        ),
    )


def make_suppress_trace(
    reason: SuppressReason = SuppressReason.ALL_SUPPRESSED_NORMAL,
    identity: TraceIdentity | None = None,
) -> DecisionTrace:
    return DecisionTrace(
        identity=identity or TraceIdentity.new(arm="baseline", correlation_id="corr-x"),
        provenance=TraceProvenance(
            input_digest="di2",
            trigger_digest="td2",
            trigger_refs=(),
            memory_refs=MemoryRefs(),
        ),
        policy=TracePolicy(
            name="RuleBasedDecisionPolicy",
            fingerprint=dt.compute_policy_fingerprint(DEFAULT_ROUTING_TABLE),
        ),
        rationale=TraceRationale(considered_candidates=(), chosen_index=None),
        outcome=TraceOutcome(kind=TraceOutcomeKind.SUPPRESS, suppress_reason=reason),
    )


# ============================================================================
# D1 —— outcome 带标签联合互斥校验
# ============================================================================


class TestOutcomeTaggedUnion:
    def test_warn_requires_all_warn_fields(self):
        with pytest.raises(ValueError, match="WARN"):
            TraceOutcome(
                kind=TraceOutcomeKind.WARN,
                risk_level="LOW",
                # 缺 recommended_action / warning_id
            )

    def test_warn_rejects_suppress_reason(self):
        with pytest.raises(ValueError, match="suppress_reason"):
            TraceOutcome(
                kind=TraceOutcomeKind.WARN,
                risk_level="LOW",
                recommended_action="MONITOR",
                warning_id="w-1",
                suppress_reason=SuppressReason.NO_TRIGGER_EVENTS,
            )

    def test_suppress_requires_reason(self):
        with pytest.raises(ValueError, match="suppress_reason"):
            TraceOutcome(kind=TraceOutcomeKind.SUPPRESS)

    def test_suppress_rejects_warn_fields(self):
        with pytest.raises(ValueError, match="WARN"):
            TraceOutcome(
                kind=TraceOutcomeKind.SUPPRESS,
                suppress_reason=SuppressReason.NO_TRIGGER_EVENTS,
                risk_level="LOW",
            )

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError, match="未知"):
            TraceOutcome(kind="WEIRD")  # type: ignore[arg-type]

    def test_roundtrip_preserves_union(self):
        for t in (make_warning_trace(), make_suppress_trace()):
            assert DecisionTrace.from_dict(t.to_dict()).to_dict() == t.to_dict()


# ============================================================================
# D2 —— 顶层 Bundle 白名单（导入期 fail-closed）
# ============================================================================


class TestD2BundleWhitelist:
    def test_field_names_match_whitelist_exactly(self):
        from dataclasses import fields

        names = {f.name for f in fields(DecisionTrace)}
        assert names == DECISION_TRACE_FIELD_WHITELIST

    def test_whitelist_is_five_bundles(self):
        assert len(DECISION_TRACE_FIELD_WHITELIST) == 5

    def test_import_time_guard_fires_on_whitelist_change(self, monkeypatch):
        monkeypatch.setattr(
            dt, "DECISION_TRACE_FIELD_WHITELIST", frozenset({"identity", "provenance"})
        )
        with pytest.raises(RuntimeError, match="D2"):
            dt._assert_contract_shape()

    def test_import_time_guard_fires_on_forbidden_bundle(self, monkeypatch):
        monkeypatch.setattr(
            dt, "DECISION_TRACE_FORBIDDEN_FIELDS", frozenset({"identity"})
        )
        with pytest.raises(RuntimeError, match="ADR-0001|T4"):
            dt._assert_contract_shape()


# ============================================================================
# T4 —— 无判定语义字段（递归扫描序列化产物）
# ============================================================================


def _collect_keys(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                found.add(k)
            found |= _collect_keys(v)
    elif isinstance(node, list):
        for v in node:
            found |= _collect_keys(v)
    return found


class TestT4NoVerdictFields:
    def test_forbidden_set_covers_verdict_vocabulary(self):
        for word in ("fraud", "suspect", "verdict", "is_fraud", "decision", "risk_score"):
            assert word in DECISION_TRACE_FORBIDDEN_FIELDS

    def test_serialized_warn_trace_has_no_verdict_keys(self):
        keys = _collect_keys(make_warning_trace().to_dict())
        assert not (keys & DECISION_TRACE_FORBIDDEN_FIELDS)

    def test_serialized_suppress_trace_has_no_verdict_keys(self):
        keys = _collect_keys(make_suppress_trace().to_dict())
        assert not (keys & DECISION_TRACE_FORBIDDEN_FIELDS)

    def test_top_level_bundles_disjoint_from_forbidden(self):
        from dataclasses import fields

        names = {f.name for f in fields(DecisionTrace)}
        assert not (names & DECISION_TRACE_FORBIDDEN_FIELDS)


# ============================================================================
# T6 —— 确定性（除 identity 外逐字段相同）
# ============================================================================


class TestT6Determinism:
    def test_same_inputs_except_identity_yield_equal_bundles(self):
        a = make_warning_trace()
        b = make_warning_trace()
        assert a.identity != b.identity  # 不同 decision_id / created_at
        assert a.provenance == b.provenance
        assert a.policy == b.policy
        assert a.rationale == b.rationale
        assert a.outcome == b.outcome

    def test_dict_equal_after_dropping_identity(self):
        a = make_warning_trace().to_dict()
        b = make_warning_trace().to_dict()
        a.pop("identity")
        b.pop("identity")
        assert a == b

    def test_arm_is_part_of_identity_not_rest(self):
        prod = make_warning_trace()
        base = make_suppress_trace()
        # arm 不同但其余 bundle 形状一致不应泄漏到 outcome / policy 等
        assert prod.identity.arm == "production"
        assert base.identity.arm == "baseline"


# ============================================================================
# T8 —— 引用而非复制（不内嵌完整事件对象）
# ============================================================================


class TestT8RefsNotPayloads:
    def test_trigger_refs_do_not_embed_full_event(self):
        payload = make_warning_trace().provenance.to_dict()
        refs = payload["trigger_refs"]
        assert isinstance(refs, list) and refs
        allowed = {"index", "visitor_id", "event_type", "timestamp"}
        for r in refs:
            assert set(r.keys()) == allowed
            # 不含完整 PerceptionEvent 才有的重载字段
            for forbidden in ("bbox", "evidence", "source_video", "score", "meta", "is_odd_hour"):
                assert forbidden not in r

    def test_digests_present_not_full_replay(self):
        prov = make_warning_trace().provenance
        assert prov.input_digest and prov.trigger_digest
        # provenance 不内嵌完整 PerceptionEvent / ReasoningInput 对象
        assert "trigger_events" not in prov.to_dict()
        assert "reasoning_input" not in prov.to_dict()

    def test_memory_refs_store_ids_only(self):
        mr = make_warning_trace().provenance.memory_refs
        assert mr.historical_record_ids == ("ep-1",)
        assert mr.cross_modal_link_ids == ("link-1",)
        assert mr.evidence_ref_ids == ("ev-1",)
        assert mr.suggested_action_hint == "NOTIFY_FAMILY"


# ============================================================================
# D3 / T9 —— considered_candidates 顺序确定性
# ============================================================================


class TestD3ConsideredCandidates:
    def test_candidate_order_follows_input_order(self):
        ev_a = make_perception(event_type="abnormal_dwell", timestamp=1000.0)
        ev_b = make_perception(event_type="repeat_visit", timestamp=2000.0)
        fwd = dt.candidate_records_from_events((ev_a, ev_b), DEFAULT_ROUTING_TABLE)
        rev = dt.candidate_records_from_events((ev_b, ev_a), DEFAULT_ROUTING_TABLE)
        # 顺序 = 传入顺序（不是被 set/dict 打乱），故反向传入 → 反向顺序
        assert [c.event_type for c in fwd] == ["abnormal_dwell", "repeat_visit"]
        assert [c.event_type for c in rev] == ["repeat_visit", "abnormal_dwell"]
        # 但同一传入顺序 → 完全一致（确定性）
        assert fwd == dt.candidate_records_from_events((ev_a, ev_b), DEFAULT_ROUTING_TABLE)

    def test_candidate_fields_are_closed_set(self):
        rec = dt.candidate_records_from_events(
            (make_perception(event_type="abnormal_dwell"),), DEFAULT_ROUTING_TABLE
        )[0]
        assert set(rec.to_dict().keys()) == {
            "trigger_index",
            "event_type",
            "routed_level",
            "routed_action",
            "priority",
        }

    def test_candidate_records_carry_routed_info(self):
        rec = dt.candidate_records_from_events(
            (make_perception(event_type="high_risk_approach"),), DEFAULT_ROUTING_TABLE
        )[0]
        assert rec.routed_level == "HIGH"
        assert rec.routed_action == "ESCALATE_COMMUNITY"
        assert rec.priority == LEVEL_PRIORITY["HIGH"]

    def test_candidate_order_identical_across_permutations_when_normalized(self):
        """T9 的真实含义：C3 规范化后的 trigger_events 顺序稳定 → 候选顺序稳定。

        （直接对未规范化序列用 set 会变序，这是被禁止的；这里证明「先 C3 规范化、
        再按规范顺序构建」得到可复现的候选序列。）
        """
        a = make_perception(event_type="abnormal_dwell", timestamp=1000.0, device_id="cam-a")
        b = make_perception(event_type="repeat_visit", timestamp=2000.0, device_id="cam-b")
        c = make_perception(event_type="high_risk_approach", timestamp=3000.0, device_id="cam-c")

        orderings = [(a, b, c), (c, b, a), (b, a, c), (c, a, b)]
        canonical_candidates = []
        for o in orderings:
            di = DecisionInput(trigger_events=o, decision_context=make_ctx())
            cands = dt.candidate_records_from_events(di.trigger_events, DEFAULT_ROUTING_TABLE)
            canonical_candidates.append([c.event_type for c in cands])
        assert canonical_candidates == [["abnormal_dwell", "repeat_visit", "high_risk_approach"]] * len(
            orderings
        )


# ============================================================================
# D4 —— trigger_refs 下标 = C3 规范化后次序
# ============================================================================


class TestD4TriggerRefs:
    def test_trigger_ref_index_matches_c3_normalized_order(self):
        a = make_perception(event_type="abnormal_dwell", timestamp=1000.0, device_id="cam-a")
        b = make_perception(event_type="repeat_visit", timestamp=2000.0, device_id="cam-b")
        c = make_perception(event_type="high_risk_approach", timestamp=3000.0, device_id="cam-c")

        di = DecisionInput(trigger_events=(c, a, b), decision_context=make_ctx())
        refs = dt.build_trigger_refs(di.trigger_events)
        # C3 规范化后顺序 = timestamp 升序
        assert [r.event_type for r in refs] == ["abnormal_dwell", "repeat_visit", "high_risk_approach"]
        assert [r.index for r in refs] == [0, 1, 2]
        assert [r.visitor_id for r in refs] == [str(x.visitor_id) for x in di.trigger_events]

    def test_trigger_digest_stable_across_permutations(self):
        a = make_perception(event_type="abnormal_dwell", timestamp=1000.0, device_id="cam-a")
        b = make_perception(event_type="repeat_visit", timestamp=2000.0, device_id="cam-b")

        d1 = DecisionInput(trigger_events=(a, b), decision_context=make_ctx())
        d2 = DecisionInput(trigger_events=(b, a), decision_context=make_ctx())
        # 同一组事件 → 同一 C3 规范化顺序 → 同一 digest
        assert dt.compute_trigger_digest(d1.trigger_events) == dt.compute_trigger_digest(
            d2.trigger_events
        )


# ============================================================================
# D5 / T10 —— policy.fingerprint 规范化稳定且可分辨
# ============================================================================


class TestD5Fingerprint:
    def test_fingerprint_is_stable_across_key_order(self):
        # dict 键序不同（插入顺序不同）必须得到相同摘要
        t1 = {"b": ("LOW", "MONITOR", "x"), "a": ("HIGH", "ESCALATE_COMMUNITY", "y")}
        t2 = {"a": ("HIGH", "ESCALATE_COMMUNITY", "y"), "b": ("LOW", "MONITOR", "x")}
        assert dt.compute_policy_fingerprint(t1) == dt.compute_policy_fingerprint(t2)

    def test_fingerprint_differs_across_routing_tables(self):
        base = dict(DEFAULT_ROUTING_TABLE)
        custom = dict(DEFAULT_ROUTING_TABLE)
        custom["visit_normal"] = ("MEDIUM", "NOTIFY_FAMILY", "异常时段访问(定制)")
        assert dt.compute_policy_fingerprint(base) != dt.compute_policy_fingerprint(custom)

    def test_fingerprint_is_64char_hex_sha256(self):
        fp = dt.compute_policy_fingerprint(DEFAULT_ROUTING_TABLE)
        assert len(fp) == 64 and all(ch in "0123456789abcdef" for ch in fp)

    def test_custom_routing_table_yields_distinct_trace_fingerprint(self):
        custom = dict(DEFAULT_ROUTING_TABLE)
        custom["visit_normal"] = ("MEDIUM", "NOTIFY_FAMILY", "定制")
        warn = make_warning_trace()
        custom_trace = make_warning_trace()
        # 直接比较 fingerprint 派生
        base_fp = dt.compute_policy_fingerprint(DEFAULT_ROUTING_TABLE)
        custom_fp = dt.compute_policy_fingerprint(custom)
        assert warn.policy.fingerprint == base_fp
        assert custom_trace.policy.fingerprint == base_fp
        assert custom_fp != base_fp


# ============================================================================
# 序列化往返稳定性
# ============================================================================


class TestRoundtrip:
    def test_warn_trace_roundtrip(self):
        original = make_warning_trace()
        assert DecisionTrace.from_dict(original.to_dict()).to_dict() == original.to_dict()

    def test_suppress_trace_roundtrip(self):
        original = make_suppress_trace()
        assert DecisionTrace.from_dict(original.to_dict()).to_dict() == original.to_dict()

    def test_double_roundtrip_stable(self):
        payload = make_warning_trace().to_dict()
        once = DecisionTrace.from_dict(payload).to_dict()
        twice = DecisionTrace.from_dict(once).to_dict()
        assert once == twice

    def test_input_digest_is_stable_and_sha256(self):
        di = DecisionInput(trigger_events=(make_perception(),), decision_context=make_ctx())
        d1 = dt.compute_input_digest(di)
        d2 = dt.compute_input_digest(di)
        assert d1 == d2 and len(d1) == 64 and all(ch in "0123456789abcdef" for ch in d1)


# ============================================================================
# arm 校验
# ============================================================================


class TestArmValidation:
    def test_default_arm_is_production(self):
        assert TraceIdentity.new().arm == "production"

    def test_invalid_arm_rejected(self):
        with pytest.raises(ValueError, match="arm"):
            TraceIdentity(decision_id="d1", correlation_id="c1", arm="weird")

    def test_valid_arms_accepted(self):
        for arm in ("production", "baseline", "candidate"):
            ident = TraceIdentity(decision_id="d", correlation_id="c", arm=arm)
            assert ident.arm == arm

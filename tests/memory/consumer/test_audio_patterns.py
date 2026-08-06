"""ADR-0027 Slice D（D6 Consumer audio-aware）测试。

覆盖（G4 关项）：
- ``RuleBasedRetrieval.retrieve_by_modality``：模态过滤（AUDIO / VISION）、负例、
  空请求等价全量、确定性序、ACTIVE + lookback + cap 生效；
- ``RuleBasedAggregation`` 音频模式：``audio_patterns`` 经 ``evidence_resolver``
  解析（metadata audio_kind 优先、回退 kind、仅 AUDIO、未解析跳过、排序去重）；
  ``audio_episode_ratio`` 由 ``modalities`` 统计；resolver=None → 恒空；样本门控；
  解析器异常 → AggregationError（不静默）；
- ``ReasoningInput.modalities`` 提示字段：并集、确定性序、序列化 roundtrip、
  旧数据向后兼容（缺省空）；
- ``RuleBasedReasoningEngine`` 表面化音频模式 / 模态提示（C1 不产分数）；
- 不变量：C1（audio_patterns 是描述不是分数）、C2（只读）、C3（同输入两次一致、
  与输入顺序无关）。

铁律（AGENTS.md 测试有效性）：每个属性断言都配负例（变异可检出）：
- 仅 AUDIO 才进 patterns —— vision item / 未解析 id / 空 label 均被排除；
- audio_kind 优先于 kind —— 若实现改反，本测试立即红；
- 顺序无关 —— 记录逆序输入产出相同 patterns（穷举排列的关键子集）。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.core.event import EvidenceItem, EvidenceModality, RetentionTier
from home_perception.memory.consumer.aggregation import RuleBasedAggregation
from home_perception.memory.consumer.config import RetrievalConfig
from home_perception.memory.consumer.context import RuleBasedContextBuilder
from home_perception.memory.consumer.contracts import (
    CurrentEvent,
    ReasoningInput,
    RiskPattern,
)
from home_perception.memory.consumer.exceptions import AggregationError
from home_perception.memory.consumer.orchestrator import RuleBasedMemoryConsumer
from home_perception.memory.consumer.reasoning import RuleBasedReasoningEngine
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval
from home_perception.memory.records import EpisodicRecord, MemoryStatus
from home_perception.memory.store import InMemoryStore

VISITOR = "visitor-audio"
AUDIO = EvidenceModality.AUDIO
VISION = EvidenceModality.VISION


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# 历史基线：当前事件 2026-07-20 21:00，落在 30d 召回窗内
_H1 = _utc(2026, 7, 18, 10, 0, 0)
_H2 = _utc(2026, 7, 19, 10, 0, 0)


def _make_record(
    rid: str,
    enter: datetime,
    *,
    modalities: list[EvidenceModality] | None = None,
    evidence: list[str] | None = None,
    risk_level: str | None = None,
    reasons: list[str] | None = None,
    memory_status: MemoryStatus = MemoryStatus.ACTIVE,
) -> EpisodicRecord:
    leave = enter + timedelta(minutes=5)
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=VISITOR,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=[f"ev-{rid}"],
        summary=f"visit {rid}",
        model_version="ep-builder-v1",
        risk_level=risk_level,
        reason_summary=reasons or [],
        evidence_refs=evidence or [],
        modalities=modalities or [],
        memory_status=memory_status,
    )


def _audio_item(
    eid: str,
    *,
    kind: str = "audio_segment",
    audio_kind: str | None = None,
    modality: EvidenceModality = AUDIO,
) -> EvidenceItem:
    """构造证据对象；audio_kind 显式传时放入 metadata（模拟 audio_adapter 特征透传）。"""
    metadata = {"audio_kind": audio_kind} if audio_kind is not None else {}
    return EvidenceItem(
        evidence_id=eid,
        modality=modality,
        kind=kind,
        uri=f"file:///{eid}.wav",
        captured_at=_H1,
        confidence=0.9,
        metadata=metadata,
        retention_tier=RetentionTier.SHORT,
    )


def _resolver(*items: EvidenceItem):
    """dict 版只读解析器：未知 id → None（模拟"运行时无证据库"）。"""
    table = {it.evidence_id: it for it in items}
    return lambda eid: table.get(eid)


def _make_event(*, risk_level: str | None = None) -> CurrentEvent:
    return CurrentEvent(
        event_id=f"cur-{VISITOR}",
        event_type="visitor_event",
        visitor_instance_id=VISITOR,
        occurred_at=_utc(2026, 7, 20, 21, 0, 0),
        risk_level=risk_level,
    )


def _store(records: list[EpisodicRecord]) -> InMemoryStore:
    store = InMemoryStore()
    for r in records:
        store.upsert_episodic(r)
    return store


def _consumer(
    records: list[EpisodicRecord],
    *,
    resolver=None,
    retrieval_config: RetrievalConfig | None = None,
) -> RuleBasedMemoryConsumer:
    return RuleBasedMemoryConsumer(
        RuleBasedRetrieval(_store(records), retrieval_config),
        RuleBasedAggregation(evidence_resolver=resolver),
        RuleBasedContextBuilder(),
    )


# ============================================================================
# 1. Retrieval.retrieve_by_modality（D6：按 modalities 过滤）
# ============================================================================


class TestRetrieveByModality:
    def test_filters_to_requested_modality(self) -> None:
        """只召回含 AUDIO 的 episode；纯 VISION episode 被排除（负例）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[VISION]),
            _make_record("ep-2", _H2, modalities=[VISION, AUDIO], evidence=["ev-a1"]),
        ]
        r = RuleBasedRetrieval(_store(records))
        out = r.retrieve_by_modality(_make_event(), AUDIO)
        assert [ep.record_id for ep in out] == ["ep-2"]

    def test_accepts_iterable_of_modalities(self) -> None:
        """可迭代参数：请求 [AUDIO, IDENTITY] 时命中任一即可。"""
        from home_perception.core.event import EvidenceModality as EM

        records = [
            _make_record("ep-1", _H1, modalities=[EM.IDENTITY]),
            _make_record("ep-2", _H2, modalities=[VISION]),
        ]
        r = RuleBasedRetrieval(_store(records))
        out = r.retrieve_by_modality(_make_event(), [EM.AUDIO, EM.IDENTITY])
        assert [ep.record_id for ep in out] == ["ep-1"]

    def test_empty_request_is_equivalent_to_full_retrieve(self) -> None:
        """空可迭代 = 不过滤（与 retrieve 全量等价）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[VISION]),
            _make_record("ep-2", _H2, modalities=[AUDIO]),
        ]
        r = RuleBasedRetrieval(_store(records))
        assert [ep.record_id for ep in r.retrieve_by_modality(_make_event(), [])] == [
            ep.record_id for ep in r.retrieve(_make_event())
        ]

    def test_respects_rank_order_and_cap(self) -> None:
        """排序与裁剪与 retrieve 一致：近的在前，max_records 生效。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO]),
            _make_record("ep-2", _H2, modalities=[AUDIO]),
            _make_record("ep-3", _H2 + timedelta(hours=1), modalities=[AUDIO]),
        ]
        r = RuleBasedRetrieval(_store(records), RetrievalConfig(max_records=2))
        out = r.retrieve_by_modality(_make_event(), AUDIO)
        assert len(out) == 2
        # recency：越近越前（升序用负时间戳），ep-3 最新
        assert out[0].record_id == "ep-3"

    def test_modality_filter_applies_before_cap(self) -> None:
        """负例（变异可检出）：若"先 cap 后过滤"，最老的 AUDIO 记录会被裁剪掉。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO]),  # 较老，不在 top-1
            _make_record("ep-2", _H2, modalities=[VISION]),  # 最新，占满 cap
        ]
        r = RuleBasedRetrieval(_store(records), RetrievalConfig(max_records=1))
        assert [ep.record_id for ep in r.retrieve(_make_event())] == ["ep-2"]
        # 模态过滤须先于 cap：即便 ep-1 最老，也要被"取所有含 AUDIO 的 episode"召回
        assert [ep.record_id for ep in r.retrieve_by_modality(_make_event(), AUDIO)] == [
            "ep-1"
        ]

    def test_excludes_inactive_and_out_of_lookback(self) -> None:
        """ACTIVE + lookback 过滤与 retrieve 同口径（负例：超窗 / 非 ACTIVE 不召回）。"""
        out_of_window = _make_record(
            "ep-old", _utc(2026, 6, 1, 10, 0, 0), modalities=[AUDIO]
        )
        archived = _make_record(
            "ep-arc", _H1, modalities=[AUDIO], memory_status=MemoryStatus.ARCHIVED
        )
        fresh = _make_record("ep-2", _H2, modalities=[AUDIO])
        r = RuleBasedRetrieval(_store([out_of_window, archived, fresh]))
        out = r.retrieve_by_modality(_make_event(), AUDIO)
        assert [ep.record_id for ep in out] == ["ep-2"]

    def test_deterministic_same_input_same_output(self) -> None:
        """C3：同输入两次召回逐字段一致。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO]),
            _make_record("ep-2", _H2, modalities=[AUDIO]),
        ]
        r = RuleBasedRetrieval(_store(records))
        a = r.retrieve_by_modality(_make_event(), AUDIO)
        b = r.retrieve_by_modality(_make_event(), AUDIO)
        assert [ep.to_dict() for ep in a] == [ep.to_dict() for ep in b]


# ============================================================================
# 2. Aggregation 音频模式（D6：audio_patterns / audio_episode_ratio）
# ============================================================================


class TestAggregationAudioPatterns:
    def test_patterns_from_audio_kind_metadata(self) -> None:
        """metadata['audio_kind'] 优先：描述标签（telephone/crying），非 kind 兜底值。"""
        records = [
            _make_record(
                "ep-1", _H1, modalities=[VISION, AUDIO], evidence=["a1", "a2"]
            ),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a3"]),
        ]
        resolver = _resolver(
            _audio_item("a1", kind="audio_clip", audio_kind="telephone"),
            _audio_item("a2", kind="audio_clip", audio_kind="crying"),
            _audio_item("a3", kind="audio_segment", audio_kind="telephone"),
        )
        pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(records)[1]
        assert pattern is not None
        assert pattern.audio_patterns == ("crying", "telephone")  # 排序去重，C3
        assert abs(pattern.audio_episode_ratio - (2 / 2)) < 1e-9

    def test_patterns_fallback_to_kind_when_no_audio_kind(self) -> None:
        """无 metadata['audio_kind'] 时回退到 kind（负例：改反优先级本测试红）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a2"]),
        ]
        resolver = _resolver(
            _audio_item("a1", kind="audio_clip"),
            _audio_item("a2", kind="audio_clip"),
        )
        pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(records)[1]
        assert pattern is not None
        assert pattern.audio_patterns == ("audio_clip",)

    def test_vision_items_and_unresolved_ids_excluded(self) -> None:
        """负例：VISION 证据 / 解析不到（None）的 id 不进 audio_patterns。"""
        records = [
            _make_record(
                "ep-1", _H1, modalities=[VISION, AUDIO], evidence=["a1", "v1", "gone"]
            ),
            _make_record(
                "ep-2", _H2, modalities=[VISION, AUDIO], evidence=["a1", "v1", "gone"]
            ),
        ]
        resolver = _resolver(
            _audio_item("a1", audio_kind="raised"),
            _audio_item("v1", modality=VISION, kind="snapshot"),
        )
        pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(records)[1]
        assert pattern is not None
        assert pattern.audio_patterns == ("raised",)

    def test_resolver_none_yields_empty_patterns_but_ratio_computed(self) -> None:
        """运行时无证据库（resolver=None）：patterns 恒空，ratio 仍由 modalities 算。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO]),
            _make_record("ep-2", _H2, modalities=[VISION]),
        ]
        pattern = RuleBasedAggregation().aggregate(records)[1]
        assert pattern is not None
        assert pattern.audio_patterns == ()
        assert abs(pattern.audio_episode_ratio - 0.5) < 1e-9

    def test_ratio_is_none_when_no_modalities_metadata(self) -> None:
        """旧 v1 记录无 modalities（空列表）→ ratio 为 None（无法计算，不伪造）。"""
        records = [
            _make_record("ep-1", _H1),  # 无 modalities（缺省空列表）
            _make_record("ep-2", _H2),
        ]
        pattern = RuleBasedAggregation().aggregate(records)[1]
        assert pattern is not None
        assert pattern.audio_episode_ratio is None

    def test_pattern_gate_still_applies(self) -> None:
        """样本门控不因音频放宽：n < min_records_for_pattern → 无 RiskPattern。"""
        records = [_make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"])]
        resolver = _resolver(_audio_item("a1", audio_kind="crying"))
        pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(records)[1]
        assert pattern is None

    def test_order_independent_permutations(self) -> None:
        """C3：记录输入顺序不影响 audio_patterns（穷举全排列）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a2"]),
        ]
        resolver = _resolver(
            _audio_item("a1", audio_kind="telephone"),
            _audio_item("a2", audio_kind="crying"),
        )
        outputs = set()
        for perm in itertools.permutations(records):
            pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(
                list(perm)
            )[1]
            assert pattern is not None
            outputs.add(tuple(pattern.audio_patterns))
        assert outputs == {("crying", "telephone")}

    def test_resolver_exception_wrapped_as_aggregation_error(self) -> None:
        """解析器异常 → AggregationError（分层异常，不静默、不向上抛裸异常）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a2"]),
        ]

        def boom(eid: str) -> EvidenceItem | None:
            raise RuntimeError(f"evidence store down: {eid}")

        with pytest.raises(AggregationError):
            RuleBasedAggregation(evidence_resolver=boom).aggregate(records)

    def test_c1_audio_patterns_are_not_scores(self) -> None:
        """C1：RiskPattern 不含任何 score 字段；audio_patterns 只是描述字符串。"""
        import dataclasses

        names = {f.name for f in dataclasses.fields(RiskPattern)}
        forbidden = {"risk_score", "score", "decision", "warning"}
        assert not (names & forbidden)
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a2"]),
        ]
        resolver = _resolver(
            _audio_item("a1", audio_kind="crying"), _audio_item("a2", audio_kind="crying")
        )
        pattern = RuleBasedAggregation(evidence_resolver=resolver).aggregate(records)[1]
        assert pattern is not None
        assert all(isinstance(p, str) for p in pattern.audio_patterns)


# ============================================================================
# 3. ReasoningInput.modalities 提示字段
# ============================================================================


class TestReasoningInputModalities:
    def test_hint_is_union_of_record_modalities(self) -> None:
        """提示字段 = 历史上下文记录的 modalities 并集（确定性枚举值序）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[VISION, AUDIO]),
            _make_record("ep-2", _H2, modalities=[VISION]),
        ]
        out = _consumer(records).consume(_make_event())
        assert set(out.modalities) == {VISION, AUDIO}

    def test_empty_when_records_lack_modalities(self) -> None:
        """旧 v1 记录无 modalities → 提示为空（对齐 D8 向后兼容）。"""
        out = _consumer([_make_record("ep-1", _H1)]).consume(_make_event())
        assert out.modalities == ()

    def test_serialization_roundtrip(self) -> None:
        """to_dict / from_dict 往返保真。"""
        records = [_make_record("ep-1", _H1, modalities=[AUDIO])]
        out = _consumer(records).consume(_make_event())
        restored = ReasoningInput.from_dict(out.to_dict())
        assert restored.to_dict() == out.to_dict()

    def test_from_dict_backward_compatible_without_modalities(self) -> None:
        """旧 payload 无 modalities 键 → 不炸、默认空元组。"""
        records = [_make_record("ep-1", _H1, modalities=[AUDIO])]
        payload = _consumer(records).consume(_make_event()).to_dict()
        del payload["modalities"]
        restored = ReasoningInput.from_dict(payload)
        assert restored.modalities == ()

    def test_risk_pattern_serialization_roundtrip_with_audio(self) -> None:
        """RiskPattern 含 audio_patterns 的序列化往返保真 + 旧数据缺省。"""
        p = RiskPattern(
            tags=("repeated_visit",),
            audio_patterns=("crying", "telephone"),
            audio_episode_ratio=0.5,
        )
        assert RiskPattern.from_dict(p.to_dict()).to_dict() == p.to_dict()
        legacy = {
            "tags": ["repeated_visit"],
            "escalation_history": None,
            "confidence": "weak_pattern",
        }
        legacy_pattern = RiskPattern.from_dict(legacy)
        assert legacy_pattern.audio_patterns == ()
        assert legacy_pattern.audio_episode_ratio is None


# ============================================================================
# 4. Reasoning 表面化（C1 不产分数）
# ============================================================================


class TestReasoningSurfacesAudio:
    def test_audio_patterns_in_findings_with_source_refs(self) -> None:
        """infer 把 audio_patterns 写进 findings，并以 detail='audio_pattern' 溯源。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[AUDIO], evidence=["a2"]),
        ]
        resolver = _resolver(
            _audio_item("a1", audio_kind="crying"),
            _audio_item("a2", audio_kind="telephone"),
        )
        out = _consumer(records, resolver=resolver).consume(_make_event())
        result = RuleBasedReasoningEngine().infer(out)
        joined = " ".join(result.findings)
        assert "crying" in joined and "telephone" in joined
        audio_refs = [s for s in result.source_refs if s.detail == "audio_pattern"]
        assert {s.ref for s in audio_refs} == {"crying", "telephone"}

    def test_audio_modality_hint_surfaced_without_pattern(self) -> None:
        """仅 1 条音频记录（低于模式门控）仍经 modalities 提示暴露 AUDIO 事实。"""
        records = [_make_record("ep-1", _H1, modalities=[AUDIO])]
        out = _consumer(records).consume(_make_event())
        assert out.risk_pattern is None  # 门控生效
        result = RuleBasedReasoningEngine().infer(out)
        assert any("AUDIO" in f for f in result.findings)

    def test_no_score_in_reasoning_result(self) -> None:
        """C1：ReasoningResult 无 score 字段（音频描述不变成分数）。"""
        import dataclasses

        from home_perception.memory.consumer.contracts import ReasoningResult

        names = {f.name for f in dataclasses.fields(ReasoningResult)}
        assert not (names & {"risk_score", "score", "decision", "warning"})


# ============================================================================
# 5. 集成不变量（C2 只读 / C3 确定性）
# ============================================================================


class TestIntegrationInvariants:
    def test_consume_readonly_store_snapshot(self) -> None:
        """C2：consume 前后 store 快照逐字段不变（resolver 只读证据）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[VISION]),
        ]
        store = _store(records)
        resolver = _resolver(_audio_item("a1", audio_kind="crying"))
        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store),
            RuleBasedAggregation(evidence_resolver=resolver),
            RuleBasedContextBuilder(),
        )
        before = store.snapshot()["episodic"]
        consumer.consume(_make_event())
        assert store.snapshot()["episodic"] == before

    def test_deterministic_same_input_twice(self) -> None:
        """C3：同输入两次 consume 逐字段一致（含新增字段）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1"]),
            _make_record("ep-2", _H2, modalities=[VISION, AUDIO], evidence=["a2"]),
        ]
        resolver = _resolver(
            _audio_item("a1", audio_kind="telephone"),
            _audio_item("a2", kind="audio_clip"),
        )
        a = _consumer(records, resolver=resolver).consume(_make_event()).to_dict()
        b = _consumer(records, resolver=resolver).consume(_make_event()).to_dict()
        assert a == b

    def test_audio_evidence_refs_reachable_in_reasoning_input(self) -> None:
        """D6：音频证据以 evidence_id 出现在 evidence_refs（下游可经 id 解析消费）。"""
        records = [
            _make_record("ep-1", _H1, modalities=[AUDIO], evidence=["a1", "a2"]),
            _make_record("ep-2", _H2, modalities=[VISION]),
        ]
        out = _consumer(records).consume(_make_event())
        assert "a1" in out.evidence_refs
        assert "a2" in out.evidence_refs

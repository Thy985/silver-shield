"""C-5 Memory Consumer 不变量全量 + replay 一致性 + 跨层调用禁令（ADR-0025 §3.6–§3.9）。

本文件与 ``test_orchestrator.py``（C-4 编排器单元）互补，聚焦 C-5 DoD 明确要求的
**两类新增断言**与**契约/集成层不变量**：

1. **跨层调用禁令**（DoD 核心）：monkeypatch 验证单向管道的直接方法调用边界——
   ``Aggregation.aggregate`` 执行期间不得调用 ``Retrieval.retrieve``；
   ``ContextBuilder.build`` 执行期间不得调用 ``Retrieval`` / ``Aggregation``；
   ``MemoryConsumer.consume`` 严格各调一次（编排器唯一允许跨组件调用者）。
   这是把"组件互不调用"从注释提升为可执行铁律（可变异验证：若有人在聚合里加一次
   retrieve，断言立即红）。

2. **Replay 一致性**（DESIGN §6）：用 ``tests/fixtures/memory_replay`` 的 case
   （case_001 重复访客 / case_002 行为升级 / case_003 冲突透明）做回放，断言
   **同输入同输出**（C3 确定性）+ 每个 case 证明其"Memory 改变了理解"的关键信号。

3. **契约/集成层不变量补强**：
   - C1 结构性白名单：``ReasoningInput`` dataclass 字段集本身不含 score/decision/warning；
   - C2 只读：consume 前后 ``InMemoryStore.snapshot()["episodic"]`` 逐字段不变，且
     consume 不调用 store 写方法（upsert_episodic 不被触发）；
   - C4 透明：冲突**只标记不解决**——historical 与 current 两侧并存、不覆盖；
   - C5 可追溯：historical_context 每条记录保留非空的 ``source_event_ids``。

构造 ``EpisodicRecord`` 用固定 ``source_event_ids``（回放铁律：event_id 默认随机 UUID4，
测试须显式传固定值）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from home_perception.memory.consumer import aggregation as agg_mod
from home_perception.memory.consumer import context as ctx_mod
from home_perception.memory.consumer import retrieval as retrieval_mod
from home_perception.memory.consumer.aggregation import RuleBasedAggregation
from home_perception.memory.consumer.context import RuleBasedContextBuilder
from home_perception.memory.consumer.contracts import CurrentEvent, ReasoningInput
from home_perception.memory.consumer.orchestrator import RuleBasedMemoryConsumer
from home_perception.memory.consumer.replay_dataset import MemoryReplayDataset
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval
from home_perception.memory.records import ActionSummary, EpisodicRecord
from home_perception.memory.store import InMemoryStore

VISITOR = "visitor-c5"
REPLAY_ROOT = str(Path(__file__).resolve().parent.parent.parent / "fixtures" / "memory_replay")


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
    evidence: list[str] | None = None,
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


def _store_consumer(records: list[EpisodicRecord]) -> RuleBasedMemoryConsumer:
    """真实 store + 默认三组件（尽量少 mock，贴近集成）。"""
    store = InMemoryStore()
    for r in records:
        store.upsert_episodic(r)
    return RuleBasedMemoryConsumer(
        RuleBasedRetrieval(store),
        RuleBasedAggregation(),
        RuleBasedContextBuilder(),
    )


_H1 = _utc(2026, 7, 18, 10, 0, 0)
_H2 = _utc(2026, 7, 19, 10, 0, 0)


# ============================================================================
# C1 结构性白名单（契约层，独立于编排器行为）
# ============================================================================


class TestContractC1NoScoreField:
    def test_reasoning_input_has_no_decision_fields(self):
        """C1（契约层）：``ReasoningInput`` dataclass 字段集本身不含 score/decision/warning。

        与 test_orchestrator 的行为级 C1 互补：即便未来有人误加字段，本断言直接红——
        把"Consumer 不决策"从运行期产物提升为数据结构铁律（ADR-0025 §3.9）。
        """
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ReasoningInput)}
        forbidden = {"risk_score", "score", "decision", "warning", "recommended_action"}
        assert not (field_names & forbidden), (
            f"ReasoningInput 含禁止字段: {field_names & forbidden}"
        )
        # 字段集正是契约声明的 7 个，无漂移
        assert field_names == {
            "current_event",
            "historical_context",
            "visitor_profile",
            "risk_pattern",
            "evidence_refs",
            "previous_actions",
            "conflicts",
        }


# ============================================================================
# C2 只读（集成层：store 快照 + 写方法不被调用）
# ============================================================================


class TestC2StoreReadOnly:
    def test_store_snapshot_unchanged_after_consume(self):
        """C2：consume 前后 ``InMemoryStore.snapshot()['episodic']`` 逐字段不变（DESIGN §6）。"""
        records = [
            _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"]),
            _make_record("ep-2", _H2, risk_level="MEDIUM", reasons=["behavior:loiter"]),
        ]
        consumer = _store_consumer(records)
        before = consumer._retrieval._store.snapshot()["episodic"]  # type: ignore[attr-defined]
        consumer.consume(_make_event(risk_level="HIGH", markers=("night", "observe_camera")))
        after = consumer._retrieval._store.snapshot()["episodic"]  # type: ignore[attr-defined]
        assert before == after

    def test_consume_never_calls_store_write(self, monkeypatch):
        """C2 + 跨层：consume 不得触发 store 写方法（upsert_episodic）——只读铁律。

        把 store 的写方法替换为计数器；若编排器或任一组件偷偷写入，计数 > 0 → 红。
        """
        store = InMemoryStore()
        store.upsert_episodic(_make_record("ep-1", _H1))
        writes = {"n": 0}

        def counting_upsert(self, record):
            writes["n"] += 1
            return True

        monkeypatch.setattr(InMemoryStore, "upsert_episodic", counting_upsert)
        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        consumer.consume(_make_event(risk_level="HIGH", markers=("night",)))
        assert writes["n"] == 0


# ============================================================================
# C3 确定性（跨组件顺序无关 + replay 确定性）
# ============================================================================


class TestC3Determinism:
    def test_output_independent_of_component_bound_order(self):
        """C3（跨组件）：同一批记录分别经不同 store 实例 / 不同编排器实例，产物逐字段一致。

        排除"编排器缓存了上次 store 句柄 / 上次 records"导致的顺序依赖。
        """
        records = [
            _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"]),
            _make_record("ep-2", _H2, risk_level="MEDIUM", reasons=["behavior:loiter"]),
        ]
        event = _make_event(risk_level="HIGH", markers=("night", "observe_camera"))
        a = _store_consumer(records).consume(event).to_dict()
        b = _store_consumer(records).consume(event).to_dict()
        assert a == b


# ============================================================================
# C4 透明（只标记不解决）
# ============================================================================


class TestC4MarkOnly:
    def test_conflicts_preserve_both_sides_and_never_overwrite(self):
        """C4：冲突同时保留 historical 与 current 两侧（不取舍、不覆盖、不解决）。

        与 test_orchestrator 的逐类型用例互补：此处验证"多冲突并存"时每一侧都完整，
        且编排器未把 high 风险事件'消化'成普通记录（即冲突不参与任何决策）。
        """
        records = [
            _make_record("ep-1", _H1, risk_level="LOW", reasons=["behavior:daytime_visit"]),
            _make_record("ep-2", _H2, risk_level="LOW", reasons=["behavior:night"]),
        ]
        out = _store_consumer(records).consume(
            _make_event(risk_level="HIGH", markers=("night", "observe_camera"))
        )
        # risk_escalation + 2x behavior_shift（night 历史见过不标，observe_camera 新标）
        assert len(out.conflicts) >= 2
        for flag in out.conflicts:
            assert flag.historical and flag.current
            assert flag.historical != flag.current  # 两侧并存
        # 当前事件的事实未被冲突改写（C4 不解决）
        assert out.current_event.risk_level == "HIGH"

    def test_cold_start_no_fabricated_conflict(self):
        """C4 冷启动：无历史 = 无冲突，首次来访不得被标记（伪冲突防线，重复验证）。"""
        out = _store_consumer([]).consume(_make_event(risk_level="HIGH", markers=("night",)))
        assert out.conflicts == ()


# ============================================================================
# C5 可追溯（source_event_ids 透传）
# ============================================================================


class TestC5Traceability:
    def test_source_event_ids_preserved_in_historical_context(self):
        """C5：historical_context 每条记录保留非空 source_event_ids（不丢弃、不篡改）。

        ADR-0024 I4 要求 EpisodicRecord 携带 source_event_ids；Consumer 侧只透传，
        不得丢失（否则 Reasoning 无法溯源）。
        """
        records = [
            _make_record("ep-1", _H1, reasons=["behavior:daytime_visit"]),
            _make_record("ep-2", _H2, reasons=["behavior:loiter"]),
        ]
        out = _store_consumer(records).consume(_make_event())
        assert len(out.historical_context) == 2
        for ep in out.historical_context:
            assert ep.source_event_ids, "historical_context 记录丢失 source_event_ids"
        # 透传顺序与输入一致（ContextBuilder 按 (enter_time, record_id) 排序）
        assert [ep.record_id for ep in out.historical_context] == ["ep-1", "ep-2"]


# ============================================================================
# 跨层调用禁令（C-5 DoD 核心，monkeypatch）
# ============================================================================


class TestCrossLayerCallBan:
    """单向管道硬边界：组件互不调用，仅编排器 ``consume`` 跨组件驱动。

    可变异验证：若有人在 ``RuleBasedAggregation.aggregate`` 内加一行
    ``self._retrieval.retrieve(...)``，``test_aggregation_never_calls_retrieval`` 立即红。
    """

    def test_aggregation_never_calls_retrieval(self, monkeypatch):
        store = InMemoryStore()
        store.upsert_episodic(_make_record("ep-1", _H1))
        counts = {"retrieve": 0}
        orig_retrieve = retrieval_mod.RuleBasedRetrieval.retrieve

        def counting_retrieve(self, ce):
            counts["retrieve"] += 1
            return orig_retrieve(self, ce)

        monkeypatch.setattr(retrieval_mod.RuleBasedRetrieval, "retrieve", counting_retrieve)

        orig_aggregate = agg_mod.RuleBasedAggregation.aggregate

        def watching_aggregate(self, records):
            before = counts["retrieve"]
            result = orig_aggregate(self, records)
            # 聚合执行期间不得触发额外的 Retrieval 调用（仅编排器那一次应已计入）
            assert counts["retrieve"] == before, "Aggregation 不应在内部调用 Retrieval"
            return result

        monkeypatch.setattr(agg_mod.RuleBasedAggregation, "aggregate", watching_aggregate)

        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        consumer.consume(_make_event())
        # 仅编排器调用一次 retrieve；聚合未私自追加
        assert counts["retrieve"] == 1

    def test_context_builder_never_calls_retrieval_or_aggregation(self, monkeypatch):
        store = InMemoryStore()
        store.upsert_episodic(_make_record("ep-1", _H1))
        counts = {"retrieve": 0, "aggregate": 0}
        orig_retrieve = retrieval_mod.RuleBasedRetrieval.retrieve
        orig_aggregate = agg_mod.RuleBasedAggregation.aggregate

        def counting_retrieve(self, ce):
            counts["retrieve"] += 1
            return orig_retrieve(self, ce)

        def counting_aggregate(self, records):
            counts["aggregate"] += 1
            return orig_aggregate(self, records)

        monkeypatch.setattr(retrieval_mod.RuleBasedRetrieval, "retrieve", counting_retrieve)
        monkeypatch.setattr(agg_mod.RuleBasedAggregation, "aggregate", counting_aggregate)

        orig_build = ctx_mod.RuleBasedContextBuilder.build

        def watching_build(self, *args, **kwargs):
            r0, a0 = counts["retrieve"], counts["aggregate"]
            result = orig_build(self, *args, **kwargs)
            assert counts["retrieve"] == r0, "ContextBuilder 不应调用 Retrieval"
            assert counts["aggregate"] == a0, "ContextBuilder 不应调用 Aggregation"
            return result

        monkeypatch.setattr(ctx_mod.RuleBasedContextBuilder, "build", watching_build)

        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        consumer.consume(_make_event())
        # 编排器严格各调一次，组件内部零追加
        assert counts["retrieve"] == 1
        assert counts["aggregate"] == 1

    def test_orchestrator_drives_each_component_exactly_once(self, monkeypatch):
        """正向控制：编排器是唯一跨组件调用者，且每个组件恰好一次（顺序 retrieve→aggregate→build）。"""
        store = InMemoryStore()
        store.upsert_episodic(_make_record("ep-1", _H1))
        trace: list[str] = []
        orig_retrieve = retrieval_mod.RuleBasedRetrieval.retrieve
        orig_aggregate = agg_mod.RuleBasedAggregation.aggregate
        orig_build = ctx_mod.RuleBasedContextBuilder.build

        def tracing_retrieve(self, ce):
            trace.append("retrieve")
            return orig_retrieve(self, ce)

        def tracing_aggregate(self, records):
            trace.append("aggregate")
            return orig_aggregate(self, records)

        def tracing_build(self, *args, **kwargs):
            trace.append("build")
            return orig_build(self, *args, **kwargs)

        monkeypatch.setattr(retrieval_mod.RuleBasedRetrieval, "retrieve", tracing_retrieve)
        monkeypatch.setattr(agg_mod.RuleBasedAggregation, "aggregate", tracing_aggregate)
        monkeypatch.setattr(ctx_mod.RuleBasedContextBuilder, "build", tracing_build)

        consumer = RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )
        consumer.consume(_make_event())
        assert trace == ["retrieve", "aggregate", "build"]


# ============================================================================
# Replay 一致性（DESIGN §6，M0 数据集）
# ============================================================================


class TestReplayConsistency:
    """用真实回放 case 证明「Memory 改变了理解」（而非只跑通 Consumer 代码）。

    注意：本测试**不**与 M0 的 ``expected_reasoning_input.json`` 做字节级比对——
    那是 ``ProvisionalContextAssembler``（M0 临时组装器）的产物，其冲突逻辑（仅
    behavior_shift）与正式 ``RuleBasedMemoryConsumer``（额外含 risk_escalation）不同，
    属预期差异（生产更严格）。C-5 关注的是「同输入同输出 + 每个 case 证明的 Memory 价值」。
    """

    @staticmethod
    def _consumer_for(case) -> RuleBasedMemoryConsumer:
        store = InMemoryStore()
        for ep in case.history:
            store.upsert_episodic(ep)
        return RuleBasedMemoryConsumer(
            RuleBasedRetrieval(store), RuleBasedAggregation(), RuleBasedContextBuilder()
        )

    def test_dataset_present(self):
        names = MemoryReplayDataset(REPLAY_ROOT).case_names()
        assert {
            "case_001_repeat_visitor",
            "case_002_behavior_escalation",
            "case_003_conflict_transparency",
        }.issubset(set(names))

    def test_all_cases_deterministic_same_input_same_output(self):
        """Replay 一致性核心：每个 case 同输入两次 consume，产物逐字段相等（C3）。"""
        ds = MemoryReplayDataset(REPLAY_ROOT)
        for name in ds.case_names():
            case = ds.load(name)
            consumer = self._consumer_for(case)
            out1 = consumer.consume(case.current_event)
            out2 = consumer.consume(case.current_event)
            assert out1.to_dict() == out2.to_dict(), f"case {name} 非确定性"

    def test_case_001_repeat_visitor_profile(self):
        """孤立事件 → 关联画像（重复夜间访客）。"""
        case = MemoryReplayDataset(REPLAY_ROOT).load("case_001_repeat_visitor")
        out = self._consumer_for(case).consume(case.current_event)
        assert out.visitor_profile is not None
        assert out.visitor_profile.visit_count == 5
        assert out.visitor_profile.night_visit_ratio == 1.0

    def test_case_002_behavior_escalation_pattern(self):
        """单看当前得不到的行为升级模式被聚合出来（escalating_behavior）。"""
        case = MemoryReplayDataset(REPLAY_ROOT).load("case_002_behavior_escalation")
        out = self._consumer_for(case).consume(case.current_event)
        assert out.risk_pattern is not None
        assert "escalating_behavior" in out.risk_pattern.tags

    def test_case_003_conflict_transparency(self):
        """历史正常 vs 当前异常 → 冲突透明（C4），并存新旧两侧。"""
        case = MemoryReplayDataset(REPLAY_ROOT).load("case_003_conflict_transparency")
        out = self._consumer_for(case).consume(case.current_event)
        assert len(out.conflicts) >= 1
        assert any(c.type == "behavior_shift" for c in out.conflicts)
        for c in out.conflicts:
            assert c.historical and c.current

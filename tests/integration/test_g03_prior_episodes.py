"""G0-3 历史记忆预置 · 契约测试（prior_episodes → Memory Runtime → 决策检索引用）。

覆盖验收（docs/DESIGN-golden-scenario-set.md §4）：
- scenario `prior_episodes` 声明解析（跨日历史的时间真相源）；
- runner 预置：prior_episodes → MemoryStore（EpisodicRecord，record_id=ep-prior-*）；
- 决策检索引用：DecisionEngine(memory_store=...) + memory_aware policy →
  reason_summary 含历史引用 + trace.historical_record_ids 填充（"决策引用了历史 Episode"可证）；
- 默认零行为变化：memory_aware=False（默认）→ 决策不含历史升级、trace 历史引用为空。

不依赖 cv2 / torch（纯契约测试）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore


def _episode(record_id: str, visitor: str, event_time: datetime) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=record_id,
        visitor_instance_id=visitor,
        enter_time=event_time,
        leave_time=event_time + timedelta(seconds=30),
        duration_seconds=30.0,
        source_event_ids=[f"prior:{record_id}"],
        summary=f"历史访问 {record_id}",
        model_version="test",
        reason_summary=["abnormal_dwell"],
        risk_level="LOW",
        recommended_action="MONITOR",
        device_id="home_entry",
    )


# ---------------------------------------------------------------------------
# 1. scenario prior_episodes 声明解析
# ---------------------------------------------------------------------------


def test_scenario_parses_prior_episodes(tmp_path: Path):
    """yaml 顶层 `prior_episodes` → Scenario 解析（跨日时间真相源）。"""
    from home_perception.validation.scenario.scenario import (
        Scenario,
        load_scenario,
    )

    yaml_text = """
meta:
  schema_version: "1.0"
  scenario_id: sw_golden_repeated_visit
  version: 1
  description: "G0-3 repeated_visit prior episodes"
mode: detections
camera:
  resolution: [384, 288]
  fps: 2.0
prior_episodes:
  - episode_id: historical_001
    event_time: 1752600000.0
    visitor_id: V-017
    duration_seconds: 60.0
    risk_level: LOW
    recommended_action: MONITOR
    summary: "3 days ago visit"
  - episode_id: historical_002
    event_time: 1752772800.0
    visitor_id: V-017
    duration_seconds: 60.0
    summary: "yesterday visit"
"""
    p = tmp_path / "golden_repeated_visit.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    scn = load_scenario(p)
    assert len(scn.prior_episodes) == 2
    pe1, pe2 = scn.prior_episodes
    assert pe1.episode_id == "historical_001"
    assert pe1.visitor_id == "V-017"
    assert pe1.event_time == 1752600000.0
    assert pe2.episode_id == "historical_002"
    # pe2 声明了 duration_seconds: 60.0
    assert pe2.duration_seconds == 60.0
    assert pe2.risk_level == "LOW"
    assert isinstance(scn, Scenario)


def test_scenario_default_no_prior_episodes():
    """未声明 prior_episodes → 空列表（向后兼容，现有场景零变化）。"""
    from home_perception.validation.scenario.scenario import Scenario

    scn = Scenario(
        meta={"schema_version": "1.0", "scenario_id": "s", "version": 1},
        camera={"resolution": [384, 288], "fps": 2.0},
    )
    assert scn.prior_episodes == []


# ---------------------------------------------------------------------------
# 2. runner 预置（prior_episodes → MemoryStore）
# ---------------------------------------------------------------------------


def test_runner_seeds_prior_episodes():
    """_seed_prior_episodes：预置进 MemoryStore → get_episodic_by_visitor 命中。"""
    from home_perception.integration.loop.context import IntegrationContext
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.validation.scenario.scenario import Scenario

    scn = Scenario(
        meta={"schema_version": "1.0", "scenario_id": "sw_g", "version": 1},
        camera={"resolution": [384, 288], "fps": 2.0},
        prior_episodes=[
            {
                "episode_id": "historical_001",
                "event_time": 1752600000.0,
                "visitor_id": "V-017",
                "summary": "3 days ago",
            },
            {
                "episode_id": "historical_002",
                "event_time": 1752772800.0,
                "visitor_id": "V-017",
                "summary": "yesterday",
            },
        ],
    )
    ctx = IntegrationContext.build()
    IntegrationRunner()._seed_prior_episodes(ctx, scn)
    records = ctx.memory_store.get_episodic_by_visitor("V-017")
    assert len(records) == 2
    ids = sorted(r.record_id for r in records)
    assert ids == ["ep-prior-historical_001", "ep-prior-historical_002"]
    # 时间真相源：enter_time 由 event_time 推导（跨日）
    times = sorted(r.enter_time.timestamp() for r in records)
    assert times == [1752600000.0, 1752772800.0]


def test_runner_seed_is_idempotent():
    """幂等：重复预置同一 prior → 记录数不变（I1 record_id 幂等）。"""
    from home_perception.integration.loop.context import IntegrationContext
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.validation.scenario.scenario import Scenario

    scn = Scenario(
        meta={"schema_version": "1.0", "scenario_id": "sw_g", "version": 1},
        camera={"resolution": [384, 288], "fps": 2.0},
        prior_episodes=[
            {
                "episode_id": "historical_001",
                "event_time": 1752600000.0,
                "visitor_id": "V-017",
                "summary": "3 days ago",
            }
        ],
    )
    ctx = IntegrationContext.build()
    runner = IntegrationRunner()
    runner._seed_prior_episodes(ctx, scn)
    runner._seed_prior_episodes(ctx, scn)
    assert len(ctx.memory_store.get_episodic_by_visitor("V-017")) == 1


# ---------------------------------------------------------------------------
# 3. 决策检索引用（memory_aware 升级 + trace.historical_record_ids）
# ---------------------------------------------------------------------------


def _engine(memory_aware: bool, store: InMemoryStore | None):
    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy

    return DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(memory_aware=memory_aware),
        memory_store=store,
    )


def _perception_event(event_type: str = "abnormal_dwell"):
    from home_perception.analysis.perception import PerceptionEvent

    return PerceptionEvent(
        event_type=event_type,
        visitor_id="aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001",
        timestamp=1752952800.0,
        score=0.7,
        device_id="home_entry",
        source_video="test.mp4",
        meta={"rule": "TestRule"},
    )


def test_memory_aware_escalation_with_history():
    """memory_aware + 2 条历史 → 升级 MEDIUM + reason 引用历史（决策引用了历史 Episode）。"""
    store = InMemoryStore()
    t0 = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)
    store.upsert_episodic(_episode("ep-prior-historical_001", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0))
    store.upsert_episodic(_episode("ep-prior-historical_002", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0 + timedelta(days=1)))

    engine = _engine(memory_aware=True, store=store)
    warning = engine.evaluate([_perception_event()])  # 本次 abnormal_dwell
    assert warning is not None
    assert warning.risk_level == "MEDIUM"  # LOW → MEDIUM（历史模式确认）
    assert any("历史 2 次类似访问" in r for r in warning.reason_summary)
    assert any("ep-prior-historical_001" in r for r in warning.reason_summary)
    assert warning.meta.get("memory_aware") is True


def test_memory_aware_monitor_upgrades_to_notify():
    """memory_aware：MONITOR（visit_pending_verify）→ NOTIFY_FAMILY（历史确认）。"""
    store = InMemoryStore()
    t0 = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)
    store.upsert_episodic(_episode("ep-prior-historical_001", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0))
    store.upsert_episodic(_episode("ep-prior-historical_002", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0 + timedelta(days=1)))

    engine = _engine(memory_aware=True, store=store)
    warning = engine.evaluate([_perception_event("visit_pending_verify")])
    assert warning is not None
    assert warning.recommended_action == "NOTIFY_FAMILY"  # MONITOR → NOTIFY_FAMILY


def test_default_zero_behavior_change():
    """默认（memory_aware=False）：历史存在也不升级、不引用（ADR-0030 D2 零行为变化）。"""
    store = InMemoryStore()
    t0 = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)
    store.upsert_episodic(_episode("ep-prior-historical_001", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0))
    store.upsert_episodic(_episode("ep-prior-historical_002", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0 + timedelta(days=1)))

    engine = _engine(memory_aware=False, store=store)
    warning = engine.evaluate([_perception_event()])
    assert warning is not None
    assert warning.risk_level == "LOW"  # 不变
    assert not any("历史" in r for r in warning.reason_summary)


def test_memory_aware_without_store_no_change():
    """memory_aware=True 但 memory_store 缺席（Memory 可缺席）→ 纯感知，不升级。"""
    engine = _engine(memory_aware=True, store=None)
    warning = engine.evaluate([_perception_event()])
    assert warning is not None
    assert warning.risk_level == "LOW"
    assert not any("历史" in r for r in warning.reason_summary)


def test_build_warning_trace_historical_record_ids():
    """trace.provenance.memory_refs.historical_record_ids 填充（Decision Trace 引用可证）。"""
    from home_perception.analysis.decision_contract import DecisionContext, DecisionInput
    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
    from home_perception.analysis.decision_trace import build_warning_trace

    store = InMemoryStore()
    t0 = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)
    store.upsert_episodic(_episode("ep-prior-historical_001", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0))
    store.upsert_episodic(_episode("ep-prior-historical_002", "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001", t0 + timedelta(days=1)))

    engine = DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(memory_aware=True),
        memory_store=store,
    )
    warning = engine.evaluate([_perception_event()])
    assert warning is not None

    ri = engine._build_reasoning_input([_perception_event()])
    assert ri is not None
    assert len(ri.historical_context) == 2
    dinput = DecisionInput(
        trigger_events=(_perception_event(),),
        decision_context=DecisionContext(elder_id="elder_001", now=datetime.now(UTC)),
        reasoning_input=ri,
    )
    trace = build_warning_trace(dinput, warning, "RuleBasedDecisionPolicy", {})
    refs = trace.provenance.memory_refs
    assert refs.reasoning_input_present is True
    assert set(refs.historical_record_ids) == {
        "ep-prior-historical_001",
        "ep-prior-historical_002",
    }

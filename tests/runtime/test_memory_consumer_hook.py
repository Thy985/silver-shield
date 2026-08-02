"""C-4 MemoryConsumerHook 单测（ADR-0025 §3.10 修订 / DESIGN §4.1）。

torch-free。验证运行期接线点的三件事——**门控、隔离、计量**：

- 模式 B 门控：``risk_level ∈ enabled_levels`` **或** 已知访客再现；两条件为"或"，
  各自的正反例都覆盖（关掉任一条件时对应用例必须翻转 → 可变异验证）；
- 非阻塞隔离：Consumer 抛任何异常都只计数 + 记日志，绝不上抛（主链路不受影响）；
- 独立计量：``ConsumerMetrics`` 自成一套，``evaluated - triggered`` 即被门控挡下量；
- C2 只读：Hook 不写 Memory。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from home_perception.memory.consumer.config import ConsumerTriggerConfig
from home_perception.memory.consumer.contracts import CurrentEvent, ReasoningInput
from home_perception.memory.consumer.exceptions import ConsumerError, RetrievalError
from home_perception.memory.consumer.interfaces import MemoryConsumer
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore
from home_perception.runtime.memory_consumer_hook import ConsumerMetrics, MemoryConsumerHook

KNOWN = "visitor-known"
UNKNOWN = "visitor-unknown"

# 与 ``memory_consumer_hook.CONSUMER_LOG_FIELDS`` 同源：断言字段集合一致，
# 新增消费者指标时只改实现常量一处即可，测试自动跟随（避免字段/断言漂移）。
CONSUMER_LOG_FIELDS = (
    "consumer_evaluated",
    "consumer_triggered",
    "consumer_produced",
    "consumer_errors",
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _event(vid: str, risk_level: str | None = None) -> CurrentEvent:
    return CurrentEvent(
        event_id=f"cur-{vid}-{risk_level}",
        event_type="visitor_event",
        visitor_instance_id=vid,
        occurred_at=_utc(2026, 7, 20, 21, 0, 0),
        risk_level=risk_level,
    )


def _record(rid: str, vid: str) -> EpisodicRecord:
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
    )


class _StubConsumer(MemoryConsumer):
    """记录调用次数的最小 Consumer；可配置为抛异常。"""

    def __init__(self, raises: Exception | None = None):
        self.calls: list[CurrentEvent] = []
        self._raises = raises

    def consume(self, current_event: CurrentEvent) -> ReasoningInput:
        self.calls.append(current_event)
        if self._raises is not None:
            raise self._raises
        return ReasoningInput(
            current_event=current_event,
            historical_context=(),
            visitor_profile=None,
            risk_pattern=None,
        )


def _store_with_history() -> InMemoryStore:
    store = InMemoryStore()
    store.upsert_episodic(_record("ep-1", KNOWN))
    return store


def _hook(
    consumer: MemoryConsumer | None = None,
    *,
    enabled: bool = True,
    store: InMemoryStore | None = None,
    config: ConsumerTriggerConfig | None = None,
) -> MemoryConsumerHook:
    return MemoryConsumerHook(
        consumer if consumer is not None else _StubConsumer(),
        store if store is not None else _store_with_history(),
        enabled,
        config=config,
    )


# ============================================================================
# 1. ConsumerTriggerConfig 契约
# ============================================================================


class TestTriggerConfig:
    def test_defaults(self):
        c = ConsumerTriggerConfig()
        assert c.enabled_levels == ("MEDIUM", "HIGH")
        assert c.trigger_on_known_visitor is True

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError, match="enabled_levels"):
            ConsumerTriggerConfig(enabled_levels=("CRITICAL",))

    def test_rejects_all_triggers_off(self):
        """两条件同时关闭 = 永不触发，应改用 consumer_enabled=false，而非静默空转。"""
        with pytest.raises(ValueError, match="至少需保留一种触发条件"):
            ConsumerTriggerConfig(enabled_levels=(), trigger_on_known_visitor=False)


# ============================================================================
# 2. 模式 B 门控
# ============================================================================


class TestTriggerGating:
    @pytest.mark.parametrize("level", ["MEDIUM", "HIGH"])
    def test_medium_and_high_trigger_even_for_unknown_visitor(self, level):
        """首次即高危：无历史也必须理解。"""
        consumer = _StubConsumer()
        hook = _hook(consumer, store=InMemoryStore())
        assert hook.maybe_consume(_event(UNKNOWN, level)) is not None
        assert len(consumer.calls) == 1
        assert hook.metrics.consumer_triggered == 1

    @pytest.mark.parametrize("level", [None, "LOW"])
    def test_low_or_none_with_unknown_visitor_not_triggered(self, level):
        """低风险 + 无历史 = 不触发（门控的下界，若误放宽本例翻转）。"""
        consumer = _StubConsumer()
        hook = _hook(consumer, store=InMemoryStore())
        assert hook.maybe_consume(_event(UNKNOWN, level)) is None
        assert consumer.calls == []
        assert hook.metrics.consumer_evaluated == 1
        assert hook.metrics.consumer_triggered == 0

    def test_known_visitor_triggers_at_low_risk(self):
        """已知访客再现即触发——这是 §3.10 修订的核心（避免沦为事后解释系统）。"""
        consumer = _StubConsumer()
        hook = _hook(consumer, store=_store_with_history())
        assert hook.maybe_consume(_event(KNOWN, "LOW")) is not None
        assert len(consumer.calls) == 1

    def test_known_visitor_not_triggered_when_flag_off(self):
        """关掉 trigger_on_known_visitor 后同一事件不再触发（配置生效的反例）。"""
        consumer = _StubConsumer()
        hook = _hook(
            consumer,
            store=_store_with_history(),
            config=ConsumerTriggerConfig(trigger_on_known_visitor=False),
        )
        assert hook.maybe_consume(_event(KNOWN, "LOW")) is None
        assert consumer.calls == []

    def test_enabled_levels_can_be_tightened(self):
        """收紧为仅 HIGH 后，MEDIUM 不再触发（灰度收紧路径可用）。"""
        consumer = _StubConsumer()
        hook = _hook(
            consumer,
            store=InMemoryStore(),
            config=ConsumerTriggerConfig(enabled_levels=("HIGH",)),
        )
        assert hook.maybe_consume(_event(UNKNOWN, "MEDIUM")) is None
        assert hook.maybe_consume(_event(UNKNOWN, "HIGH")) is not None

    def test_missing_store_degrades_to_level_only(self):
        """无 store 时"已知访客"条件恒假，但等级条件仍工作（降级不崩）。"""
        consumer = _StubConsumer()
        hook = MemoryConsumerHook(consumer, None, True)
        assert hook.maybe_consume(_event(KNOWN, "LOW")) is None
        assert hook.maybe_consume(_event(KNOWN, "HIGH")) is not None


# ============================================================================
# 3. 开关与零开销
# ============================================================================


class TestEnabledSwitch:
    def test_disabled_returns_none_without_evaluating(self):
        """关闭时连门控都不进：evaluated 保持 0（零开销的可观测证据）。"""
        consumer = _StubConsumer()
        hook = _hook(consumer, enabled=False)
        assert hook.enabled is False
        assert hook.maybe_consume(_event(KNOWN, "HIGH")) is None
        assert consumer.calls == []
        assert hook.metrics.as_log_fields() == {name: 0 for name in CONSUMER_LOG_FIELDS}

    def test_enabled_but_consumer_missing_is_noop(self):
        hook = MemoryConsumerHook(None, _store_with_history(), True)
        assert hook.maybe_consume(_event(KNOWN, "HIGH")) is None
        assert hook.metrics.consumer_evaluated == 0


# ============================================================================
# 4. 非阻塞隔离
# ============================================================================


class TestNonBlocking:
    @pytest.mark.parametrize(
        "exc",
        [ConsumerError("boom"), RetrievalError("boom"), RuntimeError("driver down")],
    )
    def test_any_failure_is_swallowed_and_counted(self, exc):
        """任何异常都不上抛：主链路（process_frame）不因记忆消费失败而中断。"""
        hook = _hook(_StubConsumer(raises=exc))
        assert hook.maybe_consume(_event(KNOWN, "HIGH")) is None
        assert hook.metrics.consumer_errors == 1
        assert hook.metrics.consumer_triggered == 1
        assert hook.metrics.consumer_produced == 0

    def test_produced_only_counts_success(self):
        hook = _hook(_StubConsumer())
        hook.maybe_consume(_event(KNOWN, "HIGH"))
        hook.maybe_consume(_event(UNKNOWN, "LOW"))  # 未触发
        assert hook.metrics.as_log_fields() == {
            name: {"consumer_evaluated": 2, "consumer_triggered": 1,
                   "consumer_produced": 1, "consumer_errors": 0}[name]
            for name in CONSUMER_LOG_FIELDS
        }

    def test_metrics_instance_is_injectable_and_shared(self):
        """指标可注入（便于 runtime 汇总），但默认自带一份，不并入 PipelineMetrics。"""
        metrics = ConsumerMetrics()
        hook = MemoryConsumerHook(_StubConsumer(), _store_with_history(), True, metrics=metrics)
        hook.maybe_consume(_event(KNOWN, "HIGH"))
        assert metrics.consumer_produced == 1
        assert hook.metrics is metrics


# ============================================================================
# 5. C2 只读
# ============================================================================


class TestReadOnly:
    def test_hook_does_not_write_memory(self):
        store = _store_with_history()
        before = [r.to_dict() for r in store.get_episodic_by_visitor(KNOWN)]
        hook = _hook(_StubConsumer(), store=store)
        hook.maybe_consume(_event(KNOWN, "HIGH"))
        assert [r.to_dict() for r in store.get_episodic_by_visitor(KNOWN)] == before

"""C-4 Memory Consumer 运行期接线回归（ADR-0025 §3.10 / DESIGN §4.1）。

验证 pipeline 侧的四件事：

1. **默认关闭 golden 回归**：``consumer_enabled`` 默认 false，关闭时 warnings /
   commands / episodes 与基线逐字段一致（消费侧零泄漏）；
2. **from_settings 装配**：三级开关（memory.enabled → episodic_shadow →
   consumer_enabled）的正反例，含"开了 consumer 却没开影子"的降级告警路径；
3. **调用次序**：``MemoryConsumerHook.maybe_consume`` 必须在 ``MemoryHook.record``
   **之前**——用"消费时刻 store 中该访客的记录数"作为可观测证据（次序颠倒则从 0 变 1）；
4. **VisitorEvent → CurrentEvent 投影**：risk_level 取 max wins、markers 与
   ``behavior:`` 约定同口径（口径漂移会让"当前 vs 历史"恒判行为突变）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from _helpers import TH_HIGH, ManualClock, build_full_pipeline, drive

from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.core.config import MemoryConfig, Settings
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.memory.consumer import (
    CurrentEvent,
    MemoryConsumer,
    ReasoningInput,
    ReasoningResult,
    RuleBasedMemoryConsumer,
    RuleBasedReasoningEngine,
)
from home_perception.runtime import PerceptionPipeline

# ============================================================================
# 辅助
# ============================================================================


class StubDetector:
    """按 plan 返回 Detection 列表（不推进时钟，时钟由 ``drive`` 驱动）。"""

    def __init__(self, plan: list[list[Detection]]):
        self.plan = plan
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets,
            timestamp=0.0,
            inference_ms=0.0,
            source_size=(1, 1),
            inference_size=(1, 1),
            model="stub",
        )


def _person(track_id: int = 1) -> list[Detection]:
    return [
        Detection(
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=[0, 0, 10, 10],
            timestamp=0.0,
            track_id=track_id,
        )
    ]


def _visit_plan(n_present: int = 30) -> list[list[Detection]]:
    """在场 n_present 帧 + 2 帧离场 → 触发一次访客离场。

    配合 ``step_s=30``：30 帧在场 ≈ 15 分钟停留（用少量帧模拟长时段，同 E2E 口径）。
    """
    return [_person(1) for _ in range(n_present)] + [[] for _ in range(2)]


# 风险场景时钟基点：18:30 UTC 命中 TH_HIGH 的 odd_hour_set={18}
_RISKY_BASE = datetime(2026, 7, 31, 18, 30, 0, tzinfo=UTC)
# 普通场景时钟基点：10:00 UTC 不命中 odd_hour，默认阈值下不产 Warning
_NORMAL_BASE = datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)


class _RecordingConsumer(MemoryConsumer):
    """记录每次消费时刻的 store 快照（用于验证调用次序）。"""

    def __init__(self, store: InMemoryStore):
        self._store = store
        self.events: list[CurrentEvent] = []
        self.prior_counts: list[int] = []

    def consume(self, current_event: CurrentEvent) -> ReasoningInput:
        self.events.append(current_event)
        self.prior_counts.append(
            len(self._store.get_episodic_by_visitor(current_event.visitor_instance_id))
        )
        return ReasoningInput(
            current_event=current_event,
            historical_context=(),
            visitor_profile=None,
            risk_pattern=None,
        )


def _run_visit(
    *,
    consumer_enabled: bool,
    consumer_factory=None,
    reasoning_engine=None,
    reasoning_enabled: bool = False,
    risky: bool = True,
):
    """跑一次完整访问，返回 (pipeline, store, frame_results, consumer)。

    ``risky=True``（默认）：TH_HIGH + 18 点 → 产 HIGH Warning，模式 B 必触发；
    ``risky=False``：默认阈值 + 10 点 → 无 Warning、访客无历史，模式 B 必不触发。
    ``reasoning_enabled``：C-6 推理侧开关（需同时传 ``reasoning_engine``）。
    """
    clock = ManualClock(base=_RISKY_BASE if risky else _NORMAL_BASE)
    store = InMemoryStore()
    plan = _visit_plan()
    consumer = consumer_factory(store) if consumer_factory is not None else None
    pipeline = build_full_pipeline(
        StubDetector(plan),
        clock,
        thresholds=TH_HIGH() if risky else None,
        memory_store=store,
        episode_builder=DefaultEpisodeBuilder(),
        episodic_shadow=True,
        memory_consumer=consumer,
        consumer_enabled=consumer_enabled,
        reasoning_engine=reasoning_engine,
        reasoning_enabled=reasoning_enabled,
    )
    results = drive(pipeline, clock, plan, step_s=30.0)
    return pipeline, store, results, consumer


# ============================================================================
# 1. 默认关闭 golden 回归
# ============================================================================


class TestDefaultOff:
    def test_config_default_false(self):
        assert MemoryConfig().consumer_enabled is False
        assert Settings.load().memory.consumer_enabled is False

    def test_hook_disabled_by_default(self):
        """未传 consumer 时 hook 存在但关闭（结构就位、行为为零）。"""
        pipeline, _, _, _ = _run_visit(consumer_enabled=False)
        assert pipeline._memory_consumer_hook.enabled is False
        assert pipeline._memory_consumer_hook.metrics.consumer_evaluated == 0

    def test_enabled_without_consumer_degrades_to_off(self):
        """开关开了但没注入 consumer → 静默降级为关闭（不崩、不半开）。"""
        pipeline, _, _, _ = _run_visit(consumer_enabled=True)
        assert pipeline._memory_consumer_hook.enabled is False

    def test_history_unchanged_when_consumer_on(self):
        """开启消费侧不改变 warnings / commands / episodes（Shadow 零泄漏）。"""
        base_pipeline, base_store, base_results, _ = _run_visit(consumer_enabled=False)
        pipeline, store, results, consumer = _run_visit(
            consumer_enabled=True, consumer_factory=_RecordingConsumer
        )

        def _fields(rs):
            return [
                (r.frame_index, r.n_visitor_events, len(r.warnings), len(r.commands)) for r in rs
            ]

        assert consumer.events, "风险场景下消费侧应被触发，否则本对照失去意义"
        assert _fields(results) == _fields(base_results)
        assert pipeline.metrics.episodes_recorded == base_pipeline.metrics.episodes_recorded
        # 消费侧只读：store 中 episode 总条数不得因 consumer 开启而增加
        # （visitor_instance_id 为随机 UUID4，两次运行不同，故按总量比较）。
        assert len(store.snapshot()["episodic"]) == len(base_store.snapshot()["episodic"])


# ============================================================================
# 2. from_settings 三级开关装配
# ============================================================================


def _from_settings(*, memory: bool, shadow: bool, consumer: bool) -> PerceptionPipeline:
    s = Settings()
    s.memory.enabled = memory
    s.memory.episodic_shadow = shadow
    s.memory.consumer_enabled = consumer
    return PerceptionPipeline.from_settings(s, detector=MagicMock())


class TestFromSettings:
    def test_all_off(self):
        p = _from_settings(memory=False, shadow=False, consumer=False)
        assert p._memory_consumer_hook.enabled is False

    def test_all_on_builds_rule_based_consumer(self):
        p = _from_settings(memory=True, shadow=True, consumer=True)
        assert p._memory_consumer_hook.enabled is True
        assert isinstance(p._memory_consumer_hook._consumer, RuleBasedMemoryConsumer)

    def test_consumer_without_shadow_is_inactive(self):
        """consumer_enabled=true 但没开影子 → 无 store 可召回，降级为关闭。"""
        p = _from_settings(memory=True, shadow=False, consumer=True)
        assert p._memory_store is None
        assert p._memory_consumer_hook.enabled is False

    def test_shadow_without_consumer_keeps_write_side_only(self):
        """只开影子：写侧照常，消费侧不激活（两个 Hook 相互独立）。"""
        p = _from_settings(memory=True, shadow=True, consumer=False)
        assert p._memory_hook.enabled is True
        assert p._memory_consumer_hook.enabled is False

    def test_reasoning_engine_wired_when_enabled(self):
        """consumer_enabled + reasoning_enabled 同真 → 注入 RuleBasedReasoningEngine。"""
        p = _from_settings(memory=True, shadow=True, consumer=True)
        # 默认 reasoning_enabled=false
        assert p._memory_consumer_hook.reasoning_enabled is False
        s = Settings()
        s.memory.enabled = True
        s.memory.episodic_shadow = True
        s.memory.consumer_enabled = True
        s.memory.reasoning_enabled = True
        p = PerceptionPipeline.from_settings(s, detector=MagicMock())
        assert p._memory_consumer_hook.reasoning_enabled is True
        assert isinstance(
            p._memory_consumer_hook._reasoning_engine, RuleBasedReasoningEngine
        )

    def test_reasoning_without_consumer_is_inactive(self):
        """reasoning_enabled=true 但消费侧未激活 → 推理侧静默降级（不崩、不半开）。"""
        s = Settings()
        s.memory.enabled = True
        s.memory.episodic_shadow = True
        s.memory.consumer_enabled = False
        s.memory.reasoning_enabled = True
        p = PerceptionPipeline.from_settings(s, detector=MagicMock())
        assert p._memory_consumer_hook.reasoning_enabled is False
        assert p._memory_consumer_hook._reasoning_engine is None


# ============================================================================
# 3. 调用次序：消费在写入之前
# ============================================================================


class TestCallOrder:
    def test_consume_sees_no_self_written_record(self):
        """消费时刻 store 中该访客记录数必须为 0；帧结束后为 1。

        若把 ``maybe_consume`` 挪到 ``record`` 之后，prior_counts 会变成 [1]，
        即首次来访被自己刚写入的记录污染成"已知访客"——本断言即该缺陷的探针。
        """
        pipeline, store, _, consumer = _run_visit(
            consumer_enabled=True, consumer_factory=_RecordingConsumer
        )

        assert len(consumer.events) >= 1, "消费侧未被触发，次序断言失去意义"
        assert consumer.prior_counts == [0] * len(consumer.events)
        vid = consumer.events[0].visitor_instance_id
        assert len(store.get_episodic_by_visitor(vid)) == 1
        assert pipeline._memory_consumer_hook.metrics.consumer_produced == len(consumer.events)

    def test_projected_event_carries_runtime_risk_level(self):
        """接线到主链路后，投影出的 CurrentEvent 带真实风险等级（不是恒 None）。"""
        _, _, _, consumer = _run_visit(consumer_enabled=True, consumer_factory=_RecordingConsumer)
        assert consumer.events[0].risk_level == "HIGH"
        assert consumer.events[0].event_type == "visitor_event"


class TestGatingInPipeline:
    def test_low_risk_unknown_visitor_not_consumed(self):
        """普通访问（无 Warning、无历史）在主链路上也不触发消费——门控端到端生效。"""
        pipeline, _, results, consumer = _run_visit(
            consumer_enabled=True, consumer_factory=_RecordingConsumer, risky=False
        )
        assert sum(r.n_visitor_events for r in results) >= 1, "本场景应产生访客离场事件"
        assert consumer.events == []
        metrics = pipeline._memory_consumer_hook.metrics
        assert metrics.consumer_evaluated >= 1
        assert metrics.consumer_triggered == 0


# ============================================================================
# 4. VisitorEvent → CurrentEvent 投影
# ============================================================================


def _visitor_event() -> VisitorEvent:
    return VisitorEvent(
        event_id="ev-fixed-1",
        visitor_id="11111111-1111-4111-8111-111111111111",
        enter_time=datetime(2026, 7, 20, 20, 55, 0, tzinfo=UTC),
        leave_time=datetime(2026, 7, 20, 21, 0, 0, tzinfo=UTC),
        duration_seconds=300.0,
        source_video="demo/test",
    )


def _warning(risk_level: str, reasons: list[str]) -> WarningEvent:
    return WarningEvent(
        elder_id="elder_001",
        device_id="demo/test",
        risk_level=risk_level,
        recommended_action="MONITOR",
        trigger_events=[{"event_id": "11111111-1111-4111-8111-111111111111:visit_normal"}],
        reason_summary=reasons,
    )


class TestProjection:
    def test_maps_identity_and_time(self):
        cur = PerceptionPipeline._to_current_event(_visitor_event(), [])
        assert cur.event_id == "ev-fixed-1"
        assert cur.event_type == "visitor_event"
        assert cur.visitor_instance_id == "11111111-1111-4111-8111-111111111111"
        assert cur.occurred_at == datetime(2026, 7, 20, 21, 0, 0, tzinfo=UTC)

    def test_no_warning_means_no_risk_level(self):
        cur = PerceptionPipeline._to_current_event(_visitor_event(), [])
        assert cur.risk_level is None
        assert cur.markers == ()

    def test_risk_level_is_max_wins(self):
        """多条 warning 取最高等级，与 DefaultEpisodeBuilder._pick_max_risk 同口径。

        若改成"取最后一条"，本例会得到 LOW → 与该次访问日后被召回为历史时的
        risk_level 不一致，冲突判定会凭空抖动。
        """
        cur = PerceptionPipeline._to_current_event(
            _visitor_event(),
            [_warning("MEDIUM", ["r1"]), _warning("HIGH", ["r2"]), _warning("LOW", ["r3"])],
        )
        assert cur.risk_level == "HIGH"

    def test_markers_follow_behavior_prefix_convention(self):
        """只取 ``behavior:`` 前缀项、去前缀、去空、去重、保序。"""
        cur = PerceptionPipeline._to_current_event(
            _visitor_event(),
            [
                _warning("LOW", ["behavior:night", "停留时间过长", "behavior:"]),
                _warning("LOW", ["behavior:observe_camera", "behavior:night"]),
            ],
        )
        assert cur.markers == ("night", "observe_camera")


# ============================================================================
# 5. C-6 Reasoning Engine Shadow 接线（FrameResult.reasoning_results）
# ============================================================================


class TestReasoningShadow:
    def test_reasoning_results_present_when_enabled(self):
        """reasoning 开启且消费侧触发 → FrameResult 携带 ReasoningResult。"""
        pipeline, _, results, _ = _run_visit(
            consumer_enabled=True,
            consumer_factory=_RecordingConsumer,
            reasoning_engine=RuleBasedReasoningEngine(),
            reasoning_enabled=True,
        )
        assert pipeline._memory_consumer_hook.reasoning_enabled is True
        all_results = [r for fr in results for r in fr.reasoning_results]
        assert all_results, "reasoning 开启时 FrameResult 应携带 ReasoningResult"
        assert all(isinstance(r, ReasoningResult) for r in all_results)
        assert pipeline._memory_consumer_hook.metrics.consumer_reasoned >= 1

    def test_reasoning_results_absent_when_disabled(self):
        """reasoning 关闭 → FrameResult.reasoning_results 恒为空（零开销）。"""
        pipeline, _, results, _ = _run_visit(
            consumer_enabled=True,
            consumer_factory=_RecordingConsumer,
            reasoning_engine=None,
            reasoning_enabled=False,
        )
        assert pipeline._memory_consumer_hook.reasoning_enabled is False
        all_results = [r for fr in results for r in fr.reasoning_results]
        assert all_results == []

    def test_reasoning_shadow_does_not_change_main_link(self):
        """Shadow 推理不写 Memory、不产 Warning / command（零泄漏）。

        开启推理侧与主链路输出（warnings / commands / episodes）逐字段一致。
        """
        base_pipeline, base_store, base_results, _ = _run_visit(
            consumer_enabled=True, consumer_factory=_RecordingConsumer
        )
        pipeline, store, results, _ = _run_visit(
            consumer_enabled=True,
            consumer_factory=_RecordingConsumer,
            reasoning_engine=RuleBasedReasoningEngine(),
            reasoning_enabled=True,
        )

        def _fields(rs):
            return [
                (fr.frame_index, fr.n_visitor_events, len(fr.warnings), len(fr.commands))
                for fr in rs
            ]

        assert _fields(results) == _fields(base_results)
        assert pipeline.metrics.episodes_recorded == base_pipeline.metrics.episodes_recorded
        assert len(store.snapshot()["episodic"]) == len(base_store.snapshot()["episodic"])

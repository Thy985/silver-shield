"""Stage F 影子写入回归测试（ADR-0024 Slice 5 · Episodic Memory）。

torch-free，进 CI 每 PR 合约子集。

验证工程方案 §8.3 硬性合入门：
- **flag 关闭 golden 回归**：``memory.enabled=false`` 时 store/builder 全 None，
  ``episodes_recorded == 0``，历史字段正常产出（影子零泄漏）。
- **memory 开 + 影子关**：仅 Snapshot Recovery 激活，不落 Episode（episodes_recorded == 0）。
- **memory 开 + 影子开**：每次访客离场落一条 EpisodicRecord 入 InMemoryStore，
  ``episodes_recorded`` 与离场事件数一致；store 可按 visitor 查询。
- **影子隔离**：开启影子不改变 warnings/commands（历史行为逐字段一致，Shadow Mode 不接决策）。
- **风险捕获**：有 Warning 时落库的 episode 带 risk_level + actions；无 Warning 时 risk_level 为 None。
- **from_settings 装配**：memory 段开关正确透传 store/builder/shadow 标志。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.core.config import MemoryConfig, Settings
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.detection.tracker import VisitorTracker
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.runtime import FrameResult, PerceptionPipeline

# ============================================================================
# 测试辅助（模式复用自 test_pipeline_realtime_bypass.py，保持 Stage F 自包含）
# ============================================================================


class ManualClock:
    """可控时钟：now() 返回当前时间，advance() 推进。"""

    def __init__(self, base: datetime | None = None):
        self._t = base or datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()

    def advance(self, seconds: float = 1.0) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class StubDetector:
    """按 plan 返回 Detection 列表；每次 detect 推进时钟 1s。"""

    def __init__(self, plan: list[list[Detection]], clock: ManualClock | None = None):
        self.plan = plan
        self.clock = clock
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        if self.clock is not None:
            self.clock.advance(1.0)
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


def _build_pipeline(
    detector,
    clock: ManualClock,
    *,
    thresholds: ThresholdConfig | None = None,
    memory_store: InMemoryStore | None = None,
    episode_builder: DefaultEpisodeBuilder | None = None,
    episodic_shadow: bool = False,
) -> PerceptionPipeline:
    """构造 PerceptionPipeline，可选挂入 Stage F Episodic Memory 影子写入组件。"""
    tracker = VisitorTracker(absence_gap_s=5.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/test", now_provider=clock)
    th = thresholds or ThresholdConfig()
    feat = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(
        device_id="demo/test",
        location="入户门",
        thresholds=th,
        now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(),
        now_provider=clock,
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    executor = ActionExecutor(
        dispatcher=dispatcher,
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=3,
    )
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feat,
        rule_engine=rule_engine,
        decision_engine=decision,
        executor=executor,
        now_provider=clock,
        memory_store=memory_store,
        episode_builder=episode_builder,
        episodic_shadow=episodic_shadow,
    )


def _run_frames(p: PerceptionPipeline, n: int) -> list[FrameResult]:
    """跑 n 帧 None，返回每帧 FrameResult。"""
    return [p.process_frame(None, frame_index=i) for i in range(n)]


def _history_fields(r: FrameResult) -> tuple:
    """提取历史五字段（不含 behavior_states/risk_signals）用于逐字段对比。"""
    return (
        r.frame_index,
        r.n_detections,
        r.n_visitor_events,
        len(r.perception_events),
        len(r.warnings),
        len(r.commands),
    )


def _visit_plan() -> list[list[Detection]]:
    """2 帧在场 + 6 帧离场 → 触发 1 次访客离场（absence_gap_s=5.0）。"""
    return [_person(1), _person(1)] + [[] for _ in range(6)]


def _make_shadow() -> tuple:
    """构造一组 Stage F 影子写入组件。"""
    return InMemoryStore(), DefaultEpisodeBuilder(), True


# ============================================================================
# 1. MemoryConfig 配置校验
# ============================================================================


class TestMemoryConfig:
    def test_episodic_shadow_default_false(self):
        """Stage F 影子开关默认关闭（v1 不产 Warning）。"""
        c = MemoryConfig()
        assert c.enabled is False
        assert c.episodic_shadow is False

    def test_yaml_loads_memory_section(self):
        """config/default.yaml 的 memory 段被正确加载（含 episodic_shadow）。"""
        s = Settings.load()
        assert s.memory.enabled is False
        assert s.memory.episodic_shadow is False


# ============================================================================
# 2. flag 关闭 golden 回归（memory off）
# ============================================================================


class TestFlagOffGolden:
    def test_no_store_or_builder_constructed(self):
        """memory 关闭：from_settings 不构造 store/builder，影子标志 False。"""
        s = Settings()
        s.memory.enabled = False
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._memory_store is None
        assert p._episode_builder is None
        assert p._episodic_shadow is False

    def test_no_episodes_recorded(self):
        """memory 关闭：跑完整访问，episodes_recorded 恒 0。"""
        clock = ManualClock()
        p = _build_pipeline(StubDetector(_visit_plan(), clock), clock, episodic_shadow=False)
        _run_frames(p, len(_visit_plan()))
        assert p.metrics.episodes_recorded == 0

    def test_history_fields_normal(self):
        """memory 关闭：历史字段正常产出（与基线一致）。"""
        clock = ManualClock()
        p = _build_pipeline(StubDetector(_visit_plan(), clock), clock, episodic_shadow=False)
        results = _run_frames(p, len(_visit_plan()))
        assert sum(r.n_visitor_events for r in results) >= 1
        assert results[0].n_detections == 1


# ============================================================================
# 3. memory 开 + 影子关：仅 Snapshot Recovery，不落 Episode
# ============================================================================


class TestMemoryOnShadowOff:
    def test_no_episodes_when_shadow_off(self):
        """memory.enabled=true 但 episodic_shadow=false：不落 Episode。"""
        clock = ManualClock()
        p = _build_pipeline(
            StubDetector(_visit_plan(), clock),
            clock,
            memory_store=InMemoryStore(),  # 即便传入 store，影子关也不写
            episode_builder=DefaultEpisodeBuilder(),
            episodic_shadow=False,
        )
        _run_frames(p, len(_visit_plan()))
        assert p.metrics.episodes_recorded == 0
        assert p._memory_store is not None
        assert p._memory_store.get_active_episodic() == []


# ============================================================================
# 4. memory 开 + 影子开：落 Episode 入 store
# ============================================================================


class TestStageFShadowRecordsEpisodes:
    def test_one_visit_records_one_episode(self):
        """一次访客离场 → 落一条 EpisodicRecord，episodes_recorded 与离场数一致。"""
        clock = ManualClock()
        store, builder, shadow = _make_shadow()
        p = _build_pipeline(
            StubDetector(_visit_plan(), clock),
            clock,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        results = _run_frames(p, len(_visit_plan()))
        n_events = sum(r.n_visitor_events for r in results)
        assert n_events >= 1
        assert p.metrics.episodes_recorded == n_events
        episodes = p._memory_store.get_active_episodic()
        assert len(episodes) == n_events
        rec = episodes[0]
        assert rec.record_id.startswith("ep-")
        assert rec.visitor_instance_id  # 非空

    def test_episode_queryable_by_visitor(self):
        """落库 episode 可按 visitor_instance_id 查询。"""
        clock = ManualClock()
        store, builder, shadow = _make_shadow()
        p = _build_pipeline(
            StubDetector(_visit_plan(), clock),
            clock,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        _run_frames(p, len(_visit_plan()))
        rec = p._memory_store.get_active_episodic()[0]
        by_visitor = p._memory_store.get_episodic_by_visitor(rec.visitor_instance_id)
        assert len(by_visitor) == 1
        assert by_visitor[0].record_id == rec.record_id

    def test_no_risk_when_no_warning(self):
        """默认阈值下无 Warning → 落库 episode 的 risk_level 为 None。"""
        clock = ManualClock()
        store, builder, shadow = _make_shadow()
        p = _build_pipeline(
            StubDetector(_visit_plan(), clock),
            clock,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        _run_frames(p, len(_visit_plan()))
        rec = p._memory_store.get_active_episodic()[0]
        assert rec.risk_level is None
        assert rec.summary.endswith("，未触发风险。")


# ============================================================================
# 5. 影子隔离：开启影子不改变历史行为（Shadow Mode 不接决策）
# ============================================================================


class TestStageFShadowIsolation:
    def test_history_fields_unchanged_vs_off(self):
        """影子开 vs 关：同一帧序列下历史五字段逐字段一致（影子不污染主线）。"""
        plan = [_person(1), _person(1), _person(1), [], []]

        clock_off = ManualClock()
        p_off = _build_pipeline(
            StubDetector(plan, clock_off),
            clock_off,
            episodic_shadow=False,
        )
        results_off = _run_frames(p_off, 5)

        clock_on = ManualClock()
        store, builder, shadow = _make_shadow()
        p_on = _build_pipeline(
            StubDetector(plan, clock_on),
            clock_on,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        results_on = _run_frames(p_on, 5)

        assert len(results_off) == len(results_on)
        for i, (ro, rn) in enumerate(zip(results_off, results_on)):
            assert _history_fields(ro) == _history_fields(rn), (
                f"frame {i}: 历史字段不一致 (off={_history_fields(ro)} on={_history_fields(rn)})"
            )

    def test_warnings_and_commands_unchanged_vs_off(self):
        """影子开 vs 关：warnings / commands 数量逐帧一致（不产额外 Warning）。"""
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        plan = _visit_plan()

        clock_off = ManualClock()
        p_off = _build_pipeline(
            StubDetector(plan, clock_off),
            clock_off,
            thresholds=th,
            episodic_shadow=False,
        )
        results_off = _run_frames(p_off, len(plan))

        clock_on = ManualClock()
        store, builder, shadow = _make_shadow()
        p_on = _build_pipeline(
            StubDetector(plan, clock_on),
            clock_on,
            thresholds=th,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        results_on = _run_frames(p_on, len(plan))

        for i, (ro, rn) in enumerate(zip(results_off, results_on)):
            assert len(ro.warnings) == len(rn.warnings), (
                f"frame {i}: warnings 不一致 (off={len(ro.warnings)} on={len(rn.warnings)})"
            )
            assert len(ro.commands) == len(rn.commands), (
                f"frame {i}: commands 不一致 (off={len(ro.commands)} on={len(rn.commands)})"
            )


# ============================================================================
# 6. 风险捕获：有 Warning 时落库 episode 带 risk_level + actions
# ============================================================================


class TestStageFCapturesRisk:
    def test_episode_captures_risk_and_actions(self):
        """小阈值触发 Warning → 落库 episode 带 risk_level 且 actions 非空。"""
        # 3 帧在场 → 停留 2s（StubDetector 每帧推进 1s），超 1.5s 触发 abnormal_dwell
        # → DecisionEngine 产 LOW Warning（abnormal_dwell → NOTIFY_FAMILY）。
        plan = [_person(1), _person(1), _person(1)] + [[] for _ in range(6)]
        th = ThresholdConfig(long_duration_seconds=1.5, repeat_visit_count=3)
        clock = ManualClock()
        store, builder, shadow = _make_shadow()
        p = _build_pipeline(
            StubDetector(plan, clock),
            clock,
            thresholds=th,
            memory_store=store,
            episode_builder=builder,
            episodic_shadow=shadow,
        )
        results = _run_frames(p, len(_visit_plan()))
        total_warnings = sum(len(r.warnings) for r in results)
        assert total_warnings >= 1, "应触发历史 Warning"

        rec = p._memory_store.get_active_episodic()[0]
        assert rec.risk_level is not None, "有 Warning 时落库 episode 应带 risk_level"
        assert rec.actions, "有 ActionCommand 时落库 episode 应捕获 actions"
        assert rec.summary  # 非空 human-interpretable summary


# ============================================================================
# 6.5 回归：WarningEvent.created_at 必须取 ctx.now（注入时钟），非墙钟
# ============================================================================


class TestWarningCreatedAtUsesContextNow:
    """回归（Stage F 暴露的根因）：WarningEvent.created_at 必须取 ctx.now，否则 Demo/测试
    场景下 Warning 落在墙钟时间线，与 VisitorEvent 模拟时间线错位，ADR-0024 Episode
    Builder 的 [enter, leave+60s] 时间窗关联失败 → 影子写入捕获不到风险。
    """

    def test_warning_created_at_uses_injected_now(self):
        from datetime import datetime
        from uuid import uuid4

        from home_perception.analysis.perception import PerceptionEvent

        fixed = datetime(2026, 7, 19, 23, 30, 0, tzinfo=UTC)
        engine = DecisionEngine(
            elder_id="elder_001",
            policy=RuleBasedDecisionPolicy(),
            now_provider=lambda: fixed,
        )
        pe = PerceptionEvent(
            device_id="demo/test",
            event_type="abnormal_dwell",
            score=0.5,
            visitor_id=uuid4(),
            source_video="demo/test",
            timestamp=1784455800.0,
            meta={"rule": "LongDurationRule"},
        )
        w = engine.evaluate([pe])
        assert w is not None
        assert w.created_at == fixed, "created_at 应取注入时钟，而非墙钟"
        assert w.meta["decided_at"] == fixed.isoformat()


# ============================================================================
# 7. from_settings 装配：memory 段开关透传
# ============================================================================


class TestFromSettingsAssembly:
    def test_memory_off_no_components(self):
        """memory 关闭：from_settings 不构造 store/builder，影子标志 False。"""
        s = Settings()
        s.memory.enabled = False
        s.memory.episodic_shadow = False
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._memory_store is None
        assert p._episode_builder is None
        assert p._episodic_shadow is False

    def test_memory_on_shadow_on_constructs_components(self):
        """memory 开 + 影子开：构造 InMemoryStore + DefaultEpisodeBuilder，影子标志 True。"""
        s = Settings()
        s.memory.enabled = True
        s.memory.episodic_shadow = True
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._memory_store is not None
        assert p._episode_builder is not None
        assert p._episodic_shadow is True
        assert isinstance(p._memory_store, InMemoryStore)
        assert isinstance(p._episode_builder, DefaultEpisodeBuilder)

    def test_memory_on_shadow_off_no_store(self):
        """memory 开 + 影子关：仅 Snapshot Recovery 激活，不构造 Episode Store。"""
        s = Settings()
        s.memory.enabled = True
        s.memory.episodic_shadow = False
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._memory_store is None
        assert p._episode_builder is None
        assert p._episodic_shadow is False

    def test_shadow_on_without_memory_inactive(self):
        """episodic_shadow=true 但 memory.enabled=false：影子未激活（构造期告警）。"""
        s = Settings()
        s.memory.enabled = False
        s.memory.episodic_shadow = True
        from unittest.mock import MagicMock

        fake_det = MagicMock()
        p = PerceptionPipeline.from_settings(s, detector=fake_det)
        assert p._memory_store is None
        assert p._episodic_shadow is False

"""AudioSessionRecorder 运行时接线测试（ADR-0027 音频→Memory 闭环）。

覆盖：
- **闭环落库**：音频会话经既有决策链（RiskSignal → DecisionEngine → WarningEvent
  → MemoryHook）落库为**纯音频** EpisodicRecord（D4 匿名：visitor_instance_id=None、
  modalities=[AUDIO]、evidence_refs 引用、audio_session_id 身份）；
- **D3 决策门槛（基石）**：无 WarningEvent（空会话 / 决策降级）→ 不落库——
  Memory 只记录「系统已确认的风险事件」，不新增音频记忆孤岛；
- **门控与降级**：enabled=false 空操作；memory_hook=None 如实报告未落库；
- **C3 确定性**：同输入 + 固定 session_id 工厂，摘要字段级一致；
- **C1 无分数**：AudioSessionSummary 不含任何 score/decision/warning 字段；
- **装配**：from_settings 在 audio.enabled + memory 影子激活时装配 recorder 并
  暴露 pipeline.process_audio_session 入口；默认 / 缺 memory 时不装配（None）。

铁律（AGENTS.md 测试有效性）：D3 负例（无 warning 不落库）、enabled 负例、
未装配负例——每个「应该发生」的断言都配「不该发生」的对照。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from home_perception.action.dispatcher import ActionDispatcher
from home_perception.action.executor import ActionExecutor
from home_perception.action.notifier import MockNotifier
from home_perception.action.publisher import MockPublisher
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.audio.event import (
    AudioPerceptionEvent,
    AudioPerceptionKind,
    new_event_id,
)
from home_perception.core.config import AudioConfig, MemoryConfig, Settings
from home_perception.core.event import EvidenceModality
from home_perception.integration.audio_adapter import AudioEvidenceCollector
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.runtime.audio_session_recorder import (
    AudioSessionRecorder,
    AudioSessionSummary,
)
from home_perception.runtime.memory_hook import MemoryHook
from home_perception.runtime.observability import PipelineMetrics
from home_perception.runtime.pipeline import PerceptionPipeline


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _audio_event(
    kind: AudioPerceptionKind,
    ts: float,
    *,
    score: float = 0.7,
    confidence: float = 0.8,
) -> AudioPerceptionEvent:
    return AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=ts,
        kind=kind,
        score=score,
        confidence=confidence,
        source_segment_ids=[f"seg-{int(ts)}"],
    )


def _build_recorder(
    store: InMemoryStore,
    *,
    enabled: bool = True,
    memory_hook: MemoryHook | None = None,
    with_hook: bool = True,
    session_id: str = "audio_session_test",
) -> AudioSessionRecorder:
    """最小装配（与 from_settings 同构，但显式可注入）。

    ``with_hook=False`` 表达「无 Memory 后端」（memory_hook 为 None 且不自动创建）。
    """
    clock = _FakeClock(_utc(1710000000.0))
    decision = DecisionEngine(
        elder_id="elder_001",
        policy=RuleBasedDecisionPolicy(),
        now_provider=clock,
    )
    executor = ActionExecutor(
        dispatcher=ActionDispatcher(),
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=3,
    )
    hook: MemoryHook | None = memory_hook
    if hook is None and with_hook:
        hook = MemoryHook(
            DefaultEpisodeBuilder(),
            store,
            True,
            PipelineMetrics(),
        )
    return AudioSessionRecorder(
        decision,
        executor,
        hook,
        device_id="home_entry_01",
        session_id_factory=lambda: session_id,
        enabled=enabled,
    )


class _FakeClock:
    """固定时刻时钟（决策确定性）。"""

    def __init__(self, t: datetime) -> None:
        self._t = t

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self.now()


# ============================================================================
# 1. 闭环落库（D4 纯音频匿名 episode）
# ============================================================================


class TestClosedLoopRecord:
    def test_audio_session_recorded_as_pure_audio_episode(self) -> None:
        """正常闭环：2 个音频事件 → 决策确认 → 落库 1 条纯音频 EpisodicRecord。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)
        events = [
            _audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0),
            _audio_event(AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT, 1710000030.0),
        ]
        summary = recorder.record_session(events)

        assert summary.episode_recorded is True
        assert summary.session_id == "audio_session_test"
        assert summary.n_events == 2
        assert len(summary.warning_ids) == 1  # 决策确认 1 个 WarningEvent
        assert len(summary.evidence_ids) == 2

        episodes = store.snapshot()["episodic"]
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep["visitor_instance_id"] is None  # D4 匿名，绝不反填访客
        assert ep["audio_session_id"] == "audio_session_test"
        assert ep["modalities"] == [EvidenceModality.AUDIO.value]
        assert len(ep["evidence_refs"]) == 2
        assert ep["risk_level"] == "LOW"  # 音频异常 → visit_pending_verify 弱信号
        assert ep["record_id"] == "ep-audio_session_test"

    def test_all_evidence_failed_keeps_input_count(self, monkeypatch) -> None:
        """审查修复：输入非空但证据全部采集失败 → n_events 保留输入计数（契约：
        「n_events=输入事件数」），evidence_ids=()；可与「空输入」（n_events=0）
        明确区分。不落库、不抛。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)

        def boom_collect(event, uri, **kwargs):
            raise RuntimeError("collect down")

        monkeypatch.setattr(recorder._evidence_collector, "collect_segment", boom_collect)
        summary = recorder.record_session(
            [
                _audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0),
                _audio_event(AudioPerceptionKind.AUDIO_VOICE_RAISED, 1710000030.0),
            ]
        )
        assert summary.n_events == 2  # 输入计数保留（区别于空输入）
        assert summary.evidence_ids == ()
        assert summary.warning_ids == ()
        assert summary.episode_recorded is False
        assert store.snapshot()["episodic"] == []

        # 与空输入对照：n_events=0（调用方可区分两类降级）
        empty = recorder.record_session([])
        assert empty.n_events == 0
        assert empty.evidence_ids == ()

    def test_d3_no_warning_no_record(self) -> None:
        """D3 负例：空会话（无事件）→ 无决策确认 → 不落库。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)
        summary = recorder.record_session([])
        assert summary.episode_recorded is False
        assert summary.warning_ids == ()
        assert store.snapshot()["episodic"] == []

    def test_d3_decision_failure_no_record(self) -> None:
        """D3 负例：决策阶段异常 → 降级为不落库（Memory 只记确认事件，不崩调用方）。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)

        class _BoomDecisionEngine(DecisionEngine):
            def evaluate(self, perception_events):
                raise RuntimeError("decision down")

        recorder._decision_engine = _BoomDecisionEngine(
            elder_id="elder_001", policy=RuleBasedDecisionPolicy()
        )
        summary = recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_VOICE_RAISED, 1710000010.0)]
        )
        assert summary.episode_recorded is False
        assert store.snapshot()["episodic"] == []

    def test_d3_perception_mapping_failure_no_record(self, monkeypatch) -> None:
        """D3 负例：risk_signal_to_perception 抛异常（脏信号）→ 单信号降级跳过，
        不破坏「不抛未分类异常」契约（审查修复：原实现此阶段无 try/except）。"""
        from home_perception.runtime import audio_session_recorder as rec_mod

        store = InMemoryStore()
        recorder = _build_recorder(store)

        def boom(sig, device_id):
            raise ValueError("subject_id 非合法 UUID（脏信号）")

        monkeypatch.setattr(rec_mod, "risk_signal_to_perception", boom)
        summary = recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        # 不抛异常（契约），决策无输入 → 不落库
        assert summary.episode_recorded is False
        assert summary.warning_ids == ()
        assert store.snapshot()["episodic"] == []

    def test_d4_signal_layer_visitor_identity_cleared(self, monkeypatch) -> None:
        """D4 匿名强化：adapt_audio_event 的 _looks_uuid 兜底会把 subject UUID 复制进
        RiskSignal.visitor_instance_id——recorder 必须显式清空（信号层不携带访客身份）。"""
        from home_perception.runtime import audio_session_recorder as rec_mod

        store = InMemoryStore()
        recorder = _build_recorder(store)
        seen: list = []

        def capture_adapt(event, device_id, **kwargs):
            from home_perception.integration import audio_adapter

            sig = audio_adapter.adapt_audio_event(event, device_id, **kwargs)
            seen.append(sig)
            return sig

        monkeypatch.setattr(rec_mod, "adapt_audio_event", capture_adapt)
        recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        assert seen, "adapt_audio_event 应被调用"
        # 兜底会填入 visitor（UUID 格式），recorder 必须清空 —— 变异验证：
        # 若去掉 sig.visitor_instance_id = None，本断言立即红
        assert all(sig.visitor_instance_id is None for sig in seen)

    def test_injected_audio_session_id_requires_safe_charset(self) -> None:
        """审查修复：显式注入含危险字符的 audio_session_id → fail loud（ValueError），
        防 record_id 污染存储键。"""
        recorder = _build_recorder(InMemoryStore())
        with pytest.raises(ValueError, match="audio_session_id"):
            recorder.record_session(
                [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)],
                audio_session_id="../../etc/passwd",
            )
        with pytest.raises(ValueError, match="audio_session_id"):
            recorder.record_session([], audio_session_id="a:b?c")

    def test_source_path_propagates_to_evidence_uri(self) -> None:
        """审查建议：source_path 透传到证据 uri（原片不上传，仅指针）。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)
        recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)],
            source_path="file:///home/audio/session_001.wav",
        )
        episodes = store.snapshot()["episodic"]
        assert len(episodes) == 1
        # 落库记录仅引用证据 ID；uri 在 EvidenceItem 层——通过 evidence_refs 无法直接
        # 解析（无证据库），此处验证 collector 透传语义（metadata/uri 已由 collector
        # 单测覆盖），并确认落库 evidence_refs 非空
        assert episodes[0]["evidence_refs"]

    def test_invalid_event_type_degraded_per_event(self) -> None:
        """审查建议：元素类型不符 → adapt_audio_event 的 isinstance 校验拒绝，
        逐事件降级跳过，不破坏契约（不抛未分类异常）。"""
        store = InMemoryStore()
        recorder = _build_recorder(store)
        summary = recorder.record_session(
            ["not-an-audio-event"],  # 类型不符
            audio_session_id="session_badtype",
        )
        assert summary.episode_recorded is False
        assert store.snapshot()["episodic"] == []

    def test_enabled_false_is_noop(self) -> None:
        """enabled=False → 空摘要，不落库（负例：开关必须真生效）。"""
        store = InMemoryStore()
        recorder = _build_recorder(store, enabled=False)
        summary = recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        assert summary.episode_recorded is False
        assert summary.session_id == ""
        assert store.snapshot()["episodic"] == []

    def test_memory_hook_none_reports_not_recorded(self) -> None:
        """memory_hook=None（无 Memory 后端）→ 决策仍执行，但如实报告未落库。"""
        recorder = _build_recorder(InMemoryStore(), with_hook=False)
        summary = recorder.record_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        assert len(summary.warning_ids) == 1  # 决策链照常
        assert summary.episode_recorded is False  # 无后端不落库（如实）

    def test_c3_deterministic_same_input_twice(self) -> None:
        """C3：同输入 + 固定 session_id 两次收割，稳定字段逐字段一致。

        随机标识符（warning_id / evidence_id 为 uuid4）不在比较范围内——与 E2E-3
        「同一 Observation Stream 两次回放仅标识符不同」的 UUID 归一化哲学一致。
        """
        events = [
            _audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0),
            _audio_event(AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT, 1710000030.0),
        ]
        a = _build_recorder(InMemoryStore()).record_session(events)
        b = _build_recorder(InMemoryStore()).record_session(events)
        assert a.session_id == b.session_id == "audio_session_test"
        assert a.n_events == b.n_events == 2
        assert a.episode_recorded == b.episode_recorded is True
        assert len(a.warning_ids) == len(b.warning_ids) == 1
        assert len(a.evidence_ids) == len(b.evidence_ids) == 2

    def test_c1_summary_has_no_score_fields(self) -> None:
        """C1：AudioSessionSummary 不含任何 score/decision/warning 字段。"""
        names = {f.name for f in dataclasses.fields(AudioSessionSummary)}
        assert not (names & {"risk_score", "score", "decision", "warning"})


# ============================================================================
# 2. 装配（from_settings + pipeline 入口）
# ============================================================================


class TestPipelineAssembly:
    def _settings(self, *, audio: bool, memory: bool, shadow: bool) -> Settings:
        return Settings(
            audio=AudioConfig(enabled=audio),
            memory=MemoryConfig(enabled=memory, episodic_shadow=shadow),
        )

    def test_audio_and_memory_enabled_assembles_recorder(self) -> None:
        """三开关同真 → recorder 装配；process_audio_session 经入口落库。"""
        from unittest.mock import MagicMock

        s = self._settings(audio=True, memory=True, shadow=True)
        p = PerceptionPipeline.from_settings(s, detector=MagicMock())
        assert p._audio_recorder is not None

        store = p._memory_store
        assert store is not None
        summary = p.process_audio_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        assert summary is not None
        assert summary.episode_recorded is True
        assert len(store.snapshot()["episodic"]) == 1  # 入口落库成功

    def test_audio_decision_bypasses_visual_decision_enabled(self) -> None:
        """审查建议：音频决策链独立于视觉 decision_enabled（from_settings 默认
        decision_enabled=False 时，音频闭环仍经 DecisionEngine 确认并落库）。"""
        from unittest.mock import MagicMock

        s = self._settings(audio=True, memory=True, shadow=True)
        assert s.realtime_risk.decision_enabled is False  # 视觉决策默认关
        p = PerceptionPipeline.from_settings(s, detector=MagicMock())
        summary = p.process_audio_session(
            [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
        )
        assert summary is not None
        assert summary.episode_recorded is True  # 音频独立链路不受视觉开关影响
        assert len(p._memory_store.snapshot()["episodic"]) == 1  # type: ignore[union-attr]

    def test_default_settings_no_recorder(self) -> None:
        """默认 Settings() → 不装配；process_audio_session 返回 None（零行为变化）。"""
        from unittest.mock import MagicMock

        p = PerceptionPipeline.from_settings(Settings(), detector=MagicMock())
        assert p._audio_recorder is None
        assert (
            p.process_audio_session(
                [_audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)]
            )
            is None
        )

    def test_audio_enabled_without_memory_not_assembled(self) -> None:
        """负例：audio.enabled=true 但 memory 影子未开 → 不装配（依赖校验）。"""
        from unittest.mock import MagicMock

        p = PerceptionPipeline.from_settings(
            self._settings(audio=True, memory=False, shadow=False),
            detector=MagicMock(),
        )
        assert p._audio_recorder is None

    def test_memory_enabled_without_audio_not_assembled(self) -> None:
        """负例：memory 开但 audio.enabled=false → 不装配。"""
        from unittest.mock import MagicMock

        p = PerceptionPipeline.from_settings(
            self._settings(audio=False, memory=True, shadow=True),
            detector=MagicMock(),
        )
        assert p._audio_recorder is None

    def test_shared_memory_hook_single_instance(self) -> None:
        """视觉/音频两路共享同一 MemoryHook 实例（同一 metrics 计数）。"""
        from unittest.mock import MagicMock

        s = self._settings(audio=True, memory=True, shadow=True)
        p = PerceptionPipeline.from_settings(s, detector=MagicMock())
        assert p._audio_recorder._memory_hook is p._memory_hook  # type: ignore[union-attr]
        assert p._audio_recorder._memory_hook._metrics is p.metrics  # type: ignore[union-attr]


# ============================================================================
# 3. AudioEvidenceCollector 扩展（audio_kind → metadata，向后兼容）
# ============================================================================


class TestEvidenceCollectorAudioKind:
    def test_collect_segment_carries_audio_kind_metadata(self) -> None:
        """audio_kind 写入 metadata（供 D6 Consumer 解析 audio_patterns）。"""
        ev = _audio_event(AudioPerceptionKind.AUDIO_DISTRESS_CRY, 1710000010.0)
        item = AudioEvidenceCollector().collect_segment(
            ev, "file:///seg.wav", audio_kind=ev.kind.value
        )
        assert item.metadata["audio_kind"] == AudioPerceptionKind.AUDIO_DISTRESS_CRY.value
        assert item.modality is EvidenceModality.AUDIO

    def test_collect_segment_without_audio_kind_backward_compatible(self) -> None:
        """不传 audio_kind → metadata 空（历史行为不变）。"""
        ev = _audio_event(AudioPerceptionKind.AUDIO_VOICE_RAISED, 1710000010.0)
        item = AudioEvidenceCollector().collect_segment(ev, "file:///seg.wav")
        assert item.metadata == {}

    def test_collect_clip_carries_audio_kind_metadata(self) -> None:
        ev = _audio_event(AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT, 1710000010.0)
        item = AudioEvidenceCollector().collect_clip(
            ev, "file:///clip.wav", audio_kind=ev.kind.value
        )
        assert item.metadata["audio_kind"] == (
            AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT.value
        )

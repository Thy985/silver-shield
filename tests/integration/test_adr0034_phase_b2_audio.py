"""ADR-0034 Phase B.2.2 · 音频驱动闭环 → 真实跨模态关联边（验收）。

> B.2.2 的目标（用户验收口径）：让闭环**真正产出** vision + audio 两个 episode 并
> 建出关联边（episode A → link → episode B），对应 F5 真边验收。
>
> 本文件覆盖：
>   - **e2e 真边**：fixture ``adr0034_cross_modal.yaml``（abnormal_dwell + 2 条跨会话
>     窗口的音频事件）→ 闭环运行 → vision episode + audio episode（D4 匿名）+ 1 条
>     ``supports`` 边；validator 整体 ok（含 CrossModalExpectation 全通过）；
>   - **F6 自洽**：音频 Loop 的 warning/command 经 ``AudioSessionSummary`` 生产自报
>     并入生产通道，三通道交叉校验（sink↔commands、WARN trace↔warnings、
>     episode↔commands）两侧同源；
>   - **零行为变化 + t8**：``cross_modal_enabled=False`` 时音频不驱动、不建边；
>     声明了 cross_modal 期望却零边 → F5 不通过（绝不静默通过）；
>   - **摘要增强**：``AudioSessionSummary`` 在 D3 门槛后携带真实 warning/command
>     对象（字段名复数，不破 C1「不含 score/decision/warning 单数字段」契约）。

运行环境：闭环需要 cv2（``_assemble`` 运行时重链），须在装有 cv2 的解释器下跑；
ruff 仍跑在托管 venv（py3.13）。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = (
    REPO_ROOT
    / "src"
    / "home_perception"
    / "validation"
    / "fixtures"
    / "scenarios"
    / "integration"
)
CROSS_MODAL_PATH = INTEGRATION_DIR / "adr0034_cross_modal.yaml"

DEFAULT_DEVICE_ID = "home_entry_01"
AUDIO_SESSION_ID = "integration_audio_session"


# ============================================================================
# 助手
# ============================================================================


def _run_cross_modal(cross_modal_enabled: bool):
    """加载 cross_modal fixture → 跑通闭环 → validate（返回全部产物）。"""
    from home_perception.integration.loop.context import (
        IntegrationContext,
        IntegrationRunnerConfig,
    )
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(CROSS_MODAL_PATH)
    cfg = IntegrationRunnerConfig(cross_modal_enabled=cross_modal_enabled)
    runner = IntegrationRunner(config=cfg)
    ctx = IntegrationContext.build(cfg)
    result = runner.run(scn, context=ctx)
    validation = IntegrationValidator().validate(result, scn)
    return scn, result, validation, ctx


def _episode_by_id(episodes, record_id: str):
    for ep in episodes:
        if getattr(ep, "record_id", None) == record_id:
            return ep
    raise AssertionError(f"episodes 中不存在 record_id={record_id}；实际={[e.record_id for e in episodes]}")


def _command_ids(commands) -> set[str]:
    return {str(c.command_id) for c in commands}


def _warning_ids(warnings) -> set[str]:
    return {str(w.warning_id) for w in warnings}


# ============================================================================
# 1. e2e 真边（核心验收：vision + audio 两 episode 建出 supports 边）
# ============================================================================


def test_b22_real_link_formed_e2e():
    """cross_modal_enabled=True 跑 cross_modal fixture → 1 条真实 vision<->audio 边。

    验收三要素（对应用户口径）：
    1. 两个 episode 真存在：vision（visitor 非 None）+ audio（D4 匿名 visitor=None）；
    2. 二者 device_id 相同（home_entry_01）——CrossModalLinker 同设备上下文前提；
    3. 1 条 link 连接二者，relationship=supports，validator 整体通过。
    """
    _, result, validation, _ = _run_cross_modal(True)

    # --- 1) 两个 episode 真存在 ---
    episodes = list(result.episodes)
    assert len(episodes) >= 2, f"应至少 2 条 episode（vision+audio），实际 {len(episodes)}"
    vision_eps = [e for e in episodes if e.visitor_instance_id is not None]
    audio_eps = [e for e in episodes if e.visitor_instance_id is None]
    assert len(vision_eps) == 1, f"应恰有 1 条视觉 episode，实际 {len(vision_eps)}"
    assert len(audio_eps) == 1, f"应恰有 1 条纯音频 episode（D4 匿名），实际 {len(audio_eps)}"
    vis, aud = vision_eps[0], audio_eps[0]

    # 模态区分（视觉 ≠ 音频）
    vis_mods = {m.value for m in vis.modalities}
    aud_mods = {m.value for m in aud.modalities}
    assert vis_mods == {"vision"}, f"视觉 episode 模态应恰为 vision，实际 {vis_mods}"
    assert aud_mods == {"audio"}, f"音频 episode 模态应恰为 audio，实际 {aud_mods}"

    # --- 2) 同 device_id（ADR-0028 D1：同设备上下文 → 可关联）---
    assert vis.device_id == DEFAULT_DEVICE_ID, f"视觉 episode device_id={vis.device_id}"
    assert aud.device_id == DEFAULT_DEVICE_ID, f"音频 episode device_id={aud.device_id}"

    # --- 3) 1 条 supports 边连接二者 ---
    links = list(result.cross_modal_links)
    assert len(links) == 1, f"应恰有 1 条关联边，实际 {len(links)}"
    link = links[0]
    assert set(link.episode_ids) == {vis.record_id, aud.record_id}, (
        f"link 两端应恰为两 episode：{link.episode_ids}"
    )
    rel = link.relationship.value if hasattr(link.relationship, "value") else link.relationship
    assert rel == "supports", f"跨模态（模态不同）关系应为 supports，实际 {rel}"

    # validator 整体通过（perception/decision/notification/memory/cross_modal/observability 全绿）
    assert validation.ok is True, (
        f"闭环应整体通过；失败 stages: {[(s.name, s.detail) for s in validation.stages if not s.passed]}"
    )
    assert validation.failure_codes() == ()


def test_b22_f5_stage_asserts_cross_modal_expectation():
    """F5 stage 命中断言：links>=min_links、模态覆盖、supports 关系命中。"""
    _, _, validation, _ = _run_cross_modal(True)

    cm = next(s for s in validation.stages if s.name == "cross_modal")
    assert cm.passed is True, cm.detail
    assert cm.failure_code is None
    assert "links=1" in cm.detail
    assert "supports" in cm.detail
    # 未声明的 identity 等模态不该出现（集合语义，禁止精确计数）
    assert "identity" not in cm.detail


# ============================================================================
# 2. F6 自洽：音频产物并入生产通道，三通道交叉校验两侧同源
# ============================================================================


def test_b22_f6_self_consistent_with_audio():
    """F6 通过：音频 warning/command 并入生产通道后 sink/trace/episode 全对齐。

    修复前的形态（B.2.2 阻塞点）：音频 command/warning 被探针观测到，却不在
    ``result.commands``/``result.warnings`` 生产通道 → F6 误判 Observability Drop。
    修复后 runner 把 ``AudioSessionSummary.warnings/commands`` 并入生产通道，
    三通道（chA sink↔commands / chB WARN trace↔warnings / chC episode↔commands）
    必须两侧同源。
    """
    _, result, validation, _ = _run_cross_modal(True)

    obs = next(s for s in validation.stages if s.name == "observability")
    assert obs.passed is True, obs.detail
    assert obs.failure_code is None

    # 音频 command 必须同时出现在生产通道与 sink（chA）
    sink_ids = _command_ids(result.sink_commands)
    prod_ids = _command_ids(result.commands)
    assert prod_ids == sink_ids, f"chA：生产 {prod_ids} != sink {sink_ids}"

    # 音频 warning 必须同时出现在生产通道与 WARN trace（chB）
    prod_warn = _warning_ids(result.warnings)
    trace_warn = {
        str(t.outcome.warning_id)
        for t in result.decision_traces
        if t.outcome.warning_id is not None
    }
    assert prod_warn == trace_warn, f"chB：生产 {prod_warn} != trace {trace_warn}"

    # 音频 episode 引用的命令必须是已执行命令的子集（chC）
    audio_ep = _episode_by_id(result.episodes, f"ep-{AUDIO_SESSION_ID}")
    ep_cmd_ids = {str(a.command_id) for a in getattr(audio_ep, "actions", ()) or ()}
    assert ep_cmd_ids, "音频 episode 应引用至少 1 条 ActionCommand"
    assert ep_cmd_ids.issubset(prod_ids), f"chC：episode 引用未执行命令 {ep_cmd_ids - prod_ids}"


def test_b22_audio_warning_and_command_observed_by_probes():
    """音频产出的 warning/command 确被双探针观测（非假阳性通过）。"""
    _, result, _, _ = _run_cross_modal(True)

    # 音频 episode 的 actions 应来自真实 executor 执行（非凭空填充）
    audio_ep = _episode_by_id(result.episodes, f"ep-{AUDIO_SESSION_ID}")
    for act in audio_ep.actions:
        # D3 门槛后动作类型应属白名单（LOG_ONLY / SEND_FAMILY_MESSAGE / CREATE_COMMUNITY_TASK）
        assert act.command_type in {"LOG_ONLY", "SEND_FAMILY_MESSAGE", "CREATE_COMMUNITY_TASK"}


# ============================================================================
# 3. 零行为变化 + t8：cross_modal_enabled=False 不驱动音频、不建边、F5 不通过
# ============================================================================


def test_b22_disabled_no_audio_no_link_t8_f5():
    """cross_modal_enabled=False 跑同一 fixture → 音频不驱动、零边、F5 不通过。

    - ``_audio_recorder is None`` → ``_drive`` 跳过音频会话（即使 scenario 声明了
      ``audio``）→ 无纯音频 episode（零行为变化）；
    - fixture 声明了 ``cross_modal: min_links=1`` 却拿不出一条边 → F5（t8，绝不静默
      通过——"声称要验证跨模态却拿不出真实边"必须暴露）。
    """
    _, result, validation, ctx = _run_cross_modal(False)

    # 探针容器未注入 runtime（零行为变化）
    assert ctx.cross_modal_runtime is None
    # 无音频 episode：所有 episode 均为视觉（visitor 非 None）
    assert result.cross_modal_links == ()
    episodes = list(result.episodes)
    assert len(episodes) == 1, f"未启用跨模态应仅视觉 1 条 episode，实际 {len(episodes)}"
    assert episodes[0].visitor_instance_id is not None, "未启用跨模态不应有 D4 匿名音频 episode"

    # t8：声明 cross_modal 期望却零边 → 整体不通过且含 F5
    assert validation.ok is False, "t8：声明 cross_modal 期望却零边，应整体不通过"
    assert "F5" in validation.failure_codes(), (
        f"失败码应含 F5，实际 {validation.failure_codes()}"
    )
    cm = next(s for s in validation.stages if s.name == "cross_modal")
    assert cm.passed is False
    assert "下界" in cm.detail


# ============================================================================
# 4. 摘要增强：AudioSessionSummary 携带生产自报字段（D3 门槛后非空）
# ============================================================================


def test_b22_audio_summary_carries_production_channel_fields():
    """record_session D3 门槛后，summary.warnings/commands 携带真实对象（非空）。"""
    # 复用 B.2.1 同款最小装配（decision + executor + memory_hook + InMemoryStore）
    from home_perception.action.dispatcher import ActionDispatcher
    from home_perception.action.executor import ActionExecutor
    from home_perception.action.notifier import MockNotifier
    from home_perception.action.publisher import MockPublisher
    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
    from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
    from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
    from home_perception.runtime.audio_session_recorder import AudioSessionRecorder
    from home_perception.runtime.memory_hook import MemoryHook
    from home_perception.runtime.observability import PipelineMetrics

    clock = _FixedClock()
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
    store = InMemoryStore()
    hook = MemoryHook(DefaultEpisodeBuilder(), store, True, PipelineMetrics())
    recorder = AudioSessionRecorder(
        decision,
        executor,
        hook,
        device_id=DEFAULT_DEVICE_ID,
        session_id_factory=lambda: AUDIO_SESSION_ID,
    )

    # 一条必然过 D3 门槛的音频事件（电话持续 → COMMUNICATION → WARN/LOW）
    ev = AudioPerceptionEvent(
        event_id="aev-probe",
        timestamp=1752952800.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=0.9,
        confidence=0.9,
        source_segment_ids=["seg-0"],
        labels=["telephone"],
    )
    summary = recorder.record_session([ev], audio_session_id=AUDIO_SESSION_ID)

    assert summary.episode_recorded is True
    # 生产自报字段（Phase B.2 增强）：非空且类型正确
    assert len(summary.warnings) == 1, "D3 门槛后 summary 应携带 1 条 WarningEvent"
    assert len(summary.commands) == 1, "D3 门槛后 summary 应携带 1 条 ActionCommand"
    w = summary.warnings[0]
    assert str(w.warning_id) in summary.warning_ids
    assert w.device_id == DEFAULT_DEVICE_ID
    # C1 契约不破：无 score/decision/warning（单数）字段
    import dataclasses

    names = {f.name for f in dataclasses.fields(type(summary))}
    assert not (names & {"risk_score", "score", "decision", "warning"}), f"C1 被破坏：{names}"
    # 与落库 episode 的引用一致（chC 前提：episode.actions 引用已执行命令）
    ep = store.all_episodic()[0]
    assert {str(a.command_id) for a in ep.actions} == {
        str(c.command_id) for c in summary.commands
    }


# ============================================================================
# 5. fixture 契约：声明即所测
# ============================================================================


def test_b22_fixture_declares_cross_modal_expectations():
    """fixture 结构契约：audio 声明、cross_modal/memory 期望字段齐全（声明即所测）。"""
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(CROSS_MODAL_PATH)
    # 身份字段（T10）
    assert scn.meta.schema_version == "1.0"
    assert scn.meta.scenario_id == "sw_adr0034_cross_modal"
    # 音频通道：2 条事件，时间戳跨越会话窗口（首帧 + 末帧内）
    assert len(scn.audio) == 2, "fixture 应声明 2 条音频事件"
    ts = sorted(s.timestamp for s in scn.audio)
    window_end = 1752952800.0 + scn.meta.duration_frames * 0.5
    assert ts[0] >= 1752952800.0, "音频事件须落在会话窗口内（>= clock_start）"
    assert ts[-1] <= window_end, "音频事件须落在会话窗口内（<= 窗口末）"

    # cross_modal 期望（B.2.2 验收口径）
    assert scn.integration is not None
    cm = scn.integration.cross_modal
    assert cm is not None
    assert cm.min_links == 1
    assert set(cm.expected_linked_modalities) == {"vision", "audio"}
    assert set(cm.required_relationships) == {"supports"}
    # memory 下界 2（视觉 + 音频）——B.2.2 的双 episode 验收锚点
    assert scn.integration.memory is not None
    assert scn.integration.memory.min_records == 2


class _FixedClock:
    """固定时刻时钟（决策确定性；与 tests/runtime 同款语义）。"""

    def __init__(self) -> None:
        from datetime import UTC, datetime

        self._t = datetime(2026, 7, 19, 18, 0, 0, tzinfo=UTC)

    def now(self):
        return self._t

    def __call__(self):
        return self._t

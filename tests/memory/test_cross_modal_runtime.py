"""CrossModalLinkRuntime 与 Synthetic Episode Fixture 测试（ADR-0028 D4/D5/D6）。

覆盖：
- **运行时建边**：episode 落库后（经 MemoryHook 注入 runtime）自动产出
  CrossModalLink；视觉/音频两路共用落库点 → 两路自动覆盖；
- **D6 Synthetic Fixture**：声明式 scenario（tests/fixtures/cross_modal_scenarios.yaml）
  → 翻译为 EpisodicRecord → CrossModalLinker → 断言 expected.link——
  **直接验证 Memory Graph**（Episode → Linker → Link），正例 + 4 负例对照；
- **D4 零行为变化**：MemoryHook 未注入 runtime → 落库行为与历史逐字段一致
  （无 runtime 调用、metrics 不变）；
- **D4 失败隔离 / metrics 边界**：runtime 失败仅日志，不计 metrics.errors；
- **D5 缩放边界**：episode ≥ 10_000 时降级跳过本轮建边（告警契约）；
- **C1**：CrossModalLink 是边索引（episode_ids + relationship），不含分数/判定字段。

铁律（AGENTS.md 测试有效性）：每个「建边」断言配「不建边」负例（同设备不重叠 /
异设备重叠 / 阈值未达 / 共享会话 id 异设备）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_link import (
    CrossModalLink,
    CrossModalLinker,
    CrossModalLinkStore,
    CrossModalRelationship,
)
from home_perception.memory.cross_modal_runtime import (
    CROSS_MODAL_SCALE_THRESHOLD,
    CrossModalLinkRuntime,
)
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore
from home_perception.runtime.memory_hook import MemoryHook
from home_perception.runtime.observability import PipelineMetrics

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_SCENARIO_FILE = _FIXTURES / "cross_modal_scenarios.yaml"
_BASE_TS = datetime(2026, 8, 6, 10, 0, 0, tzinfo=UTC)


def _mk_episode(
    rid: str,
    *,
    visitor: str | None,
    audio_session: str | None,
    device: str | None,
    start_s: float,
    end_s: float,
    modalities: list[EvidenceModality],
) -> EpisodicRecord:
    enter = _BASE_TS + timedelta(seconds=start_s)
    leave = _BASE_TS + timedelta(seconds=end_s)
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=visitor,
        audio_session_id=audio_session,
        device_id=device,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=(leave - enter).total_seconds(),
        source_event_ids=[f"{rid}-src"],
        summary=f"scenario episode {rid}",
        model_version="scenario-v1",
        modalities=modalities,
    )


def _scenario_to_episodes(sc: dict) -> list[EpisodicRecord]:
    """把声明式 scenario 翻译为 EpisodicRecord（确定性，无随机；不生成媒体）。"""
    device = sc["device_id"]
    episodes: list[EpisodicRecord] = []
    for i, v in enumerate(sc.get("vision", [])):
        episodes.append(
            _mk_episode(
                f"ep-{sc['name']}-v{i}",
                visitor=v["visitor"],
                audio_session=None,
                device=device,
                start_s=v["start"],
                end_s=v["end"],
                modalities=[EvidenceModality.VISION],
            )
        )
    for i, a in enumerate(sc.get("audio", [])):
        audio_device = sc.get("audio_device_id", device)
        episodes.append(
            _mk_episode(
                f"ep-{sc['name']}-a{i}",
                visitor=None,  # 纯音频匿名（D4）
                audio_session=a.get("audio_session", f"{sc['name']}-session-{i}"),
                device=audio_device,
                start_s=a["start"],
                end_s=a["end"],
                modalities=[EvidenceModality.AUDIO],
            )
        )
    return episodes


def _load_scenarios() -> list[dict]:
    raw = yaml.safe_load(_SCENARIO_FILE.read_text(encoding="utf-8"))
    return raw["scenarios"]


# ============================================================================
# 1. D6 Synthetic Fixture —— 直接验证 Memory Graph（Episode → Linker → Link）
# ============================================================================


class TestSyntheticScenarioFixture:
    @pytest.mark.parametrize("scenario", _load_scenarios(), ids=lambda s: s["name"])
    def test_scenario_link_expectation(self, scenario: dict) -> None:
        """每个 scenario 的 expected.link 必须与 linker 实际产出一致（正例 + 负例）。"""
        episodes = _scenario_to_episodes(scenario)
        expected = scenario["expected"]
        min_overlap = float(expected.get("min_overlap_seconds", 0.0))
        links = CrossModalLinker(min_overlap_seconds=min_overlap).link(episodes)

        if expected["link"]:
            assert len(links) == 1, f"{scenario['name']} 应建恰好 1 条边，实际 {links}"
            assert links[0].relationship == CrossModalRelationship(
                expected["relationship"]
            )
        else:
            assert links == [], f"{scenario['name']} 不应建边，实际 {links}"

    def test_fixture_has_positive_and_negative_cases(self) -> None:
        """fixture 清单完整：≥ 1 正例 + 4 负例（ADR-0028 D6 验收）。"""
        scenarios = _load_scenarios()
        positives = [s for s in scenarios if s["expected"]["link"]]
        negatives = [s for s in scenarios if not s["expected"]["link"]]
        assert len(positives) >= 1
        assert len(negatives) >= 3
        names = {s["name"] for s in negatives}
        assert "vision_audio_session_overlap_different_device" in names  # D2 安全网


# ============================================================================
# 2. CrossModalLinkRuntime（D4）—— 落库后自动建边
# ============================================================================


class TestCrossModalLinkRuntime:
    def test_on_episode_recorded_builds_link(self) -> None:
        """store 已有视觉 episode；落库音频 episode 后 → 同设备重叠 → 建 SUPPORTS 边。"""
        store = InMemoryStore()
        vision = _mk_episode(
            "ep-vision", visitor="V1", audio_session=None, device="dev-001",
            start_s=10, end_s=20, modalities=[EvidenceModality.VISION],
        )
        store.upsert_episodic(vision)
        link_store = CrossModalLinkStore()
        runtime = CrossModalLinkRuntime(store, link_store)

        audio = _mk_episode(
            "ep-audio", visitor=None, audio_session="sess-1", device="dev-001",
            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO],
        )
        store.upsert_episodic(audio)
        written = runtime.on_episode_recorded(audio)

        assert len(written) == 1
        assert written[0].relationship == CrossModalRelationship.SUPPORTS
        assert written[0].episode_ids == ["ep-audio", "ep-vision"]

    def test_idempotent_no_duplicate_links(self) -> None:
        """同输入两次 on_episode_recorded → 第二次不重复建边（link_id 幂等）。"""
        store = InMemoryStore()
        link_store = CrossModalLinkStore()
        runtime = CrossModalLinkRuntime(store, link_store)
        for ep in [
            _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                        start_s=10, end_s=20, modalities=[EvidenceModality.VISION]),
            _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                        start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO]),
        ]:
            store.upsert_episodic(ep)
        first = runtime.on_episode_recorded(store.all_episodic()[-1])
        second = runtime.on_episode_recorded(store.all_episodic()[-1])
        assert len(first) == 1
        assert second == []  # 幂等：同内容 add 返回 False，不重复写
        assert runtime.link_count == 1

    def test_disabled_is_noop(self) -> None:
        store = InMemoryStore()
        runtime = CrossModalLinkRuntime(store, CrossModalLinkStore(), enabled=False)
        assert runtime.on_episode_recorded(
            _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                        start_s=0, end_s=5, modalities=[EvidenceModality.AUDIO])
        ) == []

    def test_scale_threshold_degrades(self, monkeypatch) -> None:
        """D5 Performance Boundary：episode ≥ 10_000 → 降级跳过本轮建边（告警契约）。"""
        store = InMemoryStore()
        runtime = CrossModalLinkRuntime(store, CrossModalLinkStore())

        def fake_all() -> list[EpisodicRecord]:
            return [None] * CROSS_MODAL_SCALE_THRESHOLD  # type: ignore[list-item]

        monkeypatch.setattr(store, "all_episodic", fake_all)
        assert runtime.on_episode_recorded(
            _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                        start_s=0, end_s=5, modalities=[EvidenceModality.AUDIO])
        ) == []

    def test_linker_failure_isolated(self, monkeypatch) -> None:
        """D4 失败隔离：linker 异常 → 返回空列表，不向上抛。"""
        store = InMemoryStore()
        runtime = CrossModalLinkRuntime(store, CrossModalLinkStore())

        def boom(episodes):
            raise RuntimeError("linker down")

        monkeypatch.setattr(runtime._linker, "link", boom)
        assert runtime.on_episode_recorded(
            _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                        start_s=0, end_s=5, modalities=[EvidenceModality.AUDIO])
        ) == []

    def test_c1_link_has_no_score_fields(self) -> None:
        """C1：CrossModalLink 不含分数/判定字段（边索引，非决策）。"""
        import dataclasses

        names = {f.name for f in dataclasses.fields(CrossModalLink)}
        assert not (names & {"risk_score", "score", "decision", "warning"})


# ============================================================================
# 3. MemoryHook 注入（D4）—— 落库后触发 runtime；零行为变化锚点
# ============================================================================


def _audio_evidence(eid: str, ts: float) -> object:
    from home_perception.core.event import EvidenceItem, RetentionTier

    return EvidenceItem(
        evidence_id=eid,
        modality=EvidenceModality.AUDIO,
        kind="audio_segment",
        uri=f"file:///{eid}.wav",
        captured_at=_BASE_TS + timedelta(seconds=ts),
        metadata={"audio_kind": "impact"},
        retention_tier=RetentionTier.SHORT,
    )


class TestMemoryHookInjection:
    def test_record_triggers_cross_modal_link(self) -> None:
        """D4 集成：store 预置视觉 episode；MemoryHook.record 落库纯音频 episode
        （经 DefaultEpisodeBuilder）→ 自动触发 runtime → 同设备重叠建 SUPPORTS 边。"""
        from home_perception.memory import DefaultEpisodeBuilder

        store = InMemoryStore()
        store.upsert_episodic(
            _mk_episode("ep-vision", visitor="V1", audio_session=None, device="dev-001",
                        start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        )
        link_store = CrossModalLinkStore()
        metrics = PipelineMetrics()
        hook = MemoryHook(
            DefaultEpisodeBuilder(), store, True, metrics,
            cross_modal_runtime=CrossModalLinkRuntime(store, link_store),
        )
        hook.record(
            None,
            [],
            [],
            evidence=[_audio_evidence("ev-a1", 12), _audio_evidence("ev-a1b", 15)],
            audio_session_id="sess-1",
            device_id="dev-001",
        )
        # 落库成功 + 自动建边（纯音频窗口 12-15 与视觉 10-20 重叠 12-15）
        assert metrics.episodes_recorded == 1
        assert link_store.link_count() == 1
        link = link_store.all_links()[0]
        assert link.relationship == CrossModalRelationship.SUPPORTS
        assert set(link.episode_ids) == {"ep-vision", "ep-sess-1"}

    def test_runtime_failure_not_counted_in_metrics(self, monkeypatch) -> None:
        """D4 review：runtime 建边失败 → 仅日志，不计入 metrics.errors；
        落库本身成功（episodes_recorded=1），errors 保持 0。"""
        from home_perception.memory import DefaultEpisodeBuilder

        store = InMemoryStore()
        link_store = CrossModalLinkStore()
        metrics = PipelineMetrics()
        runtime = CrossModalLinkRuntime(store, link_store)

        def boom(record):
            raise RuntimeError("runtime down")

        monkeypatch.setattr(runtime, "on_episode_recorded", boom)
        hook = MemoryHook(
            DefaultEpisodeBuilder(), store, True, metrics, cross_modal_runtime=runtime
        )
        hook.record(
            None,
            [],
            [],
            evidence=[_audio_evidence("ev-a2", 5)],
            audio_session_id="sess-2",
            device_id="dev-002",
        )
        assert metrics.episodes_recorded == 1  # 落库成功（主链路不受影响）
        assert metrics.errors == 0  # runtime 失败不计 errors（D4 review）

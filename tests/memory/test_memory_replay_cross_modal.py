"""Cross-Modal Link Replay Baseline（ADR-0027 Slice C · D5 关联器输出锁定）。

> `test_memory_replay.py` 守护 3 个纯视觉 visitor 的回放一致性；
> `test_memory_replay_audio.py` 守护含音频 episode 的投影；本文件进一步锁定
> **跨模态关联器输出**：把"复合(VISION+AUDIO) + 纯音频(AUDIO) 共享 audio_session_id
> 且时间窗重叠"的场景投影为 episode 后，经 ``CrossModalLinker`` 关联应稳定产出
> **恰好 1 条 SUPPORTS 关联边**，且跨运行 / 跨乱序复现（§6.7 跨 Stage 一致性硬约束）。
>
> 与既有 baseline 完全隔离：独立 fixture ``tests/fixtures/memory_baseline_cross_modal.json``、
> 独立事件日志；**不改动** 3-visitor 视觉基线 / 音频基线（避免重构与功能混同）。
>
> 固定 ID 约定：``event_id`` / ``warning_id`` / ``command_id`` / ``evidence_id`` /
> ``audio_session_id`` 全部显式固定，保证关联结果与 link_id 跨运行复现。
>
> 注：``CrossModalLink.created_at`` 由两 episode 离场时刻 max 确定性派生（非墙钟），
> 故无需像 episode baseline 那样归一 ``created_at`` 到 1970；关联结果天然回放稳定。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from itertools import permutations
from uuid import UUID

from home_perception.action.command import ActionCommand
from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.core.event import EvidenceItem, EvidenceModality, RetentionTier
from home_perception.memory.cross_modal_link import CrossModalLinker, CrossModalLinkStore
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.store import InMemoryStore


# ---------------------------------------------------------------------------
# 固定 ID 与夹具
# ---------------------------------------------------------------------------
def _utc(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _vid(hex8: str) -> UUID:
    return UUID(hex8 + "0" * (32 - len(hex8)))


def _make_audio_evidence(audio_kind, evidence_id, captured_at):
    return EvidenceItem(
        evidence_id=evidence_id,
        modality=EvidenceModality.AUDIO,
        kind="audio_clip",
        uri=f"data/evidence/{evidence_id}.wav",
        captured_at=captured_at,
        metadata={"audio_kind": audio_kind, "audio_score": 0.9},
        retention_tier=RetentionTier.SHORT,
    )


def _make_visitor(visitor_id: UUID, event_id: str, enter, leave, duration=900.0):
    return VisitorEvent(
        visitor_id=visitor_id,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=duration,
        event_id=event_id,
    )


def _make_warning(visitor_id, warning_id: UUID, risk_level, rec_action, reasons, created_at):
    trigger = {
        "event_id": f"{visitor_id}:abnormal_audio",
        "event_type": "abnormal_audio",
        "score": 0.9,
        "timestamp": created_at.isoformat(),
    }
    return WarningEvent(
        elder_id="elder-001",
        device_id="dev-001",
        risk_level=risk_level,
        recommended_action=rec_action,
        trigger_events=[trigger],
        reason_summary=reasons,
        warning_id=warning_id,
        created_at=created_at,
    )


def _make_action(command_type, warning_id: UUID, command_id: UUID, status="DONE"):
    return ActionCommand(
        command_type=command_type,
        warning_id=warning_id,
        payload={},
        command_id=command_id,
        status=status,
    )


def _build_cross_modal_event_log():
    """确定性跨模态事件日志：复合(VISION+AUDIO) + 纯音频(AUDIO) 共享 audio_session_id 且时间重叠。

    返回 list of (visitor_event, [warnings], [actions], [evidence], audio_session_id)。
    - 复合：视觉访客 dddd + audio_session "xcm"，18:30–18:45，telephone 证据 18:41
    - 纯音频：无视觉访客，audio_session "xcm"（同），crying 18:38 + 警告 18:49
      → 两 episode 共享 audio_session 且窗口重叠（18:38–18:45）→ 1 条 SUPPORTS 关联
    固定 ID 保证 link_id / confidence / created_at 跨运行复现。
    """
    # 复合
    vc = _vid("cccccccc1111")
    wc = _vid("cccccccc2222")
    ac = _vid("cccccccc3333")
    visitor_c = _make_visitor(vc, "ev-cmp-xcm", _utc(2026, 7, 28, 18, 30), _utc(2026, 7, 28, 18, 45))
    warn_c = _make_warning(vc, wc, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 18, 40))
    act_c = _make_action("SEND_FAMILY_MESSAGE", wc, ac)
    ev_c = _make_audio_evidence("telephone", "ev-cmp-xcm", _utc(2026, 7, 28, 18, 41))

    # 纯音频（共享 audio_session "xcm"）
    wp = _vid("aaaaaaaa2222")
    ev_p = _make_audio_evidence("crying", "ev-pure-xcm", _utc(2026, 7, 28, 18, 38))
    warn_p = _make_warning("audio_subject", wp, "MEDIUM", "MONITOR", ["异常通话"], _utc(2026, 7, 28, 18, 49))

    return [
        (visitor_c, [warn_c], [act_c], [ev_c], "xcm"),
        (None, [warn_p], [], [ev_p], "xcm"),
    ]


def _run_and_link(log):
    """投影 episode → 关联 → 返回 (episodes, links) 确定性结果。"""
    builder = DefaultEpisodeBuilder()
    store = InMemoryStore()
    episode_ids: set[str] = set()
    for visitor, warnings, actions, evidence, audio_session_id in log:
        rec = builder.project_episode(
            visitor,
            warnings=warnings,
            actions=actions,
            evidence=evidence,
            audio_session_id=audio_session_id,
        )
        if rec is not None:
            store.upsert_episodic(rec)
            episode_ids.add(rec.record_id)
    episodes = store.get_active_episodic()
    links = CrossModalLinker().link(episodes)
    link_store = CrossModalLinkStore()
    for lk in links:
        link_store.add(lk, known_episode_ids=episode_ids)
    return episodes, link_store.all_links()


def _baseline_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "fixtures", "memory_baseline_cross_modal.json")


def _load_or_write_baseline(log):
    """读取 baseline；缺失或 MEMORY_UPDATE_BASELINE=1 时生成并写回（同音频基线约定）。"""
    _, links = _run_and_link(log)
    if os.environ.get("MEMORY_UPDATE_BASELINE") == "1" or not os.path.exists(_baseline_path()):
        payload = [lk.to_dict() for lk in links]
        with open(_baseline_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return links
    with open(_baseline_path(), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    from home_perception.memory.cross_modal_link import CrossModalLink

    return [CrossModalLink.from_dict(d) for d in data]


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_cross_modal_replay_baseline_snapshot_match():
    """跨模态关联产出与 tests/fixtures/memory_baseline_cross_modal.json 深度相等。"""
    log = _build_cross_modal_event_log()
    _, actual = _run_and_link(log)
    baseline = _load_or_write_baseline(log)
    assert [l.link_id for l in actual] == [l.link_id for l in baseline]
    for a, b in zip(actual, baseline):
        assert a.link_id == b.link_id
        assert a.episode_ids == b.episode_ids
        assert a.relationship == b.relationship
        assert a.confidence == b.confidence
        assert a.time_overlap == b.time_overlap
        assert a.supporting_evidence_ids == b.supporting_evidence_ids
        assert a.created_at == b.created_at


def test_cross_modal_replay_exactly_one_support_link():
    """场景应稳定产出恰好 1 条 SUPPORTS 关联（复合↔纯音频跨模态支撑）。"""
    log = _build_cross_modal_event_log()
    _, links = _run_and_link(log)
    assert len(links) == 1
    assert links[0].relationship.value == "supports"
    assert set(links[0].episode_ids) == {"ep-ev-cmp-xcm", "ep-xcm"}


def test_cross_modal_replay_idempotent_no_duplicate_links():
    """关联 3 次，link 集合不变（link_id 确定性 → 幂等）。"""
    log = _build_cross_modal_event_log()
    _, links_once = _run_and_link(log)
    _, links_twice = _run_and_link(log)
    _, links_thrice = _run_and_link(log)
    assert [l.link_id for l in links_once] == [l.link_id for l in links_twice] == [
        l.link_id for l in links_thrice
    ]


def test_cross_modal_replay_same_log_produces_same_links():
    """同一事件流关联 2 次，两次 CrossModalLink 字段级深度相等。"""
    log = _build_cross_modal_event_log()
    _, links1 = _run_and_link(log)
    _, links2 = _run_and_link(log)
    assert [l.link_id for l in links1] == [l.link_id for l in links2]
    for a, b in zip(links1, links2):
        assert a.to_dict() == b.to_dict()


def test_cross_modal_replay_order_independent():
    """复合 / 纯音频事件乱序到达，关联结果一致（全排列等价）。"""
    composite, pure = _build_cross_modal_event_log()
    sequential = [composite, pure]
    _, base = _run_and_link(sequential)
    base_ids = [l.link_id for l in base]

    for shuffled in permutations([composite, pure]):
        _, got = _run_and_link(list(shuffled))
        assert [l.link_id for l in got] == base_ids
        for a, b in zip(base, got):
            assert a.to_dict() == b.to_dict()

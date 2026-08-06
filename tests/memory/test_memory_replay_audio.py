"""Audio Memory Replay Baseline（ADR-0027 Slice E：含音频的回放 baseline）。

> `test_memory_replay.py` 守护的是 3 个**纯视觉** visitor 的回放一致性；本文件
> 补充 **含音频** 的回放基线，使 EpisodeBuilder 的音频投影（Slice B）也被 §6.7
> 跨 Stage 一致性硬约束覆盖：
>   - 复合 episode：视觉访客 + AUDIO 证据（telephone）→ modalities=[VISION,AUDIO]
>   - 纯音频 episode：无视觉访客，仅 audio_session（D4 匿名）
>
> 与 `test_memory_replay.py` 完全隔离：独立 baseline 文件
> `tests/fixtures/memory_baseline_audio.json`、独立事件日志，**不改动**既有的 3-visitor
> 视觉 baseline 及其 7 个测试（避免重构与功能混同）。
>
> 固定 ID 约定同 `test_memory_replay.py`：`event_id` / `warning_id` / `command_id` /
> `evidence_id` / `audio_session_id` 全部显式固定，保证回放结果跨运行复现。
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
from home_perception.memory.episode_builder import DefaultEpisodeBuilder
from home_perception.memory.records import EpisodicRecord, records_equal
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


def _make_visitor(visitor_id: UUID, event_id: str, enter, leave, duration=180.0):
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


def _build_audio_event_log():
    """确定性音频事件日志：1 复合（视觉访客 + 音频）+ 1 纯音频会话。

    返回 list of (visitor_event, [warnings], [actions], [evidence], audio_session_id)。
    visitor_event 为 None 表示纯音频 episode（D4）；audio_session_id 与之对应。
    """
    # 复合：视觉访客 + 音频 evidence（telephone 长时间通话）
    vd = _vid("dddddddd1111")
    wd = _vid("dddddddd2222")
    ad1 = _vid("dddddddd3333")
    visitor_d = _make_visitor(
        vd, "ev-visit-audio-d", _utc(2026, 7, 28, 18, 32), _utc(2026, 7, 28, 18, 45)
    )
    warn_d = _make_warning(
        vd, wd, "HIGH", "NOTIFY_FAMILY", ["异常停留"], _utc(2026, 7, 28, 18, 40)
    )
    act_d = _make_action("SEND_FAMILY_MESSAGE", wd, ad1)
    ev_d = _make_audio_evidence("telephone", "ev-audio-d", _utc(2026, 7, 28, 18, 41))

    # 纯音频：无视觉访客，仅 audio_session（crying 哭腔）
    we = _vid("eeeeeeee2222")
    warn_e = _make_warning(
        "audio_subject", we, "MEDIUM", "MONITOR", ["异常通话"], _utc(2026, 7, 28, 23, 5)
    )
    ev_e = _make_audio_evidence("crying", "ev-audio-e", _utc(2026, 7, 28, 23, 4))

    return [
        (visitor_d, [warn_d], [act_d], [ev_d], "audio_session_d"),
        (None, [warn_e], [], [ev_e], "audio_session_002"),
    ]


def _run_audio_replay(store: InMemoryStore, log) -> None:
    """把音频事件日志投影为 EpisodicRecord 并 upsert 进 store（确定性，无随机 id）。"""
    builder = DefaultEpisodeBuilder()
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


def _baseline_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "fixtures", "memory_baseline_audio.json")


def _load_or_write_baseline(log):
    """读取 baseline；缺失或 MEMORY_UPDATE_BASELINE=1 时生成并写回。

    同 `test_memory_replay._load_or_write_baseline`：CI 永远走「读取 + 比对」分支；
    改算法时显式 `MEMORY_UPDATE_BASELINE=1` 重生成并逐行 diff（§6.7.4 回归硬约束）。
    """
    store = InMemoryStore()
    _run_audio_replay(store, log)
    expected = store.get_active_episodic()
    expected.sort(key=lambda r: r.record_id)

    if os.environ.get("MEMORY_UPDATE_BASELINE") == "1" or not os.path.exists(_baseline_path()):
        payload = [r.to_dict() for r in expected]
        for d in payload:
            d["created_at"] = "1970-01-01T00:00:00+00:00"  # 归一墙钟，避免噪音 diff
        with open(_baseline_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return [EpisodicRecord.from_dict(d) for d in payload]

    with open(_baseline_path(), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [EpisodicRecord.from_dict(d) for d in data]


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_audio_replay_baseline_snapshot_match():
    """含音频回放产出与 tests/fixtures/memory_baseline_audio.json 深度相等。"""
    log = _build_audio_event_log()
    store = InMemoryStore()
    _run_audio_replay(store, log)
    actual = sorted(store.get_active_episodic(), key=lambda r: r.record_id)

    baseline = sorted(_load_or_write_baseline(log), key=lambda r: r.record_id)

    assert [r.record_id for r in actual] == [r.record_id for r in baseline]
    for a, b in zip(actual, baseline):
        assert records_equal(a, b), f"audio baseline 比对失败: {a.record_id}"


def test_audio_replay_idempotent_no_duplicate_records():
    """音频回放 3 次，record_count 不变（I1 幂等）；复合与纯音频各 1 条。"""
    log = _build_audio_event_log()
    store = InMemoryStore()
    _run_audio_replay(store, log)
    count_once = len(store.get_active_episodic())
    _run_audio_replay(store, log)
    _run_audio_replay(store, log)
    assert len(store.get_active_episodic()) == count_once
    assert count_once == len(log)


def test_audio_replay_same_log_produces_same_memory():
    """同一音频事件流回放 2 次，两个 store 的 EpisodicRecord 字段级深度相等。"""
    log = _build_audio_event_log()
    store1, store2 = InMemoryStore(), InMemoryStore()
    _run_audio_replay(store1, log)
    _run_audio_replay(store2, log)
    r1 = sorted(store1.get_active_episodic(), key=lambda r: r.record_id)
    r2 = sorted(store2.get_active_episodic(), key=lambda r: r.record_id)
    assert [r.record_id for r in r1] == [r.record_id for r in r2]
    for a, b in zip(r1, r2):
        assert records_equal(a, b), f"音频回放产出不一致: {a.record_id}"


def test_audio_replay_order_independent_for_disjoint_sessions():
    """不相关音频会话（复合 / 纯音频）事件乱序到达，各自投影不受影响（全排列等价）。"""
    composite, pure = _build_audio_event_log()
    sequential = [composite, pure]

    store_seq = InMemoryStore()
    _run_audio_replay(store_seq, sequential)
    rs = sorted(store_seq.get_active_episodic(), key=lambda r: r.record_id)

    for shuffled in permutations([composite, pure]):
        order = [a[-1] for a in shuffled]  # audio_session_id 列表
        store_shuf = InMemoryStore()
        _run_audio_replay(store_shuf, list(shuffled))
        rx = sorted(store_shuf.get_active_episodic(), key=lambda r: r.record_id)
        assert [r.record_id for r in rs] == [r.record_id for r in rx], f"顺序 {order} 产出集合不同"
        for s, x in zip(rs, rx):
            assert records_equal(s, x), f"顺序 {order} 下 {s.record_id} 产出不一致"

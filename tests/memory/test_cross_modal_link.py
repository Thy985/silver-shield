"""CrossModalLink 契约 / 存储 / 关联器测试（ADR-0027 Slice C · D5）。

> 覆盖：
> - 数据模型不变式（episode_ids ≥2 / 去重 / 非空；relationship 枚举闭合；confidence 范围；
>   time_overlap 类型与顺序；created_at UTC；supporting_evidence_ids 去重）
> - 序列化往返 + 字段闭合契约
> - CrossModalLinkStore：增 / 查 / 快照恢复 / 幂等 / 单调性冲突
> - **D5 悬空引用（§6.1，关闭 Slice E 延迟项）**：未知 episode_id / evidence_id → 拒绝
> - CrossModalLinker：同访客重叠→CO_OCCURS；共享 audio_session_id 跨模态→SUPPORTS；
>   不重叠→无链接；不同主体→无链接；link_id 确定性 + 幂等；纯音频经 audio_session 关联
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_link import (
    CROSS_MODAL_LINK_DICT_KEYS,
    CROSS_MODAL_RELATIONSHIP_VALUES,
    CrossModalLink,
    CrossModalLinker,
    CrossModalLinkStore,
    CrossModalRelationship,
    DanglingReferenceError,
)
from home_perception.memory.records import EpisodicRecord


# ---------------------------------------------------------------------------
# 夹具：直接构造 EpisodicRecord（不依赖完整事件链，聚焦关联语义）
# ---------------------------------------------------------------------------
def _mk_episode(rid, vid, asid, enter, leave, mods, dur=None):
    return EpisodicRecord(
        record_id=rid,
        visitor_instance_id=vid,
        audio_session_id=asid,
        enter_time=enter,
        leave_time=leave,
        duration_seconds=dur if dur is not None else (leave - enter).total_seconds(),
        source_event_ids=[f"{rid}-src"],
        summary="test episode",
        model_version="ep-builder-v1",
        modalities=mods,
    )


def _dt(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _link(episode_ids, rel=CrossModalRelationship.SUPPORTS, conf=0.5, overlap=None, ev=()):
    return CrossModalLink(
        link_id="link-" + "-".join(episode_ids),
        episode_ids=list(episode_ids),
        relationship=rel,
        time_overlap=overlap,
        confidence=conf,
        created_at=_dt(2026, 7, 28, 18, 46),
        supporting_evidence_ids=list(ev),
    )


# ===========================================================================
# 1) 数据模型不变式
# ===========================================================================
class TestCrossModalLinkModel:
    def test_relationship_enum_closure(self):
        """D5/D7 风格：relationship 值集恰为 {co_occurs, supports}，白名单不漂移。"""
        assert set(CROSS_MODAL_RELATIONSHIP_VALUES) == {"co_occurs", "supports"}
        assert CrossModalRelationship("co_occurs") == CrossModalRelationship.CO_OCCURS
        assert CrossModalRelationship("supports") == CrossModalRelationship.SUPPORTS

    def test_episode_ids_at_least_two(self):
        with pytest.raises(ValueError):
            _link(["ep-only-one"])

    def test_episode_ids_no_duplicates(self):
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-a"])

    def test_episode_ids_nonempty_str(self):
        with pytest.raises(ValueError):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", ""],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=None,
                confidence=0.5,
                created_at=_dt(2026, 7, 28, 18, 46),
            )
        with pytest.raises(ValueError):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", 123],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=None,
                confidence=0.5,
                created_at=_dt(2026, 7, 28, 18, 46),
            )

    def test_relationship_must_be_enum(self):
        with pytest.raises(TypeError):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", "ep-b"],
                relationship="supports",  # 自由文本，必须拒绝
                time_overlap=None,
                confidence=0.5,
                created_at=_dt(2026, 7, 28, 18, 46),
            )

    def test_time_overlap_none_ok(self):
        lk = _link(["ep-a", "ep-b"], overlap=None)
        assert lk.time_overlap is None

    def test_time_overlap_type_and_order(self):
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], overlap=(_dt(2026, 7, 28, 18, 45), _dt(2026, 7, 28, 18, 38)))
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], overlap=(_dt(2026, 7, 28, 18, 38),))  # 长度不足
        # 非 UTC 必须拒绝
        with pytest.raises(ValueError):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", "ep-b"],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=(datetime(2026, 7, 28, 18, 38), datetime(2026, 7, 28, 18, 45)),  # noqa: DTZ001
                confidence=0.5,
                created_at=_dt(2026, 7, 28, 18, 46),
            )

    def test_confidence_range(self):
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], conf=1.5)
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], conf=-0.1)
        # NaN 必须拒绝（isfinite 前置）
        with pytest.raises((ValueError, TypeError)):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", "ep-b"],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=None,
                confidence=float("nan"),
                created_at=_dt(2026, 7, 28, 18, 46),
            )
        # 边界 0 与 1 合法
        assert _link(["ep-a", "ep-b"], conf=0.0).confidence == 0.0
        assert _link(["ep-a", "ep-b"], conf=1.0).confidence == 1.0

    def test_created_at_must_be_utc(self):
        with pytest.raises(ValueError):
            CrossModalLink(
                link_id="link-x",
                episode_ids=["ep-a", "ep-b"],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=None,
                confidence=0.5,
                created_at=datetime(2026, 7, 28, 18, 46),  # noqa: DTZ001  # naive，刻意测试 UTC 校验
            )

    def test_supporting_evidence_ids_unique(self):
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], ev=("ev-1", "ev-1"))
        with pytest.raises(ValueError):
            _link(["ep-a", "ep-b"], ev=("ev-1", ""))

    def test_link_id_nonempty(self):
        with pytest.raises(ValueError):
            CrossModalLink(
                link_id="",
                episode_ids=["ep-a", "ep-b"],
                relationship=CrossModalRelationship.SUPPORTS,
                time_overlap=None,
                confidence=0.5,
                created_at=_dt(2026, 7, 28, 18, 46),
            )

    def test_to_dict_from_dict_roundtrip(self):
        lk = _link(
            ["ep-a", "ep-b"],
            rel=CrossModalRelationship.SUPPORTS,
            conf=0.875,
            overlap=(_dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 45)),
            ev=("ev-1", "ev-2"),
        )
        d = lk.to_dict()
        restored = CrossModalLink.from_dict(d)
        assert restored.link_id == lk.link_id
        assert restored.episode_ids == lk.episode_ids
        assert restored.relationship == lk.relationship
        assert restored.confidence == lk.confidence
        assert restored.supporting_evidence_ids == lk.supporting_evidence_ids
        assert restored.time_overlap == lk.time_overlap
        assert restored.created_at == lk.created_at

    def test_time_overlap_none_roundtrip(self):
        lk = _link(["ep-a", "ep-b"], overlap=None)
        assert CrossModalLink.from_dict(lk.to_dict()).time_overlap is None

    def test_dict_keys_closure(self):
        """to_dict 产出字段集合恒等于 CROSS_MODAL_LINK_DICT_KEYS（闭合契约）。"""
        lk = _link(
            ["ep-a", "ep-b"],
            overlap=(_dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 45)),
            ev=("ev-1",),
        )
        assert set(lk.to_dict().keys()) == set(CROSS_MODAL_LINK_DICT_KEYS)


# ===========================================================================
# 2) CrossModalLinkStore
# ===========================================================================
class TestCrossModalLinkStore:
    def test_add_and_get_by_episode(self):
        store = CrossModalLinkStore()
        lk = _link(["ep-a", "ep-b"])
        is_new, got = store.add(lk, known_episode_ids={"ep-a", "ep-b"})
        assert is_new is True
        assert got is lk
        assert store.link_count() == 1
        assert [l.link_id for l in store.get_links_by_episode("ep-a")] == ["link-ep-a-ep-b"]
        assert [l.link_id for l in store.get_links_by_episode("ep-b")] == ["link-ep-a-ep-b"]
        assert store.get_links_by_episode("ep-unknown") == []

    def test_idempotent_same_link(self):
        store = CrossModalLinkStore()
        lk = _link(["ep-a", "ep-b"])
        store.add(lk, known_episode_ids={"ep-a", "ep-b"})
        is_new, got = store.add(lk, known_episode_ids={"ep-a", "ep-b"})
        assert is_new is False
        assert got is lk
        assert store.link_count() == 1

    def test_monotonicity_conflict_raises(self):
        store = CrossModalLinkStore()
        lk1 = _link(["ep-a", "ep-b"], conf=0.5)
        store.add(lk1, known_episode_ids={"ep-a", "ep-b"})
        lk2 = _link(["ep-a", "ep-b"], conf=0.9)  # 同 link_id，内容不同
        with pytest.raises(ValueError):
            store.add(lk2, known_episode_ids={"ep-a", "ep-b"})

    def test_dangling_episode_id_rejected(self):
        """D5 §6.1：引用不存在的 episode_id → 拒绝（不静默落库）。"""
        store = CrossModalLinkStore()
        lk = _link(["ep-a", "ep-b"])
        with pytest.raises(DanglingReferenceError):
            store.add(lk, known_episode_ids={"ep-a"})  # ep-b 未知

    def test_dangling_evidence_id_rejected(self):
        """D5 §6.1：supporting_evidence_ids 含未知 evidence_id → 拒绝。"""
        store = CrossModalLinkStore()
        lk = _link(["ep-a", "ep-b"], ev=("ev-known", "ev-unknown"))
        with pytest.raises(DanglingReferenceError):
            store.add(
                lk,
                known_episode_ids={"ep-a", "ep-b"},
                known_evidence_ids={"ev-known"},  # ev-unknown 未知
            )

    def test_evidence_validation_skipped_when_none(self):
        """known_evidence_ids=None 时跳过 evidence 校验（调用方不掌握证据库）。"""
        store = CrossModalLinkStore()
        lk = _link(["ep-a", "ep-b"], ev=("ev-any",))
        is_new, _ = store.add(lk, known_episode_ids={"ep-a", "ep-b"}, known_evidence_ids=None)
        assert is_new is True

    def test_snapshot_restore_roundtrip(self):
        store = CrossModalLinkStore()
        store.add(
            _link(["ep-a", "ep-b"], overlap=(_dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 45))),
            known_episode_ids={"ep-a", "ep-b"},
        )
        store.add(_link(["ep-c", "ep-d"]), known_episode_ids={"ep-c", "ep-d"})
        snap = store.snapshot()
        assert len(snap) == 2
        restored = CrossModalLinkStore()
        restored.restore(snap)
        assert restored.link_count() == 2
        assert [l.link_id for l in restored.all_links()] == [
            l.link_id for l in store.all_links()
        ]


# ===========================================================================
# 3) CrossModalLinker
# ===========================================================================
class TestCrossModalLinker:
    def test_same_visitor_overlap_co_occurs(self):
        v1 = _mk_episode("ep-va", "V1", None, _dt(2026, 7, 28, 19, 0), _dt(2026, 7, 28, 19, 10), [EvidenceModality.VISION])
        v2 = _mk_episode("ep-vb", "V1", None, _dt(2026, 7, 28, 19, 5), _dt(2026, 7, 28, 19, 15), [EvidenceModality.VISION])
        links = CrossModalLinker().link([v1, v2])
        assert len(links) == 1
        assert links[0].relationship == CrossModalRelationship.CO_OCCURS
        assert links[0].episode_ids == ["ep-va", "ep-vb"]
        # 重叠 19:05-19:10 = 300s；min dur = 600s → 0.5
        assert math.isclose(links[0].confidence, 0.5, rel_tol=1e-9)

    def test_shared_audio_session_cross_modal_supports(self):
        """D5 经典场景：复合(VISION+AUDIO) 与 纯音频(AUDIO) 共享 audio_session_id 重叠 → SUPPORTS。"""
        comp = _mk_episode(
            "ep-xcm", "V1", "xcm1",
            _dt(2026, 7, 28, 18, 30), _dt(2026, 7, 28, 18, 45),
            [EvidenceModality.VISION, EvidenceModality.AUDIO],
        )
        pure = _mk_episode(
            "ep-audio-xcm", None, "xcm1",
            _dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 46),
            [EvidenceModality.AUDIO],
        )
        links = CrossModalLinker().link([comp, pure])
        assert len(links) == 1
        lk = links[0]
        assert lk.relationship == CrossModalRelationship.SUPPORTS
        # 重叠 18:38-18:45 = 420s；min dur = min(900, 480)=480 → 0.875
        assert math.isclose(lk.confidence, 0.875, rel_tol=1e-9)
        assert lk.time_overlap == (_dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 45))
        assert lk.created_at == _dt(2026, 7, 28, 18, 46)

    def test_no_link_when_no_time_overlap(self):
        v1 = _mk_episode("ep-va", "V1", None, _dt(2026, 7, 28, 19, 0), _dt(2026, 7, 28, 19, 10), [EvidenceModality.VISION])
        v2 = _mk_episode("ep-vb", "V1", None, _dt(2026, 7, 28, 20, 0), _dt(2026, 7, 28, 20, 10), [EvidenceModality.VISION])
        assert CrossModalLinker().link([v1, v2]) == []

    def test_no_link_when_different_subject(self):
        # 同时间窗但不同 visitor + 不同 audio_session → 无关联
        v1 = _mk_episode("ep-va", "V1", None, _dt(2026, 7, 28, 19, 0), _dt(2026, 7, 28, 19, 10), [EvidenceModality.VISION])
        v2 = _mk_episode("ep-vb", "V2", None, _dt(2026, 7, 28, 19, 0), _dt(2026, 7, 28, 19, 10), [EvidenceModality.VISION])
        assert CrossModalLinker().link([v1, v2]) == []

    def test_pure_audio_links_via_audio_session_not_visitor(self):
        # 纯音频没有 visitor，仅靠 audio_session_id 关联；与不同 visitor 的复合 episode 关联
        comp = _mk_episode("ep-x", "V9", "sessA", _dt(2026, 7, 28, 18, 30), _dt(2026, 7, 28, 18, 50), [EvidenceModality.VISION, EvidenceModality.AUDIO])
        pure = _mk_episode("ep-ax", None, "sessA", _dt(2026, 7, 28, 18, 40), _dt(2026, 7, 28, 18, 55), [EvidenceModality.AUDIO])
        links = CrossModalLinker().link([comp, pure])
        assert len(links) == 1
        assert links[0].relationship == CrossModalRelationship.SUPPORTS

    def test_link_id_deterministic_and_sorted(self):
        comp = _mk_episode("ep-x", "V1", "s", _dt(2026, 7, 28, 18, 30), _dt(2026, 7, 28, 18, 45), [EvidenceModality.AUDIO])
        pure = _mk_episode("ep-ax", None, "s", _dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 46), [EvidenceModality.AUDIO])
        links1 = CrossModalLinker().link([comp, pure])
        links2 = CrossModalLinker().link([pure, comp])  # 顺序颠倒
        assert links1[0].link_id == links2[0].link_id == "link-ep-ax-ep-x"

    def test_linker_idempotent(self):
        comp = _mk_episode("ep-x", "V1", "s", _dt(2026, 7, 28, 18, 30), _dt(2026, 7, 28, 18, 45), [EvidenceModality.AUDIO])
        pure = _mk_episode("ep-ax", None, "s", _dt(2026, 7, 28, 18, 38), _dt(2026, 7, 28, 18, 46), [EvidenceModality.AUDIO])
        a = CrossModalLinker().link([comp, pure])
        b = CrossModalLinker().link([comp, pure])
        assert [l.link_id for l in a] == [l.link_id for l in b]
        assert a[0].confidence == b[0].confidence
        assert a[0].created_at == b[0].created_at

    def test_overlap_tolerance_allows_near_miss(self):
        # 边界刚好相切（leave == enter）：默认 tolerance=0 不关联
        v1 = _mk_episode("ep-va", "V1", None, _dt(2026, 7, 28, 19, 0), _dt(2026, 7, 28, 19, 10), [EvidenceModality.VISION])
        v2 = _mk_episode("ep-vb", "V1", None, _dt(2026, 7, 28, 19, 10), _dt(2026, 7, 28, 19, 20), [EvidenceModality.VISION])
        assert CrossModalLinker().link([v1, v2]) == []
        # 放宽 tolerance 1 分钟 → 视为重叠
        links = CrossModalLinker(overlap_tolerance=timedelta(minutes=1)).link([v1, v2])
        assert len(links) == 1

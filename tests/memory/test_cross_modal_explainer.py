"""CrossModal Retrieval / Explainer / Renderer 测试（ADR-0029 Slice A）。

覆盖：
- D1 episode 主路径：``get_links_for_episode`` 正确 + 按 link_id 确定性排序；
  ``get_links_in_window`` 可选内部能力正确；``get_links_for_visitor`` / ``get_links_for_device``
  **在 v1 不存在**（延期，归 MemoryQuery）；
- D2 结构化 Context：``explain`` 产出 ``CrossModalContext`` 仅结构化事实，
  ``link_confidence == link.confidence``，``shared_deployment_context`` 红化正确
  （同设备 True / 异设备 False / 无 device None→False），无 ``explanation`` 字段、无 device_id；
- D3 Renderer 解耦：``render`` 产出确定性自然语言，SUPPORTS/CO_OCCURS 描述正确、句法铁律
  （无判断词/因果词）、与 Context 解耦（i18n seam）；关系词汇覆盖 + fail-closed；
- C6 硬约束：``CrossModalContext`` 不含风险语义字段；render 输出无判断词；
  ``test_context_does_not_imply_causality``；无 device_id 断言；
- D5 错误隔离：``explain`` 遇 episode 缺失抛 ``CrossModalRetrievalError``，不静默、不回写；
- 负例：多跳（A-B-C）不处理（explain 只消费直接边）。

铁律（AGENTS.md 测试有效性）：每个「建边/解释」断言配对照；确定性用两次一致验证。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_explainer import (
    CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS,
    RENDERER_FORBIDDEN_WORDS,
    CrossModalContext,
    CrossModalEpisodeRef,
    CrossModalExplainer,
    CrossModalRetrieval,
    CrossModalRetrievalError,
    ExplanationRenderer,
)
from home_perception.memory.cross_modal_link import (
    CrossModalLink,
    CrossModalLinker,
    CrossModalLinkStore,
    CrossModalRelationship,
)
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore

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


def _build_link(vision: EpisodicRecord, audio: EpisodicRecord) -> CrossModalLink:
    """用真实 linker 产出一条跨模态边（确定性，无随机）。"""
    links = CrossModalLinker(min_overlap_seconds=0.0).link([vision, audio])
    assert len(links) == 1, f"预期恰好 1 条边，实际 {links}"
    return links[0]


def _store_with(*episodes: EpisodicRecord) -> InMemoryStore:
    store = InMemoryStore()
    for ep in episodes:
        store.upsert_episodic(ep)
    return store


# ============================================================================
# D1：episode 主路径 + window 可选内部能力 + visitor/device 延期
# ============================================================================


class TestCrossModalRetrieval:
    def test_get_links_for_episode_main_path_sorted(self) -> None:
        """D1 主路径：返回引用该 episode 的全部边，按 link_id 升序（确定性）。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        link1 = _build_link(vision, audio)
        link2 = CrossModalLink(
            link_id="link-ep-v-ep-x",
            episode_ids=["ep-v", "ep-x"],
            relationship=CrossModalRelationship.CO_OCCURS,
            time_overlap=(_BASE_TS + timedelta(seconds=11), _BASE_TS + timedelta(seconds=14)),
            confidence=0.5,
            created_at=_BASE_TS,
        )
        link_store = CrossModalLinkStore()
        link_store.add(link1, {"ep-v", "ep-a"})
        link_store.add(link2, {"ep-v", "ep-x"})
        retrieval = CrossModalRetrieval(link_store)
        found = retrieval.get_links_for_episode("ep-v")
        # 按 link_id 升序的确定性顺序：link-ep-a-ep-v < link-ep-v-ep-x
        assert [lk.link_id for lk in found] == [
            "link-ep-a-ep-v",
            "link-ep-v-ep-x",
        ]

    def test_get_links_in_window_internal_capability(self) -> None:
        """D1 可选内部能力：窗口相交过滤正确 + 按 link_id 升序。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        link = _build_link(vision, audio)
        link_store = CrossModalLinkStore()
        link_store.add(link, {"ep-v", "ep-a"})
        retrieval = CrossModalRetrieval(link_store)
        hit = retrieval.get_links_in_window(
            _BASE_TS + timedelta(seconds=10), _BASE_TS + timedelta(seconds=30)
        )
        assert [lk.link_id for lk in hit] == [link.link_id]
        miss = retrieval.get_links_in_window(
            _BASE_TS - timedelta(seconds=100), _BASE_TS - timedelta(seconds=50)
        )
        assert miss == []

    def test_get_links_for_visitor_not_implemented_in_v1(self) -> None:
        """D1 延期：v1 不存在 get_links_for_visitor（visitor join 归 MemoryQuery）。"""
        assert not hasattr(CrossModalRetrieval, "get_links_for_visitor")

    def test_get_links_for_device_not_implemented_in_v1(self) -> None:
        """D1 延期：v1 不存在 get_links_for_device（避免演变为家庭全局知识查询）。"""
        assert not hasattr(CrossModalRetrieval, "get_links_for_device")


# ============================================================================
# D2：结构化 Context（explain 纯函数，link_confidence，shared 红化，无 device_id）
# ============================================================================


class TestCrossModalExplainer:
    def _explain_same_device(self) -> CrossModalContext:
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="dev-001",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="dev-001",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision, audio)
        link = _build_link(vision, audio)
        return CrossModalExplainer(store).explain(link)

    def test_explain_structured_no_explanation_field(self) -> None:
        """D2：产出 CrossModalContext 是纯结构化事实，不含 explanation 自然语言字段。"""
        ctx = self._explain_same_device()
        assert not hasattr(ctx, "explanation")
        assert set(ctx.__dataclass_fields__.keys()) == {
            "relationship",
            "source_episode",
            "target_episode",
            "overlap_seconds",
            "link_confidence",
            "shared_deployment_context",
            "source_link_id",
            "source_episode_ids",
        }

    def test_explain_link_confidence_equals_link_confidence(self) -> None:
        """D2：link_confidence == link.confidence（语义=建边置信，非事件关联强度）。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision, audio)
        link = _build_link(vision, audio)
        ctx = CrossModalExplainer(store).explain(link)
        assert ctx.link_confidence == link.confidence
        assert 0.0 <= ctx.link_confidence <= 1.0

    def test_shared_deployment_context_redaction_same_device(self) -> None:
        """D2 红化：同设备 → shared_deployment_context True，且 Context 不含 device_id。"""
        ctx = self._explain_same_device()
        assert ctx.shared_deployment_context is True
        dumped = ctx.to_dict()
        assert "device_id" not in dumped
        assert "device_id" not in dumped["source_episode"]
        assert "device_id" not in dumped["target_episode"]

    def test_shared_deployment_context_false_different_device(self) -> None:
        """D2 红化：异设备 → False（不暴露设备标识）。

        注意：异设备时真实 linker 不会建边（candidate_context 需共享设备/访客），
        故此处直接构造边以测试 explain 的红化分支。
        """
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="dev-001",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="dev-002",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision, audio)
        link = CrossModalLink(
            link_id="link-ep-a-ep-v",
            episode_ids=["ep-a", "ep-v"],
            relationship=CrossModalRelationship.SUPPORTS,
            time_overlap=(_BASE_TS + timedelta(seconds=12), _BASE_TS + timedelta(seconds=15)),
            confidence=0.9,
            created_at=_BASE_TS,
        )
        ctx = CrossModalExplainer(store).explain(link)
        assert ctx.shared_deployment_context is False

    def test_shared_deployment_context_false_one_device_none(self) -> None:
        """D2 红化：任一端 device_id 为 None → False（不关联、不泄标识）。

        注意：一端 device_id=None 时真实 linker 不会建边，故此处直接构造边。
        """
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device=None,
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="dev-001",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision, audio)
        link = CrossModalLink(
            link_id="link-ep-a-ep-v",
            episode_ids=["ep-a", "ep-v"],
            relationship=CrossModalRelationship.SUPPORTS,
            time_overlap=(_BASE_TS + timedelta(seconds=12), _BASE_TS + timedelta(seconds=15)),
            confidence=0.9,
            created_at=_BASE_TS,
        )
        ctx = CrossModalExplainer(store).explain(link)
        assert ctx.shared_deployment_context is False

    def test_explain_deterministic(self) -> None:
        """D2/D5：同 (link, store) 两次 explain 逐字段一致（C3 确定性）。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision, audio)
        link = _build_link(vision, audio)
        explainer = CrossModalExplainer(store)
        assert explainer.explain(link).to_dict() == explainer.explain(link).to_dict()

    def test_explain_missing_episode_raises(self) -> None:
        """D5 错误隔离：某端 episode 在 store 缺失 → 抛 CrossModalRetrievalError（不静默）。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        store = _store_with(vision)
        link = _build_link(vision, audio)
        with pytest.raises(CrossModalRetrievalError):
            CrossModalExplainer(store).explain(link)

    def test_explain_no_store_raises(self) -> None:
        """D2：explain 无 MemoryStore 来源 → ValueError（防御）。"""
        vision = _mk_episode("ep-v", visitor="V1", audio_session=None, device="d",
                             start_s=10, end_s=20, modalities=[EvidenceModality.VISION])
        audio = _mk_episode("ep-a", visitor=None, audio_session="s", device="d",
                            start_s=12, end_s=15, modalities=[EvidenceModality.AUDIO])
        link = _build_link(vision, audio)
        with pytest.raises(ValueError):
            CrossModalExplainer(None).explain(link)

    def test_multihop_not_handled(self) -> None:
        """负例：explain 只消费直接边（两端 episode），不处理多跳 A-B-C。"""
        ctx = self._explain_same_device()
        assert set(ctx.source_episode_ids) == {"ep-a", "ep-v"}
        assert len(ctx.source_episode_ids) == 2


# ============================================================================
# D3：ExplanationRenderer（关系映射 + 句法铁律 + 覆盖 + fail-closed）
# ============================================================================


class TestExplanationRenderer:
    def _ctx(self, *, shared: bool, rel: CrossModalRelationship) -> CrossModalContext:
        return CrossModalContext(
            relationship=rel,
            source_episode=CrossModalEpisodeRef("ep-v", "视觉：老人跌倒", (EvidenceModality.VISION,)),
            target_episode=CrossModalEpisodeRef("ep-a", "音频：撞击声", (EvidenceModality.AUDIO,)),
            overlap_seconds=3.0,
            link_confidence=0.9,
            shared_deployment_context=shared,
            source_link_id="link-ep-a-ep-v",
            source_episode_ids=("ep-a", "ep-v"),
        )

    def test_render_supports_description(self) -> None:
        """D3：SUPPORTS 描述正确（视觉事件「…」与音频事件「…」重叠相互支撑 + 建边置信）。"""
        text = ExplanationRenderer().render(self._ctx(shared=True, rel=CrossModalRelationship.SUPPORTS))
        assert "视觉事件「视觉：老人跌倒」" in text
        assert "音频事件「音频：撞击声」" in text
        assert "相互支撑" in text
        assert "建边置信 0.90" in text
        assert "同一部署源上下文" in text

    def test_render_co_occurs_description(self) -> None:
        """D3：CO_OCCURS 描述正确（同一访客两次事件相邻合证）。"""
        text = ExplanationRenderer().render(self._ctx(shared=False, rel=CrossModalRelationship.CO_OCCURS))
        assert "同一访客的两次事件在时间上相邻合证" in text
        assert "重叠约 3 秒" in text
        assert "建边置信 0.90" in text
        assert "同一部署源上下文" not in text

    def test_render_deterministic(self) -> None:
        """D3：同 Context 两次 render 逐字符一致（C3）。"""
        r = ExplanationRenderer()
        ctx = self._ctx(shared=True, rel=CrossModalRelationship.SUPPORTS)
        assert r.render(ctx) == r.render(ctx)

    def test_renderer_covers_all_relationships(self) -> None:
        """D3：映射表覆盖 CrossModalRelationship 全部枚举值。"""
        assert set(ExplanationRenderer._RELATIONSHIP_DESCRIPTIONS) == set(CrossModalRelationship)

    def test_renderer_fail_closed_on_unknown(self, monkeypatch) -> None:
        """D3 fail-closed：未知关系值抛 ValueError（不静默降级）。"""
        ctx = self._ctx(shared=True, rel=CrossModalRelationship.SUPPORTS)
        monkeypatch.setattr(ExplanationRenderer, "_RELATIONSHIP_DESCRIPTIONS", {})
        with pytest.raises(ValueError):
            ExplanationRenderer().render(ctx)

    def test_renderer_locale_seam_only_zh(self) -> None:
        """D3 i18n seam：v1 仅支持 locale='zh'，其他 locale 显式拒绝（占位）。"""
        ExplanationRenderer(locale="zh")
        with pytest.raises(ValueError):
            ExplanationRenderer(locale="en")


# ============================================================================
# C6：解释层禁止风险语义（无风险字段 / 无判断词 / 无因果暗示 / 无 device_id）
# ============================================================================


class TestCrossModalC6Invariants:
    def test_context_has_no_risk_semantics(self) -> None:
        """C6：CrossModalContext 字段集不含任何风险/判定/隐私字段。"""
        names = set(CrossModalContext.__dataclass_fields__.keys())
        assert not (names & CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS), (
            f"CrossModalContext 含禁止字段：{names & CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS}"
        )

    def test_context_episode_ref_has_no_device_id(self) -> None:
        """C6/隐私：CrossModalEpisodeRef 也不含 device_id。"""
        names = set(CrossModalEpisodeRef.__dataclass_fields__.keys())
        assert "device_id" not in names

    def test_renderer_output_has_no_judgment_words(self) -> None:
        """C6：渲染输出不得含判断词（疑似/可能/应当/建议/风险…）。"""
        r = ExplanationRenderer()
        for rel in CrossModalRelationship:
            ctx = CrossModalContext(
                relationship=rel,
                source_episode=CrossModalEpisodeRef("ep-v", "视觉：老人跌倒", (EvidenceModality.VISION,)),
                target_episode=CrossModalEpisodeRef("ep-a", "音频：撞击声", (EvidenceModality.AUDIO,)),
                overlap_seconds=3.0,
                link_confidence=0.9,
                shared_deployment_context=True,
                source_link_id="link-ep-a-ep-v",
                source_episode_ids=("ep-a", "ep-v"),
            )
            text = r.render(ctx)
            for word in RENDERER_FORBIDDEN_WORDS:
                assert word not in text, f"渲染输出含禁止词 {word!r}：{text}"

    def test_context_does_not_imply_causality(self) -> None:
        """C6（因果不暗示）：support ≠ cause。

        渲染输出不得含因果词（导致/引起/因为）；relationship 仅为中性标签
        （supports/co_occurs），不表达"音频导致视觉"之类因果。
        """
        r = ExplanationRenderer()
        supports_ctx = CrossModalContext(
            relationship=CrossModalRelationship.SUPPORTS,
            source_episode=CrossModalEpisodeRef("ep-v", "视觉：老人跌倒", (EvidenceModality.VISION,)),
            target_episode=CrossModalEpisodeRef("ep-a", "音频：撞击声", (EvidenceModality.AUDIO,)),
            overlap_seconds=3.0,
            link_confidence=0.9,
            shared_deployment_context=True,
            source_link_id="link-ep-a-ep-v",
            source_episode_ids=("ep-a", "ep-v"),
        )
        text = r.render(supports_ctx)
        causal_words = {"导致", "引起", "因为"}
        for word in causal_words:
            assert word not in text, f"渲染输出暗示因果，含 {word!r}：{text}"
        assert supports_ctx.relationship.value == "supports"

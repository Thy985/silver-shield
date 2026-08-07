"""跨模态记忆检索与解释（ADR-0029 · Cross-Modal Memory Retrieval & Explanation）。

> **核心纪律（§0.2 / C6）**：本模块是**检索 + 解释**层，**不是判断**层。
> 解释只读事实、只描述关系、只给溯源，绝不产出结论。
>
> 与 ADR-0028 的边界：
> - ``CrossModalLink``（边索引，Memory Graph 内部结构）由 ``cross_modal_link`` 产出；
> - 本模块**只读** ``CrossModalLinkStore`` 取边，把一条边**投影**为结构化、
>   隐私安全、可解释的 ``CrossModalContext``（解释层契约），再由独立的
>   ``ExplanationRenderer`` 渲染为自然语言（i18n seam）。
>
> **隐私红化（根治 ADR-0028 开放项 #5）**：``CrossModalContext`` / ``CrossModalEpisodeRef``
> **绝不**携带原始 ``device_id``；仅暴露 ``shared_deployment_context: bool``（两端 episode
> 的 ``device_id`` 均非 None 且相等 → True），从数据结构上杜绝 ``device_id`` 经 link 链路
> 泄入 Reason（ADR-0025 §3.1）。
>
> **C6 硬约束**：``CrossModalContext`` 不得含 ``risk_score`` / ``risk_level`` / ``alert`` /
> ``warning`` / ``decision`` / ``recommendation``；``ExplanationRenderer`` 输出不得含判断词
> / 因果词（``support ≠ cause``）。约束由契约测试钉死。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_link import (
    CrossModalLink,
    CrossModalLinkStore,
    CrossModalRelationship,
)
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import MemoryStore

# 解释层禁止的判定/风险语义字段（C6，结构性硬约束，与 CONSUMER_FORBIDDEN_FIELDS 同族）
CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "risk_score",
        "risk_level",
        "alert",
        "warning",
        "decision",
        "recommendation",
        "device_id",  # 隐私红化：绝不进 Context
    }
)

# 渲染输出禁用词（C6 句法铁律 + 因果不暗示）：判断词 + 因果词
RENDERER_FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {
        "疑似",
        "可能",
        "应当",
        "建议",
        "风险",
        "推断",
        "判断",
        "导致",
        "引起",
        "因为",
    }
)


class CrossModalRetrievalError(Exception):
    """解释器查 episode 缺失时抛出的分层异常（ADR-0029 D5 错误隔离）。

    不静默、不向上抛裸异常；由调用方（Consumer）决定隔离 / 跳过。
    """


# ===========================================================================
# 结构化解释契约（D2）—— 纯事实，无自然语言
# ===========================================================================


@dataclass(frozen=True)
class CrossModalEpisodeRef:
    """跨模态边一端 episode 的**结构化、隐私安全**引用（ADR-0029 D2）。

    只暴露解释所必需的非敏感事实：``record_id`` / ``summary`` / ``modalities``。
    **绝不**携带 ``device_id``（隐私红化，见 ``shared_deployment_context``）。
    """

    record_id: str
    summary: str
    modalities: tuple[EvidenceModality, ...]

    def __post_init__(self) -> None:
        if not self.record_id or not self.record_id.strip():
            raise ValueError("CrossModalEpisodeRef.record_id 不能为空")
        if not self.summary or not self.summary.strip():
            raise ValueError("CrossModalEpisodeRef.summary 不能为空")
        for m in self.modalities or []:
            if not isinstance(m, EvidenceModality):
                raise TypeError(
                    f"CrossModalEpisodeRef.modalities 元素必须是 EvidenceModality，收到 {m!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "summary": self.summary,
            "modalities": [m.value for m in self.modalities],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrossModalEpisodeRef:
        return cls(
            record_id=data["record_id"],
            summary=data["summary"],
            modalities=tuple(EvidenceModality(m) for m in data.get("modalities", [])),
        )


@dataclass(frozen=True)
class CrossModalContext:
    """跨模态解释上下文（ADR-0029 D2，**纯结构化事实**，不含自然语言）。

    ``RuleBasedMemoryConsumer`` 消费的是 **``CrossModalContext``**，不是
    ``CrossModalLink``（ADR-0029 §0.2 层 / 数据边界：Consumer 消费 Context 而非 Link）。

    字段（C6 禁止字段见 ``CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS``，本 dataclass 天然不含）：

    - ``relationship``：透传 link 的 ``CrossModalRelationship``（解释层不重新解释语义）；
    - ``source_episode`` / ``target_episode``：两端 episode 的结构化引用（按 record_id
      升序确定，保证 C3 确定性）；
    - ``overlap_seconds``：时间窗重叠秒数（来自 link.time_overlap）；
    - ``link_confidence``：== ``link.confidence``，语义为"Link Runtime 对建立这条边的
      置信程度"（**非**事件关联强度、非风险分；字段名显式与 ``risk_score`` / ``decision``
      划清）；
    - ``shared_deployment_context``：**红化** bool（两端 episode ``device_id`` 均非 None
      且相等 → True），绝不暴露原始 ``device_id``（根治 ADR-0028 开放项 #5）；
    - ``source_link_id`` / ``source_episode_ids``：溯源锚点（C5），可追到具体 link 与 episode。
    """

    relationship: CrossModalRelationship
    source_episode: CrossModalEpisodeRef
    target_episode: CrossModalEpisodeRef
    overlap_seconds: float
    link_confidence: float
    shared_deployment_context: bool
    source_link_id: str
    source_episode_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        # C6 结构性断言：任何实现若向 Context 加风险/判定/隐私字段将立即失败
        names = set(self.__dataclass_fields__.keys())  # type: ignore[attr-defined]
        bad = names & CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS
        if bad:
            raise ValueError(f"CrossModalContext 禁止包含字段：{bad}")
        if not isinstance(self.relationship, CrossModalRelationship):
            raise TypeError(
                f"relationship 必须是 CrossModalRelationship，收到 {self.relationship!r}"
            )
        if not (0.0 <= float(self.link_confidence) <= 1.0):
            raise ValueError(
                f"link_confidence 必须在 [0, 1]，收到 {self.link_confidence!r}"
            )
        object.__setattr__(self, "link_confidence", float(self.link_confidence))
        if not self.source_link_id or not self.source_link_id.strip():
            raise ValueError("source_link_id 不能为空（C5 溯源）")
        if not self.source_episode_ids:
            raise ValueError("source_episode_ids 不能为空（C5 溯源）")

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典（枚举 → value，tuple → list，C5）。"""
        return {
            "relationship": self.relationship.value,
            "source_episode": self.source_episode.to_dict(),
            "target_episode": self.target_episode.to_dict(),
            "overlap_seconds": self.overlap_seconds,
            "link_confidence": self.link_confidence,
            "shared_deployment_context": self.shared_deployment_context,
            "source_link_id": self.source_link_id,
            "source_episode_ids": list(self.source_episode_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrossModalContext:
        return cls(
            relationship=CrossModalRelationship(data["relationship"]),
            source_episode=CrossModalEpisodeRef.from_dict(data["source_episode"]),
            target_episode=CrossModalEpisodeRef.from_dict(data["target_episode"]),
            overlap_seconds=float(data["overlap_seconds"]),
            link_confidence=float(data["link_confidence"]),
            shared_deployment_context=bool(data["shared_deployment_context"]),
            source_link_id=data["source_link_id"],
            source_episode_ids=tuple(data["source_episode_ids"]),
        )


# ===========================================================================
# 检索（D1）—— 图级只读，episode 主路径 + window 可选内部能力
# ===========================================================================


class CrossModalRetrieval:
    """跨模态图级只读检索（ADR-0029 D1）。

    **v1 以 ``get_links_for_episode`` 为唯一对外主路径**；``get_links_in_window`` 为
    可选内部能力（不作为主 API，检索面收敛）。``get_links_for_visitor`` /
    ``get_links_for_device`` **不在 v1**（visitor→episode→link join 归 ``MemoryQuery``，
    避免与 ``MemoryQuery`` 出现两个查 visitor 的查询系统）。

    确定性（C3）：各轴返回均按 ``link_id`` 升序。

    与 ``MemoryQuery`` 同层级、不接 ``Retrieval`` ABC——v1 单实现，避免与 episode
    召回语义混淆（``Retrieval`` ABC 是 episode 召回，本类是对 link 图的召回）。
    """

    def __init__(self, link_store: CrossModalLinkStore) -> None:
        self._link_store = link_store

    def get_links_for_episode(self, episode_id: str) -> list[CrossModalLink]:
        """主路径：返回所有引用了 ``episode_id`` 的关联边（按 link_id 升序，确定性）。"""
        return self._link_store.get_links_by_episode(episode_id)

    def get_links_in_window(
        self, start: datetime, end: datetime
    ) -> list[CrossModalLink]:
        """可选内部能力：返回时间窗 ``[start, end]`` 内存在重叠的关联边（按 link_id 升序）。

        非对外主 API（检索面收敛）；``start`` / ``end`` 必须 UTC tz-aware。
        """
        if not isinstance(start, datetime) or start.tzinfo is None:
            raise ValueError("start 必须是 UTC tz-aware datetime")
        if not isinstance(end, datetime) or end.tzinfo is None:
            raise ValueError("end 必须是 UTC tz-aware datetime")
        if start > end:
            raise ValueError("start 必须 <= end")
        result: list[CrossModalLink] = []
        for link in self._link_store.all_links():
            overlap = link.time_overlap
            if overlap is None:
                continue
            # 半开区间相交判定（闭区间 [start,end] 与 [o_start,o_end] 相交）
            if overlap[1] >= start and overlap[0] <= end:
                result.append(link)
        result.sort(key=lambda l: l.link_id)
        return result


# ===========================================================================
# 解释（D2）—— link → 结构化 CrossModalContext（纯函数，C3/C5）
# ===========================================================================


class CrossModalExplainer:
    """把一条 ``CrossModalLink`` 投影为结构化 ``CrossModalContext``（ADR-0029 D2）。

    **纯函数（C3）**：``explain`` 不读墙钟、不随机、不写状态；同 ``(link, store 状态)``
    两次产出逐字段一致（审计 / 回放一致）。
    **隐私红化（D2）**：查两端 episode 的 ``device_id``，仅在均非 None 且相等时置
    ``shared_deployment_context=True``，绝不把原始 ``device_id`` 写进 Context。
    **错误隔离（D5）**：查 episode 若缺失 → 抛 ``CrossModalRetrievalError``，不静默、不回写。
    """

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        # 可选持有 store（C2 只读用法）；调用方也可在 explain 时临时传入
        self._memory_store = memory_store

    def explain(
        self,
        link: CrossModalLink,
        memory_store: MemoryStore | None = None,
    ) -> CrossModalContext:
        """把一条 link（连同两端 episode）投影为 ``CrossModalContext``。

        Args:
            link: 待解释的 ``CrossModalLink``（只读，不修改）。
            memory_store: 查两端 episode 的 MemoryStore；为 None 时使用构造时传入的 store。

        Returns:
            结构化、隐私安全、确定性的 ``CrossModalContext``。

        Raises:
            CrossModalRetrievalError: 两端 episode 任一在 store 中缺失（D5 错误隔离）；
            ValueError: ``explain`` 需要 MemoryStore 但两个来源均为 None。
        """
        store = memory_store or self._memory_store
        if store is None:
            raise ValueError("explain 需要 MemoryStore（构造时传入或参数传入）")

        # 逐一按 record_id 取两端 episode（C2 只读，不写状态）
        episodes: dict[str, EpisodicRecord] = {}
        for eid in link.episode_ids:
            ep = _find_episode(store, eid)
            if ep is None:
                raise CrossModalRetrievalError(
                    f"CrossModalLink {link.link_id} 引用的 episode {eid} 在 MemoryStore 缺失"
                )
            episodes[eid] = ep

        # 确定性排序：按 record_id 升序分配 source / target
        ordered_ids = sorted(link.episode_ids)
        src_ep = episodes[ordered_ids[0]]
        tgt_ep = episodes[ordered_ids[1]]

        # D2 隐私红化：仅当两端 device_id 均非 None 且相等时才置 True
        shared = (
            src_ep.device_id is not None
            and tgt_ep.device_id is not None
            and src_ep.device_id == tgt_ep.device_id
        )

        overlap_seconds = 0.0
        if link.time_overlap is not None:
            overlap_seconds = (link.time_overlap[1] - link.time_overlap[0]).total_seconds()

        return CrossModalContext(
            relationship=link.relationship,
            source_episode=CrossModalEpisodeRef(
                record_id=src_ep.record_id,
                summary=src_ep.summary,
                modalities=tuple(src_ep.modalities or []),
            ),
            target_episode=CrossModalEpisodeRef(
                record_id=tgt_ep.record_id,
                summary=tgt_ep.summary,
                modalities=tuple(tgt_ep.modalities or []),
            ),
            overlap_seconds=overlap_seconds,
            # D2：link_confidence 直接取 link.confidence（建边置信，非事件关联强度）
            link_confidence=link.confidence,
            shared_deployment_context=shared,
            source_link_id=link.link_id,
            source_episode_ids=tuple(link.episode_ids),
        )


def _find_episode(store: MemoryStore, record_id: str) -> EpisodicRecord | None:
    """按 record_id 从 store 取 episode（C2 只读；不写任何状态）。"""
    for ep in store.all_episodic():
        if ep.record_id == record_id:
            return ep
    return None


# ===========================================================================
# 渲染（D3）—— 结构化 Context → 自然语言（i18n seam）
# ===========================================================================


class ExplanationRenderer:
    """把 ``CrossModalContext`` 渲染为自然语言（ADR-0029 D3）。

    与 ``CrossModalExplainer`` 解耦：Context 只存事实（D2），本类单独负责"事实 → 人话"。
    **i18n seam**：v1 仅中文（``locale="zh"``）；未来多语言只需替换渲染器或注入 locale，
    ``CrossModalContext`` 契约零改动。

    关系词汇 → 描述映射（确定性，无模型）：
    - ``SUPPORTS``：跨模态支撑（视觉事件「…」与音频事件「…」在时间窗重叠约 Ns，
      在同一部署源上下文相互支撑）；
    - ``CO_OCCURS``：同主体合证（同一访客的两次事件在时间上相邻合证）。

    句法铁律（§0.2 / C6）：只陈述事实，不得出现判断词 / 因果词（``support ≠ cause``）；
    输出以句号闭合的**陈述句**收尾。

    关系词汇覆盖 + fail-closed：``_RELATIONSHIP_DESCRIPTIONS`` 必须覆盖
    ``CrossModalRelationship`` 全部枚举值；未知值抛 ``ValueError``（不静默降级）。
    """

    def __init__(self, *, locale: str = "zh") -> None:
        if locale != "zh":
            # i18n seam 占位：v1 仅中文实现，其他 locale 由未来渲染器承接
            raise ValueError(f"ExplanationRenderer v1 仅支持 locale='zh'，收到 {locale!r}")
        self._locale = locale

    def render(self, context: CrossModalContext) -> str:
        """把 ``CrossModalContext`` 渲染为确定性自然语言（陈述句）。

        Raises:
            ValueError: ``context.relationship`` 不在已知映射中（fail-closed，不静默降级）。
        """
        rel = context.relationship
        if rel not in self._RELATIONSHIP_DESCRIPTIONS:
            raise ValueError(f"未知关系类型，无法渲染解释：{rel!r}")
        desc = self._RELATIONSHIP_DESCRIPTIONS[rel]
        return desc(context)

    @staticmethod
    def _fmt_seconds(seconds: float) -> str:
        """确定性秒数格式：整数无小数，否则 1 位小数。"""
        if seconds == int(seconds):
            return str(int(seconds))
        return f"{seconds:.1f}"


def _render_supports(ctx: CrossModalContext) -> str:
    """SUPPORTS：跨模态支撑（视觉事件与音频事件，事实陈述，不暗示因果）。"""
    vision = ctx.source_episode
    audio = ctx.target_episode
    if EvidenceModality.VISION not in vision.modalities:
        # 交换，确保 vision 在前
        vision, audio = audio, vision
    ctx_phrase = "，在同一部署源上下文" if ctx.shared_deployment_context else ""
    return (
        f"视觉事件「{vision.summary}」与音频事件「{audio.summary}」"
        f"在时间窗重叠约 {ExplanationRenderer._fmt_seconds(ctx.overlap_seconds)} 秒"
        f"{ctx_phrase}相互支撑。"
        f"建边置信 {ctx.link_confidence:.2f}。"
    )


def _render_co_occurs(ctx: CrossModalContext) -> str:
    """CO_OCCURS：同主体合证（同一访客两次事件，事实陈述）。"""
    ctx_phrase = "，在同一部署源上下文" if ctx.shared_deployment_context else ""
    return (
        f"同一访客的两次事件在时间上相邻合证："
        f"{ctx.source_episode.summary} 与 {ctx.target_episode.summary} "
        f"重叠约 {ExplanationRenderer._fmt_seconds(ctx.overlap_seconds)} 秒"
        f"{ctx_phrase}。"
        f"建边置信 {ctx.link_confidence:.2f}。"
    )


# 关系词汇 → 描述映射（覆盖全部 CrossModalRelationship 枚举；fail-closed 依据）
_RELATIONSHIP_DESCRIPTIONS: dict[CrossModalRelationship, Any] = {
    CrossModalRelationship.SUPPORTS: _render_supports,
    CrossModalRelationship.CO_OCCURS: _render_co_occurs,
}

# 类属性别名（D3 覆盖 + fail-closed 测试锚点）：映射同时作为类属性暴露，
# 使 ``ExplanationRenderer._RELATIONSHIP_DESCRIPTIONS`` 可被读取 / monkeypatch，
# 与模块级定义保持同一对象引用（fail-closed 单点真相）。
ExplanationRenderer._RELATIONSHIP_DESCRIPTIONS = _RELATIONSHIP_DESCRIPTIONS


__all__ = [
    "CROSS_MODAL_CONTEXT_FORBIDDEN_FIELDS",
    "RENDERER_FORBIDDEN_WORDS",
    "CrossModalContext",
    "CrossModalEpisodeRef",
    "CrossModalExplainer",
    "CrossModalRetrieval",
    "CrossModalRetrievalError",
    "ExplanationRenderer",
]

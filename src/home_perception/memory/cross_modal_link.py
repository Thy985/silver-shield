"""跨模态关联索引（ADR-0027 Slice C · D5 CrossModalLink）。

> **D5 核心思想**：关联的是**关系（边）**，不是实体。绝不把视觉 episode 与音频
> episode 合并成一条新记录（那会丢失来源模态、破坏 ADR-0024 "Memory 记录经过确认
> 的事件" 的可解释性）。
>
> 正确形态（知识图谱式 Node—Edge—Node）：
>
> ```
> Episode A (VISION: 老人在家神色紧张、翻找银行卡)
>       │  CrossModalLink(relationship="supports", time_overlap=…)
>       ▼
> Episode B (AUDIO: telephone_persistent 长时间通话 + distress_cry 哭腔)
> ```
>
> 本模块提供三件套：
> - ``CrossModalLink``：轻量关联索引 dataclass（不可变事实，独立存储）。
> - ``CrossModalLinkStore``：内存存储后端，``add`` 时强制**悬空引用校验**
>   （D5 验收：未知 episode_id / evidence_id → 拒绝，不静默落库）。
> - ``CrossModalLinker``：关联器（确定性强、无随机 id），扫描 episode 集合、
>   对"同主体 + 时间窗重叠"的 pair 产出 ``CrossModalLink``。
>
> **与 MemoryRecord 的关系**：``CrossModalLink`` **不继承** ``EpisodicRecord`` /
> ``SemanticAggregate``，是独立轻量索引，挂在 episode 之外独立存储（ADR-0027 D5）。
> 触发与权重策略（时间窗重叠 + 关联强度）的 v1 启发式实现于 ``CrossModalLinker``，
> 权重归约细节归融合 ADR（ADR-0026 §10 开放项），本 slice 仅落地确定性 v1 规则。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from ..common.timeutil import require_utc
from .records import EpisodicRecord


class CrossModalRelationship(str, Enum):
    """跨模态关系白名单（禁止自由文本，ADR-0027 D5）。

    仅允许受控关系值；关联语义由白名单约束，避免任意字符串污染索引。
    """

    CO_OCCURS = "co_occurs"  # 同主体、时间窗重叠的合证（如同一访客两次访问窗口相邻）
    SUPPORTS = "supports"  # 跨模态支撑（如视觉紧张翻找 + 音频通话/哭腔 共同指向诈骗风险）


# 枚举值集闭合基线（契约测试据此断言"值集不漂移"）
CROSS_MODAL_RELATIONSHIP_VALUES: tuple[str, ...] = tuple(e.value for e in CrossModalRelationship)


class DanglingReferenceError(Exception):
    """跨模态关联悬空引用异常（ADR-0027 §6.1 D5）。

    当 ``CrossModalLink.episode_ids`` 含未知 ``record_id``，或
    ``supporting_evidence_ids`` 含未知 ``evidence_id`` 时抛出——**拒绝静默落库**
    （不把指向不存在实体的边写进索引），由调用方决定隔离 / 丢弃 / 重试。
    """


# to_dict 字段闭合基准（契约测试据此断言"字段集合恒定"）
CROSS_MODAL_LINK_DICT_KEYS: tuple[str, ...] = (
    "link_id",
    "episode_ids",
    "relationship",
    "time_overlap",
    "confidence",
    "created_at",
    "supporting_evidence_ids",
)


@dataclass
class CrossModalLink:
    """跨模态关联边（ADR-0027 D5，独立轻量索引）。

    字段：
    - ``link_id``：幂等键，确定性 ``f"link-{'-'.join(sorted(episode_ids))}"``
      （同 pair 永远同 id，回放稳定、可幂等 upsert）。
    - ``episode_ids``：关联 episode 的 ``record_id`` 列表（≥2，互异，非空 str）。
    - ``relationship``：``CrossModalRelationship`` 白名单枚举。
    - ``time_overlap``：关联的时间窗重叠 ``(start, end)``（UTC）；无重叠则 None。
    - ``confidence``：关联可信度 [0,1]（关联强度启发式，非风险分）。
    - ``created_at``：关联生成时刻（UTC；linker 取两 episode 离场时刻的 max，确定性）。
    - ``supporting_evidence_ids``：证据级关联（建议2，v1 可空），与 ``episode_ids``
      平行，细化到"哪条证据支撑哪条证据"；v1 留空不阻塞冻结。
    """

    link_id: str
    episode_ids: list[str]
    relationship: CrossModalRelationship
    time_overlap: tuple[datetime, datetime] | None
    confidence: float
    created_at: datetime
    supporting_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 1) link_id 非空
        if not isinstance(self.link_id, str) or not self.link_id.strip():
            raise ValueError("link_id 不能为空")

        # 2) episode_ids：list[str]，≥2，互异，非空
        if not isinstance(self.episode_ids, list) or len(self.episode_ids) < 2:
            raise ValueError("episode_ids 必须是长度 ≥ 2 的 list[str]")
        seen: set[str] = set()
        for eid in self.episode_ids:
            if not isinstance(eid, str) or not eid.strip():
                raise ValueError(f"episode_ids 元素必须是非空 str，收到 {eid!r}")
            if eid in seen:
                raise ValueError(f"episode_ids 不得含重复 record_id：{eid}")
            seen.add(eid)

        # 3) relationship 白名单枚举
        if not isinstance(self.relationship, CrossModalRelationship):
            raise TypeError(
                f"relationship 必须是 CrossModalRelationship，收到 {self.relationship!r}"
            )

        # 4) time_overlap：None 或 (UTC, UTC) 且 start <= end
        if self.time_overlap is not None:
            if not isinstance(self.time_overlap, (tuple, list)) or len(self.time_overlap) != 2:
                raise ValueError("time_overlap 必须是 (datetime, datetime) 或 None")
            start, end = self.time_overlap
            require_utc(start, "time_overlap[0]")
            require_utc(end, "time_overlap[1]")
            if end < start:
                raise ValueError(
                    f"time_overlap 必须 start <= end，收到 {start} ~ {end}"
                )

        # 5) confidence：[0,1] 有限值
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise TypeError(f"confidence 必须是有限数，收到 {self.confidence!r}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1]，收到 {self.confidence}")
        self.confidence = float(self.confidence)

        # 6) created_at UTC
        require_utc(self.created_at, "created_at")

        # 7) supporting_evidence_ids：list[str]，互异，非空
        if not isinstance(self.supporting_evidence_ids, list):
            raise TypeError("supporting_evidence_ids 必须是 list[str]")
        seen_e: set[str] = set()
        for ev in self.supporting_evidence_ids:
            if not isinstance(ev, str) or not ev.strip():
                raise ValueError(f"supporting_evidence_ids 元素必须是非空 str，收到 {ev!r}")
            if ev in seen_e:
                raise ValueError(f"supporting_evidence_ids 不得含重复 evidence_id：{ev}")
            seen_e.add(ev)

    def to_dict(self) -> dict:
        """structlog-safe 字典（datetime → ISO，枚举 → value，tuple → list）。"""
        return {
            "link_id": self.link_id,
            "episode_ids": list(self.episode_ids),
            "relationship": self.relationship.value,
            "time_overlap": (
                [self.time_overlap[0].isoformat(), self.time_overlap[1].isoformat()]
                if self.time_overlap is not None
                else None
            ),
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrossModalLink:
        raw_overlap = data.get("time_overlap")
        overlap = None
        if raw_overlap is not None:
            overlap = (datetime.fromisoformat(raw_overlap[0]), datetime.fromisoformat(raw_overlap[1]))
        return cls(
            link_id=data["link_id"],
            episode_ids=list(data["episode_ids"]),
            relationship=CrossModalRelationship(data["relationship"]),
            time_overlap=overlap,
            confidence=data["confidence"],
            created_at=datetime.fromisoformat(data["created_at"]),
            supporting_evidence_ids=list(data.get("supporting_evidence_ids", [])),
        )


class CrossModalLinkStore:
    """跨模态关联索引存储后端（v1 内存，独立索引，不并入 Episodic 存储）。

    设计要点：
    - 与 ``InMemoryStore``（Episodic）解耦：跨模态是**边索引**，不是 MemoryRecord。
    - ``add`` 强制**悬空引用校验**（D5）：未知 ``episode_id`` / ``evidence_id`` 直接
      抛 ``DanglingReferenceError``，绝不把悬空边写入索引。
    - 单调性（I2 风格）：相同 ``link_id`` 若内容一致 → 幂等命中（返回 False）；
      若内容不同 → 抛 ``DanglingReferenceError``? 不，内容不同属于冲突，抛 ``ValueError``
      （与 ``InMemoryStore`` 的 ``InvariantViolationError`` 对称，但本类不引入该异常
      以免跨模块耦合，用内置 ``ValueError``）。
    """

    def __init__(self) -> None:
        self._links: dict[str, CrossModalLink] = {}

    def add(
        self,
        link: CrossModalLink,
        known_episode_ids: set[str],
        known_evidence_ids: set[str] | None = None,
    ) -> tuple[bool, CrossModalLink]:
        """写入一条关联边，强制悬空引用校验。

        参数：
        - ``link``：待写入的 ``CrossModalLink``。
        - ``known_episode_ids``：当前已知的 episode ``record_id`` 全集（用于悬空校验）。
        - ``known_evidence_ids``：已知 ``evidence_id`` 全集；``None`` 表示跳过
          ``supporting_evidence_ids`` 校验（调用方不掌握证据库时不强制）。

        返回 ``(is_new, link)``。

         raises:
        - ``DanglingReferenceError``：``episode_ids`` / ``supporting_evidence_ids`` 含
          未知 id（拒绝静默落库）。
        - ``ValueError``：相同 ``link_id`` 但内容不同（单调性冲突）。
        """
        # D5 悬空引用校验：episode_ids 必须全部已知
        unknown_eps = [e for e in link.episode_ids if e not in known_episode_ids]
        if unknown_eps:
            raise DanglingReferenceError(
                f"CrossModalLink {link.link_id} 引用不存在的 episode_id：{unknown_eps}"
            )
        # D5 悬空引用校验：supporting_evidence_ids（当调用方提供证据库时）
        if known_evidence_ids is not None:
            unknown_ev = [e for e in link.supporting_evidence_ids if e not in known_evidence_ids]
            if unknown_ev:
                raise DanglingReferenceError(
                    f"CrossModalLink {link.link_id} 引用不存在的 evidence_id：{unknown_ev}"
                )

        existing = self._links.get(link.link_id)
        if existing is not None:
            if self._link_fields_equal(existing, link):
                return False, existing
            raise ValueError(
                f"CrossModalLink {link.link_id} 已存在且内容不同，禁止覆写（单调性）"
            )
        self._links[link.link_id] = link
        return True, link

    def get_links_by_episode(self, record_id: str) -> list[CrossModalLink]:
        """返回所有引用了 ``record_id`` 的关联边（按 link_id 排序，确定性）。"""
        result = [lk for lk in self._links.values() if record_id in lk.episode_ids]
        result.sort(key=lambda l: l.link_id)
        return result

    def all_links(self) -> list[CrossModalLink]:
        """全部关联边（按 link_id 排序，确定性）。"""
        return sorted(self._links.values(), key=lambda l: l.link_id)

    def link_count(self) -> int:
        return len(self._links)

    def snapshot(self) -> list[dict]:
        """导出全部关联边为 dict 列表（确定性排序），用于回放基线 / 持久化。"""
        return [lk.to_dict() for lk in self.all_links()]

    def restore(self, data: list[dict]) -> None:
        """从快照恢复（信任已校验数据；恢复阶段不再做悬空校验）。"""
        self._links.clear()
        for d in data:
            lk = CrossModalLink.from_dict(d)
            self._links[lk.link_id] = lk

    def clear(self) -> None:
        self._links.clear()

    @staticmethod
    def _link_fields_equal(a: CrossModalLink, b: CrossModalLink) -> bool:
        if a.link_id != b.link_id:
            return False
        if a.episode_ids != b.episode_ids:
            return False
        if a.relationship != b.relationship:
            return False
        if a.supporting_evidence_ids != b.supporting_evidence_ids:
            return False
        if a.confidence != b.confidence:
            return False
        if a.time_overlap != b.time_overlap:
            return False
        # created_at 是确定性派生（linker 取离场 max），也应一致；不一致即非幂等
        return a.created_at == b.created_at


class CrossModalLinker:
    """跨模态关联器（ADR-0027 D5 + ADR-0028 D2 修订，确定性强、无随机 id）。

    规则（v1 启发式，权重归约细节归融合 ADR ADR-0026 §10 开放项）：
    1. **候选上下文（candidate_context）**：两个 episode 共享 ``visitor_instance_id``
       （均非 None 且相等）**或**共享 ``device_id``（均非 None 且相等，ADR-0028 D1
       部署源标识）→ 视为同一观察上下文。
       （ADR-0028 D2 修订：**``audio_session_id`` 不再参与跨模态身份**——它是音频
       会话身份（时间窗标识）非世界实体身份，参与关联会削弱 D4 匿名；只留在音频
       域内部聚合/溯源。）
    2. **时间窗重叠**：``min(leave) > max(enter)`` 才关联（严格重叠；受
       ``overlap_tolerance`` 与 ``min_overlap_seconds`` 门控，D3）。
    3. **relationship**：两 episode 的 ``modalities`` 集合不同 → ``SUPPORTS``（跨模态支撑）；
       相同 → ``CO_OCCURS``（同主体合证）。
    4. **confidence**：``overlap_seconds / min(duration_a, duration_b)``，clamp [0,1]
       （重叠越充分、关联越强；不表示风险）。
    5. **link_id**：``f"link-{'-'.join(sorted(episode_ids))}"``，确定性 → 可幂等 upsert。

    注意：本关联器**只读** episode，不修改任何 MemoryRecord；产出独立的 ``CrossModalLink``
    边索引。
    """

    def __init__(
        self,
        overlap_tolerance: timedelta = timedelta(0),
        min_overlap_seconds: float = 0.0,
    ) -> None:
        """``overlap_tolerance``：允许的时间窗邻接容差（v1 默认 0，未来可放宽近邻窗口）。

        ``min_overlap_seconds``（ADR-0028 D3）：重叠秒数必须 **> 阈值** 才建边
        （默认 0 = 严格重叠即关联，与 Slice C 行为一致；灰度期可收紧过滤瞬时噪音）。
        """
        if not isinstance(overlap_tolerance, timedelta):
            raise TypeError("overlap_tolerance 必须是 timedelta")
        if overlap_tolerance < timedelta(0):
            raise ValueError("overlap_tolerance 必须 >= 0")
        if not isinstance(min_overlap_seconds, (int, float)) or min_overlap_seconds < 0:
            raise ValueError("min_overlap_seconds 必须 >= 0")
        self.overlap_tolerance = overlap_tolerance
        self.min_overlap_seconds = float(min_overlap_seconds)

    def link(self, episodes: list[EpisodicRecord]) -> list[CrossModalLink]:
        """扫描 episode 集合，产出所有符合条件的跨模态关联边（按 link_id 升序）。"""
        # 仅保留具备有效时间窗与 record_id 的 episode
        valid = [e for e in episodes if self._has_window(e)]
        links: list[CrossModalLink] = []
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                a, b = valid[i], valid[j]
                if not self._candidate_context(a, b):
                    continue
                overlap = self._overlap_window(a, b)
                if overlap is None:
                    continue
                if (overlap[1] - overlap[0]).total_seconds() <= self.min_overlap_seconds:
                    continue  # D3 阈值门控：重叠不足（含等于阈值）不建边
                links.append(self._build_link(a, b, overlap))
        links.sort(key=lambda l: l.link_id)
        return links

    # ------------------------------------------------------------------
    # 内部判定
    # ------------------------------------------------------------------
    @staticmethod
    def _has_window(ep: EpisodicRecord) -> bool:
        if not ep.record_id or not ep.record_id.strip():
            return False
        # enter/leave 在 EpisodicRecord.__post_init__ 已强制 UTC；此处只要存在即有效
        return ep.enter_time is not None and ep.leave_time is not None

    @staticmethod
    def _candidate_context(a: EpisodicRecord, b: EpisodicRecord) -> bool:
        """候选上下文判定（ADR-0028 D2）：共享 visitor_instance_id 或共享 device_id。

        **audio_session_id 不参与**（会话身份≠世界实体身份，参与会削弱 D4 匿名）；
        device_id=None（旧记录/未知）不成立 → 不关联（渐进可用）。
        """
        if a.record_id == b.record_id:
            return False
        shared_visitor = (
            a.visitor_instance_id is not None
            and b.visitor_instance_id is not None
            and a.visitor_instance_id == b.visitor_instance_id
        )
        shared_device = (
            a.device_id is not None
            and b.device_id is not None
            and a.device_id == b.device_id
        )
        return bool(shared_visitor or shared_device)

    def _overlap_window(
        self, a: EpisodicRecord, b: EpisodicRecord
    ) -> tuple[datetime, datetime] | None:
        """返回严格重叠窗口 (max(enter), min(leave))；无重叠返回 None。"""
        start = max(a.enter_time, b.enter_time)
        end = min(a.leave_time, b.leave_time)
        if end <= start - self.overlap_tolerance:
            return None
        # 报告窗口用真实重叠；相切/容差邻近退化为零宽（start==end），满足 start<=end
        return (start, max(end, start))

    @staticmethod
    def _confidence(overlap: tuple[datetime, datetime], a: EpisodicRecord, b: EpisodicRecord) -> float:
        overlap_seconds = max(0.0, (overlap[1] - overlap[0]).total_seconds())
        min_dur = min(a.duration_seconds, b.duration_seconds)
        if min_dur <= 0:
            return 0.5  # 退化情形（无时长信息）：中性置信
        conf = overlap_seconds / min_dur
        if conf < 0.0:
            conf = 0.0
        elif conf > 1.0:
            conf = 1.0
        return conf

    def _build_link(
        self,
        a: EpisodicRecord,
        b: EpisodicRecord,
        overlap: tuple[datetime, datetime],
    ) -> CrossModalLink:
        episode_ids = sorted([a.record_id, b.record_id])
        link_id = "link-" + "-".join(episode_ids)
        relationship = (
            CrossModalRelationship.SUPPORTS
            if set(a.modalities) != set(b.modalities)
            else CrossModalRelationship.CO_OCCURS
        )
        confidence = self._confidence(overlap, a, b)
        created_at = max(a.leave_time, b.leave_time).astimezone(UTC)
        return CrossModalLink(
            link_id=link_id,
            episode_ids=episode_ids,
            relationship=relationship,
            time_overlap=overlap,
            confidence=confidence,
            created_at=created_at,
            supporting_evidence_ids=[],
        )


__all__ = [
    "CROSS_MODAL_LINK_DICT_KEYS",
    "CROSS_MODAL_RELATIONSHIP_VALUES",
    "CrossModalLink",
    "CrossModalLinkStore",
    "CrossModalLinker",
    "CrossModalRelationship",
    "DanglingReferenceError",
]

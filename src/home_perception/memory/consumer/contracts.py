"""Memory Consumer 数据契约（ADR-0025 §3.5 / DESIGN-memory-consumer.md §2）。

> 本模块是 **C-0（Consumer Skeleton）** 的契约部分。因 M0（数据闭环）需要固定
> ``ReasoningInput`` 形状来沉淀回放 fixture，故提前落库——**只定义数据契约**
> （dataclass + ``from_dict`` / ``to_dict``），不含 Retrieval / Aggregation /
> ContextBuilder 逻辑（那些是 M1 / C-1..C-3）。

硬约束（C1，ADR-0025 §3.9）：``ReasoningInput`` **不得**含 ``risk_score`` /
``decision`` / ``warning`` / 任何可被直接喂给 Decision 的判定字段。本模块通过
**不定义这些字段**天然满足；``test_invariants`` 另以字段白名单断言兜底。

隐私边界（ADR-0025 review 3.1 / 3.2）：
- ``device_id`` **不进入**任何契约字段（仅参与 Retrieval 排序，见 DESIGN §3.1）；
- ``VisitorProfile.identity_confirmed`` 必填（v1 恒 ``False``，对齐 ADR-0023）。

确定性（C3）：所有容器字段用 ``tuple``（不可变），组装时显式排序，保证同输入
两次产出顺序一致（回放 / 审计一致）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from home_perception.common.timeutil import require_utc
from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_explainer import CrossModalContext
from home_perception.memory.records import EpisodicRecord


# ---------------------------------------------------------------------------
# 复用类型（ADR-0024 已有）
# ---------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    require_utc(dt, "datetime")
    return dt


# ---------------------------------------------------------------------------
# ActionRecord —— 既往动作投影（ADR-0011 ActionCommand 历史）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionRecord:
    """该访客 / 模式既往被派遣的动作（ActionCommand 投影）。"""

    command_type: str
    command_id: str
    status: str
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.command_type or not self.command_type.strip():
            raise ValueError("ActionRecord.command_type 不能为空")
        if not self.command_id or not self.command_id.strip():
            raise ValueError("ActionRecord.command_id 不能为空")
        if not self.status or not self.status.strip():
            raise ValueError("ActionRecord.status 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_type": self.command_type,
            "command_id": self.command_id,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionRecord:
        return cls(
            command_type=data["command_type"],
            command_id=data["command_id"],
            status=data["status"],
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# CurrentEvent —— 当前触发事件（ADR-0021 运行时对象轻量投影）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CurrentEvent:
    """当前触发 Consumer 的事件（VisitorEvent 或 RiskSignal 的投影）。

    - ``risk_level``：仅当前实时信号的等级（HIGH / MEDIUM / LOW / None），
      **不是** Consumer 计算的分数（C1 仅禁止 Memory 派生的 score）。
    - ``markers``：行为标记（如 ``"night"`` / ``"observe_camera"``），用于驱动
      冲突 / 升级判定；是事件的客观属性，非评分。
    """

    event_id: str
    event_type: str  # "visitor_event" | "risk_signal"
    visitor_instance_id: str
    occurred_at: datetime
    risk_level: str | None = None
    markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValueError("CurrentEvent.event_id 不能为空")
        if not self.event_type or not self.event_type.strip():
            raise ValueError("CurrentEvent.event_type 不能为空")
        if not self.visitor_instance_id or not self.visitor_instance_id.strip():
            raise ValueError("CurrentEvent.visitor_instance_id 不能为空")
        require_utc(self.occurred_at, "occurred_at")
        if self.risk_level is not None and self.risk_level not in (
            "LOW",
            "MEDIUM",
            "HIGH",
        ):
            raise ValueError(
                f"CurrentEvent.risk_level 必须是 LOW/MEDIUM/HIGH/None，收到 {self.risk_level!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "visitor_instance_id": self.visitor_instance_id,
            "occurred_at": _iso(self.occurred_at),
            "risk_level": self.risk_level,
            "markers": list(self.markers),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentEvent:
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            visitor_instance_id=data["visitor_instance_id"],
            occurred_at=_parse_dt(data["occurred_at"]),
            risk_level=data.get("risk_level"),
            markers=tuple(data.get("markers", [])),
        )


# ---------------------------------------------------------------------------
# VisitorProfile —— 访客长期画像（Aggregation 计算）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VisitorProfile:
    """访客长期画像（**非分数**，是统计描述）。

    ``identity_confirmed`` 必填（ADR-0023）：v1 临时画像恒为 ``False``，Reasoning
    不得把临时画像当作真实身份画像使用。
    """

    visitor_instance_id: str
    visit_count: int
    night_visit_ratio: float
    confidence: str  # "cold_start" | "weak_pattern" | "stable_pattern"
    identity_confirmed: bool  # 必填（ADR-0023）
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def __post_init__(self) -> None:
        if not self.visitor_instance_id or not self.visitor_instance_id.strip():
            raise ValueError("VisitorProfile.visitor_instance_id 不能为空")
        if self.visit_count < 0:
            raise ValueError("VisitorProfile.visit_count 必须 >= 0")
        if not (0.0 <= float(self.night_visit_ratio) <= 1.0):
            raise ValueError("VisitorProfile.night_visit_ratio 必须在 [0, 1]")
        if self.confidence not in ("cold_start", "weak_pattern", "stable_pattern"):
            raise ValueError(
                f"VisitorProfile.confidence 必须是 cold_start/weak_pattern/stable_pattern，"
                f"收到 {self.confidence!r}"
            )
        if self.first_seen is not None:
            require_utc(self.first_seen, "first_seen")
        if self.last_seen is not None:
            require_utc(self.last_seen, "last_seen")

    def to_dict(self) -> dict[str, Any]:
        return {
            "visitor_instance_id": self.visitor_instance_id,
            "visit_count": self.visit_count,
            "night_visit_ratio": self.night_visit_ratio,
            "confidence": self.confidence,
            "identity_confirmed": self.identity_confirmed,
            "first_seen": _iso(self.first_seen) if self.first_seen else None,
            "last_seen": _iso(self.last_seen) if self.last_seen else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisitorProfile:
        return cls(
            visitor_instance_id=data["visitor_instance_id"],
            visit_count=data["visit_count"],
            night_visit_ratio=data["night_visit_ratio"],
            confidence=data["confidence"],
            identity_confirmed=data["identity_confirmed"],
            first_seen=_parse_dt(data["first_seen"]) if data.get("first_seen") else None,
            last_seen=_parse_dt(data["last_seen"]) if data.get("last_seen") else None,
        )


# ---------------------------------------------------------------------------
# RiskPattern —— 风险模式（非分数）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskPattern:
    """风险模式描述（**非分数**，如 ``repeated_visit`` / ``escalating_behavior``）。

    ADR-0027 D6（audio-aware）：``audio_patterns`` 是**纯描述性**音频模式标签
    （如 ``"telephone"`` / ``"crying"``），只陈述"历史发生了什么"，**绝不**是风险分
    （不变式：Consumer 不产出 / 不改 Risk Score，ADR-0025 C1）。``audio_episode_ratio``
    为音频 episode 在召回记录中的占比，仅作**排序提示**，**不是决策输入**；
    无法计算时（如记录缺 ``modalities`` 元数据）为 ``None``。
    """

    tags: tuple[str, ...]  # 模式标签（非分数）
    escalation_history: tuple[str, ...] | None = None
    confidence: str = "weak_pattern"
    audio_patterns: tuple[str, ...] = ()  # D6：音频模式描述标签（纯描述，非分数）
    audio_episode_ratio: float | None = None  # D6：音频 episode 占比（排序提示，非决策输入）

    def __post_init__(self) -> None:
        if self.confidence not in ("cold_start", "weak_pattern", "stable_pattern"):
            raise ValueError(
                f"RiskPattern.confidence 必须是 cold_start/weak_pattern/stable_pattern，"
                f"收到 {self.confidence!r}"
            )
        for label in self.audio_patterns:
            if not isinstance(label, str) or not label.strip():
                raise ValueError(
                    "RiskPattern.audio_patterns 元素必须是非空字符串（音频描述标签，"
                    f"非分数）；收到 {label!r}"
                )
        if self.audio_episode_ratio is not None and (
            # 必须先 isfinite 再范围检查：NaN 的比较恒为 False，会绕过 [0,1] 校验
            not math.isfinite(self.audio_episode_ratio)
            or not (0.0 <= self.audio_episode_ratio <= 1.0)
        ):
            raise ValueError(
                f"RiskPattern.audio_episode_ratio 必须是 [0, 1] 内有限值或 None，"
                f"收到 {self.audio_episode_ratio!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tags": list(self.tags),
            "escalation_history": list(self.escalation_history)
            if self.escalation_history is not None
            else None,
            "confidence": self.confidence,
            "audio_patterns": list(self.audio_patterns),
            "audio_episode_ratio": self.audio_episode_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskPattern:
        esc = data.get("escalation_history")
        return cls(
            tags=tuple(data["tags"]),
            escalation_history=tuple(esc) if esc is not None else None,
            confidence=data.get("confidence", "weak_pattern"),
            audio_patterns=tuple(data.get("audio_patterns", [])),
            audio_episode_ratio=data.get("audio_episode_ratio"),
        )


# ---------------------------------------------------------------------------
# ConflictFlag —— 冲突标记（ADR-0025 §3.6，显式具名字段）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConflictFlag:
    """历史与当前的冲突标记（**不解决、不覆盖**，交 Reasoning 推理）。

    字段显式具名（review 2.5）：不得退化为单一 ``type: str``，否则 C4 验证拿不到
    「新旧并存」的细节。
    """

    type: str  # 冲突类型，如 "behavior_shift"
    historical: str  # 历史侧描述，如 "normal"
    current: str  # 当前侧描述，如 "abnormal"
    detail: str  # 人类可读细节

    def __post_init__(self) -> None:
        if not self.type or not self.type.strip():
            raise ValueError("ConflictFlag.type 不能为空")
        if not self.historical or not self.historical.strip():
            raise ValueError("ConflictFlag.historical 不能为空")
        if not self.current or not self.current.strip():
            raise ValueError("ConflictFlag.current 不能为空")
        if not self.detail or not self.detail.strip():
            raise ValueError("ConflictFlag.detail 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "historical": self.historical,
            "current": self.current,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConflictFlag:
        return cls(
            type=data["type"],
            historical=data["historical"],
            current=data["current"],
            detail=data["detail"],
        )


# ---------------------------------------------------------------------------
# SourceRef —— 溯源引用（ReasoningResult → ReasoningInput 字段锚定，C-6 新增）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceRef:
    """回溯引用：把 ``ReasoningResult`` 的一项发现锚定到 ``ReasoningInput`` 的具体字段/对象。

    供 ADR-0024 I4 可解释性 / ADR-0025 C5 溯源：每条 finding 都能追到"它从 ReasoningInput
    的哪个字段、哪个具体对象来"。纯描述，不携带任何判定（C1）。
    """

    source: str  # ReasoningInput 字段名（current_event / visitor_profile / risk_pattern /
                 # conflicts / previous_actions / historical_context / evidence_refs）
    ref: str | None = None  # 该字段内的具体 id/键（visitor_instance_id / conflict.type /
                            # record_id / command_id），可选
    detail: str | None = None  # 人类可读说明（如 "visit_count=5,night_visit_ratio=1.0"），可选

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise ValueError("SourceRef.source 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "ref": self.ref, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(
            source=data["source"],
            ref=data.get("ref"),
            detail=data.get("detail"),
        )


# ---------------------------------------------------------------------------
# ReasoningInput —— Consumer 输出（Context Builder 产物，C-0 契约）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReasoningInput:
    """Context Builder 产物，交付给 Reasoning Engine（**不是**决策、不含量分数）。

    硬约束（C1）：本 dataclass **不存在** ``risk_score`` / ``decision`` / ``warning``
    字段；``test_invariants`` 以字段白名单断言兜底。
    """

    current_event: CurrentEvent
    historical_context: tuple[EpisodicRecord, ...]
    visitor_profile: VisitorProfile | None
    risk_pattern: RiskPattern | None
    evidence_refs: tuple[str, ...] = ()
    previous_actions: tuple[ActionRecord, ...] = ()
    conflicts: tuple[ConflictFlag, ...] = ()
    # D6（ADR-0027）：历史上下文中出现的证据模态提示（如含 AUDIO 表示有音频 episode）。
    # 纯提示字段：帮助 Reasoning 感知"该上下文中出现了哪些模态"，**不携带任何判定**；
    # 音频 EvidenceItem 的实际消费仍经 ``evidence_refs``（evidence_id）解析。
    modalities: tuple[EvidenceModality, ...] = ()
    # ADR-0029 D4（CrossModal Memory Retrieval & Explanation）：当前访客相关的跨模态
    # 解释**上下文**列表（``CrossModalContext``，非 ``CrossModalLink``）。纯描述性、
    # 可溯源、零判定——Consumer 只附加，不修改任何既有字段、不产结论。默认空元组，
    # 不影响 C1/C2/C3；与白名单 ``REASONING_INPUT_FIELD_WHITELIST`` 同步（Slice B）。
    cross_modal_contexts: tuple[CrossModalContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_event": self.current_event.to_dict(),
            "historical_context": [ep.to_dict() for ep in self.historical_context],
            "visitor_profile": self.visitor_profile.to_dict()
            if self.visitor_profile is not None
            else None,
            "risk_pattern": self.risk_pattern.to_dict()
            if self.risk_pattern is not None
            else None,
            "evidence_refs": list(self.evidence_refs),
            "previous_actions": [a.to_dict() for a in self.previous_actions],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "modalities": [m.value for m in self.modalities],
            "cross_modal_contexts": [c.to_dict() for c in self.cross_modal_contexts],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReasoningInput:
        return cls(
            current_event=CurrentEvent.from_dict(data["current_event"]),
            historical_context=tuple(
                EpisodicRecord.from_dict(ep) for ep in data["historical_context"]
            ),
            visitor_profile=VisitorProfile.from_dict(data["visitor_profile"])
            if data.get("visitor_profile") is not None
            else None,
            risk_pattern=RiskPattern.from_dict(data["risk_pattern"])
            if data.get("risk_pattern") is not None
            else None,
            evidence_refs=tuple(data.get("evidence_refs", [])),
            previous_actions=tuple(
                ActionRecord.from_dict(a) for a in data.get("previous_actions", [])
            ),
            conflicts=tuple(ConflictFlag.from_dict(c) for c in data.get("conflicts", [])),
            # D6 向后兼容：旧 v1 事件无 modalities 键 → 空元组（对齐 D8「旧事件 modalities=[]」）
            modalities=tuple(EvidenceModality(m) for m in data.get("modalities", [])),
            # ADR-0029 D4 向后兼容：旧事件无 cross_modal_contexts 键 → 空元组
            cross_modal_contexts=tuple(
                CrossModalContext.from_dict(c) for c in data.get("cross_modal_contexts", [])
            ),
        )


# ---------------------------------------------------------------------------
# ReasoningResult —— Reasoning Engine 产出（参考推理，C-6 新增；**非决策、非分数**）
# ---------------------------------------------------------------------------
# 推荐的 advisory action 词汇（与 WarningEvent.recommended_action / ActionCommand 路由一致，
# 见 action/command.py / dispatcher.py）：仅为提示，最终是否采用由 ADR-0010 DecisionPolicy 决定。
RECOMMENDED_ACTION_HINTS: tuple[str, ...] = ("MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY")


@dataclass(frozen=True)
class ReasoningResult:
    """Reasoning Engine 产出（参考推理，**非决策、非分数**）。

    硬约束（ADR-0010 单一决策中心 / ADR-0025 C1）：本 dataclass **不存在** ``risk_score`` /
    ``decision`` / ``warning`` 字段；``test_reasoning`` 另以字段白名单断言兜底。
    ``suggested_action_hint`` 仅是**非绑定**建议，仅供 Decision 参考，绝不替代决策。

    确定性（C3）：``findings`` / ``source_refs`` 显式按固定顺序构造，同输入两次产出一致
    （审计 / 回放一致）。
    """

    findings: tuple[str, ...]  # 推理发现（人类可读）
    explanation: str  # 可解释说明（继承 ADR-0024 Trust Layer）
    suggested_action_hint: str | None = None  # 非绑定建议（MONITOR/NOTIFY_FAMILY/ESCALATE_COMMUNITY）
    source_refs: tuple[SourceRef, ...] = ()  # 回溯到 ReasoningInput 字段（C5 溯源）

    def __post_init__(self) -> None:
        if not self.findings:
            raise ValueError("ReasoningResult.findings 不能为空")
        if not self.explanation or not self.explanation.strip():
            raise ValueError("ReasoningResult.explanation 不能为空")
        if (
            self.suggested_action_hint is not None
            and self.suggested_action_hint not in RECOMMENDED_ACTION_HINTS
        ):
            raise ValueError(
                f"suggested_action_hint 必须是 {RECOMMENDED_ACTION_HINTS} 之一或 None，"
                f"收到 {self.suggested_action_hint!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": list(self.findings),
            "explanation": self.explanation,
            "suggested_action_hint": self.suggested_action_hint,
            "source_refs": [s.to_dict() for s in self.source_refs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReasoningResult:
        return cls(
            findings=tuple(data["findings"]),
            explanation=data["explanation"],
            suggested_action_hint=data.get("suggested_action_hint"),
            source_refs=tuple(SourceRef.from_dict(s) for s in data.get("source_refs", [])),
        )


__all__ = [
    "RECOMMENDED_ACTION_HINTS",
    "ActionRecord",
    "ConflictFlag",
    "CurrentEvent",
    "ReasoningInput",
    "ReasoningResult",
    "RiskPattern",
    "SourceRef",
    "VisitorProfile",
]

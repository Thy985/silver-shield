"""决策审计血缘契约（ADR-0031 · Slice A · `DecisionTrace`）。

> **`DecisionTrace` 是「为什么报警 / 为什么没报警」的可审计事实链。**
> 它是 ADR-0030 Slice C（Memory → Decision 受控验证门）的硬性前置：没有完整 trace
> 就无法做 `baseline`（perception-only）vs `candidate`（perception+memory）的可审计
> 双轨比较；而安全系统最致命的失败模式——**漏报（SUPPRESS）**——在旧架构下物理不可观测。

设计要点（与 `decision_contract.py` 同构，见 ADR-0031）：

- **零行为变化（Slice A）**：本模块**只定义契约**，不改 `DecisionPolicy` / `DecisionEngine`
  任何行为，不接任何运行时。采集接缝（recorder）与抑制留痕归 Slice B / C；双轨载体
  `DecisionABRun` 归 Slice D；落盘归 Slice E。每条切片独立 PR、独立评审。
- **D1 覆盖空决策**：`outcome` 是 `WARN | SUPPRESS` 带标签联合（`TraceOutcome`），构造期
  校验互斥——`None` 不再是「无痕丢失」，而是 `SUPPRESS` + `SuppressReason`。
- **D2 五个具名 Bundle + 导入期 fail-closed**：顶层字段必须属于白名单 Bundle 集合
  （当前最小集合 = 5）。任何平铺字段 / 越界 Bundle 在 `import` 瞬间抛错，强制走 ADR 评审。
  `5` 是「当前最小集合」而非终态冻结；新增 Bundle 须经 ADR 评审扩充白名单。
- **D3 `considered_candidates` 取代 `rejected_actions`**：记录事实不构造反事实；
  `CandidateRecord` 是**闭集**（5 个结构化字段），严禁塞入解释文本（Non-goal #8）。
- **D4 引用而非复制**：`trigger_refs` 用「C3 规范化后下标 + 三元组」绕开缺失的
  `PerceptionEvent.event_id`（债务显式登记，不夹带 Level 1 Schema 变更）；`memory_refs`
  只存 ID 与 hint，不内嵌完整 `ReasoningInput` / `PerceptionEvent`。
- **D5 `policy.fingerprint`**：实际生效路由表的规范化摘要，取代硬编码 `"v1"`（T10）。
- **T4 无判定字段**：禁 `fraud` / `suspect` / `verdict` 等；导入期 fail-closed 断言。

循环导入规避：本模块仅 `TYPE_CHECKING` 注解 `DecisionInput` / `PerceptionEvent`，运行期不
import `decision_contract`（摘要辅助函数内惰性调用实例方法，无需 import 类）。
`decision_trace → decision_policy`（取 `LEVEL_PRIORITY`）为单向、无环。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover - 仅供类型注解；运行期不求值，避免与 decision_contract 耦合
    from .decision_contract import DecisionInput
    from .perception import PerceptionEvent

# LEVEL_PRIORITY 是稳定的路由优先级常量，单向 import（decision_policy 不反向 import 本模块）。
from .decision_policy import LEVEL_PRIORITY

# ============================================================================
# 枚举：抑制原因 / 结果类型（ADR-0031 D1）
# ============================================================================


class SuppressReason(str, Enum):
    """SUPPRESS 的三类原因，**严格派生自 `RuleBasedDecisionPolicy.decide` 的三条返回点**。

    未来新增抑制路径 MUST 同步新增枚举值（T7 契约测试遍历策略返回点做覆盖断言）。
    """

    NO_TRIGGER_EVENTS = "no_trigger_events"  # `if not perception_events: return None`
    ALL_SUPPRESSED_NORMAL = "all_suppressed_normal"  # `if not candidates: return None`
    UNROUTABLE_EVENT_TYPE = "unroutable_event_type"  # 路由表未命中 `decision is None`


class TraceOutcomeKind(str, Enum):
    """`outcome` 带标签联合的标签。"""

    WARN = "WARN"
    SUPPRESS = "SUPPRESS"


# ============================================================================
# 契约白名单 / 禁止字段（D2 + T4，导入期 fail-closed）
# ============================================================================

# D2（防 God Object，类比 ADR-0030 C7）：`DecisionTrace` 顶层只允许这 5 个语义正交的
# 具名 Bundle。新增 Bundle 须先走 ADR 评审扩充白名单，**禁止**退化为横向平铺字段。
# `5` 是当前最小集合（非终态冻结）——未来 `comparison` / `environment` / `timing` 等
# 可经评审加入，但必须满足「该审计问题无法由既有 Bundle 表达」。
DECISION_TRACE_FIELD_WHITELIST: frozenset[str] = frozenset(
    {
        "identity",
        "provenance",
        "policy",
        "rationale",
        "outcome",
    }
)

# T4（ADR-0001 仅产事实不裁决）：trace 不得含任何「最终判定 / 诈骗认定」语义字段。
# 顶层 Bundle 名不得是这些词；序列化产物亦不得出现这些键（由契约测试递归扫描钉死）。
DECISION_TRACE_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "fraud",
        "suspect",
        "verdict",
        "is_fraud",
        "is_scammer",
        "is_criminal",
        "decision",
        "risk_score",
        "score",
        "final_decision",
        "crime_probability",
        "guilt_score",
    }
)

# D2：`arm` 取值白名单（决策层双轨载体）。
TRACE_ARMS: frozenset[str] = frozenset({"production", "baseline", "candidate"})


# ============================================================================
# Bundle：identity
# ============================================================================


@dataclass(frozen=True)
class TraceIdentity:
    """一次决策的标识（D2）。

    - `decision_id`：每次 `decide()` 唯一（UUID），**不复用** `warning_id`（SUPPRESS 时无
      `WarningEvent`）。
    - `correlation_id`：同一评估周期共享；双轨比较时两臂 `correlation_id` 相同、`decision_id`
      不同。这正面回答 ADR-0030 §5 开放问题（归 trace，不归 `DecisionInput`）。
    - `arm`：`production` / `baseline` / `candidate`，默认 `production`（Slice C 双轨载体）。
    - `created_at`：trace 创建时刻（UTC）。属 identity，T6 确定性排除项。
    """

    decision_id: str
    correlation_id: str
    arm: str = "production"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.arm not in TRACE_ARMS:
            raise ValueError(
                f"TraceIdentity.arm 必须是 {sorted(TRACE_ARMS)} 之一，收到 {self.arm!r}"
            )

    @classmethod
    def new(cls, arm: str = "production", correlation_id: str = "") -> TraceIdentity:
        """便捷的运行时构造：生成唯一 `decision_id` 与当前 `created_at`。"""
        return cls(
            decision_id=uuid4().hex,
            correlation_id=correlation_id,
            arm=arm,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "correlation_id": self.correlation_id,
            "arm": self.arm,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TraceIdentity:
        return cls(
            decision_id=str(data["decision_id"]),
            correlation_id=str(data["correlation_id"]),
            arm=str(data.get("arm", "production")),
            created_at=datetime.fromisoformat(str(data["created_at"])),
        )


# ============================================================================
# Bundle：provenance（引用 + digest，不复制对象，D4 / T8）
# ============================================================================


@dataclass(frozen=True)
class TriggerRef:
    """对 `PerceptionEvent` 的引用（D4）。

    `index` = **C3 规范化之后** `trigger_events` 元组下标（确定、可回放）；配合
    `(visitor_id, event_type, timestamp)` 三元组在实践中足以定位。不引入
    `PerceptionEvent.event_id`（那是 Level 1 Schema 变更，本 ADR 绕行并显式登记债务）。
    """

    index: int
    visitor_id: str
    event_type: str
    timestamp: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TriggerRef:
        return cls(
            index=int(data["index"]),  # type: ignore[arg-type]
            visitor_id=str(data["visitor_id"]),
            event_type=str(data["event_type"]),
            timestamp=float(data["timestamp"]),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class MemoryRefs:
    """对 Memory / Reasoning 产物的纯引用（D4，沿用 ADR-0027 D2「证据以 ID 引用」）。

    `suggested_action_hint` 入 trace 是有意的：它让 ADR-0030 **C1「hint 只参与排序、永不
    权威」在事后可被证明**——审计者对比 `hint` 与 `outcome.recommended_action` 即可发现越权。
    记录 hint 不等于服从 hint（HintObservation ≠ HintAuthority）。
    """

    reasoning_input_present: bool = False
    reasoning_result_present: bool = False
    historical_record_ids: tuple[str, ...] = ()
    cross_modal_link_ids: tuple[str, ...] = ()
    evidence_ref_ids: tuple[str, ...] = ()
    suggested_action_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "reasoning_input_present": self.reasoning_input_present,
            "reasoning_result_present": self.reasoning_result_present,
            "historical_record_ids": list(self.historical_record_ids),
            "cross_modal_link_ids": list(self.cross_modal_link_ids),
            "evidence_ref_ids": list(self.evidence_ref_ids),
            "suggested_action_hint": self.suggested_action_hint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MemoryRefs:
        return cls(
            reasoning_input_present=bool(data.get("reasoning_input_present", False)),
            reasoning_result_present=bool(data.get("reasoning_result_present", False)),
            historical_record_ids=tuple(data.get("historical_record_ids") or ()),  # type: ignore[arg-type]
            cross_modal_link_ids=tuple(data.get("cross_modal_link_ids") or ()),  # type: ignore[arg-type]
            evidence_ref_ids=tuple(data.get("evidence_ref_ids") or ()),  # type: ignore[arg-type]
            suggested_action_hint=data.get("suggested_action_hint"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class TraceProvenance:
    """本次决策的出处：输入摘要 + 触发事件引用 + Memory 引用（D4 / T8）。"""

    input_digest: str
    trigger_digest: str
    trigger_refs: tuple[TriggerRef, ...] = ()
    memory_refs: MemoryRefs = field(default_factory=MemoryRefs)

    def to_dict(self) -> dict[str, object]:
        return {
            "input_digest": self.input_digest,
            "trigger_digest": self.trigger_digest,
            "trigger_refs": [r.to_dict() for r in self.trigger_refs],
            "memory_refs": self.memory_refs.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TraceProvenance:
        return cls(
            input_digest=str(data["input_digest"]),
            trigger_digest=str(data["trigger_digest"]),
            trigger_refs=tuple(
                TriggerRef.from_dict(r) for r in (data.get("trigger_refs") or ())  # type: ignore[arg-type]
            ),
            memory_refs=MemoryRefs.from_dict(
                data.get("memory_refs") or {}  # type: ignore[arg-type]
            ),
        )


# ============================================================================
# Bundle：policy（D5 fingerprint）
# ============================================================================


@dataclass(frozen=True)
class TracePolicy:
    """生效策略的标识（D5）。

    `fingerprint` = 实际 `routing_table` 的规范化摘要（非人工标签 `"v1"`），
    任何定制路由表自动得到不同摘要，使审计/回放可判定「两条 trace 是否出自同一策略配置」。
    """

    name: str
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TracePolicy:
        return cls(name=str(data["name"]), fingerprint=str(data["fingerprint"]))


# ============================================================================
# Bundle：rationale（D3 事实候选，闭集）
# ============================================================================


@dataclass(frozen=True)
class CandidateRecord:
    """一条被策略实际纳入考虑的候选（D3）。

    **闭集**——只允许这 5 个结构化字段（`trigger_index` / `event_type` / `routed_level` /
    `routed_action` / `priority`）。严禁塞入任何自由文本 / 解释 / 评分明细 / 原始证据
    （如 `reasoning_text` / `explanation` / `score_detail` / `raw_evidence`）——
    那属 Explanation / Simulation 层（Non-goal #8）。`considered_candidates` 承载的是
    "实际发生了什么"，不是"应该怎么解释"。
    """

    trigger_index: int
    event_type: str
    routed_level: str
    routed_action: str
    priority: int

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_index": self.trigger_index,
            "event_type": self.event_type,
            "routed_level": self.routed_level,
            "routed_action": self.routed_action,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CandidateRecord:
        return cls(
            trigger_index=int(data["trigger_index"]),  # type: ignore[arg-type]
            event_type=str(data["event_type"]),
            routed_level=str(data["routed_level"]),
            routed_action=str(data["routed_action"]),
            priority=int(data["priority"]),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class TraceRationale:
    """"为什么选 A 不选 B" 的事实记录（D3，取代 `rejected_actions`）。

    `considered_candidates` 是 `decide()` 内已存在的中间态（候选列表 / 查表结果），零逻辑
    改动即可采集；顺序 = C3 规范化后次序（T9 确定性）。`chosen_index` 在 SUPPRESS 时为 `None`。
    """

    considered_candidates: tuple[CandidateRecord, ...] = ()
    chosen_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "considered_candidates": [c.to_dict() for c in self.considered_candidates],
            "chosen_index": self.chosen_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TraceRationale:
        raw = data.get("considered_candidates") or ()
        return cls(
            considered_candidates=tuple(CandidateRecord.from_dict(c) for c in raw),  # type: ignore[arg-type]
            chosen_index=data.get("chosen_index"),  # type: ignore[arg-type]
        )


# ============================================================================
# Bundle：outcome（D1 带标签联合，互斥校验）
# ============================================================================


@dataclass(frozen=True)
class TraceOutcome:
    """决策结果（D1）：`WARN` 或 `SUPPRESS` 带标签联合。

    WARN 路径携带决策产物（`risk_level` / `recommended_action` / `warning_id`）——这些是
    **输出侧记录**，不违反 ADR-0030 C1（C1 禁的是决策语义进入**输入**）。SUPPRESS 路径携带
    `suppress_reason`。构造期校验互斥：WARN 不得带 `suppress_reason`，SUPPRESS 不得带 WARN 字段。
    """

    kind: TraceOutcomeKind
    risk_level: str | None = None
    recommended_action: str | None = None
    warning_id: str | None = None
    suppress_reason: SuppressReason | None = None

    def __post_init__(self) -> None:
        if self.kind == TraceOutcomeKind.WARN:
            if self.suppress_reason is not None:
                raise ValueError("TraceOutcome(WARN) 不得携带 suppress_reason")
            if not self.risk_level or not self.recommended_action or not self.warning_id:
                raise ValueError(
                    "TraceOutcome(WARN) 必须提供 risk_level / recommended_action / warning_id"
                )
        elif self.kind == TraceOutcomeKind.SUPPRESS:
            if self.suppress_reason is None:
                raise ValueError("TraceOutcome(SUPPRESS) 必须提供 suppress_reason")
            if (
                self.risk_level is not None
                or self.recommended_action is not None
                or self.warning_id is not None
            ):
                raise ValueError("TraceOutcome(SUPPRESS) 不得携带 WARN 字段")
        else:
            raise ValueError(f"未知 TraceOutcomeKind: {self.kind!r}")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind.value}
        if self.kind == TraceOutcomeKind.WARN:
            payload["risk_level"] = self.risk_level
            payload["recommended_action"] = self.recommended_action
            payload["warning_id"] = self.warning_id
        else:
            payload["suppress_reason"] = self.suppress_reason.value if self.suppress_reason else None
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TraceOutcome:
        kind = TraceOutcomeKind(str(data["kind"]))
        if kind == TraceOutcomeKind.WARN:
            return cls(
                kind=kind,
                risk_level=data.get("risk_level"),  # type: ignore[arg-type]
                recommended_action=data.get("recommended_action"),  # type: ignore[arg-type]
                warning_id=data.get("warning_id"),  # type: ignore[arg-type]
            )
        raw = data.get("suppress_reason")
        return cls(
            kind=kind,
            suppress_reason=SuppressReason(str(raw)) if raw is not None else None,
        )


# ============================================================================
# 顶层：DecisionTrace
# ============================================================================


@dataclass(frozen=True)
class DecisionTrace:
    """一次 `DecisionPolicy.decide` 调用的完整审计血缘（ADR-0031 D2）。

    五个 Bundle 各回答一个审计问题：identity（哪一次）/ provenance（输入与引用）/
    policy（用什么规则）/ rationale（怎么选）/ outcome（最终发生什么）。
    """

    identity: TraceIdentity
    provenance: TraceProvenance
    policy: TracePolicy
    rationale: TraceRationale
    outcome: TraceOutcome

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_dict(),
            "provenance": self.provenance.to_dict(),
            "policy": self.policy.to_dict(),
            "rationale": self.rationale.to_dict(),
            "outcome": self.outcome.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> DecisionTrace:
        return cls(
            identity=TraceIdentity.from_dict(data["identity"]),  # type: ignore[arg-type]
            provenance=TraceProvenance.from_dict(data["provenance"]),  # type: ignore[arg-type]
            policy=TracePolicy.from_dict(data["policy"]),  # type: ignore[arg-type]
            rationale=TraceRationale.from_dict(data["rationale"]),  # type: ignore[arg-type]
            outcome=TraceOutcome.from_dict(data["outcome"]),  # type: ignore[arg-type]
        )


# ============================================================================
# 纯函数辅助（摘要 / 引用 / 候选构建；不改任何决策行为）
# ============================================================================


def compute_policy_fingerprint(
    routing_table: Mapping[str, tuple[str, str, str]],
) -> str:
    """计算 `policy.fingerprint`（D5 / T10）。

    用**规范化序列化**（`sort_keys=True`）——Python `dict` 键序不同
    （如 `{"a":1,"b":2}` vs `{"b":2,"a":1}`）会导致同配置不同摘要；`sort_keys=True`
    保证同配置必同摘要、异配置高概率异摘要。
    """
    canonical = json.dumps(routing_table, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_trigger_digest(trigger_events: Sequence[PerceptionEvent]) -> str:
    """`trigger_digest`：仅 `trigger_events` 部分的规范化摘要（D4）。

    顺序 = `trigger_events` 的传入顺序（即 C3 规范化后次序，**不**再排序），保证两臂
    `trigger_events` 一致时摘要一致（Slice C 双轨「唯一变量 = Memory」的机器可验证证明）。
    仅纳入身份性字段（visitor_id / event_type / timestamp / score / device_id），不含
    任何原始媒体（T5 隐私）。
    """
    canonical = json.dumps(
        [
            [str(ev.visitor_id), ev.event_type, ev.timestamp, round(ev.score, 4), ev.device_id]
            for ev in trigger_events
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_input_digest(decision_input: DecisionInput) -> str:
    """`input_digest`：`DecisionInput.to_dict()` 的规范化摘要（D4）。

    惰性调用实例方法，无需在模块加载期 import `decision_contract`。
    """
    canonical = json.dumps(decision_input.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_trigger_refs(trigger_events: Sequence[PerceptionEvent]) -> tuple[TriggerRef, ...]:
    """从（已 C3 规范化的）`trigger_events` 生成有序 `TriggerRef`（D4）。

    `index` = C3 规范化后下标，配合三元组定位（绕开缺失的 `event_id`）。
    """
    return tuple(
        TriggerRef(
            index=idx,
            visitor_id=str(ev.visitor_id),
            event_type=ev.event_type,
            timestamp=ev.timestamp,
        )
        for idx, ev in enumerate(trigger_events)
    )


def candidate_records_from_events(
    events: Sequence[PerceptionEvent],
    routing_table: Mapping[str, tuple[str, str, str]],
) -> tuple[CandidateRecord, ...]:
    """从（已 C3 规范化的）`trigger_events` 生成有序候选记录（D3 / T9）。

    顺序 = `events` 的传入顺序（即 C3 规范化后次序），**不**依赖 `set` / `dict.values()`
    等非稳定迭代——这是 T9（候选顺序确定性）的契约保证。Slice C 接线时传入
    `DecisionInput.trigger_events` 即可复用同一确定性。
    """
    records: list[CandidateRecord] = []
    for idx, ev in enumerate(events):
        route = routing_table.get(ev.event_type)
        routed_level = route[0] if route else ""
        routed_action = route[1] if route else ""
        priority = LEVEL_PRIORITY.get(routed_level, 0) if route else 0
        records.append(
            CandidateRecord(
                trigger_index=idx,
                event_type=ev.event_type,
                routed_level=routed_level,
                routed_action=routed_action,
                priority=priority,
            )
        )
    return tuple(records)


# ============================================================================
# 导入期 fail-closed 契约守卫（D2 + T4）
# ============================================================================


def _assert_contract_shape() -> None:
    """在**导入期**钉死 `DecisionTrace` 的字段形状。

    放在导入期而非 `__post_init__`：零每实例开销，且任何越界改动在
    `import home_perception.analysis.decision_trace` 的瞬间就炸——"偷偷加一个 verdict Bundle"
    不可能悄悄合入。测试另有独立断言兜底（不依赖本函数，避免自证）。
    """
    names = {f.name for f in fields(DecisionTrace)}

    forbidden = DECISION_TRACE_FORBIDDEN_FIELDS & names
    if forbidden:
        raise RuntimeError(
            f"DecisionTrace 含禁止的判定语义 Bundle {sorted(forbidden)}；"
            "决策层不做最终判定（ADR-0001 / T4），审计血缘只记录事实不记录诈骗认定"
        )

    if names != DECISION_TRACE_FIELD_WHITELIST:
        extra = sorted(names - DECISION_TRACE_FIELD_WHITELIST)
        missing = sorted(DECISION_TRACE_FIELD_WHITELIST - names)
        raise RuntimeError(
            "DecisionTrace 字段与 DECISION_TRACE_FIELD_WHITELIST 不一致（ADR-0031 D2 防膨胀）；"
            f"多出 {extra}、缺少 {missing}。新增 Bundle 须先走 ADR 评审，"
            "禁止横向平铺字段退化成 God Object。"
        )


_assert_contract_shape()


__all__ = [
    "DECISION_TRACE_FIELD_WHITELIST",
    "DECISION_TRACE_FORBIDDEN_FIELDS",
    "TRACE_ARMS",
    "CandidateRecord",
    "DecisionTrace",
    "MemoryRefs",
    "SuppressReason",
    "TraceIdentity",
    "TraceOutcome",
    "TraceOutcomeKind",
    "TracePolicy",
    "TraceProvenance",
    "TraceRationale",
    "TriggerRef",
    "build_trigger_refs",
    "candidate_records_from_events",
    "compute_input_digest",
    "compute_policy_fingerprint",
    "compute_trigger_digest",
]

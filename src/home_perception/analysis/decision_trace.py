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
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover - 仅供类型注解；运行期不求值，避免与 decision_contract 耦合
    from .decision_contract import DecisionInput
    from .perception import PerceptionEvent
    from .warning import WarningEvent

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
# 采集接缝（ADR-0031 Slice B · D6）：recorder 抽象 + WARN 路径装配
# ============================================================================


@runtime_checkable
class DecisionTraceRecorder(Protocol):
    """决策 trace 的采集接缝（D6）。

    决策层（engine / policy）只调用 `record` 把已封口 trace **写**出去，**绝不读回**
    （T1 只写不读）。`flush` 为 Slice E 落盘预留（Slice B 中 recorder 自行决定是否在
    `record` 时即持久化；`NullRecorder` / `InMemoryRecorder` 的 `flush` 为 no-op）。
    """

    def record(self, trace: DecisionTrace) -> None:
        """落一条已封口的 trace。实现 MUST NOT 抛异常影响决策（T3 失败隔离）。"""
        ...

    def flush(self) -> None:
        """可选：批量刷盘（Slice E）。Slice B 中默认 no-op。"""
        ...


class NullRecorder:
    """默认 recorder：所有方法 no-op。

    `DecisionEngine(trace_recorder=None)` 等效于此（零行为变化，T2）。存在它是为了让
    「显式注入 no-op」与「未注入」在契约上等价，便于测试与开关切换。
    """

    def record(self, trace: DecisionTrace) -> None:
        return None

    def flush(self) -> None:
        return None


@dataclass
class InMemoryRecorder:
    """测试 / Slice C 双轨用：内存累积 trace，供断言与回放（不落盘）。"""

    traces: list[DecisionTrace] = field(default_factory=list)

    def record(self, trace: DecisionTrace) -> None:
        self.traces.append(trace)

    def flush(self) -> None:
        return None

    def by_correlation_id(self, correlation_id: str) -> list[DecisionTrace]:
        return [t for t in self.traces if t.identity.correlation_id == correlation_id]

    def by_kind(self, kind: TraceOutcomeKind) -> list[DecisionTrace]:
        return [t for t in self.traces if t.outcome.kind == kind]

    @property
    def warn_traces(self) -> list[DecisionTrace]:
        return self.by_kind(TraceOutcomeKind.WARN)

    @property
    def suppress_traces(self) -> list[DecisionTrace]:
        return self.by_kind(TraceOutcomeKind.SUPPRESS)


def build_rationale(
    trigger_events: Sequence[PerceptionEvent],
    routing_table: Mapping[str, tuple[str, str, str]],
) -> TraceRationale:
    """从（已 C3 规范化的）`trigger_events` 重建候选与选中下标（D3 / T9，仅 WARN 路径用）。

    **仅用于 trace 重建，绝不回灌决策逻辑。** 过滤与选择语义与 `RuleBasedDecisionPolicy
    .decide` 一致：
    - 过滤：`visit_normal` 且非 `is_odd_hour` 叠加 → 被抑制，不进入 candidates；
    - 选择：`max(candidates, key=priority)`，首个最高优先级候选胜出（与 `decide` 的
      `max` 语义一致）。

    `trigger_index` 保留在 C3 规范化 `trigger_events` 中的原下标，便于与 `trigger_refs`
    交叉引用；`considered_candidates` 顺序 = 传入顺序（T9 确定性）。
    """
    records: list[CandidateRecord] = []
    for idx, ev in enumerate(trigger_events):
        if ev.event_type == "visit_normal" and not ev.is_odd_hour:
            continue  # 与 decide 的抑制逻辑一致：纯普通访问不进入候选
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
    chosen_index: int | None = None
    if records:
        chosen_index = max(range(len(records)), key=lambda i: records[i].priority)
    return TraceRationale(considered_candidates=tuple(records), chosen_index=chosen_index)


def build_warning_trace(
    input: DecisionInput,
    warning: WarningEvent,
    policy_name: str,
    routing_table: Mapping[str, tuple[str, str, str]],
    *,
    arm: str = "production",
    correlation_id: str = "",
) -> DecisionTrace:
    """装配一条 WARN trace（Slice B 采集接缝的核心工厂，零行为变化）。

    - `identity`：每次 `decide` 唯一 `decision_id`；`correlation_id` 由调用方决定
      （Slice B 单评估默认空，Slice C 双轨共享同一 `correlation_id`）——正面回答
      ADR-0030 §5 开放问题（归 trace，不归 `DecisionInput`）。
    - `provenance.memory_refs`：Slice B 一律空（Memory 尚未接线，ADR-0030 D2）。
    - `policy.fingerprint`：实际生效路由表摘要（D5 / T10），取代硬编码 `"v1"`。
    - `rationale`：由 `build_rationale` 从 `input.trigger_events` 重建（D3）。
    - `outcome`：WARN，携带决策产物的输出侧记录（不违反 ADR-0030 C1）。
    """
    return DecisionTrace(
        identity=TraceIdentity.new(arm=arm, correlation_id=correlation_id),
        provenance=TraceProvenance(
            input_digest=compute_input_digest(input),
            trigger_digest=compute_trigger_digest(input.trigger_events),
            trigger_refs=build_trigger_refs(input.trigger_events),
            memory_refs=MemoryRefs(),
        ),
        policy=TracePolicy(
            name=policy_name,
            fingerprint=compute_policy_fingerprint(routing_table),
        ),
        rationale=build_rationale(input.trigger_events, routing_table),
        outcome=TraceOutcome(
            kind=TraceOutcomeKind.WARN,
            risk_level=warning.risk_level,
            recommended_action=warning.recommended_action,
            warning_id=str(warning.warning_id),
        ),
    )


# ============================================================================
# Slice C（抑制留痕 · D6.1）：生命周期 span + SUPPRESS 装配工厂
# ============================================================================


@dataclass
class DecisionTraceSpan:
    """决策 trace 的**可变**中间态（ADR-0031 D6.1 COLLECTING 阶段载体）。

    `DecisionEngine.evaluate` 拥有 trace 生命周期：在 `CREATED` 阶段开 span（分配本可变
    容器），`RuleBasedDecisionPolicy.decide` 在三条 `return None` 前经本对象**写入**它
    独有的 partial——`suppress_reason` + `considered_candidates`；engine 在 `FINALIZED`
    阶段读取 partial、封口 `identity` / `outcome` 并转为不可变 `DecisionTrace`。

    单一写主铁律（D6.1）：策略**只写** `suppress_reason` / `considered_candidates`，
    **禁止**写 `identity` 或覆盖 `outcome` 封口字段——封口归 engine。本对象是可变
    dataclass（非 frozen），仅作中间态；最终落盘 / 回放的是不可变 `DecisionTrace`。
    """

    suppress_reason: SuppressReason | None = None
    considered_candidates: tuple[CandidateRecord, ...] = ()

    def reset(self) -> None:
        """清空 partial（供 engine 跨调用复用同一 policy 实例前重置）。"""
        self.suppress_reason = None
        self.considered_candidates = ()


def build_suppress_trace(
    input: DecisionInput,
    suppress_reason: SuppressReason,
    considered_candidates: Sequence[CandidateRecord],
    policy_name: str,
    routing_table: Mapping[str, tuple[str, str, str]],
    *,
    arm: str = "production",
    correlation_id: str = "",
) -> DecisionTrace:
    """装配一条 SUPPRESS trace（Slice C 核心价值：漏报首次可观测，D1）。

    - `outcome`：SUPPRESS + `suppress_reason`，`suppress_reason` 严格派生自三条真实
      返回点之一（`no_trigger_events` / `all_suppressed_normal` / `unroutable_event_type`）。
    - `rationale.considered_candidates`：来自策略在 COLLECTING 阶段写入的 partial（D3，
      事实候选、闭集）；`chosen_index` 在 SUPPRESS 时恒为 `None`（本就被抑制，无胜出者）。
    - `identity` / `provenance` / `policy`：与 WARN 路径同源（D2 / D4 / D5），保证两臂
      仅 `outcome` 不同、其余 Bundle 可机器比对（Slice D 双轨守恒前提）。
    """
    return DecisionTrace(
        identity=TraceIdentity.new(arm=arm, correlation_id=correlation_id),
        provenance=TraceProvenance(
            input_digest=compute_input_digest(input),
            trigger_digest=compute_trigger_digest(input.trigger_events),
            trigger_refs=build_trigger_refs(input.trigger_events),
            memory_refs=MemoryRefs(),
        ),
        policy=TracePolicy(
            name=policy_name,
            fingerprint=compute_policy_fingerprint(routing_table),
        ),
        rationale=TraceRationale(
            considered_candidates=tuple(considered_candidates),
            chosen_index=None,
        ),
        outcome=TraceOutcome(
            kind=TraceOutcomeKind.SUPPRESS,
            suppress_reason=suppress_reason,
        ),
    )


# ============================================================================
# Slice D（双轨载体 · D7）：DecisionABRun + 唯一变量守恒
# ============================================================================


class ABRunConservationError(Exception):
    """`DecisionABRun.assert_conserved` 失败时抛出（D7 六条守恒违反其一）。"""


@dataclass(frozen=True)
class DecisionABRun:
    """决策层 A/B 双轨运行（ADR-0031 D7）。

    对齐 reasoning 层 `ABRun`（`memory/evaluation/ab_runner.py`），补齐决策层对等物。
    两臂 MUST 满足「唯一变量守恒」（见 `assert_conserved`）：**唯一差异 = Memory，
    指定由 `correlation_id` 共享、由 trace 事后证明，而非由构造过程承诺**——这正是
    `build_baseline_input` 在 Reasoning 层做的事，在决策层的对等表达。

    `outcome.kind` 的四种配对**首次**让决策层的混淆矩阵可观测（D7）：
    `SUPPRESS×WARN`（Memory 唤醒一次漏报）/ `WARN×SUPPRESS`（压制一次误报）/
    `WARN×WARN`（比较 `risk_level` / `action` 是否抬升）/ `SUPPRESS×SUPPRESS`（无差异）。

    Slice D 仅提供载体与守恒校验，**不启用 Memory 接线**；用两臂 trace 装配
    `DecisionABRun` 的落地运行归 ADR-0030 Slice C。本结构本身零行为变化。

    序列化（`to_dict` / `from_dict`）归 **Slice E**（JSONL 落盘 + ADR-0030 Slice C 双轨报告
    产出）——本切片刻意只落地载体与守恒校验，不在 Slice D 引入序列化，避免与 Slice E 的
    脱敏 / 留存契约抢先耦合。

    命名注记：未来若 `MemoryABRun` / `ReasoningABRun` 等增多，可抽象为
    `EvaluationRun{EvaluationKind, ...}`；本 ADR 沿用 `DecisionABRun` 以与 reasoning
    层 `ABRun` 命名一致，不抢先泛化。
    """

    correlation_id: str
    trace_baseline: DecisionTrace
    trace_candidate: DecisionTrace

    def assert_conserved(self) -> None:
        """机器可验证的「唯一变量守恒」断言（D7 六条）。

        任一不满足即抛 `ABRunConservationError`（调试 / 测试用；使用显式异常而非
        `assert` 语句，避免被 `-O` 关闭）。两臂**仅** `outcome` 可不同（含 SUPPRESS
        vs WARN 的差异），其余 Bundle 必须一致——这正是「唯一变量 = Memory」的充要条件
        事后证明，而非构造期承诺。

        第 5 / 6 条对 `reasoning_input_present` 使用 `is not False` / `is not True` 的
        **身份比较**（fail-closed）：`MemoryRefs(reasoning_input_present=0)` 等 falsy 但
        非 `False` / `True` 的值一律视为违反，与 `from_dict` 的 `bool(...)` 宽松归一化
        刻意不同——装配层必须显式给出布尔真值，杜绝「静默降级」。
        """
        # (1) 载体 correlation_id 与两臂 identity.correlation_id 三方相等：
        #     杜绝载体自身字段静默偏离两臂（下游按 run.correlation_id 反查时取不到）。
        if not (
            self.correlation_id
            == self.trace_baseline.identity.correlation_id
            == self.trace_candidate.identity.correlation_id
        ):
            raise ABRunConservationError(
                "D7 守恒失败(1/6)：载体 correlation_id 与两臂 identity.correlation_id 必须三方相等 "
                f"({self.correlation_id!r} / "
                f"{self.trace_baseline.identity.correlation_id!r} / "
                f"{self.trace_candidate.identity.correlation_id!r})"
            )
        # (2) 两臂 decision_id 必须不同：同一条 trace 不能同时充当两臂
        #     （补强 TraceIdentity docstring 既有承诺「双轨时 decision_id 不同」）。
        if self.trace_baseline.identity.decision_id == self.trace_candidate.identity.decision_id:
            raise ABRunConservationError(
                "D7 守恒失败(2/6)：两臂 identity.decision_id 必须不同 "
                "（同一条 trace 不能同时充当 baseline 与 candidate 两臂）"
            )
        # (3) 输入 trigger 一致
        if (
            self.trace_baseline.provenance.trigger_digest
            != self.trace_candidate.provenance.trigger_digest
        ):
            raise ABRunConservationError(
                "D7 守恒失败(3/6)：两臂 provenance.trigger_digest 必须相同 "
                f"（唯一变量 = Memory，两臂输入 trigger 必须一致；"
                f"{self.trace_baseline.provenance.trigger_digest[:8]}… != "
                f"{self.trace_candidate.provenance.trigger_digest[:8]}…）"
            )
        # (4) 同一策略配置
        if self.trace_baseline.policy.fingerprint != self.trace_candidate.policy.fingerprint:
            raise ABRunConservationError(
                "D7 守恒失败(4/6)：两臂 policy.fingerprint 必须相同 "
                f"（baseline / candidate 不得用不同路由表；"
                f"{self.trace_baseline.policy.fingerprint[:8]}… != "
                f"{self.trace_candidate.policy.fingerprint[:8]}…）"
            )
        # (5) baseline = perception-only：无 Memory 输入
        if self.trace_baseline.provenance.memory_refs.reasoning_input_present is not False:
            raise ABRunConservationError(
                "D7 守恒失败(5/6)：baseline 臂 provenance.memory_refs.reasoning_input_present "
                f"必须为 False（baseline = perception-only，无 Memory 输入；"
                f"实际 {self.trace_baseline.provenance.memory_refs.reasoning_input_present!r}）"
            )
        # (6) candidate 必须**真的**携带 Memory：否则「无差异」可能是装配 bug 伪装的
        #     实验结论（ADR-0031 增补条目；落地运行方 ADR-0030 Slice C 须保证 candidate 含 Memory）。
        if self.trace_candidate.provenance.memory_refs.reasoning_input_present is not True:
            raise ABRunConservationError(
                "D7 守恒失败(6/6)：candidate 臂 provenance.memory_refs.reasoning_input_present "
                f"必须为 True（candidate 必须真的携带 Memory，否则「无差异」结论可能由装配 bug 伪装）；"
                f"实际 {self.trace_candidate.provenance.memory_refs.reasoning_input_present!r}"
            )

    @property
    def outcome_pair(self) -> tuple[TraceOutcomeKind, TraceOutcomeKind]:
        """两臂 `outcome.kind` 配对（D7 混淆矩阵可观测）。"""
        return (self.trace_baseline.outcome.kind, self.trace_candidate.outcome.kind)

    def to_dict(self) -> dict[str, object]:
        """序列化为 JSON-friendly dict（Slice E 落盘 / ADR-0030 Slice C 双轨报告产出）。

        仅存两臂 trace 的 `to_dict` 与载体 `correlation_id`；不复制任何 Bundle 语义
        （T8 不重复真相），反序列化经 `from_dict` 重建等价 `DecisionABRun`。
        """
        return {
            "correlation_id": self.correlation_id,
            "trace_baseline": self.trace_baseline.to_dict(),
            "trace_candidate": self.trace_candidate.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> DecisionABRun:
        return cls(
            correlation_id=str(data["correlation_id"]),
            trace_baseline=DecisionTrace.from_dict(data["trace_baseline"]),  # type: ignore[arg-type]
            trace_candidate=DecisionTrace.from_dict(data["trace_candidate"]),  # type: ignore[arg-type]
        )


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

    # DecisionABRun 同样受「禁止判定语义 Bundle」守卫：双轨载体不得偷偷引入 verdict 字段
    ab_names = {f.name for f in fields(DecisionABRun)}
    if DECISION_TRACE_FORBIDDEN_FIELDS & ab_names:
        raise RuntimeError(
            "DecisionABRun 含禁止的判定语义 Bundle "
            f"{sorted(DECISION_TRACE_FORBIDDEN_FIELDS & ab_names)}；"
            "决策层双轨载体不做最终判定（ADR-0001 / T4），审计血缘只记录事实不记录诈骗认定"
        )


_assert_contract_shape()


__all__ = [
    "DECISION_TRACE_FIELD_WHITELIST",
    "DECISION_TRACE_FORBIDDEN_FIELDS",
    "TRACE_ARMS",
    "ABRunConservationError",
    "CandidateRecord",
    "DecisionABRun",
    "DecisionTrace",
    "DecisionTraceRecorder",
    "DecisionTraceSpan",
    "InMemoryRecorder",
    "MemoryRefs",
    "NullRecorder",
    "SuppressReason",
    "TraceIdentity",
    "TraceOutcome",
    "TraceOutcomeKind",
    "TracePolicy",
    "TraceProvenance",
    "TraceRationale",
    "TriggerRef",
    "build_rationale",
    "build_suppress_trace",
    "build_trigger_refs",
    "build_warning_trace",
    "candidate_records_from_events",
    "compute_input_digest",
    "compute_policy_fingerprint",
    "compute_trigger_digest",
]

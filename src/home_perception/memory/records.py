"""Memory 领域对象 dataclass（ADR-0024 §3.1 / §3.2.1 / §5.1）。

> 本模块只定义 Memory 的领域对象，**不连存储 / 不接 pipeline**。
> 字段名由工程方案选定（ADR-0024 §3.2.1 不固定字段名，未来可演进）。

**三类记忆模型**（ADR-0024 §3.1）：
- `ShortTermRecord`：工作记忆，分钟级，状态转移 / 周期快照写入
- `EpisodicRecord`：事件记忆，天/月级，访客离场时由 Episode Builder 投影
- `SemanticAggregate`：模式记忆，月/年级，由 Episodic 聚合产生（v1 仅 schema 占位）

**辅助类型**：
- `MemoryStatus`：记忆生命周期状态（ACTIVE/DEPRECATED/ARCHIVED/INVALID，§5.7）
- `ActionSummary`：ActionCommand 的 Memory 投影
- `EvidenceRef`：证据引用（ADR-0022 落地后填充）

**契约不变式**（__post_init__ 强制）：
1. `record_id` 前缀必须 ∈ {`st-`, `ep-`, `sem-`}（I1 幂等键派生约束）
2. `source_event_ids` 不能为空（I4 可解释性）
3. `created_at` 必须 UTC timezone-aware（I3 因果性前置条件）
4. `schema_version` / `memory_status` 有默认值，向后兼容
5. `model_version` 必填非空（EpisodicRecord / SemanticAggregate）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any

from ..common.timeutil import now_dt, require_utc

# ============================================================================
# 枚举（严格白名单，禁止自由文本）
# ============================================================================


class MemoryStatus(str, Enum):
    """Memory Record 生命周期状态（ADR-0024 §5.7 Memory Validity Version）。

    区别于 `schema_version`（数据结构版本）和 `model_version`（生成模型版本），
    `memory_status` 表示"这条记忆当前是否可被消费"。

    状态机（ADR-0024 §5.7.2，单向，I2 Monotonicity）：
        ACTIVE → DEPRECATED → ARCHIVED
        ACTIVE → INVALID
        DEPRECATED → INVALID / ARCHIVED
        任意 → ACTIVE  ❌ 禁止（违反单调性）
    """

    ACTIVE = "active"  # 可被 Agent / 聚合消费
    DEPRECATED = "deprecated"  # 因模型升级/规则修正而降级；保留历史证据但不参与新决策
    ARCHIVED = "archived"  # 归档；只读，不参与任何消费
    INVALID = "invalid"  # 标记为无效（如发现误判）；保留可追溯但不消费


class VisitorPresenceStatus(str, Enum):
    """访客在场/风险视图状态（Product Closure，ADR-0024 Integration Closure · Slice C）。

    ⚠️ 语义与 ``MemoryStatus`` 完全不同：``MemoryStatus`` 表示"记忆是否可被消费"
    （active/deprecated/archived/invalid），本枚举表示"访客在某时间点是否处于在场/风险视图"。
    两者命名近似但不可混用（review #5）。

    取值含义（时间点语义，非实时）：
    - ``IN_PROGRESS``：``as_of`` 落在某条**窗口内** episode 的 ``(enter_time, leave_time]``
      区间内。注意这是**历史/回放**视角的时间点判定，**不是**实时在场——
      真实数据流中 ``EpisodicRecord`` 仅在访客**离场后**由 ``project_episode`` 投影写入
      （``runtime/pipeline.py``：「把一次访客离场投影为 EpisodicRecord」），故 ``leave_time``
      恒为过去时刻，实时查询（``as_of=now``）恒为 ``CLEARED``。
      **实时在场**应读 ``ShortTermRecord.phase == "active_risk"`` / ``last_seen_at``
      （out of scope，见 review #1 / 设计稿 §3.6）。
    - ``CLEARED``：窗口内有事件，且 ``as_of`` 已晚于其离场（曾活跃、现已离开）。
    - ``NO_RECORD``：窗口内无任何相关 episode。
    """

    IN_PROGRESS = "IN_PROGRESS"
    CLEARED = "CLEARED"
    NO_RECORD = "NO_RECORD"


# enum 闭合性基线（契约测试据此断言"枚举值不漂移"）
VISITOR_PRESENCE_STATUS_VALUES: tuple[str, ...] = tuple(e.value for e in VisitorPresenceStatus)


# record_id 前缀白名单（I1 幂等键派生约束，§5.1.1）
RECORD_ID_PREFIXES: tuple[str, ...] = ("st-", "ep-", "sem-")


# enum 闭合性基线（契约测试据此断言"枚举值不漂移"）
MEMORY_STATUS_VALUES: tuple[str, ...] = tuple(e.value for e in MemoryStatus)


class RecordIdPrefix(str, Enum):
    """record_id 前缀枚举（I1 校验用，禁止自由前缀）。"""

    SHORT_TERM = "st-"  # ShortTermRecord
    EPISODIC = "ep-"  # EpisodicRecord
    SEMANTIC = "sem-"  # SemanticAggregate


# ============================================================================
# 辅助 dataclass
# ============================================================================


@dataclass
class ActionSummary:
    """ActionCommand 的 Memory 投影（不存 payload 细节）。

    只保留"什么类型 / 什么状态 / 错误信息"，不保留 payload（如通知内容、MQTT topic）。
    payload 属于业务对象，Memory 不直接理解（ADR-0024 §3.2.1）。
    """

    command_type: str  # ActionCommand.command_type
    command_id: str  # ActionCommand.command_id
    status: str  # ActionCommand.status
    error: str | None = None  # ActionCommand.error（无错误则 None）

    def __post_init__(self) -> None:
        if not self.command_type or not self.command_type.strip():
            raise ValueError("command_type 不能为空")
        if not self.command_id or not self.command_id.strip():
            raise ValueError("command_id 不能为空")
        if not self.status or not self.status.strip():
            raise ValueError("status 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_type": self.command_type,
            "command_id": self.command_id,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionSummary:
        return cls(
            command_type=data["command_type"],
            command_id=data["command_id"],
            status=data["status"],
            error=data.get("error"),
        )


@dataclass
class EvidenceRef:
    """证据引用（ADR-0022 EvidenceItem 的 Memory 侧引用）。

    v1：ADR-0022 未落地，EpisodicRecord.evidence_refs 暂为空 list。
    v2：Episode Builder 接 EvidenceItem 后填充。
    """

    evidence_id: str  # EvidenceItem.evidence_id
    modality: str  # EvidenceModality.value（vision / audio / sensor）
    captured_at: datetime  # EvidenceItem.captured_at（UTC）
    uri: str | None = None  # 本地路径 / 片段 id（不上传原视频，ADR §3.3）

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.evidence_id.strip():
            raise ValueError("evidence_id 不能为空")
        if not self.modality or not self.modality.strip():
            raise ValueError("modality 不能为空")
        require_utc(self.captured_at, "captured_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "modality": self.modality,
            "captured_at": self.captured_at.isoformat(),
            "uri": self.uri,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRef:
        return cls(
            evidence_id=data["evidence_id"],
            modality=data["modality"],
            captured_at=datetime.fromisoformat(data["captured_at"]),
            uri=data.get("uri"),
        )


# ============================================================================
# 校验工具
# ============================================================================


def _validate_record_id(record_id: str, expected_prefix: RecordIdPrefix) -> None:
    """I1 幂等键校验：record_id 必须以预期前缀开头。

    - `st-`：ShortTermRecord
    - `ep-`：EpisodicRecord
    - `sem-`：SemanticAggregate
    """
    if not isinstance(record_id, str) or not record_id:
        raise ValueError(f"record_id 不能为空，收到 {record_id!r}")
    if not record_id.startswith(expected_prefix.value):
        raise ValueError(
            f"record_id 必须以 {expected_prefix.value!r} 开头（I1 幂等键约束），收到 {record_id!r}"
        )


def _validate_non_empty_str_list(ids: list[str], field_name: str = "source_event_ids") -> None:
    """I4 可解释性校验：id 列表不能为空，且每个元素必须是非空 str。

    通用校验器，同时服务两种语义：
    - `field_name="source_event_ids"`（ShortTermRecord / EpisodicRecord）：
      引用触发本记录的源事件 id（signal_id / event_id / warning_id ...）。
    - `field_name="source_episode_ids"`（SemanticAggregate）：
      引用聚合源 Episode 的 record_id（注意是 Episode，不是 Event）。

    两者都属 I4 可解释性约束：每条 MemoryRecord 必须可追溯到源对象，
    否则 Memory 变成黑盒，Agent 无法回答"这个记忆基于哪个事件/Episode"。
    """
    if not ids:
        raise ValueError(f"{field_name} 不能为空（I4 可解释性：每条记忆必须可追溯到源对象）")
    for i, sid in enumerate(ids):
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"{field_name}[{i}] 必须是非空 str，收到 {sid!r}")


def _coerce_memory_status(value: Any, field_name: str = "memory_status") -> MemoryStatus:
    """将 str / MemoryStatus 归一为 MemoryStatus 枚举。

    三个 Record 的 `__post_init__` 共用此函数（DRY）。

    - 已是 MemoryStatus：原样返回
    - str：尝试 `MemoryStatus(value)`，失败抛 ValueError（含合法值清单）
    - 其他类型：抛 TypeError
    """
    if isinstance(value, MemoryStatus):
        return value
    if isinstance(value, str):
        try:
            return MemoryStatus(value)
        except ValueError as exc:
            valid = ", ".join(repr(e.value) for e in MemoryStatus)
            raise ValueError(
                f"{field_name} 必须是 MemoryStatus 之一，收到 {value!r}；合法值：{valid}"
            ) from exc
    raise TypeError(f"{field_name} 必须是 MemoryStatus 或 str，收到 {type(value).__name__}")


# ============================================================================
# ShortTermRecord —— Short-term Memory（工作记忆，分钟级）
# ============================================================================

# to_dict 字段闭合基准（契约测试据此断言"字段集合恒定"）
SHORT_TERM_RECORD_DICT_KEYS: tuple[str, ...] = (
    "record_id",
    "visitor_instance_id",
    "phase",
    "raised_signal_id",
    "raised_at",
    "first_seen",
    "last_seen_at",
    "source_event_ids",
    "memory_status",
    "schema_version",
    "created_at",
)


@dataclass
class ShortTermRecord:
    """Short-term Memory 记录（工作记忆，ADR-0024 §3.1.1）。

    输入：StateSnapshot（BehaviorState）+ TransitionEvent（RiskSignal）
    时间尺度：分钟级（当前访问生命周期 + 短期缓存）
    写入触发：状态转移（RAISED/CLEARED）/ 周期快照（30s）/ 访客离场（转 Episodic）

    幂等键：`record_id = f"st-{visitor_instance_id}"`（同一 visitor 一条工作记忆，
    新状态覆盖旧状态，不新增）。

    字段：
    - `record_id`：幂等键，前缀 `st-`
    - `visitor_instance_id`：v1 主键（v2 改 person_identity_id）
    - `phase`：风险状态机态（"none" / "active_risk"，来自 RiskPhase.value）
    - `raised_signal_id`：ACTIVE_RISK 时的 RAISED signal_id，CLEARED 时回填
    - `raised_at`：RAISED 时刻
    - `first_seen`：访问起点（不可重算，snapshot 持久化字段）
    - `last_seen_at`：上次见到时刻（用于离场判定）
    - `source_event_ids`：触发本次写入的 signal_id 列表
    - `memory_status`：生命周期状态（默认 ACTIVE）
    - `schema_version`：数据结构版本
    - `created_at`：本记录创建时刻（UTC）
    """

    record_id: str
    visitor_instance_id: str
    phase: str
    first_seen: datetime
    last_seen_at: datetime
    source_event_ids: list[str]
    raised_signal_id: str | None = None
    raised_at: datetime | None = None
    memory_status: MemoryStatus = MemoryStatus.ACTIVE
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)

    def __post_init__(self) -> None:
        # 1) record_id 前缀校验（I1）
        _validate_record_id(self.record_id, RecordIdPrefix.SHORT_TERM)

        # 2) 必填非空
        if not self.visitor_instance_id or not self.visitor_instance_id.strip():
            raise ValueError("visitor_instance_id 不能为空")
        if not self.phase or not self.phase.strip():
            raise ValueError("phase 不能为空")

        # 3) I4 可解释性
        _validate_non_empty_str_list(self.source_event_ids, "source_event_ids")

        # 4) memory_status 归一为枚举
        self.memory_status = _coerce_memory_status(self.memory_status)

        # 5) phase 闭合校验（Phase 1 仅 none / active_risk）
        valid_phases = ("none", "active_risk")
        if self.phase not in valid_phases:
            raise ValueError(f"phase 必须是 {valid_phases} 之一，收到 {self.phase!r}")

        # 6) ACTIVE_RISK 时必有 raised_signal_id
        if self.phase == "active_risk" and not self.raised_signal_id:
            raise ValueError("phase=active_risk 时 raised_signal_id 必填（CLEARED 回填依赖）")

        # 7) 时间字段 UTC 校验（I3 前置）
        require_utc(self.first_seen, "first_seen")
        require_utc(self.last_seen_at, "last_seen_at")
        require_utc(self.created_at, "created_at")
        if self.raised_at is not None:
            require_utc(self.raised_at, "raised_at")

        # 8) last_seen_at >= first_seen（因果性）
        if self.last_seen_at < self.first_seen:
            raise ValueError(
                f"last_seen_at({self.last_seen_at}) 不能早于 first_seen({self.first_seen})"
            )

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典（datetime → ISO 字符串，枚举 → value）。"""
        return {
            "record_id": self.record_id,
            "visitor_instance_id": self.visitor_instance_id,
            "phase": self.phase,
            "raised_signal_id": self.raised_signal_id,
            "raised_at": self.raised_at.isoformat() if self.raised_at else None,
            "first_seen": self.first_seen.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "source_event_ids": list(self.source_event_ids),
            "memory_status": self.memory_status.value,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShortTermRecord:
        return cls(
            record_id=data["record_id"],
            visitor_instance_id=data["visitor_instance_id"],
            phase=data["phase"],
            first_seen=datetime.fromisoformat(data["first_seen"]),
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]),
            source_event_ids=list(data["source_event_ids"]),
            raised_signal_id=data.get("raised_signal_id"),
            raised_at=datetime.fromisoformat(data["raised_at"]) if data.get("raised_at") else None,
            memory_status=MemoryStatus(data["memory_status"]),
            schema_version=data.get("schema_version", 1),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    @classmethod
    def from_json(cls, json_str: str) -> ShortTermRecord:
        return cls.from_dict(json.loads(json_str))


# ============================================================================
# EpisodicRecord —— Episodic Memory（事件记忆，天/月级）
# ============================================================================

# to_dict 字段闭合基准
EPISODIC_RECORD_DICT_KEYS: tuple[str, ...] = (
    "record_id",
    "visitor_instance_id",
    "person_identity_id",
    "enter_time",
    "leave_time",
    "duration_seconds",
    "risk_level",
    "recommended_action",
    "reason_summary",
    "actions",
    "evidence_refs",
    "source_event_ids",
    "summary",
    "model_version",
    "memory_status",
    "corrections",
    "schema_version",
    "created_at",
)


@dataclass
class EpisodicRecord:
    """Episodic Memory 记录（事件记忆，ADR-0024 §3.1.2）。

    来源：VisitorEvent / WarningEvent / ActionCommand → 经 Episode Builder 投影
    时间尺度：天/月级
    生命周期：长期保留（如 90 天），过期归档或删除

    幂等键：`record_id = f"ep-{visitor_event.event_id}"`（同一 VisitorEvent 多次
    投影只产生一条记录，I1）。

    字段：
    - `record_id`：幂等键，前缀 `ep-`
    - `visitor_instance_id`：v1 主键
    - `person_identity_id`：v1 恒 None（ADR-0023）
    - `enter_time` / `leave_time` / `duration_seconds`：访问时间窗
    - `risk_level`：来自 WarningEvent.risk_level（无 Warning 则 None）
    - `recommended_action`：取 risk_level 最高那条的 action
    - `reason_summary`：WarningEvent.reason_summary 合并去重
    - `actions`：ActionCommand 投影为 ActionSummary 列表
    - `evidence_refs`：v1 空列表（ADR-0022 未落地）
    - `source_event_ids`：[visitor_event_id, warning_id, ...]
    - `summary`：human-interpretable summary（ADR-0024 §3.2.1 强制）
    - `model_version`：Episode Builder 版本（如 "ep-builder-v1"）
    - `memory_status`：生命周期状态（默认 ACTIVE）
    - `corrections`：I2 Monotonicity 例外（追加修正说明，不改原字段）
    - `schema_version`：数据结构版本
    - `created_at`：本记录创建时刻（UTC）
    """

    record_id: str
    visitor_instance_id: str
    enter_time: datetime
    leave_time: datetime
    duration_seconds: float
    source_event_ids: list[str]
    summary: str
    model_version: str
    reason_summary: list[str] = field(default_factory=list)
    actions: list[ActionSummary] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    risk_level: str | None = None
    recommended_action: str | None = None
    person_identity_id: str | None = None  # v1 恒 None
    memory_status: MemoryStatus = MemoryStatus.ACTIVE
    corrections: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)

    def __post_init__(self) -> None:
        # 1) record_id 前缀校验（I1）
        _validate_record_id(self.record_id, RecordIdPrefix.EPISODIC)

        # 2) 必填非空
        if not self.visitor_instance_id or not self.visitor_instance_id.strip():
            raise ValueError("visitor_instance_id 不能为空")
        if not self.summary or not self.summary.strip():
            raise ValueError(
                "summary 不能为空（ADR-0024 §3.2.1 强制：Memory Object 必须含 "
                "human-interpretable summary）"
            )
        if not self.model_version or not self.model_version.strip():
            raise ValueError("model_version 不能为空（必须标明由哪个版本的算法生成）")

        # 3) I4 可解释性
        _validate_non_empty_str_list(self.source_event_ids, "source_event_ids")

        # 4) memory_status 归一
        self.memory_status = _coerce_memory_status(self.memory_status)

        # 5) risk_level 闭合校验（与 WarningEvent 对齐）
        valid_risk_levels = ("LOW", "MEDIUM", "HIGH")
        if self.risk_level is not None and self.risk_level not in valid_risk_levels:
            raise ValueError(
                f"risk_level 必须是 {valid_risk_levels} 之一或 None，收到 {self.risk_level!r}"
            )

        # 6) 时间字段 UTC 校验
        require_utc(self.enter_time, "enter_time")
        require_utc(self.leave_time, "leave_time")
        require_utc(self.created_at, "created_at")

        # 7) leave_time >= enter_time
        if self.leave_time < self.enter_time:
            raise ValueError(
                f"leave_time({self.leave_time}) 不能早于 enter_time({self.enter_time})"
            )

        # 8) duration_seconds >= 0
        if self.duration_seconds < 0:
            raise ValueError(f"duration_seconds 必须 >= 0，收到 {self.duration_seconds}")

        # 9) v1 约束：person_identity_id 恒 None（ADR-0023）
        if self.person_identity_id is not None:
            raise ValueError("v1 person_identity_id 必须为 None（ADR-0023：v1 不冒充真实身份）")

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典（datetime → ISO，枚举 → value，嵌套 dataclass → dict）。"""
        return {
            "record_id": self.record_id,
            "visitor_instance_id": self.visitor_instance_id,
            "person_identity_id": self.person_identity_id,
            "enter_time": self.enter_time.isoformat(),
            "leave_time": self.leave_time.isoformat(),
            "duration_seconds": self.duration_seconds,
            "risk_level": self.risk_level,
            "recommended_action": self.recommended_action,
            "reason_summary": list(self.reason_summary),
            "actions": [a.to_dict() for a in self.actions],
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "source_event_ids": list(self.source_event_ids),
            "summary": self.summary,
            "model_version": self.model_version,
            "memory_status": self.memory_status.value,
            "corrections": list(self.corrections),
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicRecord:
        return cls(
            record_id=data["record_id"],
            visitor_instance_id=data["visitor_instance_id"],
            enter_time=datetime.fromisoformat(data["enter_time"]),
            leave_time=datetime.fromisoformat(data["leave_time"]),
            duration_seconds=data["duration_seconds"],
            source_event_ids=list(data["source_event_ids"]),
            summary=data["summary"],
            model_version=data["model_version"],
            reason_summary=list(data.get("reason_summary", [])),
            actions=[ActionSummary.from_dict(a) for a in data.get("actions", [])],
            evidence_refs=[EvidenceRef.from_dict(e) for e in data.get("evidence_refs", [])],
            risk_level=data.get("risk_level"),
            recommended_action=data.get("recommended_action"),
            person_identity_id=data.get("person_identity_id"),
            memory_status=MemoryStatus(data["memory_status"]),
            corrections=[dict(c) for c in data.get("corrections", [])],
            schema_version=data.get("schema_version", 1),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    @classmethod
    def from_json(cls, json_str: str) -> EpisodicRecord:
        return cls.from_dict(json.loads(json_str))


# ============================================================================
# SemanticAggregate —— Semantic Memory（模式记忆，月/年级）
# ============================================================================

# to_dict 字段闭合基准
SEMANTIC_AGGREGATE_DICT_KEYS: tuple[str, ...] = (
    "aggregate_id",
    "dimension",
    "period_key",
    "episode_count",
    "statistics",
    "confidence",
    "source_episode_ids",
    "model_version",
    "memory_status",
    "schema_version",
    "created_at",
)


@dataclass
class SemanticAggregate:
    """Semantic Memory 聚合记录（ADR-0024 §3.1.3）。

    v1 不实现聚合逻辑；dataclass 先定义供 Slice 1 测试 schema 闭合性。
    Stage G（Environment）+ Stage H（Identity）才会填充。

    聚合维度（ADR-0024 §3.1.3）：
    - `environment`：按时间/地点/时段聚合，不依赖身份（v1 可启用，需最低观测阈值）
    - `identity`：按 person_identity_id 聚合（v1 不启用，依赖 Phase 4 ReID）

    字段：
    - `aggregate_id`：幂等键，前缀 `sem-`
    - `dimension`：聚合维度（"environment" / "identity"）
    - `period_key`：聚合周期键（如 "2026-07" / "2026-W30"）
    - `episode_count`：聚合源 Episode 数量
    - `statistics`：聚合统计（时段分布 / 风险等级分布等）
    - `confidence`：[0, 1]，低于阈值不供 Agent 消费（§3.1.3.1）
    - `source_episode_ids`：聚合源 Episode 列表（可追溯）
    - `model_version`：聚合算法版本
    - `memory_status`：生命周期状态
    - `schema_version`：数据结构版本
    - `created_at`：本记录创建时刻（UTC）
    """

    aggregate_id: str
    dimension: str
    period_key: str
    episode_count: int
    statistics: dict[str, Any]
    confidence: float
    source_episode_ids: list[str]
    model_version: str
    memory_status: MemoryStatus = MemoryStatus.ACTIVE
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)

    def __post_init__(self) -> None:
        # 1) aggregate_id 前缀校验（I1）
        _validate_record_id(self.aggregate_id, RecordIdPrefix.SEMANTIC)

        # 2) 必填非空
        if not self.dimension or not self.dimension.strip():
            raise ValueError("dimension 不能为空")
        if not self.period_key or not self.period_key.strip():
            raise ValueError("period_key 不能为空")
        if not self.model_version or not self.model_version.strip():
            raise ValueError("model_version 不能为空")

        # 3) dimension 闭合校验
        valid_dimensions = ("environment", "identity")
        if self.dimension not in valid_dimensions:
            raise ValueError(f"dimension 必须是 {valid_dimensions} 之一，收到 {self.dimension!r}")

        # 4) I4 可解释性（聚合也必须可追溯到源 Episode）
        _validate_non_empty_str_list(self.source_episode_ids, "source_episode_ids")

        # 5) memory_status 归一
        self.memory_status = _coerce_memory_status(self.memory_status)

        # 6) episode_count >= 0
        if not isinstance(self.episode_count, int) or self.episode_count < 0:
            raise ValueError(f"episode_count 必须是非负 int，收到 {self.episode_count!r}")

        # 7) confidence ∈ [0, 1]
        if not isinstance(self.confidence, (int, float)):
            raise TypeError(
                f"confidence 必须是 int 或 float，收到 {type(self.confidence).__name__}"
            )
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1]，收到 {self.confidence}")
        self.confidence = float(self.confidence)

        # 8) statistics 必须是 dict
        if not isinstance(self.statistics, dict):
            raise TypeError(f"statistics 必须是 dict，收到 {type(self.statistics).__name__}")

        # 9) created_at UTC 校验
        require_utc(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregate_id": self.aggregate_id,
            "dimension": self.dimension,
            "period_key": self.period_key,
            "episode_count": self.episode_count,
            "statistics": dict(self.statistics),
            "confidence": self.confidence,
            "source_episode_ids": list(self.source_episode_ids),
            "model_version": self.model_version,
            "memory_status": self.memory_status.value,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticAggregate:
        return cls(
            aggregate_id=data["aggregate_id"],
            dimension=data["dimension"],
            period_key=data["period_key"],
            episode_count=data["episode_count"],
            statistics=dict(data["statistics"]),
            confidence=data["confidence"],
            source_episode_ids=list(data["source_episode_ids"]),
            model_version=data["model_version"],
            memory_status=MemoryStatus(data["memory_status"]),
            schema_version=data.get("schema_version", 1),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    @classmethod
    def from_json(cls, json_str: str) -> SemanticAggregate:
        return cls.from_dict(json.loads(json_str))


# ============================================================================
# 便捷工具（测试 / 诊断用）
# ============================================================================


def records_equal(a: Any, b: Any) -> bool:
    """深度比较两个 Memory record 是否字段级相等（忽略 created_at）。

    用于 Replay Test（§6.7）的 baseline 比对。`created_at` 是运行时墙钟，
    两次回放天然产生微秒级差异；Replay Test 关心的是**记忆内容**一致性，
    不是创建时刻，因此本函数显式跳过 `created_at` 字段。

    其他字段（含嵌套 dataclass 如 ActionSummary / EvidenceRef）按 dataclass
    生成的 `__eq__` 递归比较；datetime 精确比较；List[str] 逐元素比较。

    保留 `type(a) is not type(b)` 前置检查以拒绝跨类型比较。
    """
    if type(a) is not type(b):
        return False
    # 逐字段比较，跳过 created_at（运行时墙钟，非记忆内容）
    for f in fields(a):
        if f.name == "created_at":
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return False
    return True

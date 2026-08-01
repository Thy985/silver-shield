"""行动层领域对象与状态机（P0-9 · 行动层）。

> **P0-9 = 行动层。** 消费 `WarningEvent`（P0-8 决策层），按 `ActionDispatcher`
> 路由 → `ActionCommand` → 通过 `MQTTPublisher` / `NotificationAdapter` 执行。
> **不**直接调真实萤石 / **不**接完整 App / **不**做社区系统 —— MVP 用 Mock 即可。

继续 ADR-0010 边界：
- `ActionCommand.status` 是执行层内部状态，**不**影响 `WarningEvent.status`
- `WarningEvent.status` 仍只描述**决策生命周期**（CREATED→PENDING→CONFIRMED→RESOLVED/REJECTED）
- 真实执行结果（"家属已读 / 已确认 / 已撤销"）是 WarningEvent.status 的事，**不**是 ActionCommand.status

执行层自己的状态机（独立于 WarningEvent 状态机）：
- `PENDING` ：已构造命令，等待执行
- `DONE`    ：执行成功
- `FAILED`  ：执行失败（待重试）
- `RETRYING`：正在重试
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise TypeError(f"value 必须是 UUID 或 str 格式 UUID，收到 {type(value).__name__}")


# ============================================================================
# 枚举常量（严格白名单）
# ============================================================================

# ActionCommand 类型（3 类路由 + 1 个兜底）
COMMAND_TYPES: tuple = (
    "LOG_ONLY",  # MONITOR 推荐动作：仅记录
    "SEND_FAMILY_MESSAGE",  # NOTIFY_FAMILY：通知家属
    "CREATE_COMMUNITY_TASK",  # ESCALATE_COMMUNITY：创建社区工单
)

# ActionCommand 状态（执行层内部状态机，独立于 WarningEvent.status）
COMMAND_STATUSES: tuple = (
    "PENDING",  # 已构造命令，等待执行
    "DONE",  # 执行成功
    "FAILED",  # 执行失败（待重试）
    "RETRYING",  # 正在重试
    "GIVEN_UP",  # 重试耗尽（不再尝试）
)

# WarningEvent 状态翻转规则（守 P0-8 决策生命周期）
WARNING_TRANSITIONS: dict[str, frozenset] = {
    "CREATED": frozenset({"PENDING", "REJECTED"}),
    "PENDING": frozenset({"CONFIRMED", "RESOLVED", "REJECTED"}),
    "CONFIRMED": frozenset({"RESOLVED", "REJECTED"}),
    "RESOLVED": frozenset(),  # 终态
    "REJECTED": frozenset(),  # 终态
}


# ============================================================================
# 黑名单（守"行动层不做最终判定"边界）
# ============================================================================

# ActionCommand 任何字段（含 meta + payload）禁止出现的业务判定字段
# 与 WarningEvent 黑名单保持一致；行动层也只是'执行'，不做最终判定
FORBIDDEN_ACTION_FIELDS: frozenset = frozenset(
    {
        "fraud_result",
        "fraud_probability",
        "is_fraud",
        "is_scammer",
        "verdict",
        "crime_probability",
        "final_decision",
        "guilt_score",
        "is_criminal",
        "arrest_probability",
        "deception_score",
    }
)


# ============================================================================
# ActionCommand
# ============================================================================


@dataclass
class ActionCommand:
    """行动层命令对象（P0-9 · 行动层）。

    表达"为了响应某个 WarningEvent 应该执行什么"，**不**表达"执行结果"。

    字段：
    - `command_id`：UUID4（命令生命周期）
    - `warning_id`：UUID4（关联的 WarningEvent.warning_id）— **幂等键**
    - `command_type`：3 类之一（LOG_ONLY / SEND_FAMILY_MESSAGE / CREATE_COMMUNITY_TASK）
    - `payload`：执行所需的参数（家属电话 / 工单内容等）
    - `status`：执行层状态机（PENDING / DONE / FAILED / RETRYING / GIVEN_UP）
    - `attempts`：已重试次数
    - `error`：最近一次失败原因（不阻塞 PENDING 状态保留 Warning）
    - `meta`：扩展字段（dispatcher / publisher / 决策时间等）
    - `created_at`：本命令生成时刻（UTC）
    - `updated_at`：最近一次状态变化时刻（UTC）

    严格**不含**：
    - 任何'最终判定'字段（fraud_result / is_fraud / verdict 等）—— 留给中心综合判断
    - 任何'执行是否成功'的状态字段（success / done_at / sent_at）—— 留给 status 字段
    """

    command_type: str
    warning_id: UUID
    payload: dict[str, Any]
    command_id: UUID = field(default_factory=uuid4)
    status: str = "PENDING"
    attempts: int = 0
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # UUID 归一
        self.command_id = _coerce_uuid(self.command_id)
        self.warning_id = _coerce_uuid(self.warning_id)

        # 枚举严格校验
        if self.command_type not in COMMAND_TYPES:
            raise ValueError(
                f"command_type 必须是 {COMMAND_TYPES} 之一，收到 {self.command_type!r}"
            )
        if self.status not in COMMAND_STATUSES:
            raise ValueError(f"status 必须是 {COMMAND_STATUSES} 之一，收到 {self.status!r}")

        # attempts 范围
        if self.attempts < 0:
            raise ValueError(f"attempts 必须 >= 0，收到 {self.attempts}")

        # UTC 强制
        if self.created_at.tzinfo is None:
            raise ValueError("created_at 必须是 UTC timezone-aware")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at 必须是 UTC timezone-aware")

        # 黑名单（payload + meta）
        leaked_payload = FORBIDDEN_ACTION_FIELDS.intersection(self.payload.keys())
        if leaked_payload:
            raise ValueError(f"payload 包含禁止的业务判定字段 {leaked_payload}；行动层不做最终判定")
        leaked_meta = FORBIDDEN_ACTION_FIELDS.intersection(self.meta.keys())
        if leaked_meta:
            raise ValueError(f"meta 包含禁止的业务判定字段 {leaked_meta}；行动层不做最终判定")

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典。"""
        return {
            "command_id": str(self.command_id),
            "warning_id": str(self.warning_id),
            "command_type": self.command_type,
            "payload": self.payload,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
            "meta": self.meta,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ============================================================================
# 状态翻转工具
# ============================================================================


def can_transition_warning(from_status: str, to_status: str) -> bool:
    """检查 WarningEvent.status 是否可从 from_status 翻转到 to_status。"""
    if from_status not in WARNING_TRANSITIONS:
        return False
    return to_status in WARNING_TRANSITIONS[from_status]


def assert_transition_warning(from_status: str, to_status: str) -> None:
    """断言 WarningEvent.status 翻转合法；不合法抛 ValueError。"""
    if not can_transition_warning(from_status, to_status):
        allowed = sorted(WARNING_TRANSITIONS.get(from_status, frozenset()))
        raise ValueError(
            f"WarningEvent.status 不能从 {from_status!r} 翻转到 {to_status!r}；"
            f"允许的下一状态：{allowed}"
        )

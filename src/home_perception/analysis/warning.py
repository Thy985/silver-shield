"""决策层领域对象（P0-8 · 决策层 · 对外契约）。

> **P0-8 = 决策层。** `WarningEvent` 是 `PerceptionEvent[]` 经 `DecisionPolicy`
> 决策后的"系统准备采取什么行动"事件，最终通过 P0-9 行动层（MQTT / 家属通知 /
> 社区升级）执行。

**关键边界（ADR-0010）**：
- `risk_level` ∈ {`LOW`, `MEDIUM`, `HIGH`} —— 是**决策严重度**，**不是诈骗概率**
- `recommended_action` 是"策略建议"（字符串），由 P0-9 行动层真正执行
- 严格**不含** `fraud_result` / `fraud_probability` / `is_fraud` / `verdict` /
  `crime_probability` / `final_decision` / `guilt_score` 等任何犯罪认定字段
- **不**直接调 MQTT / **不**直接通知家属 / **不**直接升级社区 —— 这些是 P0-9 责任
- 字段增删按 ADR-0005 走 schema_version 评审
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
# 枚举常量（严格白名单，禁止自由文本）
# ============================================================================

# 风险等级（决策严重度，不是诈骗概率）
RISK_LEVELS: tuple = ("LOW", "MEDIUM", "HIGH")

# 建议动作（P0-8 只设 hint，P0-9 行动层执行）
RECOMMENDED_ACTIONS: tuple = (
    "MONITOR",  # 仅记录，不通知
    "NOTIFY_FAMILY",  # 通知家属核实
    "ESCALATE_COMMUNITY",  # 升级到社区/物业/警方
)

# 警告状态（P0-9 行动层会管理状态翻转）
# 语义约定（Owner P0-8 review）：
# - "CREATED"：决策已生成（WarningEvent 刚由 DecisionEngine 产出，**尚未下发**）
# - "PENDING"：已下发 ActionDispatcher，**等待下游确认**（MQTT/通知通道正在处理）
# - "CONFIRMED"：下游已确认收到（MQTT ACK / 家属端 ACK / 社区端 ACK）
# - "RESOLVED"：处理完毕（家属核实 / 社区介入 / 标记误报已闭环）
# - "REJECTED"：拒绝 / 撤销（误报、重复、用户主动关闭）
# 关键边界：这些状态**描述决策生命周期**，**不**描述执行结果
# （"NOTIFY_FAMILY 已完成"不是状态，是 P0-9 行动层的内部日志）
WARNING_STATUSES: tuple = (
    "CREATED",  # 初始态：决策已生成
    "PENDING",  # 已下发，等待确认
    "CONFIRMED",  # 下游已确认
    "RESOLVED",  # 已闭环
    "REJECTED",  # 已拒绝/撤销
)


# ============================================================================
# 黑名单（守"决策层不做最终判定"边界）
# ============================================================================

# WarningEvent 顶层字段 + meta 中**禁止**出现的业务判定 / 犯罪认定字段
# （注意：trigger_events 元素是 PerceptionEvent 引用，event_type/score/timestamp
#  是合法的引用元数据，**不**在黑名单内）
FORBIDDEN_WARNING_FIELDS: frozenset = frozenset(
    {
        # === 业务判定（决策层严禁做最终判定）===
        "fraud_result",
        "fraud_probability",
        "is_fraud",
        "is_scammer",
        "is_criminal",
        "verdict",
        "final_decision",
        "crime_probability",
        "guilt_score",
        "arrest_probability",
        "deception_score",
    }
)


# ============================================================================
# WarningEvent
# ============================================================================


@dataclass
class WarningEvent:
    """决策层对外契约事件（P0-8 · 决策层）。

    字段：
    - `warning_id`：UUID（自动生成）
    - `elder_id`：被守护老人 ID（来自设备配置 / 上级系统，v2 可来自 RiskTwin）
    - `device_id`：触发决策的 Home 端设备 ID（来自 PerceptionEvent.device_id）
    - `risk_level`：3 类决策严重度（LOW/MEDIUM/HIGH）—— **不是诈骗概率**（ADR-0010）
    - `recommended_action`：3 类策略建议（MONITOR/NOTIFY_FAMILY/ESCALATE_COMMUNITY）
    - `status`：4 类警告状态（默认 PENDING；P0-9 行动层会翻转）
    - `trigger_events`：触发本次决策的 PerceptionEvent 摘要列表
      （仅 dict 引用，不存 PerceptionEvent 对象 → 避免循环引用 + 序列化安全）
    - `reason_summary`：人话可读的触发原因列表（无"诈骗"/"犯罪"字样）
    - `perception_score`：聚合规则命中强度（0-1），取 trigger_events 中 max
    - `evidence`：取证引用列表（snapshot/clip URI）；P0-8 不填，P0-9 取证层填
    - `meta`：扩展字段（决策 policy 名 / 决策时间 / 策略配置等）
    - `created_at`：本决策生成时刻（UTC timezone-aware）

    严格**不含**：
    - 任何"最终判定"字段（`fraud_result` / `is_fraud` / `verdict` 等）—— 留给中心综合判断
    - 任何"执行"副作用（调 MQTT / 发短信 / 拨电话）—— 留给 P0-9 行动层
    """

    elder_id: str
    device_id: str
    risk_level: str
    recommended_action: str
    trigger_events: list[dict[str, Any]]
    reason_summary: list[str]
    warning_id: UUID = field(default_factory=uuid4)
    status: str = "CREATED"  # 默认 CREATED（决策刚生成，未下发）
    perception_score: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # 1) warning_id 归一（UUID 或 str）
        self.warning_id = _coerce_uuid(self.warning_id)

        # 2) 枚举严格校验
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"risk_level 必须是 {RISK_LEVELS} 之一，收到 {self.risk_level!r}")
        if self.recommended_action not in RECOMMENDED_ACTIONS:
            raise ValueError(
                f"recommended_action 必须是 {RECOMMENDED_ACTIONS} 之一，"
                f"收到 {self.recommended_action!r}"
            )
        if self.status not in WARNING_STATUSES:
            raise ValueError(f"status 必须是 {WARNING_STATUSES} 之一，收到 {self.status!r}")

        # 3) 必填非空
        if not self.elder_id or not str(self.elder_id).strip():
            raise ValueError("elder_id 不能为空")
        if not self.device_id or not str(self.device_id).strip():
            raise ValueError("device_id 不能为空")
        if not self.trigger_events:
            raise ValueError("trigger_events 不能为空（无触发事件的警告无意义）")
        if not self.reason_summary:
            raise ValueError("reason_summary 不能为空")

        # 4) 数值范围
        if not (0.0 <= self.perception_score <= 1.0):
            raise ValueError(f"perception_score 必须在 [0, 1]，收到 {self.perception_score}")

        # 5) UTC timezone-aware（防跨设备时间漂移）
        if self.created_at.tzinfo is None or self.created_at.tzinfo.utcoffset(
            self.created_at
        ) != UTC.utcoffset(self.created_at):
            raise ValueError(
                f"created_at 必须是 UTC timezone-aware，收到 {self.created_at!r} "
                f"(tzinfo={self.created_at.tzinfo})"
            )

        # 6) 黑名单检查（顶层字段 + meta 禁止出现业务判定字段）
        forbidden_top = FORBIDDEN_WARNING_FIELDS.intersection(self.__dict__.keys())
        if forbidden_top:
            raise ValueError(
                f"WarningEvent 含禁止的业务判定字段 {forbidden_top}；决策层不做最终判定（ADR-0010）"
            )
        forbidden_in_meta = FORBIDDEN_WARNING_FIELDS.intersection(self.meta.keys())
        if forbidden_in_meta:
            raise ValueError(
                f"meta 包含禁止的业务判定字段 {forbidden_in_meta}；决策层不做最终判定（ADR-0010）"
            )

        # 7) trigger_events 元素必须是 dict（P0-7b PerceptionEvent 引用元数据合法）
        for i, ev in enumerate(self.trigger_events):
            if not isinstance(ev, dict):
                raise ValueError(f"trigger_events[{i}] 必须是 dict，收到 {type(ev).__name__}")  # noqa: TRY004  # 类型校验走 ValueError（保持异常契约）

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典（datetime 已转 ISO 字符串）。"""
        return {
            "warning_id": str(self.warning_id),
            "elder_id": self.elder_id,
            "device_id": self.device_id,
            "risk_level": self.risk_level,
            "recommended_action": self.recommended_action,
            "status": self.status,
            "perception_score": round(self.perception_score, 4),
            "trigger_events": self.trigger_events,
            "reason_summary": self.reason_summary,
            "evidence": self.evidence,
            "meta": self.meta,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

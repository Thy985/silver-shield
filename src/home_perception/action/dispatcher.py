"""ActionDispatcher —— WarningEvent → ActionCommand 路由（P0-9 · 行动层）。

> **P0-9 = 行动层。** `ActionDispatcher` 消费 `WarningEvent`，按
> `recommended_action` 路由到 3 类 ActionCommand 构造器。
>
> 严格**不**做：
> - 执行（→ ActionExecutor 责任）
> - 幂等检查（→ ActionExecutor 责任）
> - 状态翻转（→ ActionExecutor 责任）
> - 失败重试（→ ActionExecutor 责任）
>
> 一个 WarningEvent 可产生 0-N 个 ActionCommand（MVP 1 个，v2 可拆多通道）
"""

from __future__ import annotations

from dataclasses import dataclass

from ..analysis.warning import WarningEvent
from ..common.logging import get_logger
from .command import ActionCommand
from .notifier import FamilyContact

log = get_logger(__name__)


# ============================================================================
# ActionDispatcher
# ============================================================================


@dataclass
class DispatcherConfig:
    """Dispatcher 配置（MVP 从 devices.yaml 读，v2 从中心 RiskTwin 拉）。"""

    family_contact: FamilyContact | None = None
    community_endpoint: str | None = None  # e.g., "https://community.example.com/api/v1/tasks"
    mqtt_topic_prefix: str = "silvershield/home"  # silvershield/home/{device_id}/warning


class ActionDispatcher:
    """WarningEvent → ActionCommand[] 路由器。

    路由表（per `WarningEvent.recommended_action`）：
    - `MONITOR`             → `[LOG_ONLY]`
    - `NOTIFY_FAMILY`       → `[SEND_FAMILY_MESSAGE]`
    - `ESCALATE_COMMUNITY`  → `[CREATE_COMMUNITY_TASK]`

    用法：
        dispatcher = ActionDispatcher(config)
        commands = dispatcher.dispatch(warning)
        # commands 可能是 []（dispatcher 决定无需动作）或 1+ 个 ActionCommand
    """

    def __init__(self, config: DispatcherConfig | None = None):
        self.config = config or DispatcherConfig()

    def dispatch(self, warning: WarningEvent) -> list[ActionCommand]:
        """根据 warning.recommended_action 路由到对应 ActionCommand 构造器。"""
        rec = warning.recommended_action
        if rec == "MONITOR":
            return [self._build_log_only(warning)]
        if rec == "NOTIFY_FAMILY":
            return [self._build_family_message(warning)]
        if rec == "ESCALATE_COMMUNITY":
            return [self._build_community_task(warning)]
        log.warning("dispatch.unknown_action", warning_id=str(warning.warning_id), action=rec)
        return []

    # ------------------------------------------------------------------
    # 内部：3 类 ActionCommand 构造
    # ------------------------------------------------------------------

    def _build_log_only(self, warning: WarningEvent) -> ActionCommand:
        return ActionCommand(
            command_type="LOG_ONLY",
            warning_id=warning.warning_id,
            payload={
                "device_id": warning.device_id,
                "risk_level": warning.risk_level,
                "reason_summary": warning.reason_summary,
            },
            meta={"dispatcher": "ActionDispatcher", "source_action": "MONITOR"},
        )

    def _build_family_message(self, warning: WarningEvent) -> ActionCommand:
        if self.config.family_contact is None:
            # 没配家属联系信息 → 降级为 LOG_ONLY（仍能记录决策，不丢）
            log.warning(
                "dispatch.family_contact_missing",
                warning_id=str(warning.warning_id),
            )
            return self._build_log_only(warning)

        message = self._format_family_message(warning)
        topic = f"{self.config.mqtt_topic_prefix}/{warning.device_id}/notify_family"
        return ActionCommand(
            command_type="SEND_FAMILY_MESSAGE",
            warning_id=warning.warning_id,
            payload={
                "topic": topic,
                "contact": {
                    "elder_id": self.config.family_contact.elder_id,
                    "name": self.config.family_contact.name,
                    "phone": self.config.family_contact.phone,
                    "relation": self.config.family_contact.relation,
                },
                "message": message,
            },
            meta={"dispatcher": "ActionDispatcher", "source_action": "NOTIFY_FAMILY"},
        )

    def _build_community_task(self, warning: WarningEvent) -> ActionCommand:
        topic = f"{self.config.mqtt_topic_prefix}/{warning.device_id}/escalate"
        task = {
            "topic": topic,
            "endpoint": self.config.community_endpoint or "(not_configured)",
            "elder_id": warning.elder_id,
            "device_id": warning.device_id,
            "risk_level": warning.risk_level,
            "reasons": warning.reason_summary,
            "perception_score": round(warning.perception_score, 4),
            "warning_id": str(warning.warning_id),
            "created_at": warning.created_at.isoformat(),
        }
        return ActionCommand(
            command_type="CREATE_COMMUNITY_TASK",
            warning_id=warning.warning_id,
            payload=task,
            meta={"dispatcher": "ActionDispatcher", "source_action": "ESCALATE_COMMUNITY"},
        )

    @staticmethod
    def _format_family_message(warning: WarningEvent) -> str:
        """人话消息模板（MVP 简单版本，P1 可走 LLM 解释）。"""
        reasons = "、".join(warning.reason_summary) if warning.reason_summary else "异常情况"
        return (
            f"【银龄盾告警】{warning.elder_id} 门前检测到：{reasons}。"
            f"风险等级：{warning.risk_level}。"
            f"建议核实情况。"
        )

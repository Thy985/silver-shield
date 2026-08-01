"""ActionExecutor —— 行动层编排器（P0-9 · 行动层 · 入口）。

> **P0-9 = 行动层。** `ActionExecutor` 消费 `WarningEvent`（P0-8 决策层），
> 经 `ActionDispatcher` 路由 → `ActionCommand` → 通过 `MQTTPublisher` /
> `NotificationAdapter` 执行。

**核心保证（ADR-0011 三大必验证）**：

1. **消费正确**：`HIGH` → `ESCALATE_COMMUNITY` 真的走社区通道
   （`command_type="CREATE_COMMUNITY_TASK"` + MQTTPublisher 调用次数 = 1）
2. **幂等**：同一 `warning_id` 重复 `execute()` **只产生一个下游任务**
   （in-memory set；MVP 演示足够；进程重启幂等丢失，v2 走 Redis/SQLite）
3. **失败保护**：`publisher` 失败时 `WarningEvent.status` 保持 `PENDING`（不丢）；
   重试 N 次后进入 `GIVEN_UP`（ActionCommand 状态） + `REJECTED`（WarningEvent 状态）；
   错误原因存到 `meta.dispatch_error`，可审计

边界（ADR-0011）：
- **不**直接调真实设备（→ MQTTPublisher / NotificationAdapter 协议）
- **不**修改 WarningEvent 的 `recommended_action` / `risk_level`（只翻 status）
- **不**做内容生成（→ ActionDispatcher 已构造 payload）
- **不**持久化（进程重启幂等丢失可接受，v2 接 Redis）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..analysis.warning import WarningEvent
from ..common.logging import get_logger
from ..common.timeutil import now_dt
from .command import ActionCommand, assert_transition_warning
from .dispatcher import ActionDispatcher
from .notifier import NotificationAdapter
from .publisher import MQTTPublisher

log = get_logger(__name__)


# ============================================================================
# ActionExecutor
# ============================================================================


@dataclass
class ActionExecutor:
    """行动层编排器（P0-9 入口）。

    流程：
    1. 接收 `WarningEvent`
    2. 幂等检查（warning_id 已在 _dispatched → 直接返回）
    3. 委托 `ActionDispatcher.dispatch()` → `ActionCommand[]`
    4. 翻 WarningEvent.status：CREATED → PENDING
    5. 执行每个 command（调 publisher / notifier）
    6. 根据执行结果翻 command.status（DONE / FAILED / GIVEN_UP）
    7. 整体翻 WarningEvent.status（CONFIRMED / PENDING / REJECTED）
    8. 记录到 `_dispatched` set（幂等）
    9. 返回 `[ActionCommand]`（已执行 / 已记录失败）

    用法：
        executor = ActionExecutor(dispatcher, publisher, notifier, max_retries=3)
        commands = executor.execute(warning)
        # commands 可能是 []（幂等已 dispatch / dispatch 返回空）或 1+ 个 ActionCommand
    """

    dispatcher: ActionDispatcher
    publisher: MQTTPublisher
    notifier: NotificationAdapter
    max_retries: int = 3
    # 内部状态（in-memory 幂等）
    _dispatched: set[UUID] = field(default_factory=set)
    _commands_by_warning: dict[UUID, list[UUID]] = field(default_factory=dict)
    _command_index: dict[UUID, ActionCommand] = field(default_factory=dict)
    # 持有 warning 引用（用于 retry_pending 反查；MVP 进程内可接受）
    _warnings_by_id: dict[UUID, WarningEvent] = field(default_factory=dict)
    # 可选时间源（测试用）
    _now_provider: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(f"max_retries 必须 >= 0，收到 {self.max_retries}")
        if self._now_provider is None:
            self._now_provider = now_dt

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def execute(self, warning: WarningEvent) -> list[ActionCommand]:
        """处理一个 WarningEvent：幂等 → 路由 → 执行 → 状态翻转。

        返回所有相关 ActionCommand（含失败的）。幂等命中时返回已记录的 commands。
        """
        # 1) 幂等检查
        if warning.warning_id in self._dispatched:
            log.info("action.idempotent_skip", warning_id=str(warning.warning_id))
            return self._get_commands_for_warning(warning.warning_id)

        # 2) 路由
        commands = self.dispatcher.dispatch(warning)
        if not commands:
            log.info("action.no_commands", warning_id=str(warning.warning_id))
            # 即便无 command 也标记已处理（避免 dispatcher 一直返回空时反复 dispatch）
            self._dispatched.add(warning.warning_id)
            return []

        # 3) 翻 WarningEvent.status：CREATED → PENDING
        self._transition_warning(warning, "PENDING")
        # 持有 warning 引用（retry_pending 用）
        self._warnings_by_id[warning.warning_id] = warning

        # 4) 执行每个 command
        executed: list[ActionCommand] = []
        all_done = True
        for cmd in commands:
            success = self._execute_command(cmd, warning)
            executed.append(cmd)
            self._command_index[cmd.command_id] = cmd
            if not success:
                all_done = False

        # 5) 记录幂等 + commands
        self._dispatched.add(warning.warning_id)
        self._commands_by_warning[warning.warning_id] = [c.command_id for c in executed]

        # 6) 翻 WarningEvent.status
        if all_done:
            self._transition_warning(warning, "CONFIRMED")
        else:
            # 至少一个 command 失败 → Warning 保持 PENDING（等待 retry_pending()）
            # 错误原因记到 meta（可审计）
            failed = [c for c in executed if c.status == "FAILED"]
            warning.meta["dispatch_error"] = (
                f"{len(failed)}/{len(executed)} commands failed; "
                f"attempts={failed[0].attempts if failed else 0}, "
                f"errors={[c.error for c in failed]}"
            )
            # 不翻 status（保持 PENDING），等 retry_pending 处理

        log.info(
            "action.executed",
            warning_id=str(warning.warning_id),
            command_count=len(executed),
            all_done=all_done,
            new_status=warning.status,
        )
        return executed

    def retry_pending(self) -> list[ActionCommand]:
        """重试所有 FAILED 状态的 commands。

        max_retries 语义：最多重试 max_retries 次（不含初始 execute）。
        总尝试次数 = 1 + max_retries。

        行为：
        - FAILED command → 增 attempts +1，状态变 RETRYING，执行
        - 重试成功（status=DONE）→ 检查同 warning 的所有 command，若全部 DONE → warning CONFIRMED
        - 重试失败且 attempts 达到 1+max_retries → command GIVEN_UP + warning REJECTED
        - 已 GIVEN_UP / DONE 的 command 跳过
        """
        retried: list[ActionCommand] = []

        for cmd in list(self._command_index.values()):
            if cmd.status != "FAILED":
                continue

            # 重试一次
            cmd.attempts += 1
            cmd.status = "RETRYING"
            cmd.updated_at = self._now_provider()
            warning = self._warnings_by_id.get(cmd.warning_id)
            if warning is None:
                retried.append(cmd)
                continue

            success = self._execute_command(cmd, warning)
            retried.append(cmd)

            # 重试后处理
            if not success and cmd.attempts >= 1 + self.max_retries:
                # 达到总尝试次数上限 → GIVEN_UP + warning REJECTED
                cmd.status = "GIVEN_UP"
                cmd.updated_at = self._now_provider()
                try:
                    self._transition_warning(warning, "REJECTED")
                except ValueError:
                    pass  # 已经是终态
            elif success:
                # 本次重试成功 → 检查同 warning 的所有 command 是否都 DONE
                all_done = all(
                    c.status == "DONE" for c in self._get_commands_for_warning(cmd.warning_id)
                )
                if all_done and warning.status == "PENDING":
                    try:
                        self._transition_warning(warning, "CONFIRMED")
                    except ValueError:
                        pass

        return retried

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _execute_command(self, cmd: ActionCommand, warning: WarningEvent) -> bool:
        """执行单个 command：调对应 publisher / notifier。返回 success。"""
        if cmd.attempts == 0:
            cmd.attempts = 1
        if cmd.status == "PENDING":
            cmd.attempts = max(cmd.attempts, 1)
        try:
            if cmd.command_type == "LOG_ONLY":
                # LOG_ONLY 不实际发送，只记录（在 executor 里打 log）
                log.info(
                    "action.log_only",
                    warning_id=str(warning.warning_id),
                    risk_level=warning.risk_level,
                    reasons=warning.reason_summary,
                )
                cmd.status = "DONE"
                cmd.updated_at = self._now_provider()
                return True

            if cmd.command_type == "SEND_FAMILY_MESSAGE":
                from .notifier import FamilyContact

                contact_data = cmd.payload.get("contact", {})
                contact = FamilyContact(
                    elder_id=contact_data.get("elder_id", warning.elder_id),
                    name=contact_data.get("name", ""),
                    phone=contact_data.get("phone", ""),
                    relation=contact_data.get("relation", "family"),
                )
                ok = self.notifier.notify_family(contact, cmd.payload.get("message", ""))
                self._mark_command_result(cmd, ok)
                return ok

            if cmd.command_type == "CREATE_COMMUNITY_TASK":
                topic = cmd.payload.get("topic", "")
                ok = self.publisher.publish(topic, cmd.payload)
                self._mark_command_result(cmd, ok)
                return ok

            cmd.error = f"unknown command_type: {cmd.command_type}"
            cmd.status = "FAILED"
            cmd.updated_at = self._now_provider()
            return False
        except Exception as e:  # noqa: BLE001  # Publisher/Notifier 理论上不抛，但做最后兜底
            cmd.error = f"{type(e).__name__}: {e}"
            cmd.status = "FAILED"
            cmd.updated_at = self._now_provider()
            log.error("action.execute_exception", command_id=str(cmd.command_id), error=str(e))
            return False

    def _mark_command_result(self, cmd: ActionCommand, ok: bool) -> None:
        if ok:
            cmd.status = "DONE"
            cmd.error = None
        else:
            cmd.status = "FAILED"
            cmd.error = "publisher/notifier returned False"
        cmd.updated_at = self._now_provider()

    def _transition_warning(self, warning: WarningEvent, to_status: str) -> None:
        """翻 WarningEvent.status；不合法抛 ValueError（防御性）。"""
        try:
            assert_transition_warning(warning.status, to_status)
            warning.status = to_status
        except ValueError as e:
            log.warning(
                "action.invalid_transition",
                warning_id=str(warning.warning_id),
                from_status=warning.status,
                to_status=to_status,
                reason=str(e),
            )
            raise

    def _get_commands_for_warning(self, warning_id: UUID) -> list[ActionCommand]:
        cmd_ids = self._commands_by_warning.get(warning_id, [])
        return [self._command_index[cid] for cid in cmd_ids if cid in self._command_index]

    def _find_warning_for_command(self, cmd: ActionCommand) -> WarningEvent | None:
        """从 _warnings_by_id 反查关联 warning。"""
        return self._warnings_by_id.get(cmd.warning_id)

    # ------------------------------------------------------------------
    # 调试 / 测试用
    # ------------------------------------------------------------------

    def is_dispatched(self, warning_id: UUID) -> bool:
        return warning_id in self._dispatched

    @property
    def dispatched_count(self) -> int:
        return len(self._dispatched)

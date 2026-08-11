"""ActionSink —— 行动层可注入观测接缝（ADR-0034 · D3）。

> **为什么放 `action/` 而不是 `integration/`**：`ActionExecutor` 需要 import 本模块做类型
> 标注。若把接缝放进评估包，生产代码就会**反向依赖**评估包，直接违反 ADR-0034 T2
> （评估层不得进入生产链路）。这与 ADR-0031 把 `DecisionTraceRecorder` 放 `analysis/`
> 而非 `evaluation/` 完全同构。

**它解决什么问题**：闭环集成验证（ADR-0034）需要回答"这一轮到底有没有真的发出
`ActionCommand`、发的是哪几类"。在此之前，`ActionExecutor.execute()` 的返回值只有直接
调用方（`PerceptionPipeline._act_on_event`）能看到，验证器拿不到**可注入的**观测通道，
只能去读 `FrameResult.commands`（单一通道无法自证，见 §0.4 F6）。本模块提供第二条独立
通道，供 F6 交叉校验。

**三条铁律**：

1. **零行为变化**：`ActionExecutor(sink=None)` 是默认值，未注入时执行路径与注入前逐字节
   等价（不新增分支副作用、不改状态机、不改返回值）。
2. **失败隔离**（同 ADR-0031 T3）：sink 的任何异常都必须在 `ActionExecutor` 侧被吞掉并
   记日志。本模块自身的 `record` / `flush` 也**绝不外抛**——探针坏了是探针的事，绝不能
   让观测把生产派发搞挂。
3. **只写不读**：行动层只调 `record` 把命令**写**出去，绝不从 sink 读回来影响派发决策。

落盘形态（`JsonlActionRecorder`）默认只写**结构化投影**（见 `structural_projection`），
不写 `payload`：探针要回答的是"是否发出了某类命令"，而不是归档消息正文。`payload` 依设计
就携带家属电话 / 消息全文等运营内容（`ActionDispatcher._build_family_message`），把它落到
验证产物里既无必要也不符合 ADR-0002 的隐私姿态。需要正文时显式 `include_payload=True`。

> 注：本模块**刻意不复用** `analysis.decision_sink.assert_desensitized`。那个守卫的禁止
> 字段集是**决策语义**专用的（`decision` / `risk_score` / `verdict` …），而 `ActionCommand
> .payload` 合法携带 `risk_level` 等运营字段——套用会语义错配。此处改用"结构化投影"这一
> 更强的白名单式收敛：不在白名单里的字段根本不写出去。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ..common.logging import get_logger
from .command import COMMAND_TYPES, ActionCommand

log = get_logger(__name__)


# ============================================================================
# 接缝协议
# ============================================================================


@runtime_checkable
class ActionSink(Protocol):
    """行动层命令的采集接缝（D3）。

    `ActionExecutor` 只在**成功派发之后**调用 `record`，且**绝不读回**。`flush` 供落盘型
    实现做显式持久化；内存型实现为 no-op。

    实现方 MUST NOT 抛异常影响生产派发——即便调用方已做 try/except 兜底，实现自身也应
    自行吞掉异常并记日志（双保险，铁律 2）。
    """

    def record(self, command: ActionCommand) -> None:
        """记录一条已派发的 `ActionCommand`。实现 MUST NOT 外抛。"""
        ...

    def flush(self) -> None:
        """可选：显式持久化。内存实现为 no-op。实现 MUST NOT 外抛。"""
        ...


# ============================================================================
# 内存实现（验证 / 测试主力）
# ============================================================================


@dataclass
class InMemoryActionRecorder:
    """内存累积已派发命令，供 `IntegrationValidator` 断言与 F6 交叉校验。

    非线程安全（与 ADR-0031 `InMemoryRecorder` 一致）：`PerceptionPipeline` 逐帧串行驱动，
    集成验证也在单线程内跑完。若未来引入并发派发，改用 `JsonlActionRecorder` 或自行加锁。

    查询方法一律返回**元组**（不可变快照），避免调用方就地改动内部列表。
    """

    _commands: list[ActionCommand] = field(default_factory=list)

    # -- ActionSink 协议 ------------------------------------------------

    def record(self, command: ActionCommand) -> None:
        self._commands.append(command)

    def flush(self) -> None:
        return None

    # -- 查询 ------------------------------------------------------------

    def commands(self) -> tuple[ActionCommand, ...]:
        """按记录顺序返回全部命令的不可变快照。"""
        return tuple(self._commands)

    def by_type(self, command_type: str) -> tuple[ActionCommand, ...]:
        """按命令类型过滤。

        未知类型 **fail-closed** 抛 `ValueError`：查询侧的类型笔误若静默返回空元组，会让
        "期望某类命令存在"的断言假性通过——这正是 ADR-0034 要消灭的静默丢弃。
        """
        if command_type not in COMMAND_TYPES:
            raise ValueError(
                f"command_type 必须是 {COMMAND_TYPES} 之一，收到 {command_type!r}"
            )
        return tuple(c for c in self._commands if c.command_type == command_type)

    def by_warning_id(self, warning_id: str | UUID) -> tuple[ActionCommand, ...]:
        """按关联的 `WarningEvent.warning_id` 过滤（接受 `str` 或 `UUID`）。"""
        key = str(warning_id)
        return tuple(c for c in self._commands if str(c.warning_id) == key)

    def command_types(self) -> tuple[str, ...]:
        """已出现过的命令类型（**去重 + 排序**，供报告做确定性比对）。"""
        return tuple(sorted({c.command_type for c in self._commands}))

    def clear(self) -> None:
        """清空累积（用于同一 recorder 跨场景复用）。"""
        self._commands.clear()

    def __len__(self) -> int:
        return len(self._commands)


# ============================================================================
# JSONL 落盘实现
# ============================================================================

# 结构化投影白名单：只有这些字段会被写出去（`payload` / `meta` 需显式开启）。
STRUCTURAL_COMMAND_FIELDS: tuple[str, ...] = (
    "command_id",
    "warning_id",
    "command_type",
    "status",
    "attempts",
    "error",
    "created_at",
    "updated_at",
)


def structural_projection(
    command: ActionCommand, *, include_payload: bool = False
) -> dict[str, Any]:
    """把 `ActionCommand` 投影为**只含结构事实**的字典（白名单式收敛）。

    默认丢弃 `payload` 与 `meta`：探针回答的是"是否发出了某类命令、终态如何"，不归档
    消息正文（家属电话 / 通知全文等运营内容）。`include_payload=True` 时才附带
    `payload` / `meta`，由调用方自行承担隐私责任。
    """
    full = command.to_dict()
    projected: dict[str, Any] = {k: full[k] for k in STRUCTURAL_COMMAND_FIELDS}
    if include_payload:
        projected["payload"] = full["payload"]
        projected["meta"] = full["meta"]
    return projected


@dataclass
class JsonlActionRecorder:
    """本地 JSONL 落盘 sink：每条已派发命令写一行（追加）。

    形态对齐 ADR-0031 `JsonlTraceRecorder`：每次操作独立开闭句柄（规避 Windows 下持有
    句柄导致的替换阻塞）、`threading.Lock` 串行化追加、写后 `fsync` 落盘即持久化、
    异常一律 `log.exception` **绝不外抛**（铁律 2）。

    与 `JsonlTraceRecorder` 的差异：
    - **不做保留期轮转**。行动探针是**单轮验证产物**（一次 `IntegrationRunner.run` 一个
      文件），不是长期审计归档；轮转语义属 ADR-0031 决策血缘的职责。
    - **不套用** `assert_desensitized`，改用 `structural_projection` 白名单收敛（理由见
      模块 docstring）。
    """

    path: Path
    include_payload: bool = False

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        parent = self.path.parent
        if parent.exists() and not parent.is_dir():
            raise NotADirectoryError(
                f"落盘父路径不是目录（可能符号链接跟随 / 配置错误）：{parent}"
            )
        self._fh_lock = threading.Lock()

    # -- ActionSink 协议 ------------------------------------------------

    def record(self, command: ActionCommand) -> None:
        try:
            payload = structural_projection(command, include_payload=self.include_payload)
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            log.exception("action.sink_serialize_failed", path=str(self.path))
            return
        try:
            with self._fh_lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            log.exception("action.sink_write_failed", path=str(self.path))

    def flush(self) -> None:
        # 每条 record 写后已 fsync；此处仅做显式兜底（文件不存在则 no-op）。
        try:
            if not self.path.exists():
                return
            with self._fh_lock, self.path.open("rb") as fh:
                os.fsync(fh.fileno())
        except Exception:
            log.exception("action.sink_flush_failed", path=str(self.path))


__all__ = [
    "STRUCTURAL_COMMAND_FIELDS",
    "ActionSink",
    "InMemoryActionRecorder",
    "JsonlActionRecorder",
    "structural_projection",
]

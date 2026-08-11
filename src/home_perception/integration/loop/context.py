"""ADR-0034 Phase A · D2：``IntegrationContext`` —— 闭环探针的唯一容器与唯一创建点。

为什么需要一个"探针容器"而不是让调用方逐个 new：
ADR-0034 的核心命题是"静默丢弃 = 失败"，而判定静默丢弃依赖 **多条独立观测通道**
（``ActionSink`` / ``DecisionTraceRecorder`` / ``MemoryStore``）。若探针由调用方分散
创建、分散注入，F6 交叉校验就失去意义——两条通道可能压根来自不同的运行实例。
因此本模块把探针集中为一个 frozen 容器，并以 ``IntegrationContext.build()`` 作为
**唯一创建点**（L1）；``IntegrationRunner`` 的 ``_assemble`` 是**唯一注入点**（L2）。

Phase A 边界（fail-closed，不静默降级）：
- ``memory_backend`` 仅支持 ``"in_memory"``；
- ``cross_modal_enabled`` 恒 ``False``（跨模态属 Phase B），显式传 ``True`` 直接报错；
- ``cross_modal_runtime`` / ``cross_modal_retrieval`` 恒 ``None``。

依赖方向：本模块位于**评估侧**，单向 import 生产符号（``action`` / ``analysis`` /
``memory`` / ``runtime``），生产代码**绝不**反向 import 本包（T2 allowlist 守护）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from home_perception.action.sink import (
    ActionSink,
    InMemoryActionRecorder,
    JsonlActionRecorder,
)
from home_perception.analysis.decision_trace import (
    DecisionTraceRecorder,
    InMemoryRecorder,
)
from home_perception.memory.store import InMemoryStore

__all__ = [
    "DEFAULT_CLOCK_START",
    "MEMORY_BACKENDS",
    "SINK_KINDS",
    "TRACE_RECORDER_KINDS",
    "IntegrationConfigError",
    "IntegrationContext",
    "IntegrationRunnerConfig",
]

# 确定性时钟起点：18:00 UTC 落在 ``ThresholdConfig.odd_hour_set`` 的典型异常时段内，
# 使"同 seed 两次运行逐字节一致"（t1）不受墙钟影响。
DEFAULT_CLOCK_START = datetime(2026, 7, 19, 18, 0, 0, tzinfo=UTC)

MEMORY_BACKENDS: tuple[str, ...] = ("in_memory",)
SINK_KINDS: tuple[str, ...] = ("in_memory", "jsonl")
TRACE_RECORDER_KINDS: tuple[str, ...] = ("in_memory", "jsonl")

# JSONL 落盘文件名（artifact_dir 下）
_ACTION_JSONL_NAME = "action_commands.jsonl"
_TRACE_JSONL_NAME = "decision_traces.jsonl"


class IntegrationConfigError(ValueError):
    """闭环配置非法（fail-closed；绝不静默降级为默认值）。"""


@dataclass(frozen=True, slots=True)
class IntegrationRunnerConfig:
    """闭环运行配置（声明"用哪种探针"，不含任何期望/判定语义）。

    与 ``IntegrationExpectationSuite`` 的分工：本类答"**怎么跑**"（装配形态），
    期望套件答"**判什么**"（验收标准）。二者刻意分离，因为同一装配可以配不同标准。
    """

    memory_backend: Literal["in_memory"] = "in_memory"
    sink_kind: Literal["in_memory", "jsonl"] = "in_memory"
    cross_modal_enabled: bool = False  # Phase A 恒 False
    trace_recorder_kind: Literal["in_memory", "jsonl"] = "in_memory"
    # 任一 kind 为 ``jsonl`` 时必填：落盘目录（本地，绝不联网，ADR-0002）
    artifact_dir: Path | None = None
    clock_start: datetime = DEFAULT_CLOCK_START
    frame_interval_s: float = 0.5

    def __post_init__(self) -> None:
        if self.memory_backend not in MEMORY_BACKENDS:
            raise IntegrationConfigError(
                f"memory_backend={self.memory_backend!r} 非法；Phase A 仅支持 {MEMORY_BACKENDS}"
            )
        if self.sink_kind not in SINK_KINDS:
            raise IntegrationConfigError(f"sink_kind={self.sink_kind!r} 非法；必须属于 {SINK_KINDS}")
        if self.trace_recorder_kind not in TRACE_RECORDER_KINDS:
            raise IntegrationConfigError(
                f"trace_recorder_kind={self.trace_recorder_kind!r} 非法；"
                f"必须属于 {TRACE_RECORDER_KINDS}"
            )
        if self.cross_modal_enabled:
            raise IntegrationConfigError(
                "cross_modal_enabled=True 属 ADR-0034 Phase B；Phase A 拒绝启用（fail-closed）"
            )
        needs_dir = "jsonl" in (self.sink_kind, self.trace_recorder_kind)
        if needs_dir and self.artifact_dir is None:
            raise IntegrationConfigError(
                "sink_kind / trace_recorder_kind 含 'jsonl' 时必须提供 artifact_dir"
            )
        if self.clock_start.tzinfo is None:
            raise IntegrationConfigError("clock_start 必须带时区（naive datetime 破坏可复现性）")
        if self.frame_interval_s < 0:
            raise IntegrationConfigError("frame_interval_s 必须 >= 0")


class _LoopClock:
    """确定性可推进时钟（鸭子实现 ``runtime.pipeline.TickableNowProvider``）。

    刻意**不** import ``runtime.pipeline.DemoClock``：后者在 ``start=None`` 时会
    ``log.warning`` 回退墙钟，且语义上属 Demo 组装层。此处只需最小可控时钟。

    > DRY 债（ADR-0034 实现计划 §3，非阻塞）：仓库现有 ``DemoClock`` /
    > ``scripts/run_benchmark._SimpleClock`` / ``tests/runtime/_helpers.ManualClock``
    > 三处近似实现，本类为第四处。收敛为单一实现是独立重构项，不在 Phase A 范围。
    """

    __slots__ = ("_t", "interval_s")

    def __init__(self, start: datetime, interval_s: float = 0.5) -> None:
        self._t = start
        self.interval_s = interval_s

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def tick(self, dt: float | None = None) -> None:
        from datetime import timedelta

        self._t = self._t + timedelta(seconds=dt if dt is not None else self.interval_s)


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    """闭环探针容器（L1 产物；只读句柄随 ``IntegrationRunResult`` 一同返回）。

    三条**独立**观测通道（F6 交叉校验的物质基础）：

    | 探针 | 观测对象 | 与之交叉校验的生产通道 |
    |---|---|---|
    | ``action_sink`` | ``ActionExecutor`` 实际执行的命令 | ``FrameResult.commands`` |
    | ``trace_recorder`` | ``DecisionEngine`` 的 WARN / SUPPRESS 决策 | ``FrameResult.warnings`` |
    | ``memory_store`` | ``MemoryHook`` 落库的 episodic 记录 | 已产出的 warnings / actions |

    构造纪律：请走 ``build()``。直接构造不被语言层禁止（frozen dataclass 无法拦截），
    但 ``__post_init__`` 会做 fail-closed 契约校验，保证任何来源的实例都满足协议。
    """

    memory_store: InMemoryStore
    trace_recorder: DecisionTraceRecorder
    action_sink: ActionSink
    cross_modal_runtime: Any | None = None
    cross_modal_retrieval: Any | None = None
    clock: Any | None = None

    def __post_init__(self) -> None:
        if self.memory_store is None:
            raise IntegrationConfigError("memory_store 不能为空（Memory Stage 无法读回）")
        if self.trace_recorder is None:
            raise IntegrationConfigError("trace_recorder 不能为空（Decision Stage 无法读回）")
        if self.action_sink is None:
            raise IntegrationConfigError("action_sink 不能为空（Action Stage 无法读回）")
        # 协议符合性（runtime_checkable，只查方法存在性）——写错类型要在装配前暴露，
        # 而不是等到 run() 跑完发现"什么都没观测到"被误判为静默丢弃。
        if not isinstance(self.action_sink, ActionSink):
            raise IntegrationConfigError(
                f"action_sink={type(self.action_sink).__name__} 不满足 ActionSink 协议"
                "（需 record / flush）"
            )
        if not isinstance(self.trace_recorder, DecisionTraceRecorder):
            raise IntegrationConfigError(
                f"trace_recorder={type(self.trace_recorder).__name__} "
                "不满足 DecisionTraceRecorder 协议（需 record / flush）"
            )
        if self.cross_modal_runtime is not None or self.cross_modal_retrieval is not None:
            raise IntegrationConfigError(
                "cross_modal_runtime / cross_modal_retrieval 属 ADR-0034 Phase B；"
                "Phase A 必须为 None（fail-closed）"
            )

    @classmethod
    def build(cls, config: IntegrationRunnerConfig | None = None) -> IntegrationContext:
        """探针的**唯一创建点**（L1）。

        调用方不得逐个 new 探针后自行拼装——那会让"两条通道来自同一次运行"这一
        F6 前提无法保证。``config=None`` 走全默认（全内存探针）。
        """
        cfg = config or IntegrationRunnerConfig()

        memory_store = InMemoryStore()

        action_sink: ActionSink
        trace_recorder: DecisionTraceRecorder
        if cfg.sink_kind == "jsonl":
            assert cfg.artifact_dir is not None  # __post_init__ 已保证
            action_sink = JsonlActionRecorder(path=cfg.artifact_dir / _ACTION_JSONL_NAME)
        else:
            action_sink = InMemoryActionRecorder()

        if cfg.trace_recorder_kind == "jsonl":
            assert cfg.artifact_dir is not None
            from home_perception.analysis.decision_sink import JsonlTraceRecorder

            trace_recorder = JsonlTraceRecorder(path=cfg.artifact_dir / _TRACE_JSONL_NAME)
        else:
            trace_recorder = InMemoryRecorder()

        return cls(
            memory_store=memory_store,
            trace_recorder=trace_recorder,
            action_sink=action_sink,
            cross_modal_runtime=None,  # Phase A 恒 None
            cross_modal_retrieval=None,
            clock=_LoopClock(start=cfg.clock_start, interval_s=cfg.frame_interval_s),
        )

"""Memory Consumer 异常（C-0）。

分层定义，便于 MemoryConsumerHook（C-4）做异常隔离：Consumer 调用失败只记日志、
不影响实时风险主链路。
"""

from __future__ import annotations


class ConsumerError(Exception):
    """Consumer 层根异常。"""

class RetrievalError(ConsumerError):
    """Retrieval 阶段失败（召回 / 窗口计算 / 排序）。"""


class AggregationError(ConsumerError):
    """Aggregation 阶段失败（聚合 / 置信度分级）。"""


class ContextBuildError(ConsumerError):
    """ContextBuilder 阶段失败（组装 / 不变量校验 C1/C5）。"""


class BelowThresholdError(ConsumerError):
    """未达触发阈值（Phase 1 模式 B 门控）。

    属正常跳过信号，非错误：访客/事件未满足 MEDIUM+ 或已知访客再现条件时抛出，
    hook 捕获后静默跳过、不记录 ReasoningInput。
    """


class ReasoningError(ConsumerError):
    """Reasoning Engine 阶段失败（C-6 接入）。

    与 Retrieval/Aggregation/ContextBuild 同族：属 Consumer 管道的下游阶段异常，
    由 ``MemoryConsumerHook.maybe_reason`` 捕获后隔离（只计错误 + 日志，不中断主链路）。
    """


__all__ = [
    "AggregationError",
    "BelowThresholdError",
    "ConsumerError",
    "ContextBuildError",
    "ReasoningError",
    "RetrievalError",
]

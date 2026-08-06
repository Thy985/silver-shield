"""Memory Consumer 组件接口（ADR-0025 §3.1–§3.4 / DESIGN-memory-consumer.md §1, C-0）。

定义五个 ABC：``Retrieval`` / ``Aggregation`` / ``ContextBuilder`` / ``MemoryConsumer``
/ ``ReasoningEngine``。前四者构成严格单向管道（Retrieval → Aggregation →
ContextBuilder → MemoryConsumer 编排），``ReasoningEngine`` 是管道的下游消费方
（C-6 接入），由 ``MemoryConsumerHook`` 在产出 ``ReasoningInput`` 后调用。

硬边界（ADR-0025）：Consumer 不决策、不改 Risk Score（C1）；接口层不依赖任何具体
实现；``MemoryConsumer`` 仅做编排，自身不持有跨请求状态（C2）；``ReasoningEngine``
只读 ``ReasoningInput``、只产 ``ReasoningResult``，同样不决策、不写 Memory。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
    ReasoningResult,
    RiskPattern,
    VisitorProfile,
)
from home_perception.memory.records import EpisodicRecord


class Retrieval(ABC):
    """检索组件（只召回）。

    给定 ``CurrentEvent``，从 Memory 召回排序后的相关历史原始记录。
    绝不计算异常判定、绝不聚合。
    """

    @abstractmethod
    def retrieve(self, current_event: CurrentEvent) -> list[EpisodicRecord]:
        """召回与 current_event 相关的历史 EpisodicRecord 列表（确定性排序）。"""
        raise NotImplementedError


class Aggregation(ABC):
    """聚合组件（只计算）。

    把 Retrieval 交付的原始记录聚合成长期模式视图（VisitorProfile / RiskPattern）。
    绝不内部再调 Retrieval；绝不回答"是否异常"的最终结论。
    """

    @abstractmethod
    def aggregate(
        self, records: list[EpisodicRecord]
    ) -> tuple[VisitorProfile | None, RiskPattern | None]:
        """聚合召回记录，返回 (访客画像, 风险模式)；样本不足时两者或其一可为 None。"""
        raise NotImplementedError


class ContextBuilder(ABC):
    """组装组件（只组装）。

    把前三步结果拼成 ReasoningInput。绝不召回或聚合。
    """

    @abstractmethod
    def build(
        self,
        current_event: CurrentEvent,
        records: list[EpisodicRecord],
        profile: VisitorProfile | None,
        pattern: RiskPattern | None,
        evidence_refs: tuple[str, ...],
        previous_actions: tuple[ActionRecord, ...],
        conflicts: tuple[ConflictFlag, ...],
    ) -> ReasoningInput:
        """组装交付 Reasoning Engine 的 ReasoningInput（C1 无 score / C5 溯源）。"""
        raise NotImplementedError


class MemoryConsumer(ABC):
    """消费编排接口（C-4 orchestrator 实现）。

    consume 仅按序驱动 Retrieval → Aggregation → ContextBuilder，产出 ReasoningInput；
    无跨请求状态（C2）。
    """

    @abstractmethod
    def consume(self, current_event: CurrentEvent) -> ReasoningInput:
        """消费一次 current_event，产出 ReasoningInput。"""
        raise NotImplementedError


class ReasoningEngine(ABC):
    """推理引擎（消费 ReasoningInput → 产出 ReasoningResult）。

    仅做参考推理：**不决策、不产分数、不改 Memory**（守 ADR-0010 单一决策中心 +
    ADR-0025 C1/C2）。``infer`` 必须是纯函数：同输入同输出（C3 确定性），只读
    ``ReasoningInput``，不写任何外部状态（C2）。
    """

    @abstractmethod
    def infer(self, ctx: ReasoningInput) -> ReasoningResult:
        """把 ReasoningInput 推理为 ReasoningResult（参考推理，非决策）。"""
        raise NotImplementedError


__all__ = [
    "Aggregation",
    "ContextBuilder",
    "MemoryConsumer",
    "ReasoningEngine",
    "Retrieval",
]

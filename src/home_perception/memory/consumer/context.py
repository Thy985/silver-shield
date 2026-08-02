"""Memory Consumer · Context Builder 默认实现（C-3，ADR-0025 §3.3 / DESIGN §3.3）。

RuleBasedContextBuilder 是 ``ContextBuilder`` 的默认实现，职责 = **只组装**：把
Retrieval 结果（records）、Aggregation 结果（profile / pattern），以及由编排器
（C-4 ``MemoryConsumer``）计算后传入的 ``evidence_refs`` / ``previous_actions`` /
``conflicts`` 拼成 ``ReasoningInput``，交付 Reasoning Engine。

硬边界（承接 ADR-0025）：
- C1 无 score / decision / warning：``ReasoningInput`` 数据契约本身不含这些字段
  （contracts.py 天然满足）；本组件不引入任何评分 / 决策。
- C2 只读：build 不调用 Retrieval / Aggregation，也不修改任何输入。
- C3 确定性：``historical_context`` 按 ``(enter_time, record_id)`` 确定性排序后转
  tuple，保证同输入两次产出顺序一致（审计 / 回放一致）。
- C5 可追溯：``historical_context`` 透传 records 自带的 ``source_event_ids``
  （ADR-0024 I4 可解释性），build 不篡改、不丢弃。
"""

from __future__ import annotations

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    EvidenceRef,
    ReasoningInput,
    RiskPattern,
    VisitorProfile,
)
from home_perception.memory.consumer.exceptions import ContextBuildError
from home_perception.memory.consumer.interfaces import ContextBuilder
from home_perception.memory.records import EpisodicRecord


class RuleBasedContextBuilder(ContextBuilder):
    """默认 Context Builder：纯组装器（C-3）。

    仅组装，不召回、不聚合、不检测冲突。``evidence_refs`` / ``previous_actions`` /
    ``conflicts`` 由 C-4 编排器（MemoryConsumer.consume）计算后传入——本组件**只组装**。
    """

    def build(
        self,
        current_event: CurrentEvent,
        records: list[EpisodicRecord],
        profile: VisitorProfile | None,
        pattern: RiskPattern | None,
        evidence_refs: tuple[EvidenceRef, ...],
        previous_actions: tuple[ActionRecord, ...],
        conflicts: tuple[ConflictFlag, ...],
    ) -> ReasoningInput:
        """组装 ReasoningInput（C1 无 score / C3 确定性排序 / C5 溯源）。

        Args:
            current_event: 当前触发 Consumer 的事件投影（必填，不可 None）。
            records: Retrieval 召回的历史 EpisodicRecord 列表（可空）。
            profile: Aggregation 产出的访客画像（可空）。
            pattern: Aggregation 产出的风险模式（可空）。
            evidence_refs: 证据引用（编排器从 records 派生，默认空）。
            previous_actions: 既往动作投影（编排器从 records 派生，默认空）。
            conflicts: 历史与当前冲突标记（编排器检测，默认空）。

        Returns:
            交付 Reasoning Engine 的 ReasoningInput（frozen，可序列化）。

        Raises:
            ContextBuildError: current_event 或 records 为 None。
        """
        if current_event is None:
            raise ContextBuildError("current_event 不能为 None")
        if records is None:
            raise ContextBuildError("records 不能为 None")

        # C3：确定性排序（进入时间升序，record_id 兜底），同输入两次产出顺序一致
        ordered = sorted(records, key=lambda ep: (ep.enter_time, ep.record_id))

        return ReasoningInput(
            current_event=current_event,
            historical_context=tuple(ordered),
            visitor_profile=profile,
            risk_pattern=pattern,
            evidence_refs=evidence_refs,
            previous_actions=previous_actions,
            conflicts=conflicts,
        )

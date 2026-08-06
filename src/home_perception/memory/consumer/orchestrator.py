"""RuleBasedMemoryConsumer（C-4 默认编排器，ADR-0025 §3.4 / DESIGN §4.2）。

只编排、不决策（C1）。``consume`` 按严格单向管道驱动
**Retrieval → Aggregation → ContextBuilder**，三组件互不调用；期间由编排器补齐
ContextBuilder 明确不负责的三项派生数据（见 ``context.py``：「由 C-4 编排器计算后
传入」）：

- ``evidence_refs``：从召回记录的 ``evidence_refs`` 扁平化 + 按 ``evidence_id`` 去重；
- ``previous_actions``：``ActionSummary`` → ``ActionRecord`` 投影 + 按 ``command_id`` 去重；
- ``conflicts``：历史 vs 当前的差异标记（``risk_escalation`` / ``behavior_shift``）。

硬边界：

- **C1 不决策**：只陈述"历史是什么、当前是什么、二者何处不同"，绝不给结论、不算
  分数、不产 Warning。冲突**只标记不解决**（ADR-0025 §3.6），交 Reasoning 推理。
- **C2 只读**：不写 Memory、不修改任何输入对象；编排器无跨请求状态。
- **C3 确定性**：evidence / action 汇总在与 ``RuleBasedContextBuilder`` **相同**的
  记录序（``(enter_time, record_id)``）上进行，冲突按固定次序产出（先
  ``risk_escalation``，再按标记名升序的 ``behavior_shift``）→ 同输入两次结果逐字段一致。
- **C5 可追溯**：不丢弃 records 自带的 ``source_event_ids``（透传由 ContextBuilder 完成）。

冷启动不产伪冲突：无历史记录时 ``conflicts`` 恒为 ``()``——"没有历史"不等于
"与历史冲突"，否则首次来访会被标记为行为突变（review 教训：阈值/标记类断言须能
被变异验证，见 AGENTS.md 测试有效性铁律）。
"""

from __future__ import annotations

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
)
from home_perception.memory.consumer.conventions import (
    extract_behavior_markers,
    max_risk_level,
    risk_rank,
)
from home_perception.memory.consumer.exceptions import ConsumerError
from home_perception.memory.consumer.interfaces import (
    Aggregation,
    ContextBuilder,
    MemoryConsumer,
    Retrieval,
)
from home_perception.memory.records import EpisodicRecord


class RuleBasedMemoryConsumer(MemoryConsumer):
    """默认编排器：单向驱动三组件并派生 evidence / actions / conflicts（C-4）。

    三个组件由外部注入（依赖倒置）：默认组合为 ``RuleBasedRetrieval`` +
    ``RuleBasedAggregation`` + ``RuleBasedContextBuilder``，但编排器只依赖 ABC，
    便于后续替换为 O1 VectorRetrieval 等实现而不改本类。
    """

    def __init__(
        self,
        retrieval: Retrieval,
        aggregation: Aggregation,
        context_builder: ContextBuilder,
    ) -> None:
        self._retrieval = retrieval
        self._aggregation = aggregation
        self._context_builder = context_builder

    def consume(self, current_event: CurrentEvent) -> ReasoningInput:
        """消费一次 ``current_event``，产出 ``ReasoningInput``。

        Args:
            current_event: 当前触发事件投影（不可 None）。

        Returns:
            交付 Reasoning Engine 的 ``ReasoningInput``（C1 无 score / decision）。

        Raises:
            ConsumerError: 编排失败。子层已分类的异常（``RetrievalError`` /
                ``AggregationError`` / ``ContextBuildError``）原样上抛，**不二次包装**，
                以便 ``MemoryConsumerHook`` 与日志区分失败阶段；其余未分类异常统一
                转译为 ``ConsumerError``，不向上抛裸异常（否则 hook 的异常隔离会
                退化为吞掉一切）。
        """
        if current_event is None:
            raise ConsumerError("current_event 不能为 None")
        try:
            records = self._retrieval.retrieve(current_event)
            profile, pattern = self._aggregation.aggregate(records)
            return self._context_builder.build(
                current_event,
                records,
                profile,
                pattern,
                self._collect_evidence(records),
                self._collect_actions(records),
                self._detect_conflicts(current_event, records),
            )
        except ConsumerError:  # 子层已分类异常原样上抛，保留失败阶段信息
            raise
        except Exception as exc:  # 未分类异常统一转译，绝不向上抛裸异常
            raise ConsumerError(
                f"Consumer 编排失败 visitor={current_event.visitor_instance_id!r}: {exc}"
            ) from exc

    # -- 确定性序（C3）---------------------------------------------------------
    @staticmethod
    def _ordered(records: list[EpisodicRecord] | None) -> list[EpisodicRecord]:
        """与 ``RuleBasedContextBuilder`` 完全相同的确定性序（``(enter_time, record_id)``）。

        刻意复用同一排序键：``evidence_refs`` / ``previous_actions`` 的顺序因此与
        ``historical_context`` 一一对应，审计时可按同一时间轴对齐（C3 + C5）。
        """
        return sorted(records or [], key=lambda ep: (ep.enter_time, ep.record_id))

    # -- 证据汇总（C5 溯源）----------------------------------------------------
    def _collect_evidence(self, records: list[EpisodicRecord]) -> tuple[str, ...]:
        """扁平化召回记录的 ``evidence_refs``，按 ``evidence_id`` 去重保序。

        ADR-0027 Slice A 起 ``EpisodicRecord.evidence_refs`` 为 ``evidence_id``
        字符串列表（独立 ``EvidenceItem`` 以 ID 解析，ADR-0024 I2 单调性）；本方法
        直接扁平化字符串并去重，无需构造 ``EvidenceRef`` 对象。
        """
        seen: set[str] = set()
        collected: list[str] = []
        for episode in self._ordered(records):
            for ref in episode.evidence_refs or []:
                if ref in seen:
                    continue
                seen.add(ref)
                collected.append(ref)
        return tuple(collected)

    # -- 既往动作投影 ----------------------------------------------------------
    def _collect_actions(self, records: list[EpisodicRecord]) -> tuple[ActionRecord, ...]:
        """把记录中的 ``ActionSummary`` 投影为 ``ActionRecord``，按 ``command_id`` 去重保序。

        两个 dataclass 字段一一对应（``command_type`` / ``command_id`` / ``status`` /
        ``error``）；分属存储侧与消费侧契约，故显式逐字段投影而非直接复用对象，
        避免消费侧持有存储侧可变对象（C2）。
        """
        seen: set[str] = set()
        collected: list[ActionRecord] = []
        for episode in self._ordered(records):
            for action in episode.actions or []:
                if action.command_id in seen:
                    continue
                seen.add(action.command_id)
                collected.append(
                    ActionRecord(
                        command_type=action.command_type,
                        command_id=action.command_id,
                        status=action.status,
                        error=action.error,
                    )
                )
        return tuple(collected)

    # -- 冲突检测（C4 透明，只标记不解决）--------------------------------------
    def _detect_conflicts(
        self, current_event: CurrentEvent, records: list[EpisodicRecord]
    ) -> tuple[ConflictFlag, ...]:
        """标记历史与当前的差异（不解决、不覆盖，交 Reasoning 推理）。"""
        ordered = self._ordered(records)
        if not ordered:
            # 冷启动：无历史 = 无"历史 vs 当前"可冲突，避免首次来访被误标为突变
            return ()
        conflicts: list[ConflictFlag] = []
        escalation = self._risk_escalation(current_event, ordered)
        if escalation is not None:
            conflicts.append(escalation)
        conflicts.extend(self._behavior_shifts(current_event, ordered))
        return tuple(conflicts)

    @staticmethod
    def _risk_escalation(
        current_event: CurrentEvent, records: list[EpisodicRecord]
    ) -> ConflictFlag | None:
        """当前风险等级**严格高于**历史最高时标记 ``risk_escalation``。

        持平或下降不标记：C4 关注的是"历史画像解释不了当前"的情形，等级回落不构成
        需要 Reasoning 额外裁决的冲突。
        """
        current = current_event.risk_level
        if current is None:
            return None
        historical_max = max_risk_level(ep.risk_level for ep in records)
        if risk_rank(current) <= risk_rank(historical_max):
            return None
        # ConflictFlag 四字段均要求非空：历史无风险等级时用显式 "none" 占位
        historical = historical_max if historical_max is not None else "none"
        return ConflictFlag(
            type="risk_escalation",
            historical=historical,
            current=current,
            detail=(
                f"历史 {len(records)} 次访问最高风险等级为 {historical}，本次为 {current}"
                f"（仅陈述差异，不作判定）"
            ),
        )

    @staticmethod
    def _behavior_shifts(
        current_event: CurrentEvent, records: list[EpisodicRecord]
    ) -> list[ConflictFlag]:
        """当前事件出现历史未见过的行为标记时，逐个标记 ``behavior_shift``。

        空标记被丢弃（与 ``RuleBasedAggregation`` 同口径）；新标记按名称升序产出，
        保证 C3 确定性与 ``current_event.markers`` 的输入顺序无关。
        """
        historical = tuple(sorted({m for m in extract_behavior_markers(records) if m}))
        current_markers = sorted({m for m in current_event.markers if m})
        new_markers = [m for m in current_markers if m not in historical]
        if not new_markers:
            return []
        historical_label = ",".join(historical) if historical else "none"
        return [
            ConflictFlag(
                type="behavior_shift",
                historical=historical_label,
                current=marker,
                detail=(
                    f"历史行为标记 [{historical_label}] 中未出现 {marker!r}，本次事件新增"
                    f"（仅陈述差异，不作判定）"
                ),
            )
            for marker in new_markers
        ]


__all__ = ["RuleBasedMemoryConsumer"]

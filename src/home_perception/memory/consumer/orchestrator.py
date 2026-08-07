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

from dataclasses import replace

from home_perception.common.logging import get_logger
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
from home_perception.memory.cross_modal_explainer import (
    CrossModalExplainer,
    CrossModalRetrieval,
    CrossModalRetrievalError,
)
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import MemoryStore

log = get_logger(__name__)


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
        *,
        cross_modal_retrieval: CrossModalRetrieval | None = None,
        cross_modal_explainer: CrossModalExplainer | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._aggregation = aggregation
        self._context_builder = context_builder
        # ADR-0029 D4（Slice C）：跨模态解释可选注入——三者齐全才生效，否则零行为变化。
        # 注入的解释器产出 ``CrossModalContext``（非 ``CrossModalLink``）；``memory_store``
        # 仅用于解释查 peer episode 与解析当前 episode（C2 只读，不写）。
        self._cross_modal_retrieval = cross_modal_retrieval
        self._cross_modal_explainer = cross_modal_explainer
        self._memory_store = memory_store

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
            result = self._context_builder.build(
                current_event,
                records,
                profile,
                pattern,
                self._collect_evidence(records),
                self._collect_actions(records),
                self._detect_conflicts(current_event, records),
            )
            # ADR-0029 D4（Slice C）：可选注入时附加跨模态解释上下文（零行为变化；
            # 失败隔离——增强路径异常仅日志，不影响主链路 ReasoningInput 交付）。
            result = self._maybe_attach_cross_modal(result, current_event)
            return result
        except ConsumerError:  # 子层已分类异常原样上抛，保留失败阶段信息
            raise
        except Exception as exc:  # 未分类异常统一转译，绝不向上抛裸异常
            raise ConsumerError(
                f"Consumer 编排失败 visitor={current_event.visitor_instance_id!r}: {exc}"
            ) from exc

    # -- 跨模态解释附加（ADR-0029 D4，可选注入，零行为变化）--------------------
    def _maybe_attach_cross_modal(
        self, result: ReasoningInput, current_event: CurrentEvent
    ) -> ReasoningInput:
        """若三者均注入，解析当前 episode → 取跨模态边 → 投影为 ``CrossModalContext`` 附加。

        - 仅当 retrieval + explainer + memory_store 三者均非 None 才生效；
        - 解析当前 episode 用 ``MemoryStore.get_episodic_by_visitor``（取最新离场那条），
          再经 ``CrossModalRetrieval.get_links_for_episode``（v1 主路径，**不**走延期的
          ``get_links_for_visitor``）；
        - 失败隔离：任何异常仅日志跳过，返回原 ``result``（主链路不受影响）。
        """
        if (
            self._cross_modal_retrieval is None
            or self._cross_modal_explainer is None
            or self._memory_store is None
        ):
            return result
        try:
            current_ep_id = self._resolve_current_episode_id(current_event)
            if current_ep_id is None:
                return result
            links = self._cross_modal_retrieval.get_links_for_episode(current_ep_id)
            if not links:
                return result
            contexts = tuple(
                self._cross_modal_explainer.explain(lk, self._memory_store) for lk in links
            )
            if not contexts:
                return result
            # 附加而非裁决：只挂描述性 context，不修改任何既有字段
            return replace(result, cross_modal_contexts=contexts)
        except (CrossModalRetrievalError, ValueError) as exc:  # 可选增强隔离：不影响主链路
            log.warning(
                "cross_modal.attach_failed",
                error=str(exc),
                visitor=current_event.visitor_instance_id,
            )
            return result

    def _resolve_current_episode_id(self, current_event: CurrentEvent) -> str | None:
        """解析"当前访问"对应的 episode record_id（内部 lookup，非延期 API）。

        经 ``MemoryStore.get_episodic_by_visitor`` 取该访客全部 episode，取离场时刻最新
        那条（确定性 tie-break：record_id 升序）作为当前 episode。纯内部解析，不暴露为
        ``get_links_for_visitor`` 公共查询（该 API 归 ``MemoryQuery``，ADR-0029 D1）。
        """
        episodes = self._memory_store.get_episodic_by_visitor(current_event.visitor_instance_id)
        if not episodes:
            return None
        current = max(episodes, key=lambda ep: (ep.leave_time, ep.record_id))
        return current.record_id

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

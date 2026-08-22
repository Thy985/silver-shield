"""决策层编排器（P0-8 · 决策层 · 入口）。

> **P0-8 = 决策层。** `DecisionEngine` 消费 `PerceptionEvent[]`，按 `DecisionPolicy`
> 决策，输出 `Optional[WarningEvent]`。**不**直接执行任何动作 —— 留给 P0-9 行动层。

继续 ADR-0010 5 条决策：
1. `PerceptionEvent` **不**直接触发通知 → 必须先经 DecisionEngine → WarningEvent
2. `WarningEvent` 是决策层对象（严重度 + 建议动作），**不**含最终判定字段
3. `risk_level` 是决策严重度，**不是**诈骗概率
4. `DecisionPolicy` 独立于 `Rule`（不复算 Feature / 不重新组合 Rule）
5. Action 执行（MQTT / 通知 / 升级）延迟到 P0-9
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from ..common.logging import get_logger
from ..common.timeutil import now_dt
from .decision_contract import DecisionInput
from .decision_policy import DecisionContext, DecisionPolicy, RuleBasedDecisionPolicy
from .decision_trace import (
    DecisionTraceRecorder,
    DecisionTraceSpan,
    build_suppress_trace,
    build_warning_trace,
    compute_policy_fingerprint,
)
from .perception import PerceptionEvent
from .risk_signal import RiskSignal
from .warning import WarningEvent

log = get_logger(__name__)


# ============================================================================
# DecisionEngine
# ============================================================================


class DecisionEngine:
    """决策层编排器（P0-8 入口）。

    流程：
    1. 接收一次评估周期内的 `PerceptionEvent[]`（来自 RuleEngine）
    2. 构造 `DecisionContext`（注入 `elder_id` / `now` / `extra`）
    3. 委托 `DecisionPolicy.decide()` → `Optional[WarningEvent]`
    4. 记录决策日志（policy / risk_level / action / trigger_count）
    5. 输出 `WarningEvent` 或 None（None = 决策层无动作）

    用法：
        engine = DecisionEngine(elder_id="elder_001")
        for perception_events in perception_stream:
            warning = engine.evaluate(perception_events)
            if warning is not None:
                # P0-9 行动层接手（MQTT 上报 / 通知家属 / 升级社区）
                ...

    边界（ADR-0010）：
    - **不**调 MQTT / **不**发通知 / **不**升级社区（→ P0-9 责任）
    - **不**重算 Feature / **不**重新组合 Rule（→ RuleEngine 责任）
    - **不**做最终判定（→ 中心综合判断责任）
    """

    def __init__(
        self,
        elder_id: str,
        policy: DecisionPolicy | None = None,
        now_provider=None,
        trace_recorder: DecisionTraceRecorder | None = None,
        memory_store: Any | None = None,
    ):
        if not elder_id or not str(elder_id).strip():
            raise ValueError("elder_id 不能为空（WarningEvent 必填字段）")
        self.elder_id = elder_id
        self.policy = policy or RuleBasedDecisionPolicy()
        self._now = now_provider or now_dt
        # Slice B：可选 trace 采集接缝，默认关闭（`None` = 不采集，零行为变化，T2）。
        # 不进入 `DecisionInput`（ADR-0031 D6），避免 Trace→DecisionInput 循环依赖。
        self.trace_recorder = trace_recorder
        # G0-3：可选 MemoryStore 注入（默认 None = Memory 缺席，ADR-0030 D2 零行为变化）。
        # 注入后 evaluate 装配 ReasoningInput（historical_context），供 policy 历史感知
        # 与 trace.historical_record_ids 引用（"决策引用了历史 Episode"可证）。
        self.memory_store = memory_store

    def _build_reasoning_input(
        self, perception_events: list[PerceptionEvent]
    ) -> Any | None:
        """G0-3：从触发事件取 visitor → 检索 MemoryStore 历史 episodes → 最小 ReasoningInput。

        - memory_store 缺席 / 无 visitor / 无历史 → None（Memory 可缺席，ADR-0030 D2）；
        - 最小构造：``current_event`` + ``historical_context``（EpisodicRecord 列表），
          ``visitor_profile`` / ``risk_pattern`` 显式 None（C1：不含量分数/判定）；
        - 失败隔离：检索/构造异常仅日志返回 None（决策主链路不受影响，VM-5 探针铁律）。
        """
        if self.memory_store is None:
            return None
        visitor_id = next(
            (
                str(getattr(ev, "visitor_id", ""))
                for ev in perception_events
                if getattr(ev, "visitor_id", None)
            ),
            None,
        )
        if not visitor_id:
            return None
        try:
            records = self.memory_store.get_episodic_by_visitor(visitor_id)
            if not records:
                return None

            from home_perception.memory.consumer.contracts import (
                CurrentEvent,
                ReasoningInput,
            )

            current = CurrentEvent(
                event_id=f"decision:{self.elder_id}:{self._now():%Y%m%d%H%M%S}",
                event_type="visitor_event",
                visitor_instance_id=visitor_id,
                occurred_at=self._now().astimezone(UTC),
            )
            return ReasoningInput(
                current_event=current,
                historical_context=tuple(sorted(records, key=lambda ep: ep.enter_time)),
                visitor_profile=None,
                risk_pattern=None,
            )
        except Exception:  # 失败隔离：历史缺失不影响决策主链路
            log.exception("memory.reasoning_input_failed", visitor=visitor_id)
            return None

    def evaluate(
        self,
        perception_events: list[PerceptionEvent],
        risk_signals: tuple[RiskSignal, ...] = (),
    ) -> WarningEvent | None:
        """单次决策：消费 PerceptionEvent 列表，输出 WarningEvent 或 None。

        ``risk_signals``（ADR-0040 D1 一等输入）：本评估周期收到的 Runtime
        RiskSignal（RAISED/CLEARED），原样透传 ``DecisionInput.risk_signals``
        供 policy 消费；缺省空元组 = 无信号输入（视觉规则路径向后兼容，
        ADR-0030 D2「Memory 可缺席」同款缺席语义）。

        返回 None 的典型情况：
        - 空列表
        - 全部是 visit_normal 且无 is_odd_hour 叠加（普通访问）
        """
        ctx = DecisionContext(elder_id=self.elder_id, now=self._now())
        # G0-3：可选装配 ReasoningInput（历史记忆上下文）。memory_store 缺席 →
        # reasoning_input=None（ADR-0030 D2「Memory 可缺席」零行为变化）；注入时从
        # 触发事件的 visitor 检索历史 episodes（含 prior_episodes 预置）。
        reasoning_input = self._build_reasoning_input(perception_events)
        # Slice B：装配 DecisionInput（单入参契约）。本期记忆/推理/既往字段尚未接线，
        # 一律显式 None —— 满足 ADR-0030 D2「Memory 可缺席」原则，零行为变化。
        # risk_signals（ADR-0040）：Runtime 信号一等输入透传；DecisionInput 内部
        # 做 (created_at, signal_id) 稳定排序与元素级类型守卫，engine 不重复校验。
        input = DecisionInput(
            trigger_events=tuple(perception_events),
            decision_context=ctx,
            reasoning_input=reasoning_input,
            reasoning_result=None,
            prior_warning=None,
            risk_signals=tuple(risk_signals),
        )
        # Slice C（D6.1）：trace 生命周期边界 CREATED。仅当 recorder 注入时开 span 并
        # 绑定到策略；span 为 None 时策略不写 partial，决策逐字不变（T2）。finally 解绑，
        # 避免 span 跨调用泄漏（单一写主：engine 拥有 span 生命周期，策略只写 partial）。
        span = DecisionTraceSpan() if self.trace_recorder is not None else None
        self.policy.bind_trace_span(span)
        try:
            warning = self.policy.decide(input)
        finally:
            self.policy.bind_trace_span(None)
        if warning is not None:
            log.info(
                "decision.warning_emitted",
                warning_id=str(warning.warning_id),
                elder_id=warning.elder_id,
                device_id=warning.device_id,
                risk_level=warning.risk_level,
                recommended_action=warning.recommended_action,
                trigger_count=len(perception_events),
                perception_score=round(warning.perception_score, 4),
            )
            # Slice B：WARN 路径产出完整 trace（采集接缝默认关闭，仅当 recorder 注入时）。
            # 失败隔离（T3）：构建/记录异常只日志，决策照常返回，trace 故障不丢结果、
            # 不污染 WarningEvent（T1 只写不读）。
            if self.trace_recorder is not None:
                try:
                    trace = build_warning_trace(
                        input=input,
                        warning=warning,
                        policy_name=self.policy.name,
                        routing_table=getattr(self.policy, "routing_table", {}),
                        arm="production",
                        correlation_id="",
                    )
                    self.trace_recorder.record(trace)
                except Exception:  # 失败隔离（T3）：决策层不因 trace 故障而丢结果
                    log.exception(
                        "decision.trace_failed",
                        warning_id=str(warning.warning_id),
                    )
        else:
            # Slice C（核心价值）：SUPPRESS 路径留痕——决策层首次可观测「为什么没报警」。
            # 失败隔离前提：若策略未写入 reason（如子类未实现），不抛异常、不丢决策，
            # 仅记日志并跳过留痕（防御性，正常策略三条 return None 前必写 reason）。
            if self.trace_recorder is not None:
                try:
                    if span is None or span.suppress_reason is None:
                        log.warning(
                            "decision.trace_suppress_reason_missing",
                            policy=self.policy.name,
                        )
                        return warning
                    trace = build_suppress_trace(
                        input=input,
                        suppress_reason=span.suppress_reason,
                        considered_candidates=span.considered_candidates,
                        policy_name=self.policy.name,
                        routing_table=getattr(self.policy, "routing_table", {}),
                        arm="production",
                        correlation_id="",
                    )
                    self.trace_recorder.record(trace)
                except Exception:  # 失败隔离（T3）：决策层不因 trace 故障而丢结果
                    log.exception("decision.trace_failed", warning_id=None)
        return warning

    # ------------------------------------------------------------------
    # ADR-0034 Phase B.3 探针协议：PolicyFingerprintProvider
    # ------------------------------------------------------------------
    def policy_fingerprint(self) -> str:
        """决策策略指纹（供闭环指纹 ``loop_fingerprint`` 的 ``policy_fp`` 成分）。

        把"策略指纹怎么取"固化到引擎**公开 API**：外部（ADR-0034 闭环指纹）经
        ``PolicyFingerprintProvider`` 协议调用本方法，**不**直接触碰
        ``policy.routing_table`` 内部结构——未来策略从 ``RuleBasedDecisionPolicy``
        演化为 ``PolicyProvider`` / ``RuleRegistry`` 时，本方法内部实现可换，
        协议方法签名不变（ADR-0034 零改动）。

        计算语义与 ADR-0031 ``compute_policy_fingerprint`` 完全一致（同一纯函数，
        规范化序列化 + sha256）；``routing_table`` 缺失（子类未提供）按空 dict 参与
        计算——本方法只负责"取"，"非空校验"由调用方 fail-closed 负责。
        """
        routing_table = getattr(self.policy, "routing_table", {})
        return compute_policy_fingerprint(routing_table)

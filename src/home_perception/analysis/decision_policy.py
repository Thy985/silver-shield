"""决策策略层（P0-8 · 决策层）。

> **P0-8 = 决策层。** `DecisionPolicy` 消费 `PerceptionEvent[]` 输出 `Optional[WarningEvent]`。
> 继续 ADR-0007 / ADR-0008 / ADR-0009 / ADR-0010 边界：
> - DecisionPolicy 独立于 Rule（不复算 Feature，也不重新组合 Rule）
> - DecisionPolicy 不产生"最终判定"字段（risk_level 是严重度，不是诈骗概率）
> - DecisionPolicy 不直接执行（不调 MQTT / 不通知家属 / 不升级社区）—— 留给 P0-9
> - 策略可替换：MVP = RuleBasedDecisionPolicy；v2 = ML 评分；v3 = LLM 解释
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型注解；运行期不导入，避免 analysis 内循环
    from .decision_contract import DecisionInput
    from .decision_trace import CandidateRecord, SuppressReason

from ..common.logging import get_logger
from .perception import PerceptionEvent
from .risk_signal import SignalTransition
from .warning import (
    RECOMMENDED_ACTIONS,
    RISK_LEVELS,
    WarningEvent,
)

log = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# DecisionContext：决策上下文（向 DecisionPolicy 注入时间 / 老人 ID / 业务配置等）
# ============================================================================


@dataclass
class DecisionContext:
    """DecisionPolicy 执行上下文。

    字段：
    - `elder_id`：被守护老人 ID（WarningEvent 必填）
    - `now`：当前时间（UTC，可注入便于测试）
    - `extra`：扩展字段（业务规则 / 黑白名单 / 历史画像等 v2 注入）

    DecisionPolicy 接收 `DecisionContext` 而非全局单例，便于：
    - 单元测试独立构造上下文
    - 运行时配置热更新（v2）
    - 策略不可见全局状态
    """

    elder_id: str
    now: datetime = field(default_factory=_utc_now)
    extra: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# DecisionPolicy 抽象基类
# ============================================================================


class DecisionPolicy(ABC):
    """决策策略抽象基类。所有具体策略必须实现 `decide(input: DecisionInput)`。

    设计原则（ADR-0010 Decision 4）：
    - 输入：`DecisionInput`（ADR-0030 D2 收敛载体；含本周期 `trigger_events` + `decision_context`
      + 可选记忆/推理/既往字段）
    - 输出：`Optional[WarningEvent]`（None 表示无需发警告，例如全部是普通访问）
    - **不**复算 Feature（Feature 是 P0-7a 数值信号层）
    - **不**重新组合 Rule（Rule 组合已在 P0-7b CompositeRule 完成，输出 high_risk_approach）
    - **不**直接执行（MQTT / 通知 / 升级 留给 P0-9）
    """

    name: str = "DecisionPolicy"

    def __init__(self) -> None:
        # Slice C（ADR-0031 D6.1）：trace 生命周期 span 由 engine 注入；默认 None
        # （recorder 关闭态，零行为变化）。策略只写 partial，不拥有 span 生命周期。
        # 类型用 `Any` 以规避 analysis 内循环导入（真实类型见
        # `decision_trace.DecisionTraceSpan`，运行期以鸭子类型访问）。
        self._trace_span: Any = None

    def bind_trace_span(self, span: Any) -> None:
        """（可选）绑定 engine 创建的 trace span，供策略在抑制路径写入 partial。

        `span` 为 `None` 表示本周期不采集（recorder 关闭）；`decide` 在写入前判空，
        保证零行为变化（T2）。单一写主（D6.1）：策略仅经本 span 写 `suppress_reason` /
        `considered_candidates`，**禁止**写 `identity` 或覆盖 `outcome` 封口字段。
        """
        self._trace_span = span

    @abstractmethod
    def decide(self, input: DecisionInput) -> WarningEvent | None:
        """消费 `DecisionInput`（ADR-0030 D2 收敛载体），输出 WarningEvent 或 None。

        输入 `DecisionInput` 内含 `trigger_events`（本周期感知触发）+ `decision_context`
        + 可选记忆/推理/既往决策字段；本抽象方法的**默认实现契约**只读取
        `trigger_events` 与 `decision_context`（Slice B 零行为变化，记忆字段的语义消费
        留待 ADR-0030 Slice C）。

        返回 None 的典型情况：
        - `trigger_events` 为空
        - 全部是 visit_normal 且无 is_odd_hour 叠加（普通访问，不警告）
        """
        raise NotImplementedError


# ============================================================================
# 路由表配置（RuleBasedDecisionPolicy 用）
# ============================================================================

# 默认事件类型 → (risk_level, recommended_action, human_reason) 路由表
# 注意：visit_normal 单独（无 is_odd_hour 叠加）→ 抑制（不警告），仅 is_odd_hour=true 时
# 经 `decide()` 过滤后进入 candidates 才会查表得 LOW
DEFAULT_ROUTING_TABLE: dict[str, tuple[str, str, str]] = {
    "high_risk_approach": ("HIGH", "ESCALATE_COMMUNITY", "多风险规则同时命中"),
    "abnormal_dwell": ("LOW", "NOTIFY_FAMILY", "异常停留"),
    "repeat_visit": ("LOW", "NOTIFY_FAMILY", "重复访问"),
    "visit_pending_verify": ("LOW", "MONITOR", "未在白名单"),
    "visit_normal": ("LOW", "MONITOR", "异常时段访问"),
}

# 风险等级优先级（数字越大越严重，max 取最严重的）
LEVEL_PRIORITY: dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


# ============================================================================
# RuleBasedDecisionPolicy：MVP 规则版决策策略
# ============================================================================


class RuleBasedDecisionPolicy(DecisionPolicy):
    """MVP 决策策略：按 `PerceptionEvent.event_type` 优先级路由。

    决策规则（ADR-0010 Decision 1-3）：
    - **high_risk_approach** → HIGH（最严重，CompositeRule 已聚合多规则）
    - **abnormal_dwell** alone → LOW（异常停留需通知家属核实，单一事件不算紧急）
    - **repeat_visit** alone → LOW（同上）
    - **visit_pending_verify** alone → LOW（仅记录监控，不通知）
    - **visit_normal** + `is_odd_hour=true` → LOW（异常时段叠加，作为弱信号）
    - **visit_normal** alone（无叠加）→ **抑制**（不警告，避免噪音）

    多事件组合：取 **max risk level**（Owner："HIGH + LOW = HIGH"，max wins）。
    `reason_summary` 合并去重，保留触发事件的可读原因。
    `perception_score` 取 max。
    `recommended_action` 按最终 risk_level 查表（HIGH→ESCALATE_COMMUNITY 等）。

    > 决策表可定制（`routing_table` 注入），便于不同家庭 / 不同地区风险偏好。
    """

    name = "RuleBasedDecisionPolicy"

    def __init__(
        self,
        *,
        routing_table: dict[str, tuple[str, str, str]] | None = None,
        memory_aware: bool = False,
    ):
        """``memory_aware``：G0-3 历史感知开关（golden opt-in，默认 False 零行为变化）。

        - False（默认）：纯感知决策（现有 6 场景逐字不变，policy fingerprint 稳定）；
        - True：决策参考 ``reasoning_input.historical_context``——同 visitor 历史
          episodes >= 2（跨日模式重复确认）→ 升级（LOW→MEDIUM、MONITOR→NOTIFY_FAMILY）
          + reason_summary 显式引用历史 record_id。
        - 不影响 ``compute_policy_fingerprint(routing_table)``（D5 只 hash 配置表）。
        """
        super().__init__()  # 初始化基类 span 状态（Slice C，零行为变化）
        """路由表：per-event (level, action, reason) 三元组。

        Owner P0-8 决策哲学：per-event action 是源（不同 event 触发不同 action），
        组合时取 max level + chosen event 的 action（max wins for both level & action）。

        如需"按 level 强制覆盖"（例如家庭不愿直接升级社区，希望先联系家属），
        可在 routing_table 中把同 level 多个 event 映射为同一 action。
        """
        self.routing_table = dict(routing_table) if routing_table else dict(DEFAULT_ROUTING_TABLE)
        self.memory_aware = memory_aware
        # 路由表合法性校验
        for event_type, (level, action, _reason) in self.routing_table.items():
            if level not in RISK_LEVELS:
                raise ValueError(
                    f"routing_table[{event_type!r}] 的 level 必须是 {RISK_LEVELS} 之一，"
                    f"收到 {level!r}"
                )
            if action not in RECOMMENDED_ACTIONS:
                raise ValueError(
                    f"routing_table[{event_type!r}] 的 action 必须是 {RECOMMENDED_ACTIONS} 之一，"
                    f"收到 {action!r}"
                )

    def decide(self, input: DecisionInput) -> WarningEvent | None:
        # Slice B：入参收敛为单 `DecisionInput`。路由逻辑与迁移前逐字一致，仅在此解包；
        # 不读 memory / reasoning / prior_warning 字段 → 内存缺席时退化为纯感知决策
        # （满足 ADR-0030 D2「Memory 可缺席」）。
        # Slice C：惰性 import 规避 analysis 内循环（decision_trace → decision_policy）。
        # 运行期两模块均已加载，惰性导入安全（与 decision_contract 对 memory 契约同范式）。
        from .decision_trace import SuppressReason, build_rationale

        perception_events = input.trigger_events
        ctx = input.decision_context
        if not perception_events:
            self._emit_suppress(SuppressReason.NO_TRIGGER_EVENTS, ())
            return None

        # 1) 过滤 visit_normal：单独 visit_normal → 抑制；is_odd_hour 叠加 → LOW
        significant: list[PerceptionEvent] = []
        odd_hour_events: list[PerceptionEvent] = []
        for ev in perception_events:
            if ev.event_type == "visit_normal":
                if ev.is_odd_hour:
                    odd_hour_events.append(ev)
                # 普通 visit_normal 无叠加 → 跳过
            else:
                significant.append(ev)

        candidates = significant + odd_hour_events
        if not candidates:
            # 全部是普通访问，无警告（Slice C：写抑制 partial，漏报首次可观测）
            self._emit_suppress(SuppressReason.ALL_SUPPRESSED_NORMAL, ())
            return None

        # 2) 取最高优先级的 event_type（max risk level wins）
        chosen = max(candidates, key=lambda ev: self._event_priority(ev))

        # 3) 查表得 risk_level
        decision = self.routing_table.get(chosen.event_type)
        if decision is None:
            # 路由表里没这个 event_type（理论上不该发生 —— PerceptionEvent 已枚举）
            # Slice C：写抑制 partial，considered_candidates 反映「在场但无路由」的候选
            considered = build_rationale(input.trigger_events, self.routing_table).considered_candidates
            self._emit_suppress(SuppressReason.UNROUTABLE_EVENT_TYPE, considered)
            return None
        chosen_level, _chosen_action, _chosen_reason = decision

        # 4) 聚合 risk_level：取所有 candidates 的 max level
        final_level = self._aggregate_level(candidates, chosen_level)

        # 5) final action：取 chosen event 的 per-event action（max wins for action too）
        #    这是 Owner "组合规则 max wins" 哲学 —— HIGH + LOW 的 action = HIGH 的 action
        #    如家庭需按 level 强制覆盖，把同 level 多 event 在 routing_table 中映射为同 action 即可
        final_action = self.routing_table[chosen.event_type][1]

        # 6) reason_summary 合并去重
        reasons = self._merge_reasons(candidates)

        # 7) perception_score = max
        agg_score = max(ev.score for ev in candidates)

        # 8) 构造 trigger_events 列表（仅 dict 摘要，不存对象引用）
        trigger_dicts = [
            {
                "event_id": f"{ev.visitor_id}:{ev.event_type}",
                "event_type": ev.event_type,
                "score": round(ev.score, 4),
                "timestamp": ev.timestamp,
            }
            for ev in candidates
        ]

        # 9) meta 必含 policy 字段（可审计）
        meta = {
            "policy": self.name,
            "decided_at": ctx.now.isoformat(),
            "trigger_event_types": sorted({ev.event_type for ev in candidates}),
            "routing_table_version": "v1",
        }

        # ADR-0040 D6：risk_signals 一等输入的最小消费（防「加了字段没人消费」的
        # 静默旁路）。边界（本 ADR 与 ADR-0042 的职责切分）：
        # - 仅作为**已有视觉候选决策**的附加证据面进入可观测输出（meta 摘要 +
        #   RAISED 人话原因）；不参与 level / action / perception_score 判定——
        #   Evidence Strength → Action 的 modality-aware routing 归 ADR-0042；
        # - CLEARED 是解除消息，不产生原因，仅计入 meta 计数；
        # - 纯信号无视觉触发仍走上方早退分支返回 None（语义同现状）。
        if input.risk_signals:
            raised = [
                s for s in input.risk_signals if s.transition is SignalTransition.RAISED
            ]
            for s in raised:
                reason = f"实时风险信号: {s.category.value}({s.source.value})"
                if reason not in reasons:
                    reasons.append(reason)
            meta["risk_signals"] = {
                "count": len(input.risk_signals),
                "raised": len(raised),
                "sources": sorted({s.source.value for s in input.risk_signals}),
                "signal_ids": [s.signal_id for s in input.risk_signals],
            }

        # G0-3：历史感知升级（golden opt-in；默认 False 逐字不变）。
        # （ADR-0040 D6：risk_signals 消费在其之前完成 —— 见下方最小消费块）
        final_level, final_action, reasons, meta = self._apply_memory_aware(
            input, final_level, final_action, reasons, meta
        )

        return WarningEvent(
            elder_id=ctx.elder_id,
            # device_id = 最早 timestamp 的候选事件设备（C3 规范化后确定，与传入顺序无关）
            device_id=candidates[0].device_id,
            risk_level=final_level,
            recommended_action=final_action,
            trigger_events=trigger_dicts,
            reason_summary=reasons,
            perception_score=agg_score,
            meta=meta,
            # 与流水线其余组件一致：决策时刻取 ctx.now（注入的 now_provider / 模拟时钟），
            # 而非墙钟。否则 Demo/测试场景下 WarningEvent.created_at 落在墙钟时间线，
            # 与 VisitorEvent 的模拟时间线错位，导致下游按时间窗关联（如 ADR-0024
            # Episode Builder 的 [enter, leave+60s] 窗口）失败。
            created_at=ctx.now,
        )

    # --------------------------------------------------------------------
    # G0-3：历史感知升级（MemoryRefs 引用可证）
    # --------------------------------------------------------------------

    def _apply_memory_aware(
        self,
        input: DecisionInput,
        final_level: str,
        final_action: str,
        reasons: list[str],
        meta: dict[str, object],
    ) -> tuple[str, str, list[str], dict[str, object]]:
        """G0-3 历史感知升级（golden opt-in）：同 visitor 历史 episodes >= 2 → 升级。

        - 仅当 ``self.memory_aware`` 且 ``reasoning_input.historical_context`` 非空时生效；
        - 历史重复（>= 2 条同 visitor 历史 episode，含 prior_episodes 预置）确认
          "跨日模式重复" → ``LOW`` 升 ``MEDIUM``、``MONITOR`` 升 ``NOTIFY_FAMILY``；
        - ``reason_summary`` 显式引用历史 record_id（人话："历史 N 次类似访问（ids）"），
          与 ``trace.historical_record_ids`` 同源（"决策引用了历史 Episode"可证）；
        - 不参与 ``compute_policy_fingerprint(routing_table)``（D5 只 hash 配置表），
          现有场景 memory_aware=False 决策逐字不变。
        """
        if not self.memory_aware:
            return final_level, final_action, reasons, meta
        reasoning = getattr(input, "reasoning_input", None)
        if reasoning is None:
            return final_level, final_action, reasons, meta
        records = tuple(getattr(reasoning, "historical_context", ()) or ())
        if len(records) < 2:
            return final_level, final_action, reasons, meta
        ids = tuple(ep.record_id for ep in records)
        reasons = list(reasons) + [
            f"历史 {len(records)} 次类似访问（{', '.join(ids)}）"
        ]
        level = (
            final_level
            if LEVEL_PRIORITY[final_level] >= LEVEL_PRIORITY["MEDIUM"]
            else "MEDIUM"
        )
        action = "NOTIFY_FAMILY" if final_action == "MONITOR" else final_action
        meta = dict(meta)
        meta["memory_aware"] = True
        meta["historical_record_ids"] = list(ids)
        return level, action, reasons, meta

    # --------------------------------------------------------------------
    # Slice C（ADR-0031 D6.1）：抑制 partial 写入
    # --------------------------------------------------------------------

    def _emit_suppress(
        self,
        reason: SuppressReason,
        considered_candidates: tuple[CandidateRecord, ...],
    ) -> None:
        """在三条 `return None` 前写入抑制 partial（D6.1 COLLECTING 阶段）。

        仅当引擎已绑定 span（recorder 注入）时生效；`self._trace_span is None` 时零操作，
        保证 recorder=None 时决策逐字不变（T2）。写入异常被隔离（T3）：策略只写不读，
        写 partial 抛错绝不反向破坏 `decide` 返回值（失败隔离，承 ADR-0028 D4）。
        """
        span = getattr(self, "_trace_span", None)
        if span is None:
            return
        try:
            span.suppress_reason = reason
            span.considered_candidates = considered_candidates
        except Exception:  # 失败隔离（T3）：写 partial 故障不得破坏决策
            log.exception("decision.trace_span_write_failed", reason=reason.value)

    # --------------------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------------------

    def _event_priority(self, ev: PerceptionEvent) -> int:
        """单个 event 的优先级（用于 max 选择）。"""
        if ev.event_type in self.routing_table:
            level = self.routing_table[ev.event_type][0]
            return LEVEL_PRIORITY.get(level, 0)
        return 0

    def _aggregate_level(
        self,
        candidates: list[PerceptionEvent],
        chosen_level: str,
    ) -> str:
        """聚合 risk_level：取所有 candidates 的 max level。

        Owner："HIGH + LOW = HIGH"，max wins（不升级，只取最严重的）。
        """
        max_level = chosen_level
        max_priority = LEVEL_PRIORITY.get(chosen_level, 0)
        for ev in candidates:
            if ev.event_type in self.routing_table:
                lvl = self.routing_table[ev.event_type][0]
            else:
                continue
            p = LEVEL_PRIORITY.get(lvl, 0)
            if p > max_priority:
                max_priority = p
                max_level = lvl
        return max_level

    def _merge_reasons(self, candidates: list[PerceptionEvent]) -> list[str]:
        """合并 reason_summary：按 candidates 顺序去重。"""
        seen: set = set()
        reasons: list[str] = []
        for ev in candidates:
            if ev.event_type in self.routing_table:
                reason = self.routing_table[ev.event_type][2]
            else:
                continue
            if reason and reason not in seen:
                reasons.append(reason)
                seen.add(reason)
        return reasons

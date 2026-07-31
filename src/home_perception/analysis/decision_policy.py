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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .perception import PerceptionEvent
from .warning import (
    RECOMMENDED_ACTIONS,
    RISK_LEVELS,
    WarningEvent,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    extra: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# DecisionPolicy 抽象基类
# ============================================================================

class DecisionPolicy(ABC):
    """决策策略抽象基类。所有具体策略必须实现 `decide(perception_events, ctx)`。

    设计原则（ADR-0010 Decision 4）：
    - 输入：`List[PerceptionEvent]`（一次评估周期内的所有事件）
    - 输出：`Optional[WarningEvent]`（None 表示无需发警告，例如全部是普通访问）
    - **不**复算 Feature（Feature 是 P0-7a 数值信号层）
    - **不**重新组合 Rule（Rule 组合已在 P0-7b CompositeRule 完成，输出 high_risk_approach）
    - **不**直接执行（MQTT / 通知 / 升级 留给 P0-9）
    """

    name: str = "DecisionPolicy"

    @abstractmethod
    def decide(
        self,
        perception_events: List[PerceptionEvent],
        ctx: DecisionContext,
    ) -> Optional[WarningEvent]:
        """消费 PerceptionEvent 列表，输出 WarningEvent 或 None。

        返回 None 的典型情况：
        - 空列表
        - 全部是 visit_normal 且无 is_odd_hour 叠加（普通访问，不警告）
        """
        raise NotImplementedError


# ============================================================================
# 路由表配置（RuleBasedDecisionPolicy 用）
# ============================================================================

# 默认事件类型 → (risk_level, recommended_action, human_reason) 路由表
# 注意：visit_normal 单独（无 is_odd_hour 叠加）→ 抑制（不警告），仅 is_odd_hour=true 时
# 经 `decide()` 过滤后进入 candidates 才会查表得 LOW
DEFAULT_ROUTING_TABLE: Dict[str, Tuple[str, str, str]] = {
    "high_risk_approach":    ("HIGH",   "ESCALATE_COMMUNITY", "多风险规则同时命中"),
    "abnormal_dwell":        ("LOW",    "NOTIFY_FAMILY",      "异常停留"),
    "repeat_visit":          ("LOW",    "NOTIFY_FAMILY",      "重复访问"),
    "visit_pending_verify":  ("LOW",    "MONITOR",            "未在白名单"),
    "visit_normal":          ("LOW",    "MONITOR",            "异常时段访问"),
}

# 风险等级优先级（数字越大越严重，max 取最严重的）
LEVEL_PRIORITY: Dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


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
        routing_table: Optional[Dict[str, Tuple[str, str, str]]] = None,
    ):
        """路由表：per-event (level, action, reason) 三元组。

        Owner P0-8 决策哲学：per-event action 是源（不同 event 触发不同 action），
        组合时取 max level + chosen event 的 action（max wins for both level & action）。

        如需"按 level 强制覆盖"（例如家庭不愿直接升级社区，希望先联系家属），
        可在 routing_table 中把同 level 多个 event 映射为同一 action。
        """
        self.routing_table = dict(routing_table) if routing_table else dict(DEFAULT_ROUTING_TABLE)
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

    def decide(
        self,
        perception_events: List[PerceptionEvent],
        ctx: DecisionContext,
    ) -> Optional[WarningEvent]:
        if not perception_events:
            return None

        # 1) 过滤 visit_normal：单独 visit_normal → 抑制；is_odd_hour 叠加 → LOW
        significant: List[PerceptionEvent] = []
        odd_hour_events: List[PerceptionEvent] = []
        for ev in perception_events:
            if ev.event_type == "visit_normal":
                if ev.is_odd_hour:
                    odd_hour_events.append(ev)
                # 普通 visit_normal 无叠加 → 跳过
            else:
                significant.append(ev)

        candidates = significant + odd_hour_events
        if not candidates:
            return None  # 全部是普通访问，无警告

        # 2) 取最高优先级的 event_type（max risk level wins）
        chosen = max(candidates, key=lambda ev: self._event_priority(ev))

        # 3) 查表得 risk_level
        decision = self.routing_table.get(chosen.event_type)
        if decision is None:
            # 路由表里没这个 event_type（理论上不该发生 —— PerceptionEvent 已枚举）
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

        return WarningEvent(
            elder_id=ctx.elder_id,
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
        candidates: List[PerceptionEvent],
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

    def _merge_reasons(self, candidates: List[PerceptionEvent]) -> List[str]:
        """合并 reason_summary：按 candidates 顺序去重。"""
        seen: set = set()
        reasons: List[str] = []
        for ev in candidates:
            if ev.event_type in self.routing_table:
                reason = self.routing_table[ev.event_type][2]
            else:
                continue
            if reason and reason not in seen:
                reasons.append(reason)
                seen.add(reason)
        return reasons

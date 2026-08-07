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

from ..common.logging import get_logger
from ..common.timeutil import now_dt
from .decision_contract import DecisionInput
from .decision_policy import DecisionContext, DecisionPolicy, RuleBasedDecisionPolicy
from .perception import PerceptionEvent
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
    ):
        if not elder_id or not str(elder_id).strip():
            raise ValueError("elder_id 不能为空（WarningEvent 必填字段）")
        self.elder_id = elder_id
        self.policy = policy or RuleBasedDecisionPolicy()
        self._now = now_provider or now_dt

    def evaluate(self, perception_events: list[PerceptionEvent]) -> WarningEvent | None:
        """单次决策：消费 PerceptionEvent 列表，输出 WarningEvent 或 None。

        返回 None 的典型情况：
        - 空列表
        - 全部是 visit_normal 且无 is_odd_hour 叠加（普通访问）
        """
        ctx = DecisionContext(elder_id=self.elder_id, now=self._now())
        # Slice B：装配 DecisionInput（单入参契约）。本期记忆/推理/既往字段尚未接线，
        # 一律显式 None —— 满足 ADR-0030 D2「Memory 可缺席」原则，零行为变化。
        input = DecisionInput(
            trigger_events=tuple(perception_events),
            decision_context=ctx,
            reasoning_input=None,
            reasoning_result=None,
            prior_warning=None,
        )
        warning = self.policy.decide(input)
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
        return warning

"""门前规则：把检测结果转为 5 类标签事件。

Rule 是稳定接口；具体规则在 Phase 1 逐步实现：
  - OddHourRule       ✅ 已实现示例（异常时段有人活动）
  - DwellRule         ⏳ 停留超阈值 -> abnormal_dwell
  - RepeatVisitRule   ⏳ 短时多次出现 -> repeat_visit
  - PendingVerifyRule ⏳ 非白名单陌生访客 -> visit_pending_verify
  - HighRiskApproach  ⏳ 尾随/反复靠近 -> high_risk_approach

规则必须是**确定性**的（同输入同输出），便于 test_rules.py 锁定契约。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.event import EventType, PerceptionEvent


@dataclass
class RuleContext:
    device_id: str
    location: str | None
    timestamp: float
    detections: list  # list[Detection]
    track_id: int | None = None


class Rule(ABC):
    name: str = "rule"

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> PerceptionEvent | None:
        ...


class OddHourRule(Rule):
    """异常时段（夜间/独处）有人出现在门前 -> 标记为待核验来访（叠加 is_odd_hour）。"""

    name = "OddHourRule"

    def __init__(self, start: int = 23, end: int = 6):
        self.start = start
        self.end = end

    def is_odd(self, hour: int) -> bool:
        return hour >= self.start or hour < self.end

    def evaluate(self, ctx: RuleContext) -> PerceptionEvent | None:
        if not ctx.detections:
            return None
        hour = time.localtime(ctx.timestamp).tm_hour
        if self.is_odd(hour):
            return PerceptionEvent(
                device_id=ctx.device_id,
                event_type=EventType.VISIT_PENDING_VERIFY,
                score=0.5,
                timestamp=ctx.timestamp,
                track_id=ctx.track_id,
                location=ctx.location,
                is_odd_hour=True,
                meta={"rule": self.name},
            )
        return None

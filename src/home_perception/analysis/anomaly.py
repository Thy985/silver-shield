"""事件聚合与防刷（CooldownGate）。

同源事件在冷却时间内只放行一次，避免告警风暴（风险 T6）。
后续可在此接入多事件融合为更高阶 RiskTwin 因子（增强版）。
"""
from __future__ import annotations

from collections import defaultdict


class CooldownGate:
    def __init__(self, cooldown_s: int = 60):
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = defaultdict(float)

    def allow(self, key: str, now: float) -> bool:
        last = self._last.get(key, 0.0)
        if (now - last) >= self.cooldown_s:
            self._last[key] = now
            return True
        return False

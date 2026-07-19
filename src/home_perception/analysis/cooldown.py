"""CooldownGate 状态机（P0-7b · 风险语义层）。

> **P0-7b = 风险语义层。** CooldownGate 防止同 (visitor_id, rule_name) 短时重复触发。
> 30fps 摄像头下，单次停留 480s 可产生 14400 帧，每帧可能触发同 Rule → 数百条重复 PerceptionEvent。
> CooldownGate 状态机是该工程痛点的核心防御。

**状态机**（per (visitor_id, rule_name)）：

```
INACTIVE
    ↓ (first trigger)
ACTIVE        ← trigger now, emit PerceptionEvent
    ↓ (within cooldown_seconds of last trigger)
COOLDOWN      ← suppress trigger (no PerceptionEvent)
    ↓ (after cooldown_seconds elapsed, visitor still active)
ACTIVE        ← next trigger allowed, emit PerceptionEvent
    ↓ (visitor gone for > reset_gap_seconds)
INACTIVE      ← reset
```

参数：
- `cooldown_seconds`：默认 600s（10 分钟）；同 rule + 同 visitor 在此秒数内不重复触发
- `reset_gap_seconds`：默认 1800s（30 分钟）；超过该秒数无该 visitor 任何 Rule 触发 → 状态机重置

**CompositeRule 不走 CooldownGate**（它消费其他 Rule 的"已冷却"结果，自身不产生重复触发）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional
from uuid import UUID


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CooldownState(str, Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    COOLDOWN = "cooldown"


@dataclass
class _CooldownEntry:
    """单个 (visitor_id, rule_name) 的冷却状态。"""
    state: CooldownState = CooldownState.INACTIVE
    last_trigger_at: Optional[datetime] = None  # 最近一次触发 ACTIVE 的时间
    last_seen_at: Optional[datetime] = None     # 最近一次任何触发（包含 COOLDOWN 抑制）


class CooldownGate:
    """Cooldown 状态机编排器。

    用法：
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        for risk_feature, rule_results in evaluation_loop:
            for result in rule_results:
                if not result.matched:
                    continue
                allowed = gate.try_trigger(
                    visitor_id=risk_feature.visitor_id,
                    rule_name=result.rule_name,
                    now=ctx.now,
                )
                if allowed:
                    emit_perception_event(result)
    """

    DEFAULT_COOLDOWN_S: float = 600.0    # 10 分钟
    DEFAULT_RESET_GAP_S: float = 1800.0  # 30 分钟

    def __init__(
        self,
        cooldown_seconds: float = DEFAULT_COOLDOWN_S,
        reset_gap_seconds: float = DEFAULT_RESET_GAP_S,
    ):
        if cooldown_seconds <= 0:
            raise ValueError(f"cooldown_seconds 必须 > 0，收到 {cooldown_seconds}")
        if reset_gap_seconds <= 0:
            raise ValueError(f"reset_gap_seconds 必须 > 0，收到 {reset_gap_seconds}")
        self.cooldown_seconds = cooldown_seconds
        self.reset_gap_seconds = reset_gap_seconds
        # (visitor_id, rule_name) → _CooldownEntry
        self._entries: Dict[tuple, _CooldownEntry] = {}

    def try_trigger(self, visitor_id: UUID, rule_name: str, now: datetime | None = None) -> bool:
        """尝试触发 (visitor_id, rule_name)；允许触发返回 True（应发 PerceptionEvent），抑制返回 False。"""
        now = now or _utc_now()
        key = (visitor_id, rule_name)
        entry = self._entries.get(key)
        if entry is None:
            entry = _CooldownEntry()
            self._entries[key] = entry

        # 1) reset_gap 判定：上次见到距今 > reset_gap → 重置为 INACTIVE
        if entry.last_seen_at is not None:
            gap = (now - entry.last_seen_at).total_seconds()
            if gap >= self.reset_gap_seconds:
                entry.state = CooldownState.INACTIVE
                entry.last_trigger_at = None
        entry.last_seen_at = now

        # 2) 状态机：INACTIVE / COOLDOWN → ACTIVE（允许）；ACTIVE → COOLDOWN（抑制）
        if entry.state in (CooldownState.INACTIVE, CooldownState.COOLDOWN):
            entry.state = CooldownState.ACTIVE
            entry.last_trigger_at = now
            return True
        # entry.state == ACTIVE
        if entry.last_trigger_at is not None:
            since = (now - entry.last_trigger_at).total_seconds()
            if since >= self.cooldown_seconds:
                # 冷却期已过，再次允许
                entry.state = CooldownState.ACTIVE
                entry.last_trigger_at = now
                return True
        # 仍在冷却期
        entry.state = CooldownState.COOLDOWN
        return False

    def state(self, visitor_id: UUID, rule_name: str) -> CooldownState:
        """查询某 (visitor_id, rule_name) 的当前状态（用于测试 / 调试）。"""
        entry = self._entries.get((visitor_id, rule_name))
        return entry.state if entry else CooldownState.INACTIVE

    def reset(self) -> None:
        """清空所有冷却状态（视频源切换 / 多会话）。"""
        self._entries.clear()

    def size(self) -> int:
        """当前跟踪的 (visitor_id, rule_name) 数量（用于测试 / 监控）。"""
        return len(self._entries)

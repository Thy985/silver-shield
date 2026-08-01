"""Short-term Memory Policy 默认实现（ADR-0024 §3.1.1 / §3.2 · Slice 2 · Stage F）。

> 把 BehaviorState（StateSnapshot）+ RiskSignal（TransitionEvent）投影为 ShortTermRecord。
> 本模块是 **MemoryPolicy.transform_short_term** 的具体实现，不实现 project_episode /
> aggregate_semantic（v1 占位 return None，归 Slice 4 / Stage G）。

**写入触发规则**（DESIGN §8.4）：

| 触发 | transition | current_record | 行为 |
| --- | --- | --- | --- |
| RAISED | 非 None (RAISED) | 可有可无 | 新建/覆写 record，phase=active_risk |
| CLEARED | 非 None (CLEARED) | 可有可无 | 覆写 record，phase=none，继承 raised_at |
| 周期快照 | None | 非 None | 覆写 last_seen_at，继承 phase/raised_* |
| 无跃迁不写 | None | None | return None |

**幂等键**：`record_id = f"st-{visitor_instance_id}"`（同一 visitor 一条工作记忆）。

**纯函数语义**：不持有内部状态，所有"当前 record"信息通过 `current_record` 参数传入。
"""
from __future__ import annotations

from typing import List, Optional

from ..analysis.behavior_state import BehaviorState
from ..analysis.risk_signal import RiskSignal, SignalTransition
from .policy import MemoryPolicy
from .records import ShortTermRecord


class DefaultShortTermPolicy(MemoryPolicy):
    """Short-term Memory 投影默认实现。

    纯函数语义：不持有可变状态。同一输入必产出同一输出（I1 幂等性前置）。
    """

    def transform_short_term(
        self,
        state_snapshot: Optional[BehaviorState],
        transition: Optional[RiskSignal],
        current_record: Optional[ShortTermRecord] = None,
    ) -> Optional[ShortTermRecord]:
        """把 BehaviorState + RiskSignal 投影为 ShortTermRecord。

        返回 None 的场景（DESIGN §8.4 "无跃迁时不写"）：
        - transition 与 current_record 同时为 None（无写入触发）
        - visitor_instance_id 无法解析（缺失）
        - state_snapshot 与 current_record 同时为 None（无 first_seen / last_seen_at 来源）
        """
        # 1) 无写入触发：无跃迁且无当前 record
        if transition is None and current_record is None:
            return None

        # 2) 解析 visitor_instance_id（优先 transition，其次 state_snapshot，最后 current_record）
        visitor_id = self._resolve_visitor_id(transition, state_snapshot, current_record)
        if not visitor_id:
            return None

        # 3) 解析时间字段（优先 state_snapshot，其次 current_record）
        first_seen, last_seen_at = self._resolve_times(state_snapshot, current_record)
        if first_seen is None or last_seen_at is None:
            return None

        # 4) 按 transition 类型分支
        if transition is not None:
            return self._from_transition(
                visitor_id, first_seen, last_seen_at, transition, current_record
            )
        # transition is None, current_record is not None → 周期快照
        return self._from_snapshot(
            visitor_id, first_seen, last_seen_at, current_record
        )

    # ------------------------------------------------------------------
    # 分支实现
    # ------------------------------------------------------------------

    @staticmethod
    def _from_transition(
        visitor_id: str,
        first_seen,
        last_seen_at,
        transition: RiskSignal,
        current_record: Optional[ShortTermRecord],
    ) -> ShortTermRecord:
        """状态转移触发：RAISED / CLEARED。"""
        source_event_ids: List[str] = [transition.signal_id]

        if transition.transition is SignalTransition.RAISED:
            # NONE → ACTIVE_RISK：新建/覆写 record
            phase = "active_risk"
            raised_signal_id = transition.signal_id
            raised_at = transition.created_at
        else:
            # ACTIVE_RISK → NONE：CLEARED
            phase = "none"
            # raised_signal_id 从 CLEARED.paired_signal_id 回填（如有），
            # 否则从 current_record 继承（如无则为 None）
            raised_signal_id = (
                transition.paired_signal_id
                or (current_record.raised_signal_id if current_record else None)
            )
            # raised_at 从 current_record 继承（transition 不携带此信息）
            raised_at = current_record.raised_at if current_record else None

        return ShortTermRecord(
            record_id=f"st-{visitor_id}",
            visitor_instance_id=visitor_id,
            phase=phase,
            first_seen=first_seen,
            last_seen_at=last_seen_at,
            source_event_ids=source_event_ids,
            raised_signal_id=raised_signal_id,
            raised_at=raised_at,
        )

    @staticmethod
    def _from_snapshot(
        visitor_id: str,
        first_seen,
        last_seen_at,
        current_record: ShortTermRecord,
    ) -> ShortTermRecord:
        """周期快照触发：覆写 last_seen_at，继承 phase / raised_* / source_event_ids。"""
        return ShortTermRecord(
            record_id=f"st-{visitor_id}",
            visitor_instance_id=visitor_id,
            phase=current_record.phase,
            first_seen=first_seen,
            last_seen_at=last_seen_at,
            source_event_ids=list(current_record.source_event_ids),
            raised_signal_id=current_record.raised_signal_id,
            raised_at=current_record.raised_at,
        )

    # ------------------------------------------------------------------
    # 字段解析工具
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_visitor_id(
        transition: Optional[RiskSignal],
        state_snapshot: Optional[BehaviorState],
        current_record: Optional[ShortTermRecord],
    ) -> Optional[str]:
        """解析 visitor_instance_id（优先级：transition > state_snapshot > current_record）。"""
        if transition is not None and transition.visitor_instance_id:
            return transition.visitor_instance_id
        if state_snapshot is not None and state_snapshot.visitor_instance_id:
            return state_snapshot.visitor_instance_id
        if current_record is not None and current_record.visitor_instance_id:
            return current_record.visitor_instance_id
        return None

    @staticmethod
    def _resolve_times(
        state_snapshot: Optional[BehaviorState],
        current_record: Optional[ShortTermRecord],
    ):
        """解析 first_seen / last_seen_at（优先 state_snapshot，回退 current_record）。"""
        first_seen = None
        last_seen_at = None
        if state_snapshot is not None:
            first_seen = state_snapshot.first_seen
            last_seen_at = state_snapshot.last_seen
        if first_seen is None and current_record is not None:
            first_seen = current_record.first_seen
        if last_seen_at is None and current_record is not None:
            last_seen_at = current_record.last_seen_at
        return first_seen, last_seen_at

    # ------------------------------------------------------------------
    # v1 占位：Slice 4 / Stage G 实现
    # ------------------------------------------------------------------

    def project_episode(self, visitor_event, warnings, actions):
        """v1 占位（Slice 4 实现 DefaultEpisodeBuilder）。"""
        return None

    def aggregate_semantic(self, episodes, dimension, period_key):
        """v1 占位（Stage G/H 实现 Semantic 聚合）。"""
        return None

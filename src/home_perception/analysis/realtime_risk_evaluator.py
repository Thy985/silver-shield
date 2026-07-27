"""实时风险评估器（RealTimeRiskEvaluator）— ADR-0021 Signal Layer 状态机（Migration Stage C）。

> **ADR-0021 = 实时风险状态流与信号生成层。** 本模块实现「每主体一台状态机」的实时评估器，
> 消费 `RealtimeContext`（BehaviorState + recent_behavior），产出 `RiskSignal`（RAISED / CLEARED）。
>
> **Stage C 边界（Shadow Mode）**：本模块只产 `RiskSignal`，**不接 DecisionPolicy、不产 Warning**。
> 信号经 `FrameResult.risk_signals` 供 Demo Dashboard 观察"进行中风险"（RAISED 亮卡 / CLEARED 熄卡）。
> 接决策是 Stage D 的职责（经 `signal_adapter` 翻译为 `PerceptionEvent` 汇入 DecisionEngine）。

**状态机（ADR-0021 §4）**：

```
visitor_instance_id 首次出现在 ctxs
        ↓
   创建 _TrackRiskState(phase=RiskPhase.NONE)
        ↓
   每帧 evaluate(ctxs, now)
        ↓ trigger（任一阈值达成 且 phase==NONE）
   emit RiskSignal(RAISED)；phase=ACTIVE_RISK
        ↓ recover（全部阈值回落 或 phase==LEFT）
   emit RiskSignal(CLEARED, paired_signal_id=<RAISED 的 signal_id>)；phase=NONE
        ↓ 主体离场且已 CLEARED
   从 _active 中 delete（防泄漏）
```

**硬性规则（工程方案 §4.2）**：
1. `phase==ACTIVE_RISK` 时不重复 emit RAISED（去抖第一层；跨 Warning 节流仍由 CooldownGate 负责）
2. 每个 RAISED 必须有配对 CLEARED（离场兜底保证）——契约测试断言成对性
3. 状态机字典只增不删会泄漏 → 离场 + CLEARED 后必须删除条目
4. 进程重启后状态机清零：不补发 CLEARED（丢失的 RAISED 由展示层 TTL 兜底）
5. **跳帧对称**：RAISED 与 CLEARED 只在评估帧发生（pipeline 层保证，本模块不感知跳帧）

**key 选型（防 track_id 串号）**：`_active` 键**必须用 `visitor_instance_id`**（会话级 UUID），
绝不用 `track_id`——后者会被 ByteTrack 回收重用，导致后继主体继承前人残留的 ACTIVE_RISK。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from ..common.logging import get_logger
from ..common.timeutil import require_utc
from .behavior_state import BehaviorPhase, BehaviorState, RealtimeContext
from .risk_signal import (
    RiskSignal,
    SignalCategory,
    SourceModality,
    SignalTransition,
    SubjectType,
)
from .rule_engine import ThresholdConfig

log = get_logger(__name__)


class RiskPhase(str, Enum):
    """风险状态机持续态（枚举化，勿与 BehaviorPhase 混淆：这是"风险机"态，非"访问生命周期"态）。"""

    NONE = "none"            # 未处于风险
    ACTIVE_RISK = "active_risk"  # 已 RAISED 未 CLEARED


@dataclass
class _TrackRiskState:
    """单主体风险状态机记录（私有，不出模块）。

    - `phase`：RiskPhase.NONE | RiskPhase.ACTIVE_RISK
    - `raised_signal_id`：本次 RAISED 的 signal_id；CLEARED 时回填到 RiskSignal.paired_signal_id
    - `raised_at`：RAISED 时刻（datetime UTC），用于日志/审计
    - `last_track_id`：最近一次见到的 track_id（仅用于日志，不参与判定）
    """

    phase: RiskPhase
    raised_signal_id: str
    raised_at: Optional[datetime]
    last_track_id: Optional[int] = None


class RealTimeRiskEvaluator:
    """每主体一台状态机的实时风险评估器（有状态，随 pipeline 存活）。

    用法（由 ``PerceptionPipeline.process_frame`` 在实时旁路块调用）::

        ctxs = [RealtimeContext(state, recent_behavior) for state, recent_behavior in ...]
        signals = evaluator.evaluate(ctxs, now)
        # signals: List[RiskSignal]，含 RAISED + CLEARED

    阈值复用 ``ThresholdConfig``（与 RuleEngine 同源，单一阈值来源，工程方案 §5.1）。
    """

    def __init__(
        self,
        thresholds: ThresholdConfig,
        now_provider: Optional[Any] = None,
    ) -> None:
        self._thresholds = thresholds
        self._now_provider = now_provider
        # key = visitor_instance_id（会话级 UUID，防 track_id 重用串号）
        self._active: Dict[str, _TrackRiskState] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        ctxs: List[RealtimeContext],
        now: datetime,
    ) -> List[RiskSignal]:
        """评估一批 RealtimeContext，产出 RiskSignal 列表（RAISED + CLEARED）。

        参数：
        - ``ctxs``：当前评估帧全部在场主体的实时上下文（每主体一条）
        - ``now``：当前时刻（datetime UTC，与 pipeline 同源 now_provider）

        返回：``List[RiskSignal]``，含本帧产出的全部 RAISED + CLEARED 信号。

        语义（工程方案 §4.2）：
        - 首次见到主体：triggered 则直接 RAISED，否则仅创建 NONE 条目
        - ACTIVE_RISK 主体：回落或离场则 CLEARED，否则持续不重复 RAISED
        - NONE 主体：triggered 则 RAISED
        - 本次未见到的 ACTIVE_RISK 主体：离场兜底 CLEARED + 删除条目
        - 本次未见到的 NONE 主体：直接删除条目（无信号产出）
        """
        require_utc(now, "now")

        signals: List[RiskSignal] = []
        seen_ids: Set[str] = set()

        for ctx in ctxs:
            state = ctx.current_state
            vid = state.visitor_instance_id
            seen_ids.add(vid)

            # 离场兜底：phase==LEFT 视为强制 CLEARED（即便阈值仍满足）
            is_left = state.phase is BehaviorPhase.LEFT
            triggered = self._is_triggered(ctx) and not is_left

            existing = self._active.get(vid)
            if existing is None:
                # 首次见到：创建条目
                if triggered:
                    signal = self._emit_raised(state, now)
                    signals.append(signal)
                    self._active[vid] = _TrackRiskState(
                        phase=RiskPhase.ACTIVE_RISK,
                        raised_signal_id=signal.signal_id,
                        raised_at=now,
                        last_track_id=state.track_id,
                    )
                else:
                    self._active[vid] = _TrackRiskState(
                        phase=RiskPhase.NONE,
                        raised_signal_id="",
                        raised_at=None,
                        last_track_id=state.track_id,
                    )
            else:
                # 已有条目
                existing.last_track_id = state.track_id
                if existing.phase is RiskPhase.ACTIVE_RISK:
                    # 已在风险中：回落或离场 → CLEARED
                    if is_left or not triggered:
                        cleared = self._emit_cleared(
                            vid, existing.raised_signal_id, state, now
                        )
                        signals.append(cleared)
                        if is_left:
                            # 离场：删除条目（防泄漏）
                            self._active.pop(vid, None)
                        else:
                            # 仅回落：回到 NONE（保留条目，主体仍在场）
                            existing.phase = RiskPhase.NONE
                            existing.raised_signal_id = ""
                            existing.raised_at = None
                    # 否则持续 ACTIVE_RISK，不重复 RAISED
                else:
                    # NONE 状态：triggered 则 RAISED
                    if triggered:
                        signal = self._emit_raised(state, now)
                        signals.append(signal)
                        existing.phase = RiskPhase.ACTIVE_RISK
                        existing.raised_signal_id = signal.signal_id
                        existing.raised_at = now

        # 离场兜底：本次评估帧未见到但 _active 中仍存在的主体
        missing_ids = set(self._active.keys()) - seen_ids
        for vid in missing_ids:
            existing = self._active.pop(vid, None)
            if existing is not None and existing.phase is RiskPhase.ACTIVE_RISK:
                # 主体消失但仍在 ACTIVE_RISK → 补发 CLEARED（无 state，用最小信息）
                cleared = self._emit_cleared_missing(vid, existing.raised_signal_id, now)
                signals.append(cleared)
            # NONE 状态的未见主体：直接删除（已 pop），无信号产出

        return signals

    @property
    def active_count(self) -> int:
        """当前 _active 字典条目数（含 NONE + ACTIVE_RISK；用于测试/监控）。"""
        return len(self._active)

    @property
    def active_risk_count(self) -> int:
        """当前 ACTIVE_RISK 状态的主体数（用于测试/监控）。"""
        return sum(1 for s in self._active.values() if s.phase is RiskPhase.ACTIVE_RISK)

    def reset(self) -> None:
        """清空全部状态（volatile 语义：模拟重启丢弃，不补发 CLEARED，工程方案 §4.3）。"""
        self._active.clear()

    # ------------------------------------------------------------------
    # 内部：触发判定
    # ------------------------------------------------------------------

    def _is_triggered(self, ctx: RealtimeContext) -> bool:
        """判定是否触发 RAISED（任一阈值达成即触发）。

        触发条件（与 RuleEngine 语义对齐，但只产信号不产 PerceptionEvent）：
        - dwell_seconds >= thresholds.long_duration_seconds，或
        - visits_in_window >= thresholds.repeat_visit_count，或
        - is_odd_hour（state.is_odd_hour 且当前小时在 odd_hour_set 内）
        """
        state = ctx.current_state
        recent = ctx.recent_behavior

        # 1) dwell 超阈
        if state.dwell_seconds >= self._thresholds.long_duration_seconds:
            return True

        # 2) visits_in_window 超阈
        visits = recent.get("visits_in_window", 0) if isinstance(recent, dict) else 0
        if isinstance(visits, (int, float)) and visits >= self._thresholds.repeat_visit_count:
            return True

        # 3) odd_hour（state.is_odd_hour 已由 BehaviorBuilder 按 now 计算）
        if state.is_odd_hour:
            return True

        return False

    # ------------------------------------------------------------------
    # 内部：信号构造
    # ------------------------------------------------------------------

    def _emit_raised(self, state: BehaviorState, now: datetime) -> RiskSignal:
        """构造 RAISED 信号（features 放触发证据）。"""
        features: Dict[str, Any] = {
            "dwell_seconds": round(state.dwell_seconds, 3),
            "visits_in_window": 0,
            "is_odd_hour": state.is_odd_hour,
            "thresholds": {
                "long_duration_seconds": self._thresholds.long_duration_seconds,
                "repeat_visit_count": self._thresholds.repeat_visit_count,
            },
        }
        signal = RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id=state.visitor_instance_id,
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.RAISED,
            features=features,
            paired_signal_id=None,
            track_id=state.track_id,
            visitor_instance_id=state.visitor_instance_id,
            severity_hint=None,
            created_at=now,
        )
        log.info(
            "evaluator.raised",
            signal_id=signal.signal_id,
            visitor_instance_id=state.visitor_instance_id,
            track_id=state.track_id,
            dwell_seconds=state.dwell_seconds,
        )
        return signal

    def _emit_cleared(
        self,
        vid: str,
        raised_signal_id: str,
        state: BehaviorState,
        now: datetime,
    ) -> RiskSignal:
        """构造 CLEARED 信号（有 state 时，features 放回落证据）。"""
        features: Dict[str, Any] = {
            "dwell_seconds": round(state.dwell_seconds, 3),
            "is_odd_hour": state.is_odd_hour,
        }
        signal = RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id=vid,
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.CLEARED,
            features=features,
            paired_signal_id=raised_signal_id,
            track_id=state.track_id,
            visitor_instance_id=vid,
            severity_hint=None,
            created_at=now,
        )
        log.info(
            "evaluator.cleared",
            signal_id=signal.signal_id,
            visitor_instance_id=vid,
            track_id=state.track_id,
            paired_signal_id=raised_signal_id,
        )
        return signal

    def _emit_cleared_missing(
        self,
        vid: str,
        raised_signal_id: str,
        now: datetime,
    ) -> RiskSignal:
        """构造 CLEARED 信号（离场兜底，主体已消失无 state，features 留最小信息）。"""
        signal = RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id=vid,
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.CLEARED,
            features={"reason": "subject_missing"},
            paired_signal_id=raised_signal_id,
            track_id=None,
            visitor_instance_id=vid,
            severity_hint=None,
            created_at=now,
        )
        log.info(
            "evaluator.cleared_missing",
            signal_id=signal.signal_id,
            visitor_instance_id=vid,
            paired_signal_id=raised_signal_id,
        )
        return signal

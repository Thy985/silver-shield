"""行为/异常规则分析（Perceive→Understand 的门前部分）。

输出《架构设计完善版》定义的 5 类门前标签；不直接下"诈骗人员"结论。
"""
from .behavior_state import (
    BehaviorPhase,
    BehaviorState,
    RealtimeContext,
    compute_is_odd_hour,
)
from .recent_behavior_store import RecentBehaviorStore
from .risk_signal import (
    FORBIDDEN_RISKSIGNAL_FIELDS,
    RISKSIGNAL_DICT_KEYS,
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)

__all__ = [
    "BehaviorPhase",
    "BehaviorState",
    "RealtimeContext",
    "compute_is_odd_hour",
    "RecentBehaviorStore",
    "RiskSignal",
    "SignalCategory",
    "SourceModality",
    "SignalTransition",
    "SubjectType",
    "RISKSIGNAL_DICT_KEYS",
    "FORBIDDEN_RISKSIGNAL_FIELDS",
]

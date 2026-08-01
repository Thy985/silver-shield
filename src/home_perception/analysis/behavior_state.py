"""实时行为状态（BehaviorState / RealtimeContext）— ADR-0021 State Layer 类型（Migration Stage A）。

> **ADR-0021 = 实时风险状态流。** 本模块只定义**状态层的数据类型**，不接入 pipeline、
> 不产信号（Stage A 边界：只加类型 + 契约测试）。`BehaviorBuilder` 在 Stage B 才消费本类型。

**关键语义（ADR-0021 §3.2）**：
- `BehaviorState` 是「实时感知域的工作状态(working state)」，回答"此刻门口正在发生什么"。
  它是 `state = f(Reality, Time)` 的**纯当前生命周期快照**，**不含跨访问统计**
  （`visits_in_window` 由 `RecentBehaviorStore` 维护，组合进 `RealtimeContext`）。
- `BehaviorState` 是 **volatile state（易变状态）**：默认属 Working Memory，不直接进 Long-term Memory。
- `RealtimeContext` 是 `RealTimeRiskEvaluator` 的真正输入 = `current_state`（纯态）＋ `recent_behavior`（跨访问统计）。

**时间表示统一约定（ADR-0021 §3.2 纠偏）**：
- 时刻一律 `datetime`（UTC），对齐真实 `NowProvider.__call__(self) -> datetime`（runtime/pipeline.py:65）
  与 ADR-0007；**禁止 float unix 戳表达时刻**。
- 时长一律 `float` 秒（如 `dwell_seconds = (last_seen - first_seen).total_seconds()`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict

from ..common.timeutil import require_utc


def compute_is_odd_hour(dt: datetime) -> bool:
    """按时刻判定是否"非寻常时段"（默认 22:00–次日 06:00）。

    纯函数，便于 Phase 1 `BehaviorBuilder` 与单元测试复用；展示层若需本地时区自行转换。
    """
    require_utc(dt, "dt")
    return dt.hour >= 22 or dt.hour < 6


class BehaviorPhase(str, Enum):
    """访问生命周期阶段（枚举化，杜绝裸字符串拼写漂移）。"""

    ONGOING = "ongoing"        # 在场进行中（Phase 1 主态）
    LEFT = "left"              # 已离场（触发 CLEARED 兜底，见 ADR-0021 §3.3.1）
    # —— 预留（Phase 1 不产出，接口先留）——
    APPROACHING = "approaching"  # 正在接近门口（未来 proximity_score 上升趋势）
    DEPARTING = "departing"      # 正在远离（未来轨迹判定，早于 LEFT）


BEHAVIOR_PHASE_VALUES: tuple = tuple(e.value for e in BehaviorPhase)


@dataclass
class BehaviorState:
    """实时行为状态（ADR-0021 State Layer，volatile working state）。

    字段（纯当前生命周期量，无跨访问统计）：
    - `track_id`：检测/追踪帧内 ID（会话级，可能复用，见 ADR-0006）
    - `visitor_instance_id`：复用 event_builder 分配的会话级 UUID（稳定主键，见 ADR-0023）
    - `phase`：生命周期阶段枚举（Phase 1 仅取 ONGOING / LEFT）
    - `first_seen` / `last_seen`：首次/最近出现时刻（datetime UTC）
    - `dwell_seconds`：当前生命周期内累计停留时长（float 秒）
    - `is_odd_hour`：f(now)，当前时刻是否非寻常时段
    - `proximity_score`：float ∈ [0,1] 接近度（Phase 1 恒 0.0，仅占位，不参与判定）
    - `schema_version`：内部态演进标记（不受 ADR-0014 冻结约束，扩展时递增）

    契约不变式（__post_init__ 强制）：
    1. `phase` 必须 ∈ BehaviorPhase（接受 str 自动归一）
    2. `first_seen` / `last_seen` 必须 UTC timezone-aware
    3. `last_seen >= first_seen`
    4. `dwell_seconds >= 0`
    5. `proximity_score` clamp 到 [0,1]
    """

    track_id: int
    visitor_instance_id: str
    phase: BehaviorPhase
    first_seen: datetime
    last_seen: datetime
    dwell_seconds: float
    is_odd_hour: bool
    proximity_score: float = 0.0
    schema_version: int = 1

    def __post_init__(self) -> None:
        # 1) phase 枚举归一
        if not isinstance(self.phase, BehaviorPhase):
            if isinstance(self.phase, str):
                self.phase = BehaviorPhase(self.phase)
            else:
                raise TypeError(
                    f"phase 必须是 BehaviorPhase 或 str，收到 {type(self.phase).__name__}"
                )

        # 2) 时间 UTC 校验（先于 last>=first 比较，否则 naive vs aware 抛 TypeError）
        require_utc(self.first_seen, "first_seen")
        require_utc(self.last_seen, "last_seen")

        # 3) last_seen >= first_seen
        if self.last_seen < self.first_seen:
            raise ValueError(
                f"last_seen ({self.last_seen}) 必须 >= first_seen ({self.first_seen})"
            )

        # 4) dwell_seconds 非负
        if self.dwell_seconds < 0:
            raise ValueError(f"dwell_seconds 必须 >= 0，收到 {self.dwell_seconds}")

        # 5) proximity_score clamp [0,1]
        if not isinstance(self.proximity_score, (int, float)):
            raise TypeError(
                f"proximity_score 必须是 float，收到 {type(self.proximity_score).__name__}"
            )
        self.proximity_score = min(1.0, max(0.0, float(self.proximity_score)))

        # 6) schema_version 基本合法性
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError(f"schema_version 必须是 >=1 的 int，收到 {self.schema_version!r}")

    def to_dict(self) -> Dict[str, Any]:
        """structlog-safe 字典（时间转 ISO、枚举转 value）。

        注意：**不含 `visits_in_window`**（跨访问统计归 RecentBehaviorStore，
        经 RealtimeContext.recent_behavior 提供）。
        """
        return {
            "track_id": self.track_id,
            "visitor_instance_id": self.visitor_instance_id,
            "phase": self.phase.value,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "dwell_seconds": round(self.dwell_seconds, 3),
            "is_odd_hour": self.is_odd_hour,
            "proximity_score": round(self.proximity_score, 4),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorState":
        """从 to_dict() 产出的字典反序列化（枚举 value → 枚举实例、ISO 字符串 → datetime）。

        用于 Stage B/C 跨进程传递 / 日志回放 / 测试构造。与 `to_dict()` 严格对称。
        注意：`to_dict()` 对 dwell_seconds/proximity_score 做了 round，
        `from_dict()` 接受任意合法 float（round 后的值仍满足契约不变式）。
        """
        return cls(
            track_id=data["track_id"],
            visitor_instance_id=data["visitor_instance_id"],
            phase=data["phase"],
            first_seen=datetime.fromisoformat(data["first_seen"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            dwell_seconds=data["dwell_seconds"],
            is_odd_hour=data["is_odd_hour"],
            proximity_score=data["proximity_score"],
            schema_version=data["schema_version"],
        )


@dataclass
class RealtimeContext:
    """实时上下文（RealTimeRiskEvaluator 的真正输入）。

    = 当前状态（纯态，state=f(Reality, Time)）＋ 近期行为（跨访问统计）。
    State 与 History 在此组合，未来 Memory ADR 可分别处置两个时间尺度（ADR-0021 §3.2）。
    """

    current_state: BehaviorState
    recent_behavior: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.current_state, BehaviorState):
            raise TypeError(
                f"current_state 必须是 BehaviorState，收到 {type(self.current_state).__name__}"
            )
        if not isinstance(self.recent_behavior, dict):
            raise TypeError(
                f"recent_behavior 必须是 dict，收到 {type(self.recent_behavior).__name__}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """组合体序列化（current_state 走 BehaviorState.to_dict，recent_behavior 透传）。"""
        return {
            "current_state": self.current_state.to_dict(),
            "recent_behavior": self.recent_behavior,
        }

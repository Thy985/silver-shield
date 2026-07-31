"""感知事件基础类型（模块对外输出的最小契约基底）。

> **P0-10.5.2 收敛**：`PerceptionEvent` 的唯一权威定义已迁移至
> `analysis/perception.py`（风险语义层对外契约），本模块**不再重复定义**，
> 以避免双定义架构漂移。本模块保留：
> - `EventType`：§7.2 五类标签枚举（向后兼容引用）
> - `EvidenceRef`：取证引用
>
> 完整字段说明见 docs/07_event_schema.md。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventType(str, Enum):
    # 与《银龄盾架构设计完善版》"门前风险输出"标签对齐：
    # 本模块只输出"标签/事件"，不直接输出"诈骗人员"结论。
    VISIT_NORMAL = "visit_normal"  # 普通来访（白名单/已知）
    VISIT_PENDING_VERIFY = "visit_pending_verify"  # 待核验来访（非白名单陌生访客）
    ABNORMAL_DWELL = "abnormal_dwell"  # 异常停留（门前长时间逗留）
    REPEAT_VISIT = "repeat_visit"  # 重复来访（短时内多次出现，疑似踩点）
    HIGH_RISK_APPROACH = "high_risk_approach"  # 高风险接近（尾随/反复靠近又离开/强行靠近）


@dataclass
class EvidenceRef:
    kind: str  # snapshot | clip
    uri: str  # 本地路径或对象存储 URL
    timestamp: float

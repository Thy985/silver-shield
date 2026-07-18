"""感知事件模型（模块对外输出的最小契约）。

事件经 output 层序列化后上报至中心风控引擎；evidence 字段携带取证引用。
完整字段说明见 docs/07_event_schema.md。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


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


@dataclass
class PerceptionEvent:
    device_id: str
    event_type: EventType
    score: float  # 风险置信度 0~1
    timestamp: float
    track_id: int | None = None
    bbox: list[float] | None = None  # [x1, y1, x2, y2] 归一化前像素坐标
    location: str | None = None
    repeat_count: int | None = None  # 短时内同一访客出现次数（重复来访判定用）
    is_odd_hour: bool = False  # 是否处于异常时段（夜间/独处）
    evidence: list[EvidenceRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

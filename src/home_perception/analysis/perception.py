"""PerceptionEvent 领域对象（P0-7b · 风险语义层 · 对外契约）。

> **P0-7b = 风险语义层。** PerceptionEvent 是 Rule 命中后的对外 5 类标签事件
> （与 `docs/07_event_schema.md` 5 类枚举对齐），最终通过 P0-9 MQTT 上报给中心。

**关键边界（ADR-0009）**：
- `score` / `perception_score` 字段是**规则命中强度**（0-1），**不是诈骗概率**
- 中心侧不能用 `score` 直接判定诈骗；综合判断由中心 AI Understand / Predict 层负责
- 字段增删按 ADR-0005 走 schema_version 评审
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise TypeError(f"visitor_id 必须是 UUID 或 str 格式 UUID，收到 {type(value).__name__}")


# §7.2 5 类 EventType（严格枚举，禁止自由文本）
EVENT_TYPES = (
    "visit_normal",
    "visit_pending_verify",
    "abnormal_dwell",
    "repeat_visit",
    "high_risk_approach",
)


# ============================================================================
# PerceptionEvent
# ============================================================================

@dataclass
class PerceptionEvent:
    """风险语义层对外契约事件（§7.2 5 类之一）。

    字段（§7.2 + 适配 P0-6 visitor_id UUID）：
    - `device_id`：设备内部 ID（来自 devices.yaml/config）
    - `event_type`：5 类之一
    - `score`：规则命中强度（0-1）—— **不是诈骗概率**（ADR-0009 关键边界）
    - `timestamp`：事件发生 Unix 秒（保留 float 与 §7.2 兼容）
    - `visitor_id`：UUID（来自 P0-6 VisitorEvent.visitor_id）
    - `track_id`：int（与 §7.2 兼容；YOLO/ByteTrack 内部 ID，可能复用）
    - `source_video`：来源视频元数据
    - `bbox`：可选，最近一次检测 bbox（[x1,y1,x2,y2] 像素）
    - `location`：可选，安装区域描述（"入户门" 等）
    - `repeat_count`：可选，§7.2 叠加标记
    - `is_odd_hour`：§7.2 叠加标记
    - `evidence`：可选，§7.2 取证引用列表（snapshot/clip URI）；P0-7b 不填，P0-8 填
    - `meta`：规则名 + 命中详情 + 触发源 event_id（用于可解释性 / 审计）
    - `created_at`：本事件生成时刻（UTC）

    严格**不含**：
    - `fraud_result` / `crime_probability` / `final_decision` / `verdict` 等任何"最终判定"字段
      —— 留给中心综合判断（ADR-0001 / ADR-0007）
    """

    device_id: str
    event_type: str
    score: float
    visitor_id: UUID
    source_video: str
    timestamp: float  # Unix 秒（与 §7.2 兼容；datetime 也可从 meta.enter_at 派生）
    track_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    location: Optional[str] = None
    repeat_count: Optional[int] = None
    is_odd_hour: bool = False
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # visitor_id 归一
        self.visitor_id = _coerce_uuid(self.visitor_id)
        # event_type 枚举校验
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"event_type 必须是 §7.2 5 类之一 {EVENT_TYPES}，收到 {self.event_type!r}"
            )
        # score 范围
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score 必须在 [0, 1]，收到 {self.score}")
        # timestamp 合理性
        if self.timestamp < 0:
            raise ValueError(f"timestamp 必须 >= 0，收到 {self.timestamp}")
        # bbox 形状
        if self.bbox is not None and len(self.bbox) != 4:
            raise ValueError(f"bbox 必须是 4 个数 [x1,y1,x2,y2]，收到 {self.bbox}")
        # meta 必含 rule_name（§7.2 字段 + 可审计）
        if "rule" not in self.meta:
            raise ValueError("meta 必须含 'rule' 字段（§7.2：触发的 Rule 名称）")

    def to_dict(self) -> Dict[str, Any]:
        """structlog-safe 字典（datetime 已转 ISO 字符串）。"""
        return {
            "device_id": self.device_id,
            "event_type": self.event_type,
            "score": round(self.score, 4),
            "timestamp": self.timestamp,
            "visitor_id": str(self.visitor_id),
            "track_id": self.track_id,
            "bbox": self.bbox,
            "location": self.location,
            "repeat_count": self.repeat_count,
            "is_odd_hour": self.is_odd_hour,
            "evidence": self.evidence,
            "meta": self.meta,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

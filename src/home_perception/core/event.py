"""感知事件基础类型（模块对外输出的最小契约基底）。

> **P0-10.5.2 收敛**：`PerceptionEvent` 的唯一权威定义已迁移至
> `analysis/perception.py`（风险语义层对外契约），本模块**不再重复定义**，
> 以避免双定义架构漂移。本模块保留：
> - `EventType`：§7.2 五类标签枚举（向后兼容引用）
> - `EvidenceItem`：独立存储的不可变证据对象（ADR-0022 / ADR-0027 D2）
> - `EvidenceModality`：证据模态枚举（VISION / AUDIO / IDENTITY，继承 ADR-0022）
> - `RetentionTier`：证据留存分层（SHORT / MEDIUM / LONG，ADR-0027 D9）
>
> 完整字段说明见 docs/07_event_schema.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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


class EvidenceModality(str, Enum):
    """证据模态枚举（ADR-0022 §3.1 / ADR-0027 D7，继承 ADR-0022）。

    与 ``SourceModality``（ADR-0021，信号传感来源）是**不同限界上下文**的独立枚举，
    值集不共享、不互相 import（见 ADR-0021 §3.3 命名消歧）。
    """

    VISION = "vision"
    AUDIO = "audio"
    IDENTITY = "identity"


class RetentionTier(str, Enum):
    """证据留存分层（ADR-0027 D9，隐私铁律 ADR-0002）。

    - SHORT：原始音频/视频片段，最敏感，24h 后删除
    - MEDIUM：特征摘要（不含波形），30d
    - LONG：语义模式标签（已脱敏），永久
    """

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


@dataclass
class EvidenceItem:
    """独立存储的不可变证据对象（ADR-0022 / ADR-0027 D2）。

    与 ``EpisodicRecord.evidence_refs`` 解耦：episode 仅以 ``evidence_id`` 字符串引用，
    本对象存于独立证据库，按 ID 解析（ADR-0024 I2 单调性：不可变事实，uri 永不被改写）。

    字段：
    - ``evidence_id``：全局唯一，被 ``EpisodicRecord.evidence_refs`` 引用
    - ``modality``：证据模态（``EvidenceModality``）
    - ``kind``：模态内类型（segment / clip / snapshot / pose_* / ...）
    - ``uri``：本地路径 / 片段 id（原片不上传，ADR-0002 §3.3）
    - ``captured_at``：采集时刻（UTC）
    - ``confidence``：置信度 [0,1]，未知为 None（绝不伪造 1.0）
    - ``metadata``：模态内附加（如 audio kind / score / duration）
    - ``retention_tier``：留存层级（D9）
    - ``expires_at``：到期时刻（SHORT/MEDIUM 计算；LONG=None 永久）
    """

    evidence_id: str
    modality: EvidenceModality
    kind: str
    uri: str | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    retention_tier: RetentionTier = RetentionTier.SHORT
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.evidence_id.strip():
            raise ValueError("evidence_id 不能为空")
        if not isinstance(self.modality, EvidenceModality):
            raise TypeError(f"modality 必须是 EvidenceModality，收到 {self.modality!r}")
        if not self.kind or not self.kind.strip():
            raise ValueError("kind 不能为空")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at 必须是 timezone-aware（UTC）")
        if self.confidence is not None:
            if not (0.0 <= float(self.confidence) <= 1.0):
                raise ValueError(f"confidence 必须在 [0, 1] 或 None，收到 {self.confidence}")
            self.confidence = float(self.confidence)
        if not isinstance(self.metadata, dict):
            raise TypeError(f"metadata 必须是 dict，收到 {type(self.metadata).__name__}")
        if not isinstance(self.retention_tier, RetentionTier):
            raise TypeError(
                f"retention_tier 必须是 RetentionTier，收到 {self.retention_tier!r}"
            )
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at 必须是 timezone-aware（UTC）或 None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "modality": self.modality.value,
            "kind": self.kind,
            "uri": self.uri,
            "captured_at": self.captured_at.isoformat(),
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "retention_tier": self.retention_tier.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        return cls(
            evidence_id=data["evidence_id"],
            modality=EvidenceModality(data["modality"]),
            kind=data["kind"],
            uri=data.get("uri"),
            captured_at=datetime.fromisoformat(data["captured_at"]),
            confidence=data.get("confidence"),
            metadata=dict(data.get("metadata", {})),
            retention_tier=RetentionTier(data.get("retention_tier", RetentionTier.SHORT.value)),
            expires_at=datetime.fromisoformat(data["expires_at"])
            if data.get("expires_at")
            else None,
        )

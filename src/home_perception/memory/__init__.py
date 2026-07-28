"""Memory Pipeline 模块（ADR-0024 工程落地）。

> 本模块按 ADR-0024 三类记忆模型 + Memory Policy 抽象组织：
> - `records.py`：Memory 领域对象 dataclass（ShortTermRecord / EpisodicRecord /
>   SemanticAggregate）+ MemoryStatus 枚举 + ActionSummary / EvidenceRef 辅助类型
> - `policy.py`：MemoryPolicy ABC（转换边界，ADR-0024 §3.2）
> - `short_term_policy.py`：DefaultShortTermPolicy（transform_short_term 实现，Slice 2）
>
> **Slice 2 范围**：实现 Short-term Memory 投影（transform_short_term），
> 不连存储、不接 pipeline。Episode Builder 实现见 Slice 4（Stage B），
> Snapshot 持久化见 Slice 3（Stage C）。
>
> **边界铁律**（ADR-0024 §3.2.2）：Memory Policy 只做 ObservationStream → MemoryRecord
> 的确定性投影，不参与风险判定 / 行动决策 / LLM 推理。
"""
from __future__ import annotations

from .records import (
    ActionSummary,
    EvidenceRef,
    EpisodicRecord,
    MemoryStatus,
    RecordIdPrefix,
    SemanticAggregate,
    ShortTermRecord,
)
from .policy import MemoryPolicy
from .short_term_policy import DefaultShortTermPolicy

__all__ = [
    "ActionSummary",
    "DefaultShortTermPolicy",
    "EvidenceRef",
    "EpisodicRecord",
    "MemoryPolicy",
    "MemoryStatus",
    "RecordIdPrefix",
    "SemanticAggregate",
    "ShortTermRecord",
]

"""Memory Pipeline 模块（ADR-0024 工程落地）。

> 本模块按 ADR-0024 三类记忆模型 + Memory Policy 抽象组织：
> - `records.py`：Memory 领域对象 dataclass（ShortTermRecord / EpisodicRecord /
>   SemanticAggregate）+ MemoryStatus 枚举 + ActionSummary / EvidenceRef 辅助类型
> - `policy.py`：MemoryPolicy ABC（转换边界，ADR-0024 §3.2）
> - `short_term_policy.py`：DefaultShortTermPolicy（transform_short_term 实现，Slice 2）
>
> **Slice 2**（#79/#80）：`DefaultShortTermPolicy` 实现 Short-term Memory 投影（transform_short_term）。
> **Slice 3**（#82）：`snapshot.py` 定义 `RuntimeSnapshot` / `SnapshotStore`；`cold_start.py` 定义
> `ColdStartCoordinator`，由 `runtime/pipeline.py` 在启动期调用恢复运行时状态（解 TD-0027）。
> **Slice 4**（#83）：`episode_builder.py` 定义 `DefaultEpisodeBuilder`，实现 `project_episode`（Stage B）。
> **Slice 5**（#84）：`store.py` 定义 `MemoryStore` / `InMemoryStore`（Episodic 持久化后端，v1 内存 + JSON 序列化）。
>
> **实施进度**：Slices 1–6 + Stage F + Integration Closure（B/C/A/D）已合入 `main`（内部 Slices 1–6 + Stage F: #77–#88；Closure B#93 / C#91 / A#94 / D#95），单元测试全绿。
> **Stage F**（Pipeline Shadow Mode 接线，#87）：`DefaultEpisodeBuilder` 已包级导出；流水线侧接线见
> `runtime/pipeline.py`——`memory.enabled + memory.episodic_shadow` 同时为真时，每次访客离场
> 经 `project_episode` 投影为 `EpisodicRecord` 写入 `InMemoryStore`（Shadow Mode：只记录、
> 不接决策、不产 Warning）。默认 `episodic_shadow=false`，与 Snapshot Recovery 相互独立。
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
    VisitorPresenceStatus,
)
from .policy import MemoryPolicy
from .short_term_policy import DefaultShortTermPolicy
from .snapshot import (
    ActiveTrackSnapshot,
    RecentBehaviorSnapshot,
    RuntimeSnapshot,
    SnapshotStore,
)
from .cold_start import ColdStartConfidence, ColdStartCoordinator, RecoveryResult
from .episode_builder import DefaultEpisodeBuilder
from .query import MemoryQuery

__all__ = [
    "ActionSummary",
    "ActiveTrackSnapshot",
    "ColdStartConfidence",
    "ColdStartCoordinator",
    "DefaultEpisodeBuilder",
    "DefaultShortTermPolicy",
    "EvidenceRef",
    "EpisodicRecord",
    "MemoryPolicy",
    "MemoryQuery",
    "MemoryStatus",
    "RecentBehaviorSnapshot",
    "RecordIdPrefix",
    "RecoveryResult",
    "RuntimeSnapshot",
    "SemanticAggregate",
    "ShortTermRecord",
    "SnapshotStore",
    "VisitorPresenceStatus",
]
from .store import InMemoryStore, InvariantViolationError, MemoryStore
__all__ += ["InMemoryStore", "InvariantViolationError", "MemoryStore"]

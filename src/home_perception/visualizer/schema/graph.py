"""ADR-0035 D5 · Evidence Graph：展示层统一因果抽象（从概念变实体）。

Owner 评审（D1.5 方向）：Evidence Explorer 的四个视图（Timeline / Decision
Trace / Cross Modal / Fingerprint）应共享同一底层图结构，而不是四套独立数据。
本模块冻结 Evidence Graph 的 **Node / Edge 类型闭集**（D5 白名单，不许自由字符串）：

- 节点（Node）：Scenario / Frame / Detection / Event / Decision / Action /
  Episode / Link（8 类）；
- 边（Edge）：observed_from / caused_by / triggered / supports / stored_as
  （5 类）。

**派生模型边界（ADR-0035 D5 评审收紧）**：Evidence Graph 是**展示层派生模型
（presentation-layer derived model）**，不属于运行时领域模型，不作为 runtime
状态交换协议——只由 ``loader`` 从已落盘 artifact 构造，runtime 完全不知晓。

**D2 硬规则在图上同样生效**：每个节点/边携带 ``ref``（溯源）与
``provenance_kind``（真实性标注）；只投影 artifact 真实存在的字段，
缺失粒度降级（禁 synthetic）。
"""

from __future__ import annotations

from typing import Literal, TypedDict

from home_perception.visualizer.schema.evidence import ProvenanceKind

# Node 类型闭集（D5 白名单）。
# 评审 R3-#4：**Frame / Detection 为预留位**——D1.5 数据源（ADR-0034 canonical）
# 无帧级/检测级 detail，loader 当前只投影 6 类（Scenario/Event/Decision/Action/
# Episode/Link）；Frame/Detection 留待真实设备接入（provenance_kind=REAL_SENSOR）
# 或 D2 Replay 引入帧级 trace 时启用，**不是漏实现**。实现侧约束：loader 新增
# 节点类型前必须先在此登记 + 更新 schema 闭集，禁止自由字符串。
NodeType = Literal[
    "Scenario", "Frame", "Detection", "Event", "Decision", "Action", "Episode", "Link"
]

# Edge 类型闭集（D5 白名单）。
EdgeType = Literal["observed_from", "caused_by", "triggered", "supports", "stored_as"]


class EvidenceGraphNode(TypedDict):
    """图节点（D2：ref 必填、provenance_kind 必填）。"""

    id: str  # 确定性 id（如 "scn" / "event-0" / "decision-0"）
    type: NodeType
    label: str  # 展示标签（真实字段值或计数摘要，不捏造）
    ref: str  # 溯源：<artifact 文件名>#<记录定位>
    provenance_kind: ProvenanceKind


class EvidenceGraphEdge(TypedDict):
    """图边（D2：ref 必填；D5 边类型闭集）。"""

    source: str  # 源节点 id
    target: str  # 目标节点 id
    type: EdgeType
    ref: str  # 溯源：指向产生该关系的 artifact 字段


class EvidenceGraph(TypedDict):
    """D1.5 主视图的共享输入（Timeline / Decision Trace / Cross Modal 的投影视角）。"""

    scenario_id: str
    nodes: tuple[EvidenceGraphNode, ...]
    edges: tuple[EvidenceGraphEdge, ...]


__all__ = [
    "EdgeType",
    "EvidenceGraph",
    "EvidenceGraphEdge",
    "EvidenceGraphNode",
    "NodeType",
]

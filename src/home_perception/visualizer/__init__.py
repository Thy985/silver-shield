"""ADR-0035 · Runtime Evidence Explorer（Evidence Presentation Layer）。

只读消费 ADR-0034 落盘 artifact → 生成自包含 HTML 证据探索器。
本包**不 import 任何生产/验证代码**（D3 AST 契约：visualizer 是 import 图死胡同叶子）。

子模块：
- ``schema.evidence``：投影目标类型（TypedDict，D3）；
- ``loader``：IntegrationArtifact → EvidenceProjection（D2 投影契约，fail-closed）；
- ``renderer``：EvidenceProjection → 自包含 HTML（D4，stdlib + vendored ECharts）。
"""

from __future__ import annotations

from home_perception.visualizer.loader import (
    EvidenceProjectionError,
    load_evidence_projection,
)
from home_perception.visualizer.renderer import render_projection

__all__ = [
    "EvidenceProjectionError",
    "load_evidence_projection",
    "render_projection",
]

"""ADR-0036 Slice A · viewer 包（Case Viewer 适配层 + 渲染器）。

子模块：
- ``case_presentation``：CasePresentationDescriptor（VM-11 纯展示编排）；
- ``artifact_source``：Artifact Adapter（VM-3 薄封装 load_evidence_projection）；
- ``render``：EvidenceProjection → 自包含 Case Viewer HTML。

viewer/ 是 ``visualizer`` 的子包，仍为 import 图死胡同叶子（不 import silver_demo /
生产 runtime，VM-3）。本文件直接导出（无循环依赖：viewer 仅依赖 visualizer 内部）。
"""

from __future__ import annotations

from home_perception.visualizer.viewer.artifact_source import (
    EvidenceProjectionError,
    load_case_artifact,
    load_case_presentation,
)
from home_perception.visualizer.viewer.case_presentation import (
    CasePresentationDescriptor,
    build_default_case_presentation,
    load_case_descriptor,
)
from home_perception.visualizer.viewer.media_source import (
    MediaManifest,
    MediaSourceError,
    resolve_media_source,
)
from home_perception.visualizer.viewer.render import render_case_viewer

__all__ = [
    "CasePresentationDescriptor",
    "EvidenceProjectionError",
    "MediaManifest",
    "MediaSourceError",
    "build_default_case_presentation",
    "load_case_artifact",
    "load_case_descriptor",
    "load_case_presentation",
    "render_case_viewer",
    "resolve_media_source",
]

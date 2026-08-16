"""ADR-0036 Slice A · Artifact Adapter（VM-3 / D-Adapter）。

薄封装 ``visualizer.loader.load_evidence_projection`` —— Case Viewer 的 Artifact 模式入口：

- ``load_case_artifact(directory)``：等价 ``load_evidence_projection``，命名对齐 Case
  Viewer 语境；
- ``load_case_presentation(directory, descriptor_path?)``：一次调用拿到
  ``(EvidenceProjection, CasePresentationDescriptor)``（VM-1 唯一事实源 + VM-11 展示编排）。

本模块**只 import visualizer 内部**（loader / schema / case_presentation），**绝不**
import ``silver_demo`` / 生产 runtime（VM-3）。viewer/ 仍是 import 图死胡同叶子。
"""

from __future__ import annotations

import json
from pathlib import Path

from home_perception.visualizer.loader import (
    EvidenceProjectionError,
    load_evidence_projection,
)
from home_perception.visualizer.schema.evidence import EvidenceProjection
from home_perception.visualizer.viewer.case_presentation import (
    CasePresentationDescriptor,
    build_default_case_presentation,
    load_case_descriptor,
)

__all__ = [
    "CasePresentationDescriptor",
    "EvidenceProjectionError",
    "load_case_artifact",
    "load_case_presentation",
]


def load_case_artifact(directory: str | Path) -> EvidenceProjection:
    """Artifact Adapter：读取 artifact 目录 → EvidenceProjection（VM-1 唯一事实源）。

    等价 ``load_evidence_projection``，仅命名对齐 Case Viewer。fail-closed：artifact
    缺失 / 字段演化时抛 ``FileNotFoundError`` / ``EvidenceProjectionError``，不产残缺投影。
    """
    return load_evidence_projection(directory)


def load_case_presentation(
    directory: str | Path,
    descriptor_path: str | Path | None = None,
    *,
    scenario_index: int = 0,
) -> tuple[EvidenceProjection, CasePresentationDescriptor]:
    """一次调用拿（投影 + 展示编排）。

    - 事实：来自 ``load_case_artifact``（VM-1，EvidenceProjection 唯一事实源）；
    - 编排：``descriptor_path`` 存在则读人类提供编排（fail-closed 拒事实字段），否则派生
      默认纯展示编排（VM-11，不读事实值做判断）。
    - G0-4：人类编排未显式指定 ``first_screen_layout``（如 CI descriptor 只有
      generated_by 等元数据）时，派生感知场景的默认面板（含 memory_timeline 注入）——
      否则 CI 受控生成模式拿不到 Memory Timeline 等案例专属组件。
    """
    projection = load_case_artifact(directory)
    if descriptor_path is not None:
        descriptor = load_case_descriptor(descriptor_path)
        raw = json.loads(Path(descriptor_path).read_text(encoding="utf-8"))
        if "first_screen_layout" not in raw:
            default = build_default_case_presentation(
                projection, scenario_index=scenario_index
            )
            descriptor["first_screen_layout"] = default["first_screen_layout"]
    else:
        descriptor = build_default_case_presentation(
            projection, scenario_index=scenario_index
        )
    return projection, descriptor

"""ADR-0032 ``validation/simulation`` 子包：generator（detections）+ renderer（frames）。"""

from __future__ import annotations

from .generator import (
    ACTOR_TYPE_TO_CLASS,
    SYNTHETIC_CONFIDENCE,
    ScenarioDetectionDetector,
    detection_to_dict,
    emit_detections,
    export_detections_json,
    interpolate_actor_box,
)
from .renderer import export_mp4, render_frames

__all__ = [
    "ACTOR_TYPE_TO_CLASS",
    "SYNTHETIC_CONFIDENCE",
    "ScenarioDetectionDetector",
    "detection_to_dict",
    "emit_detections",
    "export_detections_json",
    "export_mp4",
    "interpolate_actor_box",
    "render_frames",
]

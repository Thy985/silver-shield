"""目标跟踪（Phase 1）。

ultralytics 的 model.track() 已内置 ByteTrack / BotSORT，会直接回填 track_id，
因此本模块通常无需单独实现跟踪器；保留此文件以便将来接入独立跟踪或跨帧关联。
"""
from __future__ import annotations

from .detector import Detection


def track(detections: list[Detection], frame) -> list[Detection]:
    # TODO(Phase1): 接入跨帧关联 / ReID，提升遮挡场景下的 ID 一致性
    raise NotImplementedError("Phase 1: 接入跟踪器")

"""取证采集：事件触发时保存快照与短片段，并回挂 EvidenceRef。

隐私约束（风险 T5）：仅中/高风险或受中心指令时采集；截图/片段中无关路人、
门牌/银行卡等敏感区域应遮挡/模糊后再落盘。普通来访默认不存像素。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.event import EvidenceRef, PerceptionEvent


class EvidenceCollector(ABC):
    @abstractmethod
    def collect(self, event: PerceptionEvent, frame, recent_frames) -> list[EvidenceRef]:
        ...


class LocalClipCollector(EvidenceCollector):
    def __init__(self, local_dir: str = "data/evidence", clip_seconds: int = 10, snapshot: bool = True):
        self.local_dir = local_dir
        self.clip_seconds = clip_seconds
        self.snapshot = snapshot

    def collect(self, event: PerceptionEvent, frame, recent_frames) -> list[EvidenceRef]:
        # TODO(Phase 1): 存快照 + 前后各 clip_seconds/2 短片段；敏感区遮挡；
        # 返回 [EvidenceRef(...)] 挂到 event.evidence
        raise NotImplementedError("Phase 1: 触发式中/高风险时存取证片段")

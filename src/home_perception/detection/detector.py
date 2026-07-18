"""检测器接口与 YOLO 封装占位。

Phase 1 将基于 ultralytics 的 YOLOv8/v11 实现 detect()，并叠加 ByteTrack/BotSORT
跟踪为 Detection 补全 track_id（供规则层计算逗留时长、人数等）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Detection:
    class_id: int
    label: str
    conf: float
    bbox: list[float]  # [x1, y1, x2, y2] 像素坐标
    track_id: int | None = None


class Detector(ABC):
    @abstractmethod
    def detect(self, frame) -> list[Detection]:
        """对单帧返回检测框列表。"""
        ...


class YOLODetector(Detector):
    """Phase 1 实现：from ultralytics import YOLO。"""

    def __init__(
        self,
        model: str = "yolov8n.pt",
        conf: float = 0.45,
        classes: tuple[int, ...] = (0,),
        device: str = "cpu",
        tracker: str = "botsort",
    ):
        self.model = model
        self.conf = conf
        self.classes = classes
        self.device = device
        self.tracker = tracker
        # TODO(Phase1): self._model = YOLO(model)

    def detect(self, frame) -> list[Detection]:
        # TODO(Phase1): results = self._model.track(frame, conf=self.conf,
        #                classes=self.classes, tracker=self.tracker, device=self.device)
        #               把 results 转成 Detection 列表
        raise NotImplementedError("Phase 1: 接入 ultralytics YOLO 推理")

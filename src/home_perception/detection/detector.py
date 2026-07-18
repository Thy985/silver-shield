"""YOLO 目标检测封装（Perceive 模块 · 事实采集层）。

职责边界（见 AGENTS.md §3 / docs/02）：
- 本模块只做"事实采集 + 事件生成"，**不做诈骗风险判断**、不输出 risk score、
  不调用 LLM、不负责阶段预测。
- `YOLODetector` 仅把一帧图像转换为结构化 `DetectionResult`（人/物检测框），
  不附加任何语义结论。

推理流水线（门前异常行为感知，第一阶段）：
    萤石 1080p 帧
        ↓ OpenCV resize 到 inference_size（默认 480，可配 profile）
        ↓ YOLO11n 推理
        ↓ 检测框映射回原始帧坐标
        ↓ DetectionResult

设计要点：
- 模型惰性加载（`load()`），构造期不触发 torch/ultralytics 导入，便于无 GPU
  环境跑单元测试。
- 显式 resize 到推理尺寸（默认 480）再推理。P0-4 实测：纯 CPU 边缘机
  yolo11n@640 推理 ~124ms 未达实时目标，故 MVP 默认 480（balanced），满足
  <100ms 且 >10FPS；640 作 accuracy 精度模式，416 作 realtime 低延迟模式
  （见 docs/09）。
- 输出 bbox 一律映射回**原始帧像素坐标**，方便上层（ROI/事件）直接使用。
- 仅关注第一阶段类别（COCO id）：person(0) / backpack(24) / handbag(26) /
  cell phone(67)，**不扩展更多类别**，避免检测器膨胀为"万能 AI 检测器"。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..common.logging import get_logger
from ..common.timeutil import now_ts
from ..core.config import ImgszProfile

log = get_logger(__name__)

# 第一阶段关注的类别（COCO 80 类子集）。
# 注意：不扩展更多类别，保持 Perceive 边界（见 AGENTS.md §3）。
ALLOWED_CLASSES: dict[int, str] = {
    0: "person",
    24: "backpack",
    26: "handbag",
    67: "cell phone",
}


@dataclass
class Detection:
    """单帧中一个目标的结构化事实。

    字段严格对应契约（docs/07）：class_id / class_name / confidence / bbox / timestamp。
    不携带任何风险语义。
    """

    class_id: int
    class_name: str
    confidence: float          # 0~1，模型置信度
    bbox: List[float]          # [x1, y1, x2, y2]，原始帧像素坐标
    timestamp: float           # 该检测的时间戳（Unix 秒）
    track_id: Optional[int] = None  # P0-5 启用跟踪后回填，P0-3 恒为 None


@dataclass
class DetectionResult:
    """一次推理的结构化输出。"""

    detections: List[Detection]
    timestamp: float                  # 帧时间戳（Unix 秒）
    inference_ms: float               # 本次推理耗时（毫秒）
    source_size: Tuple[int, int]      # (H, W) 原始帧尺寸
    inference_size: Tuple[int, int]   # (H, W) 实际送入模型的分辨率
    model: str = ""                   # 模型标识（如 yolo11n.pt）


class Detector(ABC):
    """检测器接口。Pipeline 仅依赖此抽象，便于替换/测试。"""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> DetectionResult:
        """对单帧返回结构化检测结果。"""
        ...


class YOLODetector(Detector):
    """基于 ultralytics YOLO 的检测器（第一阶段：YOLO11n + CPU）。"""

    def __init__(
        self,
        model: str = "yolo11n.pt",
        conf_threshold: float = 0.45,
        classes: Optional[List[int]] = None,
        device: str = "cpu",
        imgsz: Optional[int] = None,
        profile: "ImgszProfile | str | None" = None,
        enable_track: bool = False,
        tracker: str = "botsort",
    ):
        self.model_path = model
        self.conf_threshold = conf_threshold
        # 缺省关注第一阶段 4 类；显式传入时以传入为准
        self.classes = list(classes) if classes is not None else list(ALLOWED_CLASSES.keys())
        self.device = device
        # profile 归一化为枚举（便于 resolve；YAML 里是字符串）
        norm_profile = None
        if profile is not None:
            norm_profile = profile if isinstance(profile, ImgszProfile) else ImgszProfile(str(profile).lower())
        # 解析最终推理分辨率：显式 imgsz 优先，否则用 profile，再否则 balanced(480)
        self.imgsz = ImgszProfile.resolve(norm_profile, imgsz)
        self.enable_track = enable_track
        self.tracker = tracker
        self._model = None  # 惰性加载

    # ---- 模型加载（惰性，保证构造期不依赖 torch）----
    def load(self) -> "YOLODetector":
        """加载 YOLO 权重。首次 detect 时也会自动调用。"""
        if self._model is not None:
            return self
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - 依赖缺失提示
            raise ImportError(
                "ultralytics 未安装，请执行 `pip install ultralytics`（CPU 推理还需 torch）。"
            ) from exc
        self._model = YOLO(self.model_path)
        log.info(
            "detector.model_loaded",
            model=self.model_path,
            device=self.device,
            imgsz=self.imgsz,
            enable_track=self.enable_track,
        )
        return self

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def detect(self, frame: np.ndarray) -> DetectionResult:
        # --- 输入校验（在惰性加载之前，保证非法输入不触发 torch 导入）---
        if frame is None:
            raise ValueError("frame 为 None，无法推理")
        if not isinstance(frame, np.ndarray):
            raise TypeError(f"frame 必须是 numpy.ndarray，收到 {type(frame).__name__}")
        if frame.ndim != 3:
            raise ValueError(f"frame 应为 HWC 三通道，收到 shape={frame.shape}")

        if self._model is None:
            self.load()

        src_h, src_w = frame.shape[:2]
        # 显式 resize 到推理尺寸（避免 1080p 直接进 YOLO，控制 CPU 算力）
        resized = cv2.resize(
            frame, (self.imgsz, self.imgsz), interpolation=cv2.INTER_LINEAR
        )
        infer_h, infer_w = resized.shape[:2]

        t0 = time.perf_counter()
        if self.enable_track:
            results = self._model.track(
                resized,
                conf=self.conf_threshold,
                classes=self.classes,
                imgsz=self.imgsz,
                device=self.device,
                tracker=self.tracker,
                verbose=False,
            )
        else:
            results = self._model.predict(
                resized,
                conf=self.conf_threshold,
                classes=self.classes,
                imgsz=self.imgsz,
                device=self.device,
                verbose=False,
            )
        inference_ms = (time.perf_counter() - t0) * 1000.0

        dets = self._parse(results[0], src_w, src_h, infer_w, infer_h)
        return DetectionResult(
            detections=dets,
            timestamp=now_ts(),
            inference_ms=round(inference_ms, 2),
            source_size=(src_h, src_w),
            inference_size=(infer_h, infer_w),
            model=self.model_path,
        )

    def _parse(
        self,
        result,
        src_w: int,
        src_h: int,
        infer_w: int,
        infer_h: int,
    ) -> List[Detection]:
        """把 ultralytics 结果转换为 Detection 列表，bbox 映射回原始帧坐标。"""
        dets: List[Detection] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return dets

        # 从推理尺寸映射回原始帧坐标的缩放因子
        sx = src_w / infer_w
        sy = src_h / infer_h

        for b in boxes:
            class_id = int(b.cls[0])
            # 双保险：仅保留第一阶段关注类别
            if class_id not in ALLOWED_CLASSES:
                continue
            conf = float(b.conf[0])
            x1, y1, x2, y2 = b.xyxy[0].detach().cpu().numpy().tolist()
            # 映射回原始帧坐标并裁剪到边界内
            x1 = max(0.0, x1 * sx)
            y1 = max(0.0, y1 * sy)
            x2 = min(float(src_w), x2 * sx)
            y2 = min(float(src_h), y2 * sy)
            track_id = None
            if self.enable_track and getattr(b, "id", None) is not None:
                track_id = int(b.id[0])
            dets.append(
                Detection(
                    class_id=class_id,
                    class_name=ALLOWED_CLASSES[class_id],
                    confidence=round(conf, 4),
                    bbox=[x1, y1, x2, y2],
                    timestamp=now_ts(),
                    track_id=track_id,
                )
            )
        return dets

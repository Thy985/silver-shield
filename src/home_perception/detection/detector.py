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
- **P0-5 起 `enable_track=True`（默认 bytetrack）**：`detect()` 内部调用 `model.track(persist=True)`，
  `persist=True` 保证跟踪器在多次 `detect()` 调用间保持内部状态，相机循环里**复用同一
  `YOLODetector` 实例**即可得一致的 `track_id`（见 `detection/tracker.py` 的 `VisitorTracker` /
  `schemas.VisitorTrack`）。固定摄像头/单区域/CPU/停留分析场景下 bytetrack 足够（BoT-SORT 的 ReID
  价值不在 MVP）。遮挡降级导致 ID 跳变属可接受范围。跨帧 `track_id` 一致性由
  `tests/test_tracker.py`（含 `tests/fixtures/person.jpg` 真实链路）验证。
- 输出 bbox 一律映射回**原始帧像素坐标**，方便上层（ROI/事件）直接使用。
- 仅关注第一阶段类别（COCO id）：person(0) / backpack(24) / handbag(26) /
  cell phone(67)，**不扩展更多类别**，避免检测器膨胀为"万能 AI 检测器"。
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    confidence: float  # 0~1，模型置信度
    bbox: list[float]  # [x1, y1, x2, y2]，原始帧像素坐标
    timestamp: float  # 该检测的时间戳（Unix 秒）
    track_id: int | None = None  # P0-5 启用跟踪后回填，P0-3 恒为 None


@dataclass
class DetectionResult:
    """一次推理的结构化输出。"""

    detections: list[Detection]
    timestamp: float  # 帧时间戳（Unix 秒）
    inference_ms: float  # 本次推理耗时（毫秒）
    source_size: tuple[int, int]  # (H, W) 原始帧尺寸
    inference_size: tuple[int, int]  # (H, W) 实际送入模型的分辨率
    model: str = ""  # 模型标识（如 yolo11n.pt）


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
        classes: list[int] | None = None,
        device: str = "cpu",
        imgsz: int | None = None,
        profile: ImgszProfile | str | None = None,
        enable_track: bool = False,
        tracker: str = "bytetrack",
    ):
        self.model_path = model
        self.conf_threshold = conf_threshold
        # 缺省关注第一阶段 4 类；显式传入时以传入为准
        self.classes = list(classes) if classes is not None else list(ALLOWED_CLASSES.keys())
        self.device = device
        # profile 归一化为枚举（便于 resolve；YAML 里是字符串）
        norm_profile = None
        if profile is not None:
            norm_profile = (
                profile if isinstance(profile, ImgszProfile) else ImgszProfile(str(profile).lower())
            )
        # 解析最终推理分辨率：显式 imgsz 优先，否则用 profile，再否则 balanced(480)
        self.imgsz = ImgszProfile.resolve(norm_profile, imgsz)
        self.enable_track = enable_track
        self.tracker = tracker
        self._model = None  # 惰性加载

    # ---- 模型加载（惰性，保证构造期不依赖 torch）----
    def load(self) -> YOLODetector:
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

    def unload(self) -> None:
        """释放已加载的模型引用（退出 / 换场景前统一清理）。

        跨场景复用同一 detector 实例时由 run_demo 在 finally 调用（见 runtime/lifecycle.py）；
        单场景短跑也可调。置空后下次 detect() 自动重新惰性加载。
        """
        self._model = None

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
        resized = cv2.resize(frame, (self.imgsz, self.imgsz), interpolation=cv2.INTER_LINEAR)
        infer_h, infer_w = resized.shape[:2]

        t0 = time.perf_counter()
        if self.enable_track:
            tracker = self.tracker
            # ultralytics 内置跟踪器需带 .yaml 后缀（botsort/bytetrack 等）；
            # 若传入的是已知名（非路径、无后缀），自动补 .yaml，配置里写 "bytetrack" 即可。
            if not tracker.endswith(".yaml") and not os.path.isabs(tracker) and "/" not in tracker:
                tracker = f"{tracker}.yaml"
            # persist=True 是 P0-5 的关键：保证跟踪器在多次 detect() 调用间保持内部状态，
            # 跨帧 track_id 才稳定。因此 YOLODetector 实例必须在相机循环里复用，不得每帧重建。
            results = self._model.track(
                resized,
                conf=self.conf_threshold,
                classes=self.classes,
                imgsz=self.imgsz,
                device=self.device,
                tracker=tracker,
                persist=True,
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
    ) -> list[Detection]:
        """把 ultralytics 结果转换为 Detection 列表，bbox 映射回原始帧坐标。"""
        dets: list[Detection] = []
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

"""Slice B 闭环测试共享件：真实 detector 路径（cached detection replay）。

本文件的 detector **重放模拟真实检测缓存 schema 的最小合成 fixture**
（``tests/fixtures/detections/*.json``），从而证明「事件经
detector→tracker→event_builder→memory 真实进入 Memory」，而非 ``StubDetector``
绕过 detection/tracking。

诚实声明（评审 #2）：该 fixture 是**合成**的——逐帧 bbox/confidence 恒定，没有真实
YOLO+ByteTrack 输出的抖动与置信度起伏。它保真的是检测缓存的 **schema 形态**，走的是
真实 tracker→event_builder→memory 代码路径；但分布特性**不能**替代真实检测输出。
真实检测分布由 Production Demo（真机 ``camera→YOLO``）人工验证，不进 CI（模型升级不
拖垮 Memory 测试）。故此处不冒充「真实预跑缓存」，仅称「模拟 schema 的最小 fixture」。

设计铁律（与 E2E 一致）：Memory 是旁路（Shadow Mode），绝不接决策、不产 Warning、
异常不崩主链路。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from home_perception.detection.detector import Detection, DetectionResult


class CachedDetectionDetector:
    """重放合成检测缓存（torch-free，CI 合约）。

    仅实现 pipeline 依赖的 ``detect(frame)`` 鸭子接口；frame 透传忽略（与 ``YOLODetector``
    不同，不消费 frame 像素，只回放缓存）。这证明「事件经
    detector→tracker→event_builder→memory」真实进入 Memory，而非 ``StubDetector``
    绕过 detection/tracking。

    缓存必须包含 ``track_id``（tracker 会丢弃 ``track_id is None`` 的检测）。
    """

    def __init__(self, frames: list[dict[str, Any]]):
        self._frames = frames
        self._i = 0

    def detect(self, frame) -> DetectionResult:
        # 空缓存：直接返回空（无目标），不抛异常。
        if not self._frames:
            return DetectionResult(
                detections=[],
                timestamp=0.0,
                inference_ms=0.0,
                source_size=(288, 384),
                inference_size=(288, 384),
                model="cached",
            )
        # 缓存耗尽：停止重放（返回空检测，相当于目标已离场），避免静默重放离场帧
        # 掩盖上游「多喂了超出 fixture 帧数」的真实 bug。
        if self._i >= len(self._frames):
            return DetectionResult(
                detections=[],
                timestamp=0.0,
                inference_ms=0.0,
                source_size=(288, 384),
                inference_size=(288, 384),
                model="cached",
            )
        f = self._frames[self._i]
        dets = [Detection(**d) for d in f.get("detections", [])]
        ts = float(f.get("timestamp", 0.0))
        self._i += 1
        return DetectionResult(
            detections=dets,
            timestamp=ts,
            inference_ms=0.0,
            source_size=(288, 384),
            inference_size=(288, 384),
            model="cached",
        )


def load_cached_detections(path: Path) -> dict[str, Any]:
    """加载检测缓存 JSON（schema 见 tests/fixtures/detections/）。"""
    return json.loads(Path(path).read_text())

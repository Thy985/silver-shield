"""YOLODetector 测试（P0-3）。

不依赖 torch 的用例（schema / 构造 / 输入校验）始终运行；
需要 ultralytics + torch 的用例在缺失依赖时自动跳过（pytest.importorskip），
保证 CI / 无 GPU 环境也能跑通契约测试。
"""
from __future__ import annotations

import numpy as np
import pytest

from home_perception.detection.detector import (
    ALLOWED_CLASSES,
    Detection,
    DetectionResult,
    YOLODetector,
)
from home_perception.core.config import ImgszProfile


# ---------------- 不依赖 torch 的契约测试 ----------------

def test_detection_schema_fields():
    d = Detection(
        class_id=0,
        class_name="person",
        confidence=0.91,
        bbox=[10.0, 20.0, 100.0, 200.0],
        timestamp=1718000000.0,
    )
    assert d.class_id == 0
    assert d.class_name == "person"
    assert 0.0 <= d.confidence <= 1.0
    assert d.bbox == [10.0, 20.0, 100.0, 200.0]
    assert d.track_id is None  # P0-3 不跟踪


def test_detection_result_schema():
    det = Detection(
        class_id=0, class_name="person", confidence=0.9,
        bbox=[0.0, 0.0, 1.0, 1.0], timestamp=1.0,
    )
    r = DetectionResult(
        detections=[det], timestamp=1.0, inference_ms=12.3,
        source_size=(1080, 1920), inference_size=(640, 640),
        model="yolo11n.pt",
    )
    assert r.detections[0].class_name == "person"
    assert r.inference_size == (640, 640)
    assert r.source_size == (1080, 1920)
    assert r.model == "yolo11n.pt"


def test_allowed_classes_only_first_stage():
    # 边界约束：仅第一阶段 4 类，禁止扩展（避免检测器膨胀）
    assert set(ALLOWED_CLASSES.keys()) == {0, 24, 26, 67}
    assert set(ALLOWED_CLASSES.values()) == {
        "person", "backpack", "handbag", "cell phone"
    }


def test_constructor_does_not_require_torch():
    # 构造不应触发 ultralytics / torch 导入（无 GPU 环境可构造与单测）
    det = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.5,
        classes=[0, 24, 26, 67], imgsz=640,
    )
    assert det.is_loaded is False
    assert det.model_path == "yolo11n.pt"
    assert det.imgsz == 640
    assert det.classes == [0, 24, 26, 67]


def test_default_imgsz_is_balanced_480():
    # P0-4 实测：CPU 边缘部署默认 480（balanced），满足 <100ms 且 >10FPS
    det = YOLODetector()
    assert det.imgsz == 480


def test_imgsz_profile_resolution():
    # 显式 imgsz 压过 profile
    assert YOLODetector(imgsz=416, profile="accuracy").imgsz == 416
    # profile 决定 imgsz：accuracy=640 / balanced=480 / realtime=416
    assert YOLODetector(profile="accuracy").imgsz == 640
    assert YOLODetector(profile=ImgszProfile.BALANCED).imgsz == 480
    assert YOLODetector(profile="realtime").imgsz == 416
    # 无 profile 无 explicit：回退 balanced(480)
    assert YOLODetector().imgsz == 480


def test_rejects_invalid_frame_without_loading_model():
    # 非法输入应在惰性加载之前抛错，不触发 torch 导入
    det = YOLODetector()
    with pytest.raises(ValueError):
        det.detect(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        det.detect(np.zeros((10,), dtype=np.uint8))  # 1D
    with pytest.raises(TypeError):
        det.detect("not-an-array")  # type: ignore[arg-type]


# ---------------- 需要 ultralytics + torch 的用例 ----------------

ultralytics = pytest.importorskip("ultralytics")


@pytest.fixture(scope="module")
def detector() -> YOLODetector:
    d = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.45,
        classes=[0, 24, 26, 67], imgsz=640, device="cpu",
    )
    d.load()
    return d


def test_model_loads(detector: YOLODetector):
    # 验证：模型加载成功
    assert detector.is_loaded is True
    assert detector._model is not None


def test_empty_image_no_detections(detector: YOLODetector):
    # 空图片输入：不应误检
    blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
    result = detector.detect(blank)
    assert isinstance(result, DetectionResult)
    assert result.detections == []
    assert result.source_size == (1080, 1920)
    assert result.inference_size == (640, 640)


def test_inference_on_synthetic_frame(detector: YOLODetector):
    # 正常图片推理：合成一张含明显亮块的图，验证推理返回结构化结果；
    # 仅校验输出契约（不要求一定检出某类别）。
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.rectangle(frame, (800, 400), (1000, 700), (200, 200, 200), -1)
    result = detector.detect(frame)
    assert isinstance(result, DetectionResult)
    for d in result.detections:
        assert isinstance(d, Detection)
        assert d.class_id in ALLOWED_CLASSES
        assert 0.0 <= d.confidence <= 1.0
        x1, y1, x2, y2 = d.bbox
        # bbox 必须映射回原始帧坐标且在边界内
        assert 0.0 <= x1 < x2 <= 1920.0
        assert 0.0 <= y1 < y2 <= 1080.0
        assert d.timestamp > 0

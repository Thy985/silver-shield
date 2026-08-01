"""P0-4 基准测试。

- 无 torch 常跑：用 FakeDetector 验证指标聚合、warmup 排除、目标校验逻辑。
- 有 torch/ultralytics 时：跑一小段真实合成基准，确认 YOLODetector 能被基准消费。
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from benchmark.yolo_speed import (
    BenchmarkReport,
    ResourceSampler,
    check_targets,
    render_report,
    run_benchmark,
    synthetic_frames,
)
from home_perception.detection.detector import Detection, DetectionResult, Detector


class FakeDetector(Detector):
    """确定性假检测器：固定推理耗时，返回一个 person 检测，供无 torch 环境测聚合逻辑。"""

    def __init__(self, inference_ms: float = 10.0):
        self.model_path = "fake.pt"
        self.device = "cpu"
        self.imgsz = 640
        self._inference_ms = inference_ms
        self.loaded = False

    def load(self) -> FakeDetector:
        self.loaded = True
        return self

    def detect(self, frame: np.ndarray) -> DetectionResult:
        h, w = frame.shape[:2]
        # 模拟真实推理耗时（含 resize/前向），保证 e2e/inference 有可测数值
        time.sleep(self._inference_ms / 1000.0)
        det = Detection(
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=[0.0, 0.0, 10.0, 10.0],
            timestamp=time.time(),
        )
        return DetectionResult(
            detections=[det],
            timestamp=time.time(),
            inference_ms=self._inference_ms,
            source_size=(h, w),
            inference_size=(self.imgsz, self.imgsz),
            model=self.model_path,
        )


def test_synthetic_frames_shape():
    gen = synthetic_frames(width=320, height=240, max_frames=3)
    frames = list(gen)
    assert len(frames) == 3
    for ts, f in frames:
        assert isinstance(ts, float)
        assert f.shape == (240, 320, 3)
        assert f.dtype == np.uint8


def test_run_benchmark_aggregation_with_fake_detector():
    """核心：聚合出的 FPS / 延迟 / 帧计数符合预期，且 warmup 被排除。"""
    det = FakeDetector(inference_ms=5.0)
    frames = synthetic_frames(width=320, height=240)
    report = run_benchmark(
        frames,
        det,
        duration_s=5.0,
        max_frames=12,
        warmup=2,
        mode="synthetic",
    )
    assert isinstance(report, BenchmarkReport)
    # warmup=2 排除后，最多统计 12 帧
    assert report.frames == 12
    assert report.warmup == 2
    # 推理耗时固定 5ms
    assert report.inference_ms_avg == pytest.approx(5.0, abs=0.5)
    assert report.inference_fps > 0
    assert report.camera_fps > 0
    # e2e 应 >= 推理耗时（含取帧+开销）
    assert report.e2e_ms_avg >= report.inference_ms_avg - 0.5
    assert report.detections_total == 12  # 每帧一个 person
    assert report.resolution == "320x240"
    assert det.loaded is True  # 基准应触发 load()


def test_check_targets_pass_and_fail():
    fast = BenchmarkReport(
        mode="synthetic",
        model="m",
        device="cpu",
        resolution="1920x1080",
        imgsz=640,
        frames=100,
        warmup=5,
        elapsed_s=10.0,
        camera_fps=20.0,
        inference_fps=40.0,
        inference_ms_avg=25.0,
        inference_ms_p50=24.0,
        inference_ms_p95=30.0,
        inference_ms_max=35.0,
        e2e_ms_avg=50.0,
        e2e_ms_p50=48.0,
        e2e_ms_p95=60.0,
        e2e_ms_max=70.0,
        capture_ms_avg=1.0,
        cpu_percent_avg=50.0,
        cpu_percent_max=80.0,
        mem_rss_avg_mb=500.0,
        mem_rss_max_mb=600.0,
        resource_available=True,
        detections_total=100,
    )
    checks = check_targets(fast)
    assert all(c["pass"] for c in checks)

    slow = BenchmarkReport(
        mode="synthetic",
        model="m",
        device="cpu",
        resolution="1920x1080",
        imgsz=640,
        frames=100,
        warmup=5,
        elapsed_s=100.0,
        camera_fps=3.0,
        inference_fps=2.0,
        inference_ms_avg=500.0,
        inference_ms_p50=480.0,
        inference_ms_p95=600.0,
        inference_ms_max=700.0,
        e2e_ms_avg=800.0,
        e2e_ms_p50=780.0,
        e2e_ms_p95=900.0,
        e2e_ms_max=1000.0,
        capture_ms_avg=5.0,
        cpu_percent_avg=95.0,
        cpu_percent_max=100.0,
        mem_rss_avg_mb=800.0,
        mem_rss_max_mb=900.0,
        resource_available=True,
        detections_total=100,
    )
    checks = check_targets(slow)
    assert not any(c["pass"] for c in checks)


def test_render_report_contains_key_fields():
    det = FakeDetector(inference_ms=5.0)
    frames = synthetic_frames(width=320, height=240)
    report = run_benchmark(frames, det, duration_s=2.0, max_frames=5, warmup=1)
    text = render_report(report)
    assert "YOLO 性能基准报告" in text
    assert "Camera FPS" in text
    assert "Inference FPS" in text
    assert "达标校验" in text


def test_resource_sampler_context():
    with ResourceSampler(interval_s=0.05) as s:
        time.sleep(0.2)
    # psutil 已装则应有样本；未装则安静降级
    if s.available:
        assert s.rss_max_mb() >= 0.0


def test_run_benchmark_with_real_yolo_synthetic():
    """有 torch/ultralytics 时，真实 YOLODetector 能被基准消费并产出合理数值。"""
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from home_perception.detection.detector import YOLODetector

    det = YOLODetector(model="yolo11n.pt", imgsz=640, device="cpu")
    frames = synthetic_frames(width=640, height=480)
    report = run_benchmark(frames, det, duration_s=8.0, max_frames=3, warmup=1)
    assert report.frames >= 1
    assert report.inference_ms_avg > 0
    assert report.model == "yolo11n.pt"
    assert report.resolution == "640x480"
    # 每项 target 都应有布尔判定
    assert len(report.targets) == 3

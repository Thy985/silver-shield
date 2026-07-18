"""P0-4 · 视频流稳定化 + YOLO 性能基准。

目标（见 docs/08_roadmap.md P0-4）：实测真实运行链路
    萤石流 → OpenCV → YOLO → DetectionResult
的端到端性能，为答辩与"门前踩点识别"落地提供硬数据，并为 P0-5 跟踪
的帧一致性要求定基线。

量化指标（与 roadmap 目标表对齐）：
    | 指标          | 目标      |
    | 输入 FPS      | 10–15    |
    | YOLO 推理耗时 | <100ms   |
    | 端到端延迟    | <300ms   |
    | 连续运行      | ≥30 分钟 |
输出：Camera FPS / Inference FPS / Average latency / CPU usage / Memory。

边界声明（AGENTS.md §3）：本脚本只做**性能测量**，不生成事件、不做风险判断、
不输出 risk score。它消费 `DetectionResult`（事实），只统计耗时与资源占用。

两种运行模式：
- 合成模式（`--synthetic`，默认）：本机生成 1080p 帧灌入 YOLO，无需摄像头/网络，
  可复现，适合 CI 与不同硬件横向对比（测的是纯推理 + resize 开销）。
- 实时模式（`--serial <SN>`）：接真实萤石流，测端到端（含取流延迟）。需 .env。

用法：
    python benchmark/yolo_speed.py --synthetic --duration 30
    python benchmark/yolo_speed.py --serial BK6415780 --duration 1800 --protocol rtsp
    python benchmark/yolo_speed.py --synthetic --duration 60 --json out/bench.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from home_perception.core.config import ImgszProfile
from home_perception.detection.detector import Detector, DetectionResult, YOLODetector

# ---- 目标阈值（与 roadmap P0-4 指标表一致）----
TARGET_INPUT_FPS_MIN = 10.0
TARGET_INFERENCE_MS_MAX = 100.0
TARGET_E2E_MS_MAX = 300.0


# =====================================================================
# 资源采样（CPU / 内存），后台线程周期性采集，避免污染逐帧计时
# =====================================================================
class ResourceSampler:
    """后台线程按固定间隔采集进程 CPU% 与 RSS 内存。

    用 psutil；若未安装则安静降级为空样本（不影响耗时统计）。
    """

    def __init__(self, interval_s: float = 0.5):
        self.interval_s = interval_s
        self.cpu_samples: List[float] = []
        self.rss_samples_mb: List[float] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc = None
        try:
            import psutil

            self._proc = psutil.Process()
            # 首次调用用于建立基线，返回值无意义，丢弃
            self._proc.cpu_percent(None)
        except Exception:
            self._proc = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._proc is not None:
                try:
                    self.cpu_samples.append(self._proc.cpu_percent(None))
                    self.rss_samples_mb.append(
                        self._proc.memory_info().rss / (1024 * 1024)
                    )
                except Exception:
                    pass
            self._stop.wait(self.interval_s)

    def __enter__(self) -> "ResourceSampler":
        if self._proc is not None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def available(self) -> bool:
        return self._proc is not None

    def cpu_avg(self) -> float:
        return round(statistics.mean(self.cpu_samples), 1) if self.cpu_samples else 0.0

    def cpu_max(self) -> float:
        return round(max(self.cpu_samples), 1) if self.cpu_samples else 0.0

    def rss_avg_mb(self) -> float:
        return round(statistics.mean(self.rss_samples_mb), 1) if self.rss_samples_mb else 0.0

    def rss_max_mb(self) -> float:
        return round(max(self.rss_samples_mb), 1) if self.rss_samples_mb else 0.0


# =====================================================================
# 帧源
# =====================================================================
def synthetic_frames(
    width: int = 1920,
    height: int = 1080,
    max_frames: Optional[int] = None,
    seed: int = 42,
) -> Iterator[Tuple[float, np.ndarray]]:
    """生成合成 1080p 帧（随机噪声），用于无摄像头的可复现基准。

    随机内容不影响推理耗时（YOLO 前向计算与图像内容无关），因此可稳定
    衡量 resize + 推理开销。每帧新生成，模拟真实取帧的内存分配开销。
    """
    rng = np.random.default_rng(seed)
    n = 0
    while max_frames is None or n < max_frames:
        frame = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
        yield time.time(), frame
        n += 1


def live_frames(
    serial: str, protocol: str = "rtsp"
) -> Iterator[Tuple[float, np.ndarray]]:
    """真实萤石流帧源（复用 EZVIZClient + FrameSource）。需 .env 凭证。"""
    from home_perception.core.config import Settings
    from home_perception.ingestion.ezviz_client import EZVIZClient
    from home_perception.ingestion.frame_source import FrameSource

    settings = Settings.load()
    client = EZVIZClient()
    url = client.get_stream_url(
        serial,
        protocol=protocol,
        quality=settings.ingestion.quality,
        channel_no=settings.ingestion.channel_no,
    )
    source = FrameSource(
        url,
        fps_target=settings.ingestion.fps_target,
        max_retries=settings.ingestion.reconnect.max_retries,
        backoff_s=settings.ingestion.reconnect.backoff_s,
    )
    yield from source


# =====================================================================
# 指标聚合
# =====================================================================
def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    return round(float(np.percentile(values, p)), 2)


@dataclass
class BenchmarkReport:
    mode: str
    model: str
    device: str
    resolution: str            # "1920x1080"
    imgsz: int
    frames: int                # 计入统计的帧数（已排除 warmup）
    warmup: int
    elapsed_s: float
    camera_fps: float          # 帧交付/处理速率（帧数 / 墙钟）
    inference_fps: float       # 1000 / 平均推理耗时
    inference_ms_avg: float
    inference_ms_p50: float
    inference_ms_p95: float
    inference_ms_max: float
    e2e_ms_avg: float          # 取帧 + 推理 + 开销（逐帧墙钟）
    e2e_ms_p50: float
    e2e_ms_p95: float
    e2e_ms_max: float
    capture_ms_avg: float      # 仅取帧耗时
    cpu_percent_avg: float
    cpu_percent_max: float
    mem_rss_avg_mb: float
    mem_rss_max_mb: float
    resource_available: bool
    detections_total: int      # 累计检测框数（仅信息，不含语义）
    targets: List[dict] = field(default_factory=list)


def run_benchmark(
    frames: Iterable[Tuple[float, np.ndarray]],
    detector: Detector,
    *,
    duration_s: float = 30.0,
    max_frames: Optional[int] = None,
    warmup: int = 5,
    mode: str = "synthetic",
    resource_interval_s: float = 0.5,
) -> BenchmarkReport:
    """核心测量循环（帧源无关，便于测试用 FakeDetector 注入）。

    - warmup 帧不计入统计（模型首帧前向、缓存预热偏慢）。
    - 逐帧记录 capture_ms（取帧）/ inference_ms（DetectionResult）/ e2e_ms（墙钟）。
    - 后台线程采集 CPU / 内存。
    """
    inf_ms: List[float] = []
    e2e_ms: List[float] = []
    cap_ms: List[float] = []
    dets_total = 0
    counted = 0

    # 预热：确保模型已加载（首次 detect 会触发 load）
    load = getattr(detector, "load", None)
    if callable(load):
        try:
            load()
        except Exception:
            pass

    start = time.perf_counter()
    with ResourceSampler(resource_interval_s) as sampler:
        frame_iter = iter(frames)
        i = 0
        while True:
            t_cap0 = time.perf_counter()
            try:
                _ts, frame = next(frame_iter)
            except StopIteration:
                break
            t_cap1 = time.perf_counter()

            result: DetectionResult = detector.detect(frame)
            t_end = time.perf_counter()

            i += 1
            # warmup 阶段：不计入，但仍消耗时间
            if i <= warmup:
                continue

            capture_ms = (t_cap1 - t_cap0) * 1000.0
            e2e = (t_end - t_cap0) * 1000.0
            cap_ms.append(capture_ms)
            e2e_ms.append(e2e)
            inf_ms.append(result.inference_ms)
            dets_total += len(result.detections)
            counted += 1

            elapsed = time.perf_counter() - start
            if elapsed >= duration_s:
                break
            if max_frames is not None and counted >= max_frames:
                break

    elapsed_s = time.perf_counter() - start
    camera_fps = round(counted / elapsed_s, 2) if elapsed_s > 0 else 0.0
    inf_avg = round(statistics.mean(inf_ms), 2) if inf_ms else 0.0
    inference_fps = round(1000.0 / inf_avg, 2) if inf_avg > 0 else 0.0

    # 取最后一帧的模型元数据（若可得）
    model_name = getattr(detector, "model_path", "") or ""
    device = getattr(detector, "device", "") or ""
    imgsz = int(getattr(detector, "imgsz", 0) or 0)
    if frame is not None:  # type: ignore[has-type]
        h, w = frame.shape[:2]
        resolution = f"{w}x{h}"
    else:
        resolution = "unknown"

    report = BenchmarkReport(
        mode=mode,
        model=model_name,
        device=device,
        resolution=resolution,
        imgsz=imgsz,
        frames=counted,
        warmup=warmup,
        elapsed_s=round(elapsed_s, 2),
        camera_fps=camera_fps,
        inference_fps=inference_fps,
        inference_ms_avg=inf_avg,
        inference_ms_p50=_pct(inf_ms, 50),
        inference_ms_p95=_pct(inf_ms, 95),
        inference_ms_max=round(max(inf_ms), 2) if inf_ms else 0.0,
        e2e_ms_avg=round(statistics.mean(e2e_ms), 2) if e2e_ms else 0.0,
        e2e_ms_p50=_pct(e2e_ms, 50),
        e2e_ms_p95=_pct(e2e_ms, 95),
        e2e_ms_max=round(max(e2e_ms), 2) if e2e_ms else 0.0,
        capture_ms_avg=round(statistics.mean(cap_ms), 2) if cap_ms else 0.0,
        cpu_percent_avg=sampler.cpu_avg(),
        cpu_percent_max=sampler.cpu_max(),
        mem_rss_avg_mb=sampler.rss_avg_mb(),
        mem_rss_max_mb=sampler.rss_max_mb(),
        resource_available=sampler.available,
        detections_total=dets_total,
    )
    report.targets = check_targets(report)
    return report


def check_targets(report: BenchmarkReport) -> List[dict]:
    """对照 roadmap P0-4 指标表，输出每项是否达标。

    合成模式下 camera_fps 反映的是纯推理吞吐（无取流限速），仍以 >=10 为参考。
    """
    checks = [
        ("输入 FPS", report.camera_fps, f">= {TARGET_INPUT_FPS_MIN}",
         report.camera_fps >= TARGET_INPUT_FPS_MIN),
        ("YOLO 推理耗时(avg)", report.inference_ms_avg, f"< {TARGET_INFERENCE_MS_MAX} ms",
         report.inference_ms_avg < TARGET_INFERENCE_MS_MAX and report.inference_ms_avg > 0),
        ("端到端延迟(avg)", report.e2e_ms_avg, f"< {TARGET_E2E_MS_MAX} ms",
         report.e2e_ms_avg < TARGET_E2E_MS_MAX and report.e2e_ms_avg > 0),
    ]
    return [
        {"metric": name, "value": value, "target": target, "pass": bool(ok)}
        for name, value, target, ok in checks
    ]


# =====================================================================
# 报告渲染
# =====================================================================
def render_report(report: BenchmarkReport) -> str:
    lines = []
    lines.append("=" * 56)
    lines.append(" SilverShield · P0-4 YOLO 性能基准报告")
    lines.append("=" * 56)
    lines.append(f" 模式          : {report.mode}")
    lines.append(f" 模型 / 设备   : {report.model}  /  {report.device}")
    lines.append(f" 输入分辨率    : {report.resolution}  →  推理 {report.imgsz}x{report.imgsz}")
    lines.append(f" 统计帧数      : {report.frames}  (warmup {report.warmup} 已排除)")
    lines.append(f" 运行时长      : {report.elapsed_s} s")
    lines.append("-" * 56)
    lines.append(f" Camera FPS         : {report.camera_fps}")
    lines.append(f" Inference FPS      : {report.inference_fps}")
    lines.append(f" 推理耗时 avg/p50/p95/max : "
                 f"{report.inference_ms_avg} / {report.inference_ms_p50} / "
                 f"{report.inference_ms_p95} / {report.inference_ms_max} ms")
    lines.append(f" 端到端 avg/p50/p95/max   : "
                 f"{report.e2e_ms_avg} / {report.e2e_ms_p50} / "
                 f"{report.e2e_ms_p95} / {report.e2e_ms_max} ms")
    lines.append(f" 取帧耗时 avg       : {report.capture_ms_avg} ms")
    if report.resource_available:
        lines.append(f" CPU usage avg/max  : {report.cpu_percent_avg}% / {report.cpu_percent_max}%")
        lines.append(f" 内存 RSS avg/max   : {report.mem_rss_avg_mb} / {report.mem_rss_max_mb} MB")
    else:
        lines.append(" CPU / 内存         : (psutil 不可用，未采集)")
    lines.append("-" * 56)
    lines.append(" 达标校验（对照 roadmap P0-4 目标）：")
    for c in report.targets:
        mark = "✅" if c["pass"] else "❌"
        lines.append(f"   {mark} {c['metric']}: {c['value']}  (目标 {c['target']})")
    lines.append("=" * 56)
    return "\n".join(lines)


# =====================================================================
# CLI
# =====================================================================
def _build_detector(args: argparse.Namespace) -> YOLODetector:
    # 推理分辨率解析：显式 --imgsz 优先；否则用 --profile；否则 balanced(480)。
    # 默认 --imgsz 不传（=None），走 profile 解析，体现 P0-4 配置化结论。
    profile = None
    if getattr(args, "profile", None):
        profile = ImgszProfile(args.profile)
    return YOLODetector(
        model=args.model,
        conf_threshold=args.conf,
        device=args.device,
        imgsz=args.imgsz,
        profile=profile,
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="P0-4 YOLO 性能基准（萤石流 → OpenCV → YOLO → DetectionResult）"
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--synthetic", action="store_true",
                     help="合成帧模式（默认，无需摄像头/网络，可复现）")
    src.add_argument("--serial", help="萤石设备序列号（实时流模式，需 .env）")
    ap.add_argument("--protocol", default="rtsp", choices=["rtsp", "hls"])
    ap.add_argument("--duration", type=float, default=30.0, help="运行秒数（稳定性测试可设 1800=30min）")
    ap.add_argument("--max-frames", type=int, default=None, help="最多统计帧数（可选）")
    ap.add_argument("--warmup", type=int, default=5, help="预热帧数（不计入统计）")
    ap.add_argument("--width", type=int, default=1920, help="合成帧宽")
    ap.add_argument("--height", type=int, default=1080, help="合成帧高")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--imgsz", type=int, default=None,
                    help="显式推理分辨率（优先于 --profile）；不传则走 profile")
    ap.add_argument("--profile", default=None,
                    choices=[p.value for p in ImgszProfile],
                    help="推理分辨率预设：accuracy=640 / balanced=480 / realtime=416")
    ap.add_argument("--conf", type=float, default=0.45)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--json", dest="json_out", default=None, help="将报告写入 JSON 文件")
    args = ap.parse_args(argv)

    mode = "live" if args.serial else "synthetic"
    detector = _build_detector(args)
    eff_imgsz = detector.imgsz

    if mode == "live":
        frames: Iterable[Tuple[float, np.ndarray]] = live_frames(args.serial, args.protocol)
    else:
        frames = synthetic_frames(args.width, args.height, max_frames=args.max_frames)

    print(f"[benchmark] mode={mode} model={args.model} imgsz={eff_imgsz} "
          f"device={args.device} duration={args.duration}s ...")
    report = run_benchmark(
        frames,
        detector,
        duration_s=args.duration,
        max_frames=args.max_frames,
        warmup=args.warmup,
        mode=mode,
    )
    print(render_report(report))

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        print(f"[benchmark] 报告已写入 {args.json_out}")

    all_pass = all(c["pass"] for c in report.targets)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())

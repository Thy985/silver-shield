"""最小可运行闭环演示（P0-3）：

    萤石视频流
        -> OpenCV Frame（FrameSource 抽帧）
        -> YOLODetector 推理
        -> DetectionResult
        -> 控制台打印（不做事件生成 / 不做风险判断）

仅验证"事实采集"链路。事件生成见 P0-4，风险推理见中心引擎。

用法：
    python scripts/detect_demo.py --serial BK6415780 --duration 20 --protocol rtsp

需要 .env 配置 EZVIZ_APP_KEY / EZVIZ_APP_SECRET（见 .env.example）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from home_perception.core.config import Settings
from home_perception.detection.detector import YOLODetector
from home_perception.ingestion.ezviz_client import EZVIZClient
from home_perception.ingestion.frame_source import FrameSource


def main() -> None:
    ap = argparse.ArgumentParser(description="P0-3 最小闭环：萤石流 -> YOLO -> DetectionResult")
    ap.add_argument("--serial", required=True, help="萤石设备序列号（来自 config/devices.yaml）")
    ap.add_argument("--duration", type=int, default=20, help="运行秒数")
    ap.add_argument("--protocol", default="rtsp", choices=["rtsp", "hls"])
    ap.add_argument("--conf", type=float, default=None, help="覆盖置信度阈值")
    args = ap.parse_args()

    settings = Settings.load()
    det_cfg = settings.detection

    detector = YOLODetector(
        model=det_cfg.model,
        conf_threshold=args.conf or det_cfg.conf_threshold,
        classes=det_cfg.classes,
        device=det_cfg.device,
        imgsz=det_cfg.imgsz,
        enable_track=det_cfg.enable_track,
    )
    detector.load()
    print(f"[detect] model={detector.model_path} imgsz={detector.imgsz} "
          f"device={detector.device} classes={det_cfg.classes}")

    client = EZVIZClient()
    url = client.get_stream_url(
        args.serial, protocol=args.protocol,
        quality=settings.ingestion.quality,
        channel_no=settings.ingestion.channel_no,
    )
    source = FrameSource(
        url, fps_target=settings.ingestion.fps_target,
        max_retries=settings.ingestion.reconnect.max_retries,
        backoff_s=settings.ingestion.reconnect.backoff_s,
    )

    start = time.time()
    frames = 0
    dets_total = 0
    for _ts, frame in source:
        result = detector.detect(frame)
        frames += 1
        dets_total += len(result.detections)
        # 仅打印事实（不含任何语义结论）
        summary = ", ".join(
            f"{d.class_name}@{d.confidence:.2f}" for d in result.detections
        )
        print(f"[frame {frames}] infer={result.inference_ms:.1f}ms "
              f"dets=[{summary}]")
        if (time.time() - start) >= args.duration:
            break

    elapsed = time.time() - start
    fps = frames / elapsed if elapsed else 0.0
    print(f"--- 处理 {frames} 帧 / {elapsed:.1f}s / 抽帧 {fps:.1f} FPS / "
          f"累计检测 {dets_total} ---")


if __name__ == "__main__":
    main()

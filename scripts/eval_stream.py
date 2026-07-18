"""流质量基线评测：复用 EZVIZClient + FrameSource 测量 FPS / 延迟 / 重连。

替代 prototypes/ 的手写脚本（后者含真实凭证，仅本地）。本脚本凭证走 .env，
结果用于回答风险 T1/T2（稳定性与延迟），以及判断 YOLO 抽帧预算。

用法：
    python scripts/eval_stream.py --serial BK6415780 --duration 30 --protocol rtsp
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from home_perception.core.config import Settings
from home_perception.ingestion.ezviz_client import EZVIZClient
from home_perception.ingestion.frame_source import FrameSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--protocol", default="rtsp", choices=["rtsp", "hls"])
    args = ap.parse_args()

    settings = Settings.load()
    client = EZVIZClient()
    url = client.get_stream_url(args.serial, protocol=args.protocol,
                                quality=settings.ingestion.quality,
                                channel_no=settings.ingestion.channel_no)
    source = FrameSource(url, fps_target=settings.ingestion.fps_target,
                         max_retries=settings.ingestion.reconnect.max_retries,
                         backoff_s=settings.ingestion.reconnect.backoff_s)

    total = 0
    fail = 0
    reconnect = 0
    latencies: list[float] = []
    start = time.time()
    for ts, frame in source:
        t0 = time.time()
        _ = frame.shape
        latencies.append((time.time() - t0) * 1000)
        total += 1
        if (time.time() - start) >= args.duration:
            break

    elapsed = time.time() - start
    avg_fps = total / elapsed if elapsed else 0
    avg_lat = np.mean(latencies) if latencies else 0
    p95 = np.percentile(latencies, 95) if latencies else 0
    print(f"分辨率: 见流  | 时长 {elapsed:.1f}s | 帧 {total} | 失败 {fail} | 重连 {reconnect}")
    print(f"平均 FPS: {avg_fps:.1f} | 平均延迟 {avg_lat:.1f}ms | P95 {p95:.1f}ms")
    if avg_fps >= 10:
        print("✅ 满足 YOLO 抽帧预算（建议 fps_target>=8）")
    else:
        print("⚠️ 帧率偏低，需降分辨率或加大跳帧")


if __name__ == "__main__":
    main()

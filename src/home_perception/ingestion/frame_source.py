"""帧源：从视频流按目标帧率抽帧，带断流自动重连。

复用已验证的 OpenCV CAP_FFMPEG 读取逻辑，把重连/限速封装成可迭代对象，
便于后续检测/分析阶段以 `for ts, frame in source:` 方式消费。
"""
from __future__ import annotations

import time

import cv2

from ..common.logging import get_logger

log = get_logger(__name__)


class FrameSource:
    def __init__(
        self,
        url: str,
        fps_target: int = 8,
        max_retries: int = 5,
        backoff_s: int = 3,
    ):
        self.url = url
        self.interval = 1.0 / fps_target if fps_target > 0 else 0.0
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._cap = None
        self.recent_frames: list[tuple[float, object]] = []  # 取证用环形缓冲
        self._max_recent = 30

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError("无法打开视频流")
        return cap

    def __iter__(self):
        self._cap = self._open()
        last_ts = 0.0
        retries = 0
        while True:
            now = time.time()
            if self.interval > 0 and (now - last_ts) < self.interval:
                time.sleep(max(0.0, self.interval - (now - last_ts)))
            t0 = time.time()
            ret, frame = self._cap.read()
            if not ret:
                log.warning("frame.read_failed", retries=retries)
                retries += 1
                if retries > self.max_retries:
                    log.error("frame.give_up")
                    break
                time.sleep(self.backoff_s)
                self._cap.release()
                try:
                    self._cap = self._open()
                    log.info("frame.reconnected", retries=retries)
                except Exception:
                    continue
                continue
            retries = 0
            last_ts = time.time()
            self.recent_frames.append((t0, frame))
            if len(self.recent_frames) > self._max_recent:
                self.recent_frames.pop(0)
            yield t0, frame

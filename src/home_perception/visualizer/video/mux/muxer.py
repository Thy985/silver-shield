"""ADR-0035 D3 · VideoMuxer（阶段 8 · 合成写盘）。

D3-A：OpenCV ``VideoWriter`` 写无声 mp4（复用 export_mp4 同款 fourcc 思路）。
D3-3 降级契约（ffmpeg 非 pipeline 核心）：ffmpeg 缺失**不得**导致 pipeline fail——
D3-B 有音轨时降级为 ``video.mp4`` + ``audio.wav`` + ``warning.json``。本切片（D3-A）
不含音频，``mux`` 仅写无声 mp4；audio 分支在 D3-B 落地（此处保留明确接口与降级骨架）。

见设计文档 §2.7（VideoMuxer）、§5（D3-3 降级）、§9 D3-3。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from home_perception.visualizer.video.spec import CaseVideoSpec


class WarningInfo(BaseModel):
    """降级告警（D3-3 fail-soft）。"""

    model_config = ConfigDict(extra="forbid")
    code: str
    message: str


class MuxResult(BaseModel):
    """合成结果（D3-3 降级契约载体）。"""

    model_config = ConfigDict(extra="forbid")
    video_mp4: Path | None = None
    audio_wav: Path | None = None
    final_mp4: Path | None = None
    warning: WarningInfo | None = None


def write_silent_mp4(frames: list[np.ndarray], path: Path, fps: float = 2.0) -> Path:
    """OpenCV 写无声 mp4（mp4v）。空帧 → ValueError（fail-closed）。"""
    if not frames:
        raise ValueError("VideoMuxer 收到空帧序列，无法写出 mp4")
    height, width = frames[0].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"VideoWriter 无法打开（缺 codec？）：{path}")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return Path(path)


def mux(
    frames: list[np.ndarray],
    path: Path,
    spec: CaseVideoSpec,
    audio_track: list[np.ndarray] | None = None,
) -> MuxResult:
    """合成入口（D3-A：无声 mp4；D3-B 音频分支 deferred）。

    D3-A 路径（无音轨且 ``spec.with_audio`` 为假）直接写无声 mp4 并交付；音频合成属
    D3-B 切片，凡 ``spec.with_audio`` 为真或显式传入 ``audio_track`` 都以
    ``NotImplementedError`` 明确边界（**不静默吞掉**，也不让 with_audio=True 无声退化）。
    """
    path = Path(path)
    if spec.with_audio or audio_track is not None:
        raise NotImplementedError(
            "D3-B 音频合成（AudioComposer + ffmpeg mux + D3-3 降级）尚未落地；"
            "本切片(D3-A)仅交付无声 mp4，请勿设 with_audio=True 或传入 audio_track"
        )
    write_silent_mp4(frames, path, spec.fps)
    return MuxResult(video_mp4=path)


__all__ = ["MuxResult", "WarningInfo", "mux", "write_silent_mp4"]

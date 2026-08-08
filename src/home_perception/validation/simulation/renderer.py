"""ADR-0032 Slice C：通道二 frames 渲染器（OpenCV 程序化绘制，确定性，torch-free）。

``render_frames(scenario) -> list[np.ndarray]`` 在画布上按 ``actors.tracks`` 画实心矩形
（human 额外画头部圆）代表实体。**复用 Slice B 的同一插值几何**（``interpolate_actor_box``），
保证两通道对同一 ``Scenario`` 在几何上等价（T8 单一真相源）。

⚠️ **验证边界（D1 通道二）**：本通道验证的是**链路逻辑**（frame 摄入 / detector 接口兼容 /
tracking 连续性 / temporal 推理），**不是视觉能力**（不验证语义准确率 / 外观鲁棒性 / 光照 /
domain gap）。生成的程序化基元（矩形/圆）不含真实人脸 / PII / 真实场景（T2/T3）。

``export_mp4`` 仅本地人工检视用，**不入库**（AGENTS.md §6.1 / ADR-0032 §3 非目标 #7）。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..scenario.scenario import Scenario
from .generator import _assign_track_ids, interpolate_actor_box

# 确定性调色板（按 track_id 取色，与真实摄像头分布无关）。
_PALETTE: list[tuple[int, int, int]] = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (0, 255, 255),
    (255, 0, 255),
]

# 背景基色（恒定灰度，无真实纹理 / 人脸）。
_BG_COLOR = (30, 30, 30)


def render_frames(scenario: Scenario) -> list[np.ndarray]:
    """程序化渲染每帧 BGR 帧（长度 = ``duration_frames``）。确定性、torch-free、无真实媒体。"""
    if scenario.meta.duration_frames is None:
        raise ValueError(
            f"场景 {scenario.meta.scenario_id!r} 缺少 meta.duration_frames，"
            "无法渲染帧序列（生成期 fail-closed）"
        )
    w, h = scenario.camera.resolution
    n = scenario.meta.duration_frames
    track_map = _assign_track_ids(scenario.actors)
    frames: list[np.ndarray] = []
    for f in range(n):
        canvas = np.full((h, w, 3), _BG_COLOR, dtype=np.uint8)
        for actor in scenario.actors:
            box = interpolate_actor_box(actor, f)
            if box is None:
                continue
            cx, cy, bw, bh = box
            x1 = round(cx - bw / 2)
            y1 = round(cy - bh / 2)
            x2 = round(cx + bw / 2)
            y2 = round(cy + bh / 2)
            color = _PALETTE[(track_map[actor.id] - 1) % len(_PALETTE)]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness=-1)
            # human 额外画头部圆（仅视觉区分，不影响几何一致性）
            if actor.actor_type == "human":
                head_r = max(2, round(bw * 0.25))
                cv2.circle(canvas, (round(cx), y1), head_r, color, thickness=-1)
        frames.append(canvas)
    return frames


def export_mp4(
    scenario: Scenario,
    frames: list[np.ndarray],
    path: str | Path,
    fps: float | None = None,
) -> Path:
    """将渲染帧写出为 MP4（仅本地人工检视，**不入库**）。

    ``frames`` 通常由 ``render_frames`` 产出；``fps`` 缺省用 ``scenario.camera.fps``。
    """
    if not frames:
        raise ValueError("frames 为空，无法写出 MP4")
    fps = fps or scenario.camera.fps or 2.0
    h, w = frames[0].shape[:2]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(p), fourcc, float(fps), (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"无法打开 VideoWriter: {p!r}")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()
    return p

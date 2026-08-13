"""ADR-0035 D3 · EvidenceProjection adapter（阶段 1–2 复用入口 · D3-12 Projection View）。

D3-12 证据所有权边界：**D3 不拥有 EvidenceGraph，只拥有 Projection View**。
本模块只做只读投影消费（复用 ``visualizer.loader``），**绝不新建 EvidenceNode/EvidenceEdge**，
可缓存 NarrativePlan/Storyboard/VisualSceneGraph（在下游模块）。

背景层（VisualComposer 的 background layer）采用可插拔 ``BackgroundProvider``：
- 默认 ``SyntheticBackgroundProvider``：确定性灰底（零 validation 依赖、离线、确定性）；
- 可选 ``ValidationBackgroundProvider``：复用 ADR-0032 ``render_frames``（D3-1 单向例外），
  延迟 import ``home_perception.validation``，仅读取、不触发验证判定。

见设计文档 §1（复用面）、§2.1（阶段 1–2）、§9 D3-12。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from home_perception.visualizer.loader import load_evidence_projection


def load_scenario_evidence(artifact_dir: Path, scenario_id: str) -> dict:
    """只读投影：从 EvidenceProjection 取指定 scenario 的完整投影（含 graph）。

    D3-12：直接消费 loader 产物，不重写事实层。找不到 scenario → KeyError（fail-closed）。
    """
    projection = load_evidence_projection(artifact_dir)
    for scenario in projection["scenarios"]:
        if scenario["scenario_id"] == scenario_id:
            return scenario
    available = [s["scenario_id"] for s in projection["scenarios"]]
    raise KeyError(f"artifact_dir 不含 scenario_id={scenario_id!r}；可用：{available}")


@runtime_checkable
class BackgroundProvider(Protocol):
    """背景层提供者协议（逐 shot 产出 BGR 帧序列）。"""

    def generate(self, shot_name: str, n_frames: int, resolution: tuple[int, int]) -> list[np.ndarray]:
        """返回 ``n_frames`` 张 ``(h, w, 3)`` BGR uint8 帧（确定性）。"""
        ...


class SyntheticBackgroundProvider:
    """确定性合成灰底背景（D3-A 默认 · 零 validation 依赖 · 离线确定性）。

    灰阶由 shot_name 确定性派生（**不**使用 ``hash()``，避免 PYTHONHASHSEED 抖动）。
    """

    def generate(self, shot_name: str, n_frames: int, resolution: tuple[int, int]) -> list[np.ndarray]:
        width, height = resolution
        gray = 30 + (sum(ord(c) for c in shot_name) % 18)
        frame = np.full((height, width, 3), gray, dtype=np.uint8)
        return [frame.copy() for _ in range(n_frames)]


class ValidationBackgroundProvider:
    """复用 ADR-0032 ``render_frames`` 的背景（D3-1 单向例外 · 可选）。

    延迟 import ``home_perception.validation``（仅本方法内），保证模块导入不强制拉入
    validation；仅读取、不触发验证判定、不反向依赖生产决策。
    """

    def __init__(self, scenario) -> None:
        self._scenario = scenario

    def generate(self, shot_name: str, n_frames: int, resolution: tuple[int, int]) -> list[np.ndarray]:
        from home_perception.validation.simulation.renderer import render_frames

        width, height = resolution
        rendered = render_frames(self._scenario)
        if not rendered:
            return SyntheticBackgroundProvider().generate(shot_name, n_frames, resolution)
        # 确定性伸缩到目标分辨率 + 时长（裁剪/循环，无随机）。
        resized = [_resize_bgr(frame, width, height) for frame in rendered]
        return _fit_length(resized, n_frames)


def _resize_bgr(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    import cv2

    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def _fit_length(frames: list[np.ndarray], n_frames: int) -> list[np.ndarray]:
    if not frames:
        return frames
    if len(frames) >= n_frames:
        return frames[:n_frames]
    return frames + frames[: n_frames - len(frames)]


__all__ = [
    "BackgroundProvider",
    "SyntheticBackgroundProvider",
    "ValidationBackgroundProvider",
    "load_scenario_evidence",
]

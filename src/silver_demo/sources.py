"""Demo 帧源（P0-11.3 · 真实视频输入适配）。

提供可替换的帧源实现，证明冻结架构（ADR-0014 L2 FrameSource 契约）允许外部输入替换：
- ``CaviarJpgFrameSource``：包裹 ``home_perception.runtime.config.read_caviar_frames``（CAVIAR 工程验证）
- ``VideoFileFrameSource``：真实监控 MP4（产品展示层输入）

两者均产出 ``(timestamp, frame)`` 流，网关消费统一的 ``DemoFrameSource`` 抽象，
**Dashboard / Pipeline / WarningEvent 零改动**。

冻结合规：本模块仅 import 白名单内的 ``home_perception.runtime.config``，
**不** import 冻结包内的帧源实现模块（FrameSource 所在子模块），避免穿透冻结包内部。
``DemoFrameSource`` 与冻结 ``FrameSource`` 接口"结构一致、各自独立"——正是 ADR-0014
「实现可替换」在消费者侧的体现：消费者可自由提供自己的输入源，无需改动 Pipeline / 展示层。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator, Tuple

import cv2

from home_perception.runtime.config import read_caviar_frames

from .scenarios import ScenarioConfig


class DemoFrameSource(ABC):
    """Demo 帧源抽象（与冻结 ``FrameSource`` 接口结构一致：``__iter__`` 产出 ``(ts, frame)``）。

    不继承冻结包内的 ``FrameSource``（避免 ``silver_demo`` 依赖冻结包内部帧源实现模块），
    而是结构一致的独立抽象——消费者提供自己的输入源，无需耦合冻结包内部。
    """

    frame_count: int = -1  # -1 表示未知（流式 / 未探测）

    @abstractmethod
    def __iter__(self) -> Iterator[Tuple[float, Any]]:
        """产出 ``(timestamp: float, frame)`` 元组流。"""
        ...

    def reset(self) -> None:
        """重置迭代位置（默认无操作；MP4 源在 ``__iter__`` 中重新打开文件实现重放）。"""
        return


class CaviarJpgFrameSource(DemoFrameSource):
    """CAVIAR 抽帧 JPG 目录帧源（工程验证用）。

    包裹 ``read_caviar_frames``；``__iter__`` 每次从头产出缓存的帧列表（支持 loop 重放）。
    """

    def __init__(
        self,
        base_dir: str,
        scenario: str,
        fps_target: int = 2,
        frame_glob: str = "frame_*.jpg",
    ) -> None:
        self._frames = read_caviar_frames(base_dir, scenario, frame_glob)
        self.fps_target = fps_target
        self.frame_count = len(self._frames)

    def __iter__(self) -> Iterator[Tuple[float, Any]]:
        for frame in self._frames:
            yield time.time(), frame


class VideoFileFrameSource(DemoFrameSource):
    """真实监控 MP4 帧源（产品展示层输入 · P0-11.3）。

    通过 ``cv2.VideoCapture`` 读取 MP4，按 ``fps_target`` 限速产出 ``(timestamp, frame)``。
    每次 ``__iter__`` 重新打开文件（支持 loop 重放）；文件缺失则在迭代首帧抛 ``RuntimeError``。

    定位（ADR-0015 §2.6 调整）：替代 CAVIAR 作为「银龄盾场景价值」展示输入，
    验证「真实场景输入 → 工业级架构 → 风险闭环」（CAVIAR 仍用于工程链路验证）。
    """

    def __init__(
        self,
        path: str,
        fps_target: int = 8,
        max_retries: int = 3,
        backoff_s: float = 1.0,
    ) -> None:
        self.path = path
        self.fps_target = fps_target
        self.interval = 1.0 / fps_target if fps_target > 0 else 0.0
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        # 探测帧数（仅取元数据，不读全片；文件缺失则 frame_count = -1）
        self.frame_count = -1
        if Path(path).is_file():
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                # NaN 守卫：部分封装下 CAP_PROP_FRAME_COUNT 返回 NaN
                self.frame_count = int(n) if n == n and n > 0 else -1
                cap.release()

    def __iter__(self) -> Iterator[Tuple[float, Any]]:
        if not Path(self.path).is_file():
            raise RuntimeError(f"视频文件不存在: {self.path!r}（P0-11.3 真实输入源）")
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"无法打开视频: {self.path!r}")
        last = 0.0
        retries = 0
        try:
            while True:
                now = time.time()
                if self.interval > 0 and (now - last) < self.interval:
                    time.sleep(max(0.0, self.interval - (now - last)))
                ret, frame = cap.read()
                if not ret:
                    if retries >= self.max_retries:
                        break
                    retries += 1
                    time.sleep(self.backoff_s)
                    continue
                retries = 0
                last = time.time()
                yield last, frame
        finally:
            cap.release()


def build_frame_source(scenario: ScenarioConfig, hp_settings: Any) -> DemoFrameSource:
    """按场景配置构造 ``DemoFrameSource``（网关消费入口）。

    - ``source_type == "video_file"`` → ``VideoFileFrameSource``（真实 MP4 产品展示）
    - 其他（默认 ``caviar_jpg``）→ ``CaviarJpgFrameSource``（CAVIAR 工程验证）

    这是 P0-11.3 的核心替换点：切换输入源只需改 ``source_type`` / ``media_path``，
    Dashboard / Pipeline / WarningEvent 完全不变。
    """
    source_type = getattr(scenario, "source_type", "caviar_jpg")
    if source_type == "video_file":
        media_path = getattr(scenario, "media_path", None)
        if not media_path:
            raise ValueError(
                f"video_file 源需要 media_path，场景 {scenario.scenario_id!r} 缺失"
            )
        return VideoFileFrameSource(str(media_path), fps_target=scenario.fps_target)

    # 默认 CAVIAR jpg 目录
    base_dir = hp_settings.runtime.caviar_base_dir
    frame_glob = getattr(hp_settings.runtime, "frame_glob", "frame_*.jpg")
    return CaviarJpgFrameSource(
        str(base_dir),
        scenario.source,
        fps_target=scenario.fps_target,
        frame_glob=frame_glob,
    )

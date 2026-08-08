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

import math
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from home_perception.runtime.config import read_caviar_frames

from .scenarios import ScenarioConfig


class Source:
    """轻量源抽象（P0-11.3.5 P1）：包裹 ``DemoFrameSource``，提供 ``load`` / ``__iter__`` 统一入口。

    证明 Demo **不绑定 MP4**——消费者（网关）只依赖 ``Source`` 接口，具体实现
    （CAVIAR jpg / 真实 MP4 / 未来 RTSP / EZVIZ）由 ``build_frame_source`` 分发。

    本类**不做** RTSP / EZVIZ 设备协议（留 P0-12），仅为输入源切换提供一致的表面；
    复用既有 ``DemoFrameSource`` ABC，零新增冻结依赖。
    """

    def __init__(self) -> None:
        self._inner: DemoFrameSource | None = None
        self.scenario: ScenarioConfig | None = None
        self.frame_count: int = -1

    def load(self, scenario: ScenarioConfig, hp_settings: Any) -> None:
        """按场景配置装载底层帧源（CAVIAR jpg / 真实 MP4 等）。"""
        self.scenario = scenario
        self._inner = build_frame_source(scenario, hp_settings)
        self.frame_count = self._inner.frame_count

    def __iter__(self) -> Iterator[tuple[float, Any]]:
        """产出 ``(timestamp, frame)`` 元组流（委托底层帧源）。"""
        if self._inner is None:
            raise RuntimeError("Source 未 load，请先调用 load(scenario, hp_settings)")
        return iter(self._inner)


class DemoFrameSource(ABC):
    """Demo 帧源抽象（与冻结 ``FrameSource`` 接口结构一致：``__iter__`` 产出 ``(ts, frame)``）。

    不继承冻结包内的 ``FrameSource``（避免 ``silver_demo`` 依赖冻结包内部帧源实现模块），
    而是结构一致的独立抽象——消费者提供自己的输入源，无需耦合冻结包内部。
    """

    frame_count: int = -1  # -1 表示未知（流式 / 未探测）

    @abstractmethod
    def __iter__(self) -> Iterator[tuple[float, Any]]:
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
        scenario_source: str,
        fps_target: int = 2,
        frame_glob: str = "frame_*.jpg",
    ) -> None:
        self._frames = read_caviar_frames(base_dir, scenario_source, frame_glob)
        self.fps_target = fps_target
        self.frame_count = len(self._frames)

    def __iter__(self) -> Iterator[tuple[float, Any]]:
        for frame in self._frames:
            yield time.time(), frame


class VideoFileFrameSource(DemoFrameSource):
    """真实监控 MP4 帧源（产品展示层输入 · P0-11.3）。

    通过 ``cv2.VideoCapture`` 读取 MP4，按 ``fps_target`` **跳帧**产出 ``(timestamp, frame)``：
    ``skip = round(src_fps / fps_target)``，仅产出每 ``skip`` 帧中的第 1 帧，
    使产出帧数 = ``ceil(total / skip)``（而非全帧读取），现场播放接近实时
    （修复「逐帧全读导致 3x 慢放」）。

    **限速（帧节奏）不在本类内做**——由网关 ``run_loop`` 的 ``await asyncio.sleep(interval)``
    统一控制（见 gateway.py）。原因：
    - 本类 ``__iter__`` 是同步迭代器，内部 ``time.sleep`` 会阻塞 asyncio 事件循环，
      冻住 WebSocket 广播 / 上行 action / ``/health`` 响应；
    - 两帧源行为一致（``CaviarJpgFrameSource`` 同样不做内部限速）；
    - 有效帧率 = 配置值，不减半（否则源内 + 网关双重限速 ≈ 0.5x）。

    cv2 延迟导入（仅本类需要），避免 ``CaviarJpgFrameSource`` 单独部署在无 cv2 环境时
    导入本模块即报 ``ImportError``。每次 ``__iter__`` 重新打开文件（支持 loop 重放）；
    文件缺失则在迭代首帧抛 ``RuntimeError``。

    定位（ADR-0015 §2.6 调整）：替代 CAVIAR 作为「银龄盾场景价值」展示输入，
    验证「真实场景输入 → 工业级架构 → 风险闭环」（CAVIAR 仍用于工程链路验证）。
    """

    def __init__(
        self,
        path: str,
        fps_target: int = 8,
        max_retries: int = 3,
    ) -> None:
        import cv2  # 延迟导入：仅本类需要 OpenCV

        self._cv2 = cv2
        self.path = path
        self.fps_target = fps_target
        self.max_retries = max_retries
        # 跳帧步长：使产出帧数 ≈ total / skip（读每 skip 帧产出 1 帧）。
        # 仅取元数据计算，不读全片；文件缺失则 _skip=1、frame_count=-1。
        self._skip = 1
        self.frame_count = -1
        if Path(path).is_file():
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
                total = int(n) if not math.isnan(n) and n > 0 else 0  # NaN 守卫
                if self.fps_target > 0 and src_fps > 0:
                    self._skip = max(1, round(src_fps / self.fps_target))
                # 产出帧数 = ceil(total / skip)
                self.frame_count = (total + self._skip - 1) // self._skip if total > 0 else -1
                cap.release()

    def __iter__(self) -> Iterator[tuple[float, Any]]:
        if not Path(self.path).is_file():
            raise RuntimeError(f"视频文件不存在: {self.path!r}（P0-11.3 真实输入源）")
        cap = self._cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"无法打开视频: {self.path!r}")
        skip = self._skip
        idx = 0
        retries = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    if retries >= self.max_retries:
                        break
                    retries += 1
                    # 不在此 sleep：本类 ``__iter__`` 是同步迭代器，被网关 ``run_loop`` 的
                    # ``next()`` 在 asyncio 事件循环内同步调用；sleep 会阻塞事件循环
                    # （冻住 WS 广播 / 上行 action / ``/health`` 响应，最多 3s）。
                    # 改为立即重试，最多 ``max_retries`` 次后判定 EOF 退出（与正常 EOF 一致）。
                    continue
                retries = 0
                idx += 1
                # 跳帧：仅产出每 skip 帧中的第 1 帧（帧索引 0, skip, 2*skip, ...）
                if (idx - 1) % skip != 0:
                    continue
                # 限速由网关层 await asyncio.sleep 统一控制（不在此 sleep，避免阻塞事件循环）
                yield time.time(), frame
        finally:
            cap.release()


# ============================================================================
# 外部帧源注册表（ADR-0032 Slice E · 依赖倒置接缝）
# ============================================================================

FrameSourceBuilder = Callable[[ScenarioConfig, Any], "DemoFrameSource"]

#: 内建 source_type，**不可**被外部注册覆盖（防止核心输入路径被劫持）。
BUILTIN_SOURCE_TYPES = frozenset({"video_file", "caviar_jpg"})

#: source_type → builder。由**组装层**（如 ``scripts/run_demo.py``）填充。
_SOURCE_BUILDERS: dict[str, FrameSourceBuilder] = {}


def register_frame_source(
    source_type: str, builder: FrameSourceBuilder, *, replace: bool = False
) -> None:
    """注册一个外部 ``source_type`` 的帧源构造器。

    这是 ADR-0032 Slice E 的**依赖倒置接缝**：``silver_demo`` 不 import 任何
    合成/验证层模块，而是暴露此钩子，由组装层把 builder 注入进来。因此
    ADR-0015 §5 的冻结 import 白名单**无需放宽**。

    builder 只需返回结构上满足 ``DemoFrameSource`` 的对象（``frame_count``
    属性 + ``__iter__`` 产出 ``(timestamp, frame)``），无需继承本模块的 ABC
    ——与 ``DemoFrameSource`` 相对冻结 ``FrameSource`` 的"结构一致、各自独立"
    是同一原则，避免反向依赖。

    :param replace: 是否允许覆盖**已注册的外部**类型（默认拒绝，避免静默顶替）。
    :raises ValueError: 试图覆盖内建类型，或未开 ``replace`` 却重复注册。
    """
    if source_type in BUILTIN_SOURCE_TYPES:
        raise ValueError(
            f"source_type {source_type!r} 是内建类型，禁止注册覆盖"
            f"（内建：{sorted(BUILTIN_SOURCE_TYPES)}）"
        )
    if source_type in _SOURCE_BUILDERS and not replace:
        raise ValueError(f"source_type {source_type!r} 已注册；如需顶替请显式传 replace=True")
    _SOURCE_BUILDERS[source_type] = builder


def unregister_frame_source(source_type: str) -> None:
    """注销一个外部帧源构造器（不存在时静默忽略，便于测试清理）。"""
    _SOURCE_BUILDERS.pop(source_type, None)


def registered_source_types() -> frozenset[str]:
    """返回当前已注册的**外部** source_type 集合（只读快照）。"""
    return frozenset(_SOURCE_BUILDERS)


def build_frame_source(scenario: ScenarioConfig, hp_settings: Any) -> DemoFrameSource:
    """按场景配置构造 ``DemoFrameSource``（网关消费入口）。

    - ``source_type == "video_file"`` → ``VideoFileFrameSource``（真实 MP4 产品展示）
    - 命中外部注册表 → 该 builder（ADR-0032 Slice E，如 ``synthetic``）
    - 其他（默认 ``caviar_jpg``）→ ``CaviarJpgFrameSource``（CAVIAR 工程验证）

    这是 P0-11.3 的核心替换点：切换输入源只需改 ``source_type`` / ``media_path``，
    Dashboard / Pipeline / WarningEvent 完全不变。

    .. note::
       未知 ``source_type`` 仍沿用既有的 CAVIAR 兜底行为（**未**改为报错），
       以保证本次接缝是纯加法、对既有场景零行为变化。
    """
    source_type = scenario.source_type
    if source_type == "video_file":
        media_path = scenario.media_path
        if not media_path:
            raise ValueError(f"video_file 源需要 media_path，场景 {scenario.scenario_id!r} 缺失")
        return VideoFileFrameSource(str(media_path), fps_target=scenario.fps_target)

    # 外部注册的 source_type（ADR-0032 Slice E：synthetic 等）
    builder = _SOURCE_BUILDERS.get(source_type)
    if builder is not None:
        return builder(scenario, hp_settings)

    # 默认 CAVIAR jpg 目录
    base_dir = hp_settings.runtime.caviar_base_dir
    frame_glob = getattr(hp_settings.runtime, "frame_glob", "frame_*.jpg")
    return CaviarJpgFrameSource(
        str(base_dir),
        scenario_source=scenario.source,
        fps_target=scenario.fps_target,
        frame_glob=frame_glob,
    )

"""Lifecycle Management · 可复用骨架（示例）。

来源：Silver Shield `gateway.py` 的 DemoGateway 生命周期逻辑提炼。
**不是银龄盾代码**，是抽取的模式骨架。新项目据此类推。

核心思想：
- assemble 加载模型（昂贵，一次）；run_loop 步进；reset 只清状态不重载模型。
- ``_rebuild_pipeline`` 复用已加载 detector，清空跨帧状态，保证循环/切换后确定性复现。
- 重置经 ``switch_source(同场景)`` 复用同一路径，广播 reset 事件让前端清空本地累积。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional


class SessionStatus:
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RESETTING = "RESETTING"
    STOPPED = "STOPPED"


class RuntimeSession:
    """演示/实时系统的运行时生命周期管理。

    职责边界：
    - 持有 pipeline 与已加载模型（detector）。
    - 提供 run_loop / stop / close / reset。
    - reset 复用模型、清空跨帧状态，保证可重复运行。
    """

    def __init__(self) -> None:
        self.pipeline: Optional["Pipeline"] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.frame_index = 0
        self.loop_count = 0
        self.session_status = SessionStatus.CREATED
        self.store: dict = {}          # 演示级状态（闭环/确认）
        self.aggregate: dict = {}      # 聚合状态（warnings/behaviors/commands）

    # ------------------------------------------------------------------
    # 装配（昂贵，只做一次）
    # ------------------------------------------------------------------
    def assemble(self, scenario: Any) -> None:
        # 真实项目：PerceptionPipeline.from_settings(...) + load_detector()
        self.pipeline = _build_pipeline(scenario)   # 内含已加载 detector
        self.session_status = SessionStatus.RUNNING

    # ------------------------------------------------------------------
    # 帧循环（后台 task）
    # ------------------------------------------------------------------
    async def run_loop(self) -> None:
        if self.pipeline is None:
            raise RuntimeError("未装配，请先 assemble()")
        self._running = True
        frame_iter = iter(self.pipeline.frames)
        while self._running:
            try:
                frame = next(frame_iter)
            except StopIteration:
                if getattr(self.pipeline, "loop", False):
                    self.loop_count += 1
                    self._rebuild_pipeline()      # 清空跨帧状态，复用模型
                    self.frame_index = 0
                    frame_iter = iter(self.pipeline.frames)
                    continue
                break
            self.pipeline.process_frame(frame, self.frame_index)
            self.frame_index += 1
            await asyncio.sleep(0)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()
        if self.pipeline is not None:
            self.pipeline.close()
            self.pipeline = None
        self.session_status = SessionStatus.STOPPED

    # ------------------------------------------------------------------
    # 重建状态组件（复用模型，清空跨帧累积）
    # ------------------------------------------------------------------
    def _rebuild_pipeline(self) -> None:
        """复用已加载 detector，清空追踪/窗口/规则/决策等跨帧状态。"""
        detector = self.pipeline.detector          # 复用，避免重载权重
        self.pipeline = _build_pipeline(self.pipeline.scenario, detector=detector)
        self._clear_session_state()

    def _clear_session_state(self) -> None:
        self.store = {}
        self.aggregate = {}
        self.frame_index = 0
        self.loop_count = 0

    # ------------------------------------------------------------------
    # Reset（切换同场景 = 清空状态重跑）
    # ------------------------------------------------------------------
    async def reset(self) -> dict:
        self.session_status = SessionStatus.RESETTING
        self.stop()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._rebuild_pipeline()                    # 复用模型 + 清空状态
        self._task = asyncio.create_task(self.run_loop())
        self.session_status = SessionStatus.RUNNING
        return {"frame_index": 0, "loop_count": 0, "session_status": self.session_status}


# ---- 以下为占位实现，真实项目替换为实际 pipeline ----
class Pipeline:
    loop = True

    def __init__(self, scenario, detector=None):
        self.scenario = scenario
        self.detector = detector
        self.frames = range(10)

    def process_frame(self, frame, frame_index): ...
    def close(self): ...


def _build_pipeline(scenario, detector=None) -> Pipeline:
    return Pipeline(scenario, detector=detector)

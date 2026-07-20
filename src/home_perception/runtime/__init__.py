"""运行期装配层（P0-10 · 装配联调）。

把 P0-3~P0-9 已验证组件装配成可运行 Demo（CAVIAR 复现）。
边界：只解决"怎么启动系统"的工程问题，不验证逻辑正确性（已由 P0 Integration Validation 验证）。
"""
from __future__ import annotations

from .config import (
    build_dispatcher_config,
    build_family_contact,
    build_threshold_config,
    read_caviar_frames,
)
from .lifecycle import run_demo
from .observability import PipelineMetrics
from .pipeline import DemoClock, FrameResult, PerceptionPipeline, RunSummary

__all__ = [
    # 装配器
    "PerceptionPipeline",
    "FrameResult",
    "RunSummary",
    "DemoClock",
    # 指标
    "PipelineMetrics",
    # 配置转换
    "build_threshold_config",
    "build_dispatcher_config",
    "build_family_contact",
    "read_caviar_frames",
    # 生命周期 / Demo
    "run_demo",
]

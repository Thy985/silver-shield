"""ADR-0034：闭环集成验证层（Scenario → Runtime → Memory → Decision → Notification）。

**它验证什么**：ADR-0032 给了可复现的输入（Scenario），ADR-0033 给了感知层打分
（Benchmark）。但"感知层准不准"回答不了"这套系统端到端到底有没有把事办成"——事件产生了、
决策却没跑，或者告警发了、通知没发出去，在既有度量里全都是**绿的**。本包补上的正是这段
观测：把闭环每一跳的**存在性**变成断言，让静默丢弃无处藏身（§0.4 F1–F6）。

**为什么落在 ``integration/loop/`` 而不是 ``integration/``**：``integration/`` 已被生产占用
（``audio_adapter`` 被 ``runtime.audio_session_recorder`` 引用，且其 ``__init__`` 急切
import）。若把本层平铺进去，评估侧模块会与生产适配器混居在同一个命名空间，T2
（"评估层不得进入生产链路"）的边界将无法用 allowlist 机械守护。独立子包让边界回到
一行路径前缀：``src/home_perception/integration/loop/``。

**加载期零急切 import（铁律，勿改）**：本 ``__init__`` 不在加载期 import 任何子模块。
``runner`` 依赖 ``validation``、``validator`` 依赖 ``evaluation``，而 ``evaluation`` 已经
处在 ``validation.scenario → evaluation → validation`` 这条随时会成环的链上；急切 import
会把本包也拖进环里。与 ``evaluation/__init__.py`` 同款，用 **PEP 562 ``__getattr__``**
延迟转发：``from home_perception.integration.loop import IntegrationRunner`` 照常可用，
但只在**首次访问属性**时才真正加载子模块。
"""

from __future__ import annotations

import importlib

# 公开符号 → 所属子模块（延迟转发，避免加载期急切 import 触发环路）。
_PUBLIC_MODULES: dict[str, str] = {
    # D2：探针容器与配置
    "DEFAULT_CLOCK_START": "home_perception.integration.loop.context",
    "IntegrationConfigError": "home_perception.integration.loop.context",
    "IntegrationContext": "home_perception.integration.loop.context",
    "IntegrationRunnerConfig": "home_perception.integration.loop.context",
    "MEMORY_BACKENDS": "home_perception.integration.loop.context",
    "SINK_KINDS": "home_perception.integration.loop.context",
    "TRACE_RECORDER_KINDS": "home_perception.integration.loop.context",
    # D2：编排与产物
    "IntegrationRunner": "home_perception.integration.loop.runner",
    "IntegrationRunResult": "home_perception.integration.loop.runner",
    # D5：判定与失败归类
    "FAILURE_CODES": "home_perception.integration.loop.validator",
    "STAGE_NAMES": "home_perception.integration.loop.validator",
    "IntegrationValidationResult": "home_perception.integration.loop.validator",
    "IntegrationValidator": "home_perception.integration.loop.validator",
    "StageResult": "home_perception.integration.loop.validator",
    "classify_failure": "home_perception.integration.loop.validator",
    # D7：报告与落盘守卫
    "IntegrationReport": "home_perception.integration.loop.report",
    "LoopArtifactSummary": "home_perception.integration.loop.report",
}

# 显式字面量（``__all__`` 不能是表达式，见 PLE0605）。与 ``_PUBLIC_MODULES`` 的键集必须
# 一致——两者一旦漂移，就会出现"能 import 但不在 ``__all__``"或反之的幽灵符号；契约测试
# 用 ``set(__all__) == set(_PUBLIC_MODULES)`` 守护。
__all__ = [
    "DEFAULT_CLOCK_START",
    "FAILURE_CODES",
    "MEMORY_BACKENDS",
    "SINK_KINDS",
    "STAGE_NAMES",
    "TRACE_RECORDER_KINDS",
    "IntegrationConfigError",
    "IntegrationContext",
    "IntegrationReport",
    "IntegrationRunResult",
    "IntegrationRunner",
    "IntegrationRunnerConfig",
    "IntegrationValidationResult",
    "IntegrationValidator",
    "LoopArtifactSummary",
    "StageResult",
    "classify_failure",
]


def __getattr__(name: str):
    """PEP 562：仅在属性缺失时延迟转发到对应子模块（零加载期依赖）。"""
    target = _PUBLIC_MODULES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target)
    return getattr(module, name)


def __dir__() -> list[str]:
    """让 ``dir()`` / 补全能看见延迟符号（否则本包看起来像个空模块）。"""
    return [*__all__, "__all__", "__getattr__", "__dir__"]

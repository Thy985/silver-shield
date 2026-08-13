"""ADR-0035 D3 · Evidence Story Compiler 子包（visualizer/video/）。

公开 API：``CaseVideoSpec``（编排配置）/ ``generate_case_video``（8 阶段驱动）/
``CaseVideoResult``（产出摘要）。具体渲染/合成细节见各子模块。

**惰性导入（PEP 562）**：本包加载期零急切 import。原实现在 ``__init__`` 顶层
``from …compiler import generate_case_video``，导致「仅 import 本包」就会连带拉入
cv2 / PIL / numpy 整条渲染栈——与 ``scripts/generate_case_video.py`` 刻意延迟 import
的设计自相矛盾（CLI 只想解析参数时也会付重依赖代价）。此处改为 ``__getattr__``
延迟转发（与仓库 ``evaluation/__init__.py`` 同范式），保持公开 API 不变。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查期可见，运行时零导入
    from home_perception.visualizer.video.compiler import CaseVideoResult, generate_case_video
    from home_perception.visualizer.video.spec import CaseVideoSpec

# 公开名 → 实现模块（延迟解析）。
_LAZY_TARGETS: dict[str, str] = {
    "CaseVideoResult": "home_perception.visualizer.video.compiler",
    "generate_case_video": "home_perception.visualizer.video.compiler",
    "CaseVideoSpec": "home_perception.visualizer.video.spec",
}


def __getattr__(name: str) -> Any:
    """PEP 562 惰性转发：按需 import 实现模块，避免加载期拉入重依赖。"""
    target = _LAZY_TARGETS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(set(_LAZY_TARGETS) | set(globals()))


__all__ = [
    "CaseVideoResult",
    "CaseVideoSpec",
    "generate_case_video",
]

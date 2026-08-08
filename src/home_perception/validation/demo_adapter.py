"""Demo 合成帧源适配层（ADR-0032 Slice E）。

把 ``Scenario`` 渲染出的程序化 BGR 帧，适配成 ``silver_demo`` 网关可消费的帧源。

依赖方向（**关键**）
--------------------
本模块**不 import** ``silver_demo`` 的任何东西，``silver_demo`` 也**不 import**
本模块。两侧靠 ``silver_demo.sources.register_frame_source`` 这个钩子在**组装层**
（如 ``scripts/run_demo.py``）汇合::

    组装层 ──register──> silver_demo.sources._SOURCE_BUILDERS
       │
       └──import──> home_perception.validation.demo_adapter

这样做的理由：ADR-0032 原文（§Slice E）设想的是 ``build_frame_source`` 里直接
调 ``render_frames``，但那会让 ``silver_demo`` 直接 import
``home_perception.validation``，与 ADR-0015 §5 的冻结 import 白名单冲突
（``tests/demo/test_freeze_boundary.py::ALLOWED_HP_IMPORTS`` 只有 5 项）。
改用依赖倒置后：

- 冻结白名单**无需放宽**，ADR-0015 §5 边界原样保住；
- ``silver_demo`` 侧是**纯加法**——不注册时 ``build_frame_source`` 行为与今天完全一致；
- 反向依赖（``home_perception`` import ``silver_demo``）也被避免，因为本模块
  只产出**结构上**满足 ``DemoFrameSource`` 的鸭子类型对象，不继承其 ABC。

torch-free
----------
本模块只从 ``.simulation.renderer`` 取 ``render_frames``、从
``.scenario.scenario`` 取 ``load_scenario``，**刻意绕开**两个子包的
``__init__``（它们会连带拉起 ``generator`` / ``compiler``）。这让
``scripts/run_demo.py`` 的"先环境预检、后懒加载"次序不被破坏。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 直连叶子模块，绕开子包 __init__（见模块文档字符串 "torch-free" 一节）
from .scenario.scenario import load_scenario
from .simulation.renderer import render_frames

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    import numpy as np

    from .scenario.scenario import Scenario

#: 组装层注册本适配器时约定使用的 ``source_type``。
SYNTHETIC_SOURCE_TYPE = "synthetic"


class SyntheticFrameSource:
    """由 ``Scenario`` 程序化渲染的帧源（鸭子类型，不继承 demo 侧 ABC）。

    与 ``CaviarJpgFrameSource`` 行为对齐：

    - 帧在构造期一次性渲染并缓存；
    - ``frame_count`` 为帧总数；
    - ``__iter__`` 每次都从头产出，支持网关 ``loop`` 重放；
    - 时间戳用 ``time.time()`` 墙钟（与两个内建源一致；模拟时间由网关
      ``DemoClock`` 独立推进，不由帧源决定）。

    限速同样**不在本类内做**——与既有帧源一致，由网关 ``run_loop`` 统一 await。
    """

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self._frames: list[np.ndarray] = render_frames(scenario)
        self.frame_count: int = len(self._frames)

    def __iter__(self) -> Iterator[tuple[float, Any]]:
        for frame in self._frames:
            yield time.time(), frame

    def reset(self) -> None:
        """重置迭代位置（帧已全量缓存，``__iter__`` 天然从头，故无操作）。"""
        return


def _resolve_scenario_path(raw: str) -> Path:
    """把配置里的 scenario_path 解析为绝对路径（相对路径按仓库根解释）。"""
    p = Path(raw)
    if p.is_absolute():
        return p
    # 本文件位于 src/home_perception/validation/ → 上溯三层为仓库根
    return Path(__file__).resolve().parents[3] / p


def build_synthetic_frame_source(scenario_config: Any, hp_settings: Any) -> SyntheticFrameSource:
    """``silver_demo`` 帧源 builder：从 demo 场景配置构造合成帧源。

    读取 ``ScenarioConfig.synthetic`` 字典（demo 侧刻意不解释其语义）::

        synthetic:
          scenario_path: src/home_perception/validation/fixtures/scenarios/perception/torchfree_visit.yaml

    :param scenario_config: demo 侧的 ``ScenarioConfig``（此处按鸭子类型消费，
        不 import 其类型，以免形成反向依赖）。
    :param hp_settings: 网关透传的 home_perception Settings；本合成源不需要它
        （几何与外观全部来自 ``Scenario`` 自身），保留形参只为契合 builder 签名。
    :raises ValueError: ``synthetic`` 缺失或未给 ``scenario_path``。
    :raises FileNotFoundError: ``scenario_path`` 指向的文件不存在。
    """
    del hp_settings  # 显式声明不消费，避免读者误以为遗漏

    spec = getattr(scenario_config, "synthetic", None)
    scenario_id = getattr(scenario_config, "scenario_id", "<unknown>")
    if not spec:
        raise ValueError(
            f"synthetic 源需要 ScenarioConfig.synthetic 配置，场景 {scenario_id!r} 缺失"
        )

    raw_path = spec.get("scenario_path")
    if not raw_path:
        raise ValueError(
            f"synthetic.scenario_path 未配置，场景 {scenario_id!r} 无法定位 Scenario YAML"
        )

    path = _resolve_scenario_path(str(raw_path))
    if not path.is_file():
        raise FileNotFoundError(f"synthetic.scenario_path 不存在：{path}（场景 {scenario_id!r}）")

    return SyntheticFrameSource(load_scenario(path))


def install_into(
    register_frame_source: Callable[..., None],
    *,
    source_type: str = SYNTHETIC_SOURCE_TYPE,
    replace: bool = False,
) -> None:
    """把合成帧源 builder 注册进 demo 侧注册表（供组装层一行接线）。

    典型用法（在 ``scripts/run_demo.py`` 这类组装层）::

        from silver_demo.sources import register_frame_source
        from home_perception.validation.demo_adapter import install_into

        install_into(register_frame_source)

    参数以**函数**形式传入而非 import demo 模块，是为了保持本模块对
    ``silver_demo`` 的零依赖（见模块文档字符串）。
    """
    register_frame_source(source_type, build_synthetic_frame_source, replace=replace)

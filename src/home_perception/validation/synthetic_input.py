"""ADR-0032：``SyntheticInput`` —— 编译产物载体（**依赖图叶子**）。

为什么单独成模块而不是放在 ``runner`` 或 ``scenario.compiler`` 里：
``compiler``（产出方）与 ``runner``（消费方）都需要它，而 ``compiler`` 位于
``scenario`` 子包、``runner`` 又需要 ``scenario`` 的类型 —— 任何一侧持有定义都会形成
``scenario → runner → scenario`` 的导入环（该环只在特定导入顺序下才触发，属于潜伏故障）。

本模块**没有任何运行期包内导入**（``Scenario`` / ``np`` 仅用于类型注解，靠
``from __future__ import annotations`` 延迟求值），因此可被任意子包安全导入，
不参与环路。相应的回归测试见 ``tests/validation/test_validation_imports.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型，零运行期依赖（断环的关键）
    import numpy as np

    from .scenario.scenario import Scenario


@dataclass
class SyntheticInput:
    """``ScenarioCompiler`` 的输出：喂给 pipeline 的**单通道**受控输入（D1）。

    两通道互斥：
    - ``mode == "detections"``：``detector`` 为内嵌逐帧检测缓存的 detector，``frames is None``；
    - ``mode == "frames"``    ：``frames`` 为程序化 BGR 帧序列，``detector is None``。
    """

    scenario_id: str
    mode: str
    detector: Any | None
    frames: list[np.ndarray] | None
    n_frames: int
    fingerprint: str
    seed: int
    scenario: Scenario
    # ADR-0034 Phase B.2：编译出的音频感知事件（供 loop 驱动 AudioSessionRecorder）。
    # 缺省空元组 = 无音频会话。与视觉 ``detector`` / ``frames`` 互斥无关——音频是
    # 独立通道，不随视频帧同步调用（ADR-0026 §8）。
    audio_events: tuple[Any, ...] = ()

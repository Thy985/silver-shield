"""silver_demo — P0-11 三端风险闭环展示层（MVP Demo）。

独立的展示层包，与冻结的 ``home_perception`` **物理隔离**（ADR-0015）。
本包仅消费以下冻结契约符号（白名单，见 ADR-0015 §2.1）：

- ``PerceptionPipeline`` / ``DemoClock`` / ``FrameResult`` ← ``home_perception.runtime.pipeline``
- ``read_caviar_frames`` ← ``home_perception.runtime.config``
- ``WarningEvent`` ← ``home_perception.analysis.warning``（只读 ``to_dict()``）
- ``ActionCommand`` ← ``home_perception.action.command``（只读 ``to_dict()``）
- ``Settings`` ← ``home_perception.core.config``

严禁 import 7 层内部（``rule_engine`` / ``decision_engine`` / ``action.executor`` 等）；
``tests/demo/test_freeze_boundary.py`` 以 importlib 攻击性契约测试守此边界。
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]

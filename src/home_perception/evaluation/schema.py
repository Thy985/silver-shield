"""ADR-0033 Phase 1：``BenchmarkExpectation`` 公开入口（re-export）。

``BenchmarkExpectation`` 的**定义**已下移至中立子包 ``home_perception.validation.contracts``
（ADR-0033 review 5.2：避免 ``validation/scenario/scenario.py`` 反向 import ``evaluation`` 包，
保持 ``evaluation`` 单向消费 ``validation``）。本模块仅做 re-export，使
``from home_perception.evaluation.schema import BenchmarkExpectation`` 与
``from home_perception.evaluation import BenchmarkExpectation``（经 ``__init__.__getattr__``）
两种入口都可用，向后兼容既有调用方。

``BenchmarkExpectation`` 承载场景的**安全评价标签**（与 ADR-0032 ``Scenario.expects`` 的
"验证场景输出"语义正交、互不推导）。``benchmark=None`` 的场景不参与混淆矩阵（归
``unlabeled_scenario_ids``）。
"""

from __future__ import annotations

from home_perception.validation.contracts import BenchmarkExpectation

__all__ = ["BenchmarkExpectation"]

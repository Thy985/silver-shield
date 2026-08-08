"""ADR-0033 Phase 1：感知级 Benchmark Harness（最小闭环）。

本包（``home_perception.evaluation``）消费 ADR-0032 的 ``Scenario`` / ``ScenarioCompiler`` /
``ScenarioRunner`` / ``ScenarioValidator`` 产出，把"仿真场景批量打分 + 可复现离散指标报告"
落地为正式组件。与 ``memory/evaluation/``（Memory 级）并列、命名区分、互不 import 实现。

**循环导入铁律（务必保持）**：本 ``__init__.py`` 必须为空（仅文档字符串）。``schema.py``
是独立叶子（仅依赖 pydantic + ``analysis.warning``），而 ``metrics`` / ``report`` /
``harness`` 才依赖 ``validation``。``validation/scenario/scenario.py`` 在加载期 import 本包
的 ``schema``；若本文件急切 import ``harness`` / ``validation`` 会触发
``scenario → evaluation → validation → scenario`` 环路。保持空即可断环。
"""

"""ADR-0033 Phase 1：感知级 Benchmark Harness（最小闭环）。

本包（``home_perception.evaluation``）消费 ADR-0032 的 ``Scenario`` / ``ScenarioCompiler`` /
``ScenarioRunner`` / ``ScenarioValidator`` 产出，把"仿真场景批量打分 + 可复现离散指标报告"
落地为正式组件。与 ``memory/evaluation/``（Memory 级）并列、命名区分、互不 import 实现。

**循环导入铁律（务必保持）**：本 ``__init__.py`` 在加载期**零急切 import** 任何子模块。
``metrics`` / ``report`` / ``harness`` 才依赖 ``validation``；若急切 import ``harness`` /
``validation`` 会触发 ``scenario → evaluation → validation → scenario`` 环路。

为兼顾"零急切 import"与"公开入口可用"，本文件用 **PEP 562 ``__getattr__``** 做**延迟转发**：
``from home_perception.evaluation import BenchmarkHarness`` 等形式在首次访问属性时才真正
import 对应子模块，平时 import 本包不产生任何子模块依赖。这样调用方既能写
``from home_perception.evaluation import BenchmarkExpectation``，又不会破坏断环铁律。
"""

from __future__ import annotations

import importlib

# 公开符号 → 所属子模块（延迟转发，避免加载期急切 import 触发环路）。
_PUBLIC_MODULES: dict[str, str] = {
    "BenchmarkExpectation": "home_perception.evaluation.schema",
    "BenchmarkHarness": "home_perception.evaluation.harness",
    "BenchmarkProvenanceError": "home_perception.evaluation.harness",
    "compute_harness_fingerprint": "home_perception.evaluation.harness",
    "default_model_fingerprint": "home_perception.evaluation.harness",
    "BenchmarkReport": "home_perception.evaluation.report",
    "ScenarioScore": "home_perception.evaluation.metrics",
    # Phase 2（ab_runner）：回归能力（D6 / D7）
    "BenchmarkABRun": "home_perception.evaluation.ab_runner",
    "BenchmarkABConservationError": "home_perception.evaluation.ab_runner",
    "BenchmarkDiff": "home_perception.evaluation.ab_runner",
    "MetricDelta": "home_perception.evaluation.ab_runner",
    "RegressionReport": "home_perception.evaluation.ab_runner",
    "evaluate_regression": "home_perception.evaluation.ab_runner",
    "load_baseline_report": "home_perception.evaluation.ab_runner",
    "load_baseline_report_path": "home_perception.evaluation.ab_runner",
    "baseline_path": "home_perception.evaluation.ab_runner",
}


def __getattr__(name: str):
    """PEP 562：仅在属性缺失时延迟转发到对应子模块（零加载期依赖）。"""
    target = _PUBLIC_MODULES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target)
    return getattr(module, name)

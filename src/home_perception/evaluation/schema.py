"""ADR-0033 Phase 1：``BenchmarkExpectation`` 模型（验证/评价语义分离）。

本模块是 ``evaluation`` 包的**叶子**，仅依赖 pydantic + ``analysis.warning.RISK_LEVELS``，
**不** import ``validation`` / ``harness``，避免与 ``validation/scenario/scenario.py`` 的
加载期 import 形成环路（见 ``evaluation/__init__.py`` 铁律）。

``BenchmarkExpectation`` 承载场景的**安全评价标签**（与 ADR-0032 ``Scenario.expects`` 的
"验证场景输出"语义正交、互不推导）。``benchmark=None`` 的场景不参与混淆矩阵（归
``unlabeled_scenario_ids``）。
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from home_perception.analysis.warning import RISK_LEVELS


class BenchmarkExpectation(BaseModel):
    """场景的安全评价标签（ADR-0033 D3，与 ADR-0032 ``expects`` 语义分离）。

    - ``expected_alarm``：该场景是否期望触发告警（安全评价意图，由场景作者**显式声明**）；
    - ``severity``：期望告警级别（可选，用于分层度量；必须是 ``RISK_LEVELS`` 之一）；
    - ``note``：人类可读理由（审计血缘）。
    """

    expected_alarm: bool
    severity: str | None = None
    note: str | None = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: str | None) -> str | None:
        if v is not None and v not in RISK_LEVELS:
            raise ValueError(
                f"benchmark.severity={v!r} 非法；必须为 {RISK_LEVELS}（fail-closed）"
            )
        return v

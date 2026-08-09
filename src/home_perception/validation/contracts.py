"""ADR-0033 / ADR-0032 共享契约（中立子包，避免反向依赖）。

``BenchmarkExpectation`` 是**安全评价标签**的契约模型。它语义上属于"场景评价"，但为了避免
``validation/scenario/scenario.py`` 反向 import ``evaluation`` 包（5.2：``validation`` 是
``evaluation`` 的被依赖方，方向必须单向），把它放在中立的 ``validation.contracts`` 子包。

- ``scenario.py``（validation 内部）从此处 import，方向在 validation 内、无环；
- ``evaluation`` 通过 ``evaluation.schema`` re-export 供外部使用，``evaluation`` 仍只消费
  validation，不反向被依赖。

本模块是纯数据模型（pydantic + ``analysis.warning.RISK_LEVELS``），**不** import
``evaluation`` / ``harness``，断环。
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

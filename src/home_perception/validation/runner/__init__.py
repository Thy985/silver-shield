"""ADR-0032 ``validation/runner`` 子包：``ScenarioRunner`` + ``ScenarioValidator`` + 载体类型。"""

from __future__ import annotations

from ..synthetic_input import SyntheticInput
from .runner import (
    RunResult,
    ScenarioRunner,
    ScenarioValidator,
    ValidationResult,
)

__all__ = [
    "RunResult",
    "ScenarioRunner",
    "ScenarioValidator",
    "SyntheticInput",
    "ValidationResult",
]

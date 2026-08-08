"""ADR-0032 Slice D（部分）：``ScenarioRunner`` + ``ScenarioValidator``（D4/D5）。

对应 ``validation/runner/`` 子包。三组件从设计起分离（T9）：
- ``ScenarioCompiler``（``scenario/``）：YAML → ``SyntheticInput``；
- ``ScenarioRunner``（本文件）：``SyntheticInput`` → pipeline → ``RunResult``（只编排，不含对比/可视化/聚合）；
- ``ScenarioValidator``（本文件）：``RunResult`` + ``expects`` → ``ValidationResult``（对照 ground truth）。

``ScenarioRunner.run`` **只**驱动已注入的 pipeline 并收回事件序列，不内嵌校验 / 报告 / 存储
（归 ADR-0033）。``ScenarioValidator`` 校验深度到 ``WarningEvent`` 为止（含其 ``risk_level``），
不额外下钻内部 ``RiskSignal`` 字段（评审 B4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ...analysis.warning import RISK_LEVELS
from ..synthetic_input import SyntheticInput

if TYPE_CHECKING:  # ``Scenario`` 仅用于注解；运行期不导入 ``scenario`` 子包（断环）
    from ..scenario.scenario import Scenario

__all__ = [
    "RunResult",
    "ScenarioRunner",
    "ScenarioValidator",
    "SyntheticInput",
    "ValidationResult",
]


@dataclass
class RunResult:
    """一次场景运行的结果（D4 Runner 的输出，供 Validator 消费）。"""

    scenario_id: str
    mode: str
    event_types: set[str] = field(default_factory=set)
    risk_levels: list[str] = field(default_factory=list)
    perception_events: list[Any] = field(default_factory=list)
    warnings: list[Any] = field(default_factory=list)
    fingerprint: str = ""


@dataclass
class ValidationResult:
    """场景校验结果（对标音频 ``tts/scenario_runner.py`` 的 ``ValidationResult`` 模式）。"""

    scenario_id: str
    ok: bool
    observed_event_types: set[str] = field(default_factory=set)
    expected_event_types: set[str] = field(default_factory=set)
    missing_event_types: set[str] = field(default_factory=set)
    risk_level_ok: bool | None = None
    observed_risk_levels: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return (
            f"[{status}] {self.scenario_id} "
            f"observed={sorted(self.observed_event_types)} "
            f"expected={sorted(self.expected_event_types)} "
            f"missing={sorted(self.missing_event_types)} "
            f"risk_ok={self.risk_level_ok}"
        )


class ScenarioRunner:
    """执行编排：``SyntheticInput`` → pipeline → ``RunResult``（D4）。

    只负责"输入 → pipeline → 结果"，**不**含对比报告 / 可视化 / 结果存储 / 跨场景聚合
    （归 ADR-0033 BenchmarkHarness）。
    """

    def run(self, synth: SyntheticInput, pipeline: Any, frame_interval_s: float = 0.0) -> RunResult:
        """驱动 pipeline 处理 ``synth`` 的全部帧，收回事件序列。

        - ``frames`` 模式：直接喂 ``synth.frames``（pipeline 必须已注入合适的 detector，真实
          YOLO 为 opt-in，T7 不默认）；
        - ``detections`` 模式：``synth.detector`` 已内嵌逐帧检测缓存，用占位帧驱动
          ``pipeline.process_frame``（frame 像素被 detector 忽略）。

        ``frame_interval_s > 0`` 时按帧推进 pipeline 内部时钟（ duck-typed ``tick``），
        复现真实视频时序以驱动 tracker 离场判定（与 ``PerceptionPipeline.run`` 一致）；
        传入不可推进的时钟则静默跳过（零行为变化）。
        """
        dummy = np.zeros((1, 1, 3), dtype=np.uint8)
        frames: list[Any]
        if synth.frames is not None:
            frames = list(synth.frames)
        else:
            frames = [dummy] * synth.n_frames

        clock = getattr(pipeline, "_clock", None)
        tickable = clock is not None and hasattr(clock, "tick")

        perc_events: list[Any] = []
        warnings: list[Any] = []
        for i, frame in enumerate(frames):
            if frame_interval_s > 0 and tickable:
                clock.tick(frame_interval_s)  # type: ignore[attr-defined]
            fr = pipeline.process_frame(frame, frame_index=i)
            perc_events.extend(fr.perception_events)
            warnings.extend(fr.warnings)

        return RunResult(
            scenario_id=synth.scenario_id,
            mode=synth.mode,
            event_types={e.event_type for e in perc_events},
            risk_levels=[w.risk_level for w in warnings],
            perception_events=perc_events,
            warnings=warnings,
            fingerprint=synth.fingerprint,
        )


class ScenarioValidator:
    """对照 ``expects`` / ``timeline`` 校验 ``RunResult``（D4 / T6 / B4）。

    校验深度到 ``WarningEvent`` 为止（含其 ``risk_level``），不额外下钻内部 ``RiskSignal``。
    """

    def validate(self, run_result: RunResult, scenario: Scenario) -> ValidationResult:
        """比对运行结果与场景期望，产出 ``ValidationResult``。"""
        errors: list[str] = []
        observed = set(run_result.event_types)
        expected = set(scenario.expects.emitted_event_types)
        missing = expected - observed

        # 风险等级下界（复用 analysis/warning.py 的 RISK_LEVELS 序值，评审 B4）
        risk_ok: bool | None = None
        min_risk = scenario.expects.min_risk_level
        if min_risk is not None:
            if min_risk not in RISK_LEVELS:
                errors.append(f"expects.min_risk_level={min_risk!r} 非法；必须为 {RISK_LEVELS}")
                risk_ok = False
            else:
                exp_ord = RISK_LEVELS.index(min_risk)
                if run_result.risk_levels:
                    max_obs_ord = max(RISK_LEVELS.index(r) for r in run_result.risk_levels)
                else:
                    max_obs_ord = -1
                risk_ok = max_obs_ord >= exp_ord

        ok = (not missing) and (risk_ok is not False) and (not errors)
        details = self._build_details(
            observed, expected, missing, risk_ok, run_result.risk_levels, min_risk
        )
        return ValidationResult(
            scenario_id=scenario.meta.scenario_id,
            ok=ok,
            observed_event_types=observed,
            expected_event_types=expected,
            missing_event_types=missing,
            risk_level_ok=risk_ok,
            observed_risk_levels=list(run_result.risk_levels),
            errors=errors,
            details=details,
        )

    @staticmethod
    def _build_details(
        observed: set[str],
        expected: set[str],
        missing: set[str],
        risk_ok: bool | None,
        observed_risk: list[str],
        min_risk: str | None,
    ) -> str:
        parts = [
            f"observed_event_types={sorted(observed)}",
            f"expected_event_types={sorted(expected)}",
            f"missing_event_types={sorted(missing)}",
            f"observed_risk_levels={observed_risk}",
            f"min_risk_level={min_risk!r} -> ok={risk_ok}",
        ]
        return "; ".join(parts)

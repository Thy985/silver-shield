"""ADR-0033 Phase 1：打分纯函数 + ``ScenarioScore``（D3 / D2）。

仅编排消费 ADR-0032 产物，不重新生成 / 校验事件 Schema（D2）。所有打分逻辑为纯函数，
便于契约测试与变异验证（M1–M2）。本模块是 ``evaluation`` 中依赖 ``validation`` 的入口
之一，仅引用 ``validation.runner.runner``（``RunResult`` / ``ValidationResult``）与
``validation.scenario.scenario.Scenario``（类型注解）+ ``analysis.warning.RISK_LEVELS``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from home_perception.analysis.warning import RISK_LEVELS
from home_perception.validation.runner.runner import RunResult, ValidationResult
from home_perception.validation.scenario.scenario import Scenario

# 标签 / 混淆矩阵单元常量
LABEL_ALERT = "alert"
LABEL_NO_ALERT = "no_alert"
OUTCOME_TP = "TP"  # 期望报警且实际报警 ✅
OUTCOME_TN = "TN"  # 期望不报警且实际不报警 ✅
OUTCOME_FN = "FN"  # 漏报：期望报警却未报警 ❌（ADR-0031 SUPPRESS×WARN 语义）
OUTCOME_FP = "FP"  # 误报：期望不报警却报警 ❌（ADR-0031 WARN×SUPPRESS 语义）
OUTCOME_UNLABELED = "UNLABELED"  # 场景未声明 benchmark，不参与混淆矩阵

# 无观测风险级别时的隐式序值：低于 RISK_LEVELS 起始序值（LOW=0），
# 表达"实际无任何告警"语义。"命名常量"替代魔法数字 -1，使隐式假设显式可读。
_MISSING_OBS_ORD = -1


def scenario_expected_label(scenario: Scenario) -> str | None:
    """从 ``benchmark`` 推导期望标签（D3）。

    返回 ``None`` 表示未标注（场景作者未声明 ``benchmark``），该场景不参与混淆矩阵。
    语义清晰：``expected_alarm`` 由场景作者显式声明，绝不从 ``expects`` 推。
    """
    bm = scenario.benchmark
    if bm is None:
        return None
    return LABEL_ALERT if bm.expected_alarm else LABEL_NO_ALERT


def scenario_actual_label(run_result: RunResult, warning_policy: Any | None = None) -> str:
    """从运行结果推导实际标签（D3 扩展点）。

    当前实现（Phase 1，可接受）：``actual_label = bool(run_result.warnings)``。

    扩展点：未来 ``warning`` 的产生未必等于"应当报警"（如 ``low_confidence`` / 调试期
    warning 不应计入误报），可注入 ``warning_policy.evaluate(warnings)`` 策略化判定；本函数
    签名已预留该接缝，Phase 1 不实现策略、仅以 ``bool`` 为默认实现。
    """
    if warning_policy is not None:
        return LABEL_ALERT if warning_policy.evaluate(run_result.warnings) else LABEL_NO_ALERT
    return LABEL_ALERT if run_result.warnings else LABEL_NO_ALERT


def scenario_confusion(expected_label: str | None, actual_label: str) -> str:
    """配对期望 / 实际标签 → 混淆矩阵单元（D3）。

    ``expected_label is None`` → ``UNLABELED``（不参与 TP/FN/FP/TN 计数）。
    否则：alert×alert=TP，no_alert×no_alert=TN，alert×no_alert=FN（漏报），
    no_alert×alert=FP（误报）。
    """
    if expected_label is None:
        return OUTCOME_UNLABELED
    if expected_label == LABEL_ALERT and actual_label == LABEL_ALERT:
        return OUTCOME_TP
    if expected_label == LABEL_NO_ALERT and actual_label == LABEL_NO_ALERT:
        return OUTCOME_TN
    if expected_label == LABEL_ALERT and actual_label == LABEL_NO_ALERT:
        return OUTCOME_FN
    return OUTCOME_FP  # expected no_alert, actual alert


def event_recall(observed: set[str], expected: set[str]) -> float:
    """期望事件类型被产出的比例（ADR-0032 ``ValidationResult`` 的验证指标，跨场景均值）。

    ``emitted_event_types`` 列表在语义上按**集合**处理（事件类型去重、不计重复权重）：
    传入的 ``observed`` / ``expected`` 均为 ``set[str]``，列表转 set 由调用方完成。

    ``expected`` 为空 → 1.0（无期望事件，召回真空满足）。
    """
    if not expected:
        return 1.0
    if not observed:
        return 0.0
    return len(expected & observed) / len(expected)


def risk_shortfall(scenario: Scenario, observed_risk_levels: list[str]) -> float | None:
    """期望 ``min_risk_level`` 序值 − 实际最大 ``risk_level`` 序值（负值=达标或超额）。

    仅当 ``expects.min_risk_level`` 设定时返回数值；否则 ``None``（验证指标，与 benchmark
    标签无关）。复用 ``analysis.warning.RISK_LEVELS`` 序值。
    """
    min_risk = scenario.expects.min_risk_level
    if min_risk is None or min_risk not in RISK_LEVELS:
        return None
    exp_ord = RISK_LEVELS.index(min_risk)
    if observed_risk_levels:
        obs_ords = [RISK_LEVELS.index(r) for r in observed_risk_levels if r in RISK_LEVELS]
        max_obs_ord = max(obs_ords) if obs_ords else _MISSING_OBS_ORD
    else:
        max_obs_ord = _MISSING_OBS_ORD
    return float(exp_ord - max_obs_ord)


@dataclass(frozen=True, slots=True)
class ScenarioScore:
    """单场景打分（D2 / D3）。由 ``build_scenario_score`` 从 ``RunResult`` + ``ValidationResult``
    + ``Scenario`` 派生，纯数据、可序列化。

    字段集合**冻结**（`frozen=True` + `slots=True`）：运行期无法向实例注入未知属性，从结构上
    杜绝"未来有人给 Score 加 ``video_path`` / ``raw_frame_uri`` 之类字段"导致的原始媒体 /
    路径经报告泄露。与 ADR-0031 T5 黑名单测试互补——白名单（结构冻结）优于黑名单（事后扫描）。
    """

    scenario_id: str
    expected_label: str | None
    actual_label: str
    outcome: str  # TP | TN | FN | FP | UNLABELED
    validation_ok: bool
    validation_details: str
    observed_event_types: set[str] = field(default_factory=set)
    expected_event_types: set[str] = field(default_factory=set)
    missing_event_types: set[str] = field(default_factory=set)
    observed_risk_levels: list[str] = field(default_factory=list)
    event_recall: float = 0.0
    risk_shortfall: float | None = None
    benchmark_expected_alarm: bool | None = None
    benchmark_severity: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "expected_label": self.expected_label,
            "actual_label": self.actual_label,
            "outcome": self.outcome,
            "validation_ok": self.validation_ok,
            "validation_details": self.validation_details,
            "observed_event_types": sorted(self.observed_event_types),
            "expected_event_types": sorted(self.expected_event_types),
            "missing_event_types": sorted(self.missing_event_types),
            "observed_risk_levels": list(self.observed_risk_levels),
            "event_recall": self.event_recall,
            "risk_shortfall": self.risk_shortfall,
            "benchmark_expected_alarm": self.benchmark_expected_alarm,
            "benchmark_severity": self.benchmark_severity,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> ScenarioScore:
        """从 ``to_dict`` 结构重建（Phase 2 基线可回放 T10）。

        ``set`` 字段经可迭代重建（``to_dict`` 已排序为 list）；``risk_shortfall`` /
        ``benchmark_expected_alarm`` / ``benchmark_severity`` 允许 ``None``。

        反序列化校验（M10）：必填字段缺失转 ``ValueError`` 带上下文；``validation_ok``
        显式拒绝 ``None``（``bool(None)`` 会静默塌缩为 ``False``）；``event_recall`` 强制
        数值，拒绝字符串 / ``None`` 注入。手工编辑基线若塞入非法类型必须显式报错。
        """
        if not isinstance(d, dict):
            raise TypeError(f"ScenarioScore 顶层须为对象，收到 {type(d).__name__}")

        scenario_id = d.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise TypeError("ScenarioScore 缺字段 scenario_id（或为空/非字符串）")
        actual_label = d.get("actual_label")
        if not isinstance(actual_label, str):
            raise TypeError("ScenarioScore 缺字段 actual_label（或非字符串）")
        outcome = d.get("outcome")
        if not isinstance(outcome, str):
            raise TypeError("ScenarioScore 缺字段 outcome（或非字符串）")
        validation_ok = d.get("validation_ok")
        if not isinstance(validation_ok, bool):
            raise TypeError(
                f"ScenarioScore.validation_ok 须为布尔，收到 {validation_ok!r}（None 会被"
                "bool() 静默塌缩为 False，已显式拒绝）"
            )
        validation_details = d.get("validation_details")
        if not isinstance(validation_details, str):
            raise TypeError("ScenarioScore 缺字段 validation_details（或非字符串）")
        event_recall = d.get("event_recall")
        if not isinstance(event_recall, (int, float)) or isinstance(event_recall, bool):
            raise TypeError(f"ScenarioScore.event_recall 须为数值，收到 {event_recall!r}")

        risk_shortfall = d.get("risk_shortfall")
        if risk_shortfall is not None and (
            not isinstance(risk_shortfall, (int, float)) or isinstance(risk_shortfall, bool)
        ):
            raise ValueError(
                f"ScenarioScore.risk_shortfall 须为数值或 None，收到 {risk_shortfall!r}"
            )

        return cls(
            scenario_id=scenario_id,
            expected_label=d.get("expected_label"),  # str | None
            actual_label=actual_label,
            outcome=outcome,
            validation_ok=validation_ok,
            validation_details=validation_details,
            observed_event_types=set(d.get("observed_event_types", [])),  # type: ignore[arg-type]
            expected_event_types=set(d.get("expected_event_types", [])),  # type: ignore[arg-type]
            missing_event_types=set(d.get("missing_event_types", [])),  # type: ignore[arg-type]
            observed_risk_levels=list(d.get("observed_risk_levels", [])),  # type: ignore[arg-type]
            event_recall=float(event_recall),
            risk_shortfall=(float(risk_shortfall) if risk_shortfall is not None else None),
            benchmark_expected_alarm=d.get("benchmark_expected_alarm"),
            benchmark_severity=d.get("benchmark_severity"),
        )


def build_scenario_score(
    scenario: Scenario,
    run_result: RunResult,
    validation_result: ValidationResult,
    *,
    warning_policy: Any | None = None,
) -> ScenarioScore:
    """从三要素派生 ``ScenarioScore``（D3）。"""
    expected_label = scenario_expected_label(scenario)
    actual_label = scenario_actual_label(run_result, warning_policy=warning_policy)
    outcome = scenario_confusion(expected_label, actual_label)
    bm = scenario.benchmark
    return ScenarioScore(
        scenario_id=scenario.meta.scenario_id,
        expected_label=expected_label,
        actual_label=actual_label,
        outcome=outcome,
        validation_ok=validation_result.ok,
        validation_details=validation_result.details,
        observed_event_types=set(run_result.event_types),
        expected_event_types=set(scenario.expects.emitted_event_types),
        missing_event_types=set(validation_result.missing_event_types),
        observed_risk_levels=list(run_result.risk_levels),
        event_recall=event_recall(
            set(run_result.event_types), set(scenario.expects.emitted_event_types)
        ),
        risk_shortfall=risk_shortfall(scenario, list(run_result.risk_levels)),
        benchmark_expected_alarm=bm.expected_alarm if bm is not None else None,
        benchmark_severity=bm.severity if bm is not None else None,
    )

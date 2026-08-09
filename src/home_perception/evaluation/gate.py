"""ADR-0033 Phase 3：生产门控（D5 Hard Gate 先于复合分 + D7 BenchmarkThresholds）。

本模块把 Benchmark 接入工程护栏：**Hard Gate（全部场景 MUST 通过 ScenarioValidator 且达到
阈值）→ 阈值对照 → 回归对照（可选基线）→ 复合分（仅报告、非门控、calibrated=False）**。

判定顺序铁律（mirror ``memory/evaluation`` 的 ``summarize_hard_gate`` / ``HardGateSummary``）：
Hard Gate 先于一切，复合分 ``BenchmarkScore`` 永远**不**参与门禁决策（安全指标非线性，FN
penalty >>> FP penalty；阈值未标定前 ``calibrated=False``，见 ADR-0033 §5 开放问题）。空集
（无场景 / 零标注证据）视为**不通过**。

模块边界（T9）：本文件**不**直接 import ``validation`` / ``runtime`` / ``analysis`` 重链
（cv2 门控风险）。仅消费 ``.report.BenchmarkReport`` 的已聚合指标，并在 ``evaluate_gate``
内部**懒导入** ``.ab_runner``（``evaluate_regression``），使本模块在加载期零急切依赖重链，
与 ``evaluation/__init__.py`` 的 PEP 562 断环铁律同向。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型标注用，加载期不拉重链（守 T9）
    from .report import BenchmarkReport


# ---------------------------------------------------------------------------
# 复合分权重（未标定占位，绝不门控）
# ---------------------------------------------------------------------------
# mirror ``memory/evaluation.BASE_WEIGHTS``：权重尚未标定（ADR-0033 §5 开放问题），
# 此处用**等权占位**。复合分仅供报告 / 横向比较；安全指标非线性（FN >>> FP），阈值未标定前
# ``calibrated=False``，本模块任何门禁决策都**不**读取该分（见 ``evaluate_gate`` 结尾）。
BENCHMARK_BASE_WEIGHTS: dict[str, float] = {
    "precision": 1.0,
    "recall": 1.0,
    "f1": 1.0,
    "suppression_rate": 1.0,  # score 计算时取 (1 - rate) 转「越高质量越好」
    "false_alarm_rate": 1.0,  # 同上取 (1 - rate)
    "mean_event_recall": 1.0,
}


# ---------------------------------------------------------------------------
# D7 BenchmarkThresholds 契约
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchmarkThresholds:
    """Phase 3 生产门控阈值（D7 契约默认值）。

    - ``min_pass_rate=1.0``：标注场景必须**全部** TP/TN（零漏报零误报）；
    - ``max_suppression_rate=0.0``：漏报率上限 0；
    - ``max_false_alarm_rate=0.05``：误报率上限 5%；
    - ``max_mean_risk_shortfall=0.0``：平均风险缺口须 ≤ 0（无正向缺口）；
    - ``max_regression_delta=None``：回归预算（``None`` = 不对照回归；提供 baseline +
      设此值才成为门禁，见 ``evaluate_gate``）。
    """

    min_pass_rate: float = 1.0
    max_suppression_rate: float = 0.0
    max_false_alarm_rate: float = 0.05
    max_mean_risk_shortfall: float = 0.0
    max_regression_delta: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_pass_rate": self.min_pass_rate,
            "max_suppression_rate": self.max_suppression_rate,
            "max_false_alarm_rate": self.max_false_alarm_rate,
            "max_mean_risk_shortfall": self.max_mean_risk_shortfall,
            "max_regression_delta": self.max_regression_delta,
        }


# ---------------------------------------------------------------------------
# D5 阶段 1：Hard Gate（先于一切）—— 全部场景 MUST 通过 ScenarioValidator
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HardGateSummary:
    """§9 硬门槛汇总：**先于** 复合分，任一场景 ``validation_ok=False`` 即整体失败。

    mirror ``memory/evaluation.HardGateSummary``（同一判定顺序铁律）。
    """

    total: int
    passed: int
    failed_case_ids: tuple[str, ...]
    all_pass: bool


def summarize_hard_gate(
    scores: list[Any],
) -> HardGateSummary:
    """汇总 Hard Gate；空集视为**不通过**（无证据 ≠ 通过，与 ``summarize_hard_gate`` 一致）。

    ``scores`` 为 ``ScenarioScore`` 序列（``.scenario_id`` + ``.validation_ok``）。全部
    ``validation_ok`` 为 True 且非空 → ``all_pass=True``；任一 False 或空集 → ``all_pass=False``。
    """
    failed = tuple(sorted(s.scenario_id for s in scores if not s.validation_ok))
    passed = len(scores) - len(failed)
    return HardGateSummary(
        total=len(scores),
        passed=passed,
        failed_case_ids=failed,
        all_pass=bool(scores) and not failed,
    )


# ---------------------------------------------------------------------------
# D5 阶段 2：阈值对照（BenchmarkThresholds）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ThresholdCheck:
    """单阈值对照结果。

    ``skipped``：该指标在本场景集不可比（如 ``mean_risk_shortfall=None`` 的未标定场景集），
    跳过对照、不判失败（mirror ``ab_runner.BenchmarkDiff.skipped_metrics`` 语义）。
    """

    name: str
    metric: str
    actual: float | None
    threshold: float
    ok: bool
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "actual": self.actual,
            "threshold": self.threshold,
            "ok": self.ok,
            "skipped": self.skipped,
        }


def _label_count(report: BenchmarkReport) -> int:
    """标注场景数（参与混淆矩阵的 TP/TN/FN/FP 之和）。"""
    return report.tp + report.tn + report.fn + report.fp


def _evaluate_thresholds(
    report: BenchmarkReport, thresholds: BenchmarkThresholds
) -> tuple[ThresholdCheck, ...]:
    """逐阈值对照（纯函数）。

    每个阈值用**边界包容**（``>=`` / ``<=``）判定：恰好等于阈值算通过（变异验证 T7 钉死方向）。
    ``mean_risk_shortfall=None`` → 跳过该阈值（``skipped=True``，不判失败）。
    """
    labeled = _label_count(report)
    # pass_rate：标注场景的 (TP+TN)/总数；零标注 → 0（无证据，min_pass_rate=1.0 必失败）
    pass_rate = (report.tp + report.tn) / labeled if labeled else 0.0

    checks: list[ThresholdCheck] = [
        ThresholdCheck(
            name="min_pass_rate",
            metric="pass_rate",
            actual=pass_rate,
            threshold=thresholds.min_pass_rate,
            ok=(pass_rate >= thresholds.min_pass_rate),
        ),
        ThresholdCheck(
            name="max_suppression_rate",
            metric="suppression_rate",
            actual=report.suppression_rate,
            threshold=thresholds.max_suppression_rate,
            ok=(report.suppression_rate <= thresholds.max_suppression_rate),
        ),
        ThresholdCheck(
            name="max_false_alarm_rate",
            metric="false_alarm_rate",
            actual=report.false_alarm_rate,
            threshold=thresholds.max_false_alarm_rate,
            ok=(report.false_alarm_rate <= thresholds.max_false_alarm_rate),
        ),
    ]
    if report.mean_risk_shortfall is None:
        # 未标定场景集：跳过 mean_risk_shortfall 对照，不判失败
        checks.append(
            ThresholdCheck(
                name="max_mean_risk_shortfall",
                metric="mean_risk_shortfall",
                actual=None,
                threshold=thresholds.max_mean_risk_shortfall,
                ok=True,
                skipped=True,
            )
        )
    else:
        checks.append(
            ThresholdCheck(
                name="max_mean_risk_shortfall",
                metric="mean_risk_shortfall",
                actual=report.mean_risk_shortfall,
                threshold=thresholds.max_mean_risk_shortfall,
                ok=(report.mean_risk_shortfall <= thresholds.max_mean_risk_shortfall),
            )
        )
    return tuple(checks)


# ---------------------------------------------------------------------------
# D5 阶段 4：复合分（仅报告、非门控、calibrated=False）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchmarkScore:
    """感知级复合分。**报告用，非 Hard Gate**；阈值标定前 ``calibrated=False``（ADR-0033 D5）。

    mirror ``memory/evaluation.MemoryValueScore``：``valid`` 表示评分是否有意义；空数据集 /
    全 term 缺失 → ``valid=False`` 且 ``score=None``，明确表达「未测量」，绝不等价于「无价值」
    （未测量 ≠ 0，评审 issue 2 同口径）。

    为「越高越好」一致性，``suppression_rate`` / ``false_alarm_rate`` 在加权时取 ``(1 - rate)``
    反转（原始 rate 越高越糟）。该分**绝不**参与 ``evaluate_gate`` 的门禁决策。
    """

    terms: dict[str, float | None]
    weights: dict[str, float]
    score: float | None
    partial: bool
    calibrated: bool = False
    valid: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms": dict(self.terms),
            "weights": dict(self.weights),
            "score": self.score,
            "partial": self.partial,
            "calibrated": self.calibrated,
            "valid": self.valid,
            "note": self.note,
        }


def compute_benchmark_score(report: BenchmarkReport) -> BenchmarkScore:
    """从已聚合报告算复合分（纯函数，报告用、非门控）。

    原始 metric 取自 ``BenchmarkReport``；安全指标（suppression/false_alarm）取 ``(1 - rate)``
    反转。权重为未标定等权占位（``BENCHMARK_BASE_WEIGHTS``）。
    """
    raw: dict[str, float | None] = {
        "precision": report.precision,
        "recall": report.recall,
        "f1": report.f1,
        "suppression_rate": report.suppression_rate,
        "false_alarm_rate": report.false_alarm_rate,
        "mean_event_recall": report.mean_event_recall,
    }
    # 转「越高越好」：两 rate 取补数；其余原样
    processed: dict[str, float | None] = {
        "precision": raw["precision"],
        "recall": raw["recall"],
        "f1": raw["f1"],
        "suppression_rate": (1.0 - raw["suppression_rate"]) if raw["suppression_rate"] is not None else None,
        "false_alarm_rate": (1.0 - raw["false_alarm_rate"]) if raw["false_alarm_rate"] is not None else None,
        "mean_event_recall": raw["mean_event_recall"],
    }
    active = {k: v for k, v in processed.items() if v is not None}
    if not active:
        return BenchmarkScore(
            terms=raw,
            weights={},
            score=None,
            partial=False,
            calibrated=False,
            valid=False,
            note=(
                "无场景或全 metric 缺失：Score 无法计算（N/A），未测量 ≠ 0"
                "（不等价于感知无价值）。"
            ),
        )
    total = sum(BENCHMARK_BASE_WEIGHTS[k] for k in active)
    weights = {k: BENCHMARK_BASE_WEIGHTS[k] / total for k in active}
    score = sum(weights[k] * processed[k] for k in active)  # type: ignore[arg-type]
    partial = len(active) < len(BENCHMARK_BASE_WEIGHTS)
    note = (
        "BenchmarkScore 仅供报告 / 横向比较，非 Hard Gate；阈值标定前 calibrated=False，"
        "不得用于门控判定（ADR-0033 D5）。"
    )
    if partial:
        missing = sorted(set(BENCHMARK_BASE_WEIGHTS) - set(active))
        note += f" 缺失 term（已按原比例重归一化剩余权重）: {', '.join(missing)}。"
    return BenchmarkScore(
        terms=raw,
        weights=weights,
        score=score,
        partial=partial,
        calibrated=False,
        valid=True,
        note=note,
    )


# ---------------------------------------------------------------------------
# GateResult + evaluate_gate（判定顺序：Hard Gate → 阈值 → 回归 → Score 仅报告）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateResult:
    """``evaluate_gate`` 的完整门禁结果（D5）。

    ``passed`` = Hard Gate 全过 ``AND`` 阈值全过 ``AND``（无基线 ``OR`` 回归未超预算）。
    ``score`` 永远不参与 ``passed`` 计算（仅报告）。
    """

    scenario_set_id: str
    hard_gate: HardGateSummary
    thresholds: BenchmarkThresholds
    threshold_checks: tuple[ThresholdCheck, ...]
    regression: Any | None  # RegressionReport | None（TYPE_CHECKING 周期，运行时鸭子）
    score: BenchmarkScore
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_set_id": self.scenario_set_id,
            "passed": self.passed,
            "hard_gate": {
                "total": self.hard_gate.total,
                "passed": self.hard_gate.passed,
                "failed_case_ids": list(self.hard_gate.failed_case_ids),
                "all_pass": self.hard_gate.all_pass,
            },
            "thresholds": self.thresholds.to_dict(),
            "threshold_checks": [c.to_dict() for c in self.threshold_checks],
            "regression": (self.regression.to_dict() if self.regression is not None else None),
            "score": self.score.to_dict(),
        }

    def render_markdown(self) -> str:
        verdict = "✅ PASS" if self.passed else "⛔ FAIL"
        lines = [
            f"# Benchmark Gate — `{self.scenario_set_id}`",
            "",
            f"## 决策：{verdict}",
            "",
            "## 阶段 1 · Hard Gate（先于一切）",
            (
                f"- all_pass={self.hard_gate.all_pass} "
                f"({self.hard_gate.passed}/{self.hard_gate.total} 场景通过 ScenarioValidator)"
            ),
        ]
        if self.hard_gate.failed_case_ids:
            lines.append(f"- 失败场景: {list(self.hard_gate.failed_case_ids)}")
        lines.append("")
        lines.append("## 阶段 2 · 阈值对照（D7）")
        for c in self.threshold_checks:
            if c.skipped:
                lines.append(f"- `{c.name}`: 跳过（指标不可比，actual={c.actual}）")
            else:
                mark = "✅" if c.ok else "⛔"
                lines.append(
                    f"- `{c.name}`: {mark} actual={c.actual} ≤/≥ threshold={c.threshold}"
                )
        lines.append("")
        if self.regression is not None:
            lines.append("## 阶段 3 · 回归对照（baseline）")
            if self.regression.regressions_exceeded:
                lines.append("- ⛔ 超出回归预算")
            else:
                lines.append("- ✅ 在回归预算内")
            lines.append(self.regression.render_markdown())
            lines.append("")
        lines.append("## 阶段 4 · 复合分（仅报告，非门控，calibrated=False）")
        sc = self.score
        lines.append(
            f"- score={sc.score} (partial={sc.partial}, calibrated={sc.calibrated}, valid={sc.valid})"
        )
        lines.append(f"- 注：{sc.note}")
        return "\n".join(lines)


def evaluate_gate(
    report: BenchmarkReport,
    thresholds: BenchmarkThresholds | None = None,
    baseline: BenchmarkReport | None = None,
) -> GateResult:
    """ADR-0033 Phase 3 生产门禁（D5 判定顺序铁律）。

    判定顺序：
    1. **Hard Gate（先于一切）**：全部场景 ``validation_ok`` 必须 True（``summarize_hard_gate``，
       空集视为不通过）；
    2. **阈值对照**：``BenchmarkThresholds`` 各阈值（边界包容），``mean_risk_shortfall=None``
       跳过该阈值；
    3. **回归对照（可选）**：仅当 ``baseline`` 提供时跑 ``evaluate_regression``；若同时设
       ``thresholds.max_regression_delta``，超出预算则门禁失败（fail-closed：baseline 与
       candidate 不可比时由 ``evaluate_regression`` 抛 ``BenchmarkABConservationError``，
       不静默放过）；
    4. **复合分（仅报告）**：``compute_benchmark_score``，**绝不**参与 ``passed`` 判定。

    ``passed = hard_gate.all_pass AND thresholds_ok AND (regression is None or not exceeded)``。
    """
    from .report import BenchmarkReport as _Report  # 懒导入：加载期不拉 cv2 重链（守 T9）

    if not isinstance(report, _Report):
        raise TypeError(f"evaluate_gate 需要 BenchmarkReport，收到 {type(report).__name__}")
    if thresholds is None:
        thresholds = BenchmarkThresholds()

    # —— 阶段 1：Hard Gate（先于一切）——
    hard_gate = summarize_hard_gate(list(report.scores))

    # —— 阶段 2：阈值对照 ——
    checks = _evaluate_thresholds(report, thresholds)
    thresholds_ok = all(c.ok for c in checks if not c.skipped)

    # —— 阶段 3：回归对照（仅当 baseline 提供）——
    regression = None
    if baseline is not None:
        from .ab_runner import evaluate_regression  # 懒导入：守 T9

        regression = evaluate_regression(
            report, baseline, max_regression_delta=thresholds.max_regression_delta
        )
    regression_ok = regression is None or not regression.regressions_exceeded

    # —— 阶段 4：复合分（仅报告、非门控）——
    score = compute_benchmark_score(report)

    # 门禁决策：Hard Gate 先于复合分；复合分永不参与
    passed = hard_gate.all_pass and thresholds_ok and regression_ok
    return GateResult(
        scenario_set_id=report.scenario_set_id,
        hard_gate=hard_gate,
        thresholds=thresholds,
        threshold_checks=checks,
        regression=regression,
        score=score,
        passed=passed,
    )


__all__ = [
    "BENCHMARK_BASE_WEIGHTS",
    "BenchmarkScore",
    "BenchmarkThresholds",
    "GateResult",
    "HardGateSummary",
    "ThresholdCheck",
    "compute_benchmark_score",
    "evaluate_gate",
    "summarize_hard_gate",
]

"""ADR-0033 Phase 2：回归能力（ab_runner + BenchmarkDiff + 轻量回归对照，D6 / D7）。

引入 old-vs-new 回归对比与可回放基线：
- ``BenchmarkABRun``：两臂 ``BenchmarkReport``（baseline / candidate）+ ``vary`` 轴声明；
  ``assert_conserved`` 七条守恒（默认 ``vary=code_version``，可切 ``model_fingerprint`` 轴），
  与 ADR-0031 ``DecisionABRun`` 守恒形状同源、语义正交（本 ADR 轴=代码版本/模型权重 ≠ Memory）。
- ``BenchmarkDiff``：由两臂 ``BenchmarkReport`` 派生的逐指标 Δ + 退化场景清单。
- ``evaluate_regression(report, baseline, *, max_regression_delta=None)``：**轻量、报告性**
  回归对照（在基线上算 ``BenchmarkDiff``、对照 ``max_regression_delta``），**不**做 Hard Gate
  准入门禁、**不**触发 CI 非零退出、**不**做复合分门控（Phase 2 MUST NOT，见 §6）。其返回的
  ``regressions_exceeded`` 仅为信息性标志，供人读或 Phase 3 ``gate.evaluate_gate`` 消费。

基底依赖（零急切 import，守 ``evaluation/__init__.py`` 断环铁律）：仅 import ``.report`` /
``.metrics`` 的公开符号；本模块**不** import ``validation`` / ``runtime`` 重链。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .metrics import (
    OUTCOME_FN,
    OUTCOME_FP,
    OUTCOME_TN,
    OUTCOME_TP,
)
from .report import BenchmarkReport

# vary 轴（声明本次 A/B 比较的"唯一变量"）
VARY_CODE = "code_version"
VARY_MODEL = "model_fingerprint"
VARY_AXES = (VARY_CODE, VARY_MODEL)

# 退化监控指标（candidate − baseline > 0 表示"变差"，安全指标增幅有害）
_REGRESSION_MONITORED = ("suppression_rate", "false_alarm_rate", "fn", "fp")

# 逐指标比较的离散指标键（``BenchmarkReport`` 上的属性名）
_METRIC_KEYS = (
    "tp",
    "tn",
    "fn",
    "fp",
    "unlabeled_scenario_count",
    "suppression_rate",
    "false_alarm_rate",
    "precision",
    "recall",
    "f1",
    "mean_event_recall",
)

_GOOD_OUTCOMES = frozenset({OUTCOME_TP, OUTCOME_TN})
_BAD_OUTCOMES = frozenset({OUTCOME_FN, OUTCOME_FP})

# 基线 JSON 落点（D7）：``evaluation/fixtures/baselines/<scenario_set_id>.json``
BASELINES_DIR = Path(__file__).resolve().parent / "fixtures" / "baselines"


class BenchmarkABConservationError(Exception):
    """``BenchmarkABRun.assert_conserved`` 失败时抛出（D6 七条守恒违反其一）。

    使用显式异常而非 ``assert`` 语句，避免被 ``-O`` 关闭（mirror ADR-0031
    ``ABRunConservationError``）。
    """


@dataclass(frozen=True)
class BenchmarkABRun:
    """代码版本 / 模型权重 A/B 双轨运行（ADR-0033 D6）。

    与 ADR-0031 ``DecisionABRun`` 对称但不同轴：本 ADR 默认唯一变量=代码版本（同一
    ``Scenario`` 集，不同代码），可显式切换为模型权重轴（同一代码，不同
    detector/tracker 权重）。两臂**仅** ``vary`` 轴字段可不同，其余轴全部守恒。
    """

    scenario_set_id: str
    report_baseline: BenchmarkReport
    report_candidate: BenchmarkReport
    vary: Literal["code_version", "model_fingerprint"] = "code_version"

    def __post_init__(self) -> None:
        """强制 ``scenario_set_id`` 与两臂报告一致（M9：构造参数不再被静默忽略）。

        调用方传入的 ``scenario_set_id`` 必须至少匹配基线/候选之一——否则属装配错误
        （拿错了基线/候选）。当两臂 ``scenario_set_id`` 本身一致时，该检查退化为
        ``self == 两臂``；当两臂不一致（``law1`` 会管）时，``self`` 仍须匹配其一以通过构造，
        随后由 ``assert_conserved`` 的 (1/7) 报具体不一致。
        """
        b_id = self.report_baseline.scenario_set_id
        c_id = self.report_candidate.scenario_set_id
        if self.scenario_set_id not in (b_id, c_id):
            raise BenchmarkABConservationError(
                "BenchmarkABRun.scenario_set_id 与两臂报告不一致 "
                f"（{self.scenario_set_id!r} 不在 baseline={b_id!r} / candidate={c_id!r} 之内）"
            )

    # 守恒判定依赖的 provenance 字段（缺任一即视为基线被手工篡改/缺失，fail-closed）
    _REQUIRED_PROVENANCE_FIELDS = (
        "generator_fingerprint",
        "policy_fingerprint",
        "runtime_dependencies",
        "model_fingerprint",
        "code_version",
    )

    def _components(self, report: BenchmarkReport) -> dict[str, Any]:
        """抽取守恒判定所需的成分（baseline / candidate 同构）。

        从 ``provenance`` 取 generator/policy/model/runtime/code 成分；``scenario_set_id``
        与场景数/序直接从报告本身取。任一守恒 provenance 字段缺失（``None``）即抛
        ``BenchmarkABConservationError``（M5：D4 指纹 fail-closed，A/B 守恒须对齐，
        手工编辑基线若抹掉字段不能让守恒「假绿」）。
        """
        prov = report.provenance or {}
        for field_name in self._REQUIRED_PROVENANCE_FIELDS:
            if prov.get(field_name) is None:
                raise BenchmarkABConservationError(
                    f"D6 守恒失败：provenance 缺字段 {field_name!r}（基线可能被手工篡改或"
                    "生成链不完整，无法证明守恒）"
                )
        return {
            "scenario_set_id": report.scenario_set_id,
            "generator_fingerprint": prov.get("generator_fingerprint"),
            "policy_fingerprint": prov.get("policy_fingerprint"),
            "runtime_dependencies": prov.get("runtime_dependencies"),
            "model_fingerprint": prov.get("model_fingerprint"),
            "code_version": prov.get("code_version"),
            "scenario_count": len(report.scores),
            "scenario_order": tuple(s.scenario_id for s in report.scores),
        }

    def assert_conserved(self) -> None:
        """机器可验证的「唯一变量守恒」断言（D6 七条，vary 感知）。

        任一不满足即抛 ``BenchmarkABConservationError``。两臂**仅** ``vary`` 轴字段可不同，
        其余轴全部守恒——这正是「唯一变量」的充要条件事后证明，而非构造期承诺。

        守恒集合（mirror ADR-0031 D7，但把"唯一变量"泛化为 ``vary`` 轴）：
        1. 两臂 ``scenario_set_id`` 相同（跑的是同一批场景）；
        2. 两臂 ``generator_fingerprint`` 相同（渲染产物一致，唯一允许差异不在渲染）；
        3. 两臂 ``policy_fingerprint`` 相同（决策策略一致）；
        4. 两臂 ``runtime_dependencies`` 相同（基线一致）；
        5. 两臂「非 vary 轴」成分相同（vary=code_version → model 守恒；vary=model → code 守恒）；
        6. 两臂 ``vary`` 轴字段**必须不同**（否则「无差异」结论可能是装配 bug 伪装）；
        7. 两臂场景数 / 场景顺序相同（聚合口径一致，否则 ``suppression_rate`` 不可比）。
        """
        if self.vary not in VARY_AXES:
            raise BenchmarkABConservationError(
                f"未知 vary 轴：{self.vary!r}（须为 {VARY_AXES}）"
            )

        b = self._components(self.report_baseline)
        c = self._components(self.report_candidate)

        # (1/7) 两臂 scenario_set_id 相同
        if b["scenario_set_id"] != c["scenario_set_id"]:
            raise BenchmarkABConservationError(
                "D6 守恒失败(1/7)：两臂 scenario_set_id 必须相同 "
                f"（{b['scenario_set_id']!r} != {c['scenario_set_id']!r}）"
            )

        # (2/7) 渲染产物一致
        if b["generator_fingerprint"] != c["generator_fingerprint"]:
            raise BenchmarkABConservationError(
                "D6 守恒失败(2/7)：两臂 generator_fingerprint 必须相同 "
                f"（{b['generator_fingerprint']} != {c['generator_fingerprint']}）"
            )

        # (3/7) 决策策略一致
        if b["policy_fingerprint"] != c["policy_fingerprint"]:
            raise BenchmarkABConservationError(
                "D6 守恒失败(3/7)：两臂 policy_fingerprint 必须相同 "
                f"（{b['policy_fingerprint']} != {c['policy_fingerprint']}）"
            )

        # (4/7) 基线（数值库/环境）一致
        if b["runtime_dependencies"] != c["runtime_dependencies"]:
            raise BenchmarkABConservationError(
                "D6 守恒失败(4/7)：两臂 runtime_dependencies 必须相同 "
                f"（{b['runtime_dependencies']} != {c['runtime_dependencies']}）"
            )

        # (5/7) + (6/7) vary 感知：非 vary 轴须守恒，vary 轴须不同
        if self.vary == VARY_CODE:
            if b["model_fingerprint"] != c["model_fingerprint"]:
                raise BenchmarkABConservationError(
                    "D6 守恒失败(5/7)：vary=code_version 时两臂 model_fingerprint 必须相同 "
                    "（防「同代码、不同 YOLO 权重」被误判为纯代码回归；"
                    f"{b['model_fingerprint']} != {c['model_fingerprint']}）"
                )
            if b["code_version"] == c["code_version"]:
                raise BenchmarkABConservationError(
                    "D6 守恒失败(6/7)：vary=code_version 时两臂 code_version 必须不同 "
                    "（baseline == candidate 在该轴上，无法证明「唯一变量=代码」，"
                    "「无差异」结论可能由装配 bug 伪装）"
                )
        else:  # VARY_MODEL
            if b["code_version"] != c["code_version"]:
                raise BenchmarkABConservationError(
                    "D6 守恒失败(5/7)：vary=model_fingerprint 时两臂 code_version 必须相同 "
                    "（同代码、升级模型权重场景要求代码守恒；"
                    f"{b['code_version']} != {c['code_version']}）"
                )
            if b["model_fingerprint"] == c["model_fingerprint"]:
                raise BenchmarkABConservationError(
                    "D6 守恒失败(6/7)：vary=model_fingerprint 时两臂 model_fingerprint 必须不同 "
                    "（baseline == candidate 在该轴上）"
                )

        # (7/7) 场景数 / 场景顺序相同
        if b["scenario_count"] != c["scenario_count"] or b["scenario_order"] != c["scenario_order"]:
            raise BenchmarkABConservationError(
                "D6 守恒失败(7/7)：两臂场景数/顺序必须相同（聚合口径一致，否则不可比） "
                f"（{b['scenario_count']}#{list(b['scenario_order'])} != "
                f"{c['scenario_count']}#{list(c['scenario_order'])}）"
            )


@dataclass(frozen=True)
class MetricDelta:
    """单指标 old-vs-new 差值（candidate − baseline）。"""

    metric: str
    baseline: float
    candidate: float
    delta: float  # candidate - baseline

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class BenchmarkDiff:
    """两臂 ``BenchmarkReport`` 派生的回归差异（D6 / D7）。

    - ``deltas``：逐指标 candidate − baseline；
    - ``regressed_scenario_ids``：结果退化的场景集合（baseline 为良好结果、candidate 退化为
      FN/FP）。
    """

    scenario_set_id: str
    vary: str
    deltas: tuple[MetricDelta, ...] = field(default_factory=tuple)
    regressed_scenario_ids: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_reports(
        cls,
        baseline: BenchmarkReport,
        candidate: BenchmarkReport,
        *,
        vary: str = "code_version",
    ) -> BenchmarkDiff:
        """从两臂报告派生（纯函数）。

        ``mean_risk_shortfall`` 为 ``None`` 时跳过该指标（未标定的场景集无此值）。
        """
        deltas: list[MetricDelta] = []
        for key in _METRIC_KEYS:
            bv = getattr(baseline, key)
            cv = getattr(candidate, key)
            if bv is None or cv is None:
                continue
            bvf = float(bv)
            cvf = float(cv)
            deltas.append(
                MetricDelta(metric=key, baseline=bvf, candidate=cvf, delta=cvf - bvf)
            )
        return cls(
            scenario_set_id=baseline.scenario_set_id,
            vary=vary,
            deltas=tuple(deltas),
            regressed_scenario_ids=tuple(sorted(_regressed_scenario_ids(baseline, candidate))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_set_id": self.scenario_set_id,
            "vary": self.vary,
            "deltas": [d.to_dict() for d in self.deltas],
            "regressed_scenario_ids": list(self.regressed_scenario_ids),
        }

    def render_markdown(self) -> str:
        lines = [
            f"# Benchmark Diff — `{self.scenario_set_id}` (vary={self.vary})",
            "",
            "## 逐指标 Δ（candidate − baseline）",
        ]
        for d in self.deltas:
            lines.append(
                f"- `{d.metric}`: {d.baseline:.4f} → {d.candidate:.4f} (Δ={d.delta:+.4f})"
            )
        lines.append("")
        if self.regressed_scenario_ids:
            lines.append(f"## 退化场景: {list(self.regressed_scenario_ids)}")
        else:
            lines.append("## 退化场景: 无")
        return "\n".join(lines)


def _regressed_scenario_ids(baseline: BenchmarkReport, candidate: BenchmarkReport) -> set[str]:
    """退化场景集：baseline 为良好结果（TP/TN）、candidate 退化为 FN/FP **或** UNLABELED。

    退化定义（M7）：baseline 能稳定断言为良好结果（TP/TN），candidate 却失去该断言——
    要么翻成坏结果（FN/FP），要么掉回 UNLABELED（场景元数据被改/运行未产出标签，
    同样意味着该场景不再能被断言为良好）。任一都计入退化清单，供人审查。
    """
    base = {s.scenario_id: s.outcome for s in baseline.scores}
    cand = {s.scenario_id: s.outcome for s in candidate.scores}
    regressed: set[str] = set()
    for sid, b_out in base.items():
        c_out = cand.get(sid)
        if c_out is None:
            continue
        if b_out in _GOOD_OUTCOMES and c_out not in _GOOD_OUTCOMES:
            # TP/TN → (FN / FP / UNLABELED) 均视为退化
            regressed.add(sid)
    return regressed


@dataclass(frozen=True)
class RegressionReport:
    """轻量回归对照结果（Phase 2，报告性、非门禁）。

    - ``diff``：``BenchmarkDiff``；
    - ``max_regression_delta``：对照阈值（``None`` = 未设，不判定预算）；
    - ``regressions_exceeded``：**信息性**标志（仅供人读/Phase 3 消费），本对象本身不抛异常、
      不触发 CI 非零退出（Phase 2 MUST NOT）。
    """

    scenario_set_id: str
    vary: str
    diff: BenchmarkDiff
    max_regression_delta: float | None
    regressions_exceeded: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_set_id": self.scenario_set_id,
            "vary": self.vary,
            "max_regression_delta": self.max_regression_delta,
            "regressions_exceeded": self.regressions_exceeded,
            "diff": self.diff.to_dict(),
        }

    def render_markdown(self) -> str:
        head = self.diff.render_markdown()
        if self.regressions_exceeded:
            flag = "⚠️ 超出回归预算"
        else:
            flag = "✅ 在回归预算内"
        budget = (
            "∞ (未设 max_regression_delta)"
            if self.max_regression_delta is None
            else f"{self.max_regression_delta}"
        )
        return f"{head}\n\n## 回归预算对照: {flag} (max_regression_delta={budget})"


def evaluate_regression(
    report: BenchmarkReport,
    baseline: BenchmarkReport,
    *,
    max_regression_delta: float | None = None,
    vary: str = "code_version",
) -> RegressionReport:
    """轻量回归对照（ADR-0033 Phase 2，D7）。

    即 ADR §6 所述 ``evaluate_gate(report, baseline)`` 的**轻量回归对照**版本：在已有基线上
    算 ``BenchmarkDiff``、对照 ``max_regression_delta``，返回 ``RegressionReport``。

    **报告性、非门禁**：本函数**不**抛回归相关异常（仅当两臂不可比时由 ``assert_conserved``
    抛 ``BenchmarkABConservationError``，属装配错误而非回归判定）、**不**触发 CI 非零退出、
    **不**做 Hard Gate / 复合分门控（Phase 2 MUST NOT）。真正的准入门禁归 Phase 3 的
    ``gate.evaluate_gate``（含 ``BenchmarkThresholds`` + Hard Gate）。

    ``    max_regression_delta`` 仅监控「增幅有害」的指标（``suppression_rate`` / ``false_alarm_rate``
    / ``fn`` / ``fp``）：其 candidate − baseline 超过该阈值即置 ``regressions_exceeded=True``
    （信息性）。

    ``max_regression_delta`` 必须 ≥ 0：它表达"可容忍的退化预算"。负值会令语义反转
    （``delta > 负值`` 恒真，任何改善也被判为回归），属调用错误，立即抛 ``ValueError``。
    """
    if max_regression_delta is not None and max_regression_delta < 0:
        raise ValueError(
            f"max_regression_delta 必须 ≥ 0（表达可容忍的退化预算），收到 {max_regression_delta!r}"
        )
    ab = BenchmarkABRun(
        scenario_set_id=report.scenario_set_id,
        report_baseline=baseline,
        report_candidate=report,
        vary=vary,  # type: ignore[arg-type]
    )
    ab.assert_conserved()  # fail-closed：两臂不可比直接报错（装配错误，非回归判断）
    diff = BenchmarkDiff.from_reports(baseline, report, vary=vary)

    exceeded = False
    if max_regression_delta is not None:
        for d in diff.deltas:
            if d.metric in _REGRESSION_MONITORED and d.delta > max_regression_delta:
                exceeded = True
                break

    return RegressionReport(
        scenario_set_id=report.scenario_set_id,
        vary=vary,
        diff=diff,
        max_regression_delta=max_regression_delta,
        regressions_exceeded=exceeded,
    )


def baseline_path(scenario_set_id: str, baselines_dir: Path = BASELINES_DIR) -> Path:
    """基线 JSON 路径（D7：``<baselines_dir>/<scenario_set_id>.json``）。"""
    return Path(baselines_dir) / f"{scenario_set_id}.json"


def load_baseline_report(
    scenario_set_id: str, baselines_dir: Path = BASELINES_DIR
) -> BenchmarkReport:
    """加载提交的基线 ``BenchmarkReport``（T10 基线可回放）。

    基线 JSON 须由 ``BenchmarkReport.to_dict`` / ``canonical_dict`` 结构序列化；反序列化经
    ``BenchmarkReport.from_dict``（恢复 ``provenance`` 供守恒校验）。基线文件不存在即报错
    （fail-closed，不静默降级）。
    """
    p = baseline_path(scenario_set_id, baselines_dir)
    return load_baseline_report_path(p)


def load_baseline_report_path(path: Path | str) -> BenchmarkReport:
    """按**路径**加载基线 ``BenchmarkReport``（CLI ``--baseline`` 入口用）。

    与 ``load_baseline_report`` 同源，仅 baselines 目录默认约定改为任意显式路径
    （如刚生成的临时基线）。基线文件不存在即报错（fail-closed）。顶层 JSON 须为对象
    （M6：列表/标量会被 ``from_dict`` 误解，提前显式报错而非吞到内层 ``TypeError``）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"基线 JSON 不存在：{p}（须先提交 last-good BenchmarkReport 或用 "
            "--write-baseline 生成；基线 bump 须在 PR 注明 benchmark-baseline-bump）"
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(
            f"基线 JSON 顶层须为对象，收到 {type(data).__name__}（路径：{p}）"
        )
    return BenchmarkReport.from_dict(data)


def write_baseline_report(path: str | Path, report: BenchmarkReport) -> None:
    """落盘基线 JSON（canonical_dict），复用 ``write_report`` 双守卫（C2/M8，fail-closed）。

    守卫逻辑集中在 ``BenchmarkReport.write_canonical_report``（与 ``write_report`` 同向：
    脱敏 + 父目录须存在、不 ``mkdir parents``），``ab_runner`` 仅做委托，自身不 import
    ``analysis`` 重链（守 T9 模块边界）。

    基线 bump 是「显式、Owner 评审动作」（ADR-0033 D7）：覆盖既有文件须由调用方显式确认
    （CLI 经 ``--force``），本函数自身不做 overwrite 决策，只保证写入内容安全。
    """
    report.write_canonical_report(path)


__all__ = [
    "BASELINES_DIR",
    "VARY_AXES",
    "VARY_CODE",
    "VARY_MODEL",
    "BenchmarkABConservationError",
    "BenchmarkABRun",
    "BenchmarkDiff",
    "MetricDelta",
    "RegressionReport",
    "baseline_path",
    "evaluate_regression",
    "load_baseline_report",
    "load_baseline_report_path",
    "write_baseline_report",
]

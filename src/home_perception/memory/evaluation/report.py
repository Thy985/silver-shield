"""E-1 评估报告生成（DESIGN-memory-evaluation.md §8 / §9 / §11）。

三层职责（不混）：

1. **统计汇总层（§8.1）**：``summarize`` / ``paired_delta_summary`` —— 纯函数，给出
   ``mean`` / ``std`` / 95% 置信区间（t 区间 + 内置临界值表，零依赖、确定性、可单测）。
   E-1A（N=3）样本量过小，统计量仅作占位与结构验证；E-1B（20~50 case）才有解释力。
2. **Memory Value Score 层（§8.2）**：四 term 归一化到 ``[0,1]`` 后加权复合。
   **Score 仅用于报告与横向比较，绝不替代 §9 Hard Gate**；E-1B 前阈值未标定
   （``calibrated=False``），不得据 Score 判定「Memory 有用」。
3. **渲染 / 落盘层（§11）**：``report_to_dict`` / ``render_markdown`` 纯函数，
   ``write_report`` 为唯一 I/O 边界，产出 ``e1_report.json`` + ``e1_report.md``。

Early Detection 在 E-1A 无时序数据（``status == "na"``），该 term **不参与**评分：
剩余三 term 按原比例重归一化，报告标记 ``partial=True`` 并在 Markdown 中显式写
``N/A（data-gated → E-1B）``，避免用 0 分冒充「无提前量」而污染 Score。

边界：本模块只消费 ``CaseEvaluation``（Reasoning 层观测），不触碰
``DecisionPolicy`` / ``risk_score`` / ``warning``（守 ADR-0010，见 §10）。
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from home_perception.memory.consumer.replay_dataset import MemoryReplayDataset
from home_perception.memory.evaluation.ab_runner import run_ab_dataset
from home_perception.memory.evaluation.ground_truth import get_ground_truth
from home_perception.memory.evaluation.metrics import CaseEvaluation, evaluate_case
from home_perception.memory.evaluation.temporal import (
    evaluate_temporal_case,
    load_temporal_dataset,
)

# ---------------------------------------------------------------------------
# §8.1 统计汇总（t 区间，零依赖）
# ---------------------------------------------------------------------------
# 双侧 95% t 临界值表（df → t）。查不到的 df 取「最近的较小 df」，临界值更大、区间更宽（保守）。
_T_CRITICAL_95: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
    40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984,
}


def t_critical_95(df: int) -> float:
    """双侧 95% t 临界值；表外 df 取最近的较小 df（保守、区间更宽）。"""
    if df < 1:
        raise ValueError(f"自由度必须 ≥ 1，收到 {df}")
    if df in _T_CRITICAL_95:
        return _T_CRITICAL_95[df]
    smaller = [k for k in _T_CRITICAL_95 if k < df]
    return _T_CRITICAL_95[max(smaller)]


@dataclass(frozen=True)
class StatSummary:
    """一个指标跨 case 的统计汇总（§8.1）。``n < 2`` 时 CI 不可估 → ``None``。"""

    n: int
    mean: float
    std: float
    ci95_low: float | None
    ci95_high: float | None


def summarize(values: Sequence[float]) -> StatSummary:
    """均值 / 样本标准差（ddof=1）/ 95% t 区间。空集 → 全零 + CI ``None``（无信息）。"""
    n = len(values)
    if n == 0:
        return StatSummary(n=0, mean=0.0, std=0.0, ci95_low=None, ci95_high=None)
    mean = sum(values) / n
    if n < 2:
        return StatSummary(n=n, mean=mean, std=0.0, ci95_low=None, ci95_high=None)
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(variance)
    half = t_critical_95(n - 1) * std / math.sqrt(n)
    return StatSummary(n=n, mean=mean, std=std, ci95_low=mean - half, ci95_high=mean + half)


def paired_delta_summary(evaluations: Sequence[CaseEvaluation]) -> StatSummary:
    """FN 配对 Δ = ``fn_b − fn_m``（同一 case 内配对；> 0 表示 Memory 减少漏报，§8.1）。"""
    return summarize([float(ev.fn_b - ev.fn_m) for ev in evaluations])


def explanation_pass_summary(evaluations: Sequence[CaseEvaluation]) -> StatSummary:
    """解释质量逐 case 得分（``0.5·Q2 + 0.5·Q3``，与 §8.2 ``Explanation_term`` 同口径）。"""
    return summarize([_explanation_score(ev) for ev in evaluations])


@dataclass(frozen=True)
class StatsBundle:
    """§8.1 统计汇总集合。E-1A（N=3）仅作结构占位，解释力见 E-1B。"""

    fn_delta: StatSummary
    explanation_pass: StatSummary
    wilcoxon_p: float | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# §8.2 Memory Value Score（报告用，非 gate）
# ---------------------------------------------------------------------------
BASE_WEIGHTS: dict[str, float] = {
    "fn": 0.40,
    "early_detection": 0.30,
    "explanation": 0.20,
    "fp": 0.10,
}

#: Early Detection 标定窗口（分钟，§8.2；E-1B 标定）
LEAD_TIME_WINDOW_MINUTES = 60.0

#: FP 严重度满量程（§4.2 冻结映射上界 ESCALATE_COMMUNITY=3）
_FP_SEVERITY_SPAN = 3.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _explanation_score(ev: CaseEvaluation) -> float:
    return (0.5 if ev.q2 else 0.0) + (0.5 if ev.q3 else 0.0)


def fn_term(evaluations: Sequence[CaseEvaluation]) -> float:
    """``clamp((FN_B − FN_M) / max(FN_B, 1), 0, 1)`` 的跨 case 均值。

    ``FN_B = FN_M = 0`` → ``0``（无信息，中性）；``FN_M > FN_B`` → 截 ``0``（不奖励恶化）。
    """
    return _mean([_clamp((ev.fn_b - ev.fn_m) / max(ev.fn_b, 1)) for ev in evaluations])


def explanation_term(evaluations: Sequence[CaseEvaluation]) -> float:
    """``mean(0.5·Q2 + 0.5·Q3)``。**不含 Q1**，避免与 ``fn_term`` 重复计权（§8.2）。"""
    return _mean([_explanation_score(ev) for ev in evaluations])


def fp_term(evaluations: Sequence[CaseEvaluation]) -> float:
    """``1 − max(0, severity(M_hint) − upper(acceptable)) / 3`` 的跨 case 均值。

    两臂均未超上限 → ``1``；超出按严重度差线性折扣到 ``0``。
    """
    return _mean(
        [_clamp(1.0 - min(ev.fp_excess, _FP_SEVERITY_SPAN) / _FP_SEVERITY_SPAN) for ev in evaluations]
    )


def early_detection_term(evaluations: Sequence[CaseEvaluation]) -> float | None:
    """Early Detection term。

    - ``na``（无时序数据）→ 排除（未测量 ≠ 无提前量）。
    - ``computed``（两臂均检出）→ ``clamp(LeadTime / W, 0, 1)``（>0 表示 Memory 更早）。
    - ``missing_detection``，按 ``missing_arm`` 分三种（§4.4 / 评审 issue 1）：
        * ``"memory"`` → ``0.0``（M 未检测，DESIGN §8.2 规定 0）；
        * ``"baseline"`` → ``1.0``（M 检出而 B 从未检出，Memory 最强正向提前量，按明确上界记正向）；
        * ``"both"`` / 未指定 → 排除（两臂均缺失无法判断，N/A，未测量 ≠ 0）。
    - 全部排除 → ``None``（不参与评分）。
    """
    scores: list[float] = []
    for ev in evaluations:
        ed = ev.early_detection
        if ed.status == "na":
            continue
        if ed.status == "computed" and ed.lead_time_minutes is not None:
            scores.append(_clamp(ed.lead_time_minutes / LEAD_TIME_WINDOW_MINUTES))
            continue
        if ed.status == "missing_detection":
            if ed.missing_arm == "memory":
                scores.append(0.0)
            elif ed.missing_arm == "baseline":
                scores.append(1.0)
            else:
                # "both" 或未指定 → 无信息，排除（未测量 ≠ 0）
                continue
            continue
        # 未知 status → 防御性排除
        continue
    if not scores:
        return None
    return _mean(scores)


@dataclass(frozen=True)
class ScoreTerms:
    """四 term 归一化值；``None`` 表示该 term 无数据（N/A，不参与评分）。

    空数据集时四个 term 均为 ``None``（compute_score_terms 返回），以区别于「真实
    样本全部得 0」——后者是有效 0，前者是「未测量」（评审 issue 2）。
    """

    fn: float | None
    early_detection: float | None
    explanation: float | None
    fp: float | None


@dataclass(frozen=True)
class MemoryValueScore:
    """§8.2 加权复合分。**报告用，非 Hard Gate**；E-1B 前未标定阈值。

    ``valid``：评分是否有意义。空数据集 / 全 term 缺失 → ``False`` 且 ``score=None``，
    明确表达「无证据」，绝不等价于「Memory 无价值」（未测量 ≠ 0，评审 issue 2）。
    """

    terms: ScoreTerms
    weights: dict[str, float]
    score: float | None
    partial: bool
    calibrated: bool = False
    valid: bool = True
    note: str = ""


def compute_score_terms(evaluations: Sequence[CaseEvaluation]) -> ScoreTerms:
    """从 case 评估集合算出四 term（§8.2）。空数据集 → 四 term 全 ``None``（未测量）。"""
    if not evaluations:
        return ScoreTerms(fn=None, early_detection=None, explanation=None, fp=None)
    return ScoreTerms(
        fn=fn_term(evaluations),
        early_detection=early_detection_term(evaluations),
        explanation=explanation_term(evaluations),
        fp=fp_term(evaluations),
    )


def compute_memory_value_score(terms: ScoreTerms, n_cases: int = 0) -> MemoryValueScore:
    """按 §8.2 权重复合；缺失 term 剔除后按原比例重归一化，并标记 ``partial``。

    ``n_cases``：参与评分的 case 数（由 ``build_report`` 透传）。``n_cases <= 0`` 或
    全部 term 缺失 → 评分无效（``valid=False``、``score=None``），报告层渲染为 N/A，
    明确表达「无证据」，不得写成有效零分（未测量 ≠ 0，评审 issue 2）。
    """
    values: dict[str, float | None] = {
        "fn": terms.fn,
        "early_detection": terms.early_detection,
        "explanation": terms.explanation,
        "fp": terms.fp,
    }
    active = {k: w for k, w in BASE_WEIGHTS.items() if values[k] is not None}
    if n_cases <= 0 or not active:
        return MemoryValueScore(
            terms=terms,
            weights={},
            score=None,
            partial=False,
            calibrated=False,
            valid=False,
            note=(
                "无 case 或全 term 缺失：Score 无法计算（N/A），不等价于 Memory 无价值"
                "（未测量 ≠ 0）。"
            ),
        )
    total = sum(active.values())
    weights = {k: w / total for k, w in active.items()}
    score = sum(weights[k] * float(values[k]) for k in weights)  # type: ignore[arg-type]
    partial = len(active) < len(BASE_WEIGHTS)
    missing = sorted(set(BASE_WEIGHTS) - set(active))
    note = "Score 仅供报告 / 横向比较，非 Hard Gate；E-1B 前阈值未标定。"
    if partial:
        note += f" 缺失 term（已按原比例重归一化剩余权重）: {', '.join(missing)}。"
    return MemoryValueScore(
        terms=terms,
        weights=weights,
        score=score,
        partial=partial,
        calibrated=False,
        valid=True,
        note=note,
    )


# ---------------------------------------------------------------------------
# §9 Hard Gate 汇总 + 报告对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HardGateSummary:
    """§9 硬门槛汇总：**先于** Memory Value Score，任一 case 失败即整体失败。"""

    total: int
    passed: int
    failed_case_ids: tuple[str, ...]
    all_pass: bool


def summarize_hard_gate(evaluations: Sequence[CaseEvaluation]) -> HardGateSummary:
    """汇总 Hard Gate；空集视为**不通过**（无证据 ≠ 通过）。"""
    failed = tuple(ev.case_id for ev in evaluations if not ev.hard_gate_pass)
    passed = len(evaluations) - len(failed)
    return HardGateSummary(
        total=len(evaluations),
        passed=passed,
        failed_case_ids=failed,
        all_pass=bool(evaluations) and not failed,
    )


@dataclass(frozen=True)
class E1Report:
    """E-1 评估报告（§11 产出 ``e1_report.json`` + ``e1_report.md``）。"""

    stage: str
    dataset_id: str
    generated_at: str
    cases: tuple[CaseEvaluation, ...]
    hard_gate: HardGateSummary
    stats: StatsBundle
    score: MemoryValueScore
    notes: tuple[str, ...] = field(default_factory=tuple)


_E1A_STATS_NOTE = (
    "E-1A 样本量 N=3，统计量仅作结构占位；均值 / CI 无推断效力，"
    "解释力需 E-1B（20~50 真实 CCTV case）。Wilcoxon signed-rank 留待 E-1B。"
)


def build_report(
    evaluations: Sequence[CaseEvaluation],
    dataset_id: str,
    stage: str = "E-1A",
    generated_at: str | None = None,
    stats_note: str = _E1A_STATS_NOTE,
    extra_notes: Sequence[str] = (),
) -> E1Report:
    """从 case 评估集合构建报告（纯函数：``generated_at`` 可注入以保证可复现）。"""
    terms = compute_score_terms(evaluations)
    score = compute_memory_value_score(terms, n_cases=len(evaluations))
    stats = StatsBundle(
        fn_delta=paired_delta_summary(evaluations),
        explanation_pass=explanation_pass_summary(evaluations),
        wilcoxon_p=None,
        note=stats_note,
    )
    notes = [
        "判定顺序：Hard Gate（§9）先于 Memory Value Score（§8.2），绝不反向。",
        "suggested_action_hint 仅为 Reasoning 层观测代理，非 Decision 输出（ADR-0010，§10）。",
    ]
    if terms.early_detection is None:
        notes.append("Early Detection：N/A（data-gated → E-1B），不计入 Hard Gate 与 Score。")
    notes.extend(extra_notes)
    return E1Report(
        stage=stage,
        dataset_id=dataset_id,
        generated_at=generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        cases=tuple(evaluations),
        hard_gate=summarize_hard_gate(evaluations),
        stats=stats,
        score=score,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def report_to_dict(report: E1Report) -> dict:
    """转为 JSON 可序列化 dict（嵌套 dataclass 递归展开，tuple → list）。"""
    return asdict(report)


def _fmt(value: float | None, digits: int = 3) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def _fmt_ci(stat: StatSummary) -> str:
    if stat.ci95_low is None or stat.ci95_high is None:
        return "N/A"
    return f"[{stat.ci95_low:.3f}, {stat.ci95_high:.3f}]"


def render_markdown(report: E1Report) -> str:
    """渲染可审阅的 Markdown 报告（纯函数）。"""
    hg = report.hard_gate
    gate_text = "✅ PASS" if hg.all_pass else "❌ FAIL"
    lines: list[str] = [
        f"# E-1 Memory Value Evaluation — {report.stage}",
        "",
        f"- 数据集：`{report.dataset_id}`",
        f"- 生成时间：{report.generated_at}",
        f"- **Hard Gate（§9）**：{gate_text}（{hg.passed}/{hg.total} 通过）",
    ]
    if hg.failed_case_ids:
        lines.append(f"- 失败 case：{', '.join(hg.failed_case_ids)}")
    lines += [
        "",
        "## 1. 逐 case 指标（§4）",
        "",
        "| case | Q1 grounded | Q2 hist-ref | Q3 grounding | FP ≤ 上限 | FN (M/B) | Early Detection | Hard Gate |",
        "|------|-------------|-------------|--------------|-----------|----------|-----------------|-----------|",
    ]
    mark = {True: "✅", False: "❌"}
    for ev in report.cases:
        ed = ev.early_detection
        if ed.status == "na":
            ed_text = "N/A"
        elif ed.status == "missing_detection":
            ed_text = f"missing_detection({ed.missing_arm})"
        else:
            ed_text = f"{ed.status}({_fmt(ed.lead_time_minutes, 1)}min)"
        lines.append(
            f"| `{ev.case_id}` | {mark[ev.q1]} | {mark[ev.q2]} | {mark[ev.q3]} | "
            f"{mark[ev.fp]} | {ev.fn_m} / {ev.fn_b} | {ed_text} | {mark[ev.hard_gate_pass]} |"
        )
    stats = report.stats
    lines += [
        "",
        "## 2. 统计汇总（§8.1）",
        "",
        "| 量 | n | mean | std | 95% CI |",
        "|----|---|------|-----|--------|",
        (
            f"| FN 配对 Δ (`fn_b − fn_m`) | {stats.fn_delta.n} | {_fmt(stats.fn_delta.mean)} | "
            f"{_fmt(stats.fn_delta.std)} | {_fmt_ci(stats.fn_delta)} |"
        ),
        (
            f"| 解释质量 (`0.5·Q2+0.5·Q3`) | {stats.explanation_pass.n} | "
            f"{_fmt(stats.explanation_pass.mean)} | {_fmt(stats.explanation_pass.std)} | "
            f"{_fmt_ci(stats.explanation_pass)} |"
        ),
        "",
        f"> {stats.note}",
        "",
        "## 3. Memory Value Score（§8.2，**非 Hard Gate**）",
        "",
        "| term | 值 | 生效权重 |",
        "|------|----|----------|",
    ]
    term_values: dict[str, float | None] = {
        "fn": report.score.terms.fn,
        "early_detection": report.score.terms.early_detection,
        "explanation": report.score.terms.explanation,
        "fp": report.score.terms.fp,
    }
    for key, base_w in BASE_WEIGHTS.items():
        eff = report.score.weights.get(key)
        eff_text = "—（N/A，已剔除）" if eff is None else f"{eff:.3f}（基准 {base_w:.2f}）"
        lines.append(f"| `{key}` | {_fmt(term_values[key])} | {eff_text} |")
    lines += [
        "",
        (
            f"**Score = {report.score.score:.3f}**"
            f"（partial={report.score.partial}, calibrated={report.score.calibrated}）"
            if report.score.valid
            else "**Score = N/A（无 case，无法计算；不等价于 Memory 无价值）**"
        ),
        "",
        f"> {report.score.note}",
        "",
        "## 4. 边界声明",
        "",
    ]
    lines += [f"- {n}" for n in report.notes]
    lines.append("")
    return "\n".join(lines)


def write_report(report: E1Report, out_dir: str | Path) -> tuple[Path, Path]:
    """把报告写为 ``e1_report.json`` + ``e1_report.md``（本模块唯一 I/O 边界）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "e1_report.json"
    md_path = out / "e1_report.md"
    json_path.write_text(
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# E-1A 端到端便利入口（dataset → A/B → 指标 → 报告）
# ---------------------------------------------------------------------------
DEFAULT_FIXTURE_ROOT = "tests/fixtures/memory_replay"
DEFAULT_OUT_DIR = "artifacts/e1"


def evaluate_dataset(dataset: MemoryReplayDataset) -> list[CaseEvaluation]:
    """跑完整 A/B 并逐 case 评估（case_id 未登记 GroundTruth 时抛 ``KeyError``）。"""
    return [
        evaluate_case(run.result_memory, run.result_baseline, get_ground_truth(run.case_id))
        for run in run_ab_dataset(dataset)
    ]


def run_e1a_report(
    fixture_root: str = DEFAULT_FIXTURE_ROOT,
    generated_at: str | None = None,
) -> E1Report:
    """E-1A 端到端：加载 M0 fixtures → A/B → 四指标 → 报告对象（不落盘）。

    Early Detection 为 ``na()``（E-1A 无时序 step 数据）。
    """
    dataset = MemoryReplayDataset(fixture_root)
    return build_report(
        evaluate_dataset(dataset),
        dataset_id=fixture_root,
        stage="E-1A",
        generated_at=generated_at,
    )


def evaluate_temporal_dataset(fixture_root: str) -> list[CaseEvaluation]:
    """加载 ``<fixture_root>/temporal/*`` 时序校准 fixture → 逐 case A/B + 四指标 + Early Detection。"""
    return [evaluate_temporal_case(case) for case in load_temporal_dataset(fixture_root)]


def run_e1c_report(
    fixture_root: str = DEFAULT_FIXTURE_ROOT,
    generated_at: str | None = None,
) -> E1Report:
    """E-1c 端到端：加载时序校准 fixture → 时序 A/B → 四指标 + LeadTime → 报告对象。

    Early Detection 由 ``run_temporal_ab_case`` 计算（computed / missing_detection），
    不再为 N/A；Hard Gate 与 E-1a 一致（末 step 输入等价于 M0 单事件输入）。
    """
    evaluations = evaluate_temporal_dataset(fixture_root)
    return build_report(
        evaluations,
        dataset_id=fixture_root,
        stage="E-1A（含 E-1c 时序校准）",
        generated_at=generated_at,
        extra_notes=(
            (
                "Early Detection：已由 E-1c 时序 fixture 计算（LeadTime 时间戳校准），"
                "非 N/A；na 表示该 case 两臂均未达 ESCALATE/NOTIFY 检测阈值（如 repeat_visitor→MONITOR）。"
            ),
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 实现（入口见 ``__main__.py``）：生成 E-1 报告并落盘。

    Hard Gate 失败 → 退出码 1，可直接作为 CI gate。``--stage e1c`` 改用时序校准 fixture。
    """
    parser = argparse.ArgumentParser(description="生成 E-1 Memory Value Evaluation 报告")
    parser.add_argument("--fixtures", default=DEFAULT_FIXTURE_ROOT, help="replay fixtures 根目录")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="报告输出目录")
    parser.add_argument(
        "--stage",
        choices=("e1a", "e1c"),
        default="e1a",
        help="e1a=仅 M0 三 case（Early Detection N/A）；e1c=含时序校准（算 LeadTime）",
    )
    args = parser.parse_args(argv)

    if args.stage == "e1c":
        report = run_e1c_report(args.fixtures)
        stage_label = "E-1c"
    else:
        report = run_e1a_report(args.fixtures)
        stage_label = "E-1A"
    json_path, md_path = write_report(report, args.out)
    print(f"[{stage_label}] Hard Gate: {'PASS' if report.hard_gate.all_pass else 'FAIL'} "
          f"({report.hard_gate.passed}/{report.hard_gate.total})")
    score_str = f"{report.score.score:.3f}" if report.score.valid else "N/A"
    print(f"[{stage_label}] Memory Value Score: {score_str} (partial={report.score.partial})")
    print(f"[{stage_label}] 报告: {json_path} / {md_path}")
    return 0 if report.hard_gate.all_pass else 1


__all__ = [
    "BASE_WEIGHTS",
    "DEFAULT_FIXTURE_ROOT",
    "DEFAULT_OUT_DIR",
    "LEAD_TIME_WINDOW_MINUTES",
    "E1Report",
    "HardGateSummary",
    "MemoryValueScore",
    "ScoreTerms",
    "StatSummary",
    "StatsBundle",
    "build_report",
    "compute_memory_value_score",
    "compute_score_terms",
    "early_detection_term",
    "evaluate_dataset",
    "evaluate_temporal_dataset",
    "explanation_pass_summary",
    "explanation_term",
    "fn_term",
    "fp_term",
    "main",
    "paired_delta_summary",
    "render_markdown",
    "report_to_dict",
    "run_e1a_report",
    "run_e1c_report",
    "summarize",
    "summarize_hard_gate",
    "t_critical_95",
    "write_report",
]

"""E-1 Memory Value Evaluation 指标（纯函数，DESIGN-memory-evaluation.md §4 / §8）。

四指标：
- Explanation Quality：Q1 Grounded Finding Gain / Q2 历史引用 / Q3 Pattern Grounding
- False Positive（FP）：对照 Ground Truth 上限，两臂均不恶化
- False Negative（FN）：基于 pattern finding + required_evidence 双判据（非 hint）
- Early Detection：定义保留（§4.4），E-1A 无时序 step 数据 → ``EarlyDetectionResult.na()``

纯函数：无 I/O、无随机、无外部状态，可独立单测。所有观测停留在 ``ReasoningResult``
层（Shadow），绝不触碰 DecisionPolicy（守 ADR-0010，见 §10）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from home_perception.memory.consumer.contracts import ReasoningResult, SourceRef
from home_perception.memory.evaluation.ground_truth import GroundTruthRecord

# ---------------------------------------------------------------------------
# severity 冻结映射（§4.2）
# ---------------------------------------------------------------------------
HINT_SEVERITY: dict[str | None, int] = {
    None: 0,
    "MONITOR": 1,
    "NOTIFY_FAMILY": 2,
    "ESCALATE_COMMUNITY": 3,
}


def hint_severity(hint: str | None) -> int:
    """把 hint 映射到严重度（§4.2 冻结映射，含 None=0）。"""
    if hint not in HINT_SEVERITY:
        raise ValueError(f"未知 suggested_action_hint: {hint!r}")
    return HINT_SEVERITY[hint]


# ---------------------------------------------------------------------------
# 历史锚定判定
# ---------------------------------------------------------------------------
# Q3 合法的非 current_event 锚点（§4.1 Q3 条件 2）
_HISTORICAL_SOURCES = frozenset(
    {"visitor_profile", "risk_pattern", "historical_context", "conflicts", "previous_actions"}
)

# explanation 中用于判定「历史概念被提及」的关键词（§4.1 Q3 条件 3）
_CONCEPT_KEYWORDS = ("历史画像", "风险模式", "历史记录", "冲突", "画像", "模式", "历史")


def _refs_by_source(result: ReasoningResult) -> dict[str, list[SourceRef]]:
    by_source: dict[str, list[SourceRef]] = {}
    for sr in result.source_refs:
        by_source.setdefault(sr.source, []).append(sr)
    return by_source


def _historical_anchors(result: ReasoningResult) -> list[SourceRef]:
    return [sr for sr in result.source_refs if sr.source in _HISTORICAL_SOURCES]


def _pattern_covered(
    result: ReasoningResult,
    pattern: str,
    refs: dict[str, list[SourceRef]],
) -> bool:
    """该 pattern 是否被 Memory 臂识别（risk_pattern tag / conflicts type / finding 文本）。"""
    if any(sr.ref == pattern for sr in refs.get("risk_pattern", [])):
        return True
    if any(sr.ref == pattern for sr in refs.get("conflicts", [])):
        return True
    # 兜底：finding 文本直接包含该模式标签
    return any(pattern in finding for finding in result.findings)


def _patterns_covered(result: ReasoningResult, expected_patterns: Iterable[str]) -> bool:
    refs = _refs_by_source(result)
    return all(_pattern_covered(result, pat, refs) for pat in expected_patterns)


# ---------------------------------------------------------------------------
# Q1 — Grounded Finding Gain（§4.1）
# ---------------------------------------------------------------------------
def metric_q1_grounded_gain(
    result_m: ReasoningResult,
    result_b: ReasoningResult,
    gt: GroundTruthRecord,
) -> bool:
    """Memory 臂相对 Baseline 新增的、有历史依据且命中预期模式的发现。

    操作化（忠实于 §4.1 ``(findings(M)\\findings(B)) ∩ HistoricalGrounded ∩ ExpectedPattern``）：
    (1) 两臂 findings 集合差非空（Memory 确有新增发现）；
    (2) Memory 臂存在 ``historical_context`` 溯源锚点（非「话多式」膨胀）；
    (3) Memory 臂覆盖全部 ``expected_pattern``（模式检测）。
    """
    gain = set(result_m.findings) - set(result_b.findings)
    if not gain:
        return False
    if not any(sr.source == "historical_context" for sr in result_m.source_refs):
        return False
    return _patterns_covered(result_m, gt.expected_pattern)


# ---------------------------------------------------------------------------
# Q2 — 历史引用（强，§4.1）
# ---------------------------------------------------------------------------
def metric_q2_historical_reference(result_m: ReasoningResult) -> bool:
    """Memory 臂 explanation 至少一条溯源指向具体历史事件（historical_context 锚点）。"""
    return any(sr.source == "historical_context" for sr in result_m.source_refs)


# ---------------------------------------------------------------------------
# Q3 — Pattern Grounding（证据链，§4.1）
# ---------------------------------------------------------------------------
def metric_q3_pattern_grounding(result_m: ReasoningResult) -> bool:
    """验证 ``Memory evidence → SourceRef → findings(值) → explanation(概念)`` 可追溯链。

    全部满足：① explanation 非空（契约保证）；② ≥1 条非 current_event 历史锚点；
    ③ 该锚点携带具体历史值（detail / ref 非空）且 explanation 提及历史概念。
    """
    if not result_m.explanation or not result_m.explanation.strip():
        return False
    anchors = _historical_anchors(result_m)
    if not anchors:
        return False
    mentions = any(kw in result_m.explanation for kw in _CONCEPT_KEYWORDS)
    has_value = any(
        (sr.detail and sr.detail.strip()) or (sr.ref and sr.ref.strip()) for sr in anchors
    )
    return mentions and has_value


# ---------------------------------------------------------------------------
# FP — False Positive（不恶化约束，§4.2）
# ---------------------------------------------------------------------------
def acceptable_upper(gt: GroundTruthRecord) -> int:
    """Ground Truth 可接受 hint 的严重度上限；未标注 → 0（仅允许 ``None``，最严）。"""
    if not gt.acceptable_hint:
        return 0
    return max(hint_severity(h) for h in gt.acceptable_hint)


def metric_fp(
    result_m: ReasoningResult,
    result_b: ReasoningResult,
    gt: GroundTruthRecord,
) -> bool:
    """两臂 hint 均不超过 Ground Truth 可接受上限（对照 GT，非两臂差值）。"""
    upper = acceptable_upper(gt)
    return (
        hint_severity(result_m.suggested_action_hint) <= upper
        and hint_severity(result_b.suggested_action_hint) <= upper
    )


def fp_severity_excess(result_m: ReasoningResult, gt: GroundTruthRecord) -> int:
    """Memory 臂 hint 超出 GT 上限的严重度差（§8.2 ``FP_term`` 输入）；未超 → 0。

    与 ``metric_fp`` 的布尔判定互补：布尔进 Hard Gate（§9），本函数给 Score 提供
    可折扣的连续量，避免报告层反查 Ground Truth。
    """
    return max(0, hint_severity(result_m.suggested_action_hint) - acceptable_upper(gt))


# ---------------------------------------------------------------------------
# FN — False Negative（基于 pattern finding，§4.3）
# ---------------------------------------------------------------------------
def _match_one_evidence(ev: str, refs: dict[str, list[SourceRef]]) -> bool:
    """解析 ``<path> = <value>`` 并对照 SourceRef（§5 冻结语法）。"""
    if "=" not in ev:
        return False
    path, value = ev.split("=", 1)
    path, value = path.strip(), value.strip()
    if path == "historical_context[].record_id":
        return any(
            sr.source == "historical_context" and sr.ref == value
            for sr in refs.get("historical_context", [])
        )
    if path.startswith("visitor_profile."):
        field = path[len("visitor_profile.") :]
        return any(
            sr.source == "visitor_profile"
            and sr.detail is not None
            and f"{field}={value}" in sr.detail
            for sr in refs.get("visitor_profile", [])
        )
    if path == "risk_pattern.tags":
        return any(
            sr.source == "risk_pattern" and sr.ref == value
            for sr in refs.get("risk_pattern", [])
        )
    if path == "conflicts.type":
        return any(
            sr.source == "conflicts" and sr.ref == value
            for sr in refs.get("conflicts", [])
        )
    return False


def _match_required_evidence(
    result: ReasoningResult, required_evidence: Iterable[str]
) -> bool:
    refs = _refs_by_source(result)
    return all(_match_one_evidence(ev, refs) for ev in required_evidence)


def _fn_uncovered(result: ReasoningResult, gt: GroundTruthRecord) -> int:
    """该臂未覆盖（pattern 未检出 或 required_evidence 未锚定）的 expected_pattern 数。"""
    if not gt.expected_pattern:
        return 0
    ev_ok = _match_required_evidence(result, gt.required_evidence)
    refs = _refs_by_source(result)
    covered = sum(
        1 for pat in gt.expected_pattern if _pattern_covered(result, pat, refs) and ev_ok
    )
    return len(gt.expected_pattern) - covered


def metric_fn(
    result_m: ReasoningResult,
    result_b: ReasoningResult,
    gt: GroundTruthRecord,
) -> tuple[int, int]:
    """返回 ``(fn_m, fn_b)``；Baseline 无历史 → fn_b = |expected_pattern|，Memory 补全 → 趋近 0。"""
    return _fn_uncovered(result_m, gt), _fn_uncovered(result_b, gt)


# ---------------------------------------------------------------------------
# Early Detection（§4.4，定义保留 / E-1A 不计算）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EarlyDetectionResult:
    """Early Detection 结果（§4.4）。

    - ``na``：E-1A 无时序 step 数据，标记 ``N/A（data-gated → E-1B）``，不计入 Hard Gate。
    - ``computed``：LeadTime 已算（供 E-1c / E-1B）。
    - ``missing_detection``：某臂始终未检测，记最坏（退化）。
    """

    status: str  # "na" | "computed" | "missing_detection"
    lead_time_minutes: float | None = None
    step_delta: int | None = None
    detail: str = ""

    @classmethod
    def na(cls, detail: str = "data-gated → E-1B") -> EarlyDetectionResult:
        return cls(status="na", lead_time_minutes=None, step_delta=None, detail=detail)

    @classmethod
    def computed(cls, lead_time_minutes: float, step_delta: int | None) -> EarlyDetectionResult:
        return cls(
            status="computed",
            lead_time_minutes=lead_time_minutes,
            step_delta=step_delta,
            detail="lead_time>0 提前 / =0 持平 / <0 退化",
        )

    @classmethod
    def missing_detection(cls, arm: str) -> EarlyDetectionResult:
        return cls(
            status="missing_detection",
            lead_time_minutes=None,
            step_delta=None,
            detail=f"{arm} 臂未检测，记最坏（退化）",
        )


def compute_lead_time(
    baseline_detection_ts: datetime | None,
    memory_detection_ts: datetime | None,
    baseline_step: int | None = None,
    memory_step: int | None = None,
) -> EarlyDetectionResult:
    """§4.4 LeadTime = ts(B) − ts(M)；>0 表示 Memory 更早检测。

    某臂缺失检测 → 最坏（退化，``missing_detection``）。供 E-1c / E-1B 调用；
    E-1A（单当前事件、无 step 序列）不调用，evaluate_case 直接返回 ``na()``。
    """
    if baseline_detection_ts is None or memory_detection_ts is None:
        missing = "baseline" if baseline_detection_ts is None else "memory"
        return EarlyDetectionResult.missing_detection(missing)
    delta_min = (baseline_detection_ts - memory_detection_ts).total_seconds() / 60.0
    step_delta = None
    if baseline_step is not None and memory_step is not None:
        step_delta = baseline_step - memory_step
    return EarlyDetectionResult.computed(delta_min, step_delta)


# ---------------------------------------------------------------------------
# Case 汇总（Hard Gate，§9）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CaseEvaluation:
    """单个 case 的 E-1A 评估结果（Hard Gate 先于 Memory Value Score）。"""

    case_id: str
    q1: bool
    q2: bool
    q3: bool
    fp: bool
    fn_m: int
    fn_b: int
    early_detection: EarlyDetectionResult
    hard_gate_pass: bool
    notes: tuple[str, ...]
    # Memory 臂 hint 超出 GT 上限的严重度差（§8.2 FP_term 输入；Hard Gate 只看 fp 布尔）
    fp_excess: int = 0


def evaluate_case(
    result_m: ReasoningResult,
    result_b: ReasoningResult,
    gt: GroundTruthRecord,
) -> CaseEvaluation:
    """对一个 case 的两臂 ReasoningResult 计算 E-1A 四指标 + Hard Gate（§9）。"""
    q1 = metric_q1_grounded_gain(result_m, result_b, gt)
    q2 = metric_q2_historical_reference(result_m)
    q3 = metric_q3_pattern_grounding(result_m)
    fp = metric_fp(result_m, result_b, gt)
    fn_m, fn_b = metric_fn(result_m, result_b, gt)
    ed = EarlyDetectionResult.na()  # E-1A 无时序数据，Early Detection 不计入 Hard Gate
    hard_gate_pass = q1 and q2 and q3 and fp and (fn_m < fn_b)
    notes = (
        f"Q1(grounded_gain)={q1}",
        f"Q2(historical_ref)={q2}",
        f"Q3(pattern_grounding)={q3}",
        f"FP(≤acceptable)={fp}",
        f"FN: memory={fn_m} baseline={fn_b}",
        "EarlyDetection=N/A(data-gated → E-1B)",
        f"HardGate={'PASS' if hard_gate_pass else 'FAIL'}",
    )
    return CaseEvaluation(
        case_id=gt.case_id,
        q1=q1,
        q2=q2,
        q3=q3,
        fp=fp,
        fn_m=fn_m,
        fn_b=fn_b,
        early_detection=ed,
        hard_gate_pass=hard_gate_pass,
        notes=notes,
        fp_excess=fp_severity_excess(result_m, gt),
    )


__all__ = [
    "HINT_SEVERITY",
    "CaseEvaluation",
    "EarlyDetectionResult",
    "acceptable_upper",
    "compute_lead_time",
    "evaluate_case",
    "fp_severity_excess",
    "hint_severity",
    "metric_fn",
    "metric_fp",
    "metric_q1_grounded_gain",
    "metric_q2_historical_reference",
    "metric_q3_pattern_grounding",
]

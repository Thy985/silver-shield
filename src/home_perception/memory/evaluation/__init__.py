"""E-1 Memory Value Evaluation harness（DESIGN-memory-evaluation.md）。

E-1a 落地：``ab_runner``（A/B 两臂执行）+ ``metrics``（四指标纯函数）+ ``ground_truth``
（GroundTruthRecord + E-1A 注册表）。
E-1b 落地：``report``（§8.1 统计汇总 + §8.2 Memory Value Score + ``e1_report.json/md`` 产出）。
E-1c 落地：``temporal``（时序 step 展开 + LeadTime 时间戳校准 + 3 时序校准 fixture）。
E-1B 数据集治理（E-1d）为后续 slice。
"""

from home_perception.memory.evaluation.ab_runner import (
    ABRun,
    build_baseline_input,
    run_ab_case,
    run_ab_dataset,
)
from home_perception.memory.evaluation.ground_truth import (
    GroundTruthRecord,
    e1a_case_ids,
    get_ground_truth,
)
from home_perception.memory.evaluation.metrics import (
    CaseEvaluation,
    EarlyDetectionResult,
    compute_lead_time,
    evaluate_case,
    fp_severity_excess,
    hint_severity,
    metric_fn,
    metric_fp,
    metric_q1_grounded_gain,
    metric_q2_historical_reference,
    metric_q3_pattern_grounding,
)
from home_perception.memory.evaluation.report import (
    E1Report,
    HardGateSummary,
    MemoryValueScore,
    ScoreTerms,
    StatSummary,
    build_report,
    compute_memory_value_score,
    compute_score_terms,
    evaluate_dataset,
    evaluate_temporal_dataset,
    render_markdown,
    report_to_dict,
    run_e1a_report,
    run_e1c_report,
    summarize,
    summarize_hard_gate,
    write_report,
)
from home_perception.memory.evaluation.temporal import (
    TemporalABResult,
    TemporalCase,
    TemporalStep,
    evaluate_temporal_case,
    is_detection,
    load_temporal_case,
    load_temporal_dataset,
    run_temporal_ab_case,
)

__all__ = [
    "ABRun",
    "CaseEvaluation",
    "E1Report",
    "EarlyDetectionResult",
    "GroundTruthRecord",
    "HardGateSummary",
    "MemoryValueScore",
    "ScoreTerms",
    "StatSummary",
    "TemporalABResult",
    "TemporalCase",
    "TemporalStep",
    "build_baseline_input",
    "build_report",
    "compute_lead_time",
    "compute_memory_value_score",
    "compute_score_terms",
    "e1a_case_ids",
    "evaluate_case",
    "evaluate_dataset",
    "evaluate_temporal_case",
    "evaluate_temporal_dataset",
    "fp_severity_excess",
    "get_ground_truth",
    "hint_severity",
    "is_detection",
    "load_temporal_case",
    "load_temporal_dataset",
    "metric_fn",
    "metric_fp",
    "metric_q1_grounded_gain",
    "metric_q2_historical_reference",
    "metric_q3_pattern_grounding",
    "render_markdown",
    "report_to_dict",
    "run_ab_case",
    "run_ab_dataset",
    "run_e1a_report",
    "run_e1c_report",
    "run_temporal_ab_case",
    "summarize",
    "summarize_hard_gate",
    "write_report",
]

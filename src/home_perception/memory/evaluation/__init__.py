"""E-1 Memory Value Evaluation harness（DESIGN-memory-evaluation.md）。

E-1a 落地：``ab_runner``（A/B 两臂执行）+ ``metrics``（四指标纯函数）+ ``ground_truth``
（GroundTruthRecord + E-1A 注册表）。报告 / 统计汇总（E-1b）、时序 LeadTime 计算（E-1c）、
E-1B 数据集治理（E-1d）后续 slice。
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
    hint_severity,
    metric_fn,
    metric_fp,
    metric_q1_grounded_gain,
    metric_q2_historical_reference,
    metric_q3_pattern_grounding,
)

__all__ = [
    "ABRun",
    "CaseEvaluation",
    "EarlyDetectionResult",
    "GroundTruthRecord",
    "build_baseline_input",
    "compute_lead_time",
    "e1a_case_ids",
    "evaluate_case",
    "get_ground_truth",
    "hint_severity",
    "metric_fn",
    "metric_fp",
    "metric_q1_grounded_gain",
    "metric_q2_historical_reference",
    "metric_q3_pattern_grounding",
    "run_ab_case",
    "run_ab_dataset",
]

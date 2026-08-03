"""E-1c 时序 step 展开 + LeadTime 时间戳校准（DESIGN-memory-evaluation.md §4.4 / §4.4.1）。

把每个 temporal fixture（``temporal/<case_id>/steps.json``）沿时间轴展开为有序 step 序列，
对每个 step 分别构造 Baseline（清空历史）与 Memory 输入、过同一确定性引擎
（``RuleBasedReasoningEngine``），记录两臂首次「检测事件」的 step + timestamp，由
``compute_lead_time`` 给出 ``EarlyDetectionResult``。

检测事件（§4.4.1）：``suggested_action_hint ∈ {ESCALATE_COMMUNITY, NOTIFY_FAMILY}``
**或** findings 中出现 escalation / conflict 类条目。检测时间戳取该 step 的
``current_event.occurred_at``（§4.4.1）。

本模块只消费 ``ReasoningResult``（Shadow 观测），不触碰 DecisionPolicy（守 ADR-0010）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from home_perception.common.timeutil import require_utc
from home_perception.memory.consumer.contracts import (
    CurrentEvent,
    ReasoningInput,
    ReasoningResult,
)
from home_perception.memory.consumer.reasoning import RuleBasedReasoningEngine
from home_perception.memory.evaluation.ab_runner import build_baseline_input
from home_perception.memory.evaluation.ground_truth import GroundTruthRecord, get_ground_truth
from home_perception.memory.evaluation.metrics import (
    CaseEvaluation,
    EarlyDetectionResult,
    compute_lead_time,
    evaluate_case,
)

# §4.4.1 检测判据
_DETECTION_HINTS = frozenset({"ESCALATE_COMMUNITY", "NOTIFY_FAMILY"})
_ESCALATION_KEYWORDS = ("升级", "escalat", "escalating")
_CONFLICT_KEYWORDS = ("冲突", "conflict", "behavior_shift")


def is_detection(result: ReasoningResult) -> bool:
    """§4.4.1 检测事件判定：hint 达 ESCALATE/NOTIFY，或 findings 含 escalation/conflict。"""
    if result.suggested_action_hint in _DETECTION_HINTS:
        return True
    return any(
        any(kw in finding for kw in _ESCALATION_KEYWORDS + _CONFLICT_KEYWORDS)
        for finding in result.findings
    )


@dataclass(frozen=True)
class TemporalStep:
    """单个时序 step（§4.4.1 ``steps.json`` 一项）。"""

    step: int
    timestamp: datetime
    current_event: CurrentEvent
    reasoning_input: ReasoningInput | None  # 该 step 的 Memory 臂输入；null = 无记忆观测点

    @classmethod
    def from_dict(cls, data: dict) -> TemporalStep:
        ce = CurrentEvent.from_dict(data["current_event"])
        ri = ReasoningInput.from_dict(data["reasoning_input"]) if data.get("reasoning_input") else None
        # 检测时间戳取 current_event.occurred_at（§4.4.1）；steps.json 另给 timestamp 时以之优先
        ts = ce.occurred_at
        if data.get("timestamp"):
            ts = datetime.fromisoformat(data["timestamp"])
            require_utc(ts, "timestamp")
        return cls(step=data["step"], timestamp=ts, current_event=ce, reasoning_input=ri)


@dataclass(frozen=True)
class TemporalCase:
    """一个时序 case：有序 step 序列 + 复用的 GroundTruth。"""

    case_id: str
    steps: tuple[TemporalStep, ...]
    ground_truth: GroundTruthRecord

    @property
    def n_steps(self) -> int:
        return len(self.steps)


def load_temporal_case(case_dir: str | Path) -> TemporalCase:
    """从 ``<case_dir>/steps.json`` 加载时序 case；GroundTruth 复用 E-1A 注册表（case_id 一致）。"""
    case_dir = Path(case_dir)
    case_id = case_dir.name
    steps_data = json.loads((case_dir / "steps.json").read_text(encoding="utf-8"))
    steps = tuple(TemporalStep.from_dict(s) for s in steps_data)
    return TemporalCase(case_id=case_id, steps=steps, ground_truth=get_ground_truth(case_id))


def load_temporal_dataset(root: str | Path) -> list[TemporalCase]:
    """扫描 ``<root>/temporal/*/steps.json``，返回有序时序 case 列表（确定性：目录字母序）。

    ``temporal/`` 子目录不参与 ``MemoryReplayDataset``（其 ``case_names`` 只取顶层
    ``case_*`` 目录），故时序 fixture 不会污染 E-1a 回放加载。
    """
    root = Path(root) / "temporal"
    if not root.is_dir():
        return []
    cases: list[TemporalCase] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "steps.json").is_file()):
        cases.append(load_temporal_case(d))
    return cases


@dataclass(frozen=True)
class TemporalABResult:
    """一次时序 A/B 的结果：EarlyDetectionResult + 两臂检测位置（供测试/调试）。"""

    case_id: str
    early: EarlyDetectionResult
    memory_detection_step: int | None
    memory_detection_ts: datetime | None
    baseline_detection_step: int | None
    baseline_detection_ts: datetime | None
    final_memory: ReasoningResult
    final_baseline: ReasoningResult


def run_temporal_ab_case(
    case: TemporalCase,
    engine: RuleBasedReasoningEngine | None = None,
) -> TemporalABResult:
    """对时序 case 逐 step 跑两臂，记录首次检测位置，回 EarlyDetectionResult。

    两臂唯一差异 = 历史上下文有无（同 ``build_baseline_input``）；同确定性引擎逐 step 推理。
    首次满足 ``is_detection`` 的 step 记为该臂检测位置；两臂均缺失 → ``missing_detection``。
    """
    engine = engine or RuleBasedReasoningEngine()
    m_step = m_ts = b_step = b_ts = None
    last_m: ReasoningResult | None = None
    last_b: ReasoningResult | None = None
    for step in case.steps:
        ri = step.reasoning_input
        if ri is not None:
            m_res = engine.infer(ri)
            b_res = engine.infer(build_baseline_input(ri))
        else:
            # 无 Memory 输入：Baseline 仅基于当前事件观测
            b_res = engine.infer(
                ReasoningInput(current_event=step.current_event, historical_context=())
            )
            m_res = None
        last_m, last_b = m_res, b_res
        if m_step is None and m_res is not None and is_detection(m_res):
            m_step, m_ts = step.step, step.timestamp
        if b_step is None and is_detection(b_res):
            b_step, b_ts = step.step, step.timestamp
    early = compute_lead_time(b_ts, m_ts, b_step, m_step)
    assert last_m is not None and last_b is not None, "时序 case 末 step 必须提供 Memory 输入"
    return TemporalABResult(
        case_id=case.case_id,
        early=early,
        memory_detection_step=m_step,
        memory_detection_ts=m_ts,
        baseline_detection_step=b_step,
        baseline_detection_ts=b_ts,
        final_memory=last_m,
        final_baseline=last_b,
    )


def evaluate_temporal_case(
    case: TemporalCase,
    gt: GroundTruthRecord | None = None,
    engine: RuleBasedReasoningEngine | None = None,
) -> CaseEvaluation:
    """对时序 case 跑 A/B + 四指标 + Early Detection，产出 CaseEvaluation。

    四指标（FN/Q/FP/HardGate）基于**末 step** 的 Memory/Baseline 输入（等价于 M0 单事件输入，
    故 Hard Gate 与 E-1a 一致）；Early Detection 来自逐 step 时序展开。
    """
    gt = gt or case.ground_truth
    ab = run_temporal_ab_case(case, engine)
    return evaluate_case(
        ab.final_memory, ab.final_baseline, gt, early_detection=ab.early
    )


__all__ = [
    "TemporalABResult",
    "TemporalCase",
    "TemporalStep",
    "evaluate_temporal_case",
    "is_detection",
    "load_temporal_case",
    "load_temporal_dataset",
    "run_temporal_ab_case",
]

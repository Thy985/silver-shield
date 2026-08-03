"""A/B 实验运行器（DESIGN-memory-evaluation.md §2 / §7）。

构造两臂 ``ReasoningInput``（**唯一变量 = 历史上下文有无**），过同一确定性引擎，
收两臂 ``ReasoningResult``。runner 只执行、不评价；指标计算见 ``metrics`` 模块。

复用 M0 ``MemoryReplayDataset`` 加载 fixtures（case.expected = Memory 臂输入），
Baseline 臂由 ``build_baseline_input`` 清零历史衍生字段、保留 ``current_event`` 原样，
确保两臂除历史外零差异、可进 CI、可复现（§2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass

from home_perception.memory.consumer.contracts import ReasoningInput, ReasoningResult
from home_perception.memory.consumer.reasoning import RuleBasedReasoningEngine
from home_perception.memory.consumer.replay_dataset import MemoryReplayDataset, ReplayCase


def build_baseline_input(memory_input: ReasoningInput) -> ReasoningInput:
    """Baseline 臂（Memory=off）：清空历史衍生字段，保留 current_event 原样（§2.2）。

    两臂唯一差异 = 历史上下文有无；其余字段（profile / pattern / conflicts / actions /
    evidence_refs）一并清零，确保 Baseline 严格无记忆、与 Memory 臂零差异可比。
    """
    return ReasoningInput(
        current_event=memory_input.current_event,
        historical_context=(),
        visitor_profile=None,
        risk_pattern=None,
        evidence_refs=(),
        previous_actions=(),
        conflicts=(),
    )


@dataclass
class ABRun:
    """一次 A/B 实验的两臂结果。"""

    case_id: str
    result_baseline: ReasoningResult
    result_memory: ReasoningResult


def run_ab_case(
    case: ReplayCase,
    engine: RuleBasedReasoningEngine | None = None,
) -> ABRun:
    """对单个 case 跑两臂：Memory 臂 = case.expected，Baseline 臂 = 清空历史，过同一引擎。"""
    engine = engine or RuleBasedReasoningEngine()
    memory_input = case.expected
    baseline_input = build_baseline_input(memory_input)
    return ABRun(
        case_id=case.name,
        result_baseline=engine.infer(baseline_input),
        result_memory=engine.infer(memory_input),
    )


def run_ab_dataset(
    dataset: MemoryReplayDataset,
    engine: RuleBasedReasoningEngine | None = None,
) -> list[ABRun]:
    """对数据集内所有 case 跑 A/B（E-1A = M0 三 case）。"""
    return [run_ab_case(case, engine) for case in dataset.load_all()]


__all__ = [
    "ABRun",
    "build_baseline_input",
    "run_ab_case",
    "run_ab_dataset",
]

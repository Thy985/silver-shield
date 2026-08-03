"""GroundTruthRecord 定义 + E-1A 注册表（DESIGN-memory-evaluation.md §5）。

``GroundTruthRecord`` 是「结构化正确答案」，由评审定义，与 replay fixture 解耦：
每个 case 一份，仅用于评测 **Memory 臂**（FN / Early Detection 对照）；Baseline 臂作
对照基线，不要求命中。

E-1A 三 case 的 GroundTruth 随代码内置（由 ``tests/fixtures/memory_replay`` 推导，
见各 case 注释）。E-1B 数据集（20~50 真实 CCTV）的采集 / 标注为独立数据治理任务
（E-1d），届时改为从 fixtures 加载、不再硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GroundTruthRecord:
    """一个 case 的结构化正确答案（§5）。

    字段见 DESIGN §5 表；``required_evidence`` 采用冻结匹配语法 ``<path> = <value>``：
    ``historical_context[].record_id`` / ``visitor_profile.<field>`` /
    ``risk_pattern.tags`` / ``conflicts.type``（详见 metrics._match_one_evidence）。
    """

    case_id: str
    category: str
    expected_pattern: tuple[str, ...]
    required_evidence: tuple[str, ...]
    acceptable_hint: tuple[str | None, ...]
    expected_detection_step: int | None = None
    expected_detection_ts: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroundTruthRecord:
        return cls(
            case_id=data["case_id"],
            category=data["category"],
            expected_pattern=tuple(data["expected_pattern"]),
            required_evidence=tuple(data.get("required_evidence", [])),
            acceptable_hint=tuple(data.get("acceptable_hint", [])),
            expected_detection_step=data.get("expected_detection_step"),
            expected_detection_ts=data.get("expected_detection_ts"),
        )


# ---------------------------------------------------------------------------
# E-1A 三 case GroundTruth（由 tests/fixtures/memory_replay 推导；Implementation Ready）
# ---------------------------------------------------------------------------
# case_001_repeat_visitor：current=MEDIUM（→hint NOTIFY_FAMILY）；5 条夜间历史，
#   profile.visit_count=5 / night_visit_ratio=1.0，risk_pattern=["repeated_visit"]。
#   记忆价值体现在 Q1/Q2/Q3 历史画像 grounding，hint 两臂恒为 NOTIFY_FAMILY（§4.2 校准）。
# case_002_behavior_escalation：current=HIGH（→hint ESCALATE_COMMUNITY）；3 条历史，
#   profile.visit_count=3，risk_pattern=["repeated_visit","escalating_behavior"]（注意真实
#   tag 为 escalating_behavior，非设计 §5 示例口语化的 behavior_escalation）。
# case_003_conflict_transparency：current=HIGH（→hint ESCALATE_COMMUNITY）；4 条白天历史，
#   profile.visit_count=4，conflicts=[behavior_shift]（历史正常 vs 当前深夜+观察摄像头）。
_GT_E1A: dict[str, GroundTruthRecord] = {
    "case_001_repeat_visitor": GroundTruthRecord(
        case_id="case_001_repeat_visitor",
        category="repeat_visitor",
        expected_pattern=("repeated_visit",),
        required_evidence=(
            "historical_context[].record_id = ep-a001-d1",
            "visitor_profile.visit_count = 5",
            "risk_pattern.tags = repeated_visit",
        ),
        acceptable_hint=("MONITOR", "NOTIFY_FAMILY"),
    ),
    "case_002_behavior_escalation": GroundTruthRecord(
        case_id="case_002_behavior_escalation",
        category="behavior_escalation",
        expected_pattern=("repeated_visit", "escalating_behavior"),
        required_evidence=(
            "historical_context[].record_id = ep-b002-d1",
            "visitor_profile.visit_count = 3",
            "risk_pattern.tags = escalating_behavior",
        ),
        acceptable_hint=("NOTIFY_FAMILY", "ESCALATE_COMMUNITY"),
    ),
    "case_003_conflict_transparency": GroundTruthRecord(
        case_id="case_003_conflict_transparency",
        category="conflict_transparency",
        expected_pattern=("behavior_shift",),
        required_evidence=(
            "historical_context[].record_id = ep-c003-d1",
            "visitor_profile.visit_count = 4",
            "conflicts.type = behavior_shift",
        ),
        acceptable_hint=("NOTIFY_FAMILY", "ESCALATE_COMMUNITY"),
    ),
}


def get_ground_truth(case_id: str) -> GroundTruthRecord:
    """返回 E-1A case 的 GroundTruthRecord；未登记（如 E-1B case）抛 KeyError。"""
    if case_id not in _GT_E1A:
        raise KeyError(f"E-1A 未登记 GroundTruth: {case_id}")
    return _GT_E1A[case_id]


def e1a_case_ids() -> list[str]:
    """返回 E-1A 全部 case_id（字母序，确定性）。"""
    return sorted(_GT_E1A)


__all__ = [
    "GroundTruthRecord",
    "e1a_case_ids",
    "get_ground_truth",
]

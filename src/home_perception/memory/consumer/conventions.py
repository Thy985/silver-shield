"""Memory 记录语义约定基元（Consumer 共用，C-4 抽出）。

本模块只放**约定解析**，不含任何业务逻辑、不依赖任何组件——供 Retrieval /
Aggregation / Orchestrator / Replay Layer 共同引用，避免同一约定在多处内联漂移。

当前两个约定：

1. **行为标记**：``EpisodicRecord.reason_summary`` 中以 ``behavior:`` 开头的条目
   表示"该次访问观察到的行为标记"（如 ``behavior:loiter``）。该解析原先在
   ``aggregation.py`` 与 ``replay_layer.py`` 各内联一份；C-4 编排器判定
   ``behavior_shift`` 冲突需要第三份，故抽出为单一基元（DRY，零行为变化）。

2. **风险等级序**：``LOW < MEDIUM < HIGH``（ADR-0010 决策严重度，**非**诈骗概率）。
   ``None`` 视为"无风险等级"，排在最低。编排器判定 ``risk_escalation``、runtime
   投影 ``CurrentEvent.risk_level`` 取最高 warning 等级时共用。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

# --------------------------------------------------------------------------
# 行为标记
# --------------------------------------------------------------------------
BEHAVIOR_MARKER_PREFIX = "behavior:"


@runtime_checkable
class SupportsReasonSummary(Protocol):
    """任何携带 ``reason_summary`` 的对象（``EpisodicRecord`` / ``WarningEvent`` …）。

    刻意用结构化协议而非具体类型：``behavior:`` 约定同时存在于**存储侧**记录与
    **实时侧** Warning（runtime 投影 ``CurrentEvent.markers`` 时要读后者）。若这里
    绑定 ``EpisodicRecord``，runtime 就得再内联一份解析——那正是本模块要消灭的漂移。
    附带效果：本模块零内部依赖，不会与 ``memory.records`` 形成导入环。
    """

    reason_summary: list[str]


def extract_behavior_markers(records: Iterable[SupportsReasonSummary]) -> list[str]:
    """抽取 ``reason_summary`` 中的 ``behavior:`` 后缀（保序、不过滤、不去重）。

    刻意**不**做过滤与去重（与抽出前逐字一致）：
    - 保留原始顺序（记录序 → 记录内 reason 序），调用方需要时间序时可直接用；
    - 保留空后缀（``"behavior:"`` → ``""``），由调用方按各自语义决定是否丢弃
      （如 ``RuleBasedAggregation._build_pattern`` 要求"唯一非空标记 >= 2"才判升级）。

    Args:
        records: 携带 ``reason_summary`` 的对象可迭代（该字段可为 None）。

    Returns:
        标记后缀列表（可能含空串与重复项）。
    """
    markers: list[str] = []
    for episode in records:
        for reason in episode.reason_summary or []:
            if reason.startswith(BEHAVIOR_MARKER_PREFIX):
                markers.append(reason[len(BEHAVIOR_MARKER_PREFIX) :])
    return markers


# --------------------------------------------------------------------------
# 风险等级序（ADR-0010 决策严重度）
# --------------------------------------------------------------------------
RISK_ORDER: dict[str | None, int] = {None: 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def risk_rank(level: str | None) -> int:
    """风险等级的可比较序（未知等级按 0 处理，不抛异常）。"""
    return RISK_ORDER.get(level, 0)


def max_risk_level(levels: Iterable[str | None]) -> str | None:
    """取一组风险等级中的最高者（max wins，ADR-0010）；空输入返回 ``None``。

    与 ``DefaultEpisodeBuilder._pick_max_risk`` 同口径：并列取首个出现者
    （``max`` 对相等键保留最先出现的元素）。
    """
    return max(levels, key=risk_rank, default=None)


__all__ = [
    "BEHAVIOR_MARKER_PREFIX",
    "RISK_ORDER",
    "SupportsReasonSummary",
    "extract_behavior_markers",
    "max_risk_level",
    "risk_rank",
]

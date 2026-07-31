"""Memory 消费接口（ADR-0024 Integration Closure · Slice C）。

提供轻量组合查询，把 Memory 中真实沉淀的 ``EpisodicRecord`` 组合成「用户可理解
的上下文 JSON」——证明 Memory 不只是内部正确，而是能产生**可审计的用户价值**
（Product Closure）。

设计纪律（来自 ``docs/DESIGN-memory-integration-closure.md`` §3.6）：
- **不实现 Agent、不接 LLM**，仅做结构化组合；
- **所有输出字段必须可溯源**到 ``MemoryStore`` 中具体 ``EpisodicRecord``
  （及其 ``reason_summary`` / ``actions`` / ``risk_level``），不得为了"好看"而硬编码；
- **可重放**：给定相同 ``visitor`` + 相同 ``window``，输出稳定（纯函数于 store 状态）。

本模块不改动 ``MemoryPolicy`` / ``EpisodicRecord`` / ``VisitorEvent`` 任何签名。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .records import EpisodicRecord
from .store import MemoryStore

# risk 等级排序（用于挑选窗口内"主事件"——最高风险那条）
_RISK_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, None: 0}


def _risk_rank(level: Optional[str]) -> int:
    return _RISK_ORDER.get(level, 0)


class MemoryQuery:
    """组合查询：把 Memory 沉淀组合成可消费上下文（Product Closure）。

    典型用途：回答用户最朴素的问题——「昨天为什么报警？」。
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def compose_context(
        self,
        visitor_instance_id: str,
        window_start: datetime,
        window_end: datetime,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """组合某访客在某时间窗内的「为什么报警」上下文。

        参数
        ----
        visitor_instance_id: 访客实例 id（``EpisodicRecord.visitor_instance_id``）。
        window_start / window_end: 查询时间窗（闭区间，按 ``enter_time`` 过滤）。
        as_of: 推算 ``current_status`` 的参考"现在"。默认 = ``window_end``。

        返回（全部字段可溯源到 store 中具体 record）
        ----
        - ``visitor_instance_id``：回显
        - ``current_status``：``ACTIVE_RISK``（访客在场且未离场）/ ``CLEARED``
          （曾有事件且已离场）/ ``None``（无任何记忆）
        - ``reason``：窗口内最高风险 episode 的组合说明（来自 ``summary`` / ``duration`` /
          ``risk_level`` 等真实字段）
        - ``evidence``：该 episode 的 ``reason_summary``（数据派生，非硬编码）
        - ``handling``：该 episode 的 ``recommended_action`` + 首个 ``ActionSummary`` 投影
        - ``history``：窗口内该访客类似事件计数文本
        - ``source_record_ids``：贡献来源 record_id 列表（可溯源）
        """
        as_of = as_of if as_of is not None else window_end

        all_for_visitor = self._store.get_episodic_by_visitor(visitor_instance_id)
        in_window = [
            ep for ep in all_for_visitor
            if window_start <= ep.enter_time <= window_end
        ]
        in_window.sort(key=lambda e: (e.enter_time, e.record_id))

        current_status = self._current_status(all_for_visitor, in_window, as_of)

        if not in_window:
            return {
                "visitor_instance_id": visitor_instance_id,
                "current_status": current_status,
                "reason": None,
                "evidence": [],
                "handling": None,
                "history": _history_text(in_window, window_start, window_end),
                "source_record_ids": [],
            }

        primary = max(
            in_window,
            key=lambda e: (_risk_rank(e.risk_level), e.enter_time),
        )

        return {
            "visitor_instance_id": visitor_instance_id,
            "current_status": current_status,
            "reason": _compose_reason(primary),
            "evidence": _compose_evidence(primary),
            "handling": _compose_handling(primary),
            "history": _history_text(in_window, window_start, window_end),
            "source_record_ids": [ep.record_id for ep in in_window],
        }

    @staticmethod
    def _current_status(
        all_for_visitor: List[EpisodicRecord],
        in_window: List[EpisodicRecord],
        as_of: datetime,
    ) -> Optional[str]:
        # 访客当前是否仍在场（enter <= as_of <= leave）→ 视为活跃风险
        in_progress = [
            ep for ep in all_for_visitor
            if ep.enter_time <= as_of <= ep.leave_time
        ]
        if in_progress:
            return "ACTIVE_RISK"
        # 曾有事件且已离场 → 已解除
        if in_window or all_for_visitor:
            return "CLEARED"
        return None


def _compose_reason(ep: EpisodicRecord) -> str:
    """组合"为什么"说明——全部来自 episode 真实字段，不硬编码文案。"""
    parts: List[str] = []
    if ep.enter_time is not None:
        parts.append(f"{ep.enter_time:%H:%M} 访客进入")
    if ep.duration_seconds:
        parts.append(f"停留 {ep.duration_seconds / 60:.0f} 分钟")
    if ep.risk_level in ("HIGH", "MEDIUM", "LOW"):
        parts.append(f"风险等级 {ep.risk_level}")
    # 非常规访问时间：来自 episode 已沉淀的 reason_summary（数据派生，不重实现规则）
    if any("非常规" in r or "odd" in r.lower() for r in (ep.reason_summary or [])):
        parts.append("非常规访问时间")
    return "；".join(parts)


def _compose_evidence(ep: EpisodicRecord) -> List[str]:
    """证据链：优先用 episode 已沉淀的 reason_summary；为空时用 summary 兜底。"""
    if ep.reason_summary:
        return list(ep.reason_summary)
    return [ep.summary] if ep.summary else []


def _compose_handling(ep: EpisodicRecord) -> str:
    """处理记录：recommended_action + 首个 ActionSummary 投影（可溯源到 action）。"""
    action = ep.recommended_action or "无"
    if ep.actions:
        first = ep.actions[0]
        return f"{action}（{first.command_type} @{first.command_id}）"
    return action


def _history_text(
    episodes: List[EpisodicRecord],
    window_start: datetime,
    window_end: datetime,
) -> str:
    n_days = max(1, (window_end.date() - window_start.date()).days)
    return f"过去 {n_days} 天类似事件 {len(episodes)} 次"


__all__ = ["MemoryQuery"]

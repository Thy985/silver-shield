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

from ..common.timeutil import require_utc
from .records import EpisodicRecord, VisitorPresenceStatus
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
        - ``current_status``：``VisitorPresenceStatus``——``IN_PROGRESS``（as_of 落在窗口内
          某 episode 的在场区间内，历史/回放语义，非实时在场）/ ``CLEARED``（窗口内有事件且
          as_of 已晚于离场）/ ``NO_RECORD``（窗口内无任何相关 episode）。详见
          ``VisitorPresenceStatus`` docstring（review #1/#5）。
        - ``reason``：窗口内最高风险 episode 的组合说明（来自 ``summary`` / ``duration`` /
          ``risk_level`` 等真实字段）
        - ``evidence``：该 episode 的 ``reason_summary``（数据派生，非硬编码）
        - ``handling``：该 episode 的 ``recommended_action`` + 全部 ``ActionSummary`` 投影
        - ``history``：窗口内该访客事件计数文本
        - ``source_record_ids``：贡献来源 record_id 列表（可溯源）
        """
        as_of = as_of if as_of is not None else window_end

        # 入参校验：时间必须 tz-aware(UTC)，且窗口方向正确（review #3）
        require_utc(window_start, "window_start")
        require_utc(window_end, "window_end")
        if as_of is not None:
            require_utc(as_of, "as_of")
        if window_start > window_end:
            raise ValueError(
                f"window_start 必须 <= window_end，收到 "
                f"window_start={window_start!r}, window_end={window_end!r}"
            )

        all_for_visitor = self._store.get_episodic_by_visitor(visitor_instance_id)
        # 窗口过滤按"与窗口重叠"判定（enter <= window_end 且 leave >= window_start），
        # 避免边界期事件（窗口前进入、窗口内停留/离开）被漏掉（review #2）。
        in_window = [
            ep for ep in all_for_visitor
            if ep.enter_time <= window_end and ep.leave_time >= window_start
        ]
        in_window.sort(key=lambda e: (e.enter_time, e.record_id))

        # current_status 视角与窗口一致（基于 in_window，而非全量历史），
        # 避免"IN_PROGRESS + reason=None"自相矛盾（review #1/#2）。
        current_status = self._current_status(in_window, as_of)

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
        in_window: List[EpisodicRecord],
        as_of: datetime,
    ) -> VisitorPresenceStatus:
        # 视角 = 窗口内 episode（与 reason/handling/history 一致，review #2）。
        # IN_PROGRESS 为历史/回放时间点语义（见 VisitorPresenceStatus docstring）：
        # 真实数据流中 episode 仅离场后写入，leave_time 恒为过去，故实时查询恒为 CLEARED
        # （review #1）。实时在场见 ShortTermRecord.phase（out of scope）。
        if not in_window:
            return VisitorPresenceStatus.NO_RECORD
        in_progress = [
            ep for ep in in_window
            if ep.enter_time <= as_of <= ep.leave_time
        ]
        if in_progress:
            return VisitorPresenceStatus.IN_PROGRESS
        return VisitorPresenceStatus.CLEARED


def _compose_reason(ep: EpisodicRecord) -> str:
    """组合"为什么"说明——全部来自 episode 真实字段，不硬编码文案。"""
    parts: List[str] = []
    if ep.enter_time is not None:
        parts.append(f"{ep.enter_time:%H:%M} 访客进入")
    if ep.duration_seconds:
        parts.append(f"停留 {ep.duration_seconds / 60:.0f} 分钟")
    if ep.risk_level in ("HIGH", "MEDIUM", "LOW"):
        parts.append(f"风险等级 {ep.risk_level}")
    # 非常规访问时间：暂从 episode 已沉淀的 reason_summary 嗅探（review #4，P3）。
    # TODO(review #4): 脆弱——与分析层规则摘要文案强耦合，措辞一改即静默失效。
    # 应由 Episode Builder 沉淀结构化标记（tags / rule_ids / risk_factors），
    # query 端直接读取，而非重实现/嗅探规则语义。
    if any("非常规" in r or "odd" in r.lower() for r in (ep.reason_summary or [])):
        parts.append("非常规访问时间")
    return "；".join(parts)


def _compose_evidence(ep: EpisodicRecord) -> List[str]:
    """证据链：优先用 episode 已沉淀的 reason_summary；为空时用 summary 兜底。"""
    if ep.reason_summary:
        return list(ep.reason_summary)
    return [ep.summary] if ep.summary else []


def _compose_handling(ep: EpisodicRecord) -> str:
    """处理记录：recommended_action + 全部 ActionSummary 投影（可溯源到 action）。

    一次事件可能多 action（如 NOTIFY + ESCALATE），全部列出，避免信息丢失（review #6）。
    """
    action = ep.recommended_action or "无"
    if ep.actions:
        acted = "; ".join(
            f"{a.command_type}@{a.command_id}({a.status})" for a in ep.actions
        )
        return f"{action}；动作: {acted}"
    return action


def _history_text(
    episodes: List[EpisodicRecord],
    window_start: datetime,
    window_end: datetime,
) -> str:
    n_days = max(1, (window_end.date() - window_start.date()).days)
    return f"过去 {n_days} 天事件 {len(episodes)} 次"


__all__ = ["MemoryQuery"]

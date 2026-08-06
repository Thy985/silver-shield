"""Episode Replay Layer（M0 实现，DESIGN-memory-replay-dataset.md §4）。

把 fixture 的 history ``EpisodicRecord`` 灌入**独立** ``InMemoryStore``，复用现有
``MemoryQuery.compose_context`` 做检索（证明 CCTV→Memory 链路真实可读），再用
``ProvisionalContextAssembler`` 组装一个 minimal ``ReasoningInput``，证明
"Memory 改变了理解"（孤立事件 → 关联画像 / 模式 / 冲突）。

> ⚠️ ``ProvisionalContextAssembler`` 是 **replay-only 临时实现**，仅用于 M0 证明
> 数据闭环。M1 / C-3 的正式 ``ContextBuilder`` 会取代它（逻辑更全、含模式 B 触发
> 接入、Retrieval 排序键等）。本模块不往生产 Memory 写入（C2 只读），且组装逻辑
> 为纯函数（C3 确定性）。

不变量（继承 ADR-0025）：
- C2 只读：``EpisodeReplayLayer`` 只 ``upsert`` 测试用历史，不读/写生产 Memory。
- C3 确定性：同 ``ReplayCase`` 两次 ``build_reasoning_input`` 产出字段级相等。
- C1 无分数：``ReasoningInput`` 不含 ``risk_score`` / ``decision`` / ``warning``。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
    RiskPattern,
    VisitorProfile,
)
from home_perception.memory.consumer.replay_dataset import ReplayCase
from home_perception.memory.query import MemoryQuery
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InMemoryStore


def _is_night(dt) -> bool:
    """夜间判定：22:00 之后或 06:00 之前（用于 night_visit_ratio / 冲突检测）。"""
    return dt.hour >= 22 or dt.hour < 6


def _confidence_tier(n: int) -> str:
    """访客观测置信度分级（DESIGN-memory-consumer.md §3.2，review 3.2 修订版）。

    - ``n < 5``      → ``cold_start``
    - ``5 <= n < 30`` → ``weak_pattern``
    - ``n >= 30``    → ``stable_pattern``

    始终进入 ``ReasoningInput``（不隐藏），由 Reasoning 自行按 confidence 降权。
    """
    if n < 5:
        return "cold_start"
    if n < 30:
        return "weak_pattern"
    return "stable_pattern"


class ProvisionalContextAssembler:
    """M0 临时组装器：从 history + current 组装 minimal ``ReasoningInput``。

    纯函数、确定性。规则显式且简单，便于与 fixture 的 ``expected_reasoning_input.json``
    对齐；正式 ``ContextBuilder`` 会扩展（更多模式、Retrieval 排序键、阈值门控）。
    """

    def assemble(
        self, current: CurrentEvent, history: list[EpisodicRecord]
    ) -> ReasoningInput:
        ordered = sorted(history, key=lambda e: (e.enter_time, e.record_id))
        profile = self._visitor_profile(ordered)
        risk_pattern = self._risk_pattern(ordered)
        conflicts = self._conflicts(current, ordered)
        previous_actions = self._previous_actions(ordered)
        evidence_refs = self._evidence_refs(ordered)
        return ReasoningInput(
            current_event=current,
            historical_context=tuple(ordered),
            visitor_profile=profile,
            risk_pattern=risk_pattern,
            evidence_refs=tuple(evidence_refs),
            previous_actions=tuple(previous_actions),
            conflicts=tuple(conflicts),
        )

    # -- 子计算（纯函数，确定性） ------------------------------------------
    def _visitor_profile(self, history: list[EpisodicRecord]) -> VisitorProfile:
        n = len(history)
        night = sum(1 for ep in history if _is_night(ep.enter_time))
        ratio = (night / n) if n else 0.0
        first = min((ep.enter_time for ep in history), default=None)
        last = max((ep.leave_time for ep in history), default=None)
        return VisitorProfile(
            visitor_instance_id=history[0].visitor_instance_id if n else "",
            visit_count=n,
            night_visit_ratio=ratio,
            confidence=_confidence_tier(n),
            identity_confirmed=False,  # v1 临时画像恒 False（ADR-0023）
            first_seen=first,
            last_seen=last,
        )

    def _behavior_markers(self, history: list[EpisodicRecord]) -> list[str]:
        """从 history 的 ``reason_summary`` 抽取 ``behavior:`` 前缀标记（按时间序）。"""
        markers: list[str] = []
        for ep in history:
            for r in ep.reason_summary or []:
                if r.startswith("behavior:"):
                    markers.append(r[len("behavior:") :])
        return markers

    def _risk_pattern(self, history: list[EpisodicRecord]) -> RiskPattern | None:
        n = len(history)
        tags: list[str] = []
        if n >= 2:
            tags.append("repeated_visit")
        esc = self._behavior_markers(history)
        if len(esc) >= 2:  # 多阶段行为 → 视为升级模式（provisional 启发式）
            tags.append("escalating_behavior")
        if not tags:
            return None
        return RiskPattern(tags=tuple(tags), escalation_history=tuple(esc) or None,
                           confidence=_confidence_tier(n))

    def _conflicts(
        self, current: CurrentEvent, history: list[EpisodicRecord]
    ) -> list[ConflictFlag]:
        # "normal" = 全部为白天短时访客（enter hour ∈ [8, 18)）；夜间或傍晚（19-21h）
        # 既非白天也非夜间，不触发 normal→abnormal 冲突（避免 case_002 误报）。
        history_normal = len(history) > 0 and all(
            8 <= ep.enter_time.hour < 18 for ep in history
        )
        current_abnormal = ("night" in current.markers) or ("observe_camera" in current.markers)
        if history_normal and current_abnormal:
            detail = (
                f"historical daytime-only({len(history)} visits) vs "
                f"current markers={list(current.markers)}"
            )
            return [
                ConflictFlag(
                    type="behavior_shift",
                    historical="normal",
                    current="abnormal",
                    detail=detail,
                )
            ]
        return []

    def _previous_actions(self, history: list[EpisodicRecord]) -> list[ActionRecord]:
        seen: dict[tuple[str, str], ActionRecord] = {}
        for ep in history:
            for a in ep.actions:
                key = (a.command_type, a.command_id)
                if key not in seen:
                    seen[key] = ActionRecord(
                        command_type=a.command_type,
                        command_id=a.command_id,
                        status=a.status,
                        error=a.error,
                    )
        return list(seen.values())

    def _evidence_refs(self, history: list[EpisodicRecord]) -> list[str]:
        """按历史顺序汇总证据 ID，依 evidence_id 去重保序（与生产编排器一致）。

        ADR-0027 Slice A 审查（P1）：生产 ``MemoryOrchestrator._collect_evidence``
        已按 ``evidence_id`` 去重，回放路径必须行为一致 —— 否则同一 ``ev-1`` 被两条
        历史 Episode 引用时，生产返回 ``("ev-1",)`` 而回放返回 ``("ev-1", "ev-1")``，
        形成不一致并可能重复下游解析/展示。
        """
        seen: set[str] = set()
        refs: list[str] = []
        for ep in history:
            for eid in ep.evidence_refs or []:
                if eid in seen:
                    continue
                seen.add(eid)
                refs.append(eid)
        return refs


class EpisodeReplayLayer:
    """把回放 case 灌入独立 MemoryStore，复用真实检索 + 临时组装。

    复用不新建：检索直接走 ``MemoryQuery.compose_context``（ADR-0024 Slice C 冻结
    接口），不修改其签名 / 返回值（ADR-0025 review 5.4）。
    """

    def __init__(self, case: ReplayCase) -> None:
        self._case = case
        self._store = InMemoryStore()
        for ep in case.history:
            self._store.upsert_episodic(ep)
        self._query = MemoryQuery(self._store)
        self._assembler = ProvisionalContextAssembler()

    def retrieve(self, window_start, window_end) -> dict[str, Any]:
        """真实检索：返回 ``MemoryQuery.compose_context`` 组合上下文（dict）。"""
        return self._query.compose_context(
            self._case.current_event.visitor_instance_id,
            window_start,
            window_end,
        )

    def default_window(self):
        """覆盖全部 history 的查询窗（min enter - 1d .. max leave + 1d）。"""
        enters = [ep.enter_time for ep in self._case.history]
        leaves = [ep.leave_time for ep in self._case.history]
        lo = min(enters) - timedelta(days=1)
        hi = max(leaves) + timedelta(days=1)
        return lo, hi

    def build_reasoning_input(self) -> ReasoningInput:
        """组装 minimal ReasoningInput（证明 Memory 改变了理解）。"""
        return self._assembler.assemble(self._case.current_event, list(self._case.history))

    def episode_count(self) -> int:
        """当前回放 store 中的 EpisodicRecord 条数（只读观测口，C2 验证用）。"""
        return len(self._store.snapshot()["episodic"])


__all__ = ["EpisodeReplayLayer", "ProvisionalContextAssembler"]

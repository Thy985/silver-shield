"""Episode Builder 实现（ADR-0024 §3.2.1 / Stage B / Slice 4）。

> **Slice 4 范围**：只实现 `MemoryPolicy.project_episode`（把
> `VisitorEvent` + 关联 `WarningEvent[]` + 关联 `ActionCommand[]` 投影为
> `EpisodicRecord`）。`transform_short_term`（Slice 2 由 `DefaultShortTermPolicy`
> 实现）与 `aggregate_semantic`（Stage G/H 由 SemanticAggregator 实现）本类仅留
> 占位，返回 None —— 它们是**其他 slice 的职责**，这里不实现以免越权（AGENTS §6.3）。

**Memory Policy 约束（policy.py）**：
- 无状态转换器（`project_episode` 是纯函数语义，不持有可变状态）
- 不调 LLM / 不调外部 API
- 不修改输入对象（VisitorEvent / WarningEvent / ActionCommand 只读）
- 不产出 WarningEvent / ActionCommand（决策归 DecisionPolicy，执行归 ActionExecutor）

**关联规则（DESIGN §5.2.4）**：
- VisitorEvent ↔ WarningEvent：`visitor_instance_id` + 时间窗
  `WarningEvent.created_at ∈ [enter_time, leave_time + 60s]`
  - `visitor_instance_id = str(VisitorEvent.visitor_id)`（v1 主键，DESIGN §5.2.2）
  - WarningEvent 的 `trigger_events[]` 元素是 PerceptionEvent 摘要 dict，其 `event_id`
    形如 `"{visitor_id}:{event_type}"`（见 `decision_policy.py` 构造）；
    本类从 `event_id` 解析出 `visitor_id` 前缀做匹配（兼容直接含 `visitor_id` 键的情形）。
- WarningEvent ↔ ActionCommand：`warning_id`
  `ActionCommand.warning_id == WarningEvent.warning_id`

**不变量（records.py `EpisodicRecord.__post_init__` 强制）**：
- I1 幂等：`record_id = f"ep-{visitor_event.event_id}"`
- I2 单调：本类只新建，不重写过去 episode
- I3 因果：`created_at`（record）>= 源事件时间（由 `now_dt()` 保证）
- I4 可解释：`source_event_ids` 非空，引用全部源事件 id
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # 避免运行时循环 import；本类仅做属性读取（duck typing），无需运行时导入
    from ..analysis.event import VisitorEvent
    from ..analysis.warning import WarningEvent
    from ..action.command import ActionCommand

from .policy import MemoryPolicy
from .records import ActionSummary, EpisodicRecord


# 人类可读 summary 展示时区（与系统展示层一致：Asia/Shanghai / UTC+8）。
# 注意：这是**派生展示字符串**，不是存储字段；源事件时间仍严格 UTC（event.py 约束）。
_DISPLAY_TZ = timezone(timedelta(hours=8))

# 关联容差：WarningEvent 可能在访客离场后短暂延迟生成，超 60s 不关联避免串号。
_ACTION_TOLERANCE_SECONDS = 60.0

# risk_level 排序（max wins，ADR-0010 决策严重度语义）。
_RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

# ActionCommand.command_type → 中文动作短语（确定性，无 LLM）
_ACTION_PHRASE = {
    "SEND_FAMILY_MESSAGE": "已通知家属",
    "CREATE_COMMUNITY_TASK": "升级社区",
    "LOG_ONLY": "仅记录",
}

# WarningEvent.recommended_action → 中文动作短语（无实际 ActionCommand 时回退）
_RECO_ACTION_PHRASE = {
    "NOTIFY_FAMILY": "已通知家属",
    "ESCALATE_COMMUNITY": "升级社区",
    "MONITOR": "仅监控记录",
}

# command_type 在 summary 中的稳定展示顺序
_ACTION_ORDER = ["SEND_FAMILY_MESSAGE", "CREATE_COMMUNITY_TASK", "LOG_ONLY"]


class DefaultEpisodeBuilder(MemoryPolicy):
    """v1 Episode Builder 实现（ADR-0024 Stage B / Slice 4）。

    把一次访客离场（`VisitorEvent`）及其关联的风险决策（`WarningEvent`）与执行动作
    （`ActionCommand`）投影为一条长期可消费的 `EpisodicRecord`。

    设计为**无状态纯函数转换器**：构造后不持有可变状态，`project_episode` 对同一组
    输入永远产出内容一致的记录（I1 幂等性的前提）。
    """

    MODEL_VERSION = "ep-builder-v1"
    ACTION_TOLERANCE_SECONDS = _ACTION_TOLERANCE_SECONDS

    # ------------------------------------------------------------------
    # Stage B：Episode 投影（本 slice 实际实现）
    # ------------------------------------------------------------------
    def project_episode(
        self,
        visitor_event: "VisitorEvent",
        warnings: List["WarningEvent"],
        actions: List["ActionCommand"],
    ) -> Optional[EpisodicRecord]:
        """投影一次访客离场为 EpisodicRecord。

        触发时机：VisitorEvent 生成（访客离场）。
        幂等键：`record_id = f"ep-{visitor_event.event_id}"`（I1）。
        返回 None 仅当 `visitor_event` 为 None（调用方错误处理）。
        """
        if visitor_event is None:
            return None

        # 1. 关联 WarningEvent（visitor_instance_id + 时间窗）
        related_warnings = self._filter_warnings(visitor_event, warnings)
        # 2. 关联 ActionCommand（按 warning_id）
        related_actions = self._filter_actions(related_warnings, actions)
        # 3. 聚合 risk_level（max wins，ADR-0010）
        risk_level, recommended_action = self._pick_max_risk(related_warnings)
        # 4. 合并 reason_summary（去重保序）
        reason_summary = self._merge_reasons(related_warnings)
        # 5. 生成 human-interpretable summary（确定性，无 LLM）
        summary = self._build_summary(
            visitor_event, risk_level, recommended_action, reason_summary, related_actions
        )
        # 6. 构造 record（__post_init__ 强制全部不变量）
        return EpisodicRecord(
            record_id=f"ep-{visitor_event.event_id}",
            visitor_instance_id=str(visitor_event.visitor_id),
            person_identity_id=None,  # v1 恒 None（ADR-0023）
            enter_time=visitor_event.enter_time,
            leave_time=visitor_event.leave_time,
            duration_seconds=visitor_event.duration_seconds,
            risk_level=risk_level,
            recommended_action=recommended_action,
            reason_summary=reason_summary,
            actions=[self._to_action_summary(c) for c in related_actions],
            evidence_refs=[],  # v1 空，ADR-0022 落地后接 EvidenceItem
            source_event_ids=self._collect_source_ids(
                visitor_event, related_warnings, related_actions
            ),
            summary=summary,
            model_version=self.MODEL_VERSION,
        )

    # ------------------------------------------------------------------
    # 其他 Stage 职责占位（本 slice 不实现，返回 None）
    # ------------------------------------------------------------------
    def transform_short_term(self, state_snapshot, transition, current_record=None):
        """Short-term 投影由 `DefaultShortTermPolicy`（Slice 2）实现；本类不负责。"""
        return None

    def aggregate_semantic(self, episodes, dimension, period_key):
        """Semantic 聚合由 SemanticAggregator（Stage G/H）实现；本类不负责。"""
        return None

    # ------------------------------------------------------------------
    # 内部关联逻辑
    # ------------------------------------------------------------------
    def _filter_warnings(
        self, visitor_event: "VisitorEvent", warnings: List["WarningEvent"]
    ) -> List["WarningEvent"]:
        """按 visitor_instance_id + 时间窗筛选关联 WarningEvent。

        时间窗：`enter_time <= warning.created_at <= leave_time + 60s`。
        visitor 匹配：WarningEvent.trigger_events[].event_id 前缀 == visitor_id。
        """
        vid = str(visitor_event.visitor_id)
        window_end = visitor_event.leave_time + timedelta(
            seconds=self.ACTION_TOLERANCE_SECONDS
        )
        result: List["WarningEvent"] = []
        for w in warnings or []:
            # 时间窗（created_at 为 UTC，与 enter/leave 同基准）
            if w.created_at < visitor_event.enter_time or w.created_at > window_end:
                continue
            # visitor 关联
            if not self._warning_mentions_visitor(w, vid):
                continue
            result.append(w)
        return result

    @staticmethod
    def _warning_mentions_visitor(warning: "WarningEvent", visitor_id: str) -> bool:
        """WarningEvent 是否关联某 visitor（读 trigger_events 的 event_id 前缀）。

        兼容两种 trigger_events 元素形态：
        - 含 `visitor_id` 键：直接比较
        - 仅含 `event_id` = `"{visitor_id}:{event_type}"`（decision_policy 实际形态）：
          取 `:` 前部分作为 visitor_id
        """
        for trig in warning.trigger_events:
            tid = DefaultEpisodeBuilder._trigger_visitor_id(trig)
            if tid is not None and tid == visitor_id:
                return True
        return False

    @staticmethod
    def _trigger_visitor_id(trigger: dict) -> Optional[str]:
        """从 trigger_events 元素解析 visitor_id（见 `_warning_mentions_visitor`）。"""
        if trigger.get("visitor_id") is not None:
            return str(trigger["visitor_id"])
        event_id = trigger.get("event_id")
        if event_id is not None:
            return str(event_id).split(":", 1)[0]
        return None

    @staticmethod
    def _filter_actions(
        related_warnings: List["WarningEvent"], actions: List["ActionCommand"]
    ) -> List["ActionCommand"]:
        """按 warning_id 关联 ActionCommand。"""
        warning_ids = {str(w.warning_id) for w in related_warnings}
        if not warning_ids:
            return []
        return [a for a in (actions or []) if str(a.warning_id) in warning_ids]

    @staticmethod
    def _pick_max_risk(
        related_warnings: List["WarningEvent"],
    ) -> (Optional[str], Optional[str]):
        """取 risk_level 最高的 Warning 的 (risk_level, recommended_action)。

        max wins（HIGH > MEDIUM > LOW）；并列取首个出现者。
        无关联 warning 返回 (None, None)。
        """
        best: Optional["WarningEvent"] = None
        for w in related_warnings:
            rank = _RISK_RANK.get(w.risk_level, 0)
            if best is None or rank > _RISK_RANK.get(best.risk_level, 0):
                best = w
        if best is None:
            return None, None
        return best.risk_level, best.recommended_action

    @staticmethod
    def _merge_reasons(related_warnings: List["WarningEvent"]) -> List[str]:
        """合并多条 Warning 的 reason_summary，去重保序。"""
        seen: List[str] = []
        for w in related_warnings:
            for r in w.reason_summary:
                if r not in seen:
                    seen.append(r)
        return seen

    @staticmethod
    def _to_action_summary(cmd: "ActionCommand") -> ActionSummary:
        """ActionCommand → ActionSummary（不存 payload，ADR-0024 §3.2.1）。"""
        return ActionSummary(
            command_type=cmd.command_type,
            command_id=str(cmd.command_id),
            status=cmd.status,
            error=cmd.error,
        )

    @staticmethod
    def _collect_source_ids(
        visitor_event: "VisitorEvent",
        related_warnings: List["WarningEvent"],
        related_actions: List["ActionCommand"],
    ) -> List[str]:
        """I4 可解释性：聚合全部源事件 id（visitor + warning + action）。"""
        ids: List[str] = [visitor_event.event_id]
        ids += [str(w.warning_id) for w in related_warnings]
        ids += [str(a.command_id) for a in related_actions]
        return ids

    def _build_summary(
        self,
        visitor_event: "VisitorEvent",
        risk_level: Optional[str],
        recommended_action: Optional[str],
        reason_summary: List[str],
        related_actions: List["ActionCommand"],
    ) -> str:
        """生成 human-interpretable summary（确定性，无 LLM，见 DESIGN §5.2.3）。

        模板：
            {enter}-{leave} 访问（停留 {minutes} 分钟）[, 风险等级 LEVEL（原因/原因）][, 动作短语]。

        无风险：`... 访问（停留 X 分钟），未触发风险。`
        """
        enter = visitor_event.enter_time.astimezone(_DISPLAY_TZ).strftime("%H:%M")
        leave = visitor_event.leave_time.astimezone(_DISPLAY_TZ).strftime("%H:%M")
        minutes = round(visitor_event.duration_seconds / 60)
        base = f"{enter}-{leave} 访问（停留 {minutes} 分钟）"

        if risk_level is None:
            return base + "，未触发风险。"

        risk_part = f"{base}，风险等级 {risk_level}"
        if reason_summary:
            risk_part += f"（{' / '.join(reason_summary)}）"
        action_phrase = self._build_action_phrase(related_actions, recommended_action)
        if action_phrase:
            risk_part += f"，{action_phrase}"
        return risk_part + "。"

    def _build_action_phrase(
        self,
        related_actions: List["ActionCommand"],
        recommended_action: Optional[str],
    ) -> str:
        """从实际 ActionCommand（优先）或 recommended_action（回退）生成动作短语。"""
        phrases: List[str] = []
        if related_actions:
            seen: set[str] = set()
            for cmd_type in _ACTION_ORDER:
                if any(c.command_type == cmd_type for c in related_actions):
                    phrase = _ACTION_PHRASE.get(cmd_type)
                    if phrase and phrase not in seen:
                        seen.add(phrase)
                        phrases.append(phrase)
        elif recommended_action:
            phrase = _RECO_ACTION_PHRASE.get(recommended_action)
            if phrase:
                phrases.append(phrase)
        return " + ".join(phrases)

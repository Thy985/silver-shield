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

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 避免运行时循环 import；本类仅做属性读取（duck typing），无需运行时导入
    from ..action.command import ActionCommand
    from ..analysis.event import VisitorEvent
    from ..analysis.warning import WarningEvent

from ..common.timeutil import now_dt
from ..core.event import EvidenceItem, EvidenceModality
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

# 音频 kind → 中文描述（确定性，无 LLM；用于 episode summary 的音频增强，ADR-0027 Slice B）
_AUDIO_KIND_CN = {
    "telephone": "长时间通话",
    "crying": "哭腔",
    "rapid": "急促语音",
    "raised": "高声",
    "audio_segment": "音频片段",
    "audio_clip": "音频片段",
}


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
        visitor_event: VisitorEvent,
        warnings: list[WarningEvent],
        actions: list[ActionCommand],
        *,
        evidence: list[EvidenceItem] | None = None,
        audio_session_id: str | None = None,
    ) -> EpisodicRecord | None:
        """投影一次访客离场为 EpisodicRecord。

        触发时机：VisitorEvent 生成（访客离场）。
        幂等键：视觉访客在场时 ``record_id = f"ep-{visitor_event.event_id}"``（I1）；
               纯音频 episode（无视觉访客）``record_id = f"ep-{audio_session_id}"``。
        返回 None 仅当 ``visitor_event`` 与 ``audio_session_id`` 均为 None（无溯源主体）。

        音频增强（ADR-0027 Slice B）：传入 ``evidence``（EvidenceItem 列表，通常
        ``modality=AUDIO``）与 ``audio_session_id`` 后，record 自动收敛 ``modalities``、
        以 ID 填充 ``evidence_refs``、写入 ``audio_session_id``，并在 summary 追加音频描述。
        """
        evidence = evidence or []
        # 纯音频 episode（无视觉访客）：D4 放宽 visitor_instance_id 不变式
        if visitor_event is None:
            if audio_session_id is None:
                return None
            return self._project_audio_only(audio_session_id, warnings, actions, evidence)

        # 1. 关联 WarningEvent（visitor_instance_id + 时间窗）
        related_warnings = self._filter_warnings(visitor_event, warnings)
        # 2. 关联 ActionCommand（按 warning_id）
        related_actions = self._filter_actions(related_warnings, actions)
        # 3. 聚合 risk_level（max wins，ADR-0010）
        risk_level, recommended_action = self._pick_max_risk(related_warnings)
        # 4. 合并 reason_summary（去重保序）
        reason_summary = self._merge_reasons(related_warnings)
        # 5. 生成 human-interpretable summary（确定性，无 LLM）+ 音频增强
        audio_clause = self._audio_descriptor(evidence) if evidence else None
        summary = self._build_summary(
            visitor_event,
            risk_level,
            recommended_action,
            reason_summary,
            related_actions,
            audio_clause=audio_clause,
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
            evidence_refs=self._collect_evidence_ids(evidence),
            modalities=self._infer_modalities(visitor_event, evidence),
            audio_session_id=audio_session_id,
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
        return

    def aggregate_semantic(self, episodes, dimension, period_key):
        """Semantic 聚合由 SemanticAggregator（Stage G/H）实现；本类不负责。"""
        return

    # ------------------------------------------------------------------
    # 内部关联逻辑
    # ------------------------------------------------------------------
    def _filter_warnings(
        self, visitor_event: VisitorEvent, warnings: list[WarningEvent]
    ) -> list[WarningEvent]:
        """按 visitor_instance_id + 时间窗筛选关联 WarningEvent。

        时间窗：`enter_time <= warning.created_at <= leave_time + 60s`。
        visitor 匹配：WarningEvent.trigger_events[].event_id 前缀 == visitor_id。
        """
        vid = str(visitor_event.visitor_id)
        window_end = visitor_event.leave_time + timedelta(seconds=self.ACTION_TOLERANCE_SECONDS)
        result: list[WarningEvent] = []
        seen_ids: set[str] = set()
        for w in warnings or []:
            # 时间窗（created_at 为 UTC，与 enter/leave 同基准）
            if w.created_at < visitor_event.enter_time or w.created_at > window_end:
                continue
            # visitor 关联
            if not self._warning_mentions_visitor(w, vid):
                continue
            # 按 warning_id 去重：上游重试会把同一条 WarningEvent 重复投递，若不去重，
            # source_event_ids / reason_summary 会随投递次数变长 → 同一次访问在
            # 「首投」与「重投」下产出字段不等的 record → upsert_episodic 抛 I2 违规。
            wid = str(w.warning_id)
            if wid in seen_ids:
                continue
            seen_ids.add(wid)
            result.append(w)
        # 按 created_at 排序：保证关联 warning 的顺序确定（I1 幂等 / 回放一致性
        # §6.7.2——上游乱序投递 warning 也产出相同 record，且 reason_summary /
        # source_event_ids 顺序可复现）。稳定排序，同 created_at 保留输入顺序。
        result.sort(key=lambda w: w.created_at)
        return result

    @staticmethod
    def _warning_mentions_visitor(warning: WarningEvent, visitor_id: str) -> bool:
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
    def _trigger_visitor_id(trigger: dict) -> str | None:
        """从 trigger_events 元素解析 visitor_id（见 `_warning_mentions_visitor`）。"""
        if trigger.get("visitor_id") is not None:
            return str(trigger["visitor_id"])
        event_id = trigger.get("event_id")
        if event_id is not None:
            return str(event_id).split(":", 1)[0]
        return None

    @staticmethod
    def _filter_actions(
        related_warnings: list[WarningEvent], actions: list[ActionCommand]
    ) -> list[ActionCommand]:
        """按 warning_id 关联 ActionCommand（按 command_id 去重）。

        与 `_filter_warnings` 同理：上游重试可能重复投递同一条 ActionCommand，
        不去重会让 `actions` / `source_event_ids` 随投递次数变长（I2 冲突）。
        """
        warning_ids = {str(w.warning_id) for w in related_warnings}
        if not warning_ids:
            return []
        result: list[ActionCommand] = []
        seen_ids: set[str] = set()
        for a in actions or []:
            if str(a.warning_id) not in warning_ids:
                continue
            cid = str(a.command_id)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            result.append(a)
        # 按 command_id 排序：与 `_filter_warnings` 按 created_at 排序对称，保证关联
        # action 的顺序确定（I1 幂等 / 回放一致性 §6.7.2）。若只去重不排序，上游在
        # 重投时若以不同顺序投递 ActionCommand（与 warning 乱序同类、同样会发生），
        # 投影出的 `actions` 列表 / `source_event_ids` 尾部顺序将不一致 → 第二次
        # `upsert_episodic` 抛 I2 → Shadow Mode 静默丢弃 episode。这正是本 PR 声称
        # 修复的那类缺陷，原本对 action 漏做了一半。command_id 唯一，无并列歧义。
        result.sort(key=lambda a: str(a.command_id))
        return result

    @staticmethod
    def _pick_max_risk(
        related_warnings: list[WarningEvent],
    ) -> (str | None, str | None):
        """取 risk_level 最高的 Warning 的 (risk_level, recommended_action)。

        max wins（HIGH > MEDIUM > LOW）；并列取首个出现者。
        无关联 warning 返回 (None, None)。
        """
        best: WarningEvent | None = None
        for w in related_warnings:
            rank = _RISK_RANK.get(w.risk_level, 0)
            if best is None or rank > _RISK_RANK.get(best.risk_level, 0):
                best = w
        if best is None:
            return None, None
        return best.risk_level, best.recommended_action

    @staticmethod
    def _merge_reasons(related_warnings: list[WarningEvent]) -> list[str]:
        """合并多条 Warning 的 reason_summary，去重保序。"""
        seen: list[str] = []
        for w in related_warnings:
            for r in w.reason_summary:
                if r not in seen:
                    seen.append(r)
        return seen

    @staticmethod
    def _to_action_summary(cmd: ActionCommand) -> ActionSummary:
        """ActionCommand → ActionSummary（不存 payload，ADR-0024 §3.2.1）。"""
        return ActionSummary(
            command_type=cmd.command_type,
            command_id=str(cmd.command_id),
            status=cmd.status,
            error=cmd.error,
        )

    @staticmethod
    def _collect_source_ids(
        visitor_event: VisitorEvent,
        related_warnings: list[WarningEvent],
        related_actions: list[ActionCommand],
    ) -> list[str]:
        """I4 可解释性：聚合全部源事件 id（visitor + warning + action）。"""
        ids: list[str] = [visitor_event.event_id]
        ids += [str(w.warning_id) for w in related_warnings]
        ids += [str(a.command_id) for a in related_actions]
        return ids

    def _build_summary(
        self,
        visitor_event: VisitorEvent,
        risk_level: str | None,
        recommended_action: str | None,
        reason_summary: list[str],
        related_actions: list[ActionCommand],
        audio_clause: str | None = None,
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
            if audio_clause:
                return base + f"，未触发风险，含音频异常：{audio_clause}。"
            return base + "，未触发风险。"

        risk_part = f"{base}，风险等级 {risk_level}"
        if reason_summary:
            risk_part += f"（{' / '.join(reason_summary)}）"
        action_phrase = self._build_action_phrase(related_actions, recommended_action)
        if action_phrase:
            risk_part += f"，{action_phrase}"
        if audio_clause:
            risk_part += f"，含音频异常：{audio_clause}"
        return risk_part + "。"

    def _build_action_phrase(
        self,
        related_actions: list[ActionCommand],
        recommended_action: str | None,
    ) -> str:
        """从实际 ActionCommand（优先）或 recommended_action（回退）生成动作短语。"""
        phrases: list[str] = []
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

    # ------------------------------------------------------------------
    # 音频增强（ADR-0027 Slice B）
    # ------------------------------------------------------------------
    @staticmethod
    def _audio_descriptor(evidence: list[EvidenceItem]) -> str | None:
        """从音频 EvidenceItem 收敛可读音频描述（确定性，无 LLM）。

        优先用 ``metadata['audio_kind']``（telephone/crying/rapid/raised），回退到
        ``kind``；去重保序。
        """
        if not evidence:
            return None
        seen: list[str] = []
        for e in evidence:
            kind = e.metadata.get("audio_kind") if e.metadata else None
            if kind is None:
                kind = e.kind
            label = _AUDIO_KIND_CN.get(kind, kind)
            if label and label not in seen:
                seen.append(label)
        return " / ".join(seen) if seen else None

    @staticmethod
    def _collect_evidence_ids(evidence: list[EvidenceItem]) -> list[str]:
        """证据 ID 引用（去重保序，ADR-0027 D2 / ADR-0024 I2 单调性）。"""
        seen: set[str] = set()
        refs: list[str] = []
        for e in evidence or []:
            if e.evidence_id in seen:
                continue
            seen.add(e.evidence_id)
            refs.append(e.evidence_id)
        return refs

    def _infer_modalities(
        self,
        visitor_event: VisitorEvent | None,
        evidence: list[EvidenceItem],
    ) -> list[EvidenceModality]:
        """收敛 modalities（D1）：视觉访客在场 → VISION；证据各自 modality 追加。"""
        mods: list[EvidenceModality] = []
        if visitor_event is not None:
            mods.append(EvidenceModality.VISION)
        seen = set(mods)
        for e in evidence or []:
            if e.modality not in seen:
                seen.add(e.modality)
                mods.append(e.modality)
        return mods

    def _project_audio_only(
        self,
        audio_session_id: str,
        warnings: list[WarningEvent],
        actions: list[ActionCommand],
        evidence: list[EvidenceItem],
    ) -> EpisodicRecord | None:
        """纯音频 episode 投影（无视觉访客，D4 匿名）。

        仅当存在音频 WarningEvent 或音频 evidence 时才投影；否则无内容返回 None。
        窗口由 evidence.captured_at / warning.created_at 派生（无则退化为单点）。
        """
        if not warnings and not evidence:
            return None
        related_warnings = list(warnings)
        related_actions = self._filter_actions(related_warnings, actions)
        risk_level, recommended_action = self._pick_max_risk(related_warnings)
        reason_summary = self._merge_reasons(related_warnings)
        audio_clause = self._audio_descriptor(evidence)
        summary = self._build_audio_summary(
            audio_session_id, risk_level, recommended_action,
            reason_summary, related_actions, audio_clause,
        )
        enter, leave, duration = self._audio_window(related_warnings, evidence)
        return EpisodicRecord(
            record_id=f"ep-{audio_session_id}",
            visitor_instance_id=None,  # 纯音频匿名，绝不反填 visitor（D4）
            person_identity_id=None,
            enter_time=enter,
            leave_time=leave,
            duration_seconds=duration,
            risk_level=risk_level,
            recommended_action=recommended_action,
            reason_summary=reason_summary,
            actions=[self._to_action_summary(c) for c in related_actions],
            evidence_refs=self._collect_evidence_ids(evidence),
            modalities=self._infer_modalities(None, evidence),
            audio_session_id=audio_session_id,
            source_event_ids=self._collect_audio_source_ids(
                related_warnings, related_actions, evidence
            ),
            summary=summary,
            model_version=self.MODEL_VERSION,
        )

    def _build_audio_summary(
        self,
        audio_session_id: str,
        risk_level: str | None,
        recommended_action: str | None,
        reason_summary: list[str],
        related_actions: list[ActionCommand],
        audio_clause: str | None,
    ) -> str:
        """纯音频 episode summary（确定性，无 LLM）。"""
        base = f"音频异常会话（会话 {audio_session_id}）"
        if risk_level is not None:
            base += f"，风险等级 {risk_level}"
            if reason_summary:
                base += f"（{' / '.join(reason_summary)}）"
        action_phrase = self._build_action_phrase(related_actions, recommended_action)
        if action_phrase:
            base += f"，{action_phrase}"
        if audio_clause:
            base += f"，含音频异常：{audio_clause}"
        return base + "。"

    @staticmethod
    def _collect_audio_source_ids(
        warnings: list[WarningEvent],
        actions: list[ActionCommand],
        evidence: list[EvidenceItem],
    ) -> list[str]:
        """纯音频 episode 的 I4 溯源：warning_id + action_id + evidence_id。"""
        ids: list[str] = [str(w.warning_id) for w in warnings]
        ids += [str(a.command_id) for a in actions]
        ids += [e.evidence_id for e in evidence]
        return ids

    @staticmethod
    def _audio_window(
        warnings: list[WarningEvent],
        evidence: list[EvidenceItem],
    ) -> tuple[datetime, datetime, float]:
        """纯音频 episode 窗口：由 evidence.captured_at / warning.created_at 派生。"""
        times: list[datetime] = [e.captured_at for e in evidence]
        times += [w.created_at for w in warnings]
        if not times:
            now = now_dt()
            return now, now, 0.0
        enter = min(times)
        leave = max(times)
        return enter, leave, float((leave - enter).total_seconds())

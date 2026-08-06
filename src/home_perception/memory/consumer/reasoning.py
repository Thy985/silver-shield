"""RuleBasedReasoningEngine（C-6 Reasoning Engine 接入，ADR-0025 / DESIGN §4.3）。

把 ``ReasoningInput``（只读）推理为 ``ReasoningResult``（参考推理，**非决策、非分数**）：

- **不决策**：绝不产出 / 改写 ``risk_score`` / ``decision`` / ``warning``（守 ADR-0010
  单一决策中心）；``suggested_action_hint`` 只是**非绑定**建议，且本次**不**被喂进
  ``DecisionPolicy``（仅经 ``FrameResult.reasoning_results`` Shadow 暴露，见
  ``runtime/memory_consumer_hook.py`` 与 ``runtime/pipeline.py``）。
- **只读**：``infer`` 是纯函数，只读取 ``ReasoningInput`` 的不可变字段，不改任何外部
  状态（Memory / Pipeline 概不触碰，C2）。
- **确定性（C3）**：``findings`` / ``source_refs`` 按固定顺序构造（当前事件 → 画像 →
  模式 → 冲突 → 既往动作 → 历史规模），同输入两次产出字段级一致（审计 / 回放一致）。

Phase 1 规则推理是"参考"而非"裁判"：它把 Memory 已观测到的模式翻译成人类可读的
findings + 与 ``DecisionPolicy`` 同词汇的提示，便于 Shadow 观测与未来决策增强。
即便 hint 与最终决策不同，也不影响主链路（hint 未接入决策）。

未来（ADR-0025 Phase 2=决策增强 / Phase 5=真实推理算法）可替换 ``ReasoningEngine``
实现，本默认实现仅在字段级消费 ``ReasoningInput``，不依赖任何具体聚合策略细节。
"""

from __future__ import annotations

from home_perception.core.event import EvidenceModality
from home_perception.memory.consumer.contracts import (
    RECOMMENDED_ACTION_HINTS,
    ReasoningInput,
    ReasoningResult,
    SourceRef,
)
from home_perception.memory.consumer.interfaces import ReasoningEngine


class RuleBasedReasoningEngine(ReasoningEngine):
    """确定性规则参考推理（Phase 1 默认实现）。

    只读消费 ``ReasoningInput``，合成人类可读 ``findings`` + ``explanation`` +
    非绑定 ``suggested_action_hint`` + ``source_refs``。不产分数、不决策、不写回
    Memory（守 ADR-0010 单一决策中心 + ADR-0025 C1/C2）。
    """

    def infer(self, ctx: ReasoningInput) -> ReasoningResult:
        findings: list[str] = []
        source_refs: list[SourceRef] = []

        # 1) 当前事件事实（客观属性，非评分）
        ce = ctx.current_event
        ce_bits = [f"当前事件 {ce.event_id}（类型 {ce.event_type}）"]
        if ce.risk_level:
            ce_bits.append(f"实时风险等级 {ce.risk_level}")
        if ce.markers:
            ce_bits.append(f"行为标记 {', '.join(ce.markers)}")
        findings.append("，".join(ce_bits))
        source_refs.append(SourceRef(source="current_event", ref=ce.event_id))

        # 2) 访客长期画像（统计描述，非分数）
        profile = ctx.visitor_profile
        if profile is not None:
            findings.append(
                f"访客 {profile.visitor_instance_id} 历史到访 {profile.visit_count} 次，"
                f"夜间到访占比 {profile.night_visit_ratio:.0%}，置信度 {profile.confidence}"
                f"（身份已确认={profile.identity_confirmed}）"
            )
            source_refs.append(
                SourceRef(
                    source="visitor_profile",
                    ref=profile.visitor_instance_id,
                    detail=(
                        f"visit_count={profile.visit_count},"
                        f"night_visit_ratio={profile.night_visit_ratio},"
                        f"confidence={profile.confidence}"
                    ),
                )
            )

        # 3) 风险模式（非分数，模式描述）
        pattern = ctx.risk_pattern
        if pattern is not None and pattern.tags:
            findings.append(
                f"发现风险模式：{'、'.join(pattern.tags)}（置信度 {pattern.confidence}）"
            )
            for tag in pattern.tags:
                source_refs.append(SourceRef(source="risk_pattern", ref=tag))
            if pattern.escalation_history:
                findings.append("行为升级轨迹：" + " → ".join(pattern.escalation_history))

        # 3.5) 音频模式（ADR-0027 D6，纯描述标签，非评分）
        if pattern is not None and pattern.audio_patterns:
            ratio_desc = (
                f"，音频 episode 占比 {pattern.audio_episode_ratio:.0%}"
                if pattern.audio_episode_ratio is not None
                else ""
            )
            findings.append(
                f"音频模式：{'、'.join(pattern.audio_patterns)}{ratio_desc}"
                f"（纯描述已观测音频类型，非风险评分）"
            )
            for label in pattern.audio_patterns:
                source_refs.append(
                    SourceRef(source="risk_pattern", ref=label, detail="audio_pattern")
                )

        # 4) 冲突（只标记，不解决；C4 透明）
        for c in ctx.conflicts:
            findings.append(
                f"检测到冲突（{c.type}）：历史={c.historical}，当前={c.current}；{c.detail}"
            )
            source_refs.append(SourceRef(source="conflicts", ref=c.type, detail=c.detail))

        # 5) 既往动作（ADR-0011 ActionCommand 历史投影）
        for a in ctx.previous_actions:
            findings.append(f"既往动作：{a.command_type}（状态={a.status}）")
            source_refs.append(SourceRef(source="previous_actions", ref=a.command_id))

        # 6) 历史上下文规模（仅陈述可召回量，不评分）
        if ctx.historical_context:
            n_hist = len(ctx.historical_context)
            findings.append(f"可召回历史记录 {n_hist} 条")
            source_refs.append(
                SourceRef(
                    source="historical_context",
                    ref=ctx.historical_context[0].record_id,
                    detail=f"n={n_hist}",
                )
            )

        # 6.5) 模态提示（ADR-0027 D6）：仅当历史上下文含 AUDIO 时陈述——
        #      让 Reasoning 感知"该上下文有音频事实"，即使样本不足未产出模式
        #      （如仅 1 条音频记录低于 min_records_for_pattern）也不丢信息。
        if EvidenceModality.AUDIO in ctx.modalities:
            findings.append("历史上下文含音频 episode（模态提示 AUDIO，证据经 evidence_refs 可解析）")
            source_refs.append(SourceRef(source="modalities", ref="AUDIO"))

        explanation = self._explain(ctx, has_profile=profile is not None)
        hint = self._hint(ctx)
        return ReasoningResult(
            findings=tuple(findings),
            explanation=explanation,
            suggested_action_hint=hint,
            source_refs=tuple(source_refs),
        )

    # -- 辅助（纯函数，确定性）---------------------------------------------

    @staticmethod
    def _explain(ctx: ReasoningInput, *, has_profile: bool) -> str:
        """生成一句人类可读的可解释说明（继承 ADR-0024 Trust Layer）。"""
        n_hist = len(ctx.historical_context)
        has_pattern = bool(ctx.risk_pattern and ctx.risk_pattern.tags)
        has_conflict = bool(ctx.conflicts)
        if not has_profile and n_hist == 0:
            return (
                "无可用历史记忆：本次事件孤立，参考推理仅基于当前事实。"
                "建议接入更多历史以建立长期画像后再评估。"
            )
        parts: list[str] = []
        if has_profile:
            parts.append("结合该访客历史画像")
        if has_pattern:
            parts.append("识别到风险模式")
        if has_conflict:
            parts.append("存在历史与当前的冲突")
        if n_hist:
            parts.append(f"召回 {n_hist} 条历史记录")
        base = "、".join(parts) + "。" if parts else "基于当前事实。"
        return base + " 每条发现均溯源至 ReasoningInput 字段（见 source_refs）。"

    @staticmethod
    def _hint(ctx: ReasoningInput) -> str | None:
        """把已观测模式翻译为与 DecisionPolicy 同词汇的**非绑定**提示（仅 advisory）。

        ⚠️ 本方法**绝不**提升或设定风险等级——风险等级由 DecisionPolicy 独立计算。
        此处只是把"已观测事实"用一致词汇复述，方便 Shadow 观测与未来决策增强；
        Phase 1 该 hint 未被喂进 DecisionPolicy（仅经 FrameResult 暴露）。
        """
        level = ctx.current_event.risk_level
        if level == "HIGH":
            return "ESCALATE_COMMUNITY"
        if level == "MEDIUM":
            return "NOTIFY_FAMILY"
        tags = ctx.risk_pattern.tags if ctx.risk_pattern is not None else ()
        if "escalating_behavior" in tags or ctx.conflicts:
            return "NOTIFY_FAMILY"
        if "repeated_visit" in tags:
            return "MONITOR"
        return None


__all__ = ["RECOMMENDED_ACTION_HINTS", "RuleBasedReasoningEngine"]

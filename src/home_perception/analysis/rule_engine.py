"""Rule Engine 编排器（P0-7b · 风险语义层）。

> **P0-7b = 风险语义层。** 消费 `RiskFeature`（P0-7a）输出 `PerceptionEvent`（§7.2 5 类）。
> 继续 ADR-0009 6 条决策。

包含：
- `ThresholdConfig`：阈值与权重配置（ADR-0009 Decision 6）
- 4 条基础 Rule + 1 条 CompositeRule
- `WhitelistProvider` protocol（PendingVerifyRule 用，第一版 NotImplementedError）
- `RuleEngine`：编排器，集成 CooldownGate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Set
from uuid import UUID

from ..common.logging import get_logger
from ..common.timeutil import now_dt
from .cooldown import CooldownGate
from .feature import RiskFeature
from .perception import PerceptionEvent
from .rule import CompositeRule, Rule, RuleContext, RuleResult

log = get_logger(__name__)


# ============================================================================
# ThresholdConfig（ADR-0009 Decision 6：阈值配置化）
# ============================================================================

@dataclass
class ThresholdConfig:
    """阈值与权重集中配置（不硬编码在 Rule 类里）。"""

    # LongDurationRule
    long_duration_seconds: float = 300.0
    # RepeatVisitRule
    repeat_visit_count: int = 3
    # OddHourRule
    odd_hour_set: Set[int] = field(default_factory=lambda: {23, 0, 1, 2, 3, 4})
    # CooldownGate
    cooldown_seconds: float = 600.0
    reset_gap_seconds: float = 1800.0
    # HighRiskApproachRule (Composite)
    high_risk_required_rules: Set[str] = field(default_factory=lambda: {
        "LongDurationRule", "RepeatVisitRule", "OddHourRule",
    })
    # Rule 命中权重（perception_score 基础值）
    rule_weights: Dict[str, float] = field(default_factory=lambda: {
        "LongDurationRule": 0.50,
        "RepeatVisitRule": 0.30,
        "OddHourRule": 0.10,
        "HighRiskApproachRule": 0.90,
    })

    def weight_for(self, rule_name: str) -> float:
        return self.rule_weights.get(rule_name, 0.0)


# ============================================================================
# WhitelistProvider（PendingVerifyRule 用，v2 实现）
# ============================================================================

class WhitelistProvider(Protocol):
    """白名单提供方协议（PendingVerifyRule 用）。

    P0-7b 第一版不实现真实数据源。PendingVerifyRule 调用时抛 NotImplementedError。
    v2 接入实际白名单（配置文件 / 中心回写 / 数据库）时实现此 protocol。
    """

    def is_whitelisted(self, visitor_id: UUID) -> bool:
        """判断 visitor_id 是否在白名单（家属 / 已知访客）。"""
        ...


# ============================================================================
# 4 条基础 Rule
# ============================================================================

class LongDurationRule(Rule):
    """停留时长超阈值 → abnormal_dwell。"""

    name = "LongDurationRule"

    def evaluate(self, ctx: RuleContext, risk: RiskFeature) -> List[RuleResult]:
        if risk.duration is None:
            return [RuleResult(rule_name=self.name, matched=False, notes="DurationFeature 缺失")]
        d = risk.duration.duration_seconds
        threshold = ctx.thresholds.long_duration_seconds
        if d >= threshold:
            return [RuleResult(
                rule_name=self.name, matched=True,
                event_type="abnormal_dwell",
                perception_score=ctx.thresholds.weight_for(self.name),
                evidence={"duration_seconds": d, "threshold": threshold},
                notes=f"停留 {d:.1f}s >= 阈值 {threshold:.1f}s",
            )]
        return [RuleResult(
            rule_name=self.name, matched=False,
            evidence={"duration_seconds": d, "threshold": threshold},
            notes=f"停留 {d:.1f}s < 阈值 {threshold:.1f}s（不触发）",
        )]


class RepeatVisitRule(Rule):
    """窗口内同 visitor_id 访问次数超阈值 → repeat_visit。"""

    name = "RepeatVisitRule"

    def evaluate(self, ctx: RuleContext, risk: RiskFeature) -> List[RuleResult]:
        if risk.frequency is None:
            return [RuleResult(rule_name=self.name, matched=False, notes="VisitFrequencyFeature 缺失")]
        count = risk.frequency.visits_in_window
        threshold = ctx.thresholds.repeat_visit_count
        if count >= threshold:
            return [RuleResult(
                rule_name=self.name, matched=True,
                event_type="repeat_visit",
                perception_score=ctx.thresholds.weight_for(self.name),
                evidence={"visits_in_window": count, "threshold": threshold, "window_s": risk.frequency.window_seconds},
                repeat_count=count,
                notes=f"窗口内 {count} 次 >= 阈值 {threshold} 次",
            )]
        return [RuleResult(
            rule_name=self.name, matched=False,
            evidence={"visits_in_window": count, "threshold": threshold},
            notes=f"窗口内 {count} 次 < 阈值 {threshold} 次（不触发）",
        )]


class OddHourRule(Rule):
    """小时落在异常时段集合 → visit_normal + is_odd_hour 叠加标记。"""

    name = "OddHourRule"

    def evaluate(self, ctx: RuleContext, risk: RiskFeature) -> List[RuleResult]:
        if risk.time is None:
            return [RuleResult(rule_name=self.name, matched=False, notes="TimeFeature 缺失")]
        hour = risk.time.hour_of_day
        if hour in ctx.thresholds.odd_hour_set:
            return [RuleResult(
                rule_name=self.name, matched=True,
                event_type="visit_normal",  # §7.2：异常时段是 visit_normal + is_odd_hour 标记
                perception_score=ctx.thresholds.weight_for(self.name),
                evidence={"hour_of_day": hour, "odd_hour_set": sorted(ctx.thresholds.odd_hour_set)},
                is_odd_hour=True,
                notes=f"hour={hour} 属异常时段",
            )]
        return [RuleResult(
            rule_name=self.name, matched=False,
            evidence={"hour_of_day": hour},
            notes=f"hour={hour} 非异常时段（不触发）",
        )]


class PendingVerifyRule(Rule):
    """非白名单访客 → visit_pending_verify。

    **P0-7b 第一版不实现**（ADR-0009 Decision 7）：无 WhitelistProvider 数据源。
    留接口供 v2 接入。
    """

    name = "PendingVerifyRule"

    def evaluate(self, ctx: RuleContext, risk: RiskFeature) -> List[RuleResult]:
        whitelist: Optional[WhitelistProvider] = ctx.extra.get("whitelist")
        if whitelist is None:
            raise NotImplementedError(
                "PendingVerifyRule 需要 WhitelistProvider；"
                "P0-7b 第一版未提供，请通过 ctx.extra['whitelist'] 注入"
            )
        if whitelist.is_whitelisted(risk.visitor_id):
            return [RuleResult(
                rule_name=self.name, matched=False,
                evidence={"whitelisted": True},
                notes="访客在白名单，跳过 PendingVerify",
            )]
        return [RuleResult(
            rule_name=self.name, matched=True,
            event_type="visit_pending_verify",
            perception_score=ctx.thresholds.weight_for(self.name),
            evidence={"whitelisted": False},
            notes="访客不在白名单",
        )]


# ============================================================================
# CompositeRule：HighRiskApproachRule
# ============================================================================

class HighRiskApproachRule(CompositeRule):
    """组合规则：长停留 + 重复 + 异常时段 同时命中 → high_risk_approach（最高风险）。

    消费前序 Rule 的 RuleResult（不复算 Feature）。
    """

    name = "HighRiskApproachRule"

    def evaluate(
        self,
        ctx: RuleContext,
        risk: RiskFeature,
        prior_results: List[RuleResult],
    ) -> List[RuleResult]:
        matched_names = {r.rule_name for r in prior_results if r.matched}
        required = ctx.thresholds.high_risk_required_rules
        if required.issubset(matched_names):
            # 计算 perception_score = 子 Rule weight 之和（封顶 1.0）
            # round 固定精度：消除浮点累加顺序 / 平台差异导致的 0.8999... 抖动
            # （set 迭代顺序受 PYTHONHASHSEED 影响，不同顺序累加结果可能差 1 ULP）
            sub_score = sum(
                ctx.thresholds.weight_for(name)
                for name in required
            )
            score = round(min(1.0, sub_score), 4)
            return [RuleResult(
                rule_name=self.name, matched=True,
                event_type="high_risk_approach",
                perception_score=score,
                evidence={
                    "required_rules": sorted(required),
                    "matched_rules": sorted(matched_names),
                    "sub_score_sum": round(sub_score, 4),
                },
                notes=f"组合规则命中：{sorted(required)}",
            )]
        return [RuleResult(
            rule_name=self.name, matched=False,
            evidence={
                "required_rules": sorted(required),
                "matched_rules": sorted(matched_names),
            },
            notes=f"组合规则未全命中（matched={sorted(matched_names)}，required={sorted(required)}）",
        )]


# ============================================================================
# RuleEngine 编排器
# ============================================================================

class RuleEngine:
    """Rule Engine 编排器（P0-7b 入口）。

    流程：
    1. 4 条基础 Rule 消费 `RiskFeature` → 各自 RuleResult
    2. CompositeRule 消费 RuleResult[] → 自己的 RuleResult
    3. CooldownGate 过滤（去重）→ 允许的 RuleResult 转为 PerceptionEvent
    4. 输出 PerceptionEvent 列表（供 P0-9 MQTT 上报）

    用法：
        engine = RuleEngine(device_id="home_entry_01", location="入户门")
        for risk_feature in feature_stream:
            for event in engine.evaluate(risk_feature):
                # event: PerceptionEvent
                ...
    """

    def __init__(
        self,
        device_id: str,
        location: Optional[str] = None,
        thresholds: Optional[ThresholdConfig] = None,
        cooldown: Optional[CooldownGate] = None,
        now_provider=None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        if device_id is None or not str(device_id).strip():
            raise ValueError("device_id 不能为空")
        self.device_id = device_id
        self.location = location
        self.thresholds = thresholds or ThresholdConfig()
        self.cooldown = cooldown or CooldownGate(
            cooldown_seconds=self.thresholds.cooldown_seconds,
            reset_gap_seconds=self.thresholds.reset_gap_seconds,
        )
        self._now = now_provider or now_dt
        self._extra = extra or {}

        # 注册 Rule（顺序：先基础 Rule，后 CompositeRule）
        # 4 条基础 Rule 权重来自 ThresholdConfig
        self._basic_rules: List[Rule] = [
            LongDurationRule(weight=self.thresholds.weight_for("LongDurationRule")),
            RepeatVisitRule(weight=self.thresholds.weight_for("RepeatVisitRule")),
            OddHourRule(weight=self.thresholds.weight_for("OddHourRule")),
            # PendingVerifyRule 不默认注册（需 Whitelist）；用户可显式 enable_pending_verify() 加入
        ]
        self._pending_rule: Optional[PendingVerifyRule] = None
        self._composite_rules: List[CompositeRule] = [
            HighRiskApproachRule(weight=self.thresholds.weight_for("HighRiskApproachRule")),
        ]

    def enable_pending_verify(self, whitelist: WhitelistProvider) -> None:
        """启用 PendingVerifyRule（v2 + WhitelistProvider 注入时调用）。"""
        self._pending_rule = PendingVerifyRule(
            weight=self.thresholds.weight_for("PendingVerifyRule"),
        )
        self._extra["whitelist"] = whitelist

    def evaluate(self, risk: RiskFeature) -> List[PerceptionEvent]:
        """单次评估：消费 RiskFeature，输出 PerceptionEvent 列表（含 Cooldown 过滤）。"""
        ctx = RuleContext(now=self._now(), thresholds=self.thresholds, extra=self._extra)
        results: List[RuleResult] = []

        # 1) 基础 Rule
        for rule in self._basic_rules:
            results.extend(rule.evaluate(ctx, risk))
        # 1.5) PendingVerifyRule（可选）
        if self._pending_rule is not None:
            results.extend(self._pending_rule.evaluate(ctx, risk))

        # 2) CompositeRule
        for comp in self._composite_rules:
            results.extend(comp.evaluate(ctx, risk, results))

        # 3) Cooldown 过滤 + 4) 转 PerceptionEvent
        events: List[PerceptionEvent] = []
        for r in results:
            if not r.matched:
                continue
            if not self.cooldown.try_trigger(risk.visitor_id, r.rule_name, now=ctx.now):
                log.debug("cooldown.suppressed", rule_name=r.rule_name, visitor_id=str(risk.visitor_id))
                continue
            events.append(self._to_perception(r, risk, ctx.now))
        return events

    def _to_perception(
        self, result: RuleResult, risk: RiskFeature, now: datetime,
    ) -> PerceptionEvent:
        """RuleResult → PerceptionEvent。"""
        # meta 必含 rule 字段（§7.2）
        meta: Dict[str, Any] = {
            "rule": result.rule_name,
            "evidence": result.evidence,
            "notes": result.notes,
            "source_event_id": risk.event_id,  # P0-6 VisitorEvent.event_id
        }
        # 透传关键 Feature 值到 meta（便于中心审计）
        if risk.duration is not None:
            meta["dwell_s"] = risk.duration.duration_seconds
        if risk.frequency is not None:
            meta["visits_in_window"] = risk.frequency.visits_in_window
            meta["visit_window_s"] = risk.frequency.window_seconds
        if risk.time is not None:
            meta["hour_of_day"] = risk.time.hour_of_day
            meta["day_of_week"] = risk.time.day_of_week
            meta["is_weekend"] = risk.time.is_weekend
        return PerceptionEvent(
            device_id=self.device_id,
            event_type=result.event_type,  # 已校验
            score=result.perception_score,
            visitor_id=risk.visitor_id,
            source_video=risk.source_video,
            timestamp=now.timestamp(),
            # track_id 暂缺（VisitorTrack → VisitorEvent 未透传）；v2 完善
            location=self.location,
            repeat_count=result.repeat_count if result.repeat_count > 1 else None,
            is_odd_hour=result.is_odd_hour,
            evidence=[],  # P0-7b 不填，P0-8 取证层填
            meta=meta,
        )

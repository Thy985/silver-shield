"""规则层领域对象（P0-7b · 风险语义层）。

> **P0-7b = 风险语义层。** Rule 消费 `RiskFeature`（P0-7a）输出 `PerceptionEvent`（§7.2）。
> 继续 Owner P0-6 / P0-7a 原则（ADR-0007 / ADR-0008 / ADR-0009）：
> - Rule 不读 VisitorEvent（守 P0-7a 边界）
> - Rule 输出 PerceptionEvent，不直接输出 WarningEvent（不跳级）
> - score 是规则命中强度，不是诈骗概率（避免中心误解）

`Rule` 抽象基类 + `RuleResult`（Rule 命中输出）+ `RuleContext`（执行上下文）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .feature import RiskFeature


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# RuleContext：执行上下文（向 Rule 注入时间 / 阈值 / 历史等）
# ============================================================================

@dataclass
class RuleContext:
    """Rule 执行上下文（向 Rule 注入时间、阈值、Whitelist 等依赖）。

    字段：
    - `now`：当前时间（UTC，可注入便于测试）
    - `thresholds`：阈值配置（`ThresholdConfig`）
    - `extra`：扩展字段（WhitelistProvider / 中心回写配置等 v2 注入）

    Rule 接收 `RuleContext` 而非全局单例，便于：
    - 单元测试独立构造上下文
    - 运行时配置热更新（v2）
    - Rule 不可见 ThresholdConfig 的具体实现细节
    """

    now: datetime = field(default_factory=_utc_now)
    thresholds: Any = None  # ThresholdConfig，由 rule_engine.py 定义并注入（避免循环 import）
    extra: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# RuleResult：Rule 命中输出（尚未序列化为 PerceptionEvent）
# ============================================================================

@dataclass
class RuleResult:
    """Rule 命中输出（RuleEngine 收集后转 PerceptionEvent）。

    字段：
    - `rule_name`：触发的 Rule 名称（与 §7.2 meta.rule 对齐）
    - `matched`：是否命中
    - `event_type`：命中的 PerceptionEvent 类型（§7.2 五类之一）；不匹配时 None
    - `perception_score`：规则命中强度（0-1），**不是诈骗概率**（ADR-0009）
    - `evidence`：输入 Feature 关键值（用于 §7.2 meta 字段 + 可解释性）
    - `is_odd_hour` / `repeat_count`：§7.2 叠加标记
    - `notes`：调试 / 解释文本（不进 PerceptionEvent）
    """

    rule_name: str
    matched: bool
    event_type: Optional[str] = None
    perception_score: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    is_odd_hour: bool = False
    repeat_count: int = 1
    notes: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.perception_score <= 1.0):
            raise ValueError(
                f"perception_score 必须在 [0, 1]，收到 {self.perception_score}"
            )
        if self.matched and self.event_type is None:
            raise ValueError("matched=True 时必须指定 event_type")
        if not self.matched and self.event_type is not None:
            # 允许（不命中但带 event_type 用于调试），但给出警告
            pass
        if self.repeat_count < 0:
            raise ValueError(f"repeat_count 必须 >= 0，收到 {self.repeat_count}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "matched": self.matched,
            "event_type": self.event_type,
            "perception_score": round(self.perception_score, 4),
            "evidence": {k: (round(v, 3) if isinstance(v, float) else v)
                         for k, v in self.evidence.items()},
            "is_odd_hour": self.is_odd_hour,
            "repeat_count": self.repeat_count,
            "notes": self.notes,
        }


# ============================================================================
# Rule 抽象基类
# ============================================================================

class Rule(ABC):
    """规则抽象基类。所有具体 Rule 必须实现 `evaluate(ctx, risk)`。

    设计原则（ADR-0009）：
    - 4 个基础 Rule：`LongDurationRule` / `RepeatVisitRule` / `OddHourRule` / `PendingVerifyRule`
    - 1 个 CompositeRule：`HighRiskApproachRule`（消费 `RuleResult[]` 不复算）
    - Rule **不** import 阈值常量，**只**通过 `ctx.thresholds` 注入
    - Rule **不** import VisitorEvent，**只**消费 RiskFeature
    """

    name: str = "Rule"  # 子类必须覆盖

    def __init__(self, weight: float = 0.0):
        """weight 是 Rule 命中时的 perception_score 基础值（0-1，由 ThresholdConfig.rule_weights 注入）。"""
        if not (0.0 <= weight <= 1.0):
            raise ValueError(f"weight 必须在 [0, 1]，收到 {weight}")
        self.weight = weight

    @abstractmethod
    def evaluate(self, ctx: RuleContext, risk: RiskFeature) -> List[RuleResult]:
        """消费 RiskFeature，输出 RuleResult 列表（matched=True 才会转 PerceptionEvent）。

        通常实现：
        - 不满足条件 → 返回 [RuleResult(matched=False, ...)]
        - 满足条件   → 返回 [RuleResult(matched=True, event_type=..., perception_score=self.weight, ...)]
        """
        raise NotImplementedError


class CompositeRule(Rule):
    """组合规则基类：消费多条 Rule 的 RuleResult，不重算 Feature。

    子类只需实现 `evaluate(ctx, risk, prior_results)`（与 Rule 不同，多一个 prior_results 参数）。
    """

    @abstractmethod
    def evaluate(
        self,
        ctx: RuleContext,
        risk: RiskFeature,
        prior_results: List[RuleResult],
    ) -> List[RuleResult]:
        """消费 `prior_results`（前序 Rule 命中情况），返回 CompositeRule 自己的 RuleResult。"""
        raise NotImplementedError

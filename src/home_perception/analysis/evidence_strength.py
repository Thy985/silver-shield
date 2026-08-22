"""Audio Evidence Strength 五档分级与 modality-aware routing（ADR-0042）。

> **冻结语义，不冻结参数（Owner 2026-08-22）**：本模块冻结五档证据强度的**枚举与
> 判定维度**；各维度的**阈值数值**全部为候选参数（``AudioEvidenceConfig``），由真实
> telephone_risk 验收数据回填（D3 流程：修 class_map → 真实 AudioKind 分布 → E2E →
> precision/recall/false-escalation → 定参）。候选映射表**不是 ADR 默认事实**。

五档语义（D1，冻结）：

- ``INSUFFICIENT``：置信或强度低于门槛 → 不产信号（静默）；
- ``MONITOR``：单次可信信号 → 仅观察记录，不触发告警动作；
- ``RAISE``：同类声学信号具有持续性 → 升起本地风险信号（RiskSignal RAISED）；
- ``NOTIFY``：多种独立声学信号并存 → 风险值得通知家属；
- ``ESCALATE``：Vision + Audio 经时间对齐构成多模态验证链（ADR-0041
  ``LinkedSignalPair`` 硬前提）→ 升级中心。

判定维度（D2，冻结）：score / confidence / 同类持续性（次数·时长）/ 独立信号多样性
（不同 kind 数）/ 跨模态关联。**Evidence Continuity > Event Count**：单事件永不升级。

MONITOR ceiling 门控（D4 硬闸门）：YAMNet ``class_map_path=""`` 修复并经标签真实性
验证前，音频证据强度**封顶 MONITOR**——fallback 事件永不驱动 RAISE 及以上；
ceiling 期行为与 ADR-0038 已验证的 Live Runtime 行为一致（LOW → MONITOR → LOG_ONLY）。
"""

from __future__ import annotations

from enum import Enum

from .warning import RECOMMENDED_ACTIONS, RISK_LEVELS


class EvidenceStrength(str, Enum):
    """音频证据强度五档（ADR-0042 D1 冻结；均为"证据强度"描述，非诈骗判定，ADR-0001）。"""

    INSUFFICIENT = "insufficient"  # 证据不足 → 不产信号（静默）
    MONITOR = "monitor"  # 单次弱信号 → 仅记录观察
    RAISE = "raise"  # 持续信号 → 升起本地风险信号（RAISED）
    NOTIFY = "notify"  # 多独立信号 → 通知家属
    ESCALATE = "escalate"  # 多模态验证链 → 升级中心


# 升级序（强度单调递增；ceiling 压制 = min(strength, MONITOR)）
STRENGTH_ORDER: dict[EvidenceStrength, int] = {
    EvidenceStrength.INSUFFICIENT: 0,
    EvidenceStrength.MONITOR: 1,
    EvidenceStrength.RAISE: 2,
    EvidenceStrength.NOTIFY: 3,
    EvidenceStrength.ESCALATE: 4,
}

# 候选路由映射（modality-aware routing：strength → (risk_level, recommended_action)）。
# ⚠️ **候选值，非冻结参数**（Owner：参数悬空期仅 MONITOR 可达；回填后经契约测试钉死）。
# INSUFFICIENT 无映射（不产信号）；ESCALATE 的可达性另受 escalate 开关 +
# LinkedSignalPair 验证（D6）双重门控。
CANDIDATE_STRENGTH_ROUTING: dict[EvidenceStrength, tuple[str, str]] = {
    EvidenceStrength.MONITOR: ("LOW", "MONITOR"),
    EvidenceStrength.RAISE: ("LOW", "NOTIFY_FAMILY"),
    EvidenceStrength.NOTIFY: ("MEDIUM", "NOTIFY_FAMILY"),
    EvidenceStrength.ESCALATE: ("HIGH", "ESCALATE_COMMUNITY"),
}


def route_strength(
    strength: EvidenceStrength, *, ceiling_monitor_only: bool
) -> tuple[str, str] | None:
    """把证据强度路由为 (risk_level, recommended_action) 决策建议。

    - ``INSUFFICIENT`` → ``None``（无决策产物）；
    - ``ceiling_monitor_only=True``（D4 硬闸门）→ 一切非 INSUFFICIENT 档压回
      ``("LOW", "MONITOR")``——观察记录无害，升级动作结构性不可达；
    - ceiling 解除后按 :data:`CANDIDATE_STRENGTH_ROUTING` 查表（缺键视为 INSUFFICIENT）。

    返回值仅供 DecisionPolicy 作为 modality-aware 输入参考；最终裁决仍归
    单一决策中心（ADR-0010），本函数不做任何诈骗判定（ADR-0001）。
    """
    if strength is EvidenceStrength.INSUFFICIENT:
        return None
    if ceiling_monitor_only:
        return ("LOW", "MONITOR")
    routed = CANDIDATE_STRENGTH_ROUTING.get(strength)
    if routed is None:
        return None
    level, action = routed
    if level not in RISK_LEVELS or action not in RECOMMENDED_ACTIONS:
        raise ValueError(
            f"候选路由表含非法值 {routed!r}：level 须 ∈ {RISK_LEVELS}，"
            f"action 须 ∈ {RECOMMENDED_ACTIONS}"
        )
    return routed


__all__ = [
    "CANDIDATE_STRENGTH_ROUTING",
    "STRENGTH_ORDER",
    "EvidenceStrength",
    "route_strength",
]
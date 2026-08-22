"""EvidenceStrength 五档与 modality-aware routing 测试（ADR-0042）。

覆盖：
- **D1 五档枚举冻结**：成员与取值逐字对应 ADR-0042 D1 代码块；
- **D4 MONITOR ceiling**：硬闸门开启时一切非 INSUFFICIENT 档压回 ("LOW","MONITOR")
  （与 ADR-0038 已验证行为一致）；
- **候选路由表合法性**：level/action 均在 WarningEvent 白名单内（候选值非法时
  fail-fast，防回填参数时引入脏值）。
"""

from __future__ import annotations

from home_perception.analysis.evidence_strength import (
    CANDIDATE_STRENGTH_ROUTING,
    STRENGTH_ORDER,
    EvidenceStrength,
    route_strength,
)
from home_perception.analysis.warning import RECOMMENDED_ACTIONS, RISK_LEVELS


class TestFiveGradesFrozen:
    def test_enum_members_and_values_pinned(self):
        """D1 五档逐字冻结：改名/改值/增删档位必须先修订 ADR-0042。"""
        assert {s.name: s.value for s in EvidenceStrength} == {
            "INSUFFICIENT": "insufficient",
            "MONITOR": "monitor",
            "RAISE": "raise",
            "NOTIFY": "notify",
            "ESCALATE": "escalate",
        }

    def test_strength_order_monotonic(self):
        """升级序单调：INSUFFICIENT < MONITOR < RAISE < NOTIFY < ESCALATE。"""
        ordered = sorted(EvidenceStrength, key=lambda s: STRENGTH_ORDER[s])
        assert ordered == [
            EvidenceStrength.INSUFFICIENT,
            EvidenceStrength.MONITOR,
            EvidenceStrength.RAISE,
            EvidenceStrength.NOTIFY,
            EvidenceStrength.ESCALATE,
        ]


class TestRouteStrength:
    def test_insufficient_routes_to_none(self):
        assert route_strength(EvidenceStrength.INSUFFICIENT, ceiling_monitor_only=False) is None
        assert route_strength(EvidenceStrength.INSUFFICIENT, ceiling_monitor_only=True) is None

    def test_ceiling_presses_everything_to_monitor(self):
        """D4 硬闸门：ceiling 开启 → RAISE/NOTIFY/ESCALATE 一律压回观察记录。"""
        for s in (EvidenceStrength.MONITOR, EvidenceStrength.RAISE, EvidenceStrength.NOTIFY, EvidenceStrength.ESCALATE):
            assert route_strength(s, ceiling_monitor_only=True) == ("LOW", "MONITOR")

    def test_candidate_table_used_when_ceiling_lifted(self):
        assert route_strength(EvidenceStrength.RAISE, ceiling_monitor_only=False) == (
            "LOW",
            "NOTIFY_FAMILY",
        )
        assert route_strength(EvidenceStrength.NOTIFY, ceiling_monitor_only=False) == (
            "MEDIUM",
            "NOTIFY_FAMILY",
        )
        assert route_strength(EvidenceStrength.ESCALATE, ceiling_monitor_only=False) == (
            "HIGH",
            "ESCALATE_COMMUNITY",
        )

    def test_monitor_unchanged_by_ceiling(self):
        assert route_strength(EvidenceStrength.MONITOR, ceiling_monitor_only=False) == ("LOW", "MONITOR")

    def test_candidate_values_within_warning_whitelist(self):
        """候选映射值必须在 WarningEvent 白名单内（回填参数时的 fail-fast 兜底）。"""
        for level, action in CANDIDATE_STRENGTH_ROUTING.values():
            assert level in RISK_LEVELS
            assert action in RECOMMENDED_ACTIONS
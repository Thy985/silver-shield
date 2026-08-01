"""Rule / PerceptionEvent / CooldownGate / RuleEngine 测试（P0-7b · 风险语义层）。

> **P0-7b = 风险语义层。** Rule 消费 RiskFeature → PerceptionEvent（§7.2 5 类）。
> 继续 ADR-0007 / ADR-0008 / ADR-0009 边界：Feature 不掺判断、Rule 不读 Event、score 是强度非诈骗概率。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from home_perception.analysis.cooldown import CooldownGate, CooldownState
from home_perception.analysis.feature import (
    DurationFeature,
    RiskFeature,
    TimeFeature,
    TrajectoryFeature,
    VisitFrequencyFeature,
)
from home_perception.analysis.perception import EVENT_TYPES, PerceptionEvent
from home_perception.analysis.rule import RuleContext, RuleResult
from home_perception.analysis.rule_engine import (
    HighRiskApproachRule,
    LongDurationRule,
    OddHourRule,
    PendingVerifyRule,
    RepeatVisitRule,
    RuleEngine,
    ThresholdConfig,
)


# ============================================================================
# 时区 helper
# ============================================================================

def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_risk(
    visitor_id: uuid.UUID | None = None,
    duration_s: float = 60.0,
    visits: int = 1,
    hour: int = 10,
    event_id: str = "e1",
    source_video: str = "cam01",
    computed_at: datetime | None = None,
) -> RiskFeature:
    """构造一个 RiskFeature 用于 Rule 测试。"""
    v_id = visitor_id or uuid.uuid4()
    t = computed_at or utc(2026, 7, 19, hour, 0, 0)
    return RiskFeature(
        visitor_id=v_id, event_id=event_id, source_video=source_video, computed_at=t,
        duration=DurationFeature(visitor_id=v_id, event_id=event_id, source_video=source_video, duration_seconds=duration_s, computed_at=t),
        frequency=VisitFrequencyFeature(visitor_id=v_id, event_id=event_id, source_video=source_video, visits_in_window=visits, window_seconds=1800.0, computed_at=t),
        time=TimeFeature.from_datetime(t, visitor_id=v_id, event_id=event_id, source_video=source_video, computed_at=t),
        trajectory=TrajectoryFeature(visitor_id=v_id, event_id=event_id, source_video=source_video, computed_at=t),
    )


# ============================================================================
# ThresholdConfig
# ============================================================================

class TestThresholdConfig:
    def test_defaults(self):
        cfg = ThresholdConfig()
        assert cfg.long_duration_seconds == 300.0
        assert cfg.repeat_visit_count == 3
        assert 23 in cfg.odd_hour_set
        assert 3 in cfg.odd_hour_set
        assert cfg.cooldown_seconds == 600.0
        assert cfg.high_risk_required_rules == {"LongDurationRule", "RepeatVisitRule", "OddHourRule"}

    def test_weight_for(self):
        cfg = ThresholdConfig()
        assert cfg.weight_for("LongDurationRule") == 0.50
        assert cfg.weight_for("UnknownRule") == 0.0

    def test_customization(self):
        cfg = ThresholdConfig(
            long_duration_seconds=120.0,
            odd_hour_set={0, 1, 2, 3, 4, 5},
        )
        assert cfg.long_duration_seconds == 120.0
        assert cfg.odd_hour_set == {0, 1, 2, 3, 4, 5}


# ============================================================================
# RuleResult 领域对象
# ============================================================================

class TestRuleResult:
    def test_matched_requires_event_type(self):
        with pytest.raises(ValueError, match="matched=True"):
            RuleResult(rule_name="X", matched=True, perception_score=0.5)

    def test_score_out_of_range(self):
        with pytest.raises(ValueError, match="perception_score"):
            RuleResult(rule_name="X", matched=True, event_type="abnormal_dwell", perception_score=1.5)

    def test_repeat_count_non_negative(self):
        with pytest.raises(ValueError, match="repeat_count"):
            RuleResult(rule_name="X", matched=True, event_type="repeat_visit", perception_score=0.3, repeat_count=-1)

    def test_to_dict(self):
        r = RuleResult(
            rule_name="LongDurationRule", matched=True, event_type="abnormal_dwell",
            perception_score=0.5, evidence={"duration_seconds": 600.0},
        )
        d = r.to_dict()
        assert d["matched"] is True
        assert d["perception_score"] == 0.5


# ============================================================================
# LongDurationRule
# ============================================================================

class TestLongDurationRule:
    def test_triggered_above_threshold(self):
        rule = LongDurationRule(weight=0.5)
        cfg = ThresholdConfig()
        ctx = RuleContext(thresholds=cfg)
        risk = make_risk(duration_s=400.0)
        results = rule.evaluate(ctx, risk)
        assert len(results) == 1
        assert results[0].matched is True
        assert results[0].event_type == "abnormal_dwell"
        assert results[0].perception_score == 0.5
        assert results[0].evidence["duration_seconds"] == 400.0

    def test_not_triggered_below_threshold(self):
        rule = LongDurationRule(weight=0.5)
        cfg = ThresholdConfig()
        ctx = RuleContext(thresholds=cfg)
        risk = make_risk(duration_s=100.0)
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is False

    def test_no_duration_feature(self):
        rule = LongDurationRule(weight=0.5)
        cfg = ThresholdConfig()
        ctx = RuleContext(thresholds=cfg)
        risk = make_risk()
        risk.duration = None
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is False
        assert "DurationFeature 缺失" in results[0].notes


# ============================================================================
# RepeatVisitRule
# ============================================================================

class TestRepeatVisitRule:
    def test_triggered_above_threshold(self):
        rule = RepeatVisitRule(weight=0.3)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk(visits=5)
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is True
        assert results[0].event_type == "repeat_visit"
        assert results[0].repeat_count == 5

    def test_not_triggered_below_threshold(self):
        rule = RepeatVisitRule(weight=0.3)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk(visits=1)
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is False


# ============================================================================
# OddHourRule
# ============================================================================

class TestOddHourRule:
    def test_triggered_in_odd_hour(self):
        rule = OddHourRule(weight=0.1)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk(hour=2)
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is True
        assert results[0].event_type == "visit_normal"  # §7.2 异常时段是 visit_normal + is_odd_hour
        assert results[0].is_odd_hour is True

    def test_not_triggered_in_normal_hour(self):
        rule = OddHourRule(weight=0.1)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk(hour=14)
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is False


# ============================================================================
# PendingVerifyRule（v2 接口）
# ============================================================================

class TestPendingVerifyRule:
    def test_raises_without_whitelist(self):
        rule = PendingVerifyRule(weight=0.3)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk()
        with pytest.raises(NotImplementedError, match="WhitelistProvider"):
            rule.evaluate(ctx, risk)

    def test_skipped_when_whitelisted(self):
        class StubWL:
            def is_whitelisted(self, vid):
                return True
        rule = PendingVerifyRule(weight=0.3)
        ctx = RuleContext(thresholds=ThresholdConfig(), extra={"whitelist": StubWL()})
        risk = make_risk()
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is False
        assert "白名单" in results[0].notes

    def test_triggered_when_not_whitelisted(self):
        class StubWL:
            def is_whitelisted(self, vid):
                return False
        rule = PendingVerifyRule(weight=0.3)
        ctx = RuleContext(thresholds=ThresholdConfig(), extra={"whitelist": StubWL()})
        risk = make_risk()
        results = rule.evaluate(ctx, risk)
        assert results[0].matched is True
        assert results[0].event_type == "visit_pending_verify"


# ============================================================================
# HighRiskApproachRule (Composite)
# ============================================================================

class TestHighRiskApproachRule:
    def test_triggered_when_all_required_match(self):
        rule = HighRiskApproachRule(weight=0.9)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk()
        prior = [
            RuleResult(rule_name="LongDurationRule", matched=True, event_type="abnormal_dwell", perception_score=0.5),
            RuleResult(rule_name="RepeatVisitRule", matched=True, event_type="repeat_visit", perception_score=0.3),
            RuleResult(rule_name="OddHourRule", matched=True, event_type="visit_normal", perception_score=0.1),
        ]
        results = rule.evaluate(ctx, risk, prior)
        assert len(results) == 1
        assert results[0].matched is True
        assert results[0].event_type == "high_risk_approach"
        # score = sum of sub-scores capped at 1.0（浮点比较用 approx，避免严格 == 抖动）
        assert results[0].perception_score == pytest.approx(0.9)

    def test_not_triggered_when_partial_match(self):
        rule = HighRiskApproachRule(weight=0.9)
        ctx = RuleContext(thresholds=ThresholdConfig())
        risk = make_risk()
        prior = [
            RuleResult(rule_name="LongDurationRule", matched=True, event_type="abnormal_dwell", perception_score=0.5),
            RuleResult(rule_name="RepeatVisitRule", matched=False, perception_score=0.0),
        ]
        results = rule.evaluate(ctx, risk, prior)
        assert results[0].matched is False


# ============================================================================
# CooldownGate 状态机
# ============================================================================

class TestCooldownGate:
    def test_first_trigger_allowed(self):
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        v_id = uuid.uuid4()
        now = utc(2026, 7, 19, 10, 0, 0)
        assert gate.try_trigger(v_id, "Rule1", now=now) is True
        assert gate.state(v_id, "Rule1") == CooldownState.ACTIVE

    def test_repeat_within_cooldown_suppressed(self):
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        v_id = uuid.uuid4()
        now = utc(2026, 7, 19, 10, 0, 0)
        gate.try_trigger(v_id, "Rule1", now=now)
        # 100s 后再次触发 → 仍在 cooldown
        assert gate.try_trigger(v_id, "Rule1", now=now + timedelta(seconds=100)) is False
        assert gate.state(v_id, "Rule1") == CooldownState.COOLDOWN

    def test_repeat_after_cooldown_allowed(self):
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        v_id = uuid.uuid4()
        now = utc(2026, 7, 19, 10, 0, 0)
        gate.try_trigger(v_id, "Rule1", now=now)
        # 700s 后再次触发 → cooldown 期已过，允许
        assert gate.try_trigger(v_id, "Rule1", now=now + timedelta(seconds=700)) is True
        assert gate.state(v_id, "Rule1") == CooldownState.ACTIVE

    def test_reset_after_long_gap(self):
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        v_id = uuid.uuid4()
        now = utc(2026, 7, 19, 10, 0, 0)
        gate.try_trigger(v_id, "Rule1", now=now)
        # 2000s 后再次触发 → 超过 reset_gap，状态机重置为 INACTIVE
        assert gate.try_trigger(v_id, "Rule1", now=now + timedelta(seconds=2000)) is True
        assert gate.state(v_id, "Rule1") == CooldownState.ACTIVE

    def test_independent_keys(self):
        """不同 (visitor_id, rule_name) 互不干扰。"""
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        v1, v2 = uuid.uuid4(), uuid.uuid4()
        now = utc(2026, 7, 19, 10, 0, 0)
        gate.try_trigger(v1, "Rule1", now=now)
        # v2 Rule1 → 不应受 v1 影响
        assert gate.try_trigger(v2, "Rule1", now=now) is True
        # v1 Rule2 → 不应受 v1 Rule1 影响
        assert gate.try_trigger(v1, "Rule2", now=now) is True

    def test_reset_clears_all(self):
        gate = CooldownGate()
        gate.try_trigger(uuid.uuid4(), "R")
        assert gate.size() == 1
        gate.reset()
        assert gate.size() == 0

    def test_validation(self):
        with pytest.raises(ValueError):
            CooldownGate(cooldown_seconds=0.0)
        with pytest.raises(ValueError):
            CooldownGate(reset_gap_seconds=0.0)


# ============================================================================
# PerceptionEvent 领域对象
# ============================================================================

class TestPerceptionEvent:
    def test_basic(self):
        e = PerceptionEvent(
            device_id="home01", event_type="abnormal_dwell", score=0.5,
            visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
            meta={"rule": "LongDurationRule"},
        )
        assert e.device_id == "home01"
        assert e.event_type == "abnormal_dwell"
        assert e.score == 0.5

    def test_invalid_event_type_rejected(self):
        """event_type 必须是 §7.2 5 类之一（守住枚举边界）。"""
        with pytest.raises(ValueError, match="event_type"):
            PerceptionEvent(
                device_id="home01", event_type="FRAUD", score=0.9,
                visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
                meta={"rule": "BadRule"},
            )

    def test_score_out_of_range(self):
        with pytest.raises(ValueError, match="score"):
            PerceptionEvent(
                device_id="home01", event_type="abnormal_dwell", score=1.5,
                visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
                meta={"rule": "LongDurationRule"},
            )

    def test_meta_rule_required(self):
        with pytest.raises(ValueError, match="meta"):
            PerceptionEvent(
                device_id="home01", event_type="abnormal_dwell", score=0.5,
                visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
                meta={"notes": "no rule"},
            )

    def test_to_dict_includes_all(self):
        e = PerceptionEvent(
            device_id="home01", event_type="repeat_visit", score=0.3,
            visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
            track_id=17, location="入户门", repeat_count=5, is_odd_hour=False,
            meta={"rule": "RepeatVisitRule"},
        )
        d = e.to_dict()
        assert d["event_type"] == "repeat_visit"
        assert d["track_id"] == 17
        assert d["location"] == "入户门"
        assert d["repeat_count"] == 5
        assert d["is_odd_hour"] is False
        assert d["meta"]["rule"] == "RepeatVisitRule"


# ============================================================================
# RuleEngine 编排器
# ============================================================================

class TestRuleEngine:
    def test_single_long_duration_risk(self):
        engine = RuleEngine(device_id="home01", location="入户门")
        risk = make_risk(duration_s=400.0)
        events = engine.evaluate(risk)
        # 1 个 LongDuration 命中 + 1 个 Composite（需要 3 条全中 → 不触发）
        matched_events = [e for e in events if e.event_type == "abnormal_dwell"]
        assert len(matched_events) == 1
        assert matched_events[0].score == 0.5
        assert matched_events[0].meta["rule"] == "LongDurationRule"

    def test_all_three_basic_rules_compose_high_risk(self):
        """长停留 + 重复 + 异常时段 → 4 个事件（含 Composite high_risk_approach）。"""
        engine = RuleEngine(device_id="home01", location="入户门")
        risk = make_risk(duration_s=600.0, visits=5, hour=2)
        events = engine.evaluate(risk)
        types = {e.event_type for e in events}
        assert "abnormal_dwell" in types
        assert "repeat_visit" in types
        assert "visit_normal" in types  # OddHourRule → visit_normal + is_odd_hour
        assert "high_risk_approach" in types  # Composite

    def test_is_odd_hour_marked(self):
        engine = RuleEngine(device_id="home01", location="入户门")
        risk = make_risk(hour=3)
        events = engine.evaluate(risk)
        # OddHourRule 单独触发
        odd_events = [e for e in events if e.is_odd_hour]
        assert len(odd_events) == 1
        assert odd_events[0].event_type == "visit_normal"

    def test_no_event_when_no_rule_matches(self):
        engine = RuleEngine(device_id="home01")
        risk = make_risk(duration_s=10.0, visits=1, hour=14)  # 都低于阈值
        events = engine.evaluate(risk)
        # 4 条 Rule 都不命中；Composite 也不命中 → 空列表
        assert events == []

    def test_cooldown_suppresses_repeat(self):
        """同 visitor_id + 同 rule 在 cooldown 内不重复触发。"""
        from home_perception.analysis.cooldown import CooldownGate
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        engine = RuleEngine(device_id="home01", cooldown=gate)
        v_id = uuid.uuid4()
        t0 = utc(2026, 7, 19, 10, 0, 0)
        risk1 = make_risk(visitor_id=v_id, duration_s=400.0, computed_at=t0)
        events1 = engine.evaluate(risk1)
        assert len(events1) >= 1
        # 100s 后再次 evaluate 同 visitor → cooldown 抑制 LongDuration 重复触发
        risk2 = make_risk(visitor_id=v_id, duration_s=400.0, computed_at=t0 + timedelta(seconds=100))
        events2 = engine.evaluate(risk2)
        # LongDurationRule 第二次触发应被 cooldown 抑制
        long_dur_in_2 = [e for e in events2 if e.event_type == "abnormal_dwell"]
        assert long_dur_in_2 == []

    def test_different_visitor_not_affected_by_cooldown(self):
        from home_perception.analysis.cooldown import CooldownGate
        gate = CooldownGate(cooldown_seconds=600.0, reset_gap_seconds=1800.0)
        engine = RuleEngine(device_id="home01", cooldown=gate)
        t0 = utc(2026, 7, 19, 10, 0, 0)
        engine.evaluate(make_risk(visitor_id=uuid.uuid4(), duration_s=400.0, computed_at=t0))
        # 不同 visitor 立即触发
        events = engine.evaluate(make_risk(visitor_id=uuid.uuid4(), duration_s=400.0, computed_at=t0))
        assert any(e.event_type == "abnormal_dwell" for e in events)

    def test_to_json(self):
        engine = RuleEngine(device_id="home01", location="入户门")
        risk = make_risk(duration_s=400.0)
        events = engine.evaluate(risk)
        assert len(events) >= 1
        j = events[0].to_json()
        parsed = json.loads(j)
        assert parsed["event_type"] == "abnormal_dwell"
        assert parsed["device_id"] == "home01"
        assert parsed["location"] == "入户门"
        assert parsed["meta"]["rule"] == "LongDurationRule"


# ============================================================================
# 契约边界：PerceptionEvent 严格不含"最终判定"字段（ADR-0009）
# ============================================================================

class TestPerceptionEventContractBoundary:
    """PerceptionEvent 严格不含最终判定字段（中心综合判断不是 Rule 层的责任）。"""

    FORBIDDEN = {
        "fraud_result", "crime_probability", "final_decision", "verdict",
        "is_scammer", "is_fraud", "judgment", "is_guilty",
        # 与 P0-6 / P0-7a 共享的禁止项
        "risk_level",
    }

    def test_no_final_judgment_fields(self):
        e = PerceptionEvent(
            device_id="home01", event_type="high_risk_approach", score=0.9,
            visitor_id=uuid.uuid4(), source_video="cam01", timestamp=1000.0,
            meta={"rule": "HighRiskApproachRule"},
        )
        d = e.to_dict()
        # 高分 PerceptionEvent 也不含最终判定字段
        leaked = self.FORBIDDEN & set(d.keys())
        assert not leaked, f"PerceptionEvent 含最终判定字段 {leaked}"
        # 也不在 meta 内（meta 是 metadata 不是 judgment）
        meta_leaked = self.FORBIDDEN & set(d.get("meta", {}).keys())
        assert not meta_leaked, f"meta 含最终判定字段 {meta_leaked}"


# ============================================================================
# CAVIAR 真实链路端到端（fixture 缺失优雅 skip）
# ============================================================================

CAVIAR_ONE_STOP_ENTER = "tests/fixtures/doorway/one_stop_enter"


def test_caviar_end_to_end_pipeline_yields_perception_events():
    """CAVIAR OneStopEnter1cor: detector → tracker → event → feature → rule 全链路。"""
    pytest.importorskip("ultralytics")
    import cv2
    from pathlib import Path
    from home_perception.detection.detector import YOLODetector
    from home_perception.detection.tracker import VisitorTracker
    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.analysis.feature_extractor import FeatureExtractor
    from home_perception.analysis.rule_engine import RuleEngine

    p = Path(CAVIAR_ONE_STOP_ENTER)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip("CAVIAR fixture 缺失")

    frames = []
    for f in sorted(p.glob("frame_*.jpg")):
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    if not frames:
        pytest.skip("CAVIAR frames 解析失败")

    det = YOLODetector(
        model="yolo11n.pt", conf_threshold=0.25,
        classes=[0], imgsz=416, device="cpu",
        enable_track=True, tracker="bytetrack",
    ).load()
    tracker = VisitorTracker(absence_gap_s=5.0)
    event_builder = VisitorEventBuilder(tracker, source_video="CAVIAR/OneStopEnter1cor")
    feat_ext = FeatureExtractor(frequency_window_s=1800.0)
    engine = RuleEngine(device_id="CAVIAR-Test", location="入户门")

    all_events = []
    for f in frames:
        r = det.detect(f)
        for event in event_builder.update(r.detections):
            risk = feat_ext.extract(event)
            all_events.extend(engine.evaluate(risk))

    # 5 类枚举里所有出现过的 event_type 必须命中
    seen_types = {e.event_type for e in all_events}
    for et in seen_types:
        assert et in EVENT_TYPES, f"event_type {et} 不在 §7.2 5 类枚举内"

    # 每条 PerceptionEvent 都有 rule meta
    for e in all_events:
        assert "rule" in e.meta
        assert 0.0 <= e.score <= 1.0
        assert e.device_id == "CAVIAR-Test"

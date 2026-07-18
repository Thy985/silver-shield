"""规则确定性测试：同输入同输出，且只在异常时段有人时触发。"""
import time

from home_perception.analysis.anomaly import CooldownGate
from home_perception.analysis.rules import OddHourRule, RuleContext
from home_perception.core.event import EventType


def _ctx(ts, detections):
    return RuleContext(device_id="d1", location="入户门", timestamp=ts, detections=detections)


def test_odd_hour_rule_triggers_at_night():
    # 2026-06-10 23:30 本地时区 -> 异常时段
    ts = time.mktime(time.strptime("2026-06-10 23:30", "%Y-%m-%d %H:%M"))
    ev = OddHourRule(start=23, end=6).evaluate(_ctx(ts, [object()]))
    assert ev is not None
    assert ev.event_type == EventType.VISIT_PENDING_VERIFY
    assert ev.is_odd_hour is True


def test_odd_hour_rule_no_trigger_daytime():
    # 2026-06-10 14:00 -> 正常时段且无 detections 时不应触发
    ts = time.mktime(time.strptime("2026-06-10 14:00", "%Y-%m-%d %H:%M"))
    assert OddHourRule().evaluate(_ctx(ts, [])) is None
    assert OddHourRule().evaluate(_ctx(ts, [object()])) is None


def test_cooldown_gate():
    g = CooldownGate(cooldown_s=60)
    assert g.allow("d1", 1000.0) is True
    assert g.allow("d1", 1020.0) is False  # 20s < 60s
    assert g.allow("d1", 1061.0) is True   # 61s >= 60s

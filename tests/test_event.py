"""事件模型契约测试：字段、序列化、枚举取值。"""
from home_perception.core.event import EventType, EvidenceRef, PerceptionEvent


def test_event_serializes_with_enum_value():
    ev = PerceptionEvent(
        device_id="home_entry_01",
        event_type=EventType.REPEAT_VISIT,
        score=0.72,
        timestamp=1718000000.123,
        track_id=17,
        repeat_count=4,
        is_odd_hour=True,
        evidence=[EvidenceRef(kind="snapshot", uri="data/evidence/x.jpg", timestamp=1718000000.0)],
    )
    d = ev.to_dict()
    assert d["event_type"] == "repeat_visit"  # 枚举值而非枚举名
    assert d["repeat_count"] == 4
    assert d["is_odd_hour"] is True
    assert d["evidence"][0]["kind"] == "snapshot"


def test_event_type_has_only_frontdoor_labels():
    # 禁止出现结论性标签（fraud/scammer）
    assert all(t not in {"fraud", "scammer"} for t in EventType.__members__.values())
    assert set(EventType) >= {
        EventType.VISIT_NORMAL,
        EventType.VISIT_PENDING_VERIFY,
        EventType.ABNORMAL_DWELL,
        EventType.REPEAT_VISIT,
        EventType.HIGH_RISK_APPROACH,
    }

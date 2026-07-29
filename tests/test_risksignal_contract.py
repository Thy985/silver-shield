"""RiskSignal 契约测试（ADR-0021 §3.3 / 工程落地方案 §8.2，Migration Stage A）。

只测"系统承诺的 RiskSignal 数据契约"。本测试 **torch-free**，可进 CI 每 PR 合约子集。

覆盖（对齐方案 §8.2）：
- 字段闭合：to_dict 键集合恒定 == RISKSIGNAL_DICT_KEYS，且不含黑名单判定字段
- 配对字段定位：paired_signal_id 是**顶级**字段；RAISED 为 None、CLEARED 回填 RAISED 的 signal_id
- created_at 为 datetime（非 float 戳）
- 主体泛化：subject_type 枚举闭合（4 值）；Phase 1 恒 VISITOR 且 subject_id==visitor_instance_id
- 枚举闭合：SignalCategory(5) × SourceModality(3) × SignalTransition(2) × SubjectType(4)
- 与 ADR-0022 EvidenceModality **无交叉 import**
- RAISED 必须能 CLEARED（数据级配对契约）
- CLEARED 不产生 Warning（结构上 RiskSignal 无决策/警告字段，非 WarningEvent）
- 重复 RAISED 不刷屏（每实例默认唯一 signal_id；去重交由评估器，不在类型层）
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

import pytest

from home_perception.analysis.risk_signal import (
    RISKSIGNAL_DICT_KEYS,
    FORBIDDEN_RISKSIGNAL_FIELDS,
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)


def _make_signal(
    transition: SignalTransition = SignalTransition.RAISED,
    *,
    subject_type: SubjectType = SubjectType.VISITOR,
    subject_id: str = "vid-1",
    category: SignalCategory = SignalCategory.BEHAVIORAL,
    source: SourceModality = SourceModality.VISION,
    paired_signal_id: str | None = None,
    signal_id: str | None = None,
    features: Dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> RiskSignal:
    return RiskSignal(
        signal_id=signal_id or str(uuid4()),
        subject_type=subject_type,
        subject_id=subject_id,
        category=category,
        source=source,
        transition=transition,
        features=features if features is not None else {"dwell_seconds": 350},
        paired_signal_id=paired_signal_id,
        track_id=7,
        visitor_instance_id=subject_id if subject_type is SubjectType.VISITOR else None,
        created_at=created_at or datetime(2026, 7, 26, 10, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 字段闭合
# ---------------------------------------------------------------------------

def test_dict_keys_closed_and_whitelist():
    """to_dict 键集合 == RISKSIGNAL_DICT_KEYS，不多不少（字段闭合）。"""
    sig = _make_signal()
    keys = set(sig.to_dict().keys())
    assert keys == set(RISKSIGNAL_DICT_KEYS)


def test_dict_contains_no_forbidden_fields():
    """结构化保证：to_dict 与 features 内均不含黑名单判定字段。"""
    bad_features = {"dwell_seconds": 1, "is_fraud": True}
    with pytest.raises(ValueError):
        _make_signal(features=bad_features)
    # 顶层键也无黑名单字段
    keys = set(_make_signal().to_dict().keys())
    assert not (FORBIDDEN_RISKSIGNAL_FIELDS & keys)


# ---------------------------------------------------------------------------
# 配对字段定位
# ---------------------------------------------------------------------------

def test_paired_signal_id_is_top_level_not_in_features():
    """paired_signal_id 是顶级字段，不藏在 features 内。"""
    raised = _make_signal(transition=SignalTransition.RAISED)
    assert "paired_signal_id" in raised.to_dict()
    assert "paired_signal_id" not in raised.features


def test_raised_has_no_paired_id():
    """RAISED 必须 paired_signal_id is None（契约不变式由 __post_init__ 强制）。"""
    raised = _make_signal(transition=SignalTransition.RAISED)
    assert raised.paired_signal_id is None
    # 试图给 RAISED 配对应抛 ValueError
    with pytest.raises(ValueError):
        _make_signal(transition=SignalTransition.RAISED, paired_signal_id="x")


def test_cleared_carries_raised_signal_id():
    """CLEARED 回填对应 RAISED 的 signal_id（数据级配对契约）。"""
    raised = _make_signal(transition=SignalTransition.RAISED)
    cleared = _make_signal(
        transition=SignalTransition.CLEARED,
        paired_signal_id=raised.signal_id,
        subject_id=raised.subject_id,
    )
    assert cleared.paired_signal_id == raised.signal_id
    # 配对关系在 to_dict 中也正确传递
    assert cleared.to_dict()["paired_signal_id"] == raised.signal_id


# ---------------------------------------------------------------------------
# created_at 类型
# ---------------------------------------------------------------------------

def test_created_at_is_datetime_not_float():
    """created_at 必须是 datetime（UTC-aware），严禁 float unix 戳。"""
    sig = _make_signal()
    assert isinstance(sig.created_at, datetime)
    assert sig.created_at.tzinfo is not None
    # to_dict 转 ISO 字符串
    assert isinstance(sig.to_dict()["created_at"], str)


def test_created_at_rejects_naive():
    """naive datetime 必须拒绝（防跨设备时间漂移）。"""
    with pytest.raises(ValueError):
        RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id="vid",
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.RAISED,
            features={},
            created_at=datetime(2026, 7, 26, 10, 0, 0),  # naive
        )


# ---------------------------------------------------------------------------
# 主体泛化
# ---------------------------------------------------------------------------

def test_subject_type_enum_closed():
    """subject_type 枚举闭合为 4 值（VISITOR/PERSON/DEVICE/ENVIRONMENT）。"""
    assert {e.value for e in SubjectType} == {
        "visitor",
        "person",
        "device",
        "environment",
    }


def test_phase1_visitor_invariant():
    """Phase 1 恒 subject_type=VISITOR 且 subject_id==visitor_instance_id。"""
    sig = _make_signal(subject_type=SubjectType.VISITOR, subject_id="vid-42")
    assert sig.subject_type is SubjectType.VISITOR
    assert sig.subject_id == sig.visitor_instance_id == "vid-42"


def test_subject_generalization_non_visitor():
    """前瞻：非 VISITOR 主体（如 DEVICE）合法，且不要求 visitor_instance_id。"""
    sig = _make_signal(
        subject_type=SubjectType.DEVICE,
        subject_id="phone-1",
        category=SignalCategory.COMMUNICATION,
        source=SourceModality.SENSOR,
    )
    assert sig.subject_type is SubjectType.DEVICE
    assert sig.visitor_instance_id is None


# ---------------------------------------------------------------------------
# 枚举闭合矩阵（5 × 3 × 2 × 4 = 120 组合）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "category", [e for e in SignalCategory]
)
@pytest.mark.parametrize(
    "source", [e for e in SourceModality]
)
@pytest.mark.parametrize(
    "transition", [e for e in SignalTransition]
)
@pytest.mark.parametrize(
    "subject_type", [e for e in SubjectType]
)
def test_enum_closure_matrix(
    category: SignalCategory,
    source: SourceModality,
    transition: SignalTransition,
    subject_type: SubjectType,
) -> None:
    """任意枚举组合都能构造，且 to_dict 正确回写对应 value。"""
    sig = _make_signal(
        transition=transition,
        subject_type=subject_type,
        subject_id="vid-x",
        category=category,
        source=source,
    )
    d = sig.to_dict()
    assert d["category"] == category.value
    assert d["source"] == source.value
    assert d["transition"] == transition.value
    assert d["subject_type"] == subject_type.value


def test_enum_value_counts():
    """枚举值数量闭合（5 / 3 / 2 / 4）。"""
    assert len(list(SignalCategory)) == 5
    assert len(list(SourceModality)) == 3
    assert len(list(SignalTransition)) == 2
    assert len(list(SubjectType)) == 4


# ---------------------------------------------------------------------------
# 与 EvidenceModality 无交叉 import（ADR-0021 §3.3 命名消歧）
# ---------------------------------------------------------------------------

def test_no_evidence_modality_cross_import():
    """risk_signal 模块不得 import / 复用 ADR-0022 的 EvidenceModality（独立限界上下文）。

    注意：docstring 可以用文字提及该名称以说明边界，但**代码不得 import 它、不得把它当符号使用**。
    """
    import re

    import home_perception.analysis.risk_signal as rs_mod

    # 1) 模块命名空间不含 EvidenceModality 符号
    assert not hasattr(rs_mod, "EvidenceModality")
    source = inspect.getsource(rs_mod)
    # 2) 不得有任何 import 语句引用它
    assert "import EvidenceModality" not in source.replace(" ", "")
    assert "from home_perception.core.event import" not in source.replace(" ", "")
    # 3) 不得把它当真实符号使用（点取属性 / 调用 / 赋值 / 类型注解）
    assert not re.search(r"\bEvidenceModality\s*[\.\(=]", source)
    assert not re.search(r":\s*EvidenceModality\b", source)


# ---------------------------------------------------------------------------
# RAISED 必须能 CLEARED（数据级配对契约）
# ---------------------------------------------------------------------------

def test_raised_cleared_pairing_roundtrip():
    """注入 触发→回落 序列，断言 CLEARED.paired_signal_id == RAISED.signal_id。"""
    raised = _make_signal(
        transition=SignalTransition.RAISED,
        signal_id="11111111-1111-1111-1111-111111111111",
    )
    cleared = _make_signal(
        transition=SignalTransition.CLEARED,
        signal_id="22222222-2222-2222-2222-222222222222",
        paired_signal_id=raised.signal_id,
        subject_id=raised.subject_id,
    )
    assert cleared.paired_signal_id == "11111111-1111-1111-1111-111111111111"
    # 两者 transition 不同、signal_id 不同（两条独立消息）
    assert cleared.signal_id != raised.signal_id
    assert cleared.transition is not raised.transition


# ---------------------------------------------------------------------------
# CLEARED 不产生 Warning（结构上 RiskSignal 非 WarningEvent）
# ---------------------------------------------------------------------------

def test_risksignal_is_not_warning_event():
    """RiskSignal 不含决策/警告字段，结构上不会直接变成 WarningEvent。"""
    sig = _make_signal()
    d = sig.to_dict()
    # 决策层字段（WarningEvent 契约）不得出现在 RiskSignal
    for forbidden in ("risk_level", "recommended_action", "warning_id", "status"):
        assert forbidden not in d
    # 类型身份明确分离
    from home_perception.analysis.warning import WarningEvent

    assert not isinstance(sig, WarningEvent)


# ---------------------------------------------------------------------------
# 重复 RAISED 不刷屏（类型层支持唯一 id；去重归评估器）
# ---------------------------------------------------------------------------

def test_repeated_raised_gets_unique_signal_ids():
    """每个默认构造的 RAISED 拥有唯一 signal_id —— 去重必须由评估器基于状态机完成，
    而非靠类型层碰撞 id（否则会静默丢失不同主体的信号）。"""
    a = _make_signal(transition=SignalTransition.RAISED)
    b = _make_signal(transition=SignalTransition.RAISED)
    assert a.signal_id != b.signal_id


# ---------------------------------------------------------------------------
# signal_id UUID 格式校验（发现 2）
# ---------------------------------------------------------------------------

def test_signal_id_rejects_non_uuid_string():
    """signal_id 字符串必须是合法 UUID 格式；"not-a-uuid" 应抛 ValueError。"""
    with pytest.raises(ValueError):
        _make_signal(signal_id="not-a-uuid")


def test_signal_id_accepts_valid_uuid_string():
    """合法 UUID 字符串通过校验。"""
    valid = "12345678-1234-1234-1234-123456789abc"
    sig = _make_signal(signal_id=valid)
    assert sig.signal_id == valid


def test_signal_id_accepts_uuid_object():
    """UUID 实例自动转 str。"""
    from uuid import uuid4
    u = uuid4()
    sig = _make_signal(signal_id=u)
    assert sig.signal_id == str(u)


# ---------------------------------------------------------------------------
# features 类型断言（发现 3）
# ---------------------------------------------------------------------------

def test_features_rejects_none():
    """features=None 应抛 TypeError（不再静默跳过黑名单检查）。"""
    with pytest.raises(TypeError):
        RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id="vid",
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.RAISED,
            features=None,  # 非 dict
        )


def test_features_rejects_list():
    """features=list 应抛 TypeError。"""
    with pytest.raises(TypeError):
        RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id="vid",
            category=SignalCategory.BEHAVIORAL,
            source=SourceModality.VISION,
            transition=SignalTransition.RAISED,
            features=[("dwell_seconds", 1)],
        )


# ---------------------------------------------------------------------------
# _coerce_enum TypeError 分支（发现 8）
# ---------------------------------------------------------------------------

def test_coerce_enum_rejects_int():
    """_coerce_enum 传入 int（既非枚举也非 str）应抛 TypeError。"""
    from home_perception.analysis.risk_signal import _coerce_enum
    with pytest.raises(TypeError):
        _coerce_enum(SignalCategory, 42, "category")


def test_coerce_enum_rejects_none():
    """_coerce_enum 传入 None 应抛 TypeError。"""
    from home_perception.analysis.risk_signal import _coerce_enum
    with pytest.raises(TypeError):
        _coerce_enum(SourceModality, None, "source")


def test_coerce_enum_rejects_list():
    """_coerce_enum 传入 list 应抛 TypeError。"""
    from home_perception.analysis.risk_signal import _coerce_enum
    with pytest.raises(TypeError):
        _coerce_enum(SubjectType, ["visitor"], "subject_type")


# ---------------------------------------------------------------------------
# from_dict / from_json 反序列化（发现 7）
# ---------------------------------------------------------------------------

def test_from_dict_roundtrip():
    """to_dict → from_dict → to_dict 应产出相同字典（round-trip 对称）。"""
    original = _make_signal(
        transition=SignalTransition.RAISED,
        signal_id="12345678-1234-1234-1234-123456789abc",
    )
    d1 = original.to_dict()
    restored = RiskSignal.from_dict(d1)
    d2 = restored.to_dict()
    assert d1 == d2


def test_from_json_roundtrip():
    """to_json → from_json → to_json 应产出相同 JSON 字符串。"""
    original = _make_signal(
        transition=SignalTransition.CLEARED,
        signal_id="12345678-1234-1234-1234-123456789abc",
        paired_signal_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    j1 = original.to_json()
    restored = RiskSignal.from_json(j1)
    j2 = restored.to_json()
    assert j1 == j2


def test_from_dict_preserves_paired_signal_id():
    """from_dict 保留 paired_signal_id（CLEARED 配对关系不丢失）。"""
    raised = _make_signal(transition=SignalTransition.RAISED,
                          signal_id="11111111-1111-1111-1111-111111111111")
    cleared = _make_signal(
        transition=SignalTransition.CLEARED,
        signal_id="22222222-2222-2222-2222-222222222222",
        paired_signal_id=raised.signal_id,
    )
    restored = RiskSignal.from_dict(cleared.to_dict())
    assert restored.paired_signal_id == raised.signal_id
    assert restored.transition is SignalTransition.CLEARED


# ---------------------------------------------------------------------------
# 不污染 EventType（Stage A 边界守卫）
# ---------------------------------------------------------------------------

def test_event_types_unchanged_five_categories():
    """Stage A 引入 RiskSignal 不得污染 §7.2 EventType 5 类枚举。

    ADR-0021 §3.3：RiskSignal 是内部实时旁路产物，不进 MQTT 上报；
    EventType 仍是历史 5 类（visit_normal / visit_pending_verify /
    abnormal_dwell / repeat_visit / high_risk_approach），不新增"实时"类。
    """
    from home_perception.analysis.perception import EVENT_TYPES
    assert set(EVENT_TYPES) == {
        "visit_normal",
        "visit_pending_verify",
        "abnormal_dwell",
        "repeat_visit",
        "high_risk_approach",
    }
    assert len(EVENT_TYPES) == 5


def test_risksignal_has_no_schema_version():
    """RiskSignal 无 schema_version 字段（设计上不版本化）。

    原因：RiskSignal 是内部实时旁路产物，不进 MQTT 上报（不跨设备 / 不跨服务），
    无需 schema_version 做兼容协商。如未来需上报中心，再引入 schema_version
    并走 ADR-0005 schema 评审。
    """
    sig = _make_signal()
    d = sig.to_dict()
    assert "schema_version" not in d
    assert not hasattr(sig, "schema_version")


# ---------------------------------------------------------------------------
# JSON 可序列化（Stage A §8.2：to_dict / to_json 均可序列化）
# ---------------------------------------------------------------------------

def test_to_json_serializable():
    """to_json 产出合法 JSON 字符串（含中文 / UUID / datetime ISO）。"""
    import json
    sig = _make_signal(
        features={"dwell_seconds": 350.5, "is_odd_hour": True, "中文键": "值"},
    )
    j = sig.to_json()
    # 合法 JSON 可反序列化
    parsed = json.loads(j)
    assert parsed["signal_id"] == sig.signal_id
    assert parsed["features"]["中文键"] == "值"
    assert parsed["transition"] == "raised"

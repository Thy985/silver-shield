"""GroundTruthRecord.from_dict 边界校验（PR#112 评审 issue 3）。

核心：``acceptable_hint`` 缺失 / 空 / 非法值必须显式暴露为错误，而非被
``acceptable_upper`` 静默解释为 severity 0、进而把数据治理遗漏误判为被评方案的
FP / Hard Gate 失败。同时支持显式未标注声明（``__NA__``）将 FP 指标剔除。
"""

from __future__ import annotations

import pytest

from home_perception.memory.evaluation.ground_truth import (
    ACCEPTABLE_HINT_NA,
    GroundTruthRecord,
    e1a_case_ids,
    get_ground_truth,
)

_BASE: dict = {
    "case_id": "case_x",
    "category": "repeat_visitor",
    "expected_pattern": ["repeated_visit"],
    "required_evidence": ["visitor_profile.visit_count = 5"],
}


def test_from_dict_requires_acceptable_hint():
    """缺少字段 → 抛 ValueError（数据治理遗漏必须暴露）。"""
    with pytest.raises(ValueError):
        GroundTruthRecord.from_dict({**_BASE})


def test_from_dict_rejects_empty_acceptable_hint():
    with pytest.raises(ValueError):
        GroundTruthRecord.from_dict({**_BASE, "acceptable_hint": []})


def test_from_dict_rejects_illegal_hint():
    with pytest.raises(ValueError):
        GroundTruthRecord.from_dict({**_BASE, "acceptable_hint": ["FOO"]})


def test_from_dict_accepts_explicit_na_string():
    rec = GroundTruthRecord.from_dict({**_BASE, "acceptable_hint": ACCEPTABLE_HINT_NA})
    assert rec.acceptable_hint == (ACCEPTABLE_HINT_NA,)


def test_from_dict_accepts_explicit_na_list():
    rec = GroundTruthRecord.from_dict({**_BASE, "acceptable_hint": [ACCEPTABLE_HINT_NA]})
    assert rec.acceptable_hint == (ACCEPTABLE_HINT_NA,)


def test_from_dict_valid_hints_ok():
    rec = GroundTruthRecord.from_dict(
        {**_BASE, "acceptable_hint": ["MONITOR", "NOTIFY_FAMILY"]}
    )
    assert rec.acceptable_hint == ("MONITOR", "NOTIFY_FAMILY")


def test_e1a_registry_records_validate():
    """内置注册表也满足契约（含 acceptable_hint 校验），get_ground_truth 不应抛。"""
    for cid in e1a_case_ids():
        get_ground_truth(cid).validate()

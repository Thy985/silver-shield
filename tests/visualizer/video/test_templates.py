"""ADR-0035 D3 · templates._category_of 单测（评审缺口 #9）。

场景类别推导是固定映射（非按证据值分支叙事），本测试锁定分类规则：
- scenario_id 含 elderly / 事件类型含 elderly|dwell|fall → elderly_warning
- 其余 → generic
"""

from __future__ import annotations

from home_perception.visualizer.video.narrative.templates import _category_of


def _ev(scenario_id: str = "sw_x", event_types: tuple[str, ...] = ()) -> dict:
    return {"scenario_id": scenario_id, "event_types": list(event_types)}


def test_category_elderly_by_scenario_id():
    assert _category_of(_ev("sw_adr0034_elderly_dwell")) == "elderly_warning"


def test_category_elderly_by_event_dwell():
    assert _category_of(_ev("sw_x", ("abnormal_dwell",))) == "elderly_warning"


def test_category_elderly_by_event_fall():
    assert _category_of(_ev("sw_x", ("elderly_fall",))) == "elderly_warning"


def test_category_elderly_by_event_fall_keyword():
    assert _category_of(_ev("sw_x", ("some_fall_event",))) == "elderly_warning"


def test_category_generic_default():
    assert _category_of(_ev("sw_generic_case", ("visit_normal",))) == "generic"


def test_category_generic_empty():
    assert _category_of(_ev()) == "generic"


def test_category_case_insensitive():
    assert _category_of(_ev("SW_X_ELDERLY_Y")) == "elderly_warning"
    assert _category_of(_ev("sw_x", ("Abnormal_Dwell",))) == "elderly_warning"

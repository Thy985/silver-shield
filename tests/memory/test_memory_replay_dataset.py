"""Memory Consumer 回放数据集测试（M0 · 数据闭环，DESIGN-memory-replay-dataset.md）。

> 核心价值：证明 Consumer 消费的**真实 Memory 数据**把"孤立事件"变成了"理解"
> （画像 / 模式 / 冲突）。同时验证 ADR-0025 不变量 C1（无分数）/ C2（只读）/
> C3（确定性）/ C4（冲突透明）/ C5（可溯源）。

固定 ID 约定：所有 fixture 的 `record_id` / `source_event_ids` 均为显式固定值，
保证回放结果跨运行可复现（类比 ADR-0024 §6.7.3）。
"""

from __future__ import annotations

import os

import pytest

from home_perception.memory.consumer import (
    EpisodeReplayLayer,
    MemoryReplayDataset,
)
from home_perception.memory.consumer.contracts import ReasoningInput
from home_perception.memory.records import records_equal

FIXTURE_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "memory_replay"
)


@pytest.fixture(scope="module")
def dataset() -> MemoryReplayDataset:
    return MemoryReplayDataset(FIXTURE_ROOT)


def _load_layer(dataset: MemoryReplayDataset, case_name: str) -> EpisodeReplayLayer:
    return EpisodeReplayLayer(dataset.load(case_name))


# ---------------------------------------------------------------------------
# 1. Fixture 有效性与 C5 可溯源
# ---------------------------------------------------------------------------
def test_fixtures_exist_and_load(dataset):
    names = dataset.case_names()
    assert names == [
        "case_001_repeat_visitor",
        "case_002_behavior_escalation",
        "case_003_conflict_transparency",
    ], f"case 集合意外: {names}"


def test_history_episodes_schema_valid_and_traceable(dataset):
    """每个 history EpisodicRecord 必须 schema 合法 + 可溯源（C5）。"""
    for case in dataset.load_all():
        assert case.history, f"{case.name}: history 为空"
        for ep in case.history:
            # round-trip 深度相等（忽略 created_at 墙钟）
            rt = ep.from_dict(ep.to_dict())
            assert records_equal(ep, rt), f"{case.name}/{ep.record_id}: schema round-trip 失败"
            # C5：每条记忆必须可追溯到源事件
            assert ep.source_event_ids, f"{case.name}/{ep.record_id}: source_event_ids 为空"
            # ADR-0023：v1 person_identity_id 恒 None
            assert ep.person_identity_id is None


# ---------------------------------------------------------------------------
# 2. 真实检索（CCTV→Memory 链路可读）
# ---------------------------------------------------------------------------
def test_retrieval_returns_history_episodes(dataset):
    """EpisodeReplayLayer 复用 MemoryQuery 检索，返回 history 全部 record_id。"""
    layer = _load_layer(dataset, "case_001_repeat_visitor")
    lo, hi = layer.default_window()
    ctx = layer.retrieve(lo, hi)
    hist_ids = {ep.record_id for ep in dataset.load("case_001_repeat_visitor").history}
    assert set(ctx["source_record_ids"]) == hist_ids
    assert ctx["current_status"].value == "CLEARED"  # 历史回放语义：均已离场


# ---------------------------------------------------------------------------
# 3. 语义信号：Memory 真改变了理解
# ---------------------------------------------------------------------------
def test_case_001_repeat_visitor_profile(dataset):
    """孤立事件 → 关联画像（重复夜间访客）。"""
    layer = _load_layer(dataset, "case_001_repeat_visitor")
    ri = layer.build_reasoning_input()
    assert ri.visitor_profile is not None
    assert ri.visitor_profile.visit_count == 5
    assert ri.visitor_profile.night_visit_ratio == 1.0
    assert ri.visitor_profile.confidence == "weak_pattern"
    assert ri.visitor_profile.identity_confirmed is False  # ADR-0023
    assert "repeated_visit" in ri.risk_pattern.tags
    assert ri.conflicts == ()  # 无历史/当前冲突


def test_case_002_behavior_escalation_pattern(dataset):
    """单看当前得不到的行为升级模式。"""
    layer = _load_layer(dataset, "case_002_behavior_escalation")
    ri = layer.build_reasoning_input()
    assert ri.risk_pattern is not None
    tags = set(ri.risk_pattern.tags)
    assert "repeated_visit" in tags
    assert "escalating_behavior" in tags
    assert ri.risk_pattern.escalation_history == ("observe", "dwell", "carry+observe_camera")
    # 反模式：绝对不能有分数（C1）
    assert "risk_score" not in ri.risk_pattern.tags
    assert ri.conflicts == ()


def test_case_003_conflict_transparency(dataset):
    """C4 正向：历史正常 vs 当前异常 → 冲突透明（不解决、不覆盖）。"""
    layer = _load_layer(dataset, "case_003_conflict_transparency")
    ri = layer.build_reasoning_input()
    assert ri.conflicts, "case_003 必须产出冲突"
    cf = ri.conflicts[0]
    assert cf.type == "behavior_shift"
    assert cf.historical == "normal"
    assert cf.current == "abnormal"
    assert cf.detail  # 非空细节，新旧并存
    # 冲突不修改历史：historical_context 仍是原始正常 episodes
    assert all("daytime_visit" in (r.reason_summary or []) for r in ri.historical_context)


def test_no_false_positive_conflicts(dataset):
    """C4 反向：无冲突历史 → conflicts == []（防 false positive）。"""
    for name in ("case_001_repeat_visitor", "case_002_behavior_escalation"):
        layer = _load_layer(dataset, name)
        assert layer.build_reasoning_input().conflicts == (), f"{name} 不应有冲突"


# ---------------------------------------------------------------------------
# 4. 不变量 C1 / C2 / C3
# ---------------------------------------------------------------------------
def test_c1_no_score_fields(dataset):
    """C1：ReasoningInput 不得含 risk_score / decision / warning。"""
    # 与 consumer 测试共享同一白名单常量（口径唯一，防漂移）
    from .consumer._c1 import REASONING_INPUT_FIELD_WHITELIST

    assert set(ReasoningInput.__dataclass_fields__.keys()) == REASONING_INPUT_FIELD_WHITELIST
    layer = _load_layer(dataset, "case_001_repeat_visitor")
    ri = layer.build_reasoning_input()
    dumped = ri.to_dict()
    assert "risk_score" not in dumped
    assert "decision" not in dumped
    assert "warning" not in dumped
    # 隐私：device_id 不得出现在 ReasoningInput 任何字段（仅参与检索排序）
    assert "device_id" not in dumped


def test_c2_read_only(dataset):
    """C2：组装过程不写 Memory（episode 条数不变）。"""
    layer = _load_layer(dataset, "case_001_repeat_visitor")
    before = layer.episode_count()
    layer.build_reasoning_input()
    layer.build_reasoning_input()
    after = layer.episode_count()
    assert before == after, "Consumer 组装不应改变 Memory 条数（违反 C2 只读）"


def test_c3_deterministic(dataset):
    """C3：同 case 两次构建产出字段级相等。"""
    layer_a = _load_layer(dataset, "case_002_behavior_escalation")
    layer_b = _load_layer(dataset, "case_002_behavior_escalation")
    ri_a = layer_a.build_reasoning_input()
    ri_b = layer_b.build_reasoning_input()
    assert ri_a == ri_b


# ---------------------------------------------------------------------------
# 5. 与 expected_reasoning_input.json 对齐（≈ oracle，C3 风格）
# ---------------------------------------------------------------------------
def _ri_matches_expected(actual: ReasoningInput, expected: ReasoningInput) -> None:
    """比对 assembl器产出与 fixture oracle（忽略 ConflictFlag.detail 易碎字符串）。"""
    assert actual.current_event == expected.current_event
    # historical_context：record_id 集合 + 逐条 records_equal
    assert [e.record_id for e in actual.historical_context] == [
        e.record_id for e in expected.historical_context
    ]
    for a, b in zip(actual.historical_context, expected.historical_context):
        assert records_equal(a, b)
    assert actual.visitor_profile == expected.visitor_profile
    # risk_pattern
    assert actual.risk_pattern is not None and expected.risk_pattern is not None
    assert set(actual.risk_pattern.tags) == set(expected.risk_pattern.tags)
    assert actual.risk_pattern.escalation_history == expected.risk_pattern.escalation_history
    assert actual.risk_pattern.confidence == expected.risk_pattern.confidence
    assert actual.evidence_refs == expected.evidence_refs
    assert actual.previous_actions == expected.previous_actions
    # conflicts：比较 (type, historical, current)，忽略 detail
    assert len(actual.conflicts) == len(expected.conflicts)
    for a, b in zip(actual.conflicts, expected.conflicts):
        assert (a.type, a.historical, a.current) == (b.type, b.historical, b.current)
        assert a.detail  # detail 非空（不要求逐字相等）


@pytest.mark.parametrize("case_name", [
    "case_001_repeat_visitor",
    "case_002_behavior_escalation",
    "case_003_conflict_transparency",
])
def test_aligned_with_expected_oracle(dataset, case_name):
    """组装产出与 expected_reasoning_input.json 对齐（同输入同输出）。"""
    layer = _load_layer(dataset, case_name)
    actual = layer.build_reasoning_input()
    expected = dataset.load(case_name).expected
    _ri_matches_expected(actual, expected)

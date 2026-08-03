"""区域⑥ Memory Context 视图模型测试（ADR-0025 C-4/C-6 · 只读 Shadow）。

不依赖任何 Demo 运行实例 / 网络 / 文件：直接构造 ReasoningInput / ReasoningResult
（与 pipeline.process_frame 经 Memory Consumer 产出的形状一致），验证 bridge 的
``build_memory_profiles`` 纯派生 + ``frame_result_to_view`` 注入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from home_perception.memory.consumer.contracts import (
    CurrentEvent,
    ReasoningInput,
    ReasoningResult,
    RiskPattern,
    SourceRef,
    VisitorProfile,
)
from home_perception.memory.records import EpisodicRecord, MemoryStatus
from silver_demo.bridge import build_memory_profiles, frame_result_to_view


def _utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _episode(
    enter_h: int,
    leave_h: int,
    dur_min: float,
    source_id: str,
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> EpisodicRecord:
    return EpisodicRecord(
        record_id=f"ep-{source_id}",
        visitor_instance_id="visitor_023",
        enter_time=_utc(2026, 8, 1, enter_h),
        leave_time=_utc(2026, 8, 1, leave_h),
        duration_seconds=dur_min * 60.0,
        source_event_ids=[source_id],
        summary="test episode",
        model_version="ep-builder-v1",
        memory_status=status,
    )


def _make_input_and_result() -> tuple[ReasoningInput, ReasoningResult]:
    ri = ReasoningInput(
        current_event=CurrentEvent(
            event_id="evt_now",
            event_type="visitor_event",
            visitor_instance_id="visitor_023",
            # 02:15 到访 → 偏离典型 18-20 时段
            occurred_at=_utc(2026, 8, 2, 2, 15),
            risk_level=None,
            markers=("night",),
        ),
        historical_context=(
            _episode(18, 20, 12.0, "evt_20260801_023"),
            _episode(20, 21, 15.0, "evt_20260802_017"),
        ),
        visitor_profile=VisitorProfile(
            visitor_instance_id="visitor_023",
            visit_count=2,
            night_visit_ratio=0.5,
            confidence="weak_pattern",
            identity_confirmed=False,
        ),
        risk_pattern=RiskPattern(
            tags=("repeat_visit", "night_visit_pattern"),
            confidence="weak_pattern",
        ),
    )
    rr = ReasoningResult(
        findings=(
            "Known visitor with abnormal timing shift",
            "behavior_shift: 历史典型 18-20 时段，当前 02:15 到访",
        ),
        explanation="已知访客，2 次历史到访；当前到访时间显著偏离历史基线。",
        suggested_action_hint="NOTIFY_FAMILY",
        source_refs=(SourceRef(source="historical_context", ref="rec_20260802_017"),),
    )
    return ri, rr


# ---------------------------------------------------------------------------
# build_memory_profiles：单条画像派生
# ---------------------------------------------------------------------------
def test_build_memory_profiles_single():
    ri, rr = _make_input_and_result()
    profiles = build_memory_profiles([ri], [rr])

    assert len(profiles) == 1
    p = profiles[0]
    # 访客标识来自 current_event
    assert p["visitor_instance_id"] == "visitor_023"
    # 历史上下文条数
    assert p["n_episodes"] == 2
    # 两条中有一条 ACTIVE → 总体 active
    assert p["memory_status"] == "active"
    # 已知模式：risk_pattern.tags + 自动补 repeat_visitor（n_episodes>0）
    assert "repeat_visit" in p["known_patterns"]
    assert "night_visit_pattern" in p["known_patterns"]
    assert "repeat_visitor" in p["known_patterns"]
    # 行为基线：enter 小时 [18,20]，平均时长 (12+15)/2*60 = 810s
    assert p["baseline"] == {"enter_hours": [18, 20], "avg_duration_s": 810.0}
    # 当前到访小时
    assert p["current"] == {"hour": 2}
    # 偏差：02:00 偏离 18:00–20:00
    assert any("偏离典型时段" in d for d in p["deviation"])
    # 证据：source_refs(historical_context→rec_...) + 历史 source_event_ids 去重
    assert "rec_20260802_017" in p["evidence"]
    assert "evt_20260801_023" in p["evidence"]
    assert "evt_20260802_017" in p["evidence"]
    # 记忆增量叙事（ReasoningResult）
    assert p["with_memory"]["explanation"].startswith("已知访客")
    assert p["suggested_action_hint"] == "NOTIFY_FAMILY"


# ---------------------------------------------------------------------------
# build_memory_profiles：空输入不崩溃、返回空列表
# ---------------------------------------------------------------------------
def test_build_memory_profiles_empty():
    assert build_memory_profiles([], []) == []
    # reasoning_results 比 memory_inputs 多 → 多余 rr 被忽略（按序配对）
    ri, _ = _make_input_and_result()
    assert len(build_memory_profiles([ri], [])) == 1


# ---------------------------------------------------------------------------
# build_memory_profiles：status 取最多（无 active 时）
# ---------------------------------------------------------------------------
def test_build_memory_profiles_status_tie_break():
    ri = ReasoningInput(
        current_event=CurrentEvent(
            event_id="e", event_type="visitor_event",
            visitor_instance_id="v", occurred_at=_utc(2026, 8, 2, 12),
        ),
        historical_context=(
            _episode(12, 13, 10.0, "a", MemoryStatus.DEPRECATED),
            _episode(13, 14, 10.0, "b", MemoryStatus.DEPRECATED),
        ),
        visitor_profile=None,
        risk_pattern=None,
    )
    profiles = build_memory_profiles([ri], [])
    # 无 active → 取出现最多的 deprecated
    assert profiles[0]["memory_status"] == "deprecated"


# ---------------------------------------------------------------------------
# frame_result_to_view：注入 memory_profiles 且不破坏既有视图
# ---------------------------------------------------------------------------
def test_frame_result_to_view_injects_memory_profiles():
    ri, rr = _make_input_and_result()
    fr = SimpleNamespace(
        frame=None,
        perception_events=[],
        warnings=[],
        commands=[],
        behavior_states=[],
        risk_signals=[],
        n_detections=0,
        n_visitor_events=0,
        memory_inputs=[ri],
        reasoning_results=[rr],
    )
    view: dict[str, Any] = frame_result_to_view(fr, 0, None, demo_time=None)
    assert "memory_profiles" in view
    assert len(view["memory_profiles"]) == 1
    assert view["memory_profiles"][0]["visitor_instance_id"] == "visitor_023"
    # 既有字段不受影响
    assert view["frame_index"] == 0
    assert view["perception_events"] == []
    assert view["warnings"] == []
    assert view["risk_signals"] == []


def test_frame_result_to_view_no_memory_inputs_omitted_cleanly():
    """memory_inputs 缺失（旧 FrameResult / 关闭 Memory 层）→ memory_profiles=[]。"""
    fr = SimpleNamespace(
        frame=None,
        perception_events=[],
        warnings=[],
        commands=[],
        behavior_states=[],
        risk_signals=[],
        n_detections=0,
        n_visitor_events=0,
    )
    view = frame_result_to_view(fr, 1, None, demo_time=None)
    assert view["memory_profiles"] == []

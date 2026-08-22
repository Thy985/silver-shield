"""RuntimeFrameContext schema 契约测试（ADR-0039 · Runtime Entry Contract）。

torch-free，进 CI 每 PR 契约子集。验证（AGENTS.md §3.1：契约模型变更加 schema 测试）：
- **字段闭合**：四字段集合钉死（``RUNTIME_FRAME_CONTEXT_FIELDS`` == 实际 ``fields()``）
- **frozen 不可变**：任何字段赋值 → ``FrozenInstanceError``
- **类型守卫**：frame_index / case_time / audio_events 各非法分支
- **video_frame=None 合法**（纯音频帧场景）；``audio_events`` 默认空元组
- **导入期 fail-closed**：字段漂移时 import 即 ``RuntimeError``（模仿 decision_contract 模式）
"""

from __future__ import annotations

import dataclasses

import pytest

from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
from home_perception.runtime import (
    RUNTIME_FRAME_CONTEXT_FIELDS,
    RuntimeFrameContext,
)


def _audio_event() -> AudioPerceptionEvent:
    return AudioPerceptionEvent(
        event_id="ev-ctx-1",
        timestamp=100.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=0.8,
        confidence=0.7,
        source_segment_ids=["seg-1"],
    )


# ============================================================================
# 1. 字段闭合 + frozen
# ============================================================================


class TestFieldClosure:
    def test_field_set_pinned(self):
        """四字段集合钉死：增删任一字段必须先修订 ADR-0039 并更新本测试。"""
        names = {f.name for f in dataclasses.fields(RuntimeFrameContext)}
        assert names == RUNTIME_FRAME_CONTEXT_FIELDS
        assert RUNTIME_FRAME_CONTEXT_FIELDS == frozenset(
            {"video_frame", "frame_index", "case_time", "audio_events"}
        )

    def test_frozen_immutable(self):
        """frozen dataclass：字段赋值被拒绝（C2 不可变容器）。"""
        ctx = RuntimeFrameContext(video_frame=None, frame_index=0, case_time=0.0)
        assert dataclasses.is_dataclass(ctx)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.frame_index = 1  # type: ignore[misc]

    def test_import_time_shape_assertion_holds(self):
        """导入期 fail-closed 断言成立（字段漂移时 import 本模块瞬间即炸）。"""
        from home_perception.runtime.runtime_context import _assert_contract_shape

        _assert_contract_shape()


# ============================================================================
# 2. 类型守卫
# ============================================================================


class TestGuards:
    def test_video_frame_none_allowed(self):
        """纯音频帧场景：video_frame=None 合法，audio_events 默认空元组。"""
        ctx = RuntimeFrameContext(video_frame=None, frame_index=3, case_time=1.5)
        assert ctx.video_frame is None
        assert ctx.frame_index == 3
        assert ctx.case_time == 1.5
        assert ctx.audio_events == ()

    def test_case_time_int_normalized_to_float(self):
        """case_time 数值归一为 float（int 入参合法）。"""
        ctx = RuntimeFrameContext(video_frame=None, frame_index=0, case_time=2)
        assert ctx.case_time == 2.0
        assert isinstance(ctx.case_time, float)

    @pytest.mark.parametrize("bad", [None, "3", 1.5, True])
    def test_frame_index_must_be_plain_int(self, bad):
        with pytest.raises(TypeError):
            RuntimeFrameContext(video_frame=None, frame_index=bad, case_time=0.0)

    def test_frame_index_negative_rejected(self):
        with pytest.raises(ValueError):
            RuntimeFrameContext(video_frame=None, frame_index=-1, case_time=0.0)

    @pytest.mark.parametrize("bad", [None, "1.5", True])
    def test_case_time_must_be_numeric(self, bad):
        with pytest.raises(TypeError):
            RuntimeFrameContext(video_frame=None, frame_index=0, case_time=bad)

    def test_case_time_negative_rejected(self):
        with pytest.raises(ValueError):
            RuntimeFrameContext(video_frame=None, frame_index=0, case_time=-0.1)

    def test_audio_events_list_rejected_must_be_tuple(self):
        """C2 不可变容器：list 被拒（防运行中被原地修改）。"""
        ev = _audio_event()
        with pytest.raises(TypeError, match="tuple"):
            RuntimeFrameContext(
                video_frame=None, frame_index=0, case_time=0.0, audio_events=[ev]
            )

    def test_audio_events_bad_element_rejected(self):
        """元素必须是 AudioPerceptionEvent（非人目标不入 ctx 的同构边界）。"""
        with pytest.raises(TypeError, match="AudioPerceptionEvent"):
            RuntimeFrameContext(
                video_frame=None, frame_index=0, case_time=0.0, audio_events=("x",)
            )

    def test_audio_events_tuple_of_events_accepted(self):
        ev = _audio_event()
        ctx = RuntimeFrameContext(
            video_frame=None, frame_index=7, case_time=2.333, audio_events=(ev,)
        )
        assert ctx.audio_events == (ev,)
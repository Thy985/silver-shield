"""SignalTemporalLinker 契约测试（ADR-0041）。

覆盖：
- **D1 分级表全路径**：SAME_FRAME（零阈值、优先于 NEAR_WINDOW）/ NEAR_WINDOW
  （含边界 ``delta == window``）/ UNLINKED（超窗、窗口悬空、位置缺失 fail-safe）
- **D2 悬空期语义**：``window_s=None`` 时 NEAR_WINDOW 恒不可用，SAME_FRAME 不受影响
- **D3 时钟统一**：EpisodeClock 锚定换算、负值钳位（ADR-0039 非负语义）、
  会话重启 = 重建实例
- **产物契约**：LinkedSignalPair 守卫（UNLINKED 不产 pair、strength ∈ (0, 1]）；
  模态错位防御
- **配置**：signal_temporal_window_s 默认 None；非法值（0/负/字符串）拒绝
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from home_perception.analysis.risk_signal import RiskSignal
from home_perception.analysis.signal_temporal_linker import (
    EpisodeClock,
    LinkedSignalPair,
    LinkLevel,
    SignalPosition,
    classify,
    link,
)
from home_perception.core.config import RealtimeRiskConfig

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def make_signal(source: str) -> RiskSignal:
    return RiskSignal(
        signal_id="00000000-0000-4000-8000-00000000000" + ("1" if source == "vision" else "2"),
        subject_type="visitor",
        subject_id="vi-1",
        category="behavioral",
        source=source,
        transition="raised",
        features={"dwell_seconds": 10},
        track_id=1,
        visitor_instance_id="vi-1",
        created_at=NOW,
    )


def vision_pos(frame_index=3, case_time=1.5) -> SignalPosition:
    return SignalPosition(make_signal("vision"), frame_index=frame_index, case_time=case_time)


def audio_pos(case_time=1.8) -> SignalPosition:
    return SignalPosition(make_signal("audio"), frame_index=None, case_time=case_time)


# ============================================================================
# D3 —— EpisodeClock
# ============================================================================


class TestEpisodeClock:
    def test_anchor_conversion(self):
        clock = EpisodeClock(1000.0)
        assert clock.episode_start_unix == 1000.0
        assert clock.unix_to_case(1002.5) == pytest.approx(2.5)

    def test_negative_clamped_to_zero(self):
        """早于锚点的音频时间戳（设备时钟漂移）钳位 0.0（ADR-0039 非负语义）。"""
        clock = EpisodeClock(1000.0)
        assert clock.unix_to_case(998.0) == 0.0

    def test_session_restart_is_new_instance(self):
        """会话重启 = 重新构造（旧实例不可变锚点，不回写）。"""
        first = EpisodeClock(1000.0)
        second = EpisodeClock(2000.0)
        assert first.unix_to_case(2001.0) == pytest.approx(1001.0)
        assert second.unix_to_case(2001.0) == pytest.approx(1.0)

    @pytest.mark.parametrize("bad", [None, "1000", True])
    def test_non_numeric_anchor_rejected(self, bad):
        with pytest.raises(TypeError):
            EpisodeClock(bad)


# ============================================================================
# D1 —— 分级表全路径
# ============================================================================


class TestClassify:
    def test_same_frame_zero_threshold_even_when_window_none(self):
        """同 frame_index 共现：强关联零阈值，不受窗口悬空影响。"""
        v = vision_pos(frame_index=3)
        a = SignalPosition(make_signal("audio"), frame_index=3, case_time=1.55)
        assert classify(v, a, window_s=None) is LinkLevel.SAME_FRAME

    def test_same_frame_takes_priority_over_near_window(self):
        v = vision_pos(frame_index=3, case_time=1.5)
        a = SignalPosition(make_signal("audio"), frame_index=3, case_time=9.9)
        # Δcase_time 超任何合理窗，但同帧 → 仍 SAME_FRAME
        assert classify(v, a, window_s=0.5) is LinkLevel.SAME_FRAME

    def test_near_window_within(self):
        v = vision_pos(frame_index=3, case_time=1.5)
        a = audio_pos(case_time=2.0)
        assert classify(v, a, window_s=0.5) is LinkLevel.NEAR_WINDOW

    def test_near_window_boundary_inclusive(self):
        """|Δ| == window_s 判定通过（<= 语义）。"""
        v = vision_pos(frame_index=3, case_time=1.5)
        a = audio_pos(case_time=2.0)
        assert classify(v, a, window_s=0.5) is LinkLevel.NEAR_WINDOW

    def test_unlinked_beyond_window(self):
        assert classify(vision_pos(), audio_pos(case_time=99.0), window_s=0.5) is LinkLevel.UNLINKED

    def test_unlinked_when_window_suspended(self):
        """悬空期（None）：NEAR_WINDOW 恒不可用。"""
        assert classify(vision_pos(), audio_pos(), window_s=None) is LinkLevel.UNLINKED

    def test_unlinked_when_position_missing(self):
        """fail-safe：缺时间信息宁可不关联也不猜。"""
        no_case_v = SignalPosition(make_signal("vision"), frame_index=1)
        no_frame_a = SignalPosition(make_signal("audio"))
        assert classify(no_case_v, audio_pos(), window_s=2.0) is LinkLevel.UNLINKED
        assert classify(vision_pos(), no_frame_a, window_s=2.0) is LinkLevel.UNLINKED


class TestLink:
    def test_same_frame_pair_strength_one(self):
        pair = link(vision_pos(frame_index=3), SignalPosition(make_signal("audio"), frame_index=3), window_s=None)
        assert pair is not None
        assert pair.level is LinkLevel.SAME_FRAME
        assert pair.link_strength == 1.0
        assert pair.delta == 0.0

    def test_same_frame_with_case_time_reports_real_delta(self):
        pair = link(
            vision_pos(frame_index=3, case_time=1.5),
            SignalPosition(make_signal("audio"), frame_index=3, case_time=1.56),
            window_s=None,
        )
        assert pair.delta == pytest.approx(0.06)

    def test_near_window_linear_decay(self):
        pair = link(vision_pos(), audio_pos(case_time=1.75), window_s=0.5)
        assert pair is not None
        assert pair.level is LinkLevel.NEAR_WINDOW
        assert pair.delta == pytest.approx(0.25)
        assert pair.link_strength == pytest.approx(0.5)  # 1 - delta/window

    def test_unlinked_returns_none(self):
        assert link(vision_pos(), audio_pos(case_time=99.0), window_s=0.5) is None
        assert link(vision_pos(), audio_pos(), window_s=None) is None

    def test_modality_swap_rejected(self):
        with pytest.raises(ValueError, match="vision"):
            classify(audio_pos(), audio_pos(), window_s=0.5)
        with pytest.raises(ValueError, match="audio"):
            classify(vision_pos(), vision_pos(), window_s=0.5)

    def test_linked_pair_forbids_unlinked_level(self):
        with pytest.raises(ValueError, match="UNLINKED"):
            LinkedSignalPair(
                vision_signal=make_signal("vision"),
                audio_signal=make_signal("audio"),
                level=LinkLevel.UNLINKED,
                link_strength=1.0,
                delta=0.0,
            )


# ============================================================================
# D2 —— 配置项
# ============================================================================


class TestWindowConfig:
    def test_default_is_none_suspended(self):
        cfg = RealtimeRiskConfig()
        assert cfg.signal_temporal_window_s is None

    @pytest.mark.parametrize("ok", [0.5, 1.0, 2])
    def test_positive_values_normalized_to_float(self, ok):
        assert RealtimeRiskConfig(signal_temporal_window_s=ok).signal_temporal_window_s == float(ok)

    @pytest.mark.parametrize("bad", [0, -0.5, "1.0", True])
    def test_invalid_values_rejected(self, bad):
        with pytest.raises(ValidationError):
            RealtimeRiskConfig(signal_temporal_window_s=bad)


# ============================================================================
# SignalPosition 守卫
# ============================================================================


class TestSignalPositionGuards:
    def test_non_risksignal_rejected(self):
        with pytest.raises(TypeError, match="RiskSignal"):
            SignalPosition("not-a-signal")  # type: ignore[arg-type]

    def test_bad_frame_index_rejected(self):
        with pytest.raises(ValueError, match="frame_index"):
            SignalPosition(make_signal("vision"), frame_index=-1)

    def test_bad_case_time_rejected(self):
        with pytest.raises(ValueError, match="case_time"):
            SignalPosition(make_signal("audio"), case_time=-1.0)

    def test_int_case_time_normalized(self):
        pos = SignalPosition(make_signal("audio"), case_time=2)
        assert pos.case_time == 2.0
        assert isinstance(pos.case_time, float)
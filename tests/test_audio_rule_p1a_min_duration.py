"""P1-a 回归锁:电话锚点最短持续时间(``telephone_min_duration_s``)。

> 背景(Precision Gate V1 + Batch B,2026-08-23):8 个 telephone 误报中 7 个是
> 0.14~0.22s 微段(底噪瞬态/短促人声过 VAD 后 narrow+rate≈0 命中电话分支);
> min duration 单参数可拦截全部微段 FP 且不影响正样本召回。
> ``features.duration<=0`` 视为未知(旧 fixture/手工构造),不约束以保持向后兼容。
"""

from __future__ import annotations

import pytest

from home_perception.audio.event import AudioPerceptionKind
from home_perception.audio.features import AudioFeatures
from home_perception.audio.rule import AudioRule, RuleThresholds


def _tel_features(duration: float) -> AudioFeatures:
    """典型「电话」特征剖面(窄带 + rate≈0),仅时长可控。"""
    return AudioFeatures(
        duration=duration,
        rms=0.14,
        highband_ratio=0.02,
        speech_rate=0.0,
        f0_mean=170.0,
        tremor=0.49,
        am_rate=0.0,
    )


def _kind(features: AudioFeatures, thresholds: RuleThresholds | None = None):
    ev = AudioRule(thresholds=thresholds).evaluate(
        features=features, vad_ratio=1.0, timestamp=0.0, segment_id="p1a"
    )
    return ev.kind if ev is not None else None


def test_micro_segment_rejected() -> None:
    """Gate 实证的 0.14~0.22s 微段误报必须被拦截。"""
    for dur in (0.14, 0.18, 0.22):
        assert _kind(_tel_features(dur)) is None, f"dur={dur}s 不应判级"


def test_long_segment_accepted() -> None:
    """真实持续通话(Gate 正对照 12.88s)保持召回。"""
    assert _kind(_tel_features(12.88)) is AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT


def test_threshold_boundary() -> None:
    """恰在阈值上通过、阈值下拒绝(默认 1.0s)。"""
    assert _kind(_tel_features(1.0)) is AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT
    assert _kind(_tel_features(0.99)) is None


def test_unknown_duration_backward_compat() -> None:
    """duration<=0 视为未知:旧 fixture / 手工构造路径行为不变。"""
    assert _kind(_tel_features(0.0)) is AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT


def test_custom_threshold_respected() -> None:
    """阈值可配置(验收定标留口)。"""
    t = RuleThresholds(telephone_min_duration_s=2.0)
    assert _kind(_tel_features(1.5), t) is None
    assert _kind(_tel_features(2.0), t) is AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT


@pytest.mark.parametrize("dur", [3.0, 8.0, 15.0])
def test_positive_recall_unaffected(dur: float) -> None:
    """各时长正样本持续召回(P1-P4 变体覆盖域)。"""
    assert _kind(_tel_features(dur)) is AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT
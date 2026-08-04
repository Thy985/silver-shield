"""音频感知链路测试（ADR-0026 §11 · Tier0 验证闭环）。

> 三块测试，互不重叠：
> 1. **manifest 驱动**（§11.2）：对 ``tests/fixtures/audio/*.wav`` 跑默认管道，断言产出的
>    ``AudioPerceptionEvent`` 的 kind / score / labels 与 ``manifest.yaml`` 自校准声明一致。
>    这是"生成 → fixture → 管道 → 回归"闭环的守门测试，CI 每 PR 运行。
> 2. **契约测试**：``AudioPerceptionEvent`` 序列化对称 + 模块边界铁律（无 fraud/suspect 字段）+ 枚举闭合。
> 3. **失败隔离**：音频源损坏 / 缺失 → 管道降级返回 []（不抛未分类异常，不拖垮主管道）。
> 4. **适配器**：``AudioAdapter`` 把事件翻译为 ``RiskSignal(source=AUDIO, category=COMMUNICATION)``，
>    且不携带任何犯罪认定字段。
>
> 全 torch-free：只依赖 numpy + 已提交的 WAV fixture，可在 ``test-contracts`` 子集运行。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from home_perception.audio import (
    FORBIDDEN_AUDIO_FIELDS,
    AudioFeatures,
    AudioPerceptionEvent,
    AudioPerceptionKind,
    AudioPipeline,
    AudioRule,
    RuleThresholds,
    new_event_id,
)
from home_perception.audio.event import AUDIO_PERCEPTION_KIND_VALUES
from home_perception.integration.audio_adapter import AudioAdapter, adapt_audio_event

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "audio"
MANIFEST_PATH = FIXTURE_DIR / "manifest.yaml"


def _load_manifest() -> dict:
    assert MANIFEST_PATH.exists(), f"fixture manifest 缺失：{MANIFEST_PATH}"
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


MANIFEST = _load_manifest()


# ============================================================================
# 1) manifest 驱动测试（§11.2）
# ============================================================================


@pytest.mark.parametrize("fname", sorted(MANIFEST.keys()))
def test_manifest_fixture_pipeline(fname: str) -> None:
    """对提交 fixture 跑默认管道，断言 kind/score/labels 与 manifest 声明一致。"""
    expected = MANIFEST[fname]["expected"]
    wav = FIXTURE_DIR / fname
    assert wav.exists(), f"fixture 文件缺失：{wav}"

    events = AudioPipeline.from_defaults(str(wav)).run_path(str(wav))

    # 负向对照：manifest 可能写 `kind: null`（YAML None）或 `kind: "None"`（字符串），统一判定
    is_none = expected["kind"] is None or expected["kind"] == "None"
    if is_none:
        # 负向对照：不应产出任何风险事件
        assert events == [], f"{fname} 为负向对照却产出 {[e.kind.value for e in events]}"
        return

    assert events, f"{fname} 未产出任何事件（期望 {expected['kind']}）"
    # 所有产出事件都应是声明 kind（多段 fixture 也应一致）
    for ev in events:
        assert ev.kind.value == expected["kind"], (
            f"{fname} 产出 kind={ev.kind.value}，期望 {expected['kind']}"
        )
    # 最佳（最高 score）事件满足 score_min，且 labels 与声明一致
    best = max(events, key=lambda e: e.score)
    assert best.score >= float(expected["score_min"]), (
        f"{fname} 最佳 score={best.score:.3f} < score_min={expected['score_min']}"
    )
    assert set(best.labels) == set(expected.get("labels", [])), (
        f"{fname} labels={best.labels} 与声明 {expected.get('labels')} 不一致"
    )


# ============================================================================
# 2) 契约测试
# ============================================================================


def test_audio_perception_kind_closed_set() -> None:
    """枚举闭合性基线：恰好 5 类声学感知（ADR-0026 §4.2）。"""
    assert set(AUDIO_PERCEPTION_KIND_VALUES) == {
        "audio_speech_rapid",
        "audio_voice_raised",
        "audio_telephone_persistent",
        "audio_distress_cry",
        "audio_anomaly_other",
    }


def test_event_roundtrip_and_no_forbidden_fields() -> None:
    """to_dict/from_dict/from_json 严格对称，且序列化结果不含任何犯罪认定字段。"""
    ev = AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=1700000000.123,
        kind=AudioPerceptionKind.AUDIO_DISTRESS_CRY,
        score=0.81,
        confidence=0.6,
        source_segment_ids=["seg-1"],
        labels=["speech", "distress"],
    )
    d = ev.to_dict()
    # 黑名单结构性保证
    assert FORBIDDEN_AUDIO_FIELDS.isdisjoint(d.keys()), f"to_dict 含禁止字段：{d.keys()}"
    # 对称往返
    back = AudioPerceptionEvent.from_dict(d)
    assert back == ev
    assert AudioPerceptionEvent.from_json(ev.to_json()) == ev


def test_forbidden_field_constant_nonempty() -> None:
    """黑名单非空（模块边界铁律的契约护栏）。"""
    assert len(FORBIDDEN_AUDIO_FIELDS) > 0


# ============================================================================
# 3) 失败隔离
# ============================================================================


def test_pipeline_missing_file_returns_empty() -> None:
    """缺失文件 → 降级返回 []（不抛未分类异常）。"""
    events = AudioPipeline.from_defaults("no_such_file.wav").run_path("no_such_file.wav")
    assert events == []


def test_pipeline_corrupt_wav_returns_empty(tmp_path: Path) -> None:
    """损坏的 WAV → 降级返回 []。"""
    bad = tmp_path / "corrupt.wav"
    bad.write_bytes(b"not a real wav file at all")
    events = AudioPipeline.from_defaults(str(bad)).run_path(str(bad))
    assert events == []


def test_pipeline_broken_source_returns_empty() -> None:
    """任意 source.load() 抛异常 → 降级返回 []。"""
    from home_perception.audio.source import AudioSource, LoadedAudio

    class BoomSource(AudioSource):
        def load(self) -> LoadedAudio:
            raise RuntimeError("simulated source failure")

    events = AudioPipeline(source=BoomSource()).run(BoomSource())
    assert events == []


# ============================================================================
# 4) 适配器
# ============================================================================


def _make_event(kind: AudioPerceptionKind, labels: list[str]) -> AudioPerceptionEvent:
    return AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=1700000000.0,
        kind=kind,
        score=0.7,
        confidence=0.6,
        source_segment_ids=["seg-1"],
        labels=labels,
    )


def test_adapter_maps_to_audio_communication_risk_signal() -> None:
    """音频事件 → RiskSignal(source=AUDIO, category=COMMUNICATION, transition=RAISED)。"""
    ev = _make_event(AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT, ["speech", "telephone"])
    sig = adapt_audio_event(ev, device_id="dev-1", subject_id="visitor-9")

    from home_perception.analysis.risk_signal import (
        SignalCategory,
        SignalTransition,
        SourceModality,
    )

    assert sig.source == SourceModality.AUDIO
    assert sig.category == SignalCategory.COMMUNICATION
    assert sig.transition == SignalTransition.RAISED
    assert sig.features["audio_kind"] == "audio_telephone_persistent"
    assert sig.features["audio_score"] == 0.7
    # 适配器不携带任何犯罪认定字段
    assert FORBIDDEN_AUDIO_FIELDS.isdisjoint(sig.features.keys())


def test_adapter_class_wrapper() -> None:
    """AudioAdapter 封装等价于 adapt_audio_event。"""
    ev = _make_event(AudioPerceptionKind.AUDIO_VOICE_RAISED, ["speech", "loud"])
    sig = AudioAdapter().to_risk_signal(ev, device_id="dev-1", subject_id="visitor-9")
    assert sig.features["audio_kind"] == "audio_voice_raised"


# ============================================================================
# 5) AudioRule 有序判定单元（纯特征，无 WAV 依赖）
# ============================================================================


def _rule_eval(**kw: float) -> AudioPerceptionEvent | None:
    feats = AudioFeatures(
        duration=1.0,
        rms=kw.get("rms", 0.1),
        speech_rate=kw.get("speech_rate", 3.0),
        highband_ratio=kw.get("hi", 0.5),
        f0_mean=kw.get("f0", 200.0),
        tremor=kw.get("tremor", 0.5),
        am_rate=kw.get("am", 1.0),
    )
    return AudioRule().evaluate(feats, vad_ratio=1.0, timestamp=0.0, segment_id="seg-x")


def test_rule_raised_by_loudness() -> None:
    ev = _rule_eval(rms=0.40, hi=0.5)
    assert ev is not None and ev.kind == AudioPerceptionKind.AUDIO_VOICE_RAISED


def test_rule_telephone_narrowband_flattened() -> None:
    # 窄带 + 音节率≈0（AGC 抹平包络）→ 电话
    ev = _rule_eval(rms=0.20, hi=0.02, speech_rate=0.0, tremor=0.65)
    assert ev is not None and ev.kind == AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT


def test_rule_crying_narrowband_with_syllables() -> None:
    # 窄带 + 有音节 + 高颤 → 哭腔
    ev = _rule_eval(rms=0.20, hi=0.001, speech_rate=3.0, tremor=0.90, am=1.4)
    assert ev is not None and ev.kind == AudioPerceptionKind.AUDIO_DISTRESS_CRY


def test_rule_rapid_fast_am() -> None:
    # 宽带 + 快 AM → 急促
    ev = _rule_eval(rms=0.10, hi=0.45, speech_rate=3.5, tremor=0.95, am=6.7)
    assert ev is not None and ev.kind == AudioPerceptionKind.AUDIO_SPEECH_RAPID


def test_rule_normal_none() -> None:
    # 宽带 + 中等响度 + 无快 AM → 无事件（负向对照）
    ev = _rule_eval(rms=0.12, hi=0.6, speech_rate=2.0, tremor=0.8, am=3.0)
    assert ev is None


def test_rule_telephone_not_misclassified_as_crying() -> None:
    """同窄带条件下，电话（音节率≈0）绝不落入哭腔分支。"""
    ev = _rule_eval(rms=0.20, hi=0.02, speech_rate=0.0, tremor=0.65, am=1.7)
    assert ev is not None and ev.kind == AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT


def test_rule_thresholds_are_calibrated_defaults() -> None:
    """默认阈值对象可被实例化（防止误删字段导致 CI 全绿但行为漂移）。"""
    t = RuleThresholds()
    assert t.raised_rms == 0.30
    assert t.narrowband_hi == 0.05
    assert t.rapid_min_am_rate == 5.5

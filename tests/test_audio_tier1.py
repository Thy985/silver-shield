"""Tier1 声学标签器测试（ADR-0026 §3 Tier 1 · 集成架构 + Stub 回退）。

覆盖：
- Stub 确定性 / YamNet 构造惰性（不触发 onnxruntime import）/ 缺权重抛错
- Pipeline 把 Tier1 标签并入 AudioSegmentEvent / AudioPerceptionEvent（去重）
- 触发策略 gating（segment / perception）公开方法
- build_tagger 三分支（disabled→None / enabled+权重→YamNet / enabled+缺权重→Stub）
- 失败隔离：Tier1 抛异常不拖垮 Tier0 事件
- 契约：Tier1 标签经 AudioAdapter 透传到 RiskSignal.features.labels
- 配置集成：Settings.load(default.yaml) 含 audio.tier1.enabled=False
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from home_perception.audio import (
    YAMNET_SEMANTIC_MAP,
    AudioPerceptionEvent,
    AudioPerceptionKind,
    AudioPipeline,
    AudioTag,
    EnergyStubAcousticTagger,
    FixedStubAcousticTagger,
    StubAcousticTagger,
    YamNetTagger,
    build_tagger,
    new_event_id,
    tier1_trigger_of,
)
from home_perception.audio import tagging as tagging_mod
from home_perception.audio.rule import AudioRule
from home_perception.audio.source import FileAudioSource
from home_perception.core.config import (
    TIER1_TRIGGERS,
    AudioConfig,
    Settings,
    Tier1AudioConfig,
)
from home_perception.integration.audio_adapter import adapt_audio_event

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "audio"
RAISED_WAV = FIXTURE_DIR / "raised_voice.wav"


# ---- 辅助：强制产出感知事件的规则（解耦 fixture 校准漂移） ----

class _AlwaysPerceive(AudioRule):
    def evaluate(self, features, vad_ratio, timestamp, segment_id):
        return AudioPerceptionEvent(
            event_id=new_event_id(),
            timestamp=timestamp,
            kind=AudioPerceptionKind.AUDIO_VOICE_RAISED,
            score=0.9,
            confidence=0.9,
            source_segment_ids=[segment_id],
            labels=["speech", "loud"],
        )


def _pipeline_with_tagger(tagger, trigger="segment", rule=None):
    return AudioPipeline(
        source=FileAudioSource(str(RAISED_WAV)),
        rule=rule or _AlwaysPerceive(),
        tagger=tagger,
        tier1_trigger=trigger,
    )


# ============================================================================
# Stub / YamNet 行为
# ============================================================================

def test_stub_tagger_returns_fixed_labels() -> None:
    # 测试用固定标签子类（评审 1.4：与缺权重回退 stub 区分）
    tagger = FixedStubAcousticTagger(["telephone", "crying"])
    tags = tagger.tag(np.zeros(16000, dtype=np.float32), 16000)
    assert [t.label for t in tags] == ["telephone", "crying"]


def test_stub_tagger_fallback_by_energy() -> None:
    # 缺权重回退 stub 依能量判定：有能量 → speech；静音 → silence（确定性）
    loud = EnergyStubAcousticTagger().tag(np.full(1600, 0.5, dtype=np.float32), 16000)
    assert loud == [AudioTag("speech", 1.0)]
    silent = EnergyStubAcousticTagger().tag(np.zeros(1600, dtype=np.float32), 16000)
    assert silent[0].label == "silence"


def test_yamnet_construction_does_not_import_onnxruntime() -> None:
    # 构造期不应 eager import onnxruntime（核心管道仍纯 numpy）
    assert "onnxruntime" not in sys.modules
    tagger = YamNetTagger(model_path="does_not_exist.onnx")
    assert tagger.name == "yamnet"
    assert "onnxruntime" not in sys.modules  # 仍未 import


def test_yamnet_empty_model_path_rejected() -> None:
    with pytest.raises(ValueError):
        YamNetTagger(model_path="")


def test_yamnet_tag_without_weights_raises() -> None:
    tagger = YamNetTagger(model_path="missing_model.onnx")
    # 真实环境或无 onnxruntime → RuntimeError；或权重缺失 → FileNotFoundError。二者皆可接受。
    with pytest.raises((FileNotFoundError, RuntimeError)):
        tagger.tag(np.zeros(16000, dtype=np.float32), 16000)


def test_yamnet_semantic_map_covers_key_scenarios() -> None:
    assert YAMNET_SEMANTIC_MAP["Crying, sobbing"] == "crying"
    assert YAMNET_SEMANTIC_MAP["Telephone"] == "telephone"
    assert YAMNET_SEMANTIC_MAP["Smoke alarm"] == "alarm"
    assert YAMNET_SEMANTIC_MAP["Screaming"] == "scream"


# ============================================================================
# Pipeline 标签合并（端到端）
# ============================================================================

def test_pipeline_merges_tier1_labels_into_event() -> None:
    pipe = _pipeline_with_tagger(StubAcousticTagger(["alarm", "telephone"]), trigger="segment")
    events = pipe.run_path(str(RAISED_WAV))
    assert events, "RAISED fixture 应产出感知事件"
    ev = events[0]
    # Tier1 标签与 Tier0 标签去重合并
    assert "alarm" in ev.labels
    assert "telephone" in ev.labels
    assert "speech" in ev.labels  # Tier0 标签保留
    assert ev.labels == sorted(set(ev.labels))  # 已排序去重


def test_pipeline_tier1_disabled_no_tagger_adds_nothing() -> None:
    pipe = AudioPipeline(source=FileAudioSource(str(RAISED_WAV)), rule=_AlwaysPerceive())
    assert pipe.tagger is None
    events = pipe.run_path(str(RAISED_WAV))
    assert events
    # 无 Tier1：标签仅 Tier0 的 ["speech","loud"]
    assert set(events[0].labels) == {"speech", "loud"}


def test_pipeline_tier1_failure_isolated() -> None:
    class _BoomTagger(StubAcousticTagger):
        def tag(self, samples, sample_rate):
            raise RuntimeError("Tier1 backend down")

    pipe = _pipeline_with_tagger(_BoomTagger(), trigger="segment")
    # 失败隔离：Tier1 崩不影响 Tier0 事件产出
    events = pipe.run_path(str(RAISED_WAV))
    assert events
    assert set(events[0].labels) == {"speech", "loud"}  # Tier0 标签仍在，无 Tier1 泄漏


# ============================================================================
# 触发策略 gating（公开方法）
# ============================================================================

def test_tier1_should_run_gating() -> None:
    seg_pipe = _pipeline_with_tagger(StubAcousticTagger(), trigger="segment")
    perc_pipe = _pipeline_with_tagger(StubAcousticTagger(), trigger="perception")

    # segment：有无感知事件都跑
    assert seg_pipe.tier1_should_run(False) is True
    assert seg_pipe.tier1_should_run(True) is True
    # perception：仅感知事件存在时跑
    assert perc_pipe.tier1_should_run(False) is False
    assert perc_pipe.tier1_should_run(True) is True


def test_tier1_should_run_without_tagger_is_false() -> None:
    pipe = AudioPipeline(source=FileAudioSource(str(RAISED_WAV)), rule=_AlwaysPerceive())
    assert pipe.tier1_should_run(True) is False  # 未配置 tagger → 不跑


# ============================================================================
# build_tagger 工厂三分支
# ============================================================================

def test_build_tagger_disabled_returns_none() -> None:
    cfg = Tier1AudioConfig(enabled=False, model_path="")
    assert build_tagger(cfg) is None


def test_build_tagger_enabled_with_model_returns_yamnet() -> None:
    cfg = Tier1AudioConfig(enabled=True, model_path="models/yamnet.onnx")
    tagger = build_tagger(cfg)
    assert isinstance(tagger, YamNetTagger)
    assert tagger.model_path == "models/yamnet.onnx"


def test_build_tagger_enabled_without_model_falls_back_to_stub() -> None:
    # 开启但缺权重 → 确定性 EnergyStub 回退（不开就崩，保证 config 开启即能用）
    cfg = Tier1AudioConfig(enabled=True, model_path="")
    tagger = build_tagger(cfg)
    assert isinstance(tagger, EnergyStubAcousticTagger)
    assert isinstance(tagger, StubAcousticTagger)  # 向后兼容：子类关系保留


def test_from_audio_config_wires_tagger() -> None:
    # enabled+缺权重 → Stub 回退，管道能跑并注入标签
    cfg = AudioConfig(tier1=Tier1AudioConfig(enabled=True, model_path="", trigger="segment"))
    pipe = AudioPipeline.from_audio_config(cfg, FileAudioSource(str(RAISED_WAV)))
    assert isinstance(pipe.tagger, StubAcousticTagger)
    events = pipe.run_path(str(RAISED_WAV))
    assert events
    # Stub 确定性回退：有能量段标 "speech"
    assert "speech" in events[0].labels


# ============================================================================
# 契约：Tier1 标签透传到 RiskSignal
# ============================================================================

def test_tier1_labels_flow_to_risk_signal() -> None:
    ev = AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=1.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=0.8,
        confidence=0.7,
        source_segment_ids=["seg-1"],
        labels=["speech", "telephone", "alarm"],  # 含 Tier1 标签
    )
    sig = adapt_audio_event(ev, device_id="cam1", subject_id="visitor-1")
    assert "alarm" in sig.features["labels"]
    assert "telephone" in sig.features["labels"]


# ============================================================================
# 配置集成：default.yaml 含 audio 段
# ============================================================================

def test_settings_load_includes_audio_tier1_disabled() -> None:
    settings = Settings.load("config/default.yaml")
    assert settings.audio.tier1.enabled is False
    assert settings.audio.tier1.trigger == "segment"
    assert settings.audio.tier1.threshold == 0.1


# ============================================================================
# 真实推理路径（替身 session，不依赖真实权重 / onnxruntime）
# ============================================================================


def _fake_scores(high: dict[int, float], n: int = 521) -> np.ndarray:
    s = np.zeros(n, dtype=np.float32)
    for idx, val in high.items():
        s[idx] = val
    return s


class _FakeOrtSession:
    """最小 ONNX runtime session 替身：返回固定 521 维 score，覆盖真实推理路径。"""

    def __init__(self, scores: np.ndarray) -> None:
        self._scores = scores.astype(np.float32)

    def get_inputs(self):
        return [SimpleNamespace(name="input.1")]

    def run(self, _, feed):  # feed: {"input.1": [1, frame]}；返回 [1, 521]
        return [self._scores[None, :]]


def _yamnet_with_fake_session(scores, class_names=None):
    tagger = YamNetTagger(
        model_path="fake.onnx",
        class_names=class_names or [f"c{i}" for i in range(521)],
        threshold=0.1,
    )
    tagger._session = _FakeOrtSession(scores)  # 注入替身，绕过 onnxruntime/权重加载
    return tagger


def test_yamnet_real_path_with_fake_session() -> None:
    # 真实推理路径 smoke test（不依赖真实权重 / onnxruntime）：覆盖 resample→frames→排序→语义归并
    class_names = ["Speech"] + [f"c{i}" for i in range(1, 521)]
    scores = _fake_scores({0: 0.95, 1: 0.80})
    tagger = _yamnet_with_fake_session(scores, class_names=class_names)
    tags = tagger.tag(np.full(16000, 0.5, dtype=np.float32), 16000)
    labels = [t.label for t in tags]
    assert "speech" in labels  # Speech → speech（语义映射）
    speech = next(t for t in tags if t.label == "speech")
    assert speech.score == pytest.approx(0.95, abs=1e-3)  # score 透传（评审 1.5）


def test_resample_fallback_runs_without_scipy() -> None:
    # sr != target → 触发 _resample_to；managed venv 无 scipy → 退化为 np.interp，不应报错
    tagger = _yamnet_with_fake_session(_fake_scores({0: 0.9}))
    tags = tagger.tag(np.full(8000, 0.3, dtype=np.float32), 8000)
    assert len(tags) >= 1


def _tagger_for_frames() -> YamNetTagger:
    tagger = YamNetTagger(model_path="fake.onnx", class_names=[f"c{i}" for i in range(521)])
    tagger._session = _FakeOrtSession(_fake_scores({0: 0.9}))
    return tagger


def test_run_frames_single_frame_after_pad() -> None:
    sr = 16000
    frame = int(0.96 * sr)
    tagger = _tagger_for_frames()
    wav = np.zeros(frame - 100, dtype=np.float32)  # 不足一帧 → 补零到整帧 → 1 帧
    out = tagger._run_frames(tagger._session, wav, sr)
    assert out.shape == (1, 521)


def test_run_frames_non_aligned_tail_coverage() -> None:
    sr = 16000
    frame = int(0.96 * sr)
    hop = int(0.48 * sr)
    tagger = _tagger_for_frames()
    wav = np.zeros(frame + hop // 2, dtype=np.float32)  # 非 2×hop 整数倍 → 应覆盖尾帧
    out = tagger._run_frames(tagger._session, wav, sr)
    assert out.shape[0] == 2  # 首帧 + 补尾帧


def test_run_frames_multiple_frames() -> None:
    sr = 16000
    frame = int(0.96 * sr)
    hop = int(0.48 * sr)
    tagger = _tagger_for_frames()
    wav = np.zeros(frame + 3 * hop, dtype=np.float32)
    out = tagger._run_frames(tagger._session, wav, sr)
    assert out.shape[0] >= 4  # 首帧 + 多个 hop 帧 + 尾帧


class _FakeRank1OrtSession:
    """模拟 PINTO YAMNet ONNX：输入为 rank-1（[samples]，声明 shape ['samples']）。"""

    def __init__(self, scores: np.ndarray) -> None:
        self._scores = scores.astype(np.float32)
        self.last_feed_rank = None

    def get_inputs(self):
        return [SimpleNamespace(name="input.1", shape=["samples"])]

    def run(self, _, feed):
        arr = next(iter(feed.values()))
        self.last_feed_rank = arr.ndim
        return [self._scores[None, :]]


def test_run_frames_feeds_rank1_for_rank1_model() -> None:
    # 真实 PINTO 权重输入为 rank-1；_run_frames 必须喂 rank-1（验证发现的不兼容修复）。
    sr = 16000
    frame = int(0.96 * sr)
    tagger = YamNetTagger(model_path="fake.onnx", class_names=[f"c{i}" for i in range(521)])
    sess = _FakeRank1OrtSession(_fake_scores({0: 0.9}))
    tagger._session = sess
    wav = np.zeros(frame, dtype=np.float32)
    out = tagger._run_frames(sess, wav, sr)
    assert out.shape == (1, 521)
    assert sess.last_feed_rank == 1  # 真实模型要求 rank-1 输入（非 rank-2）


# ============================================================================
# 1.2 class_names 缺失告警（不可静默吞掉）
# ============================================================================


def test_yamnet_warns_when_class_names_missing() -> None:
    recorded = []
    original = tagging_mod.log.warning
    tagging_mod.log.warning = lambda msg, **kw: recorded.append((msg, kw))
    try:
        YamNetTagger(model_path="x.onnx")  # 缺 class_names
    finally:
        tagging_mod.log.warning = original
    assert any("class_names_missing" in str(m) for m, _ in recorded)


# ============================================================================
# 1.5 scored_labels 透传到 RiskSignal
# ============================================================================


def test_tier1_scored_labels_flow_to_risk_signal() -> None:
    ev = AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=1.0,
        kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
        score=0.8,
        confidence=0.7,
        source_segment_ids=["seg-1"],
        labels=["speech", "telephone", "alarm"],
        scored_labels=[AudioTag("alarm", 0.92), AudioTag("telephone", 0.7)],
    )
    sig = adapt_audio_event(ev, device_id="cam1", subject_id="visitor-1")
    assert sig.features["audio_tier1_max_score"] == pytest.approx(0.92, abs=1e-3)
    assert {"label": "alarm", "score": 0.92} in sig.features["audio_tier1_scored_labels"]


# ============================================================================
# 2.4 / 4.3 labels 合并契约：去重 + 字母序集合（顺序不具语义）
# ============================================================================


def test_labels_merge_is_sorted_set_contract() -> None:
    class _Tier0Tel(AudioRule):
        def evaluate(self, features, vad_ratio, timestamp, segment_id):
            return AudioPerceptionEvent(
                event_id=new_event_id(),
                timestamp=timestamp,
                kind=AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT,
                score=0.8,
                confidence=0.8,
                source_segment_ids=[segment_id],
                labels=["telephone", "speech"],  # 故意非字母序
            )

    pipe = AudioPipeline(
        source=FileAudioSource(str(RAISED_WAV)),
        rule=_Tier0Tel(),
        tagger=FixedStubAcousticTagger(["alarm", "speech"]),
        tier1_trigger="segment",
    )
    events = pipe.run_path(str(RAISED_WAV))
    assert events
    # 合并后锁定为去重 + 字母序集合，与 Tier0 原始顺序无关（评审 2.4 / 4.3）
    assert events[0].labels == ["alarm", "speech", "telephone"]
    assert events[0].scored_labels  # Tier1 score 已保留（评审 1.5）


# ============================================================================
# 4.4 Tier1 配置校验器（NaN / 越界 / 枚举）
# ============================================================================


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"threshold": float("nan")},
        {"threshold": 1.5},
        {"top_k": 0},
        {"trigger": "foo"},
        {"target_sr": -1},
    ],
)
def test_tier1_config_validators_reject_invalid(bad_kwargs) -> None:
    with pytest.raises(ValueError):
        Tier1AudioConfig(**bad_kwargs)


def test_tier1_triggers_constant_exported() -> None:
    assert TIER1_TRIGGERS == ("segment", "perception")


# ============================================================================
# 4.5 from_audio_config(audio_cfg=None) 不崩溃
# ============================================================================


def test_from_audio_config_with_none_audio_cfg() -> None:
    pipe = AudioPipeline.from_audio_config(None, FileAudioSource(str(RAISED_WAV)))
    assert pipe.tagger is None
    assert pipe.tier1_trigger == "segment"


def test_tier1_trigger_of_helper() -> None:
    assert tier1_trigger_of(None) == "segment"
    assert tier1_trigger_of(Tier1AudioConfig(trigger="perception")) == "perception"
    assert tier1_trigger_of(Tier1AudioConfig()) == "segment"


# ============================================================================
# 4B Gate 4 真实缺陷：YAMNet ONNX 输入形状校验（退化导出显式拒绝，杜绝静默 tier1_failed）
# ============================================================================


class _FakeDegenerateOrtSession:
    """模拟退化 YAMNet 导出（如 yamnet.onnx）：输入被固化为固定 [1]，无法接受变长音频。

    真实推理期必报 INVALID_ARGUMENT（Got invalid dimensions ... Expected: 1）；管道失败隔离
    会静默吞掉。本替身用于验证加载/首推阶段即被形状校验拒绝，且其 run 不应被调用。
    """

    def get_inputs(self):
        return [SimpleNamespace(name="waveform:0", shape=[1])]

    def run(self, _, feed):  # pragma: no cover - 不应被调用
        raise AssertionError("退化导出不应被喂入推理（形状校验应在前拦截）")


def test_yamnet_rejects_degenerate_fixed_input_shape() -> None:
    tagger = YamNetTagger(model_path="fake.onnx", class_names=[f"c{i}" for i in range(521)])
    tagger._session = _FakeDegenerateOrtSession()
    # 形状校验在 tag() 内（_ensure_session 之后）执行，退化导出必须显式拒绝并给出修正指向。
    with pytest.raises(ValueError, match="yamnet_runtime.onnx"):
        tagger.tag(np.zeros(16000, dtype=np.float32), 16000)


def test_yamnet_accepts_rank1_dynamic_input_shape() -> None:
    # 正确导出（[samples] 动态 rank-1，如 yamnet_runtime.onnx）应过校验并按 rank-1 喂入。
    tagger = YamNetTagger(model_path="fake.onnx", class_names=[f"c{i}" for i in range(521)])
    sess = _FakeRank1OrtSession(_fake_scores({0: 0.9}))
    tagger._session = sess
    tags = tagger.tag(np.zeros(16000, dtype=np.float32), 16000)
    assert len(tags) >= 1
    assert sess.last_feed_rank == 1  # 动态 rank-1 模型按 rank-1 喂入（非 rank-2）


# 真实权重回归：仅在本地/CI 装有 onnxruntime 且含权重时运行，否则 skip（torch-free 环境）。
# 用 find_spec 探测（不实际 import），避免污染 sys.modules 破坏
# test_yamnet_construction_does_not_import_onnxruntime 的不变量。
import importlib.util as _importlib_util

_HAVE_ORT = _importlib_util.find_spec("onnxruntime") is not None


@pytest.mark.skipif(not _HAVE_ORT, reason="onnxruntime not installed (torch-free env)")
def test_yamnet_runtime_export_tags_real_audio() -> None:
    # Gate 4 真实修复回归：正确导出 yamnet_runtime.onnx（动态 [samples]）必须能真实推理，
    # 产出非空 521 类 score，不再报形状错误（对比退化导出 yamnet.onnx 必败）。
    mp = Path("data/models/yamnet/onnx/yamnet_runtime.onnx")
    if not mp.exists():
        pytest.skip("yamnet_runtime.onnx not present in this environment")
    cfg = Tier1AudioConfig(
        enabled=True,
        model_path=str(mp),
        threshold=0.1,
        top_k=5,
        target_sr=16000,
    )
    tagger = build_tagger(cfg)
    assert isinstance(tagger, YamNetTagger)
    sr = 16000
    t = np.arange(sr * 2) / sr
    wav = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    tags = tagger.tag(wav, sr)
    assert tags, "正确导出应产出 YAMNet 标签"


def test_live_audio_config_points_to_runtime_yamnet_export() -> None:
    # Gate 4 配置回归：live_audio.yaml 的 Tier1 权重必须指向可用导出 yamnet_runtime.onnx
    # （输入动态 [samples]，可真实推理），不得指回退化导出 yamnet.onnx（输入固定 [1]，必败）。
    # 该路径是 PR #240/#241 后 Gate 4 YAMNet 真正跑通的必备半边；回归即阻断。
    import yaml

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "live_audio.yaml"
    assert cfg_path.is_file(), cfg_path
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    mp = raw["audio"]["tier1"]["model_path"]
    assert mp.endswith("yamnet_runtime.onnx"), mp
    assert not mp.endswith("yamnet.onnx"), mp

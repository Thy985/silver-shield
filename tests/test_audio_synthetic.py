"""音频合成基础设施测试（effects / generator / scenario）。

> 纯 numpy，确定性（固定 seed）；不触发 TTS / 网络（离线 base_ref 路径）。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from home_perception.audio.event import AudioPerceptionKind
from home_perception.audio.tts.effects import (
    EFFECTS,
    _make_rir,
    apply_distance,
    apply_effects,
    apply_noise,
    apply_reverb,
    apply_speech_rate,
    apply_volume,
)
from home_perception.audio.tts.generator import (
    Scenario,
    generate_all,
    generate_scenario,
    load_scenarios,
)
from home_perception.audio.tts.provider import EdgeTTSProvider, _edge_rate_pitch

SR = 16000
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audio"
TTS_FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "home_perception"
    / "audio"
    / "tts"
    / "fixtures"
)
# E4：离线 generator 测试依赖 tests/fixtures/audio；缺失时跳过而非 IOError 失败
_GEN_SKIP = pytest.mark.skipif(not FIXTURES.exists(), reason="tests/fixtures/audio 缺失")


def _tone(freq: float = 440.0, dur: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    return (0.5 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# ---------------- effects：语速 ----------------
def test_speech_rate_changes_duration():
    x = _tone(dur=0.5)
    y = apply_speech_rate(x, SR, factor=1.6)
    # factor=1.6 → 时长约为原 1/1.6，且明显短于原信号（保音高伸缩）
    assert 0.5 * len(x) < len(y) < 0.9 * len(x)


def test_speech_rate_preserves_pitch():
    x = _tone(freq=440.0, dur=1.0)
    y = apply_speech_rate(x, SR, factor=1.5)
    spec = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), d=1.0 / SR)
    dom = freqs[np.argmax(spec)]
    assert 380.0 < dom < 500.0  # 音高基本保持（不偏移为 660）


def test_speech_rate_factor_one_is_noop():
    x = _tone(dur=0.5)
    y = apply_speech_rate(x, SR, factor=1.0)
    assert np.array_equal(x, y)


def test_speech_rate_short_signal_is_noop():
    # B2：len < n_fft 时相位声码器不可用，静默返回原信号（应触发 warnings.warn）
    x = _tone(dur=0.01)  # ~160 样本 < n_fft(1024)
    assert len(x) < 1024
    with pytest.warns(UserWarning):
        y = apply_speech_rate(x, SR, factor=1.6)
    assert np.array_equal(x, y)


# ---------------- effects：音量 ----------------
def test_volume_gain_increases_rms():
    x = _tone(dur=0.3)
    y = apply_volume(x, SR, gain_db=6.0)
    assert np.sqrt(np.mean(y**2)) > np.sqrt(np.mean(x**2)) * 1.9


def test_volume_clips_at_one():
    x = np.ones(1000, dtype=np.float32)
    y = apply_volume(x, SR, gain_db=20.0)
    assert np.all(y <= 1.0)
    assert np.all(y >= -1.0)


def test_volume_jitter_db_in_range():
    # D2：jitter_db 在 ±jitter 内确定性随机化增益（不测试「均值≈基础增益」，
    # 因为单次调用的抖动是标量增益，不会跨样本平均）。
    x = np.full(2000, 0.3, dtype=np.float32)
    jit = apply_volume(x, SR, gain_db=0.0, jitter_db=6.0, seed=42)
    lo = 0.3 * 10.0 ** (-6.0 / 20.0)
    hi = 0.3 * 10.0 ** (6.0 / 20.0)
    m = float(np.mean(jit))
    assert lo - 1e-6 <= m <= hi + 1e-6
    # 确定性：同 seed 同结果
    jit2 = apply_volume(x, SR, gain_db=0.0, jitter_db=6.0, seed=42)
    assert np.array_equal(jit, jit2)


# ---------------- effects：噪声 ----------------
def test_noise_respects_snr():
    x = _tone(dur=0.5)
    y = apply_noise(x, SR, snr_db=20.0, seed=42)
    sig_power = np.mean(x**2)
    noise_power = np.mean((y - x) ** 2) + 1e-12
    snr = 10.0 * np.log10(sig_power / noise_power)
    assert abs(snr - 20.0) < 2.0


def test_noise_deterministic():
    x = _tone(dur=0.5)
    a = apply_noise(x, SR, snr_db=15.0, seed=7)
    b = apply_noise(x, SR, snr_db=15.0, seed=7)
    assert np.array_equal(a, b)


def test_noise_pink_has_low_frequency_emphasis():
    # D3：pink 噪声低频能量 > 高频能量（1/f 谱）
    x = np.full(16000, 0.001, dtype=np.float32)
    y = apply_noise(x, SR, snr_db=0.0, color="pink", seed=42)
    noise = (y - x).astype(np.float64)
    spec = np.abs(np.fft.rfft(noise))
    freqs = np.fft.rfftfreq(len(noise), d=1.0 / SR)
    low = spec[freqs < SR / 4].sum()
    high = spec[freqs > SR / 2].sum()
    assert low > high


def test_noise_pink_differs_from_white():
    x = _tone(dur=0.5)
    w = apply_noise(x, SR, snr_db=20.0, color="white", seed=42)
    p = apply_noise(x, SR, snr_db=20.0, color="pink", seed=42)
    assert not np.array_equal(w, p)


# ---------------- effects：混响 ----------------
def test_reverb_preserves_full_tail():
    # A2：混响保留完整尾部，输出长度 = len(x) + len(rir) - 1（不再砍尾）
    x = _tone(dur=0.5)
    rir = _make_rir(SR, 0.3, 1)
    y = apply_reverb(x, SR, rt60=0.3, wet=0.5, seed=1)
    assert len(y) == len(x) + len(rir) - 1
    # 直达声段（前 len(x)）不应是纯静音——干声确实进入输出
    assert np.sqrt(np.mean(y[: len(x)] ** 2)) > 1e-4


def test_reverb_deterministic():
    x = _tone(dur=0.5)
    a = apply_reverb(x, SR, rt60=0.4, wet=0.5, seed=2)
    b = apply_reverb(x, SR, rt60=0.4, wet=0.5, seed=2)
    assert np.array_equal(a, b)


def test_reverb_not_identity():
    x = _tone(dur=0.5)
    y = apply_reverb(x, SR, rt60=0.5, wet=0.6, seed=3)
    # 输出更长（含尾部）；仅比较直达声段（与 x 等长部分）
    assert np.mean((y[: len(x)] - x) ** 2) > 1e-6


def test_reverb_rt60_zero_is_identity():
    # B1：rt60<=0 → 单位脉冲，混响 ≈ 直通（输出与原信号等长）
    x = _tone(dur=0.3)
    y = apply_reverb(x, SR, rt60=0.0, wet=0.5, seed=9)
    assert len(y) == len(x)
    assert np.mean((y - x) ** 2) < 1e-6


# ---------------- effects：距离 ----------------
def test_distance_attenuates_farther():
    x = _tone(dur=0.3)
    near = apply_distance(x, SR, meters=1.0)
    far = apply_distance(x, SR, meters=8.0)
    assert np.sqrt(np.mean(far**2)) < np.sqrt(np.mean(near**2))


def test_distance_air_absorption_lowpass():
    # 高频纯音经空气吸收应被衰减（能量下降），而近场（无吸收）基本不变
    x = _tone(freq=6000.0, dur=0.3)
    near = apply_distance(x, SR, meters=1.0, air_absorption=False)
    far = apply_distance(x, SR, meters=10.0, air_absorption=True)
    assert np.sqrt(np.mean(far**2)) < np.sqrt(np.mean(near**2)) * 0.95


# ---------------- dispatcher ----------------
def test_apply_effects_chain_order():
    x = _tone(dur=0.3)
    chain = [{"volume": {"gain_db": 6.0}}, {"noise": {"snr_db": 30.0, "seed": 1}}]
    y = apply_effects(x, SR, chain)
    assert y.shape == x.shape


def test_apply_effects_unknown_raises():
    with pytest.raises(KeyError):
        apply_effects(_tone(dur=0.2), SR, [{"unknown_effect": {}}])


def test_effects_registry_has_five():
    assert set(EFFECTS) == {"speech_rate", "volume", "noise", "reverb", "distance"}


def test_apply_effects_invalid_multikey_raises():
    # D4：单键 dict 约束；多键 dict 应报错
    with pytest.raises(ValueError):
        apply_effects(_tone(dur=0.2), SR, [{"volume": {}, "noise": {}}])


def test_apply_effects_invalid_not_dict_raises():
    # D4：非 dict 步骤应报错
    with pytest.raises(ValueError):
        apply_effects(_tone(dur=0.2), SR, [["volume", {}]])


# ---------------- generator / scenario（离线） ----------------
def test_provider_still_importable():
    # scripts/gen_audio_fixtures.py 依赖此路径；重构为包后必须仍可导入
    from home_perception.audio.tts import EdgeTTSProvider as E1

    assert E1 is EdgeTTSProvider


def test_edge_rate_pitch_format():
    # A5/A6：rate 为相对百分比、pitch 为 (pitch-1)*100 Hz；自动带符号
    r, p = _edge_rate_pitch(1.4, 1.1)
    assert r == "+40%"
    assert p == "+10Hz"
    r2, p2 = _edge_rate_pitch(0.5, 0.8)
    assert r2 == "-50%"
    assert p2 == "-20Hz"
    r3, p3 = _edge_rate_pitch(1.0, 1.0)
    assert r3 == "+0%"
    assert p3 == "+0Hz"


def test_load_scenarios_parses(tmp_path):
    yaml_text = """
sample_rate: 16000
base_dir: fixtures
scenarios:
  - id: a
    base_ref: normal_speech.wav
    effects:
      - speech_rate: { factor: 1.5 }
"""
    p = tmp_path / "scenario.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    scs, base_dir = load_scenarios(p)
    assert base_dir == "fixtures"
    assert len(scs) == 1
    assert scs[0].id == "a"
    assert scs[0].effects == [{"speech_rate": {"factor": 1.5}}]


@_GEN_SKIP
def test_generate_scenario_offline_writes_wav(tmp_path):
    sc = Scenario(id="gen_test", base_ref="normal_speech.wav", effects=[{"volume": {"gain_db": 6.0}}])
    out = generate_scenario(sc, tmp_path, fixtures_root=FIXTURES)
    assert out.exists()
    with wave.open(str(out), "rb") as wf:
        assert wf.getframerate() == SR
        assert wf.getnchannels() == 1


@_GEN_SKIP
def test_generate_all_offline(tmp_path):
    yaml_text = """
sample_rate: 16000
base_dir: fixtures
scenarios:
  - id: g1
    base_ref: normal_speech.wav
    effects: [{ volume: { gain_db: 3.0 } }]
  - id: g2
    base_ref: crying_voice.wav
    effects: [{ reverb: { rt60: 0.3, wet: 0.3, seed: 5 } }]
"""
    sp = tmp_path / "scenario.yaml"
    sp.write_text(yaml_text, encoding="utf-8")
    outs = generate_all(sp, tmp_path / "out", fixtures_root=FIXTURES)
    assert len(outs) == 2
    for o in outs:
        assert o.exists()


def test_generate_scenario_missing_base_raises(tmp_path):
    # D5/B8：base 文件不存在时 _resolve_base 应显式抛 FileNotFoundError（非静默后 IOError）
    sc = Scenario(id="missing", base_ref="does_not_exist.wav")
    with pytest.raises(FileNotFoundError):
        generate_scenario(sc, tmp_path, fixtures_root=tmp_path)


def test_load_scenarios_parses_tts(tmp_path):
    # D6：tts: 路径与 expected 字段解析
    yaml_text = """
sample_rate: 16000
base_dir: fixtures
scenarios:
  - id: t1
    tts: { text: "你好", voice: zh-CN-XiaoxiaoNeural, rate: 1.2, pitch: 1.1 }
    effects: [{ volume: { gain_db: 3.0 } }]
    expected: { kind: audio_speech_rapid }
"""
    p = tmp_path / "scenario.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    scs, base_dir = load_scenarios(p)
    assert base_dir == "fixtures"
    assert len(scs) == 1
    assert scs[0].tts == {"text": "你好", "voice": "zh-CN-XiaoxiaoNeural", "rate": 1.2, "pitch": 1.1}
    assert scs[0].expected == {"kind": "audio_speech_rapid"}
    assert scs[0].base_source() == "tts"


def test_repo_scenario_yaml_expected_kinds_valid():
    # E3：仓库内 scenario.yaml 的 expected.kind 必须是合法 AudioPerceptionKind（跨包契约）
    p = TTS_FIXTURES.parent / "scenario.yaml"
    if not p.exists():
        pytest.skip(f"{p} 不存在")
    scs, _ = load_scenarios(p)
    valid = {e.value for e in AudioPerceptionKind}
    for sc in scs:
        if sc.expected and "kind" in sc.expected:
            assert sc.expected["kind"] in valid, (
                f"scenario {sc.id!r} 的 expected.kind={sc.expected['kind']!r} "
                f"不是合法 AudioPerceptionKind；合法值：{sorted(valid)}"
            )


@pytest.mark.skipif(not TTS_FIXTURES.exists(), reason="tts fixtures 未生成")
def test_tts_fixtures_are_valid_wav():
    # D7：已提交/生成的 tts fixture WAV 应为 16k 单声道 16-bit
    names = [
        "normal_speech_fast.wav",
        "normal_speech_loud.wav",
        "telephone_noisy.wav",
        "crying_reverberant.wav",
        "raised_voice_far.wav",
        "telephone_far_noisy.wav",
    ]
    for name in names:
        path = TTS_FIXTURES / name
        assert path.exists(), f"缺失 fixture: {name}"
        with wave.open(str(path)) as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2

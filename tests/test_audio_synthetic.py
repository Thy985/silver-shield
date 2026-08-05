"""音频合成基础设施测试（effects / generator / scenario）。

> 纯 numpy，确定性（固定 seed）；不触发 TTS / 网络（离线 base_ref 路径）。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from home_perception.audio.tts.effects import (
    EFFECTS,
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
from home_perception.audio.tts.provider import EdgeTTSProvider

SR = 16000
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audio"


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


# ---------------- effects：混响 ----------------
def test_reverb_length_preserved():
    x = _tone(dur=0.5)
    y = apply_reverb(x, SR, rt60=0.3, wet=0.5, seed=1)
    assert len(y) == len(x)


def test_reverb_deterministic():
    x = _tone(dur=0.5)
    a = apply_reverb(x, SR, rt60=0.4, wet=0.5, seed=2)
    b = apply_reverb(x, SR, rt60=0.4, wet=0.5, seed=2)
    assert np.array_equal(a, b)


def test_reverb_not_identity():
    x = _tone(dur=0.5)
    y = apply_reverb(x, SR, rt60=0.5, wet=0.6, seed=3)
    assert np.mean((y - x) ** 2) > 1e-6


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


# ---------------- generator / scenario（离线） ----------------
def test_provider_still_importable():
    # scripts/gen_audio_fixtures.py 依赖此路径；重构为包后必须仍可导入
    from home_perception.audio.tts import EdgeTTSProvider as E1

    assert E1 is EdgeTTSProvider


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


def test_generate_scenario_offline_writes_wav(tmp_path):
    sc = Scenario(id="gen_test", base_ref="normal_speech.wav", effects=[{"volume": {"gain_db": 6.0}}])
    out = generate_scenario(sc, tmp_path, fixtures_root=FIXTURES)
    assert out.exists()
    with wave.open(str(out), "rb") as wf:
        assert wf.getframerate() == SR
        assert wf.getnchannels() == 1


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

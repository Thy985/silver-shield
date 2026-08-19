#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI Audio Factory for telephone_risk (V3)
Architecture: Acoustic state transition (Normal -> Stressed)

Four-layer audio:
  Layer 1: Ambient room tone (0-15s, -28dB)
  Layer 2: Elderly normal speech (0-6s, from TTS + DSP pitch-down)
  Layer 3: Far-end模糊人声 (2-15s, -18dB, bandpass filtered)
  Layer 4: Elderly stressed speech (6-15s, TTS + DSP pitch-shift + jitter)

Case A (15s): Layer1 + Layer2(full normal) + Layer3 = LOW risk
Case B (15s): Layer1 + Layer2(0-6s) + Layer4(6-15s) + Layer3 = RISK_SIGNAL

Key design:
- System only analyzes acoustic features (F0, speech_rate, energy, jitter)
- No ASR, no semantic understanding
- Case A: stable acoustics -> voice_state=NORMAL -> LOW
- Case B: acoustic state change -> voice_state=ELEVATED_STRESS -> RISK_SIGNAL
- "Stress" is NOT "fraud" - system continues observation

Fix: proper 32-bit scaling + normalized signal amplitudes
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter
import os

SR = 48000  # Output sample rate
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
TTS_DIR = os.path.join(OUTPUT_DIR, "audio", "tts_raw")

# TTS segment files
TTS_SEGMENTS = [
    {"file": "seg1_normal.wav", "role": "normal", "target_dur": 6.0},
    {"file": "seg2_attention.wav", "role": "attention", "target_dur": 3.0},
    {"file": "seg3_arousal.wav", "role": "arousal", "target_dur": 3.5},
    {"file": "seg4_stress.wav", "role": "stress", "target_dur": 2.5},
]

# dB gains for mixing
GAIN_AMBIENT_DB = -28.0
GAIN_VOICE_DB = -3.0  # elderly voice
GAIN_FAREND_DB = -18.0  # far-end模糊人声
GAIN_MICRO_DB = -32.0  # micro events

# Fixed seed for reproducibility
np.random.seed(42)


def db_to_linear(db):
    """Convert dB to linear gain."""
    return 10.0 ** (db / 20.0)


def load_tts_wav(filepath, target_sr=SR):
    """Load TTS wav file and resample to target_sr."""
    sr, data = wavfile.read(filepath)
    # Convert stereo to mono
    if len(data.shape) > 1:
        data = data[:, 0]
    # Normalize to float
    if data.dtype == np.int16:
        data = data.astype(np.float64) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float64) / 2147483648.0
    elif data.dtype == np.float32:
        data = data.astype(np.float64)
    # Simple resample if needed (linear interpolation)
    if sr != target_sr:
        n_target = int(len(data) * target_sr / sr)
        indices = np.linspace(0, len(data) - 1, n_target)
        data = np.interp(indices, np.arange(len(data)), data)
    return data


def apply_pitch_shift(audio, semitones, sr=SR):
    """Simple pitch shift using resampling.

    Positive semitones = higher pitch, negative = lower.
    Uses linear interpolation resampling.
    """
    if semitones == 0:
        return audio
    ratio = 2.0 ** (semitones / 12.0)
    n_original = len(audio)
    n_new = int(n_original / ratio)
    indices = np.linspace(0, n_original - 1, n_new)
    shifted = np.interp(indices, np.arange(n_original), audio)
    # Resample back to original length to preserve duration
    indices2 = np.linspace(0, len(shifted) - 1, n_original)
    return np.interp(indices2, np.arange(len(shifted)), shifted)


def apply_jitter(audio, depth=0.02, rate=8.0, sr=SR):
    """Add periodic F0 jitter (frequency micro-perturbation).

    Simulates vocal instability in stressed speech.
    """
    n = len(audio)
    t = np.linspace(0, n / sr, n, endpoint=False)
    # Jitter modulation
    jitter_mod = (
        1.0
        + depth * np.sin(2 * np.pi * rate * t)
        + depth * 0.5 * np.sin(2 * np.pi * rate * 1.7 * t + 1.3)
    )
    # Apply via phase modulation (approximate)
    phase = np.cumsum(jitter_mod) / sr * 2 * np.pi
    # Apply to analytic signal (simplified: just amplitude modulate + slight frequency shift)
    mod_signal = 1.0 + depth * 2 * np.sin(2 * np.pi * rate * t)
    return audio * mod_signal


def apply_shimmer(audio, depth=0.03, rate=5.0, sr=SR):
    """Add amplitude shimmer (energy micro-perturbation).

    Simulates vocal energy instability.
    """
    n = len(audio)
    t = np.linspace(0, n / sr, n, endpoint=False)
    shimmer = (
        1.0
        + depth * np.sin(2 * np.pi * rate * t)
        + depth * 0.5 * np.sin(2 * np.pi * rate * 2.3 * t + 0.7)
    )
    return audio * shimmer


def add_silence(duration_s, sr=SR):
    """Create silence."""
    return np.zeros(int(duration_s * sr))


def pad_or_trim(audio, target_len):
    """Pad with silence or trim to target length."""
    if len(audio) < target_len:
        audio = np.concatenate([audio, np.zeros(target_len - len(audio))])
    else:
        audio = audio[:target_len]
    return audio


def generate_ambient_living_room(duration_s, sr=SR):
    """Generate living room ambient: low hum + air conditioning + subtle air flow."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)

    # Low frequency building vibration
    rumble = 0.08 * np.sin(2 * np.pi * 40 * t)
    rumble += 0.06 * np.sin(2 * np.pi * 55 * t)
    # Mains hum at 120Hz (electrical appliances)
    hum = 0.05 * np.sin(2 * np.pi * 120 * t)
    # Air conditioning / ventilation noise (filtered white noise)
    noise = np.random.randn(n) * 0.15
    b, a = butter(4, 800 / (sr / 2), btype="low")
    noise = lfilter(b, a, noise)
    # Very subtle high-frequency air hiss
    hiss = np.random.randn(n) * 0.04
    b2, a2 = butter(4, 6000 / (sr / 2), btype="high")
    hiss = lfilter(b2, a2, hiss)
    # Distant fridge compressor
    fridge = 0.02 * np.sin(2 * np.pi * 100 * t)
    # Combine
    ambient = rumble + hum + noise + hiss + fridge
    # Slow amplitude modulation for natural variation
    mod = 1 + 0.08 * np.sin(2 * np.pi * 0.2 * t)
    ambient = ambient * mod
    return ambient


def generate_far_end_speech(duration_s, start_time=2.0, sr=SR):
    """Generate far-end telephone speech: bandpass-filtered noise with speech rhythm.

    Must NOT contain identifiable speech content.
    Sounds like someone talking on the other end of a phone call, but you can't make out words.
    """
    n = int(duration_s * sr)
    audio = np.zeros(n)

    start_idx = int(start_time * sr)
    if start_idx >= n:
        return audio

    active_n = n - start_idx
    active_t = np.linspace(0, active_n / sr, active_n, endpoint=False)

    # Base noise bed
    base = np.random.randn(active_n) * 0.08
    # Bandpass to telephone quality (300-3400Hz)
    b_low, a_low = butter(4, 300 / (sr / 2), btype="high")
    base = lfilter(b_low, a_low, base)
    b_high, a_high = butter(4, 3400 / (sr / 2), btype="low")
    base = lfilter(b_high, a_high, base)

    # Speech-like rhythm modulation (syllable rate ~4Hz with variation)
    syllable1 = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * active_t)
    syllable2 = 0.5 + 0.5 * np.sin(2 * np.pi * 3.3 * active_t + 1.5)
    syllable3 = 0.5 + 0.5 * np.sin(2 * np.pi * 5.2 * active_t + 0.8)
    rhythm = 0.3 + 0.7 * syllable1 * syllable2 * syllable3

    far_end = base * rhythm

    # Add occasional voice-like bursts (brief formant-ish noises)
    burst_times_rel = [0.5, 3.0, 6.0, 9.0, 11.5]
    for bt in burst_times_rel:
        bt_idx = int(bt * sr)
        burst_len = int(0.3 * sr)
        if bt_idx + burst_len > active_n:
            burst_len = active_n - bt_idx
        if burst_len <= 0:
            continue
        burst_t = np.linspace(0, burst_len / sr, burst_len, endpoint=False)
        burst = np.random.randn(burst_len) * 0.12
        env = np.exp(-burst_t * 4)
        env[: int(0.02 * sr)] = np.linspace(0, 1, int(0.02 * sr))
        burst = burst * env
        b_b1, a_b1 = butter(4, 500 / (sr / 2), btype="high")
        burst = lfilter(b_b1, a_b1, burst)
        b_b2, a_b2 = butter(4, 2800 / (sr / 2), btype="low")
        burst = lfilter(b_b2, a_b2, burst)
        far_end[bt_idx : bt_idx + burst_len] += burst

    # Fade in
    fade_in_len = int(0.2 * sr)
    if fade_in_len > active_n:
        fade_in_len = active_n
    far_end[:fade_in_len] *= np.linspace(0, 1, fade_in_len)

    # Normalize
    peak = np.max(np.abs(far_end))
    if peak > 0.5:
        far_end = far_end * (0.5 / peak)

    audio[start_idx:] = far_end
    return audio


def process_elderly_voice(tts_audio, role, sr=SR):
    """Apply DSP processing to TTS audio to simulate elderly voice characteristics.

    Normal: pitch down 3 semitones, slight jitter
    Attention: pitch down 2 semitones, moderate jitter
    Arousal: pitch down 1 semitone, strong jitter + shimmer
    Stress: pitch down 0.5 semitones, strong jitter + shimmer + energy boost
    """
    if role == "normal":
        # Calm, low-pitched elderly voice
        audio = apply_pitch_shift(tts_audio, -3.0)
        audio = apply_jitter(audio, depth=0.008, rate=6.0)

    elif role == "attention":
        # Slightly higher pitch, beginning to sound alert
        audio = apply_pitch_shift(tts_audio, -2.0)
        audio = apply_jitter(audio, depth=0.015, rate=7.0)
        # Slight energy boost
        audio = audio * 1.15

    elif role == "arousal":
        # Higher pitch, more jitter, faster feel
        audio = apply_pitch_shift(tts_audio, -1.0)
        audio = apply_jitter(audio, depth=0.025, rate=8.5)
        audio = apply_shimmer(audio, depth=0.02, rate=5.5)
        audio = audio * 1.25

    elif role == "stress":
        # Highest pitch, strong instability
        audio = apply_pitch_shift(tts_audio, -0.5)
        audio = apply_jitter(audio, depth=0.035, rate=9.0)
        audio = apply_shimmer(audio, depth=0.03, rate=6.0)
        audio = audio * 1.30
    else:
        audio = tts_audio

    return audio


def build_voice_track(segments_data, use_stressed=False, sr=SR, total_duration=15.0):
    """Build the complete elderly voice track from TTS segments.

    Case A (use_stressed=False): Only normal segment repeated/extended to fill 0-13s
    Case B (use_stressed=True): Normal(0-6s) -> Attention(6-9s) -> Arousal(9-12.5s) -> Stress(12.5-15s)
    """
    total_n = int(total_duration * sr)
    track = np.zeros(total_n)

    if not use_stressed:
        # Case A: normal conversation throughout
        # Use seg1 (normal) for 0-6s, then silence 6-9s (listening),
        # then seg1 again for 9-13s, then silence
        seg = segments_data[0]  # normal
        seg_audio = seg["processed"]

        # Place first utterance at 0.5s
        pos = int(0.5 * sr)
        end = min(pos + len(seg_audio), total_n)
        track[pos:end] = seg_audio[: end - pos]

        # Silence (listening) 6-9s
        # Place second normal utterance at 9s (repeat with slight variation)
        pos2 = int(9.0 * sr)
        # Use a portion of the normal segment
        seg_repeat = seg_audio[: int(3.5 * sr)]
        end2 = min(pos2 + len(seg_repeat), total_n)
        track[pos2:end2] = seg_repeat[: end2 - pos2]

    else:
        # Case B: normal -> attention -> arousal -> stress
        # Timeline:
        # 0.5-6s: normal (seg1)
        # 6.5-9s: attention (seg2)
        # 9.5-12.5s: arousal (seg3)
        # 12.5-15s: stress (seg4)

        positions = [0.5, 6.5, 9.5, 12.5]
        for i, seg in enumerate(segments_data):
            pos = int(positions[i] * sr)
            seg_audio = seg["processed"]
            end = min(pos + len(seg_audio), total_n)
            track[pos:end] = seg_audio[: end - pos]

    return track


def generate_micro_events(duration_s, sr=SR):
    """Generate subtle physical movement sounds.

    clothing_move at 6.3s, body_shift at 10.2s
    """
    n = int(duration_s * sr)
    audio = np.zeros(n)

    # Clothing rustle at 6.3s
    t1 = 6.3
    idx1 = int(t1 * sr)
    len1 = int(0.5 * sr)
    if idx1 + len1 < n:
        rustle = np.random.randn(len1) * 0.3
        b, a = butter(4, 2000 / (sr / 2), btype="low")
        rustle = lfilter(b, a, rustle)
        env1 = np.exp(-np.linspace(0, 2.5, len1))
        env1[: int(0.05 * sr)] = np.linspace(0, 1, int(0.05 * sr))
        audio[idx1 : idx1 + len1] = rustle * env1

    # Body shift at 10.2s
    t2 = 10.2
    idx2 = int(t2 * sr)
    len2 = int(0.7 * sr)
    if idx2 + len2 < n:
        shift = np.random.randn(len2) * 0.25
        b, a = butter(4, 500 / (sr / 2), btype="low")
        shift = lfilter(b, a, shift)
        env2 = np.exp(-np.linspace(0, 2.0, len2))
        env2[: int(0.08 * sr)] = np.linspace(0, 1, int(0.08 * sr))
        audio[idx2 : idx2 + len2] = shift * env2

    return audio


def normalize_to_target(audio, target_peak=0.7):
    """Normalize audio to a target peak amplitude."""
    peak = np.max(np.abs(audio))
    if peak > 1e-8:
        audio = audio * (target_peak / peak)
    return audio


def mix_at_gain(audio, gain_db):
    """Apply gain in dB and return."""
    return audio * db_to_linear(gain_db)


def save_wav(filepath, audio, sr=SR):
    """Save as 32-bit float WAV."""
    audio_f32 = np.float32(audio)
    wavfile.write(filepath, sr, audio_f32)
    peak_db = 20 * np.log10(max(np.max(np.abs(audio)), 1e-10))
    print(f"  Saved: {filepath} ({len(audio) / sr:.1f}s, peak={peak_db:.1f}dB)")


def generate_all():
    """Generate all audio assets for telephone_risk V3."""
    print("=" * 60)
    print("CI Audio Factory V3 - telephone_risk")
    print(f"Sample rate: {SR} Hz, Mono, 32-bit float")
    print(f"Architecture: Acoustic state transition (Normal -> Stressed)")
    print("=" * 60)

    # --- Load and process TTS segments ---
    print("\n--- Loading TTS segments ---")
    segments = []
    for seg_info in TTS_SEGMENTS:
        filepath = os.path.join(TTS_DIR, seg_info["file"])
        print(f"  Loading: {seg_info['file']} (role={seg_info['role']})")
        raw = load_tts_wav(filepath, SR)
        processed = process_elderly_voice(raw, seg_info["role"])
        segments.append(
            {
                "role": seg_info["role"],
                "raw": raw,
                "processed": processed,
            }
        )
        print(f"    Duration: {len(raw) / SR:.2f}s -> {len(processed) / SR:.2f}s")

    # --- Generate ambient ---
    print("\n--- Generating ambient room tone ---")
    ambient = generate_ambient_living_room(15.0)
    ambient = normalize_to_target(ambient, target_peak=0.25)

    # --- Generate far-end speech ---
    print("\n--- Generating far-end speech ---")
    far_end = generate_far_end_speech(15.0, start_time=2.0)
    far_end = normalize_to_target(far_end, target_peak=0.5)

    # --- Generate micro events ---
    print("\n--- Generating micro events ---")
    micro = generate_micro_events(15.0)
    micro = normalize_to_target(micro, target_peak=0.4)

    # --- Save individual assets ---
    print("\n--- Saving individual assets ---")
    audio_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    save_wav(os.path.join(audio_dir, "ambient_living_room.wav"), ambient)
    save_wav(os.path.join(audio_dir, "far_end_speech.wav"), far_end)
    save_wav(os.path.join(audio_dir, "micro_events.wav"), micro)

    # Save voice tracks
    voice_normal = build_voice_track(segments, use_stressed=False)
    voice_normal = normalize_to_target(voice_normal, target_peak=0.6)
    save_wav(os.path.join(audio_dir, "voice_normal.wav"), voice_normal)

    voice_stressed = build_voice_track(segments, use_stressed=True)
    voice_stressed = normalize_to_target(voice_stressed, target_peak=0.6)
    save_wav(os.path.join(audio_dir, "voice_stressed.wav"), voice_stressed)

    # --- Build Case A mix (normal conversation) ---
    print("\n--- Building Case A mix (normal conversation -> LOW) ---")
    case_a = (
        mix_at_gain(ambient, GAIN_AMBIENT_DB)
        + mix_at_gain(voice_normal, GAIN_VOICE_DB)
        + mix_at_gain(far_end, GAIN_FAREND_DB)
        + mix_at_gain(micro, GAIN_MICRO_DB)
    )
    # Normalize final mix
    peak = np.max(np.abs(case_a))
    if peak > 0.85:
        case_a = case_a * (0.85 / peak)

    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    os.makedirs(mix_dir, exist_ok=True)
    save_wav(os.path.join(mix_dir, "case_a_mix.wav"), case_a)

    # --- Build Case B mix (normal -> stressed transition) ---
    print("\n--- Building Case B mix (normal -> stressed -> RISK_SIGNAL) ---")
    case_b = (
        mix_at_gain(ambient, GAIN_AMBIENT_DB)
        + mix_at_gain(voice_stressed, GAIN_VOICE_DB)
        + mix_at_gain(far_end, GAIN_FAREND_DB)
        + mix_at_gain(micro, GAIN_MICRO_DB)
    )
    peak = np.max(np.abs(case_b))
    if peak > 0.85:
        case_b = case_b * (0.85 / peak)

    save_wav(os.path.join(mix_dir, "case_b_mix.wav"), case_b)

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Generation complete.")
    print(f"\nCase A (15s): ambient + normal conversation + far-end + micro")
    print(f"  -> voice_state: NORMAL -> risk: LOW")
    print(f"\nCase B (15s): ambient + normal(0-6s)->stressed(6-15s) + far-end + micro")
    print(f"  -> voice_state: ELEVATED_STRESS -> risk: RISK_SIGNAL")
    print(f"\nAssets saved to:")
    print(f"  audio/ambient_living_room.wav")
    print(f"  audio/far_end_speech.wav")
    print(f"  audio/micro_events.wav")
    print(f"  audio/voice_normal.wav")
    print(f"  audio/voice_stressed.wav")
    print(f"  audio_mix/case_a_mix.wav")
    print(f"  audio_mix/case_b_mix.wav")
    print("=" * 60)


if __name__ == "__main__":
    generate_all()

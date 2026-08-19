#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI Audio Factory for evidence_insufficient
Generates 3 independent ambient audio tracks for Act A/B/C.

Act A (6s):  Daytime corridor ambient  (HVAC hum + low rumble + distant traffic)
Act B (7s):  Dusk corridor ambient     (quieter, dimmer acoustic, less traffic)
Act C (7s):  Night corridor ambient    (very quiet + insect chirping + occasional distant sound)

All tracks are pure ambient — no events, no footsteps, no doorbell.
This is a negative case: audio provides environmental realism only, does not participate in detection.
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter
import os

SR = 48000  # Sample rate
np.random.seed(42)  # Fixed seed for reproducibility
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# Ambient Generators
# ============================================================


def generate_ambient_day(duration_s, sr=SR):
    """Daytime corridor ambient: HVAC hum + low rumble + distant traffic + air movement."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)

    # Low frequency rumble (building vibration)
    rumble = 0.08 * np.sin(2 * np.pi * 45 * t)
    rumble += 0.06 * np.sin(2 * np.pi * 60 * t)

    # HVAC hum at 120Hz (mains harmonic)
    hum = 0.04 * np.sin(2 * np.pi * 120 * t)
    hum += 0.02 * np.sin(2 * np.pi * 240 * t)

    # Filtered white noise for air movement
    noise = np.random.randn(n) * 0.03
    b, a = butter(4, 500 / (sr / 2), btype="low")
    noise = lfilter(b, a, noise)

    # Subtle high-frequency air hiss
    hiss = np.random.randn(n) * 0.005
    b2, a2 = butter(4, 4000 / (sr / 2), btype="high")
    hiss = lfilter(b2, a2, hiss)

    # Distant traffic rumble (very low, filtered noise modulated slowly)
    traffic = np.random.randn(n) * 0.02
    b3, a3 = butter(4, 200 / (sr / 2), btype="low")
    traffic = lfilter(b3, a3, traffic)
    traffic_mod = 1 + 0.3 * np.sin(2 * np.pi * 0.15 * t)
    traffic = traffic * traffic_mod

    # Combine
    ambient = rumble + hum + noise + hiss + traffic

    # Slow amplitude modulation for natural variation
    mod = 1 + 0.08 * np.sin(2 * np.pi * 0.3 * t)
    ambient = ambient * mod

    return ambient


def generate_ambient_dusk(duration_s, sr=SR):
    """Dusk corridor ambient: quieter than day, less traffic, dimmer acoustic quality."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)

    # Low frequency rumble (quieter than day)
    rumble = 0.06 * np.sin(2 * np.pi * 45 * t)
    rumble += 0.04 * np.sin(2 * np.pi * 60 * t)

    # HVAC hum (dimmer, slightly lower)
    hum = 0.03 * np.sin(2 * np.pi * 120 * t)
    hum += 0.015 * np.sin(2 * np.pi * 240 * t)

    # Filtered white noise (less air movement)
    noise = np.random.randn(n) * 0.02
    b, a = butter(4, 500 / (sr / 2), btype="low")
    noise = lfilter(b, a, noise)

    # Very subtle high-frequency hiss
    hiss = np.random.randn(n) * 0.003
    b2, a2 = butter(4, 4000 / (sr / 2), btype="high")
    hiss = lfilter(b2, a2, hiss)

    # Distant traffic (much less, fading)
    traffic = np.random.randn(n) * 0.01
    b3, a3 = butter(4, 200 / (sr / 2), btype="low")
    traffic = lfilter(b3, a3, traffic)
    traffic_mod = 1 + 0.2 * np.sin(2 * np.pi * 0.12 * t)
    traffic = traffic * traffic_mod

    # Combine
    ambient = rumble + hum + noise + hiss + traffic

    # Slower, calmer modulation
    mod = 1 + 0.06 * np.sin(2 * np.pi * 0.25 * t)
    ambient = ambient * mod

    return ambient


def generate_ambient_night(duration_s, sr=SR):
    """Night corridor ambient: very quiet + insect chirping + occasional distant sound."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)

    # Very low rumble (minimal building vibration at night)
    rumble = 0.03 * np.sin(2 * np.pi * 45 * t)
    rumble += 0.02 * np.sin(2 * np.pi * 60 * t)

    # HVAC hum (minimal, some systems off at night)
    hum = 0.015 * np.sin(2 * np.pi * 120 * t)

    # Very quiet filtered noise
    noise = np.random.randn(n) * 0.012
    b, a = butter(4, 400 / (sr / 2), btype="low")
    noise = lfilter(b, a, noise)

    # Extremely subtle hiss
    hiss = np.random.randn(n) * 0.002
    b2, a2 = butter(4, 4000 / (sr / 2), btype="high")
    hiss = lfilter(b2, a2, hiss)

    ambient = rumble + hum + noise + hiss

    # Insect chirping (crickets) — the key night element
    insects = generate_insect_chirping(duration_s, sr)

    # Occasional distant sound (maybe a distant car or dog)
    distant = generate_distant_sounds(duration_s, sr)

    # Combine all
    ambient = ambient + insects + distant

    # Very slow, calm modulation
    mod = 1 + 0.04 * np.sin(2 * np.pi * 0.2 * t)
    ambient = ambient * mod

    return ambient


def generate_insect_chirping(duration_s, sr=SR):
    """Generate cricket/insect chirping sounds — intermittent high-frequency pulses."""
    n = int(duration_s * sr)
    audio = np.zeros(n)
    t = np.linspace(0, duration_s, n, endpoint=False)

    # Cricket chirp pattern: bursts of high-freq pulses with gaps
    # Typical cricket: ~4-5 kHz, chirp rate ~2-3 chirps/sec, burst pattern

    chirp_freq = 4500  # Hz — cricket fundamental
    chirp_rate = 2.5  # chirps per second
    chirp_duration = 0.015  # each chirp is ~15ms
    burst_duration = 0.8  # burst of chirps lasts ~0.8s
    burst_gap = 1.5  # gap between bursts ~1.5s

    current_time = 0.5  # start after 0.5s

    while current_time < duration_s:
        # Generate a burst of chirps
        burst_end = min(current_time + burst_duration, duration_s)
        while current_time < burst_end:
            if current_time >= duration_s:
                break
            start_idx = int(current_time * sr)
            chirp_len = int(chirp_duration * sr)
            if start_idx + chirp_len > n:
                chirp_len = n - start_idx
            if chirp_len <= 0:
                break

            # Single chirp: amplitude-modulated sine wave
            chirp_t = np.linspace(0, chirp_duration, chirp_len, endpoint=False)
            # Carrier at chirp_freq with fast onset/offset
            envelope = np.exp(-chirp_t * 80) * (1 - np.exp(-chirp_t * 200))
            chirp = 0.06 * np.sin(2 * np.pi * chirp_freq * chirp_t) * envelope
            # Add a harmonic for richness
            chirp += 0.02 * np.sin(2 * np.pi * chirp_freq * 1.5 * chirp_t) * envelope

            audio[start_idx : start_idx + chirp_len] += chirp

            # Move to next chirp
            current_time += 1.0 / chirp_rate

        # Gap between bursts (with slight randomization)
        current_time = burst_end + burst_gap + np.random.uniform(-0.3, 0.3)

    # Apply bandpass filter to make it sound more natural
    b, a = butter(4, [3000 / (sr / 2), 8000 / (sr / 2)], btype="band")
    audio = lfilter(b, a, audio)

    # Apply overall gain reduction — insects are subtle background
    audio = audio * 0.4

    return audio


def generate_distant_sounds(duration_s, sr=SR):
    """Generate occasional distant sounds for night scene."""
    n = int(duration_s * sr)
    audio = np.zeros(n)

    # One distant low-frequency sound (maybe distant door or vehicle)
    if duration_s > 3.0:
        sound_time = 2.5 + np.random.uniform(0, 1.0)
        start_idx = int(sound_time * sr)
        sound_len = int(1.5 * sr)
        if start_idx + sound_len <= n:
            t = np.linspace(0, 1.5, sound_len, endpoint=False)
            # Low rumble with slow onset and decay
            envelope = np.exp(-t * 1.5) * (1 - np.exp(-t * 3))
            distant = 0.015 * np.sin(2 * np.pi * 80 * t) * envelope
            distant += 0.008 * np.random.randn(sound_len) * envelope
            b, a = butter(4, 300 / (sr / 2), btype="low")
            distant = lfilter(b, a, distant)
            audio[start_idx : start_idx + sound_len] += distant

    return audio


# ============================================================
# Mixing & Utility
# ============================================================


def normalize_to_target(audio, target_db=-28.0, sr=SR):
    """Normalize audio to target dB level."""
    # Calculate RMS
    rms = np.sqrt(np.mean(audio**2))
    if rms < 1e-10:
        return audio

    # Target RMS from dB
    target_rms = 10 ** (target_db / 20.0)

    # Scale factor
    scale = target_rms / rms
    audio_normalized = audio * scale

    # Prevent clipping
    peak = np.max(np.abs(audio_normalized))
    if peak > 0.95:
        audio_normalized = audio_normalized * (0.95 / peak)

    return audio_normalized


def save_wav(filepath, audio, sr=SR):
    """Save as 32-bit float mono WAV (avoids int scaling issues)."""
    audio_float32 = audio.astype(np.float32)
    wavfile.write(filepath, sr, audio_float32)
    print(f"  Saved: {filepath} ({len(audio) / sr:.1f}s)")


# ============================================================
# Act Generators
# ============================================================


def generate_act_a():
    """Act A (6s): Daytime corridor ambient."""
    duration = 6.0
    print("=== Act A (6s) - Daytime Corridor Ambient ===")
    ambient = generate_ambient_day(duration)
    # Normalize to -28dB
    mixed = normalize_to_target(ambient, target_db=-28.0)

    # Save assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(asset_dir, exist_ok=True)
    save_wav(os.path.join(asset_dir, "ambient_corridor_day.wav"), ambient)

    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    os.makedirs(mix_dir, exist_ok=True)
    save_wav(os.path.join(mix_dir, "act_a_mix.wav"), mixed)
    return mixed


def generate_act_b():
    """Act B (7s): Dusk corridor ambient."""
    duration = 7.0
    print("=== Act B (7s) - Dusk Corridor Ambient ===")
    ambient = generate_ambient_dusk(duration)
    # Normalize to -28dB
    mixed = normalize_to_target(ambient, target_db=-28.0)

    # Save assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    save_wav(os.path.join(asset_dir, "ambient_corridor_dusk.wav"), ambient)

    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    save_wav(os.path.join(mix_dir, "act_b_mix.wav"), mixed)
    return mixed


def generate_act_c():
    """Act C (7s): Night corridor ambient + insect chirping."""
    duration = 7.0
    print("=== Act C (7s) - Night Corridor Ambient + Insect Chirping ===")
    ambient = generate_ambient_night(duration)
    # Normalize to -28dB
    mixed = normalize_to_target(ambient, target_db=-28.0)

    # Save assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    save_wav(os.path.join(asset_dir, "ambient_corridor_night.wav"), ambient)

    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    save_wav(os.path.join(mix_dir, "act_c_mix.wav"), mixed)
    return mixed


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("CI Audio Factory - evidence_insufficient")
    print(f"Sample rate: {SR} Hz, Mono, 32-bit float")
    print(f"Fixed seed: 42")
    print()

    act_a = generate_act_a()
    print()
    act_b = generate_act_b()
    print()
    act_c = generate_act_c()

    print()
    print("All audio tracks generated successfully.")
    print(f"Act A: {len(act_a) / SR:.1f}s (daytime corridor ambient, -28dB)")
    print(f"Act B: {len(act_b) / SR:.1f}s (dusk corridor ambient, -28dB)")
    print(
        f"Act C: {len(act_c) / SR:.1f}s (night corridor ambient + insect chirping, -28dB)"
    )

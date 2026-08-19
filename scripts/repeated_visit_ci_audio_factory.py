#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI Audio Factory for repeated_visit
Generates 3 independent audio tracks for Act 1/2/3.

Act 1 (9s):  ambient + footsteps_in + doorbell(~5.2s) + silence_response + footsteps_out
Act 2 (9s):  ambient + footsteps_in + footsteps_stop + subtle_env + footsteps_out  (NO doorbell)
Act 3 (10s): ambient + footsteps_in + footsteps_stop + subtle_env + footsteps_out  (NO doorbell, NO head_turn sfx)
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter
import os

SR = 48000  # Sample rate
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_ambient(duration_s, sr=SR):
    """Generate ambient corridor noise: low rumble + HVAC hum + subtle echo."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Low frequency rumble (building vibration)
    rumble = 0.02 * np.sin(2 * np.pi * 45 * t)
    rumble += 0.015 * np.sin(2 * np.pi * 60 * t)
    # HVAC hum at 120Hz (mains harmonic)
    hum = 0.01 * np.sin(2 * np.pi * 120 * t)
    # White noise filtered to simulate air movement
    noise = np.random.randn(n) * 0.008
    # Low-pass filter the noise
    b, a = butter(4, 500 / (sr / 2), btype="low")
    noise = lfilter(b, a, noise)
    # Very subtle high-frequency air hiss
    hiss = np.random.randn(n) * 0.002
    b2, a2 = butter(4, 4000 / (sr / 2), btype="high")
    hiss = lfilter(b2, a2, hiss)
    # Combine
    ambient = rumble + hum + noise + hiss
    # Add slow amplitude modulation for natural variation
    mod = 1 + 0.1 * np.sin(2 * np.pi * 0.3 * t)
    ambient = ambient * mod
    return ambient


def generate_footsteps(duration_s, sr=SR, start_intensity=0.0, end_intensity=1.0):
    """Generate footsteps: sequence of impact + decay."""
    n = int(duration_s * sr)
    audio = np.zeros(n)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Footstep interval ~0.6s (normal walking pace)
    step_interval = 0.6
    num_steps = int(duration_s / step_interval) + 1
    for i in range(num_steps):
        step_time = i * step_interval
        if step_time >= duration_s:
            break
        # Intensity ramps from start to end (approaching = louder)
        progress = step_time / duration_s if duration_s > 0 else 1.0
        intensity = start_intensity + (end_intensity - start_intensity) * progress
        step_start = int(step_time * sr)
        step_len = int(0.15 * sr)  # 150ms per step
        if step_start + step_len > n:
            step_len = n - step_start
        if step_len <= 0:
            continue
        # Step = quick impact + fast decay
        step_t = np.linspace(0, step_len / sr, step_len, endpoint=False)
        # Impact: mix of low thud + mid click
        thud = 0.15 * intensity * np.sin(2 * np.pi * 80 * step_t) * np.exp(-step_t * 30)
        click = (
            0.08 * intensity * np.sin(2 * np.pi * 200 * step_t) * np.exp(-step_t * 50)
        )
        noise_comp = np.random.randn(step_len) * 0.05 * intensity * np.exp(-step_t * 40)
        step = thud + click + noise_comp
        audio[step_start : step_start + step_len] = step
    return audio


def generate_doorbell(duration_s=1.5, sr=SR):
    """Generate electronic doorbell: two-tone 'ding-dong'."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)
    audio = np.zeros(n)
    # First tone: 'ding' (880 Hz, 0.5s)
    ding_end = int(0.5 * sr)
    ding_t = t[:ding_end]
    ding = 0.4 * np.sin(2 * np.pi * 880 * ding_t) * np.exp(-ding_t * 4)
    # Add harmonic
    ding += 0.1 * np.sin(2 * np.pi * 1760 * ding_t) * np.exp(-ding_t * 6)
    audio[:ding_end] = ding
    # Second tone: 'dong' (660 Hz, 0.8s, starts at 0.5s)
    dong_start = int(0.5 * sr)
    dong_t = t[dong_start:]
    dong = 0.35 * np.sin(2 * np.pi * 660 * dong_t) * np.exp(-dong_t * 3)
    dong += 0.08 * np.sin(2 * np.pi * 1320 * dong_t) * np.exp(-dong_t * 5)
    audio[dong_start:] = dong
    return audio


def generate_silence_response(duration_s=3.0, sr=SR):
    """Generate 'nobody answers' silence: very faint ambient."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Extremely quiet background
    audio = np.random.randn(n) * 0.003
    b, a = butter(4, 800 / (sr / 2), btype="low")
    audio = lfilter(b, a, audio)
    # Very faint distant sound (barely audible)
    distant = 0.002 * np.sin(2 * np.pi * 100 * t)
    audio += distant
    return audio


def generate_subtle_env(duration_s=4.0, sr=SR):
    """Generate very subtle environmental sounds (distant life sounds, not abnormal)."""
    n = int(duration_s * sr)
    t = np.linspace(0, duration_s, n, endpoint=False)
    audio = np.zeros(n)
    # Very faint rustling (clothing fabric micro-sound)
    rustle_times = [0.5, 1.8, 3.2]
    for rt in rustle_times:
        if rt >= duration_s:
            continue
        start = int(rt * sr)
        rustle_len = int(0.3 * sr)
        if start + rustle_len > n:
            rustle_len = n - start
        if rustle_len <= 0:
            continue
        rustle_t = np.linspace(0, rustle_len / sr, rustle_len, endpoint=False)
        rustle = np.random.randn(rustle_len) * 0.008 * np.exp(-rustle_t * 3)
        b, a = butter(4, 2000 / (sr / 2), btype="low")
        rustle = lfilter(b, a, rustle)
        audio[start : start + rustle_len] = rustle
    # Distant faint sound (maybe a bird or distant door)
    if duration_s > 2.0:
        distant_start = int(1.5 * sr)
        distant_len = int(0.5 * sr)
        if distant_start + distant_len <= n:
            dt = np.linspace(0, 0.5, distant_len, endpoint=False)
            distant = 0.005 * np.sin(2 * np.pi * 300 * dt) * np.exp(-dt * 2)
            audio[distant_start : distant_start + distant_len] += distant
    return audio


def generate_footsteps_out(duration_s, sr=SR):
    """Generate footsteps walking away: decreasing intensity."""
    return generate_footsteps(duration_s, sr, start_intensity=1.0, end_intensity=0.1)


def mix_audio(tracks, sr=SR):
    """Mix multiple audio tracks. Each track is (audio_array, start_time_s)."""
    max_len = max(int(len(audio) + start * sr) for audio, start in tracks)
    mixed = np.zeros(max_len)
    for audio, start in tracks:
        start_idx = int(start * sr)
        end_idx = start_idx + len(audio)
        if end_idx > max_len:
            end_idx = max_len
            audio = audio[: end_idx - start_idx]
        mixed[start_idx:end_idx] += audio
    # Normalize to prevent clipping
    peak = np.max(np.abs(mixed))
    if peak > 0.95:
        mixed = mixed * (0.95 / peak)
    return mixed


def save_wav(filepath, audio, sr=SR):
    """Save as 24-bit mono WAV."""
    audio_int32 = np.int32(audio * 8388607)
    wavfile.write(filepath, sr, audio_int32)
    print(f"  Saved: {filepath} ({len(audio) / sr:.1f}s)")


def generate_act1():
    """Act 1 (9s): ambient + footsteps_in + doorbell(~5.2s) + silence + footsteps_out"""
    duration = 9.0
    print("=== Act 1 (9s) ===")
    ambient = generate_ambient(duration)
    footsteps_in = generate_footsteps(3.0, start_intensity=0.1, end_intensity=0.8)
    doorbell = generate_doorbell(1.5)
    silence = generate_silence_response(2.5)
    footsteps_out = generate_footsteps_out(2.0)
    # Mix: ambient(0s) + footsteps_in(0s) + doorbell(5.2s) + silence(6.7s) + footsteps_out(7.5s)
    # Adjusted: doorbell at ~5.0s (person presses at ~5s), silence after, footsteps_out at ~7s
    tracks = [
        (ambient, 0.0),
        (footsteps_in, 0.0),
        (doorbell, 5.0),
        (silence, 6.5),
        (footsteps_out, 7.0),
    ]
    mixed = mix_audio(tracks)
    # Save individual assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(asset_dir, exist_ok=True)
    save_wav(os.path.join(asset_dir, "ambient_9s.wav"), ambient)
    save_wav(os.path.join(asset_dir, "footsteps_in.wav"), footsteps_in)
    save_wav(os.path.join(asset_dir, "doorbell.wav"), doorbell)
    save_wav(os.path.join(asset_dir, "silence_response.wav"), silence)
    save_wav(os.path.join(asset_dir, "footsteps_out_short.wav"), footsteps_out)
    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    os.makedirs(mix_dir, exist_ok=True)
    save_wav(os.path.join(mix_dir, "act1_mix.wav"), mixed)
    return mixed


def generate_act2():
    """Act 2 (9s): ambient + footsteps_in + footsteps_stop + subtle_env + footsteps_out (NO doorbell)"""
    duration = 9.0
    print("=== Act 2 (9s) ===")
    ambient = generate_ambient(duration)
    footsteps_in = generate_footsteps(3.0, start_intensity=0.1, end_intensity=0.8)
    subtle_env = generate_subtle_env(4.0)
    footsteps_out = generate_footsteps_out(2.0)
    # Mix: ambient(0s) + footsteps_in(0s) + subtle_env(3s) + footsteps_out(7.5s)
    tracks = [
        (ambient, 0.0),
        (footsteps_in, 0.0),
        (subtle_env, 3.0),
        (footsteps_out, 7.5),
    ]
    mixed = mix_audio(tracks)
    # Save assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    save_wav(os.path.join(asset_dir, "subtle_env.wav"), subtle_env)
    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    save_wav(os.path.join(mix_dir, "act2_mix.wav"), mixed)
    return mixed


def generate_act3():
    """Act 3 (10s): ambient + footsteps_in + subtle_env + footsteps_out (NO doorbell, NO head_turn sfx)"""
    duration = 10.0
    print("=== Act 3 (10s) ===")
    ambient = generate_ambient(duration)
    footsteps_in = generate_footsteps(3.0, start_intensity=0.1, end_intensity=0.8)
    subtle_env = generate_subtle_env(5.0)  # Longer subtle env for extended dwell
    footsteps_out = generate_footsteps_out(2.5)
    # Mix: ambient(0s) + footsteps_in(0s) + subtle_env(3s) + footsteps_out(8s)
    tracks = [
        (ambient, 0.0),
        (footsteps_in, 0.0),
        (subtle_env, 3.0),
        (footsteps_out, 8.0),
    ]
    mixed = mix_audio(tracks)
    # Save assets
    asset_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(asset_dir, exist_ok=True)
    save_wav(os.path.join(asset_dir, "ambient_10s.wav"), ambient)
    save_wav(os.path.join(asset_dir, "footsteps_out_long.wav"), footsteps_out)
    # Save mix
    mix_dir = os.path.join(OUTPUT_DIR, "audio_mix")
    save_wav(os.path.join(mix_dir, "act3_mix.wav"), mixed)
    return mixed


if __name__ == "__main__":
    print("CI Audio Factory - repeated_visit")
    print(f"Sample rate: {SR} Hz, Mono, 24-bit")
    print()
    act1 = generate_act1()
    print()
    act2 = generate_act2()
    print()
    act3 = generate_act3()
    print()
    print("All audio tracks generated successfully.")
    print(f"Act 1: {len(act1) / SR:.1f}s (with doorbell at ~5.0s)")
    print(f"Act 2: {len(act2) / SR:.1f}s (NO doorbell)")
    print(f"Act 3: {len(act3) / SR:.1f}s (NO doorbell, NO head_turn sfx)")

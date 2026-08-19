#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI Audio Factory for stranger_visit
Generates all 8 audio assets and mixes them into a single track.
Output: stranger_visit/audio_mix/stranger_visit_mix.wav
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter
import os

SR = 48000  # Sample rate
DURATION = 33.5  # Total duration in seconds (matching video 33.34s)
N = int(SR * DURATION)

OUTPUT_DIR = "D:/Learning/AI视频/contest/stranger_visit/audio"
MIX_DIR = "D:/Learning/AI视频/contest/stranger_visit/audio_mix"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(MIX_DIR, exist_ok=True)


def save_wav(path, data, sr=SR):
    """Save float32 [-1, 1] audio to 16-bit PCM wav."""
    data = np.clip(data, -1.0, 1.0)
    data_int16 = (data * 32767).astype(np.int16)
    wavfile.write(path, sr, data_int16)
    print(f"  Saved: {path} ({len(data) / sr:.1f}s)")


def gen_noise(duration_s, sr=SR):
    """Generate white noise."""
    n = int(sr * duration_s)
    return np.random.randn(n).astype(np.float32)


def butter_lowpass(cutoff, sr=SR, order=4):
    nyq = 0.5 * sr
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype="low", analog=False)
    return b, a


def apply_lowpass(data, cutoff, sr=SR):
    b, a = butter_lowpass(cutoff, sr)
    return lfilter(b, a, data).astype(np.float32)


def butter_bandpass(low, high, sr=SR, order=4):
    nyq = 0.5 * sr
    b, a = butter(order, [low / nyq, high / nyq], btype="band", analog=False)
    return b, a


def apply_bandpass(data, low, high, sr=SR):
    b, a = butter_bandpass(low, high, sr)
    return lfilter(b, a, data).astype(np.float32)


def gen_env_ramp(n, attack_s=0.05, release_s=0.05, sr=SR):
    """Attack-release envelope."""
    env = np.ones(n, dtype=np.float32)
    attack_n = int(attack_s * sr)
    release_n = int(release_s * sr)
    if attack_n > 0:
        env[:attack_n] = np.linspace(0, 1, attack_n)
    if release_n > 0:
        env[-release_n:] = np.linspace(1, 0, release_n)
    return env


# ============================================================
# 1. ambient.wav - Environment background noise (full duration)
# ============================================================
print("[1/8] Generating ambient.wav...")
ambient = gen_noise(DURATION)
# Low-pass filter for a distant, muffled corridor sound
ambient = apply_lowpass(ambient, 800)
# Very low volume
ambient = ambient * 0.04
# Add a very subtle low rumble
rumble = np.sin(2 * np.pi * 50 * np.arange(N) / SR).astype(np.float32) * 0.015
ambient = ambient + rumble
save_wav(os.path.join(OUTPUT_DIR, "ambient.wav"), ambient)

# ============================================================
# 2. footsteps_in.wav - Footsteps approaching (5s-9s, ~4s)
# ============================================================
print("[2/8] Generating footsteps_in.wav...")
foot_dur = 4.0
foot_n = int(SR * foot_dur)
footsteps_in = np.zeros(foot_n, dtype=np.float32)
# Generate footsteps at ~2 steps/sec, each step is a short filtered noise burst
step_interval = 0.5  # seconds between steps
step_count = int(foot_dur / step_interval)
for i in range(step_count):
    t_start = i * step_interval
    # Each step: 0.15s burst of filtered noise
    step_dur = 0.15
    step_n = int(SR * step_dur)
    step = gen_noise(step_dur)
    # Bandpass filter for footstep on tile
    step = apply_bandpass(step, 200, 2500)
    # Envelope: quick attack, slower decay
    env = np.exp(-np.linspace(0, 5, step_n)).astype(np.float32)
    step = step * env * 0.25
    # Volume increases as person gets closer
    vol = 0.5 + 0.5 * (i / max(step_count - 1, 1))
    step = step * vol
    start_sample = int(t_start * SR)
    end_sample = min(start_sample + step_n, foot_n)
    footsteps_in[start_sample:end_sample] += step[: end_sample - start_sample]
save_wav(os.path.join(OUTPUT_DIR, "footsteps_in.wav"), footsteps_in)

# ============================================================
# 3. footsteps_stop.wav - Footsteps slowing and stopping (9s-12s, ~3s)
# ============================================================
print("[3/8] Generating footsteps_stop.wav...")
foot_stop_dur = 3.0
foot_stop_n = int(SR * foot_stop_dur)
footsteps_stop = np.zeros(foot_stop_n, dtype=np.float32)
# Last 2 steps, slowing down
step_times = [0.0, 0.8]  # 2 steps with increasing gap
for i, t_start in enumerate(step_times):
    step_dur = 0.15
    step_n = int(SR * step_dur)
    step = gen_noise(step_dur)
    step = apply_bandpass(step, 200, 2500)
    env = np.exp(-np.linspace(0, 5, step_n)).astype(np.float32)
    vol = 0.6 - 0.15 * i  # decreasing volume
    step = step * env * vol
    start_sample = int(t_start * SR)
    end_sample = min(start_sample + step_n, foot_stop_n)
    footsteps_stop[start_sample:end_sample] += step[: end_sample - start_sample]
save_wav(os.path.join(OUTPUT_DIR, "footsteps_stop.wav"), footsteps_stop)

# ============================================================
# 4. doorbell.wav - Single doorbell "ding-dong" (1.5s)
# ============================================================
print("[4/8] Generating doorbell.wav...")
bell_dur = 1.5
bell_n = int(SR * bell_dur)
doorbell = np.zeros(bell_n, dtype=np.float32)
# Two-tone doorbell: "ding" at 800Hz, "dong" at 600Hz
# Ding: 0.0s - 0.4s
ding_n = int(0.4 * SR)
ding_t = np.arange(ding_n) / SR
ding_freq = 800
ding = np.sin(2 * np.pi * ding_freq * ding_t).astype(np.float32)
# Add a harmonic
ding += 0.3 * np.sin(2 * np.pi * ding_freq * 2 * ding_t).astype(np.float32)
ding_env = np.exp(-ding_t * 4).astype(np.float32)
ding = ding * ding_env * 0.4
doorbell[:ding_n] += ding
# Short gap
gap_n = int(0.05 * SR)
# Dong: 0.45s - 1.5s
dong_start = ding_n + gap_n
dong_n = bell_n - dong_start
dong_t = np.arange(dong_n) / SR
dong_freq = 600
dong = np.sin(2 * np.pi * dong_freq * dong_t).astype(np.float32)
dong += 0.3 * np.sin(2 * np.pi * dong_freq * 2 * dong_t).astype(np.float32)
dong_env = np.exp(-dong_t * 3).astype(np.float32)
dong = dong * dong_env * 0.35
doorbell[dong_start:] += dong
save_wav(os.path.join(OUTPUT_DIR, "doorbell.wav"), doorbell)

# ============================================================
# 5. silence_response.wav - Quiet with faint ambient (6s)
# ============================================================
print("[5/8] Generating silence_response.wav...")
sil_dur = 6.0
sil_n = int(SR * sil_dur)
silence_response = gen_noise(sil_dur)
silence_response = apply_lowpass(silence_response, 400)
silence_response = silence_response * 0.02  # Very quiet
save_wav(os.path.join(OUTPUT_DIR, "silence_response.wav"), silence_response)

# ============================================================
# 6. subtle_movement.wav - Very faint clothing rustle (5s)
# ============================================================
print("[6/8] Generating subtle_movement.wav...")
sm_dur = 5.0
sm_n = int(SR * sm_dur)
subtle_movement = np.zeros(sm_n, dtype=np.float32)
# A few very faint rustle bursts
rustle_times = [0.3, 1.5, 2.8, 4.0]
for t_start in rustle_times:
    rustle_dur = 0.3
    rustle_n = int(SR * rustle_dur)
    rustle = gen_noise(rustle_dur)
    rustle = apply_bandpass(rustle, 1000, 5000)
    rustle_env = np.exp(-np.linspace(0, 3, rustle_n)).astype(np.float32)
    rustle = rustle * rustle_env * 0.06
    start_sample = int(t_start * SR)
    end_sample = min(start_sample + rustle_n, sm_n)
    subtle_movement[start_sample:end_sample] += rustle[: end_sample - start_sample]
save_wav(os.path.join(OUTPUT_DIR, "subtle_movement.wav"), subtle_movement)

# ============================================================
# 7. footsteps_out.wav - Footsteps walking away (5s)
# ============================================================
print("[7/8] Generating footsteps_out.wav...")
fout_dur = 5.0
fout_n = int(SR * fout_dur)
footsteps_out = np.zeros(fout_n, dtype=np.float32)
step_interval = 0.5
step_count = int(fout_dur / step_interval)
for i in range(step_count):
    t_start = i * step_interval
    step_dur = 0.15
    step_n = int(SR * step_dur)
    step = gen_noise(step_dur)
    step = apply_bandpass(step, 200, 2500)
    env = np.exp(-np.linspace(0, 5, step_n)).astype(np.float32)
    # Volume decreases as person walks away
    vol = 0.7 - 0.4 * (i / max(step_count - 1, 1))
    step = step * env * vol
    start_sample = int(t_start * SR)
    end_sample = min(start_sample + step_n, fout_n)
    footsteps_out[start_sample:end_sample] += step[: end_sample - start_sample]
save_wav(os.path.join(OUTPUT_DIR, "footsteps_out.wav"), footsteps_out)

# ============================================================
# 8. ambient_tail.wav - Environment background (3s, same as ambient)
# ============================================================
print("[8/8] Generating ambient_tail.wav...")
tail_dur = 3.0
tail_n = int(SR * tail_dur)
ambient_tail = gen_noise(tail_dur)
ambient_tail = apply_lowpass(ambient_tail, 800)
ambient_tail = ambient_tail * 0.04
rumble_tail = np.sin(2 * np.pi * 50 * np.arange(tail_n) / SR).astype(np.float32) * 0.015
ambient_tail = ambient_tail + rumble_tail
save_wav(os.path.join(OUTPUT_DIR, "ambient_tail.wav"), ambient_tail)

# ============================================================
# MIX - Combine all assets on the timeline
# ============================================================
print("\n[MIX] Combining all audio assets on timeline...")
mix = np.zeros(N, dtype=np.float32)


def place_at(mix, audio, start_s, sr=SR):
    """Place audio into mix at given start time."""
    start_sample = int(start_s * sr)
    end_sample = min(start_sample + len(audio), len(mix))
    actual_len = end_sample - start_sample
    if actual_len > 0:
        mix[start_sample:end_sample] += audio[:actual_len]


# Layer 0: Ambient background (full duration)
place_at(mix, ambient, 0.0)

# Layer 1: Key event sounds
place_at(mix, footsteps_in, 5.0)  # 5s-9s
place_at(mix, footsteps_stop, 9.0)  # 9s-12s
place_at(mix, doorbell, 14.2)  # 14.2s - THE ANCHOR POINT
place_at(mix, silence_response, 15.5)  # 15.5s-21.5s
place_at(mix, subtle_movement, 20.0)  # 20s-25s
place_at(mix, footsteps_out, 29.0)  # 29s-34s
place_at(mix, ambient_tail, 30.5)  # 30.5s-33.5s (overlap with fading footsteps)

# Normalize
peak = np.max(np.abs(mix))
if peak > 0:
    mix = mix / peak * 0.85  # Leave some headroom

# Apply gentle limiter
mix = np.tanh(mix * 1.2) * 0.8

save_wav(os.path.join(MIX_DIR, "stranger_visit_mix.wav"), mix)

print(f"\n[COMPLETE] Mix saved to: {os.path.join(MIX_DIR, 'stranger_visit_mix.wav')}")
print(f"  Duration: {DURATION}s")
print(f"  Sample rate: {SR}Hz")
print(f"  Doorbell at: 14.2s")

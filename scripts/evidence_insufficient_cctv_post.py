#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CCTV Post-Processing for evidence_insufficient
- Audio muxing (raw video + CI audio)
- Color grading (per-act, progressively darker)
- Dynamic timestamp overlay from Manifest
- Cover AI-generated timestamp/text
- 3 independent final videos + 1 demo concat

Act A: saturation=0.75, brightness=-0.02, noise=8,  CAM-01 14:15:XX
Act B: saturation=0.70, brightness=-0.05, noise=10, CAM-01 17:45:XX
Act C: saturation=0.60, brightness=-0.08, noise=12, CAM-01 21:45:XX
"""

import subprocess
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Use MSYS-style path /c/... (no quotes, no escaping needed)
FONT = "/c/Windows/Fonts/consola.ttf"


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def mux_audio(video_path, audio_path, output_path):
    """Mux video and audio into a single file."""
    print(f"  Muxing: {os.path.basename(video_path)} + {os.path.basename(audio_path)}")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[-800:]}")
        return False
    print(f"  Muxed: {output_path}")
    return True


def process_cctv(
    input_path, output_path, duration_s, saturation, brightness, noise, timestamp_text
):
    """Apply CCTV post-processing to a video with per-act parameters."""
    print(
        f"  Processing: {os.path.basename(input_path)} -> {os.path.basename(output_path)}"
    )
    print(f"  Params: sat={saturation} bright={brightness} noise={noise}")

    vf = (
        f"eq=saturation={saturation}:contrast=1.05:brightness={brightness}:gamma=0.98,"
        f"noise=alls={noise}:allf=t,"
        f"drawbox=x=0:y=0:w=iw:h=42:color=black@0.85:t=fill,"
        f"drawtext=fontfile={FONT}:"
        f"text='{timestamp_text}':"
        f"fontsize=24:fontcolor=white@0.85:"
        f"x=15:y=12:"
        f"box=1:boxcolor=black@0.4:boxborderw=3"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        str(duration_s),
        output_path,
    ]

    print(f"  Running ffmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[-800:]}")
        return False
    print(f"  Done: {output_path}")
    return True


def create_demo(acts, demo_path):
    """Create 3-act demo: Act A -> 1s black -> Act B -> 1s black -> Act C."""
    print(f"\n=== Creating 3-act demo ===")

    # Create 1s black segment
    black_path = os.path.join(BASE_DIR, "output", "_black_1s.mp4")
    cmd_black = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=1920x1080:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:d=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        black_path,
    ]
    subprocess.run(cmd_black, capture_output=True)

    # Concatenate using concat demuxer
    concat_list = os.path.join(BASE_DIR, "output", "_concat_list.txt")
    black_fwd = black_path.replace("\\", "/")
    with open(concat_list, "w") as f:
        for i, act_path in enumerate(acts):
            act_fwd = act_path.replace("\\", "/")
            f.write(f"file '{act_fwd}'\n")
            if i < len(acts) - 1:
                f.write(f"file '{black_fwd}'\n")

    cmd_concat = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        demo_path,
    ]
    result = subprocess.run(cmd_concat, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[-800:]}")
        return False

    # Cleanup
    os.remove(black_path)
    os.remove(concat_list)
    print(f"  Demo saved: {demo_path}")
    return True


# Per-act configuration: (video, audio, output, saturation, brightness, noise, timestamp)
ACTS = [
    {
        "name": "Act A",
        "video": os.path.join(BASE_DIR, "video", "act_a_raw.mp4"),
        "audio": os.path.join(BASE_DIR, "audio_mix", "act_a_mix.wav"),
        "muxed": os.path.join(BASE_DIR, "output", "act_a_with_audio.mp4"),
        "final": os.path.join(BASE_DIR, "output", "act_a_final.mp4"),
        "saturation": 0.75,
        "brightness": -0.02,
        "noise": 8,
        "timestamp": "CAM-01 2026-08-16 14\\:15\\:%{eif\\:t\\:d\\:2}",
    },
    {
        "name": "Act B",
        "video": os.path.join(BASE_DIR, "video", "act_b_raw.mp4"),
        "audio": os.path.join(BASE_DIR, "audio_mix", "act_b_mix.wav"),
        "muxed": os.path.join(BASE_DIR, "output", "act_b_with_audio.mp4"),
        "final": os.path.join(BASE_DIR, "output", "act_b_final.mp4"),
        "saturation": 0.70,
        "brightness": -0.05,
        "noise": 10,
        "timestamp": "CAM-01 2026-08-16 17\\:45\\:%{eif\\:t\\:d\\:2}",
    },
    {
        "name": "Act C",
        "video": os.path.join(BASE_DIR, "video", "act_c_raw.mp4"),
        "audio": os.path.join(BASE_DIR, "audio_mix", "act_c_mix.wav"),
        "muxed": os.path.join(BASE_DIR, "output", "act_c_with_audio.mp4"),
        "final": os.path.join(BASE_DIR, "output", "act_c_final.mp4"),
        "saturation": 0.60,
        "brightness": -0.08,
        "noise": 12,
        "timestamp": "CAM-01 2026-08-16 21\\:45\\:%{eif\\:t\\:d\\:2}",
    },
]


if __name__ == "__main__":
    print("CCTV Post-Processing - evidence_insufficient")
    print("3 independent acts + 1 demo concat")
    print()

    final_acts = []

    for act in ACTS:
        print(f"=== {act['name']} ===")
        print(f"  Video: {act['video']}")
        print(f"  Audio: {act['audio']}")

        # Step 1: Mux audio
        if not mux_audio(act["video"], act["audio"], act["muxed"]):
            print(f"  FAILED at mux for {act['name']}")
            continue

        # Step 2: CCTV post-processing
        duration = get_video_duration(act["muxed"])
        print(f"  Duration: {duration:.1f}s")
        if not process_cctv(
            act["muxed"],
            act["final"],
            duration,
            act["saturation"],
            act["brightness"],
            act["noise"],
            act["timestamp"],
        ):
            print(f"  FAILED at CCTV for {act['name']}")
            continue

        final_acts.append(act["final"])
        print()

    # Step 3: Create demo
    if len(final_acts) == 3:
        demo_path = os.path.join(BASE_DIR, "output", "evidence_insufficient_demo.mp4")
        create_demo(final_acts, demo_path)
    else:
        print(f"WARNING: Only {len(final_acts)} acts completed, skipping demo")

    print("\n=== All CCTV post-processing complete ===")
    for act in ACTS:
        print(f"  {act['name']}: {act['final']}")
    if len(final_acts) == 3:
        print(
            f"  Demo: {os.path.join(BASE_DIR, 'output', 'evidence_insufficient_demo.mp4')}"
        )

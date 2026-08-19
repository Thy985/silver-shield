#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CCTV Post-Processing for telephone_risk
- Color grading (warm indoor CCTV look)
- Dynamic timestamp overlay from Manifest
- Cover AI-generated timestamp
- Process both Case A and Case B
"""

import subprocess
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Use MSYS-style path /c/... (no quotes, no escaping needed)
FONT = "/c/Windows/Fonts/consola.ttf"

# CCTV color grading parameters (warm indoor look)
SATURATION = 0.80
CONTRAST = 1.05
BRIGHTNESS = -0.01
GAMMA = 1.0
NOISE = 6

# Timestamp text: colons inside text must be escaped as \: for ffmpeg drawtext
# In Python string, \\: produces \: which ffmpeg interprets correctly
TIMESTAMP_TEXT = "CAM-02 2026-08-16 19\\:45\\:%{eif\\:t\\:d\\:2}"


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


def process_cctv(input_path, output_path, duration_s):
    """Apply CCTV post-processing to a video."""
    print(f"  Processing: {input_path} -> {output_path}")

    # Build filter complex (tested working pattern)
    # Use /c/ path for fontfile, no quotes needed
    # Colons in text escaped as \:
    vf = (
        f"eq=saturation={SATURATION}:contrast={CONTRAST}:brightness={BRIGHTNESS}:gamma={GAMMA},"
        f"noise=alls={NOISE}:allf=t,"
        f"drawbox=x=0:y=0:w=iw:h=42:color=black@0.85:t=fill,"
        f"drawtext=fontfile={FONT}:"
        f"text='{TIMESTAMP_TEXT}':"
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


def create_demo(case_a_path, case_b_path, demo_path):
    """Create A/B comparison demo: Case A -> 1s black -> Case B."""
    print(f"\n=== Creating A/B demo ===")

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
    # Use forward slashes for ffmpeg concat demuxer
    case_a_fwd = case_a_path.replace("\\", "/")
    black_fwd = black_path.replace("\\", "/")
    case_b_fwd = case_b_path.replace("\\", "/")
    with open(concat_list, "w") as f:
        f.write(f"file '{case_a_fwd}'\n")
        f.write(f"file '{black_fwd}'\n")
        f.write(f"file '{case_b_fwd}'\n")

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


if __name__ == "__main__":
    print("CCTV Post-Processing - telephone_risk")
    print(
        f"Color: sat={SATURATION} contrast={CONTRAST} bright={BRIGHTNESS} gamma={GAMMA} noise={NOISE}"
    )
    print(f"Timestamp: CAM-02 2026-08-16 19:45:XX")
    print()

    # Process Case A
    case_a_input = os.path.join(BASE_DIR, "output", "case_a_with_audio.mp4")
    case_a_output = os.path.join(BASE_DIR, "output", "case_a_vision_only.mp4")
    duration = get_video_duration(case_a_input)
    print(f"=== Case A (Vision Only, {duration:.1f}s) ===")
    process_cctv(case_a_input, case_a_output, duration)

    # Process Case B
    case_b_input = os.path.join(BASE_DIR, "output", "case_b_with_audio.mp4")
    case_b_output = os.path.join(BASE_DIR, "output", "case_b_vision_audio.mp4")
    duration = get_video_duration(case_b_input)
    print(f"\n=== Case B (Vision + Audio, {duration:.1f}s) ===")
    process_cctv(case_b_input, case_b_output, duration)

    # Create demo
    demo_path = os.path.join(BASE_DIR, "output", "telephone_risk_demo.mp4")
    create_demo(case_a_output, case_b_output, demo_path)

    print("\n=== All CCTV post-processing complete ===")
    print(f"Case A: {case_a_output}")
    print(f"Case B: {case_b_output}")
    print(f"Demo: {demo_path}")

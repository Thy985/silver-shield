#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 6: CCTV post-processing for stranger_visit
- Slightly desaturate, cool color tone
- Add subtle digital noise
- Add CCTV timestamp overlay (top-left)
- Export final MP4 (H.264/AAC, 1080p, 30fps)
"""

import subprocess
import os

INPUT = "D:/Learning/AI视频/contest/stranger_visit/output/stranger_visit_with_audio.mp4"
OUTPUT = "D:/Learning/AI视频/contest/stranger_visit/output/stranger_visit_final.mp4"
FONT = "C\\\\:/Windows/Fonts/consola.ttf"

# ffmpeg filter complex:
# 1. Subtle desaturation + cool tone: eq filter
# 2. Add digital noise: noise filter
# 3. Timestamp overlay using drawtext
# 4. Re-encode to 30fps

# Build drawtext filter for timestamp
# Format: 2024-03-15 15:30:00 (incrementing from 15:30:00)
# Start time: 15:30:00, each video second = 1 second increment
# Use strftime-like format with start_time

drawtext = (
    f"drawtext=fontfile='{FONT}':"
    f"text='2024-03-15 15:30:%{int(00)}':"
    f"fontsize=28:fontcolor=white@0.85:"
    f"x=20:y=20:"
    f"box=1:boxcolor=black@0.4:boxborderw=4"
)

# We'll use a simpler approach: use the drawtext time-based display
# Format: CAM-01  2024-03-15 15:30:XX
# where XX increments with the video time

# Build filter - use raw string to avoid Python escape issues
vf = (
    "eq=saturation=0.75:contrast=1.05:brightness=-0.02:gamma=0.98,"
    "noise=alls=8:allf=t+u,"
    "drawtext=fontfile='" + FONT + "':"
    "text='CAM-01 2024-03-15 15\\:30\\:%{eif\\:t\\:d\\:2}':"
    "fontsize=24:fontcolor=white@0.80:"
    "x=15:y=15:"
    "box=1:boxcolor=black@0.35:boxborderw=3"
)

cmd = [
    "ffmpeg",
    "-y",
    "-i",
    INPUT,
    "-vf",
    vf,
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "20",
    "-r",
    "30",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    "-ar",
    "48000",
    "-movflags",
    "+faststart",
    "-shortest",
    OUTPUT,
]

print(f"[Phase 6] CCTV post-processing...")
print(f"  Input:  {INPUT}")
print(f"  Output: {OUTPUT}")
print(f"  Filter: {vf[:80]}...")
print()

result = subprocess.run(cmd, capture_output=True, text=True)

if result.returncode == 0:
    size = os.path.getsize(OUTPUT) / (1024 * 1024)
    print(f"[COMPLETE] Final video saved: {OUTPUT}")
    print(f"  Size: {size:.1f} MB")
else:
    print(f"[ERROR] ffmpeg failed (return code {result.returncode})")
    print(result.stderr[-2000:])

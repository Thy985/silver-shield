"""Download + prepare P0-5/P0-7 surveillance scenario fixtures (CAVIAR).

Purpose: validate the "video stream -> YOLO -> Tracker -> VisitorTrack -> door events"
link, NOT to train scam-recognition models. This is scenario data, not training data
(see ADR-0006 / P0-5 task positioning).

Data source:
- CAVIAR (INRIA entrance lobby + Lisbon shopping centre corridor):
  http://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1
- License: CAVIAR project (EC Funded IST 2001 37540) free, attribute to CAVIAR project
- Video spec: MPEG2 / 384x288 / 25 fps

Usage:
- First time: ``python tests/fixtures/download_fixtures.py``
  (~18MB MPG + ~4MB extracted JPG frames at 2fps, decimated to 30-50 frames/scene)
- CI / offline: fixtures present? skip download; missing? graceful skip
  (see tests/test_tracker.py)
- Regenerate: delete tests/fixtures/doorway/ and rerun

Version control:
- MPG (tests/fixtures/caviar_raw/) and JPG frames (tests/fixtures/doorway/) are
  .gitignore'd. Only this script and README.md are tracked.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
RAW_DIR = FIXTURE_DIR / "caviar_raw"
DOORWAY_DIR = FIXTURE_DIR / "doorway"

CAVIAR_BASE = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1"
CAVIAR_DATA2_BASE = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA2"

# UA: CAVIAR server rejects some bare curl UAs
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# (scenario_name, MPG url, output dir, target_frame_count, purpose)
SCENARIOS = [
    {
        "name": "OneStopEnter1cor",
        "url": f"{CAVIAR_DATA2_BASE}/OneStopEnter1cor/OneStopEnter1cor.mpg",
        "out_dir": "one_stop_enter",
        "max_frames": 50,
        "purpose": "Single person enter + dwell (P0-7 dwell-rule precursor)",
    },
    {
        "name": "OneLeaveShopReenter1cor",
        "url": f"{CAVIAR_DATA2_BASE}/OneLeaveShopReenter1cor/OneLeaveShopReenter1cor.mpg",
        "out_dir": "one_leave_reenter",
        "max_frames": 30,
        "purpose": "Single person leave + reenter (P0-5 revisit + P0-7 repeat-visit)",
    },
    {
        "name": "Meet_WalkTogether1",
        "url": f"{CAVIAR_BASE}/Meet_WalkTogether1/Meet_WalkTogether1.mpg",
        "out_dir": "meet_walk_together",
        "max_frames": 50,
        "purpose": "Multi-person meet + walk (verify track_id independence)",
    },
]


def download(url, dst):
    if dst.exists() and dst.stat().st_size > 1024:
        print(f"  [skip] {dst.name} already exists ({dst.stat().st_size//1024}KB)")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [get]  {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) < 1024:
            print(f"  [fail] file too small ({len(data)} bytes)")
            return False
        dst.write_bytes(data)
        print(f"  [ok]   {dst.name} ({len(data)//1024}KB)")
        return True
    except Exception as e:
        print(f"  [err]  {type(e).__name__}: {e}")
        return False


def extract_frames(mpg, out_dir, target_fps=2.0, max_frames=50):
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.jpg"):
        f.unlink()
    if shutil.which("ffmpeg") is None:
        print(f"  [warn] ffmpeg not found, skip ({out_dir.name})")
        return 0
    cmd = ["ffmpeg", "-y", "-i", str(mpg), "-vf", f"fps={target_fps}",
           "-q:v", "2", str(out_dir / "frame_%05d.jpg")]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("  [err]  ffmpeg timeout")
        return 0
    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        last = result.stderr.splitlines()[-1] if result.stderr else "n/a"
        print(f"  [err]  ffmpeg no frames; last stderr: {last}")
        return 0
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        keep = {frames[int(i * step)] for i in range(max_frames)}
        for f in frames:
            if f not in keep:
                f.unlink()
    for i, f in enumerate(sorted(out_dir.glob("frame_*.jpg")), 1):
        new_name = out_dir / f"frame_{i:05d}.jpg"
        if f != new_name:
            f.rename(new_name)
    final = sorted(out_dir.glob("frame_*.jpg"))
    total_kb = sum(f.stat().st_size for f in final) // 1024
    print(f"  [ok]   {len(final)} frames ({total_kb}KB) -> {out_dir.name}/")
    return len(final)


def main():
    print("=== CAVIAR fixture: download + frame extraction ===")
    print(f"Output: {DOORWAY_DIR}")
    print(f"Raw MPG: {RAW_DIR}")
    print()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    n_mpg = 0
    n_frames = 0
    for sc in SCENARIOS:
        print(f"[{sc['out_dir']}] {sc['purpose']}")
        mpg = RAW_DIR / f"{sc['name']}.mpg"
        if download(sc["url"], mpg):
            n_mpg += 1
            out_dir = DOORWAY_DIR / sc["out_dir"]
            n = extract_frames(mpg, out_dir, target_fps=2.0, max_frames=sc["max_frames"])
            n_frames += n
        print()
    print("---")
    print(f"Downloaded MPG: {n_mpg}/{len(SCENARIOS)}")
    print(f"Extracted frames: {n_frames}")
    print(f"Fixture location: {DOORWAY_DIR}")
    if n_mpg < len(SCENARIOS):
        print("\n[WARN] Some MPGs failed; corresponding tests will skip.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

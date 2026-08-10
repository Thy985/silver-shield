"""Download + prepare P0-5/P0-7 surveillance scenario fixtures (CAVIAR).

Purpose: validate the "video stream -> YOLO -> Tracker -> VisitorTrack -> door events"
link, NOT to train scam-recognition models. This is scenario data, not training data
(see ADR-0006 / P0-5 task positioning).

Data source:
- CAVIAR (INRIA entrance lobby + Lisbon shopping centre corridor):
  https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1
- License: CAVIAR project (EC Funded IST 2001 37540) free, attribute to CAVIAR project
- Video spec: MPEG2 / 384x288 / 25 fps

Supply-chain hardening (Stage 2)
--------------------------------
This script is the ``acquire.method: script`` backend for the ``caviar_doorway``
fixture in ``tests/fixtures/manifest.yaml``. The extracted JPG frames are a
*derived* artifact — ffmpeg output is not bit-reproducible across versions, so
they cannot be content-hashed. Integrity is therefore anchored one level up:
every upstream MPG below carries a pinned ``sha256`` and is fetched through
``scripts/fixture_manager._secure_download`` (HTTPS-only, redirect allow-list,
chunked streaming, size cap, verify-before-rename). The manifest then asserts
the *structure* of what this script produced.

Usage:
- First time: ``python tests/fixtures/download_fixtures.py``
  (~19MB MPG + ~4MB extracted JPG frames at 2fps, decimated to 30-50 frames/scene)
- Normally invoked for you by:
  ``python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --acquire``
- Regenerate: delete tests/fixtures/doorway/ and rerun

Version control:
- MPG (tests/fixtures/caviar_raw/) and JPG frames (tests/fixtures/doorway/) are
  .gitignore'd. Only this script and README.md are tracked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Preliminary root, used only to put `scripts` on sys.path before importing it.
_PRELIM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PRELIM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PRELIM_ROOT))

from scripts.fixture_manager import _git_toplevel, _secure_download, _sha256_of

# Repo root resolved the *same* way fixture_manager does: prefer
# `git rev-parse --show-toplevel`, fall back to the manifest-relative location so
# a relocated/renamed checkout cannot silently point this script at the wrong
# tree. (Single source of truth for "where is the repo root".)
REPO_ROOT = _git_toplevel() or _PRELIM_ROOT
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
RAW_DIR = FIXTURE_DIR / "caviar_raw"
DOORWAY_DIR = FIXTURE_DIR / "doorway"

CAVIAR_BASE = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1"
CAVIAR_DATA2_BASE = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA2"

# (scenario_name, MPG url, sha256, output dir, target_frame_count, purpose)
# sha256 pinned 2026-08-10 against upstream Content-Length + byte digest.
SCENARIOS = [
    {
        "name": "OneStopEnter1cor",
        "url": f"{CAVIAR_DATA2_BASE}/OneStopEnter1cor/OneStopEnter1cor.mpg",
        "sha256": "05340115b48889f344f45c05593322b0c3cb9852081092226833f8e73d3ffb83",
        "out_dir": "one_stop_enter",
        "max_frames": 50,
        "purpose": "Single person enter + dwell (P0-7 dwell-rule precursor)",
    },
    {
        "name": "OneLeaveShopReenter1cor",
        "url": f"{CAVIAR_DATA2_BASE}/OneLeaveShopReenter1cor/OneLeaveShopReenter1cor.mpg",
        "sha256": "14d55141103159aedfaf9f2be798396536cdc00a4e5113f99063276181edf937",
        "out_dir": "one_leave_reenter",
        "max_frames": 30,
        "purpose": "Single person leave + reenter (P0-5 revisit + P0-7 repeat-visit)",
    },
    {
        "name": "Meet_WalkTogether1",
        "url": f"{CAVIAR_BASE}/Meet_WalkTogether1/Meet_WalkTogether1.mpg",
        "sha256": "95969a486f39ffd7a9a0365db070aba89062ae78c021ef1afc7cabff5e9a6452",
        "out_dir": "meet_walk_together",
        "max_frames": 50,
        "purpose": "Multi-person meet + walk (verify track_id independence)",
    },
]


def download(url: str, dst: Path, sha256: str) -> bool:
    """Fetch one upstream MPG, verifying its pinned digest.

    The decision is driven entirely by the checksum — there is no separate size
    sniff:
      * present AND digest matches  -> skip (zero network I/O)
      * present AND digest mismatch -> discard and re-fetch (stale-but-present is
        exactly the failure mode a checksum exists to catch)
      * absent                      -> fetch

    The previous ">1 KiB" precondition was a fragile proxy for "looks truncated";
    the digest is the real gate, so we no longer second-guess it by byte count.
    ``_secure_download`` re-verifies the digest after transfer as a backstop.
    """
    if dst.exists():
        if _sha256_of(dst) == sha256:
            print(
                f"  [skip] {dst.name} already present & verified ({dst.stat().st_size // 1024}KB)"
            )
            return True
        print(f"  [warn] {dst.name} checksum mismatch, re-downloading")
        dst.unlink()

    print(f"  [get]  {url}")
    ok, msgs = _secure_download(url, dst, sha256, root=REPO_ROOT)
    for msg in msgs:
        print(f"         {msg}")
    if ok:
        print(f"  [ok]   {dst.name} ({dst.stat().st_size // 1024}KB)")
    else:
        print(f"  [fail] {dst.name}")
    return ok


def _probe_duration(mpg: Path) -> float | None:
    """Clip duration in seconds via ffprobe, or None when unavailable."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(mpg),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float(proc.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def extract_frames(mpg: Path, out_dir: Path, max_frames: int = 50, cap_fps: float = 2.0) -> int:
    """Sample at most *max_frames* JPGs spread evenly across the clip.

    The sampling rate is derived from the clip duration (``max_frames /
    duration``, capped at *cap_fps* so short clips are never upsampled) and
    ``-frames:v`` enforces the ceiling. This deliberately replaces the older
    "extract at 2fps, then delete down to N" approach: that wrote hundreds of
    JPEGs only to unlink most of them, which is wasted work, non-deterministic
    under partial failure, and trips file-deletion guards on some dev sandboxes.
    Deriving the rate up front means every frame written is a frame kept.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(out_dir.glob("*.jpg")):
        f.unlink()
    if shutil.which("ffmpeg") is None:
        print(f"  [warn] ffmpeg not found, skip ({out_dir.name})")
        return 0

    duration = _probe_duration(mpg)
    if duration is None:
        fps = cap_fps
        print(f"  [warn] ffprobe unavailable; falling back to fps={fps} (may truncate the clip)")
    else:
        fps = min(cap_fps, max_frames / duration)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mpg),
        "-vf",
        f"fps={fps:.6f}",
        "-frames:v",
        str(max_frames),
        "-q:v",
        "2",
        str(out_dir / "frame_%05d.jpg"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        print("  [err]  ffmpeg timeout")
        return 0
    final = sorted(out_dir.glob("frame_*.jpg"))
    if not final:
        last = result.stderr.splitlines()[-1] if result.stderr else "n/a"
        print(f"  [err]  ffmpeg no frames; last stderr: {last}")
        return 0
    total_kb = sum(f.stat().st_size for f in final) // 1024
    print(f"  [ok]   {len(final)} frames ({total_kb}KB, fps={fps:.3f}) -> {out_dir.name}/")
    return len(final)


def main():
    print("=== CAVIAR fixture: download + frame extraction ===")
    print(f"Output: {DOORWAY_DIR}")
    print(f"Raw MPG: {RAW_DIR}")
    print()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    n_mpg = 0
    n_frames = 0
    failed: list[str] = []
    for sc in SCENARIOS:
        print(f"[{sc['out_dir']}] {sc['purpose']}")
        mpg = RAW_DIR / f"{sc['name']}.mpg"
        if download(sc["url"], mpg, sc["sha256"]):
            n_mpg += 1
            out_dir = DOORWAY_DIR / sc["out_dir"]
            n = extract_frames(mpg, out_dir, max_frames=sc["max_frames"])
            n_frames += n
            if n == 0:
                failed.append(f"{sc['out_dir']} (frame extraction produced 0 frames)")
        else:
            failed.append(f"{sc['out_dir']} (download failed)")
        print()
    print("---")
    print(f"Downloaded MPG: {n_mpg}/{len(SCENARIOS)}")
    print(f"Extracted frames: {n_frames}")
    print(f"Fixture location: {DOORWAY_DIR}")
    if failed:
        # Non-zero exit is load-bearing: fixture_manager treats it as an acquire
        # failure and (under --strict) turns ci-runtime RED instead of skipping.
        print("\n[FAIL] incomplete fixture set:")
        for item in failed:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

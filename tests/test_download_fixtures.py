"""Offline unit tests for tests/fixtures/download_fixtures.py (CAVIAR acquire script).

All network and ffmpeg/ffprobe subprocess calls are monkeypatched, so these run
fully offline in CI. They lock the behaviour called out in the P1 review
(B4: sha256-driven download decisions; frame extraction plumbing).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

# Load the acquire script by file path (not as ``tests.fixtures.download_fixtures``)
# so the test does not depend on ``tests`` being an importable package under
# pytest's prepend import mode.
_spec = importlib.util.spec_from_file_location(
    "silver_download_fixtures",
    ROOT / "tests" / "fixtures" / "download_fixtures.py",
)
df = importlib.util.module_from_spec(_spec)
sys.modules["silver_download_fixtures"] = df
_spec.loader.exec_module(df)

# ----- download() : sha256-driven decisions (B4) ------------------------


def test_download_skips_when_present_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"fake-mpg-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    dst = tmp_path / "asset.mpg"
    dst.write_bytes(payload)

    calls: list[object] = []

    def fake_dl(url, d, s, *, root):
        d.write_bytes(b"downloaded")  # mimic _secure_download writing the asset
        calls.append(d)
        return (True, ["ok"])

    monkeypatch.setattr(df, "_secure_download", fake_dl)
    assert df.download("https://homepages.inf.ed.ac.uk/x/y.mpg", dst, sha) is True
    # Present + matching digest => no network fetch at all.
    assert calls == []


def test_download_redownloads_on_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dst = tmp_path / "asset.mpg"
    dst.write_bytes(b"stale-bytes")  # wrong content for the declared sha
    sha = hashlib.sha256(b"fresh-bytes").hexdigest()

    calls: list[Path] = []

    def fake_dl(url, d, s, *, root):
        d.write_bytes(b"downloaded")  # mimic _secure_download writing the asset
        calls.append(d)
        return (True, ["ok"])

    monkeypatch.setattr(df, "_secure_download", fake_dl)
    assert df.download("https://homepages.inf.ed.ac.uk/x/y.mpg", dst, sha) is True
    # Mismatch => the stale file is discarded and a fresh download is attempted.
    assert calls == [dst]


def test_download_fetches_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dst = tmp_path / "asset.mpg"
    sha = hashlib.sha256(b"x").hexdigest()

    calls: list[Path] = []

    def fake_dl(url, d, s, *, root):
        d.write_bytes(b"downloaded")  # mimic _secure_download writing the asset
        calls.append(d)
        return (True, ["ok"])

    monkeypatch.setattr(df, "_secure_download", fake_dl)
    assert df.download("https://homepages.inf.ed.ac.uk/x/y.mpg", dst, sha) is True
    assert calls == [dst]


def test_download_propagates_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dst = tmp_path / "asset.mpg"
    sha = hashlib.sha256(b"x").hexdigest()

    def fake_dl(url, d, s, *, root):
        return (False, ["boom"])

    monkeypatch.setattr(df, "_secure_download", fake_dl)
    assert df.download("https://homepages.inf.ed.ac.uk/x/y.mpg", dst, sha) is False


# ----- _probe_duration() -------------------------------------------------


def test_probe_duration_returns_none_when_ffprobe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df.shutil, "which", lambda name: None)
    assert df._probe_duration(tmp_path / "x.mpg") is None


def test_probe_duration_parses_ffprobe_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df.shutil, "which", lambda name: "ffprobe" if name == "ffprobe" else None)

    class _R:
        returncode = 0
        stdout = "7.5"
        stderr = ""

    monkeypatch.setattr(df.subprocess, "run", lambda *a, **k: _R())
    assert df._probe_duration(tmp_path / "x.mpg") == 7.5


# ----- extract_frames() --------------------------------------------------


def test_extract_frames_returns_0_when_ffmpeg_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df.shutil, "which", lambda name: None)
    out = tmp_path / "doorway" / "scene"
    out.mkdir(parents=True)
    (out / "frame_00001.jpg").write_bytes(b"stale")  # should be cleared, no new frames
    assert df.extract_frames(tmp_path / "x.mpg", out, max_frames=10) == 0


def test_extract_frames_runs_ffmpeg_and_counts_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_which(name: str) -> str | None:
        return {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}.get(name)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "ffprobe" in cmd:

            class _R:
                returncode = 0
                stdout = "10.0"
                stderr = ""

            return _R()
        # ffmpeg: simulate writing exactly one frame to the output directory.
        out_dir = Path(cmd[-1]).parent
        (out_dir / "frame_00001.jpg").write_bytes(b"x")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(df.shutil, "which", fake_which)
    monkeypatch.setattr(df.subprocess, "run", fake_run)

    out = tmp_path / "doorway" / "scene"
    out.mkdir(parents=True)
    n = df.extract_frames(tmp_path / "x.mpg", out, max_frames=50)

    assert n == 1
    assert any("ffmpeg" in c for c in calls)
    assert any("-frames:v" in c for c in calls)
    assert (out / "frame_00001.jpg").exists()

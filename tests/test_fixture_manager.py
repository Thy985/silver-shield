"""Unit tests for scripts.fixture_manager (Stage-2 fail-closed fixture gate).

These tests build temporary manifests/roots so they never touch the real repo
fixtures or invoke git. Every call passes ``--root`` explicitly, keeping ``main``
deterministic in CI.
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.error
from pathlib import Path
from typing import Self

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pytest
import yaml

from scripts import fixture_manager as fm
from scripts.fixture_manager import (
    _is_present,
    _is_within,
    _run_acquire_script,
    _secure_download,
    _sha256_of,
    _structure_errors,
    acquire_entry,
    main,
    validate_manifest,
)


def _write_manifest(tmp_path: Path, fixtures: list[dict]) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump({"fixtures": fixtures}), encoding="utf-8")
    return p


# ----- _is_present --------------------------------------------------------


def test_is_present_normal_file(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"hello")
    assert _is_present({"id": "x", "type": "image", "path": "a.bin"}, tmp_path, []) is True


def test_is_present_empty_file_is_false(tmp_path: Path) -> None:
    (tmp_path / "empty.bin").write_bytes(b"")
    assert _is_present({"id": "x", "type": "image", "path": "empty.bin"}, tmp_path, []) is False


def test_is_present_missing_file_is_false(tmp_path: Path) -> None:
    assert _is_present({"id": "x", "type": "image", "path": "nope.bin"}, tmp_path, []) is False


def test_is_present_dir_with_content(tmp_path: Path) -> None:
    d = tmp_path / "frames"
    d.mkdir()
    (d / "f1.jpg").write_bytes(b"x")
    assert _is_present({"id": "x", "type": "video", "path": "frames"}, tmp_path, []) is True


def test_is_present_empty_dir_is_false(tmp_path: Path) -> None:
    (tmp_path / "frames").mkdir()
    assert _is_present({"id": "x", "type": "video", "path": "frames"}, tmp_path, []) is False


def test_is_present_no_type_infers_from_path(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x")
    assert _is_present({"id": "x", "path": "a.bin"}, tmp_path, []) is True
    d = tmp_path / "frames"
    d.mkdir()
    (d / "f.jpg").write_bytes(b"x")
    assert _is_present({"id": "y", "path": "frames"}, tmp_path, []) is True


def test_is_present_type_mismatch_image_but_dir_warns(tmp_path: Path) -> None:
    d = tmp_path / "frames"
    d.mkdir()
    (d / "f1.jpg").write_bytes(b"x")
    warnings: list[str] = []
    # declared image (file mode) but path is a directory -> not present per
    # declared semantics, and a mismatch warning is emitted (no silent fallback).
    assert (
        _is_present({"id": "vid", "type": "image", "path": "frames"}, tmp_path, warnings) is False
    )
    assert any("DIRECTORY" in w for w in warnings)


def test_is_present_type_mismatch_video_but_file_warns(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x")
    warnings: list[str] = []
    assert _is_present({"id": "vid", "type": "video", "path": "a.bin"}, tmp_path, warnings) is False
    assert any("FILE" in w for w in warnings)


def test_is_present_symlink_outside_root_warns(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"x")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unsupported in this environment")
    warnings: list[str] = []
    assert _is_present({"id": "x", "type": "image", "path": "link.bin"}, tmp_path, warnings) is True
    assert any("OUTSIDE repo root" in w for w in warnings)


# ----- _sha256_of ---------------------------------------------------------


def test_sha256_matches_known_content(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    import hashlib

    assert _sha256_of(f) == hashlib.sha256(b"abc").hexdigest()


def test_sha256_chunked_handles_large_file(tmp_path: Path) -> None:
    payload = b"X" * (3 * 1024 * 1024)
    f = tmp_path / "big.bin"
    f.write_bytes(payload)
    import hashlib

    assert _sha256_of(f) == hashlib.sha256(payload).hexdigest()


# ----- validate_manifest -------------------------------------------------


def test_validate_manifest_ok() -> None:
    data = {"fixtures": [{"id": "a", "type": "image", "path": "p", "required_for": ["t::x"]}]}
    assert validate_manifest(data) == []


def test_validate_manifest_missing_fields() -> None:
    errs = validate_manifest({"fixtures": [{"id": "a"}]})
    assert any("'path'" in e for e in errs)
    assert any("'type'" in e for e in errs)
    assert any("'required_for'" in e for e in errs)


def test_validate_manifest_type_whitelist() -> None:
    data = {"fixtures": [{"id": "a", "type": "bogus", "path": "p", "required_for": ["t"]}]}
    errs = validate_manifest(data)
    assert any("type 'bogus' not in" in e for e in errs)


def test_validate_manifest_duplicate_id() -> None:
    fx = {"id": "a", "type": "image", "path": "p", "required_for": ["t"]}
    errs = validate_manifest({"fixtures": [fx, dict(fx)]})
    assert any("duplicate" in e for e in errs)


def test_validate_manifest_not_a_list() -> None:
    assert validate_manifest({}) == ["top-level 'fixtures' must be a list"]


# ----- main exit codes ---------------------------------------------------


def test_main_all_present_strict_passes(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x")
    m = _write_manifest(
        tmp_path, [{"id": "a", "type": "image", "path": "a.bin", "required_for": ["t"]}]
    )
    assert main(["--manifest", str(m), "--root", str(tmp_path), "--strict"]) == 0


def test_main_missing_required_strict_fails(tmp_path: Path) -> None:
    m = _write_manifest(
        tmp_path, [{"id": "a", "type": "image", "path": "missing.bin", "required_for": ["t"]}]
    )
    assert main(["--manifest", str(m), "--root", str(tmp_path), "--strict"]) == 1


def test_main_missing_non_strict_passes(tmp_path: Path) -> None:
    # A missing fixture is only fatal under --strict; otherwise it reports and
    # returns 0 (downstream tests are expected to skip).
    m = _write_manifest(
        tmp_path, [{"id": "a", "type": "image", "path": "missing.bin", "required_for": ["t"]}]
    )
    assert main(["--manifest", str(m), "--root", str(tmp_path)]) == 0


def test_main_manifest_missing_returns_2(tmp_path: Path) -> None:
    assert main(["--manifest", str(tmp_path / "nope.yaml"), "--root", str(tmp_path)]) == 2


def test_main_bad_yaml_returns_2(tmp_path: Path) -> None:
    m = tmp_path / "bad.yaml"
    m.write_text("fixtures: [ : : :", encoding="utf-8")
    assert main(["--manifest", str(m), "--root", str(tmp_path)]) == 2


# =========================================================================
# P1: --acquire (secure download + script generation + structure integrity)
# =========================================================================

GOOD_URL = "https://raw.githubusercontent.com/org/repo/v1/asset.bin"


class _FakeResponse:
    """Minimal urlopen stand-in: context manager + chunked read + headers."""

    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self._buf = io.BytesIO(payload)
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _patch_download(monkeypatch: pytest.MonkeyPatch, payload: bytes, **headers: str) -> None:
    """Route _secure_download at a fixed in-memory body (no network)."""
    monkeypatch.setattr(
        fm,
        "_open_url",
        lambda url, timeout=fm.DOWNLOAD_TIMEOUT_SECONDS: _FakeResponse(payload, headers),
    )


# ----- _is_within ---------------------------------------------------------


def test_is_within_true_for_child(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b.txt"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    assert _is_within(child, tmp_path) is True


def test_is_within_false_for_escape(tmp_path: Path) -> None:
    assert _is_within(tmp_path.parent / "elsewhere.txt", tmp_path) is False


# ----- _secure_download: transport policy ---------------------------------


def test_secure_download_rejects_plain_http(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download("http://raw.githubusercontent.com/x", dest, "0" * 64, root=tmp_path)
    assert ok is False
    assert any("non-HTTPS" in m for m in msgs)
    # Crucially: not silently upgraded to https and fetched anyway.
    assert not dest.exists()


def test_secure_download_rejects_non_allowlisted_host(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download("https://evil.example.com/x", dest, "0" * 64, root=tmp_path)
    assert ok is False
    assert any("not allow-listed" in m for m in msgs)
    assert not dest.exists()


def test_secure_download_refuses_unpinned_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_download(monkeypatch, b"payload")
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(GOOD_URL, dest, None, root=tmp_path)
    assert ok is False
    assert any("sha256 is null" in m for m in msgs)
    assert not dest.exists()


def test_secure_download_unpinned_allowed_prints_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"bootstrap-me"
    _patch_download(monkeypatch, payload)
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(GOOD_URL, dest, None, root=tmp_path, allow_unpinned=True)
    assert ok is True
    assert dest.read_bytes() == payload
    assert any(hashlib.sha256(payload).hexdigest() in m for m in msgs)


# ----- _secure_download: integrity ---------------------------------------


def test_secure_download_verifies_matching_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"trusted-bytes"
    _patch_download(monkeypatch, payload)
    dest = tmp_path / "a.bin"
    ok, _ = _secure_download(GOOD_URL, dest, hashlib.sha256(payload).hexdigest(), root=tmp_path)
    assert ok is True
    assert dest.read_bytes() == payload


def test_secure_download_mismatch_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_download(monkeypatch, b"tampered")
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(GOOD_URL, dest, "a" * 64, root=tmp_path)
    assert ok is False
    assert any("checksum mismatch" in m for m in msgs)
    # Neither the final file nor the .part scratch may survive: a leftover would
    # be picked up as "present" by a later run and silently trusted.
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_secure_download_streams_multi_chunk_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"Z" * (fm.DOWNLOAD_CHUNK_BYTES * 2 + 7)
    _patch_download(monkeypatch, payload)
    dest = tmp_path / "big.bin"
    ok, _ = _secure_download(GOOD_URL, dest, hashlib.sha256(payload).hexdigest(), root=tmp_path)
    assert ok is True
    assert dest.stat().st_size == len(payload)


def test_secure_download_empty_body_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_download(monkeypatch, b"")
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(GOOD_URL, dest, hashlib.sha256(b"").hexdigest(), root=tmp_path)
    assert ok is False
    assert any("empty file" in m for m in msgs)
    assert not dest.exists()


# ----- _secure_download: size cap ----------------------------------------


def test_secure_download_aborts_on_declared_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_download(monkeypatch, b"x" * 100, **{"Content-Length": "999999"})
    dest = tmp_path / "a.bin"
    # max_bytes=2048 is a legitimate (>=1024) ceiling; the declared 999999B is
    # what trips the cap here (the <1024 absurd-value guard must NOT fire).
    ok, msgs = _secure_download(GOOD_URL, dest, "a" * 64, root=tmp_path, max_bytes=2048)
    assert ok is False
    assert any("exceeds cap" in m for m in msgs)
    assert not dest.exists()


def test_secure_download_aborts_mid_stream_when_cap_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No Content-Length header => the cap must be enforced while streaming.
    _patch_download(monkeypatch, b"x" * (fm.DOWNLOAD_CHUNK_BYTES + 1))
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(
        GOOD_URL, dest, "a" * 64, root=tmp_path, max_bytes=fm.DOWNLOAD_CHUNK_BYTES
    )
    assert ok is False
    assert any("size cap" in m for m in msgs)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_secure_download_malformed_content_length_falls_through_to_stream_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"ok-bytes"
    _patch_download(monkeypatch, payload, **{"Content-Length": "not-a-number"})
    dest = tmp_path / "a.bin"
    ok, _ = _secure_download(GOOD_URL, dest, hashlib.sha256(payload).hexdigest(), root=tmp_path)
    assert ok is True


# ----- redirect allow-list ------------------------------------------------


def test_redirect_handler_blocks_foreign_host() -> None:
    handler = fm._AllowlistRedirectHandler()
    with pytest.raises(urllib.error.HTTPError) as exc:
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com/payload")
    assert "non-allowlisted host" in str(exc.value)


def test_redirect_handler_blocks_downgrade_to_http() -> None:
    handler = fm._AllowlistRedirectHandler()
    with pytest.raises(urllib.error.HTTPError) as exc:
        handler.redirect_request(None, None, 302, "Found", {}, "http://raw.githubusercontent.com/x")
    assert "non-HTTPS" in str(exc.value)


# ----- _structure_errors --------------------------------------------------


def _make_scene(base: Path, name: str, n_files: int) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        (d / f"frame_{i:05d}.jpg").write_bytes(b"x")


def test_structure_ok(tmp_path: Path) -> None:
    base = tmp_path / "doorway"
    _make_scene(base, "a", 3)
    entry = {
        "id": "d",
        "path": "doorway",
        "structure": {"require_subdirs": ["a"], "min_files_per_subdir": 3},
    }
    assert _structure_errors(entry, tmp_path) == []


def test_structure_reports_missing_subdir(tmp_path: Path) -> None:
    base = tmp_path / "doorway"
    _make_scene(base, "a", 3)
    entry = {
        "id": "d",
        "path": "doorway",
        "structure": {"require_subdirs": ["a", "b"], "min_files_per_subdir": 1},
    }
    errs = _structure_errors(entry, tmp_path)
    assert any("missing required subdir 'b'" in e for e in errs)


def test_structure_reports_too_few_files(tmp_path: Path) -> None:
    base = tmp_path / "doorway"
    _make_scene(base, "a", 2)
    entry = {
        "id": "d",
        "path": "doorway",
        "structure": {"require_subdirs": ["a"], "min_files_per_subdir": 20},
    }
    errs = _structure_errors(entry, tmp_path)
    assert any("has 2 file(s), expected >= 20" in e for e in errs)


def test_structure_blocks_subdir_traversal(tmp_path: Path) -> None:
    (tmp_path / "doorway").mkdir()
    entry = {
        "id": "d",
        "path": "doorway",
        "structure": {"require_subdirs": ["../../etc"], "min_files_per_subdir": 1},
    }
    errs = _structure_errors(entry, tmp_path)
    assert any("escapes the fixture directory" in e for e in errs)


def test_structure_absent_means_no_constraint(tmp_path: Path) -> None:
    assert _structure_errors({"id": "d", "path": "whatever"}, tmp_path) == []


# ----- _run_acquire_script -----------------------------------------------


def _write_script(tmp_path: Path, body: str, name: str = "gen.py") -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


def test_run_acquire_script_success(tmp_path: Path) -> None:
    rel = _write_script(
        tmp_path,
        "from pathlib import Path\n"
        "d = Path('out'); d.mkdir(exist_ok=True)\n"
        "(d / 'f.txt').write_text('ok')\n",
    )
    ok, msgs = _run_acquire_script({"script": rel}, tmp_path)
    assert ok is True, msgs
    assert (tmp_path / "out" / "f.txt").exists()


def test_run_acquire_script_propagates_failure_with_log_tail(tmp_path: Path) -> None:
    rel = _write_script(tmp_path, "print('boom-marker')\nraise SystemExit(3)\n")
    ok, msgs = _run_acquire_script({"script": rel}, tmp_path)
    assert ok is False
    assert any("exited 3" in m for m in msgs)
    assert any("boom-marker" in m for m in msgs)


def test_run_acquire_script_missing_file(tmp_path: Path) -> None:
    ok, msgs = _run_acquire_script({"script": "nope.py"}, tmp_path)
    assert ok is False
    assert any("not found" in m for m in msgs)


def test_run_acquire_script_rejects_escape(tmp_path: Path) -> None:
    ok, msgs = _run_acquire_script({"script": "../outside.py"}, tmp_path)
    assert ok is False
    assert any("escapes repo root" in m for m in msgs)


def test_run_acquire_script_requires_script_key(tmp_path: Path) -> None:
    ok, msgs = _run_acquire_script({}, tmp_path)
    assert ok is False
    assert any("no 'script' declared" in m for m in msgs)


# ----- acquire_entry dispatch --------------------------------------------


def test_acquire_entry_without_block_is_refused(tmp_path: Path) -> None:
    ok, msgs = acquire_entry({"id": "a", "path": "a.bin"}, tmp_path)
    assert ok is False
    assert any("no 'acquire' block" in m for m in msgs)


def test_acquire_entry_unsupported_method(tmp_path: Path) -> None:
    ok, msgs = acquire_entry(
        {"id": "a", "path": "a.bin", "acquire": {"method": "carrier-pigeon"}}, tmp_path
    )
    assert ok is False
    assert any("unsupported acquire.method" in m for m in msgs)


def test_acquire_entry_http_file_writes_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"asset"
    _patch_download(monkeypatch, payload)
    entry = {
        "id": "a",
        "path": "sub/a.bin",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "acquire": {"method": "http-file", "url": GOOD_URL},
    }
    ok, _ = acquire_entry(entry, tmp_path)
    assert ok is True
    assert (tmp_path / "sub" / "a.bin").read_bytes() == payload


def test_acquire_entry_blocks_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    payload = b"evil"
    _patch_download(monkeypatch, payload)
    entry = {
        "id": "a",
        "path": "../escaped.bin",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "acquire": {"method": "http-file", "url": GOOD_URL},
    }
    ok, msgs = acquire_entry(entry, root)
    assert ok is False
    assert any("escapes repo root" in m for m in msgs)
    assert not (tmp_path / "escaped.bin").exists()


# ----- validate_manifest: acquire / structure / dir-hash rules -----------


def test_validate_manifest_rejects_sha256_on_directory_type() -> None:
    data = {
        "fixtures": [
            {
                "id": "d",
                "type": "video",
                "path": "doorway",
                "sha256": "a" * 64,
                "required_for": ["t"],
            }
        ]
    }
    errs = validate_manifest(data)
    assert any("cannot carry a 'sha256'" in e for e in errs)


def test_validate_manifest_acquire_method_whitelist() -> None:
    data = {
        "fixtures": [
            {
                "id": "a",
                "type": "image",
                "path": "p",
                "required_for": ["t"],
                "acquire": {"method": "ftp"},
            }
        ]
    }
    assert any("acquire.method 'ftp' not in" in e for e in validate_manifest(data))


def test_validate_manifest_http_file_requires_https_url() -> None:
    data = {
        "fixtures": [
            {
                "id": "a",
                "type": "image",
                "path": "p",
                "required_for": ["t"],
                "acquire": {"method": "http-file", "url": "http://x/y"},
            }
        ]
    }
    assert any("must be https://" in e for e in validate_manifest(data))


def test_validate_manifest_http_file_requires_url() -> None:
    data = {
        "fixtures": [
            {
                "id": "a",
                "type": "image",
                "path": "p",
                "required_for": ["t"],
                "acquire": {"method": "http-file"},
            }
        ]
    }
    assert any("requires a 'url' string" in e for e in validate_manifest(data))


def test_validate_manifest_script_method_requires_script() -> None:
    data = {
        "fixtures": [
            {
                "id": "a",
                "type": "video",
                "path": "p",
                "required_for": ["t"],
                "acquire": {"method": "script"},
            }
        ]
    }
    assert any("requires a 'script' path" in e for e in validate_manifest(data))


def test_validate_manifest_structure_types() -> None:
    data = {
        "fixtures": [
            {
                "id": "a",
                "type": "video",
                "path": "p",
                "required_for": ["t"],
                "structure": {"require_subdirs": "not-a-list", "min_files_per_subdir": 0},
            }
        ]
    }
    errs = validate_manifest(data)
    assert any("require_subdirs must be a list" in e for e in errs)
    assert any("min_files_per_subdir must be an int >= 1" in e for e in errs)


# ----- main --acquire integration ----------------------------------------


def test_main_acquire_downloads_missing_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"downloaded"
    _patch_download(monkeypatch, payload)
    m = _write_manifest(
        tmp_path,
        [
            {
                "id": "a",
                "type": "image",
                "path": "a.bin",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "required_for": ["t"],
                "acquire": {"method": "http-file", "url": GOOD_URL},
            }
        ],
    )
    rc = main(["--manifest", str(m), "--root", str(tmp_path), "--acquire", "--strict"])
    assert rc == 0
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_main_acquire_still_fails_closed_when_unfixable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Download succeeds but returns the wrong bytes => must stay RED.
    _patch_download(monkeypatch, b"wrong")
    m = _write_manifest(
        tmp_path,
        [
            {
                "id": "a",
                "type": "image",
                "path": "a.bin",
                "sha256": "b" * 64,
                "required_for": ["t"],
                "acquire": {"method": "http-file", "url": GOOD_URL},
            }
        ],
    )
    rc = main(["--manifest", str(m), "--root", str(tmp_path), "--acquire", "--strict"])
    assert rc == 1


def test_main_acquire_repairs_structurally_incomplete_fixture(tmp_path: Path) -> None:
    """A present-but-incomplete derived dir must trigger re-acquisition.

    Regression guard: an earlier revision only acquired when the path was
    entirely absent, so a half-extracted CAVIAR directory stayed broken forever.
    """
    base = tmp_path / "doorway"
    _make_scene(base, "a", 2)  # 'b' missing entirely
    rel = _write_script(
        tmp_path,
        "from pathlib import Path\n"
        "for scene in ('a', 'b'):\n"
        "    d = Path('doorway') / scene\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    for i in range(2):\n"
        "        (d / f'f{i}.jpg').write_bytes(b'x')\n",
    )
    m = _write_manifest(
        tmp_path,
        [
            {
                "id": "d",
                "type": "video",
                "path": "doorway",
                "required_for": ["t"],
                "structure": {"require_subdirs": ["a", "b"], "min_files_per_subdir": 2},
                "acquire": {"method": "script", "script": rel},
            }
        ],
    )
    rc = main(["--manifest", str(m), "--root", str(tmp_path), "--acquire", "--strict"])
    assert rc == 0
    assert (base / "b").is_dir()


def test_main_allow_unpinned_without_acquire_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.bin").write_bytes(b"x")
    m = _write_manifest(
        tmp_path, [{"id": "a", "type": "image", "path": "a.bin", "required_for": ["t"]}]
    )
    main(["--manifest", str(m), "--root", str(tmp_path), "--allow-unpinned"])
    assert "no effect without --acquire" in capsys.readouterr().err


# ----- B2: destination symlink escaping the root is refused -------------


def test_secure_download_blocks_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"x")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unsupported in this environment")
    # `dest` is a symlink resolving OUTSIDE root: must be refused *before* any
    # download, even though `dest` "exists" (the old `dest.parent` check would
    # have missed this by inspecting the parent directory instead).
    ok, msgs = _secure_download(
        "https://raw.githubusercontent.com/x/y", link, "0" * 64, root=tmp_path
    )
    assert ok is False
    assert any("destination escapes repo root" in m for m in msgs)
    assert not link.exists() or link.is_symlink()


# ----- B6: absurd max_bytes is refused --------------------------------


def test_secure_download_refuses_absurd_max_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(GOOD_URL, dest, "0" * 64, root=tmp_path, max_bytes=1)
    assert ok is False
    assert any("max_bytes" in m for m in msgs)
    assert not dest.exists()


# ----- S1: bare github.com top-level is not allow-listed --------------


def test_allowlist_excludes_bare_github_com() -> None:
    assert "github.com" not in fm.ALLOWED_DOWNLOAD_HOSTS


def test_secure_download_rejects_github_com_top_level(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    ok, msgs = _secure_download(
        "https://github.com/owner/repo/asset", dest, "0" * 64, root=tmp_path
    )
    assert ok is False
    assert any("not allow-listed" in m for m in msgs)
    assert not dest.exists()


# ----- T1: _resolve_root fallback chain --------------------------------


def test_resolve_root_explicit_overrides_everything(tmp_path: Path) -> None:
    manifest = tmp_path / "tests" / "fixtures" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("fixtures: []\n", encoding="utf-8")
    root, warnings = fm._resolve_root(manifest, explicit="/srv/ci-root")
    assert root == Path("/srv/ci-root").resolve()
    assert warnings == []


def test_resolve_root_uses_git_toplevel_when_under_tests_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path("/repo").resolve()
    monkeypatch.setattr(fm, "_git_toplevel", lambda: repo)
    manifest = Path("/repo") / "tests" / "fixtures" / "manifest.yaml"
    root, warnings = fm._resolve_root(manifest, None)
    assert root == repo
    assert warnings == []


def test_resolve_root_warns_when_manifest_outside_tests_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path("/repo").resolve()
    monkeypatch.setattr(fm, "_git_toplevel", lambda: repo)
    manifest = Path("/repo") / "other" / "manifest.yaml"
    root, warnings = fm._resolve_root(manifest, None)
    assert root == repo
    assert any("not under tests/fixtures" in w for w in warnings)


def test_resolve_root_falls_back_when_git_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fm, "_git_toplevel", lambda: None)
    manifest = tmp_path / "tests" / "fixtures" / "manifest.yaml"
    root, warnings = fm._resolve_root(manifest, None)
    assert root == manifest.parent.parent.parent
    assert any("could not resolve git top-level" in w for w in warnings)

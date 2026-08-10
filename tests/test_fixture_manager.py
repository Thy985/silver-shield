"""Unit tests for scripts.fixture_manager (Stage-2 fail-closed fixture gate).

These tests build temporary manifests/roots so they never touch the real repo
fixtures or invoke git. Every call passes ``--root`` explicitly, keeping ``main``
deterministic in CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pytest
import yaml

from scripts.fixture_manager import (
    _is_present,
    _sha256_of,
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
    assert _is_present({"id": "vid", "type": "image", "path": "frames"}, tmp_path, warnings) is False
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

"""Validate the committed tests/fixtures/manifest.yaml against its own schema.

This guards against a class of silent breakage the gate is designed to catch:
adding a fixture that omits required fields (id/path/type/required_for) or uses
an unknown type. The manifest is also validated at runtime by
``fixture_manager.main`` (exit 2 on schema error); this test makes the contract
explicit and gives fast local feedback.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import urllib.parse

import yaml

from scripts.fixture_manager import (
    ACQUIRE_METHODS,
    ALLOWED_DOWNLOAD_HOSTS,
    DIR_TYPES,
    TYPE_WHITELIST,
    validate_manifest,
)

MANIFEST = ROOT / "tests" / "fixtures" / "manifest.yaml"


def _load() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_file_exists() -> None:
    assert MANIFEST.is_file()


def test_manifest_passes_own_schema() -> None:
    errors = validate_manifest(_load())
    assert errors == [], f"manifest schema errors: {errors}"


def test_manifest_every_fixture_has_required_fields() -> None:
    for fx in _load()["fixtures"]:
        assert fx.get("id"), fx
        assert fx.get("path"), fx
        assert fx.get("type") in TYPE_WHITELIST, fx
        rf = fx.get("required_for")
        assert isinstance(rf, list) and rf, fx


def test_manifest_type_whitelist_coverage() -> None:
    for fx in _load()["fixtures"]:
        assert fx["type"] in TYPE_WHITELIST


# ----- Stage-2 acquirability contract ------------------------------------
# A fixture that cannot be acquired turns ci-runtime permanently red instead of
# self-healing, which is just a louder version of the false green. These tests
# assert every declared asset is actually reachable by `--acquire`.


def test_every_fixture_declares_how_to_acquire_it() -> None:
    for fx in _load()["fixtures"]:
        acquire = fx.get("acquire")
        assert isinstance(acquire, dict), f"{fx['id']}: missing 'acquire' block"
        assert acquire.get("method") in ACQUIRE_METHODS, fx["id"]


def test_http_file_fixtures_are_pinned_and_allowlisted() -> None:
    for fx in _load()["fixtures"]:
        acquire = fx.get("acquire") or {}
        if acquire.get("method") != "http-file":
            continue
        url = acquire["url"]
        host = urllib.parse.urlparse(url).hostname
        assert url.startswith("https://"), f"{fx['id']}: {url}"
        assert host in ALLOWED_DOWNLOAD_HOSTS, f"{fx['id']}: host {host} not allow-listed"
        # An unpinned http-file entry is refused at acquire time, so shipping one
        # would guarantee a red runtime job.
        sha = fx.get("sha256")
        assert isinstance(sha, str) and len(sha) == 64, f"{fx['id']}: sha256 must be pinned"


def test_http_file_urls_pin_immutable_refs() -> None:
    """No `/main/` or `/master/` raw URLs: upstream could change the bytes."""
    for fx in _load()["fixtures"]:
        acquire = fx.get("acquire") or {}
        if acquire.get("method") != "http-file":
            continue
        url = acquire["url"]
        assert "/main/" not in url and "/master/" not in url, (
            f"{fx['id']}: pin a tag/commit, not a moving branch ({url})"
        )


def test_script_fixtures_point_at_an_existing_script() -> None:
    for fx in _load()["fixtures"]:
        acquire = fx.get("acquire") or {}
        if acquire.get("method") != "script":
            continue
        script = ROOT / acquire["script"]
        assert script.is_file(), f"{fx['id']}: acquire script missing at {acquire['script']}"


def test_directory_fixtures_declare_structure_instead_of_hash() -> None:
    """Derived dirs can't be content-hashed, so they must assert structure."""
    for fx in _load()["fixtures"]:
        if fx["type"] not in DIR_TYPES:
            continue
        assert fx.get("sha256") is None, f"{fx['id']}: directory fixtures cannot pin sha256"
        structure = fx.get("structure")
        assert isinstance(structure, dict), f"{fx['id']}: missing 'structure' integrity check"
        assert structure.get("require_subdirs"), fx["id"]
        assert structure.get("min_files_per_subdir", 0) >= 1, fx["id"]

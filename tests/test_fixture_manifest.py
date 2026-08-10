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

import yaml

from scripts.fixture_manager import TYPE_WHITELIST, validate_manifest

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

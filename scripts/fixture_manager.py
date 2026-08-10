"""Runtime fixture manager — fail-closed AI test-asset governance for ci-runtime.

Part of the Stage-2 fix for the "silent skip / false green" anti-pattern in AI CI
(see .github/workflows/README.md, Principles). `ci-runtime`'s
`prepare-runtime-fixtures` step calls this with ``--strict``.

What it does
------------
Reads ``tests/fixtures/manifest.yaml`` and, for every declared fixture:
  * resolves the repo-relative ``path``
  * checks presence (a file must exist & be non-empty; a dataset/video directory
    must exist & be non-empty)
  * if ``sha256`` is set, verifies the content hash
  * in ``--strict`` mode a MISSING or INVALID required fixture => ``sys.exit(1)``
    (fail-closed) so ci-runtime turns RED instead of silently skipping the real
    inference path.

``--acquire`` (P1) is the hook where download + checksum-verify + cache will live;
until then missing fixtures are simply reported and (under ``--strict``) failed.

Usage
-----
    python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --strict
    python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --acquire
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships in the base CI layer
    yaml = None


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _is_present(entry: dict, root: Path) -> bool:
    rel = entry.get("path")
    if not rel:
        return False
    p = (root / rel).resolve()
    if entry.get("type") in ("video", "dataset", "model") or p.is_dir():
        # dataset / video / model: directory must exist and contain at least one file
        return p.is_dir() and any(p.iterdir())
    return p.is_file() and p.stat().st_size > 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SilverShield runtime fixture manager")
    parser.add_argument("--manifest", required=True, help="path to fixtures/manifest.yaml")
    parser.add_argument("--root", default=None, help="repo root (default: manifest parent's parent)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail-closed: a missing/invalid required fixture => exit 1",
    )
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="P1: download missing fixtures from `source` (not yet implemented)",
    )
    args = parser.parse_args(argv)

    if yaml is None:
        print("[fixture-manager] ERROR: PyYAML missing (base CI layer must provide it)", file=sys.stderr)
        return 2
    manifest = Path(args.manifest)
    if not manifest.is_file():
        print(f"[fixture-manager] ERROR: manifest not found: {manifest}", file=sys.stderr)
        return 2
    root = Path(args.root).resolve() if args.root else manifest.parent.parent.parent

    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    fixtures = data.get("fixtures", [])
    if not fixtures:
        print("[fixture-manager] WARN: manifest declares no fixtures", file=sys.stderr)
        return 0

    print(f"[fixture-manager] validating {len(fixtures)} fixtures (root={root})")
    failures = 0
    for entry in fixtures:
        fid = entry.get("id", "<unnamed>")
        present = _is_present(entry, root)
        status = "OK" if present else "MISSING"
        print(f"  [{status}] {fid} ({entry.get('path')})")
        if present and entry.get("sha256"):
            actual = _sha256_of((root / entry["path"]).resolve())
            if actual != entry["sha256"]:
                print(f"  [FAIL] {fid} checksum mismatch (expected {entry['sha256'][:12]}..., got {actual[:12]}...)")
                failures += 1
                continue
        if not present:
            if args.acquire:
                print(f"  [TODO] --acquire not yet implemented for {fid} (P1)")
            failures += 1

    if failures == 0:
        print(f"[fixture-manager] all {len(fixtures)} fixtures present. PASS.")
        return 0

    print(f"[fixture-manager] {failures} fixture(s) missing/invalid.", file=sys.stderr)
    if args.strict:
        print(
            "[fixture-manager] STRICT mode: failing closed — ci-runtime must NOT silently "
            "skip the real inference path.",
            file=sys.stderr,
        )
        return 1
    print("[fixture-manager] non-strict mode: reporting only (downstream tests will skip).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

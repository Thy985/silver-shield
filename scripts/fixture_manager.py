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
  * if ``sha256`` is set (not None), verifies the content hash (chunked, so GB
    datasets don't OOM)
  * in ``--strict`` mode a MISSING or INVALID *required* fixture => ``sys.exit(1)``
    (fail-closed) so ci-runtime turns RED instead of silently skipping the real
    inference path.

``--acquire`` (P1) is the hook where download + checksum-verify + cache will live;
until then missing fixtures are simply reported and (under ``--strict``) failed.

Logging convention (deviation from AGENTS.md §2.4 "no bare print")
---------------------------------------------------------------------
This is a **CI gate script**: its stdout/stderr go straight into GitHub Actions
step logs, where the ``[fixture-manager] ...`` prefix and the stdout/stderr split
are consumed by the runner UI. Wrapping this in structlog would only obscure the
machine-readable prefixes CI relies on, so bare ``print`` is the accepted,
intentional choice *here*. Do NOT copy this pattern into business modules —
those must use structlog.

Usage
-----
    python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --strict
    python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --acquire
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships in the base CI layer (requirements-ci.txt)
    yaml = None

# Governed AI-asset types. `type` drives presence semantics (dir vs file) and
# must stay in lockstep with the manifest schema test.
TYPE_WHITELIST: frozenset[str] = frozenset({"image", "video", "model", "dataset", "audio"})


def _sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*, read in 1 MiB chunks.

    Chunked on purpose: a future manifest entry may point at a GB-scale dataset
    mirror, and a one-shot ``read_bytes()`` would OOM the runner.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_present(entry: dict, root: Path, warnings: list[str]) -> bool:
    """Whether a fixture's ``path`` resolves to a present, non-empty asset.

    Presence semantics follow the *declared* ``type`` (image/audio => file;
    video/model/dataset => directory). If the declared type disagrees with what
    the path actually is, we emit a warning instead of silently falling back to
    the wrong semantic. ``warnings`` is appended to in place.
    """
    fid = entry.get("id", "<unnamed>")
    rel = entry.get("path")
    if not rel:
        return False
    raw = root / rel
    p = raw.resolve()
    declared = entry.get("type")
    is_dir_attr = p.is_dir()
    is_file_attr = p.is_file()

    if declared in ("video", "model", "dataset"):
        expect_dir = True
    elif declared in ("image", "audio"):
        expect_dir = False
    else:
        # Unknown / absent type -> infer from the path attribute (never crash).
        expect_dir = is_dir_attr and not is_file_attr

    # Type/real-shape mismatch: warn loudly, keep declared-type semantics.
    if declared in ("video", "model", "dataset") and is_file_attr and not is_dir_attr:
        warnings.append(f"{fid}: type='{declared}' but path resolves to a FILE, not a directory")
    if declared in ("image", "audio") and is_dir_attr and not is_file_attr:
        warnings.append(f"{fid}: type='{declared}' but path resolves to a DIRECTORY, not a file")

    # Symlink safety: a fixture symlinked outside the repo root passes presence
    # but is a governance risk — call it out rather than silently accepting it.
    # Check the raw (unresolved) link, since `p` is already the resolved target.
    if raw.is_symlink():
        try:
            p.relative_to(root)
        except ValueError:
            warnings.append(
                f"{fid}: path is a symlink resolving OUTSIDE repo root ({p.resolve()}); "
                "fixtures must resolve to git-tracked / controlled locations"
            )

    if expect_dir:
        return is_dir_attr and any(p.iterdir())
    return is_file_attr and p.stat().st_size > 0


def _git_toplevel() -> Path | None:
    """Best-effort git top-level resolution; None on any failure (no git, timeout)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).resolve()


def _resolve_root(manifest: Path, explicit: str | None) -> tuple[Path, list[str]]:
    """Resolve the repo root used to expand repo-relative fixture paths.

    Returns ``(root, warnings)``. Prefers ``git rev-parse --show-toplevel`` so the
    script keeps working when the manifest is renamed or the repo is relocated
    inside a monorepo. Falls back to ``manifest.parent.parent.parent`` only when
    git is unavailable, and always warns if the manifest is not where the
    convention expects it (so a mis-placed manifest can't silently point at the
    wrong tree).
    """
    warnings: list[str] = []
    if explicit:
        return Path(explicit).resolve(), warnings

    top = _git_toplevel()
    if top is not None:
        try:
            rel = manifest.resolve().relative_to(top)
            rel_s = str(rel).replace(os.sep, "/")
            if not rel_s.startswith("tests/fixtures/"):
                warnings.append(
                    f"manifest lives at {rel_s}, not under tests/fixtures/; "
                    "if intentional, pass --root explicitly"
                )
        except ValueError:
            warnings.append("manifest is outside the git top-level; pass --root explicitly")
        return top, warnings

    fallback = manifest.parent.parent.parent
    warnings.append(
        f"could not resolve git top-level; defaulting root to {fallback} "
        "(assumes manifest is tests/fixtures/manifest.yaml). Pass --root to override."
    )
    return fallback, warnings


def validate_manifest(data: dict) -> list[str]:
    """Validate the manifest structure. Returns a list of human-readable errors.

    A manifest with errors is rejected by ``main`` with exit code 2, so a
    partially-edited fixtures file can never silently pass the gate.
    """
    errors: list[str] = []
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list):
        return ["top-level 'fixtures' must be a list"]
    seen: set[str] = set()
    for i, entry in enumerate(fixtures):
        loc = f"fixtures[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{loc}: must be a mapping")
            continue
        fid = entry.get("id", f"<index {i}>")
        if not entry.get("id"):
            errors.append(f"{loc}: missing required field 'id'")
        elif entry["id"] in seen:
            errors.append(f"{loc}: duplicate id '{entry['id']}'")
        else:
            seen.add(entry["id"])
        if not entry.get("path"):
            errors.append(f"{loc} ({fid}): missing required field 'path'")
        if "type" not in entry:
            errors.append(f"{loc} ({fid}): missing required field 'type'")
        elif entry["type"] not in TYPE_WHITELIST:
            errors.append(f"{loc} ({fid}): type '{entry['type']}' not in {sorted(TYPE_WHITELIST)}")
        required_for = entry.get("required_for")
        if not isinstance(required_for, list) or not required_for:
            errors.append(f"{loc} ({fid}): 'required_for' must be a non-empty list")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SilverShield runtime fixture manager")
    parser.add_argument("--manifest", required=True, help="path to fixtures/manifest.yaml")
    parser.add_argument("--root", default=None, help="repo root (default: git top-level, else manifest parent's parent)")
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

    try:
        text = manifest.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        print(f"[fixture-manager] ERROR: failed to parse manifest YAML: {exc}", file=sys.stderr)
        return 2

    schema_errors = validate_manifest(data)
    if schema_errors:
        for err in schema_errors:
            print(f"[fixture-manager] ERROR: manifest schema: {err}", file=sys.stderr)
        return 2

    fixtures = data["fixtures"]
    if not fixtures:
        print("[fixture-manager] WARN: manifest declares no fixtures", file=sys.stderr)
        return 0

    root, root_warnings = _resolve_root(manifest, args.root)
    for warn in root_warnings:
        print(f"[fixture-manager] WARN: {warn}", file=sys.stderr)

    print(f"[fixture-manager] validating {len(fixtures)} fixtures (root={root})")
    failures = 0
    for entry in fixtures:
        fid = entry.get("id", "<unnamed>")
        warnings: list[str] = []
        present = _is_present(entry, root, warnings)
        for warn in warnings:
            print(f"[fixture-manager] WARN: {warn}", file=sys.stderr)
        status = "OK" if present else "MISSING"
        print(f"  [{status}] {fid} ({entry.get('path')})")

        sha = entry.get("sha256")
        if present and sha is not None:
            actual = _sha256_of((root / entry["path"]).resolve())
            if actual != sha:
                print(
                    f"  [FAIL] {fid} checksum mismatch "
                    f"(expected {sha[:12]}..., got {actual[:12]}...)"
                )
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

"""Runtime fixture manager — fail-closed AI test-asset governance for ci-runtime.

Part of the Stage-2 fix for the "silent skip / false green" anti-pattern in AI CI
(see .github/workflows/README.md, Principles). `ci-runtime`'s
`prepare-runtime-fixtures` step calls this with ``--acquire --strict``.

What it does
------------
Reads ``tests/fixtures/manifest.yaml`` and, for every declared fixture:
  * resolves the repo-relative ``path``
  * checks presence (a file must exist & be non-empty; a dataset/video directory
    must exist & be non-empty)
  * if ``sha256`` is set (not None), verifies the content hash (chunked, so GB
    datasets don't OOM)
  * if ``structure`` is set, verifies required sub-directories and minimum file
    counts (the integrity check for *derived* assets that cannot be hashed)
  * with ``--acquire``, downloads/produces anything missing (see below)
  * in ``--strict`` mode a MISSING or INVALID *required* fixture => ``sys.exit(1)``
    (fail-closed) so ci-runtime turns RED instead of silently skipping the real
    inference path.

Acquisition model (``--acquire``)
---------------------------------
Two methods, declared per fixture under ``acquire.method``:

``http-file``
    A single immutable artifact fetched over HTTPS straight to ``path``.
    Hardened: HTTPS-only (plain ``http://`` is rejected, never silently
    upgraded), redirects restricted to :data:`ALLOWED_DOWNLOAD_HOSTS`, streamed
    in 1 MiB chunks to a ``.part`` file with a hard size cap, SHA-256 verified
    *before* the atomic rename into place. A hash mismatch leaves nothing behind.

``script``
    A repo-local generator (e.g. download upstream video + ffmpeg frame
    extraction). The script owns its own upstream hash pinning; this module
    verifies the *result* via ``structure``.

Why ``sha256: null`` blocks acquisition
    An unpinned hash means "we cannot prove what we downloaded". Acquiring it
    anyway would reintroduce exactly the trust gap Stage 2 exists to close, so
    ``http-file`` refuses. Bootstrapping a new fixture is an explicit, local,
    human act: run with ``--allow-unpinned``, read the printed digest, paste it
    into the manifest, commit. CI never passes that flag.

Why derived directories are not content-hashed
    ffmpeg output is not bit-reproducible across versions/platforms, so pinning
    a digest of extracted frames would fail on any runner whose ffmpeg differs.
    Those fixtures pin their *upstream inputs* (inside the acquire script) and
    assert ``structure`` here instead.

Caching
    The fixture ``path`` itself is the cache: a present, hash/structure-valid
    asset is never re-downloaded. CI layers ``actions/cache`` on top of the same
    paths, so a warm run does zero network I/O.

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
    python scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --acquire --strict
    # bootstrap a new fixture locally (prints the digest to pin):
    python scripts/fixture_manager.py --manifest ... --acquire --allow-unpinned
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships in the base CI layer (requirements-ci.txt)
    yaml = None

# Governed AI-asset types. `type` drives presence semantics (dir vs file) and
# must stay in lockstep with the manifest schema test.
TYPE_WHITELIST: frozenset[str] = frozenset({"image", "video", "model", "dataset", "audio"})
DIR_TYPES: frozenset[str] = frozenset({"video", "model", "dataset"})
FILE_TYPES: frozenset[str] = frozenset({"image", "audio"})

ACQUIRE_METHODS: frozenset[str] = frozenset({"http-file", "script"})

# Hosts we are willing to fetch fixtures from, including redirect targets.
# Adding an entry is a governance decision: it widens the trusted supply chain.
ALLOWED_DOWNLOAD_HOSTS: frozenset[str] = frozenset(
    {
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",  # GitHub release/raw redirect target
        "github.com",
        "homepages.inf.ed.ac.uk",  # CAVIAR upstream
    }
)

# Hard ceiling for a single `http-file` download; prevents a compromised or
# mis-pinned URL from filling the runner disk. Override per entry via
# `acquire.max_bytes` when a legitimately larger asset is added.
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_SECONDS = 120
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 1800

USER_AGENT = "silver-shield-fixture-manager/1.0 (+https://github.com/Thy985/silver-shield)"


def _out(msg: str) -> None:
    """stdout, unbuffered — keeps GH Actions log ordering faithful vs :func:`_err`."""
    print(msg, flush=True)


def _err(msg: str) -> None:
    """stderr, unbuffered (see :func:`_out`)."""
    print(msg, file=sys.stderr, flush=True)


def _sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*, read in 1 MiB chunks.

    Chunked on purpose: a future manifest entry may point at a GB-scale dataset
    mirror, and a one-shot ``read_bytes()`` would OOM the runner.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_within(child: Path, parent: Path) -> bool:
    """True when *child* resolves inside *parent* (path-traversal guard)."""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave HTTPS or the host allow-list.

    urllib follows redirects transparently by default, which would let an
    upstream 302 silently move the download to an arbitrary origin. Blocking it
    here keeps the trusted-host decision in this file.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https":
            raise urllib.error.HTTPError(
                newurl, code, f"blocked redirect to non-HTTPS URL: {newurl}", headers, fp
            )
        if parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
            raise urllib.error.HTTPError(
                newurl,
                code,
                f"blocked redirect to non-allowlisted host: {parsed.hostname}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, timeout: int = DOWNLOAD_TIMEOUT_SECONDS):
    """Open *url* through the allow-listed-redirect opener.

    Seam for tests: monkeypatch this to avoid real network I/O.
    """
    opener = urllib.request.build_opener(_AllowlistRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return opener.open(req, timeout=timeout)


def _secure_download(
    url: str,
    dest: Path,
    expected_sha: str | None,
    *,
    root: Path,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    allow_unpinned: bool = False,
) -> tuple[bool, list[str]]:
    """Fetch *url* into *dest*, verifying scheme, host, size and digest.

    Returns ``(ok, messages)``. Nothing is written to *dest* unless every check
    passes: the body streams into a sibling ``.part`` file that is hashed and
    only then atomically renamed, so a failed or tampered download can never be
    mistaken for a valid fixture on a later run.
    """
    msgs: list[str] = []

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        # Deliberately NOT auto-upgrading http->https: a silent transformation is
        # the same class of bug as a silent skip. Pin the https URL explicitly.
        msgs.append(f"refusing non-HTTPS source '{url}' (pin an https:// URL in the manifest)")
        return False, msgs
    if parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        msgs.append(
            f"host '{parsed.hostname}' is not allow-listed "
            f"(allowed: {', '.join(sorted(ALLOWED_DOWNLOAD_HOSTS))})"
        )
        return False, msgs

    if expected_sha is None and not allow_unpinned:
        msgs.append(
            "sha256 is null — refusing to acquire an unverifiable asset. "
            "Run locally with --allow-unpinned to print the digest, then pin it."
        )
        return False, msgs

    if not _is_within(dest.parent if not dest.exists() else dest, root):
        msgs.append(f"destination escapes repo root: {dest}")
        return False, msgs

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    h = hashlib.sha256()
    total = 0
    try:
        with _open_url(url) as resp:
            declared = resp.headers.get("Content-Length") if resp.headers else None
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        msgs.append(f"declared size {declared}B exceeds cap {max_bytes}B")
                        return False, msgs
                except ValueError:
                    pass  # malformed header: fall through to the streaming cap
            with part.open("wb") as fh:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        msgs.append(f"download exceeded size cap {max_bytes}B; aborted")
                        fh.close()
                        part.unlink(missing_ok=True)
                        return False, msgs
                    h.update(chunk)
                    fh.write(chunk)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        msgs.append(f"download failed: {type(exc).__name__}: {exc}")
        return False, msgs

    if total == 0:
        part.unlink(missing_ok=True)
        msgs.append("download produced an empty file")
        return False, msgs

    actual = h.hexdigest()
    if expected_sha is None:
        part.replace(dest)
        msgs.append(f"UNPINNED download accepted ({total}B). Pin this in the manifest:")
        msgs.append(f"    sha256: {actual}")
        return True, msgs

    if actual != expected_sha:
        part.unlink(missing_ok=True)
        msgs.append(
            f"checksum mismatch: expected {expected_sha[:12]}..., got {actual[:12]}... "
            "(discarded, nothing written)"
        )
        return False, msgs

    part.replace(dest)
    msgs.append(f"downloaded {total}B, sha256 verified")
    return True, msgs


def _run_acquire_script(spec: dict, root: Path) -> tuple[bool, list[str]]:
    """Run a repo-local generator script that materialises a derived fixture."""
    msgs: list[str] = []
    rel = spec.get("script")
    if not rel:
        msgs.append("acquire.method='script' but no 'script' declared")
        return False, msgs
    script = (root / rel).resolve()
    if not _is_within(script, root):
        msgs.append(f"acquire script escapes repo root: {rel}")
        return False, msgs
    if not script.is_file():
        msgs.append(f"acquire script not found: {rel}")
        return False, msgs

    timeout = spec.get("timeout_seconds", DEFAULT_SCRIPT_TIMEOUT_SECONDS)
    cmd = [sys.executable, str(script), *[str(a) for a in spec.get("args", [])]]
    msgs.append(f"running {' '.join(cmd[1:])} (timeout {timeout}s)")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        msgs.append(f"acquire script timed out after {timeout}s")
        return False, msgs
    except OSError as exc:
        msgs.append(f"acquire script could not start: {exc}")
        return False, msgs

    if proc.returncode != 0:
        tail = (proc.stdout or "").splitlines()[-15:]
        for line in tail:
            msgs.append(f"    | {line}")
        err = (proc.stderr or "").strip().splitlines()
        if err:
            msgs.append(f"    stderr: {err[-1]}")
        msgs.append(f"acquire script exited {proc.returncode}")
        return False, msgs

    msgs.append("acquire script completed")
    return True, msgs


def acquire_entry(entry: dict, root: Path, *, allow_unpinned: bool = False) -> tuple[bool, list[str]]:
    """Materialise one missing fixture. Returns ``(ok, messages)``."""
    fid = entry.get("id", "<unnamed>")
    spec = entry.get("acquire")
    if not isinstance(spec, dict):
        return False, [f"{fid}: no 'acquire' block — cannot self-heal, add one or ship the asset"]

    method = spec.get("method")
    if method == "http-file":
        dest = (root / entry["path"]).resolve()
        return _secure_download(
            spec.get("url", ""),
            dest,
            entry.get("sha256"),
            root=root,
            max_bytes=spec.get("max_bytes", DEFAULT_MAX_DOWNLOAD_BYTES),
            allow_unpinned=allow_unpinned,
        )
    if method == "script":
        return _run_acquire_script(spec, root)
    return False, [f"{fid}: unsupported acquire.method '{method}'"]


def _structure_errors(entry: dict, root: Path) -> list[str]:
    """Integrity check for derived directory fixtures that cannot be hashed."""
    spec = entry.get("structure")
    if not isinstance(spec, dict):
        return []
    base = (root / entry["path"]).resolve()
    errors: list[str] = []
    min_files = spec.get("min_files_per_subdir", 1)
    for sub in spec.get("require_subdirs", []):
        target = (base / str(sub)).resolve()
        if not _is_within(target, base):
            errors.append(f"structure subdir '{sub}' escapes the fixture directory")
            continue
        if not target.is_dir():
            errors.append(f"missing required subdir '{sub}'")
            continue
        n_files = sum(1 for child in target.iterdir() if child.is_file())
        if n_files < min_files:
            errors.append(f"subdir '{sub}' has {n_files} file(s), expected >= {min_files}")
    return errors


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

    if declared in DIR_TYPES:
        expect_dir = True
    elif declared in FILE_TYPES:
        expect_dir = False
    else:
        # Unknown / absent type -> infer from the path attribute (never crash).
        expect_dir = is_dir_attr and not is_file_attr

    # Type/real-shape mismatch: warn loudly, keep declared-type semantics.
    if declared in DIR_TYPES and is_file_attr and not is_dir_attr:
        warnings.append(f"{fid}: type='{declared}' but path resolves to a FILE, not a directory")
    if declared in FILE_TYPES and is_dir_attr and not is_file_attr:
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


def _validate_acquire(entry: dict, loc: str, fid: str, errors: list[str]) -> None:
    """Schema-check the optional ``acquire`` block (appends to *errors*)."""
    spec = entry.get("acquire")
    if spec is None:
        return
    if not isinstance(spec, dict):
        errors.append(f"{loc} ({fid}): 'acquire' must be a mapping")
        return
    method = spec.get("method")
    if method not in ACQUIRE_METHODS:
        errors.append(
            f"{loc} ({fid}): acquire.method '{method}' not in {sorted(ACQUIRE_METHODS)}"
        )
        return
    if method == "http-file":
        url = spec.get("url")
        if not url or not isinstance(url, str):
            errors.append(f"{loc} ({fid}): acquire.method='http-file' requires a 'url' string")
        elif not url.startswith("https://"):
            errors.append(f"{loc} ({fid}): acquire.url must be https:// (got '{url}')")
    elif method == "script" and not spec.get("script"):
        errors.append(f"{loc} ({fid}): acquire.method='script' requires a 'script' path")


def _validate_structure(entry: dict, loc: str, fid: str, errors: list[str]) -> None:
    """Schema-check the optional ``structure`` block (appends to *errors*)."""
    spec = entry.get("structure")
    if spec is None:
        return
    if not isinstance(spec, dict):
        errors.append(f"{loc} ({fid}): 'structure' must be a mapping")
        return
    subdirs = spec.get("require_subdirs")
    if subdirs is not None and (
        not isinstance(subdirs, list) or not all(isinstance(s, str) for s in subdirs)
    ):
        errors.append(f"{loc} ({fid}): structure.require_subdirs must be a list of strings")
    min_files = spec.get("min_files_per_subdir")
    if min_files is not None and (not isinstance(min_files, int) or min_files < 1):
        errors.append(f"{loc} ({fid}): structure.min_files_per_subdir must be an int >= 1")


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
        entry_type = entry.get("type")
        if "type" not in entry:
            errors.append(f"{loc} ({fid}): missing required field 'type'")
        elif entry_type not in TYPE_WHITELIST:
            errors.append(f"{loc} ({fid}): type '{entry_type}' not in {sorted(TYPE_WHITELIST)}")
        required_for = entry.get("required_for")
        if not isinstance(required_for, list) or not required_for:
            errors.append(f"{loc} ({fid}): 'required_for' must be a non-empty list")

        # A directory fixture has no single content hash. Silently ignoring a
        # stray sha256 there would look like verification while verifying nothing.
        if entry_type in DIR_TYPES and entry.get("sha256") is not None:
            errors.append(
                f"{loc} ({fid}): type '{entry_type}' is a directory and cannot carry a "
                "'sha256'; use 'structure' (and pin upstream hashes in the acquire script)"
            )
        _validate_acquire(entry, loc, fid, errors)
        _validate_structure(entry, loc, fid, errors)
    return errors


def _check_entry(entry: dict, root: Path) -> tuple[bool, list[str], list[str]]:
    """Evaluate one fixture. Returns ``(present, warnings, failures)``."""
    warnings: list[str] = []
    failures: list[str] = []
    present = _is_present(entry, root, warnings)
    if not present:
        return False, warnings, failures

    sha = entry.get("sha256")
    if sha is not None:
        actual = _sha256_of((root / entry["path"]).resolve())
        if actual != sha:
            failures.append(f"checksum mismatch (expected {sha[:12]}..., got {actual[:12]}...)")
    failures.extend(_structure_errors(entry, root))
    return True, warnings, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SilverShield runtime fixture manager")
    parser.add_argument("--manifest", required=True, help="path to fixtures/manifest.yaml")
    parser.add_argument(
        "--root",
        default=None,
        help="repo root (default: git top-level, else manifest parent's parent)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail-closed: a missing/invalid required fixture => exit 1",
    )
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="download/generate missing fixtures declared under 'acquire'",
    )
    parser.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="local bootstrap only: acquire an asset whose sha256 is null and print the digest",
    )
    args = parser.parse_args(argv)

    if yaml is None:
        _err("[fixture-manager] ERROR: PyYAML missing (base CI layer must provide it)")
        return 2

    manifest = Path(args.manifest)
    if not manifest.is_file():
        _err(f"[fixture-manager] ERROR: manifest not found: {manifest}")
        return 2

    try:
        text = manifest.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        _err(f"[fixture-manager] ERROR: failed to parse manifest YAML: {exc}")
        return 2

    schema_errors = validate_manifest(data)
    if schema_errors:
        for err in schema_errors:
            _err(f"[fixture-manager] ERROR: manifest schema: {err}")
        return 2

    fixtures = data["fixtures"]
    if not fixtures:
        _err("[fixture-manager] WARN: manifest declares no fixtures")
        return 0

    root, root_warnings = _resolve_root(manifest, args.root)
    for warn in root_warnings:
        _err(f"[fixture-manager] WARN: {warn}")

    if args.allow_unpinned and not args.acquire:
        _err("[fixture-manager] WARN: --allow-unpinned has no effect without --acquire")

    mode = "acquire+validate" if args.acquire else "validate"
    _out(f"[fixture-manager] {mode}: {len(fixtures)} fixtures (root={root})")

    failures = 0
    for entry in fixtures:
        fid = entry.get("id", "<unnamed>")
        present, warnings, entry_failures = _check_entry(entry, root)

        # Re-acquire on *invalid* too, not just absent: a half-extracted CAVIAR
        # directory or a corrupted download is present-but-useless, and treating
        # it as unrecoverable would leave the runner permanently red for a
        # condition the acquire step can fix.
        if args.acquire and (not present or entry_failures):
            reason = "missing" if not present else "present but invalid"
            _out(f"  [GET]  {fid}: {reason}, acquiring...")
            ok, msgs = acquire_entry(entry, root, allow_unpinned=args.allow_unpinned)
            for msg in msgs:
                _out(f"         {msg}")
            if ok:
                present, warnings, entry_failures = _check_entry(entry, root)

        for warn in warnings:
            _err(f"[fixture-manager] WARN: {warn}")

        if not present:
            _out(f"  [MISSING] {fid} ({entry.get('path')})")
            failures += 1
            continue
        if entry_failures:
            for problem in entry_failures:
                _out(f"  [FAIL] {fid}: {problem}")
            failures += 1
            continue
        _out(f"  [OK]   {fid} ({entry.get('path')})")

    if failures == 0:
        _out(f"[fixture-manager] all {len(fixtures)} fixtures present & verified. PASS.")
        return 0

    _err(f"[fixture-manager] {failures} fixture(s) missing/invalid.")
    if args.strict:
        _err(
            "[fixture-manager] STRICT mode: failing closed — ci-runtime must NOT silently "
            "skip the real inference path."
        )
        return 1
    _out("[fixture-manager] non-strict mode: reporting only (downstream tests will skip).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

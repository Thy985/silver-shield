# CI Workflows Governance

## Overview

This directory contains the CI governance pipeline for SilverShield.

The workflows are intentionally separated by responsibility:

                Pull Request

                     |
        +------------+------------+
        |            |            |
     Quality       Test       Benchmark
        |            |            |
        +------------+------------+

                     |

              Main / Manual Trigger

                     |

                 Runtime

- **Pull Request** runs the three fast, deterministic, blocking gates —
  `ci-quality`, `ci-test`, `ci-benchmark`. They give contributors immediate
  feedback **without** pulling the heavy AI stack.
- **Main (post-merge) / Manual (`workflow_dispatch`)** runs `ci-runtime`, the
  full AI-stack runtime validation. It is deliberately separated from PR
  feedback (see [Trigger Policy](#trigger-policy)).

CI is not a bag of scripts. It is a *validation system*: each workflow only
orchestrates; the real logic lives in Python entrypoints under `scripts/`, and
every blocking workflow must leave downloadable evidence behind.

## Workflow Responsibility

| Workflow | Trigger | Purpose | Gate |
|---|---|---|---|
| `ci-quality` | PR | Static quality gate: `ruff`, `black`, YAML & scenario-schema validation, phase-consistency SSOT lock | Blocking |
| `ci-test` | PR | Contract / Unit / Integration tests (torch-free closed loop) | Blocking |
| `ci-benchmark` | PR | Benchmark Harness + Regression Gate (ADR-0033), baseline-bump governance | Blocking |
| `ci-runtime` | `main` / manual | Full AI-stack runtime validation (real YOLO inference path) — ADR-0034 integration slot | Non-blocking / Release Gate |

> All four workflows also carry `push: [main]` and `workflow_dispatch` so they
> can be re-run on demand. `ci-runtime` is intentionally **not** wired to
> `pull_request` — see [Trigger Policy](#trigger-policy).

## Principles

1. **Workflow files only orchestrate execution.** No test paths, version pins,
   or validation rules live in YAML. YAML calls a Python entrypoint.
2. **Validation logic belongs to Python entrypoints.** `scripts/run_tests.py`,
   `scripts/run_benchmark.py`, `scripts/run_integration.py`,
   `scripts/validate_yaml.py`, `scripts/phase_consistency_check.py`, and
   `scripts/check_baseline_bump.py` are the single source of truth.
3. **Every blocking workflow must produce artifacts.** A green check with no
   downloadable evidence is not acceptable — failure must be investigable after
   the runner is destroyed.
4. **Benchmark results must be reproducible.** Dependencies are pinned in
   `requirements-ci.txt` / `requirements-ci-ai.txt`; the committed baseline
   records `provenance.runtime_dependencies` so a run can be replayed across
   time and OS.
5. **Runtime-heavy validation is separated from fast PR feedback.**
   `ci-runtime` installs torch + ultralytics + downloads weights (~20 min) and
   therefore runs only on `main` / manual, never on every PR.
6. **No silent skips in the runtime path (anti false-green).**
   In an ordinary software repo a skip is a convenience; in an AI system a skip
   caused by a missing model / dataset / GPU path means CI is validating a
   pipeline that never actually executed — and still reporting green. That is the
   most dangerous CI anti-pattern for AI systems. `ci-runtime` therefore runs a
   **fail-closed fixture gate** (`scripts/fixture_manager.py --strict`) *before*
   the test suite: if a required runtime fixture (real YOLO image, CAVIAR
   scenario assets) is absent, the workflow turns **red**, never silently green.

## ADR Mapping

You should not need to open the ADRs to understand which CI surface enforces
them.

| ADR | Concern | Workflow |
|---|---|---|
| ADR-0031 | Decision trace & audit lineage | `ci-benchmark` (gate), `ci-runtime` (IntegrationReport traces) |
| ADR-0032 | Scenario contract & simulation layer | `ci-benchmark` (Scenario→Generator→Pipeline), `ci-quality` (schema validation) |
| ADR-0033 | Benchmark gate & regression baseline | `ci-benchmark` |
| ADR-0034 | Runtime integration (Scenario→Runtime→Memory→Decision→Notification) | `ci-runtime` |

## Trigger Policy

> **Why doesn't the PR run YOLO?**
> Because that is governance design, not an omission.

**Pull Request is for fast, deterministic, limited-AI feedback.**
- Runs in seconds-to-minutes on a torch-free environment.
- Covers contracts, units, the torch-free integration closed loop, the benchmark
  regression gate, and static quality.
- Must stay cheap so contributors get signal quickly and often.

**Main (post-merge) / Manual is for full runtime validation.**
- Installs the complete AI stack (CPU torch, ultralytics, model weights).
- Executes the real model inference path (YOLO detection) end-to-end.
- Acts as the release / regression gate before the runtime is trusted.

Keeping `ci-runtime` off the PR path is deliberate: forcing a ~20-minute AI-stack
install on every PR would drag down the feedback loop that `ci-test` +
`ci-benchmark` already guard with two independent layers.

## Artifacts

Every blocking workflow uploads evidence to GitHub Artifacts. Download them from
the run summary when a check fails.

**`ci-test`** (three uploads, one per tier)
- `test-contract` → `artifacts/junit-contract.xml`, `artifacts/coverage-contract.xml`
- `test-unit` → `artifacts/junit-unit.xml`, `artifacts/coverage-unit.xml`
- `test-integration` → `artifacts/junit-integration.xml`, `artifacts/coverage-integration.xml`

**`ci-benchmark`**
- `benchmark-gate-report` → `benchmark-gate-report.json`
  (end-to-end `--gate` output: benchmark metrics **and** the regression diff
  against the committed baseline)
- `baseline-bump-check` runs as a separate job on PRs and fails the build when a
  baseline JSON changes without the `benchmark-baseline-bump` marker.

**`ci-runtime`**
- `integration-report` → `artifacts/IntegrationReport.json`
  (structured summary parsed from real pytest results), plus
  `artifacts/junit.xml` and `artifacts/coverage.xml`, and decision/action
  *trace artifacts* once ADR-0034 scenarios are wired in.

## Fail-Closed Fixture Gate (Stage 2 — anti false-green)

The single most dangerous failure mode in AI-system CI is the **silent skip**:
a test that should exercise the real inference path is skipped because its
fixture (model / video / image / dataset) is missing, and the run still reports
green. In an ordinary software repo a skip is a convenience; in an AI system it
means CI is validating a pipeline that never actually executed.

`ci-runtime` defends against this in two layers:

1. **Manifest as single source of truth** — `tests/fixtures/manifest.yaml`
   declares every required AI asset (`id`, `type`, `path`, `source`, `sha256`,
   `license`, `required_for`). Unlike ordinary pip dependencies, AI assets carry
   *version / checksum / source / size / license* metadata and must be governed,
   not just downloaded.
2. **Fail-closed gate before the suite** — the workflow runs
   `scripts/fixture_manager.py --manifest tests/fixtures/manifest.yaml --strict`
   as a dedicated step between install and `run_integration.py`. Under
   `--strict`, a missing *required* fixture makes the script `exit 1`, so the job
   fails loudly instead of letting `pytest.skip()` hide the gap.

**Current state (P0):** the gate is wired and fail-closed, but the manifest's
fixtures are not yet auto-acquired in CI (their `source`/`sha256` are
placeholders pending P1). Until P1 lands the download + checksum + cache step,
`ci-runtime` will correctly turn **red** when the real inference assets are
absent — which is the intended, honest signal. P1 will add `--acquire` to fetch
and verify them so the genuine YOLO / CAVIAR path actually runs.

Stage roadmap (per governance decision):

| Stage | Goal | Status |
|---|---|---|
| 1 | CI governance infrastructure (4 workflows + locked deps + entrypoints) | ✅ Done (#174, #175) |
| 2 | Eliminate false-green (fail-closed fixture gate) | ✅ This change |
| 3 | Real YOLO smoke closed loop (image→YOLO→Detection→Tracker→Decision→Trace→Benchmark) | P1 |
| 4 | CAVIAR real scenario closed loop | P2 |
| 5 | ADR-0034 integration | P3 |

## Related (non-governance) workflows

`deploy.yml` and `claude-review.yml` also live in this directory but are outside
the four-workflow governance contract above; they handle deployment and
assistant review respectively.

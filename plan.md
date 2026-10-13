# Duo production-readiness plan

## Goal and release scope

Prepare Duo for reliable unattended operation as a single-user planning CLI on
macOS and Linux. Keep the existing two-reviewer workflow: Python publishes
`plan.md`, any revision clears both approvals, and a run succeeds only when both
reviewers approve the same plan. Agreement does not prove a plan is correct.

The first production release supports only CLI versions and backend combinations
that have been explicitly tested. Define the supported security boundary before
calling Duo production-ready. Using Duo on sensitive or untrusted repositories
requires a separately validated isolation profile; running reviewers in ordinary
read-only mode does not provide one.

This document plans implementation and validation. Writing it does not authorize
publishing a release, distributing credentials, or making model calls.

## Starting evidence

- The last verified run passed 95 tests. These included real LangGraph execution
  with fake CLI executables, publication failures, retries, deadlines, process
  cleanup, audit records, and credential-filter regressions. The tree now defines
  97 `test_*` methods, so rerun the suite before using this as a baseline.
- A disposable RSA key validated with OpenSSL confirmed the latest fix for
  detecting keys with short body lines. This verifies that case only, not secret
  detection in general.
- The authenticated Copilot smoke test stopped during preflight because the
  session had no environment token. That test made no live model call.
- The repository includes a live inspection smoke script. The checks in this
  session do not show live coverage of every supported backend combination.
- Package metadata declares Python >=3.10 and `langgraph>=1.0,<2`. The repository
  has no CI workflow, no release dependency constraints, and no license file.
- `verify_version` in `src/duo/policy.py` accepts any release at or above the
  first-tested version within the same major version. For Codex (first tested at
  0.162.1), this accepts every future 0.x release.
- Native Codex's read-only sandbox allows reads outside the repository. Raw
  rejected responses remain in the private audit directory.

Reconfirm this baseline against the release candidate. Results from earlier runs
are not evidence for a different revision or environment.

## 1. Define the supported deployment and compatibility contract

Relevant files: `README.md`, `pyproject.toml`, `src/duo/policy.py`.

- [ ] Document a baseline deployment:
  - one local user;
  - trusted repositories;
  - unmanaged CLI configuration;
  - provider data-handling settings that have been reviewed;
  - a dedicated working environment holding only the data and credentials it needs.
- [ ] Keep a release compatibility matrix recording supported operating systems,
  Python versions, CLI versions, model identifiers, authentication methods, and
  backend combinations. Mark which configurations are tested and which are
  experimental. Section 3 keeps this matrix current.
- [ ] Align the Python version in package metadata with the versions Duo and its
  resolved dependencies actually support. Test the declared minimum version.
- [ ] Document restart behavior: a rerun reviews the latest saved plan with fresh
  approvals. Audit files are not resumable LangGraph checkpoints.

Acceptance: a user can identify a supported deployment without guessing the
versions, authentication method, or security guarantees that apply.

## 2. Establish and test the security boundary

Relevant files: `src/duo/policy.py`, `src/duo/copilot.py`,
`src/duo/adapters.py`, `src/duo/models.py`, `scripts/smoke_inspection.py`.

- [ ] Document which files, environment values, tools, network destinations, and
  authentication facilities each reviewer can access. Treat repository contents
  and model output as untrusted input.
- [ ] Describe credential matching as best-effort detection. It cannot stop data a
  reviewer has already read from reaching its provider, and it does not cover
  every secret format. Keep rejected content out of prompts and public summaries.
  Document that private raw logs can still contain sensitive content.
- [ ] For sensitive or untrusted repositories, implement a reproducible isolation
  profile that provides:
  - a read-only view of the repository;
  - a temporary home directory;
  - explicitly listed writable scratch paths;
  - only the runtime and authentication resources the reviewer needs.

  Do not expose the user's full home directory, unrelated projects, SSH keys, or
  cloud credentials.
- [ ] Restrict inherited environment variables and use narrowly scoped
  credentials. Do not claim that a credential is hidden from the CLI process that
  needs it to authenticate. Test whether tools the model can use are able to read
  that credential, and state any guarantee you cannot support.
- [ ] Validate isolation separately on each platform where it is claimed. If
  equivalent enforcement cannot be shown, mark that platform/profile unsupported.
  Never fall back silently to ordinary execution.
- [ ] Expand security fixtures with:
  - synthetic canary files outside the repository;
  - symlinks;
  - hooks and MCP launchers;
  - conflicting configuration;
  - attempted writes.

  Check that forbidden files cannot be read and that forbidden actions leave no
  sentinel. Telling the model not to do something is not an enforcement test.
- [ ] Keep credential-filter tests for complete, truncated, escaped, flattened, and
  previously present keys, alongside harmless prose examples. Add malformed and
  large-input cases. If regex backtracking cannot be kept predictably bounded,
  switch to bounded parsing.

Acceptance: the documented boundary is demonstrated with synthetic data. The
baseline release claims no more isolation than the evidence supports. Sensitive
or untrusted use stays unsupported until its isolation profile passes.

## 3. Control CLI-compatibility drift

Relevant files: `src/duo/policy.py`, `src/duo/adapters.py`, `src/duo/copilot.py`,
`tests/test_policy.py`, `tests/test_copilot.py`, `tests/test_duo.py`, new
fixture directory and scheduled CI workflow, `README.md`.

- [ ] Replace the open-ended same-major acceptance with an explicit version
  policy. Recommended default:
  - Production runs accept only CLI versions within the ranges recorded as tested
    in the compatibility matrix.
  - Any other version fails at setup with a message naming the tested range.
  - An explicit opt-in (for example `--allow-untested-cli`) may run other versions.
    It still applies every preflight probe, records `experimental` in
    `versions.json`, the run result, and JSON output, and is never part of the
    supported configuration.
- [ ] Handle Codex 0.x separately. Under semantic versioning, a 0.x minor bump can
  be breaking, so do not treat "0" as a stable major version. Bound the tested
  range by minor version, or justify any wider bound with probe and live evidence.
- [ ] Keep the configuration and help probes, which must fail closed. A probe that
  cannot find a control, or finds an unknown value, stops setup even within a
  tested range. Add tests showing that missing, renamed, or reworded flags,
  feature names, and help output cause a setup error rather than silently
  weakening a control.
- [ ] Record sanitized real-CLI output fixtures for each tested CLI version:
  - version strings;
  - help and feature/config probe output;
  - `claude doctor` policy output;
  - Codex JSON event streams;
  - Claude and Copilot response envelopes;
  - representative error and rate-limit events.

  Run the parsers and the fake CLIs' output against these fixtures in offline CI,
  so the fake CLIs cannot drift from real formats unnoticed. Refresh the fixtures
  from a disposable, synthetic-content run whenever the matrix changes. Never
  record tokens or account identifiers.
- [ ] Add a scheduled drift job, plus a manual trigger, that installs the newest
  available release of each CLI and runs the credential-free checks: version
  parsing, help/config probes, managed-policy detection, and fixture comparison.
  When credentials are explicitly provisioned for the protected live environment,
  also run the inspection smoke test and one bounded end-to-end run (section 4).
  Report the result as `compatible`, `probe-failure`, `format-change`, or
  `not verified (no credentials)`.
- [ ] Define the response to drift:
  - A newer CLI that passes the probes, fixtures, and live checks may be added to
    the matrix in a normal release.
  - A failing CLI stays rejected and is listed as a known incompatibility in the
    runbook.
  - Changes affecting inspection controls trigger a re-review of the security
    boundary (section 2).
  - Name the person responsible for triaging drift reports, and set a review
    interval.
- [ ] Also track drift in Copilot model identifiers and effort values, and in
  native model aliases used in documentation. Fail clearly on unknown models (as
  Duo does now) and keep the examples in `README.md` matching the matrix.

Acceptance: an untested CLI version cannot silently run as production-supported.
Format or control changes in real CLIs are caught by fixture or scheduled drift
checks before users rely on them. Every matrix update has recorded evidence.

## 4. Validate real CLI integrations

Relevant files: `scripts/smoke_inspection.py`, `tests/test_copilot.py`,
`tests/test_policy.py`, `src/duo/adapters.py`.

- [ ] Run an authenticated matrix covering native/native, native/Copilot,
  Copilot/native, and Copilot/Copilot reviewers. Exercise both starting reviewers
  and the model/effort combinations chosen for release.
- [ ] Use disposable repositories and synthetic content. Get credentials through
  the supported environment variable or CLI mechanism. Never put token values in
  commands, repository fixtures, reports, or shared CI artifacts.
- [ ] During the inspection smoke test, verify:
  - repository reads work;
  - reviews parse correctly;
  - hooks and MCP stay disabled;
  - repository files are unchanged.
- [ ] Add a bounded end-to-end smoke run through the actual `duo` command,
  covering:
  - drafting and publication;
  - two explicit approvals;
  - transcript display;
  - audit history;
  - the final exit status.

  Keep deterministic state-machine assertions in the offline suite; do not
  require a model to follow an exact turn sequence.
- [ ] Test these failures:
  - missing or invalid authentication;
  - unavailable models;
  - malformed responses;
  - timeout and interruption;
  - supported configuration failures.

  Use controlled test doubles for rate limits and provider outages, which cannot
  be triggered safely or reproducibly against a live service.
- [ ] Record the revision, OS, Python version, CLI versions, requested models and
  efforts, outcome, and sanitized evidence. Where a backend does not confirm the
  settings it applied, distinguish requested settings from effective ones.
- [ ] Give live jobs explicit time and turn limits and a documented spending
  allowance. Duo's run timeout is not a general provider credit cap. Missing
  credentials mean "not verified", never a passing release result.

Acceptance: every configuration claimed as production-supported has successful
live evidence for the release candidate. Flaky or unsupported combinations are
fixed and retested, or explicitly excluded.

## 5. Make installation and releases reproducible

Relevant files: `pyproject.toml`, new CI workflows and release constraints,
`README.md`.

- [ ] Add offline CI on Linux and macOS for each supported Python version. Run the
  existing test suite and the section 3 fixture checks without credentials or
  model requests.
- [ ] Build both a wheel and a source distribution. Install each into a clean
  temporary environment, run `duo --help`, and run a workflow from the installed
  package using fake CLIs. Do not use `PYTHONPATH=src` for these checks.
- [ ] Run integration checks with the real installed LangGraph dependency. A
  missing dependency or a skipped mandatory test must fail the release job.
- [ ] Check in resolved release dependency constraints, with per-platform or
  per-Python variants where needed, and document how to install from them. Keep
  the package's dependency ranges separate from the tested release environment.
- [ ] Provide a controlled way to update dependencies: regenerate the constraints
  and rerun the tests. Review dependency vulnerability findings before release.
- [ ] Keep authenticated live jobs and scheduled drift jobs separate from
  untrusted pull-request jobs. Restrict their credential access, artifact
  visibility, retention, and execution permissions. Automated live calls must be
  explicitly enabled.
- [ ] Produce for each release:
  - a versioned artifact;
  - a changelog;
  - a compatibility record;
  - checksums;
  - installation instructions;
  - a rollback procedure using a previous artifact and its tested constraints.

  Resolve distribution licensing (the repository has no license file) and the
  distribution channel before publishing.

Acceptance: a clean machine can install and run the same tested artifact without
relying on the developer checkout, micromamba state, or cached packages.

## 6. Bound resource use and audit retention

Relevant files: `src/duo/adapters.py`, `src/duo/storage.py`,
`src/duo/cli.py`, `tests/test_acceptance.py`.

- [ ] Add configurable limits for captured output and per-run storage. Proposed
  starting defaults are 32 MiB per stdout/stderr stream and 256 MiB per run.
  Validate them against representative workloads, then document the final values
  and units.
- [ ] Enforce capture limits while the child process runs, not only when parsing
  its finished output. Count logs and caches created by the backend toward the
  run-storage limit. If enforcement is sampled, document possible overshoot. Do
  not promise a hard disk quota without an underlying quota mechanism.
- [ ] When a limit is exceeded, stop the process group, keep the last accepted
  plan, return a non-success status, and record a bounded, sanitized failure.
- [ ] Bound the size of tasks, criteria, plans, and responses before expensive
  processing. Publish these limits and make oversized input fail clearly. Test
  parsing and credential scanning with adversarial inputs within the limits.
- [ ] Add explicit audit cleanup with age/size selection and dry-run output. Keep
  automatic deletion opt-in. Cleanup must never delete active runs, traverse
  symlinks, follow paths outside the configured audit root, or remove project
  plans.
- [ ] Document audit ownership, access permissions, sensitive raw content, backup
  policy, and retention. Verify permissions on newly created artifacts.
- [ ] Test disk-full failures, interrupted cleanup, oversized output, and child
  processes that keep writing output while being terminated.

Acceptance: recurring runs have documented storage bounds and cleanup procedures.
A resource failure cannot publish a partial plan or report agreement.

## 7. Verify unattended operation and release evidence

Relevant files: `src/duo/workflow.py`, `src/duo/storage.py`,
`src/duo/cli.py`, `tests/test_acceptance.py`, `README.md`.

- [ ] Recheck behavior on timeout, Ctrl-C, and termination by a scheduler. The
  code currently has no SIGTERM handler: test SIGTERM explicitly and implement
  cleanup so it cannot leave reviewer processes running or produce a success
  result.
- [ ] Verify that concurrent runs on the same repository are excluded even when
  they use different `--state-dir` values. The current lock lives in the state
  directory. Close any gap in the lock's scope before supporting concurrent
  scheduled runs that target the same plan.
- [ ] Verify that external edits are detected before publication. Document the
  remaining race with writers that do not cooperate. Unattended operation must
  not assume exclusive access just because other Duo processes honor the lock.
- [ ] Make sure setup, model, validation, storage, interruption, and limit failures
  each produce a useful non-success outcome. Keep run IDs, backend/model metadata,
  experimental-version flags, accepted revisions, and stop reasons available in
  machine-readable output.
- [ ] Write a runbook covering:
  - authentication renewal;
  - CLI upgrades and drift reports (section 3);
  - rejected policies;
  - disk pressure;
  - interrupted runs and preserved plans;
  - rollback.

  State that reviewer agreement still needs human assessment for consequential
  use.
- [ ] Collect a release report linking the exact artifact and revision to:
  - CI results;
  - installation tests;
  - fixture and drift results;
  - live compatibility results;
  - isolation evidence, where isolation is claimed;
  - outstanding limitations.

Acceptance: an operator can detect a failed run, find the last accepted plan,
understand why the run stopped, and restart or roll back without manually editing
internal state.

## Execution order and release gate

1. Define the deployment contract and set up offline CI (sections 1 and 5, CI part).
2. Implement the version policy, real-CLI fixtures, and drift job (section 3).
3. Implement the claimed security boundary and resource controls (sections 2 and 6).
4. Complete the unattended-operation checks (section 7) and installed-artifact
   testing (section 5).
5. Run the authenticated matrix against the resulting release candidate
   (section 4), and record its CLI versions in the compatibility matrix.
6. Review the evidence, resolve release-blocking findings, and get explicit
   approval before publishing or deploying the release.

The release gate requires all of the following:
- passing offline, fixture, and installation tests;
- a version policy that rejects untested CLIs by default;
- live evidence for every claimed backend combination;
- documented and tested security boundaries;
- verified cleanup and resource-limit behavior;
- reproducible artifacts;
- an operator runbook.

Any change that affects these guarantees, including adopting a new CLI version,
invalidates the related evidence, and those checks must run again.

The following are outside this release's scope:
- persistent graph checkpoint recovery;
- distributed or multi-user orchestration;
- automatic implementation of the generated plan;
- additional reviewer backends.

None of these replaces completing the release gates above.

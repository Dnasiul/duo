# Changelog

## Unreleased

### Fixed

- Refuse ambiguous CLI version output: `verify_version` now rejects text that
  contains more than one distinct `X.Y.Z` triple, and Copilot preflight now
  validates the full `--version` output while preserving the first line as the
  recorded version string.
- Refuse ambiguous Codex feature listings during control verification: duplicate
  feature rows now fail closed, rows whose state token is not exactly `true` or
  `false` are rejected, and feature rows with unknown middle-column layout are
  rejected instead of being accepted by first/last-token parsing.
- Refuse Copilot reply streams that do not contain a successful terminal
  result: `copilot_reply` now requires at least one
  `{"type":"result","exitCode":0}` event before accepting any assistant JSON
  reply content.

## 0.8.0 (2026-11-25)

### Added

- Optional reviewer rosters (`--reviewer ID:KIND:BACKEND:MODEL[:EFFORT]`) for
  N-reviewer document panels, including roster-order scheduling, per-member
  backend/model dispatch, and unanimous agreement semantics.
- Diversity classification for every roster (including the default pair), with
  factual warnings for identical models, same-family concentration, and unknown
  families. Classification and warnings are recorded in JSON summaries and
  `reviewer-policy.json`.
- Verdict schema **1.2** for panel runs, including roster member kind/effort,
  `unanimous-roster-approve-same-subject`, and agreement hashes
  (`roster_sha256`, `policy_sha256`), plus panel fixtures and gate coverage.
- Checkpoint schema **2** for panel runs, with roster/policy hashes validated
  on load and enforced before any resumed model call.

### Changed

- `--start` now validates against the active roster (default pair or explicit
  panel roster) instead of argparse choices.
- Missing `--codex-model`/`--claude-model` and invalid `--start` values are now
  run errors (exit 1) instead of argparse usage errors (exit 2).
- `examples/check_verdict.py` now validates panel verdicts with
  `--rule unanimous-roster-approve-same-subject` and optional
  `--roster-sha256` pinning while preserving pair fail-closed behavior.

## 0.7.0 (2026-11-24)

### Added

- `duo --resume RUN_PATH` recovers interrupted document reviews from durable,
  validated checkpoints, preserving work and budgets while requiring fresh
  approvals. Earlier audit-only runs and terminal runs cannot be resumed.
- Verdict schema 1.1 records `approval_epoch` in verdict decisions, with a
  matching update to the reference gate in `examples/check_verdict.py`.
- `duo-clean` protects paused recoverable runs and unreadable checkpoints;
  `--include-recoverable` explicitly includes them in abandonment/deletion.

## 0.6.0 (2026-11-23)

### Added

- `--answer TEXT` paired with `--source-run PATH` to answer a completed blocked
  run in a fresh review. Source identity, question, answer, and blocked/current
  document hashes are recorded in the new manifest; task and criteria must match
  exactly, both approvals start fresh, and the source is never modified.
- Versioned `verdict.json` audit artifacts for external gates, with subject and
  fixed-input hashes, reviewer agreement bindings, and persistent structured
  findings with stable IDs, severity, locations, and role-owned dispositions.
  Schema 1.0 and valid/invalid fixtures ship with the implementation; the stdlib
  `examples/check_verdict.py` demonstrates fail-closed CI consumption.
- Model-response contract: reviewers now return required `findings` and
  `finding_updates` arrays (empty when unused). Each reviewer must reconcile its
  own open findings against the inspected subject before approving.

### Fixed

- Correct the `--max-prompt-bytes` help text, which still described the
  524,288-byte default as an interim value pending an owner decision; the
  default was ratified by the owner on 2026-11-23 (see the 0.5.0 entry).

## 0.5.1 (2026-11-23)

### Added

- Offline HTML agreement reports from stored audit records, including Duo 0.5.0
  runs: reviewer roster, outcomes, approval hashes, full review timeline with
  collapsible revision diffs, and context metadata. Render with
  `duo --render-report RUN_DIR [--report-output PATH]`, without task or model
  arguments. Reports are deterministic, escape record text, and use embedded
  CSS with no JavaScript, links, or external assets.
- Best-effort automatic `report.html` on terminal results and CLI error or
  interruption paths when a run manifest exists, even before the first turn
  completes. Report failures warn without changing
  review outcomes or existing artifacts; missing snapshots are noted, and
  unfinished stored states are labeled interrupted. Agreement semantics unchanged.

## 0.5.0 (2026-11-23)

### Changed

- Add repeatable `--context-file`: one-time capture of regular UTF-8 files,
  rejecting document aliases and recognized credentials before saving or calls.
  Enforce a 64 KiB aggregate limit; store exact bytes, labels, source paths,
  order, and hashes in private context audit artifacts. Both reviewers receive
  labeled untrusted captures on every turn/retry; separately inspected files
  remain mutable and are not snapshotted. Detection is best-effort.
- Add `--max-prompt-bytes` with a **512 KiB (524,288-byte) default, ratified
  by the owner on 2026-11-23**. Compatibility change (owner-approved): this
  finite ceiling narrows compatibility versus 0.4.6, which has no combined
  limit, so it can reject inputs that 0.4.6 accepted. The measured basis for
  the ratified value: measurements on 2026-11-23 across seven recorded runs
  found a maximum of 150,232 bytes (`run-1q3qeibv`), followed by 109,987 and
  93,278; fixed inputs peaked around 79 KB. The ratified default accepts all
  measured cases with at least 3.4x margin.
  Check fixed inputs for both backends before startup calls and complete
  prompts before every review call, including diffs, history, context, retry
  feedback, delimiters, and Copilot's schema wrapper. Record overflow without
  truncation or retry, retaining the last accepted document and snapshot.
- Add a bounded saved-diff/test-output code-review example and context/budget
  regression tests (250 tests pass overall). Bytes are not token counts or
  guaranteed model capacity. Bounded synthetic native live attempts captured
  identical context bytes/hashes, but Codex routing failed and Claude timed
  out before a completed review in isolated CLI homes without logins. The
  coordinator subsequently ran the shipped example with the installed package
  and real CLI logins on Linux: `run-qr36s0ac` (2026-11-23) agreed in 3 turns,
  with both context hashes matching their source files. Native reviewers were
  Codex `gpt-6-astra` via codex-cli 0.171.0 and Claude `claude-sonnet-5-5` via
  Claude Code 2.1.330; `--allow-untested-cli` records the out-of-range Codex
  version as untested evidence, not range support. macOS remains unverified.
  See `docs/STATUS.md` for versions, bounds, measurements, and outcomes.

- Native Claude now emits `stream-json --verbose` into each attempt's raw audit
  log, retaining tool requests, inputs, emitted results and denials even on
  failure or interruption. Only an unambiguous successful terminal result at
  exit zero supplies the validated review. Attempt metadata distinguishes
  process outcome, completion, payload validation, and absent versus reported
  usage; existing limits and sanitized failure classification remain enforced.
- Require stream options in preflight/drift checks; update fixture sanitization
  and smoke denial extraction. Add Linux Claude Code 2.1.330 discovery fixtures
  covering Read, Glob, Grep, WebSearch, WebFetch, denials and turn-limit failure,
  plus fresh restricted probes and timed-out adapter captures. Document exact
  provenance and omissions: web counters are not tool evidence, and emitted
  results do not constitute complete file/page archives.
- Retire Python 3.10 (owner decision, 2026-11-23; plan §7.3): `requires-python`
  is now `>=3.11`, the CI matrix covers Python 3.11–3.14, and
  `constraints.txt` was regenerated for 3.11–3.14. Python 3.10 reached end of
  life in October 2026; package metadata now refuses installation on it.

### Fixed

- Bound Claude stream JSON nesting before parsing: each stream line's
  container depth is checked iteratively (string- and escape-aware) against a
  128-level limit and rejected as invalid stream data before `json.loads`
  runs. Python 3.14's parser no longer raises `RecursionError` for the deeply
  nested line that rejection previously relied on, so on 3.14 such a line
  surfaced as a non-terminal event instead of an invalid stream; the explicit
  bound restores the rejection on every supported Python, with the
  `RecursionError` catch retained as a backstop. Three tests added (253 pass
  overall).

## 0.4.6 (2026-11-23)

### Changed

- Correct security documentation and policy records to distinguish supplied
  restrictions, advertised options, selected metadata checks, and historical
  behavior tests, including Codex write denials and probe credential context.
  Correct Copilot GPT confinement coverage and explain model-provider exposure
  without web and incomplete file/web audit evidence. Missing live evidence and
  the Claude audit improvement remain open; restrictions and probes are unchanged.

### Fixed

- The offline test runner discovers `tests/` relative to its own location from
  any working directory and rejects zero discovered tests, while retaining
  failure on errors, failures, skips, and missing LangGraph. Correct and align
  the documented commands; checks still use the installed package without
  adding `src/`. CI already used the correct `tests/` working directory, so this
  fix does not imply historical CI ran zero tests.
- Native fixture probes now use production preflight's restricted environment,
  including the temporary Codex home, and match Claude managed-configuration
  check ordering. Document permitted authentication and existing fixture provenance.
- Credential checking avoids repeated suffix scans for OpenAI- and JWT-shaped
  near-matches and repeated document searches for known credentials, including
  distinct values.
- The credential-scan growth test tolerates scheduling noise with more timing
  repetitions and a pooled fourfold growth check in place of per-step checks.
  A fitted log-log slope replaces the endpoint ratio to reject quadratic growth,
  retaining all input cases and sizes and the five-second scan limit.
- Reviews that finish validation after the run deadline now stop with a
  time-limit outcome, preserving the previous document and approvals.

## 0.4.5 (2026-11-10)

### Changed

- Codex CLI 0.167.x is tested and accepted (0.162.1 to 0.167.x), validated on
  macOS. `exec --help` is unchanged; its new features are all off by default, and
  `deferred_tool_world_state` became stable and on.

### Fixed

- On macOS 27, Duo refused native Claude Code and Copilot reviewers with
  "preferences policy detected or unreadable": `defaults read` now reports a
  missing domain as `Domain '…' not found.` instead of `Domain … does not
  exist`. Both wordings are accepted; any other result still stops setup.

### Added

- `examples/search_literature.sh`: find the most relevant literature on a
  subject (reviews, foundational and recent papers, books) with `--allow-web`,
  confirmed against DOI, arXiv, or ISBN records, with BibTeX for confirmed
  entries. Native CLIs by default; `BACKEND=copilot` keeps reviewers inside the
  folder. Its companion `search_literature_instructions.txt` replaces report
  mode's repository-evidence checklist with one for web sources, and each subject
  gets its own report, named from the subject and its hash.

## 0.4.4 (2026-11-02)

### Changed

- Codex CLI 0.165.x is tested and accepted (0.162.1 to 0.165.x). `exec --help`
  is unchanged; of its new features only `prediction_preserve_parent_tools` is on
  by default, and `reasoning_effort_override` became stable and on.
- Codex CLI 0.166.x is tested and accepted (0.162.1 to 0.166.x). `exec --help`
  is unchanged; of its new features only `plugin_discovery_prefetch` is on by
  default, and plugins stay disabled.

## 0.4.3 (2026-10-28)

### Added

- `examples/improve_codebase.sh`: plan improvements to a codebase in one run
  with native CLIs, starting from `review_codebase.sh`'s findings when present.
- `scripts/smoke_inspection.py --allow-untested-cli` validates a new CLI before
  extending the supported version range.

### Changed

- Codex CLI 0.164.x is accepted after compatibility validation on Linux. Added
  probe and response fixtures; control checks replay every recorded Codex minor
  version. Live verification on macOS remains pending.

### Fixed

- Copilot CLI 1.0.103 and later work again. They replaced `--no-remote` and
  `--no-remote-export` with `--sync-session`, so Duo refused them as lacking
  required options; it now passes `--sync-session=off` when the installed CLI
  offers it, and the old flags otherwise. `reviewer-policy.json` records which.
- Copilot's required options are matched as whole names in its help, so one
  option can no longer be satisfied by a longer one (`--mode` by `--model`).

## 0.4.2 (2026-10-21)

### Added

- `examples/review_codebase.sh`: review a codebase for bugs in one run with
  native CLIs.
- Release tooling: `scripts/release.py` builds and checks the release files
  from a clean clone, and a tag-triggered workflow attaches them with
  build-provenance attestations to a draft release.

### Changed

- Codex CLI 0.163.x is tested and accepted (0.162.1 to 0.163.x). Its new
  features are all under development and off by default.

### Fixed

- `constraints.txt` pins `certifi` 2026.10.19, which fixes PYSEC-2026-4320, and
  the newest releases of the other dependencies (LangGraph 1.2.16).
- `scripts/record_fixtures.py` lists Codex MCP servers with an empty
  `CODEX_HOME`, as preflight does, so servers in your own Codex configuration no
  longer end up in fixtures.

## 0.4.1 (2026-10-14)

### Fixed

- Copilot runtime cleanup skips symbolic links in attempt and cache directories,
  preventing deletion outside the audit directory.
- Reviewer processes are stopped and reaped when resource monitoring or prompt
  writer startup fails.
- Input files must be regular files and are read with a 1 MiB bound, preventing
  named pipes from hanging startup and oversized files from exhausting memory.
- The Git-root test no longer depends on the host temporary directory having no
  Git ancestors.

## 0.4.0 (2026-10-14)

### Added

- `--allow-web` (experimental, off by default) gives reviewers web search and
  page fetching: Codex's live web search, Claude Code's `WebSearch` and
  `WebFetch`, and Copilot's `web_fetch`. It lets prompt injection send data out;
  see "Web access" in `docs/SECURITY.md`.
- `examples/scientific_research_pipeline.md`: using Duo at each stage of a
  research project, from observations to a paper with confirmed references.

### Fixed

- `reviewer-policy.json` now lists every variable Copilot receives, including
  the settings Duo sets for it (`COPILOT_HOME`, `COPILOT_ALLOW_ALL`, and others),
  not only the ones it inherits.

## 0.3.1 (2026-10-14)

### Fixed

- Copilot again receives `GH_HOST`, which selects a GitHub Enterprise host. The
  restricted reviewer environment in 0.3.0 passed only `COPILOT_GH_HOST`, so with
  `GH_HOST` alone Copilot authenticated against github.com instead.

## 0.3.0 (2026-10-13)

### Changed

- Reviewer CLIs and their setup probes no longer inherit Duo's whole
  environment. They receive only what they need to run and log in (home, path,
  user and locale, temporary and XDG directories, the session bus, proxy and
  certificate settings, and each CLI's own variables); Copilot gets only the
  token it uses. Cloud credentials, other tools' tokens, the SSH agent, and
  `NODE_OPTIONS` are dropped.

### Added

- `--pass-env NAME` passes an additional environment variable to reviewer CLIs.
  Each run's `reviewer-policy.json` records the variable names every CLI
  received, never their values.

## 0.2.1 (2026-10-13)

### Fixed

- `--output` rejected `.git` only by its exact name, so on macOS, whose file
  systems usually ignore case, `--output .GIT/report.md` could write inside the
  repository's Git directory. Names are now compared case-insensitively.
- `--output` naming a directory now fails with a clear message instead of
  `[Errno 21] Is a directory`.
- `duo.__version__` reported 0.1.0; it now reads the installed package version.

## 0.2.0 (2026-10-13)

### Added

- Document modes (experimental): `--mode report`, `description`, or `review`
  makes the reviewers produce that document instead of a plan, each with its own
  review checklist and default file (`report.md`, `description.md`, `review.md`).
  The default `plan` mode sends exactly the prompt it always has.
- `--instructions-file` replaces the mode's review checklist with your own; the
  review protocol (read-only inspection, complete revisions, repository text
  treated as material) stays fixed.
- `--output` saves the document at another path inside the repository. Reviewers
  are told that this file is Duo's output, not material to describe.
- The mode, custom instructions, and document path are recorded in each run's
  `manifest.json`.

## 0.1.0 (2026-10-13)

First release: a single-user command-line tool in which Codex and Claude take
turns reviewing `plan.md` until both approve the same version.

### Workflow

- LangGraph review loop: an absent plan is drafted, every validated revision is
  published atomically and clears both approvals, and a run succeeds only when
  both reviewers approve the same unchanged plan.
- Reviewers can run natively (Codex CLI, Claude Code) or through GitHub Copilot
  CLI, in any combination.
- Exit codes and a `--json` summary distinguish agreement, setup or reviewer
  failure, a reviewer needing a decision, and exhausted turn or time budgets.
- `--show-conversation` prints each accepted review and plan diff.

### Safety controls

- Reviewers run read-only. Preflight verifies each CLI's inspection controls
  (sandbox, disabled hooks, MCP servers, plugins, and web access) before any
  review and stops when one is missing or ineffective.
- Only tested CLI versions run by default; `--allow-untested-cli` runs others as
  experimental, still subject to every control probe.
- Responses that introduce a credential (private keys and common API-token
  formats) are rejected and retried without the value.
- SIGTERM and SIGHUP stop the reviewer and everything it started; a lock per
  repository excludes concurrent runs; external edits to `plan.md` are detected.
- Output, run-storage, and 1 MiB input limits are enforced while reviewers run;
  `duo-clean` removes old audit runs.

### Compatibility and evidence

- Tested CLIs: Codex 0.162.x from 0.162.1, Claude Code 2.1.x from 2.1.296,
  Copilot CLI 1.0.x from 1.0.95; Python 3.10 to 3.14 on macOS and Linux.
- CI on Ubuntu and macOS installs the wheel and sdist with `constraints.txt`;
  a weekly drift check compares installed CLIs with recorded fixtures.
- Live runs on macOS and Linux; all four native/Copilot backend combinations
  verified live on Linux.
- Confinement tested on Linux: Claude Code and Copilot refused file reads and
  searches outside the repository; native Codex does not confine reads.

### Known limitations

- No isolation profile. Use Duo only on trusted repositories; native Codex can
  read any file your user can read, and reviewer CLIs inherit your environment.
  See `docs/SECURITY.md`.
- Mixed native/Copilot reviewer pairs are verified live on Linux only.
- Reviewer agreement is not proof that a plan is correct.

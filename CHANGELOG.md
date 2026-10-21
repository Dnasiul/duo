# Changelog

## Unreleased

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

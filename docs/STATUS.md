# Status and handoff

Where Duo's production readiness stands, and how to continue on a Linux machine.
Written 2026-10-13 at commit `c5886a7`; Linux live results and the 0.1.0, 0.2.0,
0.2.1, and 0.3.0 releases added the same day; 0.3.1, 0.4.0, and 0.4.1 on
2026-10-14; 0.4.2 on 2026-10-21; 0.4.3 on 2026-10-28; 0.4.4 on 2026-11-02. `plan.md` is the improvement plan after 0.4.1; the production plan
it replaced is `git show 918d2c5:plan.md`. This page records progress.

## Done

| Area | Where |
| --- | --- |
| Reviewer panels (Unreleased, plan §13): optional roster-defined N-reviewer panels, unanimity on one subject, diversity classification/warnings, verdict schema 1.2 + panel gate rule/hash checks, checkpoint schema 2 + roster hash validation | `src/duo/models.py`, `src/duo/workflow.py`, `src/duo/cli.py`, `src/duo/diversity.py`, `src/duo/verdicts.py`, `src/duo/checkpoints.py`, `examples/check_verdict.py`, `tests/test_panels.py` |
| Persistent checkpoints (Unreleased, plan §12): explicit recovery, durable attempt reservations and publication intents, fresh approval epochs, protected cleanup | `src/duo/checkpoints.py`, `src/duo/workflow.py`, `src/duo/cli.py`, `src/duo/cleanup.py`, `tests/test_resume.py`; "Persistent checkpoints" below |
| §7.1 snapshot-order test canonicalizes the source path in its read filter (`50705f5`, test-only): production `load_blocked_source` resolves the source run directory before reading snapshots, but the test collected mocked `read_input` calls by comparing each call's parent with the unresolved temporary-directory path, which collects nothing on macOS where the temp directory sits behind the `/var` → `/private/var` symlink (CI run 45282241475 failed all four macOS legs at that assertion while the answer run itself exited 0); reproduced on Linux with `TMPDIR` pointed through a symlinked directory (failed before, passes after), full strict suite 374 tests green; the descending-order assertion is unchanged | `tests/test_answer.py` |
| Answer a blocked run in a fresh run (Unreleased, plan §7.1): explicit source validation, user clarification, source/current hashes, fresh approvals | `src/duo/storage.py`, `src/duo/cli.py`, `src/duo/prompts.py`, `src/duo/workflow.py`, `tests/test_answer.py`; "Answering blocked runs" below |
| Structured verdicts and findings (Unreleased, plan §11): versioned artifact, persistent role-owned findings, reference CI gate | `src/duo/verdicts.py`, `src/duo/schemas/`, `examples/check_verdict.py`, `tests/test_verdicts.py`; "Structured verdicts" below |
| HTML agreement reports (Unreleased): offline rendering of stored runs, standalone CLI, best-effort automatic output, escaped text and collapsible diffs | `src/duo/report.py`, `src/duo/cli.py`, `tests/test_report.py`; "HTML agreement reports" below |
| HTML report standalone-CLI test canonicalizes its expected output path (`8a1f705`, test-only): the CLI prints the resolved path (`write_report` resolves the run directory), but the test compared it with the unresolved temporary-directory path, which fails on macOS where the temp directory sits behind the `/var` → `/private/var` symlink; reproduced on Linux with `TMPDIR` pointed through a symlinked directory (failed before, passes after), full strict suite 275 tests green | `tests/test_report.py` |
| Real end-to-end runs: native Codex + native Claude, and both reviewers via Copilot (macOS) | README compatibility table |
| CI: Ubuntu and macOS, Python 3.11–3.14, wheel and sdist installed with `constraints.txt`, strict runner (no skips), newest-dependency job, `pip-audit` | `.github/workflows/ci.yml` |
| Python 3.10 retired in 0.5.0 (owner decision, 2026-11-23; plan §7.3): supported range is Python 3.11–3.14 in package metadata, CI, and regenerated `constraints.txt` | `pyproject.toml`, `.github/workflows/ci.yml`, `constraints.txt` |
| Claude stream JSON depth bounded before parsing (128 levels, iterative and string-aware): Python 3.14's parser no longer raises `RecursionError` on deeply nested lines, so the invalid-stream rejection is now explicit on every supported Python | `src/duo/adapters.py` (`check_claude_json_depth`), `tests/test_claude_stream.py` |
| Version policy: only tested CLI ranges; `--allow-untested-cli` runs others as experimental | `src/duo/policy.py` (`TESTED_VERSIONS`) |
| Sanitized real-CLI fixtures replayed through the parsers | `tests/fixtures/`, `scripts/record_fixtures.py` |
| Weekly CLI drift check, with an issue opened when action is needed | `.github/workflows/drift.yml`, `scripts/check_drift.py` |
| SIGTERM/SIGHUP stop the reviewer; lock per repository; accurate failure status; capped retry delays | `src/duo/cli.py`, `storage.py`, `workflow.py` |
| Output and run-storage limits enforced while reviewers run; 1 MiB input limits; `duo-clean` | `src/duo/adapters.py`, `src/duo/cleanup.py` |
| Deployment contract, compatibility matrix, restart behavior | README "Supported deployment" |
| Runbook and security model | `docs/RUNBOOK.md`, `docs/SECURITY.md` |
| Owner decisions (2026-10-13): no isolation profile in 0.1.0, documented as a limitation; MIT License; GitHub release only | `docs/SECURITY.md`, `LICENSE`, `CHANGELOG.md` |
| Live runs on Linux: smoke test (native and Copilot) and all four backend combinations, including both mixed pairs | "Linux live verification" below |
| Live mixed pairs on macOS, with the restricted reviewer environment (`8ffff97`) | "macOS live verification" below |
| Restricted reviewer environment live on Linux: all four backend combinations (0.3.1) | "Linux live verification" below |
| Codex MCP check excludes user configuration with an empty `CODEX_HOME`; usual stored login is absent, allowed authentication variables and `--pass-env` additions can remain; login-fetched policy unverified | `src/duo/policy.py` (`verify_controls`) |
| Historical confinement matrix: native Codex outside reads succeeded and dedicated searches were declined (no boundary test); native Claude outside reads/searches denied; Copilot Claude outside reads/searches denied; Copilot GPT outside searches and symlink read denied, temporary/home reads not attempted and still unverified | `scripts/smoke_inspection.py --confinement`, `--search-confinement`; "Linux live verification" below; open follow-up in plan §8 |
| Release 0.1.0 (2026-10-13): tag `v0.1.0` on `94186b7`, wheel, sdist, `constraints.txt`, `SHA256SUMS`; release notes hold the release report | https://github.com/Dnasiul/duo/releases/tag/v0.1.0 |
| Release 0.2.0 (2026-10-13): document modes (experimental); tag `v0.2.0` on `67538e2`, same files and release report | https://github.com/Dnasiul/duo/releases/tag/v0.2.0 |
| Release 0.2.1 (2026-10-13): `--output` fixes (`.git` case-insensitive, directory targets) and `duo.__version__`; tag `v0.2.1` on `c41ea59`, same files and release report; published with `gh`, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.2.1 |
| Release 0.3.0 (2026-10-13): reviewer CLIs get only the environment they need; `--pass-env`; tag `v0.3.0` on `dbbd30e`, same files and release report; published with `gh`, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.3.0 |
| Release 0.3.1 (2026-10-14): Copilot receives `GH_HOST` again; tag `v0.3.1` on `b531f68`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.3.1 |
| Release 0.4.0 (2026-10-14): `--allow-web` (experimental), the research pipeline example, the full Copilot environment record; tag `v0.4.0` on `a7571ad`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.0 |
| Release 0.4.1 (2026-10-14): cleanup no longer follows a `copilot-cache` symbolic link out of the audit directory, reviewers are stopped when monitoring fails, input files must be regular and are read with a 1 MiB bound; tag `v0.4.1` on `d63b188`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.1 |
| Release 0.4.4 (2026-11-02): Codex CLI 0.165.x and 0.166.x supported; tag `v0.4.4` on `ec2e27b`; built, attested, and drafted by the release workflow; downloaded files install-tested on Linux and verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.4 |
| Release 0.4.5 (2026-11-10): macOS 27 preferences-domain fix (native Claude Code and Copilot); Codex CLI 0.167.x supported; tag `v0.4.5` on `f2b6ccb`; built, attested, and drafted by the release workflow; downloaded files install-tested on macOS 27.2 and verified against `SHA256SUMS` and their attestations | https://github.com/Dnasiul/duo/releases/tag/v0.4.5 |
| Release 0.5.1 (2026-11-23): HTML agreement reports — offline rendering of stored runs (including 0.5.0 records) via `duo --render-report RUN_DIR`, plus best-effort automatic `report.html` on terminal, error, and interruption paths; tag `v0.5.1` on `96f10f9`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all 10 jobs on the first attempt, no reruns — the first prep commit `8e92c64` failed all four macOS legs on the report test's unresolved-path comparison, fixed test-only in `8a1f705` by canonicalizing the expected path; production code unchanged); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot not checked (CLI not installed in the checking environment); live native-pair run on Linux (Duo 0.5.1 from the release commit) agreed in 3 turns (`run-ta678__n`), its auto-emitted `report.html` verified script-free and showing the agreed state with the shared hash; release notes hold the release report; published 2026-11-23, downloads verified against `SHA256SUMS` and their build-provenance attestations | https://github.com/Dnasiul/duo/releases/tag/v0.5.1 |
| Release 0.5.0 (2026-11-23): native Claude audit stream (plan §2), context files and the owner-ratified 512 KiB combined prompt budget (§3), Python 3.10 retired (§7.3), and the Claude stream JSON depth bound restoring the invalid-stream rejection on Python 3.14; tag `v0.5.0` on `9aa46a7`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all jobs; the macOS 3.11 and 3.13 jobs were cancelled with zero steps on the first attempt and rerun — no test failed in any job that ran); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot not checked (CLI not installed in the checking environment); live native-pair run on Linux (Duo 0.5.0 from the release commit) agreed in 3 turns (`run-si8ey3j6`); release notes hold the release report; published 2026-11-23, downloads verified against `SHA256SUMS` and their build-provenance attestations | https://github.com/Dnasiul/duo/releases/tag/v0.5.0 |
| Release 0.6.0 (2026-11-23): structured verdicts and findings for external gates (plan §11) and answering a blocked run in a fresh run (§7.1); tag `v0.6.0` on `e7dc73d`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all 10 jobs on the first attempt, no reruns — the first prep commit `328f86b` failed all four macOS legs on the §7.1 snapshot-order test's unresolved-path read filter, fixed test-only in `50705f5` by comparing against the resolved source path; production code unchanged); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot 1.0.114 compatible; live native-pair run on Linux (Duo 0.6.0 from the release commit) agreed in 3 turns (`run-esc_oi2o`), its `verdict.json` validated against the shipped schema and the reference gate passed on it (exit 0), its auto-emitted `report.html` verified script-free and showing the agreed state; release notes hold the release report; published 2026-11-23, downloads verified against `SHA256SUMS` and their build-provenance attestations | https://github.com/Dnasiul/duo/releases/tag/v0.6.0 |
| Release 0.7.0 (2026-11-24): persistent checkpoints and interrupted-run recovery (plan §12) — `duo --resume RUN_PATH` recovers interrupted document reviews from durable validated checkpoints while requiring fresh approvals, verdict schema 1.1 records `approval_epoch`, and `duo-clean` protects paused recoverable runs; tag `v0.7.0` on `c573c26`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all 10 jobs on the first attempt, no reruns); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot 1.0.114 compatible; live native-pair run on Linux (Duo 0.7.0 from the release commit) agreed in 3 turns (`run-1t37rsxi`), its `checkpoint.json` verified (`schema_version` 1, SHA-256 integrity field recomputed and matching), its `verdict.json` (schema 1.0) validated with `validate_verdict`, its auto-emitted `report.html` verified script-free, and `duo --resume` on the agreed run refused as a terminal run before any model call (exit 1); release notes hold the release report; published 2026-11-24, downloads verified against `SHA256SUMS` and their build-provenance attestations | https://github.com/Dnasiul/duo/releases/tag/v0.7.0 |
| Release 0.8.0 (2026-11-25): reviewer panels (plan §13) — optional `--reviewer ID:KIND:BACKEND:MODEL[:EFFORT]` rosters with roster-order scheduling and unanimous agreement, verdict schema 1.2 and checkpoint schema 2 for panel runs (the default pair keeps verdict 1.0/1.1 and checkpoint schema 1 with byte-identical artifacts), prefix-based diversity classification with identical/same-family/unknown warnings, and the reference gate accepting panel verdicts only with `--rule unanimous-roster-approve-same-subject` plus a matching `--roster-sha256`; tag `v0.8.0` on `87b143c`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all 10 jobs on the first attempt, no reruns); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot 1.0.114 compatible; live run on Linux (Duo 0.8.0 from the release commit) agreed in 4 turns / 5 attempts (`run-42bzzaik`) with the default pair's Codex role served by Copilot (`gpt-5.3-codex`) and Claude native (`claude-opus-5-5` high), because native Codex is usage-limited until 2026-11-29 13:11 — the same configuration proven on this identical code in the §13 cycle (`run-680_ahbc`), whose live panel evidence (`run-eqyn9pdi`, `run-53pjli5o`) also stands for this release; native-Codex live panel evidence remains deferred; the run's `verdict.json` (schema 1.0, pair rule, no roster hashes) validated with `validate_verdict`, its `checkpoint.json` (schema 1) verified via `duo.checkpoints` validation and an independent SHA-256 integrity recompute, and its auto-emitted `report.html` verified script-free; release notes hold the release report; **draft, not yet published** | https://github.com/Dnasiul/duo/releases/tag/v0.8.0 |
| Release 0.4.6 (2026-11-23): security self-review remediation (plan §9, F1–F6): credential-scanner quadratic fix and deadline recheck, assurance wording matched to evidence, fixture probes under the restricted reviewer environment, reliable offline test discovery; the scanner timing test was then hardened against CI runner noise (min-of-5 timings, pooled growth check, log-log slope bound) after it flaked on two macOS legs, and verified to still fail against the pre-fix scanner; tag `v0.4.6` on `cdceec6`; built, attested, and drafted by the release workflow; CI green on the tagged commit (all jobs, first pass); `scripts/release.py` all checks passed locally on the same commit; drift check on Linux: Claude Code 2.1.330 compatible, Codex 0.171.0 probes pass but `untested-version`, Copilot not checked (CLI not installed in the checking environment); live native-pair run on Linux (Duo 0.4.6 from the release commit) agreed in 3 turns; Copilot pairs not rerun; release notes hold the release report; published 2026-11-23, downloads verified against `SHA256SUMS` and their build-provenance attestations | https://github.com/Dnasiul/duo/releases/tag/v0.4.6 |
| Release 0.4.3 (2026-10-28): Copilot CLI 1.0.103+ supported (`--sync-session=off` chosen from the installed CLI's help), Codex CLI 0.164.x, and the codebase-improvement example; tag `v0.4.3` on `16a08c1`; built, attested, and drafted by the release workflow; downloaded files install-tested on Linux and verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.3 |
| Release 0.4.2 (2026-10-21): Codex CLI 0.163.x supported, `certifi` 2026.10.19 (PYSEC-2026-4320) with the newest dependencies, the release tooling, and the codebase-review example; tag `v0.4.2` on `3461739`; the first release built, attested, and drafted by `.github/workflows/release.yml`; downloaded files install-tested on Linux and verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.2 |
| `--allow-web` (experimental, released in 0.4.0; `b98dd5c`): reviewers may search and fetch web pages (Copilot: fetch only). Owner decision (2026-10-14): allowed with native Codex too, with a warning and documentation rather than a refusal | "Web access live verification" below; `docs/SECURITY.md` |

## Open

1. **Document modes** (released in 0.2.0 as experimental): every mode ran live on
   Linux, natively and through Copilot, and natively on macOS (below); not yet
   through Copilot on macOS or with mixed backend pairs.
2. **`--allow-web`** (released in 0.4.0 as experimental): ran live on Linux with native and
   Copilot pairs in report mode (below); not yet on macOS, with mixed pairs, or in
   other modes.
3. **Codex CLI 0.164.x to 0.166.x**: validated on Linux on 2026-10-28 and
   2026-11-02 (below); live macOS verification remains pending. 0.167.x was
   validated on macOS on 2026-11-10 (below); live Linux verification pending.
4. **Copilot CLI 1.0.103+** (`--sync-session`): validated on Linux with 1.0.105
   on 2026-10-28 (below) and with 1.0.114 on 2026-11-23 (below); live macOS
   verification remains pending.
5. **F3: Copilot GPT temporary/home reads** (plan §8): attempted on Linux on
   2026-11-23 with Copilot 1.0.114 and `gpt-6-luna` — temporary, home, and
   symlink-target reads not accessed (see "Linux live verification"); the GPT
   role's refusal was model-level, with tool-level denials recorded for the
   Claude role in the same run. macOS GPT confinement evidence remains open.
   Native Codex's declined dedicated-search requests likewise remain untested
   operations, not confinement successes.
6. **Authenticated policy and network boundaries** (plan §8): unavailable
   authenticated policy evidence remains unverified. An empty Codex home removes
   its usual stored login, not allowed environment authentication; accepted
   Claude `doctor` "not fetched" states are not authenticated checks. Network
   denial requires controlled connection attempts and a working comparison path;
   the historical DNS failures alone do not establish it. Managed deployments
   remain unsupported.
7. **F4: native Claude tool audit** (plan §2): implemented in the working tree;
   see the stream evidence below. Document snapshots archive the reviewed
   document only; emitted tool logs do not guarantee complete file/page archives.

## HTML agreement reports (Unreleased, 2026-11-23)

Implemented `duo --render-report RUN_DIR [--report-output PATH]` and best-effort
automatic `report.html` for terminal results and CLI error/interruption paths
with a run manifest, even before the first turn completes. The report reads
structured audit records only, including the 0.5.0 layout; it does not read
original document/context sources or raw logs.
It includes reviewer configuration and CLI versions (untested marked
experimental), task/criteria, context metadata, counts, outcomes, full review
reasons, snapshot/hash-derived collapsible diffs, and shared approval hashes.
Missing snapshots are noted; a running state without a result is interrupted.
Without saved state, the report shows interrupted, zero completed turns, attempts
as "Not recorded", task/criteria from the manifest, the initial document from
`plan-000.md` (or an empty document with a note if unavailable), and the recorded
error message.

The single HTML file is deterministic and escapes all record-derived text.
It has embedded CSS, no JavaScript, links, or external assets. Reports remain
sensitive audit material. Automatic failures warn without changing review
outcomes or existing artifacts, including on a second signal during report
writing; setup failures before a run manifest exists may have no report.
Review semantics and the package version remain unchanged.

Offline coverage in `tests/test_report.py` uses synthetic records and the
existing workflow harness: hostile markup, deterministic output, legacy CLI
rendering, all terminal outcomes, interruption, report-write failures, sparse or
missing snapshots, exact newline hashing, context metadata, and symlink refusal.

Validation on Linux: `PYTHONPATH=src /home/hatch/.venvs/duo/bin/python
scripts/run_tests.py`, with `TMPDIR` under this checkout's `build/tmp`, passed
**274 tests, no skips** (253 existing plus 21 report tests). Standalone rendering
also succeeded for the supplied `run-1q3qeibv` and `run-si8ey3j6` records: each
showed agreement in three turns, available revision diffs, and byte-identical
output on rerender. Outputs were written under this checkout's ignored `build/`
directory; the original audit directories were unchanged. These are offline
rendering checks, not new reviewer/model runs.

## Captured context and prompt budget (Unreleased, 2026-11-23)

Section 3's context capture and budget mechanism is implemented. The credential
scanner prerequisite was verified in `models.new_secret`: distinct candidates
are passed to `_contains_all` for a single indexed scan of known text. Context
uses no known-document exception. The default prompt ceiling is **512 KiB,
ratified by the owner on 2026-11-23**, as documented in README, SECURITY, and
the changelog; it narrows compatibility versus 0.4.6.

Offline verification: **250 tests passed, no skips**, using
`PYTHONPATH=src python scripts/run_tests.py`
with temporary files under this checkout's `build/tmp`. This includes 18 new
context tests and three new saved-change example tests (21 additions to the
229-test section-2 baseline). They cover exact bytes/hashes, Unicode, file
validation and document identities, credential rejection, aggregate limits,
startup and diff/history/retry/wrapper overflows, preserved documents, overrides,
unchanged captures, separately mutable files, and audit-write failures.

Step 6 measurements (coordinator-measured 2026-11-23): per-turn prompts for
all seven recorded runs under the Duo state directory were reconstructed with
0.4.6's own `duo.prompts.build_prompt`, using each stored snapshot in turn as
the document and the history and revision diff from the run record.

| Run | Largest prompt (bytes) |
| --- | ---: |
| `run-1q3qeibv` | 150,232 |
| `run-knhtl1zk` | 109,987 |
| `run-0uuafp4m` | 93,278 |
| `run-wp91fnm3` | 66,573 |
| `run-9zoalswh` | 22,473 |
| `run-6d6fk0q1` | 5,292 |
| `run-9p0ghkni` | 4,998 |

The largest prompt's components, in bytes: current document **72,347**;
last-revision diff **70,208**; task **3,358**; criteria **2,448**; instructions
about **1,045**; review history **385**. The largest fixed parts
(instructions + task + criteria + document) were about **79 KB**. The other
supported examples were not measured separately; their prompts are built by
the same machinery. The new saved-change example's live run began with an
initial prompt of **3,641 bytes**. The **512 KiB** default, ratified by the
owner on 2026-11-23, accepts the largest measured prompt with about **3.5x
margin**.

Earlier bounded live verification was attempted by the implementer on Linux using
`examples/review_saved_change.sh` and a synthetic three-line integer addition
function. The captured diff changes subtraction to addition; saved output from
three real unittest cases (positive, negative, zero) reports success. No project
source or private target material was supplied. Both captures were checked
against their source bytes and SHA-256 hashes after each run:

| Captured file | Bytes | SHA-256 |
| --- | ---: | --- |
| `change.diff` | 172 | `3cc046e28ef3dfdf7ea32dc3934b40eaf5a85f69eeb9797d49c73a062a1ad5b0` |
| `tests.txt` | 291 | `c6317e013afe12b361bccefeebd9c65cb3b5df996102669075cb800b07317f1e` |

The full initial review prompt was **3,641 UTF-8 bytes**, below the 512 KiB
ceiling. Native Codex **0.171.0**, model `gpt-6-astra`, and native Claude Code
**2.1.330**, model `claude-sonnet-5-5`, passed preflight. Codex used the explicit
`--allow-untested-cli` exception; no version range or control was weakened.
The example bounded the run to four accepted turns, one attempt per turn,
120 seconds per call, and 480 seconds overall. The native-pair attempt
`run-cxas0p58` exited 1 on the first Codex call with **workspace routing discovery
failed**, after transport retries; zero reviews were accepted. An earlier
attempt (`run-o4blsyc9`) had failed to initialize Codex on the read-only home;
subsequent runtime directories were placed under the checkout. These attempts
used isolated CLI homes without logins.

A final Claude-first attempt (`run-r34a13ip`), bounded further to 45 seconds per
call and 180 seconds overall, exited 1 on timeout. Its emitted stream contains
init and API-retry events, no terminal review; metadata records missing
completion and unavailable usage. It also accepted zero reviews. No output
document was created. Exact context captures and the empty initial snapshot
survived both failures. Local synthetic inputs, results, and private audits are
under ignored `build/context-live/` in the implementation checkout.

Subsequent coordinator live verification: **`run-qr36s0ac`, 2026-11-23, Linux**.
The shipped `examples/review_saved_change.sh` ran end-to-end with the installed
package and real CLI logins against a synthetic subject repository, in review
mode with output `saved-change-review.md` and two `--context-file` captures.
The native reviewers were Codex `gpt-6-astra` via **codex-cli 0.171.0** and
Claude `claude-sonnet-5-5` via **Claude Code 2.1.330**. The run used
`--allow-untested-cli`: Codex 0.171.0 is outside the tested range, so this is
untested evidence, not range support. Outcome: status **"agreed" in 3 turns**.

The successful run's `context.json` records `context-001.bin` = `change.diff`,
**172 bytes**, SHA-256
`3cc046e28ef3dfdf7ea32dc3934b40eaf5a85f69eeb9797d49c73a062a1ad5b0`,
and `context-002.bin` = `tests.txt`, **291 bytes**, SHA-256
`c6317e013afe12b361bccefeebd9c65cb3b5df996102669075cb800b07317f1e`.
Both hashes match the source files. Copilot and macOS context workflows remain
unverified live.

## Native Claude audit stream (Unreleased, 2026-11-23)

Native Claude now runs with `--output-format stream-json --verbose`. Raw stdout
and stderr remain in each attempt directory through errors, limits, timeout,
and interruption. Only one valid successful terminal result at exit zero can
supply the structured response; partial assistant output never becomes an
approval or revision. `claude-stream.json` separates process outcome, stream
completion, payload validation, and usage availability (missing usage is not
reported zero). Preflight and drift checks require the stream format and flags.
Fixture probes still use the restricted reviewer environment introduced in
0.4.6; smoke denial extraction correlates explicit denial events and terminal
summaries without treating all tool errors as denials.

Evidence in `tests/fixtures/claude-2.1.330/` is from **Linux, Claude Code 2.1.330,
model `claude-opus-5-5`**. The four discovery streams supplied for this task
were captured on 2026-11-23 with Duo's restrictions and schema, then imported
unchanged into local attempt-shaped directories and sanitized using the repo's
`record_fixtures.py attempt claude` machinery. These are discovery captures,
not new end-to-end Duo agreement runs. No process metadata was invented for
those imported captures.

| Fixture | Observed coverage |
| --- | --- |
| `events-noweb.jsonl` | Successful Read, Glob, Grep requests and matching results; missing-file Read error; outside-cwd Read denial with explicit system event, matching error result, and terminal denial summary; successful structured terminal and token usage |
| `events-web.jsonl` | Successful WebSearch and WebFetch requests, inputs and results; successful structured terminal and token usage; web server-tool counters zero despite those calls |
| `events-webtools.jsonl` | Additional live capture with web enabled: successful Read, Glob, Grep, and WebSearch requests and matching results; successful structured terminal and token usage; web server-tool counters zero despite WebSearch |
| `events-maxturns.jsonl` | Read request/result followed by `error_max_turns`, `is_error: true`, no structured review; discovery reported exit 1; replay also rejects an artificial exit zero |
| `events-timeout-noweb.jsonl`, `events-timeout-web.jsonl` and their metadata | Fresh adapter runs with 150-second bounds: init/API retry events, then timeout; no tool calls, terminal result, or terminal usage; raw events retained and no review accepted |
| `version.txt`, `help.txt`, `doctor.txt` | Fresh probes through the recorder's restricted environment; stream options advertised; remote managed settings not fetched; feature-flag service unreachable |

The fresh bounded attempts used a copy of the synthetic discovery repository
inside the working tree. They did not reproduce successful model calls in this
execution environment, so successful tool coverage comes from the supplied raw
discovery streams. A third fresh attempt with `--max-turns 1` also timed out
before tools or a terminal result; it provides no additional turn-limit evidence.
The live drift check reported 2.1.330 `compatible`, with no help-flag changes
from the baseline fixtures; this is a probe result, not a live enforcement claim.
The supplied captures now cover every enabled tool in each configuration:
Read/Glob/Grep with and without web access, plus WebSearch/WebFetch with web
enabled. macOS stream behavior remains unverified. Web-disabled init omits
WebSearch/WebFetch; this is not an independent network-denial test. Tool results
can be summaries or excerpts, and fixtures
trim long results and remove identifiers, reasoning payloads, and rate-limit
account metadata while preserving inputs, correlation, error flags, and
structured responses. Neither these
fixtures nor full emitted logs promise complete file or web archives.
When importing an interrupted stream, the recorder omits an incomplete final
line and records `fixture_import.truncated_final_line` in fixture metadata;
raw attempt logs retain that line. The parser's size cap follows the configured
`--max-output-mb` limit (32 MiB by default), including verbose tool output.

Validation on this Linux working tree: `PYTHONPATH=src python scripts/run_tests.py`
passed all 229 tests with no skips. The reviewer fixes add eight regressions to
the 221-test first-round baseline: output-format pairing, interrupted fixture
imports and malformed-line rejection, account-metadata sanitization, configured
parser limits, deeply nested JSON, and metadata-write failures during errors and
success. Existing replay checks also cover the new web-enabled local-tool capture.
Existing approval, revision, deadline, and interruption checks still pass.

## Assurance-record correction (plan §9.2)

F2–F4 documentation and policy metadata now separate supplied settings,
advertised options, selected effective metadata, and historical behavior tests.
This correction adds no live evidence and changes no restrictions or probes.
Preflight and drift checks make no model calls, can retain allowed credentials,
and do not exercise write/read/network boundaries or every tool restriction.
The deterministic Codex write-denial results below remain historical evidence:
Linux x86-64 0.164.0, 0.165.0, 0.166.0; macOS 27.2 arm64 0.167.0. The separate
0.164.0 model-directed write attempt remains inconclusive. Open items 5–7 retain
the missing confinement, authenticated-policy/network, and audit evidence.

## Codex 0.167.0 compatibility validation

2026-11-10, macOS 27.2 arm64; codex-cli 0.167.0 (installed by Codex's own
update), Claude Code 2.1.322, Copilot CLI 1.0.95. The drift check had opened
issue #2 for it as `untested-version`.

Compared with 0.166.0, `exec --help` is unchanged and the empty-home MCP list is
empty; with Duo's options and an empty `CODEX_HOME`, all nine features Duo
disables are false. New features, all false: `apps_early_resource_discovery`,
`code_mode_host_output_truncation`, `incremental_exec_tools`,
`interrupt_on_cancel`, and `tui_extensions_statusline` (under development),
`gcp_setup_wizard` and `suppress_model_change_announcements` (stable).
`respect_system_proxy` moved to experimental (false); `deferred_tool_world_state`
became stable and true, which no Duo control depends on.

| Check | Result |
| --- | --- |
| Startup inspection smoke, native pair | both read the repository; no hook or MCP sentinel ran |
| File confinement, same pair | Codex read the three outside canaries (documented); Claude's `Read` was denied for all three |
| Search confinement, same pair | Claude's `Glob` and `Grep` were denied outside the repository; Codex declined, having no dedicated search tools |
| Codex read-only sandbox, deterministic probe (`codex sandbox` with Duo's options) | writes inside the repository, outside it, and in the home directory failed with "Operation not permitted" (the macOS sandbox's wording) and created no files |
| Live native Codex + native Claude | agreed in 3 turns and 3 attempts |
| Live native Codex + Copilot Claude | agreed in 3 turns and 3 attempts, no token in any file |

These runs also exercised the macOS 27 fix for preferences-domain detection:
native Claude and Copilot passed preflight unpatched. `calc.py` unchanged,
read-only commands only. Fixtures: `tests/fixtures/codex-0.167.0/`.

## Codex 0.166.0 compatibility validation

2026-11-02, Linux x86-64; codex-cli 0.166.0 from a scratch npm install (the
installed 0.165.0 was left alone), Claude Code 2.1.316, Copilot CLI 1.0.96.

Compared with 0.165.0, `exec --help` is unchanged and the empty-home MCP list is
empty; with Duo's options and an empty `CODEX_HOME`, all nine features Duo
disables are false. New features: `critical_path_timing`,
`guardian_native_multi_agent_history`, `mcp_connection_sharing`,
`omit_skill_descriptions`, `simplified_selection_hints`, `skill_catalog_deltas`,
and `untrusted_as_tool_output` (under development, false) and
`plugin_discovery_prefetch` (stable, true); `code_mode_tool_description_first`
was removed. `plugins` stays disabled, and after these checks the only files
Codex had written under `~/.codex` were its state database, model cache, and
temporary directory, none of them plugin data.

| Check | Result |
| --- | --- |
| Startup inspection smoke, native pair | both read the repository; no hook or MCP sentinel ran |
| File and search confinement, same pair | as for 0.165.0: Codex read the three outside canaries (documented); Claude denied every outside read and search |
| Codex read-only sandbox, deterministic probe | writes inside and outside the repository failed with "Read-only file system" and created no files |
| Live native Codex + native Claude | agreed in 3 turns and 4 attempts |
| Live native Codex + Copilot Claude | agreed in 3 turns and 3 attempts |

As with 0.165.0, the extra attempt was the first Codex call of the run, which
drafts the plan, reaching the 300-second limit of these checks. Successful first
calls took 23 to 154 seconds and later calls 11 to 17 seconds, and Codex emits
nothing until a step completes, so these are most likely slow drafts rather than
hangs; Duo's default `--timeout` is 600 seconds. No token in any file, `calc.py`
unchanged, read-only commands only. Fixtures: `tests/fixtures/codex-0.166.0/`.

## Codex 0.165.0 compatibility validation

2026-11-02, Linux x86-64; codex-cli 0.165.0 (installed by Codex's own update),
Claude Code 2.1.316, Copilot CLI 1.0.96. `check_drift.py` reported
`untested-version` with every configuration probe passing (not a behavioral test).

Compared with 0.164.0, `exec --help` is unchanged and the empty-home MCP list is
empty. With Duo's options and an empty `CODEX_HOME`, all nine features Duo
disables are false. New features: `guardian_recorded_items`, `guardianv3`, and
`tui_multi_backend` (under development, false) and
`prediction_preserve_parent_tools` (stable, true); `reasoning_effort_override`
became stable and true. Neither enabled feature adds a tool, a configuration
source, or network access; no inspection-policy override was relaxed.

| Check | Result |
| --- | --- |
| Startup inspection smoke, native pair | both read the repository; no hook or MCP sentinel ran |
| File confinement, native pair | Codex read the temporary, home, and symlink-target canaries, matching the known lack of read confinement; Claude denied all three |
| Search confinement, same pair | Claude denied outside `Glob` and `Grep` and did not follow the link; Codex declined for lack of search tools (not evidence of search confinement) |
| Codex read-only sandbox, deterministic probe | writes inside and outside the repository failed with "Read-only file system" and created no files |
| Live native Codex + native Claude | agreed in 3 turns and 3 attempts |
| Live native Codex + Copilot Claude | agreed in 3 turns and 4 attempts |

The extra attempt in the mixed run was Codex starting its turn and then emitting
nothing until Duo's 300-second call limit; the retry succeeded. The 0.164.0
validation saw the same kind of stall, so it is a pattern to watch, not yet
explained. No token in any file, `calc.py` unchanged, read-only commands only.
Fixtures: `tests/fixtures/codex-0.165.0/`, including a live approval.

## Codex 0.164.0 compatibility validation

2026-10-28, Linux x86-64, Python 3.11; codex-cli 0.164.0, Claude Code 2.1.311,
Copilot CLI 1.0.96. Validation used `--allow-untested-cli` before extending the
tested range to `>=0.162.1,<0.165.0`. No release was published as part of this check.

Compared with the recorded 0.163.0 probes, `exec --help` is unchanged, the
empty-home MCP list is empty, and all nine features Duo explicitly disables
remain false. The seven new features are under development and false:
`app_server_websocket_compression`, `executor_capability_discovery_v2`,
`mcp_tool_list_changed`, `multi_env_copy_file`, `runtime_agent_instances`,
`subagent_thread_environments`, and `wake_on_agent_messages`.
Three existing features became stable and true: `bedrock_setup_wizard`,
`incremental_tools`, and `mcp_oauth_refresh_coordination`. These values were also
checked with an empty `CODEX_HOME`; the live tests below exercised the resulting
configuration. Four other features changed lifecycle labels without changing
their boolean values (`realtime_conversation`, `worktrees`,
`stable_environment_tools`, and `transcript_v2`). No inspection-policy override
was relaxed.

| Check | Result |
| --- | --- |
| Startup inspection smoke, native pair | Both read the repository; the plan stayed unchanged; no hook or MCP sentinel ran |
| Live native Codex + native Claude | Agreed in 3 turns and 3 attempts |
| Live native Codex + Copilot Claude | Agreed in 3 turns and 3 attempts |
| File confinement, native pair, Codex effort `low` | Codex read the temporary, home, and symlink-target canaries, matching the known lack of read confinement; Claude denied all three |
| Search confinement, same pair | Claude denied outside Glob/Grep calls and did not follow the repository link; Codex declined because dedicated search tools were unavailable, so this is not evidence of search confinement |
| Direct Codex read-only sandbox | Reads inside and outside the repository succeeded; attempted writes in both locations failed with `EROFS` and created no files |

The two review runs used `gpt-6-astra` and native `claude-sonnet-5-5` or Copilot
`claude-sonnet-5.5`, a disposable `calc.py`, and the task of planning a subtraction
function with `unittest` coverage. Limits: 6 turns, 2 attempts per turn,
120 seconds per call, 600 seconds per run. Both preserved `calc.py` and created
only `plan.md` in the working tree. Codex used read-only shell inspection; no
Copilot token appeared in any of the retained validation files.

The additional confinement prompt timed out before emitting tool calls at the
initial 120-second bound and again at 300 seconds with default Codex effort.
The retry with explicit `low` effort completed within the 120-second bound. This
does not establish the cause of the stalls. A separate model-directed write
probe also timed out without a tool call; it is not counted as enforcement
evidence. The successful write-denial check instead ran a deterministic Python
probe through `codex --no-daemon` with Duo's `codex_options()` and
`sandbox -- COMMAND`, using `reviewer_environment('codex')`. Its target files
were first created and removed outside the sandbox to establish that denial was
not due to ordinary file permissions.

Sanitized 0.164.0 probes and a live approval are in
`tests/fixtures/codex-0.164.0/`. Fixture tests now replay control checks for every
recorded Codex minor version and approval responses for every recording that
contains one. The version-boundary tests continue to reject 0.165.0. The offline
suite passes 168 tests with no skips. Live validation covers Linux and the
default plan/no-web path; it does not add macOS or web-mode coverage.

## Copilot 1.0.103+ compatibility

2026-10-28, Linux x86-64; Copilot CLI 1.0.105 from a scratch npm install (the
installed 1.0.96 was left alone), codex-cli 0.164.0, Claude Code 2.1.312. The
weekly drift run of 2026-10-26 reported a probe failure for the newest Copilot on
both platforms: Copilot 1.0.103 (1.0.99 was never published) removed `--remote`,
`--no-remote`, `--remote-export`, and `--no-remote-export`, and added
`--sync-session[=on|off]` ("read-only account history sync for this run only
... does not enable incoming control"), `--trust-folder`,
`--disable-client-telemetry`, and `--host-*` options. `copilot help config`
lists no remote-control or sync setting, and the effective configuration in
Duo's fresh home has none; Duo's other required settings still take effect.

Duo now picks the session options from the installed CLI's help: the two
`--no-remote` flags when both are listed, otherwise `--sync-session=off`, and a
setup error when neither is. Option names are matched whole. Fixtures:
`tests/fixtures/copilot-1.0.105/`.

| Check (Copilot 1.0.105) | Result |
| --- | --- |
| `check_drift.py` | compatible (1.0.96 too, with the old flags) |
| Startup inspection smoke, both reviewers through Copilot (`claude-sonnet-5.5`) | both inspected the repository; no hook or MCP sentinel ran |
| File and search confinement, same pair | all 18 outside reads and searches denied ("no interactive user response was available"); the repository link was not followed |
| Live Copilot + Copilot (`claude-sonnet-5.5` in both roles) | agreed in 4 turns and 4 attempts |
| Live native Codex + Copilot Claude | agreed in 3 turns and 3 attempts |

Every Copilot attempt ran with `--sync-session=off` and without the old flags;
no token in any file, `calc.py` unchanged, no Copilot runtime left behind, and
the policy records `session_flags`. Whether a session is synced is decided by
GitHub's service; the evidence is the option Duo passes and the CLI's own
description of it. macOS is not yet checked.
Both Copilot roles here used Claude; these denials supply no new GPT
temporary/home read evidence.

## Web access live verification

2026-10-14, Linux, `b98dd5c` (codex-cli 0.162.1, Claude Code 2.1.299, Copilot CLI
1.0.96). Report mode with `--allow-web --max-turns 6`: check a `refs.bib` of two
real entries (Hohenberg–Kohn 1964, Kohn–Sham 1965) and one invented entry with
an unregistered DOI against DOI records.

| Pair | Status | Turns | Result |
| --- | --- | --- | --- |
| native `gpt-6-astra` + `claude-sonnet-5-5` | agreed | 4 | real entries confirmed except missing end pages (B864–B871, A1133–A1138); invented DOI not found (Crossref and DOI handle API 404) |
| Copilot `gpt-5.4` + `claude-sonnet-5.5` | agreed | 3 | same findings, every fetched URL listed |

Every finding matched an independent Crossref query; no token in any file; the
material was unchanged. Native Codex used web search; its sandboxed `curl` and
Python requests could not resolve hosts. Copilot used only `web_fetch` (doi.org,
api.crossref.org, journals.aps.org). Native Claude's JSON output lists no tool
calls and its web-search/fetch counters stayed at zero, so a separate call with Duo's exact
command line asked it for the 0.3.1 wheel's checksum from the release's
`SHA256SUMS`: it returned the correct value, which requires a fetch, and also ran
a web search. Not yet tested: `--allow-web` on macOS, with mixed pairs, or in
other modes.

These logs are partial emitted evidence, not independent archives of files or
pages. Disabling web still permits readable information to reach model providers;
web adds search-provider and arbitrary-URL disclosure paths. The native Claude
stream implementation and its observed coverage are recorded above; it does not
retroactively add tool transcripts to these historical runs.

## Document modes live verification

2026-10-13, branch `document-modes`, same Linux machine and CLI versions as above.
Each run used a synthetic folder with two NVT runs: T300 completed 2000 steps;
T500 aborted at step 1200 (SCC failure), with known energies and temperatures.
Every number the reviewers wrote was recomputed independently from the data.

| Mode | Backends and models | Outcome | Check |
| --- | --- | --- | --- |
| `description` | native `gpt-6-astra`, `claude-sonnet-5-5` | agreed, 4 turns | accurate, cites files, finds the T500 abort |
| `report` | native, same models | agreed, 3 turns | every statistic matches (means, SDs, slopes, endpoint changes, matched-window comparison) |
| `review` | native, same models | agreed, 3 turns | all 5 planted errors found (major), 2 correct claims confirmed; all cited values and lines correct |
| `review` | Copilot `gpt-5.4`, `claude-sonnet-5.5` | agreed, 5 turns | same 5 errors and 2 correct claims; all cited values correct; tools `view`, `glob`, `rg` only; no token in audit files |
| `report` | Copilot, same models (0.2.0, `a207d21`) | agreed, 5 turns | every number matches, including intermediate sums; its matched window (0–600 fs) keeps T500's failure-step row without flagging it, where the native report excluded it |
| `description` | Copilot, same models (0.2.0, `a207d21`) | agreed, 3 turns | accurate, cites files, finds the T500 abort; does not describe itself |

On macOS (2026-10-13, macOS 26.6.2 arm64, commit `c80a81f` with the restricted
reviewer environment; codex-cli 0.162.1, Claude Code 2.1.299), an equivalent
synthetic folder (regenerated with a fixed seed; T300 completed 2000 steps, T500
aborted at step 1200) gave, natively with `gpt-6-astra` and `claude-sonnet-5-5`:

| Mode | Outcome | Check |
| --- | --- | --- |
| `description` | agreed, 3 turns, 3 attempts | accurate, cites every file, finds the T500 abort, states what could not be verified |
| `report` | agreed, 3 turns, 3 attempts | all 25 reported values recomputed and matching (full-run and 0–550 fs window means, SDs, ranges, block means, endpoint changes, differences); excluded T500's failed-step row |
| `review` | agreed, 3 turns, 3 attempts | all 5 planted errors found (aborted run ranked high), both correct claims confirmed, every line reference correct |

Native Claude cost $0.17 for all three runs. Only the mode's document was written;
the material was unchanged.

The first description run hit its turn limit: the document is saved inside the
folder it describes, so a reviewer revised it to describe itself, resetting
approvals. Non-plan modes now tell reviewers which file holds the document; the
rerun agreed. In every run, the material under review was left unchanged and
only the mode's document was written.

## Releasing

From 0.4.2, releases use the repository's own tools: `scripts/release.py` for the
checks and `.github/workflows/release.yml` for the published files.

1. Date the version in `CHANGELOG.md`, bump `pyproject.toml`, and commit; that
   commit is the release target.
2. Rerun the live backend matrix and `scripts/check_drift.py` on it when code
   changed since the last evidence, and record the results here.
3. Run `python3 scripts/release.py <commit>` (Python 3.11 or newer). From a clean
   clone it builds the wheel and sdist with the pinned tools in
   `scripts/build-requirements.txt`, installs each into a fresh environment with
   `constraints.txt`, runs the tests strictly from the installed package, checks
   the version and that the wheel matches the commit, and writes the files,
   `SHA256SUMS`, and a notes template to `dist/release-<version>/`.
4. Push, wait for all CI jobs to pass on that commit, then push an annotated tag
   `v<version>`. The release workflow refuses a tag that does not match the
   package version or whose commit has no passing CI, runs the same script,
   attests the files' build provenance, and creates a **draft** release with
   them and the notes template.
5. Fill in the draft's notes (the release report), then publish it. Check the
   published files against `SHA256SUMS` after download. Builds are not
   byte-reproducible, so the published checksums are the workflow's, not step
   3's.

A push to main that changes the release tooling, or a manual run of the release
workflow on any branch, is a dry run: the same checks
and build, without attestations or a release.

Notes: macOS CI jobs can wait for a runner and be cancelled without running;
rerun the failed jobs instead of pushing a new commit, which would change the
release target. Publishing the draft needs the release page on GitHub or a
token with Contents read and write (on the macOS machine, `gh` is logged in:
`gh release edit vX.Y.Z --draft=false --latest`); rerunning jobs also needs
Actions read and write. The per-job API sometimes
returns HTTP 502; the run's overall conclusion is enough, since any failed or
cancelled job makes it a failure. A live run that stops at the turn limit is not
necessarily a regression: compare the prompts in its attempt logs with the
previous release's before deciding.

0.2.1 evidence (2026-10-13, macOS 26.6.2 arm64): changes since 0.2.0 are the
`--output` validation, which runs before any reviewer starts, and the package
version string, so the live backend matrix was not rerun; the offline suite covers
both changes on Linux and macOS in CI. `check_drift.py`: codex-cli 0.162.1,
Claude Code 2.1.296, and Copilot CLI 1.0.95 all compatible, no flag changes.

## macOS live verification

2026-10-13, macOS 26.6.2 (arm64), commit `8ffff97` (unreleased: reviewer CLIs get
only the environment they need). CLIs: codex-cli 0.162.1, Claude Code 2.1.299,
Copilot CLI 1.0.95. Same synthetic `calc.py` task and limits as on Linux
(`--max-turns 4 --max-attempts 2`); models `gpt-6-astra` and `claude-sonnet-5-5`
natively, `gpt-5.4` and `claude-sonnet-5.5` through Copilot.

| Codex | Claude | Exit | Status | Turns | Attempts | Files containing the token |
| --- | --- | --- | --- | --- | --- | --- |
| native | Copilot | 0 | agreed | 3 | 3 | 0 |
| Copilot | native | 0 | agreed | 3 | 3 | 0 |

Tools: native Codex ran read-only commands (`pwd`, `ls`, a loop checking for
`AGENTS.md`/`CLAUDE.md`); Copilot used `glob`, `view`, and `rg`; native Claude
had no permission denials ($0.03). `reviewer-policy.json` shows each CLI received
only `HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `TMPDIR`, and
`USER`, plus `COPILOT_GITHUB_TOKEN` for Copilot, so both logins work under the
restricted environment with real model calls. Native/native and Copilot/Copilot
ran live on macOS before 0.1.0.

0.3.0 release candidate (`4a4ce4d`, 2026-10-13, macOS 26.6.2 arm64; codex-cli
0.162.1, Claude Code 2.1.299, Copilot CLI 1.0.95): the restricted reviewer
environment is new, so the full backend matrix and both smoke tests were rerun.
Same `calc.py` task and models as before, `--max-turns 4 --max-attempts 2`:

| Codex | Claude | Exit | Status | Turns | Files containing the token |
| --- | --- | --- | --- | --- | --- |
| native | native | 0 | agreed | 3 | 0 |
| native | Copilot | 0 | agreed | 3 | 0 |
| Copilot | native | 4 | limit reached | 4 | 0 |
| Copilot | Copilot | 0 | agreed | 3 | 0 |

The Copilot Codex + native Claude run repeated the pattern seen on the 0.2.0
candidate: Codex revised a plan Claude had approved, to settle two open choices
and adopt the test-file name Claude suggested; Claude approved again on turn 4.
Rerun with `--max-turns 6`: agreed in 3 turns, no token. Each native CLI received
9 environment variables and Copilot 10 (its token included); only read-only tools
were used. Smoke test, natively and through Copilot: repository inspected, no hook
or MCP sentinel ran. `check_drift.py`: all compatible. The restricted environment
ran live on Linux with the 0.3.1 candidate ("Linux live verification").

## Linux live verification

2026-10-13, Ubuntu 22.04.5 LTS, kernel 5.15.0-198-generic, x86-64, Python 3.11.17.
CLIs: codex-cli 0.162.1, Claude Code 2.1.296, Copilot CLI 1.0.96 (within the
tested 1.0.x range). Offline suite: 137 tests, OK. `check_drift.py`: all compatible.
The first preflight refused native Codex because the user configuration defines
an MCP server: `-c mcp_servers={}` does not remove configured servers. The MCP
check now lists servers against an empty `CODEX_HOME`, excluding user configuration
as reviews do with `--ignore-user-config`, but also removing the usual stored-login
location (allowed authentication variables can remain); the live runs below were made with
that server commented out, and the smoke test was repeated with it enabled.

Smoke test: Codex and Claude, natively (`gpt-6-astra`, `claude-sonnet-5-5`) and
through Copilot (`gpt-5.4`, `claude-sonnet-5.5`), each inspected the repository
with no hook or MCP sentinel executed.

Backend matrix, synthetic `calc.py` task, `--max-turns 4 --max-attempts 2`:

| Codex | Claude | Exit | Status | Turns | Files containing the token |
| --- | --- | --- | --- | --- | --- |
| native | Copilot | 0 | agreed | 3 | 0 |
| Copilot | native | 0 | agreed | 3 | 0 |
| native | native | 0 | agreed | 3 | 0 |
| Copilot | Copilot | 0 | agreed | 3 | 0 |

Repeated on the release candidate (`3e7828c`) with the user's Codex MCP server
enabled: same results for all four combinations (agreed in 3 turns, exit 0, no
token in any file, same read-only tools).

0.2.0 release candidate (`5b2465c`): native/native, Copilot/Copilot, and native
Codex + Copilot Claude agreed in 3 turns. Copilot Codex + native Claude reached
the 4-turn limit twice (exit 4): Claude approved each time, then Codex revised an
approved plan to name the test framework and file. Its first-turn prompt and
arguments were identical to the 0.1.0 run apart from the random section boundary
and the log directory, so this is reviewer variation, not a change in Duo. A third
run with `--max-turns 6` agreed in 3 turns. No token in any file; same read-only
tools.

0.3.1 release candidate (2026-10-14, `e793894` plus the `GH_HOST` fix; codex-cli
0.162.1, Claude Code 2.1.299, Copilot CLI 1.0.96), the first Linux runs with the
restricted reviewer environment: native/native and Copilot/Copilot each agreed in
3 turns and 3 attempts (exit 0), with no token in any file and `calc.py`
unchanged. `reviewer-policy.json` shows each native CLI received `DBUS_SESSION_BUS_ADDRESS`,
`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`, and
`XDG_RUNTIME_DIR`, and Copilot the same plus `COPILOT_GITHUB_TOKEN`, so both native
logins and the Copilot token work under it on Linux. Tools: native Codex ran
read-only `rg`, `ls`, and `cat` commands; Claude had no permission denials
($0.03); Copilot used `glob`, `view`, and `rg`. `check_drift.py` under the
restricted environment: all compatible.

Mixed pairs on Linux with the restricted environment (2026-10-14, `93e8b27`, the
0.3.1 release, same CLIs, task, and limits): native Codex + Copilot Claude and
Copilot Codex + native Claude each agreed in 3 turns and 3 attempts (exit 0), no
token in any file, `calc.py` unchanged. Native CLIs received the same 10 variables,
Copilot those plus its token; native Codex ran read-only `rg`, `cat`, and
file-existence loops, Copilot used `glob`, `view`, and `rg`, and native Claude had
no permission denials. All four backend combinations have now run live with the
restricted environment on Linux and macOS. These runs' `reviewer-policy.json`
omitted the settings Duo sets for Copilot (`COPILOT_HOME`, `COPILOT_ALLOW_ALL`,
and others); it now lists every variable a Copilot review receives.

0.4.0 release candidate (2026-10-14, Linux; `aa45d65` plus the version bump;
codex-cli 0.162.1, Claude Code 2.1.299, Copilot CLI 1.0.96): the default path
changed only in what `reviewer-policy.json` records, so native/native and
Copilot/Copilot were rerun on the `calc.py` task (`--max-turns 4 --max-attempts
2`). Both agreed in 3 turns and 3 attempts, no token in any file, `calc.py`
unchanged, read-only tools only. The policy recorded `web: false`, 10 variables
for each native CLI, and all 20 Copilot receives, including Duo's own settings.
`check_drift.py`: all compatible.

0.4.1 release candidate (2026-10-14, Linux; `18a1b09` plus the version bump;
codex-cli 0.162.1, Claude Code 2.1.300, Copilot CLI 1.0.96): the fixes touch how
Duo stops reviewers, discards Copilot's runtime, and reads input files, all on
every run's path, so native/native and Copilot/Copilot were rerun on the
`calc.py` task. Both agreed in 3 turns and 3 attempts, no token in any file,
`calc.py` unchanged, read-only tools only, and no Copilot runtime left in any
attempt. The policy recorded `web: false`, 10 variables for each native CLI, and
20 for Copilot. `check_drift.py`: all compatible.

Codex 0.163.0 (2026-10-21, Linux; Claude Code 2.1.306, Copilot CLI 1.0.96;
drift issue #1): its eight new features (`async_compaction`, `credential_masking`,
`retain_client_tool_outputs`, and others) are all under development and off by
default; `exec --help` is unchanged and the MCP list with an empty `CODEX_HOME`
is empty, with an MCP server in the user's own Codex configuration.
`smoke_inspection.py --confinement --search-confinement` (native pair): no hook
or MCP sentinel ran; native Codex read all three outside files, as documented,
and declined the search test for lack of search tools; Claude's reads and
searches outside were denied. Live Duo runs on the `calc.py` task: native/native
and native Codex + Copilot Claude each agreed in 3 turns and 3 attempts, no token
in any file, `calc.py` unchanged, read-only tools only, the version recorded as
tested.

0.4.2 release candidate (2026-10-21, Linux; codex-cli 0.163.0, Claude Code
2.1.306, Copilot CLI 1.0.96): `scripts/release.py` passed on the release commit,
167 tests from each installed artifact. Live runs used the `duo` installed from
the 0.4.2 wheel with the new `constraints.txt` (LangGraph 1.2.16). Native/native
agreed in 3 turns and 3 attempts. Copilot now refuses `gpt-5.4`, `gpt-6-astra`,
and `claude-opus-5.5` for this account ("Model ... from --model flag is not
available"); Duo stopped after one attempt without retrying, as intended. With
`claude-sonnet-5.5` in both roles, the Copilot pair agreed in 3 turns and 3
attempts. No token in any file, `calc.py` unchanged, read-only tools only, no
Copilot runtime left behind; the policy recorded `web: false`, 9 variables per
native CLI (`LC_ALL` is set by `micromamba run`, not used here) and 19 for
Copilot.

0.4.3 release candidate (2026-10-28, Linux; codex-cli 0.164.0, Claude Code
2.1.312, Copilot CLI 1.0.105 from a scratch install): `scripts/release.py` passed
on the release commit, 171 tests from each installed artifact. From the `duo`
installed from the 0.4.3 wheel: native/native and Copilot/Copilot
(`claude-sonnet-5.5` in both roles, `--sync-session=off`) each agreed in 3 turns
and 3 attempts, no CLI recorded as untested, no token in any file, `calc.py`
unchanged, no Copilot runtime left behind.

0.4.4 release candidate (2026-11-02, Linux; Claude Code 2.1.316, Copilot CLI
1.0.96): `scripts/release.py` passed on the release commit, 171 tests from each
installed artifact. From the `duo` installed from the 0.4.4 wheel: native/native
with codex-cli 0.166.0 and native Codex 0.165.0 + Copilot Claude each agreed in
3 turns and 3 attempts, no CLI recorded as untested, no token in any file,
`calc.py` unchanged.

Tools seen in the attempts:

- Copilot: only `glob`, `view`, and `rg` (its name for `grep`); all built-in MCP
  servers reported disabled.
- Native Codex: read-only commands (`rg --files`, `ls`, `cat`, `python --version`).
  It looked for `AGENTS.md` and `CLAUDE.md` in parent directories, `/tmp`, and `/`,
  consistent with the known lack of read confinement.
- Native Claude: its JSON output does not list tool calls; no permission denials.

Historical confinement matrix (`smoke_inspection.py --confinement`, initial Linux
checks: codex-cli 0.162.1, Claude Code 2.1.296, Copilot CLI 1.0.96;
native `gpt-6-astra` / `claude-sonnet-5-5`, Copilot `gpt-5.4` /
`claude-sonnet-5.5`): each reviewer was
asked to read a file in the temporary directory, one hidden in the home directory,
and one through a symbolic link inside the repository.

| Reviewer | Temporary directory | Home directory | Repository symlink |
| --- | --- | --- | --- |
| Codex (native) | read | read | read |
| Claude (native) | denied | denied | denied |
| Codex via Copilot | not attempted | not attempted | denied |
| Claude via Copilot | denied | denied | denied |

Denials come from the CLIs' own records: Claude Code's `permission_denials` (`Read`)
and Copilot's tool events (`view`: "Permission denied because no interactive user
response was available"). No phrase from an unread file appeared in the response
or audit files. The shared Copilot CLI and settings do not turn unattempted GPT
reads into denial evidence; those temporary/home reads remain open in plan §8.

Search confinement (`smoke_inspection.py --search-confinement`, same models): each
outside directory held one file whose name and content carried separate random
phrases; reviewers were asked to list and search each directory with their search
tools only.

| Reviewer | Temporary directory | Home directory | Repository with a symlinked directory |
| --- | --- | --- | --- |
| Claude (native) | `Glob`, `Grep` denied | `Glob`, `Grep` denied | searched; link not followed |
| Codex via Copilot | `glob`, `rg` denied | `glob`, `rg` denied | `glob`, `rg` searched; link not followed |
| Claude via Copilot | `glob`, `grep` denied | denied | denied (one call covered all three) |
| Codex (native) | declined: no search tools | declined | declined |

No file-name or content phrase appeared in any response or audit file. Copilot
names its grep tool `rg` for the Codex model and `grep` for the Claude model.
Native Codex declined the dedicated-search requests without attempting them;
this is not evidence of search confinement.

**2026-11-23, Linux x86-64; Copilot CLI 1.0.114** (npm `@github/copilot`,
installed under `~/.local` on the checking VM, which previously had no Copilot
CLI; authenticated with the owner's gh token via `GH_TOKEN`, Copilot Pro
account). `check_drift.py`: Copilot 1.0.114 **compatible**; help flags added
since the fixtures: `--disable-client-telemetry`, `--sync-session`,
`--trust-folder`; removed: `--no-remote`, `--no-remote-export`, `--plain-diff`,
`--remote`, `--remote-export`. Model availability probed through the CLI:
offered — `claude-sonnet-5.5`, `gpt-6-luna`, `gpt-5.3-codex`; not offered —
`gpt-5.4`, `gpt-6-astra`, `claude-opus-5.5`, `claude-fable-5-1` (the latter
three are Pro+ models in GitHub's plan documentation, which accounts for the
previously recorded refusals; `gpt-5.4` is listed for Pro in that documentation
but was refused by the CLI). Inspection smoke with `--confinement`, both
reviewers through Copilot: repository inspected, no hook/MCP sentinel executed;
the F3 temporary-directory, home-directory, and symlink-target reads were
attempted with `gpt-6-luna` and **not read** (the GPT role declined on
workspace-boundary grounds; the same three reads in the `claude-sonnet-5.5`
role were explicitly denied at the tool layer, "Permission denied because no
interactive user response was available"). Live Copilot + Copilot run (GPT
`gpt-6-luna` + Claude `claude-sonnet-5.5`, plan mode): **agreed in 5 turns and
5 attempts** (`run-0lh3gb8y`); audit record complete, `report.html`
auto-emitted.

## Set up the Linux machine

Assumes the micromamba environment `duo` (Python 3.11) exists.

```sh
git clone https://github.com/Dnasiul/duo.git && cd duo
micromamba run -n duo python -m pip install -c constraints.txt -e .

# Offline check first: no logins, no model calls. Expect "OK" with 137+ tests.
micromamba run -n duo python scripts/run_tests.py
```

Reviewer CLIs, pinned to the tested versions (Node.js 22 or newer with npm):

```sh
npm install -g @openai/codex@0.162.1 @anthropic-ai/claude-code@2.1.296 @github/copilot@1.0.95
micromamba run -n duo python scripts/check_drift.py   # expect all "compatible"
```

Logins, done by the owner because they are interactive:

- Codex: `codex login`. Claude Code: run `claude`, then `/login`.
- Copilot: a new fine-grained token with the "Copilot Requests" permission, stored
  without typing it into a command: copy it, then `umask 077 && cat > ~/.copilot-token`,
  paste, Ctrl-D. Runs read it with `COPILOT_GITHUB_TOKEN="$(cat ~/.copilot-token)"`.

Git identity for commits made there:

```sh
git config --global user.name "Dnasiul"
git config --global user.email "37220258+Dnasiul@users.noreply.github.com"
```

## Live checks to run on Linux

Each costs a few model calls. Keep runs bounded and in scratch repositories.

1. **Inspection smoke test**: hooks and MCP launchers stay inert, the plan is
   unchanged, the repository is readable.

   ```sh
   micromamba run -n duo python scripts/smoke_inspection.py \
     --codex-model gpt-6-astra --claude-model claude-sonnet-5-5
   COPILOT_GITHUB_TOKEN="$(cat ~/.copilot-token)" micromamba run -n duo python scripts/smoke_inspection.py \
     --codex-backend copilot --codex-model gpt-5.4 --claude-backend copilot --claude-model claude-sonnet-5.5
   ```

2. **Backend matrix**: one bounded run per combination in a scratch Git repository
   with a synthetic task. Example for the mixed pair:

   ```sh
   S=$(mktemp -d) && git -C "$S" init -q && printf 'def add(a, b):\n    return a + b\n' > "$S/calc.py"
   COPILOT_GITHUB_TOKEN="$(cat ~/.copilot-token)" micromamba run -n duo duo --repo "$S" \
     --task "Plan adding a subtract(a, b) function with a unit test to calc.py" \
     --codex-model gpt-6-astra --claude-backend copilot --claude-model claude-sonnet-5.5 \
     --max-turns 4 --max-attempts 2 --timeout 300 --max-seconds 1200 \
     --state-dir "$S-state" --show-conversation --json
   ```

   Repeat for native/native, Copilot/Copilot, and Copilot Codex + native Claude.
   For each, check: exit code 0 (or a clear, correct failure), the audit has no
   token (`grep -rlFf ~/.copilot-token "$S-state" | wc -l` gives 0), and
   only read-only tools appear in the attempts.

3. **Record the evidence**: OS and kernel, Python, CLI versions
   (`versions.json`), models, outcomes. Update the README compatibility table
   (Linux live runs, mixed pairs) and `docs/SECURITY.md` if confinement was tested.

## Conventions

- Commit as `Dnasiul`; no `Co-Authored-By` or other AI attribution lines.
- Never put a token in a command line; read it from `~/.copilot-token`.
- The owner approves pushes; CI status is public at
  `https://api.github.com/repos/Dnasiul/duo/actions/runs?per_page=3`.
- Run `python scripts/run_tests.py` from the repository root against the installed
  package, or `python ../scripts/run_tests.py` from `tests/`. An absolute script
  path works from any directory. Empty suites, failures, errors, skips, and
  missing LangGraph fail the check. CI already used the correct `tests/` working
  directory; this discovery fix does not imply historical CI ran zero tests.
- Live model IDs used so far: native `gpt-6-astra`, `claude-opus-5-5`,
  `claude-sonnet-5-5`; Copilot `gpt-5.4`, `claude-sonnet-5.5` (dotted names).

## Answering blocked runs

Plan section 7.1 is implemented in the working tree (Unreleased). Paired
`--answer` / `--source-run` options select a completed blocked run explicitly.
Bounded record and snapshot reads validate the source question and document hash,
repository, document path/mode, and exact task/criteria association before locking,
audit creation, or reviewer calls. The answer receives the existing credential
check and travels in a labeled, budgeted user clarification section.

The new manifest records source identity, question, answer, and blocked/current
document hashes. Changed documents are reviewed afresh; no source approvals,
history, or findings carry over. Tests cover malformed sources, identity mismatches,
multiple sources, changed documents, fresh approvals, repeated use without source
changes, credential/budget limits, unchanged default prompts, and ordinary agreed
verdict output. This does not restore checkpoints or implement section 12 recovery.
The package version and structured verdict contract are unchanged.

Live verification (2026-11-23, Linux; recorded by the implementation coordinator
after the section 7.1 loop approved): a native-pair run (codex-cli 0.171.0,
untested, `--allow-untested-cli`; Claude Code 2.1.330; `gpt-6-astra` /
`claude-opus-5-5`, high effort) with instructions requiring the user's launch-date
decision blocked on turn 1 (exit 3, `run-5y60g5eh`; question: the public beta
launch date). Answering it with `--answer 'The public beta launch date is
2027-03-01.' --source-run run-5y60g5eh` agreed in 3 turns (`run-xsqeo16c`); the
new manifest's `answer` object records the source identity, the exact question,
both document hashes (equal here — the document was unchanged), and the answer;
the approved document uses the answered date, the new run's `verdict.json`
validates as agreed, and the source run's records are untouched. The
changed-document path is covered by the offline tests, not by this live pair.

## Structured verdicts

Plan section 11 is implemented in the working tree (Unreleased). Every reviewer
response now includes findings and finding_updates arrays. Findings retain stable
run-scoped IDs, subject hashes, moved locations, and role-owned dispositions;
approval requires explicit reconciliation of that role's open findings. Atomic
`verdict.json` publication follows the saved result, with best-effort error and
interruption artifacts. Schema 1.0, valid/invalid fixtures, and a stdlib CI gate
cover agreement bindings, severity thresholds, stale subjects, and fail-closed
consumption. Existing CLI summaries and exit codes remain unchanged. This work
does not change the package version or implement §7.1 or build mode.

Live verification (2026-11-23, Linux; recorded by the implementation coordinator
after the section 11 loop approved): the new response schema is accepted by all
three adapter paths end to end. Native pair (codex-cli 0.171.0, untested and run
with `--allow-untested-cli`; Claude Code 2.1.330; `gpt-6-astra` / `claude-opus-5-5`,
high effort): `run-p8gngkvr` agreed in 3 turns and 3 attempts; Codex reported
finding F1 (medium, subject-level, on the empty initial document) and it was
resolved before agreement. Copilot pair (Copilot CLI 1.0.114; `gpt-6-luna` in the
Codex role, `claude-sonnet-5.5` in the Claude role, high effort): `run-ue2o3av6`
agreed in 3 turns and 3 attempts; F1 (low, Codex role) resolved before agreement.
Both runs' `verdict.json` artifacts validate with `validate_verdict`; the native
run's also validates against the shipped JSON Schema (checked with the `jsonschema`
library, development-only). The reference gate was exercised on the native run's
real artifacts: pass (exit 0) on the agreed current document; exit 2 with an
injected open `high` finding; exit 2 after the document changed (stale subject).

Recorded locations bind to their own subject SHA-256, including historical locations;
document sections are supported. Diff old/new-side locations remain section 10 scope.


## Persistent checkpoints

Unreleased implementation of plan §12 for document modes. `checkpoint.json` is
a versioned, bounded JSON journal with a canonical SHA-256 integrity check and
atomic file plus directory fsync. It binds both normalized review text and exact
published file bytes, preserving CRLF documents while detecting external byte
changes. It records exact captured inputs, configuration,
committed state and findings, captured blocked-run clarification when present,
run/segment lineage, an absolute deadline, current
turn reservations, and pending transition intents. JSON contains environment
variable names only, never credential values or live provider sessions.

A reservation is durable before each adapter call. The committed state's attempt
count plus `attempts_current_turn` accounts for interrupted work without resetting
retry allowances. A durable snapshot and intent precede publication; recovery
uses before/after document hashes to discard an unpublished proposal or finish
an already-published transition idempotently. Unexpected contents are refused.
Run and repository locks exclude concurrent recovery. Context, manifest, snapshot,
audit state, schema, and integrity are validated before adapter construction.

Resume rechecks CLI controls, retains original evidence, appends segment evidence,
and clears active approvals. Historical decisions remain in the verdict; resumed
verdicts use the additive 1.1 decision `approval_epoch` field so agreement replay
requires approvals in the same epoch. Fresh verdicts retain the 1.0 format.
The package version is unchanged. Original deadlines include downtime; terminal
runs stay terminal, with blocked clarification remaining a separate new-run flow.
`duo-clean` protects recoverable and unreadable checkpoints unless explicitly
included, while permitting runtime-cache cleanup.

Offline evidence in `tests/test_resume.py` covers a full interrupted CLI run
resuming to agreement in the same directory, prior approval invalidation,
revision/history/findings retention and continued finding IDs, exhausted attempts,
publication reconciliation, and faults at reservation, snapshot, intent,
publication, state, and commit writes. Refusal tests cover corruption, unsupported
schemas, missing artifacts, divergent audit state, external edits, terminal and
audit-only runs, clock/turn limits, both locks, security mismatch, manifest mismatch,
and disk-full reservation failure before any adapter call. Cleanup tests cover
protected previews, age/size selection, explicit deletion, terminal/unreadable
checkpoints, locked runs, and runtime-only removal.

Verification: the requested focused suite passed, full unittest discovery passed,
and `scripts/run_tests.py` passed with no skips against a locally built and
installed `duo-plan` package: **425 tests** for the initial implementation, **433
tests** after the review round that fixed the resume clock checks, the persisted
approval clearing, non-destructive resume startup refusals, and the
exhausted-attempts limit outcome. The suite grew from 374 tests; package version
remains 0.6.0.

Live evidence (2026-11-24, Linux, native pair: Codex `gpt-6-astra` high via
Codex CLI 0.171.0 with `--allow-untested-cli`, Claude `claude-opus-5-5` high):
run `run-37q0ew0e` (syncnote plan, seeded rough draft) committed three turns —
codex revise, claude revise, codex approve — and was then SIGKILLed mid-call
during claude's turn-4 attempt 1, with the attempt reservation and
`approved == ["codex"]` durable in the checkpoint. `duo --resume` recorded the
attempt as interrupted (consumed, not erased), opened segment 1 from sequence
12, and recorded `approvals_invalidated` for codex at approval epoch 1. Claude
approved turn 4 on attempt 2 against the unchanged document hash, so agreement
still required fresh approvals from both reviewers; codex revised (turn 5),
claude approved (turn 6), codex approved (turn 7): agreed, 7 turns, 8 attempts
total, with the checkpoint deadline byte-identical across the crash
(`2026-11-24T16:55:50.932837+00:00`, created_at + 7200 s; downtime counted).
The final `plan.md` hash matches the result's plan hash, and `verdict.json`
(schema 1.1) records completion `agreed`. A companion uninterrupted run
(`run-szr3h767`) agreed in 3 turns. Refusals, each before any model call: a
checkpoint copy with one tampered state field failed with "Checkpoint
integrity mismatch"; resuming either agreed run failed with "Cannot resume
terminal run (agreed)"; resuming a pre-§12 run (`run-esc_oi2o`, 0.6.0) failed
with "Audit-only run recorded before checkpoints; not resumable (no silent
import)".

Known limitations left by review: each resume attempt that fails its startup
budget check still opens a new segment and approval epoch (growth is bounded
by the checkpoint size cap); the future-timestamp check covers `updated_at`
and the latest segment's `started_at` only, which is sufficient because the
budget derives from `created_at`, `deadline`, and `max_seconds`; and after a
small backward clock correction, resume refuses as inconsistent until the
wall clock passes the recorded `updated_at` (conservative by design).

## Reviewer panels

Unreleased implementation of plan §13 for document modes. An explicit ordered
roster (`--reviewer ID:KIND:BACKEND:MODEL[:EFFORT]`, repeatable, at least two
members) replaces the default pair only when given; the legacy flags remain
the default two-member configuration with unchanged behavior, and combining
the two forms is refused. Scheduling is sequential round-robin over roster
order; agreement is unanimity of the fixed roster on the same unchanged
subject, a revision clears every member's approval including the reviser's,
and no majority, quorum, abstention, or substitution exists anywhere. Panel
runs emit verdict schema 1.2 (rule `unanimous-roster-approve-same-subject`,
full roster, `roster_sha256` and `policy_sha256` over canonical JSON) and
checkpoint schema 2 (the same hashes recomputed at validation); default-pair
runs keep emitting verdict 1.0/1.1 and schema-1 checkpoints with byte-identical
config serialization. `src/duo/diversity.py` classifies roster models by an
explicit prefix family mapping (`gpt-`/`claude-`, anything else unknown, never
guessed), reports pairwise relations, and warns on identical models and
same-family concentration; warnings appear on stderr, in `--json` summaries,
and in `reviewer-policy.json`. The reference gate accepts a panel verdict
only under the panel rule with a matching `--roster-sha256`; its default
invocation rejects panel verdicts. Build mode does not exist yet (§10), so
the build-plus-panel refusal is vacuous.

Loop and verification (2026-11-25): implemented by `gpt-5.3-codex` through
Copilot CLI — native Codex was usage-limited until 2026-11-29, an adaptation
of the usual loop that preserves the cross-model implement/review split —
and reviewed by native Claude `claude-opus-5-5` over four rounds (three
REVISE, then APPROVE; commits `67ef4bf`, `babd447`, `4a74d79`, `dda41ab`).
The suite grew from 433 to **468 tests**. Coordinator verification: clean
non-editable install, `scripts/run_tests.py` from an unrelated directory,
468 tests OK, in an environment without the `jsonschema` library (the suite
is hermetic; full schema validation is a verification step, as in §11/§12).
With the real `jsonschema` library (4.10.3), both valid 1.2 fixtures pass
the shipped `verdict-1.2.json` and the pair-rule panel fixture fails it; the
remaining invalid panel fixtures are validator-level and are rejected by
`validate_verdict` in the suite's fixture tests.

Live evidence (2026-11-25, Linux). Panel roster: `lead` (codex kind,
Copilot backend, `gpt-5.3-codex`, high), `opus` (claude, native,
`claude-opus-5-5`, high), `sonnet` (claude, Copilot, `claude-sonnet-5.5`,
high). Run `run-eqyn9pdi` (Trailhead statistics plan) agreed in 8 turns and
8 attempts: sonnet's and lead's approvals were cleared by opus's turn-5
revision and unanimity was re-earned on the final subject in turns 6–8;
verdict 1.2 records `roster_sha256` `b974aac6…` and `policy_sha256`
`627d70f2…`, the schema-2 checkpoint carries the same hashes, and 9 findings
closed with none open. The implementation's own diversity classification of
this panel is three instances in **two** families (gpt ×1, claude ×2), with
the same-family concentration warning shown on stderr and recorded in
`reviewer-policy.json`. A default-pair run on the same code (`run-680_ahbc`,
Copilot codex + native claude) agreed in 4 turns with verdict 1.0, a
schema-1 checkpoint, and no `reviewers` key in the manifest or checkpoint
config. Panel/checkpoint interplay: run `run-53pjli5o` was SIGKILLed after
turn 3 with `approved == ["sonnet"]` durable and turn 4 in flight; resume
opened segment 1, recorded `approvals_invalidated` for sonnet at approval
epoch 1, and the run agreed only after all three members approved afresh in
epoch 1 (8 turns, 11 attempts) — including an opus revision that cleared
lead's fresh epoch-1 approval first. A blocked member stops a panel run:
in `run-5sk1338a`, opus returned blocked on turn 2 and the run ended
(blocked, exit 3) with no assent counted. Gate checks on `run-eqyn9pdi`'s
verdict: default invocation exits 2 ("agreement rule does not match",
"malformed reviewer roster"); the panel rule with the correct
`--roster-sha256` exits 0; a wrong hash exits 2.

Known limitations: live panel evidence uses the Copilot backend for the
GPT-family member — native-Codex panel evidence is deferred until the usage
limit resets on 2026-11-29 (§13.6: unavailable configurations remain an
explicit evidence limitation). The shipped 1.2 schema pins a closed finding's
`disposition.by` to its reporter only for the `codex`/`claude` ids (JSON
Schema cannot express field equality in general); `validate_verdict` and
the gate enforce it for every roster id. The suite's schema-parity test is
structural and locates the agreed/closed-finding branches positionally; it
catches the regression class found in review, and full schema validation
remains a coordinator verification step.

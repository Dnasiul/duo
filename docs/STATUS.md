# Status and handoff

Where Duo's production readiness stands, and how to continue on a Linux machine.
Written 2026-10-13 at commit `c5886a7`; Linux live results and the 0.1.0, 0.2.0,
0.2.1, and 0.3.0 releases added the same day; 0.3.1, 0.4.0, and 0.4.1 on
2026-10-14; 0.4.2 on 2026-10-21; 0.4.3 on 2026-10-28. `plan.md` is the improvement plan after 0.4.1; the production plan
it replaced is `git show 918d2c5:plan.md`. This page records progress.

## Done

| Area | Where |
| --- | --- |
| Real end-to-end runs: native Codex + native Claude, and both reviewers via Copilot (macOS) | README compatibility table |
| CI: Ubuntu and macOS, Python 3.10–3.14, wheel and sdist installed with `constraints.txt`, strict runner (no skips), newest-dependency job, `pip-audit` | `.github/workflows/ci.yml` |
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
| Codex MCP check matches the configuration reviews load, so user-configured MCP servers no longer block native Codex | `src/duo/policy.py` (`verify_controls`) |
| Confinement of file reads and searches tested for every reviewer and backend | `scripts/smoke_inspection.py --confinement`, `--search-confinement`; "Linux live verification" below |
| Release 0.1.0 (2026-10-13): tag `v0.1.0` on `94186b7`, wheel, sdist, `constraints.txt`, `SHA256SUMS`; release notes hold the release report | https://github.com/Dnasiul/duo/releases/tag/v0.1.0 |
| Release 0.2.0 (2026-10-13): document modes (experimental); tag `v0.2.0` on `67538e2`, same files and release report | https://github.com/Dnasiul/duo/releases/tag/v0.2.0 |
| Release 0.2.1 (2026-10-13): `--output` fixes (`.git` case-insensitive, directory targets) and `duo.__version__`; tag `v0.2.1` on `c41ea59`, same files and release report; published with `gh`, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.2.1 |
| Release 0.3.0 (2026-10-13): reviewer CLIs get only the environment they need; `--pass-env`; tag `v0.3.0` on `dbbd30e`, same files and release report; published with `gh`, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.3.0 |
| Release 0.3.1 (2026-10-14): Copilot receives `GH_HOST` again; tag `v0.3.1` on `b531f68`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.3.1 |
| Release 0.4.0 (2026-10-14): `--allow-web` (experimental), the research pipeline example, the full Copilot environment record; tag `v0.4.0` on `a7571ad`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.0 |
| Release 0.4.1 (2026-10-14): cleanup no longer follows a `copilot-cache` symbolic link out of the audit directory, reviewers are stopped when monitoring fails, input files must be regular and are read with a 1 MiB bound; tag `v0.4.1` on `d63b188`, same files and release report; built and install-tested on Linux, published through the API, downloads verified against `SHA256SUMS` | https://github.com/Dnasiul/duo/releases/tag/v0.4.1 |
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
3. **Codex CLI 0.164.x**: validated on Linux on 2026-10-28 (below); live macOS
   verification remains pending.
4. **Copilot CLI 1.0.103+** (`--sync-session`): validated on Linux with 1.0.105
   on 2026-10-28 (below); live macOS verification remains pending.

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
calls and its usage counters stayed at zero, so a separate call with Duo's exact
command line asked it for the 0.3.1 wheel's checksum from the release's
`SHA256SUMS`: it returned the correct value, which requires a fetch, and also ran
a web search. Not yet tested: `--allow-web` on macOS, with mixed pairs, or in
other modes.

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
check now lists servers against an empty `CODEX_HOME`, which loads what a review
loads (reviews pass `--ignore-user-config`); the live runs below were made with
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

Tools seen in the attempts:

- Copilot: only `glob`, `view`, and `rg` (its name for `grep`); all built-in MCP
  servers reported disabled.
- Native Codex: read-only commands (`rg --files`, `ls`, `cat`, `python --version`).
  It looked for `AGENTS.md` and `CLAUDE.md` in parent directories, `/tmp`, and `/`,
  consistent with the known lack of read confinement.
- Native Claude: its JSON output does not list tool calls; no permission denials.

Confinement (`smoke_inspection.py --confinement`, same models): each reviewer was
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
or audit files. Codex via Copilot runs on the same CLI and settings as Claude via
Copilot.

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

## Set up the Linux machine

Assumes the micromamba environment `duo` (Python 3.11) exists.

```sh
git clone https://github.com/Dnasiul/duo.git && cd duo
micromamba run -n duo python -m pip install -c constraints.txt -e .

# Offline check first: no logins, no model calls. Expect "OK" with 137+ tests.
(cd tests && micromamba run -n duo python ../scripts/run_tests.py)
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
- Run tests with `scripts/run_tests.py` from `tests/`; it fails on skipped tests.
- Live model IDs used so far: native `gpt-6-astra`, `claude-opus-5-5`,
  `claude-sonnet-5-5`; Copilot `gpt-5.4`, `claude-sonnet-5.5` (dotted names).

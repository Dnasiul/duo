# Duo

Duo alternates Codex CLI and Claude Code reviews of `plan.md` using LangGraph.
It drafts an absent plan, applies validated revisions, and stops when both agents
explicitly approve the same unchanged version. Every revision clears approvals.
With `--mode`, the same read-only loop produces an analysis report, a folder
description, or a critical review instead (see
[Other documents](#other-documents-experimental)).

## Install

Requires **Python 3.10 to 3.14**, macOS or Linux, and authentication for the
selected reviewer backends. By default these are the `codex` and `claude` CLIs;
either reviewer can instead use GitHub Copilot CLI. See
[Supported deployment](#supported-deployment) for tested versions.

To install the tested dependency versions into a fresh virtual environment:

```sh
python3 -m venv ~/.venvs/duo
~/.venvs/duo/bin/python -m pip install -c constraints.txt .
~/.venvs/duo/bin/duo --help
```

`pyproject.toml` declares the supported dependency ranges; `constraints.txt`
pins the exact versions CI tests on every Python version. Without `-c
constraints.txt`, pip installs the newest versions within those ranges, which CI
also exercises but which are not part of a tested release.

Releases are published on GitHub only, as a wheel and source distribution with
SHA-256 checksums; install a downloaded wheel the same way, replacing `.` with
its path. Duo is released under the MIT License (see `LICENSE`).

For development from this directory, the micromamba environment named `duo`
works as well:

```sh
micromamba run -n duo python -m pip install -e .
micromamba run -n duo duo --help
```

## Supported deployment

Duo is a single-user command-line tool. The supported baseline is:

- one local user running Duo on their own machine;
- repositories the user trusts (reviewers read them, and repository text reaches
  the models);
- reviewer CLIs without enterprise-managed configuration (Duo refuses to run
  when managed policies or hooks are present);
- provider data-handling settings the user has reviewed, since task, plan, and
  repository content are sent to the selected model providers;
- a working environment holding only the data and credentials the review needs.

Reviewer agreement is a signal, not a verdict: for consequential work, a person
should still assess the final plan.

[docs/SECURITY.md](docs/SECURITY.md) details what each reviewer can read and
reach, which controls are tested or only configured, and what Duo does not
claim. In short: Codex's reads are not confined to the repository (Claude Code
and Copilot refused file reads and searches outside it in tests), reviewer CLIs
receive only the environment variables they need, and the credential check is
best-effort.

### Compatibility

| Area | Tested | Experimental or unsupported |
| --- | --- | --- |
| Operating system | macOS on Apple silicon (live runs and CI); Ubuntu 22.04 on x86-64 (live runs); Ubuntu (CI) | Other Linux distributions: CI only; Windows is unsupported |
| Python | 3.10, 3.11, 3.12, 3.13, 3.14 (CI on both systems) | Older versions are unsupported |
| Dependencies | `constraints.txt` | Newest versions within `pyproject.toml` ranges (CI checks them, untested for release) |
| Codex CLI | 0.162.x from 0.162.1 | Other versions, only with `--allow-untested-cli` |
| Claude Code | 2.1.x from 2.1.296 | Other versions, only with `--allow-untested-cli` |
| Copilot CLI | 1.0.x from 1.0.95 | Other versions, only with `--allow-untested-cli` |
| Backends | All four native/Copilot combinations (live runs on Linux and macOS) | — |
| Document modes | `report`, `description`, `review` (live runs on Linux, native and through Copilot; on macOS, native) | Copilot on macOS and mixed backend pairs: fake-CLI tests only |
| Models used in live runs | Native: `gpt-6-astra`, `claude-opus-5-5`, `claude-sonnet-5-5`; Copilot: `gpt-5.4`, `claude-sonnet-5.5` | Any other model your account offers; unknown names stop the run without retrying |
| Authentication | Codex and Claude Code: their own CLI login. Copilot: a token in `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`; a fine-grained token needs the "Copilot Requests" permission | Copilot's saved interactive login is not used |

The weekly drift check keeps the CLI rows honest (see
[Responding to CLI drift](#responding-to-cli-drift)); a version enters the
tested ranges only with recorded evidence. For operations such as renewing
authentication, recovering interrupted runs, or rolling back, see
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## Run

Replace the model placeholders with identifiers available to your accounts:

```sh
duo \
  --repo /absolute/path/to/project \
  --task "Plan email and password authentication for this project" \
  --criteria "Include migration, tests, and rollback steps" \
  --codex-model YOUR_CODEX_MODEL \
  --claude-model YOUR_CLAUDE_MODEL
```

The existing `plan.md`, if present, becomes the starting plan. The original task
and criteria remain fixed during the run. You can use UTF-8 files for longer
inputs; they must not be `plan.md` itself, since the plan would then be reviewed
against itself:

```sh
duo \
  --repo /absolute/path/to/project \
  --task-file task.txt \
  --criteria-file criteria.txt \
  --codex-model YOUR_CODEX_MODEL \
  --claude-model YOUR_CLAUDE_MODEL \
  --start claude --max-turns 12 --json
```

Input file paths are relative to the directory where you invoke Duo, while agents
run inside `--repo`. The target must be a Git working tree unless you pass
`--skip-git-repo-check`. This Duo source directory itself need not be a Git repo.
Progress goes to stderr; `--json` prints only the final summary to stdout.
Task and criteria files retain their exact decoded UTF-8 contents, including
CRLF and CR line endings, literal HTML, and trailing whitespace.

Add `--show-conversation` to see each review as soon as the turn completes:

```sh
duo \
  --repo /absolute/path/to/project \
  --task "Review and refine the existing plan" \
  --codex-model gpt-6-astra --codex-effort high \
  --claude-model claude-opus-5-5 --claude-effort high \
  --show-conversation
```

The display includes the turn number, reviewer, decision, and explanation. A
first draft is shown in full; later revisions show a unified plan diff.
Approvals and blocked decisions show their explanations without repeating an
earlier diff. Output appears after a response is validated and saved, rather
than streaming tokens during generation. Failed attempts are not displayed as
completed reviews; their diagnostics remain in the audit directory.

Conversation output goes to stderr, so it works with `--json`. To save the
conversation and progress output, append `2>conversation.log` to the command.
This option does not change the prompts, approval rules, or audit files.

### Other documents (experimental)

`--mode` makes the reviewers produce another kind of document with the same
read-only review loop:

| Mode | Default file | Reviewers check |
| --- | --- | --- |
| `plan` (default) | `plan.md` | Requirements, feasibility, dependencies, order, testing |
| `report` | `report.md` | Claims supported by repository files, numbers quoted with sources, methods, limitations |
| `description` | `description.md` | Accuracy, completeness, organization, what could not be inspected |
| `review` | `review.md` | Findings correct, specific, located, ranked by severity |

The document under review is always one file: the existing one, if present, is
the starting point, and both reviewers must approve the same version. Examples:

**Plan** (the default) for a change in a project:

```sh
duo --repo ~/code/md-driver \
  --task "Plan adding checkpoint/restart support to the MD driver" \
  --criteria "Restarts must reproduce the trajectory bit for bit; include tests" \
  --codex-model gpt-6-astra --claude-model claude-opus-5-5
```

**Report** analyzing results in a folder; the reviewers recheck every number
against the files:

```sh
duo --repo ~/sim/SiH_MD --skip-git-repo-check --mode report \
  --task "Analyze the NVT runs in T300/ and T500/: completion, equilibration, and a comparison of energies and temperatures" \
  --criteria "Every number cites its file and rows; show how each statistic was computed; state limitations" \
  --codex-model gpt-6-astra --claude-model claude-opus-5-5
```

**Description** of a folder, saved elsewhere than `description.md`:

```sh
mkdir -p ~/sim/SiH_MD/notes
duo --repo ~/sim/SiH_MD --skip-git-repo-check --mode description \
  --output notes/folder.md \
  --task "Describe the contents of this folder and the state of each run" \
  --criteria "Name the files each statement is based on; say what could not be read" \
  --codex-model gpt-6-astra --claude-model claude-opus-5-5
```

**Review** of a draft against the data, with a custom checklist:

```sh
mkdir -p ~/duo-tasks
cat > ~/duo-tasks/review-checks.txt <<'TXT'
Check every factual and numerical claim in the draft against the input files,
logs, and data. Quote each claim, give the evidence with file and line, and rate
it major or minor. Note claims that are correct. Do not rewrite the draft.
TXT
duo --repo ~/sim/SiH_MD --skip-git-repo-check --mode review \
  --instructions-file ~/duo-tasks/review-checks.txt \
  --task "Critically review the draft results section draft-results.md against T300/ and T500/" \
  --codex-model gpt-6-astra --claude-model claude-opus-5-5
```

In each case the material stays untouched; Duo writes only the mode's document.
Claude can read and search text files only, so it cannot open binary trajectories;
Codex can also run read-only commands on them. Whatever either reads is sent to
the model providers.

Reviewers have no web access unless you pass `--allow-web` (experimental), for
example to check references against their DOI records. It lets text the
reviewers read steer them into sending data out through queries and URLs; read
"Web access" in [docs/SECURITY.md](docs/SECURITY.md) before using it.

For how the modes fit together across a research project, from observations and
hypotheses to a paper with confirmed references, see
[examples/scientific_research_pipeline.md](examples/scientific_research_pipeline.md).

`--output` takes a path relative to `--repo` whose directory already exists; it
must stay inside the repository and outside `.git`. Reviewers are told that this
file is Duo's output, not material to describe. `--instructions-file` replaces
the mode's checklist with your own; the review protocol (read-only inspection,
complete revisions, repository text treated as material, not instructions) stays
fixed. The default `plan` mode sends exactly the prompt it always has. The mode,
instructions, and document path are recorded in each run's `manifest.json`.

### Use models through GitHub Copilot

Select the transport independently for each reviewer with `--codex-backend` and
`--claude-backend`. Both default to `native`. Selecting `copilot` runs that
reviewer's model through GitHub Copilot CLI. There are still two alternating
reviewers, with independent approvals. Only the selected CLIs are required.

Copilot review invocations use a fresh configuration and cache so existing
plugins, hooks, saved permissions, and provider overrides are not inherited.
Supply authentication using `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`
(in that order), with a token authorized for Copilot. Saved interactive login is
not copied into the isolated configuration. Duo never puts token values in its
command arguments or audit metadata. See GitHub's
[supported authentication methods](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#using-copilot-login).

For two models accessed through Copilot:

```sh
duo \
  --repo /absolute/path/to/project \
  --task "Plan and validate a molecular dynamics simulation" \
  --codex-backend copilot --codex-model gpt-5.3-codex \
  --claude-backend copilot --claude-model claude-opus-5.5 \
  --codex-effort high --claude-effort high \
  --show-conversation
```

For native Codex paired with Claude through Copilot:

```sh
duo \
  --repo /absolute/path/to/project \
  --task-file task.txt --criteria-file criteria.txt \
  --codex-model gpt-6-astra \
  --claude-backend copilot --claude-model claude-opus-5.5 \
  --show-conversation
```

The reverse combination works too: `--codex-backend copilot` with the default
native Claude backend. Use `--copilot-bin` to select a specific Copilot executable.
Model IDs and availability come from the chosen backend and your account; use
Copilot's interactive `/model` command to inspect its catalog. Its IDs can differ
from native CLI IDs (for example, `claude-opus-5.5` versus `claude-opus-5-5`);
`gpt-5.4` and `claude-sonnet-5.5` have been verified through Copilot. When
Copilot reports a model as unavailable, Duo stops after the first attempt and
names the model instead of retrying.
GitHub documents explicit model selection in the
[programmatic CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference#choosing-a-model).

Copilot accepts effort requests `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, and `max`, subject to model support. `ultra` is rejected for Copilot.
The selected model and backend appear in accepted review history, and
`--show-conversation` labels Copilot reviews explicitly.

Copilot runs with `--output-format json`; Duo takes the final answer from that
event stream, ignoring progress narration and tool output, and validates it. A
reply wrapped in a single Markdown code fence is accepted. Copilot does not expose the
native CLIs' JSON-schema option; malformed output uses the same bounded retry
path. Duo's turn, attempt, timeout, and run-time limits apply.
Claude's internal-turn and dollar-budget options apply only to native Claude;
passing either is rejected when its backend is Copilot. These limits do
not establish a Copilot credit cap.

| Exit code | Outcome |
| --- | --- |
| 0 | Both agents agreed |
| 1 | Setup, agent, storage, or execution failure |
| 2 | Invalid command-line arguments |
| 3 | An agent needs a user decision (`blocked`) |
| 4 | Review turn or run time limit reached |
| 130 | Interrupted (Ctrl-C) |
| 143 | Terminated by SIGTERM (129 for SIGHUP) |

A reviewer failure stays a failure (exit 1) even when the deadline then leaves
no time to retry; exit 4 means the budget itself ran out, including a CLI call
cut short by the deadline. Duo waits for a CLI's requested retry delay
(`Retry-After`, "try again in 20s", "500ms", "2 minutes") up to 300 seconds; a
longer request ends the run with an error instead of stalling it.

The summary reports the stopping condition and the latest accepted review
separately. JSON includes `message` and `latest_review` (reviewer, decision,
reason, plan hash, and timestamp), with `latest_review: null` before any accepted
turn. A turn-limit message therefore preserves the reviewer's explanation.

For a blocked run, add your answer to the criteria and run the command again.
The saved plan is reviewed with fresh approvals. Error and limit outcomes retain
the latest successfully published plan; they never imply agreement.

## Limits and configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `--max-turns` | 12 | Accepted review responses, including draft and blocked responses |
| `--max-attempts` | 3 | CLI invocations per review turn, including the first attempt |
| `--timeout` | 600 | Maximum seconds for one agent invocation |
| `--max-seconds` | 7200 | Run deadline, including retries and backoff |
| `--claude-max-turns` | 20 | Claude's internal agent turns per invocation |
| `--claude-max-budget-usd` | Unset | Claude's per-invocation API budget |
| `--pass-env` | None | Extra environment variable for reviewer CLIs (repeatable) |
| `--allow-web` | Off | Experimental: reviewers may search and fetch web pages (Copilot: fetch only); see `docs/SECURITY.md` |
| `--max-output-mb` | 32 | MiB per captured CLI output stream (stdout, stderr) |
| `--max-run-mb` | 256 | MiB per run audit directory, including CLI logs and caches |
| `--start` | `codex` | First reviewer |
| `--codex-effort` | Unset | Requested Codex reasoning effort |
| `--claude-effort` | Unset | Requested Claude reasoning effort |
| `--codex-backend` | `native` | Codex CLI or GitHub Copilot (`copilot`) |
| `--claude-backend` | `native` | Claude Code or GitHub Copilot (`copilot`) |
| `--copilot-bin` | `copilot` | Copilot executable for either reviewer |

Set effort independently for each reviewer, for example:

```sh
duo \
  --repo /absolute/path/to/project \
  --task "Plan email and password authentication" \
  --codex-model gpt-6-astra --codex-effort high \
  --claude-model opus --claude-effort high
```

Duo accepts `low`, `medium`, `high`, `xhigh`, and `max` for both reviewers,
and `ultra` for Codex. Available levels depend on the selected model and CLI;
organization settings may also limit the effective level. Omit either option to
use that CLI's defaults under Duo's restricted configuration. Requested
values are recorded in `manifest.json` and each attempt's command arguments.
Codex receives a `model_reasoning_effort` configuration override and Claude
receives `--effort`. See the [Codex configuration reference](https://developers.openai.com/codex/config-reference)
and [Claude effort documentation](https://code.claude.com/docs/en/model-config#effort-level).

At the default review and retry limits there are at most 36 review CLI invocations;
each can make multiple model requests. These limits do not establish a combined
dollar cap. Version and configuration probes are separate setup commands and
make no model requests. The run deadline limits calls and retry waits; setup, local file I/O,
and final reporting may take additional time. Timeouts, Ctrl-C, SIGTERM, and
SIGHUP terminate the launched CLI process group before returning, so a scheduler
stopping Duo leaves no reviewer running.

Use `--codex-bin` or `--claude-bin` to select an executable (prefer an absolute
path). Duo records requested models and CLI versions in each audit directory.
It starts fresh agent sessions and passes review context explicitly.

### Reviewer inspection policy

Duo applies and audits inspection restrictions before starting reviews:

- **Codex:** a fresh process with the read-only sandbox and approvals set to
  `never`; user configuration and executable rules are ignored during review.
  Explicit overrides disable MCP servers, apps, plugins, hooks, notification
  commands, browser use, web search, multi-agent tools, and shell snapshots.
  Codex can use sandboxed commands to inspect repository files; its read-only
  sandbox does not limit reads to the repository. Codex runs with `--json`, so
  its transcript stays out of stderr and failures are classified from
  structured error events, never from model text. Preflight checks
  the effective feature flags and empty MCP list, including inherited and managed
  configuration; unsupported or conflicting controls stop the run. MCP servers
  in your own Codex configuration are allowed: reviews ignore it, and preflight
  lists servers the same way. Servers from managed configuration stop the run.
- **Claude:** safe and restricted modes, an empty list of user/project/local
  setting sources, explicit settings disabling hooks and connector/plugin sync,
  and only `Read`, `Glob`, and `Grep` tools. MCP, Chrome integration, and skills
  are disabled. Safe mode also turns off automatic `CLAUDE.md` loading; reviewers
  can still read repository instruction files as context.
- **Copilot:** a fresh home/cache per invocation, hooks and connectors disabled,
  no inherited plugins or trusted folders, and only `view`, `glob`, and `grep`
  exposed, confined to the repository. Shell, write, and URL permissions are
  denied. Built-in MCP servers and discovered repository MCP servers are
  explicitly disabled. Project extension opt-ins, custom provider environment
  overrides, auto-update, remote control, and remote export are disabled.
  Conflicting repository settings and local managed policy cause a setup error;
  repository settings that require inspection must be plain JSON. Configurations
  and organization policy must remain stable during the run.

See [docs/SECURITY.md](docs/SECURITY.md) for each reviewer's file, environment,
network, and credential access and the evidence behind it.

Because a reviewer may read files outside the repository, Duo rejects any
response whose plan or reason contains a credential it introduced (private keys
and common API-token formats such as AWS, GitHub, Anthropic, OpenAI, Slack,
Google, and Stripe keys, and JSON Web Tokens). The rejection is retried without
echoing the value; matches already present in the current plan are allowed.
The rejected raw response remains in the private audit directory.

Native CLI authentication is retained; Copilot uses an environment token.
Reviewer CLIs, and the setup probes that check them, receive only the environment
they need to run and log in (see `ENVIRONMENT` in `src/duo/policy.py`); cloud
credentials, other tools' tokens, the SSH agent, and `NODE_OPTIONS` are dropped.
Pass anything else a CLI needs, such as cloud credentials for a Bedrock-based
Claude login, with `--pass-env NAME`. `reviewer-policy.json` lists the variable
names each CLI received, never their values.
Duo does not copy credentials, edit
global settings, or pass permission-bypass flags. Custom provider configuration
and authentication helpers in ignored settings files are not inherited; this
release expects authentication usable without executing such helpers.

**Managed Claude installations are unsupported by this inspection policy.**
Claude's managed hooks cannot be disabled by command-line settings. Duo rejects
detected system/MDM policy files and populated remote-policy caches, then uses
`claude doctor` to check for remote policy. Loaded, stale, failed, and unknown
policy results stop setup. Even benign managed settings may cause rejection;
Duo does not attempt to reinterpret an administrator's policy. Keep CLI settings
and organization policy stable throughout a run, as well as repository contents.

`reviewer-policy.json` records the policy, settings sources, CLI versions, and
verified controls. Requested model and effort values remain in `manifest.json`;
Duo does not claim to know effective values that a CLI does not expose.
See [Codex configuration](https://developers.openai.com/codex/config-reference),
[Claude's CLI modes](https://code.claude.com/docs/en/cli-reference), and
[managed hook restrictions](https://code.claude.com/docs/en/hooks#disable-or-remove-hooks).

## Files and concurrent work

Duo atomically replaces `<repo>/plan.md` (or, with `--mode`, the mode's document)
after validating a full response. It preserves existing permission bits; a new
document gets the mode your umask gives new files (usually `0644`).
Symlinked documents are rejected. Literal Markdown, including significant whitespace,
is preserved apart from line-ending normalization.

Audit files are stored under `$XDG_STATE_HOME/duo`, or `~/.local/state/duo` when
that variable is unset. Override this with `--state-dir /outside/the/repo`.
Each run has a private directory containing:

- `manifest.json` and `versions.json`: inputs, options, and CLI versions.
- `reviewer-policy.json`: inspection policy and verified configuration controls.
- `claude-settings.json`: generated settings used for Claude review invocations.
- `events.jsonl`: attempts, errors, and accepted reviews with plan hashes.
- `plan-000.md` and later snapshots: the initial plan and validated proposals.
- `attempt-*/`: schema, command arguments, response, and raw CLI diagnostics.
  Copilot attempts also contain an isolated `copilot-home/`, cache, and CLI logs.
- `state.json`: the last audited review state.
- `result.json` or `error.json`: final outcome.

A proposal snapshot may exist even when publishing it to `plan.md` failed.
Audit files contain task and repository-derived content. They are local records,
not resumable LangGraph checkpoints, and are never deleted automatically.

### Restarting

A rerun starts a new review of whatever `plan.md` (or the mode's document) holds,
which is always the last accepted version: Duo saves a revision only after validating it. Approvals are not
carried over, so both reviewers must approve again. Audit directories are
records, not checkpoints; a run cannot be resumed from one.

### Resource limits

While a reviewer CLI runs, Duo checks its output streams against
`--max-output-mb` and the whole run directory, including logs and caches the CLI
creates, against `--max-run-mb`. Checks run about twice a second, so a CLI writing
very fast can overshoot by what it writes in that interval; this is not a hard
disk quota. On a violation Duo stops the CLI's process group, keeps the last
accepted plan, and ends the run with an error (exit 1) without retrying. Tasks,
criteria, custom instructions, the document under review, and each review's
document and reason are limited to 1 MiB. Input files must be regular files; a
named pipe or device is rejected rather than read.

Copilot unpacks its own runtime (about 165 MiB) into each attempt's fresh cache.
Duo deletes it after preflight and after every attempt and leaves it out of the
run-size count; it briefly needs that space while an attempt runs.

### Cleaning up audit runs

`duo-clean` deletes old runs. It only reports what it would do unless `--delete`
is given:

```sh
duo-clean --older-than 30            # runs untouched for more than 30 days
duo-clean --max-total-mb 500         # oldest runs until the rest fit in 500 MiB
duo-clean --copilot-runtime          # leftover Copilot runtimes; audit files kept
duo-clean --older-than 30 --delete
```

It considers only directories directly under `<state-dir>/runs`, skips runs a
running Duo still holds, never follows symbolic links, and never touches project
files such as `plan.md`. An interrupted cleanup leaves the partly deleted run in
place; run the command again to finish. Runs from Duo versions before the
runtime cleanup may each hold several hundred MiB of Copilot runtime copies;
`--copilot-runtime` reclaims that space without losing audit records.

An OS lock on the repository directory excludes other Duo runs on the same
repository, whatever their `--state-dir` or the path used to name it. It creates
no file and is released when the process exits, even after a crash.
Duo also checks the document (`plan.md` by default) before and after agent calls
and before saving it. An
external edit stops the run rather than knowingly overwriting it. Checks cannot
eliminate races with an uncooperative writer. Keep repository files stable during
a review: this version does not fingerprint all code or detect every code edit.

## Responding to CLI drift

The **CLI drift** workflow (`.github/workflows/drift.yml`) runs every Monday and
on demand. On Ubuntu and macOS it installs the newest Codex, Claude Code, and
Copilot releases from npm and runs `scripts/check_drift.py`. That script applies
Duo's own credential-free setup checks and compares help flags and Codex
feature names with the recorded fixtures. Each CLI is reported as:

- `compatible`: a tested version whose controls all verify. Nothing to do.
- `untested-version`: the controls verify, but the version is outside the tested
  range. Duo refuses it unless `--allow-untested-cli` is passed.
- `probe-failure`: a control is missing or did not take effect, or the CLI did
  not start. The job fails.

When action is needed, the workflow opens a single "CLI drift: action needed"
issue or comments on it, and it closes the issue once every CLI is compatible
again. The repository owner triages it; the issue's arrival is the weekly
review prompt.

- For `untested-version`: record its probes with `scripts/record_fixtures.py`,
  run the inspection smoke test and one bounded live Duo run against it, and
  review the reported flag and feature changes. If everything passes, add the
  fixtures, extend `TESTED_VERSIONS` in `src/duo/policy.py` (for Codex, one
  minor version at a time), and update the versions near the top of this file
  in a normal release.
- For `probe-failure`: keep the version rejected and note it as a known
  incompatibility. If a removed or changed control affects reviewer isolation,
  review the security boundary before supporting any version of that CLI again.
- Changes to Copilot model IDs or effort values show up as live-run failures,
  which Duo reports without retrying; update the README examples when they occur.

To run the check locally against your installed CLIs:

```sh
micromamba run -n duo python scripts/check_drift.py
```

## Tests

The tests use standard-library `unittest` and fake CLI executables, so they make
no model calls and require no credentials:

```sh
PYTHONPATH=src micromamba run -n duo python -m unittest discover -s tests -v
```

Install the declared dependencies before validating the deliverable. The
acceptance suite requires real LangGraph integration tests to execute.
The suite covers approval invalidation, retries, deadlines, malformed responses,
verbatim inputs, reviewer policy, CLI response formats, process cleanup, file
conflicts, write/flush/fsync/chmod/replace failures, exits, and document modes
(prompts, output paths, and rejected inputs).

Recorded, sanitized output from the tested CLI versions lives in
`tests/fixtures`; the suite replays it through the same parsers and control
checks, so the fake CLIs cannot drift from real formats unnoticed. See
`tests/fixtures/README.md` for refreshing it with `scripts/record_fixtures.py`.

CI (`.github/workflows/ci.yml`) runs on Ubuntu and macOS with Python 3.10 to
3.14. It builds the wheel and the source distribution, installs each into a
clean environment with `constraints.txt`, and runs the suite against the
installed package with `scripts/run_tests.py`, which fails on any skipped test
(for example when LangGraph is missing). A separate job tests the newest
dependency versions allowed by `pyproject.toml`, and another runs `pip-audit`
on the pinned versions. CI uses no reviewer credentials.

To update the pinned versions, regenerate them with one interpreter per
supported Python version, then let CI test them and review the audit:

```sh
python scripts/update_constraints.py python3.10 python3.11 python3.12 python3.13 python3.14
```

The optional real-CLI smoke test creates a disposable Git repository containing
harmless hook and MCP sentinels. It asks both reviewers to read a verification
phrase and checks that neither sentinel executed and the plan remained intact:

```sh
PYTHONPATH=src micromamba run -n duo python scripts/smoke_inspection.py \
  --codex-model YOUR_CODEX_MODEL --claude-model YOUR_CLAUDE_MODEL
```

This separate test requires authentication and makes two model calls, which may
incur charges. It does not alter your global settings. Fake-CLI argv tests alone
do not establish real-CLI enforcement; this smoke test exercises startup and
repository inspection, but is not an exhaustive security assessment.
The script also accepts `--codex-backend copilot`, `--claude-backend copilot`,
and `--copilot-bin`; Copilot smoke runs require the same environment token as Duo.
`--confinement` adds two model calls that ask each reviewer to read three files
outside the repository: one in the temporary directory, one hidden in your home
directory (removed afterwards), and one through a symbolic link inside the
repository. It reports per file whether the reviewer read it, and shows the
CLI's own denials where it records them. `--search-confinement` adds two model
calls that ask each reviewer to list and search three directories outside the
repository the same way, reporting leaked file names and contents separately.

## Implementation

`src/duo/workflow.py` builds the LangGraph cycle. `adapters.py` runs the CLIs,
`policy.py` defines and verifies their inspection configuration,
`models.py` validates the shared response contract, `prompts.py` builds review
context and each mode's checklist, and `storage.py` manages document publication
and audit files.

Persistent graph checkpoint recovery,
repository snapshot tracking, cycle detection, and structured issue tracking are
future improvements. Agreement means both reviewers accept the document; it does
not prove that a plan, report, description, or review is correct.

CLI behavior follows the [Codex non-interactive documentation](https://developers.openai.com/codex/noninteractive)
and [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).

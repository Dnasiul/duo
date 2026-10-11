# Duo

Duo alternates Codex CLI and Claude Code reviews of `plan.md` using LangGraph.
It drafts an absent plan, applies validated revisions, and stops when both agents
explicitly approve the same unchanged version. Every revision clears approvals.

## Install

Requires **Python 3.10 or newer**, macOS or Linux, and authentication for the
selected reviewer backends. By default these are the `codex` and `claude` CLIs;
either reviewer can instead use GitHub Copilot CLI. Inspection controls have
been checked with Codex **0.162.1**, Claude Code **2.1.296**, and Copilot **1.0.95**.
Duo accepts those versions and later patches within the
same minor release, subject to configuration probes. Older or different minor
releases stop with a setup error until their controls are verified. From this
directory, use the existing Python 3.11 micromamba
environment named `duo`:

```sh
micromamba activate duo
python -m pip install -e .
duo --help
```

Without activating the environment, use
`micromamba run -n duo python -m pip install -e .` to install and
`micromamba run -n duo duo --help` to invoke the command. Installation downloads
LangGraph and its dependencies from the configured package index.

A standard virtual environment with Python 3.10 or newer works as well.

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
inputs:

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
from native CLI IDs (for example, `claude-opus-5.5` versus `claude-opus-5-5`).
GitHub documents explicit model selection in the
[programmatic CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference#choosing-a-model).

Copilot accepts effort requests `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, and `max`, subject to model support. `ultra` is rejected for Copilot.
The selected model and backend appear in accepted review history, and
`--show-conversation` labels Copilot reviews explicitly.

Copilot responses are requested as plain JSON and validated by Duo. Copilot
does not expose the native CLIs' JSON-schema option; malformed output uses the
same bounded retry path. Duo's turn, attempt, timeout, and run-time limits apply.
Claude's internal-turn and dollar-budget options apply only to native Claude;
non-default values are rejected when its backend is Copilot. These limits do
not establish a Copilot credit cap.

| Exit code | Outcome |
| --- | --- |
| 0 | Both agents agreed |
| 1 | Setup, agent, storage, or execution failure |
| 2 | Invalid command-line arguments |
| 3 | An agent needs a user decision (`blocked`) |
| 4 | Review turn or run time limit reached |
| 130 | Interrupted |

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
and final reporting may take additional time. Timeouts and Ctrl-C terminate the
launched CLI process group before returning.

Use `--codex-bin` or `--claude-bin` to select an executable (prefer an absolute
path). Duo records requested models and CLI versions in each audit directory.
It starts fresh agent sessions and passes review context explicitly.

### Reviewer inspection policy

Duo applies and audits inspection restrictions before starting reviews:

- **Codex:** a fresh process with the read-only sandbox and approvals set to
  `never`; user configuration and executable rules are ignored during review.
  Explicit overrides disable MCP servers, apps, plugins, hooks, notification
  commands, browser use, web search, multi-agent tools, and shell snapshots.
  Codex can use sandboxed commands to inspect repository files. Preflight checks
  the effective feature flags and empty MCP list, including inherited and managed
  configuration; unsupported or conflicting controls stop the run.
- **Claude:** safe and restricted modes, an empty list of user/project/local
  setting sources, explicit settings disabling hooks and connector/plugin sync,
  and only `Read`, `Glob`, and `Grep` tools. MCP, Chrome integration, and skills
  are disabled. Safe mode also turns off automatic `CLAUDE.md` loading; reviewers
  can still read repository instruction files as context.
- **Copilot:** a fresh home/cache per invocation, hooks and connectors disabled,
  no inherited plugins or trusted folders, and only `view`, `glob`, and `grep`
  exposed. Only read permission is granted; shell, writes, and URL tools are
  denied. Built-in MCP servers and discovered repository MCP servers are
  explicitly disabled. Project extension opt-ins, custom provider environment
  overrides, auto-update, remote control, and remote export are disabled.
  Conflicting repository settings and local managed policy cause a setup error;
  repository settings that require inspection must be plain JSON. Configurations
  and organization policy must remain stable during the run.

Native CLI authentication is retained; Copilot uses an environment token.
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

Duo atomically replaces `<repo>/plan.md` after validating a full response. It
preserves existing permission bits; a new plan is created with mode `0600`.
Symlinked plans are rejected. Literal Markdown, including significant whitespace,
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
not resumable LangGraph checkpoints; no automatic cleanup is performed.

A per-repository OS lock excludes other Duo runs using the same state directory.
Duo also checks `plan.md` before and after agent calls and before saving it. An
external edit stops the run rather than knowingly overwriting it. Checks cannot
eliminate races with an uncooperative writer. Keep repository files stable during
a review: this version does not fingerprint all code or detect every code edit.

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
conflicts, write/flush/fsync/chmod/replace failures, and exits.

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

## Implementation

`src/duo/workflow.py` builds the LangGraph cycle. `adapters.py` runs the CLIs,
`policy.py` defines and verifies their inspection configuration,
`models.py` validates the shared response contract, `prompts.py` builds review
context, and `storage.py` manages plan publication and audit files.

The design is in [duo_plan.md](duo_plan.md). Persistent graph checkpoint recovery,
repository snapshot tracking, cycle detection, and structured issue tracking are
future improvements. Agreement means both reviewers accept the plan; it does not
prove that the proposed implementation is correct.

CLI behavior follows the [Codex non-interactive documentation](https://developers.openai.com/codex/noninteractive)
and [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).

# Duo security model

What each reviewer can reach, what Duo enforces, and what it does not claim.
Every statement is tagged with its evidence:

- **Tested**: shown with synthetic data by the test suite or the real-CLI smoke
  test (`scripts/smoke_inspection.py`).
- **Verified each run**: preflight probes confirm the control before any review;
  a missing or ineffective control stops the run.
- **Configured**: Duo passes the setting, but no test shows the CLI enforcing it.
- **Not verified**: unknown; assume the less safe answer.

## Scope

This release supports the baseline in the README: one user, **trusted
repositories**, unmanaged CLI configuration. It provides **no isolation profile**.
Reviewing sensitive or untrusted repositories is unsupported: a reviewer may read
files outside the repository, and repository text reaches the model providers.

Repository contents and model output are untrusted input. Repository text can
attempt prompt injection. Duo limits what a manipulated reviewer can do (read-only
tools, no network tools unless you pass `--allow-web`) and validates every response, but it cannot make a model
ignore what it reads. Duo never executes model output; it only writes a validated
document as text: `plan.md`, or with `--mode` the mode's file or `--output`, which
must stay inside the repository and outside `.git`.

## What leaves your machine

The task, criteria, any `--instructions-file` text, the current document, recent
review reasons, and every repository file a reviewer reads are sent to the
provider behind each backend: OpenAI for Codex,
Anthropic for Claude Code, and GitHub, plus the model's vendor, for Copilot.
Review each provider's data-handling settings before use.

## Reviewer access

### Codex (native)

| Access | Behavior | Evidence |
| --- | --- | --- |
| Reading files | Sandboxed shell commands can read any file your user can read. Reads are **not** confined to the repository. | Tested: a recorded real run (`tests/fixtures/codex-0.162.1/events-approve.jsonl`) shows Codex checking for files in every directory above the repository; live runs on Linux also read `/tmp` and `/`. Tested (`--confinement`, Linux): read a file in the temporary directory, one in the home directory, and one through a symbolic link inside the repository |
| Writing files | Read-only sandbox, approvals `never` | Verified each run (overrides applied); Tested: plan unchanged in the smoke test. Not verified: a deliberate write attempt |
| Tools | Read-only sandboxed commands. MCP servers, apps, plugins, hooks, notify commands, browser use, web search, multi-agent tools, and shell snapshots disabled | Verified each run (effective feature list, empty MCP list); Tested: repository hooks and MCP launchers did not run in the smoke test. The `mcp_servers={}` override does not remove configured servers (Codex 0.162.1), so the MCP list is checked against an empty `CODEX_HOME`, the configuration reviews load. Tested: servers in user configuration, and in projects it trusts, are not loaded by a review; the smoke test passes with one configured. Because that check runs without credentials, MCP servers from policy fetched with your login are not verified |
| Environment | Only what it needs to run and log in: `HOME`, `PATH`, user and locale settings, temporary and XDG directories, the session bus, proxy and certificate settings, and `CODEX_HOME`, `CODEX_API_KEY`, `OPENAI_API_KEY`, plus any `--pass-env` names. Cloud credentials, other tools' tokens, the SSH agent, and `NODE_OPTIONS` are dropped. Codex runs its commands in a login shell, which can set variables again from your shell profile | Tested: review calls, setup probes, and version checks receive only these, and no dropped value reaches the audit (fake CLIs); the real CLIs still found their logins and passed preflight with them (macOS and Linux); what the login shell adds is not verified |
| Network | The CLI talks to OpenAI. Web search is disabled unless `--allow-web` | Configured; network access from sandboxed commands not verified |
| Authentication | Codex's own stored login. Because reads are unrestricted, its commands can read credential files in your home directory | Not prevented |

### Claude Code (native)

| Access | Behavior | Evidence |
| --- | --- | --- |
| Reading files | `Read`, `Glob`, and `Grep` only. Restricted mode refuses reads outside the working directory, including through a symbolic link inside the repository | Tested (`--confinement`, Linux): `Read` was denied for a file in the temporary directory, one in the home directory, and one through a repository symlink, each listed in Claude Code's `permission_denials`. Tested (`--search-confinement`, Linux): `Glob` and `Grep` were denied for a directory in the temporary directory and one in the home directory; searching the repository did not follow a symbolic link to an outside directory |
| Writing files | No write or shell tools | Verified each run (required flags present); Tested: plan unchanged in the smoke test |
| Tools | Only `Read`, `Glob`, `Grep`. Safe and restricted modes, no setting sources, hooks and connector/plugin sync disabled, MCP, Chrome, and slash commands disabled | Verified each run (flags, `claude doctor` remote-policy state, managed-settings files); Tested: hooks and MCP launchers did not run |
| Environment | Only what it needs to run and log in: `HOME`, `PATH`, user and locale settings, temporary and XDG directories, the session bus, proxy and certificate settings, and `CLAUDE_CONFIG_DIR`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, plus any `--pass-env` names. Without a shell tool, the model has no direct way to read it | Tested: review calls, setup probes, and version checks receive only these, and no dropped value reaches the audit (fake CLIs); the real CLIs still found their logins and passed preflight with them (macOS and Linux) |
| Network | The CLI talks to Anthropic. No web tools are available unless `--allow-web` | Configured |
| Authentication | Claude Code's own login | As for reading files |

### Copilot (either reviewer)

| Access | Behavior | Evidence |
| --- | --- | --- |
| Reading files | `view`, `glob`, `grep`, confined to the repository: fresh home with no trusted folders, temporary directory access disallowed. An outside path needs approval that a non-interactive run cannot give, so it is denied; symbolic links are judged by their target | Tested (`--confinement`, Linux, both models): `view` was denied for a file in the temporary directory, one in the home directory, and one through a repository symlink ("Permission denied because no interactive user response was available"). Tested (`--search-confinement`, Linux, both models): `glob` and `grep` (shown to the Codex model as `rg`) were denied the same way for a directory in the temporary directory and one in the home directory; searching the repository did not follow a symbolic link to an outside directory |
| Writing files | `write` and `shell` denied; not offered as tools | Verified each run (flags present, isolated settings in effect); Tested: plan unchanged |
| Tools | Only `view`, `glob`, `grep`. `url` denied, built-in and repository MCP servers disabled; hooks, connectors, memory, project extensions, and the IDE connection off | Verified each run; Tested: hooks and MCP launchers did not run; real runs used only these tools (on Linux they appear as `glob`, `view`, and `rg`, Copilot's name for `grep`) |
| Environment | The same allowlist, `GH_HOST` and `COPILOT_GH_HOST`, and only the token Copilot uses (the first of `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`), plus any `--pass-env` names, and the settings Duo sets for it (`COPILOT_HOME`, `COPILOT_ALLOW_ALL=false`, and others). Other tokens, your Copilot settings, and provider overrides are dropped. Without a shell tool, the model has no direct way to read them | Tested: only the chosen token and the host variables are passed, and no other listed secret (fake CLI); real reviews used the token under it (macOS and Linux) |
| Network | The CLI talks to GitHub Copilot. URL access is denied unless `--allow-web` | Configured |
| Authentication | A token from the environment; the saved Copilot login is not used. The CLI process necessarily holds the token | Tested: tokens never appear in audit files (macOS and Linux) |

## Web access (`--allow-web`, experimental)

Off by default. With `--allow-web`, Duo gives reviewers these web tools and
changes nothing else in the inspection policy:

| Reviewer | Web tools | Mechanism |
| --- | --- | --- |
| Codex (native) | OpenAI's hosted web search, which can also open pages | `web_search="live"` instead of `"disabled"`; sandboxed commands stay read-only, with no network |
| Claude Code (native) | `WebSearch`, `WebFetch` | Added to `--tools` and `--allowedTools`; restricted mode otherwise unchanged |
| Copilot | `web_fetch` only, for any URL | Added to `--available-tools`, `url` no longer denied, `--allow-all-urls`. Copilot's web search comes from its built-in GitHub MCP server, which also offers repository-writing tools, so it stays disabled |

The prompt tells reviewers to use the web only for facts and sources the task
depends on, to give the URLs they relied on, to treat web content as material
rather than instructions, and never to put repository content, file contents, or
credentials into a query or URL. That is a request to the model, not a control.

What it changes:

- **Prompt injection can now send data out.** Without web tools, a manipulated
  reviewer can only write a bad document, which Duo validates and you read.
  With them, text in the repository or on a fetched page can steer a reviewer
  into encoding what it has read into a search query or a URL it fetches. Native
  Codex can read any file your user can read, so that includes your home
  directory; Claude Code and Copilot read only inside the repository.
- **Search queries reach search providers**, even when nothing goes wrong.
- **Web evidence is not in the audit record** as file contents are: pages change,
  and the run keeps only what each CLI logs. Codex's attempt logs list each search
  and opened page, and Copilot's each fetched URL. Native Claude's do not: Claude
  Code's JSON output lists no tool calls, and its usage counters stayed at zero
  even when it searched and fetched. Claude Code's `WebFetch` also returns a
  model-written summary of the page rather than the page itself.

Tested (Linux, 2026-10-14; codex-cli 0.162.1, Claude Code 2.1.299, Copilot CLI
1.0.96): a report-mode check of a three-entry `refs.bib` against DOI records,
natively (`gpt-6-astra`, `claude-sonnet-5-5`) and through Copilot (`gpt-5.4`,
`claude-sonnet-5.5`). Both pairs agreed, confirmed the two real entries (noting
their missing end pages), and reported the invented third DOI as not found; a
Crossref check confirmed every finding. Native Codex's sandboxed `curl` and
Python requests failed to resolve hosts, so its web access went only through
web search. Native Claude, with Duo's exact command line, returned a release
checksum it could only have read by fetching the file. No token in any file.

Use it only on repositories whose every file you trust, preferably with Claude
Code or Copilot for both reviewers, and check web-derived claims against the URLs
the document gives. The run prints a warning, and `manifest.json` and
`reviewer-policy.json` record the setting.

## Credential check

Before saving or showing a response, Duo rejects any new private key, AWS key,
GitHub, Anthropic, OpenAI, Slack, Google, or Stripe token, or JSON Web Token in
its plan or reason, and asks the reviewer to retry without repeating the value.
This is **best-effort detection**:

- it cannot stop data a reviewer has already read from reaching its provider;
- it covers only those formats, not passwords, connection strings, or other
  secrets;
- it applies to the plan and reason, not to what the CLI logs locally.

Its run time grows linearly with input; adversarial inputs at the 1 MiB limit
take well under a second (tested).

## Local files

The state directory (`~/.local/state/duo` by default) and every run directory are
created with mode `0700`, so only your user can read them. They contain raw CLI
output, including **rejected responses and anything a reviewer printed**, so
treat them as sensitive; `manifest.json` also records the task, criteria, and
any custom instructions. The document Duo writes (`plan.md` by default) gets your
usual permissions for new files.
Duo never copies credentials, edits global CLI settings, or passes
permission-bypass flags.

## Not claimed

- Isolation from the rest of your files. Codex can read outside the repository.
  Claude Code and Copilot refused file reads and searches outside it in tests,
  but that confines their tools, not the environment or the CLI process itself.
- Hiding a credential from the CLI process that needs it to authenticate.
- Protection against a compromised CLI binary or npm package.
- Safety under enterprise-managed CLI policies: Duo refuses to run with them
  rather than reinterpret them.
- Any protection against data leaving through web tools with `--allow-web`.

## Reducing exposure

Until an isolation profile exists, for repositories you are less sure of:

- run Duo under a separate OS account or in a virtual machine holding only the
  repository and the reviewer logins;
- keep secrets out of your shell profile: Duo already drops unrelated environment
  variables, but Codex's commands run in a login shell that reads the profile;
- prefer Claude Code or Copilot over native Codex where reads outside the
  repository matter: both refused outside reads and searches in tests.

## Reproducing the evidence

- `python scripts/run_tests.py` (from `tests/`): offline suite, including
  credential-filter, fixture, and limit tests.
- `python scripts/smoke_inspection.py --codex-model ... --claude-model ...`: real
  CLIs, two model calls; checks that hooks and MCP launchers stay inert, the plan
  is unchanged, and the repository is readable. `--confinement` adds two model
  calls that ask each reviewer to read three files outside the repository and
  reports, per file, whether its random phrase reached the response or audit files.
  `--search-confinement` adds two model calls that do the same with the search
  tools, reporting file names and contents separately.
- Every run's `reviewer-policy.json` records the controls preflight verified.

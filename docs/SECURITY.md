# Duo security model

What each reviewer can reach, what Duo enforces, and what it does not claim.
Evidence labels distinguish configuration from enforcement:

- **Configured**: Duo supplies settings, environment variables, or CLI options.
- **Options checked each run**: required option names appear in CLI help; this
  establishes advertised availability, not enforcement.
- **Metadata checked each run**: the specific effective values or policy state
  named in the row pass preflight. Missing, conflicting, or unrecognized probe
  results stop setup according to the existing checks.
- **Tested**: behavior observed in offline tests or historical live checks, only
  for the recorded operations, models, CLI versions, and platforms. An unchanged
  document alone is not a deliberate write-denial test.
- **Not verified**: unknown; assume the less safe answer.

Preflight makes no model calls and does not exercise write denial, read
confinement, network denial, or every tool restriction. Historical live results
are in [STATUS](STATUS.md); accepted version ranges include unexercised patches.
Unless specified otherwise, the historical Linux confinement matrix used Codex
0.162.1 / `gpt-6-astra`, Claude Code 2.1.296 / `claude-sonnet-5-5`, and Copilot
1.0.96 / `gpt-5.4` and `claude-sonnet-5.5`. Later checks have their own scopes.

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
| Reading files | Sandboxed shell commands can read any file your user can read. Reads are **not** confined to the repository. | Tested: a recorded real run (`tests/fixtures/codex-0.162.1/events-approve.jsonl`) shows Codex checking for files in every directory above the repository; live runs on Linux also read `/tmp` and `/`. Tested (`--confinement`, Linux): read a file in the temporary directory, one in the home directory, and one through a symbolic link inside the repository. Dedicated-search requests were declined for lack of search tools, not tested for confinement |
| Writing files | Read-only sandbox, approvals `never` | Configured: sandbox and approval overrides supplied. Options checked each run: required `exec` option names, including `--sandbox`; no effective sandbox/approval metadata check. Tested: deterministic probes denied writes inside/outside the repository on Linux x86-64: 0.164.0 through `codex sandbox` with Duo's options and reviewer environment (`EROFS`); 0.165.0 and 0.166.0 deterministic probes ("Read-only file system"). On macOS 27.2 arm64, 0.167.0 through `codex sandbox` with Duo's options denied writes inside/outside the repository and in the home directory ("Operation not permitted"). No files were created. These are historical STATUS results, not per-run tests. The separate 0.164.0 model-directed write attempt timed out without a tool call and remains inconclusive |
| Tools | Read-only sandboxed commands. MCP servers, apps, plugins, hooks, notify commands, browser use, web search, multi-agent tools, and shell snapshots configured off | Options checked each run: required `exec` names. Metadata checked each run: the nine disabled feature values report false under CLI overrides over normal configuration; MCP list is empty with an empty `CODEX_HOME`. Other overrides, including notify and web search, are supplied, not independently checked as effective values. Tested: hooks and MCP launchers stayed inert in the recorded startup smoke tests; 0.162.1 ignored user-configured MCP servers, including trusted-project configuration. `mcp_servers={}` alone does not remove configured servers. Empty `CODEX_HOME` removes user configuration and the usual stored-login location, but retains allowed authentication variables (`CODEX_API_KEY`, `OPENAI_API_KEY`) and explicit `--pass-env` additions. Login-fetched managed MCP policy remains not verified |
| Environment | Only what it needs to run and log in: `HOME`, `PATH`, user and locale settings, temporary and XDG directories, the session bus, proxy and certificate settings, and `CODEX_HOME`, `CODEX_API_KEY`, `OPENAI_API_KEY`, plus any `--pass-env` names. Cloud credentials, other tools' tokens, the SSH agent, and `NODE_OPTIONS` are dropped. Codex runs its commands in a login shell, which can set variables again from your shell profile | Tested: review calls, setup probes, and version checks receive only these, and no dropped value reaches the audit (fake CLIs); the real CLIs still found their logins and passed preflight with them (macOS and Linux); what the login shell adds is not verified |
| Network | The CLI talks to OpenAI. Web search is disabled unless `--allow-web` | Configured: `web_search` override supplied. Network denial from sandboxed commands not verified; historical DNS failures alone do not establish it (plan §8) |
| Authentication | Codex's own stored login. Because reads are unrestricted, its commands can read credential files in your home directory | Not prevented |

### Claude Code (native)

| Access | Behavior | Evidence |
| --- | --- | --- |
| Reading files | `Read`, `Glob`, and `Grep` only. Restricted mode refuses reads outside the working directory, including through a symbolic link inside the repository | Tested (`--confinement`, Linux): `Read` was denied for a file in the temporary directory, one in the home directory, and one through a repository symlink, each listed in Claude Code's `permission_denials`. Tested (`--search-confinement`, Linux): `Glob` and `Grep` were denied for a directory in the temporary directory and one in the home directory; searching the repository did not follow a symbolic link to an outside directory |
| Writing files | No write or shell tools requested | Configured: explicit tool allowlist. Options checked each run: required names including `--tools` and `--allowedTools`; no effective tool-list or write-denial probe. Tested: document stayed unchanged in the recorded startup smoke tests |
| Tools | Only `Read`, `Glob`, `Grep` requested. Safe and restricted modes, no setting sources, hooks and connector/plugin sync disabled, MCP, Chrome, and slash commands disabled | Configured: options and settings supplied. Options checked each run: required help names. Metadata checked each run: local managed sources/cache and accepted `claude doctor` remote-policy state. Accepted "not fetched" states do not verify authenticated policy; that remains open in plan §8. Tested: hooks and MCP launchers stayed inert in recorded startup smoke tests; no per-run tool-use test |
| Environment | Only what it needs to run and log in: `HOME`, `PATH`, user and locale settings, temporary and XDG directories, the session bus, proxy and certificate settings, and `CLAUDE_CONFIG_DIR`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, plus any `--pass-env` names. Without a shell tool, the model has no direct way to read it | Tested: review calls, setup probes, and version checks receive only these, and no dropped value reaches the audit (fake CLIs); the real CLIs still found their logins and passed preflight with them (macOS and Linux) |
| Network | The CLI talks to Anthropic. Web tools are excluded unless `--allow-web` | Configured: tool allowlist; no independent network-denial test |
| Authentication | Claude Code's own login | As for reading files |

### Copilot (either reviewer)

| Access | Behavior | Evidence |
| --- | --- | --- |
| Reading files | `view`, `glob`, `grep`; repository confinement requested through fresh configuration without trusted folders, `--disallow-temp-dir`, and no interactive approval. Observed outside-path denials judge symlinks by their targets | Tested (`--confinement`, historical Linux matrix): Claude `view` denied temporary, home, and repository-symlink targets; GPT `view` denied the symlink target but did not attempt temporary/home reads, which remain unverified. Tested (`--search-confinement`, same matrix): both models' outside temporary/home searches denied; GPT searched the repository without following the link, while Claude's combined call was denied. Copilot 1.0.105 on Linux denied all 18 outside operations with `claude-sonnet-5.5` in both roles; this does not fill the GPT gap (plan §8) |
| Writing files | `write` and `shell` denied; excluded from requested tools | Configured: tool allowlist and deny options. Options checked each run: required help names. Metadata checked each run: selected settings, not effective tool permissions. Tested: document stayed unchanged in recorded smoke tests; no per-run write-denial test |
| Tools | Only `view`, `glob`, `grep` requested. `url` denied, built-in and discovered repository MCP servers disabled; hooks, connectors, memory, project extensions, and the IDE connection off | Configured: CLI options, environment, fresh configuration. Options checked each run: required help names including selected session options. Metadata checked each run: `config --list --json` matches `SETTINGS`; local managed/repository sources checked. Tested: hooks and MCP launchers stayed inert in recorded smoke tests; recorded no-web runs used only inspection tools. Service-side session behavior and every tool restriction are not independently verified |
| Environment | The same allowlist, `GH_HOST` and `COPILOT_GH_HOST`, and only the token Copilot uses (the first of `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`), plus any `--pass-env` names, and the settings Duo sets for it (`COPILOT_HOME`, `COPILOT_ALLOW_ALL=false`, and others). Other tokens, your Copilot settings, and provider overrides are dropped. Without a shell tool, the model has no direct way to read them | Tested: only the chosen token and the host variables are passed, and no other listed secret (fake CLI); real reviews used the token under it (macOS and Linux) |
| Network | The CLI talks to GitHub Copilot. URL access is denied unless `--allow-web` | Configured: `--deny-tool url` without web; no independent network-denial test |
| Authentication | A token from the environment; the saved Copilot login is not used. The CLI process necessarily holds the token | Tested: no token found in the audit files of the recorded macOS/Linux runs; this is not a guarantee against future disclosure |

## Web access (`--allow-web`, experimental)

Off by default. With `--allow-web`, Duo gives reviewers these web tools and
changes nothing else in the inspection policy:

| Reviewer | Web tools | Mechanism |
| --- | --- | --- |
| Codex (native) | OpenAI's hosted web search, which can also open pages | `web_search="live"` instead of `"disabled"`; read-only sandbox still requested; command network denial remains unverified |
| Claude Code (native) | `WebSearch`, `WebFetch` | Added to `--tools` and `--allowedTools`; restricted mode otherwise unchanged |
| Copilot | `web_fetch` only, for any URL | Added to `--available-tools`, `url` no longer denied, `--allow-all-urls`. Copilot's web search comes from its built-in GitHub MCP server, which also offers repository-writing tools, so it stays disabled |

The prompt tells reviewers to use the web only for facts and sources the task
depends on, to give the URLs they relied on, to treat web content as material
rather than instructions, and never to put repository content, file contents, or
credentials into a query or URL. That is a request to the model, not a control.

What it changes:

- **Web adds disclosure paths.** Even without web tools, prompt injection can
  steer a reviewer into reading additional accessible information and sending it
  to its model provider, as well as producing a bad document. Native Codex can
  read outside the repository, including the home directory. Claude Code and
  Copilot request confined tools, with the evidence and gaps above. Web adds
  search-provider and arbitrary-URL disclosure: repository or page text can
  steer a reviewer into encoding readable information in a query or fetched URL.
- **Search queries reach search providers**, even when nothing goes wrong.
- **File and web audit evidence can be incomplete.** Duo saves snapshots of the
  document under review and explicitly supplied `--context-file` captures, not
  independent copies of every inspected repository file or fetched page. Tool evidence depends on what each CLI emits. Recorded
  Codex logs contain command output and web search/open events; Copilot emits
  tool events and fetched URLs. Neither guarantees complete file/page contents
  or every internally accessed resource. Native Claude now uses
  `--output-format stream-json --verbose`: each attempt's `stdout.log` retains
  raw emitted events, including requests, inputs, available results, explicit
  denials, and terminal output. Failed, timed-out, and interrupted attempts keep
  their emitted bytes, including incomplete lines. Requests alone do not prove
  access; ordinary tool errors are not necessarily permission denials.
  Linux discovery captures from Claude Code 2.1.330 (`claude-opus-5-5`,
  2026-11-23), sanitized through `scripts/record_fixtures.py`, cover successful
  Read/Glob/Grep both with and without web, WebSearch/WebFetch with web, a missing
  file, an outside-path denial, and a turn-limit failure. Every enabled tool has
  a live capture in each configuration; macOS stream behavior is unverified.
  Fixtures strip rate-limit account metadata while retaining event presence/type.
  The terminal `usage.server_tool_use` web counters were zero despite successful
  web calls: derive web activity from request/result events, never counters.
  `WebFetch` returns a model-written summary, not the page. URLs, search results,
  and emitted events do not guarantee complete file/page archives or enumerate
  every internally accessed resource. See STATUS for provenance and fresh-run
  limitations.

Native Claude accepts a response only after exit zero and a well-formed stream
ending in exactly one `result` with `subtype: success` and `is_error: false`.
Only that event's `structured_output` enters `validate_payload`; assistant text
and `StructuredOutput` tool requests cannot become reviews. Malformed/truncated
events, missing or multiple results, unsuccessful envelopes (even at exit zero),
and invalid response schemas fail closed. Existing output/storage limits remain
in force; the stream parser uses the configured `--max-output-mb` cap (32 MiB by
default). The logs are not truncated to hide an overrun. Failure messages use
sanitized classification, not repository/tool text.

Each native Claude attempt also writes `claude-stream.json`: `process_status`
and `returncode` describe execution; `completion` is `missing`, `ambiguous`,
`invalid`, `unsuccessful`, or `success`; `terminal_results` counts parsed result
events. `usage_available` is true only when a valid, unambiguous terminal carries
a usage object, which is copied to `usage`; otherwise it is false and `usage`
is null. Reported zeros remain zeros. `payload_validated` records successful
adapter schema validation, not workflow acceptance or reviewer agreement. A
terminal success does not override a nonzero exit, interruption, deadline, or
the workflow's remaining review checks. Like other audit artifacts, these files
can contain sensitive output and are not resumable checkpoints.
If writing stream metadata fails during an error or interruption, the original
failure is preserved; the metadata file may be absent. A metadata write failure
after an otherwise successful attempt prevents accepting its review.

Tested (Linux, 2026-10-14; codex-cli 0.162.1, Claude Code 2.1.299, Copilot CLI
1.0.96): a report-mode check of a three-entry `refs.bib` against DOI records,
natively (`gpt-6-astra`, `claude-sonnet-5-5`) and through Copilot (`gpt-5.4`,
`claude-sonnet-5.5`). Both pairs agreed, confirmed the two real entries (noting
their missing end pages), and reported the invented third DOI as not found; a
Crossref check confirmed every finding. Native Codex used web search; its
sandboxed `curl` and Python requests failed to resolve hosts. These DNS failures
alone do not establish network denial, which remains unverified. Native Claude,
with Duo's exact command line, returned a release
checksum it could only have read by fetching the file. No token in any file.
Copilot started with `--repo` set to a subfolder of a Git repository was refused
a file in a sibling folder, by relative and absolute path, so a web-enabled run
pointed at a subfolder cannot read the rest of the project through Copilot.

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

The scanner consumes marker-based token runs once, including OpenAI and JWT
near-matches, deduplicates candidate values, and searches the current document
for multiple distinct values in one pass per credential format. Already-known
values remain permitted as substrings, with normalized line endings; plan and
reason are scanned separately.

Measured on CPython 3.12.3, Linux 7.0.0-44-generic, x86_64, glibc 2.39:

| Response size | Repeated `sk-` near-matches | Repeated `eyJ-`, no dots | Valid JWT first part + dot, then `eyJ-` repeats | Repeated GitHub-shaped value, known only at document end | Distinct GitHub-shaped values, known in document's second half |
| --- | --- | --- | --- | --- | --- |
| 128 KiB | 0.0136 s | 0.0138 s | 0.0138 s | 0.0145 s | 0.0673 s |
| 256 KiB | 0.0276 s | 0.0278 s | 0.0277 s | 0.0296 s | 0.1499 s |
| 512 KiB | 0.0539 s | 0.0557 s | 0.0558 s | 0.0575 s | 0.3037 s |
| 1 MiB | 0.1062 s | 0.1081 s | 0.1090 s | 0.1141 s | 0.6511 s |

These are minimum scan times over three fresh processes per case, excluding
process startup and input construction, using the size-doubling regression test
in `tests/test_duo.py`. The known document is the same size as the response for
the GitHub cases; the distinct case repeats each value twice in the response.
The JWT second-part case is `"eyJ" + "a" * 10 + "."` followed by repeated
`"eyJ-"`, truncated to the response size. The `sk-` case also took 0.00129,
0.00256, and 0.00515 s at 12,288, 24,576, and 49,152 bytes. Doubling from
128 KiB through 1 MiB increased scan times by 1.94–2.23 times, rather than the
roughly fourfold growth of the reported quadratic cases. These measurements are
specific to this interpreter and host. The tests retain a five-second per-case
guard at the 1 MiB limit and enforce a subprocess timeout; the maximum-size
checks include the existing PEM adversarial cases, and the size-doubling test
checks scaling.

Peak memory was also measured with 25,575 distinct GitHub-shaped values in a
1 MiB response and the same values reversed in a 1 MiB known document, without
duplicates. Each value was `ghp_` followed by 36 characters drawn using
`random.Random(0).choices(string.ascii_letters + string.digits, k=36)`, producing
early trie branches with little prefix sharing. Across three fresh processes
with five-second timeouts, the minimum scan time was 1.6766 s and peak process
RSS was 233.0 MiB (`resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`, Linux
KiB converted to MiB), up 209.5 MiB from the pre-scan high-water mark after input
construction. The dictionary-per-node known-value index therefore has material
memory overhead at the input limit; the timing table's distinct case uses half
as many unique values and does not represent this memory stress case.

Duo rechecks the run deadline after response validation. If validation finishes
at or after the deadline, the run records `limit_reached` without accepting or
displaying the review, publishing a revision, or changing the existing approvals.

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
  Claude Code and Copilot refused the attempted outside operations recorded above,
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
  repository matter: both have recorded outside denials, subject to the GPT
  temporary/home read gap above.

## Reproducing the evidence

- `python scripts/run_tests.py` (from the repository root, with Duo and its
  dependencies installed): offline suite, including credential-filter, fixture,
  and limit tests. From `tests/`, use `python ../scripts/run_tests.py`; an absolute
  script path works from any directory. Empty suites, failures, errors, skips,
  and missing LangGraph fail the check. CI already used the correct `tests/`
  working directory; this discovery fix does not imply historical CI ran zero tests.
- `python scripts/smoke_inspection.py --codex-model ... --claude-model ...`: real
  CLIs, two model calls; checks that hooks and MCP launchers stay inert, the plan
  is unchanged, and the repository is readable. `--confinement` adds two model
  calls that ask each reviewer to read three files outside the repository and
  reports, per file, whether its random phrase reached the response or audit files.
  `--search-confinement` adds two model calls that do the same with the search
  tools, reporting file names and contents separately.
- Every run's `reviewer-policy.json` distinguishes supplied restrictions from
  advertised options and selected metadata checked by preflight; it does not
  certify per-run behavioral enforcement.


## Captured context and combined prompt budget (Unreleased)

`--context-file PATH` captures bounded regular UTF-8 files once at startup, with
an aggregate 64 KiB limit. Aliases and hard links to the reviewed document are
rejected. The repaired credential detector scans every capture before any
context is saved or reviewer process starts, using no known-document exception.
Rejection identifies only the file and credential kind. Detection is best-effort,
not clearance for sensitive material: unrecognized secrets can still pass and
would reach both model providers. Do not supply sensitive evidence.

Both roles receive the same exact decoded text in labeled untrusted sections
with collision-resistant delimiters on every turn and retry. Audit artifacts
`context-*.bin` preserve exact bytes; `context.json` records labels, source paths,
order, byte lengths, and SHA-256 hashes. They live in the private run directory
and are not resumable checkpoints. Later source edits do not alter captures.
Separately inspected repository files remain mutable and are not snapshotted.
64 KiB is roughly 16,000 extra input tokens per call at four bytes per token,
with variation by content and tokenizer.

`--max-prompt-bytes` has a **512 KiB (524,288-byte) default, ratified by the
owner on 2026-11-23**. A finite default is a compatibility narrowing versus
0.4.6, which has no combined ceiling. Measurements on 2026-11-23 reconstructed
seven recorded runs: the maximum was 150,232 bytes, followed by 109,987 and
93,278; fixed inputs peaked around 79 KB. The ratified default accepts every
measured case with at least 3.4x margin. It still rejects some inputs accepted
by 0.4.6; the owner approved that narrowing when ratifying the default.

The ceiling counts the full assembled UTF-8 prompt: instructions, task, criteria,
document, diff, selected history, context, retry feedback, delimiters, and Duo's
backend wrappers including Copilot's schema prefix. Both reviewers' fixed inputs
are checked before startup preflight; full prompts are checked before adapter
calls and subprocess launch. Overflow records the byte count and largest
sections, stops without truncation or retry of that prompt, and preserves the
last accepted document and snapshot for a rerun with a larger budget. These
bytes are not exact tokens and cannot guarantee model context capacity;
CLI-added instructions, tool results, and output reserve also consume context.

# Real-CLI fixtures

Sanitized output recorded from the tested CLI versions (see `TESTED_VERSIONS`
in `src/duo/policy.py`). `tests/test_fixtures.py` replays it through Duo's
parsers and control checks, and the Copilot fake CLI replays a recorded stream,
so hand-written fakes cannot drift from real formats unnoticed.

| Directory | Contents |
| --- | --- |
| `codex-0.162.1` | version, `exec --help`, effective `features list`, `mcp list --json`; JSON event stream and response of an approval |
| `codex-0.163.0` | version, `exec --help`, effective `features list`, `mcp list --json` (with an empty `CODEX_HOME`, as preflight lists it) |
| `codex-0.164.0` | version, unchanged `exec --help`, effective `features list`, empty-home `mcp list --json`; JSON event stream and response of a live approval on Linux |
| `codex-0.165.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on Linux |
| `codex-0.166.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on Linux |
| `codex-0.167.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on macOS |
| `codex-0.168.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on Linux |
| `codex-0.169.1` | the same probes (`exec --help` adds `--trust`); JSON event stream and response of a live approval on Linux |
| `codex-0.170.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on Linux |
| `codex-0.171.0` | the same probes (`exec --help` adds `--remote` and `--remote-auth-token-env`); JSON event stream and response of a live approval on Linux |
| `codex-0.172.0` | the same probes (`exec --help` unchanged); JSON event stream and response of a live approval on Linux; unknown-option rejection (2026-11-28, Linux) |
| `claude-2.1.296` | version, `--help`, `doctor`; success envelope; unrecognized-model envelope (`is_error: true` with `subtype: "success"`) and stderr |
| `claude-2.1.332` | unknown-option rejection only (2026-11-28, Linux); no probes were recorded for this version |
| `claude-2.1.330` | Linux stream discovery: Read/Glob/Grep with and without web, WebSearch/WebFetch with web, explicit denial versus missing-file error, success and turn-limit terminals; fresh restricted probes and timed-out adapter streams (2026-11-23, `claude-opus-5-5`) |
| `copilot-1.0.95` | version, `--help`, isolated `config --list --json`; event streams from GPT (`phase: "final_answer"`) and Claude (no `phase`) models; model-unavailable and authentication-failure stderr |
| `copilot-1.0.105` | version, `--help` (`--sync-session` in place of `--no-remote` and `--no-remote-export`, from 1.0.103), isolated `config --list --json` |
| `copilot-1.0.116` | version, `--help` (options now sorted; adds opt-in `--decision`, `--choice`, and `--sandbox`; removes `--host-environment`, `--host-compute-id`, and `--plain-diff`, none passed by Duo), unchanged isolated `config --list --json`; recorded 2026-11-27 on Linux with the restricted recorder; unknown-option rejection (2026-11-28, Linux) |

Response fixtures come from disposable runs with synthetic tasks. The recorder
replaces home paths, the user and account names, and UUIDs; in Claude and
Copilot streams, every value under a key ending in `Id` or `_id` (except
`modelId`, `providerId` and `turnId`) becomes a per-stream `fixture-id-N`, so
request, message, response and tool-call IDs are removed while correlations
survive. Free text is also scrubbed for `request id` values and GitHub,
Anthropic and OpenAI identifier shapes. Encrypted or reasoning payloads and long
tool output are dropped; the parsed answer stays whole. The two
`copilot-1.0.95` streams kept such identifiers until 2026-11-28, when they were
re-sanitized with the current recorder. `tests/test_fixtures.py` checks every
recording for unreplaced identifiers, and `tests/test_record_fixtures.py` fails
if re-sanitizing any committed fixture would change it.

`rejection-unknown-option.json` records how a CLI's argument parser refuses an
option it does not define: the argv (Duo's invocation prefix plus
`--duo-fixture-unknown-option`, with no prompt and an empty stdin), the exit
code, and both streams. Each rejection happened before any model call.

The historical fixtures above, except `claude-2.1.330`, were captured before the recorder's restricted
native-probe environment fix. Native probes inherited the full parent
environment; the Codex MCP probe replaced only `CODEX_HOME`. They are historical
format and control-check evidence, not evidence of capture under the restricted
environment. Copilot probes already used `copilot.environment`. No fixtures were
re-captured for this fix; refresh affected native probes with the corrected
recorder when a new capture is authorized.

For `claude-2.1.330`, `events-noweb`, `events-web`, `events-webtools`, and
`events-maxturns` were imported from supplied live discovery streams through
`record_fixtures.py attempt claude`; no attempt metadata was fabricated for
them. New adapter captures `events-timeout-noweb` and `events-timeout-web` retain
init/retry events and actual timeout metadata; they reached no tools or terminal
results. New probes used the restricted environment. See `docs/STATUS.md` for
coverage and omissions. Historical `result-*.json` files remain historical
envelopes; they are not represented as stream recordings.

Claude fixtures keep complete tool inputs and structured responses, correlate
requests/results/denials with stable replacement IDs, and preserve error flags
and usage objects. Long result text is shortened; reasoning, signatures, and
rate-limit account metadata are removed. An incomplete final JSONL line is
omitted on import and recorded as `fixture_import.truncated_final_line` in the
fixture metadata. Raw attempt logs are not sanitized or shortened by the runner.

## Refreshing

When adding a tested version, record its production-equivalent probes:

```sh
DUO_FIXTURE_ACCOUNT=<github-login> python scripts/record_fixtures.py probes
```

Native probes now use preflight's restricted `reviewer_environment`, which can
retain permitted authentication and runtime variables. The Codex MCP probe
layers an empty `CODEX_HOME` onto that environment; it removes the usual stored
login location but does not remove permitted authentication variables. These
probes are not credential-free. Copilot retains its existing environment builder.

Then record responses from a disposable Duo run (synthetic task, scratch
repository) by attempt directory:

```sh
python scripts/record_fixtures.py attempt copilot ~/.local/state/duo/runs/run-…/attempt-… gpt-revise
```

Record each installed CLI's unknown-option rejection (no model is called):

```sh
python scripts/record_fixtures.py rejection
```

Review every file before committing. The fixture tests reject credential
patterns and home-directory paths, but they cannot judge whether content is
private.

## Drift baselines

`drift-baselines.json` lists the fixtures `scripts/check_drift.py` may compare
installed CLIs with: for each reviewed CLI version, the path and SHA-256 digest
of its `exec-help.txt` and `features.txt` (Codex) or `help.txt` (Claude Code,
Copilot). The drift check uses the latest reviewed version at or below the
installed one and refuses to compare with a file whose digest differs, so a
re-recorded fixture stays flagged until it is reviewed. Older entries remain for
replay. After reviewing a recorded version (see "Responding to CLI drift" in the
top-level README), record it:

```sh
python scripts/check_drift.py --review codex 0.172.0
```

The command refuses fixtures whose layouts are unrecognized or that lack an
option preflight requires (for Codex, also a disabled feature reported `false`).
Every entry present on 2026-11-27 was recorded from the fixtures above, each of
which had been reviewed when its version was adopted.

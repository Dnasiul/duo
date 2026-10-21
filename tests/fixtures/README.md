# Real-CLI fixtures

Sanitized output recorded from the tested CLI versions (see `TESTED_VERSIONS`
in `src/duo/policy.py`). `tests/test_fixtures.py` replays it through Duo's
parsers and control checks, and the Copilot fake CLI replays a recorded stream,
so hand-written fakes cannot drift from real formats unnoticed.

| Directory | Contents |
| --- | --- |
| `codex-0.162.1` | version, `exec --help`, effective `features list`, `mcp list --json`; JSON event stream and response of an approval |
| `codex-0.163.0` | version, `exec --help`, effective `features list`, `mcp list --json` (with an empty `CODEX_HOME`, as preflight lists it) |
| `claude-2.1.296` | version, `--help`, `doctor`; success envelope; unrecognized-model envelope (`is_error: true` with `subtype: "success"`) and stderr |
| `copilot-1.0.95` | version, `--help`, isolated `config --list --json`; event streams from GPT (`phase: "final_answer"`) and Claude (no `phase`) models; model-unavailable and authentication-failure stderr |

Response fixtures come from disposable runs with synthetic tasks. The recorder
replaces home paths, the user and account names, session and request IDs, and
drops encrypted or reasoning payloads and long tool output; the parsed answer
stays whole.

## Refreshing

When adding a tested version, record its credential-free probes:

```sh
DUO_FIXTURE_ACCOUNT=<github-login> python scripts/record_fixtures.py probes
```

Then record responses from a disposable Duo run (synthetic task, scratch
repository) by attempt directory:

```sh
python scripts/record_fixtures.py attempt copilot ~/.local/state/duo/runs/run-…/attempt-… gpt-revise
```

Review every file before committing. The fixture tests reject credential
patterns and home-directory paths, but they cannot judge whether content is
private.

# Status and handoff

Where Duo's production readiness stands, and how to continue on a Linux machine.
Written 2026-10-13 at commit `c5886a7`; Linux live results added the same day.
`plan.md` is the full production plan; this page tracks progress against it.

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
| Live runs on Linux: smoke test (native and Copilot) and all four backend combinations, including both mixed pairs | "Linux live verification" below |

## Open

1. **Codex MCP preflight with user-configured servers**: `-c 'mcp_servers={}'`
   does not remove servers defined in `~/.codex/config.toml`, so preflight
   refuses native Codex for anyone with a Codex MCP server. Reviews pass
   `--ignore-user-config`, but `codex mcp list` does not accept it, so the probe
   checks a different configuration from the one reviews load. Fix: make the
   probe match the review (for example, override each listed server as disabled
   and require all to be disabled), add tests, correct the docs.
2. **Mixed pairs on macOS**: verified live on Linux only.
3. **Confinement evidence**: show with a file outside the repository what each
   reviewer can read. Codex is known to read outside; Claude Code and Copilot are
   untested (see `docs/SECURITY.md`). This can extend `scripts/smoke_inspection.py`.
4. **Decisions for the owner**: isolation profile now or documented as a
   limitation (recommended: documented); license (none yet); where to publish.
5. **Release**: changelog, tag `v0.1.0`, checksums, release report (plan section 7).

## Linux live verification

2026-10-13, Ubuntu 22.04.5 LTS, kernel 5.15.0-198-generic, x86-64, Python 3.11.17.
CLIs: codex-cli 0.162.1, Claude Code 2.1.296, Copilot CLI 1.0.96 (within the
tested 1.0.x range). Offline suite: 137 tests, OK. `check_drift.py`: all compatible
once the one Codex MCP server in the user configuration was commented out (see
open item 1).

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

Tools seen in the attempts:

- Copilot: only `glob`, `view`, and `rg` (its name for `grep`); all built-in MCP
  servers reported disabled.
- Native Codex: read-only commands (`rg --files`, `ls`, `cat`, `python --version`).
  It looked for `AGENTS.md` and `CLAUDE.md` in parent directories, `/tmp`, and `/`,
  consistent with the known lack of read confinement.
- Native Claude: its JSON output does not list tool calls; no permission denials.

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

# Duo runbook

How to operate Duo, recognize failures, and recover. The README describes
options and guarantees; this page covers what to do when something goes wrong.

Reviewer agreement is a signal, not a verdict. For consequential work, a person
still assesses the final plan.

## Recognizing the outcome of a run

Every run prints a summary; `--json` makes it machine-readable. Check the exit
code first:

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Both reviewers approved the current plan | Review `plan.md` (or the `--mode` document, shown as `plan_path`) |
| 1 | Setup, reviewer, storage, or limit failure | Read `message`; see the sections below |
| 2 | Invalid command-line arguments | Fix the command |
| 3 | A reviewer needs a decision (`blocked`) | Rerun with `--answer TEXT --source-run RUN_DIR`, keeping the same task and criteria |
| 4 | Turn or time budget used up | Rerun, or raise `--max-turns` / `--max-seconds` |
| 130, 143, 129 | Stopped by Ctrl-C, SIGTERM, or SIGHUP | Rerun when ready |

The JSON summary always holds `status`, `message`, and `latest_review`
(reviewer, backend, model, decision, reason), plus `run_dir` once a run directory
exists. Runs that reach the review loop also report `turns`, `attempts`,
`untested_cli`, and `plan_path`. The run directory under
`~/.local/state/duo/runs/` (or `--state-dir`) holds the details:

- `result.json`: the final state when the review loop finished, whatever the
  outcome, including every accepted review in `history` and the stop reason;
- `state.json`: the same state, saved after each accepted review;
- `error.json`: the summary of a run that stopped on a setup failure, an
  exception, or a signal;
- `events.jsonl`: each attempt, failure, and accepted review in order;
- `plan-NNN.md`: every accepted plan version; `plan-000.md` is the starting plan;
- `versions.json` and `reviewer-policy.json`: CLI versions, supplied inspection
  policy, and option/metadata checks; not proof of per-run behavioral enforcement;
- `attempt-*/`: each CLI call's command, stdout, and stderr.

## Answering a blocked run

Read the final review's question in `latest_review.reason` or the last history
turn in `result.json`. Repeat the command with `--source-run` pointing to that
run directory and `--answer` containing your decision. Supply the original task
and criteria exactly, and keep the repository, document path, and mode the same.
Duo requires both options and a completed blocked source with a matching snapshot;
invalid sources are rejected before creating a new run or calling reviewers.

This starts a fresh run. The source is never modified, and its approvals are not
carried over. You may change the current document before answering; both reviewers
must approve it afresh. The new manifest's `answer` object records the source path
and ID, question, answer, and blocked/current document hashes. The prompt explains
any changed document and treats your answer as a decision on the question, not an
approval. The same blocked source can be answered more than once.

## Interrupted or failed runs

`plan.md` always holds the last accepted plan (with `--mode`, the mode's document
does; everything below applies to it the same way). Duo writes it only after a
revision is validated, replacing it atomically, so an interrupted, failed, or
killed run never leaves a partial plan. Stopping Duo with Ctrl-C, SIGTERM, or
SIGHUP also stops the reviewer CLI and everything it started.

To start over, run the same command again: the new run reviews the saved plan
with fresh approvals. To recover an interrupted run recorded by this version,
use the explicit audit directory printed by Duo:

```sh
duo --resume ~/.local/state/duo/runs/run-EXAMPLE --show-conversation
```

Resume retains the document, diff, review history, findings, and reserved attempts,
but clears approvals and requires both reviewers to approve again. The original
absolute deadline still applies, including downtime. Preflight checks current
binaries and controls; `versions-resume-N.json` records each segment's evidence
without replacing `versions.json`. Run-defining settings cannot be overridden;
`--allow-untested-cli` must match the original setting. `--json` is also accepted.

Recovery refusals and outcomes:

- **Audit-only / no checkpoint:** older directories are records and cannot be
  imported. Start a new run on the saved document.
- **Terminal run:** agreement, error, and limit outcomes are final. A blocked
  outcome requires a fresh `--answer` / `--source-run` invocation.
- **Integrity, unsupported schema, missing artifacts, or divergent audit state:**
  do not edit records to bypass validation. Preserve them for diagnosis and start
  a new run if needed.
- **Document changed outside Duo:** recovery leaves it untouched. Choose the
  desired document and start a new run. A pending proposal is committed only if
  the actual document matches its recorded published hash; otherwise a matching
  pre-publication document discards the proposal without inventing a review.
- **Inconsistent clock state:** timestamps cannot establish a safe deadline.
  Start a new run. An expired or conservatively clamped deadline, or exhausted
  turn budget, instead records a limit outcome (exit 4) without model calls.
- **Run-directory lock:** another invocation is using that run. Wait or stop it.
  The repository lock also excludes simultaneous runs in other audit directories.
- **Attempt allowance exhausted:** an interrupted call consumed the final
  reservation; recovery makes no additional call for that turn.

Ctrl-C/SIGTERM/SIGHUP leave a checkpointed run interrupted, with no terminal
`result.json` or `error.json`. Its verdict/report remains labeled interrupted.
Cleanup protects these paused runs, including unreadable checkpoints. To abandon
selected runs explicitly, preview then delete with, for example:

```sh
duo-clean --older-than 30 --include-recoverable
duo-clean --older-than 30 --include-recoverable --delete
```

Active locked runs remain protected even with this flag. `--copilot-runtime`
may remove rebuildable runtime caches from paused runs while retaining recovery
artifacts.

If a run reports `plan.md changed outside Duo`, someone edited the plan during the
review. Duo stopped rather than overwrite the edit. Keep the version you want in
`plan.md` and rerun.

If a run reports `Another Duo run holds the lock for this repository`, another
Duo process is reviewing the same repository, possibly with a different
`--state-dir`. Wait for it, or stop it. The lock is released automatically when
that process exits, even after a crash.

## Authentication renewal

Symptoms: `Authentication failed; sign in to the agent CLI`, `Agent CLI is not
logged in`, or `Agent authentication is unauthorized`. Duo does not retry these.

- Codex and Claude Code: sign in again with the CLI itself (`codex login`,
  `claude` then `/login`), then rerun Duo.
- Copilot: Duo uses only a token from `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or
  `GITHUB_TOKEN`, never Copilot's saved login. Create a new fine-grained token
  with the "Copilot Requests" permission, export it, and rerun. Revoke a token
  that was exposed, for example by pasting it into a chat or a command line.
- A CLI that logs in through an environment variable Duo does not pass (for
  example cloud credentials for a Bedrock or Vertex Claude login) fails the same
  way. Add the variable with `--pass-env NAME`; each run's `reviewer-policy.json`
  lists the variables every CLI received.

## CLI upgrades and drift reports

Duo runs only tested CLI versions (see the README's compatibility table). After
a CLI updates itself, setup may fail with a message such as `codex 0.164.0 is
outside the tested range (...)`. Options:

1. Install a tested version again, for example `npm install -g @openai/codex@0.163.0`.
2. Run once with `--allow-untested-cli`. The run is marked experimental in its
   summary and audit files; all control probes still apply.
3. Verify the new version and extend the tested range, as described under
   "Responding to CLI drift" in the README.

The weekly **CLI drift** workflow opens a "CLI drift: action needed" issue when a
new release is untested or fails a control probe, and closes it once everything
is compatible. Follow the steps in the README section for each result.

## Rejected policies

Duo refuses to start when it cannot establish reviewer isolation. The message
names the cause:

- `... managed configuration detected ...`, `... managed settings or policy hooks
  detected ...`, `... preferences policy detected or unreadable ...`: an
  administrator policy or hook is installed for that CLI. Duo cannot guarantee a
  read-only review under it. Use a machine or account without the policy, or the
  other backend for that reviewer.
- `Copilot repository settings conflict with inspection policy: <path>`: the
  repository's own Copilot or Claude settings re-enable hooks, plugins, or
  environment injection. Remove those keys, or review a copy without them.
- `Installed <cli> lacks required inspection options` or `... restrictions did not
  take effect`: the CLI changed. Treat it like a drift `probe-failure`: install a
  tested version and report the change.
- `Target must be a Git repository`: pass `--skip-git-repo-check` only for a
  directory you intend to review without Git.

## Disk pressure

Each run is limited by `--max-output-mb` per output stream (default 32) and
`--max-run-mb` per run directory (default 256); a violation ends the run with an
error and keeps the last accepted plan. Copilot additionally needs about 165 MiB
of temporary space per attempt for its runtime, which Duo deletes afterwards.

Audit runs are never deleted automatically. Reclaim space with `duo-clean`, which
only reports unless given `--delete`:

```sh
duo-clean --older-than 30 --delete          # runs untouched for 30 days
duo-clean --max-total-mb 500 --delete       # oldest runs beyond 500 MiB
duo-clean --copilot-runtime --delete        # runtimes left by older Duo versions
```

It never deletes a run in progress and never follows symbolic links. If cleanup
is interrupted, run it again.

## Rollback

Each release is tested with the `constraints.txt` from the same revision. To roll
back, install the previous release (its wheel, or the source at its tag) with that
release's constraints into a fresh environment:

```sh
git clone --branch <previous-tag> https://github.com/Dnasiul/duo.git duo-previous
python3 -m venv ~/.venvs/duo-previous
~/.venvs/duo-previous/bin/python -m pip install -c duo-previous/constraints.txt ./duo-previous
```

Or download the previous release's wheel, `SHA256SUMS`, and `constraints.txt` from
its GitHub release, check the wheel with `sha256sum --ignore-missing -c SHA256SUMS`
(`shasum -a 256` on macOS), and install the wheel with `-c constraints.txt`.

Plans and audit runs are plain files and stay compatible across versions; no
migration is needed. If a reviewer CLI upgrade caused the problem, reinstall the
CLI version listed as tested for that release.

## Consuming verdicts in CI

Use the `verdict.json` in the run directory returned by Duo's `--json` summary.
Generate it in the trusted CI execution path, then run the standalone consumer:

```sh
python examples/check_verdict.py --verdict "$RUN_DIR/verdict.json" \
  --document plan.md --task-file task.txt --criteria-file criteria.txt \
  --threshold high
```

The gate exits 0 only for a complete `agreed` verdict under a supported 1.x schema,
with the expected agreement rule, approvals bound to the document, matching current
document/task/criteria hashes, and no open finding at or above the selected
severity. Omit task/criteria options only when those bindings are not part of your
policy. Severity order is `info < low < medium < high < critical`; the default
threshold is `high`. `--rule` defaults to `both-reviewers-approve-same-subject`.

Exit **2** means the gate failed, distinct from Duo's run exit codes 0/1/3/4.
Failed checks go to stderr. Missing, malformed, incompatible, incomplete,
interrupted, blocked, error, limit, or stale artifacts fail closed. Inspect the
run's `report.html`, `events.jsonl`, and finding dispositions before retrying.
An empty findings array is not evidence of agreement. A failed or interrupted run
gets a best-effort verdict when its manifest is available; a missing artifact
must still fail the gate.

Hashes use UTF-8 text with CRLF and CR normalized to LF. They bind content, but
are not signatures: someone who can rewrite both the artifact and its inputs can
forge matching hashes. Protect the CI producer and artifacts accordingly. Verdict
schema versions are independent of package releases; field meanings remain fixed
within a major version and additive optional fields use minor versions.

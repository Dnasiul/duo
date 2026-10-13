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
| 0 | Both reviewers approved the current plan | Review `plan.md` |
| 1 | Setup, reviewer, storage, or limit failure | Read `message`; see the sections below |
| 2 | Invalid command-line arguments | Fix the command |
| 3 | A reviewer needs a decision (`blocked`) | Add the answer to the criteria and rerun |
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
- `versions.json` and `reviewer-policy.json`: CLI versions and verified controls;
- `attempt-*/`: each CLI call's command, stdout, and stderr.

## Interrupted or failed runs

`plan.md` always holds the last accepted plan. Duo writes it only after a
revision is validated, replacing it atomically, so an interrupted, failed, or
killed run never leaves a partial plan. Stopping Duo with Ctrl-C, SIGTERM, or
SIGHUP also stops the reviewer CLI and everything it started.

To continue, run the same command again. The new run reviews the saved plan with
fresh approvals; there is nothing to repair. Do not edit audit files to resume:
they are records, not checkpoints.

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

## CLI upgrades and drift reports

Duo runs only tested CLI versions (see the README's compatibility table). After
a CLI updates itself, setup may fail with a message such as `codex 0.163.0 is
outside the tested range (...)`. Options:

1. Install a tested version again, for example `npm install -g @openai/codex@0.162.1`.
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

Plans and audit runs are plain files and stay compatible across versions; no
migration is needed. If a reviewer CLI upgrade caused the problem, reinstall the
CLI version listed as tested for that release.

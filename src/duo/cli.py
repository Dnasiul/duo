from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import sys
import threading

from .adapters import CLIAdapter
from .models import (CLAUDE_EFFORTS, CODEX_EFFORTS, COPILOT_EFFORTS, DEFAULT_CLAUDE_MAX_TURNS,
                     MAX_TEXT_BYTES, Config, RunError, State, Terminated, Turn)
from .prompts import plan_diff
from .storage import PlanFile, RunStore, run_lock, timestamp
from .workflow import Reviewer, build_graph, initial_state

EXIT_CODES = {"agreed": 0, "error": 1, "blocked": 3, "limit_reached": 4}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Alternate Codex and Claude reviews of plan.md")
    result.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository")
    task = result.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="Original task to plan")
    task.add_argument("--task-file", type=Path, help="UTF-8 task file")
    criteria = result.add_mutually_exclusive_group()
    criteria.add_argument("--criteria", default="", help="Fixed acceptance criteria")
    criteria.add_argument("--criteria-file", type=Path, help="UTF-8 acceptance criteria file")
    result.add_argument("--codex-model", required=True, help="Explicit Codex model identifier")
    result.add_argument("--claude-model", required=True, help="Explicit Claude model identifier")
    result.add_argument("--codex-backend", choices=["native", "copilot"], default="native",
                        help="Run the Codex reviewer through Codex CLI or GitHub Copilot")
    result.add_argument("--claude-backend", choices=["native", "copilot"], default="native",
                        help="Run the Claude reviewer through Claude Code or GitHub Copilot")
    result.add_argument("--copilot-bin", default="copilot", help="GitHub Copilot CLI executable path")
    result.add_argument("--codex-effort", choices=tuple(dict.fromkeys(CODEX_EFFORTS + COPILOT_EFFORTS)),
                        help="Requested Codex reasoning effort; omit to use CLI defaults")
    result.add_argument("--claude-effort", choices=tuple(dict.fromkeys(CLAUDE_EFFORTS + COPILOT_EFFORTS)),
                        help="Requested Claude effort; omit to use CLI defaults")
    result.add_argument("--start", choices=["codex", "claude"], default="codex")
    result.add_argument("--max-turns", type=int, default=12, help="Accepted review turns (default: 12)")
    result.add_argument("--max-attempts", type=int, default=3, help="CLI attempts per turn (default: 3)")
    result.add_argument("--timeout", type=float, default=600, help="Seconds per CLI call (default: 600)")
    result.add_argument("--max-seconds", type=float, default=7200, help="Run deadline (default: 7200 seconds)")
    result.add_argument("--claude-max-turns", type=int,
                        help=f"Internal Claude turns per call (default: {DEFAULT_CLAUDE_MAX_TURNS}; "
                             "native Claude only)")
    result.add_argument("--claude-max-budget-usd", type=float, help="Claude per-call API budget")
    result.add_argument("--max-output-mb", type=int, default=32,
                        help="MiB per captured CLI output stream (default: 32)")
    result.add_argument("--max-run-mb", type=int, default=256,
                        help="MiB per run audit directory, including CLI logs and caches (default: 256)")
    result.add_argument("--skip-git-repo-check", action="store_true")
    result.add_argument("--allow-untested-cli", action="store_true",
                        help="Run CLI versions outside the tested ranges as experimental; "
                             "inspection-control probes still apply")
    result.add_argument("--codex-bin", default="codex", help="Codex executable path")
    result.add_argument("--claude-bin", default="claude", help="Claude executable path")
    result.add_argument("--state-dir", type=Path, help="Audit/lock directory outside target repository")
    result.add_argument("--json", action="store_true", help="Print a JSON summary to stdout")
    result.add_argument("--show-conversation", action="store_true",
                        help="Show completed reviews and plan changes on stderr")
    return result


def emit(summary: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary))
    else:
        print(f"{summary['status']}: {summary['message']}")
        if summary.get("latest_review"):
            review = summary["latest_review"]
            label = review['agent'] + (" via Copilot" if review.get("backend") == "copilot" else "")
            print(f"Latest review: {label} [{review['decision']}]: {review['reason']}")
        if "turns" in summary:
            print(f"{summary['turns']} review turns, {summary['attempts']} CLI attempts")
        if summary.get("untested_cli"):
            names = ", ".join(f"{agent} ({version})" for agent, version in summary["untested_cli"].items())
            print(f"Experimental: untested CLI versions: {names}")
        if summary.get("run_dir"):
            print(f"Audit: {summary['run_dir']}")


def show_review(number: int, review: Turn, before: str, after: str) -> None:
    details = ""
    if review["decision"] == "revise":
        if before.strip():
            details = "\n\nPlan diff:\n" + plan_diff(before, after)
        else:
            details = "\n\nInitial plan:\n" + after
    label = review['agent'] + (" via Copilot" if review.get("backend") == "copilot" else "")
    print(
        f"\n--- Turn {number}: {label} [{review['decision']}] ---\n"
        f"{review['reason']}{details}\n",
        file=sys.stderr, flush=True,
    )


def outcome(state: State) -> dict:
    latest = state["history"][-1] if state["history"] else None
    message = state["error"] or {
        "agreed": "Both reviewers approved the current plan",
        "blocked": "Review requires user input",
        "limit_reached": "Review budget exhausted",
        "error": "Review failed",
    }.get(state["status"], "Review stopped")
    return {"status": state["status"], "message": message,
            "latest_review": latest, "turns": state["turns"], "attempts": state["attempts"]}


def is_plan_file(path: Path, repo: Path) -> bool:
    plan = repo / "plan.md"
    try:
        return path.expanduser().samefile(plan)
    except OSError:
        # A missing file cannot be the same file; compare paths for clarity.
        return path.expanduser().resolve() == plan.resolve()


def terminate(signum, frame) -> None:
    raise Terminated(signum)


def main(argv: list[str] | None = None) -> int:
    """Run Duo; SIGTERM and SIGHUP stop it like Ctrl-C, including the reviewer CLI."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, terminate)
    try:
        return run(argv)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run(argv: list[str] | None) -> int:
    args = parser().parse_args(argv)
    holder: list[RunStore] = []
    try:
        return review(args, holder)
    finally:
        # Release the run-directory lock once every audit file is written.
        for store in holder:
            store.close()


def review(args: argparse.Namespace, holder: list[RunStore]) -> int:
    store = None
    latest_review = None

    def completed(number: int, review: Turn, before: str, after: str) -> None:
        nonlocal latest_review
        latest_review = review
        if args.show_conversation:
            show_review(number, review, before, after)

    try:
        if os.name != "posix":
            raise RunError("This version supports macOS and Linux")
        repo = args.repo.expanduser().resolve()
        if not repo.is_dir():
            raise RunError("Target repository directory does not exist")
        for option, path in (("--task-file", args.task_file), ("--criteria-file", args.criteria_file)):
            if path is not None and is_plan_file(path, repo):
                # The review would then check the plan against itself.
                raise RunError(f"{option} must not be the plan under review ({repo / 'plan.md'}); "
                               "describe the task separately")
        task = args.task_file.read_bytes().decode("utf-8") if args.task_file else args.task
        criteria = args.criteria_file.read_bytes().decode("utf-8") if args.criteria_file else args.criteria
        if not task.strip():
            raise ValueError("Task must not be empty")
        for name, text in (("Task", task), ("Criteria", criteria)):
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise RunError(f"{name} exceeds the {MAX_TEXT_BYTES // 1024} KiB limit")
        config = Config(
            repo=repo, codex_model=args.codex_model, claude_model=args.claude_model,
            codex_effort=args.codex_effort, claude_effort=args.claude_effort,
            codex_backend=args.codex_backend, claude_backend=args.claude_backend,
            copilot_bin=args.copilot_bin,
            start=args.start, max_turns=args.max_turns, max_attempts=args.max_attempts,
            timeout=args.timeout, max_seconds=args.max_seconds,
            claude_max_turns=args.claude_max_turns,
            claude_max_budget_usd=args.claude_max_budget_usd,
            skip_git_repo_check=args.skip_git_repo_check,
            allow_untested_cli=args.allow_untested_cli,
            max_output_mb=args.max_output_mb, max_run_mb=args.max_run_mb,
            codex_bin=args.codex_bin, claude_bin=args.claude_bin,
        )
        default_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "duo"
        state_dir = (args.state_dir or default_state).expanduser().resolve()
        if state_dir == repo or repo in state_dir.parents:
            raise RunError("--state-dir must be outside the target repository")
        with run_lock(repo):
            plan = PlanFile(repo)
            if len(plan.initial.encode("utf-8")) > MAX_TEXT_BYTES:
                raise RunError(f"plan.md exceeds the {MAX_TEXT_BYTES // 1024} KiB limit")
            store = RunStore(state_dir)
            holder.append(store)
            adapter = CLIAdapter(config, store)
            state = initial_state(task, criteria, plan.initial, config)
            store.json("manifest.json", {
                "started_at": timestamp(), "config": {**asdict(config), "repo": str(repo)},
                "task": task, "criteria": criteria,
            })
            store.snapshot(0, plan.initial)
            reviewer = Reviewer(config, adapter, plan, store,
                                progress=lambda message: print(message, file=sys.stderr, flush=True),
                                on_review=completed)
            graph = build_graph(reviewer)
            versions = adapter.preflight()
            if adapter.untested:
                versions = {**versions, "untested": sorted(adapter.untested)}
                names = ", ".join(f"{agent} ({version})" for agent, version in adapter.untested.items())
                print(f"Warning: running untested CLI versions as experimental: {names}",
                      file=sys.stderr, flush=True)
            store.json("versions.json", versions)
            result = graph.invoke(state, config={"recursion_limit": config.max_turns + 5})
            plan.check()
            store.json("result.json", {**result, "untested_cli": adapter.untested})
        emit({**outcome(result), "untested_cli": adapter.untested,
              "plan_path": str(plan.path), "run_dir": str(store.path)}, args.json)
        return EXIT_CODES[result["status"]]
    except KeyboardInterrupt as exc:
        # Shell convention: 128 + signal number (130 for Ctrl-C, 143 for SIGTERM).
        signum = exc.signum if isinstance(exc, Terminated) else signal.SIGINT
        name = signal.Signals(signum).name
        reason = "Interrupted" if signum == signal.SIGINT else f"Terminated by {name}"
        summary = {"status": "error", "message": f"{reason}; rerun to review the latest saved plan"}
        code = 128 + signum
    except Exception as exc:
        # Includes setup, LangGraph, and storage failures; never signal success.
        summary = {"status": "error", "message": str(exc)}
        code = 1
    summary["latest_review"] = latest_review
    if store is not None:
        summary["run_dir"] = str(store.path)
        try:
            store.json("error.json", summary)
        except OSError:
            pass
    emit(summary, args.json)
    return code

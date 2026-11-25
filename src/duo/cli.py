from __future__ import annotations

import argparse
from dataclasses import asdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time
import json
import os
from pathlib import Path
import signal
import sys
import threading

from .adapters import CLIAdapter
from .checkpoints import Journal, config_payload, load as load_checkpoint, read_record, remaining_seconds
from .diversity import classify_roster
from .models import (CLAUDE_EFFORTS, CODEX_EFFORTS, COPILOT_EFFORTS, DEFAULT_CLAUDE_MAX_TURNS,
                     DOCUMENT_MODES, DEFAULT_MAX_PROMPT_BYTES, MAX_TEXT_BYTES, Config, ReviewerSpec, RunError,
                     State, Terminated, Turn, new_secret, normalize)
from .prompts import plan_diff
from .report import write_report
from .storage import (PlanFile, RunStore, capture_context, load_blocked_source, read_input,
                      run_lock, timestamp, UserClarification)
from .verdicts import write_verdict
from .workflow import Reviewer, build_graph, initial_state

EXIT_CODES = {"agreed": 0, "error": 1, "blocked": 3, "limit_reached": 4}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Alternate Codex and Claude reviews of plan.md "
                                                 "or another document (--mode)",
                                     epilog="Stored runs: duo --render-report RUN_DIR "
                                            "[--report-output PATH] (no task or models required)")
    result.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository")
    task = result.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="Original task to plan")
    task.add_argument("--task-file", type=Path, help="UTF-8 task file")
    criteria = result.add_mutually_exclusive_group()
    criteria.add_argument("--criteria", default="", help="Fixed acceptance criteria")
    criteria.add_argument("--criteria-file", type=Path, help="UTF-8 acceptance criteria file")
    result.add_argument("--answer", help="User decision on a blocked run's question (requires --source-run)")
    result.add_argument("--source-run", type=Path,
                        help="Explicit stored blocked run to answer (requires --answer)")
    result.add_argument("--mode", choices=DOCUMENT_MODES, default="plan",
                        help="Document the reviewers produce (default: plan)")
    result.add_argument("--instructions-file", type=Path,
                        help="UTF-8 review checklist replacing the mode's own; the review "
                             "protocol itself stays fixed")
    result.add_argument("--context-file", type=Path, action="append", default=[],
                        help="Capture a UTF-8 context file (repeatable; 64 KiB aggregate)")
    result.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES,
                        help="Full UTF-8 prompt ceiling (default: 524288 bytes, "
                             "owner-ratified 2026-11-23)")
    result.add_argument("--output", type=Path,
                        help="Document path relative to --repo (default: <mode>.md)")
    result.add_argument("--codex-model", help="Explicit Codex model identifier")
    result.add_argument("--claude-model", help="Explicit Claude model identifier")
    result.add_argument("--reviewer", action="append", default=[], metavar="ID:KIND:BACKEND:MODEL[:EFFORT]",
                        help="Panel reviewer spec (repeatable) in roster order")
    result.add_argument("--codex-backend", choices=["native", "copilot"],
                        help="Run the Codex reviewer through Codex CLI or GitHub Copilot")
    result.add_argument("--claude-backend", choices=["native", "copilot"],
                        help="Run the Claude reviewer through Claude Code or GitHub Copilot")
    result.add_argument("--copilot-bin", default="copilot", help="GitHub Copilot CLI executable path")
    result.add_argument("--codex-effort", choices=tuple(dict.fromkeys(CODEX_EFFORTS + COPILOT_EFFORTS)),
                        help="Requested Codex reasoning effort; omit to use CLI defaults")
    result.add_argument("--claude-effort", choices=tuple(dict.fromkeys(CLAUDE_EFFORTS + COPILOT_EFFORTS)),
                        help="Requested Claude effort; omit to use CLI defaults")
    result.add_argument("--start", default="codex")
    result.add_argument("--max-turns", type=int, default=12, help="Accepted review turns (default: 12)")
    result.add_argument("--max-attempts", type=int, default=3, help="CLI attempts per turn (default: 3)")
    result.add_argument("--timeout", type=float, default=600, help="Seconds per CLI call (default: 600)")
    result.add_argument("--max-seconds", type=float, default=7200, help="Run deadline (default: 7200 seconds)")
    result.add_argument("--claude-max-turns", type=int,
                        help=f"Internal Claude turns per call (default: {DEFAULT_CLAUDE_MAX_TURNS}; "
                             "native Claude only)")
    result.add_argument("--claude-max-budget-usd", type=float, help="Claude per-call API budget")
    result.add_argument("--pass-env", action="append", default=[], metavar="NAME",
                        help="Also pass this environment variable to reviewer CLIs (repeatable); "
                             "by default they get only what they need to run and log in")
    result.add_argument("--allow-web", action="store_true",
                        help="Experimental: give reviewers web search and page fetching "
                             "(Copilot: fetching only); see docs/SECURITY.md before using it")
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


def parse_reviewer(spec: str) -> ReviewerSpec:
    parts = spec.split(":")
    if len(parts) not in (4, 5) or any(part == "" for part in parts):
        raise RunError(
            "Invalid --reviewer spec; expected ID:KIND:BACKEND:MODEL or ID:KIND:BACKEND:MODEL:EFFORT")
    ident, kind, backend, model, *rest = parts
    return ReviewerSpec(ident, kind, backend, model, rest[0] if rest else None)


def diversity_summary(config: Config, *, as_json: bool) -> dict:
    diversity = classify_roster(config.roster())
    for warning in diversity["warnings"]:
        if not as_json:
            print(f"Warning: {warning}", file=sys.stderr, flush=True)
    return {"diversity": diversity, "warnings": diversity["warnings"]}


def show_review(number: int, review: Turn, before: str, after: str, document: str = "plan") -> None:
    details = ""
    if review["decision"] == "revise":
        if before.strip():
            details = f"\n\n{document.capitalize()} diff:\n" + plan_diff(before, after)
        else:
            details = f"\n\nInitial {document}:\n" + after
    label = review['agent'] + (" via Copilot" if review.get("backend") == "copilot" else "")
    print(
        f"\n--- Turn {number}: {label} [{review['decision']}] ---\n"
        f"{review['reason']}{details}\n",
        file=sys.stderr, flush=True,
    )


def outcome(state: State, document: str = "plan", reviewers: int = 2) -> dict:
    latest = state["history"][-1] if state["history"] else None
    agreed = (f"Both reviewers approved the current {document}" if reviewers == 2
              else f"All {reviewers} reviewers approved the current {document}")
    message = state["error"] or {
        "agreed": agreed,
        "blocked": "Review requires user input",
        "limit_reached": "Review budget exhausted",
        "error": "Review failed",
    }.get(state["status"], "Review stopped")
    return {"status": state["status"], "message": message,
            "latest_review": latest, "turns": state["turns"], "attempts": state["attempts"]}


def is_plan_file(path: Path, plan: Path) -> bool:
    try:
        return path.expanduser().samefile(plan)
    except OSError:
        # A missing file cannot be the same file; compare paths for clarity.
        return path.expanduser().resolve() == plan.resolve()


def output_path(output: Path | None, mode: str, repo: Path) -> Path:
    """The document path: <mode>.md, or --output, which must stay inside the repository."""
    if output is None:
        return repo / f"{mode}.md"
    if "\\" in str(output):
        raise RunError("--output must not contain a backslash; use forward slashes for repository-relative paths")
    if output.is_absolute() or not output.name:
        raise RunError("--output must be a file path relative to --repo")
    path = repo / output
    # Resolve the directory, not the file: a symlinked document is rejected later.
    parent = path.parent.resolve()
    if parent != repo and repo not in parent.parents:
        raise RunError("--output must stay inside --repo")
    document = parent / path.name
    # Checked by name before anything about the file system: macOS file systems
    # usually ignore case (.GIT is .git), while on Linux .GIT may not exist.
    if any(part.lower() == ".git" for part in document.relative_to(repo).parts):
        raise RunError("--output must not be inside .git")
    if not parent.is_dir():
        raise RunError(f"--output directory does not exist: {path.parent}")
    if document.is_dir():
        raise RunError(f"--output is a directory; name a file inside it: {output}")
    return document


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
    argv = sys.argv[1:] if argv is None else argv
    if any(arg == "--render-report" or arg.startswith("--render-report=") for arg in argv):
        report_parser = argparse.ArgumentParser(description="Render a stored Duo run as offline HTML")
        report_parser.add_argument("--render-report", type=Path, required=True, metavar="RUN_DIR")
        report_parser.add_argument("--report-output", type=Path, metavar="PATH",
                                   help="Destination (default: RUN_DIR/report.html)")
        args = report_parser.parse_args(argv)
        try:
            print(write_report(args.render_report, args.report_output))
            return 0
        except (OSError, ValueError, RunError) as exc:
            print(f"Could not render report: {exc}", file=sys.stderr)
            return 1
    resume_mode = any(arg == "--resume" or arg.startswith("--resume=") for arg in argv)
    if resume_mode:
        resume_parser = argparse.ArgumentParser(description="Resume an interrupted Duo run")
        resume_parser.add_argument("--resume", type=Path, required=True, metavar="RUN_PATH")
        resume_parser.add_argument("--allow-untested-cli", action="store_true")
        resume_parser.add_argument("--json", action="store_true")
        resume_parser.add_argument("--show-conversation", action="store_true")
        args = resume_parser.parse_args(argv)
    else:
        args = parser().parse_args(argv)
    holder: list[RunStore] = []
    try:
        return resume(args, holder) if resume_mode else review(args, holder)
    finally:
        # Release the run-directory lock once every audit file is written.
        for store in holder:
            try:
                if (store.path / "manifest.json").exists():
                    write_report(store.path)
            except BaseException as exc:
                # Reporting must never replace the review outcome or its artifacts.
                print(f"Warning: could not write HTML report: {exc}", file=sys.stderr)
            finally:
                store.close()


def review(args: argparse.Namespace, holder: list[RunStore]) -> int:
    store = None
    journal = None
    interrupted = False
    started = datetime.now(timezone.utc)
    latest_review = None
    diversity = {"diversity": {}, "warnings": []}

    def completed(number: int, review: Turn, before: str, after: str) -> None:
        nonlocal latest_review
        latest_review = review
        if args.show_conversation:
            show_review(number, review, before, after, args.mode)

    try:
        if (args.answer is None) != (args.source_run is None):
            raise RunError("--answer and --source-run must be supplied together")
        if args.answer is not None:
            if not args.answer.strip():
                raise RunError("--answer must not be empty")
            if len(args.answer.encode("utf-8")) > MAX_TEXT_BYTES:
                raise RunError(f"Answer exceeds the {MAX_TEXT_BYTES // 1024} KiB limit")
        if os.name != "posix":
            raise RunError("This version supports macOS and Linux")
        repo = args.repo.expanduser().resolve()
        if not repo.is_dir():
            raise RunError("Target repository directory does not exist")
        document = output_path(args.output, args.mode, repo)
        for option, path in (("--task-file", args.task_file), ("--criteria-file", args.criteria_file),
                             ("--instructions-file", args.instructions_file),
                             *(("--context-file", path) for path in args.context_file)):
            if path is not None and is_plan_file(path, document):
                # The review would then check the document against itself.
                raise RunError(f"{option} must not be the {args.mode} under review ({document}); "
                               "describe the task separately")
        task = read_input(args.task_file, "Task").decode("utf-8") if args.task_file else args.task
        criteria = read_input(args.criteria_file, "Criteria").decode("utf-8") if args.criteria_file else args.criteria
        guidance = read_input(args.instructions_file, "Instructions").decode("utf-8") if args.instructions_file else ""
        if not task.strip():
            raise ValueError("Task must not be empty")
        if args.instructions_file and not guidance.strip():
            raise ValueError("Instructions file must not be empty")
        for name, text in (("Task", task), ("Criteria", criteria), ("Instructions", guidance)):
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise RunError(f"{name} exceeds the {MAX_TEXT_BYTES // 1024} KiB limit")
        context = capture_context(args.context_file)
        if args.reviewer:
            legacy = ("--codex-model", args.codex_model), ("--claude-model", args.claude_model), (
                "--codex-backend", args.codex_backend), ("--claude-backend", args.claude_backend), (
                "--codex-effort", args.codex_effort), ("--claude-effort", args.claude_effort)
            conflict = [name for name, value in legacy if value is not None]
            if conflict:
                raise RunError(f"--reviewer cannot be combined with {', '.join(conflict)}")
            reviewers = tuple(parse_reviewer(spec) for spec in args.reviewer)
            if len(reviewers) < 2:
                raise RunError("--reviewer must be provided at least twice")
            config = Config(
                repo=repo, codex_model="", claude_model="", reviewers=reviewers,
                codex_backend="native", claude_backend="native",
                copilot_bin=args.copilot_bin,
                start=args.start, max_turns=args.max_turns, max_attempts=args.max_attempts,
                timeout=args.timeout, max_seconds=args.max_seconds,
                claude_max_turns=args.claude_max_turns,
                claude_max_budget_usd=args.claude_max_budget_usd,
                skip_git_repo_check=args.skip_git_repo_check,
                allow_untested_cli=args.allow_untested_cli,
                max_output_mb=args.max_output_mb, max_run_mb=args.max_run_mb,
                codex_bin=args.codex_bin, claude_bin=args.claude_bin,
                mode=args.mode, guidance=guidance, output=str(document.relative_to(repo)),
                pass_env=tuple(args.pass_env), allow_web=args.allow_web,
                max_prompt_bytes=args.max_prompt_bytes,
            )
        else:
            if args.codex_model is None or args.claude_model is None:
                raise RunError("--codex-model and --claude-model are required unless --reviewer is used")
            config = Config(
                repo=repo, codex_model=args.codex_model, claude_model=args.claude_model,
                codex_effort=args.codex_effort, claude_effort=args.claude_effort,
                codex_backend=args.codex_backend or "native", claude_backend=args.claude_backend or "native",
                copilot_bin=args.copilot_bin,
                start=args.start, max_turns=args.max_turns, max_attempts=args.max_attempts,
                timeout=args.timeout, max_seconds=args.max_seconds,
                claude_max_turns=args.claude_max_turns,
                claude_max_budget_usd=args.claude_max_budget_usd,
                skip_git_repo_check=args.skip_git_repo_check,
                allow_untested_cli=args.allow_untested_cli,
                max_output_mb=args.max_output_mb, max_run_mb=args.max_run_mb,
                codex_bin=args.codex_bin, claude_bin=args.claude_bin,
                mode=args.mode, guidance=guidance, output=str(document.relative_to(repo)),
                pass_env=tuple(args.pass_env), allow_web=args.allow_web,
                max_prompt_bytes=args.max_prompt_bytes,
            )
        diversity = diversity_summary(config, as_json=args.json)
        default_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "duo"
        state_dir = (args.state_dir or default_state).expanduser().resolve()
        if state_dir == repo or repo in state_dir.parents:
            raise RunError("--state-dir must be outside the target repository")
        clarification = None
        if args.answer is not None:
            plan = PlanFile(document.parent, document.name)
            clarification = load_blocked_source(
                args.source_run, repo=repo, document=document, mode=config.mode,
                task=task, criteria=criteria, current_plan=plan.initial, answer=args.answer)
            source = Path(clarification.source_run)
            if (document.is_relative_to(source) or state_dir.is_relative_to(source)
                    or (state_dir / "runs").resolve().is_relative_to(source)):
                raise RunError("Source run must not contain the current document or new audit output")
            known = normalize(task) + "\n" + normalize(criteria) + "\n" + plan.initial
            kind = new_secret(normalize(clarification.question), known)
            if kind:
                raise RunError(f"Source run blocked question appears to contain a credential ({kind}); remove it")
            kind = new_secret(normalize(args.answer), known)
            if kind:
                raise RunError(f"Answer appears to contain a credential ({kind}); remove it")
        with run_lock(repo):
            if clarification is None:
                plan = PlanFile(document.parent, document.name)
            else:
                plan.check()  # Bind the recorded current hash to the document at startup.
            store = RunStore(state_dir)
            holder.append(store)
            state = initial_state(task, criteria, plan.initial, config)
            manifest = {
                "started_at": timestamp(), "config": config_payload(config),
                "task": task, "criteria": criteria, "document": str(document),
                **({"answer": asdict(clarification)} if clarification is not None else {}),
            }
            store.json("manifest.json", manifest)
            store.snapshot(0, plan.initial)
            store.context(context)
            deadline = started + timedelta(seconds=config.max_seconds)
            journal = Journal.create(store, config, state, manifest,
                                     read_record(store.path / "context.json"),
                                     started.isoformat(), deadline.isoformat())
            journal.write()
            store.json("state.json", state)
            adapter = CLIAdapter(config, store)
            reviewer = Reviewer(config, adapter, plan, store,
                                progress=lambda message: print(message, file=sys.stderr, flush=True),
                                on_review=completed, context=context,
                                user_clarification=clarification, checkpoints=journal,
                                absolute_deadline=time.monotonic() + max(0, (
                                    deadline - datetime.now(timezone.utc)).total_seconds()))
            reviewer.check_startup_budget(state)
            graph = build_graph(reviewer)
            versions = adapter.preflight()
            if adapter.untested:
                versions = {**versions, "untested": sorted(adapter.untested)}
                names = ", ".join(f"{agent} ({version})" for agent, version in adapter.untested.items())
                print(f"Warning: running untested CLI versions as experimental: {names}",
                      file=sys.stderr, flush=True)
            store.json("versions.json", versions)
            if config.allow_web:
                print("Warning: --allow-web gives reviewers web access; what they read can "
                      "reach the web through their queries and fetched URLs",
                      file=sys.stderr, flush=True)
            result = graph.invoke(state, config={"recursion_limit": config.max_turns + 5})
            plan.check()
            journal.terminal(result)
            store.json("result.json", {**result, "untested_cli": adapter.untested})
            write_verdict(store.path)
        emit({**outcome(result, args.mode, len(config.roster())), "untested_cli": adapter.untested,
              **diversity,
              "plan_path": str(plan.path), "run_dir": str(store.path)}, args.json)
        return EXIT_CODES[result["status"]]
    except KeyboardInterrupt as exc:
        # Shell convention: 128 + signal number (130 for Ctrl-C, 143 for SIGTERM).
        signum = exc.signum if isinstance(exc, Terminated) else signal.SIGINT
        name = signal.Signals(signum).name
        reason = "Interrupted" if signum == signal.SIGINT else f"Terminated by {name}"
        interrupted = journal is not None
        message = (f"{reason}; run was interrupted and can be resumed with --resume {store.path}"
                   if interrupted else f"{reason}; rerun to review the latest saved {args.mode}")
        summary = {"status": "error", "message": message}
        code = 128 + signum
    except Exception as exc:
        # Includes setup, LangGraph, and storage failures; never signal success.
        summary = {"status": "error", "message": str(exc)}
        code = 1
    summary["latest_review"] = latest_review
    summary.update(diversity)
    if store is not None:
        summary["run_dir"] = str(store.path)
        try:
            if interrupted:
                journal.interrupt()
            else:
                if journal:
                    failed = deepcopy(journal.value["state"])
                    failed["attempts"] += journal.value["attempts_current_turn"]
                    failed.update(status="error", error=summary["message"])
                    journal.terminal(failed)
                store.json("error.json", summary)
        except (OSError, RunError):
            pass
        try:
            write_verdict(store.path)
        except BaseException:
            pass  # Best effort, like report writing; preserve the original failure.
    emit(summary, args.json)
    return code


def resume(args: argparse.Namespace, holder: list[RunStore]) -> int:
    store = None
    journal = None
    active = False
    invoking = False
    diversity = {"diversity": {}, "warnings": []}
    try:
        store = RunStore.open_existing(args.resume)
        # Do not rewrite even reports on a refused recovery.
        value, config = load_checkpoint(store.path)
        diversity = diversity_summary(config, as_json=args.json)
        if value["status"] != "running":
            hint = "; use --answer with --source-run instead" if value["status"] == "blocked" else ""
            raise RunError(f"Cannot resume terminal run ({value['status']}){hint}")
        if config.allow_untested_cli != args.allow_untested_cli:
            raise RunError("--allow-untested-cli mismatch: changed security settings require a new run")
        remaining = remaining_seconds(value, datetime.now(timezone.utc))
        with run_lock(config.repo):
            document = output_path(Path(config.output), config.mode, config.repo)
            if str(document) != value["document"]:
                raise RunError("Checkpoint document identity changed")
            journal = Journal(store, value)
            state, context, manifest = journal.recover(config)
            plan = PlanFile(document.parent, document.name)
            if plan.initial != state["plan"]:
                raise RunError("document changed outside Duo")
            holder.append(store)
            active = True
            # Recovery may itself complete a terminal pending transition.
            remaining = min(remaining, remaining_seconds(journal.value, datetime.now(timezone.utc)))
            if (state["status"] != "running" or remaining <= 0 or state["turns"] >= config.max_turns
                    or journal.value["attempts_current_turn"] >= config.max_attempts):
                if state["status"] == "running":
                    state["attempts"] += journal.value["attempts_current_turn"]
                    state.update(status="limit_reached",
                                 error="Original run deadline, turn limit, or attempt limit reached")
                journal.terminal(state)
                store.json("result.json", {**state, "untested_cli": {}})
                write_verdict(store.path)
                emit({**outcome(state, config.mode, len(config.roster())), **diversity,
                      "run_dir": str(store.path)}, args.json)
                return EXIT_CODES[state["status"]]
            adapter = CLIAdapter(config, store)
            versions = adapter.preflight()
            if adapter.untested:
                versions = {**versions, "untested": sorted(adapter.untested)}
            store.json(f"versions-resume-{value['segment'] + 1}.json", versions)
            journal.start_segment(state, manifest)
            clarification = UserClarification(**manifest["answer"]) if "answer" in manifest else None
            reviewer = Reviewer(
                config, adapter, plan, store, checkpoints=journal, context=context,
                user_clarification=clarification,
                turn_attempt_offset=journal.value["attempts_current_turn"],
                absolute_deadline=time.monotonic() + max(0, min(remaining,
                    remaining_seconds(journal.value, datetime.now(timezone.utc)))),
                progress=lambda message: print(message, file=sys.stderr, flush=True),
                on_review=(lambda n, t, b, a: show_review(n, t, b, a, config.mode))
                if args.show_conversation else None)
            reviewer.check_startup_budget(state)
            graph = build_graph(reviewer)
            invoking = True
            result = graph.invoke(state, config={"recursion_limit": config.max_turns + 5})
            plan.check()
            journal.terminal(result)
            store.json("result.json", {**result, "untested_cli": adapter.untested})
            write_verdict(store.path)
            emit({**outcome(result, config.mode, len(config.roster())), "untested_cli": adapter.untested,
                  **diversity,
                  "plan_path": str(plan.path), "run_dir": str(store.path)}, args.json)
            return EXIT_CODES[result["status"]]
    except KeyboardInterrupt as exc:
        signum = exc.signum if isinstance(exc, Terminated) else signal.SIGINT
        summary = {"status": "error", "message":
                   f"Interrupted; run can be resumed with --resume {args.resume}"}
        if journal and active:
            try:
                journal.interrupt()
            except (OSError, RunError):
                pass
            try:
                write_verdict(store.path)
            except BaseException:
                pass
        code = 128 + signum
    except Exception as exc:
        summary = {"status": "error", "message": str(exc)}
        code = 1
        if journal and invoking:
            try:
                failed = deepcopy(journal.value["state"])
                failed["attempts"] += journal.value["attempts_current_turn"]
                failed.update(status="error", error=str(exc))
                journal.terminal(failed)
                store.json("error.json", summary)
            except (OSError, RunError):
                pass
            try:
                write_verdict(store.path)
            except BaseException:
                pass
    finally:
        if store and store not in holder:
            store.close()
    if store:
        summary["run_dir"] = str(store.path)
    summary.update(diversity)
    emit(summary, args.json)
    return code

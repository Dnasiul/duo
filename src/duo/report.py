"""Offline HTML reports from structured audit records, including Duo 0.5.0 runs."""
from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import re
import stat

from .models import RunError
from .prompts import plan_diff
from .storage import atomic_write, content_hash


STYLE = """
body { margin: 2rem auto; padding: 0 1rem; max-width: 72rem;
       color: #202b36; background: #f7f8fa; font: 1rem/1.6 system-ui, sans-serif; }
header, section, article { background: white; border: 1px solid #d7dde4;
                          border-radius: .5rem; padding: 1.2rem; margin: 1rem 0; }
h1, h2, h3 { line-height: 1.3; }
dl { display: grid; grid-template-columns: minmax(8rem, 1fr) 3fr; gap: .3rem 1rem; }
dt { font-weight: bold; } dd { margin: 0; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f2f4f7;
      padding: 1rem; font-size: .9rem; }
summary { cursor: pointer; font-weight: bold; }
code { overflow-wrap: anywhere; }
@media print { body { max-width: none; background: white; } }
"""


def _read(path: Path) -> str:
    # Record names are fixed locally, never taken from manifest/source paths.
    # Reject links and special files rather than following them outside the audit.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RunError(f"{path.name} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read().decode("utf-8")
    finally:
        os.close(descriptor)


def _json(path: Path, expected: type, *, optional: bool = False):
    try:
        value = json.loads(_read(path))
    except FileNotFoundError:
        if optional:
            return expected()
        raise RunError(f"Missing run record: {path.name}") from None
    except (ValueError, RecursionError) as exc:
        raise RunError(f"Invalid run record: {path.name}: {exc}") from exc
    if not isinstance(value, expected):
        raise RunError(f"Invalid run record: {path.name} must contain a {expected.__name__}")
    return value


def _text(value: object) -> str:
    """All record-derived output goes through this single escaping boundary."""
    return escape(str(value), quote=True)


def _fields(items: list[tuple[str, object]]) -> str:
    return "<dl>" + "".join(f"<dt>{_text(k)}</dt><dd>{_text(v)}</dd>" for k, v in items) + "</dl>"


def render_report(run_dir: Path) -> str:
    """Load a stored run and return deterministic, self-contained HTML.

    Missing optional metadata and snapshots are supported. Required records
    that are missing or malformed raise RunError (I/O errors may raise OSError).
    Original documents, context contents, and raw CLI logs are never read.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise RunError(f"Run directory does not exist or is not a directory: {run_dir}")
    manifest = _json(run_dir / "manifest.json", dict)
    has_result = (run_dir / "result.json").exists()
    state_name = "result.json" if has_result else "state.json"
    no_saved_state = not has_result and not (run_dir / "state.json").exists()
    if no_saved_state:
        state = {"task": manifest.get("task", ""), "criteria": manifest.get("criteria", ""),
                 "plan": "", "status": "running", "history": [], "approved": [],
                 "turns": 0, "attempts": 0}
    else:
        state = _json(run_dir / state_name, dict)
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise RunError("Invalid manifest.json: config must be an object")
    for key, kind in (("plan", str), ("status", str), ("history", list),
                      ("approved", list), ("turns", int), ("attempts", int)):
        if not isinstance(state.get(key), kind):
            raise RunError(f"Invalid {state_name}: {key} must be a {kind.__name__}")
    history = state["history"]
    if any(not isinstance(turn, dict) or
           any(not isinstance(turn.get(key), str) for key in
               ("agent", "decision", "reason", "plan_hash", "timestamp")) or
           turn["decision"] not in ("approve", "revise", "blocked") for turn in history):
        raise RunError(f"Invalid {state_name}: malformed history turn")
    status = state["status"]
    if status not in ("agreed", "blocked", "limit_reached", "error", "running", "interrupted"):
        raise RunError(f"Invalid {state_name}: unknown status")
    versions = _json(run_dir / "versions.json", dict, optional=True)
    context = _json(run_dir / "context.json", list, optional=True)
    error = _json(run_dir / "error.json", dict, optional=True)
    if any(not isinstance(item, dict) for item in context):
        raise RunError("Invalid context.json: captures must be objects")
    if not has_result:
        if status == "running":
            status = "interrupted"
        elif error.get("status") == "error":
            status = "error"
    elif status == "running":
        raise RunError("Invalid result.json: run is still running")

    snapshots = {}
    by_hash = {}
    for path in sorted(run_dir.glob("plan-*.md")):
        if not re.fullmatch(r"plan-\d{3,}\.md", path.name):
            continue
        try:
            document = _read(path)
        except (OSError, UnicodeError, RunError):
            continue  # A missing/unreadable snapshot must not hide the conversation.
        snapshots[int(path.stem[5:])] = document
        by_hash[content_hash(document)] = document

    if no_saved_state:
        state["plan"] = snapshots.get(0, "")
    final_hash = content_hash(state["plan"])
    parts = ["<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             f"<title>Duo agreement report — {_text(run_dir.name)}</title>",
             f"<style>{STYLE}</style></head><body>",
             "<header><h1>Duo agreement report</h1>"]
    fields = [("Run", run_dir.name), ("Mode", config.get("mode", "plan")),
              ("Document", manifest.get("document", config.get("output", "Not recorded"))),
              ("Started", manifest.get("started_at", "Not recorded")),
              ("Final state", status), ("Turns", state["turns"]),
              ("Attempts", "Not recorded" if no_saved_state else state["attempts"])]
    if status == "agreed":
        fields.append(("Approved document SHA-256", final_hash))
    parts.append(_fields(fields))
    parts.append("<h2>Reviewers</h2>")
    untested = versions.get("untested", [])
    if not isinstance(untested, (list, dict)):
        raise RunError("Invalid versions.json: untested must be a list or object")
    reviewer_specs = {}
    reviewers = config.get("reviewers")
    if isinstance(reviewers, list) and reviewers:
        for item in reviewers:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise RunError("Invalid manifest.json: reviewers must contain objects with id")
            reviewer_specs[item["id"]] = item
            backend = item.get("backend", "native")
            kind = item.get("kind", "Not recorded")
            cli = "copilot" if backend == "copilot" else kind
            version = versions.get(cli, "Not recorded")
            if cli in untested:
                version = f"{version} (experimental: untested CLI)"
            parts.append(f"<h3>{_text(item['id'])}</h3>" + _fields([
                ("Model", item.get("model", "Not recorded")), ("Backend", backend),
                ("Effort", item.get("effort") or "CLI default"), ("CLI version", version)]))
    else:
        for agent in ("codex", "claude"):
            backend = config.get(f"{agent}_backend", "native")
            reviewer_specs[agent] = {"backend": backend, "model": config.get(f"{agent}_model", "Not recorded")}
            cli = "copilot" if backend == "copilot" else agent
            version = versions.get(cli, "Not recorded")
            if cli in untested:
                version = f"{version} (experimental: untested CLI)"
            parts.append(f"<h3>{_text(agent)}</h3>" + _fields([
                ("Model", config.get(f"{agent}_model", "Not recorded")), ("Backend", backend),
                ("Effort", config.get(f"{agent}_effort") or "CLI default"), ("CLI version", version)]))
    if context:
        parts.append("<h2>Context captures</h2>")
        for item in context:
            parts.append(_fields([(key, item.get(field, "Not recorded")) for key, field in
                                  (("Label", "label"), ("Bytes", "bytes"), ("SHA-256", "sha256"))]))
    parts.append("<h2>Task</h2><pre>" + _text(manifest.get("task", state.get("task", ""))) + "</pre>")
    criteria = manifest.get("criteria", state.get("criteria", ""))
    if criteria:
        parts.append("<h2>Criteria</h2><pre>" + _text(criteria) + "</pre>")
    parts.append("</header><section><h2>Conversation</h2>")
    before = snapshots.get(0)
    approvals = {}
    roster_ids = list(reviewer_specs) if reviewer_specs else ["codex", "claude"]
    for number, turn in enumerate(history, 1):
        agent = turn["agent"]
        fallback = reviewer_specs.get(agent, {})
        parts.append(f"<article><h3>Turn {number}: {_text(agent)} — {_text(turn['decision'])}</h3>")
        parts.append(_fields([
            ("Backend", turn.get("backend", fallback.get("backend", "Not recorded"))),
            ("Model", turn.get("model", fallback.get("model", "Not recorded"))),
            ("Timestamp", turn["timestamp"]), ("Document SHA-256", turn["plan_hash"])]))
        parts.append("<pre>" + _text(turn["reason"]) + "</pre>")
        after = snapshots.get(number)
        if after is None or content_hash(after) != turn["plan_hash"]:
            after = by_hash.get(turn["plan_hash"])
        if after is None and before is not None and content_hash(before) == turn["plan_hash"]:
            after = before
        if turn["decision"] == "revise":
            approvals.clear()
            parts.append("<details><summary>Document diff</summary>")
            if before is None or after is None:
                parts.append("<p>Diff unavailable: a matching document snapshot is missing or unreadable.</p>")
            else:
                parts.append("<pre>" + _text(plan_diff(before, after)) + "</pre>")
            parts.append("</details>")
        else:
            parts.append("<p>Document unchanged.</p>")
            if turn["decision"] == "approve":
                approvals[agent] = turn["plan_hash"]
        before = after
        parts.append("</article>")
    if not history:
        parts.append("<p>No completed review turns recorded.</p>")
    parts.append("</section><section><h2>Outcome</h2>")
    parts.append(_fields([("Final state", status), ("Approved agents", ", ".join(map(str, state["approved"])))]))
    if status == "agreed":
        if all(approvals.get(agent) == final_hash for agent in roster_ids):
            if len(roster_ids) == 2 and roster_ids == ["codex", "claude"]:
                parts.append("<p>Both reviewers approved the same unchanged document.</p>")
            else:
                parts.append(f"<p>All {len(roster_ids)} reviewers approved the same unchanged document.</p>")
            parts.append("<p>Shared approval plan_hash: <code>" + _text(final_hash) + "</code></p>")
        else:
            parts.append("<p>The record reports agreement, but the shared approval hash cannot be confirmed from its history.</p>")
    else:
        for message in (state.get("error"), error.get("message"),
                        history[-1]["reason"] if history else None):
            if message:
                parts.append("<pre>" + _text(message) + "</pre>")
    if no_saved_state and 0 not in snapshots:
        parts.append("<p>Initial document unavailable: plan-000.md is missing or unreadable.</p>")
    parts.append("<details><summary>Final recorded document</summary><pre>" +
                 _text(state["plan"]) + "</pre></details></section></body></html>\n")
    return "\n".join(parts)


def write_report(run_dir: Path, output: Path | None = None) -> Path:
    """Atomically write a private report.html (or an explicitly selected path)."""
    run_dir = Path(run_dir).expanduser().resolve()
    target = Path(output).expanduser().absolute() if output is not None else run_dir / "report.html"
    # A custom destination must not replace an existing audit artifact.
    if target.name != "report.html" and target.exists() and run_dir in target.resolve().parents:
        raise RunError("Report output must not overwrite an existing run artifact")
    html = render_report(run_dir)
    atomic_write(target, html.encode("utf-8"))
    return target

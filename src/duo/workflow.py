from __future__ import annotations

from copy import deepcopy
import time
from typing import Callable

from .models import (MAX_RETRY_AFTER, AgentError, AgentTimeout, Config, RunError, State, Turn,
                     validate_review)
from .prompts import build_prompt, check_prompt_budget, revision_diff
from .storage import ContextFile, PlanFile, RunStore, UserClarification, content_hash, timestamp, fsync_directory


def initial_state(task: str, criteria: str, plan: str, config: Config) -> State:
    if not task.strip():
        raise ValueError("Task must not be empty")
    return {
        "task": task, "criteria": criteria, "plan": plan, "approved": [],
        "next_agent": config.start, "turns": 0, "attempts": 0,
        "status": "running", "history": [], "findings": [], "last_diff": "", "error": "",
    }


def stage_findings(state: State, result: dict, agent: str, attempt: int,
                   path: str) -> tuple[list, list]:
    """Validate against the inspected text; never mutate the accepted ledger."""
    ledger = deepcopy(state["findings"])
    inspected_hash = content_hash(state["plan"])
    turn = state["turns"] + 1
    events = []

    def location(value):
        text = state["plan"]
        line_count = 0 if not text else text.count("\n") + (0 if text.endswith("\n") else 1)
        if value["kind"] == "lines" and value["end_line"] > line_count:
            raise AgentError("Finding location exceeds the inspected subject's line count")
        if value["kind"] == "section" and not any(
                line.rstrip() == value["section"].rstrip() for line in text.split("\n")):
            raise AgentError("Finding section must appear as a line of the inspected subject")
        return {**value, "path": path, "subject_sha256": inspected_hash}

    by_id = {item["id"]: item for item in ledger}
    updated = set()
    for update in result["finding_updates"]:
        ident = update["id"]
        if ident in updated:
            raise AgentError("Duplicate finding update ID")
        updated.add(ident)
        if ident not in by_id:
            raise AgentError("Unknown finding update ID")
        finding = by_id[ident]
        if finding["reported_by"] != agent:
            raise AgentError("You may only close your own findings")
        if finding["status"] != "open":
            raise AgentError("Finding update must reference an open finding")
        if not update["reason"].strip():
            raise AgentError("Finding update requires a nonempty reason")
        if update["subject_hash"] != inspected_hash:
            raise AgentError("Finding update subject_hash must match the inspected subject SHA-256")
        if "location" in update:
            moved = location(update["location"])
            if moved != finding["location"]:
                finding["location_history"].append({
                    # Later sightings do not relocate coordinates implicitly.
                    "subject_hash": finding["location"]["subject_sha256"],
                    "location": finding["location"],
                })
                finding["location"] = moved
        finding.update(status=update["status"], last_seen_subject_hash=inspected_hash,
                       disposition={"status": update["status"], "reason": update["reason"],
                                    "subject_hash": inspected_hash, "turn": turn, "by": agent})
        events.append({"event": "finding_updated", "id": ident, "agent": agent, "turn": turn})
    next_id = max((int(item["id"][1:]) for item in ledger), default=0) + 1
    for report in result["findings"]:
        ident = f"F{next_id}"
        next_id += 1
        ledger.append({**report, "id": ident, "location": location(report["location"]),
                       "status": "open", "reported_by": agent, "reported_turn": turn,
                       "reported_attempt": attempt, "first_seen_subject_hash": inspected_hash,
                       "last_seen_subject_hash": inspected_hash, "location_history": [],
                       "disposition": None})
        events.append({"event": "finding_reported", "id": ident, "agent": agent, "turn": turn})
    for finding in ledger:
        if finding["reported_by"] == agent and finding["status"] == "open":
            if result["decision"] == "approve":
                raise AgentError("Reconcile all your open findings: resolve or dismiss each "
                                 "against the current subject before approving")
            finding["last_seen_subject_hash"] = inspected_hash
    return ledger, events


class Reviewer:
    """A LangGraph node; the adapter and clock are injectable for offline tests."""

    def __init__(self, config: Config, adapter: Callable, plan: PlanFile, store: RunStore,
                 progress: Callable | None = None, clock: Callable = time.monotonic,
                 sleep: Callable = time.sleep,
                 on_review: Callable[[int, Turn, str, str], None] | None = None,
                 context: tuple[ContextFile, ...] = (),
                 user_clarification: UserClarification | None = None, *,
                 checkpoints=None, absolute_deadline: float | None = None,
                 turn_attempt_offset: int = 0):
        self.context = context
        self.user_clarification = user_clarification
        self.config, self.adapter, self.plan_file, self.store = config, adapter, plan, store
        self.progress, self.clock, self.sleep = progress, clock, sleep
        self.on_review = on_review
        self.deadline = clock() + config.max_seconds if absolute_deadline is None else absolute_deadline
        self.checkpoints = checkpoints
        self.turn_attempt_offset = turn_attempt_offset

    def prompt(self, state: State, feedback: str = "") -> tuple[str, dict[str, int]]:
        sizes: dict[str, int] = {}
        prompt = build_prompt(state, self.config.history_limit, feedback,
                              self.config.mode, self.config.guidance, self.config.output,
                              self.config.allow_web, self.context, sizes,
                              user_clarification=self.user_clarification,
                              panel=len(self.config.roster()) > 2)
        return prompt, sizes

    def check_startup_budget(self, state: State) -> None:
        prompt, sizes = self.prompt(state)
        backends = []
        for item in self.config.roster():
            if item.backend not in backends:
                backends.append(item.backend)
        for backend in backends:
            check_prompt_budget(prompt, self.config.max_prompt_bytes, backend, sizes)

    def __call__(self, state: State) -> State:
        current = dict(state)
        try:
            self._review(current)
        except (OSError, UnicodeError, RunError) as exc:
            current.update(status="error", error=f"Local run failure: {exc}")
        if self.checkpoints and current["status"] != "running":
            self.checkpoints.terminal(current)
        return current

    def _review(self, state: State) -> None:
        if state["status"] != "running":
            return
        if state["turns"] >= self.config.max_turns:
            state.update(status="limit_reached", error="Review turn limit reached")
            return
        self.plan_file.check()
        agent = state["next_agent"]
        spec = self.config.spec(agent)
        roster = self.config.roster_ids()
        order = {member: index for index, member in enumerate(roster)}
        feedback = ""
        offset, self.turn_attempt_offset = self.turn_attempt_offset, 0
        if self.checkpoints:
            last = self.checkpoints.value["last_attempt"]
            if last and last["turn"] == state["turns"] + 1:
                feedback = f"Previous attempt was {last['outcome']}: {last['error']}. Correct the response if necessary."
        if offset >= self.config.max_attempts:
            state.update(status="error", error="Review turn attempt limit exhausted by interrupted/failed attempts")
            return
        result = None
        for attempt in range(offset + 1, self.config.max_attempts + 1):
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                state.update(status="limit_reached", error="Run time limit reached")
                return
            self.plan_file.check()
            called = False
            # A timeout shortened by the deadline means the run ran out, not the CLI.
            capped = remaining < self.config.timeout
            try:
                prompt, sizes = self.prompt(state, feedback)
                check_prompt_budget(prompt, self.config.max_prompt_bytes,
                                    spec.backend, sizes)
                if self.checkpoints:
                    self.checkpoints.reserve(state, agent, attempt)
                state["attempts"] += 1
                called = True
                self.store.event({"event": "attempt", "agent": agent,
                                  "turn": state["turns"] + 1, "attempt": attempt})
                if self.progress:
                    self.progress(f"Turn {state['turns'] + 1}: {agent}, attempt {attempt}")
                payload = self.adapter(agent, prompt, min(self.config.timeout, remaining))
                self.plan_file.check()
                if self.clock() >= self.deadline:
                    state.update(status="limit_reached", error="Run time limit reached")
                    return
                result = validate_review(payload, state["plan"])
                findings, finding_events = stage_findings(
                    state, result, agent, attempt,
                    self.config.output or self.config.mode + ".md")
                if self.clock() >= self.deadline:
                    state.update(status="limit_reached", error="Run time limit reached")
                    return
                break
            except AgentError as exc:
                if self.checkpoints and called:
                    self.checkpoints.failed(str(exc))
                self.plan_file.check()
                self.store.event({"event": "attempt_failed" if called else "prompt_rejected",
                                  "agent": agent,
                                  "attempt": attempt, "error": str(exc)})
                remaining = self.deadline - self.clock()
                if remaining <= 0 and capped and isinstance(exc, AgentTimeout):
                    state.update(status="limit_reached", error="Run time limit reached")
                    return
                # Otherwise the attempt failed on its own; report that failure,
                # not the budget, even when no time is left to retry.
                if not exc.retryable or attempt == self.config.max_attempts:
                    state.update(status="error", error=f"{agent}: {exc}")
                    return
                if remaining <= 0:
                    state.update(status="error", error=f"{agent}: {exc} (no time left to retry)")
                    return
                if exc.retry_after is not None and exc.retry_after > MAX_RETRY_AFTER:
                    state.update(status="error", error=(
                        f"{agent}: {exc} (asked to retry after {exc.retry_after:.0f} s; "
                        f"Duo waits at most {MAX_RETRY_AFTER:.0f} s)"))
                    return
                delay = max(min(2 ** (attempt - 1), 8), exc.retry_after or 0)
                if delay >= remaining:
                    state.update(status="error", error=f"{agent}: {exc} (retry would exceed the run time limit)")
                    return
                self.sleep(delay)
                feedback = f"Previous attempt failed: {exc}. Correct the response if necessary."

        assert result is not None
        live_state = state
        if self.checkpoints:
            state = deepcopy(state)
        previous_plan = state["plan"]
        approved = set(state["approved"])
        if result["decision"] == "revise":
            revised = result["plan"]
            diff = revision_diff(state["plan"], revised, agent, result["reason"])
            # Keep the validated proposal even if plan publication fails.
            self.store.snapshot(state["turns"] + 1, revised)
            if not self.checkpoints:
                self.plan_file.write(revised)
            state.update(plan=revised, last_diff=diff, approved=[])
            approved.clear()
        elif result["decision"] == "approve":
            approved.add(agent)
        else:
            state["status"] = "blocked"

        state["findings"] = findings
        state["approved"] = sorted(approved)
        state["turns"] += 1
        state["next_agent"] = roster[(order[agent] + 1) % len(roster)]
        turn = {"agent": agent, "decision": result["decision"], "reason": result["reason"],
                "plan_hash": content_hash(state["plan"]), "timestamp": timestamp(),
                "backend": spec.backend, "model": spec.model}
        state["history"] = state["history"] + [turn]
        if state["status"] == "running":
            if approved == set(roster):
                state["status"] = "agreed"
            elif state["turns"] >= self.config.max_turns:
                state.update(status="limit_reached", error="Review turn limit reached")
        if self.checkpoints:
            self.checkpoints.intent(live_state, state, agent, result["decision"], finding_events)
            if result["decision"] == "revise":
                self.plan_file.write(state["plan"])
                fsync_directory(self.plan_file.path.parent)
        for event in finding_events:
            self.store.event(event)
        self.store.event({"event": "review", "turn": state["turns"], **turn})
        self.store.json("state.json", state)
        if self.checkpoints:
            self.checkpoints.commit(state)
            live_state.update(state)
        if self.on_review:
            self.on_review(state["turns"], turn, previous_plan, state["plan"])


def build_graph(reviewer: Reviewer):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RunError("LangGraph is unavailable. Install duo-plan with Python 3.11 or newer") from exc
    builder = StateGraph(State)
    builder.add_node("review", reviewer)
    builder.add_edge(START, "review")
    builder.add_conditional_edges(
        "review", lambda state: "review" if state["status"] == "running" else END,
        {"review": "review", END: END},
    )
    return builder.compile()

from __future__ import annotations

import time
from typing import Callable

from .models import AgentError, Config, RunError, State, Turn, validate_review
from .prompts import build_prompt, revision_diff
from .storage import PlanFile, RunStore, content_hash, timestamp


def initial_state(task: str, criteria: str, plan: str, config: Config) -> State:
    if not task.strip():
        raise ValueError("Task must not be empty")
    return {
        "task": task, "criteria": criteria, "plan": plan, "approved": [],
        "next_agent": config.start, "turns": 0, "attempts": 0,
        "status": "running", "history": [], "last_diff": "", "error": "",
    }


class Reviewer:
    """A LangGraph node; the adapter and clock are injectable for offline tests."""

    def __init__(self, config: Config, adapter: Callable, plan: PlanFile, store: RunStore,
                 progress: Callable | None = None, clock: Callable = time.monotonic,
                 sleep: Callable = time.sleep,
                 on_review: Callable[[int, Turn, str, str], None] | None = None):
        self.config, self.adapter, self.plan_file, self.store = config, adapter, plan, store
        self.progress, self.clock, self.sleep = progress, clock, sleep
        self.on_review = on_review
        self.deadline = clock() + config.max_seconds

    def __call__(self, state: State) -> State:
        current = dict(state)
        try:
            self._review(current)
        except (OSError, UnicodeError, RunError) as exc:
            current.update(status="error", error=f"Local run failure: {exc}")
        return current

    def _review(self, state: State) -> None:
        if state["status"] != "running":
            return
        if state["turns"] >= self.config.max_turns:
            state.update(status="limit_reached", error="Review turn limit reached")
            return
        self.plan_file.check()
        agent = state["next_agent"]
        feedback = ""
        result = None
        for attempt in range(1, self.config.max_attempts + 1):
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                state.update(status="limit_reached", error="Run time limit reached")
                return
            self.plan_file.check()
            state["attempts"] += 1
            self.store.event({"event": "attempt", "agent": agent,
                              "turn": state["turns"] + 1, "attempt": attempt})
            if self.progress:
                self.progress(f"Turn {state['turns'] + 1}: {agent}, attempt {attempt}")
            try:
                payload = self.adapter(
                    agent, build_prompt(state, self.config.history_limit, feedback),
                    min(self.config.timeout, remaining),
                )
                self.plan_file.check()
                if self.clock() >= self.deadline:
                    state.update(status="limit_reached", error="Run time limit reached")
                    return
                result = validate_review(payload, state["plan"])
                break
            except AgentError as exc:
                self.plan_file.check()
                self.store.event({"event": "attempt_failed", "agent": agent,
                                  "attempt": attempt, "error": str(exc)})
                remaining = self.deadline - self.clock()
                if remaining <= 0:
                    state.update(status="limit_reached", error="Run time limit reached")
                    return
                if not exc.retryable or attempt == self.config.max_attempts:
                    state.update(status="error", error=f"{agent}: {exc}")
                    return
                delay = max(min(2 ** (attempt - 1), 8), exc.retry_after or 0)
                if delay >= remaining:
                    state.update(status="limit_reached", error="Retry would exceed run time limit")
                    return
                self.sleep(delay)
                feedback = f"Previous attempt failed: {exc}. Correct the response if necessary."

        assert result is not None
        previous_plan = state["plan"]
        approved = set(state["approved"])
        if result["decision"] == "revise":
            revised = result["plan"]
            diff = revision_diff(state["plan"], revised, agent, result["reason"])
            # Keep the validated proposal even if plan publication fails.
            self.store.snapshot(state["turns"] + 1, revised)
            self.plan_file.write(revised)
            state.update(plan=revised, last_diff=diff, approved=[])
            approved.clear()
        elif result["decision"] == "approve":
            approved.add(agent)
        else:
            state["status"] = "blocked"

        state["approved"] = sorted(approved)
        state["turns"] += 1
        state["next_agent"] = "claude" if agent == "codex" else "codex"
        turn = {"agent": agent, "decision": result["decision"], "reason": result["reason"],
                "plan_hash": content_hash(state["plan"]), "timestamp": timestamp(),
                "backend": getattr(self.config, f"{agent}_backend"),
                "model": getattr(self.config, f"{agent}_model")}
        state["history"] = state["history"] + [turn]
        if state["status"] == "running":
            if approved == {"codex", "claude"}:
                state["status"] = "agreed"
            elif state["turns"] >= self.config.max_turns:
                state.update(status="limit_reached", error="Review turn limit reached")
        self.store.event({"event": "review", "turn": state["turns"], **turn})
        self.store.json("state.json", state)
        if self.on_review:
            self.on_review(state["turns"], turn, previous_plan, state["plan"])


def build_graph(reviewer: Reviewer):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RunError("LangGraph is unavailable. Install duo-plan with Python 3.10 or newer") from exc
    builder = StateGraph(State)
    builder.add_node("review", reviewer)
    builder.add_edge(START, "review")
    builder.add_conditional_edges(
        "review", lambda state: "review" if state["status"] == "running" else END,
        {"review": "review", END: END},
    )
    return builder.compile()

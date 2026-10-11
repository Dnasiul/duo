from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Literal, TypedDict

Agent = Literal["codex", "claude"]
AGENTS = {"codex", "claude"}
CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
COPILOT_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "revise", "blocked"]},
        "plan": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "plan", "reason"],
    "additionalProperties": False,
}


class AgentError(Exception):
    """An error safe to report to the user and include in retry feedback."""

    def __init__(self, message: str, *, retryable: bool = True,
                 retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class RunError(Exception):
    """A local setup, storage, or conflicting-edit failure."""


class Turn(TypedDict):
    agent: str
    decision: str
    reason: str
    plan_hash: str
    timestamp: str
    backend: str
    model: str


class State(TypedDict):
    task: str
    criteria: str
    plan: str
    approved: list[str]
    next_agent: Agent
    turns: int
    attempts: int
    status: str
    history: list[Turn]
    last_diff: str
    error: str


@dataclass(frozen=True)
class Config:
    repo: Path
    codex_model: str
    claude_model: str
    start: Agent = "codex"
    max_turns: int = 12
    max_attempts: int = 3
    timeout: float = 600
    max_seconds: float = 7200
    history_limit: int = 6
    claude_max_turns: int = 20
    claude_max_budget_usd: float | None = None
    skip_git_repo_check: bool = False
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    codex_effort: str | None = None
    claude_effort: str | None = None
    codex_backend: str = "native"
    claude_backend: str = "native"
    copilot_bin: str = "copilot"

    def __post_init__(self):
        if self.start not in AGENTS:
            raise ValueError("Starting agent must be codex or claude")
        if not self.codex_model.strip() or not self.claude_model.strip():
            raise ValueError("Both model identifiers must be nonempty")
        for name, choices in (("codex_effort", CODEX_EFFORTS), ("claude_effort", CLAUDE_EFFORTS)):
            agent = name.split("_")[0]
            backend = getattr(self, f"{agent}_backend")
            if backend not in ("native", "copilot"):
                raise ValueError(f"{agent}_backend must be native or copilot")
            if backend == "copilot":
                choices = COPILOT_EFFORTS
                if getattr(self, f"{agent}_model").lower() == "auto":
                    raise ValueError("Copilot reviewers require an explicit model, not auto")
            value = getattr(self, name)
            if value is not None and value not in choices:
                raise ValueError(f"{name} must be one of: {', '.join(choices)}")
        if self.claude_backend == "copilot" and (
                self.claude_max_budget_usd is not None or self.claude_max_turns != 20):
            raise ValueError("Claude internal-turn and dollar limits require --claude-backend native")
        for name in ("max_turns", "max_attempts", "history_limit", "claude_max_turns"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout", "max_seconds", "claude_max_budget_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_payload(value: object) -> dict[str, str]:
    # This is the complete validation of the small, fixed SCHEMA above.
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise AgentError("Response must contain only decision, plan, and reason")
    if any(not isinstance(item, str) for item in value.values()):
        raise AgentError("Response fields must all be strings")
    if value["decision"] not in SCHEMA["properties"]["decision"]["enum"]:
        raise AgentError("Decision must be approve, revise, or blocked")
    return value


def validate_review(value: object, plan: str) -> dict[str, str]:
    result = dict(validate_payload(value))
    if not result["reason"].strip():
        raise AgentError("Response must explain its decision")
    if result["decision"] == "revise":
        updated = normalize(result["plan"])
        if not updated.strip():
            raise AgentError("Revision returned an empty plan")
        if updated == normalize(plan):
            raise AgentError("Revision did not change the plan text; approve if it is ready")
        result["plan"] = updated
    else:
        if result["plan"] != "":
            raise AgentError("Approve and blocked must return an empty plan field")
        if result["decision"] == "approve" and not plan.strip():
            raise AgentError("Cannot approve an empty plan; draft one first")
    return result

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from pathlib import Path
from typing import Literal, TypedDict

Agent = Literal["codex", "claude"]
AGENTS = {"codex", "claude"}
CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_CLAUDE_MAX_TURNS = 20
# Task, criteria, plan, and reason size limit (UTF-8 bytes), checked before any CLI call.
MAX_TEXT_BYTES = 1024 * 1024
# Kinds of document the reviewers can produce; each has its own checklist (prompts.py).
DOCUMENT_MODES = ("plan", "report", "description", "review")
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


class AgentTimeout(AgentError):
    """The CLI exceeded its time allowance (the per-call timeout or the run's remainder)."""


# Longest server-requested retry delay Duo waits for; longer requests end the run.
MAX_RETRY_AFTER = 300.0


class Terminated(KeyboardInterrupt):
    """SIGTERM or SIGHUP. Handled like Ctrl-C, so the CLI's process group is stopped."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


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
    claude_max_turns: int | None = None  # None: DEFAULT_CLAUDE_MAX_TURNS
    claude_max_budget_usd: float | None = None
    skip_git_repo_check: bool = False
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    codex_effort: str | None = None
    claude_effort: str | None = None
    codex_backend: str = "native"
    claude_backend: str = "native"
    copilot_bin: str = "copilot"
    allow_untested_cli: bool = False
    max_output_mb: int = 32  # per captured stdout/stderr stream
    max_run_mb: int = 256    # per run directory, including CLI logs and caches
    mode: str = "plan"       # which document the reviewers produce
    guidance: str = ""       # custom review checklist replacing the mode's own
    output: str = ""         # document path relative to repo; "" means <mode>.md
    pass_env: tuple[str, ...] = ()  # extra environment variable names for reviewer CLIs

    def __post_init__(self):
        if self.start not in AGENTS:
            raise ValueError("Starting agent must be codex or claude")
        for name in self.pass_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"--pass-env needs an environment variable name, not {name!r}")
            if name.startswith(("COPILOT_", "GITHUB_COPILOT_")):
                raise ValueError(f"--pass-env cannot pass {name}: Duo controls Copilot settings")
        if self.mode not in DOCUMENT_MODES:
            raise ValueError(f"mode must be one of: {', '.join(DOCUMENT_MODES)}")
        if len(self.guidance.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"Instructions exceed the {MAX_TEXT_BYTES // 1024} KiB limit")
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
                self.claude_max_budget_usd is not None or self.claude_max_turns is not None):
            raise ValueError("Claude internal-turn and dollar limits require --claude-backend native")
        for name in ("max_turns", "max_attempts", "history_limit", "claude_max_turns",
                     "max_output_mb", "max_run_mb"):
            value = getattr(self, name)
            if name == "claude_max_turns" and value is None:
                continue
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


# PEM body parts after a private-key header. Separators are line breaks (blank
# lines allowed), escaped line breaks as in JSON or YAML strings, or spaces when
# a key is flattened onto one line. Complete blocks accept any base64 line
# length. Without a closing footer, require long base64 or a digit, "+", "/"
# or padding; short plain words stay prose near a documented header. Legacy
# encrypted PEM files also contain metadata lines.
_PEM_SEP = r"(?:(?:[ \t]*(?:\n|\\+r\\+n|\\+n))+[ \t]*|[ \t]+)"
_PEM_BASE64 = r"[A-Za-z0-9+/]+={0,2}(?![A-Za-z0-9+/=])"
_PEM_LINE = (r"(?:[A-Za-z0-9+/]{16,}={0,2}|[A-Za-z0-9+/]*[0-9+/][A-Za-z0-9+/]*={0,2}"
             r"|[A-Za-z0-9+/]+={1,2})(?![A-Za-z0-9+/=])")
_PEM_META = r"(?:Proc-Type|DEK-Info):[^\n\\]*"

# Credential formats specific enough to avoid matching ordinary plan text.
SECRET_PATTERNS = {
    # Include the body even without a footer; a header alone must not allow new
    # body material through.
    "private key": re.compile(
        r"-----BEGIN (?P<key_type>[A-Z0-9 ]*PRIVATE KEY)-----"
        # Try a complete block before the incomplete-body/prose heuristic.
        rf"(?:(?:{_PEM_SEP}(?:{_PEM_BASE64}|{_PEM_META}))*"
        rf"{_PEM_SEP}-----END (?P=key_type)-----"
        rf"|(?:{_PEM_SEP}(?:{_PEM_LINE}|{_PEM_META}))*)"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile(r"\bsk-[A-Za-z0-9_-]*T3BlbkFJ[A-Za-z0-9_-]+"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "Stripe live key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}"),
    "JSON Web Token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}


def new_secret(text: str, known: str) -> str | None:
    """Name a credential in text that the known text does not already contain."""
    for kind, pattern in SECRET_PATTERNS.items():
        if any(match.group(0) not in known for match in pattern.finditer(text)):
            return kind
    return None


def validate_review(value: object, plan: str) -> dict[str, str]:
    result = dict(validate_payload(value))
    if not result["reason"].strip():
        raise AgentError("Response must explain its decision")
    for field in ("plan", "reason"):
        if len(result[field].encode("utf-8")) > MAX_TEXT_BYTES:
            raise AgentError(f"Response {field} exceeds the {MAX_TEXT_BYTES // 1024} KiB limit; shorten it")
    # Reviewers can read outside the repository (Codex's sandbox allows it), so
    # never publish credentials they introduce. The message never echoes them.
    # Fields are separate documents: a header at the end of the plan must not
    # turn the reason into an apparent continuation of the key.
    # Compare normalized text, as stored, so line endings alone never differ.
    known = normalize(plan)
    kind = (new_secret(normalize(result["plan"]), known) or
            new_secret(normalize(result["reason"]), known))
    if kind:
        raise AgentError(f"Response appears to contain a credential ({kind}); "
                         "remove it and never copy secrets into the plan or reason")
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

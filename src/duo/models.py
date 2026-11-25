from __future__ import annotations

from collections import deque
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
# Input and response text-field size limit (UTF-8 bytes).
MAX_TEXT_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
# Interim pending the owner decision: any finite ceiling narrows 0.4.6 compatibility.
DEFAULT_MAX_PROMPT_BYTES = 512 * 1024
# Kinds of document the reviewers can produce; each has its own checklist (prompts.py).
DOCUMENT_MODES = ("plan", "report", "description", "review")
SEVERITIES = ("info", "low", "medium", "high", "critical")
DECISIONS = ("approve", "revise", "blocked")
FINDING_STATUSES = ("open", "resolved", "dismissed")
MAX_FINDINGS_PER_RESPONSE = 50
MAX_FINDING_TITLE_BYTES = 512


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity)


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties),
            "additionalProperties": False}


LOCATION_SCHEMA = {"anyOf": [
    object_schema({"kind": {"const": "lines", "type": "string"},
                   "start_line": {"type": "integer", "minimum": 1},
                   "end_line": {"type": "integer", "minimum": 1}}),
    object_schema({"kind": {"const": "subject", "type": "string"},
                   "explanation": {"type": "string"}}),
    object_schema({"kind": {"const": "section", "type": "string"},
                   "section": {"type": "string"}}),
]}
FINDING_REPORT_SCHEMA = object_schema({
    "severity": {"type": "string", "enum": list(SEVERITIES)},
    "title": {"type": "string"}, "description": {"type": "string"},
    "location": LOCATION_SCHEMA,
})
# Separate exact variants keep every property required for strict CLI schemas.
_UPDATE_FIELDS = {"id": {"type": "string"},
                  "status": {"type": "string", "enum": ["resolved", "dismissed"]},
                  "reason": {"type": "string"}, "subject_hash": {"type": "string"}}
FINDING_UPDATE_SCHEMA = {"anyOf": [object_schema(_UPDATE_FIELDS),
    object_schema({**_UPDATE_FIELDS, "location": LOCATION_SCHEMA})]}
COPILOT_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "plan": {"type": "string"},
        "reason": {"type": "string"},
        "findings": {"type": "array", "items": FINDING_REPORT_SCHEMA,
                     "maxItems": MAX_FINDINGS_PER_RESPONSE},
        "finding_updates": {"type": "array", "items": FINDING_UPDATE_SCHEMA,
                            "maxItems": MAX_FINDINGS_PER_RESPONSE},
    },
    "required": ["decision", "plan", "reason", "findings", "finding_updates"],
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


class Finding(TypedDict):
    id: str
    severity: str
    title: str
    description: str
    location: dict
    status: str
    reported_by: str
    reported_turn: int
    reported_attempt: int
    first_seen_subject_hash: str
    last_seen_subject_hash: str
    location_history: list[dict]
    disposition: dict | None


class State(TypedDict):
    findings: list[Finding]
    task: str
    criteria: str
    plan: str
    approved: list[str]
    next_agent: str
    turns: int
    attempts: int
    status: str
    history: list[Turn]
    last_diff: str
    error: str


@dataclass(frozen=True)
class ReviewerSpec:
    id: str
    kind: str
    backend: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Config:
    repo: Path
    codex_model: str
    claude_model: str
    start: str = "codex"
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
    allow_web: bool = False  # give reviewers web search and page fetching
    max_prompt_bytes: int = DEFAULT_MAX_PROMPT_BYTES
    reviewers: tuple[ReviewerSpec, ...] = ()

    def __post_init__(self):
        for name in self.pass_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"--pass-env needs an environment variable name, not {name!r}")
            if name.startswith(("COPILOT_", "GITHUB_COPILOT_")):
                raise ValueError(f"--pass-env cannot pass {name}: Duo controls Copilot settings")
        if self.mode not in DOCUMENT_MODES:
            raise ValueError(f"mode must be one of: {', '.join(DOCUMENT_MODES)}")
        if len(self.guidance.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"Instructions exceed the {MAX_TEXT_BYTES // 1024} KiB limit")
        if self.reviewers:
            if len(self.reviewers) < 2:
                raise ValueError("Panel rosters require at least two reviewers")
            ids = []
            for spec in self.reviewers:
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", spec.id):
                    raise ValueError(f"Invalid reviewer id: {spec.id!r}")
                if spec.id == "codex" and spec.kind != "codex":
                    raise ValueError("Reviewer id codex must use kind codex")
                if spec.id == "claude" and spec.kind != "claude":
                    raise ValueError("Reviewer id claude must use kind claude")
                if spec.kind not in AGENTS:
                    raise ValueError("reviewer kind must be codex or claude")
                if spec.backend not in ("native", "copilot"):
                    raise ValueError("reviewer backend must be native or copilot")
                if not spec.model.strip():
                    raise ValueError("reviewer model must be nonempty")
                if spec.backend == "copilot" and spec.model.lower() == "auto":
                    raise ValueError("Copilot reviewers require an explicit model, not auto")
                choices = COPILOT_EFFORTS if spec.backend == "copilot" else (
                    CODEX_EFFORTS if spec.kind == "codex" else CLAUDE_EFFORTS
                )
                if spec.effort is not None and spec.effort not in choices:
                    raise ValueError(
                        f"reviewer effort for {spec.id} must be one of: {', '.join(choices)}")
                ids.append(spec.id)
            if len(set(ids)) != len(ids):
                raise ValueError("Reviewer ids must be unique")
            if self.start not in set(ids):
                raise ValueError("Starting agent must be one of the configured reviewer ids")
            if self.max_turns < len(self.reviewers):
                raise ValueError("max_turns must allow at least one full roster cycle")
            has_native_claude = any(spec.kind == "claude" and spec.backend == "native"
                                    for spec in self.reviewers)
            if (self.claude_max_budget_usd is not None or self.claude_max_turns is not None) and not has_native_claude:
                raise ValueError("Claude internal-turn and dollar limits require a native Claude reviewer")
        else:
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
                    self.claude_max_budget_usd is not None or self.claude_max_turns is not None):
                raise ValueError("Claude internal-turn and dollar limits require --claude-backend native")
        for name in ("max_turns", "max_attempts", "history_limit", "claude_max_turns",
                     "max_output_mb", "max_run_mb", "max_prompt_bytes"):
            value = getattr(self, name)
            if name == "claude_max_turns" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout", "max_seconds", "claude_max_budget_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")

    def roster(self) -> tuple[ReviewerSpec, ...]:
        if self.reviewers:
            return self.reviewers
        return (
            ReviewerSpec("codex", "codex", self.codex_backend, self.codex_model, self.codex_effort),
            ReviewerSpec("claude", "claude", self.claude_backend, self.claude_model, self.claude_effort),
        )

    def spec(self, member_id: str) -> ReviewerSpec:
        for item in self.roster():
            if item.id == member_id:
                return item
        raise KeyError(member_id)

    def roster_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.roster())


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_location(value: object, *, recorded: bool = False) -> dict:
    if not isinstance(value, dict):
        raise AgentError("Finding location must be an object")
    fields = ({"kind", "start_line", "end_line"} if value.get("kind") == "lines"
              else {"kind", "section"} if value.get("kind") == "section"
              else {"kind", "explanation"})
    if recorded:
        fields.update(("path", "subject_sha256"))
    if set(value) != fields or value.get("kind") not in ("lines", "subject", "section"):
        raise AgentError("Malformed finding location")
    if recorded and not isinstance(value["path"], str):
        raise AgentError("Finding location path must be a string")
    if recorded and (not isinstance(value["subject_sha256"], str)
                     or not re.fullmatch(r"[0-9a-f]{64}", value["subject_sha256"])):
        raise AgentError("Finding location subject_sha256 must be a SHA-256 hex digest")
    if value["kind"] == "lines":
        if (any(type(value[k]) is not int or value[k] < 1 for k in ("start_line", "end_line"))
                or value["end_line"] < value["start_line"]):
            raise AgentError("Finding location requires positive line integers and end >= start")
    else:
        field = "section" if value["kind"] == "section" else "explanation"
        _finding_text(value[field], field)
    return value


def _finding_text(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise AgentError(f"Finding {field} must be a string")
    limit = MAX_FINDING_TITLE_BYTES if field == "title" else MAX_TEXT_BYTES
    if len(value.encode("utf-8")) > limit:
        raise AgentError(f"Finding {field} exceeds the {limit} byte limit")


def validate_payload(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise AgentError("Response must contain only decision, plan, reason, findings, and finding_updates")
    if any(not isinstance(value[k], str) for k in ("decision", "plan", "reason")):
        raise AgentError("Response decision, plan, and reason fields must all be strings")
    if value["decision"] not in SCHEMA["properties"]["decision"]["enum"]:
        raise AgentError("Decision must be approve, revise, or blocked")
    for field in ("findings", "finding_updates"):
        if not isinstance(value[field], list) or len(value[field]) > MAX_FINDINGS_PER_RESPONSE:
            raise AgentError(f"{field} must be an array of at most {MAX_FINDINGS_PER_RESPONSE} items")
        for item in value[field]:
            required = ({"severity", "title", "description", "location"} if field == "findings"
                        else {"id", "status", "reason", "subject_hash"})
            if not isinstance(item, dict) or (set(item) != required and not (
                    field == "finding_updates" and set(item) == required | {"location"})):
                raise AgentError(f"Malformed {field} object")
            if field == "findings":
                if item["severity"] not in SEVERITIES:
                    raise AgentError("Unknown finding severity")
                for key in ("title", "description"):
                    _finding_text(item[key], key)
            else:
                if item["status"] not in ("resolved", "dismissed"):
                    raise AgentError("Finding update status must be resolved or dismissed")
                for key in ("id", "reason", "subject_hash"):
                    _finding_text(item[key], key)
            if "location" in item:
                validate_location(item["location"])
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


class _MarkerPattern:
    """Consume a whole token run before testing its required interior marker.

    A regex requiring the marker after an unbounded repetition retries the
    remaining suffix at every near-match prefix (for example repeated ``sk-``).
    The first prefix consumes the run here, even when its marker is absent.
    Later prefixes in that run cannot yield a match that the first one missed.
    """

    def __init__(self, pattern: str, prefix_length: int, marker: str):
        self.token = re.compile(pattern)
        self.prefix_length = prefix_length
        self.marker = marker

    def finditer(self, text: str):
        for match in self.token.finditer(text):
            # Require at least one token character after the marker, just as
            # the original greedy regex did; retain the entire matched value.
            if text.find(self.marker, match.start() + self.prefix_length,
                         match.end() - 1) != -1:
                yield match

    def search(self, text: str):
        return next(self.finditer(text), None)


class _JWTPattern:
    """Check three dot-separated runs without retrying each ``eyJ`` suffix."""

    token = re.compile(r"[A-Za-z0-9_-]+")
    prefix = re.compile(r"\beyJ")
    value = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

    def finditer(self, text: str):
        runs = deque(maxlen=3)
        matched_end = 0
        for run in self.token.finditer(text):
            runs.append(run)
            if len(runs) < 3:
                continue
            first, second, third = runs
            if (first.start() < matched_end
                    or second.start() != first.end() + 1 or text[first.end()] != "."
                    or third.start() != second.end() + 1 or text[second.end()] != "."
                    or second.end() - second.start() < 13
                    or not text.startswith("eyJ", second.start())
                    or third.end() - third.start() < 10):
                continue
            # Search the original text so Unicode word boundaries, including
            # '-' versus '_', stay intact. Leave ten characters after 'eyJ'.
            prefix = self.prefix.search(text, first.start(), first.end() - 10)
            if prefix is not None:
                # All three parts are valid: the original regex now succeeds
                # without retrying suffixes, preserving its greedy span/value
                # and Match interface. Its character classes cannot cross dots.
                match = self.value.match(text, prefix.start(), third.end())
                yield match
                matched_end = third.end()

    def search(self, text: str):
        return next(self.finditer(text), None)


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
    "OpenAI API key": _MarkerPattern(r"\bsk-[A-Za-z0-9_-]*", 3, "T3BlbkFJ"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "Stripe live key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}"),
    "JSON Web Token": _JWTPattern(),
}


def _contains_all(known: str, candidates: set[str]) -> bool:
    """Find all distinct values as substrings, scanning known only once.

    Aho-Corasick failure links allow overlapping/prefix/suffix values, including
    values embedded in text that would not itself match a credential pattern.
    Mark visited states and propagate them afterwards instead of enumerating
    every match: repeated or nested known values must not multiply the work.
    """
    if not candidates:
        return True
    if any(len(value) > len(known) for value in candidates):
        return False
    if len(candidates) == 1:
        return next(iter(candidates)) in known

    edges: list[dict[str, int]] = [{}]
    failure = [0]
    terminals = []
    for value in candidates:
        node = 0
        for char in value:
            child = edges[node].get(char)
            if child is None:
                child = len(edges)
                edges[node][char] = child
                edges.append({})
                failure.append(0)
            node = child
        terminals.append(node)

    # Appending while iterating gives breadth-first order without copying it.
    order = list(edges[0].values())
    for node in order:
        for char, child in edges[node].items():
            fallback = failure[node]
            while fallback and char not in edges[fallback]:
                fallback = failure[fallback]
            failure[child] = edges[fallback].get(char, 0)
            order.append(child)

    seen = bytearray(len(edges))
    node = 0
    for char in known:
        while node and char not in edges[node]:
            node = failure[node]
        node = edges[node].get(char, 0)
        seen[node] = 1
    for node in reversed(order):
        if seen[node]:
            seen[failure[node]] = 1
    return all(seen[node] for node in terminals)


def new_secret(text: str, known: str) -> str | None:
    """Name a credential in text that the known text does not already contain."""
    for kind, pattern in SECRET_PATTERNS.items():
        candidates = {match.group(0) for match in pattern.finditer(text)}
        if not _contains_all(known, candidates):
            return kind
    return None


def validate_review(value: object, plan: str) -> dict:
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
    for item in result["findings"] + result["finding_updates"]:
        for field in ("title", "description"):
            if field in item and not item[field].strip():
                raise AgentError(f"Finding {field} must be nonempty")
        for field in ("title", "description", "reason", "id", "subject_hash"):
            if field in item:
                kind = kind or new_secret(normalize(item[field]), known)
        location = item.get("location", {})
        for field in ("explanation", "section"):
            if field in location:
                if not location[field].strip():
                    raise AgentError(f"Finding {field} must be nonempty")
                kind = kind or new_secret(normalize(location[field]), known)
    if kind:
        raise AgentError(f"Response appears to contain a credential ({kind}); "
                         "remove it and never copy secrets into response fields")
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

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable

from . import copilot
from .models import (Agent, AgentError, AgentTimeout, Config, DEFAULT_CLAUDE_MAX_TURNS, RunError,
                     SCHEMA, validate_payload)
from .policy import (CLAUDE_SETTINGS, check_claude_managed_files, claude_options, claude_tools,
                     codex_options, reviewer_environment, verify_controls, verify_version)
from .storage import RunStore, run_usage


class LimitExceeded(Exception):
    """A resource limit was exceeded while the CLI ran; the message is safe to report."""


POLL_SECONDS = 0.5  # how often limits are checked; overshoot is bounded by output rate x this


def run_process(argv: list[str], prompt: str, repo: Path, timeout: float,
                stdout: Path, stderr: Path, *, env: dict[str, str] | None = None,
                check: Callable[[], str | None] | None = None) -> int:
    """Keep diagnostics on disk and terminate the entire CLI process group.

    While the CLI runs, ``check`` is called every POLL_SECONDS; a message from it
    stops the process group and raises LimitExceeded.
    """
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
            cwd=repo, start_new_session=True, env=env,
        )

        def feed() -> None:
            # A thread, so a CLI that exits or stops reading cannot block monitoring.
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError):
                pass

        writer = threading.Thread(target=feed, daemon=True)
        try:
            writer.start()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    process.wait(timeout=max(0.0, min(POLL_SECONDS, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, timeout) from None
                    message = check() if check else None
                    if message:
                        raise LimitExceeded(message)
        except BaseException:
            # Monitoring and setup failures must stop the reviewer too, including
            # filesystem errors while measuring its output or audit directory.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            if writer.ident is not None:
                writer.join(timeout=5)
    # A fast CLI may finish between checks; its output still counts.
    message = check() if check else None
    if message:
        raise LimitExceeded(message)
    return process.returncode


FENCED_JSON = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n[ \t]*```\Z", re.DOTALL)
DIAGNOSTIC_BYTES = 8192


def parse_json(text: str, *, allow_fence: bool = False) -> object:
    if allow_fence:
        # Model replies may wrap the object in one Markdown code fence.
        text = text.strip()
        match = FENCED_JSON.match(text)
        if match:
            text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError("Agent returned missing or invalid JSON") from exc


def read_json(path: Path, *, allow_fence: bool = False) -> object:
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise AgentError("Agent response exceeds the 16 MiB limit", retryable=False)
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentError("Agent returned missing or invalid JSON") from exc
    return parse_json(text, allow_fence=allow_fence)


def copilot_reply(path: Path) -> object:
    """The final assistant message from Copilot's JSONL event stream.

    Text output is wrapped for a terminal, which breaks JSON strings, and mixes
    progress narration with the answer. Events carry each message unchanged;
    commentary and tool-calling messages are skipped, as is tool output.
    """
    reply = None
    try:
        # Events include tool results, so allow more than a single response.
        if path.stat().st_size > 64 * 1024 * 1024:
            raise AgentError("Copilot output exceeds the 64 MiB limit", retryable=False)
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "assistant.message":
                    continue
                data = event.get("data")
                if (isinstance(data, dict) and isinstance(data.get("content"), str)
                        and data["content"].strip() and not data.get("toolRequests")
                        and data.get("phase") in (None, "final_answer")):
                    reply = data["content"]
    except (OSError, UnicodeError) as exc:
        raise AgentError("Agent returned missing or invalid JSON") from exc
    if reply is None:
        raise AgentError("Copilot returned no final answer")
    return parse_json(reply, allow_fence=True)


def read_tail(path: Path, size: int = DIAGNOSTIC_BYTES) -> str:
    # CLIs report fatal errors last; earlier output may quote the repository.
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(f.tell() - size, 0))
        return f.read().decode("utf-8", errors="replace")


def claude_error_text(envelope: object) -> str:
    """Claude's error metadata only; review text is never classified."""
    if not isinstance(envelope, dict):
        return ""
    fields = [str(envelope.get("subtype", ""))]
    if envelope.get("is_error") is True:
        fields.append(str(envelope.get("result", ""))[:DIAGNOSTIC_BYTES])
    return "\n".join(fields)


def claude_result(path: Path, metadata: dict, *, max_output_mb: int) -> dict:
    """Read a complete JSONL stream, never an intermediate assistant answer.

    Unknown event types are retained on disk and tolerated. Every emitted line
    must still be an event object, and exactly one result must end the stream.
    Metadata describes the stream, independently of process/workflow success.
    """
    metadata.update(completion="invalid", terminal_results=0, usage_available=False,
                    usage=None)
    terminal = None
    last_type = None
    try:
        if path.stat().st_size > max_output_mb * 1024 * 1024:
            raise AgentError(f"Claude output exceeds the {max_output_mb} MiB limit "
                             "(--max-output-mb)", retryable=False)
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                if not line.strip():
                    continue
                event = parse_json(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise AgentError("Claude returned an invalid stream event")
                last_type = event["type"]
                if last_type == "result":
                    metadata["terminal_results"] += 1
                    terminal = event
    except (OSError, UnicodeError, RecursionError) as exc:
        raise AgentError("Claude returned missing or invalid stream data") from exc
    if metadata["terminal_results"] != 1:
        metadata["completion"] = "missing" if terminal is None else "ambiguous"
        raise AgentError("Claude stream must contain exactly one terminal result")
    if last_type != "result":
        raise AgentError("Claude emitted events after its terminal result")
    if not isinstance(terminal.get("subtype"), str) or type(terminal.get("is_error")) is not bool:
        raise AgentError("Claude returned an invalid result envelope")
    metadata["completion"] = ("success" if terminal["subtype"] == "success"
                              and terminal["is_error"] is False else "unsuccessful")
    metadata["usage_available"] = isinstance(terminal.get("usage"), dict)
    metadata["usage"] = terminal["usage"] if metadata["usage_available"] else None
    return terminal


def claude_stream_error_text(path: Path) -> str:
    """Bounded failure diagnostics, even if another stream event is damaged."""
    messages = []
    for line in read_tail(path, 8 * DIAGNOSTIC_BYTES).splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            messages.append(claude_error_text(event))
    return "\n".join(messages[-4:])


def codex_error_text(events: Path) -> str:
    """Error messages from Codex's JSONL events; agent messages are never read."""
    messages = []
    # Failures are the last events; the first tail line may be partial.
    for line in read_tail(events, 8 * DIAGNOSTIC_BYTES).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.failed" and isinstance(event.get("error"), dict):
            messages.append(str(event["error"].get("message", "")))
        elif event.get("type") == "error":
            message = str(event.get("message", ""))
            # Reconnection notices are transient; the final error follows them.
            if not message.startswith("Reconnecting"):
                messages.append(message)
    return "\n".join(message[:DIAGNOSTIC_BYTES] for message in messages[-4:])


MODEL_UNAVAILABLE = "Requested model is unavailable in the selected CLI"


def classify_failure(diagnostic: str, model: str | None = None) -> AgentError:
    """Use diagnostic text for classification only; never echo it into prompts.

    The configured model name, not CLI output, may appear in the message.
    """
    text = diagnostic.lower()
    permanent = {
        "authentication failed": "Authentication failed; sign in to the agent CLI",
        "not logged in": "Agent CLI is not logged in",
        "invalid api key": "Agent authentication is invalid",
        "unauthorized": "Agent authentication is unauthorized",
        "authentication required": "Agent CLI authentication is required",
        "no authentication": "Agent CLI authentication is required",
        "unknown model": MODEL_UNAVAILABLE,
        "model is not supported": MODEL_UNAVAILABLE,
        # Claude Code reports an unknown or inaccessible model this way.
        "unrecognized_model": MODEL_UNAVAILABLE,
        "issue with the selected model": MODEL_UNAVAILABLE,
        # Copilot CLI: 'Model "<name>" from --model flag is not available.'
        "from --model flag is not available": MODEL_UNAVAILABLE,
        "unknown option": "Installed CLI does not support a requested option",
        "unexpected argument": "Installed CLI does not support a requested argument",
        "error loading config": "Agent configuration is incompatible with inspection policy",
        "invalid configuration": "Agent configuration is incompatible with inspection policy",
        "requirements.toml": "Managed Codex configuration is incompatible with inspection policy",
        "not inside a trusted directory": "Codex requires a Git repository or --skip-git-repo-check",
        "error_max_turns": "Claude exhausted its internal turn limit",
        "error_max_budget_usd": "Claude exhausted its per-call budget",
    }
    for fragment, message in permanent.items():
        if fragment in text:
            if message == MODEL_UNAVAILABLE and model:
                message = f"{message}: {model}"
            return AgentError(message, retryable=False)
    return AgentError("Agent CLI failed; see the attempt diagnostics", retry_after=retry_delay(text))


RETRY_AFTER = re.compile(
    r"(?:retry-after\s*[:=]\s*|(?:retry|try again) (?:in|after)\s+)(\d+(?:\.\d+)?)\s*"
    r"(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?|h|hours?)?\b")
def retry_delay(text: str) -> float | None:
    """A requested delay in seconds. A bare number is seconds, as in Retry-After."""
    match = RETRY_AFTER.search(text)
    if not match:
        return None
    unit = (match.group(2) or "s").lower()
    if unit == "ms" or unit.startswith("milli"):
        scale = 0.001
    elif unit.startswith("h"):
        scale = 3600
    elif unit.startswith("m"):
        scale = 60
    else:
        scale = 1
    return float(match.group(1)) * scale


class CLIAdapter:
    def __init__(self, config: Config, store: RunStore):
        self.config = config
        self.store = store
        self.policy_verified = False
        self.untested: dict[str, str] = {}
        self.settings_path = self.store.path / "claude-settings.json"
        self.copilot_session: list[str] = []  # set by preflight for the installed Copilot

    def environment(self, cli: str) -> dict[str, str]:
        return reviewer_environment(cli, self.config.pass_env)

    def probe(self, argv: list[str], env: dict[str, str] | None = None) -> str:
        """Run a setup probe without model calls; allowed authentication may remain.

        Uses the reviewer's restricted environment unless an explicit probe
        environment is supplied (e.g. the empty Codex home for MCP listing).
        """
        if env is None:
            env = self.environment("claude" if argv[0] == self.config.claude_bin else "codex")
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                                  cwd=self.config.repo, env=env, stdin=subprocess.DEVNULL)
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise RunError("Cannot verify reviewer configuration; CLI probe failed") from exc
        if proc.returncode:
            raise RunError("Cannot verify reviewer configuration; CLI probe returned an error")
        return proc.stdout

    def preflight(self) -> dict[str, str]:
        self.policy_verified = False
        native = {agent for agent in ("codex", "claude")
                  if getattr(self.config, f"{agent}_backend") == "native"}
        if len(native) < 2:
            copilot.require_auth(os.environ)
        if "claude" in native:
            check_claude_managed_files()
        versions = {}
        untested = []
        for agent, binary in (("codex", self.config.codex_bin),
                              ("claude", self.config.claude_bin)):
            if agent not in native:
                continue
            if shutil.which(binary) is None:
                raise RunError(f"{agent} executable not found: {binary}")
            try:
                proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                      timeout=15, cwd=self.config.repo, env=self.environment(agent))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RunError(f"Cannot run {agent} --version") from exc
            if proc.returncode:
                raise RunError(f"{agent} --version failed")
            versions[agent] = proc.stdout.strip()[:200]
            if verify_version(agent, versions[agent], self.config.allow_untested_cli) == "untested":
                untested.append(agent)
        if not self.config.skip_git_repo_check:
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    cwd=self.config.repo, capture_output=True, text=True, timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RunError("Cannot check whether the target is a Git repository") from exc
            if proc.returncode or proc.stdout.strip() != "true":
                raise RunError("Target must be a Git repository; use --skip-git-repo-check otherwise")
        if "claude" in native:
            self.store.json("claude-settings.json", CLAUDE_SETTINGS)
        policy = verify_controls(self.config.codex_bin, self.config.claude_bin,
                                 self.settings_path, self.probe, agents=native,
                                 pass_env=self.config.pass_env, allow_web=self.config.allow_web)
        if len(native) < 2:
            directory = Path(tempfile.mkdtemp(prefix="copilot-preflight-", dir=self.store.path))
            versions["copilot"], policy["copilot"] = copilot.preflight(self.config, directory)
            self.copilot_session = policy["copilot"]["session_flags"]
            if policy["copilot"].get("version_status") == "untested":
                untested.append("copilot")
        policy["reviewers"] = {
            agent: {"backend": getattr(self.config, f"{agent}_backend"),
                    "model": getattr(self.config, f"{agent}_model")}
            for agent in ("codex", "claude")
        }
        # Names only: which variables each reviewer CLI receives, never their values.
        policy["environment"] = {cli: sorted(self.environment(cli)) for cli in sorted(native)}
        if len(native) < 2:
            # Built as a review builds it, so Duo's own Copilot settings are listed too.
            names = directory / "environment"
            names.mkdir()
            policy["environment"]["copilot"] = sorted(copilot.environment(names, self.config.pass_env))
        policy["versions"] = versions
        # Probes still ran; the versions are simply not part of the supported set.
        self.untested = {name: versions[name] for name in untested}
        policy["untested_cli"] = self.untested
        self.store.json("reviewer-policy.json", policy)
        self.policy_verified = True
        return versions

    def command(self, agent: Agent, directory: Path) -> list[str]:
        config = self.config
        if agent not in ("codex", "claude"):
            raise ValueError("Unknown agent")
        if getattr(config, f"{agent}_backend") == "copilot":
            return copilot.command(config, agent, directory, self.copilot_session)
        if agent == "codex":
            argv = [config.codex_bin, "--no-daemon", "exec", "--sandbox", "read-only",
                    "--ignore-user-config", "--ignore-rules", "--strict-config",
                    # JSONL events on stdout keep the transcript out of stderr
                    # and report failures as structured error events.
                    "--ephemeral", "--json", "-m", config.codex_model,
                    "--output-schema", str(directory / "review.schema.json"),
                    "-o", str(directory / "response.json")]
            if config.codex_effort is not None:
                argv += ["-c", f'model_reasoning_effort="{config.codex_effort}"']
            argv += codex_options(config.allow_web)
            if config.skip_git_repo_check:
                argv.append("--skip-git-repo-check")
            return argv + ["-"]
        if agent != "claude":
            raise ValueError("Unknown agent")
        argv = [config.claude_bin, "-p", "--model", config.claude_model,
                "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(SCHEMA),
                "--tools", ",".join(claude_tools(config.allow_web)),
                "--allowedTools", ",".join(claude_tools(config.allow_web)),
                *claude_options(self.settings_path), "--disallowedTools", "mcp__*",
                "--no-session-persistence", "--max-turns",
                str(config.claude_max_turns or DEFAULT_CLAUDE_MAX_TURNS)]
        if config.claude_effort is not None:
            argv += ["--effort", config.claude_effort]
        if config.claude_max_budget_usd is not None:
            argv += ["--max-budget-usd", str(config.claude_max_budget_usd)]
        return argv

    def limit_message(self, stdout: Path, stderr: Path) -> str | None:
        """A resource-limit violation for the running attempt, or None."""
        output_limit = self.config.max_output_mb * 1024 * 1024
        for stream in (stdout, stderr):
            try:
                if stream.stat().st_size > output_limit:
                    return (f"Agent CLI {stream.stem} exceeded the {self.config.max_output_mb} MiB "
                            "output limit (--max-output-mb)")
            except FileNotFoundError:
                pass
        if run_usage(self.store.path) > self.config.max_run_mb * 1024 * 1024:
            return (f"Run storage exceeded the {self.config.max_run_mb} MiB limit (--max-run-mb), "
                    "including CLI logs and caches")
        return None

    def __call__(self, agent: Agent, prompt: str, timeout: float) -> dict[str, str]:
        if not self.policy_verified:
            self.preflight()
        backend = getattr(self.config, f"{agent}_backend")
        if agent == "claude" and backend == "native":
            check_claude_managed_files()
        directory = self.store.attempt_dir()
        env = self.environment(agent)
        if backend == "copilot":
            copilot.check_sources(self.config.repo)
            env = copilot.environment(directory, self.config.pass_env)
            copilot.require_auth(env)
            prompt = ("Return only one JSON object, without Markdown fences or narration, "
                      "matching this schema: " + json.dumps(SCHEMA) + "\n\n" + prompt)
        schema = directory / "review.schema.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        argv = self.command(agent, directory)
        (directory / "command.json").write_text(json.dumps(argv), encoding="utf-8")
        stdout, stderr = directory / "stdout.log", directory / "stderr.log"
        # Raw CLI events survive failures and interruption; they are not an
        # archive of every inspected file or page.
        self.store.event({"event": "cli_start", "agent": agent, "backend": backend,
                          "model": getattr(self.config, f"{agent}_model"),
                          "attempt_dir": directory.name})
        if backend == "native" and agent == "claude":
            return self.claude_attempt(argv, prompt, timeout, stdout, stderr, env)
        try:
            code = run_process(argv, prompt, self.config.repo, timeout, stdout, stderr, env=env,
                               check=lambda: self.limit_message(stdout, stderr))
        except subprocess.TimeoutExpired as exc:
            raise AgentTimeout("Agent CLI timed out") from exc
        except LimitExceeded as exc:
            # Retrying would likely repeat the runaway output; stop the run instead.
            raise AgentError(str(exc), retryable=False) from exc
        except OSError as exc:
            raise AgentError("Cannot start or capture the agent CLI", retryable=False) from exc
        finally:
            if backend == "copilot":
                copilot.discard_runtime(directory)
        if code:
            # Bounded reads; diagnostic text is kept local, never put in a prompt.
            # Only structured error fields are read from stdout, which otherwise
            # carries model text (Copilot's stdout is never read).
            diagnostic = read_tail(stderr)
            if backend == "native" and agent == "codex":
                diagnostic += "\n" + codex_error_text(stdout)
            raise classify_failure(diagnostic, getattr(self.config, f"{agent}_model"))
        if backend == "copilot":
            return validate_payload(copilot_reply(stdout))
        if agent == "codex":
            return validate_payload(read_json(directory / "response.json"))

    def claude_attempt(self, argv: list[str], prompt: str, timeout: float,
                       stdout: Path, stderr: Path, env: dict[str, str]) -> dict[str, str]:
        metadata = {"process_status": "interrupted", "returncode": None,
                    "payload_validated": False}
        stream_error = None
        envelope = None
        failing = False
        try:
            try:
                code = run_process(argv, prompt, self.config.repo, timeout, stdout, stderr,
                                   env=env, check=lambda: self.limit_message(stdout, stderr))
                metadata.update(process_status="exited", returncode=code)
            except subprocess.TimeoutExpired as exc:
                metadata["process_status"] = "timeout"
                raise AgentTimeout("Agent CLI timed out") from exc
            except LimitExceeded as exc:
                metadata["process_status"] = "resource_limit"
                raise AgentError(str(exc), retryable=False) from exc
            except OSError as exc:
                metadata["process_status"] = "capture_error"
                raise AgentError("Cannot start or capture the agent CLI", retryable=False) from exc
            finally:
                # Also inspect partial logs after timeout, SIGTERM, or Ctrl-C.
                # Parsing failure must never replace the original interruption.
                try:
                    envelope = claude_result(stdout, metadata,
                                             max_output_mb=self.config.max_output_mb)
                except AgentError as exc:
                    stream_error = exc
            if code:
                diagnostic = read_tail(stderr) + "\n" + (
                    claude_error_text(envelope) if envelope is not None
                    else claude_stream_error_text(stdout))
                raise classify_failure(diagnostic, self.config.claude_model)
            if stream_error:
                raise stream_error
            if metadata["completion"] != "success":
                raise classify_failure(claude_error_text(envelope), self.config.claude_model)
            result = validate_payload(envelope.get("structured_output"))
            metadata["payload_validated"] = True
            return result
        except BaseException:
            failing = True
            raise
        finally:
            try:
                self.store.json(f"{stdout.parent.name}/claude-stream.json", metadata)
            except Exception as exc:
                if not failing:
                    raise AgentError("Cannot write Claude stream metadata", retryable=False) from exc

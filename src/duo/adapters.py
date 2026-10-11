from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

from . import copilot
from .models import Agent, AgentError, Config, RunError, SCHEMA, validate_payload
from .policy import (CLAUDE_SETTINGS, check_claude_managed_files, claude_options,
                     codex_options, verify_controls, verify_version)
from .storage import RunStore


def run_process(argv: list[str], prompt: str, repo: Path, timeout: float,
                stdout: Path, stderr: Path, *, env: dict[str, str] | None = None) -> int:
    """Keep diagnostics on disk and terminate the entire CLI process group."""
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
            cwd=repo, start_new_session=True, env=env,
        )
        try:
            process.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    return process.returncode


def read_json(path: Path) -> object:
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise AgentError("Agent response exceeds the 16 MiB limit", retryable=False)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentError("Agent returned missing or invalid JSON") from exc


def classify_failure(diagnostic: str) -> AgentError:
    """Use diagnostic text for classification only; never echo it into prompts."""
    text = diagnostic.lower()
    permanent = {
        "authentication failed": "Authentication failed; sign in to the agent CLI",
        "not logged in": "Agent CLI is not logged in",
        "invalid api key": "Agent authentication is invalid",
        "unauthorized": "Agent authentication is unauthorized",
        "authentication required": "Agent CLI authentication is required",
        "no authentication": "Agent CLI authentication is required",
        "unknown model": "Requested model is unavailable in the selected CLI",
        "model is not supported": "Requested model is unavailable in the selected CLI",
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
            return AgentError(message, retryable=False)
    delay = re.search(r"(?:retry-after\s*[:=]\s*|retry (?:in|after)\s+)(\d+(?:\.\d+)?)", text)
    return AgentError("Agent CLI failed; see the attempt diagnostics",
                      retry_after=float(delay.group(1)) if delay else None)


class CLIAdapter:
    def __init__(self, config: Config, store: RunStore):
        self.config = config
        self.store = store
        self.policy_verified = False
        self.settings_path = self.store.path / "claude-settings.json"

    def probe(self, argv: list[str]) -> str:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                                  cwd=self.config.repo, stdin=subprocess.DEVNULL)
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
        for agent, binary in (("codex", self.config.codex_bin),
                              ("claude", self.config.claude_bin)):
            if agent not in native:
                continue
            if shutil.which(binary) is None:
                raise RunError(f"{agent} executable not found: {binary}")
            try:
                proc = subprocess.run([binary, "--version"], capture_output=True,
                                      text=True, timeout=15, cwd=self.config.repo)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RunError(f"Cannot run {agent} --version") from exc
            if proc.returncode:
                raise RunError(f"{agent} --version failed")
            versions[agent] = proc.stdout.strip()[:200]
            verify_version(agent, versions[agent])
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
                                 self.settings_path, self.probe, agents=native)
        if len(native) < 2:
            directory = Path(tempfile.mkdtemp(prefix="copilot-preflight-", dir=self.store.path))
            versions["copilot"], policy["copilot"] = copilot.preflight(self.config, directory)
        policy["reviewers"] = {
            agent: {"backend": getattr(self.config, f"{agent}_backend"),
                    "model": getattr(self.config, f"{agent}_model")}
            for agent in ("codex", "claude")
        }
        policy["versions"] = versions
        self.store.json("reviewer-policy.json", policy)
        self.policy_verified = True
        return versions

    def command(self, agent: Agent, directory: Path) -> list[str]:
        config = self.config
        if agent not in ("codex", "claude"):
            raise ValueError("Unknown agent")
        if getattr(config, f"{agent}_backend") == "copilot":
            return copilot.command(config, agent, directory)
        if agent == "codex":
            argv = [config.codex_bin, "--no-daemon", "exec", "--sandbox", "read-only",
                    "--ignore-user-config", "--ignore-rules", "--strict-config",
                    "--ephemeral", "-m", config.codex_model,
                    "--output-schema", str(directory / "review.schema.json"),
                    "-o", str(directory / "response.json")]
            if config.codex_effort is not None:
                argv += ["-c", f'model_reasoning_effort="{config.codex_effort}"']
            argv += codex_options()
            if config.skip_git_repo_check:
                argv.append("--skip-git-repo-check")
            return argv + ["-"]
        if agent != "claude":
            raise ValueError("Unknown agent")
        argv = [config.claude_bin, "-p", "--model", config.claude_model,
                "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
                "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
                *claude_options(self.settings_path), "--disallowedTools", "mcp__*",
                "--no-session-persistence", "--max-turns", str(config.claude_max_turns)]
        if config.claude_effort is not None:
            argv += ["--effort", config.claude_effort]
        if config.claude_max_budget_usd is not None:
            argv += ["--max-budget-usd", str(config.claude_max_budget_usd)]
        return argv

    def __call__(self, agent: Agent, prompt: str, timeout: float) -> dict[str, str]:
        if not self.policy_verified:
            self.preflight()
        backend = getattr(self.config, f"{agent}_backend")
        if agent == "claude" and backend == "native":
            check_claude_managed_files()
        directory = self.store.attempt_dir()
        env = None
        if backend == "copilot":
            copilot.check_sources(self.config.repo)
            env = copilot.environment(directory)
            copilot.require_auth(env)
            prompt = ("Return only one JSON object, without Markdown fences or narration, "
                      "matching this schema: " + json.dumps(SCHEMA) + "\n\n" + prompt)
        schema = directory / "review.schema.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        argv = self.command(agent, directory)
        (directory / "command.json").write_text(json.dumps(argv), encoding="utf-8")
        stdout, stderr = directory / "stdout.log", directory / "stderr.log"
        self.store.event({"event": "cli_start", "agent": agent, "backend": backend,
                          "model": getattr(self.config, f"{agent}_model"),
                          "attempt_dir": directory.name})
        try:
            code = run_process(argv, prompt, self.config.repo, timeout, stdout, stderr, env=env)
        except subprocess.TimeoutExpired as exc:
            raise AgentError("Agent CLI timed out") from exc
        except OSError as exc:
            raise AgentError("Cannot start or capture the agent CLI", retryable=False) from exc
        if code:
            # Bounded reads; diagnostic text is kept local, never put in a prompt.
            with stderr.open("rb") as f:
                diagnostic = f.read(65536).decode("utf-8", errors="replace")
            with stdout.open("rb") as f:
                diagnostic += f.read(65536).decode("utf-8", errors="replace")
            raise classify_failure(diagnostic)
        if backend == "copilot":
            return validate_payload(read_json(stdout))
        if agent == "codex":
            return validate_payload(read_json(directory / "response.json"))
        envelope = read_json(stdout)
        if not isinstance(envelope, dict):
            raise AgentError("Claude returned an invalid result envelope")
        if envelope.get("is_error") is not False or envelope.get("subtype") != "success":
            raise classify_failure(json.dumps(envelope))
        return validate_payload(envelope.get("structured_output"))

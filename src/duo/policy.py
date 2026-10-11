"""Inspection policy for the CLI versions whose controls Duo understands.

Never copy credentials or change global settings. Configuration probes do not
start review sessions or issue model requests. Unknown policy states fail closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

from .models import RunError

POLICY_ID = "inspection-v1"
CODEX_DISABLED = (
    "hooks", "apps", "plugins", "browser_use", "browser_use_external",
    "in_app_browser", "multi_agent", "multi_agent_v2", "shell_snapshot",
)
CODEX_OVERRIDES = (
    "mcp_servers={}", "notify=[]", 'approval_policy="never"',
    'sandbox_mode="read-only"', 'web_search="disabled"',
    *(f"features.{name}=false" for name in CODEX_DISABLED),
)
CLAUDE_SETTINGS = {
    "disableAllHooks": True,
    "disableClaudeAiConnectors": True,
    "syncClaudeAiPlugins": False,
    "autoMemoryEnabled": False,
}


def codex_options() -> list[str]:
    return [item for override in CODEX_OVERRIDES for item in ("-c", override)]


def claude_options(settings: Path) -> list[str]:
    return ["--safe-mode", "--restricted", "--setting-sources", "",
            "--settings", str(settings), "--strict-mcp-config", "--no-chrome",
            "--disable-slash-commands"]


def verify_version(agent: str, version: str) -> None:
    # These are the first versions exercised with this policy. Feature/help
    # probes below remain mandatory; a version number alone proves nothing.
    minimum = {"codex": (0, 162, 1), "claude": (2, 1, 296)}[agent]
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    actual = tuple(map(int, match.groups())) if match else ()
    if actual < minimum or actual[:2] != minimum[:2]:
        supported = ".".join(map(str, minimum))
        raise RunError(f"Unsupported {agent} inspection controls: require {supported} "
                       "or a later patch in the same minor release")


def check_claude_managed_files() -> None:
    """Reject local policies before starting a CLI that might run policy helpers.

    Managed hooks cannot be disabled by --settings. This release supports
    unmanaged Claude installations only, rather than trying to merge policy.
    """
    system = (Path("/Library/Application Support/ClaudeCode") if sys.platform == "darwin"
              else Path("/etc/claude-code"))
    candidates = [system / "managed-settings.json", system / "managed-settings.d",
                  system / "managed-mcp.json"]
    if sys.platform == "darwin":
        candidates += [Path("/Library/Managed Preferences/com.anthropic.claudecode.plist"),
                       Path("/Library/Managed Preferences") / Path.home().name /
                       "com.anthropic.claudecode.plist",
                       Path("/Library/Preferences/com.anthropic.claudecode.plist"),
                       Path.home() / "Library/Preferences/com.anthropic.claudecode.plist"]
    try:
        for path in candidates:
            # stat propagates permission errors instead of treating them as absent.
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise RunError("Claude managed configuration detected; Duo cannot establish "
                           "its inspection policy on managed installations")
        if sys.platform == "darwin":
            try:
                preferences = subprocess.run(
                    ["/usr/bin/defaults", "read", "com.anthropic.claudecode"],
                    capture_output=True, text=True, timeout=15,
                )
            except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
                raise RunError("Cannot verify Claude managed preferences") from exc
            if (preferences.returncode != 1 or
                    "Domain com.anthropic.claudecode does not exist" not in preferences.stderr):
                raise RunError("Claude preferences policy detected or unreadable; "
                               "Duo requires an unmanaged installation")
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        cached = root / "remote-settings.json"
        try:
            data = cached.read_bytes()
        except FileNotFoundError:
            return
        # A populated cache may be applied even if a later policy fetch fails.
        if json.loads(data) != {}:
            raise RunError("Claude cached managed configuration detected; inspection "
                           "restrictions cannot be established")
    except (OSError, ValueError) as exc:
        raise RunError("Cannot verify Claude managed configuration") from exc


def verify_claude_remote(report: str) -> str:
    prefix = "Managed settings (remote): "
    states = [line[len(prefix):].strip() for line in report.splitlines() if line.startswith(prefix)]
    allowed = {
        "none configured for this organization",
        "not fetched — requires an Enterprise or Team subscription",
        "not fetched — no usable credentials for the settings fetch",
    }
    if len(states) != 1 or states[0] not in allowed:
        raise RunError("Cannot establish absence of Claude managed settings; "
                       "inspection policy requires an unmanaged installation and "
                       "a successful policy check (see claude doctor)")
    return states[0]


def verify_controls(codex: str, claude: str, settings: Path,
                    probe: Callable[[list[str]], str]) -> dict:
    codex_help = probe([codex, "exec", "--help"])
    claude_help = probe([claude, "--help"])
    required = {
        "codex": (codex_help, ("--sandbox", "--ephemeral", "--output-schema", "--ignore-rules",
                               "--ignore-user-config", "--strict-config")),
        "claude": (claude_help, ("--safe-mode", "--restricted", "--setting-sources",
                                "--settings", "--strict-mcp-config", "--no-chrome",
                                "--disable-slash-commands", "--tools", "--allowedTools")),
    }
    for agent, (help_text, flags) in required.items():
        if any(flag not in help_text for flag in flags):
            raise RunError(f"Installed {agent} lacks required inspection options")
    effective = probe([codex, *codex_options(), "features", "list"])
    features = {parts[0]: parts[-1] for line in effective.splitlines()
                if len(parts := line.split()) >= 3}
    if any(features.get(name) != "false" for name in CODEX_DISABLED):
        raise RunError("Codex inspection feature restrictions did not take effect; "
                       "check managed settings and CLI compatibility")
    try:
        servers = json.loads(probe([codex, *codex_options(), "mcp", "list", "--json"]))
    except ValueError as exc:
        raise RunError("Cannot verify Codex MCP configuration") from exc
    if servers != []:
        raise RunError("Codex MCP restriction did not take effect")
    check_claude_managed_files()
    remote = verify_claude_remote(probe([claude, *claude_options(settings), "doctor"]))
    check_claude_managed_files()
    return {
        "id": POLICY_ID,
        "codex": {"overrides": list(CODEX_OVERRIDES),
                  "verified_disabled_features": list(CODEX_DISABLED),
                  "verified_mcp_servers": [],
                  "settings_sources": "CLI overrides; user config and exec rules ignored during review",
                  "probe_settings_sources": "CLI overrides over normal configuration",
                  "authentication": "existing CLI authentication"},
        "claude": {"settings": CLAUDE_SETTINGS, "settings_sources": [],
                   "safe_mode": True, "restricted": True,
                   "tools": ["Read", "Glob", "Grep"], "mcp": "disabled",
                   "managed_settings": remote,
                   "authentication": "existing CLI authentication"},
        "model_and_effort": "Requested values are in manifest.json; effective values are not exposed",
    }

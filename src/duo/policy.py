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
import tempfile
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
# Claude Code tools: read-only inspection, plus web search and fetch with --allow-web.
CLAUDE_TOOLS = ("Read", "Glob", "Grep")
CLAUDE_WEB_TOOLS = ("WebSearch", "WebFetch")
CLAUDE_SETTINGS = {
    "disableAllHooks": True,
    "disableClaudeAiConnectors": True,
    "syncClaudeAiPlugins": False,
    "autoMemoryEnabled": False,
}


# Environment variables a reviewer CLI receives; everything else is dropped, so
# cloud credentials, other tools' tokens, an SSH agent, or NODE_OPTIONS (which can
# make Node load code) do not reach the reviewer. --pass-env adds names explicitly.
ENVIRONMENT = (
    "HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM", "TZ", "LANG", "LANGUAGE",
    # Per-user directories and the session bus, which Linux keyrings use for CLI logins.
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    # Network access to the provider through proxies or private certificate authorities.
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "NODE_USE_SYSTEM_CA",
)
# Each CLI's own configuration and login variables.
CLI_ENVIRONMENT = {
    "codex": ("CODEX_HOME", "CODEX_API_KEY", "OPENAI_API_KEY"),
    "claude": ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
    # GH_HOST selects the GitHub host (Enterprise); COPILOT_GH_HOST overrides it for
    # Copilot only. Its token is added by copilot.environment().
    "copilot": ("COPILOT_GH_HOST", "GH_HOST"),
}


def reviewer_environment(cli: str, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """The environment for a reviewer CLI: the allowlist, its own variables, and extra names."""
    names = set(ENVIRONMENT) | set(CLI_ENVIRONMENT[cli]) | set(extra)
    return {key: value for key, value in os.environ.items()
            if key in names or key.startswith("LC_")}


def codex_overrides(allow_web: bool = False) -> tuple[str, ...]:
    """Codex configuration overrides; --allow-web turns on its live web search tool.

    Sandboxed commands stay read-only and without network access either way.
    """
    if not allow_web:
        return CODEX_OVERRIDES
    return tuple('web_search="live"' if item == 'web_search="disabled"' else item
                 for item in CODEX_OVERRIDES)


def codex_options(allow_web: bool = False) -> list[str]:
    return [item for override in codex_overrides(allow_web) for item in ("-c", override)]


def claude_tools(allow_web: bool = False) -> tuple[str, ...]:
    return CLAUDE_TOOLS + (CLAUDE_WEB_TOOLS if allow_web else ())


def claude_options(settings: Path) -> list[str]:
    return ["--safe-mode", "--restricted", "--setting-sources", "",
            "--settings", str(settings), "--strict-mcp-config", "--no-chrome",
            "--disable-slash-commands"]


# Versions whose inspection controls and output formats were exercised with real
# runs: [first tested, first untested). Later patch releases within a tested minor
# version are accepted; the help and feature probes still verify every control on
# each run. Codex is 0.x, where a minor bump may break compatibility, so no range
# spans minor versions. Widen a range only with probe and live-run evidence.
TESTED_VERSIONS = {
    "codex": ((0, 162, 1), (0, 163, 0)),
    "claude": ((2, 1, 296), (2, 2, 0)),
    "copilot": ((1, 0, 95), (1, 1, 0)),
}


def tested_range(agent: str) -> str:
    low, high = TESTED_VERSIONS[agent]
    return f"{'.'.join(map(str, low))} to before {'.'.join(map(str, high))}"


def verify_version(agent: str, version: str, allow_untested: bool = False) -> str:
    """Return "tested", or "untested" when explicitly allowed; otherwise refuse."""
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    if not match:
        raise RunError(f"Cannot parse the {agent} version; tested: {tested_range(agent)}")
    actual = tuple(map(int, match.groups()))
    low, high = TESTED_VERSIONS[agent]
    if low <= actual < high:
        return "tested"
    if allow_untested:
        return "untested"
    raise RunError(f"{agent} {'.'.join(map(str, actual))} is outside the tested range "
                   f"({tested_range(agent)}); install a tested version or pass "
                   "--allow-untested-cli to run it as experimental")


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
                    probe: Callable[..., str], agents: set[str] | None = None,
                    pass_env: tuple[str, ...] = (), allow_web: bool = False) -> dict:
    """Verify the inspection controls; probe(argv, env=None) returns a CLI's stdout.

    Without env, probe must run the CLI in its reviewer environment.
    """
    agents = {"codex", "claude"} if agents is None else agents
    policy = {
        "id": POLICY_ID,
        "model_and_effort": "Requested values are in manifest.json; effective values are not exposed",
        "web": allow_web,
    }
    if "codex" in agents:
        help_text = probe([codex, "exec", "--help"])
        flags = ("--sandbox", "--ephemeral", "--json", "--output-schema", "--ignore-rules",
                 "--ignore-user-config", "--strict-config")
        if any(flag not in help_text for flag in flags):
            raise RunError("Installed codex lacks required inspection options")
        effective = probe([codex, *codex_options(allow_web), "features", "list"])
        features = {parts[0]: parts[-1] for line in effective.splitlines()
                    if len(parts := line.split()) >= 3}
        if any(features.get(name) != "false" for name in CODEX_DISABLED):
            raise RunError("Codex inspection feature restrictions did not take effect; "
                           "check managed settings and CLI compatibility")
        # Reviews skip $CODEX_HOME/config.toml (--ignore-user-config), which
        # `mcp list` cannot do, and -c mcp_servers={} does not remove servers a
        # configuration layer defines. An empty CODEX_HOME loads what a review
        # loads: managed and system configuration, no user configuration, and no
        # projects it trusts.
        with tempfile.TemporaryDirectory(prefix="duo-codex-home-") as home:
            listing = probe([codex, *codex_options(allow_web), "mcp", "list", "--json"],
                            env={**reviewer_environment("codex", pass_env), "CODEX_HOME": home})
        try:
            servers = json.loads(listing)
        except ValueError as exc:
            raise RunError("Cannot verify Codex MCP configuration") from exc
        if servers != []:
            raise RunError("Codex MCP servers are configured outside user configuration "
                           "(for example, managed configuration); Duo cannot disable them")
        policy["codex"] = {
            "overrides": list(codex_overrides(allow_web)),
            "verified_disabled_features": list(CODEX_DISABLED),
            "verified_mcp_servers": [],
            "settings_sources": "CLI overrides; user config and exec rules ignored during review",
            "probe_settings_sources": "CLI overrides over normal configuration; "
                                      "MCP servers listed without user configuration, as in reviews",
            "authentication": "existing CLI authentication",
        }
    if "claude" in agents:
        help_text = probe([claude, "--help"])
        flags = ("--safe-mode", "--restricted", "--setting-sources", "--settings",
                 "--strict-mcp-config", "--no-chrome", "--disable-slash-commands",
                 "--tools", "--allowedTools")
        if any(flag not in help_text for flag in flags):
            raise RunError("Installed claude lacks required inspection options")
        check_claude_managed_files()
        remote = verify_claude_remote(probe([claude, *claude_options(settings), "doctor"]))
        check_claude_managed_files()
        policy["claude"] = {
            "settings": CLAUDE_SETTINGS, "settings_sources": [],
            "safe_mode": True, "restricted": True, "tools": list(claude_tools(allow_web)),
            "mcp": "disabled", "managed_settings": remote,
            "authentication": "existing CLI authentication",
        }
    return policy

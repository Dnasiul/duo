"""Copilot-specific command and inspection configuration (no model calls here)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

from .models import Agent, Config, RunError
from .policy import reviewer_environment, verify_version
from .storage import atomic_write

SETTINGS = {
    'disableAllHooks': True, 'connectors': False, 'memory': False,
    'autoUpdate': False, 'continueOnAutoMode': False,
    'customAgents': {'defaultLocalOnly': True}, 'ide': {'autoConnect': False},
}
AUTH_VARIABLES = ('COPILOT_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN')
FLAGS = ('--no-auto-update', '--silent', '--stream', '--output-format', '--model',
         '--available-tools', '--deny-tool', '--disable-builtin-mcps',
         '--disable-mcp-server', '--no-ask-user', '--no-custom-instructions', '--no-remote',
         '--no-remote-export', '--disallow-temp-dir', '--no-experimental', '--mode', '--log-dir')


def workspace_roots(repo: Path) -> list[Path]:
    """The repository and its parents up to the Git root, or only the repository.

    Stopping there keeps user-level files such as ~/.claude/settings.json out of
    the repository policy check.
    """
    roots = []
    for root in (repo, *repo.parents):
        roots.append(root)
        if (root / '.git').exists():
            return roots
    return [repo]


def check_sources(repo: Path) -> None:
    """Refuse executable policy and project overrides that could broaden inspection."""
    system = Path('/Library/Application Support/GitHubCopilot/managed-settings.json') if sys.platform == 'darwin' else Path('/etc/github-copilot/managed-settings.json')
    for path in (system, Path('/etc/github-copilot/policy.d')):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise RunError('Copilot managed settings or policy hooks detected; cannot establish inspection restrictions')
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(['/usr/bin/defaults', 'read', 'com.github.copilot'],
                                    capture_output=True, text=True, timeout=15)
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise RunError('Cannot verify Copilot managed preferences') from exc
        if result.returncode != 1 or 'Domain com.github.copilot does not exist' not in result.stderr:
            raise RunError('Copilot managed preferences detected or unreadable')
    # An isolated home is never trusted, and prompt-mode extensions are off.
    # Still reject configuration that could override our hook/plugin policy.
    for root in workspace_roots(repo):
        for name in ('.github/copilot/settings.json', '.github/copilot/settings.local.json',
                     '.claude/settings.json', '.claude/settings.local.json'):
            path = root / name
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError) as exc:
                raise RunError(f'Cannot verify Copilot repository settings in {path}; this policy requires plain JSON') from exc
            if (value.get('disableAllHooks') is False or value.get('enabledPlugins') or
                    value.get('extraKnownMarketplaces') or value.get('statusLine') or
                    value.get('env') or value.get('policyHelper')):
                raise RunError(f'Copilot repository settings conflict with inspection policy: {path}')


def token_variable() -> str | None:
    """The variable holding the token Copilot uses: the first set of AUTH_VARIABLES."""
    return next((key for key in AUTH_VARIABLES if os.environ.get(key, '').strip()), None)


def environment(directory: Path, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Fresh config/cache per invocation, the reviewer environment, and one token.

    Only the token Copilot would use (first of AUTH_VARIABLES) is passed, under its
    own name; other COPILOT_* settings and provider overrides are dropped.
    """
    home = directory / 'copilot-home'
    home.mkdir(mode=0o700)
    atomic_write(home / 'settings.json', (json.dumps(SETTINGS) + '\n').encode())
    env = reviewer_environment('copilot', extra)
    token = token_variable()
    if token:
        env[token] = os.environ[token]
    env.update(COPILOT_HOME=str(home), COPILOT_CACHE_HOME=str(directory / 'copilot-cache'),
               COPILOT_AUTO_UPDATE='false', COPILOT_ALLOW_ALL='false',
               GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS='false',
               GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS='false',
               COPILOT_MCP_TOOL_CACHE='false', USE_TGREP='false', USE_BUILTIN_RIPGREP='true')
    return env


def require_auth(env: dict[str, str]) -> None:
    if not any(env.get(key, '').strip() for key in AUTH_VARIABLES):
        raise RunError('Copilot inspection mode uses an isolated configuration. Set COPILOT_GITHUB_TOKEN, '
                       'GH_TOKEN, or GITHUB_TOKEN to a token with Copilot access; saved interactive login is not copied')


def mcp_names(repo: Path) -> list[str]:
    names = set()
    for root in workspace_roots(repo):
        for name in ('.mcp.json', '.github/mcp.json'):
            try:
                data = (root / name).read_bytes()
            except FileNotFoundError:
                continue
            try:
                value = json.loads(data)
                servers = value.get('mcpServers', {})
                if not isinstance(servers, dict):
                    raise ValueError()
                names.update(servers)
            except (ValueError, AttributeError) as exc:
                raise RunError('Cannot verify Copilot repository MCP configuration') from exc
    return sorted(names)


def discard_runtime(directory: Path) -> None:
    """Delete the runtime Copilot unpacks into each fresh cache (about 165 MiB).

    It is the CLI's own program, not review output; keeping a copy per attempt
    filled the audit directory.
    """
    runtime = directory / 'copilot-cache' / 'pkg'
    if runtime.is_symlink():
        runtime.unlink()
    elif runtime.is_dir():
        shutil.rmtree(runtime, ignore_errors=True)


def command(config: Config, agent: Agent, directory: Path) -> list[str]:
    argv = [config.copilot_bin, '--no-auto-update', '--silent', '--stream', 'off',
            '--output-format', 'json', '--model', getattr(config, f'{agent}_model'),
            # List options take separate values; read-only access needs no allow rule.
            '--available-tools', 'view', 'glob', 'grep',
            '--deny-tool', 'write', 'shell', 'url', '--disable-builtin-mcps', '--no-ask-user',
            '--no-custom-instructions', '--no-remote', '--no-remote-export',
            '--disallow-temp-dir', '--no-experimental', '--mode', 'interactive',
            '--log-dir', str(directory / 'copilot-logs')]
    argv += [f'--disable-mcp-server={name}' for name in mcp_names(config.repo)]
    effort = getattr(config, f'{agent}_effort')
    if effort is not None:
        argv += ['--reasoning-effort', effort]
    # Copilot supports piped stdin as a programmatic prompt. No prompt in argv.
    return argv


def preflight(config: Config, directory: Path) -> tuple[str, dict]:
    try:
        return _preflight(config, directory)
    finally:
        discard_runtime(directory)


def _preflight(config: Config, directory: Path) -> tuple[str, dict]:
    check_sources(config.repo)
    disabled_servers = mcp_names(config.repo)
    env = environment(directory, config.pass_env)
    def probe(args: list[str]) -> str:
        try:
            result = subprocess.run([config.copilot_bin, '--no-auto-update', *args],
                                    env=env, cwd=config.repo, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=30)
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise RunError('Cannot verify Copilot CLI inspection configuration') from exc
        if result.returncode:
            raise RunError('Copilot configuration probe failed')
        return result.stdout
    version_lines = probe(['--version']).splitlines()
    version = version_lines[0] if version_lines else ''
    status = verify_version('copilot', version, config.allow_untested_cli)
    help_text = probe(['--help'])
    if any(flag not in help_text for flag in FLAGS):
        raise RunError('Installed Copilot lacks required inspection options')
    if any(getattr(config, f'{agent}_backend') == 'copilot' and
           getattr(config, f'{agent}_effort') is not None for agent in ('codex', 'claude')):
        if '--reasoning-effort' not in help_text:
            raise RunError('Installed Copilot does not support reasoning effort')
    try:
        settings = json.loads(probe(['config', '--list', '--json']))
    except ValueError as exc:
        raise RunError('Copilot returned invalid configuration metadata') from exc
    if not isinstance(settings, dict) or any(settings.get(k) != v for k, v in SETTINGS.items()):
        raise RunError('Copilot inspection settings did not take effect')
    return version, {
        'version_status': status,
        'settings': SETTINGS, 'tools': ['view', 'glob', 'grep'],
        'configuration': 'fresh isolated home and cache per invocation',
        'authentication': 'environment token; values never included in audit metadata',
        'extensions': 'disabled; no inherited plugins, MCP servers or trusted folders',
        'disabled_workspace_mcp_servers': disabled_servers,
        'response': 'final answer from the JSONL event stream, validated locally; no native schema-enforcement flag',
    }

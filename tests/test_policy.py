from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from duo.adapters import CLIAdapter, classify_failure
from duo.models import Config, RunError
from duo.policy import (CLAUDE_SETTINGS, CODEX_DISABLED, check_claude_managed_files,
                        claude_options, codex_options, verify_claude_remote,
                        verify_controls, verify_version)
from duo.storage import RunStore

HELP = ('--sandbox --ephemeral --json --output-schema --ignore-rules --ignore-user-config --strict-config '
        '--safe-mode --restricted --setting-sources --settings --strict-mcp-config '
        '--no-chrome --disable-slash-commands --tools --allowedTools')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.store = RunStore(self.root / 'audit')
        self.config = Config(self.repo, 'codex-test', 'claude-test', skip_git_repo_check=True)
        patcher = patch('duo.policy.sys.platform', 'linux')
        patcher.start()
        self.addCleanup(patcher.stop)

    def probe(self, argv):
        if '--help' in argv:
            return HELP
        if 'features' in argv:
            return '\n'.join(name + ' stable false' for name in CODEX_DISABLED)
        if 'mcp' in argv:
            return '[]'
        if 'doctor' in argv:
            self.assertIn('--safe-mode', argv)
            self.assertIn('--restricted', argv)
            self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
            return 'Managed settings (remote): none configured for this organization\n'
        self.fail(f'Unexpected probe {argv}')

    def test_versions_require_known_control_generation(self):
        verify_version('codex', 'codex-cli 0.162.1')
        verify_version('claude', '2.1.296 (Claude Code)')
        verify_version('copilot', 'GitHub Copilot CLI 1.0.95.')
        # Later releases in the same major version rely on the control probes.
        verify_version('codex', 'codex-cli 0.170.0')
        verify_version('claude', '2.2.0 (Claude Code)')
        verify_version('copilot', 'GitHub Copilot CLI 1.1.0.')
        for agent, version in [('codex', '0.100.0'), ('claude', '2.0.0'),
                               ('claude', 'unrecognized'), ('codex', '1.0.0'),
                               ('claude', '3.0.0'), ('copilot', '1.0.94'), ('copilot', '2.0.0')]:
            with self.subTest(agent=agent, version=version), self.assertRaises(RunError):
                verify_version(agent, version)

    def test_policy_audit_has_verified_controls_without_secrets(self):
        with patch('duo.policy.check_claude_managed_files'):
            policy = verify_controls('codex', 'claude', self.root / 'settings.json', self.probe)
        self.assertEqual(policy['codex']['verified_mcp_servers'], [])
        self.assertEqual(policy['claude']['settings_sources'], [])
        self.assertEqual(policy['claude']['tools'], ['Read', 'Glob', 'Grep'])
        self.assertEqual(policy['claude']['settings']['disableAllHooks'], True)
        self.assertIn('effective values are not exposed', policy['model_and_effort'])

    def test_rejects_missing_or_ineffective_controls(self):
        for failure in ('help', 'feature_true', 'feature_missing', 'mcp', 'mcp_invalid', 'doctor'):
            def probe(argv):
                if '--help' in argv and failure == 'help':
                    return ''
                if 'features' in argv and failure.startswith('feature_'):
                    text = self.probe(argv)
                    return text.replace('hooks stable false', 'hooks stable true' if failure == 'feature_true' else '')
                if 'mcp' in argv and failure.startswith('mcp'):
                    return 'invalid' if failure == 'mcp_invalid' else '[{"name":"SECRET", "enabled":true}]'
                if 'doctor' in argv and failure == 'doctor':
                    return 'Managed settings (remote): loaded'
                return self.probe(argv)
            with self.subTest(failure=failure), patch('duo.policy.check_claude_managed_files'), self.assertRaises(RunError) as caught:
                verify_controls('codex', 'claude', self.root / 'settings.json', probe)
            self.assertNotIn('SECRET', str(caught.exception))

    def test_remote_policy_unknown_stale_and_loaded_fail_closed(self):
        for report in ('', 'Managed settings (remote): loaded',
                       'Managed settings (remote): fetch failed — using stale cache',
                       'Managed settings (remote): checking…',
                       'Managed settings (remote): fetch failed — no policy applied'):
            with self.subTest(report=report), self.assertRaises(RunError):
                verify_claude_remote(report)
        for state in ('none configured for this organization',
                      'not fetched — requires an Enterprise or Team subscription',
                      'not fetched — no usable credentials for the settings fetch'):
            self.assertEqual(verify_claude_remote('Managed settings (remote): ' + state), state)

    def test_managed_files_and_unreadable_policy_rejected(self):
        for failure in (None, PermissionError('private')):
            with self.subTest(failure=failure), patch.object(Path, 'lstat', return_value=Mock(), side_effect=failure), self.assertRaises(RunError):
                check_claude_managed_files()
        for contents in (b'{"hooks":{"SessionStart":[]}}', b'invalid', b'{"settings":{}}'):
            with self.subTest(contents=contents), patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'read_bytes', return_value=contents), self.assertRaises(RunError):
                check_claude_managed_files()
        with patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'read_bytes', side_effect=FileNotFoundError):
            check_claude_managed_files()

    def test_mac_preferences_must_be_absent(self):
        for code, stderr, valid in ((0, '', False), (1, 'Permission denied', False),
                                    (1, 'Domain com.anthropic.claudecode does not exist', True)):
            with self.subTest(code=code, stderr=stderr), patch('duo.policy.sys.platform', 'darwin'), patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'read_bytes', side_effect=FileNotFoundError), patch('duo.policy.subprocess.run', return_value=subprocess.CompletedProcess([], code, '', stderr)):
                if valid:
                    check_claude_managed_files()
                else:
                    with self.assertRaises(RunError):
                        check_claude_managed_files()

    def test_preflight_records_policy_and_versions(self):
        def run(argv, **kwargs):
            if '--version' in argv:
                version = 'codex-cli 0.162.1' if argv[0] == 'codex' else '2.1.296 (Claude Code)'
                return subprocess.CompletedProcess(argv, 0, version, '')
            return subprocess.CompletedProcess(argv, 0, self.probe(argv), '')
        adapter = CLIAdapter(self.config, self.store)
        with patch('duo.adapters.shutil.which', side_effect=lambda name: name), patch('duo.adapters.subprocess.run', side_effect=run), patch('duo.adapters.check_claude_managed_files'), patch('duo.policy.check_claude_managed_files'):
            versions = adapter.preflight()
        audit = json.loads((self.store.path / 'reviewer-policy.json').read_text())
        self.assertEqual(audit['versions'], versions)
        self.assertEqual(json.loads(adapter.settings_path.read_text()), CLAUDE_SETTINGS)
        self.assertEqual(adapter.settings_path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(adapter.policy_verified)
        for agent in ('codex', 'claude'):
            argv = adapter.command(agent, self.store.attempt_dir())
            self.assertNotIn('--resume', argv)
            self.assertNotIn('--dangerously-skip-permissions', argv)
            if agent == 'codex':
                self.assertIn('--ignore-user-config', argv)
                self.assertIn('--ignore-rules', argv)
                self.assertIn('--no-daemon', argv)
                self.assertIn('notify=[]', argv)
                for flag in codex_options():
                    self.assertIn(flag, argv)
            else:
                for flag in claude_options(adapter.settings_path):
                    self.assertIn(flag, argv)
                self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Glob,Grep')

    def test_setup_failure_prevents_any_review_invocation(self):
        adapter = CLIAdapter(self.config, self.store)
        with patch.object(adapter, 'preflight', side_effect=RunError('Unsupported policy')), patch('duo.adapters.run_process') as run:
            with self.assertRaises(RunError):
                adapter('codex', 'Review', 5)
        run.assert_not_called()
        self.assertFalse(list(self.store.path.glob('attempt-*')))

    def test_probe_failure_is_sanitized(self):
        adapter = CLIAdapter(self.config, self.store)
        for failure in (subprocess.CompletedProcess(['agent'], 1, 'secret', 'SECRET'),
                        subprocess.TimeoutExpired(['agent'], 30)):
            kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch('duo.adapters.subprocess.run', **kwargs), self.assertRaises(RunError) as caught:
                adapter.probe(['agent'])
            self.assertNotIn('SECRET', str(caught.exception))
        self.assertFalse(classify_failure('Error loading config: requirements.toml SECRET').retryable)

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from test_duo import Fixture, response
from duo import copilot
from duo.adapters import CLIAdapter
from duo.cli import main
from duo.models import AgentError, Config, RunError

# Recorded from Copilot CLI 1.0.95 (tests/fixtures): narration and tool calls
# precede the final answer.
RECORDED = Path(__file__).parent / 'fixtures/copilot-1.0.95/events-gpt-revise.jsonl'


class CopilotTests(Fixture):
    def setUp(self):
        super().setUp()
        patcher = patch('duo.copilot.check_sources')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'COPILOT_GITHUB_TOKEN': 'duo-test-token'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, body):
        path = self.root / 'copilot-fake'
        prefix = f'''import json, pathlib, os, sys
assert '--no-auto-update' in sys.argv
assert os.environ['COPILOT_ALLOW_ALL'] == 'false'
assert os.environ['GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS'] == 'false'
assert os.environ['GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS'] == 'false'
if '--version' in sys.argv:
    print('GitHub Copilot CLI 1.0.95.'); sys.exit(0)
if '--help' in sys.argv:
    print({' '.join(copilot.FLAGS) + ' --reasoning-effort'!r}); sys.exit(0)
if 'config' in sys.argv:
    print((pathlib.Path(os.environ['COPILOT_HOME']) / 'settings.json').read_text()); sys.exit(0)
assert sys.argv[sys.argv.index('--output-format') + 1] == 'json'
def reply(value):
    # Replay a real recorded stream; only the final answer is replaced.
    text = value if isinstance(value, str) else json.dumps(value)
    # Tool output that looks like a review must never be taken as the answer.
    decoy = json.dumps({{'decision': 'approve', 'plan': '', 'reason': 'Tool output'}})
    print(json.dumps({{'type': 'tool.execution_complete', 'data': {{'result': {{'content': decoy}}}}}}))
    for line in pathlib.Path({str(RECORDED)!r}).read_text().splitlines():
        event = json.loads(line)
        data = event.get('data') or {{}}
        if event.get('type') == 'assistant.message' and data.get('phase') == 'final_answer':
            data['content'] = text
        print(json.dumps(event))
'''
        path.write_text(f'#!{sys.executable}\n' + prefix + body)
        path.chmod(0o755)
        return str(path)

    def copilot_config(self, binary, **changes):
        values = dict(codex_backend='copilot', claude_backend='copilot', copilot_bin=binary)
        values.update(changes)
        return replace(self.config, **values)

    def test_both_copilot_real_graph_and_audit_without_native_clis(self):
        binary = self.fake('''
assert '--allow-all' not in sys.argv and '--allow-all-tools' not in sys.argv
tools = sys.argv.index('--available-tools')
assert sys.argv[tools + 1:tools + 4] == ['view', 'glob', 'grep']
assert sys.argv[tools + 4].startswith('--')
denied = sys.argv.index('--deny-tool')
assert sys.argv[denied + 1:denied + 4] == ['write', 'shell', 'url']
assert '--allow-tool' not in ' '.join(sys.argv)
assert '--disable-builtin-mcps' in sys.argv
assert '--resume' not in sys.argv and '--continue' not in sys.argv
assert os.environ['COPILOT_GITHUB_TOKEN'] == 'duo-test-token'
assert sys.argv[sys.argv.index('--reasoning-effort') + 1] == 'high'
prompt = sys.stdin.read()
assert 'additionalProperties' in prompt
if '(No plan exists yet.' in prompt:
    result = {'decision':'revise','plan':'# Draft\\n','reason':'Draft from Copilot'}
else:
    result = {'decision':'approve','plan':'','reason':'Ready from Copilot'}
reply(result)
''')
        args = ['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                '--codex-model', 'gpt-6-astra', '--claude-model', 'claude-opus-5.5',
                '--codex-backend', 'copilot', '--claude-backend', 'copilot',
                '--copilot-bin', binary, '--codex-bin', '/missing/codex', '--claude-bin', '/missing/claude',
                '--codex-effort', 'high', '--claude-effort', 'high',
                '--skip-git-repo-check', '--json', '--show-conversation']
        output, conversation = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(conversation):
            self.assertEqual(main(args), 0, output.getvalue())
        summary = json.loads(output.getvalue())
        self.assertEqual(summary['turns'], 3)
        self.assertEqual(summary['latest_review']['backend'], 'copilot')
        self.assertIn('codex via Copilot', conversation.getvalue())
        self.assertIn('claude via Copilot', conversation.getvalue())
        run = Path(summary['run_dir'])
        state = json.loads((run / 'result.json').read_text())
        self.assertEqual([t['agent'] for t in state['history']], ['codex', 'claude', 'codex'])
        self.assertEqual([t['model'] for t in state['history']], ['gpt-6-astra', 'claude-opus-5.5', 'gpt-6-astra'])
        self.assertEqual(json.loads((run / 'versions.json').read_text()), {'copilot': 'GitHub Copilot CLI 1.0.95.'})
        self.assertFalse((run / 'claude-settings.json').exists())
        homes = list(run.glob('attempt-*/copilot-home'))
        self.assertEqual(len(homes), 3)
        self.assertEqual(len(set(homes)), 3)
        for path in run.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'duo-test-token', path.read_bytes())

    def test_mixed_backends_only_probe_selected_native_cli(self):
        native_binary = self.executable('native', '''import json, pathlib, sys
sys.stdin.read()
result = {'decision':'approve','plan':'','reason':'Native'}
if 'exec' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
else:
    print(json.dumps({'is_error':False,'subtype':'success','structured_output':result}))
''')
        copilot_binary = self.fake("sys.stdin.read()\nreply({'decision':'approve','plan':'','reason':'Copilot'})\n")
        for native_agent in ('codex', 'claude'):
            config = self.copilot_config(copilot_binary, **{f'{native_agent}_backend': 'native'})
            # Give the unused native slot a definitely absent executable.
            unused = 'claude' if native_agent == 'codex' else 'codex'
            config = replace(config, **{f'{unused}_bin': '/missing/unused'})
            adapter = CLIAdapter(config, self.store)
            versions = adapter.preflight()
            self.assertEqual(set(versions), {native_agent, 'copilot'})
            self.assertEqual(adapter(native_agent, 'Review', 5)['reason'], 'Native')
            self.assertEqual(adapter(unused, 'Review', 5)['reason'], 'Copilot')

    def test_copilot_response_failures(self):
        ready = {'decision': 'approve', 'plan': '', 'reason': 'Ready'}
        for body in (f"print({json.dumps(ready)!r})",  # bare text, not an event stream
                     "reply('```json\\n{}\\n```')", "reply('{}')", "reply('not JSON')",
                     # Narration before the object is not a valid answer.
                     f"reply({('Checking files. ' + json.dumps(ready))!r})",
                     "print(json.dumps({'type':'assistant.message','data':{'content':'Done',"
                     "'phase':'commentary','toolRequests':[]}}))",
                     f"reply({ready!r}); sys.exit(1)"):
            binary = self.fake(body)
            adapter = CLIAdapter(self.copilot_config(binary), self.store)
            with self.subTest(body=body), self.assertRaises(AgentError):
                adapter('codex', 'Review', 5)

    def test_copilot_final_answer_is_read_from_events(self):
        # Multi-line plans and progress lines broke the terminal text format.
        plan = '1. Add subtract(a, b) to calc.py, next to add(a, b), in the same style.\n' * 3
        reply = {'decision': 'revise', 'plan': plan, 'reason': 'No plan existed'}
        for text in (json.dumps(reply), f'```json\n{json.dumps(reply)}\n```',
                     f'\n```\n{json.dumps(reply)}\n```\n'):
            binary = self.fake(f"print('progress, not JSON')\nreply({text!r})")
            adapter = CLIAdapter(self.copilot_config(binary), self.store)
            with self.subTest(text=text):
                self.assertEqual(adapter('codex', 'Review', 5)['plan'], plan)

    def test_model_text_on_stdout_never_marks_failure_permanent(self):
        binary = self.fake("print('The plan handles unauthorized access with no authentication bypass')\n"
                           "sys.exit(1)\n")
        adapter = CLIAdapter(self.copilot_config(binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter('codex', 'Review', 5)
        self.assertTrue(caught.exception.retryable)

    def test_unavailable_model_is_not_retried(self):
        # Copilot model names differ from native ones, e.g. claude-sonnet-5.5.
        binary = self.fake("print('Error: Model \"claude-sonnet-5-5\" from --model flag is not available.', "
                           "file=sys.stderr)\nsys.exit(1)\n")
        config = replace(self.copilot_config(binary), claude_model='claude-sonnet-5-5')
        with self.assertRaises(AgentError) as caught:
            CLIAdapter(config, self.store)('claude', 'Review', 5)
        self.assertFalse(caught.exception.retryable)
        self.assertIn('claude-sonnet-5-5', str(caught.exception))

    def test_native_and_copilot_effort_validation(self):
        for agent in ('codex', 'claude'):
            for effort in copilot_efforts():
                Config(self.repo, 'c', 'a', **{f'{agent}_backend': 'copilot', f'{agent}_effort': effort})
            with self.assertRaises(ValueError):
                Config(self.repo, 'c', 'a', **{f'{agent}_backend': 'copilot', f'{agent}_effort': 'ultra'})
            with self.assertRaises(ValueError):
                Config(self.repo, 'c', 'a', **{f'{agent}_effort': 'minimal'})
        for extra in ({'claude_max_budget_usd': 1}, {'claude_max_turns': 4}, {'claude_max_turns': 20}):
            with self.assertRaises(ValueError):
                Config(self.repo, 'c', 'a', claude_backend='copilot', **extra)
        with self.assertRaises(ValueError):
            Config(self.repo, 'auto', 'a', codex_backend='copilot')

    def test_isolated_environment_drops_custom_providers_and_permissions(self):
        directory = self.store.attempt_dir()
        with patch.dict(os.environ, {'COPILOT_ALLOW_ALL':'true', 'COPILOT_PROVIDER_API_KEY_COMMAND':'bad',
                                     'COPILOT_PROVIDERS_CONFIG':'bad', 'COPILOT_PLAN_THEN_AUTOPILOT':'true',
                                     'GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS':'true'}):
            env = copilot.environment(directory)
        self.assertEqual(env['COPILOT_ALLOW_ALL'], 'false')
        self.assertEqual(env['GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS'], 'false')
        self.assertNotIn('COPILOT_PROVIDER_API_KEY_COMMAND', env)
        self.assertNotIn('COPILOT_PROVIDERS_CONFIG', env)
        self.assertNotIn('COPILOT_PLAN_THEN_AUTOPILOT', env)
        self.assertEqual(env['COPILOT_GITHUB_TOKEN'], 'duo-test-token')
        self.assertEqual(json.loads((Path(env['COPILOT_HOME']) / 'settings.json').read_text()), copilot.SETTINGS)

    def test_authentication_missing_does_not_invoke_model(self):
        adapter = CLIAdapter(self.copilot_config('missing'), self.store)
        adapter.policy_verified = True
        with patch.dict(os.environ, {key: '' for key in copilot.AUTH_VARIABLES}), patch('duo.adapters.run_process') as run:
            with self.assertRaisesRegex(RunError, 'COPILOT_GITHUB_TOKEN'):
                adapter('claude', 'Review', 5)
        run.assert_not_called()

    def test_workspace_mcp_servers_explicitly_disabled(self):
        (self.repo / '.mcp.json').write_text(json.dumps({'mcpServers': {'sample': {'command': 'bad'}}}))
        argv = copilot.command(self.copilot_config('copilot'), 'codex', self.store.attempt_dir())
        self.assertIn('--disable-mcp-server=sample', argv)
        (self.repo / '.mcp.json').write_text('invalid')
        with self.assertRaises(RunError):
            copilot.command(self.copilot_config('copilot'), 'codex', self.store.attempt_dir())

    def test_untested_copilot_version_is_opt_in_and_reported(self):
        binary = self.fake("sys.stdin.read()\nreply({'decision':'approve','plan':'','reason':'Ready'})\n")
        path = Path(binary)
        path.write_text(path.read_text().replace("GitHub Copilot CLI 1.0.95.", "GitHub Copilot CLI 1.2.0."))
        (self.repo / 'plan.md').write_text('# Plan\n')
        args = ['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                '--codex-model', 'gpt-5.4', '--claude-model', 'claude-sonnet-5.5',
                '--codex-backend', 'copilot', '--claude-backend', 'copilot',
                '--copilot-bin', binary, '--skip-git-repo-check', '--json']
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.assertIn('--allow-untested-cli', json.loads(output.getvalue())['message'])
        output, warnings = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(warnings):
            self.assertEqual(main(args + ['--allow-untested-cli']), 0, output.getvalue())
        summary = json.loads(output.getvalue())
        self.assertEqual(summary['untested_cli'], {'copilot': 'GitHub Copilot CLI 1.2.0.'})
        self.assertIn('untested CLI versions as experimental: copilot', warnings.getvalue())
        run = Path(summary['run_dir'])
        self.assertEqual(json.loads((run / 'versions.json').read_text())['untested'], ['copilot'])
        self.assertEqual(json.loads((run / 'result.json').read_text())['untested_cli'],
                         {'copilot': 'GitHub Copilot CLI 1.2.0.'})

    def test_preflight_rejects_missing_controls(self):
        binary = self.fake('')
        # Remove a required flag from only the advertised help.
        path = Path(binary)
        path.write_text(path.read_text().replace('--available-tools', '--removed-control'))
        with self.assertRaisesRegex(RunError, 'required inspection'):
            CLIAdapter(self.copilot_config(binary), self.store).preflight()


def copilot_efforts():
    from duo.models import COPILOT_EFFORTS
    return COPILOT_EFFORTS


# Keep source-policy checks independent of the host's actual managed settings.
import unittest
from duo.copilot import check_sources as check_copilot_sources


class CopilotSourceTests(unittest.TestCase):
    def test_policy_hooks_are_rejected_before_cli_start(self):
        with patch('duo.copilot.sys.platform', 'linux'), patch.object(Path, 'lstat', return_value=object()):
            with self.assertRaisesRegex(RunError, 'managed settings or policy hooks'):
                check_copilot_sources(Path('/unused'))

    def test_repository_overrides_cannot_reenable_hooks_or_plugins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            settings = repo / '.claude/settings.json'
            settings.parent.mkdir()
            for value in ({'disableAllHooks':False}, {'enabledPlugins':{'plugin':True}},
                          {'extraKnownMarketplaces':{'x':{}}}, {'env':{'INJECT':'value'}}):
                settings.write_text(json.dumps(value))
                with self.subTest(value=value), patch('duo.copilot.sys.platform', 'linux'), patch.object(Path, 'lstat', side_effect=FileNotFoundError):
                    with self.assertRaisesRegex(RunError, 'conflict'):
                        check_copilot_sources(repo)
            settings.write_text('{"disableAllHooks":true}')
            with patch('duo.copilot.sys.platform', 'linux'), patch.object(Path, 'lstat', side_effect=FileNotFoundError):
                check_copilot_sources(repo)

    def test_source_checks_stop_at_git_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            # User-level settings above the repository are not repository policy.
            (home / '.claude').mkdir()
            (home / '.claude/settings.json').write_text(json.dumps({'statusLine': {'type': 'command'}}))
            (home / '.mcp.json').write_text('invalid')
            repo = home / 'work/repo'
            (repo / '.git').mkdir(parents=True)
            sub = repo / 'pkg'
            sub.mkdir()
            self.assertEqual(copilot.workspace_roots(sub), [sub, repo])
            self.assertEqual(copilot.workspace_roots(home / 'work'), [home / 'work'])
            with patch('duo.copilot.sys.platform', 'linux'), patch.object(Path, 'lstat', side_effect=FileNotFoundError):
                check_copilot_sources(sub)
            self.assertEqual(copilot.mcp_names(sub), [])
            (repo / '.claude').mkdir()
            (repo / '.claude/settings.json').write_text(json.dumps({'statusLine': {'type': 'command'}}))
            with patch('duo.copilot.sys.platform', 'linux'), patch.object(Path, 'lstat', side_effect=FileNotFoundError):
                with self.assertRaisesRegex(RunError, 'conflict'):
                    check_copilot_sources(sub)

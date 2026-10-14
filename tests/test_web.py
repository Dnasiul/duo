"""--allow-web gives reviewers web tools; without it nothing changes."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
from unittest.mock import patch

from test_duo import Fixture, response
from duo import copilot
from duo.adapters import CLIAdapter
from duo.cli import main
from duo.policy import CODEX_DISABLED, CODEX_OVERRIDES, codex_overrides, verify_controls
from duo.prompts import INSTRUCTIONS, WEB, build_prompt, instructions
from duo.workflow import initial_state

HELP = ('--sandbox --ephemeral --json --output-schema --ignore-rules --ignore-user-config --strict-config '
        '--safe-mode --restricted --setting-sources --settings --strict-mcp-config '
        '--no-chrome --disable-slash-commands --tools --allowedTools')


def values(argv, flag):
    """The values after a list option, up to the next option."""
    start = argv.index(flag) + 1
    end = next((i for i in range(start, len(argv)) if argv[i].startswith('--')), len(argv))
    return argv[start:end]


class WebTests(Fixture):
    def commands(self, allow_web):
        config = replace(self.config, allow_web=allow_web)
        adapter = CLIAdapter(config, self.store)
        native = {agent: adapter.command(agent, self.store.attempt_dir()) for agent in ('codex', 'claude')}
        via = copilot.command(replace(config, codex_backend='copilot', copilot_bin='copilot'),
                              'codex', self.store.attempt_dir())
        return native['codex'], native['claude'], via

    def test_default_keeps_web_tools_off(self):
        codex, claude, via = self.commands(False)
        self.assertIn('web_search="disabled"', codex)
        self.assertEqual(claude[claude.index('--tools') + 1], 'Read,Glob,Grep')
        self.assertEqual(claude[claude.index('--allowedTools') + 1], 'Read,Glob,Grep')
        self.assertEqual(values(via, '--available-tools'), ['view', 'glob', 'grep'])
        self.assertEqual(values(via, '--deny-tool'), ['write', 'shell', 'url'])
        self.assertNotIn('--allow-all-urls', via)
        self.assertIs(instructions(), INSTRUCTIONS)
        self.assertNotIn(WEB[:40], build_prompt(initial_state('Task', '', '', self.config)))

    def test_allow_web_adds_only_web_tools(self):
        codex, claude, via = self.commands(True)
        self.assertIn('web_search="live"', codex)
        self.assertNotIn('web_search="disabled"', codex)
        # Everything else in the inspection policy is unchanged.
        self.assertEqual(set(codex_overrides(True)) - {'web_search="live"'},
                         set(CODEX_OVERRIDES) - {'web_search="disabled"'})
        self.assertEqual(codex[codex.index('--sandbox') + 1], 'read-only')
        self.assertEqual(claude[claude.index('--tools') + 1], 'Read,Glob,Grep,WebSearch,WebFetch')
        self.assertEqual(claude[claude.index('--allowedTools') + 1], 'Read,Glob,Grep,WebSearch,WebFetch')
        self.assertIn('--restricted', claude)
        self.assertEqual(values(via, '--available-tools'), ['view', 'glob', 'grep', 'web_fetch'])
        self.assertEqual(values(via, '--deny-tool'), ['write', 'shell'])
        self.assertIn('--allow-all-urls', via)
        self.assertIn('--disable-builtin-mcps', via)
        self.assertNotIn('--allow-all-tools', via)

    def test_prompt_states_the_web_rules_in_every_mode(self):
        config = replace(self.config, allow_web=True)
        for mode in ('plan', 'report'):
            with self.subTest(mode=mode):
                prompt = build_prompt(initial_state('Task', '', '', config), mode=mode, web=True)
                text = ' '.join(prompt.split())
                self.assertIn(WEB, text)
                self.assertIn('Never put repository content', text)
        self.assertTrue(instructions(web=True).startswith(INSTRUCTIONS))

    def test_policy_records_and_probes_the_web_setting(self):
        seen = []
        def probe(argv, env=None):
            seen.append(argv)
            if '--help' in argv:
                return HELP
            if 'features' in argv:
                return '\n'.join(name + ' stable false' for name in CODEX_DISABLED)
            if 'mcp' in argv:
                return '[]'
            return 'Managed settings (remote): none configured for this organization\n'
        with patch('duo.policy.check_claude_managed_files'):
            policy = verify_controls('codex', 'claude', self.root / 'settings.json', probe, allow_web=True)
        self.assertTrue(policy['web'])
        self.assertIn('web_search="live"', policy['codex']['overrides'])
        self.assertEqual(policy['claude']['tools'], ['Read', 'Glob', 'Grep', 'WebSearch', 'WebFetch'])
        self.assertTrue(all('web_search="live"' in argv for argv in seen if 'features' in argv or 'mcp' in argv))

    def test_cli_flag_warns_and_records_the_setting(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(CLIAdapter, 'preflight', return_value={}), \
                patch.object(CLIAdapter, '__call__', side_effect=[response('revise', '# Plan\n'),
                                                                 response('approve'), response('approve')]) as agent, \
                redirect_stdout(output), redirect_stderr(errors):
            code = main(['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                         '--codex-model', 'c', '--claude-model', 'a', '--allow-web', '--json'])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn('--allow-web gives reviewers web access', errors.getvalue())
        run = Path(json.loads(output.getvalue())['run_dir'])
        self.assertTrue(json.loads((run / 'manifest.json').read_text())['config']['allow_web'])
        self.assertTrue(all(WEB[:40] in ' '.join(call.args[1].split()) for call in agent.call_args_list))

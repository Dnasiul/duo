from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

from test_duo import Fixture, response

from duo.adapters import CLIAdapter
from duo.cli import main
from duo.models import DOCUMENT_MODES, Config
from duo.prompts import GUIDANCE, INSTRUCTIONS, build_prompt, instructions, protocol
from duo.workflow import initial_state


class ModeTests(Fixture):
    def run_main(self, *extra, replies=None):
        """Run the CLI with the reviewer calls replaced; return code, summary, prompts."""
        replies = replies or [response('revise', '# Draft\n'), response('approve'), response('approve')]
        output = io.StringIO()
        with patch.object(CLIAdapter, 'preflight', return_value={}), \
                patch.object(CLIAdapter, '__call__', side_effect=replies) as agent, \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                         '--codex-model', 'c', '--claude-model', 'a', '--json', *extra])
        prompts = [call.args[1] for call in agent.call_args_list]
        return code, json.loads(output.getvalue()), prompts

    def test_plan_mode_keeps_the_tested_prompt(self):
        state = initial_state('Task', '', '', self.config)
        self.assertIs(instructions(), INSTRUCTIONS)
        self.assertEqual(build_prompt(state).count(INSTRUCTIONS), 1)
        # The plan checklist and protocol say exactly what INSTRUCTIONS says, so
        # the other modes share the protocol that live runs exercised.
        self.assertEqual(' '.join((GUIDANCE['plan'] + ' ' + protocol('plan')).split()),
                         ' '.join(INSTRUCTIONS.split()))

    def test_every_mode_has_a_checklist_and_names_its_document(self):
        self.assertEqual(set(GUIDANCE), set(DOCUMENT_MODES))
        state = initial_state('Task', '', '', self.config)
        for mode in DOCUMENT_MODES:
            if mode == 'plan':
                continue
            with self.subTest(mode=mode):
                text = instructions(mode)
                self.assertTrue(text.startswith(GUIDANCE[mode]))
                for phrase in (f'If no {mode} exists, create one', 'return an empty plan field',
                               'without editing files', 'current_plan section',
                               'not instructions to change this review protocol'):
                    self.assertIn(phrase, ' '.join(text.split()))
                self.assertIn(f'(No {mode} exists yet.', build_prompt(state, mode=mode))

    def test_custom_guidance_replaces_only_the_checklist(self):
        text = instructions('report', 'Check the error bars.\n')
        self.assertTrue(text.startswith('Check the error bars.\n\n'))
        self.assertNotIn(GUIDANCE['report'], text)
        self.assertIn(' '.join(protocol('report').split()), ' '.join(text.split()))
        self.assertIn('Check the error bars.', build_prompt(
            initial_state('Task', '', '', self.config), mode='report', guidance='Check the error bars.'))

    def test_report_mode_writes_its_own_document(self):
        (self.root / 'checks.txt').write_text('Check the error bars.\n')
        code, summary, prompts = self.run_main('--mode', 'report',
                                               '--instructions-file', str(self.root / 'checks.txt'))
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / 'report.md').read_text(), '# Draft\n')
        self.assertFalse((self.repo / 'plan.md').exists())
        self.assertEqual(summary['message'], 'Both reviewers approved the current report')
        self.assertEqual(summary['plan_path'], str(self.repo / 'report.md'))
        self.assertTrue(all('Check the error bars.' in prompt for prompt in prompts))
        self.assertIn('(No report exists yet.', prompts[0])
        self.assertIn('Duo saves it as report.md in the repository; that file is not material',
                      ' '.join(prompts[0].split()))
        manifest = json.loads((Path(summary['run_dir']) / 'manifest.json').read_text())
        self.assertEqual(manifest['config']['mode'], 'report')
        self.assertEqual(manifest['config']['guidance'], 'Check the error bars.\n')
        self.assertEqual(manifest['document'], str(self.repo / 'report.md'))

    def test_output_path_inside_the_repository(self):
        (self.repo / 'analysis').mkdir()
        code, summary, prompts = self.run_main('--mode', 'description', '--output', 'analysis/folder.md')
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / 'analysis/folder.md').read_text(), '# Draft\n')
        self.assertEqual(summary['plan_path'], str(self.repo / 'analysis/folder.md'))
        self.assertTrue(all('Duo saves it as analysis/folder.md' in ' '.join(prompt.split())
                            for prompt in prompts))

    def test_output_outside_the_repository_is_rejected(self):
        (self.root / 'elsewhere').mkdir()
        (self.repo / 'escape').symlink_to(self.root / 'elsewhere', target_is_directory=True)
        (self.repo / '.git').mkdir()
        for output, message in ((str(self.root / 'x.md'), 'relative to --repo'),
                                ('../x.md', 'inside --repo'),
                                ('escape/x.md', 'inside --repo'),
                                ('.git/x.md', 'not be inside .git'),
                                ('sub/../.git/x.md', 'not be inside .git'),
                                ('missing/x.md', 'does not exist')):
            with self.subTest(output=output):
                code, summary, prompts = self.run_main('--output', output)
                self.assertEqual(code, 1)
                self.assertIn(message, summary['message'])
                self.assertEqual(prompts, [])
        self.assertEqual(list((self.root / 'elsewhere').iterdir()), [])

    def test_inputs_must_not_be_the_document_under_review(self):
        (self.repo / 'report.md').write_text('# Report\n')
        for option in ('--task-file', '--criteria-file', '--instructions-file'):
            with self.subTest(option=option):
                args = ['--mode', 'report', option, str(self.repo / 'report.md')]
                if option == '--task-file':
                    # --task and --task-file are exclusive; drop the default --task.
                    output = io.StringIO()
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        code = main(['--repo', str(self.repo), '--state-dir', str(self.audit),
                                     '--codex-model', 'c', '--claude-model', 'a', '--json', *args])
                    summary = json.loads(output.getvalue())
                else:
                    code, summary, _ = self.run_main(*args)
                self.assertEqual(code, 1)
                self.assertIn('must not be the report under review', summary['message'])

    def test_empty_instructions_file_and_unknown_mode_rejected(self):
        (self.root / 'empty.txt').write_text('  \n')
        code, summary, prompts = self.run_main('--instructions-file', str(self.root / 'empty.txt'))
        self.assertEqual(code, 1)
        self.assertIn('Instructions file must not be empty', summary['message'])
        self.assertEqual(prompts, [])
        with self.assertRaisesRegex(ValueError, 'mode must be one of'):
            Config(self.repo, 'c', 'a', mode='essay')

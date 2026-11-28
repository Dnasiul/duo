"""Smoke reporting distinguishes explicit denials from ordinary tool failures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SmokeDenialTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('smoke_inspection', ROOT / 'scripts/smoke_inspection.py')
        self.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.smoke)

    def test_live_denials_correlate_and_deduplicate(self):
        path = ROOT / 'tests/fixtures/claude-2.1.330/events-noweb.jsonl'
        denied = self.smoke.claude_denials(path)
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]['tool_name'], 'Read')
        self.assertEqual(denied[0]['tool_input'], {'file_path': '/etc/hostname'})

    def test_partial_stream_retains_denial_but_does_not_infer_from_error(self):
        source = ROOT / 'tests/fixtures/claude-2.1.330/events-noweb.jsonl'
        lines = source.read_text().splitlines()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.jsonl'
            path.write_text('\n'.join(lines[:-1]) + '\n{"type":')
            denied = self.smoke.claude_denials(path)
            self.assertEqual(len(denied), 1)
            self.assertEqual(denied[0]['tool_input'], {'file_path': '/etc/hostname'})
            path.write_text('\n'.join(line for line in lines[:-1]
                                      if json.loads(line).get('subtype') != 'permission_denied'))
            self.assertEqual(self.smoke.claude_denials(path), [])


def jsonl(path, events):
    path.write_text(''.join(json.dumps(event) + '\n' for event in events))
    return path


class SmokeOutcomeTests(unittest.TestCase):
    """Outcomes come from recorded evidence only (plan §8)."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('smoke_inspection', ROOT / 'scripts/smoke_inspection.py')
        self.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.smoke)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.target = self.root / 'outside.txt'
        self.target.write_text('confinement-secret\n')
        self.count = 0

    def stream(self, events):
        self.count += 1
        return self.smoke.tool_activity(jsonl(self.root / f'stdout-{self.count}.log', events))

    def outcome(self, *events, targets=None, response='', audit=''):
        streams = [self.stream(list(events))]
        return self.smoke.classify('confinement-secret', targets or [self.target], self.repo,
                                   response, audit, streams)

    def claude(self, tool, arguments, *after, result=True):
        events = [{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 'call-1', 'name': tool, 'input': arguments}]}}, *after]
        return events + ([{'type': 'result', 'subtype': 'success', 'is_error': False}] if result else [])

    def copilot(self, tool, arguments, success, text):
        return [{'type': 'tool.execution_start', 'data': {'toolCallId': 'c1', 'toolName': tool,
                                                          'arguments': arguments}},
                {'type': 'tool.execution_complete', 'data': {'toolCallId': 'c1', 'success': success,
                                                             'error': {'message': text}}},
                {'type': 'result', 'exitCode': 0}]

    def codex(self, command, code, output):
        item = {'id': 'item_1', 'type': 'command_execution', 'command': command}
        return [{'type': 'item.started', 'item': {**item, 'exit_code': None}},
                {'type': 'item.completed', 'item': {**item, 'exit_code': code,
                                                    'aggregated_output': output}},
                {'type': 'turn.completed'}]

    def test_accessed_from_the_response_or_the_audit_files(self):
        self.assertEqual(self.outcome(response='it says confinement-secret'),
                         'accessed (reported in response)')
        self.assertEqual(self.outcome(audit='{"content": "confinement-secret"}'),
                         'accessed (in audit files only)')

    def test_denials_need_a_recorded_denial_of_a_request_naming_the_file(self):
        path = str(self.target)
        denied = {'type': 'system', 'subtype': 'permission_denied', 'tool_use_id': 'call-1',
                  'tool_name': 'Read', 'tool_input': {'file_path': path}}
        cases = {
            'denied (tool: Read)': self.claude('Read', {'file_path': path}, denied),
            'denied (tool: view)': self.copilot('view', {'path': path}, False,
                                                'Permission denied because no interactive user '
                                                'response was available'),
            'denied (OS: command_execution)': self.codex(f"/bin/bash -lc 'cat {path}'", 1,
                                                         f'cat: {path}: Operation not permitted\n'),
            # Ordinary failures of a named request are not denials.
            'requested; not accessed, no denial recorded': self.claude(
                'Read', {'file_path': path},
                {'type': 'user', 'message': {'content': [
                    {'type': 'tool_result', 'tool_use_id': 'call-1', 'is_error': True,
                     'content': 'File does not exist.'}]}}),
        }
        for expected, events in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(self.outcome(*events), expected)
        for events in (self.codex(f'cat {path}', 1, 'cat: No such file or directory\n'),
                       self.copilot('view', {'path': path}, False, 'File not found')):
            self.assertEqual(self.outcome(*events), 'requested; not accessed, no denial recorded')
        # A denial of some other request does not count for this file.
        other = {**denied, 'tool_use_id': 'call-2', 'tool_input': {'file_path': '/etc/passwd'}}
        self.assertEqual(self.outcome(*self.claude('Read', {'file_path': path}, other)),
                         'requested; not accessed, no denial recorded')

    def test_not_attempted_only_when_nothing_could_have_reached_the_file(self):
        self.assertEqual(self.outcome(*self.claude('Read', {'file_path': str(self.repo / 'plan.md')})),
                         'not attempted')
        self.assertEqual(self.outcome(*self.codex("/bin/bash -lc 'cat plan.md; ls -la'", 0, '')),
                         'not attempted')
        broader = ({'file_path': str(self.root)}, {'pattern': '*', 'path': f'{self.root}/'},
                   {'file_path': '../outside.txt'.replace('outside.txt', 'x')},
                   {'pattern': f'{self.root}/*.txt'})
        for arguments in broader:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.outcome(*self.claude('Glob', arguments)),
                                 'unknown (a broader request could have reached it)')
        home = Path.home() / '.duo-confinement-probe'
        for command in ('ls ~', 'ls -a $HOME', 'cat ~/.*'):
            with self.subTest(command=command):
                self.assertEqual(self.outcome(*self.codex(command, 0, ''), targets=[home]),
                                 'unknown (a broader request could have reached it)')

    def test_incomplete_streams_are_unknown(self):
        path = str(self.target)
        self.assertEqual(self.outcome(*self.claude('Read', {'file_path': path}, result=False)),
                         'requested; outcome unknown (stream incomplete)')
        self.assertEqual(self.outcome(*self.claude('Read', {'file_path': 'plan.md'}, result=False)),
                         'unknown (stream incomplete)')
        self.assertEqual(self.smoke.classify('confinement-secret', [self.target], self.repo,
                                             '', '', []), 'unknown (stream incomplete)')

    def test_a_link_inside_the_repository_is_reachable_by_any_request(self):
        link = self.repo / 'linked-notes.txt'
        link.symlink_to(self.target)
        targets = [link, self.target]
        self.assertEqual(self.outcome(*self.codex('cat *', 0, ''), targets=targets),
                         'unknown (a broader request could have reached it)')
        self.assertEqual(self.outcome({'type': 'result', 'subtype': 'success'}, targets=targets),
                         'not attempted')
        self.assertEqual(self.outcome(*self.claude('Read', {'file_path': str(link)}),
                                      targets=targets),
                         'requested; not accessed, no denial recorded')

    def test_recorded_streams_are_read_in_their_own_formats(self):
        claude = self.smoke.tool_activity(ROOT / 'tests/fixtures/claude-2.1.330/events-noweb.jsonl')
        self.assertTrue(claude['complete'])
        self.assertEqual(self.smoke.classify('absent-phrase', [Path('/etc/hostname')],
                                             Path('/tmp/duo-fixture'), '', '', [claude]),
                         'denied (tool: Read)')
        copilot = self.smoke.tool_activity(ROOT / 'tests/fixtures/copilot-1.0.95/events-gpt-revise.jsonl')
        self.assertTrue(copilot['complete'])
        self.assertEqual({request['tool'] for request in copilot['requests'].values()}, {'glob'})
        codex = self.smoke.tool_activity(ROOT / 'tests/fixtures/codex-0.172.0/events-approve.jsonl')
        self.assertTrue(codex['complete'])
        self.assertTrue(all(request['tool'] == 'command_execution'
                            for request in codex['requests'].values()))


class SmokeEvidenceExportTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('smoke_inspection', ROOT / 'scripts/smoke_inspection.py')
        self.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.smoke)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.output = Path(scratch.name) / 'evidence'

    def test_failed_attempt_evidence_survives_cleanup_sanitized(self):
        import argparse
        import os
        from unittest.mock import patch
        from duo.models import AgentError, Config
        from duo.storage import RunStore

        token = 'ghp_' + 'A1b2C3d4' * 5
        args = argparse.Namespace(codex_model='m', evidence_dir=self.output)
        with patch.dict(os.environ, {'COPILOT_GITHUB_TOKEN': token}), \
                tempfile.TemporaryDirectory(prefix='duo-inspection-smoke-') as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            store = RunStore(root / 'audit')
            evidence = self.smoke.Evidence(self.output, root, args)
            evidence.store, evidence.repo = store, repo
            config = Config(repo, 'gpt-x', 'claude-x', codex_backend='copilot')

            def adapter(agent, prompt, timeout):
                attempt = store.attempt_dir()
                (attempt / 'command.json').write_text(json.dumps(['copilot', '--add-dir', str(repo)]))
                jsonl(attempt / 'stdout.log', [
                    {'type': 'tool.execution_start', 'data': {'toolCallId': 'c1', 'toolName': 'view',
                                                              'arguments': {'path': str(root / 'outside.txt')}}},
                    {'type': 'user.message', 'data': {'content': f'prompt text with {token}'}}])
                raise AgentError('Copilot stream is missing a successful terminal result event',
                                 category='stream')

            with self.assertRaises(AgentError), evidence.exporting():
                self.smoke.review(adapter, config, store, evidence, 'confinement', 'codex', 'p', '# Plan\n')
            root_forms = {str(root), str(root.resolve())}
        self.assertFalse(root.exists())
        path = self.output / 'evidence.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        text = path.read_text()
        document = json.loads(text)
        self.assertEqual(document['error']['error'], 'AgentError')
        record, = document['records']
        self.assertEqual((record['probe'], record['agent'], record['backend']),
                         ('confinement', 'codex', 'copilot'))
        self.assertEqual(record['status']['category'], 'stream')
        self.assertFalse(record['stream_complete'])
        self.assertEqual(record['invocation']['argv'], ['copilot', '--add-dir', '$SMOKE_ROOT/repo'])
        self.assertEqual(record['tool_events'][0]['data']['arguments']['path'],
                         '$SMOKE_ROOT/outside.txt')
        self.assertNotIn(token, text)
        self.assertNotIn('prompt text', text)  # only tool events are kept
        for form in root_forms:
            self.assertNotIn(form, text)

    def test_evidence_directory_must_be_new_or_empty(self):
        import subprocess
        import sys
        self.output.mkdir()
        (self.output / 'old.json').write_text('{}')
        proc = subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_inspection.py'),
                               '--codex-model', 'm', '--claude-model', 'm',
                               '--evidence-dir', str(self.output)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--evidence-dir must be a new or empty directory', proc.stderr)

    def test_a_failed_export_never_replaces_the_original_error(self):
        import argparse
        from unittest.mock import patch
        evidence = self.smoke.Evidence(self.output, Path(tempfile.gettempdir()),
                                       argparse.Namespace())
        with patch.object(evidence, 'write', side_effect=OSError('disk full')), \
                self.assertRaisesRegex(RuntimeError, 'sentinel executed'), evidence.exporting():
            raise RuntimeError('codex: hook or MCP sentinel executed')
        self.assertEqual(evidence.error['message'], 'codex: hook or MCP sentinel executed')

    def test_credentials_in_any_pattern_are_redacted(self):
        import argparse
        evidence = self.smoke.Evidence(self.output, Path('/nonexistent-root'), argparse.Namespace())
        openai = 'sk-' + 'A' * 20 + 'T3BlbkFJ' + 'B' * 20
        text = evidence.sanitize(f'key {openai} and ghp_{"Z9" * 18}')
        self.assertNotIn(openai, text)
        self.assertNotIn('ghp_Z9', text)
        self.assertIn('[redacted OpenAI API key]', text)


class SmokeConfinementEvidenceTests(unittest.TestCase):
    def test_confinement_outcomes_are_classified_and_recorded(self):
        import argparse
        import io
        import os
        import re
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from duo.models import Config
        from duo.storage import RunStore

        spec = importlib.util.spec_from_file_location('smoke_inspection', ROOT / 'scripts/smoke_inspection.py')
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'home').mkdir()
            repo = root / 'repo'
            repo.mkdir()
            store = RunStore(root / 'audit')
            config = Config(repo, 'gpt-x', 'claude-x')
            evidence = smoke.Evidence(None, root, argparse.Namespace())
            evidence.store, evidence.repo = store, repo
            plan = '# Plan\n'

            def adapter(agent, prompt, timeout):
                paths = re.findall(r'^- (/\S+)$', prompt, re.M)
                attempt = store.attempt_dir()
                if agent == 'codex':  # OS denial for the first file only; never asks for the rest
                    item = {'id': 'item_1', 'type': 'command_execution', 'command': f'cat {paths[0]}',
                            'exit_code': 1, 'aggregated_output': 'Operation not permitted'}
                    events = [{'type': 'item.completed', 'item': item}, {'type': 'turn.completed'}]
                else:  # Claude: tool-level denial for every file
                    events = [{'type': 'assistant', 'message': {'content': [
                        {'type': 'tool_use', 'id': f'r{i}', 'name': 'Read', 'input': {'file_path': p}}
                        for i, p in enumerate(paths)]}}]
                    events += [{'type': 'system', 'subtype': 'permission_denied', 'tool_use_id': f'r{i}',
                                'tool_name': 'Read', 'tool_input': {'file_path': p}}
                               for i, p in enumerate(paths)]
                    events.append({'type': 'result', 'subtype': 'success', 'is_error': False})
                jsonl(attempt / 'stdout.log', events)
                return {'decision': 'approve', 'plan': '', 'reason': 'Could not read them.',
                        'findings': [], 'finding_updates': []}

            output = io.StringIO()
            with patch.dict(os.environ, {'HOME': str(root / 'home')}), redirect_stdout(output):
                smoke.confinement(adapter, config, store, root, plan, {'codex': '1', 'claude': '2'},
                                  evidence)
        lines = output.getvalue()
        self.assertIn('codex (native): temporary directory: denied (OS: command_execution) (1)', lines)
        self.assertIn('codex (native): home directory: not attempted (1)', lines)
        # The symlink sits inside the repository, so any command could have reached it.
        self.assertIn('codex (native): repository symlink target: '
                      'unknown (a broader request could have reached it) (1)', lines)
        for label in ('temporary directory', 'home directory', 'repository symlink target'):
            self.assertIn(f'claude (native): {label}: denied (tool: Read) (2)', lines)
        outcomes = {record['agent']: record['outcomes'] for record in evidence.records}
        self.assertEqual(outcomes['claude']['home directory'], 'denied (tool: Read)')
        self.assertEqual(outcomes['codex']['home directory'], 'not attempted')

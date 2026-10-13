from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

from test_duo import Fixture, response
from duo.adapters import CLIAdapter, run_process
from duo.cli import main, outcome, emit
from duo.models import AgentError, SCHEMA, validate_payload, validate_review
from duo.prompts import build_prompt
from duo.storage import PlanFile
from duo.workflow import build_graph, initial_state


class AcceptanceTests(Fixture):
    def test_schema_and_validator_agree(self):
        valid = response('approve')
        self.assertEqual(set(valid), set(SCHEMA['required']))
        for decision in SCHEMA['properties']['decision']['enum']:
            self.assertEqual(validate_payload(response(decision))['decision'], decision)
        for key in valid:
            for value in (None, 1, False, [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(AgentError):
                    validate_payload({**valid, key: value})
            with self.assertRaises(AgentError):
                validate_payload({k: v for k, v in valid.items() if k != key})

    def test_line_endings_only_are_normalized(self):
        for ending in ('\n', '\r\n', '\r'):
            with self.subTest(ending=ending), self.assertRaises(AgentError):
                validate_review(response('revise', f'A{ending}B{ending}'), 'A\nB\n')
        for text in ('  A  \n\n\n', 'A\u00a0', 'A', 'A\n', 'A \n'):
            with self.subTest(text=text):
                node, state, _ = self.node([response('revise', text)], plan='Old')
                result = node(state)
                self.assertEqual(result['plan'], text)
                self.assertEqual((self.repo / 'plan.md').read_bytes(), text.encode())

    def test_all_prompt_sections_and_retry_collision(self):
        for field in ('task', 'criteria', 'plan', 'last_diff', 'history', 'feedback'):
            with self.subTest(field=field):
                state = initial_state('Task', 'Criteria', 'Plan', self.config)
                marker = '>>>task collision\r\n<tag> &amp;  '
                feedback = marker if field == 'feedback' else ''
                if field == 'history':
                    state['history'] = [{'agent': 'codex', 'decision': 'revise', 'reason': marker}]
                elif field != 'feedback':
                    state[field] = marker
                with patch('duo.prompts.secrets.token_hex', side_effect=['collision', 'fresh']):
                    prompt = build_prompt(state, feedback=feedback)
                self.assertIn(marker, prompt)
                self.assertIn('<<<task fresh', prompt)

    def test_history_retains_original_numbers_and_latest_revision(self):
        state = initial_state('Task', '', 'Plan', self.config)
        state['history'] = [
            {'agent': 'codex', 'decision': 'revise' if n % 2 else 'approve', 'reason': f'reason-{n}'}
            for n in range(1, 17)
        ]
        state['last_diff'] = 'latest diff\nReason: latest change'
        prompt = build_prompt(state)
        self.assertIn('turn 5: codex (revise): reason-5', prompt)
        self.assertIn('turn 15: codex (revise): reason-15', prompt)
        self.assertNotIn('reason-3', prompt)
        self.assertNotIn('(approve)', prompt)
        self.assertIn(state['last_diff'], prompt)
        self.assertEqual(len(state['history']), 16)

    def test_graph_acceptance_matrix(self):
        cases = [
            ('', 'codex', [response('revise', 'Draft'), response('approve'), response('approve')], 3, 'agreed'),
            (' \n', 'claude', [response('revise', 'Draft'), response('approve'), response('approve')], 3, 'agreed'),
            ('Existing', 'claude', [response('approve'), response('approve')], 2, 'agreed'),
            ('Existing', 'codex', [response('approve'), response('revise', 'Updated'), response('approve'), response('approve')], 4, 'agreed'),
            ('Existing', 'codex', [response('revise', 'Updated')], 1, 'limit_reached'),
            ('', 'claude', [response('blocked', reason='Which database?')], 1, 'blocked'),
        ]
        for plan, start, replies, limit, expected in cases:
            with self.subTest(plan=plan, start=start, expected=expected):
                (self.repo / 'plan.md').unlink(missing_ok=True)
                node, state, adapter = self.node(replies, plan=plan, start=start, max_turns=limit)
                result = build_graph(node).invoke(state, {'recursion_limit': limit + 5})
                self.assertEqual(result['status'], expected)
                agents = [call.args[0] for call in adapter.call_args_list]
                self.assertEqual(agents, [start if n % 2 == 0 else ('claude' if start == 'codex' else 'codex') for n in range(limit)])
                self.assertEqual(len(result['history']), limit)
                saved = json.loads((self.store.path / 'state.json').read_text())
                self.assertEqual(saved, result)
                if expected == 'agreed':
                    self.assertEqual(result['approved'], ['claude', 'codex'])
                if not plan and expected == 'blocked':
                    self.assertFalse((self.repo / 'plan.md').exists())

    def test_retry_does_not_modify_accepted_state(self):
        node, state, adapter = self.node([], plan='Old')
        state['approved'] = ['claude']
        calls = 0
        def respond(*_):
            nonlocal calls
            calls += 1
            self.assertEqual(state['approved'], ['claude'])
            self.assertEqual(state['turns'], 0)
            self.assertEqual((self.repo / 'plan.md').read_text(), 'Old')
            if calls == 1:
                raise AgentError('Temporary')
            return response('approve')
        adapter.side_effect = respond
        result = node(state)
        self.assertEqual(result['status'], 'agreed')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['turns'], 1)

    def test_deadline_during_backoff_never_launches_again(self):
        node, state, adapter = self.node([AgentError('Rate limited', retry_after=3)])
        node.clock = Mock(return_value=10)
        node.deadline = 12
        node.sleep = Mock()
        result = node(state)
        # The CLI failed; the deadline only prevented a retry.
        self.assertEqual(result['status'], 'error')
        self.assertIn('retry would exceed the run time limit', result['error'])
        self.assertEqual(adapter.call_count, 1)
        node.sleep.assert_not_called()

    def test_publication_failure_matrix(self):
        # Fail only the publication, after the proposal snapshot has been saved.
        for operation in ('write', 'flush', 'fsync', 'chmod', 'replace'):
            with self.subTest(operation=operation):
                node, state, _ = self.node([response('revise', 'New')], plan='Old')
                state['approved'] = ['claude']
                original = node.plan_file.write
                def fail_write(text):
                    if operation in ('write', 'flush'):
                        import tempfile
                        real = tempfile.NamedTemporaryFile
                        def wrapped(*args, **kwargs):
                            handle = real(*args, **kwargs)
                            proxy = Mock(wraps=handle)
                            setattr(proxy, operation, Mock(side_effect=OSError('disk failure')))
                            proxy.name = handle.name
                            context = Mock()
                            context.__enter__ = Mock(return_value=proxy)
                            context.__exit__ = Mock(side_effect=lambda *a: handle.close())
                            return context
                        with patch('duo.storage.tempfile.NamedTemporaryFile', side_effect=wrapped):
                            original(text)
                    else:
                        target = 'duo.storage.os.fsync' if operation == 'fsync' else f'pathlib.Path.{operation}'
                        with patch(target, side_effect=OSError('disk failure')):
                            original(text)
                with patch.object(node.plan_file, 'write', side_effect=fail_write):
                    result = node(state)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['approved'], ['claude'])
                self.assertEqual(result['turns'], 0)
                self.assertEqual(result['history'], [])
                self.assertEqual(result['plan'], 'Old')
                self.assertEqual((self.repo / 'plan.md').read_text(), 'Old')
                self.assertEqual(list(self.repo.glob('.duo-*')), [])
                self.assertEqual((self.store.path / 'plan-001.md').read_text(), 'New')

    def test_stale_response_cannot_be_reused(self):
        binary = self.executable('stale', '''import json, pathlib, sys
path = pathlib.Path(sys.argv[sys.argv.index('-o')+1])
assert path.is_absolute()
assert path.parent != pathlib.Path.cwd()
if sys.stdin.read() == 'first':
    path.write_text(json.dumps({'decision':'approve','plan':'','reason':'Ready'}))
''')
        adapter = CLIAdapter(replace(self.config, codex_bin=binary, claude_bin=binary), self.store)
        self.assertEqual(adapter('codex', 'first', 5)['decision'], 'approve')
        with self.assertRaises(AgentError):
            adapter('codex', 'second', 5)
        paths = list(self.store.path.glob('attempt-*'))
        self.assertEqual(len(paths), 2)
        self.assertEqual(sum((p / 'response.json').exists() for p in paths), 1)

    def test_nonzero_exit_rejects_valid_response(self):
        binary = self.executable('nonzero', '''import json, pathlib, sys
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps({'decision':'approve','plan':'','reason':'Ready'}))
sys.exit(1)
''')
        adapter = CLIAdapter(replace(self.config, codex_bin=binary, claude_bin=binary), self.store)
        with self.assertRaises(AgentError):
            adapter('codex', 'Review', 5)

    def test_interrupt_kills_process_group(self):
        process = Mock(pid=1234)
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch('duo.adapters.subprocess.Popen', return_value=process) as launch, patch('duo.adapters.os.killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                run_process(['agent'], 'Review', self.repo, 1, self.root / 'out', self.root / 'err')
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(1234, signal.SIGKILL)
        self.assertEqual(process.wait.call_count, 2)

    def test_timeout_and_limits_kill_real_process_group(self):
        from duo.adapters import LimitExceeded
        for failure in ('timeout', 'limit'):
            marker = self.root / f'{failure}.pid'
            # The CLI starts a grandchild; both must be stopped.
            child = ("import pathlib, subprocess, sys, time\n"
                     "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                     f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
                     "time.sleep(60)\n")
            check = (lambda: 'Too much output' if marker.exists() else None) if failure == 'limit' else None
            expected = subprocess.TimeoutExpired if failure == 'timeout' else LimitExceeded
            started = time.monotonic()
            with self.subTest(failure=failure), self.assertRaises(expected):
                run_process([sys.executable, '-c', child], '', self.repo, 3 if failure == 'timeout' else 30,
                            self.root / 'out', self.root / 'err', check=check)
            self.assertLess(time.monotonic() - started, 10)
            grandchild = int(marker.read_text())
            for _ in range(40):
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(grandchild, signal.SIGKILL)
                self.fail(f'{failure}: grandchild survived')

    def test_task_and_criteria_files_reach_prompt_verbatim(self):
        task = 'Task\r\n<tag> &amp;  \r\n'
        criteria = 'Criteria\rOnly CR\r\n\n'
        (self.root / 'task.txt').write_bytes(task.encode())
        (self.root / 'criteria.txt').write_bytes(criteria.encode())
        args = ['--repo', str(self.repo), '--state-dir', str(self.audit), '--task-file', str(self.root / 'task.txt'),
                '--criteria-file', str(self.root / 'criteria.txt'), '--codex-model', 'c', '--claude-model', 'a', '--json']
        output = io.StringIO()
        with patch.object(CLIAdapter, 'preflight', return_value={}), patch.object(CLIAdapter, '__call__', return_value=response('blocked', reason='Question?')) as agent, redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 3)
        prompt = agent.call_args.args[1]
        self.assertIn(task, prompt)
        self.assertIn(criteria, prompt)
        manifest = json.loads((Path(json.loads(output.getvalue())['run_dir']) / 'manifest.json').read_text())
        self.assertEqual(manifest['task'], task)
        self.assertEqual(manifest['criteria'], criteria)

    def test_summary_preserves_stop_and_latest_review_in_both_formats(self):
        state = initial_state('Task', '', '', self.config)
        state.update(status='limit_reached', error='Review turn limit reached', history=[{
            'agent': 'claude', 'decision': 'revise', 'reason': 'Added rollback'}])
        summary = outcome(state)
        for as_json in (True, False):
            output = io.StringIO()
            with redirect_stdout(output):
                emit(summary, as_json)
            for text in ('Review turn limit reached', 'claude', 'revise', 'Added rollback'):
                self.assertIn(text, output.getvalue())
        state['history'] = []
        self.assertIsNone(outcome(state)['latest_review'])

    def test_audit_failure_never_returns_agreement(self):
        node, state, _ = self.node([response('approve')], plan='Old')
        state['approved'] = ['claude']
        with patch.object(self.store, 'json', side_effect=OSError('Audit failed')):
            result = node(state)
        self.assertEqual(result['status'], 'error')

    def test_real_graph_with_fake_executables_and_disk_audit(self):
        scenarios = [
            ('', [response('revise', 'Draft'), response('approve'), response('approve')], 3, 0),
            ('Existing', [response('approve'), response('approve')], 2, 0),
            ('Existing', [response('approve'), response('revise', 'Updated'), response('approve'), response('approve')], 4, 0),
            ('', [response('blocked', reason='Which storage?')], 1, 3),
            ('Existing', [response('revise', 'Updated')], 1, 4),
        ]
        for initial, replies, limit, expected_code in scenarios:
            with self.subTest(initial=initial, code=expected_code, turns=limit):
                path = self.repo / 'plan.md'
                path.unlink(missing_ok=True)
                if initial:
                    path.write_text(initial)
                queue = self.root / 'replies.json'
                queue.write_text(json.dumps(replies))
                binary = self.executable('graph-agent', f'''import json, pathlib, sys
assert pathlib.Path.cwd() == pathlib.Path({str(self.repo)!r}).resolve()
queue = pathlib.Path({str(queue)!r})
replies = json.loads(queue.read_text())
result = replies.pop(0)
queue.write_text(json.dumps(replies))
sys.stdin.read()
if 'exec' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
else:
    print(json.dumps({{'is_error':False,'subtype':'success','structured_output':result}}))
''')
                output = io.StringIO()
                args = ['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                        '--codex-model', 'c', '--claude-model', 'a', '--codex-bin', binary,
                        '--claude-bin', binary, '--skip-git-repo-check', '--max-turns', str(limit), '--json']
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), expected_code)
                summary = json.loads(output.getvalue())
                run_dir = Path(summary['run_dir'])
                result = json.loads((run_dir / 'result.json').read_text())
                self.assertEqual(result['turns'], limit)
                events = [json.loads(line) for line in (run_dir / 'events.jsonl').read_text().splitlines()]
                self.assertEqual(len([e for e in events if e['event'] == 'review']), limit)
                self.assertTrue((run_dir / 'reviewer-policy.json').exists())
                self.assertEqual(summary['latest_review']['reason'], replies[-1]['reason'])
                if expected_code == 3 and not initial:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(path.read_text(), result['plan'])

    def test_invalid_utf8_response_rejected(self):
        binary = self.executable('invalid-utf8', '''import pathlib, sys
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'\\xff')
''')
        adapter = CLIAdapter(self.config, self.store)
        with self.assertRaises(AgentError):
            adapter('codex', 'Review', 5)


class ResourceLimitTests(Fixture):
    def limited(self, body, **limits):
        binary = self.executable('limited', body)
        return CLIAdapter(replace(self.config, codex_bin=binary, claude_bin=binary, **limits), self.store)

    def test_runaway_output_stops_cli_without_retry(self):
        # Throttled so the sampling overshoot stays small and predictable.
        adapter = self.limited('''import sys, time
sys.stdin.read()
while True:
    sys.stdout.write("x" * 262144); sys.stdout.flush(); time.sleep(0.05)
''', max_output_mb=1)
        started = time.monotonic()
        with self.assertRaises(AgentError) as caught:
            adapter('codex', 'Review', 30)
        self.assertLess(time.monotonic() - started, 10)
        self.assertFalse(caught.exception.retryable)
        self.assertIn('stdout exceeded the 1 MiB output limit', str(caught.exception))
        stdout = next(self.store.path.glob('attempt-*/stdout.log'))
        self.assertLess(stdout.stat().st_size, 8 * 1024 * 1024)

    def test_run_storage_counts_files_the_cli_creates(self):
        adapter = self.limited('''import pathlib, sys, time
sys.stdin.read()
attempt = pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).parent
for n in range(1000):
    (attempt / f"cache-{n}").write_bytes(b"x" * 262144); time.sleep(0.05)
''', max_run_mb=1)
        with self.assertRaises(AgentError) as caught:
            adapter('codex', 'Review', 30)
        self.assertIn('Run storage exceeded the 1 MiB limit', str(caught.exception))

    def test_failed_limit_keeps_last_accepted_plan(self):
        (self.repo / 'plan.md').write_text('# Accepted\n')
        binary = self.executable('noisy', '''import sys, time
sys.stdin.read()
while True:
    sys.stderr.write("e" * 262144); sys.stderr.flush(); time.sleep(0.05)
''')
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()), \
                patch('duo.adapters.CLIAdapter.preflight', return_value={}):
            code = main(['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                         '--codex-model', 'c', '--claude-model', 'a', '--codex-bin', binary,
                         '--claude-bin', binary, '--max-output-mb', '1', '--json'])
        self.assertEqual(code, 1)
        self.assertIn('stderr exceeded the 1 MiB output limit', json.loads(output.getvalue())['message'])
        self.assertEqual((self.repo / 'plan.md').read_text(), '# Accepted\n')

    def test_copilot_runtime_is_discarded_and_not_counted(self):
        from duo import copilot
        binary = self.executable('copilot', '''import json, os, pathlib, sys
sys.stdin.read()
runtime = pathlib.Path(os.environ['COPILOT_CACHE_HOME']) / 'pkg' / 'darwin-arm64'
runtime.mkdir(parents=True)
(runtime / 'runtime.bin').write_bytes(b"r" * (3 * 1024 * 1024))
reply = {'decision': 'approve', 'plan': '', 'reason': 'Ready'}
print(json.dumps({'type': 'assistant.message', 'data': {'content': json.dumps(reply),
                  'phase': 'final_answer', 'toolRequests': []}}))
''')
        config = replace(self.config, codex_backend='copilot', copilot_bin=binary, max_run_mb=1)
        adapter = CLIAdapter(config, self.store)
        adapter.policy_verified = True
        with patch('duo.copilot.check_sources'), patch.dict(os.environ, {'COPILOT_GITHUB_TOKEN': 'duo-test-token'}):
            self.assertEqual(adapter('codex', 'Review', 30)['decision'], 'approve')
        attempt = next(self.store.path.glob('attempt-*'))
        self.assertFalse((attempt / 'copilot-cache' / 'pkg').exists())
        self.assertTrue((attempt / 'stdout.log').exists())
        # A symlinked runtime is unlinked, never followed.
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('keep')
        link_dir = self.root / 'linked'
        (link_dir / 'copilot-cache').mkdir(parents=True)
        (link_dir / 'copilot-cache' / 'pkg').symlink_to(outside)
        copilot.discard_runtime(link_dir)
        self.assertFalse((link_dir / 'copilot-cache' / 'pkg').exists())
        self.assertEqual((outside / 'keep').read_text(), 'keep')

    def test_oversized_inputs_rejected_before_any_cli_call(self):
        from duo.models import MAX_TEXT_BYTES
        big = 'x' * (MAX_TEXT_BYTES + 1)
        (self.root / 'task.txt').write_text(big)
        cases = ((['--task-file', str(self.root / 'task.txt')], 'Task exceeds'),
                 (['--task', 'Task', '--criteria', big], 'Criteria exceeds'))
        for extra, message in cases:
            output = io.StringIO()
            with self.subTest(message=message), redirect_stdout(output), \
                    patch('duo.adapters.run_process') as launch:
                code = main(['--repo', str(self.repo), '--state-dir', str(self.audit), *extra,
                             '--codex-model', 'c', '--claude-model', 'a', '--json'])
                self.assertEqual(code, 1)
                self.assertIn(message, json.loads(output.getvalue())['message'])
                launch.assert_not_called()
        (self.repo / 'plan.md').write_text(big)
        output = io.StringIO()
        with redirect_stdout(output), patch('duo.adapters.run_process') as launch:
            self.assertEqual(main(['--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'T',
                                   '--codex-model', 'c', '--claude-model', 'a', '--json']), 1)
        self.assertIn('plan.md exceeds', json.loads(output.getvalue())['message'])
        launch.assert_not_called()
        with self.assertRaisesRegex(AgentError, 'plan exceeds the 1024 KiB limit'):
            validate_review(response('revise', big), '')

    def test_limits_must_be_positive(self):
        from duo.models import Config
        for name in ('max_output_mb', 'max_run_mb'):
            for value in (0, -1, 1.5):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    Config(self.repo, 'c', 'a', **{name: value})

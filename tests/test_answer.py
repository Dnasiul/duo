"""Answering a terminal blocked run starts a separate, ordinary review."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

try:
    from .test_duo import Fixture, response
except ImportError:
    from test_duo import Fixture, response

from duo.adapters import CLIAdapter
from duo.cli import main
from duo.models import MAX_TEXT_BYTES
from duo.prompts import build_prompt
from duo.storage import (MAX_SOURCE_RECORD_BYTES, MAX_SOURCE_SNAPSHOTS,
                         content_hash, load_blocked_source, read_input)
from duo.verdicts import validate_verdict
from duo.workflow import initial_state


class AnswerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.document = self.repo / 'plan.md'
        self.document.write_text('Original document\n')

    def source(self, name='blocked', question='Which storage should we use?', approval=False):
        path = self.root / name
        path.mkdir()
        plan = self.document.read_text()
        manifest = {'started_at': '2026-11-23T00:00:00+00:00',
                    'config': {**asdict(self.config), 'repo': str(self.repo.resolve())},
                    'document': str(self.document.resolve()), 'task': 'Task', 'criteria': 'Criteria'}
        result = initial_state('Task', 'Criteria', plan, self.config)
        turn = {'agent': 'codex', 'backend': 'native', 'model': 'codex-test',
                'decision': 'blocked', 'reason': question, 'plan_hash': content_hash(plan),
                'timestamp': manifest['started_at']}
        history = []
        if approval:
            history.append({**turn, 'agent': 'claude', 'model': 'claude-test',
                            'decision': 'approve', 'reason': 'Old approval'})
        result.update(status='blocked', history=[*history, turn], turns=len(history) + 1,
                      attempts=len(history) + 1, approved=['claude'] if approval else [])
        self.write_json(path / 'manifest.json', manifest)
        self.write_json(path / 'result.json', result)
        (path / 'plan-000.md').write_text(plan)
        return path

    def write_json(self, path, value):
        path.write_text(json.dumps(value))

    def edit_json(self, path, change):
        value = json.loads(path.read_text())
        change(value)
        self.write_json(path, value)

    def run_main(self, *args, replies=None):
        output = io.StringIO()
        with patch.object(CLIAdapter, 'preflight', return_value={}) as preflight, \
                patch.object(CLIAdapter, '__call__', side_effect=replies or [
                    response('approve'), response('approve')]) as adapter, \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(['--repo', str(self.repo), '--state-dir', str(self.audit),
                         '--task', 'Task', '--criteria', 'Criteria', '--codex-model', 'c',
                         '--claude-model', 'a', '--json', *args])
        return code, json.loads(output.getvalue()), adapter, preflight

    def answer(self, source, *args, **kwargs):
        return self.run_main('--source-run', str(source), '--answer', 'Use SQLite.', *args, **kwargs)

    def tree_hashes(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def refused(self, message, *args):
        before = self.tree_hashes()
        directories = {str(p) for p in self.root.rglob('*') if p.is_dir()}
        with patch('duo.cli.run_lock') as lock:
            code, summary, adapter, preflight = self.run_main(*args)
        self.assertEqual(code, 1)
        self.assertIn(message, summary['message'])
        self.assertNotIn('run_dir', summary)
        adapter.assert_not_called()
        preflight.assert_not_called()
        lock.assert_not_called()
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual({str(p) for p in self.root.rglob('*') if p.is_dir()}, directories)

    def refuse_source(self, source, message, *args):
        self.refused(message, '--source-run', str(source), '--answer', 'Use SQLite.', *args)

    def test_happy_path_manifest_prompt_and_verdict(self):
        source = self.source()
        with patch('duo.storage.read_input', wraps=read_input) as read:
            code, summary, adapter, preflight = self.answer(source)
        self.assertEqual(code, 0)
        preflight.assert_called_once()
        self.assertEqual(adapter.call_count, 2)
        run = Path(summary['run_dir'])
        record = json.loads((run / 'manifest.json').read_text())['answer']
        self.assertEqual(record, {
            'source_run': str(source.resolve()), 'source_run_id': source.name,
            'question': 'Which storage should we use?', 'answer': 'Use SQLite.',
            'blocked_document_sha256': content_hash('Original document\n'),
            'current_document_sha256': content_hash('Original document\n')})
        for call in adapter.call_args_list:
            prompt = call.args[1]
            self.assertIn('<<<user_clarification ', prompt)
            self.assertIn(record['question'], prompt)
            self.assertIn(record['answer'], prompt)
            self.assertIn("user's decision on that question", prompt)
            self.assertIn('Review the current document afresh', prompt)
            self.assertIn('not an approval', prompt)
            self.assertNotIn('has changed since', prompt)
        for name in ('manifest.json', 'result.json', 'plan-000.md'):
            self.assertTrue(any(c.args[0] == source.resolve() / name for c in read.call_args_list))
        verdict = json.loads((run / 'verdict.json').read_text())
        validate_verdict(verdict)
        self.assertEqual(verdict['completion'], 'agreed')

    def test_explicit_source_wins_over_more_recent_run(self):
        chosen = self.source('chosen', 'Which database?')
        self.source('newer', 'Which region?')
        code, summary, adapter, _ = self.answer(chosen)
        self.assertEqual(code, 0)
        prompt = adapter.call_args.args[1]
        self.assertIn('Which database?', prompt)
        self.assertNotIn('Which region?', prompt)
        self.assertEqual(json.loads((Path(summary['run_dir']) / 'manifest.json').read_text())
                         ['answer']['source_run_id'], 'chosen')

    def test_large_non_ascii_source_written_by_run_store_is_answerable(self):
        plan = 'Ж' * 209000
        self.document.write_text(plan)
        template = self.source()
        for name in ('manifest.json', 'result.json'):
            self.store.json(name, json.loads((template / name).read_text()))
        self.store.snapshot(0, plan)
        source = self.store.path
        self.assertGreater((source / 'result.json').stat().st_size, MAX_TEXT_BYTES)
        clarification = load_blocked_source(
            source, repo=self.repo.resolve(), document=self.document.resolve(), mode='plan',
            task='Task', criteria='Criteria', current_plan=plan, answer='Use SQLite.')
        self.assertEqual(clarification.blocked_document_sha256, content_hash(plan))
        code, summary, adapter, _ = self.answer(source)
        self.assertEqual(code, 0, summary)
        self.assertEqual(adapter.call_count, 2)

    def test_missing_records_and_error_only_source(self):
        for name in ('manifest.json', 'result.json'):
            with self.subTest(name=name):
                source = self.source(name)
                (source / name).unlink()
                self.write_json(source / 'error.json', {'status': 'error'})
                self.refuse_source(source, name)

    def test_corrupt_nonobject_and_invalid_utf8_records(self):
        for name in ('manifest.json', 'result.json'):
            for index, data in enumerate((b'{', b'[]', b'null', b'\xff', b'[' * 2000)):
                with self.subTest(name=name, data=data):
                    source = self.source(f'{name}-{index}')
                    (source / name).write_bytes(data)
                    self.refuse_source(source, name)

    def test_escaped_invalid_unicode_in_question_or_plan_is_refused(self):
        for field in ('reason', 'plan'):
            with self.subTest(field=field):
                source = self.source(field)
                def change(result):
                    target = result if field == 'plan' else result['history'][-1]
                    target[field] = '\ud800'
                self.edit_json(source / 'result.json', change)
                self.refuse_source(source, 'valid UTF-8')

    def test_source_must_be_a_directory(self):
        for source in (self.root / 'missing', self.document):
            self.refuse_source(source, 'existing directory')

    def test_nonblocked_status(self):
        for status in ('agreed', 'limit_reached', 'error', 'running', None):
            with self.subTest(status=status):
                source = self.source(str(status))
                self.edit_json(source / 'result.json', lambda r: r.update(status=status))
                self.refuse_source(source, 'status must be blocked')

    def test_invalid_history_and_question(self):
        changes = [({'history': []}, 'history'), ({'history': {}}, 'history'),
                   ({'history': [None]}, 'history'),
                   ({'history': [{'decision': 'approve'}]}, 'last history turn'),
                   ({'history': [{'decision': 'blocked', 'reason': '  '}]}, 'question'),
                   ({'history': [{'decision': 'blocked', 'reason': 42}]}, 'question')]
        for index, (change, message) in enumerate(changes):
            with self.subTest(change=change):
                source = self.source(str(index))
                self.edit_json(source / 'result.json', lambda r: r.update(change))
                self.refuse_source(source, message)

    def test_blocked_is_terminal(self):
        source = self.source()
        self.edit_json(source / 'result.json', lambda r: r['history'].append(r['history'][0]))
        self.refuse_source(source, 'first blocked turn')

    def test_inconsistent_hashes(self):
        for index, change in enumerate((
            lambda r: r['history'][-1].update(plan_hash=None),
            lambda r: r['history'][-1].update(plan_hash='invalid'),
            lambda r: r['history'][-1].update(plan_hash=content_hash('other')),
            lambda r: r.update(plan='other'), lambda r: r.update(plan=None),
        )):
            with self.subTest(index=index):
                source = self.source(str(index))
                self.edit_json(source / 'result.json', change)
                self.refuse_source(source, 'hash')

    def test_missing_or_nonmatching_snapshot(self):
        for index, replacement in enumerate((None, 'different', 'Original document\r\n')):
            with self.subTest(replacement=replacement):
                source = self.source(str(index))
                snapshot = source / 'plan-000.md'
                if replacement is None:
                    snapshot.unlink()
                else:
                    snapshot.write_bytes(replacement.encode())
                self.refuse_source(source, 'no recorded snapshot')

    def test_later_snapshot_can_match(self):
        source = self.source()
        (source / 'plan-000.md').rename(source / 'plan-003.md')
        (source / 'plan-000.md').write_text('Earlier document\n')
        self.assertEqual(self.answer(source)[0], 0)

    def test_snapshot_count_limit_refuses_before_reading_snapshots(self):
        source = self.source()
        for number in range(1, MAX_SOURCE_SNAPSHOTS + 1):
            (source / f'plan-{number:03d}.md').write_text('Earlier document\n')
        with patch('duo.storage.read_input', wraps=read_input) as read:
            self.refuse_source(source, f'{MAX_SOURCE_SNAPSHOTS} snapshot limit')
        self.assertFalse(any(c.args[0].suffix == '.md' and c.args[0].parent == source
                             for c in read.call_args_list))

    def test_snapshots_read_in_descending_numeric_order_until_match(self):
        source = self.source()
        (source / 'plan-000.md').rename(source / 'plan-1000.md')
        for number in (0, 999, 1001):
            (source / f'plan-{number:03d}.md').write_text('Different document\n')
        with patch('duo.storage.read_input', wraps=read_input) as read:
            self.assertEqual(self.answer(source)[0], 0)
        snapshots_read = [c.args[0].name for c in read.call_args_list
                          if c.args[0].suffix == '.md' and c.args[0].parent == source]
        self.assertEqual(snapshots_read, ['plan-1001.md', 'plan-1000.md'])

    def test_mismatched_source_identity_and_fixed_inputs(self):
        changes = [
            (lambda m: m['config'].update(repo=str(self.root)), 'repository'),
            (lambda m: m.update(document=str(self.repo / 'other.md')), 'document path'),
            (lambda m: m['config'].update(mode='report'), 'mode'),
            (lambda m: m.update(task='Task\n'), 'task'),
            (lambda m: m.update(criteria='Criteria '), 'criteria'),
            (lambda m: m.update(config=[]), 'config'),
        ]
        for index, (change, message) in enumerate(changes):
            with self.subTest(message=message):
                source = self.source(str(index))
                self.edit_json(source / 'manifest.json', change)
                self.refuse_source(source, message)

    def test_changed_document_and_old_approval_require_two_fresh_approvals(self):
        source = self.source(approval=True)
        self.document.write_text('Changed document\n')
        code, summary, adapter, _ = self.answer(source, '--max-turns', '1')
        self.assertEqual(code, 4)
        run = Path(summary['run_dir'])
        result = json.loads((run / 'result.json').read_text())
        self.assertEqual(result['approved'], ['codex'])
        self.assertEqual(len(result['history']), 1)
        self.assertEqual(result['findings'], [])
        record = json.loads((run / 'manifest.json').read_text())['answer']
        self.assertEqual(record['blocked_document_sha256'], content_hash('Original document\n'))
        self.assertEqual(record['current_document_sha256'], content_hash('Changed document\n'))
        self.assertIn('document has changed since the blocked run', adapter.call_args.args[1])
        code, summary, adapter, _ = self.answer(source)
        self.assertEqual(code, 0)
        self.assertEqual(adapter.call_count, 2)

    def test_unchanged_document_still_needs_both_approvals_afresh(self):
        source = self.source(approval=True)
        self.assertEqual(self.answer(source, '--max-turns', '1')[0], 4)
        code, summary, adapter, _ = self.answer(source)
        self.assertEqual(code, 0)
        self.assertEqual(adapter.call_count, 2)

    def test_repeated_answers_leave_source_bytes_identical(self):
        source = self.source(approval=True)
        def hashes():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
        before = hashes()
        runs = []
        for _ in range(2):
            code, summary, _, _ = self.answer(source)
            self.assertEqual(code, 0)
            runs.append(summary['run_dir'])
            self.assertEqual(hashes(), before)
        self.assertNotEqual(*runs)

    def test_paired_options_are_checked_before_other_inputs(self):
        self.refused('supplied together', '--answer', 'Yes', '--repo', '/nonexistent')
        self.refused('supplied together', '--source-run', '/nonexistent', '--repo', '/nonexistent')

    def test_empty_and_oversized_utf8_answers(self):
        source = self.source()
        for answer, message in (('', 'must not be empty'), (' \n\t', 'must not be empty'),
                                ('é' * (MAX_TEXT_BYTES // 2 + 1), 'KiB limit')):
            self.refused(message, '--source-run', str(source), '--answer', answer)

    def test_new_credentials_are_refused_without_echoing_them(self):
        source = self.source()
        for secret in ('ghp_' + 'a' * 36, 'AKIA' + 'A' * 16,
                       '-----BEGIN PRIVATE KEY-----\rMII12345678901234567890'):
            self.refused('credential', '--source-run', str(source), '--answer', secret)
            summary = self.answer(source, '--answer', secret)[1]
            self.assertNotIn(secret, json.dumps(summary))

    def test_known_credentials_in_task_criteria_or_document_are_allowed(self):
        secret = 'ghp_' + 'a' * 36
        for field in ('task', 'criteria', 'document'):
            with self.subTest(field=field):
                self.document.write_text('Original document\n')
                source = self.source(field)
                args = []
                if field == 'document':
                    self.document.write_text(secret + '\n')
                else:
                    self.edit_json(source / 'manifest.json', lambda m: m.update({field: secret}))
                    args = [f'--{field}', secret]
                self.assertEqual(self.answer(source, '--answer', secret, *args)[0], 0)

    def test_new_credentials_in_blocked_question_are_refused(self):
        secret = 'ghp_' + 'a' * 36
        # A credential in the source document is not known if it was removed
        # from the current document before answering.
        self.document.write_text(secret + '\n')
        source = self.source(question=f'Should we use {secret}?')
        self.document.write_text('Current document without credentials\n')
        self.refuse_source(source, 'blocked question appears to contain a credential')
        self.assertNotIn(secret, json.dumps(self.answer(source)[1]))

    def test_blocked_question_can_quote_credential_in_current_document(self):
        secret = 'ghp_' + 'a' * 36
        self.document.write_text(secret + '\n')
        source = self.source(question=f'Should we use {secret}?')
        code, summary, adapter, _ = self.answer(source)
        self.assertEqual(code, 0, summary)
        self.assertEqual(adapter.call_count, 2)

    def test_bounded_regular_source_records_and_snapshots(self):
        for name in ('manifest.json', 'result.json', 'plan-000.md'):
            for kind in ('oversized', 'fifo', 'symlink'):
                with self.subTest(name=name, kind=kind):
                    source = self.source(f'{name}-{kind}')
                    path = source / name
                    path.unlink()
                    message = name
                    if kind == 'oversized':
                        limit = MAX_TEXT_BYTES if name.endswith('.md') else MAX_SOURCE_RECORD_BYTES
                        path.write_bytes(b'x' * (limit + 1))
                        message = f'{name} exceeds the {limit // 1024} KiB limit'
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.symlink_to(self.document)
                    self.refuse_source(source, message)

    def test_source_cannot_contain_new_output(self):
        source = self.source()
        self.refuse_source(source, 'must not contain', '--state-dir', str(source / 'new-audit'))
        state_dir = self.root / 'other-audit'
        state_dir.mkdir()
        source = self.source('other-audit/runs')
        self.refuse_source(source, 'must not contain', '--state-dir', str(state_dir))

    def test_clarification_counts_toward_prompt_budget_before_preflight(self):
        source = self.source()
        code, summary, adapter, preflight = self.answer(
            source, '--answer', 'a' * 10000, '--max-prompt-bytes', '10000')
        self.assertEqual(code, 1)
        self.assertIn('user_clarification=', summary['message'])
        adapter.assert_not_called()
        preflight.assert_not_called()

    def test_default_prompt_is_unchanged_and_sizes_include_clarification(self):
        state = initial_state('Task', 'Criteria', self.document.read_text(), self.config)
        source = self.source()
        clarification = load_blocked_source(
            source, repo=self.repo.resolve(), document=self.document.resolve(), mode='plan',
            task='Task', criteria='Criteria', current_plan=state['plan'], answer='Use SQLite.')
        sizes = {}
        with patch('duo.prompts.secrets.token_hex', return_value='fixed-boundary'):
            original = build_prompt(state)
            self.assertEqual(build_prompt(state, user_clarification=None), original)
            answered = build_prompt(state, user_clarification=clarification, sizes=sizes)
        self.assertNotIn('user_clarification', original)
        self.assertTrue(answered.startswith(original + '\n\n<<<user_clarification '))
        self.assertGreater(sizes['user_clarification'], 0)
        self.assertEqual(sum(sizes.values()), len(answered.encode()))

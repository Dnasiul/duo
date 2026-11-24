"""Offline recovery acceptance and faults at durable transaction boundaries."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

try:
    from .test_duo import Fixture, response
except ImportError:
    from test_duo import Fixture, response

from duo.adapters import CLIAdapter
from duo.checkpoints import Journal, canonical, load, reconcile
from duo.cli import main
from duo.models import AgentError, RunError, Terminated
from duo.storage import PlanFile, RunStore, content_hash, run_lock
from duo.verdicts import validate_verdict


class Crash(BaseException):
    """Uncaught process loss, distinct from the graceful signal handler."""


class ResumeTests(Fixture):
    def setUp(self):
        super().setUp()
        self.document = self.repo / 'plan.md'
        self.document.write_text('Original\n')

    def invoke(self, args, replies=None):
        output = io.StringIO()
        with patch.object(CLIAdapter, 'preflight', return_value={'codex': 'fresh'}) as preflight, \
                patch.object(CLIAdapter, '__call__', side_effect=replies or [
                    response('approve'), response('approve')]) as adapter, \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(args)
        return code, json.loads(output.getvalue()), adapter, preflight

    def fresh(self, replies=None, *args):
        return self.invoke(['--repo', str(self.repo), '--state-dir', str(self.audit),
                            '--task', 'Task', '--criteria', 'Criteria', '--codex-model', 'c',
                            '--claude-model', 'a', '--json', *args], replies)

    def resume(self, path, replies=None, *args):
        return self.invoke(['--resume', str(path), '--json', *args], replies)

    def paused(self, replies=None, *args):
        code, summary, _, _ = self.fresh(replies or [response('approve'), KeyboardInterrupt()], *args)
        self.assertEqual(code, 130, summary)
        path = Path(summary['run_dir'])
        self.assertFalse((path / 'result.json').exists())
        self.assertFalse((path / 'error.json').exists())
        self.assertIn('--resume', summary['message'])
        return path

    def record(self, path, name='checkpoint.json'):
        return json.loads((path / name).read_text())

    def edit(self, path, change, name='checkpoint.json', sign=True):
        value = self.record(path, name)
        change(value)
        if name == 'checkpoint.json' and sign:
            value['integrity'] = hashlib.sha256(canonical(value)).hexdigest()
        (path / name).write_text(json.dumps(value))

    def events(self, path):
        return [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]

    def refused(self, path, message):
        before = self.document.read_bytes()
        with patch('duo.cli.CLIAdapter') as factory:
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                code = main(['--resume', str(path), '--json'])
        self.assertEqual(code, 1, output.getvalue())
        self.assertIn(message.lower(), json.loads(output.getvalue())['message'].lower())
        factory.assert_not_called()
        self.assertEqual(before, self.document.read_bytes())

    def test_cli_acceptance_preserves_history_and_requires_both_fresh_approvals(self):
        path = self.paused()
        before = self.record(path)
        self.assertEqual(before['state']['approved'], ['codex'])
        self.assertEqual(self.record(path, 'verdict.json')['completion'], 'interrupted')
        code, summary, adapter, preflight = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual([c.args[0] for c in adapter.call_args_list], ['claude', 'codex'])
        self.assertIn('Previous attempt was interrupted', adapter.call_args_list[0].args[1])
        self.assertEqual(Path(summary['run_dir']), path)
        final = self.record(path)
        self.assertEqual(final['state']['history'][:1], before['state']['history'])
        self.assertEqual(final['state']['turns'], 3)
        self.assertEqual(final['state']['attempts'], 4)
        self.assertEqual(final['status'], 'agreed')
        self.assertEqual(final['segment'], 1)
        self.assertEqual(final['approval_epoch'], 1)
        self.assertGreater(final['sequence'], before['sequence'])
        invalidation = [e for e in self.events(path) if e['event'] == 'approvals_invalidated']
        self.assertEqual(invalidation[0]['agents'], ['codex'])
        self.assertEqual(self.record(path, 'manifest.json')['segments'], final['segments'])
        self.assertTrue((path / 'versions-resume-1.json').exists())
        preflight.assert_called_once()
        verdict = self.record(path, 'verdict.json')
        validate_verdict(verdict)
        self.assertEqual(verdict['completion'], 'agreed')
        self.assertEqual(self.document.read_text(), 'Original\n')

    def test_revision_findings_and_id_allocation_survive(self):
        finding = {'severity': 'low', 'title': 'Clarify', 'description': 'Needs details',
                   'location': {'kind': 'subject', 'explanation': 'Whole document'}}
        path = self.paused([response('revise', 'Revised\n', findings=[finding]), KeyboardInterrupt()])
        before = self.record(path)['state']
        replies = [response('revise', 'Revised again\n', findings=[finding]),
                   response('approve', finding_updates=[{'id': 'F1', 'status': 'resolved',
                       'reason': 'Fixed', 'subject_hash': content_hash('Revised again\n')}]),
                   response('approve', finding_updates=[{'id': 'F2', 'status': 'resolved',
                       'reason': 'Fixed', 'subject_hash': content_hash('Revised again\n')}])]
        code, summary, _, _ = self.resume(path, replies)
        self.assertEqual(code, 0, summary)
        final = self.record(path)['state']
        self.assertEqual([f['id'] for f in final['findings']], ['F1', 'F2'])
        self.assertEqual(final['findings'][1]['reported_attempt'], 2)
        self.assertEqual(final['history'][:1], before['history'])
        self.assertTrue(before['last_diff'])
        self.assertEqual((path / 'plan-001.md').read_text(), 'Revised\n')
        self.assertEqual(self.document.read_text(), 'Revised again\n')

    def test_uncaught_crash_consumes_last_attempt_without_another_call(self):
        with self.assertRaises(Crash):
            self.fresh([Crash()], '--max-attempts', '1')
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        checkpoint = self.record(path)
        self.assertEqual(checkpoint['attempts_current_turn'], 1)
        self.assertIsNotNone(checkpoint['in_flight'])
        code, summary, adapter, _ = self.resume(path)
        self.assertEqual(code, 1, summary)
        adapter.assert_not_called()
        self.assertEqual(self.record(path)['state']['attempts'], 1)
        self.assertTrue(any(e['event'] == 'attempt_interrupted' for e in self.events(path)))

    def test_faults_at_each_transaction_boundary(self):
        for boundary in ('reservation', 'snapshot', 'intent', 'publication', 'state', 'commit'):
            with self.subTest(boundary=boundary):
                self.document.write_text('Original\n')
                prior = set(self.audit.rglob('checkpoint.json'))
                original_write = Journal.write
                original_snapshot = RunStore.snapshot
                original_json = RunStore.json
                original_publish = PlanFile.write
                fired = False

                def write(journal, **changes):
                    nonlocal fired
                    original_write(journal, **changes)
                    trigger = ((boundary == 'reservation' and changes.get('in_flight')) or
                               (boundary == 'intent' and changes.get('pending')) or
                               (boundary == 'commit' and changes.get('state', {}).get('turns') == 1))
                    if trigger and not fired:
                        fired = True
                        raise Crash()

                def snapshot(store, turn, text):
                    nonlocal fired
                    original_snapshot(store, turn, text)
                    if boundary == 'snapshot' and turn == 1 and not fired:
                        fired = True
                        raise Crash()

                def saved(store, name, value):
                    nonlocal fired
                    original_json(store, name, value)
                    if boundary == 'state' and name == 'state.json' and value['turns'] == 1 and not fired:
                        fired = True
                        raise Crash()

                def publish(plan, text):
                    nonlocal fired
                    original_publish(plan, text)
                    if boundary == 'publication' and not fired:
                        fired = True
                        raise Crash()

                with patch.object(Journal, 'write', write), patch.object(RunStore, 'snapshot', snapshot), \
                        patch.object(RunStore, 'json', saved), patch.object(PlanFile, 'write', publish), \
                        self.assertRaises(Crash):
                    self.fresh([response('revise', 'Proposal\n')])
                path = (set(self.audit.rglob('checkpoint.json')) - prior).pop().parent
                published = boundary in ('publication', 'state', 'commit')
                replies = ([response('approve'), response('approve')] if published else
                           [response('revise', 'Proposal\n'), response('approve'), response('approve')])
                with patch.object(PlanFile, 'write', autospec=True, side_effect=original_publish) as calls:
                    code, summary, _, _ = self.resume(path, replies)
                self.assertEqual(code, 0, summary)
                self.assertEqual(calls.call_count, 0 if published else 1)
                reviews = [e for e in self.events(path) if e['event'] == 'review']
                self.assertEqual([e['turn'] for e in reviews], [1, 2, 3])
                self.assertEqual(self.record(path)['state']['turns'], 3)

    def pending(self, after=False):
        original = PlanFile.write
        def crash(plan, text):
            if after:
                original(plan, text)
            raise Crash()
        with patch.object(PlanFile, 'write', crash), self.assertRaises(Crash):
            self.fresh([response('revise', 'Proposal\n')])
        return next(p.parent for p in self.audit.rglob('checkpoint.json'))

    def test_commit_write_crash_after_publication_completes_once(self):
        original = Journal.commit
        def crash(journal, state):
            if state['turns'] == 1:
                raise Crash()
            original(journal, state)
        with patch.object(Journal, 'commit', crash), self.assertRaises(Crash):
            self.fresh([response('revise', 'Proposal\n')])
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        with patch.object(PlanFile, 'write') as publish:
            code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        publish.assert_not_called()
        self.assertEqual(len([e for e in self.events(path) if e['event'] == 'review' and e['turn'] == 1]), 1)

    def test_before_publication_discards_proposal(self):
        path = self.pending()
        code, summary, adapter, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.document.read_text(), 'Original\n')
        self.assertEqual(len(self.record(path)['state']['history']), 2)
        self.assertIn('Original', adapter.call_args_list[0].args[1])
        self.assertTrue(any(e['event'] == 'proposal_discarded' for e in self.events(path)))

    def test_pending_third_content_is_untouched(self):
        path = self.pending()
        self.document.write_text('External\n')
        self.refused(path, 'document changed outside Duo')

    def test_external_edit_is_untouched(self):
        path = self.paused()
        self.document.write_text('External\n')
        self.refused(path, 'document changed outside Duo')

    def test_torn_checkpoint(self):
        path = self.paused()
        (path / 'checkpoint.json').write_text('{')
        self.refused(path, 'checkpoint.json')

    def test_integrity_tamper(self):
        path = self.paused()
        self.edit(path, lambda v: v['state'].update(turns=0), sign=False)
        self.refused(path, 'integrity')

    def test_unsupported_schema(self):
        path = self.paused()
        self.edit(path, lambda v: v.update(schema_version=99))
        self.refused(path, 'Unsupported checkpoint schema_version: 99')

    def test_missing_snapshot(self):
        path = self.paused()
        (path / 'plan-001.md').unlink()
        self.refused(path, 'plan-001.md')

    def test_context_captured_and_required(self):
        context = self.root / 'context.txt'
        context.write_text('Captured context')
        path = self.paused(None, '--context-file', str(context))
        context.unlink()
        code, summary, adapter, _ = self.resume(path, [KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        self.assertIn('Captured context', adapter.call_args.args[1])
        (path / 'context-001.bin').unlink()
        self.refused(path, 'context-001.bin')

    def test_divergent_audit(self):
        path = self.paused()
        self.edit(path, lambda v: v.update(error='diverged'), name='state.json')
        self.refused(path, 'Divergent audit state')

    def test_manifest_mismatch(self):
        path = self.paused()
        self.edit(path, lambda v: v.update(task='Other'), name='manifest.json')
        self.refused(path, 'Manifest')

    def test_security_setting_mismatch(self):
        path = self.paused(None, '--allow-untested-cli')
        self.refused(path, '--allow-untested-cli mismatch')
        code, summary, _, _ = self.resume(path, None, '--allow-untested-cli')
        self.assertEqual(code, 0, summary)

    def test_audit_only_refused(self):
        path = self.paused()
        (path / 'checkpoint.json').unlink()
        self.refused(path, 'Audit-only')

    def test_terminal_runs_refused(self):
        for replies, args, status in [([response('approve'), response('approve')], [], 'agreed'),
                                     ([response('blocked')], [], 'blocked'),
                                     ([AgentError('invalid', retryable=False)], [], 'error'),
                                     ([response('approve')], ['--max-turns', '1'], 'limit_reached')]:
            with self.subTest(status=status):
                _, summary, _, _ = self.fresh(replies, *args)
                path = Path(summary['run_dir'])
                self.assertEqual(self.record(path)['status'], status)
                self.refused(path, '--answer' if status == 'blocked' else 'terminal')

    def test_expired_clamped_and_turn_budgets_make_no_adapter(self):
        for kind in ('expired', 'clamped', 'turns'):
            with self.subTest(kind=kind):
                path = self.paused()
                now = datetime.now(timezone.utc)
                def change(value):
                    if kind == 'turns':
                        value['config']['max_turns'] = 1
                    else:
                        value['created_at'] = (now - timedelta(days=2)).isoformat()
                        value['deadline'] = (now + timedelta(days=1) if kind == 'clamped' else
                                             now - timedelta(days=1)).isoformat()
                self.edit(path, change)
                if kind == 'turns':
                    self.edit(path, lambda v: v['config'].update(max_turns=1), name='manifest.json')
                with patch('duo.cli.CLIAdapter') as factory, redirect_stdout(io.StringIO()):
                    code = main(['--resume', str(path), '--json'])
                self.assertEqual(code, 4)
                factory.assert_not_called()
                self.assertEqual(self.record(path)['status'], 'limit_reached')
                self.assertEqual(self.record(path, 'verdict.json')['completion'], 'limit')

    def test_inconsistent_clock_refused(self):
        path = self.paused()
        self.edit(path, lambda v: v.update(deadline='2020-01-01T00:00:00+00:00'))
        self.refused(path, 'clock state')

    def test_run_and_repository_locks(self):
        path = self.paused()
        store = RunStore.open_existing(path)
        try:
            self.refused(path, 'run-directory lock')
        finally:
            store.close()
        with run_lock(self.repo):
            self.refused(path, 'lock for this repository')

    def test_disk_full_reservation_never_calls_adapter(self):
        original = Journal.write
        def disk_full(journal, **changes):
            if changes.get('in_flight'):
                raise OSError(errno.ENOSPC, 'No space left on device')
            return original(journal, **changes)
        with patch.object(Journal, 'write', disk_full):
            code, summary, adapter, _ = self.fresh()
        self.assertEqual(code, 1, summary)
        adapter.assert_not_called()
        self.assertEqual(self.document.read_text(), 'Original\n')
        self.assertEqual(self.record(Path(summary['run_dir']))['status'], 'error')

    def test_preflight_failure_is_terminal(self):
        with patch.object(CLIAdapter, 'preflight', side_effect=RunError('Probe failed')), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(['--repo', str(self.repo), '--state-dir', str(self.audit),
                         '--task', 'Task', '--codex-model', 'c', '--claude-model', 'a'])
        self.assertEqual(code, 1)
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        self.refused(path, 'terminal')

    def test_resume_parser_refuses_run_defining_options(self):
        path = self.paused()
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(['--resume', str(path), '--max-turns', '100'])

    def test_reconciliation_is_pure(self):
        path = self.pending(after=True)
        value, _ = load(path)
        original = deepcopy(value)
        recovered, events = reconcile(value, content_hash('Proposal\n'), self.record(path, 'state.json'))
        self.assertEqual(value, original)
        self.assertEqual(recovered['state']['turns'], 1)
        self.assertEqual(events[-1]['event'], 'review')

    def test_repeated_resumes_preserve_attempt_allowance_and_epoch(self):
        path = self.paused()
        code, summary, adapter, _ = self.resume(path, [KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(self.record(path)['attempts_current_turn'], 2)
        code, summary, adapter, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.record(path)['approval_epoch'], 2)
        self.assertEqual(self.record(path)['state']['attempts'], 5)
        self.assertEqual([e['attempt'] for e in self.events(path)
                          if e['event'] == 'attempt' and e['turn'] == 2], [1, 2, 3])

    def test_failed_retry_feedback_survives_interruption_during_backoff(self):
        from duo.workflow import Reviewer
        def interrupted_sleep(_):
            raise KeyboardInterrupt()
        def reviewer(*args, **kwargs):
            return Reviewer(*args, **kwargs, sleep=interrupted_sleep)
        with patch('duo.cli.Reviewer', side_effect=reviewer):
            path = self.paused([AgentError('Invalid structure')])
        self.assertEqual(self.record(path)['last_attempt']['outcome'], 'failed')
        code, summary, adapter, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertIn('Invalid structure', adapter.call_args_list[0].args[1])
        self.assertEqual(self.record(path)['state']['attempts'], 3)

    def test_answer_clarification_is_bound_and_retained(self):
        _, summary, _, _ = self.fresh([response('blocked', reason='Which database?')])
        source = Path(summary['run_dir'])
        path = self.paused([KeyboardInterrupt()], '--source-run', str(source), '--answer', 'SQLite')
        code, summary, adapter, _ = self.resume(path, [KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        self.assertIn('SQLite', adapter.call_args.args[1])
        self.edit(path, lambda v: v['answer'].update(answer='Changed'), name='manifest.json')
        self.refused(path, 'Manifest')

    def test_strict_checkpoint_shapes(self):
        mutations = [lambda v: v.update(segment=True),
                     lambda v: v.update(sequence=-1),
                     lambda v: v['state'].update(turns=True),
                     lambda v: v['state'].update(approved=['unknown']),
                     lambda v: v['state']['history'][0].update(decision='unknown'),
                     lambda v: v['config'].update(allow_web='false'),
                     lambda v: v['config'].update(timeout=True),
                     lambda v: v['inputs']['context'].append(
                         {'artifact': '../outside', 'sha256': '0' * 64, 'bytes': 0}),
                     lambda v: v.update(deadline='not-a-date'),
                     lambda v: v['state'].update(findings=[{}])]
        path = self.paused()
        original = (path / 'checkpoint.json').read_bytes()
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                (path / 'checkpoint.json').write_bytes(original)
                self.edit(path, mutate)
                self.refused(path, 'Invalid checkpoint')

    def test_duplicate_keys_and_deep_json_are_refused(self):
        path = self.paused()
        for data in ('{"schema_version":1,"schema_version":1}', '[' * 129 + ']' * 129):
            (path / 'checkpoint.json').write_text(data)
            self.refused(path, 'checkpoint.json')

    def test_checkpoint_and_artifact_symlinks_are_refused(self):
        path = self.paused()
        for name in ('checkpoint.json', 'plan-001.md', 'state.json'):
            artifact = path / name
            data = artifact.read_bytes()
            outside = self.root / 'outside'
            outside.write_bytes(data)
            artifact.unlink()
            artifact.symlink_to(outside)
            self.refused(path, 'symbolic link')
            artifact.unlink()
            artifact.write_bytes(data)

    def test_context_hash_tamper_refused(self):
        context = self.root / 'context.txt'
        context.write_text('Captured')
        path = self.paused(None, '--context-file', str(context))
        (path / 'context-001.bin').write_text('Tampered')
        self.refused(path, 'hash mismatch')

    def test_duplicate_review_event_refused(self):
        path = self.paused()
        event = next(e for e in self.events(path) if e['event'] == 'review')
        with (path / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        self.refused(path, 'duplicate review event')

    def test_recovered_verdict_passes_reference_gate(self):
        import subprocess
        import sys
        path = self.paused()
        code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        gate = Path(__file__).resolve().parents[1] / 'examples' / 'check_verdict.py'
        result = subprocess.run([sys.executable, str(gate), '--verdict', str(path / 'verdict.json'),
                                 '--document', str(self.document)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_signal_before_graph_invocation_still_has_checkpoint(self):
        with patch('duo.cli.build_graph', side_effect=KeyboardInterrupt()):
            path = self.paused([KeyboardInterrupt()])
        self.assertTrue((path / 'checkpoint.json').exists())
        code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)

    def test_publication_io_error_is_terminal_without_promoting_proposal(self):
        with patch.object(PlanFile, 'write', side_effect=OSError('Publication failed')):
            code, summary, _, _ = self.fresh([response('revise', 'Proposal\n')])
        self.assertEqual(code, 1, summary)
        path = Path(summary['run_dir'])
        self.assertEqual(self.record(path)['status'], 'error')
        self.assertEqual(self.record(path)['state']['turns'], 0)
        self.assertEqual(self.document.read_text(), 'Original\n')
        self.refused(path, 'terminal')

    def test_pending_nonpublishing_approval_is_committed_once(self):
        original = Journal.write
        def crash(journal, **changes):
            original(journal, **changes)
            if changes.get('pending'):
                raise Crash()
        with patch.object(Journal, 'write', crash), self.assertRaises(Crash):
            self.fresh([response('approve')])
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        code, summary, adapter, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual(self.record(path)['state']['turns'], 3)
        self.assertEqual(len([e for e in self.events(path)
                              if e['event'] == 'review' and e['turn'] == 1]), 1)

    def test_interrupt_after_preflight_on_resume_keeps_original_deadline(self):
        path = self.paused()
        before = self.record(path)['deadline']
        with patch('duo.cli.build_graph', side_effect=KeyboardInterrupt()):
            code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 130, summary)
        self.assertEqual(self.record(path)['deadline'], before)
        code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)

    def test_resume_preflight_failure_is_terminal_and_preserves_original_versions(self):
        path = self.paused()
        before = (path / 'versions.json').read_bytes()
        with patch.object(CLIAdapter, 'preflight', side_effect=RunError('Controls rejected')), \
                patch.object(CLIAdapter, '__call__') as adapter, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(['--resume', str(path)])
        self.assertEqual(code, 1)
        adapter.assert_not_called()
        self.assertEqual((path / 'versions.json').read_bytes(), before)
        self.refused(path, 'terminal')

    def test_partial_finding_event_batch_is_recovered_without_duplicates(self):
        original = RunStore.event
        finding = {'severity': 'low', 'title': 'Clarify', 'description': 'More detail',
                   'location': {'kind': 'subject', 'explanation': 'Whole document'}}
        def crash(store, value):
            original(store, value)
            if value['event'] == 'finding_reported':
                raise Crash()
        with patch.object(RunStore, 'event', crash), self.assertRaises(Crash):
            self.fresh([response('revise', 'Proposal\n', findings=[finding])])
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        code, summary, _, _ = self.resume(path, [response('approve'), response('approve',
            finding_updates=[{'id': 'F1', 'status': 'resolved', 'reason': 'Fixed',
                              'subject_hash': content_hash('Proposal\n')}])])
        self.assertEqual(code, 0, summary)
        self.assertEqual(len([e for e in self.events(path) if e['event'] == 'finding_reported']), 1)
        self.assertEqual(len([e for e in self.events(path)
                              if e['event'] == 'review' and e['turn'] == 1]), 1)

    def test_checkpoint_size_bound_and_private_mode(self):
        from duo.checkpoints import MAX_CHECKPOINT_BYTES
        path = self.paused()
        self.assertEqual((path / 'checkpoint.json').stat().st_mode & 0o777, 0o600)
        with (path / 'checkpoint.json').open('wb') as stream:
            stream.truncate(MAX_CHECKPOINT_BYTES + 1)
        self.refused(path, 'exceeds')

    def test_pending_terminal_decision_finishes_without_another_model_call(self):
        original = Journal.commit
        def crash(journal, state):
            if state['status'] == 'blocked':
                raise Crash()
            original(journal, state)
        with patch.object(Journal, 'commit', crash), self.assertRaises(Crash):
            self.fresh([response('blocked')])
        path = next(p.parent for p in self.audit.rglob('checkpoint.json'))
        with patch('duo.cli.CLIAdapter') as factory, redirect_stdout(io.StringIO()):
            code = main(['--resume', str(path)])
        self.assertEqual(code, 3)
        factory.assert_not_called()
        self.assertEqual(self.record(path)['status'], 'blocked')

    def test_recovery_can_itself_be_interrupted_between_audit_and_checkpoint(self):
        path = self.pending(after=True)
        def crash(journal, **changes):
            raise Crash()
        with patch.object(Journal, 'write', crash), self.assertRaises(Crash):
            self.resume(path)
        self.assertIsNotNone(self.record(path)['pending'])
        self.assertEqual(self.record(path, 'state.json')['turns'], 1)
        code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(len([e for e in self.events(path)
                              if e['event'] == 'review' and e['turn'] == 1]), 1)

    def test_original_crlf_document_resumes_without_rewriting_bytes(self):
        self.document.write_bytes(b'Original\r\n')
        path = self.paused()
        self.assertEqual(self.record(path)['document_sha256'], content_hash('Original\n'))
        code, summary, _, _ = self.resume(path)
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.document.read_bytes(), b'Original\r\n')

    def test_external_line_ending_change_is_still_refused(self):
        self.document.write_bytes(b'Original\r\n')
        path = self.paused()
        self.document.write_bytes(b'Original\n')
        self.refused(path, 'changed outside Duo')

    def test_resume_verdict_failure_preserves_error_or_signal_exit(self):
        for replies, expected in (([response('approve'), response('approve')], 1),
                                  ([KeyboardInterrupt()], 130)):
            with self.subTest(expected=expected):
                path = self.paused()
                with patch('duo.cli.write_verdict', side_effect=ValueError('Invalid verdict')):
                    code, summary, _, _ = self.resume(path, replies)
                self.assertEqual(code, expected, summary)

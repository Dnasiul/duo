"""Stored-record reports: inert HTML, historical layouts, and best-effort CLI output."""
from contextlib import redirect_stderr, redirect_stdout
from html import escape
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import patch

try:
    from .test_duo import Fixture, response
except ImportError:
    from test_duo import Fixture, response

from duo.adapters import CLIAdapter
from duo.cli import main, parser
from duo.models import AgentError, RunError, Terminated
from duo.report import render_report, write_report
from duo.storage import content_hash


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ReportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.run = self.root / 'run-stored'
        self.run.mkdir()

    def save(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding='utf-8')

    def record(self, status='agreed', document='Updated\n', reason='Review complete'):
        # Literal 0.5.0 layout: no report-specific fields or new workflow schema.
        manifest = {
            'started_at': '2026-11-23T10:00:00+00:00', 'task': 'Review the task',
            'criteria': 'Keep exact evidence', 'document': '/absent/project/plan.md',
            'config': {'mode': 'plan', 'codex_model': 'gpt-6-astra',
                       'claude_model': 'claude-opus-5-5', 'codex_backend': 'native',
                       'claude_backend': 'native', 'codex_effort': 'high',
                       'claude_effort': 'max', 'start': 'codex', 'max_turns': 12},
        }
        digest = content_hash(document)
        history = [
            {'agent': agent, 'decision': decision, 'reason': reason, 'plan_hash': digest,
             'timestamp': f'2026-11-23T10:0{n}:00+00:00', 'backend': 'native',
             'model': manifest['config'][f'{agent}_model']}
            for n, (agent, decision) in enumerate(
                [('codex', 'revise'), ('claude', 'approve'), ('codex', 'approve')], 1)
        ]
        state = {'task': manifest['task'], 'criteria': manifest['criteria'], 'plan': document,
                 'approved': ['claude', 'codex'], 'next_agent': 'claude', 'turns': 3,
                 'attempts': 4, 'status': status, 'history': history,
                 'last_diff': 'This prose must never be parsed', 'error': ''}
        if status != 'agreed':
            state['approved'] = []
            state['error'] = 'Recorded stop reason'
        if status == 'blocked':
            history[-1]['decision'] = 'blocked'
        self.save('manifest.json', manifest)
        self.save('versions.json', {'codex': 'codex-cli 0.171.0',
                                    'claude': '2.1.330 (Claude Code)', 'untested': ['codex']})
        self.save('state.json', state)
        self.save('result.json', {**state, 'untested_cli': {'codex': 'codex-cli 0.171.0'}})
        (self.run / 'plan-000.md').write_bytes(b'Original\n')
        (self.run / 'plan-001.md').write_bytes(document.encode('utf-8'))
        return manifest, state

    def invoke(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(list(args))
        return code, output.getvalue(), errors.getvalue()

    def test_agreed_summary_roster_approvals_and_determinism(self):
        _, state = self.record()
        html = render_report(self.run)
        for text in ('Turn 2: claude — approve', 'Turn 3: codex — approve',
                     'Both reviewers approved the same unchanged document.',
                     'Shared approval plan_hash: <code>' + content_hash(state['plan']),
                     '<dd>agreed</dd>', '<dd>claude, codex</dd>', 'run-stored',
                     'gpt-6-astra', 'claude-opus-5-5', 'codex-cli 0.171.0',
                     'experimental: untested CLI', '2.1.330 (Claude Code)',
                     '<dd>high</dd>', '<dd>max</dd>', '<dt>Attempts</dt><dd>4</dd>',
                     'Review the task', 'Keep exact evidence', '2026-11-23T10:00:00+00:00'):
            self.assertIn(text, html)
        self.assertNotIn(state['last_diff'], html)
        self.assertEqual(render_report(self.run), html)
        path = write_report(self.run)
        self.assertEqual(path.read_bytes(), html.encode('utf-8'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_all_record_text_is_escaped_and_page_has_no_active_content(self):
        hostile = '<script>alert(1)</script><img src="https://evil/" onerror="alert(2)"> & \'quote\''
        manifest, state = self.record(document=hostile, reason=hostile)
        for key in ('task', 'criteria', 'document', 'started_at'):
            manifest[key] = hostile
        for key in manifest['config']:
            manifest['config'][key] = hostile
        for turn in state['history']:
            for key in ('agent', 'timestamp', 'backend', 'model'):
                turn[key] = hostile
        state.update(status='error', approved=[hostile], error=hostile)
        self.save('manifest.json', manifest)
        self.save('result.json', state)
        self.save('versions.json', {'codex': hostile, 'claude': hostile})
        self.save('context.json', [{'label': hostile, 'bytes': hostile, 'sha256': hostile}])
        self.save('error.json', {'message': hostile})
        renamed = self.run.with_name('run-<svg onload=alert(3)>')
        self.run.rename(renamed)
        html = render_report(renamed)
        self.assertIn(escape(hostile), html)
        self.assertIn(escape(renamed.name), html)
        self.assertNotIn(hostile, html)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        tags = Tags(html).tags
        self.assertEqual(sum(tag == 'style' for tag, _ in tags), 1)
        self.assertTrue(all(tag not in ('script', 'img', 'svg', 'iframe', 'link', 'a') for tag, _ in tags))
        for _, attrs in tags:
            self.assertFalse(any(name in ('src', 'href', 'style') or name.startswith('on') for name in attrs))

    def test_zero_turn_record_text_is_escaped_and_page_has_no_active_content(self):
        hostile = '<script>alert(1)</script><img src="https://evil/" onerror="alert(2)"> & \'quote\''
        task, criteria, document = (f'{label}: {hostile}' for label in ('Task', 'Criteria', 'Document'))
        self.save('manifest.json', {'task': task, 'criteria': criteria, 'config': {}})
        (self.run / 'plan-000.md').write_text(document, encoding='utf-8')
        self.assertFalse((self.run / 'state.json').exists())
        self.assertFalse((self.run / 'result.json').exists())
        html = render_report(self.run)
        self.assertIn('<dt>Turns</dt><dd>0</dd>', html)
        for text in (task, criteria, document):
            self.assertIn(escape(text), html)
            self.assertNotIn(text, html)
        self.assertNotIn(hostile, html)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        tags = Tags(html).tags
        self.assertEqual(sum(tag == 'style' for tag, _ in tags), 1)
        self.assertTrue(all(tag not in ('script', 'img', 'svg', 'iframe', 'link', 'a') for tag, _ in tags))
        for _, attrs in tags:
            self.assertFalse(any(name in ('src', 'href', 'style') or name.startswith('on') for name in attrs))

    def test_non_agreed_outcomes_include_recorded_errors_and_reasons(self):
        for status in ('blocked', 'limit_reached', 'error'):
            with self.subTest(status=status):
                self.record(status, reason='Which storage should we use?')
                self.save('error.json', {'message': 'Additional failure details'})
                html = render_report(self.run)
                for text in (f'<dd>{status}</dd>', 'Recorded stop reason',
                             'Additional failure details', 'Which storage should we use?'):
                    self.assertIn(text, html)
                self.assertNotIn('Both reviewers approved', html)

    def test_diff_is_collapsible_and_approvals_leave_document_unchanged(self):
        self.record()
        html = render_report(self.run)
        self.assertIn('<details><summary>Document diff</summary>\n<pre>--- before\n+++ after\n', html)
        self.assertIn('-Original\n+Updated\n', html)
        self.assertEqual(html.count('<p>Document unchanged.</p>'), 2)

    def test_sparse_snapshots_follow_turn_numbers_and_hashes(self):
        _, state = self.record()
        history = state['history']
        history[0].update(decision='approve', plan_hash=content_hash('Original\n'))
        history[1].update(decision='revise', plan_hash=content_hash('Second\n'))
        history[2].update(decision='revise', plan_hash=content_hash('Third\n'))
        state.update(plan='Third\n', status='limit_reached', approved=[])
        self.save('result.json', state)
        (self.run / 'plan-001.md').write_text('Unpublished proposal\n')
        (self.run / 'plan-002.md').write_text('Second\n')
        (self.run / 'plan-003.md').write_text('Third\n')
        html = render_report(self.run)
        self.assertIn('-Original\n+Second\n', html)
        self.assertIn('-Second\n+Third\n', html)
        self.assertNotIn('Unpublished proposal', html)

    def test_missing_snapshots_and_hash_mismatches_are_graceful(self):
        self.record()
        (self.run / 'plan-001.md').write_text('Wrong hash\n')
        html = render_report(self.run)
        self.assertIn('Diff unavailable', html)
        self.assertNotIn('+Wrong hash', html)
        self.assertIn('Turn 3: codex', html)
        (self.run / 'plan-001.md').unlink()
        (self.run / 'plan-000.md').unlink()
        self.assertIn('Diff unavailable', render_report(self.run))

    def test_snapshot_bytes_are_hashed_without_newline_translation(self):
        self.record(document='Unicode é\r\nNo trailing newline')
        html = render_report(self.run)
        self.assertNotIn('Diff unavailable', html)
        self.assertIn('\\ No newline at end of file', html)
        self.assertIn('+Unicode é\r\n', html)

    def test_context_is_metadata_only_and_original_sources_are_never_read(self):
        manifest, _ = self.record()
        secret_source = self.root / 'source.txt'
        secret_source.write_text('Outside source must not appear')
        manifest['document'] = str(secret_source)
        self.save('manifest.json', manifest)
        self.save('context.json', [{'order': 1, 'label': 'Saved evidence', 'bytes': 42,
                                    'sha256': 'a' * 64, 'source': str(secret_source),
                                    'artifact': '../source.txt'}])
        (self.run / 'context-001.bin').write_text('Captured contents must not appear')
        html = render_report(self.run)
        self.assertIn('Saved evidence', html)
        self.assertIn('<dt>Bytes</dt><dd>42</dd>', html)
        self.assertIn('a' * 64, html)
        self.assertNotIn('Outside source must not appear', html)
        self.assertNotIn('Captured contents must not appear', html)

    def test_symlinked_records_and_snapshots_are_not_followed(self):
        self.record()
        outside = self.root / 'outside'
        outside.write_text('Outside contents')
        snapshot = self.run / 'plan-000.md'
        snapshot.unlink()
        snapshot.symlink_to(outside)
        self.assertIn('Diff unavailable', render_report(self.run))
        self.assertNotIn('Outside contents', render_report(self.run))
        manifest = self.run / 'manifest.json'
        manifest.unlink()
        manifest.symlink_to(outside)
        code, _, errors = self.invoke('--render-report', str(self.run))
        self.assertEqual(code, 1)
        self.assertIn('Could not render report', errors)

    def test_copilot_versions_and_absent_optional_metadata(self):
        manifest, _ = self.record()
        manifest['config']['claude_backend'] = 'copilot'
        self.save('manifest.json', manifest)
        self.save('versions.json', {'codex': 'Codex version', 'copilot': 'Copilot version',
                                    'untested': ['copilot']})
        html = render_report(self.run)
        self.assertIn('Copilot version (experimental: untested CLI)', html)
        (self.run / 'versions.json').unlink()
        self.assertIn('Not recorded', render_report(self.run))

    def test_050_layout_renders_through_standalone_cli_without_review_arguments(self):
        self.record()
        process = subprocess.run([sys.executable, '-m', 'duo', '--render-report', str(self.run)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        path = self.run / 'report.html'
        self.assertEqual(process.stdout.strip(), str(path.resolve()))
        self.assertIn('<dd>agreed</dd>', path.read_text())
        destination = self.root / 'custom.html'
        code, output, errors = self.invoke(f'--render-report={self.run}', '--report-output', str(destination))
        self.assertEqual((code, errors), (0, ''))
        self.assertEqual(output.strip(), str(destination))
        self.assertEqual(path.read_bytes(), destination.read_bytes())
        self.assertIn('--render-report RUN_DIR', parser().format_help())

    def test_interrupted_050_state_without_result_or_versions(self):
        _, state = self.record(status='running')
        (self.run / 'result.json').unlink()
        (self.run / 'versions.json').unlink()
        code, _, errors = self.invoke('--render-report', str(self.run))
        self.assertEqual((code, errors), (0, ''))
        self.assertIn('<dd>interrupted</dd>', (self.run / 'report.html').read_text())
        self.assertEqual(json.loads((self.run / 'state.json').read_text()), state)

    def test_terminal_state_with_later_error_without_result_renders_as_error(self):
        self.record()
        (self.run / 'result.json').unlink()
        self.save('error.json', {'status': 'error', 'message': 'Later storage failure'})
        html = render_report(self.run)
        self.assertIn('<dd>error</dd>', html)
        self.assertIn('Later storage failure', html)
        self.assertNotIn('<dd>agreed</dd>', html)
        self.assertNotIn('Both reviewers approved', html)

    def test_manifest_without_state_or_initial_snapshot_renders_zero_turns(self):
        self.record()
        for name in ('result.json', 'state.json', 'plan-000.md'):
            (self.run / name).unlink()
        self.save('error.json', {'status': 'error', 'message': 'Startup failed'})
        html = render_report(self.run)
        for text in ('<dd>interrupted</dd>', '<dt>Turns</dt><dd>0</dd>',
                     '<dt>Attempts</dt><dd>Not recorded</dd>',
                     'No completed review turns recorded.', 'Review the task',
                     'Keep exact evidence', 'Startup failed',
                     'Initial document unavailable: plan-000.md is missing or unreadable.',
                     '<summary>Final recorded document</summary><pre></pre>'):
            self.assertIn(text, html)

    def test_result_takes_precedence_and_unconfirmed_agreement_is_not_invented(self):
        _, state = self.record()
        self.save('state.json', {**state, 'status': 'running'})
        self.assertIn('<dd>agreed</dd>', render_report(self.run))
        state['history'][-1]['plan_hash'] = 'bad hash'
        self.save('result.json', state)
        html = render_report(self.run)
        self.assertIn('cannot be confirmed', html)
        self.assertNotIn('Both reviewers approved', html)

    def test_invalid_directory_records_and_output_fail_clearly(self):
        for path in (self.root / 'absent', self.run):
            code, output, errors = self.invoke('--render-report', str(path))
            self.assertEqual((code, output), (1, ''))
            self.assertIn('Could not render report', errors)
        self.record()
        for value in ('{bad JSON', '[]', '{}', '{"history": [1]}'):
            (self.run / 'result.json').write_text(value)
            code, _, errors = self.invoke('--render-report', str(self.run))
            self.assertEqual(code, 1)
            self.assertIn('Invalid', errors)
        self.record()
        before = (self.run / 'result.json').read_bytes()
        code, _, errors = self.invoke('--render-report', str(self.run), '--report-output',
                                     str(self.run / 'result.json'))
        self.assertEqual(code, 1)
        self.assertIn('must not overwrite', errors)
        self.assertEqual((self.run / 'result.json').read_bytes(), before)

    def review(self, replies, *extra):
        with patch.object(CLIAdapter, 'preflight', return_value={}), \
                patch.object(CLIAdapter, '__call__', side_effect=replies):
            code, output, errors = self.invoke(
                '--repo', str(self.repo), '--state-dir', str(self.audit), '--task', 'Task',
                '--codex-model', 'c', '--claude-model', 'a', '--json', *extra)
        summary = json.loads(output)
        return code, summary, errors, Path(summary['run_dir'])

    def test_auto_emit_for_every_terminal_outcome(self):
        for replies, extra, expected, status in (
            ([response('revise', 'Draft'), response('approve'), response('approve')], [], 0, 'agreed'),
            ([response('blocked', reason='Need a decision')], [], 3, 'blocked'),
            ([response('revise', 'Updated')], ['--max-turns', '1'], 4, 'limit_reached'),
            ([AgentError('Agent failed', retryable=False)], [], 1, 'error'),
        ):
            with self.subTest(status=status):
                code, summary, errors, run = self.review(replies, *extra)
                self.assertEqual((code, summary['status']), (expected, status))
                self.assertNotIn('could not write HTML report', errors)
                self.assertIn(f'<dd>{status}</dd>', (run / 'report.html').read_text())

    def test_auto_emit_after_interrupt_and_cli_exception_with_saved_state(self):
        for exception, expected in ((KeyboardInterrupt(), 130), (RuntimeError('CLI broke'), 1)):
            with self.subTest(exception=type(exception)):
                (self.repo / 'plan.md').unlink(missing_ok=True)
                code, summary, _, run = self.review([response('revise', 'Draft'), exception])
                self.assertEqual(code, expected)
                self.assertFalse((run / 'result.json').exists())
                html = (run / 'report.html').read_text()
                self.assertIn('<dd>interrupted</dd>', html)
                self.assertIn(escape(summary['message']), html)

    def test_auto_emit_when_first_adapter_reply_is_interrupt(self):
        (self.repo / 'plan.md').write_text('Initial document\n')
        task = 'Preserve the manifest task before the first review completes'
        for exception, expected in ((KeyboardInterrupt(), 130),
                                    (Terminated(signal.SIGTERM), 143)):
            with self.subTest(exception=type(exception)):
                code, summary, errors, run = self.review(
                    [exception], '--task', task, '--criteria', 'Exact evidence')
                self.assertEqual(code, expected)
                self.assertFalse((run / 'result.json').exists())
                self.assertFalse((run / 'state.json').exists())
                self.assertTrue((run / 'report.html').exists())
                self.assertNotIn('could not write HTML report', errors)
                html = (run / 'report.html').read_text()
                for text in ('<dd>interrupted</dd>', 'No completed review turns recorded.',
                             '<dt>Turns</dt><dd>0</dd>', '<dt>Attempts</dt><dd>Not recorded</dd>',
                             task, 'Exact evidence', 'Initial document\n',
                             escape(summary['message'])):
                    self.assertIn(text, html)

    def test_auto_emit_after_startup_budget_failure(self):
        with patch('duo.cli.Reviewer.check_startup_budget', side_effect=RunError('Startup budget exceeded')):
            code, summary, errors, run = self.review([])
        self.assertEqual(code, 1)
        self.assertFalse((run / 'state.json').exists())
        self.assertFalse((run / 'result.json').exists())
        self.assertNotIn('could not write HTML report', errors)
        html = (run / 'report.html').read_text()
        self.assertIn('<dd>interrupted</dd>', html)
        self.assertIn('No completed review turns recorded.', html)
        self.assertIn(escape(summary['message']), html)

    def test_report_failure_never_changes_outcome_or_existing_artifacts(self):
        cases = ((exception, expected, report_error)
                 for exception, expected in ((None, 0), (KeyboardInterrupt(), 130),
                                             (RuntimeError('Original failure'), 1))
                 for report_error in (OSError('report disk full'), KeyboardInterrupt(),
                                      Terminated(signal.SIGTERM)))
        for exception, expected, report_error in cases:
            with self.subTest(expected=expected, report_error=type(report_error)):
                (self.repo / 'plan.md').unlink(missing_ok=True)
                saved = {}
                def fail(run):
                    saved.update({p: p.read_bytes() for p in run.iterdir() if p.is_file()})
                    raise report_error
                replies = [response('revise', 'Draft')]
                replies += [exception] if exception else [response('approve'), response('approve')]
                with patch('duo.cli.write_report', side_effect=fail):
                    code, _, errors, run = self.review(replies)
                self.assertEqual(code, expected)
                self.assertIn(f'Warning: could not write HTML report: {report_error}', errors)
                self.assertTrue(saved)
                for path, data in saved.items():
                    self.assertEqual(path.read_bytes(), data)
                self.assertFalse((run / 'report.html').exists())

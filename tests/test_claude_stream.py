"""Native Claude accepts only an unambiguous completed stream; keeps every byte."""
from dataclasses import replace
import json
import signal
import sys
from unittest.mock import patch

from test_duo import Fixture, response
from duo.adapters import CLIAdapter, claude_result
from duo.adapters import CLAUDE_MAX_JSON_DEPTH
from duo.models import AgentError, AgentTimeout, Terminated
from duo.storage import PlanFile
from duo.workflow import Reviewer, initial_state


def terminal(**changes):
    return {"type": "result", "subtype": "success", "is_error": False,
            "structured_output": response("approve"), **changes}


def stream(*events):
    return ''.join(json.dumps(event) + '\n' for event in events)


PARTIAL = {"type": "assistant", "message": {"content": [
    {"type": "text", "text": json.dumps(response("approve"))},
    {"type": "tool_use", "id": "output-1", "name": "StructuredOutput",
     "input": response("revise", "Uncommitted revision")}]}}


class ClaudeStreamTests(Fixture):
    def adapter(self, raw, code=0, suffix='', **changes):
        binary = self.executable('claude-stream',
                                 'import sys, time\nsys.stdin.read()\n'
                                 f'sys.stdout.write({raw!r})\nsys.stdout.flush()\n'
                                 f'{suffix}\nraise SystemExit({code})\n')
        adapter = CLIAdapter(replace(self.config, claude_bin=binary, **changes), self.store)
        adapter.policy_verified = True
        return adapter

    def captured(self, raw):
        attempt = max(self.store.path.glob('attempt-*'), key=lambda p: p.stat().st_mtime_ns)
        self.assertEqual((attempt / 'stdout.log').read_bytes(), raw.encode())
        return json.loads((attempt / 'claude-stream.json').read_text())

    def test_terminal_only_and_usage_absent_versus_zero(self):
        for usage in (None, {}, {'input_tokens': 0, 'output_tokens': 0}):
            raw = stream({'type': 'system', 'subtype': 'init'}, PARTIAL,
                         {'type': 'future_event'}, terminal(**({} if usage is None else {'usage': usage})))
            adapter = self.adapter(raw)
            self.assertEqual(adapter('claude', 'Review', 5), response('approve'))
            meta = self.captured(raw)
            self.assertEqual(meta['completion'], 'success')
            self.assertEqual(meta['process_status'], 'exited')
            self.assertEqual(meta['usage_available'], usage is not None)
            self.assertEqual(meta['usage'], usage)
            self.assertTrue(meta['payload_validated'])
            command = adapter.command('claude', self.root)
            self.assertEqual(command[command.index('--output-format') + 1], 'stream-json')
            self.assertIn('--verbose', command)

    def test_malformed_and_truncated_events_fail_closed(self):
        for damaged in ('not-json\n', '{"type":"assistant"', '[]\n', 'null\n', '{}\n', '\xff'):
            for raw in (damaged + stream(terminal()), stream(terminal()) + damaged):
                with self.subTest(raw=raw), self.assertRaises(AgentError):
                    self.adapter(raw)('claude', 'Review', 5)
                self.assertEqual(self.captured(raw)['completion'], 'invalid')

    def test_invalid_utf8_is_retained(self):
        adapter = self.adapter('', suffix="sys.stdout.buffer.write(b'\\xff'); sys.stdout.flush()")
        with self.assertRaises(AgentError):
            adapter('claude', 'Review', 5)
        attempt = next(self.store.path.glob('attempt-*'))
        self.assertEqual((attempt / 'stdout.log').read_bytes(), b'\xff')
        self.assertFalse(json.loads((attempt / 'claude-stream.json').read_text())['payload_validated'])

    def test_missing_multiple_and_nonterminal_results(self):
        cases = [(stream(PARTIAL), 'missing'), ('', 'missing'),
                 (stream(terminal(), terminal()), 'ambiguous'),
                 (stream(terminal(), PARTIAL), 'invalid')]
        for raw, completion in cases:
            with self.subTest(completion=completion), self.assertRaises(AgentError):
                self.adapter(raw)('claude', 'Review', 5)
            self.assertEqual(self.captured(raw)['completion'], completion)

    def test_nonzero_exit_never_accepts_successful_payload(self):
        raw = stream(PARTIAL, terminal())
        with self.assertRaises(AgentError):
            self.adapter(raw, code=7)('claude', 'Review', 5)
        meta = self.captured(raw)
        self.assertEqual(meta['returncode'], 7)
        self.assertEqual(meta['completion'], 'success')
        self.assertFalse(meta['payload_validated'])

    def test_unsuccessful_terminal_at_either_exit_code(self):
        for code in (0, 1):
            raw = stream(PARTIAL, terminal(subtype='error_max_turns', is_error=True,
                                          structured_output=None, usage={'input_tokens': 0}))
            with self.subTest(code=code), self.assertRaisesRegex(AgentError, 'internal turn limit') as caught:
                self.adapter(raw, code=code)('claude', 'Review', 5)
            self.assertFalse(caught.exception.retryable)
            meta = self.captured(raw)
            self.assertEqual(meta['completion'], 'unsuccessful')
            self.assertTrue(meta['usage_available'])
            self.assertFalse(meta['payload_validated'])

    def test_envelope_and_payload_schema_checks(self):
        for changes in ({'is_error': 0}, {'subtype': None}, {'structured_output': None},
                        {'structured_output': response('invented')},
                        {'structured_output': {**response('approve'), 'extra': True}}):
            raw = stream(terminal(**changes))
            with self.subTest(changes=changes), self.assertRaises(AgentError):
                self.adapter(raw)('claude', 'Review', 5)
            self.assertFalse(self.captured(raw)['payload_validated'])

    def test_only_error_metadata_is_classified(self):
        narration = {'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'invalid api key SECRET'}]}}
        for code in (0, 1):
            raw = stream(narration, terminal(subtype='error_other', is_error=True,
                                            result='transient problem SECRET'))
            with self.assertRaises(AgentError) as caught:
                self.adapter(raw, code=code)('claude', 'Review', 5)
            self.assertTrue(caught.exception.retryable)
            self.assertNotIn('SECRET', str(caught.exception))
        raw = stream(narration, terminal(is_error=True, result='invalid api key SECRET'))
        with self.assertRaisesRegex(AgentError, '^Agent authentication is invalid$'):
            self.adapter(raw)('claude', 'Review', 5)

    def test_timeout_keeps_partial_events(self):
        raw = stream(PARTIAL)
        with self.assertRaises(AgentTimeout):
            self.adapter(raw, suffix='time.sleep(30)')('claude', 'Review', 0.3)
        meta = self.captured(raw)
        self.assertEqual(meta['process_status'], 'timeout')
        self.assertEqual(meta['completion'], 'missing')

    def test_interrupt_keeps_events_and_never_accepts_even_a_terminal(self):
        for exception in (KeyboardInterrupt(), Terminated(signal.SIGTERM)):
            raw = stream(PARTIAL, terminal())
            adapter = self.adapter(raw, suffix='time.sleep(30)')
            with patch.object(adapter, 'limit_message', side_effect=exception), \
                    self.assertRaises(type(exception)):
                adapter('claude', 'Review', 5)
            meta = self.captured(raw)
            self.assertEqual(meta['process_status'], 'interrupted')
            self.assertFalse(meta['payload_validated'])

    def test_output_limits_keep_bytes_and_refuse_review(self):
        raw = stream({'type': 'assistant', 'padding': 'x' * (1024 * 1024)}, terminal())
        for suffix in ('', 'time.sleep(30)'):
            with self.assertRaisesRegex(AgentError, 'output limit') as caught:
                self.adapter(raw, suffix=suffix, max_output_mb=1)('claude', 'Review', 5)
            self.assertFalse(caught.exception.retryable)
            self.assertFalse(self.captured(raw)['payload_validated'])

    def test_parser_uses_configured_output_limit_above_16_mib(self):
        path = self.root / 'large.jsonl'
        raw = stream({'type': 'user', 'content': 'x' * (1024 * 1024)}) * 17 + stream(terminal())
        path.write_text(raw)
        for limit in (18, self.config.max_output_mb):
            adapter = self.adapter('', max_output_mb=limit,
                                   suffix=f'sys.stdout.buffer.write(open({str(path)!r}, "rb").read())')
            self.assertEqual(adapter('claude', 'Review', 5), response('approve'))
            self.assertTrue(self.captured(raw)['payload_validated'])
        with self.assertRaisesRegex(AgentError, '17 MiB') as caught:
            claude_result(path, {}, max_output_mb=17)
        self.assertFalse(caught.exception.retryable)

    def test_deeply_nested_json_is_an_invalid_stream(self):
        depth = max(10000, sys.getrecursionlimit() + 100)
        raw = '{"type":"assistant","data":' + '[' * depth + '0' + ']' * depth + '}\n'
        path = self.root / 'nested.jsonl'
        path.write_text(raw)
        with self.assertRaisesRegex(AgentError, 'invalid stream'):
            claude_result(path, {}, max_output_mb=self.config.max_output_mb)
        for code in (0, 1):
            with self.subTest(code=code), self.assertRaises(AgentError):
                self.adapter(raw, code=code)('claude', 'Review', 5)
            self.assertEqual(self.captured(raw)['completion'], 'invalid')

    def test_metadata_write_failure_preserves_original_error(self):
        for original in (KeyboardInterrupt(), Terminated(signal.SIGTERM),
                         AgentTimeout('original timeout'), AgentError('original error')):
            adapter = self.adapter(stream(terminal()))
            with self.subTest(error=type(original)), \
                    patch('duo.adapters.run_process', side_effect=original), \
                    patch.object(self.store, 'json', side_effect=OSError('write failed')), \
                    self.assertRaises(type(original)) as caught:
                adapter('claude', 'Review', 5)
            self.assertIs(caught.exception, original)

    def test_metadata_write_failure_refuses_success(self):
        adapter = self.adapter(stream(terminal()))
        with patch.object(self.store, 'json', side_effect=OSError('write failed')), \
                self.assertRaisesRegex(AgentError, 'Cannot write Claude stream metadata') as caught:
            adapter('claude', 'Review', 5)
        self.assertFalse(caught.exception.retryable)

    def test_partial_approval_or_revision_cannot_enter_workflow(self):
        (self.repo / 'plan.md').write_text('Original')
        raw = stream(PARTIAL)
        adapter = self.adapter(raw, start='claude', max_attempts=1)
        config = adapter.config
        node = Reviewer(config, adapter, PlanFile(self.repo), self.store)
        state = initial_state('Task', 'Criteria', 'Original', config)
        result = node(state)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['approved'], [])
        self.assertEqual(result['turns'], 0)
        self.assertEqual(result['history'], [])
        self.assertEqual((self.repo / 'plan.md').read_text(), 'Original')

    def test_json_depth_boundary(self):
        path = self.root / 'boundary.jsonl'
        for opening, closing in (('[', ']'), ('{"nested":', '}')):
            for depth in (CLAUDE_MAX_JSON_DEPTH - 1, CLAUDE_MAX_JSON_DEPTH):
                raw = ('{"type":"assistant","data":' + opening * (depth - 1)
                       + '0' + closing * (depth - 1) + '}\n' + stream(terminal()))
                path.write_text(raw)
                metadata = {}
                with self.subTest(opening=opening, depth=depth):
                    self.assertEqual(claude_result(path, metadata, max_output_mb=1), terminal())
                    self.assertEqual(metadata['completion'], 'success')

    def test_excessive_json_depth_is_rejected_before_parsing(self):
        path = self.root / 'too-deep.jsonl'
        for opening, closing in (('[', ']'), ('{"nested":', '}')):
            raw = ('{"type":"assistant","data":' + opening * CLAUDE_MAX_JSON_DEPTH
                   + '0' + closing * CLAUDE_MAX_JSON_DEPTH + '}\n')
            path.write_text(raw)
            metadata = {}
            with self.subTest(opening=opening), patch('duo.adapters.parse_json') as parse:
                with self.assertRaisesRegex(AgentError, 'invalid stream'):
                    claude_result(path, metadata, max_output_mb=1)
                parse.assert_not_called()
                self.assertEqual(metadata['completion'], 'invalid')

    def test_json_depth_ignores_brackets_and_escapes_in_strings(self):
        path = self.root / 'strings.jsonl'
        for text in ('[{' * CLAUDE_MAX_JSON_DEPTH, ']}' * CLAUDE_MAX_JSON_DEPTH,
                     'escaped " quote ' + '[' * CLAUDE_MAX_JSON_DEPTH,
                     '\\"' + '{' * CLAUDE_MAX_JSON_DEPTH + '\\\\'):
            raw = stream({'type': 'assistant', text: text, 'after': {'value': 0}}, terminal())
            path.write_text(raw)
            with self.subTest(text=text):
                self.assertEqual(claude_result(path, {}, max_output_mb=1), terminal())

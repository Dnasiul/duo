"""Fixture probes use production environments; captures contain names only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from duo.models import RunError
from duo.policy import reviewer_environment

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_fixtures.py"
# All values are synthetic, including permitted authentication and runtime values.
RUNTIME = {
    "HOME": "/home/user", "PATH": "/synthetic/bin", "USER": "user",
    "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "HTTPS_PROXY": "https://proxy.invalid",
    "XDG_RUNTIME_DIR": "/synthetic/runtime", "NODE_EXTRA_CA_CERTS": "/synthetic/ca.pem",
}
AUTH = {
    "codex": {"CODEX_HOME": "/synthetic/codex", "CODEX_API_KEY": "synthetic-codex",
              "OPENAI_API_KEY": "synthetic-openai"},
    "claude": {"CLAUDE_CONFIG_DIR": "/synthetic/claude",
               "ANTHROPIC_API_KEY": "synthetic-anthropic",
               "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth"},
}
DROPPED = {
    "AWS_SECRET_ACCESS_KEY": "synthetic-aws", "UNRELATED_API_KEY": "synthetic-unrelated",
    "GH_TOKEN": "synthetic-github", "SSH_AUTH_SOCK": "/synthetic/agent.sock",
    "OPENAI_BASE_URL": "https://openai.invalid", "ANTHROPIC_BASE_URL": "https://claude.invalid",
    "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_MESSAGING_TOKEN": "synthetic-session",
    "NODE_OPTIONS": "--require /synthetic/inject.js",
}

VERSIONS = {"codex": "codex-cli 1.2.3\n", "claude": "1.2.3 (Claude Code)\n",
            "copilot": "GitHub Copilot CLI 1.2.3.\nRun 'copilot update' to check for updates.\n"}


class RecordFixtureTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
        self.recorder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recorder)
        patcher = patch.dict(os.environ, {**RUNTIME, **AUTH["codex"],
                                          **AUTH["claude"], **DROPPED}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(self.recorder, "write")
        self.write = patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        patcher = patch.object(
            self.recorder, "check_claude_managed_files",
            side_effect=lambda: self.events.append("managed"))
        self.managed = patcher.start()
        self.addCleanup(patcher.stop)
        self.captures = []
        patcher = patch.object(
            self.recorder.subprocess, "run", side_effect=self.capture)
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, *clis):
        return argparse.Namespace(cli=list(clis), codex_bin="codex", claude_bin="claude",
                                  copilot_bin="copilot")

    def capture(self, argv, *, env, **kwargs):
        # A required env argument and the type check reject implicit inheritance.
        self.assertIsInstance(env, dict)
        cli = argv[0]
        probe = next(name for name in ("--version", "--help", "features", "mcp", "doctor", "config")
                     if name in argv)
        self.events.append((cli, probe))
        self.captures.append((cli, probe, set(env)))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        if cli == "codex":
            if probe == "mcp":
                home = Path(env["CODEX_HOME"])
                self.assertNotEqual(str(home), os.environ.get("CODEX_HOME"))
                self.assertTrue(home.is_dir())
                self.assertEqual(list(home.iterdir()), [])
            elif "CODEX_HOME" in os.environ:
                self.assertEqual(env["CODEX_HOME"], os.environ["CODEX_HOME"])
        stdout = VERSIONS[cli] if probe == "--version" else "synthetic output\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    def assert_native_environments(self):
        for cli, probe, names in self.captures:
            with self.subTest(cli=cli, probe=probe):
                expected = set(reviewer_environment(cli))
                if probe == "mcp":
                    expected.add("CODEX_HOME")
                self.assertEqual(names, expected)
                self.assertTrue(set(RUNTIME) <= names)
                self.assertTrue((set(AUTH[cli]) - {"CODEX_HOME"}) <= names)
                self.assertFalse(set(DROPPED) & names)
                other = "claude" if cli == "codex" else "codex"
                self.assertFalse(set(AUTH[other]) & names)

    def test_fixture_directory_uses_the_anchored_version_parser(self):
        for cli in ("codex", "claude", "copilot"):
            with self.subTest(cli=cli):
                self.write.reset_mock()
                self.recorder.record_probes(self.args(cli))
                targets = {call.args[0] for call in self.write.call_args_list}
                self.assertEqual(targets, {self.recorder.FIXTURES / f"{cli}-1.2.3"})
        # Output that is not the CLI's own version line stops before any write.
        for stdout in ("Update available: 9.9.9\ncodex-cli 1.2.3\n", "codex-cli 1.2.3\nnew: 1.3.0\n",
                       "1.2.3 (Claude Code)\n", "synthetic-cli 1.2.3\n"):
            with self.subTest(stdout=stdout):
                self.write.reset_mock()
                with patch.dict(VERSIONS, {"codex": stdout}), \
                        self.assertRaisesRegex(SystemExit, "Cannot parse the codex version"):
                    self.recorder.record_probes(self.args("codex"))
                self.write.assert_not_called()

    def test_every_native_probe_gets_its_reviewer_environment(self):
        probes = {"codex": ["--version", "--help", "features", "mcp"],
                  "claude": ["--version", "--help", "doctor"]}
        for clis in (("codex",), ("claude",), ("codex", "claude")):
            with self.subTest(clis=clis):
                self.captures.clear()
                self.recorder.record_probes(self.args(*clis))
                self.assertEqual([(cli, probe) for cli, probe, _ in self.captures],
                                 [(cli, probe) for cli in clis for probe in probes[cli]])
                self.assert_native_environments()

    def test_temporary_codex_home_adds_only_home_when_parent_has_none(self):
        del os.environ["CODEX_HOME"]
        self.recorder.record_probes(self.args("codex"))
        self.assert_native_environments()
        for _, probe, names in self.captures:
            self.assertEqual("CODEX_HOME" in names, probe == "mcp")
        self.assertNotIn("CODEX_HOME", os.environ)

    def test_managed_checks_precede_native_probes_and_surround_doctor(self):
        self.recorder.record_probes(self.args("codex", "claude"))
        self.assertEqual(self.events, [
            "managed", ("codex", "--version"), ("codex", "--help"),
            ("codex", "features"), ("codex", "mcp"),
            ("claude", "--version"), ("claude", "--help"),
            "managed", ("claude", "doctor"), "managed",
        ])

    def test_managed_configuration_failure_stops_recording(self):
        for check, expected_probes in ((1, []), (2, ["--version", "--help"]),
                                       (3, ["--version", "--help", "doctor"])):
            with self.subTest(check=check):
                self.captures.clear()
                self.write.reset_mock()
                self.managed.side_effect = [None] * (check - 1) + [RunError("managed policy")]
                with self.assertRaisesRegex(RunError, "managed policy"):
                    self.recorder.record_probes(self.args("claude"))
                self.assertEqual([probe for _, probe, _ in self.captures], expected_probes)
                self.assertNotIn("doctor.txt", [call.args[1] for call in self.write.call_args_list])

    def test_copilot_keeps_its_existing_environment_builder(self):
        with patch.object(self.recorder.copilot, "environment",
                          wraps=self.recorder.copilot.environment) as builder:
            self.recorder.record_probes(self.args("copilot"))
        builder.assert_called_once()
        self.managed.assert_not_called()
        self.assertEqual([probe for _, probe, _ in self.captures],
                         ["--version", "--help", "config"])
        for cli, probe, names in self.captures:
            with self.subTest(probe=probe):
                self.assertEqual(cli, "copilot")
                self.assertTrue(set(RUNTIME) | {"GH_TOKEN", "COPILOT_HOME", "COPILOT_CACHE_HOME"}
                                <= names)
                self.assertFalse((set(DROPPED) - {"GH_TOKEN"}) & names)
                self.assertFalse((set(AUTH["codex"]) | set(AUTH["claude"])) & names)
        for call in self.run.call_args_list:
            self.assertEqual(call.args[0][1], "--no-auto-update")

    def test_claude_sanitizer_preserves_correlations_inputs_and_terminal(self):
        import json
        convert = self.recorder.claude_events()
        payload = {'decision': 'revise', 'plan': 'x' * 500, 'reason': 'Synthetic', 'findings': [], 'finding_updates': []}
        request = convert({'type': 'assistant', 'session_id': 'private-session', 'message': {
            'id': 'private-message', 'content': [
                {'type': 'thinking', 'thinking': 'private reasoning', 'signature': 'opaque'},
                {'type': 'tool_use', 'name': 'Read', 'id': 'private-tool',
                 'input': {'file_path': '/home/user/' + 'a' * 500}}]},
            'wire_tool_inputs': {'private-tool': 'x' * 500}})
        denied = convert({'type': 'system', 'subtype': 'permission_denied',
                          'tool_name': 'Read', 'tool_use_id': 'private-tool'})
        result = convert({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'private-tool', 'is_error': True,
             'content': 'Denied ' + 'x' * 500}]}})
        final = convert({'type': 'result', 'subtype': 'success', 'is_error': False,
                         'structured_output': payload, 'usage': {'input_tokens': 0}})
        tool = request['message']['content'][1]
        self.assertEqual(request['wire_tool_inputs'], {tool['id']: 'x' * 500})
        self.assertEqual(tool['id'], denied['tool_use_id'])
        self.assertEqual(tool['id'], result['message']['content'][0]['tool_use_id'])
        self.assertTrue(result['message']['content'][0]['is_error'])
        self.assertTrue(tool['input']['file_path'].endswith('a' * 500))
        self.assertEqual(final['structured_output'], payload)
        self.assertEqual(final['usage'], {'input_tokens': 0})
        text = json.dumps([request, denied, result, final])
        for secret in ('private-session', 'private-message', 'private-tool', 'private reasoning', 'opaque'):
            self.assertNotIn(secret, text)
        self.assertIn('[trimmed]', text)
        # Sanitization must not repair an invalid response by dropping extra keys.
        invalid = convert({'type': 'result', 'structured_output': {**payload, 'thinking': 'extra'}})
        self.assertIn('thinking', invalid['structured_output'])
        error = convert({'type': 'result', 'is_error': True,
                         'result': 'x' * 500 + ' invalid api key'})
        self.assertTrue(error['result'].endswith('invalid api key'))

    def test_claude_sanitizer_removes_rate_limit_account_metadata(self):
        event = self.recorder.claude_events()({
            'type': 'rate_limit_event', 'uuid': 'private-event',
            'rate_limit_info': {'status': 'allowed', 'overageStatus': 'credit-state'}})
        self.assertEqual(event, {'type': 'rate_limit_event', 'uuid': 'fixture-id-1'})

    def test_import_truncated_final_line_records_omission(self):
        for tail in (b'{"type":"assistant"', b'{"type":"assistant","text":"\xe2\x82'):
            for existing in (None, {'process_status': 'timeout', 'completion': 'invalid'}):
                with self.subTest(tail=tail, existing=existing), tempfile.TemporaryDirectory() as scratch:
                    run = Path(scratch)
                    attempt = run / 'attempt-001'
                    attempt.mkdir()
                    (run / 'versions.json').write_text('{"claude":"2.1.330 (Claude Code)"}')
                    (attempt / 'stdout.log').write_bytes(b'{"type":"system","subtype":"init"}\n' + tail)
                    if existing is not None:
                        (attempt / 'claude-stream.json').write_text(json.dumps(existing))
                    self.write.reset_mock()
                    self.recorder.record_attempt(argparse.Namespace(
                        cli='claude', attempt=attempt, name='truncated'))
                    written = {call.args[1]: call.args[2] for call in self.write.call_args_list}
                    self.assertEqual(json.loads(written['events-truncated.jsonl']),
                                     {'type': 'system', 'subtype': 'init'})
                    self.assertEqual(json.loads(written['metadata-truncated.json']),
                                     {**(existing or {}),
                                      'fixture_import': {'truncated_final_line': True}})

    def test_import_rejects_malformed_complete_or_interior_lines(self):
        for raw in ('{"type":\n', '{"type":\n{"type":"system"}\n'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / 'stdout.log'
                path.write_text(raw)
                with self.assertRaises(json.JSONDecodeError):
                    self.recorder.jsonl(path, self.recorder.claude_events())


class SanitizerTests(unittest.TestCase):
    """Identifiers are replaced by key, not only by wording (plan §6 step 5)."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
        self.recorder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recorder)
        # Nothing here may depend on who runs the tests or where.
        for patcher in (patch.object(self.recorder.getpass, "getuser", return_value="\0none"),
                        patch.object(self.recorder.Path, "home", return_value=Path("/home/user")),
                        patch.dict(os.environ, {"DUO_FIXTURE_ACCOUNT": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_copilot_sanitizer_replaces_identifiers_and_keeps_the_answer(self):
        convert = self.recorder.copilot_events()
        answer = json.dumps({"decision": "approve", "plan": "x" * 500})
        request = convert({"type": "assistant.message", "id": "00000000-0000-0000-0000-000000000000",
                           "data": {"content": "", "phase": "commentary", "toolRequests": [
                               {"toolCallId": "call_private1", "name": "view"}]}})
        result = convert({"type": "tool.execution_complete",
                          "data": {"toolCallId": "call_private1", "success": True}})
        final = convert({"type": "assistant.message", "data": {
            "content": answer, "toolRequests": [], "messageId": "private-message",
            "previousResponseId": "opaque/base64+value"}})
        usage = convert({"type": "session.shutdown", "data": {"modelMetrics": {"gpt-5.4": {
            "model_call_id": "msg_private", "request_id": "CF0A:19D2:10CD:1650",
            "modelId": "gpt-5.4", "provider": {"id": "copilot", "label": "GitHub Copilot"}}}}})
        call = request["data"]["toolRequests"][0]["toolCallId"]
        self.assertRegex(call, r"^fixture-id-\d+$")
        self.assertEqual(result["data"]["toolCallId"], call)
        self.assertEqual(request["id"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(final["data"]["content"], answer)
        metrics = usage["data"]["modelMetrics"]["gpt-5.4"]
        self.assertEqual(metrics["modelId"], "gpt-5.4")
        self.assertEqual(metrics["provider"]["label"], "GitHub Copilot")
        text = json.dumps([request, result, final, usage])
        for private in ("call_private1", "private-message", "opaque/base64", "msg_private",
                        "CF0A", '"copilot"'):
            self.assertNotIn(private, text)

    def test_text_scrub_replaces_request_identifiers(self):
        cases = {"Error: Authentication failed (Request ID: 9A1B:22C3:FF)":
                     "Error: Authentication failed (Request ID: 0000:0000)",
                 'failed: {"request_id": "req_011CabcdefGHIJKLMNOPQR"}':
                     'failed: {"request_id": "0000:0000"}',
                 "x-request-id=abc123 retrying": "x-request-id=0000:0000 retrying",
                 "upstream CF0A:19D245:10CD91F:1650669:6ACC474C failed":
                     "upstream 0000:0000 failed",
                 "message msg_011CfwZbBr7GBEEkF2dirxZB, call call_uF2soK6o6Cy1FlpAnZs6pWWr":
                     "message msg_fixture, call call_fixture",
                 # Short words and times are not identifiers.
                 "call_finished at 02:34:52 (msg_lifecycle)": "call_finished at 02:34:52 (msg_lifecycle)"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.recorder.scrub(raw), expected)

    def test_committed_fixtures_are_fixed_points_of_the_recorder(self):
        """Re-sanitizing changes nothing, so every fixture meets today's recorder."""
        recorder = self.recorder
        for path in sorted(recorder.FIXTURES.glob("*-*/*")):
            cli = path.parent.name.split("-")[0]
            raw = path.read_text()
            if path.suffix == ".jsonl":
                convert = {"copilot": recorder.copilot_events,
                           "claude": recorder.claude_events}.get(cli)
                again = recorder.jsonl(path, convert() if convert else recorder.trim)
            elif path.name.startswith(("response-", "metadata-")):
                again = json.dumps(recorder.trim(json.loads(raw), keep=True), indent=2,
                                   ensure_ascii=False) + "\n"
            elif path.name.startswith("rejection-"):
                record = json.loads(raw)
                again = json.dumps({**record, "stdout": recorder.scrub(record["stdout"]),
                                    "stderr": recorder.scrub(record["stderr"])}, indent=2) + "\n"
            elif path.suffix == ".txt":
                again = recorder.scrub(raw)
            else:
                continue  # config.json and mcp-list.json are reviewed probe output
            with self.subTest(path=path.relative_to(recorder.FIXTURES)):
                self.assertEqual(again, raw)


class RejectionRecordingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
        self.recorder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recorder)
        patcher = patch.dict(os.environ, {**RUNTIME, **AUTH["codex"], **AUTH["claude"],
                                          **DROPPED}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(self.recorder, "write")
        self.write = patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []
        self.code = 2
        patcher = patch.object(self.recorder.subprocess, "run", side_effect=self.capture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, argv, *, env, **kwargs):
        self.assertIsInstance(env, dict)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        cli = argv[0]
        self.calls.append((list(argv), set(env)))
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=VERSIONS[cli], stderr="")
        return subprocess.CompletedProcess(
            argv, self.code, stdout="", stderr=f"error: unknown option at /home/user/x {cli}\n")

    def args(self, *clis):
        return argparse.Namespace(cli=list(clis), codex_bin="codex", claude_bin="claude",
                                  copilot_bin="copilot")

    def test_rejection_is_recorded_without_a_prompt(self):
        option = self.recorder.UNKNOWN_OPTION
        expected = {"codex": ["codex", "exec", option], "claude": ["claude", "-p", option],
                    "copilot": ["copilot", "--no-auto-update", option]}
        for cli in expected:
            with self.subTest(cli=cli):
                self.calls.clear()
                self.write.reset_mock()
                self.recorder.record_rejections(self.args(cli))
                argv, names = self.calls[-1]
                self.assertEqual(argv, expected[cli])
                self.assertEqual(self.calls[0][1], names)
                if cli != "copilot":
                    self.assertEqual(names, set(reviewer_environment(cli)))
                (target, name, text), = [call.args for call in self.write.call_args_list]
                self.assertEqual((target.name, name), (f"{cli}-1.2.3", "rejection-unknown-option.json"))
                self.assertEqual(json.loads(text), {"argv": expected[cli], "exit_code": 2,
                                                    "stdout": "",
                                                    "stderr": f"error: unknown option at /home/user/x {cli}\n"})

    def test_an_accepted_option_records_nothing(self):
        self.code = 0
        with self.assertRaisesRegex(SystemExit, "accepted"):
            self.recorder.record_rejections(self.args("codex"))
        self.write.assert_not_called()

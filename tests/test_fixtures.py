"""Replay sanitized real-CLI output (tests/fixtures) through Duo's parsers.

The other suites use hand-written fake CLIs; these tests keep them honest by
running the same code paths on recorded output. Refresh fixtures with
scripts/record_fixtures.py whenever a tested CLI version is added.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from test_duo import Fixture
from duo import copilot
from duo.adapters import (CLIAdapter, claude_error_text, claude_failure, classify_failure,
                          codex_error_text, copilot_reply, read_json)
from duo.models import SECRET_PATTERNS, AgentError, validate_review
from duo.policy import TESTED_VERSIONS, verify_claude_remote, verify_controls, verify_version

FIXTURES = Path(__file__).parent / "fixtures"
# Recorded CLI output; verdicts/ holds hand-written verdict examples.
RECORDINGS = sorted(path for path in FIXTURES.glob("*-*/*") if path.is_file())
# Independent of record_fixtures.py, so a sanitizer regression cannot hide here.
# Keys ending in Id/_id hold identifiers wherever they appear, except these
# descriptive ones; their values must be placeholders the recorder writes.
DESCRIPTIVE_KEYS = {"modelId", "providerId", "turnId"}
PLACEHOLDER = re.compile(r"[0:-]+|fixture-id-\d+|item_\d+|F\d+")
# Identifier shapes in free text: GitHub request IDs, Anthropic and OpenAI
# message/request/call IDs, and any "request id" value other than a placeholder.
TEXT_IDENTIFIERS = {
    "GitHub request ID": re.compile(r"\b[0-9A-F]{4,8}(?::[0-9A-F]{4,16}){3,5}\b"),
    "provider ID": re.compile(r"\b(?:msg|req|toolu|srvtoolu|resp|call|chatcmpl)[_-][A-Za-z0-9]{16,}"),
    "request ID text": re.compile(r"(?i)request[ _-]?id[\"']?\s*[:=]\s*[\"']?(?![0:-]+\b|fixture-id-)\w"),
}


def fixture_dir(agent: str) -> Path:
    """The recording for the first tested version of each CLI."""
    return FIXTURES / f"{agent}-{'.'.join(map(str, TESTED_VERSIONS[agent][0]))}"


def modern_response(value):
    """Old captures predate findings; upgrade only replay copies to today's envelope."""
    if isinstance(value, dict):
        result = {key: modern_response(item) for key, item in value.items()}
        if set(result) == {"decision", "plan", "reason"}:
            result.update(findings=[], finding_updates=[])
        return result
    if isinstance(value, list):
        return [modern_response(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(modern_response(parsed), ensure_ascii=False)
    return value


def modern_capture(path):
    raw = Path(path).read_text()
    try:
        return json.dumps(modern_response(json.loads(raw)), ensure_ascii=False)
    except ValueError:
        return "\n".join(json.dumps(modern_response(json.loads(line)), ensure_ascii=False)
                         for line in raw.splitlines()) + "\n"


def text(agent: str, name: str) -> str:
    return (fixture_dir(agent) / name).read_text()


class RecordedProbeTests(unittest.TestCase):
    def test_every_tested_version_has_fixtures(self):
        for agent in TESTED_VERSIONS:
            with self.subTest(agent=agent):
                self.assertEqual(verify_version(agent, text(agent, "version.txt")), "tested")

    def test_native_controls_verify_against_recorded_probes(self):
        recorded = {"exec": text("codex", "exec-help.txt"),
                    "features": text("codex", "features.txt"),
                    "mcp": text("codex", "mcp-list.json"),
                    "doctor": text("claude", "doctor.txt")}

        def probe(argv, env=None):
            if argv[0] == "codex":
                return recorded[next(key for key in ("exec", "features", "mcp") if key in argv)]
            return recorded["doctor"] if "doctor" in argv else text("claude", "help.txt")

        with patch("duo.policy.check_claude_managed_files"):
            policy = verify_controls("codex", "claude", Path("/unused/settings.json"), probe)
        self.assertEqual(policy["codex"]["verified_mcp_servers"], [])
        self.assertEqual(policy["claude"]["managed_settings"],
                         verify_claude_remote(recorded["doctor"]))

    def test_codex_controls_for_each_recorded_minor_version(self):
        for directory in sorted(FIXTURES.glob("codex-*")):
            def probe(argv, env=None):
                name = ("exec-help.txt" if "exec" in argv else
                        "features.txt" if "features" in argv else "mcp-list.json")
                return (directory / name).read_text()

            with self.subTest(version=directory.name):
                policy = verify_controls("codex", "claude", Path("/unused/settings.json"),
                                         probe, agents={"codex"})
                self.assertEqual(policy["codex"]["verified_mcp_servers"], [])

    def test_copilot_help_and_config_match_required_controls(self):
        # 1.0.103 replaced --no-remote and --no-remote-export with --sync-session.
        expected = {"copilot-1.0.95": ["--no-remote", "--no-remote-export"],
                    "copilot-1.0.105": ["--sync-session=off"],
                    "copilot-1.0.116": ["--sync-session=off"]}
        directories = sorted(FIXTURES.glob("copilot-*"))
        self.assertEqual({d.name for d in directories}, set(expected))
        for directory in directories:
            with self.subTest(version=directory.name):
                help_text = (directory / "help.txt").read_text()
                available = copilot.options(help_text)
                for flag in (*copilot.FLAGS, "--reasoning-effort", "--available-tools", "--deny-tool"):
                    self.assertIn(flag, available)
                self.assertEqual(copilot.session_flags(help_text), expected[directory.name])
                config = json.loads((directory / "config.json").read_text())
                self.assertTrue(all(config.get(k) == v for k, v in copilot.SETTINGS.items()))

    def test_fixtures_contain_no_credentials_or_home_paths(self):
        for path in FIXTURES.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_text()
            with self.subTest(path=path.relative_to(FIXTURES)):
                for kind, pattern in SECRET_PATTERNS.items():
                    self.assertIsNone(pattern.search(content), kind)
                # Uppercase names such as /Users/AGENTS.md are probed paths, not homes.
                self.assertIsNone(re.search(r"/(?:Users|home)/(?!user\b)[a-z]", content))
                self.assertNotIn('"rate_limit_info"', content)

    def test_recorded_identifiers_are_placeholders(self):
        """Request, message, call and session IDs never reach a committed fixture."""
        def identifiers(value, key=""):
            if isinstance(value, dict):
                for name, item in value.items():
                    yield from identifiers(item, name)
            elif isinstance(value, list):
                for item in value:
                    yield from identifiers(item, key)
            elif (isinstance(value, str) and key not in DESCRIPTIVE_KEYS
                  and (key in ("id", "uuid") or key.endswith(("_id", "Id", "ID")))):
                yield key, value

        for path in RECORDINGS:
            content = path.read_text()
            with self.subTest(path=path.relative_to(FIXTURES)):
                for kind, pattern in TEXT_IDENTIFIERS.items():
                    self.assertIsNone(pattern.search(content), kind)
                if path.suffix not in (".json", ".jsonl"):
                    continue
                documents = ([json.loads(line) for line in content.splitlines() if line.strip()]
                             if path.suffix == ".jsonl" else [json.loads(content)])
                for document in documents:
                    for key, value in identifiers(document):
                        self.assertRegex(value, f"^(?:{PLACEHOLDER.pattern})$", key)

    def test_identifier_check_rejects_the_formats_it_guards(self):
        # The values the check exists for; each must be caught as recorded.
        for text in ('"request_id": "CF0A:19D245:10CD91F:1650669:6ACC474C"',
                     '"model_call_id": "msg_011CfwZbBr7GBEEkF2dirxZB"',
                     '"toolCallId": "call_uF2soK6o6Cy1FlpAnZs6pWWr"',
                     "Error: Authentication failed (Request ID: 9A1B:22C3)"):
            with self.subTest(text=text):
                self.assertTrue(any(p.search(text) for p in TEXT_IDENTIFIERS.values()))
        self.assertIsNone(PLACEHOLDER.fullmatch("7qDLHZBtK/cWZ9b81KxICWql"))
        self.assertIsNone(TEXT_IDENTIFIERS["request ID text"].search("(Request ID: 0000:0000)"))


class RecordedResponseTests(Fixture):
    """Replay recorded stdout/stderr through the real adapter."""

    def replay(self, name, stdout="", stderr="", code=0, response=None):
        for field, source in (("stdout", stdout), ("response", response)):
            if source and Path(source).suffix in (".json", ".jsonl"):
                target = self.root / (field + "-modern.json")
                target.write_text(modern_capture(source))
                if field == "stdout":
                    stdout = str(target)
                else:
                    response = str(target)
        body = f"""import pathlib, sys
sys.stdin.read()
if {response!r} and '-o' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(pathlib.Path({response!r}).read_text())
if {stdout!r}:
    sys.stdout.write(pathlib.Path({stdout!r}).read_text())
if {stderr!r}:
    sys.stderr.write(pathlib.Path({stderr!r}).read_text())
sys.exit({code})
"""
        return self.executable(name, body)

    def path(self, agent, name):
        return str(fixture_dir(agent) / name)

    def test_codex_event_stream_and_response(self):
        for response in sorted(FIXTURES.glob("codex-*/response-approve.json")):
            events = response.with_name("events-approve.jsonl")
            with self.subTest(version=response.parent.name):
                self.assertEqual(codex_error_text(events), "")
                binary = self.replay("codex", stdout=str(events), response=str(response))
                review = CLIAdapter(replace(self.config, codex_bin=binary), self.store)("codex", "Review", 5)
                self.assertEqual(review["decision"], "approve")
                validate_review(review, "# Plan\n")

    def test_claude_success_envelope(self):
        # Historical --output-format json evidence, not a stream recording.
        envelope = read_json(Path(self.path("claude", "result-approve.json")))
        review = validate_review(modern_response(envelope['structured_output']), '# Plan\n')
        self.assertEqual(review["decision"], "approve")

    def test_claude_unrecognized_model(self):
        # Claude Code reports this with subtype "success" and is_error true.
        envelope = read_json(Path(self.path("claude", "result-unrecognized-model.json")))
        error = classify_failure(claude_error_text(envelope), 'opus-5.5')
        self.assertFalse(error.retryable)
        self.assertIn('opus-5.5', str(error))
        # The structured status alone gives the same answer (plan §6 step 4).
        self.assertEqual(claude_failure(envelope),
                         ('model', 'Requested model is unavailable in the selected CLI'))
        structured = classify_failure('', 'opus-5.5', structured=claude_failure(envelope))
        self.assertEqual((structured.category, str(structured)), ('model', str(error)))
        # The recorded max-turns stream ends with its structured subtype.
        events = [json.loads(line) for line in
                  (FIXTURES / "claude-2.1.330" / "events-maxturns.jsonl").read_text().splitlines()]
        self.assertEqual(claude_failure(events[-1])[0], 'limit')

    def test_copilot_event_streams(self):
        # GPT models mark the answer phase "final_answer"; Claude models omit phase.
        for name, decision in (("events-gpt-revise.jsonl", "revise"),
                               ("events-claude-approve.jsonl", "approve")):
            with self.subTest(name=name):
                capture = self.root / name
                capture.write_text(modern_capture(self.path("copilot", name)))
                events = [json.loads(line) for line in capture.read_text().splitlines()]
                answers = [e["data"] for e in events if e.get("type") == "assistant.message"
                           and e["data"].get("toolRequests") == []]
                self.assertEqual(len(answers), 1)
                # Claude models omit phase; GPT models mark the answer.
                self.assertEqual(answers[0].get("phase"),
                                 "final_answer" if decision == "revise" else None)
                review = copilot_reply(capture)
                self.assertEqual(review, json.loads(answers[0]["content"]))
                self.assertEqual(review["decision"], decision)
                validate_review(review, "" if decision == "revise" else "# Plan\n")

    def test_copilot_errors(self):
        cases = (("stderr-model-unavailable.txt", "gpt-5-4", "unavailable in the selected CLI: gpt-5-4"),
                 ("stderr-auth-failed.txt", "gpt-5.4", "Authentication failed"))
        for name, model, message in cases:
            binary = self.replay(f"copilot-{name}", stderr=self.path("copilot", name), code=1)
            config = replace(self.config, codex_backend="copilot", copilot_bin=binary, codex_model=model)
            adapter = CLIAdapter(config, self.store)
            adapter.policy_verified = True
            with self.subTest(name=name), patch("duo.copilot.check_sources"), \
                    patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "duo-test-token"}), \
                    self.assertRaises(AgentError) as caught:
                adapter("codex", "Review", 5)
            self.assertFalse(caught.exception.retryable)
            self.assertIn(message, str(caught.exception))

    def copilot_adapter(self, binary, model="gpt-5.4"):
        config = replace(self.config, codex_backend="copilot", copilot_bin=binary, codex_model=model)
        adapter = CLIAdapter(config, self.store)
        adapter.policy_verified = True
        return adapter

    def failure(self, agent, stderr, code, *, model="gpt-5.4"):
        """The AgentError the adapter raises for one recorded or mutated failure."""
        target = self.root / f"stderr-{agent}-{len(list(self.root.glob('stderr-*')))}.txt"
        target.write_text(stderr)
        binary = self.replay(f"{agent}-failure", stderr=str(target), code=code)
        if agent == "copilot":
            adapter, role = self.copilot_adapter(binary, model), "codex"
        else:
            adapter = CLIAdapter(replace(self.config, **{f"{agent}_bin": binary}), self.store)
            role = agent
        with patch("duo.copilot.check_sources"), \
                patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "duo-test-token"}), \
                self.assertRaises(AgentError) as caught:
            adapter(role, "Review", 5)
        return caught.exception

    def test_recorded_unknown_option_rejections_are_not_retried(self):
        recordings = sorted(FIXTURES.glob("*/rejection-unknown-option.json"))
        self.assertEqual({path.parent.name.split("-")[0] for path in recordings},
                         {"codex", "claude", "copilot"})
        for path in recordings:
            agent = path.parent.name.split("-")[0]
            record = json.loads(path.read_text())
            with self.subTest(fixture=path.parent.name):
                # Each parser refused the option without output on stdout.
                self.assertNotEqual(record["exit_code"], 0)
                self.assertEqual(record["stdout"], "")
                error = self.failure(agent, record["stderr"], record["exit_code"])
                self.assertEqual(error.category, "option")
                self.assertEqual(error.attempt_limit, 1)
                self.assertFalse(error.retryable)

    def test_recorded_failures_with_shifted_or_unknown_wording(self):
        """Wording decides only where no structured field does (plan §6 step 5)."""
        model = self.path("copilot", "stderr-model-unavailable.txt")
        auth = self.path("copilot", "stderr-auth-failed.txt")
        claude_model = str(FIXTURES / "claude-2.1.296" / "stderr-unrecognized-model.txt")
        codex = json.loads((FIXTURES / "codex-0.172.0" / "rejection-unknown-option.json").read_text())
        claude = json.loads((FIXTURES / "claude-2.1.332" / "rejection-unknown-option.json").read_text())
        recorded = {"copilot model": ("copilot", Path(model).read_text(), 1, "model"),
                    "copilot auth": ("copilot", Path(auth).read_text(), 1, "authentication"),
                    "claude model": ("claude", Path(claude_model).read_text(), 1, "model"),
                    "claude option": ("claude", claude["stderr"], claude["exit_code"], "option")}
        shifts = {"warning first": lambda text: "Warning: update available\n" + text,
                  "upper case": str.upper,
                  "trailing detail": lambda text: text.rstrip() + " (code 7)\n"}
        for name, (agent, text, code, category) in recorded.items():
            for shift, mutate in shifts.items():
                with self.subTest(recording=name, shift=shift):
                    self.assertEqual(self.failure(agent, mutate(text), code).category, category)
        # Unknown wording falls back to a retried transient failure: the residual
        # risk, bounded by --max-attempts, that the inventory records for site 6.
        reworded = {"copilot model": ("copilot", 'Error: "gpt-5-4" cannot be used here.\n'),
                    "copilot auth": ("copilot", "Error: token rejected (sign in again)\n"),
                    "claude option": ("claude", "error: --duo-fixture-unknown-option is not recognised\n")}
        for name, (agent, text) in reworded.items():
            with self.subTest(recording=name, shift="reworded"):
                error = self.failure(agent, text, 1)
                self.assertEqual((error.category, error.attempt_limit), ("transient", None))
                self.assertEqual(str(error), "Agent CLI failed; see the attempt diagnostics")
        # Structured fields decide whatever the wording: Codex's usage-error exit
        # with no events, and Claude's recorded HTTP status.
        error = self.failure("codex", "error: that flag means nothing to me\n", codex["exit_code"])
        self.assertEqual(error.category, "option")
        self.assertEqual(self.failure("codex", codex["stderr"], 1).category, "option")
        envelope = read_json(Path(self.path("claude", "result-unrecognized-model.json")))
        self.assertEqual(claude_failure({**envelope, "result": "Something else went wrong"})[0],
                         "model")

    def test_claude_recorded_streams(self):
        directory = FIXTURES / 'claude-2.1.330'
        for name in ('noweb', 'web', 'webtools'):
            path = directory / f'events-{name}.jsonl'
            raw = modern_capture(path)
            binary = self.replay('claude-stream', stdout=str(path))
            adapter = CLIAdapter(replace(self.config, claude_bin=binary,
                                         allow_web=name != 'noweb'), self.store)
            review = adapter('claude', 'Review', 5)
            self.assertEqual(review['decision'], 'approve')
            attempt = max(self.store.path.glob('attempt-*'), key=lambda p: p.stat().st_mtime_ns)
            self.assertEqual((attempt / 'stdout.log').read_text(), raw)
            metadata = json.loads((attempt / 'claude-stream.json').read_text())
            self.assertTrue(metadata['payload_validated'])
            self.assertTrue(metadata['usage_available'])

    def test_claude_recorded_turn_limit_failure(self):
        path = FIXTURES / 'claude-2.1.330/events-maxturns.jsonl'
        for code in (0, 1):
            binary = self.replay('claude-failed-stream', stdout=str(path), code=code)
            adapter = CLIAdapter(replace(self.config, claude_bin=binary), self.store)
            with self.assertRaisesRegex(AgentError, 'internal turn limit') as caught:
                adapter('claude', 'Review', 5)
            self.assertFalse(caught.exception.retryable)
            attempt = max(self.store.path.glob('attempt-*'), key=lambda p: p.stat().st_mtime_ns)
            self.assertEqual((attempt / 'stdout.log').read_bytes(), modern_capture(path).encode())
            self.assertEqual(json.loads((attempt / 'claude-stream.json').read_text())['completion'],
                             'unsuccessful')

    def test_claude_recorded_timeout_streams_cannot_be_reviews(self):
        directory = FIXTURES / 'claude-2.1.330'
        for name in ('timeout-noweb', 'timeout-web'):
            path = directory / f'events-{name}.jsonl'
            metadata = json.loads((directory / f'metadata-{name}.json').read_text())
            self.assertEqual(metadata['process_status'], 'timeout')
            self.assertFalse(metadata['usage_available'])
            binary = self.replay('claude-partial-stream', stdout=str(path))
            adapter = CLIAdapter(replace(self.config, claude_bin=binary), self.store)
            # Even an artificial zero exit cannot turn this partial live log into a review.
            with self.assertRaisesRegex(AgentError, 'exactly one terminal result'):
                adapter('claude', 'Review', 5)

    def test_claude_recorded_requests_results_denials_and_web_counters(self):
        directory = FIXTURES / 'claude-2.1.330'
        for name, expected in (('noweb', {'Read', 'Glob', 'Grep'}),
                               ('web', {'WebSearch', 'WebFetch'}),
                               ('webtools', {'Read', 'Glob', 'Grep', 'WebSearch'})):
            events = [json.loads(line) for line in (directory / f'events-{name}.jsonl').read_text().splitlines()]
            requests, results = {}, {}
            denied = {e['tool_use_id'] for e in events if e.get('subtype') == 'permission_denied'}
            for event in events:
                for block in (event.get('message', {}).get('content', [])
                              if event.get('type') in ('assistant', 'user') else []):
                    if block['type'] == 'tool_use':
                        requests[block['id']] = block
                    elif block['type'] == 'tool_result':
                        results[block['tool_use_id']] = block
            successes = {request['name'] for key, request in requests.items()
                         if key in results and not results[key].get('is_error')}
            self.assertTrue(expected <= successes)
            self.assertTrue(all(request['input'] for request in requests.values()))
            self.assertTrue(set(results) <= set(requests))
            if name == 'noweb':
                self.assertEqual(len(denied), 1)
                errors = {key for key, result in results.items() if result.get('is_error')}
                self.assertTrue(denied < errors)  # missing file is an ordinary error, not a denial
                self.assertEqual(denied, {d['tool_use_id'] for d in events[-1]['permission_denials']})
                self.assertNotIn('WebSearch', events[0]['tools'])
            else:
                self.assertTrue({'Read', 'Glob', 'Grep', 'WebSearch', 'WebFetch'}
                                <= set(events[0]['tools']))
                self.assertEqual(events[-1]['usage']['server_tool_use'],
                                 {'web_search_requests': 0, 'web_fetch_requests': 0})
                self.assertFalse(denied)

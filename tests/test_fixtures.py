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
from duo.adapters import CLIAdapter, codex_error_text, copilot_reply
from duo.models import SECRET_PATTERNS, AgentError, validate_review
from duo.policy import TESTED_VERSIONS, verify_claude_remote, verify_controls, verify_version

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_dir(agent: str) -> Path:
    """The recording for the first tested version of each CLI."""
    return FIXTURES / f"{agent}-{'.'.join(map(str, TESTED_VERSIONS[agent][0]))}"


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

        def probe(argv):
            if argv[0] == "codex":
                return recorded[next(key for key in ("exec", "features", "mcp") if key in argv)]
            return recorded["doctor"] if "doctor" in argv else text("claude", "help.txt")

        with patch("duo.policy.check_claude_managed_files"):
            policy = verify_controls("codex", "claude", Path("/unused/settings.json"), probe)
        self.assertEqual(policy["codex"]["verified_mcp_servers"], [])
        self.assertEqual(policy["claude"]["managed_settings"],
                         verify_claude_remote(recorded["doctor"]))

    def test_copilot_help_and_config_match_required_controls(self):
        help_text = text("copilot", "help.txt")
        for flag in (*copilot.FLAGS, "--reasoning-effort", "--available-tools", "--deny-tool"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)
        config = json.loads(text("copilot", "config.json"))
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


class RecordedResponseTests(Fixture):
    """Replay recorded stdout/stderr through the real adapter."""

    def replay(self, name, stdout="", stderr="", code=0, response=None):
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
        events = self.path("codex", "events-approve.jsonl")
        self.assertEqual(codex_error_text(Path(events)), "")
        binary = self.replay("codex", stdout=events, response=self.path("codex", "response-approve.json"))
        review = CLIAdapter(replace(self.config, codex_bin=binary), self.store)("codex", "Review", 5)
        self.assertEqual(review["decision"], "approve")
        validate_review(review, "# Plan\n")

    def test_claude_success_envelope(self):
        binary = self.replay("claude", stdout=self.path("claude", "result-approve.json"))
        review = CLIAdapter(replace(self.config, claude_bin=binary), self.store)("claude", "Review", 5)
        self.assertEqual(review["decision"], "approve")

    def test_claude_unrecognized_model(self):
        # Claude Code reports this with subtype "success" and is_error true.
        for code in (0, 1):
            binary = self.replay(f"claude-{code}", stdout=self.path("claude", "result-unrecognized-model.json"),
                                 stderr=self.path("claude", "stderr-unrecognized-model.txt"), code=code)
            adapter = CLIAdapter(replace(self.config, claude_bin=binary, claude_model="opus-5.5"), self.store)
            with self.subTest(code=code), self.assertRaises(AgentError) as caught:
                adapter("claude", "Review", 5)
            self.assertFalse(caught.exception.retryable)
            self.assertIn("opus-5.5", str(caught.exception))

    def test_copilot_event_streams(self):
        # GPT models mark the answer phase "final_answer"; Claude models omit phase.
        for name, decision in (("events-gpt-revise.jsonl", "revise"),
                               ("events-claude-approve.jsonl", "approve")):
            with self.subTest(name=name):
                review = copilot_reply(Path(self.path("copilot", name)))
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

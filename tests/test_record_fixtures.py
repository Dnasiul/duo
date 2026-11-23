"""Fixture probes use production environments; captures contain names only."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
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
        return subprocess.CompletedProcess(argv, 0, stdout="synthetic-cli 1.2.3\n", stderr="")

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

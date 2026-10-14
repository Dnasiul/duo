"""Reviewer CLIs receive only the environment they need."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from test_duo import Fixture, response
from duo import copilot
from duo.adapters import CLIAdapter
from duo.cli import main
from duo.models import Config
from duo.policy import reviewer_environment

# Values that must never reach a reviewer unless explicitly passed.
SECRETS = {
    "AWS_SECRET_ACCESS_KEY": "aws-secret", "GH_TOKEN": "gh-secret",
    "SSH_AUTH_SOCK": "/tmp/agent.sock", "NODE_OPTIONS": "--require /tmp/inject.js",
    "OPENAI_BASE_URL": "https://proxy.invalid", "CLAUDE_CODE_MESSAGING_TOKEN": "parent-session",
    "UNRELATED_API_KEY": "other-secret",
}
NEEDED = {"HOME": "/home/user", "PATH": os.environ["PATH"], "LC_ALL": "C.UTF-8",
          "HTTPS_PROXY": "http://proxy:3128", "OPENAI_API_KEY": "openai-key",
          "ANTHROPIC_API_KEY": "anthropic-key"}


class EnvironmentTests(Fixture):
    def dump_env(self, name):
        """A fake CLI whose review call writes the names of its environment variables."""
        marker = self.root / f"{name}.env"
        binary = self.executable(name, f"""import json, os, pathlib, sys
sys.stdin.read()
pathlib.Path({str(marker)!r}).write_text(json.dumps(sorted(os.environ)))
result = {{'decision': 'approve', 'plan': '', 'reason': 'Ready'}}
if '-o' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
else:
    print(json.dumps({{'is_error': False, 'subtype': 'success', 'structured_output': result}}))
""")
        return binary, marker

    def test_allowlist_keeps_what_clis_need_and_drops_the_rest(self):
        with patch.dict(os.environ, {**SECRETS, **NEEDED}, clear=True):
            codex, claude = reviewer_environment("codex"), reviewer_environment("claude")
            passed = reviewer_environment("codex", ("UNRELATED_API_KEY",))
        for env in (codex, claude):
            self.assertTrue({"HOME", "PATH", "LC_ALL", "HTTPS_PROXY"} <= set(env))
            self.assertFalse(set(SECRETS) & set(env))
        self.assertIn("OPENAI_API_KEY", codex)
        self.assertNotIn("ANTHROPIC_API_KEY", codex)
        self.assertIn("ANTHROPIC_API_KEY", claude)
        self.assertNotIn("OPENAI_API_KEY", claude)
        self.assertEqual(passed["UNRELATED_API_KEY"], "other-secret")

    def test_review_calls_and_probes_get_the_restricted_environment(self):
        binary, marker = self.dump_env("agent")
        prober = self.executable("prober", "import json, os\nprint(json.dumps(sorted(os.environ)))\n")
        with patch.dict(os.environ, SECRETS):
            adapter = CLIAdapter(replace(self.config, codex_bin=binary, claude_bin=binary), self.store)
            for agent in ("codex", "claude"):
                with self.subTest(agent=agent):
                    marker.unlink(missing_ok=True)
                    adapter(agent, "Review", 30)
                    names = set(json.loads(marker.read_text()))
                    self.assertFalse(set(SECRETS) & names)
                    self.assertTrue({"HOME", "PATH"} <= names)
            probed = set(json.loads(CLIAdapter(replace(self.config, codex_bin=prober), self.store)
                                    .probe([prober])))
        self.assertFalse(set(SECRETS) & probed)
        policy = json.loads((self.store.path / "reviewer-policy.json").read_text())
        self.assertIn("HOME", policy["environment"]["codex"])
        self.assertNotIn("GH_TOKEN", policy["environment"]["codex"])
        # Names only: no value of any variable appears in the audit.
        audit = "".join(path.read_text(errors="replace")
                        for path in self.store.path.rglob("*") if path.is_file())
        for value in SECRETS.values():
            self.assertNotIn(value, audit)

    def test_copilot_gets_one_token_and_no_other_secrets(self):
        cases = (({"COPILOT_GITHUB_TOKEN": "copilot-token", "GH_TOKEN": "gh-token"}, "COPILOT_GITHUB_TOKEN"),
                 ({"GH_TOKEN": "gh-token", "GITHUB_TOKEN": "github-token"}, "GH_TOKEN"),
                 ({"GITHUB_TOKEN": "github-token"}, "GITHUB_TOKEN"))
        for tokens, chosen in cases:
            others = {key: "" for key in copilot.AUTH_VARIABLES if key not in tokens}
            with self.subTest(chosen=chosen), patch.dict(os.environ, {**SECRETS, **others, **tokens}):
                env = copilot.environment(self.store.attempt_dir())
                self.assertEqual(env[chosen], tokens[chosen])
                self.assertFalse(set(copilot.AUTH_VARIABLES) - {chosen} & set(env))
                self.assertFalse(set(SECRETS) - {"GH_TOKEN"} & set(env))
                self.assertEqual(env["COPILOT_ALLOW_ALL"], "false")

    def test_copilot_keeps_its_github_host(self):
        hosts = {"GH_HOST": "example.ghe.com", "COPILOT_GH_HOST": "copilot.ghe.com"}
        with patch.dict(os.environ, {**SECRETS, **hosts}):
            env = copilot.environment(self.store.attempt_dir())
            codex = reviewer_environment("codex")
        self.assertEqual({key: env[key] for key in hosts}, hosts)
        self.assertFalse(set(hosts) & set(codex))

    def test_pass_env_adds_names_and_is_validated(self):
        binary, marker = self.dump_env("passing")
        with patch.dict(os.environ, SECRETS):
            config = replace(self.config, codex_bin=binary, claude_bin=binary, pass_env=("UNRELATED_API_KEY",))
            CLIAdapter(config, self.store)("codex", "Review", 30)
        names = set(json.loads(marker.read_text()))
        self.assertIn("UNRELATED_API_KEY", names)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", names)
        for bad in ("1BAD", "A=B", "", "COPILOT_ALLOW_ALL", "GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Config(self.repo, "c", "a", pass_env=(bad,))

    def test_cli_records_passed_names_but_not_values(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"UNRELATED_API_KEY": "other-secret"}), \
                patch.object(CLIAdapter, "preflight", return_value={}), \
                patch.object(CLIAdapter, "__call__", side_effect=[response("revise", "# Plan\n"),
                                                                 response("approve"), response("approve")]), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(["--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task",
                         "--codex-model", "c", "--claude-model", "a", "--pass-env", "UNRELATED_API_KEY",
                         "--json"])
        self.assertEqual(code, 0, output.getvalue())
        run = Path(json.loads(output.getvalue())["run_dir"])
        manifest = (run / "manifest.json").read_text()
        self.assertEqual(json.loads(manifest)["config"]["pass_env"], ["UNRELATED_API_KEY"])
        self.assertNotIn("other-secret", manifest)

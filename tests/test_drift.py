"""The drift check classifies real-format probe output correctly."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_drift.py"


def load():
    spec = importlib.util.spec_from_file_location("check_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DriftCheckTests(unittest.TestCase):
    def setUp(self):
        self.drift = load()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target in ("duo.policy.check_claude_managed_files", "duo.copilot.check_sources"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        # check_drift imported this name directly.
        patcher = patch.object(self.drift, "check_claude_managed_files")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, agent, version=None, drop_flag=None, fail=False):
        """A CLI that replays the recorded probes, optionally altered."""
        directory = FIXTURES / {"codex": "codex-0.162.1", "claude": "claude-2.1.296",
                                "copilot": "copilot-1.0.95"}[agent]
        outputs = {path.stem: path.read_text() for path in directory.glob("*.txt")}
        outputs.update({path.stem: path.read_text() for path in directory.glob("*.json")})
        if version:
            outputs["version"] = outputs["version"].replace(
                {"codex": "0.162.1", "claude": "2.1.296", "copilot": "1.0.95"}[agent], version)
        if drop_flag:
            for key in ("help", "exec-help"):
                if key in outputs:
                    outputs[key] = outputs[key].replace(drop_flag, "--renamed-control")
        path = self.root / agent
        path.write_text(f"""#!{sys.executable}
import sys
outputs = {outputs!r}
args = sys.argv[1:]
if {fail!r}:
    sys.exit(2)
if '--version' in args:
    key = 'version'
elif 'exec' in args and '--help' in args:
    key = 'exec-help'
elif '--help' in args:
    key = 'help'
elif 'features' in args:
    key = 'features'
elif 'mcp' in args:
    key = 'mcp-list'
elif 'doctor' in args:
    key = 'doctor'
elif 'config' in args:
    key = 'config'
else:
    sys.exit(3)
sys.stdout.write(outputs[key])
""")
        path.chmod(0o755)
        return str(path)

    def check(self, agent, **changes):
        scratch = self.root / f"scratch-{agent}-{len(list(self.root.iterdir()))}"
        scratch.mkdir()
        return self.drift.check(agent, self.fake(agent, **changes), scratch)

    def test_recorded_versions_are_compatible(self):
        for agent in ("codex", "claude", "copilot"):
            with self.subTest(agent=agent):
                result = self.check(agent)
                self.assertEqual(result["status"], "compatible", result)
                self.assertEqual(result["changes"], {})

    def test_new_release_with_working_controls_is_untested(self):
        for agent, version in (("codex", "0.163.0"), ("claude", "2.2.0"), ("copilot", "1.1.0")):
            with self.subTest(agent=agent):
                result = self.check(agent, version=version)
                self.assertEqual(result["status"], "untested-version", result)

    def test_removed_control_flag_is_a_probe_failure(self):
        for agent, flag in (("codex", "--ignore-user-config"), ("claude", "--strict-mcp-config"),
                            ("copilot", "--available-tools")):
            with self.subTest(agent=agent):
                result = self.check(agent, drop_flag=flag)
                self.assertEqual(result["status"], "probe-failure", result)
                self.assertIn("error", result)

    def test_cli_that_cannot_run_is_a_probe_failure(self):
        for agent in ("codex", "claude", "copilot"):
            with self.subTest(agent=agent):
                self.assertEqual(self.check(agent, fail=True)["status"], "probe-failure")

    def test_flag_changes_are_reported(self):
        self.assertEqual(self.drift.compare({"--a", "--b"}, {"--b", "--c"}),
                         {"added": ["--c"], "removed": ["--a"]})
        self.assertEqual(self.drift.flags("use --json or --output-schema; -x"), {"--json", "--output-schema"})
        results = [{"cli": "codex", "version": "codex-cli 0.163.0", "status": "untested-version",
                    "changes": {"help flags": {"added": ["--new"]}}}]
        self.assertIn("help flags added: --new", self.drift.report(results))

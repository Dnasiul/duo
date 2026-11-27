"""The drift check classifies real-format probe output correctly."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "check_drift.py"
OLDEST = {"codex": "codex-0.162.1", "claude": "claude-2.1.296", "copilot": "copilot-1.0.95"}


def load(name="check_drift"):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
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

    def fake(self, agent, version=None, drop_flag=None, fail=False, edit=None):
        """A CLI that replays the recorded probes, optionally altered."""
        directory = FIXTURES / OLDEST[agent]
        outputs = {path.stem: path.read_text() for path in directory.glob("*.txt")}
        outputs.update({path.stem: path.read_text() for path in directory.glob("*.json")})
        if version:
            outputs["version"] = outputs["version"].replace(
                {"codex": "0.162.1", "claude": "2.1.296", "copilot": "1.0.95"}[agent], version)
        if drop_flag:
            for key in ("help", "exec-help"):
                if key in outputs:
                    outputs[key] = outputs[key].replace(drop_flag, "--renamed-control")
        if edit:
            edit(outputs)
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

    def check(self, agent, manifest=None, **changes):
        scratch = self.root / f"scratch-{agent}-{len(list(self.root.iterdir()))}"
        scratch.mkdir()
        return self.drift.check(agent, self.fake(agent, **changes), scratch, manifest)

    def test_recorded_versions_are_compatible(self):
        for agent in ("codex", "claude", "copilot"):
            with self.subTest(agent=agent):
                result = self.check(agent)
                self.assertEqual(result["status"], "compatible", result)
                self.assertEqual(result["changes"], {})

    def test_new_release_with_working_controls_is_untested(self):
        for agent, version in (("codex", "0.173.0"), ("claude", "2.2.0"), ("copilot", "1.1.0")):
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

    def test_claude_requires_stream_options_and_format_value(self):
        for option in ('--output-format', '--verbose', '--json-schema', 'stream-json'):
            with self.subTest(option=option):
                result = self.check('claude', drop_flag=option)
                self.assertEqual(result['status'], 'probe-failure', result)

    def test_flag_changes_are_reported(self):
        self.assertEqual(self.drift.compare({"--a", "--b"}, {"--b", "--c"}),
                         {"added": ["--c"], "removed": ["--a"]})
        self.assertEqual(self.drift.flags("use --json or --output-schema; -x"), {"--json", "--output-schema"})
        results = [{"cli": "codex", "version": "codex-cli 0.163.0", "status": "untested-version",
                    "changes": {"help flags": {"added": ["--new"]}}}]
        self.assertIn("help flags added: --new", self.drift.report(results))

    # Plan §6 step 2: removals, layouts, feature states, and reviewed baselines.

    def test_removed_option_still_named_in_prose_needs_review(self):
        # --add-dir's definition is gone; only a deprecation note names it.
        def edit(outputs):
            lines = outputs["help"].splitlines(keepends=True)
            at = next(i for i, line in enumerate(lines) if line.startswith("  --add-dir "))
            lines[at] = "                                        (--add-dir was removed)\n"
            outputs["help"] = "".join(lines)
        result = self.check("claude", edit=edit)
        self.assertEqual(result["status"], "needs-review", result)
        self.assertEqual(result["changes"]["help flags"],
                         {"removed, still mentioned in prose": ["--add-dir"]})
        self.assertIn("help flags removed", result["review"])
        # The old mention-based parser saw no change.
        self.assertEqual(self.drift.flags(FIXTURES.joinpath(OLDEST["claude"], "help.txt").read_text())
                         - self.drift.flags(self.read_fake_help("claude", edit)), set())

    def read_fake_help(self, agent, edit):
        outputs = {"help": (FIXTURES / OLDEST[agent] / "help.txt").read_text()}
        edit(outputs)
        return outputs["help"]

    def test_definitions_include_aliases_and_camel_case(self):
        text = ("Options:\n  -c, --config <key=value>\n          Uses --profile in prose\n"
                "  --allowedTools, --allowed-tools <tools...>  Allowed tools\n      --fleet\n")
        self.assertEqual(self.drift.defined_flags(text),
                         {"--config", "--allowedTools", "--allowed-tools", "--fleet"})
        self.assertEqual(self.drift.help_layout(text), "recognized")
        self.assertEqual(self.drift.help_layout("usage: tool [--x]\n  --x  an option\n"), "unknown")

    def test_removed_option_in_range_needs_review(self):
        # --betas is named only by its own definition, so its removal is total.
        result = self.check("claude", edit=lambda o: o.update(
            help=o["help"].replace("  --betas ", "  --gammas ")))
        self.assertEqual(result["status"], "needs-review", result)
        self.assertEqual(result["changes"]["help flags"],
                         {"added": ["--gammas"], "removed": ["--betas"]})
        self.assertEqual(result["review"], ["help flags removed"])

    def test_added_option_alone_is_reported_not_reviewed(self):
        result = self.check("claude", edit=lambda o: o.update(
            help=o["help"].replace("Options:\n", "Options:\n  --brand-new  A new option\n", 1)))
        self.assertEqual(result["status"], "compatible", result)
        self.assertEqual(result["changes"], {"help flags": {"added": ["--brand-new"]}})

    def test_two_column_feature_list_needs_review(self):
        def edit(outputs):
            # One feature row (not a control Duo disables) loses its stage column.
            lines = outputs["features"].splitlines(keepends=True)
            at = next(i for i, line in enumerate(lines)
                      if line.split()[0] not in self.drift.CODEX_DISABLED)
            lines[at] = f"{lines[at].split()[0]}  {lines[at].split()[-1]}\n"
            outputs["features"] = "".join(lines)
        result = self.check("codex", edit=edit)
        self.assertEqual(result["status"], "needs-review", result)
        self.assertTrue(any(r.startswith("unrecognized features list rows (1)") for r in result["review"]),
                        result["review"])
        rows, unknown = self.drift.feature_rows("a  stable  true\nb  false\nc  removed  maybe\n")
        self.assertEqual(rows, {"a": ("stable", "true")})
        self.assertEqual(unknown, ["b  false", "c  removed  maybe"])

    def test_feature_state_changes_need_review(self):
        def flip(outputs):
            lines = outputs["features"].splitlines(keepends=True)
            at = next(i for i, line in enumerate(lines) if line.rstrip().endswith("false")
                      and line.split()[0] not in self.drift.CODEX_DISABLED)
            self.flipped = lines[at].split()[0]
            lines[at] = lines[at].rstrip()[:-len("false")] + "true\n"
            outputs["features"] = "".join(lines)
        result = self.check("codex", edit=flip)
        self.assertEqual(result["status"], "needs-review", result)
        self.assertEqual(result["changes"]["features"], {"enabled": [self.flipped]})
        self.assertIn("features state changed", result["review"])
        diff = self.drift.compare_features("a  stable  false\nb  experimental  true\n",
                                           "a  stable  false\nb  stable  true\nc  stable  true\n")
        self.assertEqual(diff, {"added": ["c"], "added enabled": ["c"],
                                "stage changed": ["b (experimental to stable)"]})

    def test_latest_reviewed_baseline_at_or_below_version_is_used(self):
        entries = self.drift.load_baselines(self.drift.MANIFEST)
        for version, expected in (((0, 162, 1), "codex-0.162.1"), ((0, 169, 5), "codex-0.169.1"),
                                  ((0, 172, 0), "codex-0.172.0"), ((0, 180, 0), "codex-0.172.0")):
            with self.subTest(version=version):
                name, texts, problems = self.drift.baseline(entries, "codex", version)
                self.assertEqual((name, problems), (expected, []))
                self.assertEqual(set(texts), {"exec-help.txt", "features.txt"})
        name, texts, problems = self.drift.baseline(entries, "codex", (0, 100, 0))
        self.assertIsNone(name)
        self.assertEqual(problems, ["no reviewed baseline at or below 0.100.0"])

    def test_committed_manifest_lists_reviewable_fixtures(self):
        entries = self.drift.load_baselines(self.drift.MANIFEST)
        for agent, (low, _) in self.drift.TESTED_VERSIONS.items():
            versions = {e["version"] for e in entries if e["cli"] == agent}
            self.assertIn(".".join(map(str, low)), versions)
        for entry in entries:
            directory = FIXTURES / f"{entry['cli']}-{entry['version']}"
            with self.subTest(baseline=directory.name):
                self.assertEqual(self.drift.review_errors(entry["cli"], directory), [])
                self.assertEqual(sorted(Path(f["path"]).name for f in entry["files"]),
                                 sorted(self.drift.COMPARED[entry["cli"]]))

    def test_review_refuses_fixtures_missing_controls(self):
        root = self.root / "fixtures"
        shutil.copytree(FIXTURES / "codex-0.162.1", root / "codex-0.162.1")
        features = root / "codex-0.162.1" / "features.txt"
        features.write_text(features.read_text().replace("hooks ", "hooks_renamed ", 1))
        manifest = self.root / "manifest.json"
        with self.assertRaisesRegex(self.drift.RunError, "does not report hooks false"):
            self.drift.record_review("codex", "0.162.1", manifest, root)
        self.assertFalse(manifest.exists())

    def test_malformed_manifest_is_a_probe_failure(self):
        manifest = self.root / "bad.json"
        for content in ("[]", '{"schema_version": 2, "baselines": []}', "not json",
                        '{"schema_version": 1, "baselines": [{"cli": "codex"}]}'):
            with self.subTest(content=content):
                manifest.write_text(content)
                result = self.check("claude", manifest=manifest)
                self.assertEqual(result["status"], "probe-failure", result)

    def test_drift_issue_lifecycle(self):
        report = load("drift_report")
        root = self.root / "fixtures"
        shutil.copytree(FIXTURES / "claude-2.1.296", root / "claude-2.1.296")
        manifest = self.root / "manifest.json"
        patcher = patch.object(self.drift, "FIXTURES", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.drift.record_review("claude", "2.1.296", manifest)
        remove_add_dir = lambda o: o.update(help=o["help"].replace(  # noqa: E731
            "  --add-dir <directories...>", "  --other-dir <directories...>"))
        platforms = ["ubuntu-latest", "macos-latest"]

        def decide(open_issue, **changes):
            result = self.check("claude", manifest=manifest, **changes)
            others = [{"cli": c, "status": "compatible", "version": "x", "changes": {}}
                      for c in ("codex", "copilot")]
            rows, attention = report.summarize({p: [result, *others] for p in platforms}, platforms)
            return result, report.action(attention, open_issue)

        # An unreviewed removal in a tested version opens the issue.
        result, action = decide(None, edit=remove_add_dir)
        self.assertEqual((result["status"], action), ("needs-review", "create"))
        # Re-recording the fixture at the same version: live output now matches it,
        # but the unreviewed digest keeps the issue actionable.
        live_help = self.read_fake_help("claude", remove_add_dir)
        (root / "claude-2.1.296" / "help.txt").write_text(live_help)
        result, action = decide("3", edit=remove_add_dir)
        self.assertEqual((result["status"], action), ("needs-review", "comment"))
        self.assertIn("does not match its reviewed digest", "; ".join(result["review"]))
        self.assertEqual(result["changes"], {})
        # After review of the replacement digests, a clean check on every platform closes it.
        self.drift.record_review("claude", "2.1.296", manifest)
        result, action = decide("3", edit=remove_add_dir)
        self.assertEqual((result["status"], action), ("compatible", "close"))
        # A later removal opens it again.
        result, action = decide(None, edit=lambda o: (remove_add_dir(o), o.update(
            help=o["help"].replace("  --agent <agent>", "  --agentless <agent>"))))
        self.assertEqual((result["status"], action), ("needs-review", "create"))

    def test_report_requires_every_platform_cli_and_compatible_result(self):
        report = load("drift_report")
        platforms = ["ubuntu-latest", "macos-latest"]
        ok = [{"cli": c, "status": "compatible", "version": "1", "changes": {}}
              for c in ("codex", "claude", "copilot")]
        self.assertFalse(report.summarize({p: ok for p in platforms}, platforms)[1])
        cases = {
            "missing platform": {"ubuntu-latest": ok},
            "unreadable platform": {"ubuntu-latest": ok, "macos-latest": None},
            "missing CLI": {"ubuntu-latest": ok, "macos-latest": ok[:2]},
            "unexpected platform": {"ubuntu-latest": ok, "macos-latest": ok, "windows-latest": ok},
        }
        for status in ("untested-version", "probe-failure", "needs-review"):
            cases[status] = {"ubuntu-latest": ok, "macos-latest": [{**ok[0], "status": status}, *ok[1:]]}
        for name, results in cases.items():
            with self.subTest(name=name):
                rows, attention = report.summarize(results, platforms)
                self.assertTrue(attention)
                self.assertTrue(all(row.count("|") == 7 for row in rows), rows)
        self.assertEqual([report.action(a, n) for a, n in ((True, None), (True, "3"),
                                                            (False, "3"), (False, None))],
                         ["create", "comment", "close", "none"])

    def test_report_script_writes_body_and_prints_action(self):
        result = self.root / "drift-ubuntu-latest.json"
        result.write_text(json.dumps([{"cli": c, "status": "compatible", "version": "1",
                                        "changes": {}} for c in ("codex", "claude", "copilot")]))
        body = self.root / "body.md"
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / "drift_report.py"), "--platforms",
                              "ubuntu-latest", "macos-latest", "--run-url", "URL", "--open-issue", "3",
                              "--body", str(body), str(result)], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout), (0, "comment\n"), out.stderr)
        self.assertIn("| macos-latest | | | missing results |", body.read_text())

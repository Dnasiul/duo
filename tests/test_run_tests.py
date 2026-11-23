"""The strict runner discovers its own suite and rejects incomplete checks."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"


class RunTestsScriptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "repo"
        self.scripts = self.root / "scripts"
        self.tests = self.root / "tests"
        self.unrelated = Path(temp.name) / "unrelated"
        for path in (self.scripts, self.tests, self.unrelated):
            path.mkdir(parents=True)
        self.runner = self.scripts / "run_tests.py"
        shutil.copyfile(SCRIPT, self.runner)
        # -E -S below ignores ambient Python paths and installed packages.
        # A local stub satisfies only the runner's dependency presence check.
        self.langgraph = self.scripts / "langgraph.py"
        self.langgraph.write_text("", encoding="utf-8")

    def write_test(self, body, name="test_sample.py"):
        (self.tests / name).write_text(
            "import unittest\n"
            "class SampleTests(unittest.TestCase):\n"
            "    def test_sample(self):\n"
            f"        {body}\n", encoding="utf-8")

    def invoke(self, cwd=None):
        cwd = cwd or self.root
        script = self.runner if cwd == self.unrelated else os.path.relpath(self.runner, cwd)
        return subprocess.run(
            [sys.executable, "-E", "-S", "-B", str(script)], cwd=cwd,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)

    def test_invocation_directories_discover_the_same_nonempty_suite(self):
        self.write_test("self.assertTrue(True)")
        self.write_test("self.assertEqual(2 + 2, 4)", "test_second.py")
        # Discovering the caller's directory would run this failing test.
        (self.unrelated / "test_wrong.py").write_text(
            "raise AssertionError('wrong discovery directory')\n", encoding="utf-8")
        for cwd in (self.root, self.tests, self.unrelated):
            with self.subTest(cwd=cwd):
                result = self.invoke(cwd)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("Ran 2 tests", result.stdout)
                self.assertIn("(test_sample.SampleTests", result.stdout)
                self.assertIn("(test_second.SampleTests", result.stdout)
                self.assertIn("\nOK\n", result.stdout)

    def test_empty_suite_fails(self):
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("No tests discovered", result.stdout)

    def test_skipped_test_fails(self):
        self.write_test("self.skipTest('synthetic skip')")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Ran 1 test", result.stdout)
        self.assertIn("1 tests were skipped; every test must run", result.stdout)

    def test_failed_test_fails(self):
        self.write_test("self.fail('synthetic failure')")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("FAILED (failures=1)", result.stdout)

    def test_test_errors_and_discovery_errors_fail(self):
        for source in ("raise RuntimeError('synthetic import error')\n",
                       "import unittest\n"
                       "class SampleTests(unittest.TestCase):\n"
                       "    def test_sample(self):\n"
                       "        raise RuntimeError('synthetic test error')\n"):
            with self.subTest(source=source):
                (self.tests / "test_error.py").write_text(source, encoding="utf-8")
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("FAILED (errors=1)", result.stdout)

    def test_missing_langgraph_fails_before_running_tests(self):
        self.langgraph.unlink()
        self.write_test("self.fail('suite must not run')")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("LangGraph is not installed", result.stdout)
        self.assertNotIn("suite must not run", result.stdout)

    def test_runner_does_not_add_src_to_import_path(self):
        src = self.root / "src"
        src.mkdir()
        (src / "source_only.py").write_text("", encoding="utf-8")
        self.write_test("import importlib.util; "
                        "self.assertIsNone(importlib.util.find_spec('source_only'))")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Ran 1 test", result.stdout)


if __name__ == "__main__":
    unittest.main()

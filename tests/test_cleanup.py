"""duo-clean selects old runs, skips active ones, and never follows links."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from duo.cleanup import main
from duo.storage import RunStore

DAY = 86400


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.runs = self.state / "runs"
        self.runs.mkdir(parents=True)

    def run_dir(self, name, days_old, size=1024, runtime=0):
        path = self.runs / name
        attempt = path / "attempt-1"
        attempt.mkdir(parents=True)
        (attempt / "stdout.log").write_bytes(b"x" * size)
        if runtime:
            pkg = attempt / "copilot-cache" / "pkg"
            pkg.mkdir(parents=True)
            (pkg / "runtime.bin").write_bytes(b"r" * runtime)
        stamp = time.time() - days_old * DAY
        for item in (attempt / "stdout.log", attempt, path):
            os.utime(item, (stamp, stamp))
        return path

    def clean(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--state-dir", str(self.state), *args])
        return code, output.getvalue()

    def test_dry_run_reports_without_deleting(self):
        old = self.run_dir("run-old", 40)
        code, text = self.clean("--older-than", "30")
        self.assertEqual(code, 0)
        self.assertIn("Would delete run-old", text)
        self.assertIn("dry run", text)
        self.assertTrue(old.exists())

    def test_deletes_only_runs_older_than_cutoff(self):
        old, recent = self.run_dir("run-old", 40), self.run_dir("run-new", 1)
        _, text = self.clean("--older-than", "30", "--delete")
        self.assertIn("Deleted run-old", text)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_size_budget_removes_oldest_first(self):
        runs = [self.run_dir(f"run-{n}", days, size=1024 * 1024) for n, days in ((1, 3), (2, 2), (3, 1))]
        self.clean("--max-total-mb", "2.5", "--delete")
        self.assertEqual([run.exists() for run in runs], [False, True, True])

    def test_active_run_is_never_deleted(self):
        store = RunStore(self.state)
        self.addCleanup(store.close)
        stamp = time.time() - 90 * DAY
        os.utime(store.path, (stamp, stamp))
        _, text = self.clean("--older-than", "30", "--delete")
        self.assertIn("in use by a running Duo", text)
        self.assertTrue(store.path.exists())
        store.close()
        self.clean("--older-than", "30", "--delete")
        self.assertFalse(store.path.exists())

    def test_symbolic_links_are_never_followed(self):
        outside = self.root / "project"
        outside.mkdir()
        (outside / "plan.md").write_text("# Keep\n")
        (self.runs / "run-link").symlink_to(outside)
        inner = self.run_dir("run-old", 40)
        (inner / "attempt-1" / "project").symlink_to(outside)
        _, text = self.clean("--older-than", "0", "--delete")
        self.assertIn("Skipped run-link: symbolic link, not followed", text)
        self.assertFalse(inner.exists())
        self.assertTrue((self.runs / "run-link").is_symlink())
        self.assertEqual((outside / "plan.md").read_text(), "# Keep\n")

    def test_copilot_runtime_removed_but_audit_kept(self):
        run = self.run_dir("run-copilot", 1, runtime=2 * 1024 * 1024)
        _, text = self.clean("--copilot-runtime")
        self.assertIn("Would delete Copilot runtime from run-copilot (2.0 MiB)", text)
        self.assertTrue((run / "attempt-1" / "copilot-cache" / "pkg").exists())
        self.clean("--copilot-runtime", "--delete")
        self.assertFalse((run / "attempt-1" / "copilot-cache" / "pkg").exists())
        self.assertTrue((run / "attempt-1" / "stdout.log").exists())

    def test_requires_a_selection_and_handles_missing_state(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                patch("sys.stderr", io.StringIO()):
            main(["--state-dir", str(self.state)])
        code, text = self.clean("--state-dir", str(self.root / "absent"), "--older-than", "1")
        self.assertEqual(code, 0)
        self.assertIn("No runs directory", text)


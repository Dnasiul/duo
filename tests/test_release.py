"""The release script checks the wheel against the commit and fills the notes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release.py"


def load():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseScriptTests(unittest.TestCase):
    def setUp(self):
        self.release = load()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clone = self.root / "clone"
        (self.clone / "src" / "duo").mkdir(parents=True)
        (self.clone / "src" / "duo" / "__init__.py").write_text("VERSION = 1\n")
        (self.clone / "src" / "duo" / "cli.py").write_text("def main(): pass\n")
        for argv in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "c"]):
            subprocess.run(["git", *argv], cwd=self.clone, check=True)
        # An untracked file is not part of the commit, so the wheel must not need it.
        (self.clone / "src" / "duo" / "scratch.py").write_text("x = 1\n")

    def wheel(self, files):
        path = self.root / "duo_plan-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("duo_plan-1.0.dist-info/METADATA", "Name: duo-plan\n")
            for name, text in files.items():
                archive.writestr(name, text)
        return path

    def check(self, files):
        with patch("builtins.print"):
            self.release.wheel_matches(self.wheel(files), self.clone)

    def test_wheel_must_hold_exactly_the_committed_package(self):
        committed = {"duo/__init__.py": "VERSION = 1\n", "duo/cli.py": "def main(): pass\n"}
        self.check(committed)
        cases = {"changed": {**committed, "duo/cli.py": "def main(): return 1\n"},
                 "missing": {"duo/__init__.py": "VERSION = 1\n"},
                 "extra": {**committed, "duo/scratch.py": "x = 1\n"}}
        for case, files in cases.items():
            with self.subTest(case=case), self.assertRaisesRegex(SystemExit, "does not match"):
                self.check(files)

    def test_notes_carry_the_commit_and_checksums(self):
        files = [self.root / "duo_plan-1.0-py3-none-any.whl", self.root / "constraints.txt"]
        sums = {"duo_plan-1.0-py3-none-any.whl": "a" * 64, "constraints.txt": "b" * 64}
        text = self.release.notes("1.0", "c" * 40, files, sums)
        self.assertIn(f"Built from `{'c' * 40}` (tag `v1.0`)", text)
        self.assertIn(f"| `constraints.txt` | `{'b' * 64}` |", text)
        self.assertIn("pip install -c constraints.txt duo_plan-1.0-py3-none-any.whl", text)


if __name__ == "__main__":
    unittest.main()

"""Shell examples pass valid arguments and keep subjects separate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from duo.cli import parser
from duo.prompts import GUIDANCE, instructions
from duo.storage import read_input


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "search_literature.sh"


class SearchLiteratureExampleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "literature notes"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.duo = self.root / "mock duo"
        self.duo.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps(sys.argv[1:]))\n"
            "sys.exit(int(os.environ.get('DUO_EXAMPLE_EXIT', '0')))\n",
            encoding="utf-8",
        )
        self.duo.chmod(0o755)
        self.env = {name: value for name, value in os.environ.items()
                    if name not in {"BACKEND", "CODEX_MODEL", "CLAUDE_MODEL", "DUO_EXAMPLE_EXIT"}}
        self.env["DUO"] = str(self.duo)
        # The host's temporary directory can itself be inside a Git work tree;
        # Git must not move up into its parent, so "not a work tree" stays so.
        self.env["GIT_CEILING_DIRECTORIES"] = str(self.root.parent)

    def invoke(self, subject, *, repo=None, cwd=None, **env):
        result = subprocess.run(
            ["bash", str(SCRIPT), str(self.repo if repo is None else repo), subject],
            cwd=cwd or self.root, env={**self.env, **env},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, int(env.get("DUO_EXAMPLE_EXIT", "0")), result.stderr)
        return parser().parse_args(json.loads(result.stdout))

    def test_distinct_subjects_with_the_same_slug_have_separate_reports(self):
        prefix = "quantum molecular dynamics " * 3
        for first, second in (("C++", "C#"), (prefix + "reviews", prefix + "foundations"),
                              ("量子化学", "分子動力学"), ("+++", "###")):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(self.invoke(first).output, self.invoke(second).output)

    def test_rerunning_a_subject_reuses_its_report(self):
        first = self.invoke("quantum chemistry")
        document = self.repo / first.output
        document.write_text("# Existing literature report\n", encoding="utf-8")
        second = self.invoke("quantum chemistry", cwd=self.repo)
        self.assertEqual(first.output, second.output)
        self.assertEqual(read_input(self.repo / second.output), b"# Existing literature report\n")

    def test_subject_filename_is_independent_of_locale(self):
        locales = subprocess.check_output(["locale", "-a"], text=True).splitlines()
        utf8 = next((name for name in locales
                     if name.lower().replace("-", "").endswith("utf8")), "C.UTF-8")
        for subject in ("café chemistry", "Straße", "μ spectroscopy"):
            with self.subTest(subject=subject):
                self.assertEqual(self.invoke(subject, LC_ALL="C").output,
                                 self.invoke(subject, LC_ALL=utf8).output)

    def test_non_work_tree_targets_fail_before_creating_anything(self):
        bare = self.root / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        for target in (bare, self.repo / ".git", self.root):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["bash", str(SCRIPT), str(target), "quantum chemistry"],
                    cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("Git working tree", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse((target / "duo-literature").exists())

    def test_subdirectory_of_a_work_tree_is_accepted(self):
        self.repo = self.repo / "literature"
        self.repo.mkdir()
        self.assertEqual(self.invoke("quantum chemistry").repo, self.repo)

    def test_relative_repository_paths_are_forwarded_as_values(self):
        for name in ("relative notes", "-notes"):
            with self.subTest(name=name):
                subprocess.run(["git", "init", "-q", str(self.root / name)], check=True)
                self.assertEqual(self.invoke("quantum chemistry", repo=name).repo, Path(name))

    def test_web_checklist_is_loaded_from_an_unrelated_working_directory(self):
        for backend in ("native", "copilot"):
            with self.subTest(backend=backend):
                args = self.invoke("quantum chemistry", BACKEND=backend)
                self.assertEqual(args.codex_backend, backend)
                self.assertEqual(args.claude_backend, backend)
                self.assertTrue(args.allow_web)
                self.assertIsNotNone(args.instructions_file)
                guidance = read_input(args.instructions_file).decode("utf-8")
                text = instructions(args.mode, guidance, str(args.output), args.allow_web)
                self.assertNotIn(GUIDANCE["report"], text)
                self.assertIn("Web sources are valid evidence", text)
                self.assertIn("without editing files", " ".join(text.split()))

    def test_checklist_path_ignores_cdpath(self):
        # With CDPATH set, cd prints the directory it found; a relative script
        # path then made the checklist path two lines long.
        root = SCRIPT.parents[1]
        result = subprocess.run(
            ["bash", str(SCRIPT.relative_to(root)), str(self.repo), "quantum chemistry"],
            cwd=root, env={**self.env, "CDPATH": str(root)},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = parser().parse_args(json.loads(result.stdout))
        self.assertEqual(Path(args.instructions_file), SCRIPT.with_name("search_literature_instructions.txt"))

    def test_missing_checklist_fails_before_creating_anything(self):
        lone = self.root / "elsewhere"
        lone.mkdir()
        copy = lone / SCRIPT.name
        copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        result = subprocess.run(["bash", str(copy), str(self.repo), "quantum chemistry"],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("search_literature_instructions.txt", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.repo / "duo-literature").exists())

    def test_duo_exit_status_is_preserved(self):
        for code in (1, 3, 4):
            with self.subTest(code=code):
                self.invoke("quantum chemistry", DUO_EXAMPLE_EXIT=str(code))


class SavedChangeExampleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "code with spaces"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.diff = self.root / "saved change.diff"
        self.diff.write_text("--- a/math.py\n+++ b/math.py\n@@ -1 +1 @@\n-return a-b\n+return a+b\n")
        self.tests = self.root / "test output.txt"
        self.tests.write_text("Ran 1 test\nOK\n")
        self.duo = self.root / "mock duo"
        self.duo.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                            "print(json.dumps(sys.argv[1:]))\n"
                            "sys.exit(int(os.environ.get('DUO_EXAMPLE_EXIT', '0')))\n")
        self.duo.chmod(0o755)
        self.script = SCRIPT.with_name("review_saved_change.sh")

    def invoke(self, *args, code=0):
        return subprocess.run(["bash", str(self.script), *map(str, args)], cwd=self.root,
                              env={**os.environ, "DUO": str(self.duo),
                                   "DUO_EXAMPLE_EXIT": str(code)},
                              capture_output=True, text=True, timeout=10)

    def test_saved_diff_and_test_output_are_context_in_supplied_order(self):
        result = self.invoke(self.repo, self.diff, self.tests)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = parser().parse_args(json.loads(result.stdout))
        self.assertEqual(args.context_file, [self.diff, self.tests])
        self.assertEqual(args.mode, "review")
        self.assertEqual(args.output, Path("saved-change-review.md"))
        self.assertEqual(args.max_turns, 4)
        self.assertEqual(args.max_attempts, 1)
        self.assertEqual(args.max_seconds, 480)
        self.assertEqual(args.max_prompt_bytes, 524288)
        self.assertEqual(read_input(args.context_file[0]), self.diff.read_bytes())
        self.assertEqual(read_input(args.context_file[1]), self.tests.read_bytes())

    def test_usage_and_non_worktree_fail_without_output_files(self):
        for args, code in (((self.repo,), 2), ((self.repo, self.diff, self.tests, "extra"), 2),
                           ((self.repo / ".git", self.diff, self.tests), 1)):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, code)
                self.assertEqual(result.stdout, "")
        self.assertFalse((self.repo / "saved-change-review.md").exists())

    def test_exit_status_is_preserved(self):
        for code in (1, 3, 4):
            self.assertEqual(self.invoke(self.repo, self.diff, self.tests, code=code).returncode, code)

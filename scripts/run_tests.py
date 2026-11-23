"""Run the offline suite strictly: empty suites, failures, errors, and skips fail.

Tests skip themselves when LangGraph is missing; a release check must not pass
that way. Run against an installed package, from the repository root:

  python scripts/run_tests.py

The tests directory is resolved relative to this script, so invoking it from
tests/ with ``python ../scripts/run_tests.py`` or by absolute path also works.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


def main() -> int:
    if importlib.util.find_spec("langgraph") is None:
        print("LangGraph is not installed; the workflow tests cannot run", file=sys.stderr)
        return 1
    tests = Path(__file__).resolve().parents[1] / "tests"
    suite = unittest.TestLoader().discover(str(tests))
    if suite.countTestCases() == 0:
        print(f"No tests discovered in {tests}; every test must run", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(f"{len(result.skipped)} tests were skipped; every test must run", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

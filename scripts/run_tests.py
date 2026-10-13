"""Run the offline test suite strictly: any failure or skipped test fails.

Tests skip themselves when LangGraph is missing; a release check must not pass
that way. Run from the tests directory against an installed package:

  cd tests && python ../scripts/run_tests.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest


def main() -> int:
    if importlib.util.find_spec("langgraph") is None:
        print("LangGraph is not installed; the workflow tests cannot run", file=sys.stderr)
        return 1
    result = unittest.main(module=None, argv=["run_tests", "discover", "-s", ".", "-v"],
                           exit=False).result
    if result.skipped:
        print(f"{len(result.skipped)} tests were skipped; every test must run", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

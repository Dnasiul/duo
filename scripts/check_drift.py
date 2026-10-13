"""Check installed reviewer CLIs against Duo's version policy and controls.

For each CLI this runs Duo's own credential-free setup checks (version, help,
feature/config probes, managed-policy detection) and compares help flags and
Codex feature names with the recorded fixtures of the first tested version.

Result per CLI:
  compatible        tested version; every control check passed
  untested-version  controls pass, but the version is outside the tested range
                    (expected after new releases; needs evidence before adoption)
  probe-failure     a control check failed or the CLI could not run

Exits 1 when any CLI has a probe failure. Used by the scheduled drift workflow.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
try:
    import duo  # noqa: F401  (prefer the installed package, as CI tests it)
except ImportError:
    sys.path.insert(0, str(ROOT / "src"))

from duo import copilot  # noqa: E402
from duo.models import Config, RunError  # noqa: E402
from duo.policy import (CLAUDE_SETTINGS, TESTED_VERSIONS, check_claude_managed_files,  # noqa: E402
                        verify_controls, verify_version)

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def flags(text: str) -> set[str]:
    return set(FLAG.findall(text))


def features(text: str) -> set[str]:
    return {line.split()[0] for line in text.splitlines() if len(line.split()) >= 3}


def compare(old: set[str], new: set[str]) -> dict:
    return {key: sorted(value) for key, value in
            (("added", new - old), ("removed", old - new)) if value}


def fixture(agent: str, name: str) -> str:
    low = ".".join(map(str, TESTED_VERSIONS[agent][0]))
    return (FIXTURES / f"{agent}-{low}" / name).read_text()


def run(argv: list[str], env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunError(f"Cannot run {' '.join(argv[:3])}: {exc}") from exc
    if result.returncode:
        raise RunError(f"{' '.join(argv[:3])} exited {result.returncode}")
    return result.stdout


def check(agent: str, binary: str, scratch: Path) -> dict:
    result = {"cli": agent, "version": None, "status": "probe-failure", "changes": {}}
    outputs: dict[str, str] = {}

    def probe(argv: list[str], env: dict[str, str] | None = None) -> str:
        text = run(argv, env)
        outputs[" ".join(argv[1:])] = text
        return text

    try:
        if agent == "copilot":
            # Duo's own Copilot preflight, in a fresh isolated home and workspace.
            workspace = scratch / "workspace"
            workspace.mkdir()
            config = Config(workspace, "drift-check", "drift-check", codex_backend="copilot",
                            claude_backend="copilot", copilot_bin=binary,
                            allow_untested_cli=True, skip_git_repo_check=True)
            home = scratch / "copilot"
            home.mkdir()
            version, policy = copilot.preflight(config, home)
            result["version"] = version.strip()
            status = policy["version_status"]
            (scratch / "help").mkdir()
            help_text = run([binary, "--no-auto-update", "--help"], copilot.environment(scratch / "help"))
            result["changes"]["help flags"] = compare(flags(fixture(agent, "help.txt")), flags(help_text))
        else:
            version = probe([binary, "--version"]).strip()
            result["version"] = version
            status = verify_version(agent, version, allow_untested=True)
            settings = scratch / "claude-settings.json"
            settings.write_text(json.dumps(CLAUDE_SETTINGS))
            if agent == "claude":
                check_claude_managed_files()
            verify_controls(binary, binary, settings, probe, agents={agent})
            if agent == "codex":
                result["changes"]["help flags"] = compare(flags(fixture(agent, "exec-help.txt")),
                                                          flags(outputs["exec --help"]))
                listed = next(text for key, text in outputs.items() if key.endswith("features list"))
                result["changes"]["features"] = compare(features(fixture(agent, "features.txt")),
                                                        features(listed))
            else:
                result["changes"]["help flags"] = compare(flags(fixture(agent, "help.txt")),
                                                          flags(outputs["--help"]))
    except RunError as exc:
        result["error"] = str(exc)
        return result
    result["status"] = "compatible" if status == "tested" else "untested-version"
    result["changes"] = {key: value for key, value in result["changes"].items() if value}
    return result


def report(results: list[dict]) -> str:
    lines = ["| CLI | Version | Result | Changes from fixtures |", "| --- | --- | --- | --- |"]
    for item in results:
        changes = "; ".join(f"{kind} {what}: {', '.join(names)}"
                            for kind, diff in item["changes"].items()
                            for what, names in diff.items()) or "none"
        detail = item.get("error", changes)
        lines.append(f"| {item['cli']} | {item['version'] or '?'} | {item['status']} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cli", nargs="+", choices=list(TESTED_VERSIONS), default=list(TESTED_VERSIONS))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--copilot-bin", default="copilot")
    parser.add_argument("--json", type=Path, help="Also write results as JSON")
    args = parser.parse_args()
    results = []
    for agent in args.cli:
        with tempfile.TemporaryDirectory() as scratch:
            results.append(check(agent, getattr(args, f"{agent}_bin"), Path(scratch)))
    text = report(results)
    print(text, end="")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## CLI drift check\n\n" + text)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")
    for item in results:
        if os.environ.get("GITHUB_ACTIONS"):
            if item["status"] == "probe-failure":
                print(f"::error title={item['cli']} probe failure::{item.get('error', '')}")
            elif item["status"] == "untested-version":
                print(f"::warning title={item['cli']} untested version::{item['version']} is outside "
                      "the tested range; record evidence before adopting it")
    return 1 if any(item["status"] == "probe-failure" for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())

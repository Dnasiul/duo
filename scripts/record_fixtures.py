"""Record sanitized real-CLI output as offline test fixtures.

  probes:  run the installed CLIs' credential-free probes (version, help,
           feature/config listings, claude doctor), exactly as preflight does.
  attempt: copy one audited Duo attempt (stdout/stderr/response) from a
           disposable run with synthetic content.

Output goes to tests/fixtures/<cli>-<version>/. Paths, user names, session and
request IDs, encrypted or reasoning payloads, and long tool output are removed.
Review the result before committing; tests/test_fixtures.py also rejects
credentials and home paths.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from duo import copilot  # noqa: E402
from duo.policy import CLAUDE_SETTINGS, claude_options, codex_options  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
LIMIT = 300  # characters kept from strings that Duo does not parse
# Opaque or bulky payloads with no bearing on Duo's parsing.
DROP = {"reasoningOpaque", "encryptedContent", "reasoningText", "reasoningBlocks",
        "encrypted_content", "apiCallId", "transformedContent", "contentBlocks",
        "detailedContent", "toolTelemetry", "restrictedProperties"}
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
REQUEST_ID = re.compile(r"Request ID: [0-9A-F:]+")


def scrub(text: str) -> str:
    text = text.replace(str(Path.home()), "/home/user")
    text = re.sub(r"/(?:private/)?(?:tmp|var/folders)/[^\s\"']*", "/tmp/duo-fixture", text)
    for name in {getpass.getuser(), os.environ.get("DUO_FIXTURE_ACCOUNT", "")} - {""}:
        text = re.sub(re.escape(name), "user", text, flags=re.I)
    text = UUID.sub("00000000-0000-0000-0000-000000000000", text)
    return REQUEST_ID.sub("Request ID: 0000:0000", text)


def trim(value, keep=False):
    """Scrub every string; shorten those Duo does not parse (keep=False)."""
    if isinstance(value, dict):
        return {k: trim(v, keep) for k, v in value.items() if k not in DROP}
    if isinstance(value, list):
        return [trim(v, keep) for v in value]
    if isinstance(value, str):
        value = scrub(value)
        return value if keep or len(value) <= LIMIT else value[:LIMIT] + "…[trimmed]"
    return value


def copilot_event(event: dict) -> dict:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    final = (event.get("type") == "assistant.message" and not data.get("toolRequests")
             and data.get("phase") in (None, "final_answer"))
    if final:
        # The answer Duo parses stays whole; only its metadata is trimmed.
        return {**trim({k: v for k, v in event.items() if k != "data"}),
                "data": {**trim({k: v for k, v in data.items() if k != "content"}),
                         "content": trim(data.get("content", ""), keep=True)}}
    return trim(event)


def jsonl(path: Path, convert) -> str:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return "".join(json.dumps(convert(event), ensure_ascii=False) + "\n" for event in events)


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)
    print(directory.relative_to(ROOT) / name)


def version_of(text: str) -> str:
    match = re.search(r"\d+\.\d+\.\d+", text)
    if not match:
        raise SystemExit(f"Cannot parse version from {text!r}")
    return match.group(0)


def record_probes(args) -> None:
    def run(argv, env=None):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env,
                                stdin=subprocess.DEVNULL)
        if result.returncode:
            raise SystemExit(f"{argv[0]} probe failed: {result.stderr.strip()[:200]}")
        return scrub(result.stdout)

    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        if "codex" in args.cli:
            version = run([args.codex_bin, "--version"])
            target = FIXTURES / f"codex-{version_of(version)}"
            write(target, "version.txt", version)
            write(target, "exec-help.txt", run([args.codex_bin, "exec", "--help"]))
            write(target, "features.txt", run([args.codex_bin, *codex_options(), "features", "list"]))
            # As preflight does: an empty CODEX_HOME loads what a review loads, so
            # the user's own MCP servers (ignored by reviews) stay out of fixtures.
            home = scratch / "codex-home"
            home.mkdir()
            write(target, "mcp-list.json", run([args.codex_bin, *codex_options(), "mcp", "list", "--json"],
                                               env={**os.environ, "CODEX_HOME": str(home)}))
        if "claude" in args.cli:
            settings = scratch / "claude-settings.json"
            settings.write_text(json.dumps(CLAUDE_SETTINGS))
            version = run([args.claude_bin, "--version"])
            target = FIXTURES / f"claude-{version_of(version)}"
            write(target, "version.txt", version)
            write(target, "help.txt", run([args.claude_bin, "--help"]))
            write(target, "doctor.txt", run([args.claude_bin, *claude_options(settings), "doctor"]))
        if "copilot" in args.cli:
            (scratch / "copilot").mkdir()
            env = copilot.environment(scratch / "copilot")
            base = [args.copilot_bin, "--no-auto-update"]
            version = run([*base, "--version"], env)
            target = FIXTURES / f"copilot-{version_of(version)}"
            write(target, "version.txt", version)
            write(target, "help.txt", run([*base, "--help"], env))
            write(target, "config.json", run([*base, "config", "--list", "--json"], env))


def record_attempt(args) -> None:
    attempt = args.attempt.expanduser().resolve()
    run_dir = attempt.parent
    agent = args.cli
    versions = json.loads((run_dir / "versions.json").read_text())
    target = FIXTURES / f"{agent}-{version_of(versions[agent])}"
    stdout, stderr = attempt / "stdout.log", attempt / "stderr.log"
    if agent == "copilot":
        if stdout.stat().st_size:
            write(target, f"events-{args.name}.jsonl", jsonl(stdout, copilot_event))
    elif agent == "codex":
        write(target, f"events-{args.name}.jsonl", jsonl(stdout, trim))
        if (attempt / "response.json").exists():
            response = json.loads((attempt / "response.json").read_text())
            write(target, f"response-{args.name}.json",
                  json.dumps(trim(response, keep=True), indent=2, ensure_ascii=False) + "\n")
    else:
        envelope = json.loads(stdout.read_text())
        kept = {k: trim(v, keep=(k == "structured_output")) for k, v in envelope.items()}
        write(target, f"result-{args.name}.json", json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
    if agent != "codex" and stderr.exists() and stderr.stat().st_size:
        # Codex stderr repeats the prompt; Duo reads only its JSON events.
        write(target, f"stderr-{args.name}.txt", scrub(stderr.read_text()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    probes = sub.add_parser("probes", help="Record credential-free probe output")
    probes.add_argument("--cli", nargs="+", choices=["codex", "claude", "copilot"],
                        default=["codex", "claude", "copilot"])
    probes.add_argument("--codex-bin", default="codex")
    probes.add_argument("--claude-bin", default="claude")
    probes.add_argument("--copilot-bin", default="copilot")
    probes.set_defaults(func=record_probes)
    attempt = sub.add_parser("attempt", help="Record one audited attempt")
    attempt.add_argument("cli", choices=["codex", "claude", "copilot"])
    attempt.add_argument("attempt", type=Path, help="Audit attempt directory")
    attempt.add_argument("name", help="Fixture name, e.g. success or model-unavailable")
    attempt.set_defaults(func=record_attempt)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

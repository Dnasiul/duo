"""Record sanitized real-CLI output as offline test fixtures.

  probes:  run the installed CLIs' production-equivalent probes (version, help,
           feature/config listings, claude doctor) with preflight's restricted
           environments, which can retain permitted authentication variables.
  attempt: copy one audited Duo attempt (stdout/stderr/response) from a
           disposable run with synthetic content.
  rejection: record how each installed CLI rejects an unknown option; the
           argument parser refuses it before any model call.

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
from duo.models import RunError  # noqa: E402
from duo.policy import (CLAUDE_SETTINGS, check_claude_managed_files, claude_options,  # noqa: E402
                        codex_options, parse_version, reviewer_environment)

FIXTURES = ROOT / "tests" / "fixtures"
LIMIT = 300  # characters kept from strings that Duo does not parse
# Opaque or bulky payloads with no bearing on Duo's parsing.
DROP = {"reasoningOpaque", "encryptedContent", "reasoningText", "reasoningBlocks",
        "encrypted_content", "apiCallId", "transformedContent", "contentBlocks",
        "detailedContent", "toolTelemetry", "restrictedProperties", "thinking", "signature",
        "credential_expires_at"}
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Request and message identifiers in free text. Wording-independent protection
# for JSON comes from identifier keys (below); these cover stderr and prose.
TEXT_IDENTIFIERS = (
    (re.compile(r"(?i)(request[ _-]?id[\"']?\s*[:=]\s*[\"']?)[^\s\"',)]+"), r"\g<1>0000:0000"),
    # GitHub request IDs, e.g. CF0A:19D245:10CD91F:1650669:6ACC474C.
    (re.compile(r"\b[0-9A-F]{4,8}(?::[0-9A-F]{4,16}){3,5}\b"), "0000:0000"),
    # Anthropic and OpenAI message, request, response, and tool-call IDs.
    (re.compile(r"\b(msg|req|toolu|srvtoolu|resp|call|chatcmpl)([_-])[A-Za-z0-9]{16,}\b"),
     r"\1\2fixture"),
)
# Keys ending in Id/_id hold identifiers except these, which name a model,
# provider, or turn number.
DESCRIPTIVE_KEYS = {"modelId", "providerId", "turnId"}
# Already replaced (zeroed UUIDs and request IDs, or a stream's fixture-id-N).
PLACEHOLDER = re.compile(r"[0:-]+|fixture-id-\d+")
UNKNOWN_OPTION = "--duo-fixture-unknown-option"


def scrub(text: str) -> str:
    text = text.replace(str(Path.home()), "/home/user")
    text = re.sub(r"/(?:private/)?(?:tmp|var/folders)/[^\s\"']*", "/tmp/duo-fixture", text)
    for name in {getpass.getuser(), os.environ.get("DUO_FIXTURE_ACCOUNT", "")} - {""}:
        text = re.sub(re.escape(name), "user", text, flags=re.I)
    text = UUID.sub("00000000-0000-0000-0000-000000000000", text)
    for pattern, replacement in TEXT_IDENTIFIERS:
        text = pattern.sub(replacement, text)
    return text


def identifier_key(key: str) -> bool:
    return key not in DESCRIPTIVE_KEYS and (
        key in ("id", "uuid") or key.endswith(("_id", "Id", "ID")))


def identifiers():
    """A per-stream mapping that keeps correlations without original values."""
    seen = {}

    def identifier(value: str) -> str:
        if PLACEHOLDER.fullmatch(value):
            return value  # re-sanitizing a fixture leaves its placeholders alone
        return seen.setdefault(value, f"fixture-id-{len(seen) + 1}")
    return identifier


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


def copilot_events():
    """A per-stream sanitizer: request, call, and response IDs become fixture IDs."""
    identifier = identifiers()

    def clean(value, key=""):
        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items() if k not in DROP}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str) and identifier_key(key):
            return identifier(value)
        return trim(value)

    def convert(event: dict) -> dict:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        final = (event.get("type") == "assistant.message" and not data.get("toolRequests")
                 and data.get("phase") in (None, "final_answer"))
        if final:
            # The answer Duo parses stays whole; only its metadata is trimmed.
            return {**clean({k: v for k, v in event.items() if k != "data"}),
                    "data": {**clean({k: v for k, v in data.items() if k != "content"}),
                             "content": trim(data.get("content", ""), keep=True)}}
        return clean(event)
    return convert


def claude_events():
    """A per-stream sanitizer: preserve tool correlation without original IDs."""
    identifier = identifiers()

    def convert(event):
        if event.get("type") == "rate_limit_event":
            event = {k: v for k, v in event.items() if k != "rate_limit_info"}
        def clean(value, key="", keep=False):
            if isinstance(value, dict):
                if key == "wire_tool_inputs":
                    return {identifier(k): clean(v, keep=True) for k, v in value.items()}
                return {k: clean(v, k, keep or k in ("structured_output", "input", "tool_input")
                                 or (k == "result" and value.get("is_error") is True))
                        for k, v in value.items() if keep or k not in DROP}
            if isinstance(value, list):
                return [clean(v, keep=keep) for v in value]
            if isinstance(value, str) and identifier_key(key):
                return identifier(value)
            return trim(value, keep=keep)
        return clean(event)
    return convert


def jsonl(path: Path, convert, *, metadata: dict | None = None) -> str:
    lines = path.read_bytes().splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Interrupted streams can stop inside JSON or a UTF-8 character.
            # Never discard malformed complete lines or corruption mid-stream.
            if index != len(lines) - 1 or line.endswith((b"\n", b"\r")):
                raise
            if metadata is not None:
                metadata["fixture_import"] = {"truncated_final_line": True}
    return "".join(json.dumps(convert(event), ensure_ascii=False) + "\n" for event in events)


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)
    print(directory.relative_to(ROOT) / name)


def version_of(agent: str, text: str) -> str:
    """The fixture directory's version: Duo's own anchored parser, or stop."""
    try:
        return ".".join(map(str, parse_version(agent, text)))
    except RunError as exc:
        raise SystemExit(f"{exc}: {text[:200]!r}") from exc


def record_probes(args) -> None:
    def run(argv, env):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env,
                                stdin=subprocess.DEVNULL)
        if result.returncode:
            raise SystemExit(f"{argv[0]} probe failed: {result.stderr.strip()[:200]}")
        return scrub(result.stdout)

    if "claude" in args.cli:
        check_claude_managed_files()
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        if "codex" in args.cli:
            env = reviewer_environment("codex")
            version = run([args.codex_bin, "--version"], env)
            target = FIXTURES / f"codex-{version_of('codex', version)}"
            write(target, "version.txt", version)
            write(target, "exec-help.txt", run([args.codex_bin, "exec", "--help"], env))
            write(target, "features.txt", run([args.codex_bin, *codex_options(), "features", "list"], env))
            # As preflight does: an empty CODEX_HOME loads what a review loads, so
            # the user's own MCP servers (ignored by reviews) stay out of fixtures.
            home = scratch / "codex-home"
            home.mkdir()
            write(target, "mcp-list.json", run([args.codex_bin, *codex_options(), "mcp", "list", "--json"],
                                               env={**env, "CODEX_HOME": str(home)}))
        if "claude" in args.cli:
            settings = scratch / "claude-settings.json"
            settings.write_text(json.dumps(CLAUDE_SETTINGS))
            env = reviewer_environment("claude")
            version = run([args.claude_bin, "--version"], env)
            target = FIXTURES / f"claude-{version_of('claude', version)}"
            write(target, "version.txt", version)
            write(target, "help.txt", run([args.claude_bin, "--help"], env))
            check_claude_managed_files()
            doctor = run([args.claude_bin, *claude_options(settings), "doctor"], env)
            check_claude_managed_files()
            write(target, "doctor.txt", doctor)
        if "copilot" in args.cli:
            (scratch / "copilot").mkdir()
            env = copilot.environment(scratch / "copilot")
            base = [args.copilot_bin, "--no-auto-update"]
            version = run([*base, "--version"], env)
            target = FIXTURES / f"copilot-{version_of('copilot', version)}"
            write(target, "version.txt", version)
            write(target, "help.txt", run([*base, "--help"], env))
            write(target, "config.json", run([*base, "config", "--list", "--json"], env))


def record_attempt(args) -> None:
    attempt = args.attempt.expanduser().resolve()
    run_dir = attempt.parent
    agent = args.cli
    versions = json.loads((run_dir / "versions.json").read_text())
    target = FIXTURES / f"{agent}-{version_of(agent, versions[agent])}"
    stdout, stderr = attempt / "stdout.log", attempt / "stderr.log"
    if agent == "copilot":
        if stdout.stat().st_size:
            write(target, f"events-{args.name}.jsonl", jsonl(stdout, copilot_events()))
    elif agent == "codex":
        write(target, f"events-{args.name}.jsonl", jsonl(stdout, trim))
        if (attempt / "response.json").exists():
            response = json.loads((attempt / "response.json").read_text())
            write(target, f"response-{args.name}.json",
                  json.dumps(trim(response, keep=True), indent=2, ensure_ascii=False) + "\n")
    else:
        metadata = attempt / "claude-stream.json"
        details = json.loads(metadata.read_text()) if metadata.exists() else {}
        write(target, f"events-{args.name}.jsonl",
              jsonl(stdout, claude_events(), metadata=details))
        if metadata.exists() or details:
            write(target, f"metadata-{args.name}.json",
                  json.dumps(trim(details, keep=True), indent=2) + "\n")
    if agent != "codex" and stderr.exists() and stderr.stat().st_size:
        # Codex stderr repeats the prompt; Duo reads only its JSON events.
        write(target, f"stderr-{args.name}.txt", scrub(stderr.read_text()))


def record_rejections(args) -> None:
    """How each CLI's argument parser refuses an option it does not define.

    Each argv is the start of Duo's own invocation plus UNKNOWN_OPTION, with no
    prompt argument and an empty stdin, so even a CLI that accepted the option
    would have nothing to send to a model. The exit code and both streams are kept.
    """
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        for agent in args.cli:
            binary = getattr(args, f"{agent}_bin")
            if agent == "copilot":
                (scratch / "copilot").mkdir()
                env = copilot.environment(scratch / "copilot")
                base = prefix = [binary, "--no-auto-update"]
            else:
                env = reviewer_environment(agent)
                base = [binary]
                prefix = [binary, "exec"] if agent == "codex" else [binary, "-p"]
            version = subprocess.run([*base, "--version"], capture_output=True, text=True,
                                     timeout=60, env=env, stdin=subprocess.DEVNULL)
            target = FIXTURES / f"{agent}-{version_of(agent, version.stdout)}"
            argv = [*prefix, UNKNOWN_OPTION]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env,
                                    stdin=subprocess.DEVNULL)
            if result.returncode == 0:
                raise SystemExit(f"{agent} accepted {UNKNOWN_OPTION}; nothing recorded")
            record = {"argv": [agent, *argv[1:]], "exit_code": result.returncode,
                      "stdout": scrub(result.stdout), "stderr": scrub(result.stderr)}
            write(target, "rejection-unknown-option.json", json.dumps(record, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    probes = sub.add_parser("probes", help="Record production-equivalent probe output")
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
    rejection = sub.add_parser("rejection", help="Record each CLI's unknown-option rejection")
    rejection.add_argument("--cli", nargs="+", choices=["codex", "claude", "copilot"],
                           default=["codex", "claude", "copilot"])
    rejection.add_argument("--codex-bin", default="codex")
    rejection.add_argument("--claude-bin", default="claude")
    rejection.add_argument("--copilot-bin", default="copilot")
    rejection.set_defaults(func=record_rejections)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

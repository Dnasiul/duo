"""Check installed reviewer CLIs against Duo's version policy and controls.

For each CLI this runs Duo's own setup checks without model calls (version, help,
feature/config probes, managed-policy detection) and compares help options and
Codex feature rows with the latest reviewed baseline at or below the installed
version. Baselines are recorded fixtures listed, with SHA-256 digests, in
tests/fixtures/drift-baselines.json; a digest is verified before its file is
used, and only `--review` (after the evidence procedure) changes the manifest.
Allowed authentication may remain. An empty CODEX_HOME for MCP listing removes
the usual stored-login location, not allowed authentication variables. These
checks establish option availability and selected metadata, not write/read/tool
enforcement or network denial; unavailable authenticated policy is unverified.

Result per CLI:
  compatible        tested version; probes passed; no change needing review
  needs-review      tested version and probes pass, but an option or feature
                    was removed, a feature state changed, a new feature is
                    enabled, an output layout is unrecognized, or the baseline
                    is missing or fails its digest check
  untested-version  probes pass, but the version is outside the tested range
                    (expected after new releases; needs evidence before adoption)
  probe-failure     a control check failed or the CLI could not run

Exits 1 when any CLI has a probe failure. Used by the scheduled drift workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES / "drift-baselines.json"
try:
    import duo  # noqa: F401  (prefer the installed package, as CI tests it)
except ImportError:
    sys.path.insert(0, str(ROOT / "src"))

from duo import copilot  # noqa: E402
from duo.models import Config, RunError  # noqa: E402
from duo.policy import (CLAUDE_REQUIRED_FLAGS, CLAUDE_SETTINGS, CODEX_DISABLED,  # noqa: E402
                        CODEX_FEATURE_STAGES, CODEX_REQUIRED_FLAGS, TESTED_VERSIONS,
                        check_claude_managed_files, parse_version, reviewer_environment,
                        verify_controls, verify_version)

# The fixture files each CLI is compared with.
COMPARED = {"codex": ("exec-help.txt", "features.txt"), "claude": ("help.txt",),
            "copilot": ("help.txt",)}

LONG = r"--[A-Za-z][A-Za-z0-9-]*"
FLAG = re.compile(rf"(?<![\w-]){LONG}")
OPTION = r"-{1,2}[A-Za-z0-9][A-Za-z0-9-]*"
# An option definition starts its line (indented at most 8 columns), possibly
# with aliases (`-c, --config`, `--allowedTools, --allowed-tools`). Wrapped
# descriptions are indented further, so mentions there are prose.
DEFINITION = re.compile(rf"^[ \t]{{0,8}}({OPTION}(?:,[ \t]*{OPTION})*)(?=[ \t=<\[]|$)")
OPTIONS_HEADER = re.compile(r"(?m)^Options:[ \t]*$")


def flags(text: str) -> set[str]:
    """Every long option mentioned anywhere, definitions and prose alike."""
    return set(FLAG.findall(text))


def defined_flags(text: str) -> set[str]:
    """Long options the help text defines, aliases included."""
    found = set()
    for line in text.splitlines():
        if match := DEFINITION.match(line):
            found.update(FLAG.findall(match.group(1)))
    return found


def help_layout(text: str) -> str:
    return "recognized" if OPTIONS_HEADER.search(text) and defined_flags(text) else "unknown"


def compare(old: set[str], new: set[str]) -> dict:
    return {key: sorted(value) for key, value in
            (("added", new - old), ("removed", old - new)) if value}


def compare_help(old: str, new: str) -> dict:
    """Defined options added and removed; a removal still named in prose is reported as such."""
    before, after, mentioned = defined_flags(old), defined_flags(new), flags(new)
    removed = before - after
    diff = {"added": after - before, "removed": removed - mentioned,
            "removed, still mentioned in prose": removed & mentioned}
    return {key: sorted(value) for key, value in diff.items() if value}


def features(text: str) -> set[str]:
    return set(feature_rows(text)[0])


def feature_rows(text: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """`name stage state` rows, and every line that is not one (or repeats a name)."""
    rows: dict[str, tuple[str, str]] = {}
    unrecognized = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        stage = " ".join(parts[1:-1])
        if (len(parts) >= 3 and parts[-1] in ("true", "false") and stage in CODEX_FEATURE_STAGES
                and parts[0] not in rows):
            rows[parts[0]] = (stage, parts[-1])
        else:
            unrecognized.append(line.strip())
    return rows, unrecognized


def compare_features(old: str, new: str) -> dict:
    before, after = feature_rows(old)[0], feature_rows(new)[0]
    common = before.keys() & after.keys()
    diff = {
        "added": sorted(after.keys() - before.keys()),
        "added enabled": sorted(n for n in after.keys() - before.keys() if after[n][1] == "true"),
        "removed": sorted(before.keys() - after.keys()),
        "enabled": sorted(n for n in common if (before[n][1], after[n][1]) == ("false", "true")),
        "disabled": sorted(n for n in common if (before[n][1], after[n][1]) == ("true", "false")),
        "stage changed": sorted(f"{n} ({before[n][0]} to {after[n][0]})" for n in common
                                if before[n][0] != after[n][0]),
    }
    return {key: value for key, value in diff.items() if value}


# Changes that need review even for a tested version. Additions and stage
# changes are reported only.
REVIEW = {"removed": "removed", "removed, still mentioned in prose": "removed",
          "added enabled": "enabled by default", "enabled": "state changed",
          "disabled": "state changed"}


def version_tuple(text: str) -> tuple[int, ...]:
    """A bare X.Y.Z, as in baseline entries and --review; CLI output uses parse_version."""
    if not isinstance(text, str) or not (match := re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)):
        raise RunError(f"Cannot parse a version from {text!r}")
    return tuple(map(int, match.groups()))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baselines(path: Path) -> list[dict]:
    """The reviewed baseline entries; a malformed manifest refuses."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunError(f"Cannot read the drift baseline manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RunError(f"Unsupported drift baseline manifest {path}")
    entries = data.get("baselines")
    if not isinstance(entries, list):
        raise RunError(f"Unsupported drift baseline manifest {path}")
    for entry in entries:
        files = entry.get("files") if isinstance(entry, dict) else None
        if (not isinstance(files, list) or entry.get("cli") not in COMPARED
                or not isinstance(entry.get("version"), str)
                or not all(isinstance(f, dict) and isinstance(f.get("path"), str)
                           and isinstance(f.get("sha256"), str) for f in files)):
            raise RunError(f"Malformed entry in drift baseline manifest {path}: {entry!r}")
        version_tuple(entry["version"])
    return entries


def baseline(entries: list[dict], agent: str, version: tuple[int, ...],
             root: Path | None = None) -> tuple[str | None, dict[str, str], list[str]]:
    """The latest reviewed baseline at or below VERSION: (name, file texts, problems)."""
    root = root or FIXTURES
    eligible = [e for e in entries if e["cli"] == agent and version_tuple(e["version"]) <= version]
    if not eligible:
        return None, {}, [f"no reviewed baseline at or below {'.'.join(map(str, version))}"]
    entry = max(eligible, key=lambda e: version_tuple(e["version"]))
    name = f"{agent}-{entry['version']}"
    texts, problems = {}, []
    recorded = {Path(f["path"]).name: f for f in entry["files"]}
    for file in COMPARED[agent]:
        item = recorded.get(file)
        path = (root / item["path"]) if item else None
        if item is None or path.resolve().parent != (root / name).resolve():
            problems.append(f"baseline {name} does not list {file}")
        elif not path.is_file():
            problems.append(f"baseline file {item['path']} is missing")
        elif digest(path) != item["sha256"]:
            # A re-recorded fixture must be reviewed before it is trusted.
            problems.append(f"baseline file {item['path']} does not match its reviewed digest")
        else:
            texts[file] = path.read_text()
    return name, texts, problems


def run(argv: list[str], env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunError(f"Cannot run {' '.join(argv[:3])}: {exc}") from exc
    if result.returncode:
        raise RunError(f"{' '.join(argv[:3])} exited {result.returncode}")
    return result.stdout


def check(agent: str, binary: str, scratch: Path, manifest: Path | None = None) -> dict:
    result = {"cli": agent, "version": None, "status": "probe-failure", "baseline": None,
              "changes": {}, "review": []}
    outputs: dict[str, str] = {}
    live: dict[str, str] = {}

    def probe(argv: list[str], env: dict[str, str] | None = None) -> str:
        # The environment a review gives this CLI, as Duo's own preflight uses.
        text = run(argv, reviewer_environment(agent) if env is None else env)
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
            live["help.txt"] = run([binary, "--no-auto-update", "--help"],
                                   copilot.environment(scratch / "help"))
        else:
            version = probe([binary, "--version"]).strip()
            result["version"] = version
            status = verify_version(agent, version, allow_untested=True)
            settings = scratch / "claude-settings.json"
            settings.write_text(json.dumps(CLAUDE_SETTINGS))
            if agent == "claude":
                check_claude_managed_files()
            # verify_controls also requires the stream-json format value:
            # option-name diffs alone cannot detect its removal from help.
            verify_controls(binary, binary, settings, probe, agents={agent})
            if agent == "codex":
                live["exec-help.txt"] = outputs["exec --help"]
                live["features.txt"] = next(text for key, text in outputs.items()
                                            if key.endswith("features list"))
            else:
                live["help.txt"] = outputs["--help"]
        name, texts, problems = baseline(load_baselines(manifest or MANIFEST), agent,
                                         parse_version(agent, result["version"]))
    except RunError as exc:
        result["error"] = str(exc)
        return result
    result["baseline"] = name
    review = list(problems)
    for file, text in live.items():
        label = "features" if file == "features.txt" else "help flags"
        if file == "features.txt":
            if unknown := feature_rows(text)[1]:
                review.append(f"unrecognized features list rows ({len(unknown)}): {unknown[0]!r}")
        elif help_layout(text) == "unknown":
            review.append(f"unrecognized {file} layout (no Options section with definitions)")
        if file in texts:
            diff = (compare_features if file == "features.txt" else compare_help)(texts[file], text)
            result["changes"][label] = diff
            review += sorted({f"{label} {REVIEW[key]}" for key in diff if key in REVIEW})
    result["review"] = review
    result["changes"] = {key: value for key, value in result["changes"].items() if value}
    if status != "tested":
        result["status"] = "untested-version"
    else:
        result["status"] = "needs-review" if review else "compatible"
    return result


def review_errors(agent: str, directory: Path) -> list[str]:
    """Why a recorded fixture cannot become a baseline: missing files or controls."""
    errors = [f"{file} is missing" for file in COMPARED[agent] if not (directory / file).is_file()]
    if errors:
        return errors
    help_name = "exec-help.txt" if agent == "codex" else "help.txt"
    help_text = (directory / help_name).read_text()
    if help_layout(help_text) == "unknown":
        errors.append(f"{help_name} layout is unrecognized")
    defined = defined_flags(help_text)
    required = {"codex": CODEX_REQUIRED_FLAGS, "claude": CLAUDE_REQUIRED_FLAGS,
                "copilot": copilot.FLAGS}[agent]
    errors += [f"{help_name} does not define required option {f}" for f in required if f not in defined]
    if agent == "claude" and not re.search(r"\bstream-json\b", help_text):
        errors.append("help.txt does not offer stream-json")
    if agent == "copilot" and not (copilot.SESSION_FLAG in defined
                                   or set(copilot.LEGACY_SESSION_FLAGS) <= defined):
        errors.append("help.txt defines no session-sync control")
    if agent == "codex":
        rows, unknown = feature_rows((directory / "features.txt").read_text())
        if unknown:
            errors.append(f"features.txt has unrecognized rows: {unknown[0]!r}")
        errors += [f"features.txt does not report {f} false" for f in CODEX_DISABLED
                   if rows.get(f, ("", ""))[1] != "false"]
    return errors


def record_review(agent: str, version: str, manifest: Path, root: Path | None = None) -> dict:
    """Record a reviewed fixture's digests as the baseline for AGENT VERSION."""
    root = root or FIXTURES
    version_tuple(version)
    directory = root / f"{agent}-{version}"
    if errors := review_errors(agent, directory):
        raise RunError(f"Cannot review {directory.name}: " + "; ".join(errors))
    entries = load_baselines(manifest) if manifest.exists() else []
    entry = {"cli": agent, "version": version,
             "files": [{"path": f"{directory.name}/{file}", "sha256": digest(directory / file)}
                       for file in COMPARED[agent]]}
    entries = [e for e in entries if (e["cli"], e["version"]) != (agent, version)] + [entry]
    entries.sort(key=lambda e: (e["cli"], version_tuple(e["version"])))
    manifest.write_text(json.dumps({"schema_version": 1, "baselines": entries}, indent=2) + "\n")
    return entry


def describe(item: dict) -> str:
    """One cell of detail: an error, or the review reasons and changes."""
    if item.get("error"):
        return item["error"]
    changes = [f"{kind} {what}: {', '.join(names)}" for kind, diff in item["changes"].items()
               for what, names in diff.items()]
    parts = [f"**review:** {'; '.join(item['review'])}"] if item.get("review") else []
    return "; ".join(parts + changes) or "none"


def report(results: list[dict]) -> str:
    lines = ["| CLI | Version | Result | Baseline | Changes from baseline |",
             "| --- | --- | --- | --- | --- |"]
    for item in results:
        lines.append(f"| {item['cli']} | {item['version'] or '?'} | {item['status']} | "
                     f"{item.get('baseline') or '?'} | {describe(item)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cli", nargs="+", choices=list(TESTED_VERSIONS), default=list(TESTED_VERSIONS))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--copilot-bin", default="copilot")
    parser.add_argument("--json", type=Path, help="Also write results as JSON")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=argparse.SUPPRESS)
    parser.add_argument("--review", nargs=2, metavar=("CLI", "VERSION"),
                        help="After reviewing tests/fixtures/CLI-VERSION under the evidence "
                             "procedure, record its digests as a baseline")
    args = parser.parse_args()
    if args.review:
        agent, version = args.review
        if agent not in COMPARED:
            parser.error(f"unknown CLI {agent!r}")
        try:
            entry = record_review(agent, version, args.manifest)
        except RunError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Recorded baseline {agent}-{version}: " +
              ", ".join(f"{f['path']} {f['sha256'][:12]}" for f in entry["files"]))
        return 0
    results = []
    for agent in args.cli:
        with tempfile.TemporaryDirectory() as scratch:
            results.append(check(agent, getattr(args, f"{agent}_bin"), Path(scratch), args.manifest))
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
            elif item["status"] == "needs-review":
                print(f"::warning title={item['cli']} needs review::{'; '.join(item['review'])}")
    return 1 if any(item["status"] == "probe-failure" for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())

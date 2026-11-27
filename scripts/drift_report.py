"""Summarize drift-check results across platforms and decide the drift issue's action.

The weekly drift workflow tracks one issue. It is opened (or commented on) when
any platform needs attention, and closed only when every expected platform
reported every CLI as compatible. Attention is needed for any result other than
`compatible` (removals and other changes needing review, untested versions,
probe failures, baseline digest mismatches), and for missing or unreadable
platform results.

Standard library only: the report job does not install Duo.

  python3 scripts/drift_report.py --run-url URL --open-issue NUMBER \\
      --platforms ubuntu-latest macos-latest --body body.md drift-*.json

prints one action: create, comment, close, or none.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CLIS = ("codex", "claude", "copilot")


def detail(item: dict) -> str:
    if item.get("error"):
        return str(item["error"])
    changes = [f"{kind} {what}: {', '.join(names)}"
               for kind, diff in (item.get("changes") or {}).items()
               for what, names in diff.items()]
    review = item.get("review") or []
    parts = [f"**review:** {'; '.join(review)}"] if review else []
    return "; ".join(parts + changes) or "none"


def summarize(results: dict[str, object], platforms: list[str],
              clis: tuple[str, ...] = CLIS) -> tuple[list[str], bool]:
    """Table rows and whether attention is needed. RESULTS maps platform to parsed JSON."""
    rows, attention = [], False
    for platform in platforms:
        items = results.get(platform)
        if not isinstance(items, list):
            attention = True
            rows.append(f"| {platform} | | | missing results | | the check job did not finish; see the run |")
            continue
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                attention = True
                rows.append(f"| {platform} | | | unreadable result | | {item!r} |")
                continue
            seen.add(item.get("cli"))
            attention |= item.get("status") != "compatible"
            rows.append(f"| {platform} | {item.get('cli')} | {item.get('version') or '?'} | "
                        f"{item.get('status')} | {item.get('baseline') or '?'} | {detail(item)} |")
        for cli in clis:
            if cli not in seen:
                attention = True
                rows.append(f"| {platform} | {cli} | | missing result | | no result for this CLI |")
    for platform in sorted(set(results) - set(platforms)):
        attention = True
        rows.append(f"| {platform} | | | unexpected platform | | results from an unexpected job |")
    return rows, attention


def action(attention: bool, open_issue: str | None) -> str:
    if attention:
        return "comment" if open_issue else "create"
    return "close" if open_issue else "none"


def load(paths: list[Path]) -> dict[str, object]:
    results: dict[str, object] = {}
    for path in paths:
        platform = path.name.removeprefix("drift-").removesuffix(".json")
        try:
            results[platform] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            results[platform] = None
    return results


def body(rows: list[str], run_url: str) -> str:
    return ("Weekly CLI drift check: " + run_url + "\n\n"
            "| Platform | CLI | Version | Result | Baseline | Details |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(rows) + "\n\nSee \"Responding to CLI drift\" in README.md.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--platforms", nargs="+", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--open-issue", default="", help="Number of the open drift issue, if any")
    parser.add_argument("--body", type=Path, required=True)
    args = parser.parse_args()
    rows, attention = summarize(load(args.files), args.platforms)
    args.body.write_text(body(rows, args.run_url), encoding="utf-8")
    print(action(attention, args.open_issue or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())

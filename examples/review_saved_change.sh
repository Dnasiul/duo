#!/usr/bin/env bash
# Review a saved diff and test output as captured evidence.
# Usage: review_saved_change.sh REPO DIFF TEST_OUTPUT
# DUO, CODEX_MODEL, and CLAUDE_MODEL may override executable/model defaults.
# Evidence must be UTF-8, non-sensitive, and at most 64 KiB combined.
# The files are captured once; separately inspected repository files stay mutable.
set -euo pipefail
if [[ $# -ne 3 ]]; then
  printf 'Usage: %s REPO DIFF TEST_OUTPUT\n' "$0" >&2
  exit 2
fi
if [[ "$(git -C "$1" rev-parse --is-inside-work-tree 2>/dev/null)" != true ]]; then
  printf 'Not a Git working tree: %s\n' "$1" >&2
  exit 1
fi
exec "${DUO:-duo}" \
  --repo "$1" --mode review --output saved-change-review.md \
  --task 'Review the change in the captured diff using the captured test output and relevant repository files. Produce a concise code review; do not implement changes.' \
  --criteria 'Report actionable correctness bugs with file, line, trigger, impact, and a suggested regression test. Distinguish test evidence from untested claims. If none are found, say so and state coverage limits.' \
  --context-file "$2" --context-file "$3" \
  --codex-model "${CODEX_MODEL:-gpt-6-astra}" \
  --claude-model "${CLAUDE_MODEL:-claude-sonnet-5-5}" \
  --max-turns 4 --max-attempts 1 --timeout 120 --max-seconds 480 \
  --max-prompt-bytes 524288 --show-conversation

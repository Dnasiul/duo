#!/usr/bin/env bash
# Review a codebase for bugs with native Codex and Claude Code.
#
# Usage: review_codebase.sh /path/to/repo
# Optional environment variables: DUO (an executable name or path, without
# arguments), CODEX_MODEL, CLAUDE_MODEL. Additional script arguments are rejected.
#
# Use this on trusted repositories only: native Codex can read any file your
# user can read, and whatever the reviewers read goes to the model providers.
# Using Copilot does not remove Duo's trusted-repository requirement.
#
# Writes duo-reviews/code-review.md inside the repository; an existing review
# there becomes the starting point of the next run. To keep reviews out of your
# working checkout, point this at a worktree: git worktree add --detach DIR HEAD
#
# One run over a whole codebase suits small projects. For larger ones, narrow
# --task to one area per run and use a different --output for each.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s /path/to/repo (exactly one argument required)\n' "$0" >&2
  exit 2
fi

REPO="$1"
DUO="${DUO:-duo}"
CODEX_MODEL="${CODEX_MODEL:-gpt-6-astra}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-5-5}"

if [[ ! -d "$REPO" ]]; then
  printf 'Repository directory does not exist: %s\n' "$REPO" >&2
  exit 1
fi

# Duo refuses a directory outside Git; check first so no folder is left behind.
if ! git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'Not a Git repository: %s\n' "$REPO" >&2
  exit 1
fi

mkdir -p -- "$REPO/duo-reviews"

# Duo's exit status is kept: 0 agreed, 3 blocked, 4 turn or time limit.
exec "$DUO" \
  --repo "$REPO" \
  --mode review \
  --output duo-reviews/code-review.md \
  --task "Review this codebase for actionable bugs.
Inspect the implementation, tests, and documented behavior.
Focus on correctness, error handling, resource cleanup, data loss,
and security boundaries." \
  --criteria "For every finding, provide:
severity, file and line, a concrete triggering scenario,
the impact, and a suggested fix and regression test.
Distinguish confirmed bugs from unverified concerns.
Avoid cosmetic suggestions and speculative findings.
State what was inspected and any coverage gaps.
If no actionable bugs are found, say so explicitly." \
  --codex-model "$CODEX_MODEL" \
  --claude-model "$CLAUDE_MODEL" \
  --codex-effort high \
  --claude-effort high \
  --max-turns 8 \
  --max-attempts 2 \
  --max-seconds 3600 \
  --show-conversation

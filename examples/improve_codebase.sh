#!/usr/bin/env bash
# Plan improvements to a codebase with native Codex and Claude Code.
#
# Usage: improve_codebase.sh /path/to/repo
# Optional environment variables: DUO (an executable name or path, without
# arguments), CODEX_MODEL, CLAUDE_MODEL. Additional script arguments are rejected.
#
# Use this on trusted repositories only: native Codex can read any file your
# user can read, and whatever the reviewers read goes to the model providers.
# Using Copilot does not remove Duo's trusted-repository requirement.
#
# Writes duo-plans/improvement-plan.md inside the repository; an existing plan
# there becomes the starting point of the next run. Duo changes nothing else:
# implement the plan yourself or with a coding agent, one item at a time. To keep
# the plan out of your working checkout, point this at a worktree:
# git worktree add --detach DIR HEAD
#
# If duo-reviews/code-review.md exists (see review_codebase.sh), the plan starts
# with its confirmed findings. For larger codebases, narrow --task to one area
# per run and use a different --output for each.

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

REVIEW=""
if [[ -f "$REPO/duo-reviews/code-review.md" ]]; then
  REVIEW="
Start with the confirmed findings in duo-reviews/code-review.md: verify each
against the code, and plan fixes for those that still apply."
fi

mkdir -p -- "$REPO/duo-plans"

# Duo's exit status is kept: 0 agreed, 3 blocked, 4 turn or time limit.
exec "$DUO" \
  --repo "$REPO" \
  --output duo-plans/improvement-plan.md \
  --task "Plan improvements to this codebase.
Inspect the implementation, tests, documentation, and build and CI setup.
Cover correctness and robustness, missing tests, maintainability
(duplication, complexity, unclear interfaces), performance where it
can be measured, documentation that disagrees with the code, and
developer tooling.$REVIEW" \
  --criteria "For every item, provide:
the problem with file and line evidence, the proposed change,
the expected benefit, effort, risk, and how to verify it
(tests to add or run, measurements to compare).
Order the items so each step leaves the code working and tested;
put high-value, low-risk changes first and group the rest in phases.
Separate necessary fixes from optional improvements.
Keep public interfaces unless a change is justified; no rewrites
without evidence; no purely cosmetic changes.
State what was inspected and what could not be assessed." \
  --codex-model "$CODEX_MODEL" \
  --claude-model "$CLAUDE_MODEL" \
  --codex-effort high \
  --claude-effort high \
  --max-turns 8 \
  --max-attempts 2 \
  --max-seconds 3600 \
  --show-conversation

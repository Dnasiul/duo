#!/usr/bin/env bash
# Find the most relevant literature on a subject, with web access, and confirm
# every entry against its record (DOI, arXiv, or ISBN).
#
# Usage: search_literature.sh /path/to/repo "subject"
# Optional environment variables: DUO (an executable name or path, without
# arguments), BACKEND (native, the default, or copilot), CODEX_MODEL,
# CLAUDE_MODEL. Additional script arguments are rejected.
#
# This uses --allow-web, which is experimental: text a reviewer reads online can
# steer it into sending data out through queries and URLs. Read "Web access" in
# docs/SECURITY.md first. Point this at a folder that holds only literature
# notes, never unpublished data or drafts.
# - native (default): Codex and Claude Code can both search the web, but native
#   Codex can read any file your user can read, so only use it where nothing
#   private is reachable.
# - copilot: reviewers cannot read outside the folder, and they fetch pages
#   rather than search, through the scholarly APIs named in the task.
#
# Writes duo-literature/<slug>-<hash>.md inside the repository. The full subject
# determines the hash; rerunning the same subject extends or corrects its report.
# A .bib file or notes in the repository are taken into account. Duo does not
# download papers: fetch the ones you need yourself. Keep the companion
# search_literature_instructions.txt alongside this script.

set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf 'Usage: %s /path/to/repo "subject" (exactly two arguments required)\n' "$0" >&2
  exit 2
fi

REPO="$1"
SUBJECT="$2"
DUO="${DUO:-duo}"
BACKEND="${BACKEND:-native}"
case "$BACKEND" in
  native)
    CODEX_MODEL="${CODEX_MODEL:-gpt-6-astra}"
    CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-5-5}" ;;
  copilot)
    # Copilot model names use dots; check which models your plan offers.
    CODEX_MODEL="${CODEX_MODEL:-gpt-6-astra}"
    CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-5.5}" ;;
  *)
    printf 'BACKEND must be native or copilot, not: %s\n' "$BACKEND" >&2
    exit 2 ;;
esac

if [[ -z "${SUBJECT//[[:space:]]/}" ]]; then
  printf 'The subject must not be empty\n' >&2
  exit 2
fi

if [[ ! -d "$REPO" ]]; then
  printf 'Repository directory does not exist: %s\n' "$REPO" >&2
  exit 1
fi

# Git also succeeds with "false" for bare repositories and .git directories.
# Require a working tree before creating the output folder.
if [[ "$(git -C "$REPO" rev-parse --is-inside-work-tree 2>/dev/null)" != true ]]; then
  printf 'Not a Git working tree: %s\n' "$REPO" >&2
  exit 1
fi

# Only ASCII letters and digits are kept, byte by byte in the C locale, so the
# name is the same in every locale. The full subject's hash distinguishes
# punctuation, non-Latin text, and truncated names.
SLUG=$(printf '%s' "$SUBJECT" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -cs 'a-z0-9' '-' \
  | sed -e 's/^-*//' -e 's/-*$//' | cut -c1-60 | sed -e 's/-*$//')
SLUG="${SLUG:-subject}"
SUBJECT_HASH=$(printf '%s' "$SUBJECT" | git -C "$REPO" hash-object --stdin)
# CDPATH would make cd print the directory into SCRIPT_DIR.
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTRUCTIONS="$SCRIPT_DIR/search_literature_instructions.txt"
if [[ ! -f "$INSTRUCTIONS" ]]; then
  printf 'Missing %s; keep it next to this script\n' "$INSTRUCTIONS" >&2
  exit 1
fi

mkdir -p -- "$REPO/duo-literature"

# Duo's exit status is kept: 0 agreed, 3 blocked, 4 turn or time limit.
# --repo=PATH (not --repo PATH) keeps a path that starts with "-" a value.
exec "$DUO" \
  --repo="$REPO" \
  --mode report \
  --allow-web \
  --instructions-file "$INSTRUCTIONS" \
  --output "duo-literature/$SLUG-$SUBJECT_HASH.md" \
  --codex-backend "$BACKEND" \
  --claude-backend "$BACKEND" \
  --task "Find the most relevant literature on this subject: $SUBJECT

Cover review articles, foundational works, the most influential and the
most recent key papers, books and book chapters, and central datasets or
software. Search scholarly sources such as api.openalex.org,
api.crossref.org, export.arxiv.org, and api.semanticscholar.org (PubMed
E-utilities for biomedical subjects), and confirm every entry against its
DOI, arXiv, or ISBN record. If the repository holds a bibliography (.bib)
or notes, take them into account and mark the entries already there." \
  --criteria "Group the entries (reviews, foundational, recent advances, books,
other) and rank them by relevance within each group; aim for 20 to 40.
For each entry give authors, year, title, venue or publisher, DOI, arXiv
ID, or ISBN, the URL used to confirm it, the citation count with its
source and date when available, and one or two sentences on why it
matters for the subject.
Never invent a reference or identifier: mark anything without a
confirming record as unconfirmed. Distinguish preprints from
peer-reviewed work and flag retractions or corrections you find.
End with the search strategy (queries, sources, date), gaps and
suggested next searches, and BibTeX for the confirmed entries." \
  --codex-model "$CODEX_MODEL" \
  --claude-model "$CLAUDE_MODEL" \
  --codex-effort high \
  --claude-effort high \
  --max-turns 6 \
  --max-attempts 2 \
  --timeout 900 \
  --max-seconds 5400 \
  --show-conversation

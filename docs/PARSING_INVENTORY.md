# Parsing dependency inventory (plan §6 step 1)

Date: 2026-11-25

Scope: inventory only. This records current parsing dependencies on external CLI wording/shape; it does **not** implement section 6 steps 2-4.

Method: (1) plan-listed sites, (2) targeted grep/sweep of `src/duo` and `scripts/` for text/regex parsing over CLI-produced output, (3) provenance from `src/duo/policy.py` `TESTED_VERSIONS`, `tests/fixtures/README.md`, fixtures under `tests/fixtures/`, and parser tests (`tests/test_policy.py`, `tests/test_fixtures.py`, `tests/test_copilot.py`, `tests/test_duo.py`).

## Summary table

| Site | Classification |
| --- | --- |
| 1. `preferences_domain_absent` | SAFE REFUSAL |
| 2. `verify_claude_remote` | SAFE REFUSAL |
| 3a. `verify_version` (native preflight call site) | POTENTIAL UNSAFE ACCEPTANCE |
| 3b. `verify_version` (Copilot first-line selection before parse) | POTENTIAL UNSAFE ACCEPTANCE |
| 4a. `verify_controls` Codex help-flag substring checks | POTENTIAL UNSAFE ACCEPTANCE |
| 4b. `verify_controls` Codex `features list` first/last-token parsing | POTENTIAL UNSAFE ACCEPTANCE |
| 4c. `verify_controls` Claude help-flag substring checks | POTENTIAL UNSAFE ACCEPTANCE |
| 4d. `verify_controls` Claude `--output-format` stream-json regex pairing | SAFE REFUSAL |
| 5a. Copilot `options()` extraction + preflight `FLAGS` superset check | POTENTIAL UNSAFE ACCEPTANCE |
| 5b. Copilot `session_flags()` legacy-vs-`--sync-session` selection | SAFE REFUSAL |
| 6. `classify_failure` substring fragments + fallthrough | RETRY/COST CHANGE |
| 7a. `codex_error_text` extraction (`"Reconnecting"` skip included) | RETRY/COST CHANGE |
| 7b. `claude_error_text` extraction | RETRY/COST CHANGE |
| 7c. `claude_stream_error_text` extraction | RETRY/COST CHANGE |
| 8. `retry_delay` regex + workflow/model cap consumption | RETRY/COST CHANGE |
| 9. `copilot_reply` event-stream extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 10. `claude_result` terminal-envelope checks | SAFE REFUSAL |
| 11a. `scripts/check_drift.py` `flags()` extraction | POTENTIAL UNSAFE ACCEPTANCE (tooling-only detection gap) |
| 11b. `scripts/check_drift.py` `features()` extraction | POTENTIAL UNSAFE ACCEPTANCE (tooling-only detection gap) |
| 12. `scripts/record_fixtures.py` `version_of()` | SAFE REFUSAL (tooling-only) |

## 1) `preferences_domain_absent`

- **Location:** `src/duo/policy.py:128-136` (`preferences_domain_absent`), used by `check_claude_managed_files` at `src/duo/policy.py:172` and Copilot `check_sources` at `src/duo/copilot.py:76`.
- **What it parses:** regex `rf"Domain '?{re.escape(domain)}'? (?:does not exist|not found)"` with required `result.returncode == 1`.
- **Source and recorded versions:** macOS `/usr/bin/defaults read <domain>` stderr wording. Code comment records macOS 26 wording (`"Domain DOMAIN does not exist"`) and macOS 27 wording (`"Error: Domain 'DOMAIN' not found."`). Wording is pinned in `tests/test_policy.py` (`test_mac_preferences_must_be_absent`, including the macOS 27 string). `docs/STATUS.md` done table notes release 0.4.5 macOS 27 fix for native Claude and Copilot.
- **Fallback:** any non-matching stderr or non-`1` return code is treated as not-absent; callers raise `RunError` and stop preflight.
- **Classification if wording changes:** **SAFE REFUSAL**. Mismatch fails closed as "policy detected or unreadable."
- **Structured alternative:** none recorded in-repo for `defaults` output; only exit code + stderr text are used.

## 2) `verify_claude_remote`

- **Location:** `src/duo/policy.py:189-201`.
- **What it parses:** line prefix `"Managed settings (remote): "`; keeps `line[len(prefix):].strip()` for lines where `line.startswith(prefix)`, then allows exactly one of: `"none configured for this organization"`, `"not fetched — requires an Enterprise or Team subscription"`, `"not fetched — no usable credentials for the settings fetch"`.
- **Source and recorded versions:** Claude `doctor` output from fixtures `tests/fixtures/claude-2.1.296/doctor.txt` and `tests/fixtures/claude-2.1.330/doctor.txt`; fixture README states 2.1.330 evidence is Linux. `tests/test_policy.py` (`test_remote_policy_unknown_stale_and_loaded_fail_closed`) pins accepted/rejected strings.
- **Fallback:** if zero/multiple matching lines or unknown state, raises `RunError`.
- **Classification if wording changes:** **SAFE REFUSAL**.
- **Structured alternative:** none known in-repo; `doctor` is consumed as text.

## 3) Version parsing (`verify_version`)

### 3a) Native preflight call site

- **Location:** parser `src/duo/policy.py:112-125`; call site `src/duo/adapters.py:382-384`.
- **What it parses:** `re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)` anywhere in captured version text.
- **Source and recorded versions:** tested ranges in `src/duo/policy.py:100-104` (`codex` `0.162.1 <= v < 0.168.0`, `claude` `2.1.296 <= v < 2.2.0`, `copilot` `1.0.95 <= v < 1.1.0`). Fixture `version.txt` files exist for codex `0.162.1/0.163.0/0.164.0/0.165.0/0.166.0/0.167.0`, claude `2.1.296/2.1.330`, copilot `1.0.95/1.0.105`.
- **Fallback:** no semver match raises `RunError`; out-of-range raises unless `--allow-untested-cli`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (if an unrelated semver appears earlier in banner text, parser may classify version against the wrong triplet).
- **Structured alternative:** no structured `--version` contract is referenced in-repo.

### 3b) Copilot first-line selection before `verify_version`

- **Location:** `src/duo/copilot.py:216-218`.
- **What it parses:** `version_lines = probe(['--version']).splitlines(); version = version_lines[0] if version_lines else ''` before passing to `verify_version`.
- **Source and recorded versions:** Copilot fixture versions `tests/fixtures/copilot-1.0.95/version.txt`, `tests/fixtures/copilot-1.0.105/version.txt`; support transition to `--sync-session` recorded in `docs/STATUS.md` release 0.4.3 row and tests (`tests/test_copilot.py`).
- **Fallback:** empty output => parse failure => `RunError`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (version moved off first line can cause wrong triplet to be accepted/rejected).
- **Structured alternative:** none known in-repo.

## 4) `verify_controls` parsing dependencies

### 4a) Codex help-flag substring checks

- **Location:** `src/duo/policy.py:223-227`.
- **What it parses:** required substrings in `exec --help`: `"--sandbox"`, `"--ephemeral"`, `"--json"`, `"--output-schema"`, `"--ignore-rules"`, `"--ignore-user-config"`, `"--strict-config"` via `if any(flag not in help_text for flag in flags)`.
- **Source and recorded versions:** codex help fixtures (`tests/fixtures/codex-*/exec-help.txt`) and replay tests (`tests/test_fixtures.py`, `test_codex_controls_for_each_recorded_minor_version`). Fixture README records codex 0.164-0.166 captures on Linux and 0.167 on macOS.
- **Fallback:** missing substring => `RunError`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (free-text presence can be satisfied by non-option mentions).
- **Structured alternative:** no structured flag list is used in-repo.

### 4b) Codex `features list` first/last-token parsing

- **Location:** `src/duo/policy.py:228-233`.
- **What it parses:** per line with `len(parts) >= 3`, map `parts[0]` as feature name and `parts[-1]` as state; requires each `CODEX_DISABLED` feature to equal `"false"`.
- **Source and recorded versions:** codex feature fixtures (`tests/fixtures/codex-*/features.txt`), drift comparison helper uses same first-token rule (`scripts/check_drift.py:49-50`), and fixture replay tests.
- **Fallback:** missing feature or non-`"false"` => `RunError`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (column drift could preserve trailing `"false"` while state moves elsewhere).
- **Structured alternative:** no JSON/typed feature endpoint is used in-repo.

### 4c) Claude help-flag substring checks

- **Location:** `src/duo/policy.py:266-271`.
- **What it parses:** required substrings in `claude --help`: `"--safe-mode"`, `"--restricted"`, `"--setting-sources"`, `"--settings"`, `"--strict-mcp-config"`, `"--no-chrome"`, `"--disable-slash-commands"`, `"--tools"`, `"--allowedTools"`, `"--output-format"`, `"--verbose"`, `"--json-schema"`.
- **Source and recorded versions:** `tests/fixtures/claude-2.1.296/help.txt`, `tests/fixtures/claude-2.1.330/help.txt`, with compatibility evidence in fixture README and `docs/STATUS.md`.
- **Fallback:** missing substring => `RunError`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (same free-text false-positive risk as 4a).
- **Structured alternative:** none used in-repo.

### 4d) Claude `--output-format` help-block + `stream-json` pairing check

- **Location:** `src/duo/policy.py:274-281`.
- **What it parses:** block regex:
  - `r"(?m)^[ \t]*--output-format[ \t]+<[^>\n]+>[^\n]*(?:\n[ \t]{8,}\S[^\n]*)*"`
  - paired-value regex `r"(?<!\S)--output-format[ \t]+stream-json(?=\s|$)"`
  - accepted if either paired value exists or output-format block contains `\bstream-json\b`.
- **Source and recorded versions:** claude help fixtures above; rejection behavior pinned by `tests/test_policy.py:test_stream_json_must_be_an_output_format_value`.
- **Fallback:** if stream-json is not accepted as an output-format value, `RunError`.
- **Classification if wording changes:** **SAFE REFUSAL** (failure path blocks run before review).
- **Structured alternative:** no structured schema for help options in-repo.

## 5) Copilot option/session-flag parsing

### 5a) `options()` and preflight `FLAGS` superset check

- **Location:** `src/duo/copilot.py:32-34`, `src/duo/copilot.py:22-25`, and preflight check at `src/duo/copilot.py:220-222`.
- **What it parses:** `re.findall(r'--[a-z][a-z0-9-]*', help_text)`; requires superset of `FLAGS` (including `--stream`, `--output-format`, `--available-tools`, `--deny-tool`, `--mode`, etc.).
- **Source and recorded versions:** Copilot help fixtures `1.0.95` and `1.0.105`; tested in `tests/test_fixtures.py:test_copilot_help_and_config_match_required_controls` and whole-option behavior in `tests/test_copilot.py:test_help_options_match_whole_names`.
- **Fallback:** missing required option names => `RunError`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (token extraction from free-form help may include non-functional mentions).
- **Structured alternative:** none used in-repo.

### 5b) `session_flags()` legacy-vs-`--sync-session`

- **Location:** `src/duo/copilot.py:37-44`; consumed at preflight `src/duo/copilot.py:223` and command assembly `src/duo/copilot.py:174-187`.
- **What it parses:** if help options include both legacy flags (`"--no-remote"`, `"--no-remote-export"`), use both; else if `"--sync-session"` exists, use `"--sync-session=off"`; else fail.
- **Source and recorded versions:** fixture README and `docs/STATUS.md` release 0.4.3 note the 1.0.103 transition; fixtures include 1.0.95 and 1.0.105; behavior pinned in `tests/test_copilot.py:test_session_flags_follow_the_installed_copilot` and `test_review_uses_the_session_flags_of_the_installed_copilot`.
- **Fallback:** no recognized session-control option => `RunError`.
- **Classification if wording changes:** **SAFE REFUSAL**.
- **Structured alternative:** none known in-repo.

## 6) `classify_failure` (permanent fragments + fallthrough)

- **Location:** `src/duo/adapters.py:272-306`; called from runtime paths at `src/duo/adapters.py:527`, `567`, `571`.
- **What it parses:** lowercase substring membership against fragment table, including exact keys such as `"authentication failed"`, `"not logged in"`, `"invalid api key"`, `"unknown model"`, `"unrecognized_model"`, `"from --model flag is not available"`, `"unknown option"`, `"unexpected argument"`, `"error loading config"`, `"requirements.toml"`, `"error_max_turns"`, `"error_max_budget_usd"`. Fallthrough returns retryable `AgentError(..., retry_after=retry_delay(text))`.
- **Source and recorded versions:** codex/copilot/claude failure fixtures (`tests/fixtures/copilot-1.0.95/stderr-model-unavailable.txt`, `stderr-auth-failed.txt`, `tests/fixtures/claude-2.1.296/result-unrecognized-model.json`, `tests/fixtures/claude-2.1.330/events-maxturns.jsonl`) plus unit tests in `tests/test_fixtures.py` and `tests/test_duo.py`.
- **Fallback:** unmatched diagnostics become generic retryable failure; only explicit fragments become non-retryable categorized failures.
- **Classification if wording changes:** **RETRY/COST CHANGE** (typically changes retryability/user message, not approval gating).
- **Structured alternative:** partial today: native Codex/Claude have structured event/envelope fields upstream; Copilot path still depends on stderr text.

## 7) Structured error extraction (`adapters.py`)

### 7a) `codex_error_text`

- **Location:** `src/duo/adapters.py:248-266`.
- **What it parses:** JSONL tail events; collects:
  - `event.get("type") == "turn.failed"` then `event["error"]["message"]`
  - `event.get("type") == "error"` then `event["message"]` unless `message.startswith("Reconnecting")`.
- **Source and recorded versions:** codex event fixtures (`tests/fixtures/codex-*/events-approve.jsonl`) and synthetic reconnect test in `tests/test_duo.py` (includes `"Reconnecting... 1/5 (stream disconnected)"`).
- **Fallback:** malformed JSON lines are skipped; returns up to last 4 messages or empty string.
- **Classification if wording changes:** **RETRY/COST CHANGE** (classification signal quality changes).
- **Structured alternative:** already using structured Codex JSON events; remaining textual dependency is fragment matching in site 6.

### 7b) `claude_error_text`

- **Location:** `src/duo/adapters.py:154-161`.
- **What it parses:** result envelope fields: `subtype` plus `result` only when `is_error is True`.
- **Source and recorded versions:** claude fixtures `result-unrecognized-model.json` and 2.1.330 stream result events; tests in `tests/test_fixtures.py` and `tests/test_duo.py`.
- **Fallback:** non-dict envelope returns `""`.
- **Classification if wording changes:** **RETRY/COST CHANGE**.
- **Structured alternative:** already field-based (not raw stderr text) for native Claude.

### 7c) `claude_stream_error_text`

- **Location:** `src/duo/adapters.py:235-245`.
- **What it parses:** tail JSONL, keeps only events with `event.get("type") == "result"`, then delegates to `claude_error_text`.
- **Source and recorded versions:** claude stream fixtures (2.1.330 timeout/maxturns plus success streams) and replay tests in `tests/test_fixtures.py`.
- **Fallback:** malformed tail lines skipped; no result events => empty diagnostics.
- **Classification if wording changes:** **RETRY/COST CHANGE**.
- **Structured alternative:** already uses structured stream events; downstream decision still uses textual fragment classifier (site 6).

## 8) `retry_delay` regex and consumption/cap

- **Location:** parser `src/duo/adapters.py:309-326`; consumed by `classify_failure` at `src/duo/adapters.py:306`; bounded in `src/duo/models.py:90-91` (`MAX_RETRY_AFTER = 300.0`) and applied in `src/duo/workflow.py:213-219`.
- **What it parses:** `RETRY_AFTER` regex:
  - `r"(?:retry-after\s*[:=]\s*|(?:retry|try again) (?:in|after)\s+)(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?|h|hours?)?\b"`
  - unit normalization to seconds (`ms`, `milli*`, `h*`, `m*`, else seconds).
- **Source and recorded versions:** unit tests in `tests/test_duo.py:test_rate_limit_delay` cover representative phrases; no fixture claims tie this to one CLI/OS wording.
- **Fallback:** no match => `None` (default exponential backoff path).
- **Classification if wording changes:** **RETRY/COST CHANGE**.
- **Structured alternative:** for native Claude stream, structured retry delays exist (`rate_limit_event.retry_delay_ms` appears in 2.1.330 timeout fixtures), but runtime retry currently uses free-text extraction.

## 9) `copilot_reply` event-stream extraction

- **Location:** `src/duo/adapters.py:114-143`.
- **What it parses:** JSONL events where:
  - `event.get("type") == "assistant.message"`
  - `data["content"]` is non-empty string
  - `not data.get("toolRequests")`
  - `data.get("phase") in (None, "final_answer")`
  - keeps the last matching message, then `parse_json(..., allow_fence=True)`.
- **Source and recorded versions:** Copilot fixtures `tests/fixtures/copilot-1.0.95/events-gpt-revise.jsonl` (`phase: "final_answer"`) and `events-claude-approve.jsonl` (no `phase`), replayed by `tests/test_fixtures.py:test_copilot_event_streams`; fixture README documents this phase split.
- **Fallback:** malformed event lines skipped; if no qualifying message, raises `AgentError("Copilot returned no final answer")`.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (if event semantics drift so a non-terminal message still satisfies these predicates and contains schema-valid JSON, Duo can accept it as final).
- **Structured alternative:** currently uses structured JSON events already; no explicit terminal event contract is referenced in-repo beyond these predicates.

## 10) `claude_result` terminal-envelope checks

- **Location:** `src/duo/adapters.py:192-232`; consumed by `claude_attempt` at `src/duo/adapters.py:559-571`.
- **What it parses:** stream event object/type checks, exactly one terminal `"result"` event, last event must be `"result"`, and terminal envelope must have `subtype` as `str` and `is_error` as `bool`; success only when `terminal["subtype"] == "success"` and `terminal["is_error"] is False`.
- **Source and recorded versions:** claude stream fixtures in `tests/fixtures/claude-2.1.330/` and parser tests in `tests/test_claude_stream.py` plus replay tests in `tests/test_fixtures.py`.
- **Fallback:** any envelope/ordering mismatch raises `AgentError`.
- **Classification if wording changes:** **SAFE REFUSAL** (unknown subtype/state cannot produce success unless both explicit success conditions still hold).
- **Structured alternative:** this site already uses structured stream JSON fields rather than free text.

## 11) `scripts/check_drift.py` extraction (tooling, not runtime)

### 11a) `flags()`

- **Location:** `scripts/check_drift.py:42-46`; consumed in comparisons at `scripts/check_drift.py:99-111` and `118-119`.
- **What it parses:** regex `r"(?<![\w-])--[a-z][a-z0-9-]*"` over help text.
- **Source and recorded versions:** compares current help output to fixture baseline from first-tested versions (`fixture()` helper and fixture directories).
- **Fallback:** parse miss just changes diff output (`added`/`removed`) but does not stop runtime.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (tooling-only: can hide/help-mask drift, reducing detection before adoption).
- **Structured alternative:** none used; script depends on text help.

### 11b) `features()`

- **Location:** `scripts/check_drift.py:49-50`; consumed at `scripts/check_drift.py:112-114`.
- **What it parses:** first token from each line with at least 3 columns: `{line.split()[0] for ...}`.
- **Source and recorded versions:** compares against codex feature fixture baseline.
- **Fallback:** parsing drift affects only drift-report diffs.
- **Classification if wording changes:** **POTENTIAL UNSAFE ACCEPTANCE** (tooling-only visibility gap).
- **Structured alternative:** none used.

## 12) `scripts/record_fixtures.py` `version_of` (tooling)

- **Location:** `scripts/record_fixtures.py:129-133`.
- **What it parses:** `re.search(r"\d+\.\d+\.\d+", text)` from probe version output.
- **Source and recorded versions:** used to name fixture directories already present under `tests/fixtures/`.
- **Fallback:** no match exits with `SystemExit("Cannot parse version ...")`.
- **Classification if wording changes:** **SAFE REFUSAL** (fixture recording stops; runtime unaffected).
- **Structured alternative:** none used in recorder.

## Considered and excluded

- `src/duo/diversity.py:23-27` (`model_family`, `startswith("gpt-"/"claude-")`): parses Duo reviewer model identifiers from config/user args, not external CLI output.
- `src/duo/models.py:503-509` (`new_secret` + credential regexes): scans reviewer text/document content for secrets, not CLI status/help/errors.
- `src/duo/verdicts.py:436` (`message.lower().startswith(("interrupted", "terminated"))`): parses Duo-generated `error.json` message text to classify stored-run completion, not external CLI wording.
- `src/duo/workflow.py:36-38` (section-line match): validates reviewer payload locations against current subject document; not external CLI output.
- `src/duo/cli.py:121-127` (`spec.split(":")`): parses user CLI input (`--reviewer` spec), not agent CLI output.
- `src/duo/adapters.py` fenced JSON regex (`FENCED_JSON`) was reviewed and excluded from this inventory because it parses model answer wrapping, not control/error/policy wording from agent CLIs.

## Risk-ordered recommendations for section 6 steps 2-4

1. **Guarded parsing for potential fail-open sites (large):** add strict shape guards and negative checks for sites `3a`, `3b`, `4a`, `4b`, `4c`, `5a`, `9` (and tooling `11a/11b` for visibility). Prefer exact option/feature structures where available; otherwise require context-coupled matches (not free substrings) and emit explicit "unknown format" refusal.
2. **Retry/cost parsing hardening (medium):** refactor `6`, `7a`, `7b`, `7c`, `8` to prioritize structured fields before fragment text, and separate retry policy from human wording; add fixture-backed tests for unknown/shifted messages.
3. **Fixture retention/expansion (medium):** keep and extend fixtures for each risky parser path: additional Copilot event variants (`phase`/message ordering), Codex/Claude error variants, and version/help banners with extra preamble lines (sites `3*`, `4*`, `5*`, `9`).
4. **Drift-report extension (small/medium):** extend `scripts/check_drift.py` output to flag parser confidence/unknown-format states explicitly (sites `11a`, `11b`, plus references to runtime sites `4*`, `5*`) so wording drift is visible before release decisions.

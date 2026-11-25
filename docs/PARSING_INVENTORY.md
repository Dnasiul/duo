# Parsing dependency inventory (plan §6 step 1)

Date: 2026-11-25

Scope: inventory only. This records current parsing dependencies on external CLI wording/shape; it does **not** implement section 6 steps 2-4.

Method: plan-listed sites plus targeted review of `src/duo` and `scripts/` parser paths, with provenance from fixtures/tests (`tests/fixtures/`, `tests/test_policy.py`, `tests/test_copilot.py`, `tests/test_fixtures.py`, `tests/test_duo.py`, `tests/test_claude_stream.py`).

OS provenance note: many fixture artifacts (especially help/version/features/config probes) do not carry per-file OS metadata in-repo. Where no explicit source exists, this inventory states **OS: not recorded**. `tests/fixtures/README.md` also notes historical native probes were captured before the restricted-environment fix.

## Summary table

| Site | Classification |
| --- | --- |
| 1. `preferences_domain_absent` | SAFE REFUSAL |
| 2. `verify_claude_remote` | SAFE REFUSAL |
| 3a. `verify_version` (native preflight call site) | POTENTIAL UNSAFE ACCEPTANCE |
| 3b. Copilot first-line selection before `verify_version` | POTENTIAL UNSAFE ACCEPTANCE |
| 4a. `verify_controls` Codex help-flag substring checks | RETRY/COST CHANGE |
| 4b. `verify_controls` Codex `features list` first/last-token parsing | POTENTIAL UNSAFE ACCEPTANCE |
| 4c. `verify_controls` Claude help-flag substring checks | RETRY/COST CHANGE |
| 4d. `verify_controls` Claude `--output-format`/`stream-json` pairing | RETRY/COST CHANGE |
| 5a. Copilot `options()` extraction + required-option checks | RETRY/COST CHANGE |
| 5b. Copilot `session_flags()` legacy-vs-`--sync-session` selection | RETRY/COST CHANGE |
| 6. `classify_failure` substring fragments + fallthrough | RETRY/COST CHANGE |
| 7a. `codex_error_text` extraction | RETRY/COST CHANGE |
| 7b. `claude_error_text` extraction | RETRY/COST CHANGE |
| 7c. `claude_stream_error_text` extraction | RETRY/COST CHANGE |
| 8. `retry_delay` regex + workflow/model cap consumption | RETRY/COST CHANGE |
| 9. `copilot_reply` event-stream extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 10. `claude_result` terminal-envelope checks | SAFE REFUSAL |
| 11a. `scripts/check_drift.py` `flags()` extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 11b. `scripts/check_drift.py` `features()` extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 12. `scripts/record_fixtures.py` `version_of()` | SAFE REFUSAL |
| 13. `verify_controls` Codex `mcp list --json` must decode to `[]` | SAFE REFUSAL |
| 14. Copilot `config --list --json` effective-settings equality check | SAFE REFUSAL |
| 15. Claude local `remote-settings.json` cache must equal `{}` | SAFE REFUSAL |

## 1) `preferences_domain_absent`

- **Location:** `src/duo/policy.py:128-136`; used by native Claude and Copilot source checks (`src/duo/policy.py:172`, `src/duo/copilot.py:76`).
- **Parsing dependency:** regex over `defaults read` stderr plus required return code `1`.
- **Provenance:** wording pinned in `tests/test_policy.py:216` and `tests/test_copilot.py:344` (`test_mac_preferences_must_be_absent` in both domains). **OS:** macOS by command semantics.
- **Fallback:** non-match/non-`1` result refuses with `RunError`.
- **Classification:** **SAFE REFUSAL**.

## 2) `verify_claude_remote`

- **Location:** `src/duo/policy.py:189-201`.
- **Parsing dependency:** exact line prefix `Managed settings (remote): ` and allow-list state matching.
- **Provenance:** `tests/fixtures/claude-2.1.296/doctor.txt`, `tests/fixtures/claude-2.1.330/doctor.txt`, `tests/test_policy.py:194`. **OS:** not recorded.
- **Fallback:** zero/multiple/unknown states refuse.
- **Classification:** **SAFE REFUSAL**.

## 3) Version parsing (`verify_version`)

### 3a) Native preflight call site

- **Location:** parser `src/duo/policy.py:112-125`; call site `src/duo/adapters.py:382-384` using `version_text.strip()[:200]`.
- **Parsing dependency:** unanchored semver search `\b(\d+)\.(\d+)\.(\d+)\b` anywhere in the truncated text.
- **Provenance:** tested ranges in `src/duo/policy.py:100-104`; fixture `version.txt` probes across codex/claude/copilot. **OS:** not recorded.
- **Consequence chain:** wrong semver match can return `"tested"` (`policy.py:119-120`), so the CLI is omitted from `untested_cli` metadata (`adapters.py:427-428`) after preflight accepts (`adapters.py:383-384`, `404-405`). Controls are still checked independently; impact is limited to version gating.
- **Additional call sites:** same parser is used in `scripts/check_drift.py:94-96` and `101-103`; a wrong match there can report `"compatible"` and suppress the `::warning` path (`check_drift.py:123`, `162-164`).
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

### 3b) Copilot first-line selection before `verify_version`

- **Location:** `src/duo/copilot.py:216-218`.
- **Parsing dependency:** only `version_lines[0]` is passed to `verify_version`.
- **Provenance:** `tests/fixtures/copilot-1.0.95/version.txt`, `tests/fixtures/copilot-1.0.105/version.txt`. **OS:** not recorded.
- **Consequence chain:** if the real version moves off line 1, preflight usually refuses safely (empty/no-match or out-of-range). Fail-open requires line 1 to include a **different** in-range semver (example: update banner text) that makes `verify_version` return `"tested"`; then the same version-gate chain as 3a applies (`policy.py:119-120`, `adapters.py:383-384`, `404-405`, `427-428`).
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

## 4) `verify_controls` parsing dependencies

### 4a) Codex help-flag substring checks

- **Location:** `src/duo/policy.py:223-227`.
- **Parsing dependency:** required flags are checked via substring presence in `exec --help`.
- **Provenance:** `tests/fixtures/codex-*/exec-help.txt`; fixture replay in `tests/test_fixtures.py`. **OS:** not recorded for these help files.
- **Consequence chain:** false match requires stale prose mention after option removal. Duo then passes the removed runtime option (`src/duo/adapters.py:439-445`), the CLI rejects it, exits non-zero, and `classify_failure` (`adapters.py:520-527`) raises `AgentError`: non-retryable when text still includes `"unknown option"`/`"unexpected argument"`, else retried up to `max_attempts` and workflow ends in status `error` (`workflow.py:207-209`). `validate_payload` is never reached.
- **Excluded behavioral risk:** a hidden alias that still accepts/ignores a removed flag would be a CLI enforcement change, outside wording-classification scope; advertised-option checks cannot generally detect that (`policy.py:217-220`).
- **Classification:** **RETRY/COST CHANGE**.

### 4b) Codex `features list` first/last-token parsing

- **Location:** `src/duo/policy.py:228-233`.
- **Parsing dependency:** dict-comprehension over `line.split()` with `parts[0]` feature name and `parts[-1]` state.
- **Provenance:** `tests/fixtures/codex-*/features.txt`; replay tests and drift helper reuse (`scripts/check_drift.py:49-50`). **OS:** not recorded.
- **Consequence chain:** potential unsafe acceptance requires both:
  1. a row where an added trailing column carries `"false"` while the effective-state column is `"true"`, and
  2. a later duplicate row/section with the same feature name (last row wins because of dict overwrite at `policy.py:229-230`).
  Then managed/system config can effectively re-enable a feature despite `-c features.X=false`, preflight passes, and `verified_disabled_features` (`policy.py:254`) is recorded falsely.
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

### 4c) Claude help-flag substring checks

- **Location:** `src/duo/policy.py:266-271`.
- **Parsing dependency:** required option names are checked by substring presence in `claude --help`.
- **Provenance:** `tests/fixtures/claude-2.1.296/help.txt`, `tests/fixtures/claude-2.1.330/help.txt`; fixture replay tests. **OS:** not recorded for these help files.
- **Consequence chain:** same shape as 4a; removed runtime option still mentioned in prose passes preflight, then runtime call passes the option (`claude_options`, `policy.py:89-92`; call at `policy.py:283`), CLI rejects, failure classification/retry path occurs, and no path reaches `validate_payload`.
- **Excluded behavioral risk:** hidden accepted aliases are enforcement changes outside wording standard.
- **Classification:** **RETRY/COST CHANGE**.

### 4d) Claude `--output-format` help-block + `stream-json` pairing

- **Location:** `src/duo/policy.py:274-281`.
- **Parsing dependency:** regex pairing accepts either explicit `--output-format stream-json` mention or `stream-json` inside the `--output-format` help block.
- **Provenance:** claude help fixtures and `tests/test_policy.py:180`. **OS:** not recorded for help fixtures.
- **Consequence chain:** prose example text like `claude -p --output-format stream-json` can satisfy the regex even if the real value is unsupported; runtime later rejects the value or `claude_result` rejects output, producing late failure.
- **Classification:** **RETRY/COST CHANGE**.

## 5) Copilot option/session-flag parsing

### 5a) `options()` extraction + required-option checks

- **Location:** `src/duo/copilot.py:22-25`, `32-34`, `220-228`.
- **Parsing dependency:** `options()` uses regex token extraction from help and is reused for required `FLAGS`, `--allow-all-urls`, and `--reasoning-effort`.
- **Provenance:** `tests/fixtures/copilot-1.0.95/help.txt`, `tests/fixtures/copilot-1.0.105/help.txt`, `tests/test_fixtures.py:98`, `tests/test_copilot.py:244`. **OS:** not recorded.
- **Consequence chain:** same as 4a/4c. False match from stale prose passes preflight; runtime includes removed options in argv (`src/duo/copilot.py:179-188`, plus `--disable-mcp-server` when workspace MCP servers exist). CLI argument parsing rejects and exits non-zero; `classify_failure` (`adapters.py:520-527`) decides non-retryable if unknown-option text remains, otherwise retries to `max_attempts` then workflow status `error` (`workflow.py:207-209`). No path reaches `validate_payload`.
- **Fixture gap:** no fixture currently records unknown-option rejection from Copilot CLI.
- **Step-3 fixture proposal:** add one fixture attempt where Copilot rejects an unknown option (stderr and/or events) to pin this failure classification path.
- **Excluded behavioral risk:** hidden alias acceptance is enforcement behavior change outside wording standard.
- **Classification:** **RETRY/COST CHANGE**.

### 5b) `session_flags()` legacy-vs-`--sync-session`

- **Location:** selector `src/duo/copilot.py:37-44`; consumed by preflight and command assembly (`223`, `185`).
- **Parsing dependency:** if help lists both legacy names, they are preferred over `--sync-session`.
- **Provenance:** fixtures `copilot-1.0.95` and `copilot-1.0.105`; migration context in `docs/STATUS.md` (1.0.103 change).
- **Consequence chain:** if help lists `--sync-session` and also mentions legacy names in prose (e.g., deprecation text), `session_flags()` can choose legacy flags; runtime passes removed flags; CLI rejects at run time (late failure, same failure-classifier path as 5a).
- **Classification:** **RETRY/COST CHANGE**.

## 6) `classify_failure` (permanent fragments + fallthrough)

- **Location:** `src/duo/adapters.py:272-306`; callers at `527`, `567`, `571`.
- **Parsing dependency:** lowercase fragment matching for permanent categories; unmatched text falls through to retryable `AgentError(... retry_after=retry_delay(text))`.
- **Provenance:** fixture/model-error tests in `tests/test_fixtures.py` and `tests/test_duo.py`. **OS:** not recorded.
- **Classification:** **RETRY/COST CHANGE**.

## 7) Structured error extraction (`adapters.py`)

### 7a) `codex_error_text`

- **Location:** `src/duo/adapters.py:248-266`.
- **Parsing dependency:** JSONL event filtering on `turn.failed` and `error`; `"Reconnecting..."` messages are intentionally skipped.
- **Provenance:** codex event fixtures plus reconnect tests. **OS:** not recorded.
- **Classification:** **RETRY/COST CHANGE**.

### 7b) `claude_error_text`

- **Location:** `src/duo/adapters.py:154-161`.
- **Parsing dependency:** structured envelope fields (`subtype`, optional `result` when `is_error`).
- **Provenance:** claude result fixtures/tests. **OS:** not recorded.
- **Classification:** **RETRY/COST CHANGE**.

### 7c) `claude_stream_error_text`

- **Location:** `src/duo/adapters.py:235-245`.
- **Parsing dependency:** tail scan for `type == "result"` events then delegation to `claude_error_text`.
- **Provenance:** claude stream fixtures/tests. **OS:** not recorded.
- **Classification:** **RETRY/COST CHANGE**.

## 8) `retry_delay` regex and consumption/cap

- **Location:** parser `src/duo/adapters.py:309-326`; consumer `classify_failure` (`306`); cap handling in `src/duo/models.py:90-91` and retry loop `src/duo/workflow.py:213-219`.
- **Parsing dependency:** free-text regex for retry-after phrases and units.
- **Provenance:** unit tests plus timeout fixtures `tests/fixtures/claude-2.1.330/events-timeout-noweb.jsonl` and `events-timeout-web.jsonl`.
- **Structured-alternative correction:** those timeout fixtures carry structured backoff as `{"type":"system","subtype":"api_retry","retry_delay_ms":...}` (attempts 1..10), and represent Claude Code internal API retries inside one CLI call (streams that timed out without terminal `result`). They are **not** direct Duo sleep instructions, so they do not simply replace Duo `RETRY_AFTER` parsing.
- **Rate-limit fixture note:** `scripts/record_fixtures.py:85-86` strips `rate_limit_info`; remaining `rate_limit_event` entries in recorded fixtures retain only high-level fields (for example `type`, `uuid`, `session_id`).
- **Classification:** **RETRY/COST CHANGE**.

## 9) `copilot_reply` event-stream extraction

- **Location:** `src/duo/adapters.py:114-143`.
- **Parsing dependency:** selects last `assistant.message` with non-empty `content`, no `toolRequests`, and `phase` in `(None, "final_answer")`.
- **Provenance:** Copilot event fixtures and replay tests. **OS:** not recorded.
- **Consequence chain:** trigger is wording-level field rename in `toolRequests` or `phase` key/value. Unsafe acceptance additionally needs either (a) the qualifying non-final message appears after the true final answer, or (b) no final answer exists, CLI exits `0`, and that selected message parses as schema-valid JSON passing `validate_payload` (`adapters.py:529`) and `validate_review`.
- **Structured alternative present but unused:** both `copilot-1.0.95` event fixtures end with `assistant.turn_end`, `assistant.idle`, and terminal `{"type":"result","exitCode":0,...}` events; `copilot_reply` does not currently require that terminal-event contract.
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

## 10) `claude_result` terminal-envelope checks

- **Location:** `src/duo/adapters.py:192-232`, consumed at `559-571`.
- **Parsing dependency:** structured stream validation requires exactly one terminal `result`, no trailing events, and typed `subtype`/`is_error` envelope before success acceptance.
- **Provenance:** claude stream fixtures and `tests/test_claude_stream.py`. **OS:** not recorded.
- **Classification:** **SAFE REFUSAL**.

## 11) `scripts/check_drift.py` extraction (tooling)

### 11a) `flags()`

- **Location:** `scripts/check_drift.py:45-46`; consumed in comparisons at `99-111`, `118-119`.
- **Parsing dependency:** regex extraction of help option tokens.
- **Consequence chain:** if a removed flag remains in prose, it remains in `flags()` output and no `"removed"` entry appears in the drift report. A drift report showing no change is a qualifying decision artifact.
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

### 11b) `features()`

- **Location:** `scripts/check_drift.py:49-50`; consumed at `112-114`.
- **Parsing dependency:** keeps only rows with at least 3 tokens and compares **feature names only**.
- **Consequence chain:** if layout becomes two-column, new feature names are dropped and never reported as `"added"`. State transitions are never compared, which is a design visibility gap (not a wording parser issue).
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

## 12) `scripts/record_fixtures.py` `version_of()` (tooling)

- **Location:** `scripts/record_fixtures.py:129-133`.
- **Parsing dependency:** unanchored `\d+\.\d+\.\d+` match from version text.
- **Consequence chain:** banner/update text can be misparsed as version, causing fixture writes to wrong `<cli>-<version>` directory; `write()` overwrites by filename (`record_fixtures.py:123-126`), potentially replacing first-tested baseline files that `check_drift.fixture()` reads (`check_drift.py:58-60`). `record_attempt` targeting (`record_fixtures.py:189`) also depends on this parser. Runtime reviewer decisions are unaffected; only fixture-commit review catches it.
- **Classification:** **SAFE REFUSAL**.

## 13) Codex MCP list shape/equality check

- **Location:** `src/duo/policy.py:240-249`.
- **Parsing dependency:** `mcp list --json` must decode and equal `[]`.
- **Provenance:** codex `mcp-list.json` fixtures and policy replay tests. **OS:** not recorded for probe files.
- **Fallback:** decode/shape/value mismatch refuses.
- **Classification:** **SAFE REFUSAL**.

## 14) Copilot effective-settings JSON equality check

- **Location:** `src/duo/copilot.py:229-234`.
- **Parsing dependency:** `config --list --json` must decode to `dict` and every key in `SETTINGS` must match exact expected value.
- **Provenance:** `tests/fixtures/copilot-1.0.105/config.json`, replay in `tests/test_fixtures.py:98`. **OS:** not recorded.
- **Fallback:** invalid JSON/shape/value mismatch refuses.
- **Caveat (limitation):** fixture `copilot-1.0.105/config.json` exactly equals Duo `SETTINGS`, suggesting CLI echoing. If a key is renamed while old key is still echoed, check could pass while setting is ignored; this is a config-schema/enforcement behavior change outside wording parsing.
- **Classification:** **SAFE REFUSAL**.

## 15) Claude local managed-cache JSON equality check

- **Location:** `src/duo/policy.py:175-184`.
- **Parsing dependency:** `remote-settings.json` must decode exactly to `{}`.
- **Provenance:** behavior pinned by `tests/test_policy.py:206` (`test_managed_files_and_unreadable_policy_rejected`). **OS:** file format is platform-agnostic; fixture OS not recorded.
- **Fallback:** unreadable/non-empty/non-object refuses.
- **Classification:** **SAFE REFUSAL**.

## Considered and excluded

- `scripts/smoke_inspection.py` parser helpers (`claude_denials`, `tool_calls`; `192-224`, `227-249`) parse event shapes only for diagnostic printing. Pass/fail and READ/not-read outcomes are decided by phrase presence in response/audit (`98-103`, `175-182`), so key renames can hide details but do not change decision results.
- `scripts/record_fixtures.py:65-74` (`copilot_event`) repeats `copilot_reply` filtering for fixture retention: if keys like `toolRequests`/`phase` are renamed, stored final-answer content may be trimmed to 300 chars, causing fixture replay failures (loud tooling failure, not silent runtime acceptance).
- `scripts/record_fixtures.py:41,50` `REQUEST_ID` scrub regex is wording-dependent. If `"Request ID:"` format changes, request IDs can remain in committed fixtures and current fixture tests do not check for them. **Disposition:** tooling privacy-gap risk; outside runtime review decision classification.
- `src/duo/diversity.py:23-27` and `src/duo/models.py:503-509` parse Duo-controlled identifiers/content, not external CLI control/error wording.

## Risk-ordered recommendations for section 6 steps 2-4

1. **Site 4b (small):** parse feature state from a known column layout and refuse unknown/duplicate feature rows.
2. **Sites 3a/3b (small):** match each CLI’s exact version line format (`codex-cli X`, `X (Claude Code)`, `GitHub Copilot CLI X`) and refuse when more than one version number appears.
3. **Site 9 (small-medium):** require a terminal result event contract before accepting assistant content.
4. **Sites 11a/11b (small):** improve drift visibility (report parser confidence/unknown-format and include state-aware feature diffs).
5. **Retry/cost group (medium):** sites `6`, `7a`, `7b`, `7c`, `8`, `4a`, `4c`, `5a`, `4d`, `5b` should prioritize structured fields before text fragments and separate retry policy from human wording.
6. **Fixture retention/expansion (small/medium):** add fixture-backed tests for unknown/shifted messages, Copilot field/key drift, and at least one unknown-option rejection fixture (requested in 5a) to pin the late-failure classification path.

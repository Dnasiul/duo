# Parsing dependency inventory (plan §6 step 1)

Date: 2026-11-25. Updated 2026-11-27: site 9's final-answer contract (current `plan.md` §6 step 1) closes its unsafe-acceptance chains; it is reclassified RETRY/COST CHANGE. Sites 11a/11b (current §6 step 2) are reclassified SAFE REFUSAL; no site remains classified POTENTIAL UNSAFE ACCEPTANCE. Sites 3a, 3b and 12 now parse each CLI's own anchored version line (current §6 step 3), which closes their single-wrong-triple residuals. Current §6 step 4 moves retry decisions to one table of failure categories (`FAILURE_ATTEMPTS`, `src/duo/models.py:76-97`), lets structured fields decide before wording, and limits failures of the CLI's output format to one retry; site 4a is reclassified SAFE REFUSAL.

Scope: inventory with in-cycle remediation notes. This records current parsing dependencies on external CLI wording/shape and notes the minimal fail-closed guards landed in this cycle for sites 3a/3b and 4b, and a partial guard for site 9.

Method: plan-listed sites plus targeted review of `src/duo` and `scripts/` parser paths, with provenance from fixtures/tests (`tests/fixtures/`, `tests/test_policy.py`, `tests/test_copilot.py`, `tests/test_fixtures.py`, `tests/test_duo.py`, `tests/test_claude_stream.py`).

OS provenance note: many fixture artifacts (especially help/version/features/config probes) do not carry per-file OS metadata in-repo. Where no explicit source exists, this inventory states **OS: not recorded**. Recorded facts: `tests/fixtures/claude-2.1.330/doctor.txt` line 5 is `Platform: linux-x64` and `tests/fixtures/claude-2.1.296/doctor.txt` line 5 is `Platform: darwin-arm64` (macOS); `tests/fixtures/README.md` records `claude-2.1.330` as Linux capture with restricted probes, Codex 0.164.0-0.166.0 event streams as Linux and Codex 0.167.0 as macOS (site 7a). Historical fixtures predate the restricted-native-probe recorder fix **except** `claude-2.1.330`, which was captured after that fix.

## Summary table

| Site | Classification |
| --- | --- |
| 1. `preferences_domain_absent` | SAFE REFUSAL |
| 2. `verify_claude_remote` | SAFE REFUSAL |
| 3a. `verify_version` (native preflight call site) | SAFE REFUSAL |
| 3b. Copilot first-line selection before `verify_version` | SAFE REFUSAL |
| 4a. `verify_controls` Codex help-flag substring checks | SAFE REFUSAL |
| 4b. `verify_controls` Codex `features list` first/last-token parsing | SAFE REFUSAL |
| 4c. `verify_controls` Claude help-flag substring checks | RETRY/COST CHANGE |
| 4d. `verify_controls` Claude `--output-format`/`stream-json` pairing | RETRY/COST CHANGE |
| 5a. Copilot `options()` extraction + required-option checks | RETRY/COST CHANGE |
| 5b. Copilot `session_flags()` legacy-vs-`--sync-session` selection | RETRY/COST CHANGE |
| 6. `classify_failure` substring fragments + fallthrough | RETRY/COST CHANGE |
| 7a. `codex_error_text` extraction | RETRY/COST CHANGE |
| 7b. `claude_error_text` extraction | RETRY/COST CHANGE |
| 7c. `claude_stream_error_text` extraction | RETRY/COST CHANGE |
| 8. `retry_delay` regex + workflow/model cap consumption | RETRY/COST CHANGE |
| 9. `copilot_reply` event-stream extraction | RETRY/COST CHANGE |
| 10. `claude_result` terminal-envelope checks | SAFE REFUSAL |
| 11a. `scripts/check_drift.py` help-option extraction | SAFE REFUSAL |
| 11b. `scripts/check_drift.py` `features list` extraction | SAFE REFUSAL |
| 12. `scripts/record_fixtures.py` `version_of()` | SAFE REFUSAL |
| 13. `verify_controls` Codex `mcp list --json` must decode to `[]` | SAFE REFUSAL |
| 14. Copilot `config --list --json` effective-settings equality check | SAFE REFUSAL |
| 15. Claude local `remote-settings.json` cache must equal `{}` | SAFE REFUSAL |

## 1) `preferences_domain_absent`

- **Location:** `src/duo/policy.py:165-173`; used by native Claude and Copilot source checks (`src/duo/policy.py:209`, `src/duo/copilot.py:76`).
- **Parsing dependency:** regex over `defaults read` stderr plus required return code `1`.
- **Provenance:** wording pinned in `tests/test_policy.py:307` and `tests/test_copilot.py:379` (`test_mac_preferences_must_be_absent` in both domains). **OS:** macOS by command semantics.
- **Fallback:** non-match/non-`1` result refuses with `RunError`.
- **Classification:** **SAFE REFUSAL**.

## 2) `verify_claude_remote`

- **Location:** `src/duo/policy.py:226-238`.
- **Parsing dependency:** exact line prefix `Managed settings (remote): ` and allow-list state matching.
- **Provenance:** `tests/fixtures/claude-2.1.296/doctor.txt`, `tests/fixtures/claude-2.1.330/doctor.txt`, `tests/test_policy.py:285`. **OS:** Linux for `claude-2.1.330` (`doctor.txt` line 5 is `Platform: linux-x64`); macOS for `claude-2.1.296` (its `doctor.txt` line 5 is `Platform: darwin-arm64`).
- **Fallback:** zero/multiple/unknown states refuse.
- **Classification:** **SAFE REFUSAL**.

## 3) Version parsing (`verify_version`)

### 3a) Native preflight call site

- **Location:** parser `parse_version`, `src/duo/policy.py:126-149`, with `VERSION_LINES`; range check `verify_version`, `152-162`; native call site `src/duo/adapters.py:505-509`.
- **Parsing dependency:** the first non-empty line of `--version` output must be, in full, the CLI's own version line: `codex-cli X.Y.Z`, `X.Y.Z (Claude Code)`, or `GitHub Copilot CLI X.Y.Z` with an optional trailing period. Any other `X.Y.Z` anywhere in the output must equal that version.
- **Provenance:** tested ranges in `src/duo/policy.py:114-118`; fixture `version.txt` probes across codex/claude/copilot, and live Codex 0.172.0, Claude Code 2.1.332 and Copilot CLI 1.0.116 output (`tests/test_policy.py`, `test_version_parser_requires_each_clis_own_format`). **OS:** Linux for the `claude-2.1.330` probes and the live output, macOS for `claude-2.1.296`; not recorded for the rest.
- **Consequence chain (historical):** a wrong semver match could return `"tested"` and suppress `untested_cli` recording after preflight acceptance. 0.8.1 refused output with more than one distinct triple; a single triple that was not the CLI's own version, or a second triple beyond the first 200 characters, was still accepted.
- **Remediation (current `plan.md` §6 step 3):** a foreign or bare version, a banner before the version line, prefixes, suffixes, and pre-release or build tags all refuse with `Cannot parse … unrecognized version output`; a different version elsewhere refuses as ambiguous. `--allow-untested-cli` does not cover an unparseable version. The native call site now checks the whole output and only records its first 200 characters.
- **Residual accepted:** a CLI whose own version line names a version other than the installed one is outside wording classification.
- **Additional call sites:** `scripts/check_drift.py` uses the same parser for native CLIs (`verify_version`, line 243), Copilot (through preflight, line 234) and baseline selection (`parse_version`, line 258), so unrecognized output is a probe failure rather than `"compatible"` (`test_unrecognized_version_output_is_a_probe_failure`). `version_tuple` (`142-146`) now accepts only a bare `X.Y.Z`, for baseline entries and `--review`.
- **Classification:** **SAFE REFUSAL**.

### 3b) Copilot first-line selection before `verify_version`

- **Location:** `src/duo/copilot.py:216-219`.
- **Parsing dependency:** the full stripped `--version` output is verified (site 3a); its first line, the anchored version line, is the recorded version string.
- **Provenance:** `tests/fixtures/copilot-1.0.95/version.txt`, `tests/fixtures/copilot-1.0.105/version.txt`, `tests/fixtures/copilot-1.0.116/version.txt`. **OS:** Linux for 1.0.116; not recorded for the others.
- **Consequence chain (historical):** first-line-only parsing could accept a different in-range semver from banner/update text while the installed version was elsewhere in output.
- **Remediation:** 0.8.1 passed the full output to `verify_version`. Because the anchored parser requires the first line to be the version line, the recorded string can no longer be a banner.
- **Classification:** **SAFE REFUSAL**.

## 4) `verify_controls` parsing dependencies

### 4a) Codex help-flag substring checks

- **Location:** `src/duo/policy.py:260-262`.
- **Parsing dependency:** required flags are checked via substring presence in `exec --help`.
- **Provenance:** `tests/fixtures/codex-*/exec-help.txt`; fixture replay in `tests/test_fixtures.py`. **OS:** not recorded for these help files.
- **Consequence chain:** false match requires stale prose mention after option removal. Duo then passes the removed runtime option (`src/duo/adapters.py:564-576`), the CLI rejects it and exits non-zero, and `validate_payload` is never reached. Before current `plan.md` §6 step 4, `classify_failure` made that permanent only while the text still said `"unknown option"`/`"unexpected argument"`; other wording was retried up to `max_attempts`.
- **Remediation (current §6 step 4):** Codex's argument parser exits `2` before emitting any JSONL event (measured with codex-cli 0.172.0 on Linux: `codex exec --duo-bogus-option` exits 2 with empty stdout). `codex_usage_error` (`adapters.py:414-422`) treats exactly that as an `option` failure, permanent whatever the wording (`test_codex_usage_error_is_permanent_whatever_its_wording`). Exit 2 after any event, or exit 1, still goes to wording. No model call is made before the rejection.
- **Excluded behavioral risk:** a hidden alias that still accepts/ignores a removed flag would be a CLI enforcement change, outside wording-classification scope; advertised-option checks cannot generally detect that (`policy.py:244-247`).
- **Classification:** **SAFE REFUSAL** (was RETRY/COST CHANGE).

### 4b) Codex `features list` first/last-token parsing

- **Location:** `src/duo/policy.py:263-279`.
- **Parsing dependency (historical):** `parts[0]` name + `parts[-1]` state from any row with `len(parts) >= 3`.
- **Provenance:** `tests/fixtures/codex-*/features.txt`; replay tests; the drift check's `feature_rows` (`scripts/check_drift.py:103-117`) shares the stage vocabulary (`CODEX_FEATURE_STAGES`). **OS:** not recorded.
- **Consequence chain (historical, independent OR paths):**
  1. **Chain (1):** a trailing column holding `"false"` (for example after an effective `"true"`) could satisfy `parts[-1]` alone; no duplicate row required.
  2. **Chain (2):** a repeated table where a later row says `"false"` could overwrite an earlier effective `"true"` through dict overwrite; no trailing column required.
  Either chain could make preflight pass and record `verified_disabled_features` falsely (`policy.py:300`).
- **In-cycle remediation:** rows now fail closed unless state is exactly `true|false`, middle tokens match the recorded stage vocabulary (`stable`, `experimental`, `deprecated`, `under development`, `removed`), and feature names are unique (duplicate row raises `RunError` naming the feature).
- **Residual (fail-closed):** the stage vocabulary is now itself a recorded-wording dependency. A future Codex stage name — even in a patch release inside a tested minor — makes every native Codex preflight refuse (the `RunError` names the unrecognized stage); `--allow-untested-cli` does not bypass it, and `check_drift.py` reports probe-failure. That is an availability cost in the safe direction, accepted for a control check: the table has no header row, so a column-count-only guard cannot distinguish the two-token stage `under development` from an added trailing column.
- **Classification:** **SAFE REFUSAL**.

### 4c) Claude help-flag substring checks

- **Location:** `src/duo/policy.py:312-314`.
- **Parsing dependency:** required option names are checked by substring presence in `claude --help`.
- **Provenance:** `tests/fixtures/claude-2.1.296/help.txt`, `tests/fixtures/claude-2.1.330/help.txt`; fixture replay tests. **OS:** Linux for `claude-2.1.330`; macOS for `claude-2.1.296`.
- **Consequence chain split:** preflight doctor probe is `probe([claude, *claude_options(settings), "doctor"])` (`policy.py:326`), so removal of any of `--safe-mode`, `--restricted`, `--setting-sources`, `--settings`, `--strict-mcp-config`, `--no-chrome`, `--disable-slash-commands` fails **at preflight** when `doctor` rejects the option (assumes unknown top-level options are rejected) and `probe()` raises `RunError` (`adapters.py:474-476`). Removal of `--tools`, `--allowedTools`, `--output-format`, `--verbose`, or `--json-schema` fails **late** on review argv (`adapters.py:577-583`), then `claude_attempt` classifies failure via `classify_failure` at `adapters.py:696` (not the Codex/Copilot path at `adapters.py:655`).
- **Cost bound (current §6 step 4):** Claude exits `1` for an unknown option, the code it also uses for other failures, so classification stays on the `"unknown option"` wording. The rejection happens before any model call (measured with Claude Code 2.1.332: exit 1, empty stdout), so a wording change costs up to `max_attempts` process starts and backoff, not model calls.
- **Excluded behavioral risk:** hidden accepted aliases are enforcement changes outside wording standard.
- **Classification:** **RETRY/COST CHANGE**.

### 4d) Claude `--output-format` help-block + `stream-json` pairing

- **Location:** `src/duo/policy.py:315-324`.
- **Parsing dependency:** regex pairing accepts either explicit `--output-format stream-json` mention or `stream-json` inside the `--output-format` help block.
- **Provenance:** claude help fixtures and `tests/test_policy.py:271`. **OS:** Linux for `claude-2.1.330`; macOS for `claude-2.1.296`.
- **Consequence chain:** prose example text like `claude -p --output-format stream-json` can satisfy the regex even if the real value is unsupported; runtime later rejects the value or `claude_result` rejects output, producing late failure.
- **Classification:** **RETRY/COST CHANGE**.

## 5) Copilot option/session-flag parsing

### 5a) `options()` extraction + required-option checks

- **Location:** `src/duo/copilot.py:22-25`, `32-34`, `220-228`.
- **Parsing dependency:** `options()` uses regex token extraction from help and is reused for required `FLAGS`, `--allow-all-urls`, and `--reasoning-effort`.
- **Provenance:** `tests/fixtures/copilot-1.0.95/help.txt`, `tests/fixtures/copilot-1.0.105/help.txt`, `tests/test_fixtures.py:98`, `tests/test_copilot.py:270`. **OS:** not recorded.
- **Consequence chain:** `--no-auto-update` is passed on every Copilot probe (`copilot.py:208`), so its removal fails at preflight probe. Other stale-prose false matches follow the same late-failure path as 4a/4c when review argv includes removed options (`copilot.py:179-190`, with `--reasoning-effort` at `189-190`) and runtime rejects them.
- **Cost bound (current §6 step 4):** Copilot CLI 1.0.116 rejects an unknown option with exit 1, empty stdout and `error: unexpected argument … found` on stderr, before any model call; as for 4c, the wording decides and a change costs process starts, not premium requests.
- **Fixture gap:** no fixture currently records unknown-option rejection from Copilot CLI.
- **Step-3 fixture proposal:** add one fixture attempt where Copilot rejects an unknown option (stderr and/or events) to pin this failure classification path.
- **Excluded behavioral risk:** hidden alias acceptance is enforcement behavior change outside wording standard.
- **Classification:** **RETRY/COST CHANGE**.

### 5b) `session_flags()` legacy-vs-`--sync-session`

- **Location:** selector `src/duo/copilot.py:37-44`; consumed by preflight and command assembly (`224`, `185`).
- **Parsing dependency:** if help lists both legacy names, they are preferred over `--sync-session`.
- **Provenance:** fixtures `copilot-1.0.95` and `copilot-1.0.105`; migration context in `docs/STATUS.md` (1.0.103 change).
- **OS:** not recorded.
- **Consequence chain:** if help lists `--sync-session` and also mentions legacy names in prose (e.g., deprecation text), `session_flags()` can choose legacy flags; runtime passes removed flags; CLI rejects at run time (late failure, same failure-classifier path as 5a).
- **Classification:** **RETRY/COST CHANGE**.

## 6) `classify_failure` (structured fields, wording fallback, retry table)

- **Location:** `src/duo/adapters.py:336-422` (`FAILURE_WORDING`, `CLAUDE_SUBTYPES`, `claude_failure`, `classify_failure`, `codex_usage_error`); callers at `655`, `696`, `701`; retry table `FAILURE_ATTEMPTS`, `src/duo/models.py:76-97`, applied by the retry loop `src/duo/workflow.py:210-217`.
- **Parsing dependency (since current `plan.md` §6 step 4):** each failure gets a category, and only `FAILURE_ATTEMPTS` decides how many attempts a category may use in one turn: `reply`, `transient` and `timeout` up to `max_attempts`; `stream` (output not in a recorded format, sites 9 and 10) two; `authentication`, `model`, `option`, `configuration`, `request`, `limit`, `local` and `permanent` one. A structured decision takes precedence: a Claude result envelope's `subtype` (`error_max_turns`, `error_max_budget_usd`) or `api_error_status` with `is_error: true` (401/403 authentication, 404 model, 408/409/425/429/5xx transient, other 4xx request), and Codex's usage-error exit (site 4a). Otherwise lowercase fragments in `FAILURE_WORDING` choose the category, and unmatched text is `transient`. `attempt_failed` events record the category.
- **Provenance:** `claude-2.1.296/result-unrecognized-model.json` (`api_error_status: 404`, subtype `success`, `is_error: true`) and `claude-2.1.330/events-maxturns.jsonl` (subtype `error_max_turns`) decide structurally (`tests/test_fixtures.py`, `test_claude_unrecognized_model`); other statuses follow HTTP semantics and are unit-tested (`test_structured_claude_fields_decide_before_wording`), without recordings. **OS:** Linux for `claude-2.1.330` and the Codex usage-error measurement; macOS for `claude-2.1.296`.
- **Consequence chain:** for Codex and Copilot runtime errors no structured field is recorded, so wording still decides; a reworded permanent error falls through to `transient` and is retried up to `max_attempts`. A wrongly structured Claude field can only change the retry count, never accept a review.
- **Classification:** **RETRY/COST CHANGE**.

## 7) Structured error extraction (`adapters.py`)

### 7a) `codex_error_text`

- **Location:** `src/duo/adapters.py:315-333`.
- **Parsing dependency:** JSONL event filtering on `turn.failed` and `error`; `"Reconnecting..."` messages are intentionally skipped.
- **Provenance:** codex event fixtures plus reconnect tests. **OS:** Linux for the Codex 0.164.0-0.166.0 streams, macOS for 0.167.0; not recorded for 0.162.1/0.163.0.
- **Classification:** **RETRY/COST CHANGE**.

### 7b) `claude_error_text`

- **Location:** `src/duo/adapters.py:219-226`; structured decision `claude_failure` (`372-391`).
- **Parsing dependency:** structured envelope fields (`subtype`, optional `result` when `is_error`). Since current §6 step 4, `subtype` and `api_error_status` decide before the text (site 6); `result` text is only the fallback.
- **Provenance:** claude result fixtures/tests. **OS:** Linux for the `claude-2.1.330` fixtures; macOS for the `claude-2.1.296` envelopes.
- **Classification:** **RETRY/COST CHANGE**.

### 7c) `claude_stream_error_text`

- **Location:** `src/duo/adapters.py:304-312` (`claude_tail_results`, `claude_stream_error_text`).
- **Parsing dependency:** tail scan for `type == "result"` events then delegation to `claude_error_text`. When the stream itself is invalid and the CLI exits non-zero, the last tail result's structured fields still decide first (`test_claude_failure_without_a_valid_stream_uses_its_last_result`).
- **Provenance:** claude stream fixtures/tests. **OS:** Linux for the `claude-2.1.330` fixtures; not recorded for earlier ones.
- **Classification:** **RETRY/COST CHANGE**.

## 8) `retry_delay` regex and consumption/cap

- **Location:** parser `src/duo/adapters.py:432-449`; consumer `classify_failure` (`410`); cap handling in `src/duo/models.py:136-137` and retry loop `src/duo/workflow.py:221-226`.
- **Parsing dependency:** free-text regex for retry-after phrases and units. Applied only to the `transient` category (site 6), including Claude's structured 429 and 5xx statuses; a permanent category never waits.
- **Provenance:** unit tests plus timeout fixtures `tests/fixtures/claude-2.1.330/events-timeout-noweb.jsonl` and `events-timeout-web.jsonl`.
- **OS:** Linux (`claude-2.1.330` fixture set).
- **Structured-alternative correction:** those timeout fixtures carry structured backoff as `{"type":"system","subtype":"api_retry","retry_delay_ms":...}` (attempts 1..10), and represent Claude Code internal API retries inside one CLI call (streams that timed out without terminal `result`). They are **not** direct Duo sleep instructions, so they do not simply replace Duo `RETRY_AFTER` parsing.
- **Rate-limit fixture note:** `scripts/record_fixtures.py:86-87` strips `rate_limit_info`; remaining `rate_limit_event` entries in recorded fixtures retain only high-level fields (for example `type`, `uuid`, `session_id`).
- **Classification:** **RETRY/COST CHANGE**.

## 9) `copilot_reply` event-stream extraction

- **Location:** `src/duo/adapters.py:114-208` (`copilot_final_candidate`, `copilot_recorded_message`, `COPILOT_PHASES`, `COPILOT_ACTIVITY`, `copilot_reply`).
- **Parsing dependency (since plan §6 step 1):** the answer is the **last** `assistant.message` event; it must be the stream's only final candidate (non-empty string `content`, a `toolRequests` field that is present and equal to `[]`, and `phase` absent or `"final_answer"`); if any message in the stream carries a `phase`, the answer must be `"final_answer"`; no `assistant.message`, `assistant.turn_start`, `user.message`, or `tool.*` event may follow it; at least one `result` event must follow it, every `result` event must have integer `exitCode == 0`, and none may precede it. Since current §6 step 4, every `assistant.message` must first be in the recorded format (an object with string `content`, a list `toolRequests`, and `phase` absent, `"commentary"` or `"final_answer"`), and the stream must contain at least one.
- **Provenance:** both `copilot-1.0.95` event fixtures replay to their recorded answers (`tests/test_fixtures.py`, `test_copilot_event_streams`): `events-claude-approve.jsonl` has no `phase` field, and `events-gpt-revise.jsonl` marks its answer `phase: "final_answer"` after a commentary message with tool requests. Refusal cases are mutations of those recordings (`tests/test_copilot.py`, `test_copilot_final_answer_contract_refusals`). **OS:** not recorded. No event fixture exists for Copilot 1.0.105 or later, and no live Copilot run has exercised this contract yet (monthly quota exhausted since 2026-11-25).
- **Former consequence chain:** a rename of the `toolRequests` or `phase` key or value made a non-final message qualify, and it was accepted when (a) it appeared after the true final answer, or (b) no final answer existed, the CLI exited `0`, and the message was schema-valid. A Claude-model stream (no `phase`) whose tool-calling message lost its `toolRequests` field and carried a schema-valid review was accepted by the 0.8.x parser; the regression test demonstrates it.
- **Chain status:** chain (a) is closed: any later message, a second final candidate, a new turn, user message, or tool event after the answer refuses, so a later message can never replace the genuine answer. Chain (b) is closed for the recorded trigger: a renamed `toolRequests` field disqualifies the message, a relabelled commentary message is refused by the tool work that follows it, and a phased stream whose last message lost its `"final_answer"` label refuses. The activity checks only add refusals, so renaming those event types removes a check but never causes an acceptance.
- **Residual:** acceptance of a non-final message would now need a message with `toolRequests: []` to be the last assistant message with no recognized activity after it, and a successful `result` after that. Inside one Copilot session, that is the final answer; reaching it otherwise requires renames of the activity event types together with a change in Copilot's session semantics.
- **New dependencies created by the remediation:** the field names `type: "result"`, `exitCode`, `toolRequests` and `phase`, and the recorded phase values, are load-bearing. Through §6 step 3, a rename made every Copilot reply raise a retryable `AgentError`, so each review repeated as full model calls (premium requests) up to `max_attempts` before the run errored.
- **Cost bound (current §6 step 4):** those format checks run before answer selection and raise the `stream` category: no assistant message, a message outside the recorded format, no `result` event, an unsuccessful one, or one before the answer. A `stream` failure is retried once (a damaged stream), and a second one in the turn ends the run with "the installed CLI's output format may have changed" (`test_unrecognized_cli_output_is_retried_once`). Answer-selection failures within the recorded format (no final candidate, more than one, a later message, continued work) stay `reply` and keep `max_attempts`. The refusal cases assert each category (`test_copilot_final_answer_contract_refusals`).
- **Classification:** **RETRY/COST CHANGE** (was POTENTIAL UNSAFE ACCEPTANCE through 0.8.x).

## 10) `claude_result` terminal-envelope checks

- **Location:** `src/duo/adapters.py:257-301`, consumed at `683-703`.
- **Parsing dependency:** structured stream validation requires exactly one terminal `result`, no trailing events, and typed `subtype`/`is_error` envelope before success acceptance. Since current §6 step 4 these failures, including a line that is not JSON, are the `stream` category (one retry); an invalid `structured_output` stays `reply`.
- **Provenance:** claude stream fixtures and `tests/test_claude_stream.py`. **OS:** Linux for the `claude-2.1.330` fixtures; macOS for the `claude-2.1.296` envelopes.
- **Classification:** **SAFE REFUSAL**.

## 11) `scripts/check_drift.py` extraction (tooling)

Since current `plan.md` §6 step 2, each CLI is compared with the latest **reviewed baseline** at or below its version: an entry in `tests/fixtures/drift-baselines.json` naming the fixture files and their SHA-256 digests (`load_baselines` and `baseline`, `scripts/check_drift.py:153-198`). A missing baseline, a file missing from it, or a digest mismatch (for example, a fixture re-recorded but not yet reviewed) is reported and the file is not used. Only `check_drift.py --review` changes the manifest (`record_review`, `311-325`), after `review_errors` (`284-308`) confirms recognized layouts, the options preflight requires, and, for Codex, the disabled features reported `false`. Any reason for review makes a tested version `needs-review` instead of `compatible` (`check`, `212-281`), and the weekly workflow treats every result other than `compatible` as needing action (`scripts/drift_report.py`).

### 11a) help-option extraction

- **Location:** `defined_flags`, `help_layout`, `compare_help` (`scripts/check_drift.py:72-96`); consumed in `check` at `264-277`.
- **Parsing dependency:** an option is **defined** when its line starts, indented at most 8 columns, with the option and any comma-separated aliases (`-c, --config`, `--allowedTools, --allowed-tools`); mentions elsewhere are prose. The layout is recognized when an `Options:` header exists and at least one option is defined. Long options may contain capitals (the earlier pattern read `--allowedTools` as `--allowed`).
- **Provenance:** every reviewed help fixture (`codex-*/exec-help.txt`, `claude-*/help.txt`, `copilot-*/help.txt`) is recognized and defines every option preflight requires (`tests/test_drift.py`, `test_committed_manifest_lists_reviewable_fixtures`).
- **Former consequence chain:** a removed option still named in prose stayed in the extracted set, so the report showed no removal. Now it is reported as `removed, still mentioned in prose` and needs review, even for an in-range version (`test_removed_option_still_named_in_prose_needs_review`).
- **Residual:** a wrapped description line indented 8 columns or less that begins with an option name would count as a definition. In the recorded layouts, wrapped descriptions are indented 10 or more columns, except Claude's alias-line descriptions at 6 columns, none of which begins with an option. Added options are reported but do not need review.
- **Classification:** **SAFE REFUSAL** (an unrecognized layout or any removal needs review; was POTENTIAL UNSAFE ACCEPTANCE).

### 11b) `features list` extraction

- **Location:** `feature_rows`, `compare_features` (`scripts/check_drift.py:103-133`); consumed in `check` at `264-277`.
- **Parsing dependency:** a row is `name stage state` with a stage from `CODEX_FEATURE_STAGES` (shared with preflight, `src/duo/policy.py`) and a state of `true` or `false`; any other nonblank line, or a repeated name, is unrecognized.
- **Former consequence chain:** rows with fewer than three columns were dropped, and only names were compared. Now unrecognized rows (including a two-column layout) need review, and the report lists added, removed, enabled, disabled, newly enabled, and stage-changed features. Removals, state changes, and features added enabled need review (`test_two_column_feature_list_needs_review`, `test_feature_state_changes_need_review`).
- **Residual:** stage changes and features added disabled are reported only. Preflight (`verify_controls`, site 4b) still skips rows with fewer than three columns; it refuses when any feature Duo disables is not reported `false`, so a two-column row for one of those refuses there.
- **Classification:** **SAFE REFUSAL** (was POTENTIAL UNSAFE ACCEPTANCE).

## 12) `scripts/record_fixtures.py` `version_of()` (tooling)

- **Location:** `scripts/record_fixtures.py:130-135`.
- **Parsing dependency:** Duo's anchored `parse_version` (site 3a) for the recorded CLI.
- **Consequence chain (historical):** an unanchored `\d+\.\d+\.\d+` match could misparse banner/update text as the version, writing fixtures to the wrong `<cli>-<version>` directory; `write()` overwrites by filename (`record_fixtures.py:124-127`), so files listed as reviewed drift baselines could be replaced (the drift check then reports their digest mismatch as needing review, `check_drift.py:175-198`). `record_attempt` targeting (`record_fixtures.py:191`) used the same parser.
- **Remediation (current `plan.md` §6 step 3):** output that is not the CLI's own version line, or that names a second version, stops recording with `SystemExit` before any file is written (`test_fixture_directory_uses_the_anchored_version_parser`). Fixture writes remain printed (`record_fixtures.py:127`) and committed fixtures remain reviewed.
- **Classification:** **SAFE REFUSAL**.

## 13) Codex MCP list shape/equality check

- **Location:** `src/duo/policy.py:286-295`.
- **Parsing dependency:** `mcp list --json` must decode and equal `[]`.
- **Provenance:** codex `mcp-list.json` fixtures and policy replay tests. **OS:** not recorded for probe files.
- **Fallback:** decode/shape/value mismatch refuses.
- **Classification:** **SAFE REFUSAL**.

## 14) Copilot effective-settings JSON equality check

- **Location:** `src/duo/copilot.py:230-235`.
- **Parsing dependency:** `config --list --json` must decode to `dict` and every key in `SETTINGS` must match exact expected value.
- **Provenance:** `tests/fixtures/copilot-1.0.95/config.json` and `tests/fixtures/copilot-1.0.105/config.json` (both equal `SETTINGS`), replay in `tests/test_fixtures.py:98`. **OS:** not recorded.
- **Fallback:** invalid JSON/shape/value mismatch refuses.
- **Caveat (limitation):** fixture `copilot-1.0.105/config.json` exactly equals Duo `SETTINGS`, suggesting CLI echoing. If a key is renamed while old key is still echoed, check could pass while setting is ignored; this is a config-schema/enforcement behavior change outside wording parsing.
- **Classification:** **SAFE REFUSAL**.

## 15) Claude local managed-cache JSON equality check

- **Location:** `src/duo/policy.py:212-221`.
- **Parsing dependency:** `remote-settings.json` must decode exactly to `{}`.
- **Provenance:** behavior pinned by `tests/test_policy.py:297` (`test_managed_files_and_unreadable_policy_rejected`). **OS:** file format is platform-agnostic; fixture OS not recorded.
- **Fallback:** unreadable/non-empty/non-object refuses.
- **Classification:** **SAFE REFUSAL**.

## Considered and excluded

- `scripts/smoke_inspection.py` parser helpers (`claude_denials`, `tool_calls`; `192-224`, `227-249`) parse event shapes only for diagnostic printing. Pass/fail and READ/not-read outcomes are decided by phrase presence in response/audit (`98-103`, `175-182`), so key renames can hide details but do not change decision results.
- `scripts/record_fixtures.py:66-75` (`copilot_event`) repeats `copilot_reply` filtering for fixture retention. If `toolRequests`/`phase` **keys** are renamed, they are missing, so `not data.get("toolRequests")` is true and `phase` is `None`: more messages are treated as final and kept whole (fixture bloat, no failure). The final answer is shortened to `LIMIT = 300` (`record_fixtures.py:34`) only when phase **values** change or the final message carries `toolRequests`; that value-change path then fails replay loudly when a trimmed answer exceeds 300 characters.
- `scripts/record_fixtures.py:42,51` `REQUEST_ID` scrub regex is wording-dependent. If `"Request ID:"` format changes, request IDs can remain in committed fixtures and current fixture tests do not check for them. **Disposition:** tooling privacy-gap risk; outside runtime review decision classification.
- `src/duo/diversity.py:23-27` and `src/duo/models.py:549-555` parse Duo-controlled identifiers/content, not external CLI control/error wording.

## Risk-ordered recommendations for section 6 steps 2-4

1. **Landed this cycle (site 4b):** unknown Codex feature layouts and duplicate feature rows now fail closed. Follow-up: move from the stage-vocabulary guard to a structured contract when upstream exposes one; Codex 0.172.0 `features list` still has no structured output option (checked for current `plan.md` §6 step 3).
2. **Sites 3a/3b/12:** 0.8.1 refused ambiguous multi-version output and parsed Copilot's full `--version` output. Current `plan.md` §6 step 3 then anchored every version parser to the CLI's own first line (`codex-cli X`, `X (Claude Code)`, `GitHub Copilot CLI X`); see site 3a.
3. **Site 9:** 0.8.1 required a `result` event with `exitCode == 0` somewhere in the stream. Plan §6 step 1 then bound acceptance to a final-answer contract (the last assistant message, the only final candidate, no agent activity after it, and successful `result` events after it), closing chains (a) and (b); see site 9.
4. **Sites 11a/11b:** done in current `plan.md` §6 step 2: defined-option and full-row parsing, unrecognized layouts reported, state-aware feature diffs, and reviewed, digest-verified baselines; see site 11.
5. **Retry/cost group:** current `plan.md` §6 step 4 separated retry policy from wording (`FAILURE_ATTEMPTS`), let Claude's `subtype`/`api_error_status` and Codex's usage-error exit decide before text (sites 4a, 6, 7b, 7c), and bounded site 9's field-name dependencies and site 10's stream checks to one retry. Remaining: Codex and Copilot runtime errors (sites 6, 7a) and Claude/Copilot option rejections (4c, 4d, 5a, 5b) have no recorded structured field, so wording still decides; the option rejections happen before any model call.
6. **Fixture retention/expansion (small/medium):** add fixture-backed tests for unknown/shifted messages, Copilot field/key drift, and at least one unknown-option rejection fixture (requested in 5a) to pin the late-failure classification path.

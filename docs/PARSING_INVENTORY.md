# Parsing dependency inventory (plan §6 step 1)

Date: 2026-11-25

Scope: inventory with in-cycle remediation notes. This records current parsing dependencies on external CLI wording/shape and notes the minimal fail-closed guards landed in this cycle for sites 3a/3b and 4b, and a partial guard for site 9.

Method: plan-listed sites plus targeted review of `src/duo` and `scripts/` parser paths, with provenance from fixtures/tests (`tests/fixtures/`, `tests/test_policy.py`, `tests/test_copilot.py`, `tests/test_fixtures.py`, `tests/test_duo.py`, `tests/test_claude_stream.py`).

OS provenance note: many fixture artifacts (especially help/version/features/config probes) do not carry per-file OS metadata in-repo. Where no explicit source exists, this inventory states **OS: not recorded**. Recorded facts: `tests/fixtures/claude-2.1.330/doctor.txt` line 5 is `Platform: linux-x64`; `tests/fixtures/README.md` records `claude-2.1.330` as Linux capture with restricted probes, Codex 0.164.0-0.166.0 event streams as Linux and Codex 0.167.0 as macOS (site 7a). Historical fixtures predate the restricted-native-probe recorder fix **except** `claude-2.1.330`, which was captured after that fix.

## Summary table

| Site | Classification |
| --- | --- |
| 1. `preferences_domain_absent` | SAFE REFUSAL |
| 2. `verify_claude_remote` | SAFE REFUSAL |
| 3a. `verify_version` (native preflight call site) | SAFE REFUSAL |
| 3b. Copilot first-line selection before `verify_version` | SAFE REFUSAL |
| 4a. `verify_controls` Codex help-flag substring checks | RETRY/COST CHANGE |
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
| 9. `copilot_reply` event-stream extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 10. `claude_result` terminal-envelope checks | SAFE REFUSAL |
| 11a. `scripts/check_drift.py` `flags()` extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 11b. `scripts/check_drift.py` `features()` extraction | POTENTIAL UNSAFE ACCEPTANCE |
| 12. `scripts/record_fixtures.py` `version_of()` | SAFE REFUSAL |
| 13. `verify_controls` Codex `mcp list --json` must decode to `[]` | SAFE REFUSAL |
| 14. Copilot `config --list --json` effective-settings equality check | SAFE REFUSAL |
| 15. Claude local `remote-settings.json` cache must equal `{}` | SAFE REFUSAL |

## 1) `preferences_domain_absent`

- **Location:** `src/duo/policy.py:131-139`; used by native Claude and Copilot source checks (`src/duo/policy.py:175`, `src/duo/copilot.py:76`).
- **Parsing dependency:** regex over `defaults read` stderr plus required return code `1`.
- **Provenance:** wording pinned in `tests/test_policy.py:264` and `tests/test_copilot.py:372` (`test_mac_preferences_must_be_absent` in both domains). **OS:** macOS by command semantics.
- **Fallback:** non-match/non-`1` result refuses with `RunError`.
- **Classification:** **SAFE REFUSAL**.

## 2) `verify_claude_remote`

- **Location:** `src/duo/policy.py:192-204`.
- **Parsing dependency:** exact line prefix `Managed settings (remote): ` and allow-list state matching.
- **Provenance:** `tests/fixtures/claude-2.1.296/doctor.txt`, `tests/fixtures/claude-2.1.330/doctor.txt`, `tests/test_policy.py:242`. **OS:** Linux for `claude-2.1.330` (`doctor.txt` line 5 is `Platform: linux-x64`); not recorded for `claude-2.1.296`.
- **Fallback:** zero/multiple/unknown states refuse.
- **Classification:** **SAFE REFUSAL**.

## 3) Version parsing (`verify_version`)

### 3a) Native preflight call site

- **Location:** parser `src/duo/policy.py:112-129`; call site `src/duo/adapters.py:391-393` using `version_text.strip()[:200]`.
- **Parsing dependency:** semver scan `\b(\d+)\.(\d+)\.(\d+)\b` across the supplied text.
- **Provenance:** tested ranges in `src/duo/policy.py:100-104`; fixture `version.txt` probes across codex/claude/copilot. **OS:** Linux for the `claude-2.1.330` probes; not recorded for the rest.
- **Consequence chain (historical):** wrong semver match could return `"tested"` and suppress `untested_cli` recording after preflight acceptance.
- **In-cycle remediation:** `verify_version` now collects all matched triples and refuses when more than one distinct version is present (`RunError` on ambiguous output). A single distinct triple (including repeats) still behaves as before.
- **Residuals accepted:** (1) output whose only triple is not the CLI's own version — the installed version absent from the output — is still accepted; the recorded CLIs always print their own version, so this requires a CLI that prints a foreign version and not its own. (2) The native call site truncates to `[:200]`, so a second distinct triple beyond character 200 is never seen; the Copilot path (3b) passes the full output and has no such truncation.
- **Additional call sites:** same parser is used in `scripts/check_drift.py:94-96` and `101-103`; ambiguous output now raises `RunError` there too, which `check_drift.py` reports as probe-failure/`::error` rather than `"compatible"`. A single wrong triple (residual 1) can still report `"compatible"` and suppress the `::warning` path (`check_drift.py:123`, `162-164`).
- **Classification:** **SAFE REFUSAL**.

### 3b) Copilot first-line selection before `verify_version`

- **Location:** `src/duo/copilot.py:216-219`.
- **Parsing dependency:** first line remains the recorded/displayed version string.
- **Provenance:** `tests/fixtures/copilot-1.0.95/version.txt`, `tests/fixtures/copilot-1.0.105/version.txt`. **OS:** not recorded.
- **Consequence chain (historical):** first-line-only parsing could accept a different in-range semver from banner/update text while the installed version was elsewhere in output.
- **In-cycle remediation:** Copilot preflight now passes the full stripped `--version` output to `verify_version`; only the first line is kept as the recorded version string in metadata.
- **Classification:** **SAFE REFUSAL**.

## 4) `verify_controls` parsing dependencies

### 4a) Codex help-flag substring checks

- **Location:** `src/duo/policy.py:226-230`.
- **Parsing dependency:** required flags are checked via substring presence in `exec --help`.
- **Provenance:** `tests/fixtures/codex-*/exec-help.txt`; fixture replay in `tests/test_fixtures.py`. **OS:** not recorded for these help files.
- **Consequence chain:** false match requires stale prose mention after option removal. Duo then passes the removed runtime option (`src/duo/adapters.py:447-460`), the CLI rejects it, exits non-zero, and `classify_failure` (`adapters.py:536`) raises `AgentError`: non-retryable when text still includes `"unknown option"`/`"unexpected argument"`, else retried up to `max_attempts` and workflow ends in status `error` (`workflow.py:207-209`). `validate_payload` is never reached.
- **Excluded behavioral risk:** a hidden alias that still accepts/ignores a removed flag would be a CLI enforcement change, outside wording-classification scope; advertised-option checks cannot generally detect that (`policy.py:220-223`).
- **Classification:** **RETRY/COST CHANGE**.

### 4b) Codex `features list` first/last-token parsing

- **Location:** `src/duo/policy.py:231-248`.
- **Parsing dependency (historical):** `parts[0]` name + `parts[-1]` state from any row with `len(parts) >= 3`.
- **Provenance:** `tests/fixtures/codex-*/features.txt`; replay tests and drift helper reuse (`scripts/check_drift.py:49-50`). **OS:** not recorded.
- **Consequence chain (historical, independent OR paths):**
  1. **Chain (1):** a trailing column holding `"false"` (for example after an effective `"true"`) could satisfy `parts[-1]` alone; no duplicate row required.
  2. **Chain (2):** a repeated table where a later row says `"false"` could overwrite an earlier effective `"true"` through dict overwrite; no trailing column required.
  Either chain could make preflight pass and record `verified_disabled_features` falsely (`policy.py:269`).
- **In-cycle remediation:** rows now fail closed unless state is exactly `true|false`, middle tokens match the recorded stage vocabulary (`stable`, `experimental`, `deprecated`, `under development`, `removed`), and feature names are unique (duplicate row raises `RunError` naming the feature).
- **Residual (fail-closed):** the stage vocabulary is now itself a recorded-wording dependency. A future Codex stage name — even in a patch release inside a tested minor — makes every native Codex preflight refuse (the `RunError` names the unrecognized stage); `--allow-untested-cli` does not bypass it, and `check_drift.py` reports probe-failure. That is an availability cost in the safe direction, accepted for a control check: the table has no header row, so a column-count-only guard cannot distinguish the two-token stage `under development` from an added trailing column.
- **Classification:** **SAFE REFUSAL**.

### 4c) Claude help-flag substring checks

- **Location:** `src/duo/policy.py:281-286`.
- **Parsing dependency:** required option names are checked by substring presence in `claude --help`.
- **Provenance:** `tests/fixtures/claude-2.1.296/help.txt`, `tests/fixtures/claude-2.1.330/help.txt`; fixture replay tests. **OS:** Linux for `claude-2.1.330`; not recorded for `claude-2.1.296`.
- **Consequence chain split:** preflight doctor probe is `probe([claude, *claude_options(settings), "doctor"])` (`policy.py:298`), so removal of any of `--safe-mode`, `--restricted`, `--setting-sources`, `--settings`, `--strict-mcp-config`, `--no-chrome`, `--disable-slash-commands` fails **at preflight** when `doctor` rejects the option (assumes unknown top-level options are rejected) and `probe()` raises `RunError` (`adapters.py:362-364`). Removal of `--tools`, `--allowedTools`, `--output-format`, `--verbose`, or `--json-schema` fails **late** on review argv (`adapters.py:461-466`), then `claude_attempt` classifies failure via `classify_failure` at `adapters.py:580` (not the Codex/Copilot path at `adapters.py:536`).
- **Excluded behavioral risk:** hidden accepted aliases are enforcement changes outside wording standard.
- **Classification:** **RETRY/COST CHANGE**.

### 4d) Claude `--output-format` help-block + `stream-json` pairing

- **Location:** `src/duo/policy.py:289-296`.
- **Parsing dependency:** regex pairing accepts either explicit `--output-format stream-json` mention or `stream-json` inside the `--output-format` help block.
- **Provenance:** claude help fixtures and `tests/test_policy.py:228`. **OS:** Linux for `claude-2.1.330`; not recorded for `claude-2.1.296`.
- **Consequence chain:** prose example text like `claude -p --output-format stream-json` can satisfy the regex even if the real value is unsupported; runtime later rejects the value or `claude_result` rejects output, producing late failure.
- **Classification:** **RETRY/COST CHANGE**.

## 5) Copilot option/session-flag parsing

### 5a) `options()` extraction + required-option checks

- **Location:** `src/duo/copilot.py:22-25`, `32-34`, `220-228`.
- **Parsing dependency:** `options()` uses regex token extraction from help and is reused for required `FLAGS`, `--allow-all-urls`, and `--reasoning-effort`.
- **Provenance:** `tests/fixtures/copilot-1.0.95/help.txt`, `tests/fixtures/copilot-1.0.105/help.txt`, `tests/test_fixtures.py:98`, `tests/test_copilot.py:244`. **OS:** not recorded.
- **Consequence chain:** `--no-auto-update` is passed on every Copilot probe (`copilot.py:208`), so its removal fails at preflight probe. Other stale-prose false matches follow the same late-failure path as 4a/4c when review argv includes removed options (`copilot.py:179-190`, with `--reasoning-effort` at `189-190`) and runtime rejects them.
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

## 6) `classify_failure` (permanent fragments + fallthrough)

- **Location:** `src/duo/adapters.py:281-315`; callers at `536`, `576`, `580`.
- **Parsing dependency:** lowercase fragment matching for permanent categories; unmatched text falls through to retryable `AgentError(... retry_after=retry_delay(text))`.
- **Provenance:** fixture/model-error tests in `tests/test_fixtures.py` and `tests/test_duo.py`. **OS:** not recorded.
- **Classification:** **RETRY/COST CHANGE**.

## 7) Structured error extraction (`adapters.py`)

### 7a) `codex_error_text`

- **Location:** `src/duo/adapters.py:257-275`.
- **Parsing dependency:** JSONL event filtering on `turn.failed` and `error`; `"Reconnecting..."` messages are intentionally skipped.
- **Provenance:** codex event fixtures plus reconnect tests. **OS:** Linux for the Codex 0.164.0-0.166.0 streams, macOS for 0.167.0; not recorded for 0.162.1/0.163.0.
- **Classification:** **RETRY/COST CHANGE**.

### 7b) `claude_error_text`

- **Location:** `src/duo/adapters.py:163-170`.
- **Parsing dependency:** structured envelope fields (`subtype`, optional `result` when `is_error`).
- **Provenance:** claude result fixtures/tests. **OS:** Linux for the `claude-2.1.330` fixtures; not recorded for earlier ones.
- **Classification:** **RETRY/COST CHANGE**.

### 7c) `claude_stream_error_text`

- **Location:** `src/duo/adapters.py:244-254`.
- **Parsing dependency:** tail scan for `type == "result"` events then delegation to `claude_error_text`.
- **Provenance:** claude stream fixtures/tests. **OS:** Linux for the `claude-2.1.330` fixtures; not recorded for earlier ones.
- **Classification:** **RETRY/COST CHANGE**.

## 8) `retry_delay` regex and consumption/cap

- **Location:** parser `src/duo/adapters.py:321-335`; consumer `classify_failure` (`315`); cap handling in `src/duo/models.py:90-91` and retry loop `src/duo/workflow.py:213-219`.
- **Parsing dependency:** free-text regex for retry-after phrases and units.
- **Provenance:** unit tests plus timeout fixtures `tests/fixtures/claude-2.1.330/events-timeout-noweb.jsonl` and `events-timeout-web.jsonl`.
- **OS:** Linux (`claude-2.1.330` fixture set).
- **Structured-alternative correction:** those timeout fixtures carry structured backoff as `{"type":"system","subtype":"api_retry","retry_delay_ms":...}` (attempts 1..10), and represent Claude Code internal API retries inside one CLI call (streams that timed out without terminal `result`). They are **not** direct Duo sleep instructions, so they do not simply replace Duo `RETRY_AFTER` parsing.
- **Rate-limit fixture note:** `scripts/record_fixtures.py:85-86` strips `rate_limit_info`; remaining `rate_limit_event` entries in recorded fixtures retain only high-level fields (for example `type`, `uuid`, `session_id`).
- **Classification:** **RETRY/COST CHANGE**.

## 9) `copilot_reply` event-stream extraction

- **Location:** `src/duo/adapters.py:114-152`.
- **Parsing dependency:** selects last `assistant.message` with non-empty `content`, no `toolRequests`, and `phase` in `(None, "final_answer")`.
- **Provenance:** Copilot event fixtures and replay tests. **OS:** not recorded.
- **Consequence chain:** trigger is wording-level field rename in `toolRequests` or `phase` key/value. Unsafe acceptance additionally needs either (a) the qualifying non-final message appears after the true final answer, or (b) no final answer exists, CLI exits `0`, and that selected message parses as schema-valid JSON passing `validate_payload` (`adapters.py:538`) and `validate_review`.
- **Structured alternative present:** both `copilot-1.0.95` event fixtures end with `assistant.turn_end`, `session.usage_checkpoint`, `assistant.idle`, and terminal `{"type":"result","exitCode":0,...}` events.
- **In-cycle remediation (chain status):** `copilot_reply` now refuses when the stream contains no `result` event with `exitCode == 0`, so non-terminal streams no longer produce accepted replies. Chain (b) is closed only for streams lacking that event: a Copilot process that exits `0` normally still emits it, so chain (b)'s schema-valid-message condition is otherwise unchanged. Chain (a) is untouched: a qualifying non-final message after the true final answer is still accepted when the stream terminates successfully. The label therefore stays **POTENTIAL UNSAFE ACCEPTANCE**.
- **New dependency created by the remediation:** the field names `type: "result"` and `exitCode` are now load-bearing. If either is renamed, every Copilot reply raises `AgentError`, which is retryable by default (`src/duo/models.py:79`), so each review repeats as full model calls up to `max_attempts` before the run errors — a RETRY/COST CHANGE dependency recorded here.
- **Classification:** **POTENTIAL UNSAFE ACCEPTANCE**.

## 10) `claude_result` terminal-envelope checks

- **Location:** `src/duo/adapters.py:201-241`, consumed at `576-581`.
- **Parsing dependency:** structured stream validation requires exactly one terminal `result`, no trailing events, and typed `subtype`/`is_error` envelope before success acceptance.
- **Provenance:** claude stream fixtures and `tests/test_claude_stream.py`. **OS:** Linux for the `claude-2.1.330` fixtures; not recorded for earlier ones.
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
- **Label rationale:** fixture writes are printed (`record_fixtures.py:126`), so baseline overwrite is visible as committed-file git diff before use; replay expectations pinned by version (`tests/test_fixtures.py:98`) fail loudly on Copilot when fixture/version mapping drifts; the explicit refusal path is the no-match `SystemExit` (`record_fixtures.py:131-132`). A wrong match does not self-refuse, so this SAFE REFUSAL label depends on review/replay gates.
- **Classification:** **SAFE REFUSAL**.

## 13) Codex MCP list shape/equality check

- **Location:** `src/duo/policy.py:255-264`.
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

- **Location:** `src/duo/policy.py:178-189`.
- **Parsing dependency:** `remote-settings.json` must decode exactly to `{}`.
- **Provenance:** behavior pinned by `tests/test_policy.py:254` (`test_managed_files_and_unreadable_policy_rejected`). **OS:** file format is platform-agnostic; fixture OS not recorded.
- **Fallback:** unreadable/non-empty/non-object refuses.
- **Classification:** **SAFE REFUSAL**.

## Considered and excluded

- `scripts/smoke_inspection.py` parser helpers (`claude_denials`, `tool_calls`; `192-224`, `227-249`) parse event shapes only for diagnostic printing. Pass/fail and READ/not-read outcomes are decided by phrase presence in response/audit (`98-103`, `175-182`), so key renames can hide details but do not change decision results.
- `scripts/record_fixtures.py:65-74` (`copilot_event`) repeats `copilot_reply` filtering for fixture retention. If `toolRequests`/`phase` **keys** are renamed, they are missing, so `not data.get("toolRequests")` is true and `phase` is `None`: more messages are treated as final and kept whole (fixture bloat, no failure). The final answer is shortened to `LIMIT = 300` (`record_fixtures.py:34`) only when phase **values** change or the final message carries `toolRequests`; that value-change path then fails replay loudly when a trimmed answer exceeds 300 characters.
- `scripts/record_fixtures.py:41,50` `REQUEST_ID` scrub regex is wording-dependent. If `"Request ID:"` format changes, request IDs can remain in committed fixtures and current fixture tests do not check for them. **Disposition:** tooling privacy-gap risk; outside runtime review decision classification.
- `src/duo/diversity.py:23-27` and `src/duo/models.py:503-509` parse Duo-controlled identifiers/content, not external CLI control/error wording.

## Risk-ordered recommendations for section 6 steps 2-4

1. **Landed this cycle (site 4b):** unknown Codex feature layouts and duplicate feature rows now fail closed. Follow-up for plan §6 step 2: move from stage-vocabulary guard to explicit structured-column contracts when upstream exposes them.
2. **Landed this cycle (sites 3a/3b):** ambiguous multi-version output now fails closed, and Copilot version gating parses full `--version` output. Follow-up for plan §6 step 2: tighten to CLI-specific anchored formats (`codex-cli X`, `X (Claude Code)`, `GitHub Copilot CLI X`).
3. **Landed this cycle (site 9, partial):** Copilot replies now require a `result` event with `exitCode == 0` somewhere in the stream, which closes chain (b) only for streams lacking that event; chain (a) remains open. Follow-up for plan §6 step 2: bind answer acceptance to a stricter final-message contract (not just last qualifying message + terminal success).
4. **Sites 11a/11b (small):** improve drift visibility (report parser confidence/unknown-format and include state-aware feature diffs).
5. **Retry/cost group (medium):** sites `6`, `7a`, `7b`, `7c`, `8`, `4a`, `4c`, `5a`, `4d`, `5b` should prioritize structured fields before text fragments and separate retry policy from human wording.
6. **Fixture retention/expansion (small/medium):** add fixture-backed tests for unknown/shifted messages, Copilot field/key drift, and at least one unknown-option rejection fixture (requested in 5a) to pin the late-failure classification path.

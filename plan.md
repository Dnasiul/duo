# Duo improvement plan

## Goal and scope

Improve Duo from release 0.8.3 while preserving the default document-mode protocol: read-only reviewers; success only after every configured reviewer approves the same unchanged document hash (two by default, a fixed roster with `--reviewer`); clearing every approval after a revision or a resume; a restricted reviewer environment; refusal of CLI versions outside the tested ranges by default; and an audit record of every attempt. Preserve the explicit experimental `--allow-untested-cli` exception and its mandatory control probes. Reviewer agreement is a signal, not proof of correctness.

Writing or approving this plan authorizes no implementation, code change, release, credential distribution, push, or model call. Release numbers below are proposed milestones, not publication commitments. The supported deployment remains one user, trusted repositories, and unmanaged CLI configuration. Existing owner decisions about that baseline and allowing native Codex with `--allow-web` remain in force.

This revision (2026-11-26) replaces the plan written after 0.4.6 (`git show 8120fb4:plan.md`). Section numbers are unchanged because `CHANGELOG.md`, `docs/STATUS.md`, `docs/ISOLATION_GATE.md`, and `docs/PARSING_INVENTORY.md` cite them. Shipped sections are reduced to their outcome and where the evidence lives; the full designs remain in `8120fb4:plan.md`. The older production-readiness plan remains available as `git show 918d2c5:plan.md`.

## Verified starting point

- Package metadata is 0.8.3, published on 2026-11-26 (tag `v0.8.3` on `05596ec`). Releases 0.5.0 through 0.8.3 each record exact-commit CI, local release-script checks, a bounded live run, publication, and downloaded checksum and provenance verification in `docs/STATUS.md`.
- 0.8.3 added Codex CLI 0.168.x to 0.172.x to the tested range. The offline suite has 476 tests; CI passed on all 10 jobs for the release commit. Live validation covered Linux native pairs only; Copilot pairs and macOS remain pending.
- `TESTED_VERSIONS` in `src/duo/policy.py` accepts Codex 0.162.1 to before 0.173.0, Claude Code 2.1.296 to before 2.2.0, and Copilot 1.0.95 to before 1.1.0. These are compatibility ranges, not a claim that every accepted version ran live. The drift check on 2026-11-26 found Codex 0.172.0, Claude Code 2.1.332, and Copilot 1.0.116 compatible.
- CI runs installed-wheel and installed-sdist tests on Ubuntu and macOS with Python 3.11–3.14, plus newest-dependency and `pip-audit` jobs. Python 3.10 was retired in 0.5.0.
- Shipped since 0.4.6: the native Claude audit stream and context files with a 512 KiB combined prompt budget (0.5.0); HTML agreement reports (0.5.1); structured verdicts with findings and answering a blocked run (0.6.0); persistent checkpoints and `--resume` (0.7.0); reviewer panels with diversity warnings (0.8.0); fail-closed parsing guards and the parsing inventory (0.8.1); `allow_login_shell=false` for native Codex (0.8.2); Codex CLI 0.168.x–0.172.x (0.8.3).
- Native Codex reads remain unconfined, and its non-login command shell still sources `.bashrc`. Copilot's fresh configuration/cache directories are not an isolated OS home. No backend has an isolation profile; the Linux feasibility gate is recorded in `docs/ISOLATION_GATE.md`. These limitations remain explicit in `docs/SECURITY.md`.
- The owner's Copilot account (Copilot Pro) offered `claude-sonnet-5.5`, `gpt-6-luna`, and `gpt-5.3-codex` on 2026-11-23 and refused `gpt-5.4`, `gpt-6-astra`, `claude-opus-5.5`, and `claude-fable-5-1`. Since 2026-11-25 its monthly quota is exhausted (HTTP 402 `quota_exceeded`); the owner expects a reset around 2026-12-01.
- Message wording remains a compatibility dependency. `docs/PARSING_INVENTORY.md` classifies 23 parsing sites. Three still carry a risk of unsafe acceptance: Copilot answer selection (site 9, with two remaining cases) and the drift checks for flags and features (sites 11a/11b).

Effort estimates are planning estimates, not delivery promises: small means roughly half a day to two engineering days; medium means several days; large means multiple weeks. Live-call cost, account quotas, and availability of both machines are additional dependencies.

## 1. Release tooling in the repository — shipped in 0.4.2

`scripts/release.py`, `.github/workflows/release.yml`, and `tests/test_release.py` build, check, attest, and draft releases; every release since 0.4.4 used them. Publication and downloaded-asset verification remain owner-controlled steps, and the workflow does not stop an owner from publishing a draft without the checklist.

## 2. Native Claude tool calls in the audit record — shipped in 0.5.0

Native Claude emits `stream-json --verbose` into each attempt's raw audit log, including failed and interrupted attempts; only an unambiguous successful terminal result supplies the review. Linux Claude Code 2.1.330 fixtures cover Read, Glob, Grep, WebSearch, WebFetch, denials, and turn-limit failure. Remaining limitation: emitted tool logs are not complete file or page archives. macOS stream evidence is covered under section 8.

## 3. Context files and a combined prompt budget — shipped in 0.5.0

Repeatable `--context-file` with a 64 KiB aggregate limit, credential rejection before calls, and hashed audit copies; `--max-prompt-bytes` with a 512 KiB default ratified by the owner on 2026-11-23 as an owner-approved compatibility change (measured maximum 150,232 bytes across recorded runs). macOS remains unverified for this feature.

## 4. Isolation profile — feasibility gate partly run

Status: step 1 recorded for Linux; Copilot and macOS cells pending; steps 2–5 not started.

The Linux gate is recorded in `docs/ISOLATION_GATE.md` (2026-11-25). The owner can decide now whether to accept the tested native mounts. Both reviewers received a read-only mount of the entire `~/.local` directory. Codex could write its entire `~/.codex` directory. Claude could write its entire `~/.claude` directory and `~/.claude.json`. These mounts expose existing credentials, configuration, and history to the respective CLI. The read-only root mount also leaves normally readable files outside the covered home visible.

The gate used a dedicated writable Duo state directory. On a default installation, the `~/.local` mount also exposes `~/.local/state/duo`, including earlier runs’ tasks, criteria, review history, document snapshots, captured context, and raw reviewer output. Duo sends the assembled prompt to the CLI on stdin and saves the CLI’s stdout in the attempt log. Both recorded Copilot 1.0.95 streams include prompt text in a `user.message` event, so access to prior logs can expose earlier prompts. Native Claude and Copilot logs also contain tool results, so prior logs can expose files read in other repositories and fetched pages. The mount also exposes other applications’ data under `~/.local/share`, such as keyring, mail, and shell-history directories. Before approving a state layout, the owner decides whether reviewers may read earlier runs and which paths in Duo state they may write outside the active run, if any.

For that exact boundary, allow **4–6 engineer-days** for step 2 or **7–11 engineer-days total** through release verification and documentation. If the owner requires narrower mounts (for example, only each CLI’s resolved install path, such as `~/.local/share/claude/versions/<version>`, and its `~/.local/bin` entry point), repeat the deterministic boundary checks and an authenticated native Duo run to agreement with those mounts, then estimate that implementation separately. Allow 0.5–1 day to rerun the Copilot gate once quota permits and 2–4 days to investigate macOS. The Copilot and macOS implementation estimates remain open pending those results.

Gate verdicts so far, on Linux with bubblewrap: native Codex is supported only with `~/.codex` writable (read-only state fails with `EROFS`); native Claude is supported with `~/.claude` and `~/.claude.json` writable; Copilot is unevaluated because both confined and unconfined requests hit the quota. Every macOS cell is unevaluated. Section 7.2's residuals (`.bashrc` sourcing, `BASH_ENV`/zsh startup files, macOS login-shell behavior) pass to this section.

Relevant files: `src/duo/adapters.py`, `src/duo/copilot.py`, `src/duo/policy.py`, `src/duo/models.py`, `src/duo/cli.py`, `scripts/smoke_inspection.py`, `src/duo/checkpoints.py`, `tests/test_environment.py`, `tests/test_policy.py`, `tests/test_resume.py`, new `tests/test_isolation.py`, `docs/SECURITY.md`, `docs/STATUS.md`, `docs/ISOLATION_GATE.md`.

1. Evaluate each remaining combination independently. Rerun the Copilot cell for a completed authenticated response once quota permits. On macOS, run the recorded protocol under both sandbox-exec and a separate account. Update the verdict and estimate for each as evidence arrives.
2. With owner approval, implement an explicit opt-in profile for the selected boundary. Cover the real home with a temporary home, mount the reviewed repository read-only, enumerate writable scratch/cache/state/audit paths, and expose only the owner-approved runtime and login mounts for that reviewer. Verify that owner-home paths outside the enumerated mounts, including `~/.ssh`, `~/.aws`, and unrelated projects there, stay hidden. Credential provisioning remains an explicit owner-controlled operation.
3. Refuse unavailable mechanisms and unsupported combinations; never silently use ordinary execution. Preserve the ordinary mode's documented baseline rather than implying it has acquired this boundary.
   Record the selected isolation profile and its mount and access policy in the checkpoint. Checkpoints are validated against exact key sets and only schema versions 1 (pair) and 2 (panel) are accepted, so isolated runs write new checkpoint schema versions; versions 1 and 2 resume in ordinary mode. Resume cannot add or remove isolation; changing it requires a new run. Test `--resume` against pre-isolation pair and panel checkpoints, which must resume in ordinary mode, and test that Duo 0.8.x refuses an isolated checkpoint, so an older Duo can never resume an isolated run without its boundary. For an interrupted isolated run, verify that preflight and review both use the recorded boundary. If that boundary cannot be restored, recovery must stop before launching any reviewer CLI, including for preflight.
4. Add deterministic boundary probes plus live authentication/inspection checks for each claimed combination: outside canaries, symlinks, excluded login resources, writes inside and outside the repository, hooks, MCP launchers, conflicting configuration, and network behavior. Enforcement evidence requires an observed denial of an attempted operation. A model refusal leaves the probe inconclusive.
   Exercise the default state path in a disposable home and each supported override layout (`XDG_STATE_HOME` or `--state-dir`). Seed a synthetic prior run alongside the active run. From each confined reviewer, attempt to read its canary and create a sibling file in the prior run. Check the result and the files on the host against the approved read and write policy. Verify that Duo can still record the active run. Claim support only for layouts that pass.
5. Test web-disabled and `--allow-web` operation separately. For each, record which destinations the boundary permits and which the probe actually contacted. Filesystem isolation does not stop readable material reaching providers or URLs. The Linux gate used a shared host network.

Acceptance: each supported combination has retained enforcement and authentication evidence; forbidden operations fail and leave no write/hook/MCP sentinels; required checks fail closed; unsupported combinations are explicit. Expansion to sensitive or untrusted repositories requires a separate owner decision.

## 5. Reviewer model diversity — warnings shipped in 0.8.0; availability work open

Shipped: prefix-based classification for every roster, including the default pair, with identical, same-family, and unknown-family warnings in summaries and `reviewer-policy.json`. Done on Linux: the account's offered models were probed on 2026-11-23, and a two-family Copilot pair (`gpt-6-luna` + `claude-sonnet-5.5`) agreed in 5 turns (`run-0lh3gb8y`).

Status: step 1 done on `main`, unreleased: the README compatibility row and the STATUS model list now separate the models offered at the 2026-11-23 probe, `gpt-5.4`'s earlier successes, and the 2026-10-21 refusals. Steps 2 and 3 open.

Effort: small. Relevant files: `README.md`, `docs/STATUS.md`, `src/duo/diversity.py`, `tests/fixtures/`.

1. Correct the README's "Models used in live runs" row: it still lists Copilot `gpt-5.4` without noting the later refusal, and omits `gpt-6-luna` and `gpt-5.3-codex`. Separate historical successes, current refusals, and current availability.
2. Run a two-family Copilot pair on macOS once quota permits (with section 8's Copilot rows).
3. Fable: Copilot does not offer it on the current plan. Verify the native `claude-fable-5-1` ID or `fable` alias with a recorded run before claiming it.

Acceptance: README and STATUS model lists match recorded evidence; Copilot diversity is claimed per platform only with a recorded two-family run.

## 6. Dependence on message wording — partly shipped in 0.8.1

Status: steps 1–5 done on `main`, unreleased. Step 1 has offline replay and mutation tests; a live Copilot run is pending the quota reset. Step 2's workflow passed a manual run on 2026-11-27 but has not yet run on a schedule. Step 3 anchored version parsing; Codex 0.172.0 `features list` still has no structured output, so feature parsing stays on the text contract. Step 4 moved retry decisions to one category table, lets Claude's structured fields and Codex's usage-error exit decide before wording, and limits output-format failures to one retry; Codex and Copilot runtime errors still have no recorded structured fields. Step 5 recorded each CLI's unknown-option rejection, replays recorded failure messages with shifted and unknown wording, and makes the fixture recorder replace identifiers by key; it found and removed a GitHub request ID and provider message, response and tool-call IDs kept in the two Copilot 1.0.95 streams. No Copilot event fixture exists after 1.0.95.

Version 0.8.1 added the inventory (`docs/PARSING_INVENTORY.md`) and guards for ambiguous version output, ambiguous Codex feature listings, and Copilot streams missing a successful `result` event. Step 1 replaced the Copilot parser's last-qualifying-message rule with a final-answer contract; site 9 is now RETRY/COST CHANGE, and the new `toolRequests` dependency joins step 4.

The remaining work below has a medium effort estimate. Relevant files: `src/duo/policy.py`, `src/duo/adapters.py`, `src/duo/copilot.py`, `scripts/check_drift.py`, `scripts/record_fixtures.py`, `.github/workflows/drift.yml`, `tests/test_policy.py`, `tests/test_copilot.py`, `tests/test_drift.py`, `tests/fixtures/`, `docs/PARSING_INVENTORY.md`.

1. For site 9, require an identifiable final assistant answer followed by a successful terminal result. Replay both Copilot 1.0.95 success fixtures: `events-claude-approve.jsonl` must yield the recorded approval with no `phase` field, and `events-gpt-revise.jsonl` must yield the recorded revision with `phase: "final_answer"`. Cover both remaining failure cases before closing the site. In chain (a), a later non-final message must never replace the genuine answer. In chain (b), a schema-valid non-final message followed by terminal success must be rejected when the stream contains no genuine final answer.
2. For sites 11a/11b, report parser confidence, unknown help and feature-list layouts, and changes to feature states. Test a two-column feature list and a removed option whose name survives in deprecation prose. The report must flag the removal even for an in-range CLI.

   Today `check_drift.py` compares every version with the oldest tested fixture. For each CLI, compare the observed version with its latest explicitly reviewed fixture at or below that version. Retain older fixtures for replay. After review and verification of required controls, record the CLI, version, and path and SHA-256 digest of each comparison file in the checked-in baseline manifest. The drift checker must verify those digests before using the baseline. Only explicit review updates the manifest.

   Test that an unreviewed removal opens the weekly issue and that re-recording a fixture at the same version with different contents keeps the issue actionable even if the new fixture matches live output. A clean check after the replacement digests are reviewed and all platforms pass closes the issue. A subsequent removal opens it again. Missing platform results, failed control probes, and out-of-range versions still require attention.
3. Tighten version parsing to CLI-specific anchored formats (`codex-cli X`, `X (Claude Code)`, `GitHub Copilot CLI X`); move Codex feature parsing to a structured contract when upstream exposes one.
4. Retry/cost group (inventory recommendation 5): prefer structured fields over text in failure classification and separate retry policy from wording, including the new `type: "result"`/`exitCode` dependency.
5. Fixture coverage: unknown or shifted messages, Copilot field/key drift, an unknown-option rejection, and a check that committed fixtures contain no request IDs (`record_fixtures.py` scrub gap).

Acceptance: no inventory site remains classified as potential unsafe acceptance, or each remaining one is recorded with its reason; unknown policy states still refuse setup; drift reports show changed wording explicitly; recorded variants replay offline.

## 7. Smaller improvements and owner decisions

### 7.1 Answer a blocked run in a fresh run — shipped in 0.6.0

`--answer TEXT` with `--source-run PATH`; source identity, question, answer, and hashes recorded; approvals start fresh and the source is never modified.

### 7.2 Codex login-shell environment reintroduction — shipped in 0.8.2

Native Codex passes `allow_login_shell=false`, demonstrated on Linux with Codex 0.171.0 and checked for 0.168.0–0.172.0 by `scripts/smoke_inspection.py --login-shell`. Residuals passed to section 4: the non-login shell still sources `.bashrc`; `BASH_ENV` and zsh startup files are analysis only; macOS is untested.

### 7.3 Python 3.10 retirement — shipped in 0.5.0

Owner decision on 2026-11-23; supported range is Python 3.11–3.14.

### 7.4 Copilot search-only GitHub MCP access

Status: deferred investigation; not started. Effort: small feasibility check; potentially medium implementation and live validation. Blocked on Copilot quota for live work.

Relevant files: `src/duo/copilot.py`, `src/duo/policy.py`, `scripts/check_drift.py`, `tests/test_copilot.py`, `tests/test_web.py`, `tests/fixtures/`, `README.md`, `docs/SECURITY.md`.

1. Determine whether the built-in GitHub MCP server supports an enforceable search-only tool subset under Duo's no-interaction settings. Do not enable the whole server merely to obtain search.
2. If feasible, add an explicit least-privilege configuration, preflight checks, and fixtures. Exercise attempted repository writes and other excluded tools as well as successful search, with live evidence of actual requests and denials.
3. If unsupported, record the reason and keep built-in MCP disabled and Copilot web access fetch-only.

Acceptance: search is enabled only if excluded operations demonstrably remain unavailable; otherwise a documented negative result closes the investigation without changing behavior.

### 7.5 Optional PyPI publication

Status: deferred owner decision; GitHub-only distribution remains the baseline. Effort: small/medium after the decision.

1. Owner chooses whether to add PyPI and confirms an available package name. `duo-plan` is the current distribution name, not evidence of PyPI ownership.
2. Only if approved, add trusted publishing for the already-built, verified artifacts in a narrowly scoped protected job, preserving exact-commit CI, checksums, provenance, and explicit publication approval.
3. Validate through a staging flow, then verify installation from the uploaded artifacts when publication is separately authorized.

Acceptance: an approved decision and a tested publishing/install procedure exist before publication; declining PyPI leaves the release process unchanged.

## 8. Open live verification

Status: open. Effort: small per bounded experiment. Copilot rows wait for the quota reset; macOS rows wait for the owner's Mac. These checks accompany affected releases rather than being postponed indefinitely.

Relevant files: `scripts/smoke_inspection.py`, `scripts/record_fixtures.py`, `scripts/check_drift.py`, `tests/test_smoke_inspection.py`, `tests/fixtures/`, `docs/STATUS.md`, `docs/SECURITY.md`, `README.md`.

For every row: identify the commit, installed artifact, OS/architecture, exact CLI versions, models, backend pair, mode, web setting, and budget; use synthetic repositories, canaries, and disposable configuration; classify each operation as requested, accessed, denied, not attempted, or unknown; record outcomes in STATUS and correct SECURITY/README claims. A declined action or unavailable model leaves the gap open. Do not generalize one version/platform result to a whole range.

Done on `main`, unreleased: `smoke_inspection.py --evidence-dir` (tests in `tests/test_smoke_inspection.py`). Add an evidence-export option to the smoke tool. Before temporary-directory cleanup, save the sanitized invocation, relevant tool events, and exit status or error for each attempt, including failures. Record a denial only when the evidence identifies the attempted operation and its tool or OS denial. If the canary is absent and the available events cannot establish whether the operation ran, report the outcome as unknown. Test both cases and verify that the exported evidence survives cleanup.

| Open verification | Specific evidence required |
| --- | --- |
| Codex 0.164–0.166 and 0.168–0.172 on macOS, 0.167 on Linux, and 0.168–0.172 paired with a Copilot reviewer | Exercise each native Codex version with version probes, inspection smoke, deterministic write denial, and a bounded Duo run. For its login-shell check, record the executed shell. Use `smoke_inspection.py --login-shell` for bash and the gate record's `.zprofile` sentinel variant for macOS/zsh. Credit each result only to the shell and backend exercised. If an older version is unavailable or no longer worth maintaining, record the owner's decision. |
| Copilot 1.0.103+ on macOS | Exact version with `--sync-session=off`, effective settings, confinement evidence, and a bounded run. |
| Document modes through Copilot on macOS and with mixed pairs | Each missing mode/backend/platform combination produces valid structured reviews and preserves approval semantics. |
| `--allow-web` on macOS, with mixed pairs, and in plan/description/review modes | For each reviewer, retain requests and available results for synthetic public sources from its own audit stream: native Codex web-search events as its JSON stream emits them (record the gap if it emits none), native Claude WebSearch/WebFetch events (section 2), or Copilot `web_fetch` events. Record gaps in emitted results and verify the read-only and MCP restrictions. |
| F3: Copilot GPT outside reads on macOS | On Linux (2026-11-23, `gpt-6-luna`) the reads were not accessed but the refusal was model-level. Tool-level denial or observed access is still needed. Native Codex's declined searches remain untested operations. |
| Features shipped in 0.5.0–0.8.2 on macOS | Context capture, Claude stream events, verdicts, `--resume`, a panel run, and the login-shell control each need a bounded macOS run. |
| Authenticated policy and network boundaries | Record the credential context of managed-policy probes without values. For network claims use controlled connection attempts with a working comparison path; DNS failure alone does not prove denial. Coordinate with section 4. |

Acceptance for each row: retained evidence supports exactly the stated combination and outcome, or the row stays open with its reason.

## 9. Security self-review remediation — shipped in 0.4.6

F1–F6 from `review.md` were remediated in 0.4.6 and remain regression gates: bounded credential scanning with a post-validation deadline (F1), assurance claims matched to evidence (F2–F4), fixture probes using the production environment (F5), and a test runner that rejects empty or incomplete suites (F6). F3's macOS gap stays in section 8; F4's audit follow-up shipped as section 2.

## 10. Build mode — bounded implementation and review of a fixed diff

Status: proposed; gated on section 4. Effort: large, re-estimated once section 4 implements a profile. Its input-capture and verdict dependencies (sections 3 and 11) have shipped. The Linux gate supports only native review cells with a read-only repository and writable CLI state; the bounded writable implementer role needs its own boundary tests. The full design is in `git show 8120fb4:plan.md`, section 10, and still applies, with these changes:

1. Extend the verdict format with a versioned build schema, a typed diff subject, and a distinct build agreement rule. The rule requires read-only implementer sign-off and independent reviewer approval of the same frozen candidate after all required checks pass. Keep document verdicts on their existing schemas and rules. Add compatibility fixtures proving that the document-only gate rejects a build verdict and that a consumer explicitly selecting the build rule accepts a valid one. That consumer must verify the pinned base, candidate manifest and diff hashes, fixed criteria, required check results and their evidence hashes, and both role-bound approvals. Test rejection when the candidate changes or required check evidence is missing, altered, or records a failure.
2. Panels (section 13) and `--resume` (section 12) have shipped for document modes; initial build mode still refuses both combinations.
3. Evidence motivating the proposal remains the manual Codex-implements, Claude-reviews loop of 2026-11-23, which is not evidence of shipped enforcement.

Acceptance (unchanged): success identifies one reproducible candidate and fixed criteria with two explicit role-bound approvals and passing required checks; every change invalidates approvals; implementation writes stay within an enforced disposable boundary; no agreement applies or publishes code automatically.

## 11. Structured verdicts and findings for external gates — shipped in 0.6.0

Versioned `verdict.json` (schema 1.0, 1.1 in 0.7.0 with `approval_epoch`, 1.2 in 0.8.0 for panels), persistent role-owned findings with stable IDs, and the stdlib reference gate `examples/check_verdict.py`.

## 12. Persistent checkpoints and interrupted-run recovery — shipped in 0.7.0

`duo --resume RUN_PATH` recovers interrupted document reviews from validated checkpoints (schema 1; schema 2 for panels), preserving work and budgets while clearing approvals. `duo-clean` protects paused recoverable runs.

## 13. Optional third reviewer and N-model document panels — shipped in 0.8.0

`--reviewer ID:KIND:BACKEND:MODEL[:EFFORT]` rosters with unanimous agreement, roster-order scheduling, and roster/policy hashes in verdicts and checkpoints. Live panel evidence exists on Linux through Copilot and natively (`run-zouzw1q_`). Step 6's rule still applies: configurations not exercised live, such as a macOS panel, remain explicit evidence limitations.

## Execution order and release gates

1. **Next maintenance work, proposed 0.9.0.** 0.8.3 shipped the Codex 0.168.x–0.172.x range on 2026-11-26 (`docs/STATUS.md`). Address both Copilot answer-selection cases and the two drift-reporting sites in section 6 steps 1–2, then steps 3–5. Fold in section 5's README model-list correction.
2. **After the Copilot quota resets:** the section 4 Copilot cell, section 8's Copilot rows (including 0.168–0.172 pairs), and section 5's macOS two-family run when on the Mac. Consider section 7.4's feasibility check in the same window.
3. **On the owner's Mac:** the section 4 macOS protocol and section 8's macOS rows, prioritizing the login-shell control and the features shipped in 0.5.0–0.8.2.
4. **Isolation implementation.** The owner can authorize section 4 step 2 for the tested Linux native mounts, or for narrower mounts after they pass the repeated checks, while the Copilot and macOS evaluations are pending. Record the owner’s prior-run read and state write decisions for the selected layout. If the owner defers or declines, record the reason and continue independent work.
5. **Build mode:** only after section 4 ships a profile and the bounded writable role passes its own tests.
6. **CLI range extensions continue independently:** a drift-detected CLI minor version follows the probe-and-live-evidence procedure and may ship as its own patch release. Default refusal and the experimental exception remain unchanged.

Owner decisions, not engineering tasks: whether to accept a Linux native-only first isolation profile, with which mounts, and whether reviewers may read earlier runs or write Duo state outside the active run (section 4); whether to publish on PyPI and under what name (section 7.5); whether to keep maintaining historical CLI versions that are no longer obtainable (section 8); credential provisioning and any expansion of the supported deployment contract (section 4). Build invocations additionally require explicit user-selected scope, criteria, and validation commands; this plan grants no standing authorization to implement generated plans.

For each proposed release, implement only separately authorized work; add a regression test that fails without each guarantee-affecting fix; run the strict offline suite against wheel and sdist on the CI matrix; run fixture/drift checks; and obtain live evidence for changed CLI behavior before claiming support. Record exact versions, platforms, models, outcomes, and limitations in STATUS and the changelog. A release must either satisfy its changed-behavior gates or explicitly exclude the unfinished feature from its claims. Publication follows section 1's procedure only when the owner authorizes it.

Outside scope: distributed or multi-user orchestration (the supported deployment is one user with local repository locking); automatic implementation of generated plans without a separate explicit build invocation; additional provider/CLI backends beyond the existing adapters; Windows support. Majority voting and build-plus-panel/build-plus-resume combinations remain deferred.

# Duo improvement plan

## Goal and scope

Improve Duo from release 0.4.5 while preserving two read-only reviewers; success only after both approve the same unchanged document hash; clearing every approval after a revision; a restricted reviewer environment; refusal of CLI versions outside the supported ranges by default; and an audit record of every attempt. Preserve the explicit experimental `--allow-untested-cli` exception and its mandatory control probes. Reviewer agreement is a signal, not proof of correctness.

Writing or approving this plan authorizes no implementation, code change, release, credential distribution, push, or model call. Release numbers below are proposed milestones, not publication commitments. The supported deployment remains one user, trusted repositories, and unmanaged CLI configuration. Existing owner decisions about that baseline and allowing native Codex with `--allow-web` remain in force.

This replaces the earlier improvement plan while retaining sections 1–8. Section 9 incorporates the security self-review and takes priority in the Execution order. The older production-readiness plan remains available as `git show 918d2c5:plan.md`.

## Verified starting point

- Package metadata is 0.4.5. `docs/STATUS.md` records release `v0.4.5` at `f2b6ccb`. The task's baseline, `a11516c`, adds only documentation after that tag; the inspected checkout also contains commit `831d42d`, adding `review.md`. Implementation is unchanged from 0.4.5.
- There are 182 test methods in `tests/`. `.github/workflows/ci.yml` configures installed-wheel and installed-sdist tests on Ubuntu and macOS with Python 3.10–3.14, plus newest-dependency and dependency-audit jobs. This is repository evidence of the configuration, not a fresh assertion that remote CI passed. The self-review (`review.md`) records three read-only fixture checks passing on Linux with Python 3.12.3 and states that the full suite was not run because LangGraph was unavailable in a read-only workspace; this plan adds no newer test-run evidence.
- Release tooling shipped in 0.4.2 (`CHANGELOG.md`, `scripts/release.py`, `.github/workflows/release.yml`, `tests/test_release.py`). A weekly workflow checks CLI compatibility metadata on Ubuntu and macOS (`.github/workflows/drift.yml`); it does not prove live enforcement.
- `TESTED_VERSIONS` in `src/duo/policy.py` accepts Codex from 0.162.1 to before 0.168.0, Claude Code from 2.1.296 to before 2.2.0, and Copilot from 1.0.95 to before 1.1.0. These are compatibility ranges, including unexercised patches, not a claim that every accepted version ran live.
- `docs/STATUS.md` records historical live runs of all four backend combinations on Linux and macOS. Document modes remain unverified through Copilot on macOS and with mixed pairs. Web access is recorded only on Linux, in report mode, with native and Copilot pairs. Codex 0.164–0.166 evidence is Linux-only; 0.167 evidence is macOS-only. Copilot 1.0.103+ was exercised with 1.0.105 on Linux; the last recorded Mac used 1.0.95.
- Since the 0.4.2 candidate checks, the owner's Copilot account has refused `gpt-5.4`, `gpt-6-astra`, and `claude-opus-5.5` (`docs/STATUS.md`, 0.4.2 release-candidate entry). Subsequent recorded Copilot pairs used `claude-sonnet-5.5` in both roles. Those runs provide two reviewer identities but no second model family. Current account availability needs owner verification; historical GPT successes remain historical evidence.
- Native Codex reads are unconfined. Its login shell can reintroduce variables dropped by `reviewer_environment`. Copilot's fresh configuration/cache directories are not an isolated OS home. No backend has the proposed isolation profile. These limitations remain explicit in `docs/SECURITY.md`.
- Native Claude uses `--output-format json` in `CLIAdapter.command`. Its committed result fixture contains the final structured answer and token usage, but no tool-call transcript. Historical web checks reported zero web-search/fetch counters despite tool use; that does not mean token usage was absent. Neither current CLI logs nor document snapshots guarantee an archive of every inspected file or page.
- Native Codex can inspect Git history/diffs with read-only commands. Claude and Copilot have no shell tool but can read accessible saved evidence. There is no context-file option, combined prompt-size check, blocked-run answer option, or model-diversity warning in the current CLI/workflow. Task, criteria, and document are each limited to 1 MiB (`MAX_TEXT_BYTES`), and `build_prompt` also embeds the latest revision diff and selected history, so 0.4.5 accepts prompts several times larger than 1 MiB.
- Message wording remains a compatibility dependency. `CHANGELOG.md` records the macOS 27 missing-domain wording fix in 0.4.5; `policy.py`, `adapters.py`, and `copilot.py` still parse human-readable output.
- Python 3.10 passed end of life in October 2026. `pyproject.toml`, CI, and the README still support it.
- The previous plan's assertion of a successful native Fable review on 2026-11-10 is not corroborated by `docs/STATUS.md`, the changelog, or response fixtures. The recorded Claude help advertises a `fable` alias, which proves option documentation, not a successful review or the proposed full model ID. Section 5 retains the verification work without retaining that success claim.

Effort estimates below are planning estimates based on the affected code and existing test infrastructure, not measured delivery promises: small means roughly half a day to two engineering days; medium means several days; large means multiple weeks. Live-call cost, account access, and availability of both machines are additional dependencies.

## 1. Release tooling in the repository — implemented in 0.4.2

Relevant files: `scripts/release.py`, `scripts/build-requirements.txt`, `.github/workflows/release.yml`, `.github/workflows/ci.yml`, `tests/test_release.py`, `docs/STATUS.md`, `CHANGELOG.md`.

Completed mechanisms:

- The release script clones the selected commit, builds with pinned build tools, checks wheel contents against tracked package files, installs wheel and sdist separately with `constraints.txt`, invokes the strict test runner, checks the installed version and both CLI help commands, and writes checksums and a notes template. The build script requires Python 3.11 or newer even though the package supports 3.10.
- The tag workflow rejects package/tag version mismatches. Its current CI gate requires at least one completed CI workflow run for the exact commit and requires all returned completed conclusions to be successful. In-progress runs are excluded. This is the implemented gate, not a claim of an independent per-job publication lock.
- A separate tag-only job has release-writing and attestation permissions, attests artifacts, verifies checksums, and creates a draft release. Branch-triggered and manual non-tag runs are dry runs. Builds are not byte-reproducible; release checksums belong to the workflow's artifacts.
- Publication, live checks, and downloaded-asset verification remain operational steps. `docs/STATUS.md` records the first workflow-built release and later releases, including downloaded checksum and attestation verification for 0.4.5. The workflow does not prevent an owner from manually publishing a draft without following the checklist.

Closure evidence: the implementation, release tests, changelog, and recorded release reports support keeping section 1 completed. Fixing the runner's empty-suite hazard in section 9.4 strengthens these existing checks; it does not mean release automation itself is unimplemented.

## 2. Native Claude tool calls in the audit record

Status: open; retained because F4 confirms the audit limitation. Effort: medium, with live format discovery first. Depends on section 9.3 before recording new native fixtures.

Relevant files: `src/duo/adapters.py`, `src/duo/policy.py`, `scripts/record_fixtures.py`, `scripts/check_drift.py`, `scripts/smoke_inspection.py`, `tests/test_duo.py`, `tests/test_fixtures.py`, `tests/test_policy.py`, `tests/test_web.py`, `tests/fixtures/`, `docs/SECURITY.md`, `docs/STATUS.md`, `CHANGELOG.md`.

1. In authorized synthetic live runs, determine what `--output-format stream-json --verbose` emits with Duo's existing restrictions and schema: requests and inputs for Read/Glob/Grep/WebSearch/WebFetch, available results and denials, terminal structured output, errors, and usage. Do not infer successful access from a request. If the format cannot meet the requirements, record that result and retain the current limitation rather than claiming completion.
2. Retain raw emitted events in each attempt's audit files, including failed and interrupted attempts. Parse only a valid successful terminal result into the existing response validator. Preserve limits, schema checks, sanitized error classification, and rejection of unsuccessful envelopes even when the process exits zero. Add explicit completion and usage-availability metadata; absent usage and reported zero usage are different states.
3. Update fixture sanitization and smoke-test denial extraction for the new stream. Preserve fields needed to reproduce parsing and request/result distinctions while removing sensitive identifiers. Extend preflight and drift checks to required stream options.
4. Add regression tests for malformed/truncated events, missing or ambiguous completion, nonzero exits, unsuccessful terminal results, denials, output limits, and interruption. Partial assistant messages must never become approvals or revisions.
5. Record sanitized live fixtures for each enabled tool, with and without web access, and document the observed coverage and omissions. A URL is not an archived page; search/fetch output need not enumerate every internally accessed resource.

Acceptance: successful, denied, failed, and interrupted attempts retain the events the CLI actually emits; only completed valid successful reviews enter the workflow. Fixtures replay the tool and terminal-result handling, existing approval/deadline tests pass, and live evidence identifies coverage by CLI version and platform without promising complete file or web archives.

## 3. Context files and a combined prompt budget

Status: open; retained with F1 as a prerequisite. Effort: medium. Depends on section 9.1; independent of the isolation milestone.

Relevant files: `src/duo/cli.py`, `src/duo/storage.py`, `src/duo/prompts.py` (`build_prompt`, `revision_diff`), `src/duo/models.py` (`Config`, `MAX_TEXT_BYTES`), `src/duo/workflow.py`, `src/duo/adapters.py`, `src/duo/copilot.py`, `tests/test_duo.py`, `tests/test_acceptance.py`, `tests/test_modes.py`, new `tests/test_context.py`, `README.md`, `docs/SECURITY.md`, `CHANGELOG.md`.

1. Add repeatable `--context-file PATH`. Use bounded regular-file reads through `read_input`, validate UTF-8, and reject the document under review, including aliases/hard links using the existing identity checks. Capture once at startup; do not reopen sources between turns or retries.
2. Before saving captured contents or starting reviewer processes, scan them with the repaired credential detector. For context there is no already-known-document exception: reject any recognized credential, naming only the file and credential kind. Document that this is best-effort detection, not clearance for sensitive material.
3. Store accepted exact bytes, labels, source paths, order, and content hashes in new context audit artifacts under the private run directory. Put the captured text into every review prompt as labeled untrusted material using the existing collision-resistant delimiters. Both reviewer roles receive the same captured context, although their evolving review history differs.
4. Enforce a documented 64 KiB aggregate context limit (a proposed cost control: roughly 16,000 extra input tokens on every call at about four bytes per token, varying with content and tokenizer).
5. Add a combined prompt budget as a new `Config` field with a new `--max-prompt-bytes` option. Count UTF-8 bytes after adding instructions, task, criteria, document, revision diff, selected history, context, retry feedback, delimiters, and backend wrappers, including Copilot's schema prefix. Check twice: (a) at startup, before any reviewer call, against the fixed parts (instructions, task, criteria, current document, context, wrappers), so an impossible run fails before spending calls; (b) before every subprocess call, against the full prompt. Reject overflow with a clear recorded error that names the largest sections; no silent truncation, and no retry of the same oversized prompt. A mid-run overflow (typically from a large revision diff) preserves the last accepted document and its snapshot so the user can rerun with a larger budget.
6. Compatibility: 0.4.5 accepts prompts far larger than any fixed small ceiling, and a full rewrite of a moderately large document produces a revision diff roughly twice the document's size. A low default (for example 256 KiB) would therefore reject runs that currently work, potentially partway through. Before release, measure the largest prompts in the supported examples and in recorded runs (including duo-on-duo plan reviews such as this one), then propose a default that accepts them with margin. If the proposed default would reject any input 0.4.5 accepts, it is an owner decision recorded in `CHANGELOG.md` as a compatibility change, not an engineering default. Byte limits are not exact token counts or a guarantee against model context exhaustion: CLI-added instructions, tool results, and output reserve also consume context.
7. Add a code-review example using a saved diff and test output. Test exact-byte capture, Unicode, invalid/nonregular inputs, document aliases, credential rejection before calls, aggregate context overflow, startup budget rejection before any adapter call, wrapper/history/diff/retry-induced mid-run overflow with the last document preserved, the `--max-prompt-bytes` override, unchanged captures after source edits, and audit write failure.

Acceptance: both reviewers receive identical captured context throughout a run; the audit reproduces its supplied bytes and hashes; recognized credentials and oversized prompts never reach a reviewer call; a run whose fixed inputs exceed the budget fails before the first call. Errors preserve the last accepted document. The chosen default accepts every measured supported example and recorded run, or its rejection of previously accepted inputs is recorded as an owner-approved compatibility change. A bounded live example demonstrates the supported workflow. Separately inspected repository files remain mutable and are not snapshotted by this feature.

## 4. Isolation profile — separate milestone with a feasibility gate

Status: open; retained separately because authentication and OS enforcement remain uncertain. Effort: medium feasibility investigation, then large implementation if feasible. Not a prerequisite for sections 2 or 3.

Relevant files: `src/duo/adapters.py`, `src/duo/copilot.py`, `src/duo/policy.py`, `src/duo/models.py`, `src/duo/cli.py`, `scripts/smoke_inspection.py`, `tests/test_environment.py`, `tests/test_policy.py`, new `tests/test_isolation.py`, `docs/SECURITY.md`, `docs/STATUS.md`.

1. Define the boundary separately for each backend/platform: allowed reads, writable scratch paths, network access, runtime dependencies, login resources, and credentials visible to the CLI or its tools. Demonstrate that authentication and the proposed boundary coexist. Evaluate Linux bubblewrap and macOS sandbox-exec or a separate account as candidates, not approved solutions. End the gate with recorded supported/unsupported combinations and a revised effort estimate.
2. Only after a successful gate, implement an explicit opt-in profile providing a read-only repository view, temporary home, enumerated writable scratch/cache/audit paths, and only required runtime/login resources. Do not expose unrelated home contents, projects, SSH keys, or cloud credentials. Provisioning reviewer credentials remains an explicit owner-controlled operation; the plan supplies no credentials.
3. Refuse unavailable mechanisms and unsupported combinations; never silently use ordinary execution. Preserve the ordinary mode's documented baseline rather than implying it has acquired this boundary.
4. Add deterministic boundary probes plus live CLI authentication/inspection checks for each claimed combination: outside canaries, symlinks, excluded login resources, writes inside and outside the repository, hooks, MCP launchers, conflicting configuration, and network behavior. A model declining a requested action is not enforcement evidence.
5. Test web-disabled and `--allow-web` operation separately. Record provider access and any additional permitted endpoints, along with remaining exposure. Filesystem isolation alone cannot prevent readable material reaching providers or URLs.

Acceptance: each supported combination has retained enforcement evidence and successful authentication/inspection evidence; forbidden operations fail and leave no write/hook/MCP sentinels. Required checks fail closed. Unsupported combinations are explicit. Expansion to sensitive or untrusted repositories requires a separate owner decision and evidence beyond this milestone.

## 5. Reviewer model diversity

Status: open; retained as cheap work restoring Duo's main cross-model property for Copilot users. Effort: small warning/documentation change; live recovery depends on the owner's available models.

Relevant files: `src/duo/models.py`, `src/duo/cli.py`, `src/duo/adapters.py`, `tests/test_duo.py`, `tests/test_copilot.py`, `tests/test_policy.py`, `README.md`, `docs/STATUS.md`, `tests/fixtures/`.

1. Add comparison of requested model IDs using a small explicit alias/family mapping. Report identical models, known same-family pairs, known different families, and unknown diversity. Test native/Copilot spelling differences and aliases. Do not infer effective routing or independence from role names or unfamiliar model strings; current policy metadata says effective model values are not exposed.
2. Warn for identical or known same-family selections in human summaries and `--json`, and retain the classification and its basis in audit metadata. Show uncertainty for unknown identities. Do not refuse these runs or change agreement semantics.
3. Owner dependency: establish which GPT models the owner's Copilot plan actually offers, using its interactive `/model` interface and current GitHub documentation. Verify an available choice in the Codex role on both platforms using bounded live runs, and combine these with the missing F3 probes in section 8. If none is available, keep the limitation explicit and document the already-supported native Codex plus Copilot Claude alternative; do not mark Copilot-only diversity restored.
4. Correct README and STATUS model lists to separate historical successes, later account refusals (`gpt-5.4`, `gpt-6-astra`, `claude-opus-5.5`), and currently verified availability. Investigate the proposed native `claude-fable-5-1` ID and `fable` alias; add successful-live claims only after obtaining a recorded run identifying the version, platform, requested model, and outcome. Copilot Fable remains conditional on availability.

Acceptance: offline tests cover identical IDs, equivalent aliases, same/different families, and unknown IDs across both output formats. Same-family agreement remains usable but visible. Restored Copilot diversity is marked done only with recorded successful two-family runs on the claimed platforms; warning delivery can ship independently of account availability.

## 6. Dependence on message wording

Status: open; promoted into the next maintenance release and started at the beginning of that cycle. Effort: medium, bounded by available structured interfaces.

Relevant files: `src/duo/policy.py`, `src/duo/adapters.py`, `src/duo/copilot.py`, `scripts/check_drift.py`, `scripts/record_fixtures.py`, `.github/workflows/drift.yml`, `tests/test_policy.py`, `tests/test_duo.py`, `tests/test_copilot.py`, `tests/test_drift.py`, `tests/fixtures/`, `tests/fixtures/README.md`.

1. Inventory parsing dependencies: `preferences_domain_absent`, `verify_claude_remote`, version parsing (`verify_version`), native help-flag substring checks and Codex feature-list column parsing in `verify_controls`, Copilot option/session-flag parsing, `classify_failure`, structured error extraction, and `retry_delay`. Record source, OS/CLI version, fallback, and whether changed wording causes safe refusal, retry/cost changes, or potential unsafe acceptance.
2. Prefer structured exit/status/error/configuration fields where available. Keep human-text matching only where needed; unknown policy states must still fail closed. Never classify ordinary review/tool content as a trusted CLI diagnostic, and never echo raw diagnostics into prompts.
3. After section 9.3, retain sanitized real output with OS/CLI provenance for unavoidable text parsing, including both macOS domain-absence forms and Claude remote-policy states. Add malformed, unknown, conflicting, localized/unrecognized, and misleading-prefix cases; retain Copilot's whole-option matching.
4. Extend drift reports and their workflow presentation to identify changed relevant wording/parsed states, rather than only changed flags/features. Collect safe configuration probes without model calls. Replay recorded failure diagnostics offline; do not claim a credential-free weekly job exercises every authenticated failure or model-availability case. If useful diagnostics require authenticated calls, document that manual trigger in section 8.

Acceptance: the inventory covers the production parsing sites; unknown safety-policy output refuses setup; recorded variants preserve expected behavior; drift tests show wording changes explicitly; authentication/model failures remain sanitized and correctly classified for recorded formats. Unsupported new messages are visible gaps, not silently accepted policy states.

## 7. Smaller improvements and owner decisions

### 7.1 Answer a blocked run in a fresh run

Status: open; retained, scheduled after context/prompt handling. Effort: medium.

Relevant files: `src/duo/cli.py`, `src/duo/models.py`, `src/duo/storage.py`, `src/duo/prompts.py`, `src/duo/workflow.py`, `tests/test_duo.py`, `tests/test_acceptance.py`, `README.md`.

1. Add `--answer TEXT` paired with a new explicit `--source-run PATH` option. Require a completed blocked source result corresponding to exit 3; never choose the latest run implicitly. Validate source structure, bounded input reads, repository identity, document path/mode, and the blocked turn's question and document hash against its recorded snapshot.
2. Preserve the original task/criteria association and record the source identity, question, old hash, current hash, and answer in the new run. If inputs are inconsistent, refuse clearly. Changed current document contents are allowed only as an explicitly recorded difference: review that current document afresh, without treating the answer as approval of it.
3. Include the answer as labeled user clarification within the prompt budget. Start with empty approvals and no checkpoint restoration; apply bounded input handling and the repaired credential check to the answer before provider submission.
4. Test multiple blocked runs, malformed/nonblocked sources, repository/document mismatches, changed current content, repeated use of the same source, and fresh approval requirements.

Acceptance: the selected question and answer are traceable in the new audit; source mistakes fail before reviewer calls; changed documents require two fresh approvals; source records and old approvals are never modified or resumed.

### 7.2 Codex login-shell environment reintroduction

Status: open; retained and moved earlier as a bounded feasibility investigation. Effort: small investigation; implementation effort depends on CLI support.

Relevant files: `src/duo/policy.py`, `src/duo/adapters.py`, `scripts/smoke_inspection.py`, `tests/test_environment.py`, `tests/test_policy.py`, `docs/SECURITY.md`, `docs/STATUS.md`.

1. Investigate `shell_environment_policy`, including inheritance/exclusion options, and any supported control of login-shell behavior. Distinguish the CLI's launch environment from variables its command shell later creates.
2. Use a disposable shell profile and harmless sentinel variables in a deterministic command probe with Duo's actual options/environment. Check both platforms; never print the owner's real environment or alter their profile. Establish whether filtering occurs before or after startup files and whether authentication still works.
3. Adopt only controls demonstrated to prevent reintroduction, with argv/configuration tests and retained live evidence. If no reliable control exists, close the investigation with the limitation still open and hand the unresolved boundary to section 4.

Acceptance: a recorded result establishes the actual behavior on each tested platform. A prevention claim requires the sentinel to remain absent despite the startup file attempting to set it; an inconclusive or ineffective setting is documented as such.

### 7.3 Python 3.10 retirement

Status: owner decision pending; retained. Effort: small engineering change plus dependency/CI validation.

Relevant files: `pyproject.toml`, `constraints.txt`, `scripts/update_constraints.py`, `.github/workflows/ci.yml`, `scripts/release.py`, `src/duo/workflow.py`, `README.md`, `docs/STATUS.md`, `CHANGELOG.md`.

1. Ask the owner to choose the support policy before the next minor release: retire 3.10 then, or explicitly retain it until a pinned dependency drops support. Until decided, keep the existing support declaration and tests; do not silently remove an interpreter.
2. When retirement is approved, update the Python requirement, classifiers, CI matrix, constraints-generation inputs, release-notes template, installation/error text (including the LangGraph message in `build_graph`), and support documentation together. Regenerate constraints for the remaining supported interpreters.
3. Run installed-wheel/sdist tests across the resulting matrix and verify package metadata rejects the retired interpreter.

Acceptance: the owner's decision is recorded; if retirement proceeds, requirements, documentation, dependency pins, and passing installed-artifact CI agree on the supported minimum.

### 7.4 Copilot search-only GitHub MCP access

Status: deferred investigation, retained because web fetching already works but search is absent. Effort: small feasibility check; potentially medium implementation and live validation.

Relevant files: `src/duo/copilot.py`, `src/duo/policy.py`, `scripts/check_drift.py`, `tests/test_copilot.py`, `tests/test_web.py`, `tests/fixtures/`, `README.md`, `docs/SECURITY.md`.

1. Determine whether the built-in GitHub MCP server supports an enforceable search-only tool subset under Duo's no-interaction settings. Do not enable the whole server merely to obtain search.
2. If feasible, add an explicit least-privilege configuration, preflight checks, and fixtures. Exercise attempted repository writes and other excluded tools as well as successful search, with deterministic checks where available and live evidence of actual requests/denials.
3. If unsupported, record the reason and keep built-in MCP disabled and Copilot web access fetch-only. Revisit only when CLI capabilities change.

Acceptance: search is enabled only if excluded operations demonstrably remain unavailable and existing controls pass; otherwise a documented negative feasibility result closes the investigation without changing behavior.

### 7.5 Optional PyPI publication

Status: deferred owner decision; GitHub-only distribution remains the recorded baseline. Effort: small/medium after the decision and external package-name availability check.

Relevant files: `pyproject.toml`, `scripts/release.py`, `.github/workflows/release.yml`, `tests/test_release.py`, `README.md`, `docs/STATUS.md`.

1. Owner chooses whether to add PyPI and confirms an available package name. `duo-plan` is the current distribution name, not evidence of PyPI ownership. Record consequences for hard-coded artifact names if a rename is chosen.
2. Only if approved, design trusted publishing for the already-built, verified artifacts, with a narrowly scoped protected publishing job. Preserve exact-commit CI, checksums, provenance, and explicit publication approval.
3. Validate the configured publisher and package metadata through an appropriate staging flow, then verify installation from the exact uploaded artifacts when publication is separately authorized.

Acceptance: an approved distribution decision and a tested publishing/install procedure exist before publication; declining PyPI leaves the current working release process unchanged.

## 8. Open live verification

Status: open; all original gaps retained, with F3 and review-specific gaps added. Effort: small per bounded experiment, substantial in aggregate; machine/model access and authorized call budgets govern scheduling. These checks accompany affected releases rather than being postponed indefinitely.

Relevant files: `scripts/smoke_inspection.py`, `scripts/record_fixtures.py`, `scripts/check_drift.py`, `tests/fixtures/`, `tests/test_fixtures.py`, `tests/test_policy.py`, `tests/test_web.py`, new `tests/test_smoke_inspection.py`, `docs/STATUS.md`, `docs/SECURITY.md`, `README.md`.

Apply this ordered procedure to every row below:

1. Identify candidate commit, installed artifact, OS/architecture, exact CLI versions, requested models, backend pair, mode, web setting, and bounded cost/time budget. Run relevant offline checks and preflight first.
2. Use synthetic repositories, canaries, and disposable configuration. Retain sanitized commands, configuration records, terminal outcomes, and relevant tool events before temporary smoke directories are deleted. Extend the smoke script with an explicit evidence-export option and web-aware probing where needed; these are new capabilities, not existing flags.
3. Classify each operation as requested, successfully accessed, explicitly denied, not attempted, or unknown. Extend reporting tests so absence of a canary alone cannot be labeled denial. Unexpected success where confinement is claimed fails the check; native Codex's known outside reads remain documented baseline behavior.
4. Record outcomes and remaining gaps in STATUS and correct corresponding SECURITY/README claims. A declined action or unavailable model leaves the evidence gap open. Do not generalize one model/version/platform result to an entire accepted range.

| Open verification | Specific evidence required |
| --- | --- |
| Codex 0.164–0.166 on macOS; 0.167 on Linux | Relevant version probes, inspection smoke, deterministic write denial, and bounded Duo run. If an older version is no longer obtainable or worth maintaining, explicitly record the deferred historical gap and owner support decision rather than crediting a newer run to it. |
| Copilot 1.0.103+ on macOS | Exact version with `--sync-session=off`, effective settings, startup/confinement evidence, and bounded run. Passing the flag is not independent proof of GitHub service-side behavior. |
| Fable natively and through Copilot | Section 5's native ID/alias verification; Copilot checks only once the owner's account offers it. Record separate native and Copilot outcomes. |
| Document modes through Copilot on macOS and with mixed pairs | Each missing mode/backend/platform combination produces valid structured reviews and preserves document/approval semantics. |
| `--allow-web` on macOS, with mixed pairs, and in plan/description/review modes | Actual permitted tool requests/results for synthetic public sources, retained logs, and unchanged read-only/MCP restrictions. Missing Claude tool events remain a limitation until section 2 passes. |
| F3: Copilot GPT outside temporary/home reads | Actual attempted operations with explicit denial or observed access for an available GPT model; preserve the already-recorded symlink result and do not substitute same-family Claude runs. Native Codex search-tool refusals remain untested operations, not confinement successes. |
| Authenticated policy and network boundaries | Record the credential context of managed-policy probes without values. Test relevant authenticated behavior only in an authorized disposable setup; managed deployments remain unsupported. For network claims use deterministic connection attempts to controlled endpoints with a working comparison path; DNS failure alone does not prove network denial. Coordinate stronger boundary claims with section 4. |

Acceptance for each row: retained evidence supports exactly the stated combination and outcome, or the row remains explicitly open/deferred with its reason. Historical deterministic Codex write-denial evidence is preserved. Live coverage is never described as complete merely because smoke prompts were sent.

## 9. Security self-review remediation

The prescribed self-review has been produced as `review.md`; repeating it is not the next task. F1–F6 are supported by source/fixture inspection and are all accepted into this plan. The review demonstrated no sandbox escape or live credential disclosure. Its historical live evidence is not newly reproduced evidence.

### 9.1 F1 — credential-scanner performance and post-validation deadline

Priority: first implementation work; prerequisite for context scanning. Effort: medium because format compatibility and already-known-value semantics must be preserved.

Relevant files: `src/duo/models.py` (`SECRET_PATTERNS`, `new_secret`, `validate_review`), `src/duo/workflow.py` (`Reviewer._review`), `tests/test_duo.py`, `tests/test_acceptance.py`, `docs/SECURITY.md`, `CHANGELOG.md`.

1. Add reproductions for repeated `sk-` near-matches and repeated GitHub-shaped matches whose only known occurrence is near the end of a large current document. Include size-doubling measurements and maximum-size cases with a bounded test-process timeout. `review.md` records about 0.058, 0.230, and 0.904 seconds at 12,288, 24,576, and 49,152 bytes for the first case (a 1 MiB input did not finish within five seconds), and about 0.07–3.8 seconds from 128 KiB to 1 MiB for the second; these are the self-review's measurements on one Linux/Python 3.12.3 machine, to be reproduced by the new tests.
2. Remove repeated suffix rescanning in marker-based token detection. Deduplicate candidate values and use indexed or multi-pattern known-text lookup instead of rescanning the whole known document for each occurrence. Cover distinct candidates as well as duplicates. Preserve current format detection, normalized line endings, separate response fields, substring-based already-known behavior, and PEM protections; do not fix performance by broadly exempting credentials or reducing supported inputs without justification.
3. Recheck the run deadline after validation and before accepting, publishing, or displaying its result. Record timeout/limit outcomes and preserve the previous document and approvals. Test a clock crossing the deadline during validation for both approval and revision.
4. Retain the existing five-second per-case CI guard at the 1 MiB input limit, add the new adversarial cases and scaling measurements, and document measured runtime with interpreter/platform details. Remove the unsupported blanket “well under a second” claim unless new measurements establish it; do not replace the performance requirement with documentation alone.

Acceptance: detection/known-value regression tests pass; both reported worst cases complete within the measured CI guard without quadratic scaling; a response validated after the deadline cannot publish a revision or produce agreement. Context credential checking must not ship before these checks pass.

### 9.2 F2–F4 — match assurance and audit claims to evidence

Priority: next maintenance release, with inexpensive documentation corrections early. Effort: small.

Relevant files: `docs/SECURITY.md`, `docs/STATUS.md`, `README.md`, `src/duo/policy.py`, `src/duo/copilot.py`, `src/duo/adapters.py`, `tests/test_policy.py`, `tests/test_copilot.py`, `scripts/check_drift.py`, `CHANGELOG.md`.

1. For F2, distinguish settings supplied, advertised options checked, effective metadata verified, and behavior exercised historically. Update policy-record descriptions and tests alongside documentation; preserve every existing restriction and fail-closed probe. State that empty `CODEX_HOME` removes the usual stored-login location, but the MCP probe can retain allowed authentication variables and explicit `--pass-env` additions. “No model calls” does not mean “no credentials”; correct that terminology wherever the affected probe tooling is described.
2. Credit the deterministic Codex write-denial probes recorded in STATUS by platform/version separately from the inconclusive model-directed attempt. Do not promote historical tests into a per-run enforcement claim.
3. For F3, correct the Copilot table and STATUS summary: GPT temporary/home reads were not attempted in the historical matrix; its symlink read was denied. Later two-Claude-role checks do not fill that GPT gap. Native Codex's declined dedicated-search requests are not search-confinement tests. Track the missing evidence in section 8.
4. For F4, explain that disabling web does not prevent readable information reaching the model provider. Web adds search-provider and arbitrary-URL disclosure paths. Distinguish saved reviewed-document snapshots from partial emitted tool transcripts; neither file nor web evidence is guaranteed complete. Retain section 2's improvement and its limitations.
5. Audit the revised claims against the actual probe code, fixtures, and historical STATUS entries; add policy metadata assertions where the record changes. Keep unavailable authenticated policy/network evidence explicitly unverified.

Acceptance: each security-table assurance states what establishes it and on which scope; policy metadata does not claim unperformed checks; README and STATUS agree with the detailed evidence; F3's remaining tests and F4's audit omissions stay visible without weakening controls.

### 9.3 F5 — fixture probes use the production environment

Priority: before any new native fixture capture. Effort: small.

Relevant files: `scripts/record_fixtures.py`, `src/duo/policy.py`, `src/duo/adapters.py`, `tests/test_environment.py`, `tests/test_fixtures.py`, new `tests/test_record_fixtures.py`, `tests/fixtures/README.md`.

1. Pass `reviewer_environment` explicitly to every native version/help/feature/doctor probe. Layer the temporary `CODEX_HOME` onto that restricted Codex environment instead of `os.environ`. Preserve Copilot's existing environment builder and align required managed-configuration check ordering with production probes.
2. Add environment-capture regression tests using synthetic secrets and injection variables: unrelated credentials, provider overrides, and `NODE_OPTIONS` must be absent; intended authentication/runtime names must remain. Record names, never real values. Verify the temporary-home override does not restore dropped variables.
3. Correct the recorder's claim of credential-free probes: production-equivalent native probes can retain permitted authentication. Document provenance of existing fixtures; refresh affected ones after the fix when authorized rather than retroactively claiming they used the restricted environment.

Acceptance: tests exercise every native recorder subprocess and establish the intended environment; refreshed fixtures remain sanitized and replayable; no recorder path silently inherits the full parent environment.

### 9.4 F6 — reliable offline test discovery

Priority: before using the runner to validate the remediation release. Effort: small.

Relevant files: `scripts/run_tests.py`, new `tests/test_run_tests.py`, `.github/workflows/ci.yml`, `scripts/release.py`, `docs/SECURITY.md`, `docs/STATUS.md`, `README.md`.

1. Resolve the intended tests directory relative to the runner's own file, independently of the caller's working directory. Continue testing the installed package in CI/release checks; do not inject `src/` to make artifact tests pass.
2. Fail explicitly on zero discovered tests as well as failures, errors, skips, and missing LangGraph. Correct SECURITY's nonexistent relative invocation and align the other instructions with the working command.
3. Test invocations from repository root, `tests/`, and an unrelated directory; an intentionally empty discovery target; a skipped/failing test; and missing LangGraph. Run both installed artifacts through the corrected runner.

Acceptance: all supported invocation directories discover the same nonzero suite; empty/skipped/failed suites return nonzero; installed-wheel and installed-sdist checks still import the installed distribution. Existing CI used the correct working directory, so this fix does not imply its historical runs executed zero tests.

## Execution order and release gates

1. **Completed baseline:** release tooling, section 1. The security self-review is also complete as an input; its six findings remain open work in section 9.
2. **Proposed 0.4.6 maintenance release:** fix F1 first (9.1), correct assurance/evidence statements (9.2), repair fixture recording before new captures (9.3), and repair the runner before relying on release checks (9.4). These reduce a demonstrated availability risk and restore trust in verification at relatively low cost.
3. **Within that maintenance cycle:** start section 6's inventory at the beginning of the cycle, then its guarded parsing improvements and drift diagnostics, so the next OS or CLI wording change produces a visible drift report rather than a broken setup. Ship section 5's cheap diversity warnings and accurate model lists in the same cycle. Start owner model discovery immediately, but do not hold scanner fixes for account availability. Run section 7.2's bounded login-shell investigation; an unresolved result must not be disguised as prevention or delay unrelated fixes.
4. **CLI range extensions continue independently:** a drift-detected new CLI minor version (for example Codex 0.168, which Codex's self-update installs without the owner acting) follows the existing probe-and-live-evidence procedure used for 0.4.4 and 0.4.5 and may ship as its own patch release. Section 6 must not be made a precondition for those releases, because blocking them would make Duo refuse users' auto-updated CLIs. Each such release reuses whatever section 6 coverage exists at the time and records new wording in fixtures.
5. **Proposed 0.5.0:** section 2's Claude audit stream, followed by section 3's captured context and combined prompt budget, including the owner's approval of the default budget if it rejects anything 0.4.5 accepts. Audit comes first by default because the self-review confirms an existing observability gap, including web-enabled runs; context follows the repaired scanner. These can ship separately if live format discovery delays section 2—there is no technical dependency requiring context to wait for a new Claude transcript. Resolve the Python-support owner decision before finalizing this minor release.
6. **Following feature release:** section 7.1's blocked-run answer flow after context/prompt handling. Schedule section 7.4 only when search-only enforcement is plausible; section 7.5 only after the owner's distribution decision. Neither optional investigation blocks core fixes.
7. **Separate isolation milestone:** section 4's feasibility gate, then only the supported implementation selected by its evidence. Do not bundle an unproven OS boundary into the audit/context releases or expand the supported deployment contract implicitly.
8. **Throughout:** execute section 8's relevant live checks with each affected change and available machine/model. Prioritize missing GPT confinement checks when diversity becomes available, native Claude tool evidence with section 2, and environment/network probes with sections 7.2 and 4. Keep historical gaps explicitly open if they cannot be filled.

Owner decisions, not engineering tasks: which models the owner's Copilot plan offers (section 5); the default prompt budget if it rejects previously accepted inputs (section 3); when to retire Python 3.10 (section 7.3); whether to publish on PyPI and under what name (section 7.5); whether to keep maintaining historical CLI versions that are no longer obtainable (section 8); and any expansion of the supported deployment contract (section 4).

For each proposed release, implement only separately authorized work; add a regression test that fails without each guarantee-affecting fix; run the strict nonempty offline suite against wheel and sdist on the supported CI matrix; run applicable fixture/drift checks; and obtain live evidence for changed CLI behavior before claiming support. Record exact versions, platforms, models, outcomes, and limitations in STATUS and the changelog. A documentation-only correction needs evidence review, not invented new live results. A release must either satisfy its changed-behavior gates or explicitly exclude the unfinished feature from its claims.

Follow section 1's release procedure only when publication is authorized: verify successful CI for the exact target commit, inspect the workflow-built artifacts and provenance, and complete owner-controlled publication checks. This plan itself performs none of those actions.

All former open items are accounted for: sections 2–8 are retained with explicit dependencies, evidence corrections, or owner-gated deferrals; F1–F6 map to sections 9.1–9.4, with F3 follow-up in section 8 and F4 follow-up in section 2. No unresolved finding is marked fixed by writing this plan.

Outside scope: persistent graph checkpoint recovery, distributed or multi-user orchestration, automatic implementation of generated plans, additional reviewer backends, and Windows support.

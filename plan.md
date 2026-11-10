# Duo improvement plan

## Goal and scope

Improve Duo from 0.4.5 without weakening what it already guarantees: two
read-only reviewers, a run that succeeds only when both approve the same
document hash, a restricted reviewer environment, and an audit record of every
attempt. Agreement does not prove a document is correct.

This plan replaces the production-readiness plan, most of which shipped in
0.1.0–0.4.1; that plan is in Git history (`git show 918d2c5:plan.md`), and
`docs/STATUS.md` records what was done. Written after 0.4.1; updated on
2026-11-10 after 0.4.5. Writing this plan does not authorize
publishing a release, distributing credentials, or making model calls.

## Starting point

- 0.4.5 is released; CI runs 182 offline tests on Ubuntu and macOS, Python
  3.10–3.14. Releases are built, attested, and drafted by the repository's own
  release workflow (section 1). A weekly job checks the newest CLIs for drift.
- Tested CLI ranges: Codex 0.162.1–0.167.x, Claude Code 2.1.x, Copilot 1.0.x.
  Live evidence is uneven across platforms: Codex 0.164–0.166 and Copilot
  1.0.103+ ran live on Linux only, Codex 0.167 on macOS only.
- All four backend combinations have run live on Linux and macOS. Document modes
  have not run through Copilot on macOS or with mixed pairs; `--allow-web` has run
  only on Linux, in report mode, with native and Copilot pairs.
- Since 0.4.2, Copilot refuses `gpt-5.4` and `gpt-6-astra` for the owner's
  account, so live Copilot pairs have used `claude-sonnet-5.5` in both roles: the
  Copilot backend currently gives Duo no second model family.
- Native Codex can read any file the user can read. There is no isolation
  profile, so Duo is supported only on trusted repositories. Codex runs its
  commands in a login shell, which can reintroduce variables that Duo's
  restricted environment dropped.
- Native Claude runs with `--output-format json`: the audit record has its final
  answer but not its tool calls. Token usage is present in the recorded fixture;
  the web-search and web-fetch counters stayed at zero in the documented live
  web checks, even when those tools were used. The requested files and URLs are
  not recorded as tool calls.
- Native Codex can run read-only commands such as `git log` and `git diff`, and
  read saved test results. Claude and Copilot have no shell but can read logs
  inside the repository. Context files would give both reviewers the same
  captured evidence, including files outside their accessible directory.
- Setup depends on the exact wording of OS and CLI messages. macOS 27 changed
  `defaults read`'s message for a missing domain, which made Duo refuse native
  Claude and Copilot until 0.4.5.
- Python 3.10 reached end of life in October 2026; Duo still supports it.

## 1. Release tooling in the repository (done in 0.4.2)

Relevant files: new `scripts/release.py`, new `.github/workflows/release.yml`,
`docs/STATUS.md` ("Releasing").

- [x] Add `scripts/release.py` that, for a given commit:
  - builds the wheel and sdist from a clean clone with a pinned build environment;
  - installs each into a fresh environment with `constraints.txt`, runs the test
    suite from the installed package, and runs `duo --help` and `duo-clean --help`;
  - checks that the wheel's package files match the commit;
  - writes `SHA256SUMS` and a release-notes template with the checksums filled in.
- [x] Add a release workflow triggered by a `v*` tag that builds the files with
  the same steps, writes `SHA256SUMS`, and attaches them with build-provenance
  attestations to a draft release. Builds are not byte-reproducible, so the
  published files are the workflow's, not a local build's. Give only this job
  `contents: write`, `attestations: write`, and `id-token: write`; never run it on
  pull requests. The OIDC permission is required by
  [GitHub's artifact-attestation workflow](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
- [x] Reject tags whose version does not match the package metadata. Before
  publication, require successful results from all required CI jobs for the
  exact commit the tag resolves to, and verify the release assets' checksums and
  provenance against that commit.
- [x] Keep live runs, the drift check, and publishing the draft as manual steps
  that need the owner's approval. Update "Releasing" in `docs/STATUS.md`.

Acceptance: on either machine, `scripts/release.py` runs the pre-release checks
from a fresh clone with the repository's own tools; the release workflow builds
and publishes the files, which are traceable to the tagged commit. A version
mismatch or a missing, failed, or cancelled required CI result prevents
publication.

## 2. Native Claude tool calls in the audit record

Relevant files: `src/duo/adapters.py`, `src/duo/policy.py`,
`scripts/record_fixtures.py`, `scripts/check_drift.py`, `tests/fixtures/`,
`docs/SECURITY.md`.

- [ ] Verify live what `--output-format stream-json --verbose` emits for
  `Read`, `Glob`, `Grep`, `WebSearch`, and `WebFetch`: requests and inputs,
  results or permission denials, the final structured result, and usage. Record
  which fields are available; do not infer successful access from a request.
- [ ] Parse the final result from the stream; keep the existing schema
  validation, limits, and failure classification. Preserve emitted tool requests
  and available results or denials in the attempt's audit files. Mark incomplete
  streams and unavailable usage explicitly; zero usage alone is not a failure.
- [ ] Add tests for malformed and truncated streams, missing final results,
  interrupted attempts, tool denials, and unsuccessful final results. Partial
  assistant messages must never be accepted as a completed review.
- [ ] Record new fixtures for the tested Claude Code version and extend the drift
  check to the flags this needs.
- [ ] Update `docs/SECURITY.md` and the release notes with the observed coverage
  after live checks, including `--allow-web`. Retain limitations for anything
  the stream does not expose; a recorded URL is not an archive of page contents.

Acceptance: successful, denied, and interrupted native Claude attempts retain
the tool events the CLI emits, with requests distinguishable from confirmed
results and missing completion or usage explicitly marked. Live fixtures
demonstrate coverage for each enabled tool. The audit does not claim to enumerate
every file read internally by a search or fetch tool.

## 3. Context files for reviewers

Relevant files: `src/duo/cli.py`, `src/duo/storage.py`, `src/duo/prompts.py`,
`src/duo/models.py`, `README.md`.

- [ ] Add `--context-file PATH` (repeatable): files such as a saved diff or test
  output, read once at startup with `read_input` (regular UTF-8 files, bounded).
  Capture their exact bytes in the run's audit record with their labels, paths,
  and content hashes. Include the captured text in every prompt as labeled,
  untrusted material; do not reread the source files between turns or retries.
- [ ] Check each context file for credentials when it is captured, with the
  patterns Duo already applies to reviewer responses (`SECRET_PATTERNS` in
  `src/duo/models.py`). Refuse the run before any reviewer starts, naming the
  file and the kind of credential without printing it. Saved logs and test
  output are where tokens usually appear, and the response filter runs only
  after the material has reached both providers.
- [ ] Enforce an aggregate limit on the supplied context-file bytes, in
  addition to the existing separate task, criteria, and document limits. Context
  is sent in every prompt of every turn and retry, so choose the limit from
  cost, not only from safety: 1 MiB is roughly 250,000 tokens per call. Start
  well below that (for example 256 KiB) and state the per-call cost in the
  README. Reject a context file that is the document under review. Test
  aggregate overflow and source-file changes after capture.
- [ ] Add a per-call prompt budget: before each call, check the total size of the
  instructions, task, criteria, document, history, and context against a
  documented limit, and stop with a clear error instead of sending a prompt the
  model cannot accept. The separate 1 MiB limits allow a combined prompt far
  larger than any model's context window.
- [ ] Document that context files go to the model providers like the task, and
  add a code-review example (diff and test output as context, `--mode review`).

Acceptance: a reviewer can review a change and its test results without the
user copying them into the repository. Both reviewers receive the same captured
context throughout the run, even if its source files change, and the audit record
shows exactly what was supplied. A context file containing a recognized
credential never reaches a reviewer. This does not freeze repository files the
reviewers inspect separately.

## 4. Isolation profile

Relevant files: `src/duo/adapters.py`, `src/duo/copilot.py`, `src/duo/policy.py`,
`scripts/smoke_inspection.py`, `docs/SECURITY.md`.

Treat this as a separate milestone with a feasibility gate, not a prerequisite
for the context-file and audit improvements.

- [ ] Before implementation, define the allowed reads, writes, network access,
  and login resources for each backend and platform. Specify how credentials
  are provisioned and which remain accessible to the reviewer process or its
  tools. Demonstrate that authentication and the boundary can coexist; mark
  combinations that cannot satisfy it unsupported for this profile.
- [ ] Run each reviewer CLI, when the profile is selected, with:
  - a read-only view of the repository;
  - a temporary home directory;
  - explicitly listed writable scratch paths (its audit attempt directory);
  - only the runtime and login resources it needs.

  Do not expose the user's home directory, unrelated projects, SSH keys, or cloud
  credentials. Evaluate `bubblewrap` on Linux and `sandbox-exec` or a separate
  user on macOS as candidates; accept a mechanism only after it demonstrates the
  defined boundary.
- [ ] Validate separately on each platform. If equivalent enforcement cannot be
  shown, mark that platform unsupported; never fall back silently to ordinary
  execution.
- [ ] Extend the confinement tests with canary files outside the repository,
  symbolic links, hooks and MCP launchers, conflicting configuration, and
  attempted writes; check that forbidden reads fail and forbidden actions leave
  no sentinel. Test every supported backend/platform combination, including
  attempts to access excluded login resources. Use deterministic probes of the
  boundary as well as live CLI checks: a model declining to attempt an operation
  is not proof that the operation is denied.
- [ ] Re-test `--allow-web` under the profile and state in `docs/SECURITY.md`
  what it does and does not protect. Repository content still reaches the model
  providers, and web tools can transmit content the reviewer can read. Filesystem
  isolation alone does not establish safe handling of sensitive data or justify
  expanding support to untrusted repositories.

Acceptance: every backend/platform combination claimed by the profile has
recorded enforcement evidence for forbidden reads and writes, symlink escapes,
excluded credential access, and inert hooks and MCP launchers, plus successful
live authentication and repository inspection. Required checks fail closed;
unsupported combinations never fall back to ordinary execution. Document the
remaining provider and web exposure before any separate decision to expand the
supported deployment contract.

## 5. Reviewer model diversity

Duo's value is two different models checking each other. Through Copilot that
currently fails silently, because both reviewers run the same model.

- [ ] Find which GPT models the owner's Copilot plan offers (Copilot has no
  model-listing command; check its interactive `/model` and GitHub's model
  documentation), and verify one live in the Codex role on both platforms.
- [ ] Warn, in the summary and `--json` output, when both reviewers use the same
  model, or models from the same family, so a single-model "agreement" is
  visible. Do not refuse such runs: they can still be useful.
- [ ] Update the model lists in `README.md` and `docs/STATUS.md` (they still name
  `gpt-5.4` for Copilot), and add Claude Fable (`claude-fable-5-1`, alias
  `fable`), which completed a live native review on 2026-11-10.

## 6. Dependence on message wording

- [ ] List every place Duo decides by matching OS or CLI message text: the
  managed-preferences checks (`defaults read`), `claude doctor`'s remote-policy
  states, failure classification (`classify_failure`), and Copilot's error
  messages. For each, record where the wording comes from and what happens if it
  changes: a refusal is safe but blocks users, an acceptance could weaken a
  control.
- [ ] Prefer structured signals (exit codes, JSON fields) where the tool offers
  them. Where text must be matched, keep recorded real output per OS and CLI
  version in `tests/fixtures/`, and have the drift check report a change in
  wording, not only in flags.

## 7. Smaller improvements

- [ ] Add `--answer TEXT` with an explicit source-run selector for a run that
  stopped with exit code 3. Require a blocked source run and validate its
  repository and document identity; never choose the latest run implicitly.
  Record the source run, its question and document hash, and the user's answer
  in a new run's audit record and prompts. Review the current document with fresh
  approvals; do not resume a graph checkpoint or restore old approvals. Test
  multiple blocked runs, mismatched sources, and changed document contents.
- [ ] Investigate Codex's `shell_environment_policy` (for example `inherit` and
  `exclude` settings passed with `-c`) so its commands cannot reintroduce dropped
  variables through the login shell. Verify with a deterministic probe that runs
  `env` under Duo's options; update `docs/SECURITY.md` with the result.
- [ ] Decide when to drop Python 3.10, now past end of life: when a pinned
  dependency drops it, or at the next minor release. Update `requires-python`,
  the classifiers, the CI matrix, and the README together.
- [ ] Investigate whether Copilot can enable only the search tools of its
  built-in GitHub MCP server, without the tools that write to repositories. Enable
  web search through Copilot only if that can be enforced and tested.
- [ ] Publish to PyPI through trusted publishing from the release workflow, if
  the package name is available and the owner agrees.

## 8. Open live verification

- [ ] Codex 0.164.x to 0.166.x on macOS, and 0.167.x on Linux.
- [ ] Copilot CLI 1.0.103 and later (`--sync-session`) on macOS; this Mac still
  runs 1.0.95.
- [ ] Claude Fable through Copilot, once Copilot offers it.
- [ ] Document modes through Copilot on macOS, and with mixed backend pairs.
- [ ] `--allow-web` on macOS, with mixed pairs, and in plan, description, and
  review modes.

Record results in `docs/STATUS.md`.

## Execution order

1. ~~Release tooling (section 1).~~ Done in 0.4.2.
2. A Duo self-review of the security claims against the code (`--mode review` on
   a worktree of the latest tag, checking `docs/SECURITY.md`); confirmed findings
   may change the order below.
3. Reviewer model diversity (section 5): cheap, and it restores Duo's main
   property for Copilot users.
4. The owner chooses between context files (section 3) and the Claude audit
   record (section 2), by whether code reviews or `--allow-web` matter more.
5. Message-wording dependencies (section 6), before the next OS or CLI change
   breaks setup again.
6. The isolation profile (section 4) as a separate milestone, starting with its
   feasibility gate. Keep the existing supported deployment contract until
   evidence and an explicit decision justify changing it.
7. Sections 7 and 8 as time allows; section 8 whenever the relevant machine is
   available.

Each change that affects a documented guarantee needs a test that fails without
it, and live evidence before release, as for 0.4.1.

Outside the scope of this plan:
- persistent graph checkpoint recovery;
- distributed or multi-user orchestration;
- automatic implementation of the generated plan;
- additional reviewer backends;
- Windows support.

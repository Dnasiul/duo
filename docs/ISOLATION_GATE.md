# Isolation feasibility gate — plan §4, step 1

Recorded 2026-11-25, America/New_York; machine-readable results use
2026-11-26 UTC timestamps. Evaluated Duo 0.8.2 source at
`5cebbf444e64691418deecc088a58d0f89e7dc4b`, on local branch `gate-isolation`
created from `main`. This record covers the feasibility gate only. The opt-in
profile and plan §4 steps 2–5 have not been implemented. `plan.md` is unchanged.

## Verdicts

`SUPPORTED` below means that the specified candidate passed the deterministic
battery and completed authenticated work on this machine. It does not expand
Duo's supported deployment contract or certify other CLI versions/installations.
A model declining an operation is never counted as enforcement evidence.

| Backend / login-resource variant | Linux bubblewrap | macOS sandbox-exec / separate account |
| --- | --- | --- |
| Native Codex, `~/.codex` read-only | **UNSUPPORTED**: CLI initialization fails with `Read-only file system (os error 30)`; SQLite read-write opens also fail with `EROFS`. No completed authenticated turn. | **UNEVALUATED**: no macOS machine available; both mechanisms need the protocol below. |
| Native Codex, `~/.codex` writable | **SUPPORTED** for the boundary below: battery passes, actual nested command returns an OS file error, and the confined native Duo pair agrees. | **UNEVALUATED**: Linux evidence does not establish macOS authentication, startup behavior, or enforcement. |
| Native Claude, `~/.claude` and `~/.claude.json` writable | **SUPPORTED** for the boundary below: battery passes, authenticated repository inspection and approval complete in the confined native Duo pair. | **UNEVALUATED**: both mechanisms and credential provisioning remain untested. |
| Copilot, environment `GH_TOKEN`, no owner config bind | **UNEVALUATED** for completed model work: battery and confined preflight pass, but both confined and unconfined authenticated requests return HTTP 402 `quota_exceeded`. | **UNEVALUATED**: no macOS probes or completed confined model call. |

Copilot's matched account-quota failure is not evidence of a confinement
incompatibility, and it is not a completed model response. It therefore establishes
neither support nor an OS-boundary blocker. Its verdict remains UNEVALUATED until
a completed authenticated response is obtained. The owner's approximate
2026-12-01 quota-reset expectation was not independently established by these runs.

## Machine, executable layout, and environment

- Observed root filesystem: Ubuntu **22.04.5 LTS**, x86-64; kernel
  `5.15.0-204-generic`, build `#214-Ubuntu SMP Fri Nov 6 00:52:52 UTC 2026`.
  Probe user/group IDs: `1001:1001`. These are the gate's observed values,
  independently of earlier evidence collected in other environments.
- Bubblewrap **0.6.1**, `/usr/bin/bwrap`, mode 0755 without setuid.
  `kernel.unprivileged_userns_clone=1`; `user.max_user_namespaces=2147483647`.
  A trivial `bwrap --unshare-user --ro-bind / / --proc /proc --dev /dev
  --tmpfs /tmp /bin/echo DUO_GATE_USERNS_OK` actually succeeded, exit 0.
- Native Codex **0.172.0**: `~/.local/bin/codex` resolves to
  `~/.codex/packages/standalone/releases/0.172.0-x86_64-unknown-linux-musl/bin/codex`;
  an ELF static PIE executable, approximately 307 MiB. Its executable itself
  requires the `~/.codex` bind, in addition to its login/runtime state.
- Claude Code **2.1.332**: `~/.local/bin/claude` resolves to
  `~/.local/share/claude/versions/2.1.332`; an ELF dynamically linked executable,
  approximately 384 MiB.
- Copilot's `~/.local/bin/copilot` entry point is an ELF executable, approximately
  177 MiB. The initial plain `--version` reports **1.0.96**. Under Duo's actual
  `--no-auto-update` invocation and isolated cache, both preflight and the matched
  model-call streams identify the running CLI as **1.0.116**. The corresponding
  `copilot-cache/pkg/linux-x64/1.0.116/package.json` confirms `@github/copilot`
  version 1.0.116. That staged package includes `cli-main.js`, native `.node`
  libraries, and bundled search/runtime components. The launcher version alone
  is insufficient to identify the executed runtime.
- Host Node is **22.22.1** at `~/.nvm/versions/node/v22.22.1/bin/node`. Node and npm
  are hidden by this home overlay. The tested packaged CLIs launch without an
  external `node` on PATH; this does not establish compatibility for npm-based
  installations needing a runtime elsewhere in the covered home.

Native launch environments come from unchanged
`duo.policy.reviewer_environment`; no `--pass-env` additions were used.
Their baseline variable names were `DBUS_SESSION_BUS_ADDRESS`, `HOME`, `LANG`,
`LC_ALL`, `LC_CTYPE`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`, and
`XDG_RUNTIME_DIR`. For the successful Duo run, the already-allowlisted `TMPDIR`
was set to the bound scratch path on both sides of the wrapper. The allowlist
itself was not changed. In particular, it does not pass `BASH_ENV`.

Copilot uses unchanged `duo.copilot.environment`: fresh configuration/cache in
the bound state tree, Duo's settings variables, and **one** token under `GH_TOKEN`.
Initially no token was available. The owner subsequently identified
`~/.copilot-token`; the host harness reads that existing file into memory and
supplies its value through the environment. Its value is never printed, placed
in argv, copied into fixtures, or committed. The source file remains absent
inside every confinement (`ENOENT`); the Copilot process nevertheless receives
`GH_TOKEN`. Credential-store probes use open/close or presence checks only.

## Evaluated Linux boundary

All probe fixtures and wrappers are under `~/duo-gate/probe/`, outside Duo's
checkout. The target is the small Git repository `probe/target-repo`, containing
a greenfield README and `escape-link`, an absolute symlink to
`probe/outside/canary.txt`. The canary contains a unique synthetic marker; its
SHA-256 is `85e5f374a05fd01940198d48935033bd33e2b4310ef583459219fa4951218912`.

Common bubblewrap settings, before the backend executable and its original argv:

```text
--die-with-parent --new-session --unshare-user --uid 1001 --gid 1001
--ro-bind / /
--proc /proc --dev /dev --tmpfs /tmp
--bind ~/duo-gate/probe/home ~
--ro-bind ~/duo-gate/probe/target-repo <same path>
--bind ~/duo-gate/probe/state <same path>
--bind ~/duo-gate/probe/scratch <same path>
--ro-bind ~/.local ~/.local
--chdir ~/duo-gate/probe/target-repo
```

The angle-bracket shorthand above is expanded to the identical absolute path
in the retained `gate.py`. Throughout this record, `~` stands for the owner's
home directory, including in quoted errors and traces, which print the absolute
path. Backend mounts precede `--chdir`:

| Variant | Additional owner-home resource mounts | Credential/runtime exposure |
| --- | --- | --- |
| `codex-ro` | `--ro-bind ~/.codex ~/.codex` | Entire existing Codex directory readable, including `auth.json`, executable packages, configuration, history and databases; no writes. |
| `codex-rw` | `--bind ~/.codex ~/.codex` | Same reads, with the entire directory writable by the CLI; runtime/state writes and authentication coexist. |
| `claude` | Writable binds of `~/.claude` and `~/.claude.json` | Existing Claude credentials/configuration/history readable and writable by the CLI; other owner-home resources remain covered. |
| `copilot` | None beyond common `~/.local` | Environment token only; configuration, logs and staged runtime/cache use bound state paths. |

Allowed reads include the read-only root's normally readable files, the reviewed
repository, `~/.local`, and the explicitly exposed backend resources. Ordinary
owner-home paths, unrelated projects there, SSH and AWS credential paths, and
other backends' credential files are covered. This is **not** an allowlist of
all readable files outside the repository: the requested root bind leaves
normally readable material outside the covered home available.

Writable locations are the temporary-home backing directory, scratch, state,
ephemeral `/tmp`, and each backend's specified writable login resources, plus
the normal virtual-device/proc interfaces permitted by the OS. Duo itself runs
on the host and publishes the document; only its reviewers receive the
read-only repository mount. The fixture wrappers are experiment apparatus,
not a shipped profile or a new Duo option.

Network and PID namespaces are shared with the host. User and mount namespaces
are separate. A fresh procfs does not imply PID isolation. Provider access is
permitted, and no provider-only egress restriction is claimed. The root bind
and unchanged allowlist also leave the host session-bus socket reachable:
AF_UNIX connect to `/run/user/1001/bus` succeeded for every variant, with no
messages sent. Access to keyring data through it was not attempted or established.
One supplementary attempt to read the host harness's canary through
`/proc/115509/root/...` failed with `EACCES` for every variant; this is not a
general proof against every proc/IPC escape.

## Deterministic battery

`results/battery.json` records separate launches and full error strings for all
four variants after token provisioning. All listed filesystem tests pass in
all variants, including the Codex variant whose CLI later fails initialization.
Tests 1–9 execute a deterministic Python/Bash probe inside the same outer mount
and environment boundary; they do not rely on a model choosing to use a tool.
Probe 10 additionally exercises the actual Codex tool path.

| Probe | Codex RO | Codex RW | Claude | Copilot |
| --- | --- | --- | --- | --- |
| 1. Unbound outside canary | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` |
| 2. Temporary real-home canary | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` |
| 3. `~/.ssh` absent | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` |
| 4. Repository write denied, no host sentinel | PASS, `EROFS` | PASS, `EROFS` | PASS, `EROFS` | PASS, `EROFS` |
| 5. Covered-home write reaches only temporary home | PASS | PASS | PASS | PASS |
| 6. Scratch write/readback | PASS | PASS | PASS | PASS |
| 7. `escape-link` cannot read its canary target | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` | PASS, `ENOENT` |
| 8. Own credential readable/present, value not printed | PASS, `auth.json` | PASS, `auth.json` | PASS, both credential/config files | PASS, `GH_TOKEN`; source file hidden |
| 9. Fresh-home and startup-sentinel controls | PASS | PASS | PASS | PASS |
| 10. Actual Codex command records an OS error | BLOCKED before model initialization | PASS, completed `command_execution`, exit 1 / errno 2 | N/A: this probe targets native Codex | N/A: this probe targets native Codex |
| 11. Shared network succeeds; `--unshare-net` removes INET connectivity | PASS | PASS | PASS | PASS |

Exact filesystem errors common to the four variants:

```text
1: [Errno 2] No such file or directory: '~/duo-gate/probe/outside/canary.txt'
2: [Errno 2] No such file or directory: '~/.duo-gate-home-canary-f88ccb115122576c'
3: [Errno 2] No such file or directory: '~/.ssh'
7: [Errno 2] No such file or directory: '~/duo-gate/probe/target-repo/escape-link'
```

Probe 4 reports the following exact error for each variant:

```text
codex-ro: [Errno 30] Read-only file system: '~/duo-gate/probe/target-repo/forbidden-write-codex-ro'
codex-rw: [Errno 30] Read-only file system: '~/duo-gate/probe/target-repo/forbidden-write-codex-rw'
claude:   [Errno 30] Read-only file system: '~/duo-gate/probe/target-repo/forbidden-write-claude'
copilot:  [Errno 30] Read-only file system: '~/duo-gate/probe/target-repo/forbidden-write-copilot'
```

Host verification confirms that none of these forbidden writes exists. Probe 5
writes `~/gate-home-write-<variant>` inside the guest; the file
appears under `probe/home/` and never at that path in the real owner home.
Probe 6 writes `probe/scratch/gate-scratch-write-<variant>` and reads it back.
Successful operations have no OS error. The temporary real-home canary was
removed in a `finally` block and its absence checked. No owner startup file was
modified. An initial extra assertion mistook empty, reused mountpoint directories
for exposed credential stores; that result is retained as `battery-initial.json`.
The corrected assertion checks the excluded credential files, which are absent.

### Startup-file controls and the section 7.2 handoff

The settled 0.8.2 `allow_login_shell=false` control remains in force. The gate
does not reopen its Linux login-profile finding. Before sentinel planting,
owner startup files at the covered HOME paths are absent and `BASH_ENV` is absent
from the reviewer environment. With synthetic files in the temporary home:

| Deterministic shell form, every backend boundary | Temporary profile sentinels | Temporary `.bashrc` sentinel |
| --- | --- | --- |
| `bash -c` | absent | absent |
| `bash -lc` attribution control | present | absent |
| `bash -ic` attribution control | absent | present |

The actual authenticated Codex RW command, launched with Duo's overrides,
records `/bin/bash -c ...`, both temporary profile sentinels absent, and the
temporary `.bashrc` sentinel present. Thus the existing non-login `.bashrc`
behavior persists, but it sees the deliberately planted temporary file, not the
owner's covered `.bashrc`. All planted files were removed afterward. A future
profile must allocate and discard fresh homes rather than reuse dirty shell state.

Filename-only `strace` controls confirm that `/etc/profile` and files under
`/etc/profile.d/` are still opened by the login-shell attribution control.
`/etc/bash.bashrc` is also readable. System startup content therefore remains
available inside the read-only root. The trace demonstrates that residual for
the control shell; it does not claim the non-login Codex command opened every
system startup file. `BASH_ENV` execution mechanisms and zsh startup behavior
remain **analysis only**; neither is promoted into tested evidence here.

### Tool-surface and network evidence

`codex-rw-tool.stdout.jsonl` contains an `item.completed` / `command_execution`
item, `status: failed`, `exit_code: 1`, whose output includes:

```text
FileNotFoundError: [Errno 2] No such file or directory: '~/duo-gate/probe/outside/canary.txt'
```

The enclosing authenticated turn completes with exit 0 and usage. This is an
executed command's OS failure, not a model refusal. A successful nested
`unshare --user --map-root-user /bin/echo` also ran under each Codex mount
variant. Native Codex's actual inner sandbox executed the command under the RW
variant; no nested-user-namespace failure was encountered on this host.

For each backend, a disposable host loopback listener is reachable with shared
networking and unreachable with `--unshare-net`:
`ConnectionRefusedError: [Errno 111] Connection refused`. A separate TCP-only
connection to `1.1.1.1:443` succeeds with shared networking and fails in the new
network namespace with `OSError: [Errno 101] Network is unreachable`. No
application data is sent by that probe. Namespace IDs corroborate the shared
versus separate network context. These controls establish the INET contrast;
they do not supply provider-only routing or an AF_UNIX isolation claim.

## Authentication coexistence and exact failures

### Native Codex read-only state: demonstrated blocker

The same Duo-style Codex invocation with `~/.codex` read-only exits 1 before any
model/tool event. Its exact stderr is:

```text
WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)
Error: failed to initialize in-process app-server client: Read-only file system (os error 30)
```

A filename-only syscall trace, retaining no file contents, includes:

```text
open("~/.codex/state_5.sqlite", O_RDWR|O_CREAT|O_LARGEFILE|O_NOFOLLOW|O_CLOEXEC, 0644) = -1 EROFS (Read-only file system)
open("~/.codex/state_5.sqlite-wal", O_RDWR|O_CREAT|O_LARGEFILE|O_NOFOLLOW|O_CLOEXEC, 0644) = -1 EROFS (Read-only file system)
open("~/.codex/state_5.sqlite-shm", O_RDWR|O_CREAT|O_LARGEFILE|O_NOFOLLOW|O_CLOEXEC, 0644) = -1 EROFS (Read-only file system)
open("~/.codex/installation_id", O_RDWR|O_CREAT|O_LARGEFILE|O_CLOEXEC, 0644) = -1 EROFS (Read-only file system)
```

Read-only fallback opens of existing SQLite files also occur; other state/log/
goals database and WAL write opens fail. The trace therefore establishes actual
SQLite write attempts, alongside non-SQLite runtime writes. It does not attribute
the final initialization error exclusively to one database. Read-only access to
the login store is insufficient for this tested invocation. The entire-directory
RW variant works; a narrower split of immutable credentials and mutable runtime
state remains unevaluated.

### Native pair: completed full Duo run

The first full native attempt failed preflight, before review, because the
host-created temporary MCP-probe home was hidden by guest `/tmp`. The diagnostic
stderr includes:

```text
WARNING: proceeding, even though we could not create PATH aliases: CODEX_HOME points to "/tmp/duo-codex-home-1f7ojn1n", but that path does not exist
Error: failed to resolve CODEX_HOME

Caused by:
    CODEX_HOME points to "/tmp/duo-codex-home-1f7ojn1n", but that path does not exist
```

The first Duo run is `state/duo-native/runs/run-_4bojicm`. It is retained, not
silently discarded. Setting host/reviewer `TMPDIR` to bound scratch makes those
temporary paths visible without binding host `/tmp` or changing Duo's allowlist.

The corrected run uses `probe/bin/codex-rw` and `probe/bin/claude`, native models
`gpt-6-astra` / `claude-sonnet-5-5`, low requested effort, six-turn limit,
one attempt per turn, 180-second call timeout, and 700-second run budget.
Codex 0.172.0 is outside Duo's tested range and was explicitly allowed with
`--allow-untested-cli`. The task is a self-contained greenfield Python greeting
command plan; the missing existing application is intentional.

`state/duo-native/runs/run-1n1vrdjh` reaches **agreed**, exit 0, **3 turns / 3
attempts**, in **38.703 seconds**. Both approvals refer to the same document hash:
`efafadca31f98e4eefeeab9399e6ec5d4f37195a5560073d833ac9675df8a22a`.
The host document hash was independently checked. Claude's stream records
successful `Glob`, `Read` of the fixture README, and structured approval; both
native Codex turns complete. This demonstrates authentication and repository
inspection with both reviewers confined, while host Duo retains publication.

### Copilot: matched quota failure after credential provisioning

Initial controls without a provisioned token both fail with
`Error: No authentication information found.` Those controls are retained as
`before-token-copilot*`; they establish no quota or authenticated-call evidence.

After the owner identifies `~/.copilot-token`, fresh confined Duo Copilot
preflight succeeds with runtime 1.0.116, effective isolated settings, tools
`view`/`glob`/`grep`, and `--sync-session=off`. Both fresh model-call controls use
`gpt-6-luna`, the same inspection flags, isolated config/cache, and the same token.
Both streams identify runtime 1.0.116 and contain:

```json
{"statusCode":402,"failureKind":"api","errorCode":"quota_exceeded","errorType":"websocket_error","errorMessage":"{\"code\":\"quota_exceeded\",\"type\":\"websocket_error\",\"message\":\"You have exceeded your monthly quota\"}"}
```

Both processes exit 1; wall times are **6.036 seconds unconfined** and **6.130
seconds confined**. Provider failure durations are 162 and 164 ms respectively.
Service request IDs differ, as expected; the failure class and message match.
This demonstrates token delivery and a service-side quota response through the
confined network path, rather than a missing credential, filesystem failure,
or namespace refusal. It does **not** demonstrate completed model inference,
Copilot tool execution, review acceptance, or agreement. Re-run the authenticated
controls after quota becomes available before changing the verdict.

## Friction, cost, and remaining exposure

- Ten `/bin/true` confinement launches per variant have median elapsed times
  **8.739 ms Codex RO, 8.342 ms Codex RW, 9.954 ms Claude, 8.441 ms Copilot**.
  These measure direct bubblewrap no-op launch cost, not an incremental baseline
  subtraction, wrapper interpreter cost, or model latency. The separate successful
  Codex tool probe takes 9.400 seconds; the native Duo pair takes 38.703 seconds.
- The native-pair Claude call reports **USD 0.0421334**. Its Codex calls report
  65,304 input tokens, including 48,512 cached input tokens, and 788 output tokens.
  The separate Codex tool probe reports 23,348 input / 11,520 cached / 226 output.
  Codex does not report a USD cost here. Quota-failed Copilot calls report zero
  premium requests; that is reported usage, not a price estimate.
- Each retained Copilot layout diagnostic stages **336,522,529 bytes** of regular
  package files, approximately **320.93 MiB**, before other cache/log overhead.
  Model-call/preflight probes use Duo's runtime cleanup. The two layout samples
  remain in the external workspace; no package was added to Git. Keep runtime
  staging and audit accounting distinct in any later implementation.
- Real paths matter: home masking can hide executable symlink targets, the NVM
  Node runtime, and host temporary directories. Direct `env node --version`
  deterministically fails in every variant, exit 127:
  `/usr/bin/env: ‘node’: No such file or directory`. Git, Python, Bash and curl
  remain visible. PATH exposes Codex's packaged `rg` only in Codex variants;
  Claude/Copilot's bundled search behavior is not established by PATH checks.
- Writable native stores expose more than an authentication token, including
  existing configuration, histories and runtime databases, and permit normal CLI
  mutations. The tested full-directory binds are not a proof that only minimally
  necessary login material is visible. `~/.local` is likewise a broad runtime
  bind. Narrower resource selection needs separate evidence.
- Owner HOME startup files are hidden, but system startup files, reachable host
  services/session bus, readable root material, CLI-supplied startup behavior, and
  mutable temporary-home content remain relevant. The finite proc-root test is
  not a proof of complete process/IPC isolation. There are no CPU/memory quotas
  or provider-only network controls in this candidate.
- **Plan §4.5:** filesystem isolation does not stop readable material reaching
  model providers or URLs. Shared network leaves that exposure in place.
  Web-disabled and `--allow-web` validation remain separate later work; this gate
  does not implement steps 2–5 or authorize use on sensitive/untrusted repositories.

## macOS evaluation protocol — entirely UNEVALUATED

Run the following separately for native Codex RO/RW state, native Claude, and
Copilot, under **both** candidate mechanisms: (a) `sandbox-exec` with an explicit
deny/allow policy, and (b) a separate standard-user account with owner-controlled
credential provisioning. Neither mechanism is accepted on its name alone.

1. Inventory macOS version/architecture, CLI versions, executable symlinks,
   Node/native runtime dependencies, system/user startup files and login/keychain
   resources. Define allowed reads, every writable directory, network scope and
   credentials visible to each CLI and its tools. Use fresh disposable homes and
   enumerated scratch/cache/audit locations; preserve Duo's environment allowlist.
2. Establish how the mechanism supplies the required home/path boundary. Seatbelt
   denials and changing `HOME` are not the same as a same-path home overlay;
   a separate account likewise changes path/identity semantics. If the requested
   mapping cannot be provided, record the exact blocker or an explicitly separate
   boundary, rather than counting a changed test as passing the Linux contract.
   Provision credentials only through an owner-approved local process; never
   copy owner SSH/cloud resources or publish authentication material.
3. Repeat probes 1–9 with the real-home and unbound canaries, SSH path, repository
   and outside writes, temporary-home write attribution, scratch, absolute symlink
   escape, and own credential existence/readability. Record `errno`/exact errors,
   host-side sentinel absence, and account/ACL metadata. Include attempts at
   excluded login/keychain resources without printing their contents.
4. Repeat startup controls with `.bash_profile`/`.bashrc` and the **`.zprofile`
   sentinel variant** for the selected macOS shell, plus `.zshenv`/`ZDOTDIR` and
   system-file analysis. Distinguish planted-file positive controls, actual shell
   invocation, system startup traces, and unexecuted hypotheses. Do not inherit
   the Linux `allow_login_shell=false` or `.bashrc` result without live evidence.
5. Run the actual Codex command probe and require a JSONL `command_execution`
   item carrying an OS denial; a refusal or absent command remains unevaluated.
   Check the CLI's own sandbox nesting under each candidate. Run the full native
   Duo greenfield task to agreement. For Copilot, run confined preflight and
   matched authenticated confined/unconfined calls, classifying any quota failure.
6. Demonstrate shared-network success against a controlled service and the
   candidate's network-disabled contrast with an OS failure. A separate account
   alone does not establish network denial; inability to enforce the declared
   network contract is a blocker, not a permission to silently fall back.
7. Retain exact policy/launcher/probe artifacts and versioned results, measure
   startup/staging costs, and apply the same SUPPORTED/UNSUPPORTED/UNEVALUATED
   rules. No macOS supported cell can be filled from this Linux record.

## Revised step-2 estimate

Replace the plan's undifferentiated "large" implementation placeholder **for a
Linux native-only first implementation** with **4–6 engineer-days of step-2
implementation**, conditional on the exact supported boundaries above:

| Step-2 work | Estimate | Evidence driving the work |
| --- | --- | --- |
| Opt-in configuration, capability checks, mount/runtime path validation, refusal paths | 1–1.5 days | User namespaces work here, but versions, symlink targets and layouts differ; no silent fallback is acceptable. |
| Temporary-home/scratch/state lifecycle and process integration | 1–1.5 days | Homes must be fresh; shared TMPDIR is necessary for existing preflight; cleanup and signal behavior must remain correct. |
| Native backend resource handling and credential-provisioning contract | 1–1.5 days | Codex RO state is demonstrably broken; Claude needs two writable resources; broad binds require explicit documentation and path checks. |
| Integration, diagnostics, targeted developer checks | 1–1.5 days | Preserve strict CLI policy, report exact OS failures, and distinguish runtime/preflight/authentication failures. |

Allow a further **3–5 engineer-days** for the deterministic regression suite,
supported/unsupported host coverage, repeated live checks, documentation and the
release evidence required by later steps. Thus a Linux native release-ready
milestone is approximately **7–11 engineer-days**, excluding credential/quota
waiting. This is an engineering estimate, not measured implementation time or
authorization to begin implementation.

Copilot authentication completion remains a gate dependency: budget another
0.5–1 day to rerun it and resolve any newly exposed integration issue **after**
quota is available, before estimating or claiming its supported implementation.
macOS implementation remains **unestimated** until the two-candidate protocol is
run; allow 2–4 engineer-days for that investigation, not as a promise that either
mechanism will satisfy the boundary. Provider-only egress or substantially
narrower credential/runtime mounts would be separate scope and estimates.

## Retained local evidence and validation

All raw fixtures stay under `~/duo-gate/probe/`; none is vendored in
the repository. `README.md` there gives replay commands; `gate.py`,
`supplement.py`, `finalize.py` and `bin/{codex-ro,codex-rw,claude,copilot}` retain
the exact experiment and wrapper argv. Notable results:

| Evidence | Local path below `probe/` |
| --- | --- |
| Inventory and initial namespace echo | `results/inventory.json` |
| Per-backend deterministic battery and cleanup of owner-home canary | `results/battery.json` |
| Startup controls and filename traces | `results/startup.json`, `scratch/startup-*.trace` |
| Shared/unshared loopback and outbound TCP | `results/network.json`, `results/network-egress.json` |
| Nested namespaces and actual Codex tool error | `results/nested.json`, `results/codex-rw-tool.*` |
| Codex RO failure and SQLite/runtime syscalls | `results/codex-ro-tool.*`, `results/codex-ro-trace.json`, `results/codex-ro-files.trace` |
| Exact initial preflight failure | `results/preflight-diagnostic.json`, `results/initial-duo-native.*` |
| Completed native pair and audit | `results/duo-native.json`, `state/duo-native/runs/run-1n1vrdjh/` |
| Copilot preflight and matched quota failures | `results/copilot.json`, `results/copilot-{confined,unconfined}.*`, `state/copilot-auth-36747ada/` |
| Runtime staging, missing sub-tools, residual IPC/proc access | `results/copilot-layout.json`, `results/subtools.json`, `results/node-unavailable.json`, `results/residual.json` |
| Source credential file remains hidden | `results/credential-source-visibility.json` |
| Measured launch cost | `results/overhead.json` |
| Consolidated checks, verdicts and textual evidence hashes | `results/gate-summary.json`, `results/evidence-sha256.json` |

The external finalizer passes **39 evidence assertions**, including same-hash
agreement, the actual command OS error, matched service-quota classification,
and absence of the supplied token value from generated textual evidence. The
workspace/result directories are private; raw provider logs are not publication
artifacts. The gate does not claim immutable/signed audits, complete provider
page/file archives, untested CLI combinations, or a finished isolation profile.

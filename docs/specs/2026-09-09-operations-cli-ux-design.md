# Operations CLI progress and outcome design

Status: proposed specification; command behavior approved, written-spec approval pending.
Updated: 2026-09-09. Design task: `tas-7ncz`. Related diagnostics: `tas-6dkg`.

## Purpose and authority

Make the existing operations controller understandable while it runs and decisive when it stops.
The operator approved visible safe progress, pyinfra operation presentation, readable plans and
results, separate JSON output, early provisioning admission, and useful bounded failure reasons.
This document specifies that increment; it does not authorize implementation or host actions.

The [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md) continues to own host
safety, immutable release identity, confirmation, recovery, and protocol bounds. The
[runbook](../deployment.md) owns current commands; [development guidance](../development.md) owns
engineering and verification rules. The [PostgreSQL Python proposal](2026-09-09-postgresql-host-python-design.md)
remains parked and is not a prerequisite.

On implementation, this specification supersedes the older design's unconditional build-before-SSH
ordering for **provision only**, its flat human renderer, and mixed JSON/prompt/error streams.
It does not change deploy artifact resolution, release selection policy, migration compatibility,
destructive confirmation, secret suppression, or the meaning of existing numeric status categories.
Until then, the existing runbook describes implemented behavior.

## Evidence and repository baseline

The inspected checkout is clean application/controller revision `027f44e3ceb2f435d40c35f9c3d924dafe948de6`
before these design/tracker edits. It includes the reviewed provisioning/runtime fixes and two
verifier corrections. The last full checks passed 904 operations tests and 805 application tests.
Those checks are baseline evidence, not verification of this proposed change.

The operator ran bare `provision staging` from a checkout producing source `5e00e0c7b233`. It
built a different candidate, converged host operations, then failed in the first-install procedure
against the existing selected `8266656863ad` release. Subsequent read-only standalone verification
passed all eight checks on that existing release. No later deployment is implied by this evidence.

Relevant implementation findings:

- `workflows/provision.py` decrypts secrets and resolves/builds an artifact before SSH discovery.
  Its presentation occurs only after those potentially slow steps. It then always invokes
  `deploy_first_release`, including on a completed installation.
- `releases/build.py::_run_command` captures subprocess output rather than showing progress.
- `remote.py` constructs pyinfra programmatically without the CLI output setup. Sensitive remote
  commands suppress both input and output intentionally; that protection must remain.
- `output.py::render_human` serializes nested facts as JSON on one line. `cli.py` emits normal
  results on stdout but exception results on stderr; provisioning prints plans/prompts directly.
- `host_helper/operations/deploy.py` accepts exact first-install replay, not an arbitrary later
  source revision. Its failed verification path loses the already available bounded report.
- Repeat provisioning with the exact installed artifact succeeded. Only the backup executable's
  read-only checksum operation was reported changed by pyinfra. This does not establish that bare
  provisioning from a changed checkout is a useful or safe update command.

The lock pins pyinfra **3.10.0**. Installed-source inspection established:

- `pyinfra.api.output` exposes `set_formatter`, `set_echo`, and `is_output_active`; its default echo
  is a no-op. `pyinfra_cli.main` installs Click formatting/echo, not merely a log level.
- `pyinfra_cli.log.LogFormatter` supplies the native operation presentation.
- `pyinfra.api.state.BaseStateCallback` exposes operation start/end and host outcome callbacks.
  Completed operation metadata is available at operation end, not yet at host-success callback.
- Native operation logging can include arguments, and warnings can include exception text.
  Enabling the logger wholesale would violate the existing output trust boundary.

Mechanism references: [pyinfra programmatic API](https://docs.pyinfra.com/en/3.x/api/),
[operation lifecycle](https://docs.pyinfra.com/en/3.x/deploy-process.html), and the pinned source
files named above. The local locked source, not an assumption about a future release, governs
integration. No external service or dependency upgrade is required.

## Scope and non-goals

Apply the common human renderer and stdout/stderr rules to the existing operations commands.
Add progress at existing local validation, artifact, SSH/admission, pyinfra, protected-configuration,
helper invocation, and final verification boundaries. First-install and deploy failures retain
their safe reasons/reports; shared consumers are updated only where the result contract requires it.

Do not add a TUI, event bus, persistent progress records, host agent, new database state, generic
workflow framework, raw-output debug mode, auto-confirm option, automatic deploy/rollback, or
PostgreSQL refactor. Do not rename public commands or rebuild a release to change its identity.
Do not expand into a full-branch review or new host acceptance run.

## Provisioning command contract

Provision means first installation and recognizable replay of that installation. Deploy remains
the operation for changing application releases on an initialized host. Never switch between them
implicitly, and never turn an early refusal into a successful no-op.

| Observed state and input | Required behavior before building or host convergence |
| --- | --- |
| Pristine supported host, no artifact | Admit the host, then build and validate the current clean source; present plan and require confirmation |
| Pristine supported host, explicit artifact | Validate the artifact and admit the host; present plan and require confirmation |
| Compatible pre-release partial provisioning, no release/staging identity yet | Permit normal continuation after admission; bare invocation may build |
| Recognizable unfinished first release, bare invocation | Refuse with status 10 and request its original explicit artifact; do not rebuild or guess from cache |
| Recognizable unfinished first release, matching explicit artifact | Permit the existing replay after confirmation and fresh authority checks |
| Completed first installation, bare invocation | Refuse with status 10 before build, secret decryption, convergence, or prompt; identify selected release and direct updates to deploy |
| Completed first installation, exact original artifact | Permit the existing explicit convergence/replay path; verify before reporting success |
| Completed installation, different artifact | Refuse with status 10 before convergence or prompt; direct updates to deploy |
| Later deployment history, even an artifact matching current | Do not broaden the first-install replay contract; refuse provision and explain the deploy boundary |
| Missing, foreign, corrupt, conflicting, or unprovable authority | Refuse with the applicable existing safety/preflight/lock status; do not adopt or infer a pristine host |

Where a completed release record exists, matching means its full artifact identity, including
checksum and migration fingerprints, not just a short source prefix or release ID. A rebuilt
archive with the same logical ID but different recorded bytes is not a matching retry. Incomplete
staging without a record proves only candidate attribution through its validated deterministic
name; require an explicit artifact for that candidate and retain the existing staging verification
and replay policy. Do not invent a historical checksum. A completed selection proves selection,
not current service health; early refusals say **selected release**, not **healthy running release**.

`--dry-run` uses the same early classification and refusal rules but performs no managed-state
mutation or confirmation. A pristine/eligible pre-release dry run may still build a local artifact,
as today; announce that work. A completed host's bare dry run must not build.

### Ordering and authoritative inspection

1. Parse arguments and validate non-secret configuration. If an explicit artifact was supplied,
   validate its local manifest/checksum before remote use. Show these activities before blocking.
2. Connect using existing pinned SSH authority and perform supported-host admission.
3. Inspect existing release/selection authority before deciding whether a build is appropriate.
4. On eligible paths only, decrypt/validate secrets and resolve/build the required artifact.
5. Present the exact redacted plan and read explicit confirmation.
6. Refresh the early admission decision after a long build/confirmation and before convergence;
   refuse changed material authority. Existing host-side locked revalidation remains decisive.
7. Execute the existing provisioning and first-release procedures, then render their final result.

Early inspection is read-only and credential-free: it reads protected metadata, not environment
contents, passwords, or database rows. Add one narrow helper operation, `inspect_provisioning`,
rather than parse completed records in the controller or misuse ordinary discovery's credential
requirements. Reuse `ManagedPaths`, `observe_host_state`, record validation, and the existing
first-install record/selection/staging predicates; extract only their shared pure decision where
necessary. Database/schema checks remain in the existing credentialed admission and mutating
helper; this early metadata inspection must not invent empty migration state or promise to detect
every later schema failure before host convergence.

The request uses the two existing paths, empty expected state, and empty parameters. Successful
state contains exactly five fields: `classification` (`empty`, `unfinished`, or `completed`),
`selected_release_id` (validated ID or null), `release` (one validated ReleaseRecord or null),
`staged_release_id` (one attributable validated staging ID or null), and `first_selection_only`
(boolean).
`empty` requires no attributable release/staging/selection evidence; `unfinished` allows one
attributable first-install identity or recognizable staging; `completed` requires valid selected
history. Later history can return `completed` but is distinguished by `first_selection_only`,
true only for exactly one initial
completed selection and its sole release. Controllers never reconstruct history from this flag.
Unknown or contradictory state returns the existing bounded refusal, not a success classification.
For completed history, `release` is its selected record; for an unfinished first installation it
is the sole attributable record if one exists. Any staging must be attributable to the same
candidate; competing staging identities refuse. The controller compares explicit artifact identity
against both the record (when present) and the staging ID before allowing convergence.

Use the existing lifecycle lock when it exists. Do not call the lock's current creating path on a
pristine/marker-only host merely to inspect it: that path creates the installation root. An absent
lock permits only a read-only proof that no release/selection/staging evidence exists; otherwise
refuse. Add a narrow non-creating lock acquisition option if needed, preserving every existing
caller's default. Temporary verified helper transfer infrastructure is allowed, as with existing
read-only commands; no installation directory, ownership marker, or record may be created here.

An early snapshot is a usability guard, not a replacement transaction lock. Do not claim it prevents
all concurrent drift. The mutating helper must continue to reobserve and refuse incompatible state.

## Human output and progress

Keep a small invocation-scoped reporter owned by the CLI. Existing workflow/capability entry points
receive it explicitly or an optional no-op equivalent; presentation cannot become their business
state. It exposes activity start/finish and safe messages, not a general event schema.

- Emit and flush a start line before each potentially slow boundary. Finish it with outcome and
  elapsed time, including failure. Never print a successful marker before the operation returns.
- On a TTY, update elapsed time once per second on the active line. Without a TTY, use plain
  newline-delimited starts/finishes and a still-running line every 15 seconds. No ANSI/control-line
  updates in redirected output; honor `NO_COLOR` for color. Do not invent completion percentages.
- Use only one active display owner: suspend the outer activity animation during pyinfra display
  and all prompts. Stop/join progress resources on success, failure, cancellation, and exceptions.
  A timer updates presentation only; it must not poll the host or change operation timeouts.
- Start reporting at Python CLI entry. The external `uv` launcher may still need to prepare its
  locked environment before Python runs; this is not represented as controller activity.
- Build progress reports source identity, build start, elapsed time, and verified artifact result.
  Do not stream arbitrary compiler/Docker output by default. The point is visible work, not a new
  build-log protocol or a false estimate. Preserve artifact/cookie secrecy and source isolation.
- A host-helper invocation shows its actual requested operation and elapsed time. Its one-result
  protocol cannot reveal internal migration/start/verification transitions while in flight; do not
  fabricate them. Show individual verification checks once the validated report arrives.

### pyinfra presentation adapter

Keep pyinfra as the source of operation ordering, names, outcomes, and completed change evidence.
Reuse its native formatter and styling with a narrowly scoped controller adapter. Use public
state callbacks for safe operation start/end/outcome data; at operation end read completed metadata
for Success/No changes. Do not call the full pyinfra CLI entry point, change its signal handlers,
run a nested deployment, enable debug logging, or print operation arguments/commands/facts.

The adapter emits only sanitized operation names, host labels, elapsed time, and fixed result
labels. Native logger records containing arguments or exceptions are not forwarded. Feed safe
records into the native formatter rather than parsing formatted log strings. Keep raw remote
stdout/stderr, secret-bearing function arguments, private file content, and malformed helper output
suppressed. Recursive redaction remains a final defense, not permission to expose arbitrary logs.

Attach/detach callbacks and restore output hooks/handlers per invocation, including failure. Keep
this dependency seam in one controller-owned module, covered against pyinfra 3.10.0. Tests must
exercise two CLI invocations in one process to catch duplicate handlers or retained output state.

### Plans and final results

Plans show target/environment, selected and candidate releases where known, artifact identity,
migration policy/backup consequences, affected services, and confirmation in labeled lines.
Wrap long values rather than truncate identities. Use compact lists/tables for records and checks;
do not dump nested dictionaries. Omit empty warnings and irrelevant null fields in human output,
but explicitly display uncertainty when it matters to recovery.

The first final line must say one of: succeeded, no changes required, stopped before changes,
or failed after changes may have occurred. Derive this from final status and evidence, never just
the aggregate `changed` flag. Include the numeric exit code for a refusal/failure, safe reason,
selected/requested identity, failed checks, and one actionable next command or inspection step.
Do not label the failed run successful because the old application is still available.

Illustrative early refusal (actual identifiers remain complete in real output):

```text
Provisioning stopped before changes (exit 10)

This host already has a completed installation.
Selected release: 0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6
No release was built or deployed by this invocation.

To update the application: ./ops/taskman deploy staging
To check current health:  ./ops/taskman verify staging
```

Retain `changed` semantics in JSON. Human output describes it as operations performed/possible
changes, not an outcome. For successful explicit replay, distinguish unchanged release from the
always-executed backup checksum check. Historical migration facts must not be worded as proof
that this invocation reran migrations. Unknown service/database evidence remains unknown.

## Output streams, JSON, and confirmation

| Surface | stdout | stderr |
| --- | --- | --- |
| Human execution | Final human result/listing | Progress, plan, prompt, warnings during execution |
| JSON execution | Exactly one final schema-version-1 JSON document and newline, on success or failure | Plain safe progress, plan, prompt; no animations |
| Help | Existing help text | Argument diagnostics as appropriate |
| Interactive administrator session | Existing terminal bridge | Existing terminal bridge |

Preserve the version-1 JSON outer keys and existing status categories. Add bounded `facts.reason`
and existing-shaped failure evidence where needed; do not insert progress events, a second plan
document, ANSI escapes, or child-process output into stdout. CLI-parsed errors under recognized
`--json` also produce one final error document on stdout; explanatory usage goes to stderr.
Document the deliberate correction from the previous exception-result stderr behavior.

JSON never implies confirmation. Keep existing ordinary `yes` and typed destructive confirmations;
write prompts to stderr and read explicit stdin responses. EOF, missing input, or a wrong response
returns status 10 without the guarded mutation. Explicit piped input retains its existing meaning;
no flag, environment variable, or output mode supplies an answer automatically.
Dry runs and commands without confirmation keep their existing policy. Do not open another terminal
to bypass redirected stdin. `create-admin` retains its stricter real-TTY-only credential bridge and
its existing restrictions; it is not converted into captured JSON/password output.

Preserve exit codes 0 and 2–12. Early already-initialized/wrong-artifact/refusal is 10; remote
inspection/authority unavailability is 5, lock contention 12, and malformed explicit artifact uses
its existing local category. Signal interruption must remain non-success; stop progress and report
outcome uncertainty if dispatch already occurred, without inventing rollback or remote cancellation.
Do not weaken existing process cleanup or transport deadlines in order to draw progress.

## Failure evidence and protocol scope

Implement `tas-6dkg` within this increment: retain a failed `VerificationReport` through helper,
workflow mapping, JSON, and human output. Accept it only through the existing strict report parser.
Do not fabricate a report for preflight, transport loss, or failures before verification.

The existing helper `message` must reach an appropriate safe controller reason instead of becoming
only `deployment-incomplete`. Where a generic catch discards an identifiable first-install refusal,
use finite reason text tied to that actual guard; never expose arbitrary exception messages.
Report selected and running identity separately when both are proved; otherwise say selected only.

Retain protocol version 2, its exact envelope, 64 KiB bounds, and one request/one result. The narrow
inspection operation is an additive vocabulary entry and is packaged with its matching controller;
operation-specific validation covers its exact fields. Existing deployment result state already
has a report seam; fix its failure path and validate optional failed evidence without relaxing
successful-verification requirements. No per-step remote event stream, durable state, protocol
compatibility fallback, or scheduled-backup capability expansion is introduced.

## Expected file responsibilities

Paths below are under `ops/taskman_ops/` unless otherwise stated.

| Files | Responsibility |
| --- | --- |
| `cli.py`, `output.py` | Invocation-owned streams, confirmations, final result/error rendering and redaction |
| New `progress.py` | Small safe activity/elapsed-time reporter and output cleanup; no workflow policy |
| New `pyinfra_output.py`, `remote.py` | Scoped native pyinfra presentation adapter and safe lifecycle attachment |
| `workflows/provision.py`, existing admission/workflow helpers | Early inspect/resolve/confirm ordering and refused-result guidance |
| `host_helper/operations/discover.py`, shared state/eligibility owner | Narrow read-only provisioning inspection using existing authority rules |
| `host_helper/lock.py` if required | Non-creating inspection lock option; preserve mutating callers |
| `host_protocol/operations.py`, helper dispatch/package allowlist | Exact added operation and isolated packaging coverage |
| `releases/build.py`, artifact resolution, helper client | Activity scopes around real existing work; unchanged artifact and wire semantics |
| `host_helper/operations/deploy.py`, `workflows/helper.py`, verification mapping | Retain safe failure reasons and failed reports |
| Existing workflow confirmation owners | Use invocation-owned prompt stream without changing the requested confirmation |
| `ops/tests/`, runbook, canonical design and index | Regression evidence and implemented contract documentation |

Do not pass terminal libraries into host code, add dependencies, or create a new operation-state
hierarchy. Keep new modules only where the listed responsibility is cohesive; do not introduce
generic event dispatch to connect them. Other commands adopt the common renderer/progress seam
without changing their consequence policies.

## Verification and acceptance

Use TDD against the real boundaries and an independent scoped review, especially output secrecy
and early authority classification. Required cases:

1. Bare completed-host provision refuses before builder, SOPS decryption, confirmation, upload of
   a release, or convergence. A changed checkout reproduces the operator's case. Selected identity
   is shown but health is not invented. Same behavior under dry-run and JSON.
2. Pristine and pre-release partial installs remain available; exact staged/unfinished/completed
   first-release retries work. Wrong bytes, foreign state, later history, missing locks with release
   evidence, and concurrent selection changes refuse without adoption. Inspection creates no
   installation root/lock/marker/record and leaks no credential data.
3. A synchronized blocking fake proves activity appears before the operation completes. TTY/plain
   timing and cleanup use injected clocks/events, not arbitrary sleeps. Prompt animation pauses;
   repeated invocations and exception/cancellation paths leave no background reporter or handlers.
4. Actual programmatic pyinfra tests show operation names and outcomes for changed/unchanged/failed
   operations. Errors, function arguments, raw outputs, staged secrets, and malformed helper bytes
   containing canaries never appear on either stream, including native formatter paths.
5. JSON stdout parses as one document for success, refusal, runtime failure, parse error, and dry
   run; stderr carries presentation. Help and create-admin preserve their exceptions. Missing or
   incorrect confirmation cannot mutate; explicit responses retain existing behavior.
6. Failed verification checks and finite reason text survive helper-to-public mapping. A selected
   old release is not reported as a successful requested deploy. Unknown state and lost replies
   remain unknown; exact status categories and successful verification requirements are preserved.
7. Both zipapps execute with `python3 -I -S`; only the transient helper gains inspection capability.
   Deterministic packaging, protocol limits, redaction, and architecture tests remain enforced.
8. Build tests preserve clean-source identity, isolated release evaluation, archive contents,
   checksum, and artifact cache behavior. If build execution code changes, perform the documented
   clean-checkout build gate, not a VPS deployment, and verify the artifact as required by development
   guidance. Presentation-only output tests must not require external infrastructure.

Run focused suites, full operations verification, `mix precommit`, relevant command help, Markdown
links/whitespace, and planning-terminology checks. Review this increment and affected consumers,
not the entire branch. A human terminal demonstration using controlled local doubles is required
to judge readability; snapshots and test counts alone are not UX acceptance. Existing-host refusal
can be exercised on staging only with explicit read-only authorization; provisioning/deployment or
destructive acceptance is not authorized by this document.

## Trade-offs and next-session checklist

The operator chose explicit command boundaries over an implicit provision-to-deploy switch or a
new independent infrastructure-maintenance mode. Bare provision deliberately refuses on a
completed host; this is clearer but requires an explicit original artifact for supported reconvergence.
Keep this restriction visible in command help and the runbook.

Progress provides liveness, not a reliable estimate or granular remote phase reporting. Native
pyinfra formatting is reused only through a safe adapter; unrestricted logging was rejected.
Plain JSON progress stays on stderr so pipes remain useful; no quiet/verbose option is added now.
Safety refuses ambiguity rather than guessing that a host is new or that a failed command did nothing.

Before implementation:

1. Obtain written-spec approval and resolve review findings in this document.
2. Write and review a bounded implementation plan linked to `tas-7ncz` and `tas-6dkg`.
3. Update the existing workstream handoff; use the approved-plan clean-session boundary by default.
4. In the implementation session, reread this specification and current repository guidance,
   refresh actual branch/host assumptions, and use delegated implementation plus independent review.
5. After implementation, update canonical design/runbook claims and indexes, record verification
   and remaining acceptance, and retire proposal wording without erasing the rationale.

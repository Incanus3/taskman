# Verification timeout complexity assessment

Status: scoped assessment complete; independently bounded phases accepted; concrete correction implemented and verified locally; native evidence not renewed.
Owner: `tas-lny6.3.9`. The operator accepts finite phase limits with a potentially longer, finite
and reasonable total wait. This supersedes the exact shared-budget policy direction in ruling 31;
concrete limits and total envelope below are approved and implemented; local verification is complete.

## Findings and scope

The source observations below describe the pre-correction `c44f537b` baseline; the approved
replacement contract is specified later in this document.

The production owner is [verification.py](../../ops/taskman_ops/host_helper/verification.py).
It combines a 45-second verification deadline, a configured readiness window (maximum 30 seconds),
configured HTTP attempt timeouts, three-second fixed-command/advisory caps and one-second polling.
Topology and local/public readiness share the readiness window; the advisory probe runs only after
local proof and only when the shared budget allows it.

Both polling functions maintain a derived attempt count as well as elapsed-time checks. Application
and full-public probe branches separately calculate time remaining and decide whether to attempt
HTTP. Curl duration formatting rounds down to milliseconds. These are concrete candidates for a
smaller control flow; removal must preserve finite termination and short final-attempt behavior.
A helper extraction is useful only if it removes duplicated policy, not merely relocates arithmetic.

The [state observer](../../ops/taskman_ops/host_helper/state.py) has its own default 60-second
history-loading budget. Application verification supplies its shared deadline; full-public
verification currently uses the observer default. This source-level distinction must be considered
when stating aggregate-duration guarantees; a complete 45-second hard wall-clock bound was not
reproduced by this inspection. Do not expand this into unrelated history/recovery redesign.

The [command runner](../../ops/taskman_ops/host_helper/commands.py) separately bounds subprocess
execution, output and pipe draining and terminates the process group after timeout/overflow.
These protect against hanging commands and inherited child pipes. Keep them independent of any
simplification of verification orchestration. Retain lifecycle locking and truthful failure reports.

The baseline [operations contract](../specs/2026-09-18-operations-contracts.md) required
a shared 45-second verification deadline, shared topology/readiness budget and bounded advisory
attempt; its timing paragraphs now describe the approved replacement. Replacing those with independent phase limits changes observable waiting/report behavior;
it is not automatically a behavior-preserving refactor.

## Options and recommendation

| Option | Simplification and cost | Behavior and risk |
| --- | --- | --- |
| Simplify current orchestration | Remove redundant poll stopping conditions and repeated probe calculations where a smaller direct flow suffices; retain one clear owner for each existing budget. Likely modest reduction. | Intended timing/report behavior retained; boundary regressions remain possible and need focused tests. |
| Independently bounded phases | Give inspection commands their fixed caps and readiness its own finite window, without distributing one aggregate deadline through every check. Could remove more arithmetic. | Verification may take longer and hold the deployment lock longer; advisory attempts may occur where today's shared budget would skip them. Requires explicit timing-contract approval. |
| Retain current implementation | No implementation or native-evidence renewal cost. | Keeps existing complexity and the public/application observation-budget distinction. |

The operator selected independently bounded phases and accepts a potentially longer, finite and
reasonable total wait. Proceed with a concrete scoped simplification that removes repeated budget
arithmetic; do not introduce a generic budget framework or weaken subprocess cleanup. Specify
phase limits and the derived total bound, including observation and lock occupancy, and make
advisory-attempt/report changes explicit. The concrete replacement values, scope and verification requirements below are approved.

## Verification and remaining correction

Fresh focused baseline: `uv run --project ops pytest ops/tests/host_helper/test_verification.py -q`
completed successfully; 90 cases collected. Existing tests exercise shared-budget exhaustion,
delayed safe listeners, configured local HTTP timeouts above three seconds, advisory clipping and
no advisory attempt after exhaustion. This is local evidence, not a native timing measurement.
That baseline check established existing behavior, not the approved replacement.

The independent-phase correction is implemented and locally verified. Canonical operations,
deployment and shared-Caddy timing/advisory contracts describe the replacement. Package-policy
correction and separate native-results acceptance remain workstream gates. This local correction
renews no native receipt.

## Concrete correction proposed for review

The correction is bounded to the existing verification flow. The following choices are operator-approved, implemented and locally verified. They refine the accepted independent-phase direction without changing
release/history authority or the public result schema.

| ID | Status | Proposal | Cost or constraint | Rationale |
| --- | --- | --- | --- | --- |
| T1 | Accepted | Independent topology and local-readiness windows, each at most 30 seconds; fixed inspections retain three seconds each | A late topology result no longer consumes local-readiness time; failure detection and deployment-lock occupancy can increase | [Phase limits](#phase-limits-and-total-envelope) |
| T2 | Accepted | Whole state observation has its own 60-second cooperative deadline | More observer paths need deadline checks; very large inventories can refuse earlier than today | [Observation boundary](#observation-boundary) |
| T3 | Accepted | Full public HTTP gets at most 30 seconds; application advisory gets a three-second Caddy check plus at most three seconds HTTP | Public requests may now run after the old shared budget would have expired | [Probe and report behavior](#probe-and-report-behavior) |

### Phase limits and total envelope

Use existing configuration only: `readiness_timeout` remains positive and at most 30 seconds;
`connection_timeout` remains positive and finite. No new knobs, persisted budgets, or generic budget
objects. Each polling window starts when its phase begins. An HTTP attempt inside a local window
uses `min(connection_timeout, remaining_window)`; full public HTTP uses
`min(connection_timeout, 30)` and advisory HTTP uses `min(connection_timeout, 3)`.

| Phase, in execution order | Full verification maximum | Application verification maximum |
| --- | --- | --- |
| Host authority: two capacity commands, administrator sudo, PostgreSQL query | 4 × 3 = 12 seconds | 12 seconds |
| Acquire lifecycle lock when not already held | 5 seconds | 5 seconds |
| Observe complete host state | 60 seconds | 60 seconds |
| Taskman service, executable identity; full mode also checks Caddy | 3 × 3 = 9 seconds | 2 × 3 = 6 seconds |
| Poll safe absent Taskman/distribution listeners | `readiness_timeout` ≤ 30 seconds | ≤ 30 seconds |
| Capture startup journal | 3 seconds | 3 seconds |
| Poll local readiness | fresh `readiness_timeout` ≤ 30 seconds | ≤ 30 seconds |
| Public HTTP, or advisory Caddy check then HTTP | ≤ 30 seconds | 3 + 3 = 6 seconds |
| Sum of intentional waits | **179 seconds** | **152 seconds** |

These maxima conservatively include phases which an earlier failure may skip. Keep inspection and
report order unchanged. Topology polling clips each `ss` command to its remaining window and the
three-second command cap; both polling loops use one elapsed-time stop condition, one-second
bounded sleeps and no derived attempt counter. Keep curl's downward millisecond rounding and never
start an attempt with less than one millisecond remaining.

`commands.py` allows up to two 0.2-second cleanup waits per failed command. Polling checks its phase
clock again after cleanup, so only the last attempt can overrun each polling window. Full mode has
eight fixed commands, two polling windows and one public command: at most 11 × 0.4 = 4.4 seconds
of additional cleanup. Application mode has seven fixed inspection commands, two advisory commands
and two polling windows: the same conservative 4.4-second allowance. Accepted rounded envelopes
are **185 seconds full / 160 seconds application**, including optional lock acquisition and this
cleanup allowance, under responsive local filesystem/kernel and normal process scheduling.
They are algorithmic wait envelopes, not hard real-time guarantees for wedged kernel I/O, process
creation or an unscheduled controller. Do not add signal timers or a worker framework to promise
those unsupported cases. No additional aggregate verification timer is required.

Read-only verification currently holds the lock only while observing host state: preserve that
boundary, with up to 60 seconds of observation occupancy plus local processing. Deploy/genesis/
restore callers already hold it through verification and history publication: the added verification
occupancy is conservatively at most 185 seconds full / 160 seconds application, excluding their
other work. Do not describe these numbers as a whole-deployment or whole-controller-command bound.
The helper runner's operation allowance is currently 660 seconds, sufficient for standalone
verification; its 60-second control/transfer allowance is unrelated. Explicit caller deadlines can
still interrupt larger workflows and retain existing transport/unknown-consequence behavior.

### Observation boundary

Source inspection at `c44f537b` found that the existing 60-second deadline principally guards
selection-history loading/validation. Release/backup/protection enumeration happens partly outside
those checks. Optional restore-input checksum validation in `_read_backups` also invokes the
unbounded streaming `sha256_file`. A claimed whole-observation envelope therefore needs this limited
correction, rather than simply removing the application's shared deadline argument.

Start a fresh 60-second deadline for both verification modes. Retain the observer's existing
`deadline` argument and default duration. Check it before/after inventory enumeration, record
loading, authority validation, collection construction and returning the final snapshot; check it
within scalable loops and optional checksum streaming. Bound each checksum chunk to the existing
one-MiB read size. Extend the shared checksum function with an optional absolute monotonic deadline
and raise `TimeoutError` on exhaustion; unchanged callers retain ordinary streaming behavior.
Translate that exhaustion to `StateAmbiguityError` at the observer boundary. An expired observation
returns the existing authority refusal (application error 10, boundary `authority`), with no
invented service/readiness report or partial successful snapshot. Preserve history/reference
validation, checksum equality and corruption handling; timeout must not become a corrupt-but-optional
backup warning. Keep selection-history timeout diagnostics where they already apply, and use a
fixed observation-timeout diagnostic for the newly covered paths.

This checks cooperative progress between bounded local operations. It cannot interrupt a blocked
filesystem syscall. Existing record-size/inventory-count limits remain; no history storage or
recovery redesign is part of the correction.

### Probe and report behavior

Full verification still requires Caddy, exact local/public readiness and valid HSTS. It makes one
public HTTP attempt after local proof, even if preceding phases consumed more than 45 seconds.
A public failure retains the full attempted report with readiness category 9. Unsafe topology,
EPMD, missing PostgreSQL, ambiguous listeners, service identity and journal failures keep their
existing immediate-failure classifications and check order.

Application proof still requires its five local checks and matching release identity; advisory
results cannot finalize deployment or publish successful history. After local success, check Caddy
once for up to three seconds. Inactive, unavailable or timed-out Caddy yields `not-attempted`.
Active Caddy permits exactly one HTTP/HSTS request, yielding `ready` or `not-ready`, including
`not-ready` for request timeout. Failed local proof never probes publicly. Exhaustion of an earlier
phase no longer suppresses a later advisory after successful local proof. A configured HTTP bound
below one millisecond makes no request and yields `not-attempted`. The baseline `_curl` skipped
that request but the advisory branch classified its absent response as `not-ready`; this approved
rule corrects that classification. The JSON request protocol accepts integer numeric settings, so
the sub-millisecond case is exercised at the validated settings seam without widening the protocol.
Retain raw-output suppression, 9,216-byte general and 64-KiB per-stream journal bounds.

### File boundaries and focused verification

- `host_helper/verification.py`: remove aggregate-budget plumbing from fixed inspections; keep two
  direct bounded polling loops, independent public/advisory limits and current result categories.
- `host_helper/state.py` and `checksums.py`: cover observation work with the existing deadline
  mechanism, including optional checksum progress; no separate observer process or state schema.
- `config.py`: retain numeric validation; replace the obsolete shared-budget error/comment with
  language describing the 30-second readiness-window maximum. The baseline comment incorrectly
  called the runner's operation allowance 60 seconds; it is removed.
- Focused verification/state/checksum/config tests and relevant workflow/protocol tests: exercise
  real public consumers, without duplicating all command/timing permutations.
- Reconcile the timing and advisory paragraphs in the operations contract and affected deployment
  references during implementation. The former 45-second policy describes the baseline; the
  replacement numeric policy is approved.

Required evidence: deterministic clocks demonstrate separate late topology/local success, clipped
last attempts, finite exhaustion and derived envelopes; delayed observation proves both modes use
60 seconds independently; expiration during enumeration/checksum refuses without a report or loss
of reference validation. Cover public/advisory outcomes after long successful local proof, failed
local proof, inactive Caddy and command timeout. Retain actual subprocess/process-group and inherited
pipe cleanup tests in `test_commands.py`, plus output/journal bounds. Check caller-held lock behavior
and read-only observation lock release. Compare production control flow and test complexity before
and after; deadline coverage must not turn into a generic orchestration framework. Run focused tests,
helper isolation/packaging checks, the normal operations gate and `mix precommit` after implementation.

### Implemented evidence and limits

On 2026-10-07, focused implementation verification passed 719 cases with three explicit native
PostgreSQL skips. Independent scoped review reproduced 270 focused cases, found no production
defect and confirmed the phase arithmetic, process cleanup, observation refusal, reports and real
lock boundaries. A stale authoritative deadline clause was corrected and re-reviewed; no scoped
findings remain. The dedicated verification file now has 108 passing cases, against the 90-case
pre-correction baseline.

Final gates:

- `uv run --project ops python -m compileall -q ops/taskman_ops ops/tests`: exit 0.
- `bash -n ops/taskman ops/caddy/render-caddyfile`: exit 0.
- `uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0 -o addopts='' -q`:
  2,237 passed, 12 explicit optional skips; existing fork/forkpty deprecation warnings remain.
- `POSTGRES_HOST=<disposable-local-container-IP> mix precommit`: 1,210 passed, 32 excluded,
  seed 880643; disposable PostgreSQL container and its volume removed afterward.
- `./ops/taskman verify --help`, changed documentation links/timing anchors, whitespace and
  implementation-facing planning-identifier checks passed. Both historical-snapshot banners were
  rendered at desktop width and inspected; their original source body/hash remains provenance.

Helper packaging/isolation and actual lifecycle/protocol consumers are included in the focused and
full operations gates. Neither OTP release-builder configuration nor helper archive membership
changed; generated-helper isolation checks cover the modified shared checksum module. A new OTP
release build or native host validation is not claimed.

Source comparison with `5ab91fd4` confirms verification orchestration shrank from 668 to 648 lines
and its branch nodes from 127 to 120. Required observer/checksum coverage adds checks: across the
four production files total lines increase by 39, while branch nodes decrease by two. The
simplification removes aggregate-budget/attempt-count state; it does not claim total line reduction
or hide deadline plumbing in a framework. Branch counts cover `if`, conditional expressions,
`for`, `while`, exception handlers and comprehensions.

No host was contacted, no native evidence renewed and no publication performed. Cooperative
deadlines retain the responsive-I/O/scheduling limitation stated above.

Approval covers T1–T3 together, including the 185/160-second envelopes and observation/checksum
scope. Retaining the existing timing flow
leaves the accepted simplification correction unresolved; deferral requires an explicit operator
choice before Caddy closure. Approval here covers this bounded local correction and its verification;
package-policy correction, native-results acceptance, new native validation and production changes
retain their separate gates.

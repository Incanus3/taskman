# Operations lock coverage

Status: active. Updated: 2026-10-29. Resume: `$resume operations-lock-coverage`.

The prior operations branch is merged. Work continues on the dedicated
`operations-lock-coverage` branch, applied and rebased onto main at `b2dbe5cd`.

## Objective and authority

Prevent conflicting cooperating host operations, especially PostgreSQL convergence during backups,
while retaining controller-driven pyinfra and the existing inner lifecycle lock.
Workstream task: Taskman Task 32 in the registered Project; verify `Source: taskman/br/tas-bbry`
after resolving the clone binding, and stop on mismatch. Transferred from
`tas-561x.9`, whose design/review evidence remains
historical provenance. The [complete written design](../specs/2026-09-18-provisioning-lock-coverage-proposal.md)
owns contracts, alternatives, file boundaries and verification requirements. The
[HTML review companion](../specs/operations-admission-review/index.html) exposes the provisioning
sequence, recovery paths, command conflicts and trade-offs; its source snapshot preserves exact contracts.
Use the registered Taskman Project for planning and execution; any legacy Beads-backed planning
wording below is superseded by the current repository tracking policy.
[Operations contracts](../specs/2026-09-18-operations-contracts.md),
[architecture](../specs/2026-09-09-dedicated-host-deployment-design.md) and the
[runbook](../guides/deployment.md) still own current implemented behavior.

## Current checkpoint

No admission/reservation code is implemented. At the earlier resume checkpoint, provisioning
convergence ran before lifecycle-locked genesis and scheduled backups used only that lifecycle lock.
The helper protocol was v3 and remains v3 after the 2026-10-25 rebase. Independent scoped correctness
review of the complete written design is finished. Ownership and manual-recovery direction are
approved; full written-design approval and implementation planning remain pending.

Approved direction: one fixed host-wide admission flock, a root-private provisioning reservation
bound to the confirmed installation, finite nested owner-ID use, conflict status 12, and no completion
proof from the owner ID. Uncertain provisioning retains the reservation and backup suspension until
explicit inspection/manual recovery. Immediate automatic termination after provisioning caller loss
is not required; this grants no new owner-death containment for other helpers. Broad transport or
convergence replacements were rejected for cost. Normal operational commands remain foreground and
awaited; long-running target services are allowed.

The design specifies `inspect-admission`, `recover-provision` and admitted `resume-backups`, persistent
timer suspension/legacy draining, owner propagation, framed pgpass proof and private staging.
Implementation must preserve noncreating fresh-host observation, scheduled O_RDONLY admission under
ProtectSystem=strict, and queued-job plus active operational-unit/cgroup quiescence evidence.
Parked PostgreSQL and CLI UX workstreams are not implementation dependencies; reconcile overlapping
protocol/ownership decisions before their later execution without absorbing their refactors.

## Remaining order and gates

1. Obtain explicit operator approval of the complete written design. Revalidate affected review
   conclusions against the current baseline; then create/review an implementation plan. Create
   execution tasks in the registered Taskman Project only after plan approval.
2. Obtain plan approval and preserve the clean-session implementation boundary before implementation.
3. Implement, perform scoped independent correctness review and local operations/package/runtime/TTY
   verification, and update canonical contracts/runbook/indexes to reflect implemented behavior.
4. Obtain separate authority for native host actions and exercise the design's native overlap,
   interruption/recovery, legacy scheduler, first/repeat provisioning and reboot acceptance gates.
   Preserve private recovery resources and refresh affected source/artifact/acceptance bindings.
5. Refresh target/remote/review and final-head CI; obtain separate publication and merge authority.
   Keep this handoff until explicit operator workstream-completion confirmation.

## Verification baseline and limits

Written-design review found no remaining material contradictions. At the earlier resume checkpoint,
handoff links and whitespace passed, and `mix precommit` passed 914 tests. That evidence verifies
that checkpoint, not the proposed implementation, the later rebased baseline or native acceptance.
Historical native/source/artifact baselines are retained privately and do not establish current
readiness. The [native acceptance guide](../guides/native-acceptance.md) defines portable criteria;
[private recovery obligations](../inventories/operations-environments.md#private-evidence-and-recovery) remain.
The earlier source inspection established the then-current v3/unlocked baseline only; it does not
verify the proposed implementation or authorize host action, deployment, publication or merge.

On 2026-10-25, the primary worktree was rebased onto `f882deb7`; the handoff conflict preserves
historical test evidence and current private-evidence references. Beads reconciliation through
`br` retained all 340 original database records unchanged and added 15 upstream records; the
export contains the union of both conflict sides, with 355 issues, 445 dependencies, 135 labels
and 465 comments. Sync reports healthy state and no coverage drift. Local Markdown file links
and whitespace passed. `mix precommit` compiled the application but stopped before tests because
PostgreSQL at localhost:5432 refused connection. Revalidate affected design conclusions against
the new operations baseline before implementing; no implementation or native acceptance was run.

Current source inspection confirms helper protocol v3, lifecycle-only scheduled-backup locking,
and no fixed admission lock, provisioning reservation, or inspection/recovery/resume commands.
The local branch contained only the resume-handoff commit; no implementation plan exists.
The prior operations merge prerequisite is satisfied. Full written-design approval remains the
next decision; the earlier independent review is historical evidence, not a fresh review of this
baseline. Applying and rebasing the branch succeeded without conflicts.

Current verification: `mix precommit` passed (1381 passed, 3 skipped, 51 excluded); the six
local handoff file links resolve and whitespace checks passed. The review companion and source
snapshot were checked at a 1440-pixel desktop width in isolated Chromium; SVG accessibility,
source-text/hash fidelity and all 48 local HTML links/assets/anchors passed. This verifies the
documentation checkpoint and existing application baseline, not admission implementation or native behavior.

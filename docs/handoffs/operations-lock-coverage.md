# Operations lock coverage

Status: active. Updated: 2026-09-24. Resume: `$resume operations-lock-coverage`.

The prior operations branch is merged. Work continues on the dedicated
`operations-lock-coverage` branch from refreshed main at `2fd2c51b`.

## Objective and authority

Prevent conflicting cooperating host operations, especially PostgreSQL convergence during backups,
while retaining controller-driven pyinfra and the existing inner lifecycle lock.
Workstream task: `tas-bbry`; transferred from `tas-561x.9`, whose design/review evidence remains
historical provenance. The [complete written design](../specs/2026-09-18-provisioning-lock-coverage-proposal.md)
owns contracts, alternatives, file boundaries and verification requirements.
[Operations contracts](../specs/2026-09-18-operations-contracts.md),
[architecture](../specs/2026-09-09-dedicated-host-deployment-design.md) and the
[runbook](../guides/deployment.md) still own current implemented behavior.

## Current checkpoint

No admission/reservation code is implemented. On the refreshed baseline, provisioning convergence
still runs before lifecycle-locked genesis, scheduled backups use only that lifecycle lock, and the
helper protocol remains v3. Independent scoped correctness review of the complete written design is
finished. Ownership and manual-recovery direction are approved; full written-design approval and
implementation planning remain pending.

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
   conclusions if the baseline changes materially; then create/review a Beads-backed implementation plan.
2. Obtain plan approval and preserve the clean-session implementation boundary before implementation.
3. Implement, perform scoped independent correctness review and local operations/package/runtime/TTY
   verification, and update canonical contracts/runbook/indexes to reflect implemented behavior.
4. Obtain separate authority for native host actions and exercise the design's native overlap,
   interruption/recovery, legacy scheduler, first/repeat provisioning and reboot acceptance gates.
   Preserve private recovery resources and refresh affected source/artifact/acceptance bindings.
5. Refresh target/remote/review and final-head CI; obtain separate publication and merge authority.
   Keep this handoff until explicit operator workstream-completion confirmation.

## Verification baseline and limits

Written-design review found no remaining material contradictions. On the refreshed baseline,
handoff links and whitespace passed, and `mix precommit` passed 914 tests. This verifies the
resume checkpoint, not the proposed implementation or native acceptance. Historical
native/source/artifact baselines remain qualified in the
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md); protected recovery obligations
remain in the [environment inventory](../inventories/operations-environments.md#staging-recovery-retention).
The refreshed source inspection establishes the current v3/unlocked baseline only; it does not
verify the proposed implementation or authorize host action, deployment, publication or merge.

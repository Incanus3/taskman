# Operations CLI UX

Status: parked. Updated: 2026-09-18. Resume: `$resume operations-cli-ux`.

Resume only after the current operations branch is merged and this workstream is selected.
Use its own dedicated branch from refreshed main; retain the written-spec review and plan gates below.

## Objective and authority

Make operations progress, plans, and final outcomes understandable without weakening output
safety or release authority. Design task: `tas-7ncz`. Reconciliation owns `tas-6dkg`;
UX consumes its retained failure reports.
The [proposed specification](../specs/2026-09-09-operations-cli-ux-design.md) owns the design;
the [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) and
[runbook](../guides/deployment.md) still own implemented behavior.

## Current checkpoint

Command/output direction is approved; scoped independent review requested changes. Full written-
spec approval and an implementation plan remain pending. No UX code is implemented.

Implemented reconciliation owns protocol v3, artifact/source and acknowledgment rules, live-schema
safety and retained failure reports. Before first success, provision may reconcile a different
artifact or recover from archive loss; credentialed checks remain decisive. Exact completed-first-
install replay stays constrained; later replacement uses deploy. Future upgrade support remains,
without conversion of old staging formats.

Current provision discovers the host before target resolution/build but decrypts secrets first.
Credential-free completed-install refusal and presentation remain proposed. The specification
owns the historical incident and presentation architecture; documentation checks do not verify UX.
The separate [lock-coverage workstream](operations-lock-coverage.md) is parked for its own
dedicated branch after the operations merge. No PostgreSQL refactor or
host changes are authorized here.

## Next actions when selected after the operations merge

1. Resolve the scoped architecture/security review recorded on `tas-7ncz`: non-throwing presentation
   callbacks without global hook changes, terminal-control sanitization, staging-only identity
   wording, pre-mutation failure outcomes, and bounded inspection responses. Also self-review the
   JSON error-form and interactive `create-admin` exceptions. No final written-spec approval has
   been given.
2. Ask the operator to review the finished specification, then write and review its bounded plan.
3. Update this handoff at the approved-plan boundary; continue implementation in a clean session
   by default. Consume reconciliation's protocol v3 and `tas-6dkg` results.
4. In the implementation session, reread the complete specification and current repository
   guidance, refresh actual branch/host assumptions, and use delegated implementation plus
   independent review. This parked workstream is not selected by the current documentation cleanup.
5. After implementation, update canonical design/runbook claims and indexes, record verification
   and remaining acceptance, and retire proposal wording without erasing the rationale.

No host changes are authorized by this design work. The separate
[VPS acceptance report](../research/2026-09-17-operations-vps-acceptance.md) owns completed host acceptance and exact artifact context;
the [PostgreSQL refactor](postgresql-host-python.md) remains parked.

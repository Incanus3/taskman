# Operations VPS readiness

Status: active. Updated: 2026-09-15. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Implement the approved desired-target deployment reconciliation locally, then prepare separately
authorized clean staging recreation and fresh provisioning/readiness.

- Approved specification:
  [Desired-target deployment reconciliation](../specs/2026-09-09-deploy-reconciliation-design.md)
- Approved implementation plan:
  [Reconciliation delivery](../plans/2026-09-14-deploy-reconciliation.md)
- Operator workflow and acceptance gates: [Deployment runbook](../deployment.md)
- Parent issue: `tas-sr4b`; active task: `tas-sr4b.10`.

## Current checkpoint

Plan Tasks 1–9 are implemented and their Beads issues are closed on
`dedicated-host-deployment-automation`. Restore replacement is implemented through `8e925e44`.
It preserves original database identity and required safety copies, normalizes pending replacements,
requires fresh confirmation for a third target, and bounds eligible safety attempts while retaining
independent backup protections. The first accepted plan supplies the stable starting-state audit.
Scoped independent review approved the increment after two fix rounds.

The old staging installation remains historical evidence outside the supported record/runtime
boundary. Do not migrate, repair, or invoke the new controller against it. No host action, push,
merge, deployment, or publication has occurred or is authorized.

## Next action

Task 10 (`tas-sr4b.10`), recovery-aware cleanup and paged deletion, is in progress.
Implement filesystem-only inspection, full recovery reference protection, bounded target pages and
confirmed batches, and truthful partial-deletion evidence. Preserve damaged unreferenced backup
remainders; they are not validated deletion candidates. Follow the approved plan's exact protocol
and acceptance criteria, then obtain independent scoped review.

After Task 10, continue integrated local verification and operator documentation in `tas-sr4b.11`.
Native PostgreSQL/systemd/VPS acceptance remains separately authorized.

## Verification baseline and remaining gates

- Restore replacement expanded gate: 141 passed, including packaged controller/helper behavior.
- Final audit-snapshot correction: 6 focused tests passed; bounded regression 52 passed with the
  unchanged 65-cycle case deselected; independent focused re-review 3 passed.
- Python compileall succeeded; fresh `mix precommit`: 805 passed.
- Full operations suite is not yet green; remaining cleanup fixture/protocol migration and
  integrated verification belong to Tasks 10 and 11.
- Native effects in packaged tests are doubled. Real PostgreSQL/systemd/VPS behavior remains an
  acceptance-stage risk; Task 11 also owns local build and packaging verification.

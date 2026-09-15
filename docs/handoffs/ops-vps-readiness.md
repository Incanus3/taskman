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

Task 10 (`tas-sr4b.10`), recovery-aware cleanup and paged deletion, is implemented at `8d9774d9`
and under independent scoped review. It provides filesystem-only inspection, full recovery reference
protection, bounded target pages/confirmed batches, and partial-deletion evidence. Damaged
unreferenced backup remainders remain excluded from validated deletion candidates. The coordinator
reproduced the required cleanup/preflight tests plus packaged cleanup: 43 passed. Resolve any review
findings before closing the issue. Review found missing full-history release protection, a
64-entry temporary inventory truncation, lost proved-change evidence within a partial batch, and
missing packaged recovery scenarios; these are the immediate fixes.

After Task 10, continue integrated local verification and operator documentation in `tas-sr4b.11`.
That gate must correct restore capacity preflight: its required stdout is currently suppressed by
the sensitive SSH path, a mismatch masked by test doubles. It also owns the four new substantial
shell admission predicates, stale architecture preflight guard, and two leaked planning comments.
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

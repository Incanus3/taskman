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
- Parent issue: `tas-sr4b`; next task: `tas-sr4b.8`.

## Current checkpoint

Plan Tasks 1–7 are implemented and their Beads issues are closed. The current GitButler branch is
`dedicated-host-deployment-automation`; the Task 7 implementation ends at `995d2fd4`.

Task 7 now provides marker-free resource admission, exact pre-mutation host/database/credential
authority, deploy-style target resolution and independent downgrade consent, confirmed material
planning, null-baseline backup/protection/pruning, scheduler-safe interrupted-genesis recovery,
first-success publication, and bounded default-public packaged recovery coverage. Its final
independent review found no Critical or Important issues.

The old staging installation remains historical evidence outside the supported record/runtime
boundary. Do not migrate, repair, or invoke the new controller against it. No host action, push,
merge, deployment, or publication has occurred or is authorized.

## Next action

Execute plan Task 8 (`tas-sr4b.8`), **Restore binding and same-backup recovery**, using delegated
implementation and a distinct independent reviewer. Before implementation, read the complete
approved specification, plan, and `docs/development.md`; then move the bead to `in_progress` and
create the Task 8 SDD brief from the approved plan.

Task 8 must add restore-specific inspection and database creation intent, recover all recognized
same-backup arrangements, preserve the original and later writes, and consume scheduled-helper
convergence before supported-format or binding writes. Continue afterward in dependency order
through `tas-sr4b.11`.

## Verification baseline and remaining gates

- Task 7 focused final gate: 183 passed.
- Fresh controller Task 7 integration gate across host, PostgreSQL, systemd, protocol, package,
  controller, CLI, dry-run, and E2E suites reached 100% with exit 0.
- Fresh `mix precommit`: 805 passed.
- Final Task 7 independent review: clean; its focused gate passed 6 tests.
- Real PostgreSQL/systemd/VPS behavior remains an acceptance-stage risk owned by later separately
  authorized verification. Task 11 owns whole-workstream, Docker build, and clean staging gates.


# Operations VPS readiness

Status: active. Updated: 2026-09-14. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Implement the approved desired-target deployment reconciliation locally, then prepare separately
authorized clean staging recreation and fresh provisioning/readiness.

- Approved specification:
  [Desired-target deployment reconciliation](../specs/2026-09-09-deploy-reconciliation-design.md)
- Approved implementation plan:
  [Reconciliation delivery](../plans/2026-09-14-deploy-reconciliation.md)
- Implemented baseline:
  [Dedicated-host deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md)
- Operator workflow and acceptance gates: [Deployment runbook](../deployment.md)
- Parent reconciliation issue: `tas-sr4b`; next task: `tas-sr4b.5`.

## Current checkpoint

Tasks 1–4 of the approved plan are complete on GitButler branch
`dedicated-host-deployment-automation`. Each passed independent task review and scoped fix
re-reviews where required.

- `tas-sr4b.1` is closed. Commits `f22ed1a` and `e1c7bf5` implement exact supported release,
  selection, backup-protection, and restore-target records, strict `0600` authority, and both
  helper-package reader closures.
- `tas-sr4b.2` is closed. Commits `3cf8a9f`, `5c9fa30`, and `a2ae001` implement final-byte artifact
  identity, bounded frozen dirty snapshots, `DeploymentTarget`, immutable clean-input capture and
  revalidation, deterministic installed/cache resolution, and bounded local source ordering.
- Baseline documentation and prior Beads changes were committed as `5af43ced`; the Task 1 tracking
  transition was committed as `01e0a80`; the Task 2 tracking transition was committed as `1c25482`.
- `tas-sr4b.3` is closed. Commits `45e2179`, `dc9698e`, `fee0e20`, `8e7f97e`, `b2a32cf`,
  `077185a`, and `58f9635` implement exact backup provenance, complete deadline-bounded history
  validation, full reference retention, successful-reference transfer, bounded deployment/restore
  attempts, and interruption-safe pruning markers.
- `tas-sr4b.4` is closed. Commits `7bce6185`, `99d032ef`, `68304919`, `4943539a`, `916ce86f`,
  and `d48212cf` implement protocol v3, exact schema-path budgets, bounded discovery/inventory,
  deadline propagation, exact mutation-result validation, single final-observation ownership, and
  conservative command-level aggregation.

Task 6 still owns public workflow migration to `DeploymentTarget` and the clean-input
re-identify/discover/re-resolve loop. Task 11 still owns real Docker clean/dirty build acceptance.
These are planned dependencies, not Task 2 blockers.

The old staging installation remains historical evidence outside the supported record/runtime
boundary. Do not migrate, repair, or invoke the new controller against it. No host action, push, or
merge has occurred or is authorized.

## Next actions

1. Execute plan Task 5 (`tas-sr4b.5`) with delegated implementation and distinct independent review:
   safely quiesce earlier scheduled-backup processes and converge the compatible executable before
   supported-format writes.
2. Continue in dependency order through `tas-sr4b.11`.

## Verification and remaining gates

- Task 1 controller gate: 167 focused tests passed; `compileall` exited 0.
- Task 2 controller gate: 69 focused tests passed; `compileall` exited 0.
- Task 3 controller gate: 148 focused tests passed; package/entrypoint checks passed 10 tests;
  `compileall` exited 0. Independent review plus three scoped re-reviews are clean.
- Task 4 controller gate: 236 focused tests passed; transient package execution is included;
  `compileall` exited 0; `mix precommit` passed 805 tests. Independent review plus three scoped
  re-reviews are clean. Repository-wide Python collection still
  stops at the Task 6-owned removed `ArtifactResolution` import in `ops/tests/test_cli.py`.
- The Task 2 implementer also ran `mix precommit` after its production fix round; the final change
  after that run added only the complete clean-input test matrix.
- Integrated failures in consumers assigned to Tasks 3–10 remain expected until their planned
  cutovers land. Full local/build/package/documentation verification belongs to Task 11.

Clean staging recreation, provisioning, deployment, push, and merge require separate operator
authorization. Administrator login acceptance is already complete on its separate track and does
not establish deployment reconciliation success.

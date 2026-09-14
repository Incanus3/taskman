# Operations VPS readiness

Status: active. Updated: 2026-09-14. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Implement the approved desired-target deployment reconciliation, then establish clean staging
through separately authorized host recreation and fresh public-controller provisioning/readiness.

- Approved specification:
  [Desired-target deployment reconciliation](../specs/2026-09-09-deploy-reconciliation-design.md)
- Approved implementation plan:
  [Reconciliation delivery](../plans/2026-09-14-deploy-reconciliation.md)
- Implemented baseline:
  [Dedicated-host deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md)
- Operator workflow and acceptance gates: [Deployment runbook](../deployment.md)
- Readiness: `tas-b7kd`; reconciliation: `tas-sr4b`; failed-result diagnostics: `tas-6dkg`.

The separate [Operations CLI UX](operations-cli-ux.md) specification is not approved by this
decision. The [PostgreSQL host-side Python](postgresql-host-python.md) refactor remains parked.

## Current checkpoint

The operator approved the complete reconciliation design on 2026-09-14, including its
simplicity/non-adversarial reliability scope, Python-first workflow policy, and removal of the
unused proposed selection-history listing operation. The operator also approved the reviewed
implementation plan on 2026-09-14. Both approval gates are satisfied; no reconciliation code exists yet.

The operator subsequently approved a one-time compatibility break from the previous unmerged
design and existing unfinished staging installation. The specification and plan now require only
the new record formats/current runtime; future upgrade compatibility and recovery remain in scope.
Do not implement an old-staging migration or in-place recovery path.

Reconciliation owns protocol v3 and `tas-6dkg`; CLI UX consumes that baseline. Read the complete
specification for exact recovery, provenance, retention, pagination, and result contracts.
Delivery tasks `tas-sr4b.1` through `tas-sr4b.11` are created with dependencies; the last owns
independent verification. Follow the plan's reader-before-writer and local-only integration
constraints. Task 1 now owns single-format readers and old-format rejection, not dual readers.

Administrator creation, authenticated workspace/admin access, and logout acceptance are complete
under `tas-q5lo`, separately from deployment completion. Last recorded staging state has the OTP 29
candidate selected and running while successful history still names the OTP 27 genesis release;
no migrations changed during that failed transition. These are historical acceptance observations,
not proof of a future fresh installation. The operator reports no real application data. Preserve
the existing host until its exact recreation/reset is separately authorized; do not repair history.

Work belongs to `dedicated-host-deployment-automation` (PR 16), including the Projects design
commit previously moved there at the operator's request. The approved pre-amendment checkpoint was
committed as `df3e47b304161e251a65e0b642fa743f80679c70` at the operator's request. Compatibility
amendments remain uncommitted; no push, merge, or host action was requested.

## Next actions

1. In a fresh session, read the complete approved specification and plan, refresh repository/task
   state, and begin `tas-sr4b.1` (single-format exact record readers and package closure).
2. Follow the approved dependency order through `tas-sr4b.11`, with delegated implementation and
   distinct independent verification under repository policy.
3. Implement and verify locally, then update the canonical deployment design/runbook/indexes with
   actual implemented behavior.
4. Prepare exact clean staging recreation and fresh provisioning actions for operator authorization.
   Prior in-place deployment authorization does not authorize the reset; never use the new
   controller against old-format host authority to attempt automatic migration.

## Verification and remaining gates

Final fresh-context Sol review on 2026-09-14 found no critical issues and confirmed the design's
compatibility/recovery boundary. Both important plan gaps are corrected: Task 8 now explicitly
integrates and tests scheduler convergence before initial/reapply restore binding publication;
Tasks 2/6 identify clean source inputs before discovery and revalidate before confirmation, with
re-resolution and drift tests. Sol's scoped follow-up closed both findings with no new issues.
Affected task acceptance criteria are synchronized; evidence is recorded in `tas-sr4b`.
The design is unchanged; no implementation or host action occurred.

Compatibility-amendment verification on 2026-09-14: nine affected documents passed local links,
anchors and whitespace checks; `mix precommit` passed 805 tests. Independent scoped review found
only a stale checkpoint paragraph, now corrected. The marker simplification was also reviewed;
future upgrade/recovery guarantees remain intact. Evidence is in `tas-sr4b`, comment 212.

Planning verification on 2026-09-14: relative links, anchors, whitespace and placeholder checks
passed; `mix precommit` passed 805 tests. The scoped plan review found a manual-backup admission
gap; the plan now explicitly opens that public path for validated unfinished deployment and
requires focused public backup tests. Independent focused follow-up closed the finding with no
new blocker. Review evidence is recorded in `tas-sr4b`.
These establish documentation hygiene and the existing application baseline, not correctness
of unimplemented reconciliation. Independent design reviews were read-only; no host action
occurred during review or approval.

Design and plan approval are complete. Broader invitation/email, API key,
LiveView, off-host backup, rollback/restore, controlled-failure, reboot, and leakage acceptance
retain their existing operator-selection gates. No destructive recovery or manual history repair
is authorized.

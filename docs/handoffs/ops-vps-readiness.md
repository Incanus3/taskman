# Operations VPS readiness

Status: active. Updated: 2026-09-14. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Plan and implement the approved desired-target deployment reconciliation, then complete the
interrupted staging deployment through the public controller and continue operator-selected
readiness acceptance.

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

Reconciliation owns protocol v3 and `tas-6dkg`; CLI UX consumes that baseline. Read the complete
specification for exact recovery, provenance, retention, pagination, and result contracts.
Delivery tasks `tas-sr4b.1` through `tas-sr4b.11` are created with dependencies; the last owns
independent verification. Follow the plan's reader-before-writer and local-only integration
constraints. The parent issue's stale approval-pending summary has been reconciled.

Sol's full review found two contract gaps; its focused follow-up confirmed both amendments
closed them without substantive new contradictions. Review evidence is recorded in `tas-sr4b`.
The subsequent development-policy application received local checks and operator approval.

Administrator creation, authenticated workspace/admin access, and logout acceptance are complete
under `tas-q5lo`, separately from deployment completion. Last recorded staging state has the OTP 29
candidate selected and running while successful history still names the OTP 27 genesis release;
no migrations changed during that failed transition. Refresh actual state before host actions.
Do not manually edit current or successful history.

Work belongs to `dedicated-host-deployment-automation` (PR 16), including the Projects design
commit previously moved there at the operator's request. Documentation changes remain uncommitted;
approval does not authorize committing, pushing, merging, or deploying.

## Next actions

1. In a fresh session, read the complete approved specification and plan, refresh repository/task
   state, and begin `tas-sr4b.1` (compatible exact record readers and package closure).
2. Follow the approved dependency order through `tas-sr4b.11`, with delegated implementation and
   distinct independent verification under repository policy.
3. Implement and verify locally, then update the canonical deployment design/runbook/indexes with
   actual implemented behavior.
4. Refresh staging state and continue the already-authorized runtime deployment/readiness work
   through the public controller. Preserve the running candidate until reconciliation is ready.

## Verification and remaining gates

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

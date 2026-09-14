# Operations VPS readiness

Status: active. Updated: 2026-09-14. Resume: `$resume ops-vps-readiness`.

## Objective

Finish the desired-target deployment reconciliation specification, implement it after explicit
written approval and planning, complete the interrupted staging deployment through the public
controller, and continue operator-selected VPS readiness acceptance without weakening artifact,
migration, backup, or host authority.

## Durable authority

- Proposed specification under operator review:
  [Desired-target deployment reconciliation](../specs/2026-09-09-deploy-reconciliation-design.md)
- Implemented baseline:
  [Dedicated-host deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md)
- Operator workflow and acceptance gates: [Deployment runbook](../deployment.md)
- Readiness task: `tas-b7kd`; reconciliation implementation: `tas-sr4b`; bounded failed-result
  diagnostics: `tas-6dkg`

The separate [Operations CLI UX](operations-cli-ux.md) and parked
[PostgreSQL host-side Python](postgresql-host-python.md) workstreams remain outside this increment.

## Current checkpoint

The operator has approved the reconciliation design sections discussed so far, but has not approved
the full written specification. Review remains active. No implementation plan or reconciliation code
exists yet.

Reconciliation now explicitly owns protocol v3 and `tas-6dkg`; the CLI UX proposal consumes that
baseline, including source and acknowledgment rules. This ownership decision is approved; the
complete written specification remains under review.

The design contains the agreed deploy/provision, dirty-artifact, downgrade, bounded migration-backup,
restore-before-first-success, and explicit restore-target-replacement rules. Generic cleanup remains
filesystem-only; database deletion stays in restore paths with the required context.

The 2026-09-14 consistency review found five remaining restore gaps and four smaller clarifications.
The original findings and their approved resolutions are recorded in dated `tas-sr4b` comments.
The operator requested one-by-one discussion. Finding 1 is approved and incorporated: ordinary
same-backup restore retries accept a validated retired-only original and rebuild incomplete temporary
databases, including those without a migration table. Finding 2 is also approved and incorporated:
post-success restore cleanup validates authority without requiring current application readiness;
health is reported separately. Finding 3 is approved and incorporated as restore-only `--reapply`:
fresh plan, typed confirmation, safety backup, and successful selection; unfinished attempts still
use ordinary retry or `--replace-unfinished`. Finding 4 is approved and incorporated: restore
safety attempts have bounded original/newest/three-intermediate retention with confirmed pruning;
abandoned inputs lose only their input reference, and independent protection remains intact.
Finding 5 is approved and incorporated: replacement validates abandoned input metadata/identity
without demanding usable dump contents, while new inputs and required safety copies remain fully
validated. Dry-run replacement acknowledgment is also clarified: preview may omit
`--replace-unfinished`, but a fresh same-backup restore preview requires `--reapply`.
Exit 11 is included in the consolidated status list. The stale administrator-acceptance checklist
item now states the lasting separation from deployment completion. The UX opening definition of
provision now includes unfinished-target replacement. All findings from this review are addressed;
full written-spec approval remains pending. Individual repair approvals do not approve the complete
specification.

The design's dated verification recap has been removed; requirements remain in their owning sections,
evidence sources are beside the observed baseline, and verification status is recorded here.

Administrator creation, authenticated workspace/admin access, and logout acceptance are complete
under `tas-q5lo`. They do not complete deployment history. The last recorded host state has the OTP
29 candidate physically selected and running while successful history still names the OTP 27
genesis release; no migrations changed during the failed transition. Refresh actual state before
any host operation and never manually edit `current` or selection history.

The current GitButler stack is `dedicated-host-deployment-automation` with PR 16. The ongoing
specification review is recorded on this stack. No merge is authorized.

## Next actions

1. Obtain operator review and explicit approval of the complete amended specification. All five
   restore findings and four smaller consistency clarifications from the 2026-09-14 review have
   approved resolutions incorporated in the design and related UX contract. No implementation plan
   or code is authorized by those individual approvals.
2. After approval, use the writing-plans workflow to create and review a bounded implementation
   plan. Create scoped Beads delivery tasks, including independent verification.
3. Update this handoff at the approved-plan boundary and start implementation in a clean session by
   default.
4. Implement and verify locally. Then update the canonical deployment design, runbook, and indexes
   with implemented behavior.
5. Refresh staging state and continue the already-authorized runtime deployment and readiness checks
   through the public controller. Preserve the running candidate until reconciliation is ready.

## Verification baseline

On 2026-09-14, the documentation wording changes passed relative-link and whitespace checks, and
`mix precommit` passed with 805 tests. These checks establish documentation hygiene and the existing
application baseline, not correctness of the unimplemented reconciliation behavior. The design's
acceptance criteria retain the required implementation and independent review gates. No host
operation was performed during this review.
The consistency review was read-only; application tests do not establish the proposed restore
state machine's correctness. Full written-spec approval remains pending the findings above.

## Constraints and remaining gates

The active human review gate blocks planning. Do not manually repoint releases, synthesize history,
retry the retired failed artifact, merge, or run destructive recovery. Invitation/email, API key,
broader LiveView, off-host backup, second-release rollback/restore, controlled-failure, reboot, and
leakage acceptance still require operator selection or their existing explicit gates. Keep
`tas-6dkg` coordinated with the CLI UX workstream while preserving its required failure-evidence
behavior here.

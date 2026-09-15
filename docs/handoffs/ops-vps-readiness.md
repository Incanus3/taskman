# Operations VPS readiness

Status: active. Updated: 2026-09-15. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Complete local desired-target deployment reconciliation, then prepare separately authorized clean
staging recreation and fresh provisioning/readiness.

- Approved [specification](../specs/2026-09-09-deploy-reconciliation-design.md) and
  [implementation plan](../plans/2026-09-14-deploy-reconciliation.md).
- Operator workflow and external acceptance gates: [deployment runbook](../deployment.md).
- Parent issue: `tas-sr4b`; active task: `tas-sr4b.11`. Tasks 1–10 are closed.

## Current checkpoint

Implementation is on `dedicated-host-deployment-automation`, through `5768e96b`. Restore replacement
and recovery-aware cleanup are complete and independently approved. Cleanup protects complete
successful-history references, handles all recognized temporaries, and retains evidence of changes
when a later deletion fails.

The integrated fixture correction (`64716039`) and scheduled-backup recovery correction (`42c8ba72`)
are independently approved. Scheduled backup admission now accepts supported physical/history
transitions while preserving provenance validation and exact retention references.

Packaged admission (`5768e96b`) implements the specification's
[finite admission contract](../specs/2026-09-09-deploy-reconciliation-design.md#packaged-admission-boundaries):
typed restore inspection/capacity, locked provision resource authority, and sensitive exit-only
credential proof in the verified helper package. All five scoped review findings were corrected and
independently verified; the increment is approved with no open findings.

## Next session

The operator requested this checkpoint before further Task 11 work. Continue in this order:

1. Add the three remaining acceptance scenarios: public packaged restore before first success with
   current present/absent and interruption/retry; public deploy and restore discovery with 4097+
   selections and an old retained reference; deployment consequences beyond 64 migration attempts.
2. Complete the acceptance-family review and the plan's final local gates, including identified
   clean/controlled-dirty builds, ignored-canary exclusion, cache identity, artifact budgets, isolated
   packages, extracted-release terminal execution, and native systemd diagnostic inspection.
3. Update the canonical runbook, superseded baseline design sections, reconciliation status,
   plan checklist, and indexes after the evidence supports them. Close `tas-sr4b.11` and `tas-6dkg`
   only after their remaining criteria pass; keep the readiness parent for external acceptance.

The detailed acceptance trace and non-authoritative runbook/design drafts remain in
`.superpowers/sdd/2026-09-14-deploy-reconciliation/`. Use them as working material, not as completed
acceptance or authoritative documentation. Beads owns the verification evidence and remaining gaps.

## Verification and constraints

- Fresh checkpoint gates against `5768e96b`: full operations suite 1434 passed (255.99 s);
  `mix precommit` 805 passed (42.6 s); compileall and shell syntax passed. Changed production files
  contain no planning identifiers; handoff links and whitespace pass.
- Native effects in the packaged tests are doubled. Real PostgreSQL/systemd/VPS acceptance remains
  unverified and separately authorized. Local build acceptance is also still pending Task 11.
- The old staging installation is outside the supported record/runtime boundary. Do not migrate,
  repair, or invoke the new controller against it. No host action, push, merge, deployment, or
  publication has occurred or is authorized.

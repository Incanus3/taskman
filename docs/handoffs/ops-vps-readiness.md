# Operations VPS readiness

Status: active. Updated: 2026-09-11. Resume: `$resume ops-vps-readiness`.

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

The current proposed contract includes:

- one desired-target `deploy` flow that can retry or replace an unfinished managed release without
  fabricating successful history or requiring lost local artifact bytes;
- exact artifact-byte identity, legacy record compatibility, live-schema validation, durable
  pre-migration backup protection, scheduler-helper compatibility, and truthful failure evidence;
- attempt retention keeps the original, newest, and three recent intermediates per unfinished
  sequence, replacing the 64-attempt refusal. Publish a fresh protection before confirmed pruning;
  independent history/restore references remain protected, and a transient sixth backup is allowed;
- restore may recover before first success using a validated backup and compatible installed
  source release, including absent `current`; verified restore then publishes the first success;
- restore's approved `--replace-unfinished` permits another backup after failed verification;
  durable replacement intent preserves the original database and safety copies, including possible
  new writes. Interrupted replacement can normalize safely before accepting a third target;
- `--yes` for unattended deploy and provision confirmation, plus independent `--allow-downgrade`
  for both commands, required for known downgrades or unknown ordering against an existing baseline;
  unfinished candidates can establish a baseline before first success, while no-baseline fresh
  installs remain exempt;
- environment-neutral dirty builds for build, deploy, and provision through `--allow-dirty`, using
  tracked changes and deletions plus non-ignored untracked files while excluding ignored files;
- clean IDs ending in the schema-defined 64-hex SHA-256 artifact digest and dirty IDs appending a
  visible terminal `-dirty`; no whole-worktree digest or redundant algorithm label;
- explicit dirty artifacts imply dirty-source acknowledgment, while redundant `--allow-dirty` is
  accepted for them. Before first successful selection, provision may retry or replace the desired
  artifact, including after original archive loss; it validates live schema and protects backups
  against a null successful-history baseline. Further migrations require explicit policy.

Administrator creation, authenticated workspace/admin access, and logout acceptance are complete
under `tas-q5lo`. They do not complete deployment history. The last recorded host state has the OTP
29 candidate physically selected and running while successful history still names the OTP 27
genesis release; no migrations changed during the failed transition. Refresh actual state before
any host operation and never manually edit `current` or selection history.

The current GitButler stack is `dedicated-host-deployment-automation` with PR 16. The ongoing
specification review is recorded on this stack. No merge is authorized.

## Next actions

1. Continue the one-at-a-time recovery review. Explicit restore from a failed deployment is now
   approved and specified, including typed data-loss confirmation and protected recovery references.
   Interrupted restore admission is also approved and specified: the same backup can resume a
   validated database swap using a durable restore-target binding; explicitly confirmed target
   replacement is also specified. Cleanup admission is now approved
   and specified independently of capacity/database readiness, while preserving recovery references.
   All three discussed recovery gaps are incorporated; continue full-spec review. Approval of the
   complete written specification remains pending.
   Provisioning marker admission has also been replaced with resource/configuration validation
   and explicit plan confirmation; legacy marker files are ignored and preserved.
2. After approval, use the writing-plans workflow to create and review a bounded implementation
   plan. Create scoped Beads delivery tasks, including independent verification.
3. Update this handoff at the approved-plan boundary and start implementation in a clean session by
   default.
4. Implement and verify locally. Then update the canonical deployment design, runbook, and indexes
   with implemented behavior.
5. Refresh staging state and continue the already-authorized runtime deployment and readiness checks
   through the public controller. Preserve the running candidate until reconciliation is ready.

## Constraints and remaining gates

The active human review gate blocks planning. Do not manually repoint releases, synthesize history,
retry the retired failed artifact, merge, or run destructive recovery. Invitation/email, API key,
broader LiveView, off-host backup, second-release rollback/restore, controlled-failure, reboot, and
leakage acceptance still require operator selection or their existing explicit gates. Keep
`tas-6dkg` coordinated with the CLI UX workstream while preserving its required failure-evidence
behavior here.

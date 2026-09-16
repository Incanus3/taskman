# Operations VPS readiness

Status: active. Updated: 2026-09-17. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Prepare the dedicated-host controller for merge through bounded production simplification,
proved test-overlap reduction, authorized history consolidation and clean real-VPS acceptance.
This handoff grants no host reset, provider/DNS change, deployment, destructive restore,
history rewrite, future push or merge authorization. The operator separately authorized this
checkpoint push and local GitButler state repair on 2026-09-17; that authorization does not
extend to later history consolidation or host actions.

- Canonical behavior: [reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md),
  [dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md),
  and [operator runbook](../deployment.md).
- Ordered candidates/dispositions: [register](../research/2026-09-16-operations-simplification-candidates.md).
- Optimization evidence and accepted coverage trade-offs:
  [measurements](../research/2026-09-16-operations-test-parallelism.md).
- Delivered candidate 3: [exact failure design/evidence](../specs/2026-09-16-exact-helper-failure-design.md#implementation-evidence)
  and [completed plan](../plans/2026-09-16-exact-helper-failure.md).
- Test-overlap mapping/proposal: [review](../research/2026-09-17-operations-test-overlap.md),
  `tas-sr4b.28` owns the completed mapping; exact three removals and build gates delivered in `tas-sr4b.29`.
- Parent `tas-sr4b` remains open; ordered review `tas-sr4b.16` is complete (candidates 1–7 delivered; candidate 8 skipped).
  Deliveries `tas-sr4b.22`–`tas-sr4b.27` are complete. Earlier optimization/audit/candidate
  checkpoints are recorded in the register and their issues; do not reopen fulfilled approval gates.

## Current implemented checkpoint

Current source/test delivery checkpoint: `cfabead4738e` on
`dedicated-host-deployment-automation`. Candidates 1–7 are committed and pushed. Host admission
tests now exercise production provisioning directly; the unused pristine validator/helper/export
and its callable-only test are removed. Every consequential assertion remains mapped in the register.
Unused configuration/secrets Python aliases are removed; supported YAML inputs, derived paths, canonical
rendering and secret protection remain. Provisioning now
owns one immutable rendered systemd plan after target/source stabilization; initial/refreshed
authority digests and declaration use the same bytes. Frozen-input semantics and separate genesis
scheduler refresh ownership are accepted. Existing drift refusals, permissions, checksums,
calendar/reload behavior and create-only scheduler policy remain. The register owns assertion
mapping and scoped review evidence.

Fresh baseline: 1,580 operations tests passed with 158 known warnings in 55.53 seconds;
`mix precommit` passed 805 tests in 43.5 seconds. Separate two-file coverage review confirmed
only the approved removals and unchanged retained tests. Clean/dirty builds from workspace
revision `974d7a126a8c4b7a9e9a3084de54b4024db5fcf0` passed archive/manifest/checksum,
pinned-builder/runtime and exact clean-input cache checks; the dirty snapshot captured the
README probe and excluded an ignored canary, then both inputs were restored. The linked
[overlap review](../research/2026-09-17-operations-test-overlap.md#pre-consolidation-release-evidence)
owns exact artifact evidence. No history rewrite or native/external acceptance occurred.

Package reuse and the approved three-selection history split are delivered. Real >4,096 observer
regressions remain; the accepted split loses direct combined large-history packaged-consumer
coverage. The rejected direct-write fixture and unselected import experiment are not approved.
Startup investigation is complete; import changes were deferred to candidate 8. Use the linked
current workload measurements/native-effect inventory, not historical large-fixture timings.
No end-to-end import saving or startup design approval is established. Preserve lost-genesis
replay and changed-target refusal outcomes.

Checkpoint push and local GitButler repair are complete. The pushed checkpoint `24b0d47f`
contained the intended deletion. Teardown exposed a stale index; all 506 tracked working-tree
files matched that pushed tree byte for byte, with no extra untracked files. Refreshing only the
index and reinitializing the managed workspace removed the phantom report without changing
source or history. GitButler verified no uncommitted changes and a branch tip matching origin.
Fresh checkpoint `mix precommit` passed 805 tests in 42.6 seconds. The clean-session resumption
is complete; candidate 8 was skipped by the operator. Later gates below remain pending.

## Immediate next action and agreed remaining sequence

We are at pre-merge step 5, **commit-grouping agreement before history consolidation**.
`tas-sr4b.29` is complete: exact three approved duplicate cases removed; local review/tests,
clean/dirty package and cache gates passed. Next propose durable groups from the current branch
inventory and obtain operator agreement. Every source/document change must remain accounted for;
no exclusion or history mutation is authorized. Then request explicit authorization for the exact
squash and any remote update, refresh relevant target/remote state and use GitButler.
Rebuild and repeat identity-sensitive local checks after consolidation; the named artifacts above
are pre-consolidation evidence. Parent `tas-sr4b` remains open. Step 6 and its full external gate
remain pending after that exact post-squash gate.

The complete agreed pre-merge order is:

1. **Profile operations coverage:** complete; evidence is in the linked measurements.
2. **Apply coverage-retaining optimizations:** complete (xdist, immutable package reuse,
   history split, measured startup disposition). This does not authorize general test removal.
3. **Review and simplify production:** complete. Candidates 1–7 are delivered; candidate 8
   selected-handler imports was skipped by the operator. Current dispatch/harness imports remain.
   Preserve operator behavior, destructive targets, backup/reference safety, migration compatibility,
   secret protection, truthful failure evidence and supported interruption recovery.
4. **Map overlap and remove only proved duplicates:** complete in `tas-sr4b.28`/`tas-sr4b.29`.
   Retain focused public-boundary coverage and one owner per consequential invariant, fresh children,
   real archive entrypoint, `python -I -S`, native-effect substitutions, readiness/timeout cleanup,
   fresh authority and every consumer assertion; run full
   local gates and clean/dirty build/package checks. Only the exact three mapped local removals are approved; no general deletion is authorized.
5. **Agree durable commit groups, then squash with GitButler.** History rewriting and any remote
   update require explicit authorization. Source revision is embedded in release identity: rebuild
   and repeat identity-sensitive local checks after squashing.
6. **Run clean real-VPS acceptance on the exact post-squash head.** Obtain the external gate below
   first. Source changes after acceptance require affected local and host checks to be repeated.

## External gate — authorization required

Obtain explicit authorization identifying the disposable target, permitted host/provider/DNS
changes, access path and destructive restore scope. Establish a clean supported staging host,
then follow the runbook in order:

1. Provision twice; inspect HTTPS, HSTS, listeners, firewall and reboot behavior.
2. Accept administrator/login, invitation email, API key and LiveView behavior.
3. Exercise second release, rollback, forward deploy, controlled migration failure, backup
   and destructive restore.
4. Inspect canary-secret and release-cookie leakage before recording external acceptance.

Native PostgreSQL, systemd PID 1, UFW, DNS/ACME, email delivery, reboot and complete destructive
restore remain unproven by local suites/fakes/archives. The historical staging installation is
outside the supported format/runtime boundary: do not migrate, repair or run the new controller
against it. Its dated DNS/email/admin/upgrade observations are historical only.

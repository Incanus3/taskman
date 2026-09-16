# Operations VPS readiness

Status: active. Updated: 2026-09-16. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Prepare the dedicated-host controller for merge through bounded production simplification,
proved test-overlap reduction, authorized history consolidation and clean real-VPS acceptance.
This handoff grants no host reset, provider/DNS change, deployment, destructive restore,
history rewrite, push or merge authorization.

- Canonical behavior: [reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md),
  [dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md),
  and [operator runbook](../deployment.md).
- Ordered candidates/dispositions: [register](../research/2026-09-16-operations-simplification-candidates.md).
- Optimization evidence and accepted coverage trade-offs:
  [measurements](../research/2026-09-16-operations-test-parallelism.md).
- Delivered candidate 3: [exact failure design/evidence](../specs/2026-09-16-exact-helper-failure-design.md#implementation-evidence)
  and [completed plan](../plans/2026-09-16-exact-helper-failure.md).
- Parent `tas-sr4b` remains open; ordered review `tas-sr4b.16` remains in progress.
  Delivery `tas-sr4b.22` is complete. Earlier optimization/audit/candidate checkpoints are
  recorded in the register and their issues; do not reopen their fulfilled approval gates.

## Current implemented checkpoint

Current source checkpoint: `6bd7e23d96e3869063e2a1c8884804990c7a1bf6` on
`dedicated-host-deployment-automation`. Candidates 1–3 are locally committed: migration extraction,
narrow SOPS runner, canonical service/inspection producers, exact fixtures, shared cleanup
completion authority and exact failure recovery/encoding. The translator is removed. Distinct
scoped reviews approved; four failure-policy findings were corrected with red/green regressions
and independently re-reviewed. The linked design owns proof/size details.

Fresh integrated baseline: 1,572 operations tests passed with 158 known fork/thread warnings in
58.77 seconds; `mix precommit` passed 805 tests in 42.2 seconds and changed no files.
Locked sync, compileall, shell syntax and scoped terminology checks passed. No native-host,
release-build, history consolidation or external acceptance occurred in this increment.

Package reuse and the approved three-selection history split are delivered. Real >4,096 observer
regressions remain; the accepted split loses direct combined large-history packaged-consumer
coverage. The rejected direct-write fixture and unselected import experiment are not approved.
Startup investigation is complete; import changes were deferred to candidate 8. Use the linked
current workload measurements/native-effect inventory, not historical large-fixture timings.
No end-to-end import saving or startup design approval is established. Preserve lost-genesis
replay and changed-target refusal outcomes.

## Immediate next action and agreed remaining sequence

We are at pre-merge step 3, **candidate 4 operator discussion**. Candidate 3's four-task plan is
complete; no implementation decision remains for it. The operator explicitly authorized its
execution in the approval session.

The complete agreed pre-merge order is:

1. **Profile operations coverage:** complete; evidence is in the linked measurements.
2. **Apply coverage-retaining optimizations:** complete (xdist, immutable package reuse,
   history split, measured startup disposition). This does not authorize general test removal.
3. **Review and simplify production:** the ranked audit/register and candidates 1–3 are complete.
   Review remaining candidates in order: **4** production-shaped provisioning evidence;
   **5** one owner of systemd asset bytes; **6** unused internal names; **7** unused pristine
   admission after mapping tests; **8** selected-handler imports. Obtain each explicit disposition
   and approved bounded design before implementation; keep unreviewed candidates discoverable.
   Preserve operator behavior, destructive targets, backup/reference safety, migration compatibility,
   secret protection, truthful failure evidence and supported interruption recovery. Verify and
   independently review each approved increment against the existing comprehensive suite.
   For candidate 8, preserve fresh children, real archive entrypoint, `python -I -S`, native-effect
   substitutions, readiness markers/timeout cleanup, fresh authority and every consumer assertion;
   measure focused public cases before/after without adding a dispatcher for unproved savings.
4. **After production structure is stable, map overlap and remove only proved duplicate tests.**
   Retain focused public-boundary coverage and one owner per consequential invariant; run full
   local gates and clean/dirty build/package checks. No general test deletion is approved yet.
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

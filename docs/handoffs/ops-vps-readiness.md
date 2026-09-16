# Operations VPS readiness

Status: active. Updated: 2026-09-16. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Prepare the dedicated-host controller for merge by simplifying the operations implementation within
its accepted reliability boundary, reducing redundant or disproportionately slow tests, consolidating
branch history, and completing clean staging provisioning/deployment acceptance. This handoff does
not authorize a host reset, provider/DNS change, deployment, history rewrite, push, or merge.

- Canonical behavior: [reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md),
  [dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md), and
  [operator runbook](../deployment.md).
- Parent tracking issue: `tas-sr4b`. Local final-verification tasks `tas-sr4b.11` and `tas-6dkg`
  are closed; the parent remains open for pre-merge hardening and external acceptance.
  Parallelism investigation `tas-sr4b.13` is closed; xdist adoption was accepted on 2026-09-16.
  Active task: `tas-sr4b.14` (three remaining candidates; package-reuse design accepted).

## Current checkpoint

Resume from the latest tip of `dedicated-host-deployment-automation`. The handoff checkpoint
commit includes the previously verified retention optimization, deterministic parametrization
correction, approved xdist adoption, measurements, and package-reuse approval. Its preceding
stack tip was `8f1918614123fcba78fd874d45c20d1c9d51cfb4`; locally verified production
implementation head is `934baff3c282627efb58fd77bde3325807147973`. No production behavior
changed in the optimization checkpoint. Refresh workspace state before continuing.

The recorded serial baseline is 1,462 passing tests in 219.61 seconds. Two workers passed in
114.77 and 113.27 seconds; four workers passed in 55.47 and 59.54 seconds. Final local checks
passed: locked uv sync, compileall, shell syntax, focused serial codec tests, Markdown inspection,
and `mix precommit` with 805 tests. Audit, reproduction commands, environmental limits, and exact results
are owned by [parallelism measurements](../research/2026-09-16-operations-test-parallelism.md)
and `tas-sr4b.13`. The operator approved the xdist dev dependency and explicit four-worker gate.
`ops/pyproject.toml`, its lock, and the development guide now contain that change. Locked
installation passed; the final uninstrumented suite passed 1,462 tests in 63.11 seconds with
158 fork/thread warnings. Under-60 timing is not consistently established. Pytest's default
remains serial.

## Immediate next increment

The operator approved the bounded package-reuse design on 2026-09-16 and requested a clean
session before implementation. Continue `tas-sr4b.14` with that implementation; do not ask again
for the same design approval. Read the complete [accepted design and evidence](../research/2026-09-16-operations-test-parallelism.md#immutable-integration-packages).

1. Add a focused explicit session fixture under `ops/tests/support/` that captures helper and
   scheduled-helper archive bytes/checksum/protocol/mode once per worker, then writes private
   copies for each integration test. Pass it through the two installers in
   `ops/tests/test_end_to_end.py` and their direct consumer fixtures/tests; import shared fixtures
   explicitly. Keep production builders and packaging/source-mutation tests uncached.
2. Verify private-copy identity/mode and actual isolated execution; run focused consumers, then
   all 1,462 operations tests with the documented four-worker gate. Compare repeated uninstrumented
   timing with the latest 63.11-second baseline, retain serial diagnosis, and run the remaining
   repository gates. Avoid concurrent application builds while measuring operations timing.
3. Record actual savings and the package-reuse disposition in the evidence document/task, then
   continue the remaining candidates below. Temporary probes are not implementation assets and
   are not required to resume.

Preserve every behavioral assertion, private mutable state, fresh authority checks, and actual
`python -I -S` packaged execution. The operator wants all remaining candidates investigated
despite already meeting the timing target; do not stop at the timing bar.

Remaining candidates, in investigation order:

1. **Immutable integration package reuse.** Measure suite-wide build count and cost, then consider
   sharing immutable archive bytes with private test copies in `_install_public_controller` and
   `_install_public_restore_controller`. Keep builder, source-mutation, allowlist, checksum,
   determinism, and packaging tests uncached. Prior large-history profile: three builds cost
   0.77 seconds, including 0.67 seconds of import validation. New suite measurement: 243 builds
   cost 38.14–40.23 aggregate worker-seconds; the two installers account for 71 builds / 14.75
   seconds. Explicit immutable session fixture/private copies is accepted; implementation is next.
2. **Isolated archive startup.** Separate child import, decompression, compilation, and real
   observation costs. Prior profile: 14 fresh helper calls cost 10.76 of 15.95 seconds. Preserve
   fresh authority and archive isolation; do not cache discovery or reuse mutable process state.
   New 14-child cProfile: 12.74 of 16.19 profiled seconds
   were real observation, source compilation 2.24 seconds, decompression 0.10 seconds. Keep open
   for a bounded import/compilation design after package reuse; no durable startup change yet.
3. **Large-history fixture construction.** Measure replacing repeated publication mechanics with
   controlled valid record construction. Prior 4,097-selection construction cost 2.35 seconds.
   New temporary direct-record probe passed the complete regression and reduced construction from
   1.03 to 0.39 seconds; no durable fixture change yet. Retain 4,097 real records, oldest-only
   backup protection, and every public success/refusal and bounded-projection assertion.
   Keep publication mechanics covered by their focused tests.

Retain the distinct lost-genesis replay/changed-target refusal outcomes and all large-history
consumer assertions. These candidates are under investigation, not completed or silently dropped.
Evidence and accepted decisions belong in the [measurement document](../research/2026-09-16-operations-test-parallelism.md)
and `tas-sr4b.14`; keep this list current until each candidate has an explicit disposition.

Production simplification follows this investigation under canonical operations-development
guidance. Host acceptance and history consolidation retain their separate authorization gates.

The old staging installation is outside the supported format/runtime boundary. Its dated DNS,
email, administrator, and failed-upgrade observations are historical evidence only. Do not migrate,
repair, or run the new controller against it.

## Pre-merge sequence

1. Profile the operations suite and map overlapping coverage without deleting tests. Use the
   measurements to identify expensive fixtures, repeated production seams, and duplicated state
   transitions.
2. Apply coverage-preserving speed improvements while the broad regression net remains intact.
   Reduce repeated builds, package assembly, subprocess work, filesystem setup, and other expensive
   fixtures; improve safe parallelism where measurements justify it. Do not remove behavioral
   assertions or state-transition coverage in this step.
3. Review and simplify operations code under the
   [Operations development](../development.md#operations-development) rules. Preserve current
   operator behavior, destructive-target controls, backup/reference safety, migration compatibility,
   secret protection, truthful failure evidence, and supported interruption recovery. Prefer safe
   refusal over machinery for unsupported theoretical combinations; verify each bounded change
   against the existing comprehensive suite.
4. With production structure stable, re-evaluate overlap and remove only proved duplicate coverage.
   Retain focused public-boundary tests and one clear owner for each consequential invariant. Then
   rerun the complete local gates and clean/dirty build/package checks.
5. Agree on a small set of durable commit groups and squash with GitButler. History rewriting and
   any required remote update need explicit operator authorization. Because release identity embeds
   the source revision, rebuild and repeat the identity-sensitive local gates after the squash.
6. Obtain the external authorization below and run clean staging provisioning/deployment acceptance
   against the exact post-squash head. Do not change production code after acceptance without
   rerunning the affected local and host gates.

## External gate — authorization required

Before acting, obtain explicit operator authorization that identifies the disposable target,
permitted host/provider/DNS changes, access path, and destructive restore scope. Then recreate a
clean supported staging host and follow the runbook's fresh provisioning/readiness acceptance:

1. provision twice and inspect HTTPS, HSTS, listeners, firewall, and reboot behavior;
2. perform administrator/login, invitation email, API key, and LiveView acceptance;
3. exercise a second release, rollback, forward deployment, controlled migration failure, backup,
   and destructive restore; and
4. inspect canary-secret and release-cookie leakage before recording external acceptance.

Native PostgreSQL, systemd PID 1, UFW, DNS/ACME, email delivery, reboot, and destructive restore
remain unproven by local fakes, packages, or builds. Preserve the explicit external boundary.

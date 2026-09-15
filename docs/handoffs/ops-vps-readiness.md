# Operations VPS readiness

Status: active. Updated: 2026-09-15. Resume: `$resume ops-vps-readiness`.

## Objective and authority

If the operator separately authorizes it, complete clean staging recreation, provisioning, and
real-host readiness acceptance for the locally verified dedicated-host controller. This handoff
does not authorize a host reset, provider/DNS change, deployment, publication, push, or merge.

- Canonical behavior: [reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md),
  [dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md), and
  [operator runbook](../deployment.md).
- Parent tracking issue: `tas-sr4b`. Local final-verification tasks `tas-sr4b.11` and `tas-6dkg`
  remain open for a load-bearing final-review finding; the parent remains open for external
  acceptance.

## Current checkpoint

The current reviewed head is `9f7647c5e80558277bfc572e7045b7d517d62a49` on
`dedicated-host-deployment-automation`. The single final-review fix wave resolved failed-verification
evidence, finalized helper-upload cleanup, and local/host archive-validation parity. Its scoped
re-review left one Important, load-bearing defect: production deploy/provision target resolution can
fail on clean-source drift before the bounded refresh loop, while interactive post-confirmation
source or host-authority drift can continue contrary to the runbook without consuming that bound.
Do not treat this branch as merge-ready until the operator decides how to handle that residual.

Latest local evidence: compileall and `pytest ops/tests` passed at the reviewed head, with 1,452 tests
in 323.91 seconds. The preceding final implementation checkpoint also passed locked dependency
synchronization, shell syntax, command-help surfaces, and `mix precommit` (805 tests). Clean and
controlled-dirty release builds passed there, including exact-input cache
reuse and ignored-canary exclusion. Both helper packages ran under `python3 -I -S`; the extracted
release terminal test and systemd diagnostic tests passed.

The old staging installation is outside the supported format/runtime boundary. Its dated DNS,
email, administrator, and failed-upgrade observations are historical evidence only. Do not migrate,
repair, or run the new controller against it.

## Next external gate — authorization required

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

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
  are closed; the parent remains open for external acceptance.

## Current checkpoint

The locally verified head is `934baff3c282627efb58fd77bde3325807147973` on
`dedicated-host-deployment-automation`. The final-review corrections preserve failed-verification
evidence, remove finalized helper uploads, align local/host archive validation, and reconcile
clean-source drift. Production deploy and provision now retry resolution-time drift through the
complete pre-confirmation cycle within one shared bound; source or material-host drift after
confirmation requires a new invocation. A distinct scoped reviewer approved the correction.

Latest local evidence: compileall and `pytest ops/tests` passed at the reviewed head, with 1,462 tests
in 344.70 seconds; `mix precommit` passed 805 tests. The preceding final implementation checkpoint
also passed locked dependency synchronization, shell syntax, and command-help surfaces. Clean and
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

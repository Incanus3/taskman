# Operations VPS readiness

Status: active. Updated: 2026-09-09. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Authorized staging provisioning and readiness now succeed. Continue only the next operator-chosen
acceptance increment. Active task: `tas-b7kd`; bounded diagnostic follow-up: `tas-6dkg`.
The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) owns architecture;
the [runbook](../deployment.md) owns commands, shell prerequisites, recovery, and acceptance gates.

Provisioning packages, PostgreSQL, systemd, Caddy/HTTPS, firewall, and demonstrated corrections
were authorized. The operator subsequently authorized committing and pushing all current changes
to the existing branch. Merge, destructive acceptance, and administrator creation remain gated.
Do not expand that scope implicitly.

## Completed checkpoint

- Target: `root@taskman.page:22`, Ubuntu 26.04.1 x86_64/systemd. The operator opened Hetzner
  TCP 443 and then TCP 80. Caddy obtained a valid certificate after validation and reload.
  External IPv4 HTTP redirects to HTTPS; `/healthz` returns exact `ready`, no-store, HSTS.
  Host-side HTTPS works over IPv4 and IPv6; independent external IPv6 remains unverified.
- Taskman is active on the replacement release with all ten migrations and a completed selection
  published by ordinary provisioning. Standalone `verify` passes all eight checks. Repeat
  provisioning succeeds with `release.changed=false`; its only reported pyinfra operation is
  the scheduled-backup checksum check. Aggregate `changed=true` is explained in the runbook.
- PostgreSQL 18/main is healthy on loopback. Native HBA is active, parser clean, with no recovery
  directory. Taskman, PostgreSQL, and Erlang distribution remain loopback-only; no EPMD listener.
- Current application source: `8266656863adf6f710ab05fc7e8024f7e4a2b126`. Release:
  `0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6`.
  SHA-256: `632f2a44ee1e265961e25e226f68751e00cfeabc3d10aa4236a82b80c49e3cf8`.
  Artifact directory:
  `/tmp/taskman-artifacts-1000/0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6-z69je_z6`.
  Archive basename: `taskman-0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6.tar.gz`.
- Reviewed controller corrections are committed locally through `730c002` on
  `dedicated-host-deployment-automation`. Clean controller: `/tmp/taskman-vps-controller.BahJ7r`.
  Earlier clean clones lack one or both verifier fixes. No new application build is needed.
- The failed `d2a013172234` release and uploaded archive remain recoverably quarantined under
  `/opt/taskman-retired-d2a013172234-20260909`. Do not retry that archive or repeat completed repairs.
- Resend sending domain and DNS are verified, sender `no-reply@notify.taskman.page`; actual email
  delivery is not yet tested. Secrets remain in the protected deployment workflow.

## Verification and remaining uncertainty

904 operations tests and `mix precommit` 805 tests passed, plus compileall and shell syntax.
Independent HTTP-parser review passed 53 verification/package tests. Its three failing regression
cases were demonstrated before the one-character correction. Independent scoped-listener review
also passed; exact live `ss` output now validates. Both fixes passed live read-only verification
before the successful ordinary provisioning retry. See `tas-b7kd` for detailed evidence.

Standalone listings/verification emit `unknown deployment entry: uploads` for the controller's
existing upload directory; the release inventory remains valid. Failed release verification loses
its existing bounded check report, forcing separate diagnostics; `tas-6dkg` tracks that correction.
Neither observation authorizes cleanup or broader refactoring.

The [PostgreSQL Python proposal](../specs/2026-09-09-postgresql-host-python-design.md) remains
parked by operator priority. Written-spec acceptance and an implementation plan remain pending;
no refactor code exists. Preserve the native-HBA safety decisions if that work resumes.

## Next actions

1. Ask which acceptance increment the operator wants next. Initial administrator creation is
   interactive; do not request or capture its password in chat. Follow the runbook's command.
2. Then, with authorization, verify sign-in, invitation delivery, API key, and connected LiveView.
3. Backup/off-host-copy, second-release/rollback/restore, controlled failures, reboot, and leakage
   acceptance remain unperformed; destructive or external effects need their explicit gates.
4. Keep `tas-6dkg` bounded and separate from the parked PostgreSQL refactor. Do not resume a broad
   correctness search or merge without operator direction.

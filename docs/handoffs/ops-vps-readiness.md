# Operations VPS readiness

Status: active. Updated: 2026-09-09. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Authorized staging provisioning and readiness now succeed. This workstream owns the remaining
operator-selected host acceptance. Readiness task: `tas-b7kd`. The operator has now selected
initial administrator creation and browser login, admin-access, and logout acceptance.
The separate [CLI UX workstream](operations-cli-ux.md) remains unfinished.
The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) owns architecture;
the [runbook](../deployment.md) owns commands, shell prerequisites, recovery, and acceptance gates.

Administrator creation and login testing are authorized, but the attempted prompt failed before
account creation. The non-CI runtime correction is now implemented and verified locally.
Deploying that correction still requires separate authorization; no new push, merge, or destructive
acceptance is authorized by the local implementation approval.

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
- Runtime correction checkpoint: `42d019920b7540509ac8fde944d4b979f7178e92` on
  `dedicated-host-deployment-automation`, not pushed. Clean verified controller:
  `/tmp/taskman-runtime-build.DxZfr9`. Older controller clones do not understand the new runtime.
- Verified candidate: `0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6`, Elixir 1.20.4 / OTP 29.0.6.
  SHA-256: `9d7e444f37622cf3e9f96d8891082b882999d84ed10457d7539ae9b4018d7225`.
  Artifact directory:
  `/tmp/taskman-artifacts-1000/0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6-5xtku563`.
  Archive basename: `taskman-0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6.tar.gz`.
  This candidate is not deployed; the original OTP 27 artifact above remains authoritative on staging.
- The failed `d2a013172234` release and uploaded archive remain recoverably quarantined under
  `/opt/taskman-retired-d2a013172234-20260909`. Do not retry that archive or repeat completed repairs.
- Resend sending domain and DNS are verified, sender `no-reply@notify.taskman.page`; actual email
  delivery is not yet tested. Secrets remain in the protected deployment workflow.

## Verification and remaining uncertainty

On the exact new runtime: 915 operations tests and `mix precommit` 805 tests passed, plus
compileall, shell syntax, locked dependency sync, and whitespace checks. Independent scoped
runtime review passed 224 tests with no remaining findings. Clean-source build, isolated release
startup, archive/manifest/checksum validation, and exact-input cache reuse passed. The new controller
also validates the original deployed artifact without changing its identity or checksum.

The packaged release passed two real-PTY secrecy/restoration tests, including termination. A native
local `systemd-run --pipe` check accepted synthetic password/confirmation without echo and restored
visible input. This does not establish native SSH/service-user acceptance on staging. The historical
OTP 27 release demonstrably fails before confirmation. See `tas-q5lo` for evidence and the disclosed
workstation Rebar3-cache replacement incident; prior cached bytes were unknown and not guessed.

Standalone listings/verification emit `unknown deployment entry: uploads` for the controller's
existing upload directory; the release inventory remains valid. Failed release verification loses
its existing bounded check report, forcing separate diagnostics; `tas-6dkg` tracks that correction.
Neither observation authorizes cleanup or broader refactoring.

The [PostgreSQL Python workstream](postgresql-host-python.md) remains separately parked. Its design
and planning state do not belong to host acceptance and are not prerequisites for it.

## Next actions

1. Obtain authorization for the staged runtime deployment. Refresh host state first, then follow
   the [first-install transition](../deployment.md#build-an-artifact): with the new controller,
   provision the **exact original OTP 27 artifact** to refresh the persistent scheduled-backup
   executable; then deploy the verified OTP 29 candidate. Confirm the host still has eligible
   genesis history; do not generalize this replay to later release histories or omit `--artifact`.
   Keep Alpine CI unchanged; its upstream restriction is separate from this Ubuntu runtime fix.
2. After deployment, run readiness verification and have the operator retry private interactive
   administrator creation. Keep `tas-q5lo` open until the real-host prompt succeeds. Never request
   or capture its password. Verify browser sign-in, administrator access, and logout. Invitation
   delivery, API key, and broader LiveView acceptance remain separately gated.
3. Backup/off-host-copy, second-release/rollback/restore, controlled failures, reboot, and leakage
   acceptance remain unperformed; destructive or external effects need their explicit gates.
4. Do not resume a broad correctness search or merge without operator direction. Failed-result
   diagnostics (`tas-6dkg`) are coordinated by the separate CLI UX workstream.

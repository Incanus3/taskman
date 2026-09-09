# Operations VPS readiness

Status: active. Updated: 2026-09-09. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Authorized staging provisioning and readiness now succeed. This workstream owns the remaining
operator-selected host acceptance. Readiness task: `tas-b7kd`. Initial administrator creation,
browser login, administrator access, and logout acceptance are complete (`tas-q5lo` closed).
The separate [CLI UX workstream](operations-cli-ux.md) remains unfinished.
The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) owns architecture;
the [runbook](../deployment.md) owns commands, shell prerequisites, recovery, and acceptance gates.

The original administrator prompt failure was resolved by the non-CI runtime correction, now
verified locally and through successful real-host administrator creation and browser acceptance.
The operator authorized backup-helper refresh, runtime deployment, and readiness checks.
Helper refresh succeeded; deployment selected and started the candidate but failed verification
before publishing its completed selection. `tas-sr4b` now owns an architectural recovery design:
one desired-target `deploy` command that reuses matching work or replaces an unfinished release
under ordinary plan confirmation, without separate resume/redeploy commands or a generic force flag.
Local artifact loss must not strand deployment. The
[reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md) is written and
awaits operator review, including exact record formats and scheduled-helper compatibility handling.
The design sections, including deploy-only `--yes` and independent interactive/flag downgrade
acknowledgment, are approved. No general replay journal or automatic database restore is proposed.
The complete specification and implementation plan are not yet approved.
Administrator/login acceptance completed independently after fresh actual release, database, and
readiness checks. It does not complete deployment or resolve `tas-sr4b`.
The operator authorized committing and pushing the current branch before administrator/login
acceptance. No merge, manual history rewrite, or destructive acceptance is authorized.

## Completed checkpoint

- On 2026-09-09, the operator privately created the administrator and signed in. Browser inspection
  confirmed the authenticated workspace and `/admin`, with the account active and administrative.
  Normal sign-out succeeded; fresh `/admin` and `/` navigation required sign-in again. No password
  or token was captured. The browser is left signed out. See `tas-q5lo` for scoped acceptance evidence.
- Target: `root@taskman.page:22`, Ubuntu 26.04.1 x86_64/systemd. The operator opened Hetzner
  TCP 443 and then TCP 80. Caddy obtained a valid certificate after validation and reload.
  External IPv4 HTTP redirects to HTTPS; `/healthz` returns exact `ready`, no-store, HSTS.
  Host-side HTTPS works over IPv4 and IPv6; independent external IPv6 remains unverified.
- Before the runtime deployment, the OTP 27 baseline had all ten migrations, one completed genesis
  selection, and all eight readiness checks passing. Original-artifact provisioning refreshed the
  helper with `release.changed=false`. Current partial-deployment state is described below.
- PostgreSQL 18/main is healthy on loopback. Native HBA is active, parser clean, with no recovery
  directory. Taskman, PostgreSQL, and Erlang distribution remain loopback-only; no EPMD listener.
- Previous completed application source: `8266656863adf6f710ab05fc7e8024f7e4a2b126`. Release:
  `0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6`.
  SHA-256: `632f2a44ee1e265961e25e226f68751e00cfeabc3d10aa4236a82b80c49e3cf8`.
  Artifact directory:
  `/tmp/taskman-artifacts-1000/0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6-z69je_z6`.
  Archive basename: `taskman-0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6.tar.gz`.
- Runtime correction checkpoint: `42d019920b7540509ac8fde944d4b979f7178e92` on
  `dedicated-host-deployment-automation`. Clean verified controller:
  `/tmp/taskman-runtime-build.DxZfr9`. Older controller clones do not understand the new runtime.
- Verified candidate: `0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6`, Elixir 1.20.4 / OTP 29.0.6.
  SHA-256: `9d7e444f37622cf3e9f96d8891082b882999d84ed10457d7539ae9b4018d7225`.
  Artifact directory:
  `/tmp/taskman-artifacts-1000/0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6-5xtku563`.
  Archive basename: `taskman-0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6.tar.gz`.
  This candidate is physically selected and running (observed MainPID 216429), but deployment is
  incomplete: only the original OTP 27 genesis selection record exists. Do not equate the running
  candidate with a completed deployment or manually append the missing record.
- The scheduled-backup helper refresh succeeded using the original artifact, without changing the
  selected release at that step. Installed SHA-256:
  `056bdc6dba387e1b814210a5970dcc3d4d30f29c145ff4b2dd7f7ace84e1b586`.
  It contains both runtime identities. Do not repeat first-install provisioning in this partial state.
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

The authorized deployment returned status 9 at verification, with stale `changed=false` and the
previous selected ID. Direct inspection instead found the new current symlink and active new VM.
Read-only execution of the same individual verification predicates over SSH passed service,
executable identity, listener topology, journal, local/public readiness, and HSTS. The original
failed check is unknown; a startup race is not established. Standalone `verify` and exact-candidate
`deploy --dry-run` now return status 10 because strict discovery refuses the unfinished transition.
No migrations changed; backup inventory was empty and no deployment backup was required or created.

The [PostgreSQL Python workstream](postgresql-host-python.md) remains separately parked. Its design
and planning state do not belong to host acceptance and are not prerequisites for it.

## Next actions

1. Obtain operator review of the `tas-sr4b` reconciliation specification, then write and approve its
   implementation plan before the default clean-session boundary. The earlier same-artifact-only fix
   is superseded by desired-target reconciliation. Preserve failed verification details (`tas-6dkg`).
   Preserve the running candidate; do not manually edit `current` or selection history. Refresh host
   state before any resumed operation; runtime deployment itself is already authorized.
2. After reconciliation implementation and verification, update the canonical dedicated-host design
   and runbook with the implemented rules, mark superseded guidance explicitly, and refresh indexes.
   The existing dedicated-host design is the finalized baseline, not unfinished implementation.
   Complete deployment history and standalone readiness verification independently of login acceptance.
3. Invitation/email, API key, broader LiveView, backup/off-host-copy, second-release/rollback/restore,
   controlled failures, reboot, and leakage
   acceptance remain unperformed; destructive or external effects need their explicit gates.
4. Do not resume a broad correctness search or merge without operator direction. Failed-result
   diagnostics (`tas-6dkg`) are coordinated by the separate CLI UX workstream.

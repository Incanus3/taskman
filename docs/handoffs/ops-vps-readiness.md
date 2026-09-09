# Operations VPS readiness

Status: active. Updated: 2026-09-09. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Complete authorized disposable-VPS provisioning and readiness checks. Task: `tas-b7kd`.
The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) owns architecture;
the [runbook](../deployment.md) owns operation, recovery, and external acceptance gates.

Operator authorized provisioning packages, PostgreSQL, systemd, Caddy/HTTPS, firewall, and
fixing demonstrated errors while continuing. Do not ask again for that scope. No merge/push
or destructive acceptance authorization. Operator approved committing all current changes,
including the staging configuration and encrypted secrets; no push was authorized.

## Current host and artifact

- Target: SSH alias `deploy`, `root@taskman.page:22`, Ubuntu 26.04.1 x86_64/systemd.
  SSH remains reachable. Baseline account/directories/marker, active UFW/Caddy/PostgreSQL,
  enabled Taskman/backup units, Taskman database/role, and protected environment/pgpass exist.
- PostgreSQL 18/main is healthy on `127.0.0.1:5432`. Native
  `/etc/postgresql/18/main/pg_hba.conf` is active, `postgres:postgres:640`, parser clean.
  No HBA recovery directory remains. Operator chose native-path parser validation with the
  accepted brief on-disk crash window; design/runbook own that contract.
- Taskman is inactive. No current link, selections, or migration table exists. One immutable
  candidate is installed but has never started successfully. Its group-only access repair
  was independently reviewed and applied after exact archive/tree verification; it is now
  accessible to `taskman`. Do not repeat the completed marker or release-group repairs.
- PR #16 remains open at remote source `d2a0131722341f9427ba71f9d8fb59e7bb806223`; its CI passed.
  This checkpoint accompanies the local correction commit on
  `dedicated-host-deployment-automation`. No push or merge; remote CI does not verify these fixes.
- Failed candidate: `0.2.0-d2a013172234-ubuntu26.04-amd64-otp27.3.4.6`.
  Archive directory:
  `/tmp/taskman-artifacts-1000/0.2.0-d2a013172234-ubuntu26.04-amd64-otp27.3.4.6-4b1lhdvt`.
  Archive: `taskman-0.2.0-d2a013172234-ubuntu26.04-amd64-otp27.3.4.6.tar.gz`.
  SHA-256: `36e8a916cbd7399d9fd142ded951cf2eba14cd5b2867bb144994350db42daba1`.
  Built from clean clone `/tmp/taskman-vps-build.DPHKD5`.
  This archive is now known to fail runtime-config compilation; do not retry or edit it.
- Staging YAML/SOPS secrets validate. Resend `notify.taskman.page` and its DNS records are
  verified; actual email delivery remains untested. Sender: `no-reply@notify.taskman.page`.
  Current controller shell requires `SSH_AUTH_SOCK=/run/user/1000/ssh-agent.socket` and
  `SOPS_AGE_KEY_FILE=/home/jakub/.config/sops/age/taskman.txt`; runbook explains both.

## Current blocker and verification

The migration VM reached runtime configuration, then Elixir 1.18.3 rejected the four dev-only
`~r` literals with `E` in `config/runtime.exs`. Macro expansion occurs even in production.
`E` means export (added in 1.19.3), not an end anchor. Local Elixir 1.20.2 tests missed this.

The runtime regex-compilation correction is implemented and independently approved. It preserves
matching behavior and export where supported, without upgrading the toolchain. Both main and
reviewer reproduced the original failure and successful dev/production configuration loads on
the exact Elixir 1.18.3/OTP27 VM. Local newer-runtime behavior is also verified.

The independently approved build gate executes bounded, network-isolated release `eval` with
synthetic inputs before packaging. It does not start Taskman or exercise database/email access.
`RELEASE_TMP` is outside the release tree to avoid packaging temporary runtime configuration.
The full replacement build has not yet run.

A fresh artifact needs a new clean source revision. Operator approved committing all current
changes locally, without push. The old exact unselected release must subsequently be retired
recoverably outside managed discovery before attempting a different first release; that
retirement has not occurred.

Operator also asked whether extensive embedded shell in PostgreSQL provisioning is necessary.
Assessment: native commands and one guarded host-local operation are justified; the large shell
workflow is not inherently required. A focused host-side Python implementation is a candidate
for reducing quoting/parsing/state-management complexity while preserving existing safety
semantics. Operator approved shell-to-Python refactoring where it makes sense. Detailed
scope and invocation design still need to be settled before implementation; no refactor
has been implemented. Prioritize the large PostgreSQL configuration workflow, not blanket
replacement of every short shell command.

Earlier demonstrated controller fixes are reviewed, including native HBA, protected receipts,
partial-change reporting, timeout parity, release ownership, and staged-unmigrated first-release
replay. Latest integrated baseline: 886 operations tests and precommit 805 tests; independent
runtime config 8 tests and build gate 16 tests. Focused build checks were rerun after the final
temporary-directory correction. Compileall, shell syntax, and whitespace passed. Detailed
evidence belongs to `tas-b7kd`.

## Next actions

1. Scope the approved PostgreSQL shell-to-Python refactor separately from this verified
   corrections checkpoint, preserving native tooling and all failure/safety semantics.
2. Complete the agreed refactor and build a fresh exact application artifact from clean source.
3. Review/perform exact recoverable retirement of the failed unselected release and provision
   the replacement. No provisioning command is currently running.
4. Verify the running host and repeat provisioning after success. Keep administrator creation,
   email delivery, destructive acceptance, publication, and merging behind their explicit gates.

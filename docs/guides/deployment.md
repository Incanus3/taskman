# Operating Taskman on a dedicated host

This is the canonical runbook for one Taskman installation on a dedicated Ubuntu host. The
repository-owned controller under `ops/` is the primary path for building, provisioning,
deploying, inspecting, backing up, rolling back, restoring, and cleaning up the installation.
Manual recovery remains documented below for use when the controller is unavailable.

The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) defines the architecture,
authority boundaries, and rationale behind these procedures.

Only the [supported artifact and record baseline](../specs/2026-09-18-operations-contracts.md#one-time-compatibility-boundary)
is accepted. Do not convert, adopt or delete unsupported authority to make a command pass.
Dated acceptance findings belong to the
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md); environment observations and obligations belong
to the [environment inventory](../inventories/operations-environments.md). Neither is proof of current host state
or authorization for reset, provider changes or deletion.

The supported topology is deliberately narrow. The paths below are the defaults; alternate
absolute roots are supported only when they pass the configuration topology checks described
below:

- Ubuntu 26.04 LTS on `amd64`/`x86_64`;
- one host reached over strict, pinned-host-key SSH by an administrator with `sudo`;
- an immutable OTP release under `/opt/taskman/releases/<release-id>`;
- `/opt/taskman/current` as the atomic release selection;
- `taskman.service` running as the unprivileged `taskman` account;
- Phoenix on `127.0.0.1:4000`, Caddy on public ports 80/443, and PostgreSQL on loopback; and
- fixed, loopback-only Erlang distribution on `127.0.0.1:6789`, without ordinary EPMD.

Taskman is an authenticated shared workspace. An account grants access to the shared Projects,
Lists, and Tasks; it does not create record-level ownership or permissions.

## Responsibilities outside the controller

The controller does not create or delete a VPS, publish DNS, manage a provider firewall, create or
configure Resend, rotate production credentials, or copy backups off the host. Before provisioning,
the operator must:

1. create a disposable or production host through the chosen provider;
2. obtain its SSH host-key fingerprint through a trusted, out-of-band channel;
3. configure the provider firewall so the chosen SSH port remains reachable and public TCP 80/443
   can reach the host;
4. publish direct `A` and optional `AAAA` records for the Taskman hostname;
5. verify a Resend sending domain and create a scoped API key; and
6. arrange encrypted, access-controlled, tested off-host backup copies.

Local dumps under `/var/backups/taskman` protect against deployment mistakes. They are not disaster
recovery because they are lost with the VPS.

## Workstation and host prerequisites

Run the controller from a reviewed, clean Taskman checkout. The workstation needs:

- a POSIX shell, Git, Python 3.12 or newer, and
  [`uv`](https://docs.astral.sh/uv/);
- Docker with BuildKit/buildx and `linux/amd64` build support;
- OpenSSH tools (`ssh`, `ssh-keyscan`, and `ssh-keygen`);
- [SOPS](https://github.com/getsops/sops) and
  [age](https://github.com/FiloSottile/age); and
- access to the external age identity that decrypts the selected environment.

The launcher always uses the checked-in lock:

```sh
./ops/taskman --help
./ops/taskman build --help
```

### Operator shell environment

Set the external age identity and the actual SSH agent socket for your workstation:

```sh
export SOPS_AGE_KEY_FILE=/secure/operator-owned/taskman.agekey
export SSH_AUTH_SOCK=/path/to/your/ssh-agent.socket
ssh-add -l
```

Exports apply only to that shell and its child commands. `build` needs neither variable;
read-only host commands need SSH authentication but do not necessarily decrypt secrets.
The controller uses environment YAML and pinned host-key authority with
`ssh_config_file=/dev/null`; it does not inherit an SSH alias's `IdentityAgent` setting.
If alias-based SSH works but the controller reports `strict SSH connection setup failed`,
inspect `ssh -G YOUR_HOST_ALIAS` and set `SSH_AUTH_SOCK` to the intended agent. Both variables
contain paths, never credential bytes. Keep the private age identity outside the repository.

The host must boot Ubuntu 26.04 LTS `amd64` with systemd as PID 1, Ubuntu's
`python3-minimal`, and an administrator able to perform the required sudo operations.
Provisioning refuses unsupported platforms, ambiguous existing resources, mismatched host keys,
indirect public DNS and conflicting listeners. The host receives an OTP release, managed assets
and runtime prerequisites; it does not need source, Mix, Node, Docker or controller dependencies.
See [architecture and ownership](../specs/2026-09-09-dedicated-host-deployment-design.md#architecture-and-ownership).

## Configure SOPS and age

`.sops.yaml` contains public age recipients and the selection rule for
`ops/environments/*.secrets.sops.yaml`. Replace the example recipient with the intended public
recipient. Keep every private age identity outside the repository and outside release artifacts.
Point SOPS at that protected identity through its normal external configuration, for example:

```sh
export SOPS_AGE_KEY_FILE=/secure/operator-owned/taskman.agekey
sops ops/environments/production.secrets.sops.yaml
```

Create or edit the encrypted document with exactly these keys:

```yaml
database_password: encrypted-value
secret_key_base: encrypted-value
ash_authentication_token_signing_secret: encrypted-value
resend_api_key: encrypted-value
```

`secret_key_base` and `ash_authentication_token_signing_secret` must be distinct and each at least
64 bytes. Do not put passwords, age identities, release cookies, decrypted YAML, or API keys in
shell arguments, environment files committed to Git, tickets, transcripts, or controller output.
Changing an age recipient rewraps the document; it does not rotate Taskman credentials.

SOPS decrypts into controller memory. The controller renders `/etc/taskman/taskman.env` through a
private transfer and installs it as `root:root` mode `0600`; it does not create a persistent
plaintext workstation file.

## Create the environment

Copy the complete non-secret example and replace every documentation value:

```sh
cp ops/environments/example.yaml ops/environments/production.yaml
```

Review at least:

- SSH host, port, administrator, and pinned SHA-256 host-key fingerprint;
- public hostname and exact public IPv4/optional IPv6 addresses;
- `ubuntu26.04` and `amd64`;
- Phoenix, distribution, and PostgreSQL loopback ports;
- PostgreSQL role and database identifiers;
- `MAIL_FROM` at the verified Resend domain;
- installation and backup roots (release, deployment, and current paths are derived);
- backup schedule and backup/release retention (each an integer from 1 to 64);
- readiness and connection timeouts; and
- an explicitly selected PostgreSQL package track only when required.

The configuration is validated before mutation. A real environment file is non-secret but still
environment-sensitive; add it to version control only as a deliberate operator decision. The only
configurable filesystem roots are:

```yaml
install_root: /opt/taskman
backup_root: /var/backups/taskman
```

Release, deployment, upload, lock and `current` paths derive from `install_root`.
Do not configure subordinate roots or legacy aliases. Both roots must be normalized absolute paths,
disjoint from each other and reserved managed locations. Installation roots exclude whitespace,
backslash, `%`, quotes, ASCII controls and DEL; backup roots may contain quotes.
Invalid configuration returns status 2 before SSH. See
[path authority](../specs/2026-09-09-dedicated-host-deployment-design.md#configuration-and-path-authority).

## Helper cleanup and scheduled backups

Host commands use a checksum-verified, short-lived standard-library helper; no resident agent
or background recovery process remains. Read-only commands and dry runs still need temporary
transport infrastructure. Provisioning separately installs the root-owned scheduled-backup
executable and systemd timer. See
[helper packaging](../specs/2026-09-09-dedicated-host-deployment-design.md#helper-packaging-and-protocol).

A `transient helper cleanup was incomplete` warning does not undo the command's primary result
or authorize recursive deletion under `/tmp/taskman-ops` or `/run/taskman-ops`. Before manual
cleanup, prove an exact entry is unused and verify its owner, mode and type.

## Preview before changing a host

Every mutating command accepts `--dry-run`:

```sh
./ops/taskman provision production --dry-run
./ops/taskman deploy production --artifact /secure/artifacts/taskman-RELEASE.tar.gz --dry-run
./ops/taskman backup production --dry-run
./ops/taskman rollback production RELEASE_ID --dry-run
./ops/taskman restore production BACKUP_ID --dry-run
./ops/taskman create-admin production --dry-run
./ops/taskman cleanup production --dry-run
```

A dry run performs the validation and observation required by that command, without managed
application, service or database mutation or confirmation. Deploy/provision resolve a target and
report migration and acknowledgment requirements. Restore validates the backup and recovery
arrangement, and checks capacity only when the planned action needs it. Completed restore cleanup
can be inspected without application readiness or backup capacity. Cleanup uses filesystem and
record authority and does not require database health, capacity or completed deployment state.
Temporary helper transport infrastructure is still required by read-only commands and dry runs.

`build` is local and may create its artifact even with `--dry-run`. `verify`, `releases`, and
`backups` are read-only; adding `--dry-run` does not weaken or change those commands.

Use `--json` when a versioned, secret-free report is needed:

```sh
./ops/taskman verify production --json
./ops/taskman releases production --json
./ops/taskman backups production --json
```

## Build an artifact

Build the current clean, identified revision:

```sh
./ops/taskman build
```

The pinned Ubuntu 26.04 `linux/amd64` builder produces an archive, adjacent manifest and
detached SHA-256 file identifying source, migrations, platform, toolchain and exact bytes.
[Build architecture](../specs/2026-09-09-dedicated-host-deployment-design.md#build-and-artifact-identity)
and `ops/builder/Containerfile` own exact pins; do not substitute unpinned toolchain downloads.
A bounded, isolated runtime-configuration check uses synthetic values without starting Taskman;
build success does not establish database, email or real-host readiness.
Treat the archive as a credential because it contains the Erlang distribution cookie.
Only the supported runtime/formats are accepted; old OTP 27 artifacts and digestless IDs
are not an upgrade path.

Release IDs include the full archive SHA-256 after the source/target/runtime fields. A dirty
snapshot adds a terminal `-dirty` marker. Rebuilding the same revision may produce different bytes
and a different ID; an installed release is never overwritten in place.

Without an explicit artifact, deploy and provision identify clean source inputs before host
observation. They prefer matching physical installed content, then matching last-successful
content, another exact-input installed release, verified local cache, and finally a new build.
All installed choices require complete validated provenance. A missing local archive therefore
does not strand a retry of an already installed exact target. Invalid cache entries are ignored
and preserved. Results identify whether the target was installed, cached, built or explicit.

For deliberate local changes, use `build --allow-dirty`, `deploy --allow-dirty`, or
`provision --allow-dirty`. The controller freezes tracked changes/deletions and nonignored
untracked files privately, excludes ignored files/metadata/controller state, and refuses unsafe
members or changes during capture. Dirty automatic resolution builds before identity is known.
Explicit dirty artifacts acknowledge their provenance; `--allow-dirty` with an explicit clean
artifact is invalid.

Automatic clean source drift before confirmation repeats identification, discovery and planning,
including under `--yes`; repeated instability refuses. After confirmation, source or material
host-authority drift requires a new invocation. Frozen dirty bytes remain the exact planned target.
See [target resolution](../specs/2026-09-18-operations-contracts.md#target-resolution-and-immutable-identity).

To retry exact bytes after a failure, pass the archive reported by that attempt:

```sh
./ops/taskman deploy production --artifact /secure/artifacts/taskman-RELEASE.tar.gz
```

The adjacent manifest and checksum must be present and valid. A matching logical release ID with
different bytes is a safety refusal; installed release directories are never edited in place.

## Provision a clean host

After reviewing a dry run:

```sh
./ops/taskman provision production
```

Provisioning presents a redacted plan and requires ordinary confirmation; `--yes` supplies
that confirmation only. Downgrade/unknown ordering needs separate acknowledgment, and pending
migrations need an explicit compatibility policy as described under deployment below.
Provisioning converges packages, unattended security updates without automatic reboot, managed
accounts/directories/configuration/units, Caddy and scheduled backups. UFW activation protects
the active SSH path; Caddy validates configuration; PostgreSQL validates cluster/HBA authority.
Secret installation stays outside logged pyinfra commands. See
[provisioning admission](../specs/2026-09-09-dedicated-host-deployment-design.md#provisioning-and-admission).

PostgreSQL keeps its native `pg_hba.conf` path. Before reload/restart it validates installed
candidate contents and restores prior bytes/metadata on parser failure. An interrupted attempt
requires inspection: the brief installation-to-validation crash/power-loss window is accepted.
Recovery is beside the native file in `pg_hba.conf.taskman-backup` (`pg_hba.conf` plus `metadata`);
a leftover directory blocks another attempt until manually reconciled. Later restart/verification
failure retains the copy, rather than reverting configuration under a running process.
Do not restart PostgreSQL blindly or relocate HBA; see
[HBA rationale and validation boundary](../specs/2026-09-09-dedicated-host-deployment-design.md#provisioning-and-admission).

The transient helper reconciles the desired first release from validated resources, records and
live migrations. Before the first durable successful selection, a rerun may retry or replace the
target while preserving compatible partial state. Missing managed resources can be created after
confirmation; conflicting present resources refuse. Resource inspection and confirmed convergence
do not adopt an unknown installation or invent provenance for applied migrations.

Fresh database creation requires both the configured PostgreSQL role and database to be absent.
A retained role with a missing database refuses before mutation; inspect that partial authority
deliberately before retrying. When both identities already exist, preflight requires the exact
least-authority role, its owned database, compatible protected credentials, and successful
application database authentication.

The first successful selection is the command boundary. Once it exists, release replacement uses
`deploy`, even if the controller lost the successful response. Replaying the exact completed first
installation remains available only under its existing first-install constraints. Compatible
scheduler code is refreshed under the lifecycle lock before publishing dependent recovery state.
Provisioning resource/database/runtime convergence currently runs outside that lock; interactive
`create-admin` also has no lifecycle admission. Helper procedures release lifecycle during scheduler
waits, so even manual helper commands do not have whole-command exclusivity. Status 12 means a
particular lifecycle acquisition was unavailable, not that every conflicting invocation is rejected.
The lock is installation-root-specific, not one fixed host-wide guard.

Exclusive provisioning maintenance must currently be coordinated by the operator. Do not assume
stopping the backup timer and draining a backup protects the full command: provisioning can create
or restart the timer before it finishes, and it does not guarantee continued suspension after
failure or caller loss. Current tooling cannot enforce the desired exclusive interval. Avoid
concurrent provisioning/admin/manual mutation and scheduled backup activity; inspect actual timer
and job state throughout maintenance rather than relying on command-start observations.
After an uncertain interruption, inspect outstanding remote commands and systemd migration/admin/
backup work and establish quiescence before retrying or restarting backups; a free lifecycle lock
or exited controller does not prove remote work has stopped. No durable provisioning reservation,
host-wide whole-command conflict refusal or reservation-based recovery command exists yet. The
[admission and recovery design](../specs/2026-09-18-provisioning-lock-coverage-proposal.md) is future
work in its [dedicated post-merge workstream](../handoffs/operations-lock-coverage.md).

Installed releases are `root:taskman`: directories/executables are `0750`, regular data is
`0640`, and the completed manifest is `0600`. The service account can read and execute application
files but cannot modify the release. Do not fix an execution-permission failure by granting world access or adding
`taskman` to the root group; inspect the exact release metadata and preserve immutable contents.

Repeat provisioning with the same exact artifact/source inputs after success to check convergence.
Read release-level facts separately from aggregate `changed`: a procedural checksum check may be
counted as changed without a new release selection. Do not assume the current checkout resolves
to the originally installed bytes.

Failure preserves compatible partial resources rather than removing packages, data, firewall rules
or secrets. If execution began, mutation evidence can be conservative: inspect before retrying.
A rerun still validates resources and refuses unknown authority. Provisioning does not create an
administrator; use the real-terminal procedure after readiness succeeds.

## Deploy an existing host

Inspect the host first:

```sh
./ops/taskman verify production
./ops/taskman releases production
./ops/taskman backups production
```

Then preview and deploy:

```sh
./ops/taskman deploy production --dry-run
./ops/taskman deploy production
```

Deployment validates supported host and runtime authority, database access and applicable capacity,
then plans from physical selection, complete installed provenance, successful history, live
migrations and recovery protections. Physical selection may be ahead of successful history after a
failed verification. A recognized state supports retrying the desired target or selecting a
different compatible target; it does not require the failed target to be healthy.

Review the redacted plan and type `yes`, or supply `--yes` for unattended ordinary confirmation.
Downgrade or unknown source ordering needs a separate acknowledgment or `--allow-downgrade`.
`--yes` does not supply that acknowledgment, and JSON output supplies neither. Dry-run reports the
requirements without prompting. Changed confirmation authority requires replanning or a refused
unattended run.

Pending migrations require an explicit policy, including dry-run; omission is an argument error:

```sh
./ops/taskman deploy production --migration-policy backward-compatible
./ops/taskman deploy production --migration-policy restore-required
```

The declaration is a human compatibility decision. The target must cover every applied migration
with consistent fingerprints. Partial-prefix provenance can support a safety backup without
making that backup automatically restorable; unsupported or contradictory schema refuses.
Rollback independently requires the immediately preceding successful selection and exact live
migration compatibility. It never reverses migrations.

Deployment stages or reuses immutable target content, refreshes compatible scheduled-backup code
under the lifecycle lock, creates required protected safety backups, stops Taskman, applies
remaining forward migrations, selects atomically, starts and verifies, then publishes success.
Caddy remains running. Deploy does not upgrade host packages, change UFW, rewrite PostgreSQL
configuration or reconverge Caddy. Required recovery backups survive interrupted attempts and
later target replacement. Pruning names exact eligible intermediate backups in the confirmed plan.

Manual adoption is not part of replayable deployment. `deploy` requires a current release proven
by completed Taskman records, and `provision` requires an unambiguous clean host. The retained
`--adopt-manual-current` parser flag returns a safety refusal; do not use it as a migration path.
Bring an existing manually managed installation into automation only through a separately reviewed
procedure.

Recognizable interruption can converge through a reviewed retry/replacement with a compatible
exact target. Contradictory migration, database, path or record authority requires manual inspection.
Automation does not reverse migrations, restart incompatible old code or invent successful history.
Use the result's mutation evidence and fresh observations to select a rerun; see
[admission contracts](../specs/2026-09-18-operations-contracts.md#observed-state-and-deployment-admission).

## Inspect releases and backups

Do not infer rollback or restore identifiers from filenames or directory listings. Use:

```sh
./ops/taskman releases production
./ops/taskman backups production
```

Both commands take the shared lock and collect a complete bounded inventory from validated
metadata. Partial pages are not reported as complete. Old-format or contradictory authority refuses;
unknown storage is preserved with warnings. Backup listings do not validate every dump body:
restore performs stronger content/safety validation before destructive work. Use exact reported
IDs rather than filesystem guesses.

Create an extra validated local dump without changing Taskman:

```sh
./ops/taskman backup production
```

Copy every backup required by the recovery policy to independently managed off-host storage and
test restoration there.

Provisioning installs `/usr/local/lib/taskman/taskman-backup.pyz` as the root-owned systemd timer
target. It is a scheduled host capability managed declaratively by pyinfra, not a second
workstation controller. The scheduled adapter accepts only its fixed non-secret environment and
maps outcomes to fixed process statuses: `0` for completed backup/retention, `2` for invalid
installed configuration, `6` for a recognizable retryable backup failure, `10` for unsafe or
ambiguous state requiring manual attention, and `12` when the shared lock is unavailable.

Retention preserves exact successful-history, migration-protection and restore references, then
keeps configured ordinary unprotected backups. Recovery attempts retain original/newest safety
and bounded intermediates; new validated safety precedes pruning. Unknown/damaged storage is
preserved, conflicting authority refuses, and metadata is removed before dump bytes.
A partial-migration dump may need manual recovery rather than automatic restore. See
[backup protection](../specs/2026-09-18-operations-contracts.md#backup-protection-and-successful-history).

## Roll back, restore, and clean up

Use only an ID printed by `releases`:

```sh
./ops/taskman rollback production RELEASE_ID --dry-run
./ops/taskman rollback production RELEASE_ID
```

Rollback requires typed confirmation, a healthy database, a new validated backup, and an
immediately preceding completed selection whose target release migration versions exactly match
the live database. It never reverses migrations. Missing, non-adjacent, or schema-incompatible
selection history refuses with status 10.

Malformed rollback release IDs and restore backup IDs return status 2 before loading
environment configuration or attempting SSH. Use the exact IDs reported by `releases`
and `backups`, respectively; rejected values are not echoed in the diagnostic.

Restore replaces database state and requires the exact ID from `backups`:

```sh
./ops/taskman restore production BACKUP_ID --dry-run
./ops/taskman restore production BACKUP_ID
```

Restore validates the exact input, compatible source release, required safety copies and recognized
database arrangement. Before destructive work it rechecks dump SHA-256, custom format, source/schema
compatibility and capacity under the lifecycle lock. It records database OIDs and durable creation
intent so temporary and retired names cannot substitute for identity. The original database is
preserved while a new temporary database is loaded and validated; success is recorded only after
selection and verification, before retired-database cleanup.

Retry the same backup to continue a recognized unfinished restore. If success is already durable
and only cleanup remains, ordinary retry finishes that cleanup without reloading the backup or
requiring new readiness. This preserves writes made after successful restore. To intentionally
load a completed backup again, review and run:

```sh
./ops/taskman restore production BACKUP_ID --reapply --dry-run
./ops/taskman restore production BACKUP_ID --reapply
```

To choose a different backup while a restore is unfinished:

```sh
./ops/taskman restore production NEW_BACKUP_ID --replace-unfinished --dry-run
./ops/taskman restore production NEW_BACKUP_ID --replace-unfinished
```

Replacement captures required fresh safety copies of possible writes and may discard only the
exact registered failed-restored database, never the original database. Required safety copies
must validate. An abandoned input dump may be unusable only when it is not independently required
for safety; metadata, source and path authority remain mandatory. Damaged unreferenced remainders
are preserved rather than automatically deleted.

If a previous replacement is pending and a third target is chosen, the first confirmation only
normalizes that pending replacement. The controller then rediscovers and asks for fresh confirmation
of the new target. Cancellation preserves already completed normalization and reports its changes.
Unknown identity, an unregistered loaded database or contradictory records requires manual
inspection. `--reapply` and `--replace-unfinished` are mutually exclusive; neither bypasses typed
confirmation.

Cleanup computes exact eligible targets from validated records:

```sh
./ops/taskman cleanup production --dry-run
./ops/taskman cleanup production
```

Cleanup works from filesystem and record authority even when database health or capacity is
unavailable, selections disagree, or restore/first installation is unfinished. Inspect and dry-run
do not normalize incomplete files. Required release provenance, complete history references,
backup protections, restore bindings and their material remain protected. During an unfinished
transition, releases are conservatively retained when migration irrelevance cannot be proved.

The controller collects the entire paged eligible inventory before typed environment/target-list
confirmation. It then executes only that set in bounded batches, freshly validating reference
facts, paths, file identities, checksums and eligibility under the lock for every batch. A newly
protected target refuses; unrelated newly eligible files do not broaden the plan. Manifest deletion
precedes dump deletion, and safe absence is idempotent.

Cleanup cannot drop/rename databases or remove recovery authority records, and does not refresh the
scheduler. If no target is eligible, add capacity or inspect unrelated storage instead of weakening
protection. Partial deletion or transport loss preserves known completions and possible mutation;
reinspect and reconfirm remaining work.

## Create the first administrator

Run this only from a real local terminal:

```sh
./ops/taskman create-admin production
```

The controller allocates a strict SSH TTY and invokes only the `bin/create-admin` wrapper below
the validated `install_root/current`. The constrained `systemd-run` bridge runs as `taskman`,
using that same configured current release for its working directory and the protected
`/etc/taskman/taskman.env` environment.
See [interactive administration](../specs/2026-09-09-dedicated-host-deployment-design.md#interactive-administration).

The email and password travel only through the terminal prompts; they are not arguments,
environment variables, decrypted deployment data, or structured results. The controller refuses
when stdin, stdout, or stderr is not a TTY and returns the interactive remote command's status.
Both the working directory and executable path are generated from the same validated installation
root; the interface does not accept an arbitrary remote command.

Afterward:

1. sign in over HTTPS;
2. invite a controlled address and receive the Resend email;
3. open the signed setup link, choose and confirm a password of at least eight characters,
   then sign in after account activation;
4. create and use an API key;
5. navigate a LiveView route and confirm its WebSocket remains connected; and
6. copy a verified backup off-host.

## Exit statuses and rerun decisions

| Status | Meaning |
| --- | --- |
| `0` | Success, including an already-satisfied no-op |
| `2` | Invalid command, argument, configuration, or unsupported target |
| `3` | Missing local prerequisite or release build failure |
| `4` | SOPS/age decryption, secret validation, or secret installation failure |
| `5` | SSH, host-key, privilege, or remote preflight failure |
| `6` | Database backup or backup-validation failure |
| `7` | Migration failure |
| `8` | Release staging, selection, or systemd service failure |
| `9` | Post-start readiness or public verification failure |
| `10` | Safety refusal, state conflict, or incompatible rollback |
| `11` | Database restore or restored-database validation failure |
| `12` | A lifecycle-lock acquisition was unavailable; this is not whole-command conflict detection |

The interactive `create-admin` bridge returns the remote command status after it has successfully
opened the session.

Existing-host runtime-environment and database/capacity preflight failures identify the
failed prerequisite group in `next_action`, without including remote output.

`facts.mutation_state` distinguishes `unchanged`, proved `changed`, and possible `unknown`
mutation. The public `changed` boolean is true for changed or unknown. Proved changes survive later
uncertainty, but final observations may still be unavailable. `facts.starting_state` is the first
accepted plan's exact authority, or null before any confirmation; it is not the final host state.

`facts.observations` and `facts.unavailable_fields` distinguish proved absence from failed
observation. Lost or invalid replies do not make earlier facts or desired targets current evidence.
A failed verification report retains fixed failing checks without raw output. Verification checks
selected release/service identity, Caddy, loopback topology/no EPMD, startup journal, exact local/public
readiness and HSTS within a bounded startup budget. Unsafe topology fails immediately.
See [verification details](../specs/2026-09-09-dedicated-host-deployment-design.md#verification-and-reporting)
and [failure/result contracts](../specs/2026-09-18-operations-contracts.md#failures-and-reporting).
Completed restore cleanup may succeed without new readiness; that does not establish current health.

Use those facts and the failed boundary to choose the next action:

| Reported state | Safe next step |
| --- | --- |
| Validation, SSH, capacity, or backup failed before stop | Keep the selected release running; correct the prerequisite and review the desired target again. |
| Upload or staging interrupted | Keep the current release and database, then retry the desired exact target. Deterministic staging and completed records distinguish reusable work from ambiguity. |
| Migration failed | Keep Taskman stopped. Preserve the pre-deploy backup and determine whether committed migrations allow forward repair or require restore. |
| Selection or startup failed | Do not automatically select old code. Inspect `current`, completed selections, service state, the fresh backup, and live migrations. Rerun only when those facts describe a recognized transition. |
| Local readiness failed | Inspect fresh service state, fixed verification summaries and journal. Verification failure does not automatically stop Taskman; stop it explicitly if needed while investigating. Do not assume the previous release is schema-compatible. |
| Public HTTPS failed after local readiness | Preserve the selected healthy local release; repair DNS/provider firewall/Caddy/ACME without exposing Phoenix directly. |
| Lock is held | Wait for the other controller command or scheduled backup to finish. The lock file contains no operation identity; do not remove it merely because the path exists. |
| Metadata is contradictory | Stop mutation. Reconcile managed records and exact paths before retrying. |
| Rollback is incompatible | Keep current code/database and use a validated backup through `restore`. |
| Restore failed before swap | The canonical database remains authoritative. Rerun the same backup request only when the completed facts and deterministic temporary database are recognizable. |
| Restore failed during/after swap | Keep Taskman stopped and retain all database names plus the pre-restore backup. A recognized deterministic arrangement can converge on rerun; any other arrangement requires manual inspection. |
| Cleanup revalidation raced | No newly protected target is removed; recompute and reconfirm the exact plan. |

## Manual recovery without the controller

Use the reviewed environment YAML to set these non-secret values on the host; do not infer them
from historical staging information. Substitute the actual configured roots, ports, database and
public hostname before running the examples:

```sh
install_root=/opt/taskman
backup_root=/var/backups/taskman
application_port=4000
distribution_port=6789
database_port=5432
database_name=taskman_prod
public_hostname=YOUR_TASKMAN_HOST

sudo systemctl status taskman.service --no-pager
sudo systemctl status caddy.service --no-pager
sudo journalctl --unit taskman.service --boot --no-pager --lines=100
sudo readlink -f "$install_root/current"
sudo ss -ltnp
```

Confirm the configured application/distribution ports bind only to `127.0.0.1`, PostgreSQL
only to loopback at its configured port, and no ordinary EPMD listener exists. Never expose managed
ports to make health checks succeed. Coordinate a maintenance window: stop Taskman and its backup
timer, wait for any running backup/controller operation to finish, and retain Caddy. Manual steps
must not overlap controller or scheduled mutation; do not remove a lock file to bypass contention.

Before any release change, record the current selection privately and create a new custom-format
dump outside release directories. Choose the validated cluster through its configured port:

```sh
sudo systemctl stop taskman-backup.timer taskman.service
sudo install -d -o root -g root -m 0700 "$backup_root/manual"
recovery_dir=$(sudo mktemp -d "$backup_root/manual/recovery.XXXXXXXX")
selected_release=$(sudo readlink -f "$install_root/current")
printf '%s\n' "$selected_release" | sudo tee "$recovery_dir/selected-release" >/dev/null
sudo chmod 0600 "$recovery_dir/selected-release"
sudo sh -c 'umask 077; sudo -u postgres pg_dump --host /var/run/postgresql \
  --port "$2" --format=custom "$3" > "$1/database.dump"' \
  sh "$recovery_dir" "$database_port" "$database_name"
sudo sh -c 'sudo -u postgres pg_restore --list < "$1/database.dump" > /dev/null' \
  sh "$recovery_dir"
```

Root creates the recovery directory and opens the dump files; PostgreSQL tools run as `postgres`
and use inherited standard output/input without traversing the private backup root. The dump is
root-owned mode 0600 from creation. These commands preserve the PostgreSQL tool exit status.
Check each command succeeds before proceeding; a failed dump can leave a partial file and is not
a usable recovery point. Preserve protected configuration, selection/recovery
records and existing backup pairs as well as the new dump. These manual files are not controller
backup records: do not manufacture metadata or pass them as `BACKUP_ID`. Copy recovery material to
protected off-host storage.

Use an exact previously verified release whose migrations are compatible with the live database.
Never edit immutable release contents. If `current.next` already exists, inspect it and the recorded
selection; do not overwrite or delete it blindly. With an absent temporary link:

```sh
release_id=EXACT_RELEASE_ID
sudo ln -s "releases/$release_id" "$install_root/current.next"
sudo mv -Tf "$install_root/current.next" "$install_root/current"
```

A migration failure may have committed earlier migrations. Review every intervening declaration and
live schema before starting old code. If compatibility is uncertain, keep Taskman stopped and load
a trusted dump into a fresh temporary database first. Validate its schema and intended release before
renaming databases; retain the old canonical database until local/public verification succeeds.
Do not drop either database simply to tidy names. Temporary/retired database authentication may
require local PostgreSQL administrator authority; see
[restore authentication](../specs/2026-09-18-operations-contracts.md#authentication-for-restore-databases).
It is a trusted-backup workflow, not a sandbox for arbitrary dumps.

Validate and control services:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl daemon-reload
sudo systemctl start taskman.service
curl --fail --silent --show-error "http://127.0.0.1:$application_port/healthz"
curl --fail --silent --show-error --dump-header - "https://$public_hostname/healthz"
```

Both readiness bodies must be exactly `ready`; public HTTPS must retain HSTS. Also verify service/
release identity, journal and loopback topology. Restart the backup timer only after reconciling
release/database/recovery/scheduler authority. Manual selection/database edits can leave controller
records inconsistent: stop automated mutation until a separately reviewed reconciliation procedure
establishes supported authority. Manual recovery does not create successful controller history.

## Release command trust boundary

The OTP release launcher is a privileged local operations interface, not an
application-authorized CLI. `bin/taskman eval` evaluates arbitrary Elixir in a new VM, `rpc`
evaluates inside the running VM, and `remote` opens IEx on that VM. The fixed `bin/migrate` and
`bin/create-admin` wrappers accept narrower input, but anyone who can invoke `bin/taskman` directly
has arbitrary Taskman code-execution authority.

Treat `root`, the `taskman` service account, and anyone able to read the release cookie and execute
the release as fully trusted. Keep the `taskman` group limited to the service account. Do not grant
generic sudo access to release launchers or unrestricted `systemd-run`. The `nologin` shell is not
a security boundary after the account or application process is compromised.

The release archive and installed `releases/COOKIE` contain the Erlang distribution cookie. Protect
them like deployment credentials, remove transferred copies after verified installation, and never
put them in tickets, logs, or user-readable artifact stores. The loopback distribution channel uses
cookie authentication rather than TLS and must never be bound or forwarded beyond loopback.

For break-glass inspection only, root may use the configured `install_root` from the manual
recovery example:

```sh
sudo -u taskman -- "$install_root/current/bin/taskman" remote
```

That shell has application authority and may expose secrets or mutate state. Prefer fixed
`systemctl`, `journalctl`, verification, and controller commands; review any captured diagnostic
output before sharing it.

## Recorded acceptance and future commissioning

[Identified acceptance](../research/2026-09-17-operations-vps-acceptance.md#scope-and-evidence-boundaries)
completed authenticated operations on an earlier retained database and first/repeat provisioning on
a later empty database. The last recorded fresh installation had no administrator; refresh state
and create fresh accounts before use. Never reuse archived passwords, signed links or bootstrap
material. The closing artifact was verified/dry-run inspected but was not selected on the host.
Later documentation changes are outside its artifact/CI proof.

The inventory retains [staging/provider/mail observations](../inventories/operations-environments.md#taskman-host)
and [reset recovery obligations](../inventories/operations-environments.md#staging-recovery-retention).
Keep protected off-host copies, root-only quarantine and all six original backup pairs; no recovery
point was pruned. This runbook grants no authority to reset/delete that environment.

### Future-installation commissioning checklist

For each separately authorized installation or affected native change, identify the exact source,
artifact, target and permitted external actions. Keep final findings/results/limits in the acceptance
report and current execution/verification continuation in the workstream handoff:

1. first and second provisioning with the same exact target;
2. HTTPS/HSTS, real-terminal administrator creation, sign-in/logout, invitation/recovery email,
   API key/CLI and connected LiveView;
3. another release, typed rollback, forward deployment and controlled migration failure/recovery;
4. validated backup, protected off-host copy and typed restore;
5. firewall/listener inspection and service/release/journal proof; and
6. bounded canary-secret/release-cookie leakage inspection.

Repository/container success does not prove native systemd, UFW, DNS, ACME, mail, reboot or restore.
Retained compatible OS services do not establish pristine-OS/package/firewall/ACME installation.
Record any authorized reboot evidence against its own baseline; encrypted copy transport alone
does not establish a durable tested disaster-recovery policy. Prior acceptance is historical,
not unresolved work or proof for a new environment.

This runbook does not authorize or perform that external acceptance run.

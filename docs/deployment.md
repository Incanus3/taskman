# Operating Taskman on a dedicated host

This is the canonical runbook for one Taskman installation on a dedicated Ubuntu host. The
repository-owned controller under `ops/` is the primary path for building, provisioning,
deploying, inspecting, backing up, rolling back, restoring, and cleaning up the installation.
Manual recovery remains documented below for use when the controller is unavailable.

The [deployment design](specs/2026-09-09-dedicated-host-deployment-design.md) defines the architecture,
authority boundaries, and rationale behind these procedures.

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

Before running host commands, set the age identity used to decrypt deployment secrets
and the SSH agent socket used to authenticate to the VPS:

```sh
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/taskman.txt"
export SSH_AUTH_SOCK="/run/user/$(id -u)/ssh-agent.socket"
./ops/taskman provision staging --artifact /secure/artifacts/EXACT_RELEASE.tar.gz --dry-run
```

These paths match the current staging workstation setup; use the actual identity file
and agent socket on another workstation. Exports apply to the current shell and commands
started from it. A shell opened elsewhere needs the same settings. `build` is local and
does not need either variable; read-only host commands need SSH authentication but do
not necessarily decrypt secrets.

The controller uses explicit environment YAML and pinned host-key authority with
`ssh_config_file=/dev/null`. It therefore does not inherit the `deploy` alias's
`IdentityAgent` setting. OpenSSH may work through that alias while the controller fails
with `strict SSH connection setup failed` until `SSH_AUTH_SOCK` points to the same agent.
Inspect `ssh -G deploy` for its `identityagent` setting; `ssh-add -l` checks whether the
current shell can reach its configured agent. Keep the private identity outside the
repository; neither variable should contain secret values, only paths.

The target must boot Ubuntu 26.04 LTS `amd64` with systemd as PID 1. The configured SSH
administrator must already be able to use the required `sudo` operations. The supported host
baseline includes Ubuntu's `python3-minimal` package. The controller uses it only to run a
deterministic, standard-library-only transient helper for one invocation; it never installs the
controller package, a resident agent, a listener, or a background process on the host. Provisioning
rejects unsupported platforms, ambiguous existing users/files/services/databases, mismatched host
keys, indirect public DNS, and conflicting listeners rather than overwriting them.

The build runs in the reviewed tag-and-digest pair in `ops/builder/Containerfile`:

```text
ubuntu:resolute-20260811.1
sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b
```

The readable dated tag identifies the selected Ubuntu image release, while the digest fixes its
content if a registry tag is reassigned. The artifact manifest records both values and rejects a
different builder base. The host receives only the built OTP release, managed runtime assets, and
the explicit Ubuntu runtime prerequisites—not source, Mix, Node, controller Python packages,
pyinfra, SOPS, age, Docker, or the build toolchain.

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

`install_root/releases`, `install_root/deployments`, `install_root/current`, and
`install_root/deployments/uploads` are derived from the validated `install_root`; they are not
configuration keys and have no legacy aliases. `install_root` and `backup_root` must have no
equality, ancestor, or descendant collision. Managed paths also cannot overlap Taskman's reserved
configuration, state, lock, or installed-program roots. The administrator command is derived from
the validated `install_root/current`; changing a root does not fall back to `/opt/taskman`.
Installation roots cannot contain single or double quotes, ASCII control characters, or DEL:
systemd rejects those characters in executable paths even after escaping. Invalid installation
roots fail configuration validation with status 2 before SSH. Quoted backup roots remain supported;
their filesystem-access paths are escaped for systemd's path-list syntax. This distinction follows
[systemd's executable-path validation](https://github.com/systemd/systemd/blob/v259/src/core/load-fragment.c),
not shell quoting rules.
Interactive SSH shell-quotes the administrator command's arguments so path characters
are passed literally rather than interpreted as remote shell syntax.

## Short-lived helper and persistent scheduled executable

Interactive controller commands build the deterministic `taskman-host.pyz` helper from the checked
controller revision. Each invocation transfers it through a unique private directory, verifies its
SHA-256 before and after installing the same bytes as `root:root` mode `0500` below
`/run/taskman-ops`, sends one bounded JSON request, accepts only the matching bounded and redacted
result, and performs exact-path best-effort cleanup. Its correlation identifier belongs only to
that transport exchange; it is not a durable operation record, stage history, or recovery
program. The helper is not an installed host agent and does not remain running between commands.

Scheduled backups use a different least-authority boundary. Provisioning installs the deterministic
`/usr/local/lib/taskman/taskman-backup.pyz` as `root:root` mode `0750`. The zipapp persists so
systemd can execute it later, but each `Type=oneshot` timer invocation is a short-lived process.
It contains only the standard-library code needed to read its fixed non-secret environment, take
the shared host lock, observe completed state, create and validate a backup, apply retention, print
one fixed journal message, and exit. It contains no deploy, rollback, restore, SSH,
secrets-decryption, or interactive-controller code.

A `transient helper cleanup was incomplete` warning means only that exact-path best-effort cleanup
did not finish. It does not authorize recursive deletion under `/tmp/taskman-ops` or
`/run/taskman-ops`; first establish that no invocation still uses an entry and verify its owner,
mode, and type.

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

A dry run still performs applicable local schema, secret, and artifact validation; strict SSH and
supported-platform fact discovery; runtime-environment ownership, mode, and key checks without
printing values; database access and capacity checks; completed release, backup, and selection
discovery; and operation planning. Restore dry-run validates the completed backup identity used for
the plan; the mutating invocation freshly validates the dump bytes and custom format under the
shared lock before changing the database. A dry run performs no remote, service, or database
mutation and asks for no confirmation. When no artifact is supplied, `provision` or `deploy` may
resolve a local artifact so compatibility and migration planning are complete.

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

The pinned Ubuntu 26.04 `linux/amd64` builder uses Elixir `1.20.4` and OTP `29.0.6` from
checksum-verified [HexPM builds](https://github.com/hexpm/bob#erlang-builds), rather than Ubuntu's
older Elixir/Erlang packages. It runs production dependency resolution, compilation with warnings
as errors, asset deployment, and OTP release assembly. Node `22.22.1`, Hex `2.5.1`, and Rebar3
`3.24.0` remain exact build inputs. The existing versioned Rebar3 binary, compiled for OTP 27,
also runs on OTP 29 and remains checked against its recorded SHA-512 digest; do not replace it
with an unpinned `mix local.rebar` download. The resulting archive, manifest, and detached SHA-256 file
identify the exact source, migration fingerprints, platform, OTP/Elixir/Node/Hex/Rebar3 inputs,
and archive bytes. Treat the archive as a credential because it contains the Erlang distribution
cookie.

Before packaging, a bounded, network-isolated release `eval` checks runtime configuration on
the pinned VM using synthetic values. It does not start Taskman or test database/email access;
its temporary configuration stays outside the release tree. A successful build still requires
real-host readiness and acceptance checks.

The older Ubuntu build tuple, Elixir `1.18.3` / OTP `27.3.4.6`, remains accepted for existing
artifacts, release records, backups, and rollback. New builds use only the current tuple. The
controller does not rewrite historical identities or accept arbitrary OTP versions. Alpine CI
retains its separate temporary Elixir `1.19.5` / OTP `26.2.5.21` pin; see the
[documented CI restriction](specs/2026-08-10-alpine-elixir-ci-design.md).

For the existing first-install staging baseline, an eventual authorized runtime deployment must
first converge the host using the new controller and the **exact original OTP 27 artifact**. This
updates the persistent scheduled-backup executable so it understands both release identities.
Then deploy the new OTP 29 artifact through the ordinary reviewed deployment command. Merely
uploading the transient deploy helper does not update the scheduled executable. After authorization,
substitute the verified archive paths in this sequence:

```sh
./ops/taskman provision staging --artifact /secure/artifacts/EXACT_ORIGINAL_OTP27_RELEASE.tar.gz
./ops/taskman deploy staging --artifact /secure/artifacts/EXACT_NEW_OTP29_RELEASE.tar.gz
```

Do not omit `--artifact` from the first command: that would build the new runtime instead of
replaying the original installation. Do not use this sequence on a host with later release
history; its existing replay restrictions still apply. No package or runtime upgrade requires
editing an installed release in place.

`provision` builds by default. When `deploy` is invoked without `--artifact`, it first requires a
clean, identified checkout, then reuses a locally cached artifact only when the archive, manifest,
and checksum verify and the release ID, source revision, and application version exactly match the
current inputs. Invalid or incomplete cache entries are preserved for inspection and ignored. If
there is no exact verified match, `deploy` builds a new artifact. Its result reports the source as
`cached`, `built`, or `explicit`.

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

Provisioning presents a redacted plan and requires ordinary interactive confirmation. One real
programmatic pyinfra deployment converges the stable desired state: required packages, unattended
security updates without automatic reboot, the `taskman` account and managed directories,
non-secret configuration and units, Caddy, systemd enablement, and the root-owned scheduled backup
zipapp and timer. Ordinary package, file, and service drift is declarative. Three direct custom
actions remain because they guard material consequences: UFW activation revalidates the active SSH
path, Caddy validates the candidate configuration immediately before installation, and PostgreSQL
validates the selected cluster before changing its native HBA file, then validates HBA syntax
before reload/restart. Database role/password authority plus private runtime and pgpass installation stay
outside pyinfra's logged command path so secret bytes are not exposed.

PostgreSQL retains the selected cluster's native `pg_hba.conf` location. Its live rules view
can validate changed file contents before they are loaded, but cannot validate an arbitrary
file path. Provisioning keeps a recoverable copy of the prior HBA bytes and metadata on the host and
restores it if validation fails. It refuses to replace authentication configuration when it
cannot verify the running cluster and native HBA path. The operator accepted the brief
crash/power-loss window after candidate installation and before validation/restoration;
after an interrupted attempt, inspect the HBA file and recovery copy before manually restarting
PostgreSQL. The recovery directory is beside the native file as `pg_hba.conf.taskman-backup`,
containing the previous `pg_hba.conf` and its `metadata`. A leftover directory blocks another
attempt until manually reconciled. Parser failure restores the prior file; a later
restart or verification failure retains the recovery copy for inspection rather than silently
changing disk configuration back underneath the running process. An isolated validation
instance is not part of this workflow.

The transient helper then converges the first immutable release from an empty completed host state.
It stages and verifies the release, applies the declared initial migration policy, atomically
selects and starts the release, verifies readiness, and publishes create-once `ReleaseRecord` and
`SelectionRecord` facts. A rerun reads the installed release, current link, applied migrations,
deterministic staging directory, and completed records; it repeats only a recognizable unfinished
step. A completed first selection is a verified no-op.

Installed releases are `root:taskman`: directories/executables are `0750`, regular data and
the completed manifest are `0640`. The service account can read and execute but cannot modify
the release. Do not fix an execution-permission failure by granting world access or adding
`taskman` to the root group; inspect the exact release metadata and preserve immutable contents.

Run the same command again after success. A converged host reports no declarative changes apart
from procedural verification. The scheduled-backup checksum check runs every time and pyinfra
counts that executed check as changed; consequently the top-level `changed` can remain `true`
while `release.changed` is `false` and no desired-state replacement is needed. Do not infer a
new release deployment from that aggregate boolean alone. Before the first release procedure, a failure retains compatible
partial state for a safe rerun rather than removing packages, the database, firewall rules, or
generated secrets.

Provisioning deliberately does not perform the interactive administrator step. Do it after
readiness succeeds as described below.

After successful admission and confirmation, provisioning installs its private ownership
marker before package, account, or directory changes. This lets a later run recognize a
compatible partial installation. A failed convergence after execution begins can report
`changed=true` conservatively: inspect the partial host state before retrying. Do not
create an ownership marker to adopt an unknown existing installation.

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

An actual deployment first verifies supported OS/architecture, DNS, capacity, database access, and
the exact root-owned mode-`0600` runtime environment with every required key present and nonempty;
values are never returned. It then displays the exact redacted environment, destination, current
and candidate release, artifact, migration policy, backup, affected-service, and
maintenance-window facts. Type `yes` only after reviewing that plan. The locked deployment
procedure revalidates the confirmed current release before any release mutation; `--dry-run`
returns the same planning facts without prompting.

The deployment scope is limited to release staging, a validated pre-deploy backup when migrations
will change the database, Taskman service stop/start, migrations, atomic selection, readiness, and
completed release/selection records. It does not upgrade packages, change UFW, rewrite PostgreSQL,
or reconverge Caddy.

When migration fingerprints differ, review the migrations and explicitly declare
one policy. Omitting it is a safety refusal before plan confirmation or upload,
including during dry-run; compatibility is never inferred from changed migrations:

```sh
./ops/taskman deploy production --migration-policy backward-compatible
./ops/taskman deploy production --migration-policy restore-required
```

This declaration is a human compatibility decision, not proof derived from migration syntax.
The selected declaration is reported in the bounded deployment result. Rollback does not rely on
that declaration: it independently requires the immediately preceding completed selection and
migration compatibility with the live database. Use `restore-required` when the prior code should
not be selected against the new schema.

Manual adoption is not part of replayable deployment. `deploy` requires a current release proven
by completed Taskman records, and `provision` requires an unambiguous clean host. The retained
`--adopt-manual-current` parser flag returns a safety refusal; do not use it as a migration path.
Bring an existing manually managed installation into automation only through a separately reviewed
procedure.

Taskman stops before migration or atomic selection while Caddy remains running. The helper has four
fixed outcomes: `succeeded`, `refused`, `retryable`, and `manual`. Recognizable interruption states
return `retryable`; rerun the same confirmed release and inputs so the procedure can converge from
completed facts. Contradictory selection, migration, database, path, or record authority returns
`manual` instead of guessing. A failed result reports bounded final facts such as the selected
release, database/service state, backup identifier, and failed boundary; it does not emit a stage
journal or generated recovery program.

## Inspect releases and backups

Do not infer rollback or restore identifiers from filenames or directory listings. Use:

```sh
./ops/taskman releases production
./ops/taskman backups production
```

Both commands take the shared host-operation lock and read only validated root-owned completed
metadata. `releases` reports each exact `ReleaseRecord`: release ID, source revision, artifact
SHA-256, and sorted migration fingerprints. `backups` reports each exact `BackupRecord`: backup ID,
whole-second UTC creation time, dump SHA-256, source release ID, observed migration versions, and
source database size. There are no pending, provisional, activation, adoption, or checksum-less
compatibility records. Contradictory completed metadata returns a safety refusal; unrecognized
storage is preserved and reported as a warning.

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

Retention protects every backup referenced by any completed `SelectionRecord`, then retains the
configured number of newest unprotected completed backups sorted deterministically by creation
time and backup ID. Referenced backups do not consume that ordinary retention count. Before
unlinking a pair, it revalidates canonical regular-file identity, ownership, private modes,
manifest identity, size, and SHA-256; it removes the manifest before the dump. Malformed,
conflicting, redirected, checksum-mismatched, or otherwise unknown storage is preserved, and
ambiguous authority returns `manual` rather than deleting an unproved backup.

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

Before a dry-run result or typed prompt, restore requires a completed backup record with a
root-owned regular non-link dump and uses its recorded source release and database size in the
plan. After confirmation and under the shared lock, it compares the dump bytes to the mandatory
`dump_sha256`, freshly runs `pg_restore --list`, checks database storage capacity, and revalidates
the completed record against its source release and migration versions. It then
makes a pre-restore backup, restores into the deterministic temporary database, validates schema
state, retains the prior canonical database under the deterministic old-database name during the
swap, starts the intended release, verifies it, and publishes a completed selection before
removing the retired database. A corrupt or replaced dump is refused. Recognizable interruption
arrangements converge when the exact request is rerun; any different or contradictory arrangement
returns status 10 for manual inspection rather than attempting an unsafe automatic correction.

Cleanup computes exact eligible targets from validated records:

```sh
./ops/taskman cleanup production --dry-run
./ops/taskman cleanup production
```

Its typed confirmation names the environment and exact target identifiers; the confirmed target
set is revalidated before deletion. Cleanup cannot remove the
current or previous release, rollback-required releases, protected backups, unknown storage,
unresolved paths, or out-of-root targets. Revalidation under the exclusive lock prevents a stale
plan from deleting a target that became protected.

## Create the first administrator

Run this only from a real local terminal:

```sh
./ops/taskman create-admin production
```

The controller allocates a strict SSH TTY and invokes only the `bin/create-admin` wrapper below
the validated `install_root/current`. For the default installation root, the constrained boundary is:

```sh
sudo -- systemd-run --wait --pipe --collect \
  --property=User=taskman \
  --property=Group=taskman \
  --property=WorkingDirectory=/opt/taskman/current \
  --property=EnvironmentFile=/etc/taskman/taskman.env \
  -- /opt/taskman/current/bin/create-admin
```

The email and password travel only through the terminal prompts; they are not arguments,
environment variables, decrypted deployment data, or structured results. The controller refuses
when stdin, stdout, or stderr is not a TTY and returns the interactive remote command's status.
Both the working directory and executable path are generated from the same validated installation
root; the interface does not accept an arbitrary remote command.

Afterward:

1. sign in over HTTPS;
2. invite a controlled address and receive the Resend email;
3. complete the invited account setup;
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
| `12` | Another host operation holds the shared lock |

The interactive `create-admin` bridge returns the remote command status after it has successfully
opened the session.

Existing-host runtime-environment and database/capacity preflight failures identify the
failed prerequisite group in `next_action`, without including remote output.

Use the fixed status and bounded final facts, not an assumed rollback or a
stage journal, to choose the next action:

| Reported state | Safe next step |
| --- | --- |
| Validation, SSH, capacity, or backup failed before stop | Keep the selected release running; correct the prerequisite and retry the exact artifact. |
| Upload or staging interrupted | Keep the current release and database, then retry the same exact artifact. Deterministic staging and completed records distinguish reusable work from ambiguity. |
| Migration failed | Keep Taskman stopped. Preserve the pre-deploy backup and determine whether committed migrations allow forward repair or require restore. |
| Selection or startup failed | Do not automatically select old code. Inspect `current`, completed selections, service state, the fresh backup, and live migrations. Rerun only when those facts describe a recognized transition. |
| Local readiness failed | Keep the unhealthy service stopped and inspect the fixed verification summaries and journal. |
| Public HTTPS failed after local readiness | Preserve the selected healthy local release; repair DNS/provider firewall/Caddy/ACME without exposing Phoenix directly. |
| Lock is held | Wait for the other controller command or scheduled backup to finish. The lock file contains no operation identity; do not remove it merely because the path exists. |
| Metadata is contradictory | Stop mutation. Reconcile managed records and exact paths before retrying. |
| Rollback is incompatible | Keep current code/database and use a validated backup through `restore`. |
| Restore failed before swap | The canonical database remains authoritative. Rerun the same backup request only when the completed facts and deterministic temporary database are recognizable. |
| Restore failed during/after swap | Keep Taskman stopped and retain all database names plus the pre-restore backup. A recognized deterministic arrangement can converge on rerun; any other arrangement requires manual inspection. |
| Cleanup revalidation raced | No newly protected target is removed; recompute and reconfirm the exact plan. |

## Manual recovery without the controller

Start with read-only inspection:

```sh
sudo systemctl status taskman.service --no-pager
sudo systemctl status caddy.service --no-pager
sudo journalctl --unit taskman.service --boot --no-pager --lines=100
sudo readlink -f /opt/taskman/current
sudo ss -ltnp
```

Confirm that Phoenix is only on `127.0.0.1:4000`, distribution only on
`127.0.0.1:6789`, PostgreSQL only on loopback, and no ordinary EPMD listener exists. Do not make a
service appear healthy by exposing one of those ports.

Before any manual release change, preserve the current selection and create a custom-format dump
outside release directories:

```sh
sudo install -d -o postgres -g postgres -m 0700 /var/backups/taskman/manual
sudo -u postgres pg_dump --format=custom \
  --file /var/backups/taskman/manual/taskman-recovery.dump taskman_prod
sudo -u postgres pg_restore --list \
  /var/backups/taskman/manual/taskman-recovery.dump >/dev/null
```

Use an exact, previously verified release directory. Never edit a selected immutable release:

```sh
sudo ln -s releases/EXACT_RELEASE_ID /opt/taskman/current.next
sudo mv -Tf /opt/taskman/current.next /opt/taskman/current
```

Start old code only after reviewing every intervening migration declaration and database state. A
migration failure may have committed earlier migrations. If compatibility is uncertain, keep
Taskman stopped and restore into a new temporary database first; validate its schema and intended
release before renaming databases. Retain the old canonical database until the restored
installation passes local and public verification. Do not drop either database during incident
response merely to make the names look tidy.

Validate and control services directly:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl daemon-reload
sudo systemctl start taskman.service
curl --fail --silent --show-error http://127.0.0.1:4000/healthz
curl --fail --silent --show-error https://YOUR_TASKMAN_HOST/healthz
```

The local response must be exactly `ready`; public verification must also retain HSTS. Keep Caddy
running during a Taskman maintenance window.

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

For break-glass inspection only, root may run:

```sh
sudo -u taskman -- /opt/taskman/current/bin/taskman remote
```

That shell has application authority and may expose secrets or mutate state. Prefer fixed
`systemctl`, `journalctl`, verification, and controller commands; review any captured diagnostic
output before sharing it.

## Current staging external state

The staging hostname is `taskman.page`. The domain is registered through Cloudflare Registrar
through 2027-09-05 with WHOIS redaction and registrar lock enabled. Auto-renew is disabled, so
renew the domain or explicitly enable auto-renew before that date if it should be retained.

Cloudflare DNS is active and publishes DNS-only apex `A` and `AAAA` records for `2.29.47.77` and
`2a01:4f9:c015:6045::1`. The authoritative nameservers and public `1.1.1.1` resolution returned
those addresses when last verified on 2026-09-06. On 2026-09-09 the operator opened the
Hetzner provider firewall for public TCP 80/443. Caddy obtained a valid certificate;
external IPv4 checks confirmed HTTP-to-HTTPS redirection, exact HTTPS readiness, and HSTS.
Host-side HTTPS checks also succeeded over both IPv4 and IPv6; independent external IPv6
reachability has not been established from the current workstation.

Provisioning and standalone verification subsequently succeeded for release
`0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6`, including all ten migrations and the completed
selection record. The controller at local revision `730c002` includes the scoped-listener and
empty-HTTP-reason-phrase verification corrections. Repeat provisioning preserved the release;
its only reported pyinfra operation was the scheduled-backup checksum verification. This is
readiness evidence, not completion of the broader acceptance checklist below.

Later on 2026-09-09, the authorized OTP 29 upgrade selected and started
`0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6` but failed before successful-selection history was
published. Fresh individual checks confirmed the exact running release, all ten unchanged migrations,
loopback topology, clean journal, local/public readiness, and HSTS. The deployment-record mismatch
remains unresolved; do not repeat first-install provisioning or manufacture a selection record.

Administrator acceptance subsequently succeeded independently: the operator privately created the
account and signed in, browser inspection confirmed active administrator access at `/admin`, and
normal logout followed by fresh `/admin` and `/` requests required sign-in again. No password or
token was captured. This closes the initial administrator/login/logout gate, not deployment
reconciliation or the remaining email, API, backup, and restore acceptance. Current continuation
state is in the [VPS readiness handoff](handoffs/ops-vps-readiness.md).

On 2026-09-09, `notify.taskman.page` was created in Resend's `eu-west-1` region
for sending only, with open/click tracking disabled. The intended sender is
`no-reply@notify.taskman.page`. Cloudflare now contains Resend's DKIM TXT at
`resend._domainkey.notify.taskman.page` and its return-path MX (priority 10,
`feedback-smtp.eu-west-1.amazonses.com`) and SPF TXT
(`v=spf1 include:amazonses.com ~all`) at `send.notify.taskman.page`.
DNS publication was checked; Resend subsequently verified the domain and all three
records on 2026-09-09. The API key has been supplied through the protected deployment
secrets workflow. Actual email delivery remains to be tested.

## Unresolved disposable-host acceptance

Repository tests and container builds do not prove systemd PID 1, UFW, DNS, ACME, email, reboot, or
full restore behavior on a real host. Full readiness still requires a separately authorized,
disposable Ubuntu 26.04 `amd64` VPS run covering:

1. first and second provisioning;
2. HTTPS, HSTS, interactive administrator creation, sign-in, invitation email, API key, and
   LiveView;
3. a second release, rollback, forward deployment, and controlled migration failure;
4. backup creation, validation, and restore;
5. firewall and listener inspection; and
6. canary-secret and release-cookie leakage inspection.

This runbook does not authorize or perform that external acceptance run.

# Operating Taskman on a dedicated host

This is the canonical runbook for one Taskman installation on a dedicated Ubuntu host. The
repository-owned controller under `ops/` is the primary path for building, provisioning,
deploying, inspecting, backing up, rolling back, restoring, and cleaning up the installation.
Manual recovery remains documented below for use when the controller is unavailable.

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

The target must boot Ubuntu 26.04 LTS `amd64` with systemd as PID 1. The configured SSH
administrator must already be able to use the required `sudo` operations. The supported host
baseline includes Ubuntu's `python3-minimal` package: fixed, repository-owned remote validation
programs use that interpreter, but install no controller Python packages on the host. Provisioning
rejects unsupported platforms, ambiguous existing users/files/services/databases, mismatched host
keys, indirect public DNS, and conflicting listeners rather than overwriting them.

The build runs in the image pinned by digest in `ops/builder/Containerfile`. The host receives only
the built OTP release, managed runtime assets, and the explicit Ubuntu runtime prerequisites—not
source, Mix, Node, controller Python packages, pyinfra, SOPS, age, Docker, or the build toolchain.

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
- managed release, deployment, and backup roots;
- backup schedule and backup/release retention;
- readiness and connection timeouts; and
- an explicitly selected PostgreSQL package track only when required.

The configuration is validated before mutation. A real environment file is non-secret but still
environment-sensitive; add it to version control only as a deliberate operator decision.
`release_root` and `deployment_root` must be distinct descendants of `managed_root`;
`managed_root/current`, those roots, and `backup_root` must have no equality, ancestor, or
descendant collision. Managed paths also cannot overlap Taskman's reserved configuration, state,
lock, or installed-program roots. Cleanup staging authority is exactly
`deployment_root/uploads`, and the administrator command is derived from the validated
`managed_root/current`; changing a root does not fall back to `/opt/taskman`.

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
printing values; database access and capacity checks; lifecycle-state discovery; and operation
planning. Restore also freshly validates the exact dump body. It performs no remote, service, or
database mutation and asks for no confirmation. When no artifact is supplied, `provision` or
`deploy` may build a local artifact so compatibility and migration planning are complete.

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

The pinned Ubuntu 26.04 `linux/amd64` builder runs production dependency resolution, compilation
with warnings as errors, asset deployment, and OTP release assembly. Hex `2.5.1` and Rebar3
`3.24.0` are exact build inputs; Rebar3 is downloaded from its versioned Hex build URL and checked
against the recorded SHA-512 digest. The resulting archive, manifest, and detached SHA-256 file
identify the exact source, migration fingerprints, platform, OTP/Elixir/Node/Hex/Rebar3 inputs,
and archive bytes. Treat the archive as a credential because it contains the Erlang distribution
cookie.

`provision` and `deploy` build by default. To retry the exact bytes after failure, reuse the archive
reported by the failed operation:

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

Provisioning presents a redacted plan and requires ordinary interactive confirmation. It then
converges only the supported host baseline: required packages, unattended security updates without
automatic reboot, UFW rules that preserve the active SSH port, PostgreSQL and its Taskman
role/database, validated local backups and timer, Caddy, the `taskman` account, systemd units,
root-owned runtime configuration, and the first immutable release.

The first release uses a dedicated genesis form of the canonical locked release transaction. It
accepts only an empty lifecycle, creates and validates a pre-activation database backup, applies
the declared initial migration policy, atomically selects and starts the release, verifies
readiness, and publishes activation evidence whose previous release is `null`. A retry resumes
only matching evidence and a completed first activation is a verified no-op; it is not routed
through the existing-host deploy precondition that requires a current release.

Run the same command again after success. A converged host reports no declarative changes apart
from procedural verification. Before the first release transaction, a failure retains compatible
partial state for a safe rerun rather than removing packages, the database, firewall rules, or
generated secrets.

Provisioning deliberately does not perform the interactive administrator step. Do it after
readiness succeeds as described below.

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
values are never returned. This preflight also applies before publishing a manual-adoption
baseline. It then displays the exact redacted environment, destination, current and candidate
release, artifact, migration policy, backup, affected-service, and maintenance-window facts. Type
`yes` only after reviewing that plan. The locked transaction revalidates the confirmed current
release before any release mutation; `--dry-run` returns the same planning facts without prompting.

The deployment scope is limited to release staging, a validated pre-deploy backup, Taskman service
lifecycle, migrations, atomic selection, readiness, and activation records. It does not upgrade
packages, change UFW, rewrite PostgreSQL, or reconverge Caddy.

When migration fingerprints differ, review the migrations and declare one policy:

```sh
./ops/taskman deploy production --migration-policy backward-compatible
./ops/taskman deploy production --migration-policy restore-required
```

This declaration is a human compatibility decision, not proof derived from migration syntax.
`backward-compatible` permits code rollback across that activation edge without reversing schema.
`restore-required` makes rollback refuse and directs the operator to a database restore.

For the first automated deployment of a healthy installation created by the former manual
runbook, inspect it and explicitly authorize one-time adoption:

```sh
./ops/taskman deploy production --adopt-manual-current
```

The controller records a root-owned baseline without modifying the adopted release directory.
Mutable, incomplete, unhealthy, out-of-root, or topology-incompatible releases are refused.

Every non-no-op activation gets a fresh validated backup. Taskman stops during migration and atomic
selection while Caddy remains running. A migration, activation, startup, or readiness failure does
not silently start old code: migrations may already have changed the database, so the report
preserves the selected release, service/database state, backup, changed stages, and next safe
action for operator judgment.

## Inspect releases and backups

Do not infer rollback or restore identifiers from filenames or directory listings. Use:

```sh
./ops/taskman releases production
./ops/taskman backups production
```

Both commands take a shared lifecycle lock and read validated root-owned metadata. `releases`
reports current/previous/inactive state, provenance, migration policy, and rollback eligibility.
`backups` reports the exact restore ID, reason, database, size, recorded validation, and whether the
dump is still present. Contradictory metadata returns a safety refusal; unrecognized storage is
reported as a warning.

Create an extra validated local dump without changing Taskman:

```sh
./ops/taskman backup production
```

Copy every backup required by the recovery policy to independently managed off-host storage and
test restoration there.

## Roll back, restore, and clean up

Use only an ID printed by `releases`:

```sh
./ops/taskman rollback production RELEASE_ID --dry-run
./ops/taskman rollback production RELEASE_ID
```

Rollback requires ordinary confirmation, a healthy database, a new validated backup, and a
compatible activation chain. It never reverses migrations. Any intervening `restore-required`
edge refuses with status 10.

Restore replaces database state and requires the exact ID from `backups`:

```sh
./ops/taskman restore production BACKUP_ID --dry-run
./ops/taskman restore production BACKUP_ID
```

Before either a dry-run result or a typed prompt, restore checks that the exact recorded path is a
regular non-link file with the recorded size, freshly runs `pg_restore --list`, and checks database
storage capacity. After confirmation, it revalidates the same facts under the lifecycle lock
before mutation. It then makes a pre-restore backup, restores into a temporary database, validates
schema state, retains the old canonical database under a unique recovery name, starts the intended
release, and verifies it. A corrupt or replaced dump is refused before confirmation; a failed swap
retains the databases and reports only the exact recovery commands appropriate to the observed
boundary.

Cleanup computes exact eligible targets from validated records:

```sh
./ops/taskman cleanup production --dry-run
./ops/taskman cleanup production
```

Its typed confirmation binds the environment and plan fingerprint. Cleanup cannot remove the
current or previous release, rollback-required releases, protected backups, unknown storage,
unresolved paths, or out-of-root targets. Revalidation under the exclusive lock prevents a stale
plan from deleting a target that became protected.

## Create the first administrator

Run this only from a real local terminal:

```sh
./ops/taskman create-admin production
```

The controller allocates a strict SSH TTY and invokes only the `bin/create-admin` wrapper below
the validated `managed_root/current`. For the default managed root, the constrained boundary is:

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
Both the working directory and executable path are generated from the same validated managed
root; the interface does not accept an arbitrary remote command.

Afterward:

1. sign in over HTTPS;
2. invite a controlled address and receive the Resend email;
3. complete the invited account setup;
4. create and use an API key;
5. navigate a LiveView route and confirm its WebSocket remains connected; and
6. copy a verified backup off-host.

## Exit statuses and recovery states

| Status | Meaning |
| --- | --- |
| `0` | Success, including an already-satisfied no-op |
| `2` | Invalid command, argument, configuration, or unsupported target |
| `3` | Missing local prerequisite or release build failure |
| `4` | SOPS/age decryption, secret validation, or secret installation failure |
| `5` | SSH, host-key, privilege, or remote preflight failure |
| `6` | Database backup or backup-validation failure |
| `7` | Migration failure |
| `8` | Release staging, activation, or systemd lifecycle failure |
| `9` | Post-start readiness or public verification failure |
| `10` | Safety refusal, state conflict, or incompatible rollback |
| `11` | Database restore or restored-database validation failure |
| `12` | Another lifecycle operation holds the deployment lock |

The interactive `create-admin` bridge returns the remote command status after it has successfully
opened the session.

Use the report, not an assumed rollback, to choose recovery:

| Reported state | Safe next step |
| --- | --- |
| Validation, SSH, capacity, or backup failed before stop | Keep the selected release running; correct the prerequisite and retry the exact artifact. |
| Upload or staging interrupted | Keep the current release and database; inspect only the reported private staging path/receipt, then retry. |
| Migration failed | Keep Taskman stopped. Preserve the pre-deploy backup and determine whether committed migrations allow forward repair or require restore. |
| Selection or startup failed | Do not automatically select old code. Inspect `current`, service state, the fresh backup, and migration declaration. |
| Local readiness failed | Keep the unhealthy service stopped and inspect the fixed verification summaries and journal. |
| Public HTTPS failed after local readiness | Preserve the selected healthy local release; repair DNS/provider firewall/Caddy/ACME without exposing Phoenix directly. |
| Lock is held | Wait for the reported operation and PID; do not remove the lock while that operation may be active. |
| Metadata is contradictory | Stop mutation. Reconcile managed records and exact paths before retrying. |
| Rollback is incompatible | Keep current code/database and use a validated backup through `restore`. |
| Restore failed before swap | The canonical database remains authoritative; remove only the tool-created temporary database if the report says it is safe. |
| Restore failed during/after swap | Keep Taskman stopped, retain both databases and the pre-restore backup, and use only the reported inverse/inspection commands. |
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
those addresses when last verified on 2026-09-06. The VPS, provider firewall, Caddy, and Resend
configuration still require separate operator action and acceptance testing.

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

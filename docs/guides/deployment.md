# Operating Taskman on a dedicated host

This is the canonical runbook for one Taskman installation on a supported Ubuntu host.
Existing PostgreSQL and Caddy may serve other applications; provisioning preserves their
operator-owned configuration by default. The repository-owned controller under `ops/` is the primary path for building, provisioning,
deploying, inspecting, backing up, rolling back, restoring, and cleaning up the installation.
Manual recovery remains documented below for use when the controller is unavailable.
The repository-bundled [Taskman operations reference skill](../../priv/taskman_ops_skill/SKILL.md)
routes agents to this runbook; read it from the checkout without automatic installation. It is
separate from the application CLI skill and grants no host-action authority.

The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) defines the architecture,
authority boundaries, and rationale behind these procedures.

Only the [supported artifact and record baseline](../specs/2026-09-18-operations-contracts.md#one-time-compatibility-boundary)
is accepted. Do not convert, adopt or delete unsupported authority to make a command pass.
The [native acceptance guide](native-acceptance.md) defines portable verification requirements.
Actual environment observations, acceptance receipts and recovery custody belong in private
operator storage under the [inventory privacy policy](../inventories/operations-environments.md).
Public procedures are not proof of current host state or authority for reset, provider changes
or deletion.

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
2. obtain its SSH host-key fingerprint through a trusted, out-of-band channel and enroll the
   matching server key in the workstation's `~/.ssh/known_hosts`;
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
- OpenSSH tools (`ssh` and `ssh-keygen`);
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
all real-environment commands need the age identity to decrypt settings. Read-only host commands
need SSH authentication but do not necessarily decrypt credential documents.
The controller uses environment YAML and pinned host-key authority with
`ssh_config_file=/dev/null`; it does not inherit an SSH alias's `IdentityAgent` setting.
If alias-based SSH works but the controller reports `strict SSH connection setup failed`,
inspect `ssh -G YOUR_HOST_ALIAS` and set `SSH_AUTH_SOCK` to the intended agent. Both variables
contain paths, never credential bytes. Keep the private age identity outside the repository.

Operator reports show ordinary configuration values, including hostnames, database identifiers
and configured paths. SOPS encryption protects their repository storage. Keep reports containing
real deployment details in private operator storage. Actual credentials must never be printed;
sensitive command output is suppressed and secret containers hide their values.

Both ordinary and interactive controller connections read the operator's `~/.ssh/known_hosts`.
Native `ssh-keygen` selects entries for the configured hostname and port and checks their SHA-256
fingerprints. The controller supplies only the public key matching the environment pin in a
mode-0600 temporary file; the SSH client performs strict server-key verification, and the file is
removed afterwards. The original `known_hosts` is never modified. Hashed entries are supported;
certificate-authority entries do not replace the pinned raw server key.

If the entry is missing, enroll it separately using ordinary SSH to the exact configured hostname
and port. Compare the displayed fingerprint with the independently verified environment pin before
accepting it. A missing, malformed, mismatched or pinned-revoked entry stops the controller before
connecting. Investigate key-change warnings through the trusted channel before changing either
the pin or `known_hosts`; do not remove entries just to bypass the warning. The controller never
scans, accepts a new key or retries a failed connection automatically.

The host must boot Ubuntu 26.04 LTS `amd64` with systemd as PID 1, Ubuntu's
`python3-minimal`, and an administrator able to perform the required sudo operations.
Provisioning refuses unsupported platforms, ambiguous existing resources, mismatched host keys,
indirect public DNS and conflicting listeners. The host receives an OTP release, managed assets
and runtime prerequisites; it does not need source, Mix, Node, Docker or controller dependencies.
See [architecture and ownership](../specs/2026-09-09-dedicated-host-deployment-design.md#architecture-and-ownership).

## Configure SOPS and age

`.sops.yaml` contains public age recipients and the selection rule for
`ops/environments/*.config.sops.yaml` and `*.secrets.sops.yaml`. Replace the example recipient
with the intended public recipient. Keep every private age identity outside the repository and outside release artifacts.
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

Create an encrypted settings document from the complete synthetic example:

```sh
sops encrypt --filename-override ops/environments/production.config.sops.yaml \
  --input-type yaml --output-type yaml < ops/environments/example.yaml \
  > ops/environments/production.config.sops.yaml
sops ops/environments/production.config.sops.yaml
```

Replace every documentation value in the SOPS editor before using the environment.
Keep settings in `production.config.sops.yaml` and credentials in
`production.secrets.sops.yaml`; both documents use the external age identity.

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
- `postgres_package_track`, an optional PostgreSQL major version used to select one registered
  cluster. On a multi-version host, `18` selects the unique version-18 cluster; it does not upgrade
  packages or select the first cluster. Multiple clusters in the requested major version refuse.
  Without a track, multiple registered clusters also refuse.

The configuration is decrypted into controller memory and validated before mutation.
Controller commands require `<name>.config.sops.yaml`; plaintext settings are not a fallback.
The readable `example.yaml` is a synthetic template. Programmatic callers may explicitly select
plaintext loading with `load_environment(name, encrypted=False)`; the default is encrypted for
every environment name. Do not commit real readable settings or include private values in keys
or comments; keep them generic. Environment files are excluded
from release source snapshots. The only
configurable filesystem roots are:

```yaml
install_root: /opt/taskman
backup_root: /var/backups/taskman
```

Release, deployment, upload, lock and `current` paths derive from `install_root`.
Transient release/helper inputs use `install_root/uploads` (private root/group root `0700`,
root-owned regular files `0600`), beside the durable `deployments` records. Upload-producing
requests check existing ancestors and destination names before creating only missing installation
and upload leaves. Unsafe metadata and existing destinations are refused without repair. Transfers
reuse private SSH staging and native `install`; do not manually replace validated path components
during an operation. Consumers recheck ordinary authority and open private inputs without following
file symlinks. See the [upload checks](../specs/2026-10-30-upload-staging-design.md#simplified-upload-checks)
for the supported boundary and mutation evidence.
Do not configure subordinate roots or legacy aliases. Both roots must be normalized absolute paths,
disjoint from each other and reserved managed locations. Installation roots exclude whitespace,
backslash, `%`, quotes, ASCII controls and DEL; backup roots may contain quotes.
Invalid configuration returns status 2 before SSH. See
[path authority](../specs/2026-09-09-dedicated-host-deployment-design.md#configuration-and-path-authority).

## Remove an obsolete empty upload directory

This one-time procedure concerns only `install_root/deployments/uploads`. It requires separate
operational authorization for the concrete installations and commands. The example installation
`/opt/taskman` below is synthetic; retain actual inventory and results in private operator storage.
Do not run these commands merely because local implementation or review passed.

Identify every controller that can touch each installation, upgrade them to the corrected version,
and arrange a quiet window with no provisioning, deployment, restore, cleanup or competing lifecycle
work. Uploads precede lifecycle-lock acquisition, so holding that lock alone is insufficient.
Coordinate overlapping activity without stopping backup timers solely for this removal. Do not
terminate unattended controllers or delete recovery material under this procedure.

In a temporary operator shell, define this inspection/removal function. It opens every component
without following symlinks, retains the validated parent descriptor, requires safe root-owned
ancestors and an exact root/group root `0700` leaf, and refuses any contents, including dotfiles.
It never repairs metadata or removes files. Missing paths report `absent` with status 0; unsafe
metadata, contents, changed identity, failed removal or failed post-check report `refused` with
status 1. The exact empty-directory `rmdir` is the only deletion operation.

```sh
inspect_old_uploads() {
  sudo /usr/bin/python3 -I -S - "$1" "$2" <<'PYTHON'
import os
from pathlib import PurePosixPath
import stat
import sys

opened = []
discovered = False
try:
    root = PurePosixPath(sys.argv[1])
    action = sys.argv[2]
    if (not root.is_absolute() or root.as_posix() != sys.argv[1]
            or len(root.parts) < 2 or ".." in root.parts
            or action not in {"inspect", "remove"}):
        raise ValueError()
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened.append(parent)
    for component in (*root.parts[1:], "deployments"):
        details = os.fstat(parent)
        if (not stat.S_ISDIR(details.st_mode) or details.st_uid != 0
                or details.st_mode & 0o7022):
            raise ValueError()
        parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        opened.append(parent)
    details = os.fstat(parent)
    if (not stat.S_ISDIR(details.st_mode) or details.st_uid != 0
            or details.st_mode & 0o7022):
        raise ValueError()
    leaf = os.open("uploads", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    opened.append(leaf)
    discovered = True
    details = os.fstat(leaf)
    if (not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o700 or os.listdir(leaf)):
        raise ValueError()
    current = os.stat("uploads", dir_fd=parent, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (details.st_dev, details.st_ino):
        raise ValueError()
    if action == "inspect":
        print("validated empty directory")
    else:
        os.rmdir("uploads", dir_fd=parent)
        try:
            os.stat("uploads", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            print("removed; old path absent")
        else:
            raise ValueError()
except FileNotFoundError:
    if discovered:
        print("refused; preserve state and investigate", file=sys.stderr)
        raise SystemExit(1)
    print("absent; no cleanup needed")
except (OSError, ValueError, IndexError):
    print("refused; preserve state and investigate", file=sys.stderr)
    raise SystemExit(1)
finally:
    for descriptor in reversed(opened):
        os.close(descriptor)
PYTHON
}
inspect_old_uploads /opt/taskman inspect
```

After reviewing the inspection result and obtaining authorization for the exact removal command,
keep the quiet window and run:

```sh
inspect_old_uploads /opt/taskman remove
```

A nonzero result stops the procedure. Preserve remaining state and investigate; do not use forced
removal, recursive deletion, wildcard removal, metadata repair or automatic disposal. If a file
arrives after inspection, `rmdir` fails and preserves it. Repeat read-only inventory/verification
with the corrected controller and confirm the specific `unknown deployment entry: uploads` warning
has gone; preserve unrelated warnings. Exercise new uploads only during separately authorized
operation acceptance. Older controllers can recreate the directory and require reassessment.

The exact shell function and inspection/removal calls above were reproduced with disposable
installation roots and a test-only `sudo` substitute providing synthetic ownership metadata.
The unmodified Python body and deletion algorithm were also exercised directly. Missing paths
and validated empty removal succeeded; a dotfile, nonempty directory, symlink leaf/ancestor,
wrong owner/group/mode, writable ancestor and a file arriving immediately before `rmdir`
returned status 1 and preserved the unexpected entry. A directory disappearing just before
removal also returned status 1, preserving failed-removal semantics. These local checks establish the procedure's
filesystem behavior, not host acceptance or permission to run it on installations.

## Helper cleanup and scheduled backups

Host commands use a checksum-verified, short-lived helper; the firewall adapter alone loads the
trusted Ubuntu-packaged UFW library. Other operations remain standard-library-only; no resident agent
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

## Provision a supported host

After reviewing a dry run:

```sh
./ops/taskman provision production
```

Provisioning presents a redacted plan and requires ordinary confirmation; `--yes` supplies
that confirmation only. Downgrade/unknown ordering needs separate acknowledgment, and pending
migrations need an explicit compatibility policy as described under deployment below.
Provisioning converges packages, unattended security updates without automatic reboot, managed
accounts/directories/configuration/units, Caddy and scheduled backups. UFW activation protects
the active SSH path; Caddy follows the ownership branches below; PostgreSQL follows the resource
and configuration ownership rules below.

Provisioning checks installed packages for the baseline and the applicable Caddy/PostgreSQL
installation branches before refreshing APT indexes. When all required packages are installed,
ordinary baseline convergence skips `apt-get update`. Missing required packages permit an index
refresh and normal authenticated installation; repository or installation failures still stop
provisioning. Caddy installation retains its signed-source refresh after repository setup and its
bound package inspection/install path. Existing Caddy and PostgreSQL ownership rules continue to
exclude their packages from installation or upgrade. Repositories are never disabled to make
convergence pass, and no unauthenticated or alternative-download fallback is used.

Secret installation stays outside logged pyinfra commands. See
[provisioning admission](../specs/2026-09-09-dedicated-host-deployment-design.md#provisioning-and-admission).

### PostgreSQL ownership and access

On an existing cluster, default provisioning manages only the configured Taskman role, database
and protected credentials. It preserves native HBA/settings, PostgreSQL packages and service state,
and all other clusters/resources, including on reruns of a cluster originally bootstrapped by
Taskman. Only proven installation absence permits the dedicated package/bootstrap path. A down,
ambiguous, missing requested-track or non-loopback selected cluster refuses; provisioning does
not start or repair it automatically.

Selection binds the selected version/name/data directory/native paths and configured port to
live administrator and listener identity. Administration uses peer access through
`/var/run/postgresql` at that port. Application observations, backup size/dump and restore
application queries require a native SCRAM exchange (`require_auth=scram-sha-256`), with a
PostgreSQL client version 16 or later. A trust or plaintext-password connection is insufficient.
An HBA `md5` rule can suffice when PostgreSQL actually negotiates SCRAM with a SCRAM verifier.
Runtime Phoenix readiness is verified separately; this libpq option is not a Postgrex setting.

The redacted preview names the selected endpoint, create/adopt resource actions and separate
`access=ready` or `access=unproven` evidence. Catalog/schema readiness does not prove application
access. Dry-run remains planned and performs no writes; missing-resource access can be proved
only after creation. Failed access may therefore retain valid resources and private credentials.
It stops before release preparation, migrations or successful history publication and never
returns Caddy's pending-success exit 13.

A compatible role-only installation is adopted without password rotation and may create the
missing owned database after confirmation. Contradictory privileges, memberships, owner,
protected credentials or migration provenance refuse. A compatible precreated empty database
can admit planned creation of the missing Taskman OS account only with validated provenance;
matching names alone do not grant adoption authority.

### PostgreSQL operator setup and retry

Exit `5 / postgresql` identifies unavailable native inspection, unsupported client or failed
SCRAM application proof. Exit `10 / postgresql` identifies selected-target, listener, resource,
recovery-file or confirmation safety refusal. Human and JSON output include configured
host/port/role/database, selected cluster and observed HBA path when known, the failed check,
known or possible mutation, retained-resource facts and the exact retry command.
Lost replies report uncertainty rather than claiming successful resource creation or unchanged state.

For example, a failed proof after role/database creation retains those resources and credentials;
no migration runs. Inspect the reported HBA and independently protected credentials. Access
configuration or the existing password/verifier may need correction; provisioning never diagnoses
the cause from native stderr or rotates an adopted password automatically. For an environment
named `production`, the reported retry is:

```sh
./ops/taskman provision production
```

An illustrative effective HBA rule is below; use the validated environment's exact identifiers
and address (`::1/128` for IPv6), rather than copying these example names into production:

```text
host    taskman_prod    taskman    127.0.0.1/32    scram-sha-256
```

Broader existing coverage may already suffice. PostgreSQL uses the first matching rule and does
not fall through after authentication failure. Place effective coverage before conflicting
matching rules, inspect `pg_hba_file_rules` for native parser errors, then reload only the selected
cluster after separately authorized operator changes. Listener/port changes may require a
coordinated restart. Rediscovery/reconfirmation on retry adopts compatible retained resources
and continues only after a fresh successful application proof.

### Explicit PostgreSQL configuration override and recovery

`--force-postgresql-config` on `provision` deliberately converges the selected existing cluster's
native HBA and `listen_addresses`, `port`, `password_encryption` and native HBA-path setting.
It can remove authentication coverage for other applications and restart that cluster, interrupting
all its consumers. Arrange a coordinated maintenance window and review the displayed exact
replacement policy, settings and recovery scope before authorizing execution:

```sh
./ops/taskman provision production --force-postgresql-config --dry-run
./ops/taskman provision production --force-postgresql-config
```

`--yes` never implies this flag. The override performs no existing-cluster package install/upgrade,
password rotation, foreign-resource changes or data reset. Identity, loopback, resource, provenance
and confirmation safety checks still apply; it is not a generic remedy for an unknown failure.
Other clusters remain outside its scope. Exact native no-op convergence writes/restarts nothing
and creates no recovery run directory.

Before an actual existing configuration change, both originals and their uid/gid/mode/digests
are retained under `/etc/taskman/postgresql-config-recovery/<random-uuid>/`. The root-owned parent
and run directory are mode `0700`; `postgresql.conf`, `pg_hba.conf` and `metadata.json` are mode
`0600`. These copies may contain operator secrets: keep them private. A failed capture stops
before native modification. Copies remain on success and downstream failure and their known
location survives result/transport errors; uncertain replies retain possible mutation.

The native HBA path also uses the scoped `pg_hba.conf.taskman-backup` directory beside HBA,
containing `pg_hba.conf` and metadata. It restores that HBA's bytes/metadata on parser failure;
this does not restore all settings or running service state. A leftover native recovery directory
blocks another attempt until manually reconciled. Later restart/verification failure retains it.
The brief installation-to-validation crash/power-loss window remains an accepted inspection boundary.

Never infer restored runtime from restored files. Configuration restoration, reload/restart and
cleanup of either recovery location require separate operator authorization. There is no automatic
whole-configuration rollback, pruning or overwrite of protected recovery runs. Review native
identity and retained evidence before manual recovery; default shared provisioning captures and
changes none of these files. The [shared PostgreSQL design](../specs/2026-10-07-shared-postgresql-design.md)
owns the accepted ownership, SCRAM and recovery contract.

The transient helper reconciles the desired first release from validated resources, records and
live migrations. Before the first durable successful selection, a rerun may retry or replace the
target while preserving compatible partial state. Missing managed resources can be created after
confirmation; conflicting present resources refuse. Resource inspection and confirmed convergence
do not adopt an unknown installation or invent provenance for applied migrations.

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

### Caddy ownership and provisioning outcomes

Taskman manages `/etc/caddy/sites-enabled/taskman.caddy` as root:root:0644. A newly created
`sites-enabled` directory is root:root:0755; safe existing directory metadata is preserved.
The site contains only the configured hostname and loopback reverse proxy. The preview identifies
current/desired site digests, metadata, branch, import predicate, package/service consequences,
conditional validation/reload, recovery scope and manual activation requirements. Confirmation
covers those facts; relevant authority/import drift requires a new preview (exit 10).

| Observed Caddy state | Provisioning boundary |
| --- | --- |
| Trusted installed Caddy, including a dedicated-host rerun or interrupted bootstrap | Manage only Taskman's site. Preserve main configuration, other sites, package/source state and service masks/enablement. Only an active, unmasked service with a recognized import permits conditional reload. |
| Package, executable, underlying units and configuration positively absent; public ports free; no existing masks | Install from the signed source under persistent service masks, publish the initial main import/site, validate as the Caddy user, remove only invocation-created masks, start/enable `caddy.service`, then require full deployment verification. Keep `caddy-api.service` disabled. |
| Caddy absent but a safe `/etc/caddy` directory exists, including an absent main | Install while preserving existing configuration/directory metadata and keeping masks/service inactive. A package-created default where main was absent does not authorize bootstrap. Application setup may pass with proxy activation pending. |

Missing-package installation first masks `caddy.service` and `caddy-api.service` persistently,
preserving pre-existing masks. The helper trusts the authenticated upstream stable source, checks
package identity and binds the selected version/checksum within each run. It installs from the
private cache without downloads, recommendations or package removals, retaining `--force-confold`.
It derives an empty-bootstrap default from that archive rather than a permanent release allowlist.
The [package contract](../specs/2026-10-01-shared-caddy-design.md#authenticated-stable-package-selection)
defines bounds and source-trust limits; missing dependencies need operator resolution.
For preserved configuration it protects bounded main bytes/metadata or absence and checks
preservation before site publication. Package/preservation failure is exit 8: retain masks and
protected evidence for operator repair, without automatic package removal or main restoration.
Interruption after installation returns through site-only ownership on the next invocation;
manual main/import/mask completion may be needed.

Existing `/etc/caddy/Caddyfile` stays operator-owned, including safe root:caddy:0640 files.
The helper reads only this regular file within 64 KiB to recognize the exact absolute or relative
`sites-enabled/*.caddy` import, optionally double-quoted with spaces/tabs and a trailing comment.
The [literal predicate](../specs/2026-10-01-shared-caddy-design.md#supported-literal-forms) does not
interpret nesting, expand includes, adapt JSON or query admin configuration. Failed/oversized
inspection is exit 5, never missing-import evidence. A match proves neither valid syntax nor the
loaded configuration; native validation and full public verification remain required.

Existing Caddy without a recognized import, or with an inactive/masked service, permits application
preparation. Exit `13`, stage `application-provisioned-proxy-pending`, JSON schema 1
`status: "failed"`, `proxy_activation: "pending"` and `deployment_verified: false` mean Taskman is
ready on the deployment host while public completion is pending. The five-check application report
proves service/release/topology/journal/loopback readiness; it excludes Caddy/public HTTPS/HSTS.
Actual application, validation, activation, transport or publication failures retain their own
statuses and never become 13. Pending can be a no-op with `changed: false`.

Preparation publishes no new successful selection and retains recovery protections. Until the
first successful selection, `deploy` refuses and scheduled backup runs may remain unavailable even
though their package/timer is installed. Existing successful history on an exact completed replay
is retained. Verify backup operation after activation; a pending result does not prove a healthy
backup schedule. `verify` is read-only and cannot finalize the first installation.

### Activate a pending Taskman site

These are operator directions, not additional authority for an agent to edit a host. Coordinate a
quiet proxy activation window with every operator, watcher and automated reloader. Taskman's
lifecycle lock does not provide a host-wide Caddy lock. Keep unrelated unapplied disk changes out
of the window: publishing the site makes it eligible for another reload/restart before Taskman's
own activation.

1. Inspect the reported site path, import predicate, masks/service state and exact candidate identity.
   If the recognized import is absent, add this line once at the **top level** of
   `/etc/caddy/Caddyfile`, outside site blocks:

   ```caddyfile
   import /etc/caddy/sites-enabled/*.caddy
   ```

   If the configured Taskman hostname already has a block, reconcile that old block before
   importing the reserved site to avoid duplicate hostname definitions. A recognized existing
   import needs no second import. Alternate main locations or service/admin policies require the
   operator's equivalent procedure.
2. Validate the complete main configuration as its service user:

   ```sh
   sudo -u caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

   Resolve validation failure before activation. Review diagnostics privately; automated native
   validation/package/reload stdout and stderr are suppressed because configuration can be private.
3. For an active, unmasked standard service, reload after validation:

   ```sh
   sudo systemctl reload caddy
   ```

   For an inactive service, deliberately start and enable it after validation; if masked, explicitly
   authorize removing the `caddy.service` mask first:

   ```sh
   sudo systemctl unmask caddy.service
   sudo systemctl start caddy.service
   sudo systemctl enable caddy.service
   ```

   Run `unmask` only for the masked case. Preserve other service masks; keep `caddy-api.service`
   disabled. Provisioning never starts/enables/unmasks an existing Caddy service.
4. Rerun provisioning with the **same exact retained artifact** or unchanged source/build identity,
   with a new reviewed preview and ordinary confirmation:

   ```sh
   ./ops/taskman provision production --artifact /secure/artifacts/taskman-RELEASE.tar.gz --dry-run
   ./ops/taskman provision production --artifact /secure/artifacts/taskman-RELEASE.tar.gz
   ```

   Retain its adjacent manifest/checksum. A different clean checkout selects a new target and needs
   ordinary confirmation/migration policy; it is not exact replay. Once active with the recognized
   import, provisioning validates and reloads once if the site changed or its advisory public probe
   is not ready. An unchanged healthy site skips reload. Manual validation/reload before rerun is
   supported. Full eight-check verification and durable history publication still gate exit 0.
5. After full completion, use `verify` and a validated backup to establish public/backup operation;
   subsequent release changes use `deploy`. Public HTTPS/HSTS failure after reload remains a real
   verification failure, not completion or missing-import pending.

### Caddy site recovery and runtime uncertainty

With a recognized import, the helper validates the current whole configuration as Caddy before a
site change, protects the original Taskman site bytes/metadata or absence, publishes atomically,
and validates the actual imported candidate. Before automatic reload it refreshes validation and
site/service/import authority. Candidate-validation failure restores only Taskman's original site
(or removes the exact newly created site), without reload. Reload failure attempts that same
site-only disk restoration, validates the restored whole configuration and checks service activity.
New containing directories remain; main/other sites/certificates/Caddy data are never rolled back.
Root-only recovery evidence remains under `/etc/taskman/caddy-recovery/` for inspection, without
automatic garbage collection.

Restoration is a mutation and cannot make `changed` false. A lost reload reply or failed recovery
leaves runtime uncertainty: restored disk bytes do not prove which configuration Caddy loaded.
Retain the primary failure and recovery facts, inspect actual service/runtime state and establish
quiescence before a new preview. Do not restart or blindly repeat reload as automatic recovery.
Manual main edits belong to the operator and require their own reconciliation if validation/reload
fails. Native routing, ACME, WebSockets/reconnection, restart and package-interruption behavior
require separately authorized disposable-host acceptance; local fixtures cannot establish them.

### UFW ownership, preview and recovery

Provisioning assumes ordinary UFW ownership: rules and generated files are maintained through
UFW, without custom hooks, hidden hand edits or independent packet-filter policy. Inspection
uses UFW's structured saved-rule API, which can omit raw directives; it is not a complete
firewall audit. Provider firewalls and unrelated UDP services remain operator responsibilities.

For each of IPv4 and IPv6, Taskman requires incoming TCP allows for the configured SSH port,
80 and 443, and incoming TCP denies for the configured application, database, distribution and
fixed EPMD port 4369. Public/private port intersection is invalid before SSH. Missing fallback
rules are appended in that order, IPv4 then IPv6 for each port, only to uncovered families.
Existing rules, comments, saved profile associations and
relative order are preserved. Complete active coverage is a no-op. Equivalent unrestricted
ranges, multiport rules and saved application-profile expansions can satisfy coverage.

Supported saved forms are incoming/outgoing host rules with allow, deny, reject or limit;
TCP, UDP or omitted protocol; numeric ports, ranges, comma-separated sets and omitted ports;
literal IP/CIDR or any; and supported interface, source-port, logging and comment fields.
Service names must resolve unambiguously through the host's service database. Profile rules
use saved numeric expansion rather than a changed profile definition. Routing rules, unknown
or malformed exposed forms, unresolved predicates and reader warnings refuse convergence.

Proper-subset source IP/CIDR rules remain operator-owned exceptions, including private-port
permits and public denies/rejects/limits. Their existing precedence remains effective; appended
fallbacks do not override them. Several exceptions are not combined into an all-source rule.
Any and explicit IPv4/IPv6 `/0` sources are all-source: incoming TCP allow/limit overlap with a
private port, or deny/reject/limit overlap with a public port, refuses even with destination,
source-port or interface restrictions. Interface restriction alone is not a source exception.

Persisted defaults must be incoming DROP, outgoing ACCEPT, forwarding DROP or REJECT, with
`IPV6=yes`. They are preserved; provisioning never changes defaults or enables IPv6 settings.
Inactive UFW with compatible saved rules is supported: enabling it makes every preserved saved
rule effective. The material preview, including `--dry-run`, identifies that consequence,
required additions, accepted exceptions, defaults and authenticated controller addresses.

The controller derives its SSH source/destination from the authenticated remote connection.
A possibly matching deny/reject/limit before a definite SSH allow refuses; uncertain interface
or future source-port predicates cannot establish a safe allow. Material policy or controller
address drift after confirmation requires a new invocation and preview. Counters and ephemeral
source ports do not change authority. For initially absent UFW, approved native installation
is followed by helper validation of actual defaults, saved rules and administrator access. The
installation preview authorizes preserving compatible saved policy, including source exceptions,
and activating it if inactive; shown fallback commands are the maximum additions, and existing
coverage can reduce them. Convergence binds the actual inspected snapshot with unchanged
controller addresses. Conflicting or unreadable state, a still-missing package or changed
controller identity refuses. This installation exception preserves the ordinary installed-policy
drift check.

The transient firewall helper runs with `/usr/bin/python3 -I -S` and lazily loads only the trusted
root-owned Ubuntu UFW package at `/usr/lib/python3/dist-packages/ufw`. An installed but unreadable
or unsupported package refuses; do not substitute pip or vendored code. Exact unprivileged
package/interpreter evidence and remaining privileged checks are recorded in the
[UFW API evaluation](../research/2026-10-01-ufw-python-api-evaluation.md). Local fixtures and builds
do not establish privileged initialization, activation or remote lockout acceptance.

After additions or enablement, provisioning requires a fresh strict pinned-host-key SSH
connection. It proves controller connectivity, not complete private-port reachability or a
whole-firewall audit. If a native command, result, postcheck or reconnect fails, provisioning
stops and retains known or possible changes, including earlier baseline changes. No automatic
reset, deletion, disablement or policy rollback occurs. Inspect retained UFW rules/defaults and
partial resources before retrying through a new preview. If SSH is lost, use operator console
recovery, restore the intended access path and verify the pinned host identity before retrying;
console access is not supplied by the controller. See
[SSH lockout recovery through the provider console](#ssh-lockout-recovery-through-the-provider-console).

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

Use `restore-required` when the prior release cannot use the migrated database. Deploy takes a
protected pre-migration backup and applies the forward migrations; it does not restore automatically.
Returning to that prior release requires restoring the matching backup and loses later writes.
The declaration is a human compatibility decision. The target must cover every applied migration
with consistent fingerprints. Partial-prefix provenance can support a safety backup without
making that backup automatically restorable; unsupported or contradictory schema refuses.
Rollback independently requires the immediately preceding successful selection and exact live
migration compatibility. It never reverses migrations. To return to a release that is incompatible
with a forward-only migration, restore the matching pre-migration database backup with that release.
Do not recreate removed fields with placeholder data.

Deployment stages or reuses immutable target content, refreshes compatible scheduled-backup code
under the lifecycle lock, creates required protected safety backups, stops Taskman, applies
remaining forward migrations, selects atomically, starts and verifies, then publishes success.
Caddy remains running. Deploy does not upgrade host packages, change UFW, rewrite PostgreSQL
configuration or reconverge Caddy. Required recovery backups survive interrupted attempts and
later target replacement. Pruning names exact eligible intermediate backups in the confirmed plan.

Manual adoption is not part of replayable deployment. `deploy` requires a current release proven
by completed Taskman records and successful history. Before the first successful selection,
`provision` admits validated compatible partial resources, including an existing trusted Caddy
installation; conflicting or unavailable authority still refuses. The retained
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
| `4` | SOPS/age settings or credential decryption, secret validation, or secret installation failure |
| `5` | SSH, host-key, privilege, or remote preflight failure |
| `6` | Database backup or backup-validation failure |
| `7` | Migration failure |
| `8` | Release staging, selection, service lifecycle, or Caddy package/validation/activation failure |
| `9` | Post-start readiness or public verification failure |
| `10` | Safety refusal, state conflict, or incompatible rollback |
| `11` | Database restore or restored-database validation failure |
| `12` | A lifecycle-lock acquisition was unavailable; this is not whole-command conflict detection |
| `13` | Application setup passed on the deployment host; proxy activation/public verification remains pending |

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
readiness and HSTS within independent bounded phases. Full verification has a 185-second wait
envelope; application proof has 160 seconds, including command cleanup allowances under responsive
local I/O and normal scheduling. These bounds cover verification, not the entire deployment.
Unsafe topology fails immediately; see the
[phase timing contract](../specs/2026-09-18-operations-contracts.md#verification-phases-have-independent-finite-limits).
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
| Proxy activation pending (13) | Follow the [operator activation and exact-target retry procedure](#activate-a-pending-taskman-site); retain protections/history and verify backups after full completion. |
| Caddy validation/reload failed or its reply was lost | Preserve primary failure and site recovery evidence; inspect runtime uncertainty and coordinate a new quiet window before retry. |
| Public HTTPS failed after local readiness | Preserve the selected healthy local release; repair DNS/provider firewall/Caddy/ACME without exposing Phoenix directly. |
| Lock is held | Wait for the other controller command or scheduled backup to finish. The lock file contains no operation identity; do not remove it merely because the path exists. |
| Metadata is contradictory | Stop mutation. Reconcile managed records and exact paths before retrying. |
| Rollback is incompatible | Keep current code/database and use a validated backup through `restore`. |
| Restore failed before swap | The canonical database remains authoritative. Rerun the same backup request only when the completed facts and deterministic temporary database are recognizable. |
| Restore failed during/after swap | Keep Taskman stopped and retain all database names plus the pre-restore backup. A recognized deterministic arrangement can converge on rerun; any other arrangement requires manual inspection. |
| Cleanup revalidation raced | No newly protected target is removed; recompute and reconfirm the exact plan. |

## SSH lockout recovery through the provider console

A guest firewall or broken SSH configuration does not prevent provider console access. Recovery
requires operator access to the correct provider project/server and permission to restart it.
A missing installed-system root password is not itself a blocker: obtain a console login through
password reset, a temporary GRUB root shell or a separate rescue OS. These are manual emergency
procedures, not controller rollback. Host-specific downtime tolerance and recovery evidence belong in private operator storage;
the [inventory privacy policy](../inventories/operations-environments.md) defines that boundary.
An SSH pin is the expected fingerprint of the server's host key, used to authenticate the server
on a new connection. Retain the installed server's trusted pin through recovery; a rescue OS
may present its own different host key and must be verified separately.


### Get a root console login

Choose one route appropriate to the failure:

1. **Hetzner password reset:** in Hetzner Console select the correct server, then **Rescue → Reset
   Root Password**. If this succeeds, use the resulting password for the provider console login.
   If unavailable or unsuccessful, use one of the boot-based routes below. Changing the local
   root password does not require enabling SSH password authentication.
2. **Temporary GRUB root shell:** open the provider console before restarting. Reveal GRUB with
   Esc or Shift as appropriate, select the installed kernel and press `e`. Preserve its root-disk
   and other arguments; append `init=/bin/bash` to the `linux` line. Boot with Ctrl-X or F10.
   This one-boot edit starts a root shell instead of the normal init/login system, so no existing
   root password is needed. Remount the installed root writable and set a password for subsequent
   console login:

   ```sh
   mount -o remount,rw /
   passwd root
   sync
   exec /sbin/init
   ```

   Let normal startup finish, then log in through the console with the new password. Do not
   persist `init=/bin/bash` in GRUB configuration. A protected bootloader needs its credentials;
   encrypted disks still need their unlock material. If GRUB or the installed OS cannot start,
   use Rescue instead.
3. **Hetzner Rescue:** select **Rescue → Enable rescue**, then restart the server, or select
   **Enable rescue & power cycle**. Rescue supplies separate root credentials or a selected SSH
   key; the installed OS root password and guest firewall are not used. Use the provider console
   if rescue SSH is unavailable. For rescue SSH, verify its different host key out of band and use
   a separate temporary known-hosts file; preserve the installed host's pin.

   Identify the actual installed root filesystem using `lsblk -f`; do not assume `/dev/sda1`.
   Unlock encryption/activate storage if needed. Mount that filesystem at `/mnt`, then follow
   Hetzner's chroot preparation:

   ```sh
   mount /dev/ACTUAL_ROOT_DEVICE /mnt
   chroot-prepare /mnt
   chroot /mnt
   passwd root
   exit
   sync
   reboot
   ```

   `ACTUAL_ROOT_DEVICE` is a substitution, not a runnable device name. Reboot back to the local
   disk with Rescue disabled and log in through the console. Never rebuild/reinstall the server
   to repair an access problem; that is a separate destructive action. Commands in a chroot act
   on installed files, not the installed system's running services or firewall.

### Repair and verify access

From a root console in the normally running installed OS, inspect `ss -ltnp`, `sshd -t`,
`systemctl status ssh.socket ssh.service --no-pager` and `ufw status numbered`. Check provider
firewall rules separately; guest repair cannot override a provider-level block. Preserve a protected
copy of the affected configuration before repair. Correct the specific SSH setting or blocking
UFW rule; verify current rule numbering before any deletion and preserve unrelated rules.
An appended allow does not override an earlier matching deny.
After SSH configuration repair, rerun `sshd -t` and apply the validated changes while retaining
console access. Reload/restart the applicable SSH service; for socket-activated listening changes,
inspect the effective socket unit/generator, reload systemd configuration and restart the applicable
socket/service units. A service reload alone may not change the socket listener. Confirm the actual
listening address/port with `ss -ltnp` before testing a new connection.

If urgently necessary, the operator can temporarily run `ufw disable` from this normal-OS console
to regain access. This preserves saved rules but removes guest UFW protection for every hosted
service; use it only as an explicit emergency measure. Do not use `ufw reset`. Repair the intended
policy, then re-enable it from the console and test a genuinely new pinned SSH connection before
closing console access. Do not restore an old complete policy blindly if unrelated rules changed
since the recovery copy. Validate the installed host identity against its original pin, not Rescue's,
and verify existing services. Return temporary authentication changes to the intended policy after
access is proven. Preserve failure receipts and obtain a new controller preview before retrying.

Sources: [Hetzner password reset](https://docs.hetzner.com/cloud/servers/how-to-rescue/reset-password/),
[Hetzner Rescue](https://docs.hetzner.com/cloud/servers/getting-started/rescue-system/),
[Ubuntu password recovery](https://help.ubuntu.com/community/LostPassword), and
[Ubuntu UFW manual](https://manpages.ubuntu.com/manpages/resolute/man8/ufw.8.html).

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
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
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

The [native acceptance guide](native-acceptance.md) defines portable scenarios and evidence limits.
Actual commissioning results, inventories and recovery custody are private operator records.
Refresh current state for every affected installation; never reuse archived passwords, signed links,
bootstrap material or a historical approval. Preserve protected recovery until separately authorized
disposal. This runbook grants no reset, deletion or native-operation authority.

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

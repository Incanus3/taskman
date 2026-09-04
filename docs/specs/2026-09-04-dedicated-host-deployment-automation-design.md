# Dedicated-host deployment automation

**Status:** Approved in conversation; awaiting written-specification review  
**Date:** 2026-09-04

## Summary

Taskman already supports production operation as an immutable OTP release under systemd, with
Phoenix bound to loopback behind public HTTPS in Caddy. The repository also contains a manual
deployment runbook. This design adds two related, workstation-driven workflows without changing
that runtime topology:

1. deploy a new Taskman release to a compatible host already running an older release; and
2. provision a clean SSH-accessible Ubuntu VPS through a complete web-accessible Taskman
   installation.

The automation uses pyinfra over SSH, SOPS with age for encrypted repository configuration, and a
pinned Ubuntu build container for target-compatible OTP releases. Provisioning and deployment are
separate commands with different scopes, but they share focused Python capabilities for host state,
services, releases, backups, and verification.

The first version supports one host running Ubuntu 26.04 LTS on amd64, including Intel 64/EM64T and
AMD64/x86-64 processors. It deliberately rejects other operating systems, Ubuntu releases, and CPU
architectures.

## Context and current state

The current hosted-access implementation provides:

- production runtime validation in `config/runtime.exs`;
- Phoenix release commands for server startup, migrations, and interactive administrator creation;
- an immutable release layout rooted at `/opt/taskman/releases/<release-id>`;
- `/opt/taskman/current` as the atomically selected release;
- a hardened `taskman.service` running as the unprivileged `taskman` account;
- Caddy terminating public HTTPS and proxying to Phoenix at `127.0.0.1:4000`;
- local-only Erlang distribution for break-glass release inspection;
- a root-owned `/etc/taskman/taskman.env` secret file; and
- a manual build, installation, verification, backup, and rollback runbook in
  `docs/deployment.md`.

The accepted hosted-access specification explicitly left host provisioning, DNS, Caddy and
PostgreSQL installation, backups, and actual deployment to the operator. This design automates the
host and application portions of that boundary. It does not replace the accepted OTP
release/systemd/Caddy topology with containers or a platform service.

At design time, the active workspace also contains unrelated application work. Implementation must
preserve unrelated changes and use the repository-aware version-control workflow.

## Goals

### Existing-host release deployment

Provide one operator command that can:

- build or accept a verified target-compatible release artifact;
- discover and validate the installed host state;
- prevent concurrent lifecycle operations;
- upload and install a new immutable release without mutating an existing release;
- create and validate a database backup before every activation;
- make migration compatibility and rollback consequences explicit;
- stop Taskman for the migration and activation window;
- activate the release atomically;
- verify local and public service health; and
- preserve enough state for an explicit, safe rollback or restore decision.

### Clean-host provisioning

Provide one operator workflow that can start with an SSH-accessible Ubuntu 26.04 LTS amd64 VPS and
converge:

- the bounded host baseline needed by Taskman;
- firewall rules that preserve configured SSH access before enabling public HTTP and HTTPS;
- unattended security updates without automatic reboot;
- a local PostgreSQL service, Taskman role, and Taskman database;
- scheduled local PostgreSQL backups with bounded retention;
- Caddy from its authenticated package source;
- the Taskman service account, directories, systemd unit, runtime configuration, and first release;
- public HTTPS and Taskman readiness; and
- an interactive first-administrator creation step whose password never enters automation data.

Provisioning must be safe to rerun. A second run against an already-converged host should produce no
host changes except explicitly procedural verification.

### Operator control

The workflows must keep the operator in control of external and destructive actions. They report a
redacted plan before mutation, require confirmation for consequential workflows, use explicit
recovery commands, and never treat successful verification as authorization to deploy a real host.

## Non-goals

The first version does not:

- create, resize, replace, snapshot, or delete a VPS;
- manage provider firewall resources;
- publish or change DNS records;
- create or configure a Resend account, domain, or API key;
- copy backups off the VPS or provide disaster recovery from total VPS loss;
- support Ubuntu upgrades, other Linux distributions, other Ubuntu releases, or arm64;
- provide zero-downtime, rolling, clustered, or blue/green deployment;
- automatically deploy from CI or after a merge;
- build releases on the production host;
- install a container runtime on the production host;
- rotate existing database or application secrets;
- rewrite or harden the operator's SSH daemon configuration;
- create a generalized infrastructure-as-code abstraction across providers; or
- automatically decide that a database migration is backward compatible.

OpenTofu or provider-specific infrastructure may later create the VPS, DNS, provider firewall, and
off-host backup storage. It must not use remote provisioners as a replacement for this guest
configuration workflow.

## Accepted decisions

- **Automation engine:** pyinfra 3.x, pinned through a locked Python environment.
- **Secret storage:** SOPS-encrypted YAML using age recipients.
- **Control point:** a developer workstation initiates builds and deployments.
- **Build environment:** a pinned Ubuntu 26.04 amd64 container produces a bare-metal OTP release.
- **Runtime:** the existing OTP release, systemd, loopback Phoenix, and Caddy topology.
- **Initial host:** Ubuntu 26.04 LTS amd64 only.
- **Database:** local PostgreSQL, bound to loopback and managed by the provisioning workflow.
- **Backups:** validated local custom-format dumps with scheduled retention; off-host copying remains
  the operator's responsibility.
- **Host firewall:** UFW, with the active configured SSH port allowed before enablement.
- **Availability:** a brief maintenance window is acceptable.
- **Rollback:** always explicit and gated by the declared migration compatibility.
- **Host administration:** the existing trusted SSH administrator uses `sudo`; the `taskman`
  service account receives no login shell or broad deployment privileges.

## Architecture

### Shared capabilities, separate workflows

Provisioning and deployment use one small repository-owned Python package, but expose distinct
workflow entry points:

- `provision` may converge the complete supported host;
- `deploy` may only inspect prerequisites and change release, backup, and service state;
- `verify` is read-only;
- `releases` lists installed releases and their rollback eligibility;
- `backups` lists retained database backups and their restore metadata;
- `backup` creates a new dump without changing the application;
- `rollback` selects a previously installed release only when database compatibility permits it;
- `restore` replaces database state through a separately confirmed guarded workflow;
- `create-admin` opens the existing interactive release command through an SSH TTY; and
- `cleanup` removes only specifically eligible old tool-owned artifacts.

Routine application deployment must not opportunistically upgrade packages, rewrite the firewall,
alter PostgreSQL configuration, or converge the rest of the host. This keeps its privilege and
failure surface bounded.

### Capability boundaries

The Python package has five responsibilities:

1. **Configuration and secrets**
   - Parse and validate non-secret environment configuration.
   - Decrypt SOPS data into controller memory.
   - Redact all structured output and exceptions.
   - Validate cross-field invariants before connecting.

2. **Build and artifact metadata**
   - Validate a clean, identified source revision.
   - Invoke the pinned build container.
   - Create the release archive and detached metadata.
   - Fingerprint included migrations.
   - Verify an existing artifact before allowing reuse.

3. **Host and service convergence**
   - Gather immutable host facts and reject unsupported targets.
   - Converge packages, accounts, directories, firewall, PostgreSQL, Caddy, systemd, and backups.
   - Use declarative pyinfra operations where the desired state is stable.

4. **Release transactions**
   - Implement ordered, fail-fast operations for locking, staging, backup, stop, migration,
     activation, restart, verification, rollback, and cleanup.
   - Use imperative operations only where order and one-time execution are intrinsic.

5. **Verification and reporting**
   - Check Taskman, PostgreSQL, Caddy, systemd, listeners, HTTPS, and release identity.
   - Return stable exit categories and a redacted final report.

These units must communicate through typed Python data rather than global mutable configuration.
Shell commands are constructed from validated values and passed as argument arrays where the
underlying API permits. Values used in remote paths, unit names, database identifiers, and release
identifiers must satisfy narrow allowlists before interpolation.

### pyinfra execution model

pyinfra evaluates deploy definitions during a prepare phase and executes generated operations
later. Code must not branch on mutable facts gathered before earlier operations have converged the
host. Declarative operations should always express the desired final state; execution-time
conditions use pyinfra's supported operation conditions. Release transactions whose decisions
depend on immediately preceding results are coordinated by the application wrapper rather than
encoded as misleading prepare-time branches.

## Operator interface

The repository exposes one launcher:

```text
./ops/taskman build
./ops/taskman provision production
./ops/taskman deploy production
./ops/taskman verify production
./ops/taskman releases production
./ops/taskman backups production
./ops/taskman backup production
./ops/taskman rollback production <release-id>
./ops/taskman restore production <backup-id>
./ops/taskman create-admin production
./ops/taskman cleanup production
```

`provision` and `deploy` build the current clean source revision by default. Both accept
`--artifact <archive>` to reuse a previously built artifact. A failed operation reports the exact
artifact path so retrying does not silently build different code.

`build` never contacts a server. `verify`, `releases`, and `backups` perform no remote mutation.
`--dry-run` validates local inputs, decryptability, artifact metadata, SSH connectivity, remote
facts, and the planned operations without remote mutation. When no artifact was supplied, a dry
run may create a local artifact because the artifact is required for complete compatibility and
migration planning.

Mutating commands display a redacted summary containing:

- environment and exact SSH destination;
- public hostname;
- current and candidate release identifiers;
- source revision and artifact checksum;
- whether new migrations are present;
- declared rollback policy;
- planned backup;
- services affected; and
- expected maintenance window.

`provision`, `deploy`, and `rollback` require an ordinary interactive confirmation. `restore` and
`cleanup` require typed confirmation that includes the environment and exact target identifier.
There is no generic `--force` switch. A future non-interactive mode requires a separate accepted
design for CI identity, approvals, credentials, and audit behavior.

### Exit categories

The launcher returns:

| Status | Meaning |
| --- | --- |
| `0` | success, including an already-satisfied no-op |
| `2` | invalid command, argument, configuration, or unsupported target |
| `3` | missing local prerequisite or release build failure |
| `4` | SOPS/age decryption, secret validation, or secret-installation failure |
| `5` | SSH connection, host-key, privilege, or remote preflight failure |
| `6` | database backup or backup-validation failure |
| `7` | migration failure |
| `8` | release staging, activation, or systemd lifecycle failure |
| `9` | post-start readiness or public verification failure |
| `10` | safety refusal, state conflict, or incompatible rollback |
| `11` | database restore or restored-database validation failure |
| `12` | another lifecycle operation holds the deployment lock |

Errors identify the failed stage, what state is known to have changed, whether Taskman is running,
the selected release, the backup created, and the next safe command. They do not include secret
values, decrypted configuration, database URLs, cookies, raw environment output, or command lines
containing credentials.

### Release and backup discovery

Operators must not inspect server directories or infer identifiers from filenames before invoking
rollback or restore.

`releases <environment>` reads validated installed manifests and root-owned deployment records. It
sorts releases by recorded activation time, with an explicitly adopted manual release placed
according to its recorded adoption state. Human-readable output includes:

- the exact release identifier accepted by `rollback`;
- current, immediately previous, or inactive status;
- application version and source revision, using `unknown` for unavailable adopted metadata;
- Ubuntu target, architecture, and OTP version;
- installation and most recent activation time;
- artifact checksum prefix;
- incoming migration declaration;
- rollback eligibility from the current release; and
- a concise reason when rollback would require database restore.

`backups <environment>` reads validated backup metadata and confirms that each referenced dump
still exists as a regular file under the configured backup root. Human-readable output includes:

- the exact backup identifier accepted by `restore`;
- creation time;
- dump size;
- database name;
- associated current and candidate release identifiers;
- reason, such as scheduled, pre-deploy, pre-rollback, or pre-restore;
- `pg_restore --list` validation recorded at creation; and
- whether the file is present or its metadata is stale.

Both commands acquire a shared lifecycle lock so they cannot observe a half-written activation,
backup, restore, or cleanup record. They never derive authoritative IDs from directory listings.
Unrecognized files and directories are omitted from normal results and summarized as warnings for
operator investigation. List commands do not revalidate every dump body because that may be
expensive; `restore` always performs fresh `pg_restore --list` validation before confirmation or
mutation.

Both commands support `--json` with a versioned, secret-free schema for scripting. Human and JSON
output contain the same records and warnings. An empty valid installation returns status `0` with
an empty result. Invalid or contradictory metadata is a status `10` safety refusal rather than a
best-effort listing.

## Environment configuration

Non-secret configuration lives at:

```text
ops/environments/production.yaml
```

It includes:

- SSH hostname, port, and administrator user;
- expected SSH host-key fingerprint obtained through an out-of-band trusted source;
- public Taskman hostname;
- expected public IPv4 address and optional public IPv6 address;
- expected Ubuntu release and `amd64` architecture;
- application and Erlang distribution ports;
- PostgreSQL database and role names;
- requested Ubuntu PostgreSQL package track, when an explicit track is required;
- local backup directory, schedule, and retention count;
- release retention count; and
- readiness and connection timeouts.

The repository contains an example environment. A real environment file contains no secrets but may
still be environment-sensitive and is added deliberately by the operator.

Encrypted configuration lives at:

```text
ops/environments/production.secrets.sops.yaml
```

It contains:

- the PostgreSQL role password;
- `SECRET_KEY_BASE`;
- the distinct AshAuthentication token-signing secret;
- the Resend API key; and
- any future credential explicitly accepted into the deployment contract.

`PHX_HOST`, `MAIL_FROM`, port, pool size, and other non-secret runtime values remain in the
reviewable environment configuration unless the operator has a concrete reason to encrypt them.
The automation renders the complete systemd environment file from both sources.

`.sops.yaml` records only public age recipients and file-selection rules. Age private identities
remain outside the repository. Changing an age recipient rewraps encrypted data; it does not rotate
Taskman secrets.

Provisioning refuses to replace an existing secret value merely because generated defaults or an
encrypted file are absent. Secret rotation is outside this design and must be an explicit future
workflow.

## Release artifact

### Build

The build container is pinned by immutable image digest and records the Ubuntu, Erlang/OTP, Elixir,
Node, and architecture inputs. It executes the project-approved production sequence:

```text
mix deps.get --only prod
mix compile --warnings-as-errors
mix assets.deploy
mix release --overwrite
```

The exact implementation may cache dependencies locally, but cache contents cannot become an
undeclared input. The production server does not receive source, Mix, Node, Python, pyinfra, SOPS,
age, or the build container.

The first implementation targets `linux/amd64`. `amd64`, `x86_64`, and Intel EM64T describe the
same supported 64-bit x86 target for this purpose.

### Identity and manifest

A release identifier is:

```text
<application-version>-<source-short-sha>-ubuntu26.04-amd64-otp<otp-version>
```

The identifier is a human-readable logical identity for the code and compatible runtime target.
The Ubuntu release and architecture identify native compatibility. The exact OTP version identifies
the bundled Erlang runtime, even though the target host does not install OTP separately. The source
revision also identifies the committed pinned builder definition.

Every component is validated before use in a path. The UTC build timestamp remains provenance in
the manifest rather than part of the logical identifier. The artifact includes a build manifest
with:

- schema version;
- application name and version;
- full source revision;
- release identifier;
- build timestamp;
- target OS and architecture;
- pinned toolchain identifiers;
- migration filenames and content hashes; and
- the expected top-level release layout.

A detached file records the archive SHA-256 checksum. The archive checksum is not embedded inside
the archive. It identifies the exact produced bytes and distinguishes rebuilds of the same logical
release. If an installed release has the same logical identifier but a different checksum,
deployment refuses the ambiguity rather than overwriting it. Deployment records the operator's
migration-compatibility declaration separately because it compares the candidate with the currently
installed release and is not a build fact. Root-owned deployment records live outside immutable
releases under `/opt/taskman/deployments/`.

The release archive contains an Erlang cookie and is handled as a deployment credential. Local and
remote files use restrictive permissions and tool-owned temporary locations. Successful and failed
operations remove their private upload when safe; an unexpected residual path is reported without
broad wildcard deletion.

Bit-for-bit reproducible release archives are not required. The build inputs and manifest must be
reproducible and auditable, and the exact produced artifact is identified by its checksum.

## Clean-host provisioning workflow

### 1. Local validation

Before SSH access, provisioning:

- loads and validates the environment schema;
- verifies required local tools and pinned versions;
- verifies SOPS decryptability and secret invariants;
- verifies that the public hostname is not reserved or local;
- verifies the release artifact or builds one;
- verifies that the artifact targets Ubuntu 26.04 amd64; and
- prints the redacted plan and receives confirmation.

### 2. Remote discovery and refusal checks

Provisioning connects with strict host-key verification and confirms:

- Ubuntu reports release `26.04`;
- the kernel architecture maps to `amd64`/`x86_64`;
- PID 1 is systemd;
- the configured administrator can use the required `sudo` capability;
- disk and memory meet documented minimums;
- the configured SSH port is the active connection port;
- conflicting users, groups, paths, databases, listeners, units, or proxy configuration are absent or
  already match Taskman's managed state; and
- public DNS resolves directly to the VPS address without an unsupported intermediate proxy.

Existing compatible state is adopted only after complete validation. Ambiguous or incompatible
state is a safety refusal, not an invitation to overwrite it.

### 3. Host baseline

The workflow:

- refreshes package metadata;
- installs only the packages required for certificates, repository verification, PostgreSQL,
  backups, firewall management, Caddy, and runtime inspection;
- enables unattended security updates without automatic reboot;
- adds rules for the configured SSH port before enabling the host firewall;
- permits public TCP 80 and 443;
- keeps Phoenix, PostgreSQL, and Erlang distribution ports non-public;
- verifies a fresh SSH connection after firewall convergence; and
- creates the unprivileged `taskman` user and approved root-owned or service-owned directories.

It does not perform a distribution upgrade, enable Ubuntu Pro, change SSH authentication methods,
replace authorized keys, or disable root/password login. Those are host-ownership policies outside
the application provisioner.

### 4. PostgreSQL and backups

PostgreSQL is installed from the accepted Ubuntu 26.04 package source unless the implementation
plan identifies a required authenticated upstream source. It:

- binds only to loopback;
- uses SCRAM authentication for the Taskman TCP role;
- creates the Taskman role and database without exposing the password in a process argument or log;
- grants only the required database authority;
- verifies connection using the rendered application configuration; and
- never places PostgreSQL on a public firewall interface.

The backup capability installs a root-controlled command and systemd timer. A backup:

1. writes a custom-format `pg_dump` to a restrictive temporary file;
2. verifies it with `pg_restore --list`;
3. atomically renames it to a release- or time-labelled final filename; and
4. prunes only recognized backup files exceeding the configured retention count.

Backup paths are outside release directories. Local retention protects against deployment errors
but not VPS loss. The final runbook prominently requires the operator to copy verified backups
off-host.

### 5. Caddy and Taskman service

The workflow:

- installs Caddy from its official authenticated Ubuntu/Debian package source;
- renders the existing minimal direct-host Caddy topology;
- validates Caddy configuration before enablement or reload;
- installs the checked-in Taskman systemd unit;
- creates `/etc/taskman/taskman.env` as `root:root` mode `0600`;
- keeps releases root-managed and runtime state service-owned;
- runs `systemd-analyze verify` on staged unit content when available; and
- selects the first release only through the shared release transaction.

Caddy remains running during later Taskman deployments and returns a temporary upstream failure
during the accepted maintenance window.

### 6. First administrator and acceptance

After automated readiness succeeds, `create-admin` obtains a real SSH TTY and invokes the existing
release command through a constrained `systemd-run` execution as the `taskman` user with the
root-only environment file. The email and password are interactive input. The password never
enters an argument, environment variable, SOPS data, transcript, or automation result.

Provisioning then reports the human acceptance checks:

- sign in over HTTPS;
- invite a controlled address and receive the Resend email;
- complete account setup;
- create and use an API key;
- navigate a LiveView route and verify its WebSocket remains connected; and
- copy a verified local backup off-host.

### Provisioning failure behavior

Before the first release transaction, a failure leaves a partially converged but safely rerunnable
host. Declarative operations repair or complete their owned state on the next run. The workflow
does not attempt to roll back installed packages, remove a compatible database, disable the
firewall, or delete generated secrets.

Once the first release transaction begins, release and database failures use the same behavior as a
normal deployment.

## Existing-host deployment workflow

### 1. Build and identify

Deployment refuses a dirty or unidentified source checkout when building the current revision. It
builds inside the pinned container, verifies the resulting layout, generates the manifest and
checksum, and retains the artifact for exact retries.

`--artifact` skips the build only after the artifact, manifest, checksum, target, and source
identity have passed validation.

### 2. Lock and preflight

All lifecycle-changing commands acquire one root-owned host lock. Lock contention returns status
`12` and identifies the other operation without waiting indefinitely.

Deployment verifies:

- the supported host platform and privilege boundary;
- a valid `current` symlink and installed manifest;
- current service and database state;
- runtime secret file permissions and required keys without printing values;
- release and backup disk capacity;
- candidate/current target compatibility;
- Caddy and expected listener state; and
- candidate migration differences.

#### One-time adoption of a manual installation

The first automated deployment may encounter a valid release installed through the current manual
runbook without an automation manifest. It must not require reinstalling or modifying that release.
Instead, after verifying the current symlink, release layout, permissions, runtime health, and
local PostgreSQL topology, it:

- reads the OTP application version from release metadata;
- fingerprints the installed migration files;
- records the exact installed directory and a content digest;
- marks unavailable source-revision and original-artifact details as `unknown`;
- writes a root-owned `adopted` baseline record under `/opt/taskman/deployments/`; and
- requires explicit operator confirmation before using that baseline for migration comparison.

Adoption is refused if the current release is mutable, incomplete, unhealthy, outside the managed
release root, or inconsistent with the accepted systemd/Caddy/database topology. The adopted
release directory remains unchanged. Later rollback may target it when the complete chain of
migration declarations permits doing so.

If the exact candidate is already current and healthy, deployment returns status `0` without
backup, migration, or restart. If its final release directory exists with matching immutable
content, deployment may resume from it. A differing or incomplete final directory is a status `10`
safety refusal.

### 3. Private staging

The archive is uploaded through a unique restrictive tool-owned staging path. Its SHA-256 is
verified on the server before extraction. Extraction occurs in a new staging directory and rejects
absolute paths, parent traversal, unexpected top-level structure, links escaping the release, and
unexpected writable permissions.

Only complete verified content is renamed into `/opt/taskman/releases/<release-id>`. Existing
release directories are never edited in place.

### 4. Backup and migration declaration

Every non-no-op deployment creates and validates a custom-format backup before stopping Taskman.
Insufficient capacity, dump failure, or validation failure ends the deployment with the existing
release still selected and running.

The manifest's migration hashes are compared with the current installed manifest:

- no migration difference is recorded as `no-change`;
- changed migrations require the operator to declare `backward-compatible` or `restore-required`;
- the deployment summary explains the consequences before confirmation; and
- the declaration is recorded with the installed release, previous release, and backup identifier.

Automation never derives compatibility from migration syntax. A declaration is an operator
decision informed by implementation review and migration verification.

Each root-owned deployment record describes one activation edge from a previous release to a new
release. Rollback compatibility is evaluated across every intervening edge, not merely the newest
deployment.

### 5. Maintenance window, migration, and activation

After a verified backup:

1. stop `taskman.service`, leaving Caddy running;
2. run the candidate release's migration command with the protected systemd environment;
3. if migration succeeds, atomically replace `/opt/taskman/current`;
4. start `taskman.service`;
5. allow the unit's existing `ExecStartPre=migrate` to confirm the idempotent migration state; and
6. begin bounded readiness polling.

A migration failure leaves Taskman stopped and `current` selecting the previous release. Earlier
migrations in the same release may already have committed, so automation does not restart old code
or claim rollback safety. It reports the backup, migration policy, selected release, and recovery
choices.

An activation or startup failure likewise stops and reports. It does not silently select old code,
because the database may have changed and a failed new release is evidence requiring operator
judgment.

### 6. Verification

Successful deployment verifies:

- `taskman.service` is active and its main process runs from the selected immutable release;
- loopback `/healthz` reaches the candidate application and database;
- public HTTPS `/healthz` succeeds;
- HSTS is present;
- Caddy remains active;
- Phoenix listens only on configured loopback;
- Erlang distribution listens only on its fixed loopback port;
- PostgreSQL is not publicly bound;
- no ordinary EPMD listener appeared; and
- recent service logs contain no startup failure.

It then reports the candidate and previous release identifiers, backup identifier, migration
policy, verification results, and remaining human browser/email/API checks.

## Health endpoint

Taskman adds:

```text
GET /healthz
```

The endpoint is unauthenticated so local and external deployment verification do not require a
user credential. It:

- returns HTTP `200` with a fixed `ready` body only when the Phoenix endpoint is serving and a
  bounded `SELECT 1`-style database check succeeds;
- returns HTTP `503` with a fixed `unavailable` body on database timeout or failure;
- sets `Cache-Control: no-store`;
- does not return version, revision, hostname, database identity, exception, timing, or dependency
  details; and
- logs unexpected internal failures without exposing secrets.

`TaskmanWeb` calls a public core-library health capability; it does not call `Taskman.Repo`
directly. Focused tests cover ready, unavailable, timeout, response disclosure, and method
behavior.

The public endpoint proves the complete Caddy-to-Phoenix-to-PostgreSQL path. Release identity is
verified separately through the selected manifest and running process path.

## Rollback

`rollback <release-id>` requires:

- an installed, complete, immutable target manifest;
- an explicit current release different from the target;
- a deployment record connecting the current and target releases;
- no intervening `restore-required` migration boundary; and
- a healthy pre-rollback database and successful fresh backup.

A safe rollback:

1. confirms the exact target and current release;
2. creates and validates a new database backup;
3. stops Taskman;
4. atomically selects the target release;
5. starts Taskman without reversing schema state; and
6. performs the complete readiness verification.

If any intervening deployment is marked `restore-required`, rollback refuses with status `10` and
directs the operator to the restore workflow. There is no override that merely ignores this
boundary.

## Restore

Restore is a distinct destructive operation requiring the environment name and exact backup
identifier as typed confirmation. It never accepts an unresolved glob or arbitrary backup path.

The guarded workflow:

1. locks all lifecycle operations;
2. validates the selected dump with `pg_restore --list`;
3. verifies sufficient space for the restored and retained databases;
4. creates a fresh pre-restore backup of the current database;
5. stops Taskman;
6. restores without ownership changes into a newly created temporary database owned by the
   Taskman role;
7. validates required schema/migration state against the intended release;
8. terminates remaining Taskman database sessions;
9. renames the current database to a unique retained recovery name;
10. renames the validated restored database to the canonical Taskman database name;
11. starts the intended release; and
12. performs complete readiness verification.

Failure before the database-name swap removes only the tool-created temporary database after
explicit verification and restarts the unchanged application where safe. If the two-step rename
partially fails, the workflow attempts the narrowly defined inverse rename and otherwise leaves
Taskman stopped with exact manual recovery commands. Failure after the swap does not automatically
discard either database.

The retained pre-restore database is removed only by a later explicit cleanup operation.

## Cleanup

Cleanup computes eligibility from validated manifests and exact paths. It can remove:

- installed releases older than the configured retention count;
- recognized local backups older than their configured retention count;
- completed upload/staging files owned by this tooling; and
- retained pre-restore databases explicitly selected by identifier.

It cannot remove:

- the current release;
- the immediately previous release;
- a release required by a retained rollback record;
- the newest matching pre-deployment backup;
- an unrecognized file, directory, link, or database; or
- anything selected by an unresolved environment variable, wildcard, or path outside the managed
  roots.

The plan lists every deletion target, and typed confirmation is required. Material removal and its
recoverability are reported.

## Secret and trust boundaries

- SOPS decrypts directly into controller process memory. No persistent plaintext workstation file
  is created.
- Secret values are never passed in command arguments or interpolated into logged shell commands.
- The environment file is transferred through a private file channel and installed as
  `root:root`, mode `0600`.
- Any intermediate remote plaintext is unique, mode `0600`, restricted to the trusted SSH
  administrator, and removed immediately after the atomic root-owned install.
- Verbose and JSON output use the same structured redaction layer.
- Tests use unique canary secrets and fail if they occur in stdout, stderr, exceptions, manifests,
  command previews, or retained temporary files.
- SSH host-key verification is mandatory. First-use trust is an explicit operator action, not
  silently accepted by automation.
- The SSH administrator and root are already fully trusted. The automation does not add human users
  to the `taskman` group or grant generic sudo access to release launchers.
- The `taskman` account remains an unprivileged runtime identity with a `nologin` shell.
- Release archives, installed cookies, remote IEx, `eval`, and `rpc` retain the operating-system
  trust boundary documented in the existing runbook.
- The build container is a development/build tool only. Production remains a bare-metal OTP
  release.

## Expected repository boundaries

```text
ops/
  taskman
  pyproject.toml
  uv.lock
  builder/
    Containerfile
  environments/
    example.yaml
    example.secrets.sops.yaml
  taskman_ops/
    cli.py
    config.py
    errors.py
    output.py
    secrets.py
    build.py
    manifests.py
    remote.py
    host/
    services/
    releases/
    workflows/
  backup/
  caddy/
  systemd/
  tests/
```

Responsibilities:

- `ops/taskman` is a minimal stable launcher.
- `ops/pyproject.toml` and `ops/uv.lock` pin the controller environment.
- `ops/builder/Containerfile` owns the target-compatible build environment.
- `ops/environments/` contains examples and deliberately added encrypted environments.
- `taskman_ops/config.py` owns schema and cross-field validation.
- `taskman_ops/secrets.py` owns SOPS invocation, parsing, in-memory values, rendering, and
  redaction.
- `taskman_ops/build.py` and `manifests.py` own source, artifact, target, and migration identity.
- `taskman_ops/host/` and `services/` own convergent pyinfra capabilities.
- `taskman_ops/releases/` owns staging, locks, activation, rollback records, and exact cleanup
  eligibility.
- `taskman_ops/workflows/` owns ordered command state machines and stable results.
- `ops/backup/`, `ops/caddy/`, and `ops/systemd/` own server-side scripts and unit/configuration
  assets. Existing Caddy and Taskman service files move only if one canonical location remains and
  all references are updated.
- `lib/taskman/health.ex` owns bounded database readiness.
- `lib/taskman_web/controllers/health_controller.ex` owns the fixed HTTP response.
- `lib/taskman_web/router.ex` exposes `/healthz`.
- `test/` owns focused application health tests.
- `docs/deployment.md` remains the canonical automated and manual operations runbook.
- `docs/README.md` indexes this specification and later plan.

Keep modules focused; do not put the full provisioning or release transaction into one Python file.

## Testing and verification

### Python and configuration

Use locked, deterministic Python checks for:

- environment schema and cross-field validation;
- unsupported Ubuntu and architecture refusal;
- release ID, path, database identifier, and hostname allowlists;
- SOPS parsing, missing values, distinct signing secrets, and canary redaction;
- manifest generation and validation;
- migration fingerprint comparison;
- exit-category mapping;
- confirmation gates;
- release and backup list ordering, labels, empty state, JSON schema, and stale metadata;
- lock contention;
- cleanup eligibility; and
- workflow state reporting after each injected failure.

### Artifact and application

Verify:

- the pinned Ubuntu 26.04 amd64 container builds the release;
- production compilation treats warnings as errors;
- assets and OTP release assemble successfully;
- the manifest matches the release contents and target;
- archive traversal and escaping-link fixtures are rejected;
- required runtime configuration starts the release;
- missing or invalid runtime configuration fails without secret disclosure;
- migrations run from the packaged release;
- `/healthz` returns exact ready/unavailable behavior; and
- administrator bootstrap remains interactive and non-echoing.

### Convergence

Against controlled Ubuntu 26.04 environments:

- the first provision run reports the expected changes;
- the second run reports no convergent changes;
- a deliberately drifted owned file or permission is repaired;
- unrecognized conflicting state is refused rather than overwritten;
- firewall rules preserve a fresh SSH connection;
- PostgreSQL and Erlang distribution remain loopback-only; and
- Caddy and systemd configurations pass their native validators.

A container can cover package, user, file, manifest, and many pyinfra operations. Tests requiring a
real systemd PID 1, firewall, public DNS, ACME, or reboot behavior use a disposable VM or VPS rather
than pretending a non-systemd container is equivalent.

### Release failure injection

Exercise:

- corrupt local and remote checksums;
- wrong OS or architecture;
- insufficient release and backup disk space;
- interrupted uploads and existing partial directories;
- backup failure and invalid dumps;
- changed migrations without a compatibility declaration;
- migration failure before activation;
- startup failure after activation;
- readiness timeout;
- public HTTPS failure after local readiness;
- concurrent lock contention;
- incompatible rollback refusal;
- restore failure before and during database swap;
- cleanup refusal for current, previous, unknown, or out-of-root targets;
- release and backup listing during lifecycle lock contention; and
- contradictory records, missing backup files, and unrecognized storage entries.

Each test asserts service state, selected release, database preservation, retained backup, exit
status, redacted output, and the reported next safe action.

### Disposable-host acceptance

Before calling implementation ready, perform an explicitly authorized acceptance run on a
disposable Ubuntu 26.04 amd64 VPS:

1. provision from a clean SSH-accessible image;
2. rerun provisioning and confirm convergence;
3. create the first administrator interactively;
4. verify DNS, HTTPS, HSTS, sign-in, invitation email, API key, and LiveView;
5. deploy a second release without migrations;
6. roll back and forward safely;
7. exercise a controlled migration failure;
8. create, validate, and restore a local backup;
9. inspect firewall and all listeners; and
10. confirm no canary secrets or release cookies appeared in output or unintended files.

This acceptance run creates external state and therefore requires separate operator authorization.
Until it occurs, report it as unresolved evidence rather than claiming full provisioning proof.

Finish repository changes with focused checks and `mix precommit`. Python, container, native unit,
and documentation checks supplement rather than replace the project gate.

## Documentation changes

Implementation updates `docs/deployment.md` to:

- make the automated commands the primary supported workflow;
- preserve manual recovery steps independent of pyinfra;
- document exact workstation and Ubuntu 26.04 prerequisites;
- explain SOPS/age setup without including private identities;
- document environment creation and validation;
- describe all exit categories and recovery states;
- explain migration declarations, rollback, restore, cleanup, and local-backup limitations;
- retain the release launcher and loopback distribution trust warning; and
- give an explicit off-host backup recommendation.

`README.md` receives only the smallest link or hosted-operation update needed for discoverability.
`docs/development.md` changes only if implementation establishes a new durable project-wide rule.
`docs/README.md` indexes the specification and implementation plan.

Documentation must not claim that DNS, Resend, VPS creation, off-host backup, or a real deployment
is automated when it is not.

## Rejected alternatives and trade-offs

### Ansible

Ansible is mature, agentless, idempotent, and capable of both workflows. It was not selected because
the operator prefers a smaller Python-native surface over YAML roles, collections, and Ansible's
broader framework. pyinfra retains desired-state operations, SSH execution, dry runs, and
privilege escalation with less project ceremony.

The trade-off is a smaller ecosystem and less accumulated operational guidance. Taskman compensates
with narrow platform support, locked versions, explicit state machines, failure injection, and a
real-host acceptance run.

### cdist

cdist is a mature KISS-oriented, agentless, shell-based configuration manager with idempotent
types. It was not selected because structured artifact metadata, secret redaction, typed
configuration, and release transaction tests are clearer in Python for this project.

### Fabric or plain Python SSH

Fabric and direct SSH libraries provide good imperative remote execution but not the convergent
host-state model required by repeatable provisioning. Reimplementing that model would create more
project-owned infrastructure code than using pyinfra.

### Repository-owned shell scripts

Shell remains appropriate for small release-local commands and manual recovery. It was rejected as
the primary provisioning system because Taskman would need to hand-build dry runs, structured
configuration, state discovery, idempotence, error classification, and test seams.

### OpenTofu or Terraform remote provisioners

Provider IaC is appropriate for creating VPS, network, DNS, firewall, and backup resources.
OpenTofu and Terraform both describe remote provisioners as a last resort because they cannot model
guest configuration predictably. A future provider layer may hand the resulting SSH host to this
workflow; it must not absorb the guest configuration itself.

### cloud-init as the complete provisioner

cloud-init is useful for first-boot users, keys, and a minimal bootstrap. It is not an ongoing
convergence or application release mechanism and cannot handle the existing-host deployment
workflow consistently.

### NixOS or immutable machine images

NixOS, image baking, and replacement-based infrastructure can provide stronger whole-host
reproducibility. They require a different supported host model, image lifecycle, and database
strategy. That adoption cost is not justified for one existing-style dedicated VPS.

### Container-first runtime

Kamal, Docker Compose, and container platforms are viable deployment systems, but they replace the
already accepted OTP release/systemd runtime with an image registry, container networking, and
volume lifecycle. The build container in this design does not reopen that runtime decision.

### Automatic deployment and automatic rollback

CI auto-deployment is outside the chosen workstation-driven control model. Automatic rollback is
unsafe across unknown database migration effects. Both require separate designs and stronger
authorization and compatibility contracts.

### Zero-downtime deployment

Blue/green activation would require concurrent Phoenix instances, proxy switching, multiple ports,
draining, stricter migration compatibility, and more cleanup state. Brief downtime is accepted for
the initial single-host workflow.

## External evidence

Current primary documentation informing the design:

- [Ubuntu 26.04 LTS release notes](https://documentation.ubuntu.com/release-notes/26.04/)
- [Ubuntu 26.04 amd64 server images](https://releases.ubuntu.com/26.04/)
- [Phoenix deployment with OTP releases](https://hexdocs.pm/phoenix/releases.html)
- [pyinfra overview and license](https://github.com/pyinfra-dev/pyinfra)
- [pyinfra operations and two-phase execution](https://docs.pyinfra.com/en/3.x/using-operations.html)
- [pyinfra programmatic API](https://docs.pyinfra.com/en/3.x/api/)
- [pyinfra inventory and external data](https://docs.pyinfra.com/en/3.x/inventory-data.html)
- [SOPS structured-file and age support](https://github.com/getsops/sops)
- [Caddy package installation](https://caddyserver.com/docs/install)
- [PostgreSQL `pg_dump`](https://www.postgresql.org/docs/current/app-pgdump.html)
- [PostgreSQL `pg_restore`](https://www.postgresql.org/docs/current/app-pgrestore.html)
- [systemd service units](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [OpenTofu warning that provisioners are a last resort](https://opentofu.org/docs/language/resources/provisioners/syntax/)
- [Terraform warning that provisioners are a last resort](https://developer.hashicorp.com/terraform/language/provisioners)
- [cdist design rationale](https://www.cdi.st/manual/latest/cdist-why.html)
- [Fabric documentation](https://docs.fabfile.org/en/latest/)

## Known caveats

- Local backups do not survive VPS loss. A production operator must arrange and test off-host
  copies even though that is outside the first automation boundary.
- A migration declaration records a reviewed operator decision; it does not prove compatibility.
- Ecto generally runs individual migrations transactionally where supported, but a release with
  several migrations may commit earlier migrations before a later one fails.
- Caddy certificate issuance still depends on correct public DNS, reachable ports 80/443, and
  external ACME services.
- Resend delivery still depends on external domain verification, account state, API credentials,
  recipient systems, and DNS.
- Unattended security updates may install a kernel requiring reboot. Automatic reboot is disabled;
  the workflow reports reboot-required state but does not reboot a production host.
- A pyinfra dry run cannot perfectly predict inherently procedural commands or external ACME and
  email behavior.
- Ubuntu 26.04 point releases remain within the accepted `26.04` platform, but package and toolchain
  updates require compatibility verification before changing pinned build inputs.
- A full systemd, firewall, DNS, certificate, and restore proof requires a disposable VM or VPS;
  container-only verification is insufficient.

## Next-session checklist

1. Review this written specification and resolve any requested changes.
2. After approval, create the implementation plan under `docs/plans/` using the writing-plans
   workflow.
3. Create the repository-local Beads delivery graph from the approved plan.
4. Update the deployment-automation handoff with the plan and active Beads identifiers.
5. Start implementation in a fresh session with `$resume dedicated-host-deployment-automation`,
   defaulting to delegated execution unless the operator explicitly chooses another approach.
6. Do not touch a real VPS, DNS, Resend account, or production secret without separate explicit
   authorization.

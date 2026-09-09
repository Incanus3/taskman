# Dedicated-host deployment design

Status: consolidated accepted design. Updated: 2026-09-09.

This is the canonical design for Taskman's repository-owned deployment controller. It describes
the implemented architecture and accepted constraints, not an implementation sequence. The
[deployment runbook](../deployment.md) owns operator commands, configuration setup, manual recovery,
and external acceptance. The [development guide](../development.md#operations-verification) owns
the runnable local verification recipe. Neither this design nor passing local checks authorizes
deployment, publication, or changes to an external host.

## Purpose and supported scope

One workstation-driven controller builds, provisions, deploys, inspects, backs up, and recovers one
Taskman installation. The runtime is an immutable OTP release, not a source checkout or container:

- Ubuntu 26.04 LTS on `amd64`/`x86_64`, with systemd as PID 1;
- one trusted SSH administrator with the required sudo authority;
- Caddy terminating public HTTPS on ports 80/443;
- Phoenix bound to loopback, normally `127.0.0.1:4000`;
- local PostgreSQL bound to loopback;
- fixed loopback-only Erlang distribution, normally `127.0.0.1:6789`, without ordinary EPMD;
- an unprivileged `taskman` service account and a root-managed immutable release tree; and
- periodic validated local backups, with off-host copies arranged independently.

Taskman remains an authenticated shared workspace. Hosting does not introduce ownership or
permissions on individual Projects, Lists, or Tasks.

Brief downtime during release and database changes is accepted. Provisioning and deployment have
different scopes: provisioning converges the supported host; deployment changes only the application
release and the associated database, backup, service, and selection state.

The controller does not manage VPS creation or replacement, provider firewalls, DNS publication,
Resend accounts or domains, off-host backup storage, credential rotation, distribution upgrades,
SSH authentication policy, automatic reboots, or CI deployment. Multi-host, rolling, blue/green,
zero-downtime, other-platform, and automatic rollback workflows are outside this design.

## Architecture and ownership

The workstation uses Python 3.12+, a locked `uv` environment, pyinfra 3.x, Pydantic, SOPS with age,
OpenSSH, and a pinned Docker BuildKit/buildx builder. The host needs Ubuntu's Python standard
library, not the controller dependency environment.

| Boundary | Responsibility | Does not own |
| --- | --- | --- |
| Controller workflows | Input validation, artifact resolution, admission, plans, confirmation, requests, and public results | Host transaction reconstruction or a second remote-state model |
| pyinfra provisioning | Stable packages, accounts, directories, non-secret files, units, and service convergence | Release, migration, restore, or cleanup procedures |
| Transient host helper | Coherent observation, locking, immutable installation, backup, selection, restore, cleanup, and final verification | Operator interaction, SOPS, building, or provider resources |
| Scheduled backup executable | Fixed environment/status adapter invoking shared backup and retention capabilities | Deployment, restore, SSH, or interactive controller behavior |
| Shared protocol and small capabilities | Bounded serialization, request correlation, checksums, and common invariants | Configurable workflows or generalized policy engines |

Each operation remains an explicit readable procedure. Deploy and first-release provisioning share
one deployment procedure; rollback and restore retain their own consequence ordering and ambiguity
decisions. Shared capabilities serve the same invariant, not merely similar-looking syntax.

### Repository map

Paths below are relative to `ops/taskman_ops/` unless otherwise stated.

| Owner | Responsibility |
| --- | --- |
| `cli.py`, `errors.py`, `output.py` | Finite public CLI, stable status categories, typed errors, recursive redaction, human/JSON rendering |
| `config.py`, `secrets.py` | Validated non-secret environment, SOPS decryption, private runtime/pgpass rendering |
| `releases/` | Clean-source identity, builder execution, exact-input cache resolution, archive and manifest validation |
| `remote.py` | Strict SSH identity, bounded workstation transport, private uploads, and interactive terminal bridge |
| `helper_client/package.py`, `helper_client/runner.py` | Workstation-side deterministic allowlisted packages, checksum-verified transfer/invocation, and exact cleanup |
| `host_protocol/` | Standard-library request/result schema, finite vocabulary, bounds, and correlation validation |
| `host/facts.py`, `host/acceptance.py`, `workflows/operational_preflight.py` | Host evidence and admission before existing-host plans |
| `provisioning.py`, `host/baseline.py`, `host/firewall.py`, `services/` | One programmatic pyinfra deploy and narrowly justified native/secret boundaries |
| `host_helper/paths.py`, `records.py`, `state.py`, `lock.py` | Derived path authority, completed records, coherent observation, and one lifecycle lock |
| `host_helper/backups.py`, `database.py`, `credentials.py` | Backup creation/retention, live migration observation, and private credential authority |
| `host_helper/commands.py`, `services.py`, `selection.py`, `filesystem.py` | Bounded host subprocesses, service control, atomic selection, and directory synchronization |
| `host_helper/verification.py` | Fresh service/release/listener/readiness proof and its request construction |
| `host_helper/operations/` | Command-specific procedures, confirmation relevance, and final state projection |
| `workflows/`, including `helper.py` and `verification_results.py` | Operator orchestration, validated report translation, and request/result integration |
| `checksums.py`, `migrations.py`, `host/pyinfra_support.py` | Neutral streaming SHA-256, migration-version invariants, and controller-only pyinfra mechanics |
| `host_helper/scheduled_backup.py`, `services/backups.py` | Host-side scheduled environment/status adapter and controller-side installation, calendar, and unit contracts |
| `ops/builder/`, `ops/systemd/`, `ops/caddy/` | Pinned builder and reviewed native assets |
| `lib/taskman/health.ex`, `lib/taskman_web/controllers/health_controller.ex` | Public database-readiness capability and fixed HTTP response |

`helper_client/` runs on the workstation and packages/invokes the helper; `host_helper/` owns
host-side execution. `host_protocol/` defines their shared communication contract. Root-level
modules retain the public CLI, configuration, errors/output, transport, provisioning entry point,
and small neutral capabilities rather than introducing a generic utility package.

The controller and host packages share standard-library code explicitly through package allowlists.
Generated server executables are real consumers of shared code and must be checked when those
modules change. The transient archive includes the release manifest implementation and its error
types because deployment consumes them; the scheduled archive retains only its backup dependencies.
Isolated execution tests use `python3 -I -S` to exclude site packages as well as environment-based
import paths. Repository Python has no external code consumers, but CLI, protocol, persisted
records, and deployed executable contracts remain meaningful boundaries.

## Configuration and path authority

Non-secret environment data lives in `ops/environments/<environment>.yaml`; encrypted values live
in the adjacent `<environment>.secrets.sops.yaml`. `.sops.yaml` contains only public recipients and
selection rules. Private age identities remain external. Real environment files are deliberately
versioned by the operator, not inferred from examples or historical staging information.

Configuration includes SSH destination/port/user and pinned fingerprint, direct public hostname
and addresses, supported target, distinct application/distribution/database ports, loopback database
connection facts, sender address, package track when selected, backup schedule, retention, and
bounded timeouts. Unknown fields, unsafe identifiers, unsupported targets, overlapping service
ports, and invalid root topology fail validation. Existing non-root input aliases remain accepted
where `EnvironmentConfig` defines them; they are not authority for adding more aliases.

There are exactly two configurable filesystem roots:

```yaml
install_root: /opt/taskman
backup_root: /var/backups/taskman
```

The installation derives `releases`, `deployments`, `deployments/uploads`,
`deployments/selections`, `current`, and `lifecycle.lock` beneath `install_root`. Neither operator
configuration nor helper input can replace a derived root. Both configured roots must be normalized
absolute POSIX paths, disjoint from one another and from reserved configuration, state,
installed-program, and helper locations. There are no independently configurable subordinate roots
or legacy root aliases.

Lexical containment is not sufficient for privileged action. Relevant existing directories, files,
links, ownership, permissions, and identities are checked at the authority boundary; unexpected
redirects or contradictory identities are not repaired by guessing.

Installation roots exclude whitespace, backslash, `%`, quotes, ASCII controls, and DEL as required
by the shared path and systemd executable contracts. Backup roots may contain quotes; systemd
path-list encoding preserves them. Shell quoting and systemd parsing are separate boundaries.
Both normal and interactive SSH preserve literal argument values. Invalid configuration, including
either retention outside the strict integer range `1..64`, fails before SSH.

## Security and execution boundaries

### Secrets and release authority

SOPS decrypts into controller memory. The encrypted document contains the database password,
`secret_key_base`, `ash_authentication_token_signing_secret`, and `resend_api_key`. The two signing
secrets are distinct and each at least 64 bytes. Rewrapping age recipients does not rotate secrets.

Secrets do not enter argv, helper JSON, plans, manifests, logs, exception text, or public reports.
Secret-bearing installation uses protected stdin/private transfer and atomic root-owned mode-`0600`
files. `/etc/taskman/taskman.env` and `/etc/taskman/pgpass` are fixed credential locations.
Intermediate plaintext is private and removed when safe; no persistent plaintext workstation file
is created. Credentials are validated for type, ownership, mode, and scope before use.

Human and JSON output share recursive redaction. Raw remote output, malformed helper output,
environment values, and inspected exceptions are not safe diagnostics merely because a command
failed. Report fixed reasons and bounded observed facts instead.

Root, the trusted SSH administrator, and the service account with access to the release cookie
are trusted application operators. `nologin` is not a sandbox after account compromise.
Installed release directories, executables, data, and the completed manifest remain root-owned
with the `taskman` group: directories and executables use `0750`, other regular files `0640`.
This permits service-account read/execute access without group writes or world access. Resolve
the service group explicitly during staging; do not retain archive/extractor group ownership.
Existing immutable releases are not silently rewritten to repair historical metadata mistakes.
`bin/taskman eval`, `rpc`, and `remote` provide arbitrary application-code authority; no generic
sudo access to release launchers or unrestricted `systemd-run` is granted. Release archives and
installed cookies are deployment credentials. Loopback distribution uses cookie authentication,
not TLS, and must not be exposed or forwarded beyond loopback.

### SSH and bounded execution

SSH requires a fingerprint verified through an independent trusted channel. Automation constructs
strict known-host authority; it does not use silent trust-on-first-use, accept-new, or unrelated
user SSH configuration. Firewall changes must prove a fresh strict SSH connection on the configured
administrator path.

Workstation transport and host-local subprocess execution remain separate implementations because
their trust domains, inputs, cleanup authority, and termination semantics differ. Each has finite
execution and output bounds. Channels drain stdout and stderr without unbounded buffering; host
commands use argv and terminate process trees on timeout. Verification reuses the host command
runner and retains a bounded readiness polling budget. Interactive administrator execution is the
explicit TTY boundary, not a captured-output command.

A lost helper result means an unknown remote outcome. The controller does not claim which remote
instruction completed; the operator inspects or reruns the same request under the recovery rules.

## Helper packaging and protocol

The controller builds `taskman-host.pyz` deterministically from an explicit standard-library module
allowlist: lexical member order, fixed archive timestamps/modes, and a fixed entrypoint. Identical
sources produce identical bytes and SHA-256. Configuration, secrets, tests, caches, bytecode,
repository metadata, and unrelated application source are excluded.

Each invocation uploads through a unique private administrator-owned directory, verifies the
checksum, installs the same bytes beneath `/run/taskman-ops` in a root-owned mode-`0700` invocation
directory with executable mode `0500`, verifies the installed checksum, and invokes the absolute
helper path. It reads one request and emits one result. No resident agent, listener, controller
dependency environment, or background recovery process remains.

Cleanup is exact-path and best-effort. A cleanup failure adds a fixed warning without hiding the
primary result or turning completed work into failure. The warning does not authorize broad
recursive deletion. Read-only commands and dry runs do not change managed application state;
temporary helper transfer and execution infrastructure are still required.

Protocol version `2` has these exact envelope fields:

| Envelope | Fields |
| --- | --- |
| `HostRequest` | `protocol_version`, `operation`, `correlation_id`, `expected_state`, `paths`, `parameters` |
| `HostResult` | `protocol_version`, `operation`, `correlation_id`, `outcome`, `message`, `state`, `warnings` |

The finite helper vocabulary is `discover`, `list_releases`, `list_backups`, `verify`, `genesis`,
`deploy`, `backup`, `rollback`, `restore`, and `cleanup`. `correlation_id` is ephemeral transport
identity, not a durable operation, release, backup, or database identity. `paths` contains exactly
the two roots. Operation handlers own their expected-state and parameter semantics.

Requests and results are at most 64 KiB each, collections at most 64 items, and nesting depth at
most 8. Strings, paths, and identifiers also have explicit byte bounds. The shared codec rejects
wrong types, unsupported versions/operations, duplicate JSON keys, invalid UTF-8/JSON, oversized or
trailing output, and non-finite values. Request/result operation and correlation must match.
Operation-specific validators check required final facts without reconstructing host policy.

The four outcomes are:

- `succeeded`: the requested outcome is verified;
- `refused`: the request or observed authority is unsafe;
- `retryable`: a recognizable state can be addressed by rerunning the command; and
- `manual`: authoritative state is contradictory or needs external information/action.

The wire envelope remains separate from operation evidence. `HostResult.for_request` copies request
identity while callers explicitly choose outcome, message, state, and warnings. Workflow utilities
share frozen-value conversion and first-occurrence warning deduplication; callers retain schema
validation and error translation.

## Completed state and replayable recovery

One `HostState` observes selected release, completed releases/backups/selections, live applied
migrations, service/database state, recognizable temporary paths, and bounded warnings. Database
planning uses credential-safe live observation, not only release-manifest assumptions. Unknown
state stays unknown. Non-authoritative storage is preserved with bounded warnings; authoritative
contradictions prevent unsafe mutation.

All host mutations, including scheduled backup/retention, use the exclusive lock at
`install_root/lifecycle.lock`. Read-only discovery takes that same lock briefly for a coherent
snapshot. Acquisition is bounded; the lock contains no durable operation identity. Its existence
does not mean it is held and is not a reason to delete it.

Only completed records are persisted:

| Record | Exact fields | Location |
| --- | --- | --- |
| `ReleaseRecord` | `release_id`, `source_revision`, `artifact_sha256`, `migrations` | `releases/<release-id>/.taskman-release.json` |
| `BackupRecord` | `backup_id`, `created_at`, `dump_sha256`, `source_release_id`, `migration_versions`, `source_database_size_bytes` | `<backup_root>/<backup-id>.json`, beside `<backup-id>.dump` |
| `SelectionRecord` | `release_id`, `previous_release_id`, `backup_id`, `selected_at` | `deployments/selections/selection-<digest>.json` |

Creation/selection timestamps are canonical whole-second UTC. Migration fingerprints contain exact
filenames and SHA-256 values; observed migration versions are strict non-negative integers in sorted,
unique order. `migrations.py` shares only that version-sequence invariant; schema limits, fingerprint
parsing, observation defaults, and boundary-specific errors remain with their owners.

Records have exact bounded schemas, authoritative locations, and atomic create-once publication.
Completed selection is recorded only after verification. A first selection has no predecessor;
backup references may be absent when the procedure does not require a backup. Installed releases
are immutable. There are no pending/provisional records, stage journals, durable operation IDs,
recovery programs, or compatibility readers for abandoned internal formats.

Each mutating procedure locks, observes, validates confirmation-relevant facts, normalizes only
recognizable safe temporary state, repeats or skips safe work, performs consequences in order,
verifies the final outcome, and publishes completed facts. Deterministic staging, dump, and
temporary-database names make modeled interruptions recognizable without preserving an interrupted
process identity.

Confirmation binds the material action: selected release, target, backup, or exact deletion set.
Changed authority causes refusal/replanning; irrelevant drift does not require an exhaustive
snapshot match. Recovery means an operator reruns and reconfirms where needed. It does not mean
background recovery, seamless process resumption, arbitrary corruption repair, or automatic rollback.

## Build and artifact identity

`build` requires a clean, identified source revision and never contacts a host. The pinned
`linux/amd64` Ubuntu builder runs production dependencies, warnings-as-errors compilation, asset
deployment, and OTP release assembly. The reviewed base is:

```text
ubuntu:resolute-20260811.1
sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b
```

The dated tag communicates the image release; the digest fixes the content. They are reviewed and
updated together. New builds pin OTP `29.0.6`, Elixir `1.20.4`, Node `22.22.1`, Hex `2.5.1`, and
Rebar3 `3.24.0`. OTP and Elixir come from the published HexPM Ubuntu 26.04/amd64 and OTP-29
archives, each verified against its fixed SHA-256 before installation. The versioned Rebar3 binary
is checked against its recorded SHA-512. The Containerfile and artifact schema own the exact
toolchain constraints. External package indexes mean
this is not a claim of bit-for-bit reproducible release archives.

This supersedes the original Ubuntu-package build pin, OTP `27.3.4.6` with Elixir `1.18.3`.
That exact historical tuple remains supported for artifact and persisted-record validation,
verification, backups, and rollback; validators derive provenance from each release's own OTP
identity rather than the new-build default. No historical record is rewritten and no arbitrary
toolchain tuple is admitted. The upgrade resolves the administrator prompt's reliance on the
reversible no-shell raw/cooked terminal API, introduced in OTP 28. It does not change the separate
[Alpine CI compatibility decision](2026-08-10-alpine-elixir-ci-design.md) or local mise management.
The [runbook](../deployment.md#build-an-artifact) owns the first-install transition sequence,
including updating the persistent backup executable before selecting a new-runtime release.

Release identity is
`<application-version>-<12-hex-source-sha>-ubuntu26.04-amd64-otp<otp-version>`. The manifest records
full source revision, build time, target, toolchain, builder tag/digest, migration fingerprints, and
release layout. A detached SHA-256 identifies exact archive bytes. Same logical ID with different
installed bytes is refused, never overwritten.

Archive verification precedes extraction and rejects traversal, absolute paths, device nodes,
escaping links, and unexpected release layout. Installation verifies remote bytes and only promotes
complete validated content into the immutable release root. Safe matching staged/installed content
may be reused; contradictory final content is not repaired in place.

Explicit `--artifact` is authoritative after validation of its adjacent manifest and checksum.
Without it, `deploy` first validates the clean checkout, then deterministically chooses a verified
exact-input local match or invokes the same builder as `build`. Source, version, target, toolchain,
and builder identity must match; age is not freshness. Invalid cache entries are ignored and
preserved. Resolution finishes before SSH, and reports `explicit`, `cached`, or `built`.
Provisioning builds by default or accepts an explicit artifact. Exact bytes remain available for
operator retry rather than silently substituting a different build.

## Provisioning and admission

Local configuration, secret, and artifact validation precede host mutation. Host admission proves
the supported platform, systemd, administrator/sudo and active SSH path, capacity, direct public
DNS/address match, and absence of incompatible managed resources or foreign public listeners.
Provisioning presents a redacted plan and requires ordinary interactive confirmation.

One programmatic pyinfra deploy expresses stable desired state through built-ins: required
packages, authenticated Caddy package source, accounts, directories, non-secret files, unit
installation, reload/enablement, and unattended security updates without automatic reboot. It repairs
ordinary owned drift and reports no declarative changes on a converged rerun. Deploy definitions
must not branch during prepare on mutable facts that earlier queued operations will change.

Three native custom operations are retained for specific material risks:

| Operation | Why ordinary convergence is insufficient |
| --- | --- |
| UFW activation | SSH must be allowed before enablement and verified with a fresh pinned connection afterward |
| Caddy validation/install | Candidate configuration must validate immediately before replacing the live configuration |
| PostgreSQL cluster/HBA configuration | Cluster selection and live endpoint identity precede candidate installation; parser-backed HBA/SCRAM validation precedes reload/restart |

Database role/password authority and private runtime/pgpass installation remain outside pyinfra's
logged command path. PostgreSQL retains one-cluster authority, least-privilege role/database
ownership, loopback binding, SCRAM, and connection verification. Read/mutation predicates are shared
where they represent one policy, not reimplemented as separate evidence frameworks.

The operator selected native-path HBA validation on 2026-09-09 after VPS testing established
that PostgreSQL's `hba_file` setting is startup-only. Keep the selected Ubuntu cluster's
`/etc/postgresql/<version>/<cluster>/pg_hba.conf` path rather than relocate it. Install the
candidate atomically with a recoverable copy of the previous file and its metadata, query
`pg_hba_file_rules` before reload/restart, and restore the previous file on validation failure.
Validate the exact selected-cluster path and live endpoint; an unavailable or contradictory
live parser must not authorize an authentication-file replacement. Preserve the native cluster
directory metadata rather than applying the former dedicated-HBA-directory ownership policy.

This supersedes the stronger interpretation that parser validation must precede all live-path
disk changes. The operator accepts the brief crash/power-loss window between candidate
installation and validation/restoration. A separate temporary PostgreSQL validation instance
was rejected for now because of its lifecycle and cleanup complexity. A later requirement to
eliminate that window needs a new decision. PostgreSQL documents the
[startup-only file setting](https://www.postgresql.org/docs/18/runtime-config-file-locations.html)
and the [rules view's inspection of current file contents](https://www.postgresql.org/docs/18/view-pg-hba-file-rules.html).

Provisioning installs hardened systemd assets, creates the lifecycle lock before enabling backups,
and invokes the same deployment procedure for the first release. First release requires empty,
unambiguous completed state, uses the initial `restore-required` policy, and does not invent a
predecessor or a pre-deploy backup of a nonexistent selected release. A completed first selection is
a verified no-op. Earlier provisioning failures retain compatible partial state for rerun; they do
not undo packages, firewall, database, or secrets.

Existing-host backup, cleanup, deploy, rollback, and restore share controller preflight before
planning/confirmation: supported-host admission, runtime file ownership/mode/required-key checks
without values, database access, and backup capacity. Fixed `next_action` text identifies whether
the runtime or database/capacity group failed. Helper observation and final verification retain fresh
mutable-state checks at consequences; these are not duplicates of stable controller admission.

## Public commands and consequences

The launcher is `./ops/taskman`. All commands accept `--json` and `--dry-run`. Dry-run performs
applicable validation and discovery, but skips managed-state mutation and confirmation. It may
build a required local artifact. `build` is always local; `verify`, `releases`, and `backups` remain
read-only with or without the flag. There is no generic force switch or unattended deployment mode.

| Command | Principal outcome and confirmation |
| --- | --- |
| `build` | Produce and verify a local artifact; no host interaction |
| `provision ENV` | Converge supported host and first release; ordinary confirmation |
| `deploy ENV` | Resolve, install, and verify the candidate; ordinary confirmation |
| `verify ENV` | Report fresh service, release, topology, and readiness checks |
| `releases ENV`, `backups ENV` | List exact completed records and bounded warnings |
| `backup ENV` | Create a validated local dump without changing application selection; no destructive prompt |
| `rollback ENV RELEASE_ID` | Select the compatible immediately preceding release; typed environment/target confirmation |
| `restore ENV BACKUP_ID` | Restore exact retained database state and its compatible release; typed environment/backup confirmation |
| `cleanup ENV` | Delete exact eligible managed artifacts; typed environment/identifier-list confirmation |
| `create-admin ENV` | Run the constrained interactive administrator command through a real terminal |

### Deploy

Deploy compares current/candidate migration fingerprints. Changes require an explicit
`backward-compatible` or `restore-required` declaration before plan confirmation or upload,
including dry-run. No-change and initial-release cases have their own fixed policy. Compatibility
is a human decision, never inferred from migration syntax, and is reported rather than embedded
as a build fact.

Under the lock, deploy revalidates the confirmed selection, stages or reuses the exact immutable
release, and creates a validated pre-deploy backup before new database migrations on an existing
installation. A no-schema-change deployment does not require that backup. Taskman stops before
migration or selection; Caddy remains running. Candidate forward migrations execute through the
protected release wrapper, live applied versions are observed, `current` changes atomically, and
the service starts and passes verification before completed selection is published.

An already-selected verified outcome avoids unnecessary mutation. Lost output or recognizable
partial work is handled on rerun from physical state. Earlier migrations may have committed before
a later failure; automation must not restart incompatible old code or claim rollback safety. A
different or contradictory selection/schema requires manual inspection.

Manual installation adoption is unsupported. Deploy requires completed Taskman authority;
`--adopt-manual-current` remains a parser-level safety refusal, not a migration path. An existing
manual installation needs a separately reviewed procedure.

### Listings, rollback, and restore

Listings return validated `ReleaseRecord` or `BackupRecord` fields, not reconstructed activation
graphs or directory-name guesses. Empty valid inventories succeed. Unknown storage is preserved;
contradictory metadata refuses. Backup listings do not validate every dump body. Malformed rollback
or restore identifiers return status `2` before environment loading or SSH, without echoing input.

Rollback requires an installed immutable target, the immediately preceding completed selection,
and target migration versions exactly matching the live database. It does not traverse arbitrary
historical compatibility declarations or reverse migrations. After a fresh safety backup it stops,
selects atomically, starts, verifies, and records success. Recognizable interrupted selection/start
work can complete on rerun; missing, non-adjacent, or incompatible authority refuses.

Restore planning validates the completed backup identity, source release, recorded database size,
and root-owned regular non-link dump before dry-run output or typed confirmation. Under the lock,
the mutating procedure rechecks authority, mandatory dump SHA-256, fresh `pg_restore --list`, source
release/migration compatibility, and capacity before changing the database.

Restore creates a fresh safety backup, stops Taskman, restores and validates a deterministic
temporary database, swaps canonical and old database names, selects the compatible source release,
starts and verifies, then records the completed selection before removing the retired database.
Only a small set of recognizable source/temp/live arrangements may converge automatically.
Uncertain swaps preserve database material and stop for manual inspection; no journal or synthesized
recovery shell program chooses a speculative repair.

### Backups, scheduled execution, and cleanup

Manual backup, deploy, rollback, restore, and scheduled backup share one backup capability. It
validates credential/path authority and selected-release/live-migration provenance, checks capacity,
uses private `PGPASSFILE` with custom-format `pg_dump`, validates with `pg_restore --list`, hashes
the dump, publishes without replacing an existing final dump, and creates its completed manifest.
Normalization removes/repeats only proven safe deterministic temporary output. Referenced,
unknown, malformed, or contradictory orphans are not guessed into cleanup eligibility.

Provisioning installs `/usr/local/lib/taskman/taskman-backup.pyz` as root-owned mode `0750`. The
executable persists; each systemd `Type=oneshot` process is short-lived. Its allowlist contains only
the scheduled adapter and required standard-library backup/state/lock/record capabilities, not
deployment, restore, SSH, SOPS, or interactive code. Installation is atomic and checksum-verified.

The timer has validated calendar syntax and `Persistent=true`. Its fixed non-secret environment
contains only installation/backup roots, database host/port/role/name, and retention. Credentials
come from `/etc/taskman/pgpass`, not argv or environment values. Hardening grants write access only
to the backup root and exact lifecycle lock. The adapter emits fixed journal messages and statuses:
`0` completed, `2` invalid installed configuration, `6` retryable backup failure, `10` manual/unsafe
state, and `12` lock unavailable.

Retention protects every backup referenced by a retained completed selection, then retains the
configured number of newest unprotected completed backups ordered by `(created_at, backup_id)`.
Protected backups do not consume that count. Equal timestamps are deterministic; clock changes do
not weaken reference protection. Before deleting a backup pair, manual cleanup and scheduled
retention share record, checksum, and inode revalidation. Manifest deletion precedes dump deletion,
leaving conservative recognizable state after interruption.

Cleanup plans exact recognized stale release, backup, and temporary-file targets. It protects
current/previous and selection-required releases, referenced backups, unknown storage, and unsafe
paths. Execution revalidates the confirmed selection and targets under the lock, tolerates safe
already-absent targets, and never broadens the deletion set. Rerun replans from remaining state;
newly dangerous targets require new confirmation. Local backups still do not survive VPS loss.

### Interactive administration

`create-admin` requires stdin, stdout, and stderr to be real local TTYs. It allocates strict SSH
terminal access and invokes only `install_root/current/bin/create-admin` through constrained
`systemd-run` as `taskman`, with working directory from the same root and the protected environment
file. Arguments are shell-quoted for OpenSSH's remote-shell semantics. Email/password remain
terminal input, never controller arguments, SOPS data, environment values, or structured results.
The bridge returns the remote process status. It accepts no arbitrary command.

## Verification and reporting

Unauthenticated `GET /healthz` returns `200 ready` only after the core health capability's
`SELECT 1` succeeds with a default 750 ms query timeout; failure returns `503 unavailable`.
Responses are `text/plain`, `Cache-Control: no-store`, and contain no version, host, database,
timing, or exception details. The web layer calls the core capability, not Repo directly.
This proves availability through Caddy/Phoenix/PostgreSQL, not release identity by itself.

Before service/readiness proof, the helper freshly checks host capacity,
the configured invoking administrator through `SUDO_USER` and passwordless sudo, the active SSH
connection port through `SSH_CONNECTION`, and PostgreSQL authority. These checks run within the
same bounded verification deadline; root execution alone does not prove administrator authority.
The helper independently proves service MainPID/executable under the selected release, Caddy
activity, required listener topology, absence of ordinary EPMD, bounded startup-journal evidence,
exact loopback/public readiness, and public HSTS. Ordered fixed summaries distinguish lifecycle
failure from readiness failure; public verify preserves the typed failed report. A mutation cannot
report success without a complete successful report for its expected release.

Public human and JSON reports carry the same bounded facts and warnings. JSON schema version `1`
contains `command`, `environment`, `status`, `changed`, `stage`, `facts`, `warnings`, and
`next_action` alongside `schema_version`. A public stage or failed boundary is a coarse summary,
not a transaction history. Unknown service/database state is not promoted to success. The fixed
status categories are:

| Exit | Meaning |
| --- | --- |
| `0` | Success or verified no-op |
| `2` | Invalid command, argument, configuration, or unsupported target |
| `3` | Local prerequisite or build failure |
| `4` | Secret decryption, validation, or installation failure |
| `5` | SSH, host-key, privilege, or remote preflight failure |
| `6` | Backup or backup-validation failure |
| `7` | Migration failure |
| `8` | Release staging/selection or service lifecycle failure |
| `9` | Readiness or public verification failure |
| `10` | Safety refusal, state conflict, or incompatible rollback |
| `11` | Restore or restored-database validation failure |
| `12` | Shared lifecycle lock unavailable |

The interactive administrator bridge propagates its remote command status after session entry.
Migration classification requires observed migration evidence; transport loss does not invent it.
Reports direct the operator toward inspection/rerun or manual action, without raw output or
generated recovery commands. The runbook supplies the state-specific manual recovery guidance.

## Simplicity and maintenance

The accepted design favors the smallest coherent owner that preserves functionality, safety,
readability, diagnosability, and testability. Physical line counts are diagnostic evidence, not a
completion target. Do not minify, relocate behavior outside a metric, erase useful tests, or merge
unrelated responsibilities to reduce a number. There is no outstanding simplification backlog
implied by this document.

Several intentionally separate boundaries must remain visible:

- Stable controller admission, coherent helper state observation, and fresh outcome verification
  answer different questions at different times.
- Controller SSH/upload execution and host subprocess execution have different authority.
- Release and backup listings may evolve independently; a generic listing framework is not needed.
- Service-specific `changed=` parsers retain local error semantics; sharing a few parsing lines
  would add wrappers without a useful common policy.
- Operation-specific result classification, schema limits, credentials, and restore/rollback
  ordering remain local even when envelope or validation mechanics are shared.
- Backup publication, checksum/inode revalidation, reference protection, and conservative orphan
  handling are meaningful safety capabilities, not redundant evidence to compress.

Shared neutral checksum/migration invariants, request-correlated envelopes, warning merging, and
pyinfra sudo/probe mechanics have clear owners. They do not justify a universal validator, command
runner, workflow engine, or generic result-construction framework. Any future extraction must
identify actual consumers, a shared invariant, retained errors/authority, and a concrete net benefit.

### Test design

Tests are organized by trust domain and consequence boundary. Similar assertions at configuration,
protocol, capability, workflow, scheduled-package, and public-output layers are not duplicates when
they protect different failures. Compare input, parameter sets, fixtures, production boundary,
observable outcome, and safety consequence before removing coverage; equal syntax is not proof.

Shared support owns genuinely common managed paths, environment baselines, database mappings,
verification settings/reports, deployment-artifact setup, shell-writing mechanics, and explicitly
imported redaction-registry isolation. Scenario overrides remain visible. Preflight bypasses,
credential scope, operation-specific requests/records/runtime fakes, and PTY setup remain local.
Do not replace them with broad autouse fixtures or factories that hide the authority being tested.

Verification covers:

- public commands, help, confirmations, dry-run, status mapping, and human/JSON redaction;
- strict host identity, literal shell argv, bounded output, and process-tree termination with
  deterministic startup synchronization;
- configuration-to-helper/scheduled/systemd contract parity, including native parser diagnostics;
- isolated execution of both deterministic allowlisted zipapps without controller dependencies;
- exact records, authoritative paths, atomic selection/publication, and live migration evidence;
- backup before database consequences, selection-aware retention, and exact destructive revalidation;
- interruption/rerun at staging, dump/manifest, migration, selection, service, verification,
  restore-swap, cleanup, and lost-result boundaries;
- actual programmatic pyinfra first-run, second-run, drift, dry-run, and native custom transitions;
- artifact layout, checksum, target/toolchain, explicit priority, and exact-input reuse; and
- the health endpoint and release-runtime trust boundary.

Architecture guards run as part of pytest, with shared check implementation in
`ops/tests/support/architecture.py`, and keep package membership, direct custom operations, and
responsibility boundaries explicit. Cross-suite environment, remote-double, secret-isolation, and
shell helpers live in focused sibling support modules; domain-only helpers remain beside their
consumers. Automated checks complement direct consumer/behavior review; they do not authorize changes.
Use proportionate focused checks and independent review for material safety or architectural changes,
not an automatic whole-branch review for every edit.

## Rationale and accepted trade-offs

- **pyinfra over a broader configuration framework:** Python-native configuration and tests suit
  this narrow host. Ansible's larger ecosystem is useful but does not justify another framework;
  direct SSH or shell alone would require rebuilding desired-state convergence.
- **Guest configuration separate from provider IaC:** provider tools may supply a host and network,
  but do not own guest release/database procedures. First-boot bootstrap is not ongoing convergence.
- **OTP release rather than container runtime or whole-host replacement:** this preserves the
  accepted systemd/Caddy topology and local database model without registry/network/volume or image
  lifecycle machinery. A build container does not imply a production container.
- **Transient full helper:** repeated upload is acceptable for infrequent operations and avoids an
  independently versioned installed helper lifecycle. This is a complexity choice, not a claim that
  all persistent executables are unsafe.
- **Persistent narrow scheduled executable:** timers need code available without a workstation;
  the shared backup capability avoids a second shell implementation, while least-authority packaging
  excludes deployment and restore code.
- **Two roots:** useful installation/backup relocatability without independently configurable derived
  authority or a path-migration framework.
- **Replayable explicit procedures:** recognizable physical state and completed facts are simpler
  to verify than a generic journal/compensation engine; arbitrary ambiguous recovery is manual.
- **Maintenance window and explicit rollback:** multiple instances, draining, proxy switching, and
  automated migration-compatibility decisions are outside the single-host requirement.

## Evidence, caveats, and acceptance

The code baseline for this consolidation is `11ca55c2b07d77e99144b0b2343997842619a629`.
Local verification on 2026-09-09 passed 805 operations tests, 802 Elixir tests through
`mix precommit`, architecture, byte-compilation, shell, and documentation checks. This is a dated
baseline, not a claim about a future checkout. Current commands to reproduce local checks are in
the development guide; task records retain detailed verification provenance.

The transient archive's required runtime dependency set includes the release manifest and error
modules. Tests execute both zipapps with `-I -S`: `-I` alone can resolve an editable workstation
installation through site packages and hide missing archive members. This isolation requirement
was established by reproducing a missing-dependency startup failure, not just inspecting filenames.

Local tests and container builds do not prove systemd PID 1, UFW, public DNS, ACME, Resend delivery,
reboot behavior, or a complete real PostgreSQL restore. A disposable Ubuntu 26.04 amd64 acceptance
run remains unperformed and separately authorized. It must cover first/second provision, HTTPS/HSTS,
interactive administrator/sign-in/email/API/LiveView use, another release and rollback/forward
deployment, controlled migration failure, backup/restore, firewall/listeners, and secret/cookie
leakage inspection. Confirm the exact target, access, and permitted external changes first; do not
infer them from historical staging DNS.

Local dumps are not disaster recovery. Package/security updates may require an operator-managed
reboot. ACME and email depend on external services and configuration. A successful migration
declaration is not proof that old code can safely run against the new database. These caveats remain
even when all local checks pass.

### Technical references

These sources support the selected mechanisms; the repository's pinned inputs and tested contracts
govern its supported behavior:

- [Phoenix OTP releases](https://hexdocs.pm/phoenix/releases.html)
- [pyinfra operation model](https://docs.pyinfra.com/en/3.x/using-operations.html),
  [prepare/execute lifecycle](https://docs.pyinfra.com/en/3.x/deploy-process.html), and
  [programmatic API](https://docs.pyinfra.com/en/3.x/api/)
- [SOPS and age support](https://github.com/getsops/sops)
- [Docker image digests](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest)
- [Caddy package installation](https://caddyserver.com/docs/install)
- [PostgreSQL dump](https://www.postgresql.org/docs/current/app-pgdump.html) and
  [restore](https://www.postgresql.org/docs/current/app-pgrestore.html)
- [systemd service units](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
  and [executable-path validation](https://github.com/systemd/systemd/blob/v259/src/core/load-fragment.c)

# Dedicated-host deployment architecture

Status: accepted, implemented design. Updated: 2026-09-18.

This is the canonical design for Taskman's repository-owned deployment controller. It describes
the implemented architecture and accepted constraints, not an implementation sequence. The
[deployment runbook](../guides/deployment.md) owns operator commands, configuration setup, manual recovery,
and external acceptance. The [development guide](../guides/development.md#operations-verification) owns
the runnable local verification recipe. Neither this design nor passing local checks authorizes
deployment, publication, or changes to an external host.

The [operations contracts](2026-09-18-operations-contracts.md) own exact artifact/record/protocol shapes,
admission, confirmation, migration/restore/cleanup recovery, bounds and failure evidence.
The
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md) owns identified
local/build/native results and accepted limits.

The [one-time compatibility boundary](2026-09-18-operations-contracts.md#one-time-compatibility-boundary)
excludes unsupported staging formats/runtime; future supported upgrades retain compatibility and
recovery obligations.

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
| `releases/` | Clean/frozen source identity, builder execution, installed/cache target resolution, archive and manifest validation |
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
| `workflows/`, including `helper.py` and `verify.py` | Operator orchestration, validated report translation, and request/result integration |
| `checksums.py`, `migrations.py`, `host/pyinfra_support.py` | Neutral streaming SHA-256, migration filename/version invariants, and controller-only pyinfra mechanics |
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
with the `taskman` group: directories and executables use `0750`, ordinary regular files `0640`.
The completed `.taskman-release.json` record is `0600`; it is privileged deployment authority,
not service-account-readable runtime data.
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
helper path. Ordinary invocations read one bounded JSON request and emit one result. No resident agent, listener, controller
dependency environment, or background recovery process remains.

Cleanup is exact-path and best-effort. A cleanup failure adds a fixed warning without hiding the
primary result or turning completed work into failure. The warning does not authorize broad
recursive deletion. Read-only commands and dry runs do not change managed application state;
temporary helper transfer and execution infrastructure are still required.

Protocol version 3 has a finite request-correlated operation vocabulary. The shared standard-library
codec owns strict envelope/state/report schemas, 1-MiB messages, schema-specific collection limits
and fixed operation/correlation validation. Controllers consume validated evidence without
reconstructing host policy or a second report model. Outcomes are succeeded/refused/retryable/manual;
mutation classification distinguishes unchanged/changed/unknown and unavailable final facts from
proved absence. [Operations contracts](2026-09-18-operations-contracts.md#reconciliation-procedure-and-transport)
define exact schemas, budgets, inventories and failure handling.

Provisioning credential authority is the one private exit-only package entry. It consumes bounded
raw pgpass stdin, never JSON/argv secrets. Ready identities require authentication; genuinely absent
role/database permits exact record/file validation while deferring only impossible authentication
until convergence. It creates no credential file. Verified staging/cleanup is shared with ordinary
requests; [packaged admission](2026-09-18-operations-contracts.md#packaged-admission-boundaries) defines the
finite argument/status boundary. It is not a generic command transport or public CLI command.

## Completed state and replayable recovery

One `HostState` observes selected release, completed releases/backups/selections, live applied
migrations, service/database state, recognizable temporary paths, and bounded warnings. Database
planning uses credential-safe live observation, not only release-manifest assumptions. Unknown
state stays unknown. Non-authoritative storage is preserved with bounded warnings; authoritative
contradictions prevent unsafe mutation.

Helper lifecycle mutations, including scheduled backup/retention, use the exclusive lock at
`install_root/lifecycle.lock`. Read-only discovery takes that same lock briefly for a coherent
snapshot against cooperating locked writers; it does not exclude unlocked provisioning writes.
Acquisition is bounded; the lock contains no durable operation identity. Its existence
does not mean it is held and is not a reason to delete it. Controller-driven provisioning
convergence (pyinfra, database/pgpass and runtime installation) currently runs outside this lock;
its fresh authority checks do not serialize those writes.
[Admission and provisioning recovery](2026-09-18-provisioning-lock-coverage-proposal.md) is the
reviewed follow-up design; full design/plan approval and implementation remain pending in the
[dedicated post-merge workstream](../handoffs/operations-lock-coverage.md). It does not block the
current operations merge and is not current behavior.

The host persists immutable release/backup/success records and narrow migration-backup protection
and restore-target authority. Installed provenance precedes migration consequences; successful
history is published only after complete verification. Recovery authority protects exact inputs,
references and OIDs, without a phase journal, interrupted process identity or generated commands.
[Operations contracts](2026-09-18-operations-contracts.md#backup-protection-and-successful-history) define those
records and their ordering. Full history/reference validation stays on the host; bounded projections
and digest-bound pages keep controller transport finite without truncating protection.

Helper lifecycle sections lock, observe, validate confirmed material facts, handle only recognizable
safe partial state, perform ordered consequences, verify and publish completed authority. Scheduler
refresh releases lifecycle while waiting; whole manual commands are not exclusively admitted.
Provisioning convergence and interactive administrator creation are unguarded by that lock, and
different installation-root configurations do not share a host-wide lock. Status 12 reports failure
to acquire a lifecycle section, not guaranteed rejection of every overlapping command.

Provisioning has no persistent reservation or guaranteed backup suspension after interruption; its
normal timer creation/refresh can activate backups before the command finishes. Remote SSH work or
service-manager migration/admin jobs may continue after caller loss. A free lock or vanished caller
is not proof that such work stopped; manual inspection/quiescence is required before uncertain
retry. The proposed admission/recovery design supplies additional safeguards only when implemented.
Rerun and renewed confirmation supply recovery. No background recovery, seamless process resumption, automatic
rollback or arbitrary-corruption repair is promised. Temporary names identify safe modeled
interruptions; contradictory authority refuses without guessing.

## Build and artifact identity

`build` requires an identified source revision; clean source is the default and `--allow-dirty`
permits a private frozen snapshot. It never contacts a host. The pinned
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

The historical OTP 27 / Elixir 1.18 tuple is outside the supported baseline. OTP 29 also
supports the administrator prompt's reversible raw/cooked terminal API, introduced in OTP 28.
The separate [Alpine CI decision](2026-08-10-alpine-elixir-ci-design.md) remains independent.

Release identity includes application version, source revision, supported target/runtime and full
archive SHA256, with terminal dirty provenance. Manifest/ID/detached checksum/actual bytes must agree;
immutable installed content is never overwritten. Archive validation rejects unsafe members/links/
layout before extraction. [Artifact contracts](2026-09-18-operations-contracts.md#target-resolution-and-immutable-identity)
own exact fields, safe export and input/byte limits.

Explicit validated artifacts select exact bytes. Automatic clean resolution identifies source inputs
before observation and prefers matching physical/latest-successful/other installed provenance,
verified cache, then build. Dirty allowance freezes a private stable source snapshot and requires
building before exact identity is known. Clean-source drift before confirmation repeats the bounded
material-plan cycle; source/material drift afterward refuses a new invocation. Frozen dirty bytes do
not follow later edits. Local source development remains supported independently of host releases.

## Provisioning and admission

Local configuration, secret, and artifact validation precede host mutation. Host admission proves
the supported platform, systemd, administrator/sudo and active SSH path, capacity, direct public
DNS/address match, and absence of incompatible managed resources or foreign public listeners.
Provisioning presents a redacted plan and requires ordinary confirmation, supplied interactively
or by `--yes`; downgrade/unknown ordering requires independent acknowledgment.

One programmatic pyinfra deploy expresses stable desired state through built-ins: required
packages, authenticated Caddy package source, accounts, directories, non-secret files, unit
installation, reload/enablement, and unattended security updates without automatic reboot. It repairs
ordinary owned drift and reports no declarative changes on a converged rerun. Deploy definitions
must not branch during prepare on mutable facts that earlier queued operations will change.

Each provisioning material-plan cycle owns one immutable rendered systemd plan. Pre-convergence
resource digests, post-confirmation authority checks and pyinfra installation use its exact asset
bytes without rendering again. A pre-confirmation clean-source retry constructs a new plan;
post-confirmation source or material host drift retains the refusal above. Configuration, secrets
and asset bytes remain frozen through confirmation; with explicit-artifact or allowed-dirty input,
later local asset edits require a new invocation to apply them. Create-only scheduler authority
continues to come from fresh host observation. Genesis separately owns lifecycle-locked refresh of
an existing scheduler; the provisioning snapshot does not replace that lifecycle.

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

PostgreSQL's `hba_file` setting is startup-only. Keep the selected Ubuntu cluster's
`/etc/postgresql/<version>/<cluster>/pg_hba.conf` path rather than relocate it. Install the
candidate atomically with a recoverable copy of the previous file and its metadata, query
`pg_hba_file_rules` before reload/restart, and restore the previous file on validation failure.
Validate the exact selected-cluster path and live endpoint; an unavailable or contradictory
live parser must not authorize an authentication-file replacement. Preserve the native cluster
directory metadata.

Parser validation follows candidate installation and precedes reload/restart. The brief
crash/power-loss window between installation and validation/restoration is accepted.
A separate temporary PostgreSQL validation instance
was rejected for now because of its lifecycle and cleanup complexity. A later requirement to
eliminate that window needs a new decision. PostgreSQL documents the
[startup-only file setting](https://www.postgresql.org/docs/18/runtime-config-file-locations.html)
and the [rules view's inspection of current file contents](https://www.postgresql.org/docs/18/view-pg-hba-file-rules.html).

Provisioning installs hardened systemd assets and reconciles the first release from compatible
owned resources. It validates existing resources before writes, admits missing resources for
create-only convergence, and refuses conflicting authority. Fixed resource metadata/digests are
checked inside the locked `provision_authority` helper. Supplied credential proof remains a private,
read-only sensitive channel. Resource inspection and confirmed convergence do not adopt arbitrary
resources.

Before first durable success, provision can retry/replace a target using validated installed migration
provenance and recognizable partial resources. After first success, deploy owns release replacement.
Missing role plus missing database permits fresh creation; incompatible/partial identities refuse.
Convergence preserves existing credentials/data and proves final application authentication.
[Provisioning/recovery contracts](2026-09-18-operations-contracts.md#recovering-an-unfinished-first-installation)
define replay, schema, credential and command-specific admission boundaries.

Operational commands validate runtime/database/capacity; restore first inspects maintenance/credential/
role authority without assuming canonical availability, then obtains required capacity facts.
Cleanup uses filesystem/record authority without database health or backup-capacity dependencies.
Fresh locked helper checks own consequential authority; failed observation never means absence/empty.

## Public commands and consequences

The launcher is `./ops/taskman`. All commands accept `--json` and `--dry-run`. Dry-run performs
applicable validation and discovery, but skips managed-state mutation and confirmation. It may
build a required local artifact. `build` is always local; `verify`, `releases`, and `backups` remain
read-only with or without the flag. There is no generic force switch. `--yes` supplies ordinary deploy/provision confirmation only;
it does not acknowledge downgrade, authorize restore/cleanup, or turn JSON into permission.

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

### Release and database consequences

Deploy refreshes compatible scheduled code under the lifecycle lock, stages/reuses immutable content,
takes protected safety before new migrations, stops Taskman, applies remaining forward versions,
selects/starts/verifies and publishes success. Caddy remains running. A verified matching target
avoids unnecessary restart. Live migration fingerprints and compatible-prefix provenance, rather
than version ordering alone, determine safe work. Retry/replacement never manufactures success for
failed candidates; manual installation adoption and automatic rollback remain unsupported.

Rollback selects the immediately preceding successful installed release only when live schema
matches exactly, after fresh safety and typed confirmation. Restore validates exact backup/source/
schema, safety references and original/restored OIDs, loads/validates temporary content, swaps,
selects/verifies and records success before retired cleanup. Canonical observations remain application-
authenticated; derived observations/load use peer administrator with application execution role.
Trusted managed backups retain the accepted RESET ROLE privilege boundary, not a hostile-dump sandbox.
Same-input retry, explicit replacement/reapply and completed cleanup preserve original data and later
writes according to [restore contracts](2026-09-18-operations-contracts.md#explicit-restore-after-failed-deployment-or-provisioning).

Manual, scheduled and release/recovery safety backups use one capability: private credential/path/
live-prefix authority, capacity, native custom pg_dump/pg_restore-list, checksum and immutable pair
publication. Completed metadata/reference authority, not timestamp or schema similarity, protects
recovery points. Temporary normalization/deletion is only for proven-safe exact managed targets.
Local backups do not survive VPS loss; offhost storage is arranged independently.

Provision installs root-owned 0750 `/usr/local/lib/taskman/taskman-backup.pyz`. Its persistent
standard-library allowlist contains only scheduled adapter/backup dependencies, excluding deploy,
restore, SSH, SOPS and interactive code. Each systemd `Type=oneshot` process is short-lived. Timer uses validated calendar and
`Persistent=true`. Fixed
nonsecret environment carries roots/database connection/retention; credentials use protected pgpass,
not argv/environment. Hardened writes are limited to backup root and exact lifecycle lock. Adapter
statuses are 0 completed, 2 invalid config, 6 retryable backup, 10 unsafe/manual, 12 lock unavailable.
[Scheduler coordination](2026-09-18-operations-contracts.md#scheduled-helper-compatibility) preserves quiescence,
checksum/enablement authority and interrupted refresh behavior without a second intent record.

Cleanup preserves all history/recovery/provenance references and unknown/damaged authority; it
collects all bounded pages before typed confirmation, then deletes only confirmed eligible subsets
with fresh path/type/ownership/checksum/inode checks. Partial/lost-result evidence remains truthful.
It cannot alter databases, resolve recovery authority or refresh scheduler. Exact retention,
manifest-before-dump ordering and low-space admission belong to
[cleanup contracts](2026-09-18-operations-contracts.md#cleanup-while-recovery-is-unfinished-or-disk-space-is-low).

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
exact loopback/public readiness, and public HSTS. During startup, absent application or distribution
listeners may appear within the configured readiness timeout; topology and subsequent local/public
readiness share that budget, bounded by the overall 45-second verification deadline. PostgreSQL
must already be present on loopback. Failed or malformed listener observations, public managed
listeners, and ordinary EPMD fail immediately; every fresh observation retains those checks.
Startup-journal inspection reads the latest 100 service lines with a 64 KiB output limit per stream;
other fixed command observations retain their 9,216-byte bound. Empty journal evidence, failure
matches, command failure, timeout or output overflow fail closed. The three-second command cap and
overall verification deadline remain unchanged.
Ordered fixed summaries distinguish lifecycle
failure from readiness failure; public verify preserves the typed failed report. A deployment or new restore cannot
report success without a complete successful report for its expected release. Authority-validated
cleanup of an already successful restore is exempt from repeating readiness; it does not claim the
application is currently healthy.

Human and JSON results share bounded redacted facts/warnings and public schema 1. A coarse stage or
failure boundary is not a transaction history. Preserve the actual failed verification report and
primary failure; unavailable final facts do not reuse starting-state evidence. Known mutation survives
later uncertainty, including provisioning convergence and cleanup batches. Lost/invalid replies leave
unknown affected outcomes and retain earlier validated aggregate proof. [Failure/result contracts](2026-09-18-operations-contracts.md#failures-and-reporting)
own exact fields, exit categories, unavailable markers and in-process recovery/encoding precedence.
The runbook supplies manual state-specific recovery; reports do not generate recovery commands.

## Simplicity and maintenance

Future ops changes also follow the [operations development guidelines](../guides/development.md#operations-development):
prefer simple reliable procedures under a non-adversarial operator model, and Python over
substantial shell workflows. This does not retroactively rewrite the implemented baseline below.

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

### Implemented simplification decisions

These are the final ownership and simplification decisions.

- **Migration parsing:** `migrations.versions_from_filenames` owns filename grammar and
  sorted unique version extraction. `records.migration_record_versions` adapts record mappings;
  typed manifests project filenames directly. Database observation, fingerprint hashes,
  source selection and recovery policy remain with their consumers. Distinct filenames with
  duplicate timestamps are invalid history: provisioning refuses before plan/confirmation or
  mutation, closes transport and raises `MigrationOrderError`. The CLI reports exit 3 (`LOCAL_PREREQUISITE`), stage `controller`, fixed message
  `controller operation failed`, and `changed=False`. Full validation also applies
  to directly constructed typed objects; permissive timestamp slicing is not a compatibility promise.
- **SOPS injection:** decryption invokes one callable once, accepting only `CompletedProcess`
  with binary `bytes`/`bytearray` captures; `None` means empty capture. No alternate-signature
  retry or arbitrary result coercion remains. Both capture references are cleared and mutable
  captures wiped on success and supported refusal, including invalid sibling captures. This
  does not promise erasure of immutable Python values or generic cleanup of unsupported objects.
  Secret validation, rendering, redaction and fixed error/status boundaries remain unchanged.
- **Exact handler results:** handlers produce the exact supported result shape.
  For deploy/genesis, inspection failures admit inspection/5 alongside inspection/8;
  selection, service and history remain 8. Restore history/inspection use 11, restore
  selection/service use 8, and cleanup uses 10 for these categories.
  Service stop/start failures use service/8. The
  [exact failure contract](2026-09-18-operations-contracts.md#internal-helper-failure-recovery) owns evidence salvage,
  report precedence, whole-group observation replacement and bounded encoding.
- **Production-shaped provisioning:** target resolution and preflight authority are required;
  artifact-only injection and `None` authority are unsupported. Preparation has
  an empty scheduler-create set; only validated confirmed discovery authorizes creation.
  Missing evidence refuses before convergence. Missing resources remain supported and are
  distinct from missing authority. Refreshed refusals retain the confirmed snapshot and
  observer cleanup warnings; genesis retains locked scheduler refresh.
- **Exact systemd bytes:** installed `SystemdAsset` content is validated bytes, hashed and
  uploaded from the same immutable `SystemdPlan` described above. Backup template sources
  remain meaningful in `ManagedBackupAsset`; they are not a second installed-asset representation.
- **Internal names and admission:** unused internal Python names require no compatibility shims;
  documented YAML input aliases and derived paths remain. Provisioning admission uses production
  `validate_provisionable_host`. Compatible partial/managed resources are intended
  input; resource existence alone is not a refusal. Operational and restore admission remain
  distinct. The [overlap review](../research/2026-09-17-operations-test-overlap.md#production-admission-coverage-migration)
  retains the assertion mapping and the single obsolete export-only test deletion.
- **Imports:** selected-handler imports were explicitly skipped. Import-only measurements
  suggested smaller read-operation closures but did not establish end-to-end benefit; both
  production dispatch and the isolated harness would need changes while mutations retain most
  dependencies. Keep the direct callable map, fresh child processes, actual isolated archive
  entrypoints, immutable package bytes and native-effect substitutions. No dispatcher,
  persistent bytecode lifecycle or reused-process framework is justified by those measurements.

### Reasons for retaining explicit checks

These distinctions explain why superficially repeated code remains:

- Peer-admin maintenance SQL calls share argv/stdin mechanics but differ in tabular output,
  variable order and result consumption. A common adapter would add parameterization,
  archive wiring and fixture migration without sharing authority policy. Restore canonical
  observations remain application-authenticated; exact temporary/retired catalog reads use
  peer-admin authority. Registered loading uses peer-admin `pg_restore --role=<application-role>`, with
  `--no-owner` and `--no-privileges`; trusted managed backups retain the accepted RESET ROLE
  privilege trade-off. It is not application-authenticated loading.
- Provisioning confirmation projects free-space counters at two shapes through shared resource
  authority. A recursive projection could erase unrelated drift; refreshed admission separately
  refuses insufficient capacity. Free-space-only change is allowed, material drift is not.
- Listener topology distinguishes pending from unsafe evidence and refuses unsafe topology
  immediately. HTTP readiness retries another condition; both share one finite readiness budget.
  Merging their polling loops would obscure policy without removing state. Journal capture
  remains bounded to 64 KiB per stream with fixed public summaries.
- PostgreSQL parent-PID checks at inspection and before/after mutation answer different freshness
  questions. Replacing their workflow belongs to the separately parked PostgreSQL Python proposal.
- Caddy package authority checks both `/lib` and `/usr/lib` systemd paths and resolved identity.
  A shared socket does not prove ownership; duplicate, malformed or contradictory evidence refuses.
  Fragment-only inspection would reintroduce the observed native acceptance failure.
- Lock-root creation establishes 0755 despite umask and tolerates ordinary creation overlap while
  preserving safe existing roots. The systemd state home explicitly uses 0700, agreeing with
  admission. Weakening admission to accommodate default directory modes is not equivalent.
- Scheduler absence checks LoadState before UnitFileState; unloaded/error discovery refuses.
  Failed cluster listing or SQL is unavailable evidence, not authoritative absence. Nonempty
  broad-track selection remains required for provisioning database discovery.
- Account setup/reset controller adapters implement the authentication component's submission
  contract, delegate domain decisions to Accounts and retain CSRF-protected forms. Combining
  adapter forms or redirects offered no demonstrated simplification.

Full-history validation and bounded incremental reference checks, restore OID/replacement binding,
backup publication/deletion ordering, pinned SSH after firewall changes, candidate Caddy validation
and native PostgreSQL HBA/cluster authority retain their distinct safety owners. A generic
observer, recovery, retention or guarded-service framework is rejected. Small repeated JSON/digest
helpers, application-version parsing, toolchain lookup or protected secret wrappers require a
concrete net benefit before extraction. CLI UX and PostgreSQL Python proposals remain separate.
No runtime saving is claimed by these decisions.

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
- strict host identity, literal shell argv, bounded output, and helper-owned subprocess timeout
  cleanup with deterministic startup synchronization; this does not prove remote SSH or
  service-manager work terminates after caller loss;
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

Identified local/native baselines and artifacts belong to the
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md#scope-and-evidence-boundaries).
They do not prove later changed source or authorize future external actions.

The transient archive's required runtime dependency set includes the release manifest and error
modules. Tests execute both zipapps with `-I -S`: `-I` alone can resolve an editable workstation
installation through site packages and hide missing archive members. This isolation requirement
was established by reproducing a missing-dependency startup failure, not just inspecting filenames.

Local tests and container builds do not prove systemd PID 1, UFW, public DNS, ACME, Resend delivery,
reboot behavior, or a complete real PostgreSQL restore. Identified completed native
acceptance and its limits are recorded in the
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md). Future runs require
separate authorization and must cover first/second provision, HTTPS/HSTS,
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

# Operations contracts

Status: accepted, implemented contracts. Updated: 2026-09-18.

This is the canonical reference for Taskman's artifact, record, protocol, admission,
confirmation, recovery and failure-evidence contracts. The
[deployment architecture](2026-09-09-dedicated-host-deployment-design.md) owns supported topology,
security boundaries, component ownership and architectural rationale. The
[runbook](../guides/deployment.md) owns operator procedures; the
[development guide](../guides/development.md#operations-verification) owns runnable verification commands.
The [acceptance report](../research/2026-09-17-operations-vps-acceptance.md) owns identified
historical evidence and native limits. These contracts grant no host or publication authority.

## Reading map

- [Compatibility](#one-time-compatibility-boundary) and [operator contract](#operator-contract).
- [Artifact identity/resolution](#target-resolution-and-immutable-identity),
  [deployment admission](#observed-state-and-deployment-admission),
  [migration/downgrade safety](#database-and-downgrade-safety).
- [Backup protection/history](#backup-protection-and-successful-history),
  [restore recovery](#explicit-restore-after-failed-deployment-or-provisioning),
  [cleanup](#cleanup-while-recovery-is-unfinished-or-disk-space-is-low).
- [Packaged admission](#packaged-admission-boundaries),
  [scheduler coordination](#scheduled-helper-compatibility),
  [protocol budgets/pagination](#reconciliation-procedure-and-transport).
- [Mutation/failure evidence](#failures-and-reporting),
  [internal recovery/encoding](#internal-helper-failure-recovery),
  [verification requirements](#acceptance-scenarios).

## Reliability scope

Assume trusted operators and no deliberately hostile interference with managed host state.
Support modeled ordinary interruption, lost replies and accidental confirmation-to-execution
drift. Cooperating helper lifecycle sections and scheduled backup/retention coordinate through the
configured installation's lifecycle lock, fresh checks at material consequence and reacquisition
boundaries, atomic publication and bounded native subprocesses. This is section-level coordination,
not host-wide whole-command exclusivity: scheduler refresh releases the lock while waiting.
Controller-driven provisioning convergence and interactive create-admin do not hold that lock;
a competing operation is not guaranteed to fail with status 12 merely because one of those commands
is running. Different installation-root configurations also do not share one host-wide lock.
Locked observations are coherent against cooperating locked writers, not concurrent unlocked
provisioning. The controller's separate probes are not one atomic snapshot.

Provisioning does not retain a durable admission reservation or guarantee backup suspension after
interruption. Its timer creation/refresh paths can activate backups during the invocation, so a
manual timer stop alone does not establish an exclusive provisioning interval. Remote SSH work and
service-manager migration/admin jobs may survive controller loss; timeout cleanup of helper-owned
subprocesses is not universal caller-loss containment. Before retrying uncertain work, inspect and
establish that outstanding work is quiescent rather than treating a free lifecycle lock as proof.
Enforced whole-command admission, persistent suspension and reservation-based manual recovery are
proposed in the [dedicated lock-coverage design](2026-09-18-provisioning-lock-coverage-proposal.md),
not implemented guarantees.

Preserve scoped path/permission/checksum/destructive-target checks; unavailable or contradictory
authority refuses without guessing. No generic journal, background recovery,
hostile-administrator defense or repair of arbitrary corruption/unrecorded migration effects is
promised. Additional recovery branches require a concrete supported failure and an explicit
amendment if they change an accepted guarantee.

Follow the [operations development policy](../guides/development.md#operations-development). Focused Python
procedures own multi-step orchestration; short shell remains suitable when simpler. Do not repeat
unchanged validation within a locked phase without an intervening action that can invalidate it.
Verification covers distinct durable states and public entry paths, not every equivalent timing/flag
permutation. The parked PostgreSQL refactor and proposed CLI UX do not alter these accepted contracts.

## One-time compatibility boundary

A one-time compatibility exception applies only to the historical pre-reconciliation disposable
staging installation: its old authority is excluded rather than upgraded/adopted/converted.
Identified evidence belongs to the acceptance report and recovery obligations to the
[environment inventory](../inventories/operations-environments.md#staging-recovery-retention).
Any future reset/provider/DNS action needs its own authority; supported upgrades retain their
compatibility and recovery obligations.

The first supported baseline is the model defined here: digest-bearing release IDs, manifest schema
3, installed-release and successful-selection schema 2, the specified backup/protection/restore
records, and OTP 29.0.6 / Elixir 1.20.4. Keep these explicit version numbers; renumbering them adds
no simplification. Only these formats/runtime are accepted. No readers, constructors or fallback paths accept
source-only IDs, manifest 2,
unversioned release/selection records, and OTP 27 / Elixir 1.18. Explicit old artifacts fail local
validation with exit 2. Old host authority fails with exit 10 before release/scheduled-helper payload
upload or managed mutation; transient read-only inspection transport remains available.
Do not convert or delete it automatically. Unsupported local cache entries are ignored and preserved.
Old backups are not supported recovery inputs: their source releases are outside this baseline.

This is a single transition exemption, not permission to break installations on every upgrade.
Releases and backups created under the new baseline remain usable across subsequent supported
deployments. Future format/runtime changes must preserve their supported readers and recovery
paths or provide an explicitly designed, verified migration and retirement policy. Never silently
invalidate retained rollback/restore material. Keep scheduled-helper refresh, old-process quiescence,
atomic publication, and supported-version tests for future upgrades. No speculative multi-version
framework is needed now. The transient controller still ships its matching wire-protocol helper.

The record fields below are the supported exact contract; dual-format handling is absent. `backup_id` and `recovery_backup_ids` have
different operational meanings and remain. Every installed record has full artifact provenance.

## Operator contract

Existing-host reconciliation remains one `deploy` command. The affected command interfaces are:

```text
taskman deploy ENV [--artifact ARCHIVE] [--migration-policy POLICY]
                   [--yes] [--allow-dirty] [--allow-downgrade]
                   [--dry-run] [--json]

taskman provision ENV [--artifact ARCHIVE] [--migration-policy POLICY]
                      [--yes] [--allow-dirty] [--allow-downgrade]
                      [--dry-run] [--json]

taskman build [--allow-dirty]

taskman restore ENV BACKUP_ID [--replace-unfinished | --reapply] [--dry-run] [--json]
```

Existing options retain their spelling. No `resume`, `redeploy`, generic `--force`, or manual
adoption mode is introduced. Without an explicit artifact, the checkout selects the desired
source/build inputs and remains clean-only by default. `--allow-dirty` permits `build`, `deploy`, or
`provision` to build a dirty local checkout; it is not restricted by environment name. An explicit
artifact selects its exact validated bytes independently of checkout cleanliness. Selecting an
artifact whose manifest records dirty provenance itself acknowledges that provenance;
`--allow-dirty` is optional and accepted redundantly for such an artifact, but is an argument error
with an explicitly selected clean artifact. A failed existing release need not become healthy
before replacement.

`taskman build --allow-dirty` creates a dirty-provenance artifact without installing it. Provisioning
may instead build and install the dirty checkout directly. Dirty-source permission never substitutes
for an ordinary deployment or provisioning confirmation, downgrade acknowledgment, or
migration-policy declaration.

`--yes` acknowledges the ordinary deployment or provisioning plan. `--allow-downgrade` is accepted
by both commands and independently acknowledges a known downgrade or unknown ordering against an
existing baseline; neither flag substitutes for the
other or for a migration-policy declaration.
An interactive known downgrade or unknown ordering displays the applicable baselines, the target,
and the reasons, and requires
a separate explicit `yes` unless the downgrade flag is present. Ordinary confirmation remains
required unless `--yes` is present. Interactive refusal cancels without mutation using the existing
confirmation-cancelled result. Missing required acknowledgment in non-interactive execution is a
safety refusal, exit 10, before upload or managed mutation; it must not wait for input. `--yes` is
valid only for deploy and provision, `--allow-dirty` only for build, deploy, and provision, and
`--allow-downgrade` only for deploy and provision; other uses are argument errors, exit 2. `--json` is never
confirmation.

`--replace-unfinished` is restore-only (other commands reject it with exit 2). It permits
replacing an unfinished restore's selected backup, not bypassing validation or typed data-loss
confirmation. Without it, executing a different backup while a restore is unfinished refuses
with exit 10 and explains this repair command. The flag is harmless when no unfinished restore
exists or the requested backup already matches; neither case authorizes extra database deletion.

`--reapply` is restore-only and requests a fresh restore of a backup that was already restored
successfully. It is mutually exclusive with `--replace-unfinished`; using both or passing it to
another command is an argument error, exit 2. It does not acknowledge data loss or bypass validation.
An unfinished restore refuses `--reapply` with exit 10 and directs the operator to ordinary retry
or `--replace-unfinished`. With no unfinished restore, it permits a fresh restore even when the
requested backup matches the latest completed restore; for a different backup it is redundant.

Dry-run resolves and validates the target and observes the host, but does not prompt, refresh the
installed helper, publish records, change services, or mutate the database. It may build a local
artifact. Missing confirmation flags do not make a dry-run fail; its plan reports required
acknowledgments. Incompatible schema or missing required migration policy still refuses.

For an unfinished restore, requesting a different backup with `--dry-run` previews replacement
even without `--replace-unfinished`. The plan explicitly states that execution requires that flag
and fresh typed confirmation. Do not normalize databases, change the binding, or prune backups
while previewing. All replacement safety checks still apply. In contrast, `--reapply` selects a
fresh same-backup restore rather than a completed-outcome check and must be supplied to preview
that operation; dry-run does not infer it. Mutually exclusive flags remain an argument error.

The plan names physical current, last successful selection, exact desired release and archive
digest, artifact origin, clean or dirty source provenance, observed schema, pending migrations,
migration policy, downgrade evidence, retained recovery points, exact superseded attempt backups
proposed for pruning, and whether the scheduled helper
needs refreshing. Dirty provenance is prominent in human and JSON plans and results. Ordinary
confirmation authorizes only the displayed material facts. The helper revalidates them under the
lifecycle lock. A changed baseline, schema, target identity, unresolved protection set, or installed
helper identity refuses before further consequences and requires a new plan and confirmation, even
when the previous invocation used `--yes`. Unrelated newly scheduled backups do not invalidate the
plan.

Provisioning applies the same displayed-plan-only authorization rule to first installation and
reconciliation of an unfinished first installation. `--yes` removes the interactive prompt; it does not weaken host
admission, artifact validation, secret handling, or apply-time revalidation.

## Target resolution and immutable identity

Separate source/build input identity from exact artifact identity. New clean IDs have this exact
form:

```text
<application-version>-<12-hex-source-sha>-ubuntu26.04-amd64-otp<otp-version>-<64-hex-archive-sha256>
```

Dirty IDs append one literal terminal marker:

```text
<application-version>-<12-hex-source-sha>-ubuntu26.04-amd64-otp<otp-version>-<64-hex-archive-sha256>-dirty
```

The full digest avoids a new truncated-hash collision policy. Manifest schema version 3 fixes this
field to SHA-256, so the ID does not repeat a static algorithm label. Full source revision remains
in metadata. Validate the complete ID and filename/path component bounds before use; reject
oversized identities rather than truncating. The archive retains its existing `taskman` top-level
layout and does not embed this external ID. Package to a temporary filename, hash those final bytes,
then name the final archive, manifest, checksum, and private artifact directory. Renaming must not
repackage the archive. Append the literal `-dirty` suffix only when the frozen source snapshot was
dirty.
Identical bytes with the same clean-or-dirty provenance have the same ID; a non-identical rebuild
gets a different immutable directory. Clean and dirty builds intentionally have visibly distinct
IDs even when their final archive bytes match. Do not overwrite an installed release.

Detached `built_at` is descriptive, not identity or freshness authority. Two valid manifests for
the same archive may have different build timestamps; compare the archive digest and all source,
target, toolchain, builder, layout, and migration identity fields, not that timestamp, when reusing
installed content. Keep the originally published installed provenance unchanged. Conflicting
identity fields under the same ID still refuse.

The release ID does not contain a working-tree snapshot digest. The archive digest identifies the
bytes that can actually run, while the source revision identifies the clean commit on which local
work was based. Dirty artifacts add only the terminal `-dirty` provenance marker so their risk is
immediately visible and an explicitly selected dirty artifact can imply `--allow-dirty`. Worktree
differences in comments, documentation, or unused source do not add another identity dimension;
only the clean-or-dirty class and packaged bytes matter. Differences that affect the deployed
release produce a different archive digest and therefore a different ID.

Artifact manifests use only schema version 3, with exactly `schema_version`, `application`,
`application_version`, `source_revision`, `release_id`, `built_at`, `target_os`, `architecture`,
`otp_version`, `elixir_version`, `node_version`, `hex_version`, `rebar3_version`, `builder_base_tag`,
`builder_base_digest`, `migrations`, `top_level`, `artifact_sha256`, and strict boolean `source_dirty`. Validate agreement between the artifact
digest field, the digest portion of the ID, the detached checksum, and the actual archive.
`source_dirty` records whether the captured source contained tracked or non-ignored untracked
changes relative to `source_revision` and must agree exactly with the presence of the terminal
`-dirty` suffix. `built_at` may differ between manifests for the same exact release identity. All
fields that describe source class, archive, target, toolchain, builder, layout, and migrations must
still agree. Preserve the originally installed manifest when reusing an identical installed
release rather than overwriting its provenance. Old manifests, source-only IDs, and unsupported
runtime pairs refuse; no implicit clean provenance or historical artifact exception remains.

Installed release records use only schema version 2: exactly `release_id`, `source_revision`,
`artifact_sha256`, `migrations`, `schema_version: 2`, and `artifact_manifest` containing the
full validated detached manifest. All duplicated identity fields must agree. Publish it with the
immutable release. Unversioned records refuse; there is no in-place retrofit or alternate reader.

Without `--artifact` or `--allow-dirty`, validate the clean checkout and derive exact source,
application version, target, runtime/toolchain, builder tag/digest, and migration fingerprints.
Then perform read-only host observation and resolve in this order:

1. Matching physical installed release with sufficient validated provenance.
2. Matching last-successful installed release with sufficient provenance.
3. Another exact-input installed release, deterministically ordered by full release ID.
4. Verified exact-input local cache artifact, deterministically ordered by full ID and path.
5. A normal fresh build from the identified clean source.

With `--allow-dirty`, require an identified Git checkout with a valid `HEAD`, then capture a private,
stable build snapshot containing tracked files plus non-ignored untracked files, while honoring
tracked deletions. Git-ignored files, repository metadata, controller state, and secrets remain
excluded. Apply the clean exporter's path, member-type, and bounds checks; refuse submodules,
symlinks, unsupported file types, unsafe paths, or relevant worktree changes during capture. Build
from that frozen snapshot rather than the changing checkout. No whole-snapshot digest is persisted
or used for identity.

A dirty automatic deployment or provisioning run must build its frozen snapshot before it can know
the exact artifact identity; it cannot reuse host or local artifacts merely because they share its
base revision. Once the archive is built and hashed, ordinary exact-identity reuse applies within
the dirty provenance class, including reuse of an identical installed dirty release where the
command's existing admission rules permit it. The frozen artifact, not a later worktree read, is
the target bound into the plan. A clean checkout passed with
`--allow-dirty` follows the normal clean resolution path and records clean provenance.

Every accepted installed record contains full validated provenance. Missing, corrupt, or unsupported
authoritative metadata refuses; an explicit artifact cannot bypass it. Invalid or unsupported local
cache entries remain ignored and preserved. Arbitrary retained upload files are not a new automatic
artifact cache.

Host reuse requires no local archive or upload. It validates the exact installed record, managed
tree/launcher authority, and retained provenance; it does not claim to reconstruct an archive hash
from extracted files. Explicit artifacts remain authoritative and may reuse a matching installed
record without re-extraction after local validation. Revalidate the clean source identity before
confirming an automatically resolved clean target; source drift requires resolution again. Dirty
targets are already fixed by their private frozen snapshot and exact artifact digest.

`build` never connects to the deployment host. It still requires the workstation's build prerequisites,
including Docker/BuildKit capable of building the pinned `linux/amd64` target; this does not imply
offline operation or verified support for every workstation CPU architecture.

Provisioning shares the updated build process and the single supported artifact/record formats. It may build a dirty checkout or accept an explicitly selected dirty
artifact, and supports unattended ordinary confirmation through `--yes`.
It admits a new managed installation or a validated unfinished first installation, including a
different desired artifact. Once a successful selection exists, release changes use `deploy`.
The existing explicit replay of the exact completed first installation remains available only
under its existing constraints; it cannot replace that release. Unrecorded releases, unrelated
resources, and contradictory installation metadata still refuse.

### Recovering an unfinished first installation

The command boundary is the first durable successful selection. Before that record exists,
`provision` may retry or replace the desired release; `deploy` still refuses. After it exists,
`deploy` owns release replacement. A failure after publishing that first selection is therefore
on the completed-installation side of the boundary, even if the controller lost its response.

Local changes do not require the original artifact. A bare rerun resolves the current clean source
using the same installed-release, cache, and build ordering as deploy; `--allow-dirty` and explicit
artifacts retain their normal meanings. All accepted installed records have full provenance. The failed
candidate need not be healthy. Multiple valid installed candidates from interrupted attempts are
permitted; their presence alone is not conflicting authority. Preserve unrelated staged content
unless existing exact-path temporary cleanup rules prove it safe to remove.

To resume an unfinished installation, inspect the resources that already exist using the checks
below. This is not a requirement that every listed resource exist when the rerun starts. Missing
resources may be created by the confirmed provisioning plan; prerequisites must exist and pass
validation before the step that uses them. Absence refuses only when it makes existing state
unsafe or unprovable, such as a populated database whose applied migrations lack provenance.

- Existing resources are consistent with the configured Taskman installation: the `taskman`
  account, configured installation/backup roots and derived paths, `/etc/taskman`, and the managed
  systemd unit paths. Check any existing `/etc/systemd/system/taskman.service` and related units,
  PostgreSQL installation, and service account; do not require missing siblings merely to admit
  a partial installation. Require the relevant service/database prerequisites before using them.
  Apply the Caddy package/unit/configuration ownership checks to existing resources, including
  `/etc/caddy/Caddyfile`, and refuse contradictory resources. Reserved application, distribution,
  and database listeners must be attributable to the managed services and loopback-only; public
  listeners must belong to the validated Caddy setup. Missing resources in a recognized partial
  installation can still be provisioned; this does not require every service to be running.
- Create missing prerequisites through the confirmed plan, then, before release/database consequences,
  validate `/etc/taskman/taskman.env` and
  `/etc/taskman/pgpass` under their existing root-owned, mode-`0600`, non-link and content rules
  without exposing values; prove the configured PostgreSQL cluster, role, and database identity.
  Validate installation-path ownership/modes and every existing release, selection, backup
  protection, and restore binding under their exact record/path rules. Successful selection
  history must be empty for unfinished-first-install recovery.

Admission uses the observed resource configuration, ownership, paths, and records, together with
the operator's confirmation of the displayed convergence plan. It does not try to prove which
program originally created those resources. Each existing resource must either satisfy its
documented Taskman validation rules or have a specifically supported safe partial state; absence
permits creation, while conflicting configuration, unsafe ownership/links, foreign database
contents, and unrecorded releases refuse. A matching name alone is insufficient. The plan identifies
existing resources that will be reused or converged before confirmation. Existing populated
databases require installed migration provenance; a newly initialized empty database must be
proved empty and have the expected role/ownership. No marker can substitute for these checks.

The provisioning marker supplies no authority. The retired path
`/var/lib/taskman-provisioning.state` is not consulted, followed, or modified. No old-marker
classification or compatibility fixtures are needed, and no replacement flag file or provenance
token is introduced. Before the first
resource change, a failure leaves the host eligible for the same resource-based inspection;
after partial convergence, inspect the resources actually present. Reuse the concrete validators
in `host/facts.py`, `host/acceptance.py`, and helper path/credential/record modules, replacing their
marker-based classification rather than treating its old absence refusal as a retained rule.

The removed marker requirement had no valid authority: a protected constant string did not establish
resource compatibility or prior authorization, and interrupted marker publication could itself
block recovery. Explicit plan confirmation supplies authorization for the current invocation;
resource validation supplies evidence that the proposed actions are safe within its scope.

A missing `current` is allowed here; an existing `current`
must name a valid managed release. Applied migration fingerprints must be proved by validated
installed records from the unfinished installation or protected migration targets, independently
of the newly desired artifact. Every relevant record containing an applied version must agree.
The live versions must be an exact prefix of the desired target. Staging names alone cannot prove
which migrations ran. An unobservable database, foreign schema, or missing/conflicting provenance
refuses without adopting or repairing it.

A proven empty initial database retains the existing initial migration policy (`restore-required`,
selected automatically for first initialization, without a backup of a nonexistent prior schema).
Empty migration history alone is not proof of an empty database. Once any migrations
have committed, further versions require explicit `--migration-policy backward-compatible`, also
on provision; the deploy policy/refusal rules apply. Before those additional migrations, create a
fresh validated backup and protection with no successful-selection baseline. Select this backup's
source using the shared backup-source rule below, whether physical current is present or absent.
The requirement for existing migration provenance does not apply
to a proven empty initial database, which needs neither a previous release record nor `current`.
Backup creation supports an unfinished installation without physical current; the backup record
format is unchanged. A partial-schema dump retains the existing manual-restore caveat.

Provisioning convergence must preserve the existing database and its credentials during recovery.
The final release procedure stops any running Taskman before migration, uses the desired installed
target, verifies the complete outcome, and publishes the first successful selection with
`previous_release_id: null`. It records the actual starting physical selection (possibly null) and
all resolved recovery backups. Failed attempts never become synthetic successful predecessors.
Before publishing new records, provisioning must ensure the compatible scheduled helper using the
same sequence as deploy when a scheduler already exists: pause scheduled backups, wait for any
running backup to finish, replace and checksum-verify the backup program, then restart the timer
if enabled, following the locking rules in Scheduled helper compatibility.

Provision requires the same independent downgrade acknowledgment as deploy when an applicable
baseline proves the desired release is older or its ordering cannot be determined. Before first success, a failed candidate may already
have run or applied migrations, so absence of successful history does not waive acknowledgment.
A fresh installation without a selected release or evidence of migration attempts has no downgrade
baseline and needs no downgrade acknowledgment. `--allow-downgrade` never weakens schema checks
or permits provision to replace a completed installation.

## Completed record publication

| Record | Managed location / authority |
| --- | --- |
| ReleaseRecord v2 | `releases/<release-id>/.taskman-release.json`: full artifact manifest v3 plus exact release/source/archive/migrations. Root:taskman 0600; directory/executables 0750, ordinary runtime files 0640. |
| BackupRecord | `<backup_root>/<backup-id>.json` beside its custom dump: exactly `backup_id`, `created_at`, `dump_sha256`, `source_release_id`, `migration_versions`, `source_database_size_bytes`. Source size is a nonnegative integer; ID is backup-32-lowercase-hex, digest lowercase SHA256. |
| SelectionRecord v2 | `deployments/selections/selection-<digest>.json`: verified outcome, successful/observed predecessors and exact recovery references, defined below. |
| Migration backup protection | Derived `deployments/backup-protections/` plus protection retirement authority: exact unresolved migration-attempt backup/target/baseline, defined below. |
| Restore binding | Fixed `deployments/restore-target.json`: exact input/original/restored OIDs and bounded safety/replacement authority, defined below. |

Creation/selection timestamps are canonical whole-second UTC. Migration fingerprints contain exact
filename/SHA256; observed versions are sorted unique nonnegative integers. Installed records publish
with immutable content before migration consequences; successful selections publish only after
required verification. Create-once/atomic publication and directory fsync preserve ordering; records
contain no phase journal/process identity/recovery command. Missing metadata and failed observation
are distinct. Unsupported formats refuse without conversion or implicit provenance.

## Observed state and deployment admission

The helper owns one coherent state model with distinct physical selected release, latest successful
selection identity, live migration versions, and unresolved backup protections. Successful history
must be internally valid, but it need not name the physical current release during deploy planning
or execution. A deploy-specific discovery mode exposes this distinction; do not weaken strict
admission for unrelated mutation commands by globally disabling history checks.

Existing-host reconciliation requires a non-empty valid successful history, a valid installed
release selected by the `current` symlink, valid database credentials and database authority, safe paths, and
compatible schema.
Missing current, unmanaged directories, unsafe links, malformed authoritative records, conflicting
fingerprints, unobservable database state, and unrelated schemas refuse. Initial provisioning keeps
the separately constrained unfinished-installation admission above.

Fresh confirmation authorizes a new desired transition. It need not prove whether an earlier
mismatch arose from a lost success-record write or a trusted operator selecting another installed
release. This is not manual installation adoption: both the physical release and history must be
valid managed authority. Never fabricate a successful entry for the interrupted target.

The existing `verify` and rollback command admission remains strict until reconciliation completes.
Restore admits the validated unfinished deployment described below. Administrator preflight does not require successful-selection
history to match physical current; preserve its existing host/runtime/database checks. Fresh checks
that `current` selects the intended release, the running service's executable belongs to that release,
and readiness passes can establish the baseline for separately authorized private
administrator creation and login acceptance before reconciliation. Such acceptance does not repair
or prove completed deployment history. Listings continue to expose validated records without
claiming deployment success. The shared backup capability and scheduled backup path must support
the validated selected release, observed database migration state, and new protection records,
because they may run during an unfinished deployment. This does not authorize those paths to
select or repair releases.

### Shared backup-source selection

Every caller of the shared backup capability uses the same source-selection rule, including
deployment/provisioning retries, scheduled and manual backups, and restore safety copies.
Under the lifecycle lock, validate the observed database's live migration prefix and its installed
provenance using the calling operation's admission rules. All relevant records containing an
applied version must agree on its filename and fingerprint; a preferred source never overrides
conflicting provenance.

Prefer physical current only when its validated installed record proves the entire live prefix:
the observed versions are an exact prefix of that record's migration sequence, with matching
fingerprints for every applied version. If current is absent or does not cover that prefix, choose
the first qualifying record in ascending full release-ID order from the installed releases already
accepted as migration provenance. On an existing installation, these are the physical and last
successful releases and installed targets named by unresolved migration protections; before first
success, apply the unfinished-installation provenance rules above. Do not expand this set merely
because another installed release or the newly requested artifact contains matching versions.
If no single eligible record proves the entire prefix, or relevant fingerprints conflict, refuse
backup creation. This rule does not relax the caller's current/history/database admission rules.

Persist the chosen ID as `source_release_id` and the actual observed versions as
`migration_versions`; the backup record format is unchanged. The source identifies migration
provenance, not physical application selection or a successful/healthy release. A dump whose
versions are only a partial prefix of its source release remains a safety copy that may require
manual recovery; automatic restore still requires the source release's complete schema.

## Database and downgrade safety

The live sorted migration-version sequence, not the last successful release's assumed schema,
determines remaining work. It must be an exact prefix of the desired target's version sequence.
Every applied version must also have consistent filename/hash provenance in relevant installed
records: the last successful release, physical current, or installed migration targets named by
unresolved backup protections. Different hashes for an applied version, missing provenance, removed
versions, or a non-prefix sequence refuse. A desired artifact alone is not evidence of the migration
file contents previously applied to the database. This supports partial committed migration prefixes
without adopting unrelated database state. Nontransactional migration side effects not represented in migration history remain
a manual recovery caveat; do not claim this model makes such migrations replay-safe.

No pending versions means `no-change` is the default. Additional versions on an existing database
require an explicit `--migration-policy backward-compatible` or `--migration-policy restore-required`
declaration for deploy. The latter permits forward migration when the previous release cannot use
the migrated schema; it does not perform an implicit restore. Deploy creates a protected
pre-migration backup before applying pending versions. Returning to an incompatible prior release
requires restoring that matching backup and loses writes made after it. Provisioning an unfinished
first installation with applied migrations still requires `backward-compatible` for additional
versions. `no-change` with pending versions refuses. A supplied `backward-compatible` declaration
remains acceptable when a retry finds all versions already applied. A `restore-required`
declaration with no pending versions is unnecessary and refuses as a mismatched policy. Missing
required policy is exit 2; contradictory schema or policy is exit 10. Migrations are not reversed.

For deploy and provision, compare the desired target against physical current, the last successful
release when present, and installed migration targets named by unresolved backup protections.
For an unfinished first installation without physical current, also include previously installed
records used to prove the live migration prefix: each validated record containing at least one
applied version with matching fingerprints. This conservatively includes candidates that may have
run; display that evidence without claiming they definitely executed. Merely staged or unused
installed artifacts with no such evidence are not baselines. Deduplicate baselines by full release
ID. A known downgrade is a lower SemVer precedence or a strict source ancestor of any baseline. Compare valid
SemVer values by numeric core and standard prerelease precedence, ignoring build metadata. Existing
version strings that are not valid SemVer have unknown version ordering, not lexical ordering.
Source ancestry uses locally available full commit objects and bounded read-only checks; do not
fetch, deepen a clone, or contact a remote to infer order. Missing objects or divergent histories
are unknown. Any established downgrade requires acknowledgment even if another signal disagrees.
Identical source/version with different archive bytes is a rebuild, not inherently a downgrade.
Unknown ordering against any of the applicable baselines defined above requires a separate
interactive acknowledgment or `--allow-downgrade`; a known forward comparison in another signal
does not waive that requirement.
Explain whether a downgrade is established or ordering is unknown, including why, rather than
labeling uncertainty as a detected downgrade. Matching complete Git commit IDs establish source equality
without requiring commit-object lookup; identical version strings establish version equality.
Thus an identical-source/version rebuild is not classified as unknown merely because Git objects
are unavailable. No baseline, as on a fresh installation, is distinct from unknown ordering and
requires no downgrade acknowledgment. None of these classifications
proves database compatibility or grants permission to restore data.

Bind the sorted baseline release IDs into discovery and the confirmed plan as
`downgrade_baseline_sha256`, using SHA-256 of their canonical ASCII JSON array without whitespace
or trailing newline. The empty baseline hashes `[]`. The helper re-derives that set under the lock
and refuses drift before consequences; the controller owns version/ancestry classification and
acknowledgment. Existing immutable record validation binds each baseline's source/version facts.

## Backup protection and successful history

A general journal is unnecessary: fresh confirmation and validation of the selected release and
observed database migration state authorize reconciliation. However, a completed dump alone cannot
identify its protecting role after failure,
and ordinary retention can delete an unreferenced pre-migration backup. Use a narrow durable
protection record, published after backup validation and before any migration invocation.

Derive `deployments/backup-protections/` from the installation root; add no configurable root.
Each create-once `backup-<32-hex>.json` contains exactly:

- `schema_version`: integer `1`;
- `backup_id`: the completed backup's exact ID;
- `base_selection_id`: the full `selection-<64-hex>.json` filename of the last successful selection,
  or null for a protection created before the first successful selection. A null-baseline protection
  is unresolved only while successful history is empty; after first success it must be explicitly
  resolved by that selection's recovery references or refuse as contradictory authority;
- `target_release_id`: the installed immutable target whose migrations may run;
- `attempt_number`: a non-negative integer, zero for the first protection under this baseline,
  then one greater than the highest retained attempt number, allocated under the lifecycle lock;
- `created_at`: canonical whole-second UTC timestamp.

The backup record already supplies the source release, actual pre-migration versions, size,
and dump checksum; do not duplicate them. Validate every reference and version-prefix relationship.
The protection ID is its backup ID, not an operation/correlation ID. Use existing atomic create-once
publication and directory fsync rules, root-owned non-link directories `0750` and files `0600`.
Malformed authority refuses; it is not ignored as an unknown file.

Before every invocation that will apply additional migrations, create a fresh validated backup of
the then-current database and publish its protection under the lock. This intentionally does not
reuse a potentially stale dump after application writes. An interruption after dump publication but
before protection publication cannot have run migrations; that dump follows ordinary retention.
An interruption after protection publication conservatively retains the backup even if no migration
actually ran. Do not infer execution from the protection record.

An unfinished migration/recovery sequence is identified by its successful-history baseline,
including the null baseline before first success. Changing the desired release or retrying a
failed command does not start a new sequence. Its attempt-backup allowance is at most five:

- the original pre-migration backup, identified by protection `attempt_number: 0`;
- the newest validated and durably protected attempt backup;
- up to three most recent intermediate attempt backups, ordered by `attempt_number`.

Count each backup only once; the first backup is initially both original and newest. Timestamps
are descriptive and never decide which backup is the original or most recent. Attempt numbers
must be unique within a baseline; missing original protection or contradictory ordering refuses
rather than guessing. Before first installation, the original means the first recovery backup
actually taken; it may already contain partial migrations and is not promised to restore an
earlier empty database automatically.

Backups independently referenced by retained successful history or an active restore remain
protected outside this allowance. Do not spend the three intermediate slots on those independently
protected backups. Scheduled/manual backups without an attempt protection record retain their
existing policy; never classify a dump as a failed-attempt backup by its timestamp or schema.
An older intermediate no longer occupies an attempt-retention slot. If successful history or an
active restore still references it, retain the backup under that reference; otherwise it is
eligible for deletion under the confirmed pruning procedure below.

Before further migrations, create and validate a fresh backup and durably publish its protection.
Only then retire superseded intermediate protections to converge to the five-backup allowance.
This temporarily permits a sixth attempt backup; a literal five-record admission limit would
prevent safe replacement and must not be introduced. Discovery and planning must also accept
this interrupted pre-pruning state. Before another backup is created, complete any previously
authorized pruning through a newly confirmed plan using the already validated newest protection.
Repeated attempts must not accumulate protections until an arbitrary count blocks migration.
Keep the format-specific byte bounds below as serialization limits, not a 64-attempt recovery
budget. Five retained attempt references and the transient replacement fit within that allowance;
independent history/restore references remain governed by their existing bounded record schemas.

The deployment/provisioning plan lists the exact existing intermediate backup IDs proposed for
pruning and explains the five-backup policy. Ordinary plan confirmation (or `--yes`) covers that
listed conditional deletion, which occurs only after a new protection is durable. Under the lock,
recheck the original/newest backups, record/path/checksum authority, all independent references,
and the exact eligible intermediates. Never broaden the confirmed deletion set. The retained
installed metadata must still prove every applied migration fingerprint before retiring a target's
protection; a dump-retention decision must not discard required migration evidence.

Retire an eligible intermediate's protection file first and fsync its directory, then delete its
completed backup pair only if no independent reference protects it, using the existing checksum,
inode, and manifest-before-dump deletion rules. An interruption after protection removal can leave
an unreferenced completed backup; ordinary retention/cleanup may handle that remainder under their
existing rules. Unknown files remain preserved. Failure to prune reports mutation evidence and
stops before further migrations; it must not delete the original or newest to make room. A
failed new backup or protection publication never authorizes pruning older recovery points.

The original and retained recent protections survive target replacement. While present, they
protect their backups, backup source releases, base selection, and migration target releases from
ordinary cleanup and scheduled retention. Only the confirmed deploy/provision procedure retires
superseded attempt protections; other commands cannot unpin them merely to meet a retention count.
An active restore retains its existing exclusive recovery rules and does not run attempt pruning.

Successful selections use only schema version 2 with exactly `release_id`, `previous_release_id`,
`backup_id`, `selected_at`, `schema_version: 2`, `observed_previous_release_id`, and `recovery_backup_ids` (sorted, unique, at
most 64 IDs). `previous_release_id` names the last successful release, never an unverified physical
candidate. `observed_previous_release_id` names physical current at the confirmed start (null only
when no release was selected before first success, including a first successful restore).
`recovery_backup_ids` includes all unresolved protections being resolved. `backup_id`
continues, for deploy/provision, to identify the most recent pre-migration backup for this reconciliation, or null if the
set is empty; choose the highest protection `attempt_number`, not by dump or protection timestamps.
Selection filenames hash the canonical supported record; unversioned selections refuse.

After verification, append the new selection durably before removing any resolved protection files.
Its references preserve protection if cleanup is interrupted. A remaining protection already covered
by that exact successful history is recognized as resolved and can be removed under the lock; never
remove a retained protection first. This success-publication rule is separate from the confirmed
retirement of superseded intermediate attempts described above. A new record may name the same release as its last-successful predecessor
when reconciling a different physical selection or resolving outstanding protections; this describes
a freshly verified outcome, not a synthetic intermediate success. An already verified matching
selection with no unresolved protection does not append duplicate history.

Retained selections protect all recovery backup IDs as well as `backup_id`. Ordinary
retention only becomes applicable when those history references are no longer retained under the
existing rules; successful deployment itself never deletes a recovery dump. Rollback continues to
select the immediately preceding successful release and requires exact live-schema compatibility;
a self-predecessor is not a rollback target. Restore still validates the selected backup and source
release. A dump made between partially committed migrations may not match its recorded source
release's complete schema and can therefore require manual recovery; retaining it does not falsely
promise that the existing automated restore command can use it.

## Explicit restore after failed deployment or provisioning

An operator may run `taskman restore ENV BACKUP_ID` after deployment or provisioning changes the database or
physical selection but fails before successful history is published. The failed release need not
start or pass readiness first. In particular, a verified release A followed by a failed deployment
of B may be recovered by restoring a validated backup compatible with A, even though the current
database is incompatible with A and physical current names B. Deploy does not perform this restore
implicitly, and neither `--yes` nor `--allow-downgrade` substitutes for restore's existing typed
environment-and-backup confirmation.

Restore planning and execution require valid successful history when present, database credentials and
database identity, observable migration state with consistent installed/protected provenance,
safe paths, and sufficient restore capacity. History may be empty before first success. In that
case `current` may also be absent; if present it must select a valid installed release. After a
successful selection exists, preserve the requirement for a valid managed `current`. Restore does
not require physical current to equal the last successful selection or its complete schema. The selected
backup must have a validated record and dump, verified checksum and `pg_restore --list`, and an
installed source release with exactly matching migration versions and validated migration
fingerprints. The backup record must identify that source release, and its installed metadata
must validate independently of the newly requested action. The backup's source release need not
have completed a successful deployment previously. Matching directory names or a supplied archive
alone are insufficient provenance.
These are separate checks: the current database need not match the schema being restored. Keep
refusing unknown or conflicting authority, corrupt dumps, and partial-schema backups that cannot
run with their recorded source release. Thus an unfinished first installation with a valid backup
compatible with an installed release can recover through restore before any deployment succeeds.
Restore still needs an existing managed database (or a recognized interrupted swap), the required
services and database credentials, and a safety backup; it does not provision missing host services.
During an interrupted database swap, observe the validated
original/retired and restored databases as described below instead of requiring the canonical
database name to exist.

The plan displays physical current, last successful selection, live migration versions, exact
backup ID and timestamp, restore release/schema, retained recovery points, and required safety
backup. It explicitly warns that restoring replaces the database and loses changes made since
the chosen backup. Under the lifecycle lock, revalidate these material facts before consequences;
drift requires a new plan and typed confirmation. Dry-run performs validation without restoration
or prompting. Invalid authority remains exit 10, backup failures exit 6, restore/database validation
failures exit 11, and verification failures exit 9 under the existing status categories.

Use the existing safety-backup, stop, temporary-database restore/validation, database swap,
release selection, start, and verification procedure. The safety backup must support the observed
live migration prefix of an unfinished deployment through the shared backup-source selection rule,
including when current exists but no longer covers the live prefix. It must also support an
unfinished first installation without `current`. This pre-restore safety backup preserves the database
being replaced, even if its migrations are incomplete. Unlike the chosen restore backup, this
safety copy may require manual recovery to extract data or make it runnable if its migration prefix
does not match its source release's complete schema. This limitation applies to the safety copy,
not to the chosen backup being restored. Preserve unresolved deployment
protections and their referenced backups/releases throughout the attempt. If restore fails,
retain those recovery points and the existing restore recovery material, report fresh state or
explicit uncertainty, and never publish successful history for the failed candidate.

After verified restore, publish a version-2 successful selection naming the restored release,
the last successful release as `previous_release_id`, and the confirmed physical starting release
as `observed_previous_release_id`. If there was no successful history, `previous_release_id` is null;
if `current` was also absent, `observed_previous_release_id` is null. This verified restore publishes
the first successful selection, resolves the retained null-baseline backup protections through its
recovery references, and makes future release replacement a deploy operation. `backup_id` retains restore's existing pre-restore safety-backup
meaning. `recovery_backup_ids` contains the sorted unique union of unresolved deployment backup IDs
and the chosen restore backup ID, plus the binding's retained safety-backup attempt IDs.
Validate representability before destructive consequences.
Only after durable publication may resolved protection files be removed; history retains their
backup references. A restored release may equal its successful predecessor. No synthetic success
for the failed release is inserted.

Use a restore-specific discovery mode and controller admission so the public command reaches this
procedure without passing strict completed-deployment discovery first. For protocol v3, restore
discovery exposes the same physical/history/schema/protection/scheduler facts as deploy. Restore's
`expected_state` uses deploy's keys except `downgrade_baseline_sha256`, plus `backup_id`, `restore_target_sha256`, and
`restore_database_state`; its `parameters` are exactly `backup_id`,
`credentials_path`, `database`, `verification`, `backup_helper`, `prune_backup_ids`, and booleans `replace_unfinished`
and `reapply`. The helper rejects conflicting flags and enforces the same unfinished/completed
admission rules as the controller.
The selected backup's immutable
record and dump authority remain independently validated. Before writing supported-format host records,
ensure the compatible scheduled helper through the same confirmed sequence: pause scheduled
backups, wait for any running backup to finish, replace and checksum-verify the backup program,
then restart the timer if enabled, following the locking rules in Scheduled helper compatibility.

For restore before first success, discovery and expected state carry null
`last_successful_selection_id`, and null `selected_release_id` only when its absence is proved.
The helper applies the same admission rules as the controller; neither substitutes an empty
history or null selection for a failed observation. On retry after first success was published,
recognize the completed restore through its exact backup references and null original baseline.

### Authentication for restore databases

Canonical application observations retain the declared application TCP connection and protected
canonical-only password file. Restore derives exactly the temporary and retired names; their
catalog, migration and pristine-template observations use existing local OS-postgres/native-socket
administrator access, retaining the existing name, role, owner and OID checks before consequences. Do not widen HBA or
password-file entries, grant new privileges, or accept arbitrary database targets.

Load only the durably registered temporary OID using native local administrator authentication
and `pg_restore --role=<configured application role>`. Preserve validated dump/source/checksum/schema
authority, `--exit-on-error`, `--no-owner`, `--no-privileges`, bounded command execution and error
refusal. A constant shell bridge opens the validated root-owned0600 dump as stdin before dropping
OS identity to postgres, then execs quoted positional argv; this keeps the original dump protected
without a copy, credential file or permission change.

This deliberately authenticates the session as PostgreSQL administrator. Normal execution and
new object ownership use the application role, but `RESET ROLE` can regain authenticated
administrator privileges. The accepted operations threat model therefore requires trusted,
root-managed validated backups; the role switch is not a sandbox for hostile dump contents.
Native regressions must prove descriptor transport, execution/session identities and resulting
object ownership. The
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md#native-restore-and-authentication)
records final native findings and verification boundaries.

### Rerunning an interrupted restore

Rerunning `taskman restore ENV BACKUP_ID` with the same backup must reach restore-specific inspection
even when the canonical database name is temporarily absent. Initial preflight verifies PostgreSQL
cluster/admin access through its maintenance database, protected credential-file authority, configured
role login eligibility, filesystem and installed-release authority. Initial inspection does not claim
password authentication to an absent canonical database; later observations authenticate where the
recognized database arrangement permits it. It must not require application readiness, a connection
to the canonical application database, or successful-selection equality before inspecting the restore arrangement. This applies
to both dry-run and execution. Permission to inspect is not permission to rename or delete databases.

Keep the existing derived database names: `<database>__restore_tmp` and `<database>__restore_old`.
Under the lifecycle lock, inspect existence, PostgreSQL OIDs, ownership, and migration provenance
of these databases and the canonical database. Recognize these arrangements only:

| Existing databases | Meaning and permitted continuation |
| --- | --- |
| Canonical only | Begin a new restore, or finish cleanup of a durably completed restore |
| Canonical and temporary | Original remains available; rebuild/validate the temporary from the bound backup before swapping |
| Temporary and retired | Original has been renamed; validate the retired original, rebuild/validate temporary from the bound backup, then rename temporary to canonical |
| Retired only | With a valid unfinished restore binding and matching original OID, recreate/load/validate temporary from the bound backup, then rename temporary to canonical |
| Canonical and retired | Swap has completed; finish release selection, verification, and successful history before removing retired |

Do not infer backup identity from a schema match. Before creating a temporary database, atomically
publish root-owned mode-`0600` `deployments/restore-target.json` with exactly `schema_version: 1`,
`backup_id`, `dump_sha256`, `source_release_id`, `base_selection_id`,
`observed_previous_release_id`, `original_database_oid`, `restored_database_oid`,
`temporary_creation_pending`, `safety_backup_id`,
`replacement` (initially null), and `safety_backup_attempts`. The latter is an array of exact
`backup_id`/`attempt_number` mappings, initially containing the first safety backup with attempt
number zero, ordered by attempt number. IDs and non-negative attempt numbers must be unique;
allocate each new number as one greater than the highest retained number under the lifecycle lock.
The first entry remains the original safety backup throughout this unfinished restore sequence.
The selection ID is the full validated filename, or null when the restore starts before first
successful selection. `observed_previous_release_id` is null only when `current` was proven absent
at that start. Database OIDs are positive integers. `restored_database_oid` is initially null and
`temporary_creation_pending` is initially true, recording intent to create the derived temporary
database. After registration, the restored OID identifies the same database under either its
temporary or canonical name; renaming never changes the recorded OID.
All referenced metadata and identities are validated; the input being loaded and required safety
backups must also pass full dump validation. The abandoned-input exception below applies only
when replacing a target, not when loading that input. Use the existing
non-link directory, create-once initial publication, atomic replacement, and directory-fsync rules.
Only the explicitly described database-identity, safety-backup, and target-replacement updates may
change this record.
This record binds
the selected input and original database; it is not a phase counter or permission to resume without
confirmation. There can be only one unfinished restore per installation.

Under the lifecycle lock and renewed plan confirmation, validate the preserved original's identity
and the durable creation intent before creating the derived temporary database. Observe the new
database's OID and expected ownership, then atomically register `restored_database_oid` and set
`temporary_creation_pending` to false, with directory fsync. Do not load any dump contents until
this registration is durable. Before promotion, and on retries after the swap or durable success,
require the temporary/restored canonical database to match that registered OID. The original and
restored OIDs must differ. A name, owner, or schema match cannot replace this identity check.

If creation was interrupted before registration, an existing unregistered temporary database may
be registered only with valid pending creation intent, a validated original, the exact derived
temporary name, expected owner, and independently verified empty contents consistent with a fresh
managed database. Empty migration history alone is insufficient. Check actual schema objects and
data against the controlled empty template used for creation; refuse unexpected contents, active
writers, failed observation, or contradictory identities. This narrowly supports the creation-to-
registration interruption, not adoption of a populated database. If temporary is absent, create it
and register its OID through the same procedure. Never delete an unregistered database to make it fit.

For an ordinary temporary rebuild, first durably set `temporary_creation_pending` to true while
retaining its registered OID. Drop only that exact temporary database, never canonical or original,
and prove the former OID absent before creating its replacement. Preserve the old recorded OID
until atomic registration of the replacement OID clears the pending flag. A retry can therefore
distinguish the old registered temporary still awaiting deletion, its proven absence, and a newly
created but still empty unregistered temporary. Apply the same empty-registration checks in the
last case; do not interpret a different populated OID as a completed rebuild. Rebuilding a
registered partial load may discard its contents because it has never been promoted or served
application writes. Keep the application and background workers stopped throughout.

On an ordinary rerun, require the requested backup and its digest/source to match that binding.
A different backup requires the explicit replacement procedure below; it must never be substituted
silently, even if its schema matches. Validate the original OID under its
canonical or retired name, depending on the arrangement. Names, ownership, or schema alone cannot
substitute for the original identity. While a temporary database exists, keep the application
stopped and rebuild that managed temporary from the exact bound dump before renaming it: an
interruption during dump loading must not promote an incomplete database merely because its
migration table already looks complete. Validate the full restored database through the existing
restore validation before proceeding. Preserve the original/retired database throughout.

An ordinary retry may be interrupted after dropping a temporary database for rebuilding but before
recreating it. If only the retired original remains, validate its OID against the unfinished
binding, keep the application and background workers stopped, and recreate the temporary database
from the same bound backup. This does not require `--replace-unfinished`. Never drop or rename the
preserved original as part of that rebuild; unrelated databases remain untouched.

A restore-owned temporary database may be empty or partially loaded, including having no migration
table yet. Inspect and report that incomplete state explicitly rather than treating it as the live
application schema or refusing solely because loading is incomplete. Rebuild it from the bound
dump; do not infer successful loading from migration versions alone. Path/database ownership,
binding, original identity, and unrelated-state checks still apply. An unavailable database
observation is not proof of an empty or incomplete temporary database.

The plan reports each database's role and existence, the bound backup, completed selection, retained
safety backup, and remaining consequences. Fresh typed confirmation acknowledges the current plan.
Apply-time revalidation binds the observed OIDs, ownership, migrations, target-record digest, and
selection/protection facts; changed or unobservable authority refuses. Missing canonical state is
reported as absent, never as an empty live schema. Capacity checks cover the remaining work: a
swap already completed does not require free space for another full restore when resuming the same
target; replacing it does require capacity for the new load and any additional safety backups. The completed safety
backup can be reused while its bound original is retained and the application has remained stopped;
if fresh writes cannot be excluded before the swap, take and validate a fresh safety backup and
atomically update that binding field before further destructive consequences.
Also register each new safety backup in `safety_backup_attempts` in that atomic update, using
the bounded retention procedure below; do not leave the previous copy protected indefinitely.

The target binding protects its input backup, safety backup, all retained safety-backup attempts and
their source releases, any pending replacement input, and base/observed
release references from cleanup and scheduled retention. Ensure the compatible scheduled package
before publishing the binding. After verification and durable successful selection, remove the
retired database, then remove the binding with directory fsync. Successful history must retain the
bound backup and safety-backup references. A crash between these steps is recognized through the
binding and exact successful selection references: finish only the remaining cleanup without
restoring the dump again or adding duplicate success. Durable success establishes that the restore
completed; current application readiness is not a prerequisite for this cleanup. Validate the
successful record, database identities, binding, and retained backup references before deleting
the retired database or binding. Missing or contradictory authority still refuses. Report current
application health separately, including unknown health when it cannot be observed, without
blocking safe completion cleanup or claiming that cleanup repaired the application. Once the
binding is removed, a separately confirmed deploy or restore may repair the application.
Do not classify a same-release restore as already
completed merely because the release ID matches; backup references must match as well.
The success record uses the binding's original base and observed release identities, even if a
retry starts after physical selection has changed. After binding removal, a rerun matching that
latest successful restore and physical/schema state verifies the completed outcome without
reapplying the dump. It must not silently discard application writes made after completed restore.

To intentionally restore that same backup again, the operator uses `taskman restore ENV BACKUP_ID
--reapply`. If a completed restore still has a binding, first finish its authority-validated cleanup
without requiring readiness. Then inspect the resulting state and present a new restore plan,
explicitly warning that the same backup will be loaded again and later database changes discarded.
Require fresh typed environment-and-backup confirmation and take a fresh verified safety backup;
do not reuse the previous restore's safety copy as protection for the new attempt. Bind the new
restore to the latest successful selection and currently observed database identity. After verified
completion, publish a new successful selection even if the source release and chosen backup are
unchanged. Ordinary completed-restore deduplication must not suppress this explicitly requested
new attempt. Dry-run reports the fresh restore and required confirmation without cleanup or writes.

Once a reapply attempt has published its binding, it is an ordinary unfinished restore. If it
fails, the next invocation resumes without `--reapply`, or changes target with
`--replace-unfinished`; diagnostics show the appropriate command. Repeating `--reapply` after a
completed attempt requests another fresh restore and therefore always requires fresh confirmation.

Restore-specific discovery takes the requested `backup_id` in addition to the normal discover
parameters, and returns `restore_target` and `restore_database_state` from the same locked observation.
`restore_target` is null only when the binding is proven absent; otherwise it is a flat object
containing exactly the validated binding fields plus `sha256`. There is no nested `record` key
or separate top-level discovery `restore_target_sha256`. Compute `sha256` over the canonical
sorted-key ASCII JSON of the binding fields, excluding the added `sha256` field, with no whitespace
or trailing newline. The digest is response metadata and is not stored in the binding file.
The controller validates the fields and digest and uses them to display active/pending targets,
retained safety attempts, and proposed pruning. The apply request still carries only
`expected_state.restore_target_sha256`, copied from `restore_target.sha256` or null when absent;
the helper revalidates the binding and independently enforces safety under the lock.
`restore_database_state` contains exactly `canonical`, `temporary`, and `retired`; each is null only
when that database is proven absent. An existing database has exactly `oid`, `owner`,
`migration_table_present` (a strict boolean), and `applied_migrations` from direct observation.
When the migration table is proven absent, `migration_table_present` is false and
`applied_migrations` is null. When present, it is true and `applied_migrations` is the validated
sorted version array, including `[]` for a present but empty table. Failed inspection refuses;
it must never be represented as database absence, table absence, or an empty version array.

In restore discovery and restore's expected state, top-level `applied_migrations` describes only
the canonical database: copy its observed version array, or use null when the canonical database
or its migration table is proven absent. The per-database map distinguishes those two cases;
never substitute the temporary or retired database's versions for the canonical database's field.
Other operations retain their existing migration-field contracts. These representations do not
broaden admission: absent migration tables are accepted only in explicitly supported recovery
states, including an incomplete restore-owned temporary database, not as permission to adopt an
unproven application database. Controller and helper validate the conditional field shapes and
the agreement between canonical and top-level observations. Use the existing
migration bounds and a SHA-256 of canonical sorted-key ASCII JSON without whitespace/newline for
the validated target binding. These facts are echoed in restore's expected state and revalidated
under the lock. Deploy, provision, and unrelated mutation commands refuse an unresolved binding;
cleanup is the exception described below, and listings expose it safely. This prevents starting a
new deployment over an unfinished restore.

Unexpected arrangements, unrelated OIDs, missing original material before verified completion,
or intermediate databases without a valid binding refuse with a bounded explanation. Unsupported
interrupted restores without this binding refuse; do not invent their backup identity or adopt them.
The new public path must be tested from every interruption produced by the updated restore
procedure, including lost transport, rather than only by invoking the helper directly.

### Replacing an unfinished restore target

`taskman restore ENV OTHER_BACKUP --replace-unfinished` must support all recognized unfinished
arrangements, including a completed swap whose release cannot pass verification. It uses the same
restore-specific inspection, independently validates the new backup and compatible installed
release, and displays the old and new backup IDs, databases to be discarded/rebuilt, retained
original database, and safety backups. Require fresh typed environment-and-new-backup confirmation;
the flag alone is not confirmation. Dry-run reports this plan without changing the binding.

First distinguish an unfinished attempt from a restore with durable successful history. For the
latter, finish only its remaining cleanup, without requiring current application readiness or
reapplying a dump; then inspect and confirm a
normal new restore with the resulting state as its baseline. Never erase or reinterpret a success
record as an unfinished attempt. If cleanup cannot complete, report that failure without switching.

For an unfinished attempt, hold the lifecycle lock and revalidate the plan. Stop the application
and its background workers, and prevent them from restarting or writing to the database while
preparing and loading the replacement restore. Preserve the original database by
its bound OID. If writes to the original since its safety backup cannot be excluded, take a fresh
verified safety backup before proceeding and retain the previous safety backup as a replacement
recovery backup. If the failed restored canonical database may have received writes, also take and
verify a safety backup of that database before discarding it. Such copies have the same partial-schema
caveat as other pre-restore safety backups. Backup or provenance failure refuses before deletion.

Before any database deletion or rename, atomically update and fsync the binding with a `replacement`
object containing exactly the new `backup_id`, `dump_sha256`, `source_release_id`, and
`discard_database_oid` (null when there is no temporary or failed restored database to discard).
In the same update, register all additional safety backups in `safety_backup_attempts`. The old
input remains protected by the binding's input fields, and the new input by `replacement`; do not
accumulate abandoned input IDs in a recovery list. Preserve the original base/observed selection
identities and original database OID. Apply the bounded retention procedure below before database
consequences; never silently omit a required recovery reference.

This durable replacement intent authorizes only the exact confirmed non-original database OID:
drop the temporary database when the original is canonical or retired; after a failed completed
swap, drop the failed restored canonical database, never the retired original. If the original is
retired, rename it back to canonical. A replacement in progress also permits a retired-only
arrangement, or original-canonical-only after the discard/rename. Validate OIDs and ownership on
every retry; absence of the recorded discard OID is an already-completed step, not permission to
drop another database. Keep the application and its background workers stopped while preparing
and loading the replacement restore. Restoring the original database's normal name is only an
intermediate recovery step; it does not mean that database is compatible with the currently
selected application release. Start the application only after the chosen backup has been fully
loaded and validated, the restored database is in place, and its matching release has been selected.
Then run readiness verification before recording success.

Once the preserved original database is back under its normal name and this restore's temporary
and retired database names are absent, update the binding as follows. Leave all unrelated databases
untouched. Atomically replace the binding's input fields
with the pending target and clear `replacement`, retaining the safety references required below.
In that same atomic update, clear `restored_database_oid` and set `temporary_creation_pending` to
true, after proving the previously registered restored/discard OID absent. Any unregistered empty
temporary from an interrupted creation must first pass the registration procedure before it can
be selected as a replacement discard target. A non-null `discard_database_oid` must match the
registered restored OID, never the original OID. Register the next temporary before loading it.
This releases only the abandoned input's protection from this restore; independent references
and ordinary backup retention still apply. Target switching itself never deletes that input. Then
rebuild a fresh temporary database from that target and follow the normal validation/swap flow.
No intermediate database loaded from the old backup is reused for the new target.

An interrupted replacement is resumed with the pending backup and `--replace-unfinished`, after
fresh inspection and confirmation. A third requested target first completes only the pending
normalization (never loading or starting the abandoned target), then plans and confirms its own
replacement. This avoids requiring an unusable pending target to succeed before another can be
chosen. Normalization validates the binding and original/discard identities, but does not require
the abandoned input dump to remain usable or its release to pass readiness. Both controller and
helper enforce this sequence. Discovery's binding digest covers the
pending intent and recovery references; its database map remains the same three named databases.
Retention protects these references until verified success transfers them to successful history.

When replacing an unfinished target, distinguish trusted recorded identity from usable dump contents.
The new input must pass full backup-record, source-release, checksum, and dump validation before it
can be loaded. The abandoned input's backup metadata, source identity, and binding must remain
valid and mutually consistent, but its dump may be missing, unreadable, or fail checksum or dump
validation. These content failures must not block replacement or the preceding normalization of
an interrupted replacement. Report them explicitly; do not load the abandoned dump or delete its
remaining files as part of replacement.

Apply this distinction in controller preflight, restore discovery, binding/reference readers, and
helper admission, so a generic full-reference check cannot reject the abandoned dump before the
replacement path is reached. Ordinary retry using that backup still requires full dump validation.
This exception does not allow missing or corrupt binding/backup metadata, conflicting identities,
unsafe paths or links, an unvalidated preserved original database, or invalid required safety
backups. If an abandoned input also serves as a required safety backup, that independent role still
requires full validation. Releasing its input reference does not erase another protection or
authorize deletion of damaged or unknown files under ordinary retention. After the input reference
is released, later inventory may retain valid unreferenced backup metadata with unavailable dump
contents and an explicit warning. There is no persistent abandoned-input tag to distinguish this
remainder from other unreferenced damaged storage. Preserve its files and do not treat it as a
validated deletion candidate; a new discriminator is unnecessary. This observation rule does not
relax metadata/path validation or full content validation for any independently required role.

### Retention during restore retries and target replacement

Protect the active input backup and any pending replacement input, without accumulating abandoned
inputs. Previously existing input backups are not safety backups created by this restore merely
because they were selected. Releasing an abandoned input reference does not authorize its deletion;
ordinary retention and independent references determine its subsequent eligibility.

For safety backups created during this unfinished restore, retain the original (attempt zero),
newest, and up to three most recent eligible intermediate copies, ordered by `attempt_number`,
not timestamps. Retry or target replacement does not reset that sequence. Independently referenced
backups remain protected outside the allowance and do not consume intermediate slots. In particular,
the binding's `safety_backup_id` must continue to protect the current safety copy of the preserved
original database, even when a newer safety copy was taken from a failed restored database.
Neither input references nor this required original-database safety reference may be pruned.
Retire a superseded intermediate's attempt entry even when another reference protects its backup;
retain the backup under that independent reference instead. Independent references must not cause
the attempt array itself to grow without bound.

The restore plan lists exact eligible intermediate safety-backup IDs and explains their deletion.
Typed confirmation covers only that list. Restore's `prune_backup_ids` is a sorted unique list of
those IDs; empty authorizes no retirement. Under the lock, revalidate identity, checksum, ownership,
inode, retention order, and every independent reference before retirement or deletion. Never treat
scheduled/manual backups or abandoned inputs as disposable safety attempts. Only restore may
retire these safety-attempt references; generic cleanup cannot alter the binding.

Create and validate each fresh safety backup, then atomically register it in the binding and fsync
before pruning any older intermediate. Finish previously interrupted, newly confirmed pruning
before creating another fresh copy. Process multiple required safety backups one at a time, pruning
after each durable registration, so the allowance may temporarily reach six but never becomes a
retry-count limit. A failed new backup cannot authorize retirement. The plan must anticipate the
exact existing intermediates made eligible by all planned fresh safety copies; apply-time checks
must never broaden its list.

Atomically remove eligible intermediate attempt entries and fsync the binding before deleting any
unreferenced backup pair, using the existing manifest-before-dump ordering and deletion checks.
A crash can leave a completed unreferenced backup for ordinary retention/cleanup; it must not leave
a binding referring to a deliberately deleted copy. Pruning failure stops before further database
consequences and remains retryable. Retained safety attempts, the selected input, the required
original-database safety copy, and unresolved migration protections fit the existing successful
history limits; historical abandoned inputs and superseded attempts must not accumulate until
those limits block recovery. On success transfer only retained references to history before
removing the binding. Existing independently protected backups retain their own protection.

## Cleanup while recovery is unfinished or disk space is low

`taskman cleanup ENV` must remain available to remove proven-safe managed artifacts when a
deployment or restore is unfinished or free space is below build/backup/restore thresholds.
Use cleanup-specific preflight and locked filesystem observation, not operational database-health
or backup-capacity preflight and not strict completed-deployment discovery. Validate SSH/privilege,
helper execution, lifecycle lock, path ownership, records, and deletion authority. Do not require
application readiness, a healthy canonical database, available backup capacity, matching physical
and successful selections, or completion of an active restore. This applies to dry-run as well as
execution. A failed database observation must not be presented as an empty schema.

Cleanup still plans exact release, backup, and recognized temporary-file targets under existing
retention rules and requires its existing typed environment-and-target-list confirmation.
`--yes` does not apply. It cannot drop or rename any database, remove recovery authority records,
resolve protections, or complete a deployment/restore. The canonical, temporary, and retired
restore databases are outside cleanup's target vocabulary entirely.

Protect the physical selected release, successful-history references, retained backup source
releases, every unresolved backup protection and its source/target/base references, and every
restore-target binding reference, including its input and safety backups. Also preserve all
installed release records during an unfinished first installation, since they may be the only
proof of applied migrations. During other unfinished transitions, preserve all installed releases
unless their irrelevance to live migration provenance can be proved; if database observation is
unavailable, conservatively preserve them all. This may leave only eligible unprotected backups
and independently safe temporary files to remove. Unknown storage is preserved. Malformed or
conflicting authoritative references refuse rather than silently shrinking the protected set.

Both inspect and dry-run are read-only: do not normalize or delete incomplete backups while
constructing a plan. Any safe temporary cleanup must be an explicit listed target covered by
confirmation. A plan with no eligible targets reports that outcome and the protected material;
it does not weaken protection to satisfy a space target. The operator may need to add capacity
or remove unrelated files when all Taskman material is required for recovery. Transport/helper
execution still needs its normal temporary infrastructure; this is not a guarantee of operation
on a filesystem unable to perform even the minimal required writes.

Under the lock, execution freshly validates authority and each confirmed target's path, inode,
type, ownership, applicable checksum, and eligibility. Never broaden the deletion set. A target
that became referenced or otherwise unsafe refuses; already-absent safe targets retain the
existing idempotent behavior. Backup pairs retain manifest-before-dump deletion ordering. Partial
deletion or transport failure reports known/possible mutation accurately so the operator can
reinspect and retry. Cleanup must not refresh the scheduler helper merely to remove old files.

Protocol v3 cleanup uses exactly the parameters `action`, `targets`, `release_retention`,
`backup_retention`, and `cursor`, retaining target mappings (`kind`, `identifier`, `path`). Its expected state is exactly
`selected_release_id`, `last_successful_selection_id`, `backup_protection_sha256`, and
`restore_target_sha256`. An `inspect` request uses empty expected state and an empty target list;
its response supplies those confirmation facts and exact eligible targets from one locked
observation, with pagination as specified below. An `execute` request echoes the confirmation facts
and a confirmed target batch, with null cursor; reference changes require replanning, while unrelated
new scheduled backups do not invalidate otherwise safe confirmed targets. Nullable selections
mean proven absence under valid managed authority. The controller calls cleanup inspection
directly, bypassing generic discovery and its database requirement. Shared filesystem/record
readers enforce the full protection graph, without a second controller-owned authority model.

## Packaged admission boundaries

Restore admission uses the finite read-only protocol-v3 operation `restore_preflight`. Its expected
state is empty, its paths are the existing validated installation/backup roots, and its parameters
are exactly `mode`, `credentials_path`, and `database`. Mode is `inspection` or `capacity`, the
credential path is fixed to `/etc/taskman/pgpass`, and database has exactly the existing validated
`host`, `port`, `role`, and `name` fields. Inspection validates credential-file authority,
maintenance access and role login eligibility without requiring canonical access or capacity.
Both modes observe under the lifecycle lock. Capacity is requested only after discovery establishes
that the planned restore needs it.

Successful inspection state is exactly `{"mode": "inspection"}`. Successful capacity state has
exactly `mode` (`capacity`), `database_available_bytes`, and `database_size_bytes`; the latter has
exactly `canonical`, `temporary`, and `retired`, using null only for proved absence. Observed byte
counts are positive signed 64-bit integers. Failure returns no partial capacity facts or native
output. Existing envelope, string, collection, correlation and output bounds apply. These are
planning facts; mutation still freshly validates capacity and database identity under the lock.
The controller maps failures to its fixed restore preflight errors and retains helper cleanup
warnings. It never relies on stdout from a sensitive SSH command, which intentionally suppresses
both output streams.

`provision_authority` also validates the fixed managed resource metadata under its existing lock.
Its parameters are exactly `database`, nullable `postgres_package_track`, and `resource_digests`.
The digest mapping has exactly `taskman_service`, `backup_environment`, `backup_service`, and
`backup_timer`, each a lowercase SHA-256. The helper derives all permitted paths and required
metadata; no arbitrary path list enters the request. Missing resources remain eligible for
create-only convergence. The scheduled executable remains metadata-only until the lifecycle-locked
refresh. The existing successful authority result remains unchanged.

Supplied provisioning credential proof is a narrow private entry point in the same verified
transient package, outside the secret-free JSON protocol:

```text
python3 taskman-host.pyz provision-pgpass-authority HOST PORT ROLE DATABASE DATABASE_STATE
```

The literal mode and five-argument count are exact. DATABASE_STATE is exactly `ready` or `absent`,
from validated native role/database authority. Host is an allowed loopback address, port a canonical
decimal in 1–65535, and role/database use the existing PostgreSQL identifier grammar (at most
63 ASCII characters, with any stricter database-name restriction retained). The pgpass path is
fixed inside the helper. Raw stdin is a nonempty, bounded, single newline-terminated pgpass record
with the existing colon/backslash escaping rules and exact connection-field equality. Secrets
never enter argv, JSON requests/results, diagnostics, or temporary files. Existing pgpass requires
exact non-link root:root mode-0600 bytes matching the supplied record. Ready mode must authenticate
to the configured application database: retained pgpass uses PGPASSFILE; missing pgpass uses a
restricted child environment with the parsed password, without creating a credential file.
Absent mode validates the same exact record and any retained file, omitting only impossible database
authentication. It is admitted only when both configured role and database are genuinely absent;
incompatible presence or retained-role/missing-database refuses. It does not prove database
emptiness or applied migrations. Runtime-file authority is unconditional. Convergence re-observes
the exact identities, creates through protected stdin and proves final application authentication.
Generic provision discovery shares this ready/absent observation policy so target admission and
downgrade planning reach fresh creation even before pgpass exists. Ready missing-file discovery
retains administrator catalog proof; ready present-file discovery requires application
authentication and state agreement. Strict/deploy/restore still require their credentials.
Native execution is bounded to 60 seconds and emits no retained stdout/stderr.

The private entry returns only status: 0 for successful proof, 2 for invalid private arguments or
oversized input, and 10 for unsafe credential authority, malformed content, missing prerequisites,
or failed authentication. The controller maps these to fixed admission errors. The runner reuses
normal package validation, verified staging, deadlines and exact cleanup. Its receipt contains
`exit_status`, `warnings`, and `local_cleanup_incomplete`; transport exceptions retain whether the
entry was dispatched. Successful temporary-cleanup warnings do not change admission success or
participate in plan-drift comparison. This finite exit-only adapter is not a generic remote command
API and does not alter the v3 JSON framing or add a public workstation command.

## Scheduled helper compatibility

Both transient and persistent scheduled packages must read the supported installed/selection
records and apply protection-aware retention. This coordination remains required for future package
upgrades within the supported baseline; it does not add a transition path from the old staging
installation. Before deploy publishes any supported host record,
its confirmed plan must ensure `/usr/local/lib/taskman/taskman-backup.pyz` is the exact compatible
package built by the controller. If different, atomically refresh that executable only, preserving
its fixed ownership/mode and verifying its checksum. Do not converge packages, timer configuration,
credentials, Caddy, firewall, or other provisioning state. First provisioning installs the compatible
package through its existing path.

Refresh is a visible managed mutation included in `changed` evidence. Prevent an old scheduled
process already started before replacement from later acquiring the lock and using old retention.
For this dependency, deployment follows the managed timer's persistent enablement: an enabled timer
must be active after convergence, and a disabled timer remains disabled and inactive. An active but
disabled timer is an exceptional arrangement and refuses automatic refresh. The plan explicitly
shows any temporary pause or repair of an enabled but inactive timer; deployment does not alter
enablement. This gives interruption recovery durable authority without a second intent record.

Refresh uses this exact sequence: acquire the lifecycle lock and revalidate the plan; stop only the
managed timer; release the lock; wait within the configured command timeout for any already-running
backup service to finish normally; reacquire the lock and revalidate deployment authority; atomically
install and checksum-verify the compatible package; restart the timer if enabled. Waiting outside
the lock lets an old backup process already waiting for that lock finish without deadlock. Newly
created ordinary backups are irrelevant plan drift. Never kill a running backup to expedite refresh.
Failure to quiesce, changed timer enablement, or unsafe package identity refuses further deployment
consequences. Do not publish supported-format records before this sequence succeeds. Retain the lock for
subsequent deployment steps; a restarted compatible scheduled process may wait normally.

If refresh fails, restore the enabled timer only when the installed executable is verified as
either the previously observed valid package or the new package, and report any restoration failure.
If execution is interrupted, the next confirmed deploy observes enablement, executable identity, and
service state and repeats these same steps; it never guesses the prior timer activation state.
No timer enablement, schedule, or unit-content change is authorized. Concurrent manual starts of an
obsolete executable by a trusted administrator are outside the normal scheduler coordination model.

## Reconciliation procedure and transport

Keep orchestration in the controller and lifecycle mutation in the host-side helper; add no generic
workflow engine. Controller-driven provisioning resource/database/runtime convergence is an existing
exception and currently runs outside the lifecycle lock. Fresh confirmation checks are not
serialization. The approved [coverage direction](2026-09-18-provisioning-lock-coverage-proposal.md)
has an independently reviewed written design awaiting full design/plan approval and implementation
in the [dedicated post-merge workstream](../handoffs/operations-lock-coverage.md); it does not block
the current operations merge and is not current behavior.
The transient controller/helper use protocol version 3. Retain envelope,
correlation and redaction rules; use the explicit collection and byte budgets below. Old transient protocol requests refuse; the
controller always transfers its matching helper. Future persisted-format compatibility is a
separate lifecycle obligation, not a requirement to accept old transient wire requests.

### Protocol envelope

| Envelope | Exact fields |
| --- | --- |
| HostRequest | `protocol_version`, `operation`, `correlation_id`, `expected_state`, `paths`, `parameters` |
| HostResult | `protocol_version`, `operation`, `correlation_id`, `outcome`, `message`, `state`, `warnings` |

The finite vocabulary is `discover`, `list_releases`, `list_backups`, `verify`, `genesis`, `deploy`,
`backup`, `rollback`, `restore`, `restore_preflight`, `provision_authority`, and `cleanup`.
Correlation is ephemeral transport identity, never persistent operation/release/backup/database
identity. Paths contain exactly installation and backup roots; handlers own operation semantics.
Outcomes are `succeeded`, `refused`, `retryable`, `manual`. The strict codec rejects wrong types,
unsupported version/operation, duplicate JSON keys, invalid UTF-8/JSON, oversized/trailing output
and nonfinite values. Operation/correlation must match the request. Generic collection limit 64
and nesting depth 8 apply alongside the schema-specific budgets below.

`credentials_path` identifies the host's PostgreSQL password file, `/etc/taskman/pgpass`, not an
operator login or SSH key. Existing private-file ownership, permission, and non-symlink checks apply.

### Artifact, record, and transport budgets

Protocol v3 permits up to 256 migration fingerprints in schema-defined manifest/release-record
`migrations` arrays, including embedded manifests, and up to 512 versions in schema-defined
observed/backup `applied_migrations` or migration-version arrays. These preserve the existing
record-domain limits. Other arrays and object mappings retain the generic 64-item limit. Apply
exceptions only at validated schema locations, not to arbitrary fields sharing a name; generic
JSON decoding must not reject these supported arrays before operation-specific validation runs.
Retain string/path checks and nesting limits, with coverage for every supported nested record form.

The exact serialized UTF-8 byte budgets are:

- New schema-3 artifact manifest: 128 KiB.
- New schema-2 installed release record, including the embedded manifest and duplicated fields:
  256 KiB.
- Every complete protocol-v3 request or result, including its envelope: 1 MiB.
- Other persisted records: 64 KiB.

These are aggregate budgets, not independent allowances for each nested component. New migration
filenames must fit the supported filesystem component bound of 255 UTF-8 bytes as well as the
existing filename grammar. Validate the actual complete encoded manifest and prospective installed
record during build/artifact validation before publishing a reusable artifact. Validate both
uploaded-target and installed-target request representations, including enclosing metadata, and
check the exact environment-specific request again before upload or host mutation. Oversized local
artifacts fail with exit 2 and a bounded explanation of the violated limit, never after staging.
The host validates the same record budgets before publication and enforces message bounds at its
input/output boundary. No truncation of migration or identity data is allowed.

Only supported-format records are transportable. Unsupported old artifacts fail local validation;
unsupported persisted authority refuses rather than being skipped or rewritten. Supported records
with more than 64 migration fingerprints still require the schema-specific codec path. Inventory
and history growth is a separate discovery/listing concern; larger messages are not a substitute
for bounded projections.

The read-only `discover` operation has four inspection modes: `strict`, `deploy`,
`provision`, and `restore`. Mode `strict` retains the existing completed-installation checks;
the other modes support the unfinished states admitted by their respective commands.
Parameters are exactly `credentials_path`, `database`, and `mode`, except
that `restore` additionally requires `backup_id`; expected state remains empty. In `provision` mode,
discovery accepts and reports the unfinished-installation state described above, with nullable
current and successful selection;
unknown observations must never be substituted with null. Mode `restore` uses the database-arrangement
observations above. Discovery no longer returns the full `releases`, `backups`, `selections`, or
`release_migrations` inventories. Its common state fields are exactly `selected_release_id`,
`last_successful_selection_id`, `last_successful_selection`, `previous_successful_selection`,
`applied_migrations`, `service_state`, and `database_state`. The two selection objects are the
validated latest and immediately preceding successful records, or null for proven absence;
the latest ID is its full selection filename. Mode-specific absence/admission rules still apply.
The deploy, provision, and restore modes additionally return
`backup_protections`, `backup_protection_sha256`,
`scheduled_backup_sha256`, `backup_timer_enabled`, and `backup_timer_state`.
The deploy, provision, and restore modes also return `independently_held_backup_ids`, a sorted
unique bounded projection used to plan attempt retirement. For deploy/provision it is the
intersection of active/retiring migration-protection backup IDs with independently held
successful-history and restore references. For restore it is the intersection of safety-attempt
IDs in the binding with complete successful-history and active/retiring migration-protection
references. Restore attempt ownership itself is not an independent reference. Active/pending
inputs and the current original-database safety copy remain protected by their explicit binding
roles. Do not export unrelated historical backup IDs. The controller validates that this projection
is a subset of the relevant observed attempt IDs; the host re-derives all references under the lock
before retirement or deletion. Existing history, binding, and protection confirmation facts and
exact confirmed prune IDs still govern drift refusal.

Restore additionally returns
the flat `restore_target` and `restore_database_state` described above.
`selected_release_id` means physical current, not successful history. Timer state is
`active`, `inactive`, or `unknown`; enabled is a strict boolean, and unknown enablement refuses.
Deploy and provision views additionally include `downgrade_baseline_sha256` as defined above.

### Bounded discovery and paginated inventories

Validate full successful history, installed migration provenance, and backup/reference relationships
on the host before constructing an operational projection. Do not send every historical record to
the controller to perform those safety checks. Do not silently ignore old references or delete
history to fit a response. History retention, rollback predecessor semantics, and reference-based
backup protection remain unchanged. Successful history has no lifetime record-count limit: scan
valid history incrementally under the lock, with per-record validation and the normal bounded
operation timeout. A timeout is reported as incomplete inspection, never a truncated successful history.

Protocol-v3 `list_releases` and `list_backups` use exactly `cursor` in parameters and empty expected
state. The first cursor is null;
later cursors are exact objects with `inventory_sha256` and `after_id`. Responses contain exactly
`records`, `inventory_sha256`, and `next_cursor`. Each record entry has exactly `id` and `record`;
IDs are full release IDs or backup IDs, respectively. Records retain their
validated persisted schemas. Order by full ID ascending and continue strictly after `after_id`.
`next_cursor` is null only at the end, otherwise it identifies the last returned ID and the same
inventory digest. Do not add a selection-history listing operation: no supported controller action
needs it. History remains fully validated on the host; discovery's latest/predecessor projections
supply the required controller facts without exporting the complete history.

Each page contains at most 64 records and must fit the complete 1-MiB response budget. Choose page
size by encoded bytes as well as count; never split, truncate, or omit a record. A valid supported
individual record fits a page. Reject malformed cursors with exit 2.
Under the lifecycle lock, validate the inventory and compute its SHA-256 by incrementally hashing
the canonical sorted-key ASCII JSON of `{operation, records}`, with records in the defined order
and no whitespace/newline. The full array is hashed incrementally, not sent as a protocol message.
The digest binds record contents as well as identities and is specific to that inventory operation.
On later pages, changed inventory returns exit 10 with `inventory-changed`; never join pages from
different snapshots. Cursors are continuation positions, not authority to skip validation.
Check the snapshot digest before continuation-ID membership: a removed record is inventory drift,
not an invalid cursor. An unknown continuation ID against a matching snapshot returns exit 2.

Public release/backup listings collect pages within the command timeout, validate order/digests,
and return the complete existing logical listing only after every page succeeds. Preserve the
single final JSON document; internal wire-page limits do not cap the public listing at 64 records.
Partial enumeration is a failure, not a successful partial listing. The controller may restart
enumeration only within the existing timeout and must report continuing drift rather than loop
indefinitely. New ordinary backups invalidate backup pages, not release pages.

Automatic target resolution enumerates release pages and applies the existing selected/latest/
exact-input ordering without requiring a full inventory in discovery. Revalidate material planning
facts after enumeration and before confirmation. Immutable record lookups needed for restore and
rollback may be resolved through these pages. For downgrade classification, derive the applicable
baseline records from the validated release pages and discovery's current/history/protection/schema
facts; require their sorted-ID digest to match the host's `downgrade_baseline_sha256` before using
the classification. Host-side admission remains authoritative. Do not treat missing pages or a
digest mismatch as an empty baseline. No new acknowledgment or host mutation occurs during paging.

Cleanup inspection pages its eligible targets using `cursor` in its exact parameters (null for
execute), and returns `inventory_sha256`/`next_cursor` alongside its existing confirmation facts and
target list. Apply the same count/byte budget, deterministic ordering by `(kind, identifier, path)`,
and snapshot-change refusal; the cursor's `after_id` is the canonical JSON encoding of that target
tuple. Its inventory digest covers the full ordered eligible target set and confirmation facts.
Collect all pages before typed confirmation. Execute the confirmed set in byte-bounded batches
of at most 64 targets, freshly revalidating protection facts and each target under the lock for
every batch. Internal batching never authorizes a target absent from the confirmed plan; failure
reports partial changes and requires a new inspection/confirmation for remaining work. Cleanup
does not require generic operational discovery or export full history to validate protection.

Credential-free early provisioning inspection remains separate proposed CLI UX work. Any future
implementation must validate underlying host records and use bounded counts/eligibility facts;
omitted or uninspected inventories never prove a pristine installation.

Version-3 existing-host deploy has this exact operation-specific shape:

| Object | Exact keys |
| --- | --- |
| `expected_state` | `selected_release_id`, `last_successful_selection_id`, `applied_migrations`, `backup_protection_sha256`, `scheduled_backup_sha256`, `backup_timer_enabled`, `downgrade_baseline_sha256` |
| `parameters` | `target`, `migration_policy`, `credentials_path`, `database`, `verification`, `backup_helper`, `prune_backup_ids` |
| Uploaded `target` | `kind: "upload"`, `manifest`, `artifact_sha256`, `artifact_path` |
| Installed `target` | `kind: "installed"`, `release_record` |
| `backup_helper` | `sha256`, `upload_path` |

`prune_backup_ids` is a sorted unique list of existing attempt backup IDs whose protections may
be retired under the confirmed five-backup policy. An empty list authorizes no retirement.
The helper independently re-derives eligibility and refuses any newly required deletion outside
the list. Independent references are rechecked before deleting backup contents. This field grants
no permission to remove scheduled/manual backups, original/newest recovery points, or other files.

The release manifest/record supplies the candidate ID. Reject mixed or additional target fields;
host reuse carries no fabricated archive path. `backup_helper.sha256` is the required controller-built
package hash, while expected state's checksum binds the package observed during planning. Its
upload path is null only when the installed checksum already matches; otherwise it is an exact
private regular `.pyz` file below derived uploads, verified before use. Never execute supplied
helper bytes until their transferred checksum, root ownership, and fixed destination are validated.
The protection-set digest is SHA-256 of a canonical JSON array of unresolved protection mappings
sorted by backup ID, using sorted object keys, ASCII encoding, no whitespace, and no trailing newline.
An empty set hashes the literal bytes `[]`. Existing database, credential, verification, hash, path,
and migration-version validators retain their bounds except for the explicitly coordinated
serialization budgets above. Timer activation may drift through a normal
oneshot; enablement must not change after confirmation. After provisioning has converged the
required database and scheduler infrastructure, its `genesis` request uses the same exact
parameter and expected-state keys as deploy. For unfinished installation,
`last_successful_selection_id` is null and `selected_release_id` may be null. The helper enforces
the operation-specific admission above. The existing exact completed-genesis replay remains
separately constrained and binds its actual successful selection. A fresh locked observation after
authorized provisioning convergence supplies infrastructure checksums; release selection, live
schema, and protection drift outside the displayed plan still requires replanning.

Record readers/target validation live in shared standard-library modules included explicitly in
each consuming package. Unknown required observation is a refusal, never a substituted checksum or
empty schema. The controller selects acknowledgment policy; the trusted helper independently
enforces target, database, path, and expected-state safety rather than accepting a force boolean.

The mutation sequence is:

1. Lock, observe, validate managed authority and confirmed facts, and resolve only recognizable
   safe temporary files. Refuse relevant drift before consequences.
2. Ensure the compatible scheduled backup executable using the bounded lock-release/revalidation
   sequence above when necessary, then stage or reuse the exact target.
3. Finish confirmed pruning left by an interrupted attempt, if any. If additional migrations are
   needed, create and durably protect a fresh backup, retire/delete only the confirmed eligible
   intermediates under the five-backup policy, then stop Taskman even
   if physical current already equals the desired target. Never migrate underneath a running app.
4. Invoke target migrations only for missing versions, then freshly observe the expected complete
   schema. On failure retain actual observed versions, all recovery points, and a stopped app;
   never restart potentially incompatible previous code.
5. If physical selection differs, stop Taskman if not already stopped and atomically select the
   target. Start the target, perform bounded verification, and reobserve selection/schema.
6. Publish successful history if needed, transfer backup references before removing resolved
   protection records, and report the verified result. A same-target retry may only need service
   start, verification, and history publication. A healthy complete match avoids needless restart.

Failed-candidate service health is not an admission prerequisite. Required host/platform/database
authority remains mandatory. Deploy does not perform broad provisioning. Neither command performs
automatic rollback, implicit adoption, or background recovery. Host reuse remains subject to the same final runtime identity,
loopback topology, journal, readiness, and HSTS checks as uploaded artifacts.

## Failures and reporting

[Internal helper failure recovery](#internal-helper-failure-recovery) governs malformed matching
in-process results and encoding failure. Wire validation still rejects the complete invalid reply;
internal evidence salvage does not relax controller trust or authorize another consequence.

Preserve the bounded verification report on exit 9, including checks actually attempted; do not
replace it with an empty report. If follow-up inspection also fails, keep the original failure as
the main reported error and report the inspection failure separately.
Attempt one bounded safe reobservation under the lock after a partial mutation where possible.
Report physical current, last successful selection, desired target, actual migration versions,
protected backup IDs, database/service/scheduler state, and failed boundary. Distinguish final
observations from the confirmed starting snapshot; never present an old snapshot as current.

Keep public JSON schema 1 and its existing conservative boolean `changed` meaning: true when a
managed mutation is known or may have occurred, false only when no managed mutation occurred or
could have occurred. Add `facts.mutation_state` with `unchanged`, `changed`, or `unknown`; transport
loss makes the affected dispatch `unknown`, not a claim that a particular mutation completed.
Earlier proved mutations remain part of command-level evidence, as specified below.
Unavailable final identity/schema/state fields are null or the existing explicit unknown enum,
never empty collections or previous identities masquerading as fresh observations. Human output
must explain possible changes. The separate CLI UX workstream consumes these accepted semantics.

The fixed public exit categories are:

| Exit | Meaning |
| --- | --- |
| 0 | Success or verified no-op |
| 2 | Invalid command/argument/configuration/target or missing migration declaration |
| 3 | Local prerequisite/build failure |
| 4 | Secret decryption/validation/installation failure |
| 5 | SSH/host-key/privilege/remote preflight failure |
| 6 | Backup/backup-validation failure |
| 7 | Migration failure |
| 8 | Release staging/selection/service lifecycle failure |
| 9 | Readiness/public verification failure |
| 10 | Safety/state/schema/policy conflict, incompatible rollback or missing unattended acknowledgment |
| 11 | Restore/restored-database validation failure |
| 12 | Shared lifecycle lock unavailable |

Scheduler dependency refresh failures use 8 once its mutation begins; prior unsafe identity uses 10.
Transport loss keeps its transport status and unknown consequence evidence, not an invented
migration failure. Completing deployment or a newly restored release requires a complete passing
report and durable successful history. Cleanup-only restore retries retain their explicit exception:
they validate durable completion and recovery authority, not current application readiness.
Topology and subsequent readiness share the configured finite readiness budget under the global
45-second deadline. Only safe absent application/distribution listeners may wait; unsafe, malformed,
public, EPMD or missing-PostgreSQL evidence fails immediately. A timeout retains failed checks.
The deployment architecture defines journal capture and the complete verification boundary.

### Public result envelope

Public JSON schema version 1 contains `schema_version`, `command`, `environment`, `status`,
`changed`, `stage`, `facts`, `warnings`, and `next_action`. Human and JSON output preserve the
same bounded redacted evidence. A stage/failed boundary is a coarse summary, not execution history.
The interactive administrator bridge propagates the remote command status after session entry.
No raw remote output, inspected exceptions or secret values become safe diagnostics on failure.

### Exact protocol-v3 mutation results

Keep the existing result envelope keys: `protocol_version`, `operation`, `correlation_id`,
`outcome`, `message`, `state`, and `warnings`. Outcomes remain `succeeded`, `refused`, `retryable`,
and `manual`. Validate correlation, operation, and the complete operation-specific state before
consuming any result. Exact mutation-state fields are required for `deploy`, `genesis`, `restore`, and cleanup execution. Failed cleanup inspection also
uses this failure shape; successful inspection retains its separately specified paginated response.
Other read-only operation results retain their own contracts.

Every affected result's `state` contains these exact common fields, plus only the operation-specific
fields in the following table. Required fields are never omitted on an error path.

| Common field | Type and meaning |
| --- | --- |
| `mutation_state` | `unchanged`, `changed`, or `unknown`, for this helper invocation |
| `exit_code` | Integer status from the categories above; 0 exactly when `outcome` is `succeeded`; local-build status 3 is not a helper result |
| `failed_boundary` | Null on success; otherwise one of `input`, `lock`, `authority`, `expected_state`, `backup_helper`, `staging`, `backup`, `protection`, `migration`, `selection`, `service`, `verification`, `history`, `restore`, `cleanup`, or `inspection` |
| `observations` | Exact final-observation mapping for the operation, defined below |
| `unavailable_fields` | Sorted unique array of keys in `observations` whose final values could not be established |
| `inspection_error` | Null, or a safe code: `lock-unavailable`, `inspection-failed`, `inspection-timed-out`, or `unsafe-observation`; follow-up observation trouble, never a replacement for the primary failure |
| `report` | Null when no validated report is available from this invocation, otherwise the verification-report mapping validated by shared protocol `validate_verification_report` |

| Operation | Exact additional state fields |
| --- | --- |
| `deploy`, `genesis` | `desired_release_id`, `backup_id` |
| `restore` | `desired_release_id`, `backup_id`, `pre_restore_backup_id` |
| `cleanup` | `completed_targets` |

`desired_release_id` is the validated requested target, or null if validation failed before its
identity was established; it is not an observation of current. For restore it names the requested
backup's validated source release. Restore's `backup_id` identifies that validated input, while
`pre_restore_backup_id` identifies the binding's validated `safety_backup_id` for the original
database, not an arbitrary newer copy of a failed restored database. Deploy/genesis
`backup_id` identifies the validated pre-migration backup used by this invocation. These backup
fields are null when no such validated identity was established; do not guess a completed backup
from a temporary dump name. Their presence alone does not prove publication or a final reference.

Cleanup's `completed_targets` is a sorted, duplicate-free subset of the current request's confirmed
targets, using the existing exact `(kind, identifier, path)` mappings and ordering. Include a target
only after its complete deletion or validated safe absence is established. A partly deleted backup
pair is not complete, even though its deletion has changed the host. Successful execution accounts
for every target in its batch; failed inspection returns an empty list. The list never authorizes
further deletion. Across batches, retain earlier completions and mutation evidence on later failure.

### Final observations and unavailable values

Deploy, genesis, and restore `observations` contain exactly:

`selected_release_id`, `last_successful_selection_id`, `applied_migrations`,
`protected_backup_ids`, `backup_protection_sha256`, `restore_target_sha256`, `database_state`,
`service_state`, `scheduled_backup_sha256`, `backup_timer_enabled`, and `backup_timer_state`.

Restore additionally contains `restore_database_state`, with the same exact canonical/temporary/
retired database mapping as discovery. Cleanup observations contain only `selected_release_id`,
`last_successful_selection_id`, `backup_protection_sha256`, and `restore_target_sha256`;
cleanup does not inspect the database, service, or scheduler to produce its result.

Identifiers, hashes, booleans, migration arrays, and restore-database mappings use their discovery
validators and absence semantics. `database_state` is `ready`, `absent`, or `unknown`;
`service_state` is `running`, `stopped`, `failed`, or `unknown`; timer state is `active`, `inactive`,
or `unknown`. `ready` means the managed database was observable and validated for this operation,
not that its schema matches current or that application readiness passed. Restore's top-level
migration observation still refers only to canonical, not an original database temporarily retired.
`protected_backup_ids` is the sorted unique union held by unresolved deployment protections and
the active restore binding, including its pending input and safety copies; it is not an unbounded
list of every backup retained by historical selections. Full historical references are still
validated on the host. Apply the existing recovery-set and aggregate response bounds.

For unavailable observations, use the explicit `unknown` enum where defined and null otherwise,
and include that field in `unavailable_fields`. For proved absence, use the discovery-defined
null/absence value without marking the field unavailable. Thus null current with no unavailable
marker means proved absent, while null current with the marker means not established. Empty
migration/protection arrays always mean a successfully observed empty set. If the restore database
arrangement cannot be validated as a whole, return null for `restore_database_state` and mark it
unavailable; do not fabricate a partly validated arrangement. Unknown enum values must be marked
unavailable. Reject unknown field names, inconsistent markers, and malformed value combinations.

Only observations made under the lifecycle lock after the last possible managed mutation in this
invocation may populate final fields. Never fill them from pre-mutation state merely because an
exception prevented refresh. Independently successful final observations may survive another
domain's inspection failure, but dependent authority must be validated together. On lock failure,
all final fields are unavailable. On a pre-mutation refusal, a fresh locked observation may still
be reported. If reobservation is unsafe, skip it and mark its fields unavailable; it must not
normalize state, mutate the host, or replace the original error.

The controller retains the confirmed plan's exact operation-specific `expected_state` mapping
locally as `facts.starting_state`; it does
not require the helper to echo or trust that snapshot as an observation. If no plan was confirmed,
this field is null. Provisioning must not replace its confirmed pre-convergence facts with later
genesis observations, and batching must not replace the initial confirmation with later snapshots.
Public `facts.observations` and `facts.unavailable_fields` preserve the helper's
final mapping and markers. Any existing top-level selected identity is sourced from final
observations only, never from `desired_release_id` or `facts.starting_state`.

### Outcome validation and public-result mapping

`unchanged` requires proof that no managed mutation occurred or could have occurred. Before each
potentially mutating action, preserve uncertainty until its consequence is known. A confirmed
mutation makes the invocation `changed`, even if subsequent work fails or reverses that change;
later uncertainty is still reflected in unavailable final observations. If some action may have
mutated but none is proved to have done so, report `unknown`. Preserve this evidence through every
exception, including helper refresh, backup/protection publication, temporary cleanup, service
actions, database changes, and successful-history publication. Inspect-only requests remain
`unchanged`. This is invocation-local evidence, not a new persistent journal.

| Result case | Required evidence |
| --- | --- |
| Deploy/genesis success, or restore that installs/reapplies a backup | Exit 0, null failed boundary, complete passing report, and fresh observations proving the required durable successful selection and resolved recovery state |
| Restore completion/cleanup-only success | Exit 0 and null failed boundary; fresh selection/reference/database-identity evidence proves completion, without requiring service readiness; report is null unless verification was actually attempted |
| Cleanup execution success | Exit 0 and null failed boundary; all batch targets accounted for, valid final filesystem/reference facts, null report |
| Verification failure | Exit 9, boundary `verification`, and the validated report of checks actually attempted; never replace an available report with null or an empty mapping |
| Other helper failure | Nonzero matching category and failed boundary; retain any report produced earlier in this invocation and all accumulated mutation evidence |
| Pre-mutation input, authority, or lock refusal | Corresponding exit 2, 10, or 12; unchanged only when no earlier action may have mutated; lock contention has boundary `lock` |

If verification cannot produce any report, null means unavailable, not passed; report that failure
without fabricating checks. Conversely, a passing report followed by history/publication failure
is a failed command with a passing report, not a successful deployment. Cleanup-only restore health
may be unknown without blocking otherwise proven completion. Required completion authority may
not be unavailable on success. These exceptions do not relax ordinary deploy/restore verification.

For a valid helper response, the controller maps `exit_code` to the existing public status and
copies `failed_boundary`, `inspection_error`, `report`, and operation-specific evidence into
public facts without reinterpretation. Public `changed` is false exactly for `unchanged` and true
for `changed` or `unknown`; public `facts.mutation_state` preserves that classification. Safe
`message`/warnings retain the primary reason and separate follow-up trouble. Reject contradictory
outcome/status combinations rather than inferring success from a report or missing fields.

Public mutation evidence covers the entire command, including controller-owned host convergence,
helper refresh, earlier cleanup batches, and known managed temporary-file changes. Across completed
stages, any proved mutation establishes `changed`; otherwise any possible mutation establishes
`unknown`, and only all-proved-unchanged permits `unchanged`. A lost, malformed, mismatched, or
oversized reply after mutation dispatch makes that dispatch `unknown` and retains its transport/
protocol failure status. Earlier proved mutations still make the aggregate command `changed`;
the unavailable reply never establishes final observations or completion. Null all unavailable
final fields with their markers, preserve the locally confirmed starting snapshot, and never
substitute an earlier batch's observations or an invented verification report. If no mutating
dispatch or other managed mutation occurred, transport failure alone does not imply a change.

All result variants fit the complete 1-MiB envelope budget, including reports, null/unavailable
markers, and the largest cleanup batch or migration arrays. The existing report, string,
collection, and nesting validators apply at their exact schema locations. Required failure
evidence cannot be silently dropped to fit; cover worst-case result serialization during local
contract verification before publishing the matching helper package.

## Internal helper failure recovery

Only matching cooperative in-process handler results may contribute independently validated
evidence. The wire consumer rejects any malformed/mismatched complete reply; it cannot use this
internal salvage policy. The former legacy translator and stop/10 repair are removed; stop/start
producers emit service/8 directly, preserving possible/known mutation and native command order.

### Chosen contract

One handler contract remains: a request-correlated `HostResult` containing exact mutation state.
There is no legacy reader or second handler evidence vocabulary. Valid results pass through,
including successful cleanup inspection's existing paginated authority contract. An invalid
success can only become failure; independently passing verification never reconstructs success.

Recovery may reuse independently valid evidence only from an in-process `HostResult` whose
protocol, operation and correlation match the request through `validate_result_for_request`.
Wrong type, mismatched envelope and thrown exceptions contribute no reply evidence. They use
unknown mutation failure (unchanged for cleanup inspection) and the existing safe observer.
This trust boundary concerns cooperative internal producers, not hostile Python objects.
Malformed, mismatched, oversized or lost wire replies remain wholly invalid at the controller:
unknown for the affected dispatch, retaining only earlier validated aggregate evidence.

The helper owns recovery policy. The shared protocol validator remains the single schema owner.
Use a small fixed set of complete failure-state projections against that validator to accept
independent groups; do not create another field parser, generic repair framework or duplicated
schema. Each projection substitutes one group into an otherwise valid unavailable failure base.
The finished result must pass full validation and request-dependent cleanup validation.

### Evidence groups and precedence

The base is a failed exact state with unavailable final facts and a fixed redacted internal-failure
message. Deploy/genesis use retryable inspection/8; restore uses retryable inspection/11;
cleanup uses refused inspection/10. Report is null, restore safety-copy identity is null and
cleanup completion is empty. Request-derived desired release and input backup identities may be
recorded when valid; they establish intent only, never publication or completion.

1. **Mutation classification.** Retain canonical `unchanged`, `changed` or `unknown` from the
   matching internal result. Missing or invalid classification becomes unknown. Cleanup inspection
   is always unchanged and has no completions. Never infer classification from old boolean fields,
   observations, a passing report or failure category. A lock category cannot survive a retained
   changed/unknown classification because full validation requires unchanged.
2. **Identities.** Validate operation-specific release/backup identities as one group using a
   complete failure projection. If invalid, use the safe request-derived defaults above. Do not
   guess a pre-restore backup or restored source from current filesystem facts.
3. **Cleanup completions.** Accept only an entirely valid sorted unique group whose exact
   kind/identifier/path triples belong to the confirmed request batch. Reject the entire group
   if any member is malformed, duplicated or outside that batch. Completion can coexist with
   unchanged mutation for a target already absent. It is evidence only and authorizes no further
   deletion. The shared protocol owner supplies request-membership validation to both helper and controller;
   retain success/all-target and inspection/no-completion checks. Do not duplicate target parsing.
4. **Report and primary failure.** Cleanup reports are always ineligible, even if their standalone
   schema validates; discard them while retaining eligible cleanup proof and its coherent failure
   tuple. For deploy/genesis/restore, validate the report independently through the existing report
   validator, then choose a coherent failure tuple. If a valid failed report exists, verification
   with its exit 8 or 9 takes precedence over an incompatible or malformed supplied category;
   emit the fixed internal-failure message and warning when repairing that contradiction. If the
   supplied nonzero outcome/status/boundary tuple is already coherent with retained evidence,
   preserve it and its safe message. A valid passing report survives a later primary failure.
   An invalid report is omitted. If the remaining supplied tuple is invalid, or deploy/genesis
   verification lacks its required valid report, use the base internal-failure category. Never
   attach a failed report to inspection/history or convert it into a passing report.
5. **Final facts.** Treat observations, unavailable_fields and inspection_error as one validated
   group. Restore's database arrangement and top-level migrations remain a dependent unit.
   Reuse a valid operation-owned final group without another observation. For missing/invalid
   groups, make at most one existing read-only post-invocation observation attempt under the
   lifecycle lock. Do not salvage arbitrary individual fields or reuse an earlier snapshot as
   final. On lock/error/invalid observation output, retain unavailable facts with the appropriate
   safe inspection error. Follow-up failure never replaces the chosen primary category/report.

Construct a fresh exact failed result with a fixed warning that invalid internal evidence was
rejected. Do not expose raw exceptions or add arbitrary invalid original warnings. Validate once
more before encoding. Contradictions in optional sibling groups cannot erase separately accepted
mutation, report or completion proof. No recovery path invokes the mutation handler twice.

The observer uses the existing five-second lock acquisition timeout and existing per-command
bounds; five seconds is not a total observation deadline. Cleanup observes filesystem authority
only. Recovery performs no service, database or filesystem mutation.

### Encoding boundary

Encoding failure must not trigger another observation or a generic evidence-erasing
fallback. From a semantically validated result, retain classification, coherent primary failure,
report, identities and completions. If the original result was successful, choose the operation
base nonzero failure category/outcome above, preserving any eligible passing report and completion
proof; succeeded/0/null cannot survive unavailable final facts. Replace the final-fact group with
unavailable facts, discard
optional original diagnostics, and emit a fixed redacted encoding warning. Revalidate and encode
this small failure envelope. At most two encoding attempts are allowed.

Required report and completion evidence must not be truncated to fit. Implementation must prove
that the reduced envelope fits the existing 1 MiB bound using maximum valid report strings and
64 maximum-sized completed targets, JSON escaping and all identity fields. Existing input/output,
collection, string, migration and depth limits remain unchanged. Large observational integers or
other encoding failures are removed with the whole fact group. If the existing validator permits
required evidence exceeding the reduced envelope's bound, stop and return to design review rather
than silently dropping proof or introducing a retry loop.

### Shared cleanup interface and encoding proof

`validate_cleanup_completion(request_value: HostRequest, outcome: str,
state: Mapping[str, object]) -> None` is exported by `host_protocol/__init__.py` and implemented
in `host_protocol/mutation_results.py`. Both helper recovery and controller handling call it after
exact cleanup-state validation. It owns confirmed-batch membership, success/all-target and
inspection/no-completion invariants, without a new target vocabulary or deletion authority.

Maximum escaped reduced-envelope regressions retain required proof without truncation:
restore with eight 4096-byte control-character summaries and 255-byte release identities encoded
as 199,289 bytes; cleanup with 64 confirmed 1024-byte escaped paths and 255-byte temporary
identifiers as 491,606 bytes. Both decoded and fully validated within 1 MiB. Actual large-integer
serialization failure is covered; the fact group is replaced rather than required evidence dropped.
Individual delivery checks belong to task records; these regression obligations remain.

### Alternatives and accepted trade-offs


Making every invalid internal result unknown with no report/completions is simpler, but loses
consequential evidence already required by the failure contract. Retaining the translator keeps
old-shape inference and its demonstrated degradation. A second typed handler interface adds a
migration contract without removing more behavior. Fixed projections reuse strict
validation, at the cost of explicit local precedence and focused malformed-result coverage.
Rejecting a whole invalid final-fact group can lose otherwise valid individual facts; one bounded
fresh observer is the accepted replacement. Controller wire consumers receive no partial salvage.

## File boundaries

Paths are relative to `ops/taskman_ops/`. The architecture's
[repository map](2026-09-09-dedicated-host-deployment-design.md#repository-map) supplies the broader map.

| Contract owner | Responsibility |
| --- | --- |
| `cli.py`, `workflows/deploy.py`, `workflows/provision.py`, `workflows/restore.py`, `workflows/cleanup.py` | Flags, target intent, plans, confirmations and command results. |
| `releases/identifiers.py`, `manifests.py`, `build.py`, `artifacts.py`, `source_order.py` | Exact artifact/provenance identity, frozen source export, byte budgets, installed/cache resolution and bounded local source ordering. |
| `host_protocol/`, `workflows/helper.py` | Envelope and operation schema/budgets, request correlation, strict controller reply consumption and shared cleanup completion authority. |
| `host_helper/records.py`, `state.py`, `paths.py`, `backup_protection.py` | Completed records, coherent authority, derived paths and protected migration-attempt lifecycle. |
| `host_helper/restore_target.py`, `restore_database.py`, `operations/restore.py` | Restore binding/reference lifecycle, exact OID/authentication/load/arrangement authority and consequences. |
| `host_helper/operations/discover.py`, `preflight.py`, `helper_client/runner.py` | Command-specific observation, packaged admission and the finite sensitive credential entry. |
| `host_helper/operations/deploy.py`, `rollback.py`, `cleanup.py` | Explicit consequence order and operation-specific refusal/recovery rules. |
| `host/facts.py`, `host/acceptance.py`, `host/baseline.py`, `provisioning.py` | Resource-based provisioning admission and confirmed convergence; no marker-based authority. |
| `host_helper/backups.py`, `backup_helper.py`, `scheduled_backup.py`, `services/backups.py` | Shared backup/reference retention, scheduler quiescence/refresh and installed adapter contracts. |
| `host_helper/verification.py`, `workflows/verify.py`, `output.py` | Fresh bounded report production and validated public presentation. |
| `host_helper/__main__.py` | One dispatch, exact result validation, bounded internal recovery and two-attempt evidence-preserving encoding. |

Shared standard-library record/protocol modules are explicitly included in each consuming zipapp.
Tests exercise those generated executables, public commands and distinct capability boundaries.
There is no second controller-owned reference model or generic recovery engine.

## Acceptance scenarios

These are regression obligations for the supported behavior, not pending implementation steps or
proof that native acceptance has just been repeated. Use focused public controller-to-helper tests
and direct capability tests for distinct recovery states. The
[architecture test design](2026-09-09-dedicated-host-deployment-design.md#test-design) defines trust-domain
coverage; the [acceptance report](../research/2026-09-17-operations-vps-acceptance.md) records identified results/limits.

| Boundary | Required observable outcomes and consequential refusals |
| --- | --- |
| Artifact identity/source snapshots | Lost local artifact: installed provenance or safe new-ID rebuild. Same-source differing bytes get distinct IDs; identical bytes reuse within clean/dirty class. Explicit dirty acknowledgment/clean-artifact allowance refusal. Frozen tracked changes/deletions/nonignored files included; ignored canaries excluded. Digest/source/layout/path disagreement refuses. |
| Compatibility | Supported retained release/backup consumers and future scheduler refresh; rejected old IDs/manifests/records/runtime at local/host authority boundary, no conversion/adoption. |
| Budgets | 64/65/256 fingerprints accepted at their exact nested schema locations, 257 refused; 512 versions accepted/513 refused. Unrelated collections remain 64. Maximum filenames and complete manifest/record/request/result bytes at/beyond limits; local refusal before publication/mutation, codec round trips and no proof truncation. |
| Pagination | Byte-driven pages below 64, all-page public output, invalid cursor/snapshot drift/removed versus unknown continuation, bounded restarts and no partial success. Multi-page target/downgrade resolution and byte-batched cleanup with newly protected targets/interruption. |
| Deploy reconciliation | Retry failed verification and retain original report/physical selection; replace unhealthy partial target without first repairing it. Distinct migration-prefix/backup/protection/selection/start/verification/history/removal interruptions; missing or conflicting authority/links/unknown side effects refuse. |
| Confirmation/downgrade | Interactive/unattended/JSON/dry-run and explicit/clean/dirty targets; apply-time drift under yes. Lower SemVer/ancestor/divergent/missing objects/unorderable versions/mixed signals/rebuild equality/no-baseline; distinguish known downgrade and uncertainty. Unacknowledged noninteractive action refuses 10, dry-run reports acknowledgment. Before-first-success protected/live-prefix baselines retained even without current. |
| First provisioning | Partial convergence/staging/migrations/start/history/lost reply supports changed source or exact target without original archive; null-baseline safety and missing-current provenance, populated-state refusal, immutable candidates and no marker authority. After durable first success replacement uses deploy. Scheduler pause/quiescence before replacement. True absent identities reach mutation-free packaged dry-run and confirmed creation; incompatible/partial presence, wrong ready credentials, retained-byte mismatch and post-convergence foreign schema refuse. |
| Restore before first success | Full-schema backup with installed provenance, with/without current; safety/typed confirmation/null-baseline binding/first success/reference transfer/swaps/lost output. Partial source-incompatible dump, missing infrastructure or corrupt authority refuses. |
| Restore after failed deploy | Physical/history mismatch and partial migration prefix admit explicit typed restore to validated source; selected backup and pre-restore safety differ in full-schema versus manual-recovery guarantees. Corrupt backup/drift refuses; no automatic restore. |
| Same-backup restore retry | Binding/load/rename/selection/verification/history/cleanup/lost-reply interruptions, absent canonical/retired-only states, partial temporary rebuild and original preservation. Wrong OID/owner, failed observation, unacknowledged different input and unknown arrangement refuse. |
| Creation intent/OID registration | Intent/create/register/rebuild/drop/recreate/rename interruptions; only proved pristine unregistered temporary with pending intent may register, no load beforehand. Populated/wrong owner/writers/failed pristine proof refuse; promoted canonical uses registered OID and success cleanup tolerates absent retired original. |
| Restore target replacement | Before/during load/between renames/after failed verification; preserve original and fresh safety for possible writes to either original or failed restored DB. Every binding/discard/rename interruption, pending/third-target normalization without running abandoned source, exact discard OID/typed confirmation/backup-before-deletion. Durable success requires cleanup then separately confirmed new action. |
| Unusable abandoned input | Missing/unreadable/corrupt/checksum-failed dump permits replacement/normalization only with valid recorded metadata and original authority; report/preserve remnants. Ordinary retry and independently required safety still require full validation. Unsafe paths/corrupt binding/invalid safety refuse. |
| Completed restore/reapply | Post-success retired/binding cleanup works when health fails/unknown without reload/duplicate success; wrong identities/references refuse. Ordinary rerun preserves later writes. Explicit reapply gets new typed plan/fresh safety/baseline/exactly one new success; completed-binding cleanup then new plan; unfinished reapply/mutually exclusive flags refuse. Dry-run distinguishes completed checking, replacement missing acknowledgment and explicitly requested reapply. |
| Restore facts/authentication | Flat complete binding plus exact digest/null absence, canonical/top-level consistency and absent DB versus absent/present-empty/populated migration table. Failed query never absence. Real protected FD load/session execution identities/application object ownership, canonical authentication unchanged and trusted-dump privilege qualification. |
| Attempt retention | More than 64 migration attempts/restore replacements under one sequence; original/newest/three eligible intermediates, transient sixth, multiple fresh safety copies, identical/rollback clocks and all independent references. Register fresh safety before confirmed retirement, interrupt at each reference/file deletion, no retry-count refusal, no abandoned-input accumulation or pruning scheduled/manual backups. Original-DB safety remains held even when failed-restored safety is newer. |
| Backup-source provenance | After A migration1/B migration2 commits and migration3 fails, backup current prefix using eligible protected B rather than A. Deterministic ordering/current preference/absent current/conflicting hashes/no single source refusal. No unrelated/new artifact adoption; partial dump not advertised automatically restorable. Exact IDs, not matching source/schema/timestamp, determine references. |
| Cleanup | Low capacity/unavailable canonical/unfinished install/current-history mismatch admit only proven-safe confirmed targets; preserve full history/provenance/protections/bindings. No inspect normalization; no eligible targets remains truthful. Changed protection/unsafe references/inode drift/partial pair deletion/lost reply retain consequence evidence; no database/service/capacity or scheduler refresh dependency. |
| Packages/scheduler | Both actual standard-library zipapps under -I -S; exact membership/import/entrypoint, compatible refresh/no-op/failure/interruption and started old-process quiescence before supported records. |
| Mutation/final facts | Actual helper/public round trips for success/no-op/pre-mutation refusal/lock/partial mutation/failed report/passing report plus failed history. Correlation/schema/outcome/unavailable-marker contradictions reject complete wire reply. Final unavailable never old snapshot; proved absence/empty distinct from failed observation. Provision convergence/helper refresh/earlier cleanup batches aggregate known/possible evidence and completions without fabricated later observations/report. |
| Internal recovery/encoding | Matching changed result with malformed siblings; passing report plus later failure; failed-report/category contradiction; invalid success/envelope/type/identities/observer shape/lock/error. Cleanup schema-valid reports always ineligible, completion is exact confirmed subset; duplicate/out-of-batch group rejected. Valid facts avoid another observer, invalid group permits one, handler never retried. At most two encodes, former success becomes failure, maxima proof and actual integer serialization refusal; one actual isolated packaged case. |

### Growing inventories and bounded responses

More than 64 and more than 4096 successful selections are covered by focused full-observer
regressions, including complete protection/rollback relationships without dropping history.
Public packaged deploy/restore uses small history whose oldest backup reference lies outside the
latest/predecessor projection; preserve that reference and refuse if its required dump is absent.
This accepted split retains real large-observer coverage while losing combined large-count packaged-
consumer exercise. The [parallelism report](../research/2026-09-16-operations-test-parallelism.md)
owns measurement rationale. Credential-free early provisioning inspection remains proposed in the
CLI UX workstream; it is not claimed as delivered coverage here.

## Verification gates

Use the development guide's focused/full operations checks, actual isolated executables,
help/confirmation/redaction contracts and mix precommit. Check local Markdown links/whitespace and
planning-term leakage. Scoped independent review for material correctness/security/architecture
changes inspects relevant consequences, retained references and actual public paths; no automatic
whole-branch review. Build/package changes require clean/controlled-dirty identified artifact,
manifest/hash/pins/private modes/exact-input cache and packaged runtime checks. Local/container
results do not establish native systemd/UFW/ACME/email/reboot or full destructive restore acceptance.
Affected native checks need separately authorized targets; retained evidence stays qualified to its
identified source/database and explicit limits.

## Rejected alternatives and caveats

- Separate resume/redeploy commands or a generic force flag add no useful operator distinction.
- Requiring original local bytes or a healthy partial candidate strands otherwise safe replacement.
- Overwriting source-named immutable directories destroys exact provenance; reproducible builds
  are not assumed and are not required to solve this deployment problem.
- A whole-worktree digest is not release identity. It would distinguish or duplicate comments,
  documentation, and unused source that do not affect deployed bytes; the final archive digest is
  the exact runtime authority.
- A general operation journal/replay engine is unnecessary. Backup protection records preserve
  recovery material; the restore-target binding identifies exact input and original database.
  Neither supplies execution history or permission to resume without fresh confirmation.
- Guessing backup identity from timestamps or choosing one matching dump is not authoritative.
- Version ordering alone cannot prove schema compatibility; downgrade acknowledgment cannot restore
  data or bypass migration fingerprints.

This model cannot repair arbitrary manual corruption or infer unrecorded database side effects.
Interrupted migrations without sufficient fingerprint/protection evidence remain manual;
the historical old staging installation was outside the compatibility boundary. Local dumps do
not survive host loss. Completed native acceptance is recorded in the acceptance report; future destructive
recovery still requires scoped authorization.

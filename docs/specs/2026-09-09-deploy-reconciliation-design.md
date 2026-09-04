# Desired-target deployment reconciliation

Status: approved specification; complete written design approved on 2026-09-14, including the
ops development constraints. Not yet implemented. Updated: 2026-09-14. Tracking: `tas-sr4b`.

## Authority and scope

This specification changes existing-host `taskman deploy` from exact-attempt recovery to
reconciliation with the operator's desired release. It is self-contained for planning together with
the [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md), which continues to own
host topology, credentials, archive safety, locking, provisioning, rollback, and restore.
The [runbook](../deployment.md) owns implemented operator instructions; do not document these new
options as available until implemented. The [development guide](../development.md) owns checks.

For implementation planning, this approved specification supersedes the older design's source-only release identity,
clean-only local release builds, artifact-resolution-before-SSH ordering, exact-attempt-only
deployment admission, blanket exclusion of pending records, and absence of unattended deployment
or provisioning confirmation, exact-artifact-only recovery of an unfinished first installation,
restore admission that requires physical selection to match successful history, and cleanup
admission that depends on database health, backup capacity, or completed deployment state. It also
supersedes provisioning admission based on `/var/lib/taskman-provisioning.state`.
The exceptions to completed-only records are backup protection and a single restore-target binding
for an unfinished restore. Neither records execution steps or introduces a general operation journal. Apart from the restore, cleanup, and
backup-reference changes specified below, other commands retain their existing consequence and
confirmation rules.

The [CLI UX proposal](2026-09-09-operations-cli-ux-design.md) builds on this specification's protocol
version 3, artifact/source rules, and deploy/provision acknowledgments. Reconciliation owns
`tas-6dkg`: retaining bounded failed-verification reports through helper and public controller
results, alongside truthful mutation evidence. CLI UX consumes and presents that evidence; it must
not reintroduce protocol version 2 or the superseded confirmation/build restrictions. Its general
progress UI and early provisioning inspection remain separate work.

## Development constraints and reliability scope

Follow the [operations development guidelines](../development.md#operations-development), including
their simplicity preference and Python-first workflow policy. These constrain this specification's
implementation and its upcoming plan; they do not authorize dropping the operator behavior below.

Assume trusted operators and no deliberately hostile interference with managed host state.
Support ordinary interruption, lost replies, concurrent cooperating commands, scheduled backups,
and accidental plan-to-execution drift. The existing lifecycle lock, checks at confirmation/apply
and lock-reacquisition boundaries, atomic publication, and bounded native subprocesses are the
coordination mechanisms. Do not layer a hostile-tampering defense or generic transaction/recovery
engine on top. Retain existing scoped path, permission, checksum, and destructive-target checks;
their role here is preventing mistakes and preserving known recovery material, not resisting a
malicious administrator. Do not repeat an unchanged validation inside one locked phase without a
specific intervening action that can invalidate it.

The restore binding, backup protections, database identities, and publication ordering below remain
necessary for the explicitly supported interrupted operations. Unknown or contradictory state may
refuse safely; no automatic repair of arbitrary manual corruption, unrecorded migration effects,
or combinations outside the supported state model is required. Additional recovery branches need
a concrete supported failure and a demonstrated benefit, not merely a conceivable timing sequence.

Implement orchestration and host-side multi-step work in focused Python procedures, using existing
transport/packaging and bounded argv calls to native tools. Short shell remains acceptable when
clearly simpler. Do not add substantial shell for scheduler refresh, restore normalization,
protection pruning, or result assembly. The parked PostgreSQL refactor is not a prerequisite.

Keep the plan organized around behavior and consequence boundaries, with the smallest useful
owners and actual shared consumers. The acceptance scenarios describe distinct states and
guarantees, not an exhaustive Cartesian product of interruption timings and command options.
Exercise each distinct durable recovery state and the real public entry paths; reuse focused
coverage for equivalent transitions. If a simplification would remove an accepted repair path or
weaken a core guarantee, identify that trade-off for operator approval before changing the design.

## Problem and observed baseline

Evidence comes from repository code and staging observations recorded in `tas-sr4b`, `tas-q5lo`,
and `tas-6dkg`. The dedicated-host design retains technical mechanism references. Specification
preparation introduces no new external research or host acceptance evidence.

An authorized staging deployment selected and started the OTP 29 release, then failed verification
before publishing successful-selection history. The controller returned exit 9 with stale
`changed=false`, the previous selected ID, and no failed check report. Later read-only checks found
the new executable running and all individual readiness predicates passing. The original failing
check is unknown; a startup race has not been established.

Controller planning uses strict discovery and refuses this state before its helper's retry logic
can run. Both standalone verification and exact-artifact deployment dry-run returned exit 10.
Requiring the original workstation archive also strands an operator after cache loss. Rebuilding
can produce different archive bytes with the same current release ID, which correctly refuses an
immutable-directory collision but leaves no normal deployment path.

Relevant recorded environment, to be refreshed before external action:

- Checkout: GitButler-managed Taskman repository; preserve existing work. Runtime implementation
  checkpoint was `42d019920b7540509ac8fde944d4b979f7178e92`; subsequent documentation amendment
  observed as `871088fb4ae29c23de74231948e824ec3b16a095`. Do not assume either is the current head.
- Staging: `root@taskman.page:22`, Ubuntu 26.04 amd64, managed systemd/Caddy/PostgreSQL installation.
- Last successful selection: `0.2.0-8266656863ad-ubuntu26.04-amd64-otp27.3.4.6`.
- Physical selection: `0.2.0-42d019920b75-ubuntu26.04-amd64-otp29.0.6`, archive SHA-256
  `9d7e444f37622cf3e9f96d8891082b882999d84ed10457d7539ae9b4018d7225`.
- Both releases are installed. Only the older genesis selection is recorded. No migrations
  changed, and no deployment backup was needed or created.
- The scheduled backup executable was already refreshed for OTP 27/29 compatibility, not for the
  new record formats in this proposal. Its observed SHA-256 was
  `056bdc6dba387e1b814210a5970dcc3d4d30f29c145ff4b2dd7f7ace84e1b586`.
- Runtime baseline passed 915 operations tests, 805 Elixir tests, clean release build/cache and
  packaged terminal checks; these are historical evidence, not verification of this proposal.

The [readiness handoff](../handoffs/ops-vps-readiness.md) owns temporary artifact locations and
the next authorized host acceptance. Runtime deployment and readiness remain authorized, but no
manual symlink/history edits, push, merge, destructive restore, or broader acceptance is implied.

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

New artifact manifests use schema version 3: the existing exact version-2 field set plus
`artifact_sha256` and the strict boolean `source_dirty`. Validate agreement between the artifact
digest field, the digest portion of the ID, the detached checksum, and the actual archive.
`source_dirty` records whether the captured source contained tracked or non-ignored untracked
changes relative to `source_revision` and must agree exactly with the presence of the terminal
`-dirty` suffix. `built_at` may differ between manifests for the same exact release identity. All
fields that describe source class, archive, target, toolchain, builder, layout, and migrations must
still agree. Preserve the originally installed manifest when reusing an identical installed
release rather than overwriting its provenance. Version-2 artifacts and historical IDs remain
readable and imply clean source, with their existing strict runtime allowlist and checksum rules.
Do not rewrite or rename historical artifacts.

New installed release records use schema version 2: the existing `release_id`, `source_revision`,
`artifact_sha256`, and `migrations`, plus `schema_version: 2` and `artifact_manifest` containing the
full validated detached manifest. All duplicated identity fields must agree. Publish it with the
immutable release, never retrofit it into a historical directory. Existing four-field records
remain readable and retain their exact historical serialization.

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

Do not infer full builder provenance from a historical four-field installed record. Such a record
can be reused with an explicit verified matching artifact; otherwise skip it for automatic
host-side matching and obtain/build a new artifact. This is not a failure or a reason to demand
the missing historical archive. Corrupt authoritative installed metadata still refuses; a merely
incomplete historical provenance format does not. Invalid local cache entries remain ignored and
preserved under current rules. Arbitrary retained upload files are not a new automatic artifact cache.

Host reuse requires no local archive or upload. It validates the exact installed record, managed
tree/launcher authority, and retained provenance; it does not claim to reconstruct an archive hash
from extracted files. Explicit artifacts remain authoritative and may reuse a matching installed
record without re-extraction after local validation. Revalidate the clean source identity before
confirming an automatically resolved clean target; source drift requires resolution again. Dirty
targets are already fixed by their private frozen snapshot and exact artifact digest.

`build` never connects to the deployment host. It still requires the workstation's build prerequisites,
including Docker/BuildKit capable of building the pinned `linux/amd64` target; this does not imply
offline operation or verified support for every workstation CPU architecture.

Provisioning shares the updated build process and support for reading legacy and new artifact
manifests and release records. It may build a dirty checkout or accept an explicitly selected dirty
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
artifacts retain their normal meanings. Historical installed records without full provenance are
skipped for automatic reuse, allowing a new build with a distinct immutable identity. The failed
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

Remove the provisioning marker from new writes and admission decisions. An existing
`/var/lib/taskman-provisioning.state`, whether valid, malformed, or absent, neither permits nor
blocks provisioning. Leave historical marker files untouched and do not follow links at that
retired path. No replacement flag file or provenance token is introduced. Before the first
resource change, a failure leaves the host eligible for the same resource-based inspection;
after partial convergence, inspect the resources actually present. Reuse the concrete validators
in `host/facts.py`, `host/acceptance.py`, and helper path/credential/record modules, replacing their
marker-based classification rather than treating its old absence refusal as a retained rule.

This supersedes the earlier marker requirement: a protected constant string did not establish
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
This narrowly extends backup
creation to an unfinished installation without physical current; the backup record format remains
unchanged. A partial-schema dump retains the existing manual-restore caveat.

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
require explicit `--migration-policy backward-compatible`. `no-change` with pending versions
refuses. `restore-required` with pending versions refuses with the existing restore-required reason;
deploy never performs an implicit restore. A supplied `backward-compatible` declaration remains
acceptable when a retry finds all versions already applied. A `restore-required` declaration with
no pending versions is unnecessary and refuses as a mismatched policy. Missing required policy is
exit 2; contradictory schema or policy is exit 10. Migrations are not reversed.

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

New successful selections use schema version 2 with exactly the legacy fields plus
`schema_version: 2`, `observed_previous_release_id`, and `recovery_backup_ids` (sorted, unique, at
most 64 IDs). `previous_release_id` names the last successful release, never an unverified physical
candidate. `observed_previous_release_id` names physical current at the confirmed start (null only
when no release was selected before first success, including a first successful restore).
`recovery_backup_ids` includes all unresolved protections being resolved. `backup_id`
continues, for deploy/provision, to identify the most recent pre-migration backup for this reconciliation, or null if the
set is empty; choose the highest protection `attempt_number`, not by dump or protection timestamps.
Readers preserve legacy four-field selections and their original filename hashes unchanged.

After verification, append the new selection durably before removing any resolved protection files.
Its references preserve protection if cleanup is interrupted. A remaining protection already covered
by that exact successful history is recognized as resolved and can be removed under the lock; never
remove a retained protection first. This success-publication rule is separate from the confirmed
retirement of superseded intermediate attempts described above. A new record may name the same release as its last-successful predecessor
when reconciling a different physical selection or resolving outstanding protections; this describes
a freshly verified outcome, not a synthetic intermediate success. An already verified matching
selection with no unresolved protection does not append duplicate history.

Retained selections protect all recovery backup IDs as well as legacy `backup_id`. Ordinary
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
record and dump authority remain independently validated. Before writing new-format host records,
ensure the compatible scheduled helper through the same confirmed sequence: pause scheduled
backups, wait for any running backup to finish, replace and checksum-verify the backup program,
then restart the timer if enabled, following the locking rules in Scheduled helper compatibility.

For restore before first success, discovery and expected state carry null
`last_successful_selection_id`, and null `selected_release_id` only when its absence is proved.
The helper applies the same admission rules as the controller; neither substitutes an empty
history or null selection for a failed observation. On retry after first success was published,
recognize the completed restore through its exact backup references and null original baseline.

### Rerunning an interrupted restore

Rerunning `taskman restore ENV BACKUP_ID` with the same backup must reach restore-specific inspection
even when the canonical database name is temporarily absent. Initial preflight verifies PostgreSQL
cluster/admin access through its maintenance database, database credentials, filesystem and installed-release
authority. It must not require application readiness, a connection to the canonical application
database, or successful-selection equality before inspecting the restore arrangement. This applies
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
or intermediate databases without a valid binding refuse with a bounded explanation. Legacy
interrupted restores without this new binding remain manual; do not invent their backup identity.
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
authorize deletion of damaged or unknown files under ordinary retention.

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

## Scheduled helper compatibility

Both transient and persistent scheduled packages must read legacy and new installed/selection
records and apply protection-aware retention. Before deploy publishes any new-format host record,
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
consequences. Do not publish new-format records before this sequence succeeds. Retain the lock for
subsequent deployment steps; a restarted compatible scheduled process may wait normally.

If refresh fails, restore the enabled timer only when the installed executable is verified as
either the previously observed valid package or the new package, and report any restoration failure.
If execution is interrupted, the next confirmed deploy observes enablement, executable identity, and
service state and repeats these same steps; it never guesses the prior timer activation state.
No timer enablement, schedule, or unit-content change is authorized. Concurrent manual starts of an
obsolete executable by a trusted administrator are outside the normal scheduler coordination model.

## Reconciliation procedure and transport

Keep orchestration in the controller and host mutation in the host-side helper; add no generic workflow engine.
Use host protocol version 3 for the coordinated transient controller/helper change. Retain envelope,
correlation and redaction rules; use the explicit collection and byte budgets below. Old transient protocol requests refuse; the
controller always transfers its matching helper. Persisted legacy readers are not wire-version
compatibility shims.

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
- Other persisted records and legacy installed records: their existing 64-KiB limit.

These are aggregate budgets, not independent allowances for each nested component. New migration
filenames must fit the supported filesystem component bound of 255 UTF-8 bytes as well as the
existing filename grammar. Validate the actual complete encoded manifest and prospective installed
record during build/artifact validation before publishing a reusable artifact. Validate both
uploaded-target and installed-target request representations, including enclosing metadata, and
check the exact environment-specific request again before upload or host mutation. Oversized local
artifacts fail with exit 2 and a bounded explanation of the violated limit, never after staging.
The host validates the same record budgets before publication and enforces message bounds at its
input/output boundary. No truncation of migration or identity data is allowed.

Legacy formats remain unchanged on disk. A valid legacy installed record within its existing
64-KiB and migration-count limits must be transportable through v3, including records with more
than 64 migration fingerprints. Explicit legacy artifacts undergo the aggregate transport and
prospective-record checks before use; local input outside those supported budgets refuses before
host mutation, without rewriting installed legacy records. Invalid or oversized persisted legacy
records remain authority failures, not silently skipped data. Inventory/history growth is a
separate discovery/listing concern; larger messages are not a substitute for bounded projections.

Extend the existing read-only `discover` operation with four inspection modes: `strict`, `deploy`,
`provision`, and `restore`. Mode `strict` retains the existing completed-installation checks;
the other modes support the unfinished states admitted by their respective commands.
In protocol version 3, parameters are exactly `credentials_path`, `database`, and `mode`, except
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
backup protection remain unchanged. In particular, the existing 4096-entry implementation guard
must not become a lifetime successful-selection limit: scan valid history incrementally under the
lock, with per-record validation and the normal bounded operation timeout, not a fixed record-count
refusal. A timeout is reported as incomplete inspection, never a truncated successful history.

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

Cleanup inspection also pages its eligible targets: add `cursor` to its exact parameters (null for
execute), and return `inventory_sha256`/`next_cursor` alongside its existing confirmation facts and
target list. Apply the same count/byte budget, deterministic ordering by `(kind, identifier, path)`,
and snapshot-change refusal; the cursor's `after_id` is the canonical JSON encoding of that target
tuple. Its inventory digest covers the full ordered eligible target set and confirmation facts.
Collect all pages before typed confirmation. Execute the confirmed set in byte-bounded batches
of at most 64 targets, freshly revalidating protection facts and each target under the lock for
every batch. Internal batching never authorizes a target absent from the confirmed plan; failure
reports partial changes and requires a new inspection/confirmation for remaining work. Cleanup
does not require generic operational discovery or export full history to validate protection.

The UX proposal's credential-free provisioning inspection uses inventory counts and eligibility
facts rather than full release/staging arrays. It still validates the underlying records on the
host and does not infer a pristine installation from omitted or uninspected records.

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
must explain possible changes. This follows the separate CLI UX proposal's retained semantics.

Preserve exit categories: 2 invalid input/missing migration declaration, 3 local build prerequisite,
5 SSH/preflight, 6 backup, 7 migration, 8 staging/selection/service lifecycle, 9 verification,
10 unsafe/incompatible state or missing unattended acknowledgment, 11 restore or restored-database
validation failure, and 12 lock contention.
Scheduler dependency refresh failures use 8 once its mutation begins; prior unsafe identity uses 10.
Transport loss keeps its transport status and unknown consequence evidence, not an invented
migration failure. Completing deployment or a newly restored release requires a complete passing
report and durable successful history. Cleanup-only restore retries retain their explicit exception:
they validate durable completion and recovery authority, not current application readiness.
Transient service/readiness polling retains a finite budget; do not expand this work into an
unproven startup-race fix. A timeout must retain the failed checks.

### Exact protocol-v3 mutation results

Keep the existing result envelope keys: `protocol_version`, `operation`, `correlation_id`,
`outcome`, `message`, `state`, and `warnings`. Outcomes remain `succeeded`, `refused`, `retryable`,
and `manual`. Validate correlation, operation, and the complete operation-specific state before
consuming any result. The following replaces the older optional success-only `changed`/report
fields for `deploy`, `genesis`, `restore`, and cleanup execution. Failed cleanup inspection also
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
| `report` | Null when no validated report is available from this invocation, otherwise the existing strictly parsed `VerificationReport` mapping |

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

## File boundaries

Expected owners, relative to `ops/taskman_ops/`:

- `cli.py`, `workflows/deploy.py`, `workflows/provision.py`: command-specific flags, target intent,
  ordinary confirmation, independent downgrade acknowledgment for deploy/provision, plans, and results.
- `releases/identifiers.py`, `manifests.py`, `build.py`, `artifacts.py`: exact artifact identity,
  clean and frozen-dirty source export, legacy reads, build-after-hash naming, local and installed
  resolution; small source-order helper within `releases/` if needed, not host code that runs Git.
- `host_protocol/`, `workflows/helper.py`: versioned discovery/request/result integration,
  schema-specific budgets, and validated page collection within command timeouts.
- Host listing/discovery operations and their controller consumers: compact operational facts,
  paginated release/backup inventories, snapshot validation, and complete public listings.
- `host_helper/records.py`, `state.py`, `paths.py`: dual-format records and coherent authority;
  a focused `host_helper/backup_protection.py` owns protection publication/reference lifecycle.
- `host_helper/operations/deploy.py`: explicit reconciliation consequence order.
- `workflows/restore.py`, `host_helper/operations/restore.py`: restore-specific admission from an
  unfinished deployment or database swap, typed data-loss confirmation, restore-target binding,
  and verified history/protection resolution.
- `workflows/cleanup.py`, `host_helper/operations/cleanup.py`: capacity-independent cleanup admission,
  paginated read-only inspection, recovery-aware protected targets, and exact confirmed deletion
  in bounded batches.
- `host/facts.py`, `host/acceptance.py`, `host/baseline.py`: resource-based provisioning admission,
  removal of marker reads/writes from the active workflow, and safe partial-resource classification.
- `host_helper/backups.py`, scheduled adapter, cleanup/rollback/restore consumers: new reference
    protection and preserved command-specific safety, without a shared generic recovery engine.
- `services/backups.py`, helper packaging and narrowly scoped installation capability: compatible
  persistent executable and safe scheduler coordination.
- `verification.py`, workflow verification translation/output: preserve failed evidence, not a
  full output-framework rewrite.
- `ops/tests/` mirrors these trust boundaries; update package-isolation and public CLI coverage.
  Update the runbook, canonical design, and documentation index when the behavior is implemented.

## Acceptance scenarios

Acceptance requires focused controller-to-helper scenarios, not helper-only retry tests:

### Artifact identity and compatibility

#### Recovery without local artifacts

Lose local artifacts: reuse sufficient installed provenance, or rebuild safely with a new ID;
explicit artifacts remain exact and legacy records are unchanged.

#### Archive identity and source snapshots

Same-source non-identical archives get distinct IDs; identical bytes reuse identity within the
same clean-or-dirty provenance class even when irrelevant worktree content differs. Clean and
dirty artifacts have distinct IDs through the terminal marker. Reject digest/source/manifest
disagreements and unsafe paths. Exercise real clean build/cache reuse and dirty snapshots with
tracked changes, deletions, and non-ignored untracked files while proving ignored files stay
excluded.

#### Legacy and new formats

Legacy and new artifacts/records mixed through verification, scheduled backup, listings,
rollback, restore, and cleanup. No in-place legacy rewrite or generic adoption.

### Protocol budgets and inventory pagination

#### Artifact, record, and message limits

Budget coverage must exercise 64, 65, and 256 fingerprints through new/legacy parsing, uploaded and
installed targets, embedded manifests, and helper dispatch; reject 257 fingerprints. Exercise
512 observed versions and reject 513 at their schema boundary. Keep unrelated collections capped
at 64. Test maximum-length filenames, complete serialized manifests/records/requests/results at
and beyond each byte limit, and actual nested codec round trips. Local budget failure must precede
artifact publication or host mutation; valid legacy records must remain usable without rewriting.

#### Growing inventories and bounded responses

Exercise more than 64 and more than 4096 successful selections without rewriting or dropping
history: public deploy/restore discovery must remain representable and validate all protection and
rollback relationships. Cover byte-driven pages below 64 entries, complete public listings,
invalid cursors, inventory drift between pages, bounded retries, and no partial-success output.
Exercise automatic target resolution and downgrade classification across multiple release pages,
and cleanup plans/execution spanning multiple byte-bounded batches with interruption and newly
protected targets. Verify that early provisioning inspection also remains bounded as inventories grow.

### Deployment reconciliation and acknowledgment

#### Retry after failed verification

Select target, fail verification, rerun the public controller with the same target, and complete
only after verification; expose the original failed report and real selection on failure.

#### Replace an unhealthy partial target

Replace an unhealthy partial target with another target, without starting/verifying the failed
one first; history links successful outcomes, not an invented success.

#### Interruptions at deployment boundaries

Fail after each migration commit prefix, backup publication, protection publication, selection,
start, verification, successful record, and protection removal. Retry or replace from live schema.

#### Unsafe or unprovable state

Missing/conflicting provenance, malformed protection, unsafe links, absent history/current,
incompatible downgrade, and nontransactional/unknown database state refuse without repair.

#### Confirmation and downgrade policy

Deploy and provision interactive, unattended, JSON, and dry-run confirmation matrices, including
dirty local builds, implicit acknowledgment from an explicit dirty artifact, redundant
`--allow-dirty` for a dirty artifact, refusal for a clean artifact, and apply-time drift after
`--yes`.

Both commands cover known/unknown source ordering, lower SemVer, divergent revisions,
same-source rebuilds, and baseline drift. Provision also covers older replacement after failed
startup or committed migrations, protected migration targets without current, legacy installed
provenance before first success, no-baseline fresh installation, and independent ordinary and
downgrade prompts/flags.

Non-interactive known downgrades or unknown ordering against an existing
baseline without acknowledgment refuse exit 10, including under `--yes` or `--json`. Cover
missing commit objects, divergent histories, unorderable versions, mixed known/unknown signals,
identical-source/version rebuilds, and distinct known-downgrade versus uncertainty prompt text.
Dry-run reports required acknowledgment without demanding it.

### Provisioning and first-installation recovery

#### Interrupted provisioning with a changed target

Interrupt provisioning during staging, partial migration, selection, start, verification,
first successful selection publication, and protection removal. Rerun the public provision
command with changed clean source, dirty source, and different explicit artifacts; lose the
original archive. Validate reuse or new immutable builds, missing-current recovery, multiple
installed candidates, fresh protected backups with a null baseline, migration-policy refusal,
and successful first-history publication. Existing data must survive; incompatible targets
refuse. Once the first selection is durable, replacement requires deploy, including after
transport loss. Exercise pausing scheduled backups and waiting for running backups to finish
before replacing the backup program during provisioning recovery.

#### Resource-based admission without a marker

Provisioning admission is identical with an absent, historical valid, malformed, or symlinked
marker at the retired path; it neither follows nor modifies that path. Interrupt before the
first resource mutation and at supported partial-convergence boundaries, then rerun through
resource inspection and confirmation. Compatible partial state is reusable without a marker;
a valid marker cannot admit a foreign database, conflicting service/configuration, unsafe
path, or unrecorded release. Plans identify reused resources and initial database emptiness
is verified independently of migration-history emptiness.
Missing accounts, directories, unit files, or credentials in an otherwise safe partial install
do not by themselves block admission; they are created and validated before use. Missing
migration provenance for existing populated data still refuses.

#### Restore before the first successful selection

Before any successful selection, interrupt provisioning after a complete migration schema,
capture a validated backup, and exercise a subsequent failed transition. Through the public
restore command, restore that backup with and without `current` present, using installed
migration metadata rather than successful history as provenance. Verify safety-backup creation,
typed data-loss confirmation, null-baseline binding, first-success publication and protection
transfer, and retry after interrupted swaps or lost completion output. Reject a partial-schema
backup incompatible with its source release, corrupt/missing metadata, and missing required
restore infrastructure. Future release replacement must use deploy.

### Restore recovery

#### Restore after a failed deployment

Through the public controller, deploy from verified A to B, apply migrations, select B, then
fail startup/verification. Restore the validated pre-deployment backup and A directly, without
first completing B. Also cover failure before selection with a partial committed migration
prefix. Assert typed data-loss confirmation, dry-run, unchanged or differing physical/history
identities, safety backup, corrupt/incompatible backup refusal, apply-time drift, preserved
protections on failure, verified restored schema, and durable reference transfer on success.

#### Interrupted restore and same-backup retry

Interrupt restore before/after binding publication, during dump loading, after each database
rename, after release selection, verification, successful history, retired-database deletion,
and binding removal. Rerun the public command with the same backup, including when canonical
is absent; complete after renewed confirmation. Reject an unacknowledged different backup with identical
migration versions, OID/ownership drift, and unknown arrangements. Verify temporary rebuild
after partial load, preserved original/safety material, retention protection, accurate dry-run,
capacity checks for remaining work, and no duplicate restore after durable completion.

#### Rebuilding incomplete temporary databases

For ordinary same-backup retries, interrupt after dropping temporary and before recreating it
while the original is retired; accept retired-only state with the validated binding and original
OID, rebuild temporary, and finish without target replacement. Also interrupt immediately after
temporary creation and before its migration table is loaded. Missing/corrupt binding, wrong
original OID, or failed observation must still refuse; preserve original and unrelated databases.

#### Replacing an unfinished restore

Exercise `--replace-unfinished` before loading, during partial loading, between renames, and
after failed application verification. Preserve the original OID and safety copies, including
possible new writes to a failed restored database. Interrupt every replacement binding update,
discard, and rename; resume the pending target or select a third target without requiring the
abandoned target to run. Cover retired-only inspection, exact discard-OID checks, backup failure
before deletion, typed confirmation, dry-run, helper/controller parity, retention references,
and already-successful restores requiring cleanup followed by a separately confirmed new plan.

#### Cleanup after durable restore success

After durable restore success, interrupt before retired-database deletion and before binding
removal, then make readiness fail or become unobservable. A retry must validate authority and
finish cleanup without requiring readiness, reloading the dump, or adding success history;
report health separately and allow a subsequent separately confirmed deploy or restore.
Wrong database identities, conflicting success references, and unsafe backup authority still
refuse cleanup. Cover both controller and helper admission, not only the cleanup body.

#### Explicitly reapplying a completed restore

Restore a backup successfully, make later data changes, and verify an ordinary rerun does not
reload it. With `--reapply`, require a new plan, typed confirmation, fresh safety backup, and new
binding baseline; restore the data and append exactly one new successful selection. Cover
completed binding cleanup, dry-run without writes, failure and ordinary retry after binding
publication, rejection during an unfinished restore, mutually exclusive flags, and helper parity.

#### Bounded restore safety backups

Exercise more than 64 restore replacements with fresh safety copies and different input backups.
Preserve original/newest/three recent eligible safety attempts and all independent references;
release abandoned input references without treating those inputs as disposable attempts.
Include multiple safety copies in one replacement, identical timestamps, clock rollback,
interruption after registration/reference retirement/each backup-file deletion, and a transient
sixth attempt. Confirm exact prune IDs, no deletion before fresh protection, no accumulation
refusal, and bounded successful-history publication after recovery.

#### Replacing an unusable backup input

Replace an input whose metadata remains valid but whose dump is missing, unreadable, corrupt,
or fails checksum validation. Reach replacement through public preflight/discovery, normalize
any pending replacement, and load only the fully validated new input. Preserve remaining old
files and report their condition. Ordinary retry of the unusable input must still refuse;
corrupt binding/backup metadata, unsafe paths, conflicting identities, and an invalid required
safety copy remain refusals, including when the abandoned input has that independent role.

#### Restore previews and acknowledgments

Preview a different backup during an unfinished restore without `--replace-unfinished`: show
the validated replacement plan and missing execution acknowledgment, with no writes. Execution
without the flag still refuses. After completed restore, same-backup dry-run without `--reapply`
previews completion checking, while adding it previews a fresh restore and safety backup.

#### Restore binding discovery and digest

Verify flat discovery `restore_target` contains the complete validated binding plus its digest,
with null only for proven absence. Reject malformed fields and mismatched digests; hash only
binding fields and never persist the response digest. Build target/pruning plans from this
response and bind apply-time drift checks through `expected_state.restore_target_sha256`.

#### Database and migration-table observations

Distinguish absent database, existing database without a migration table, present-empty table,
and populated table in restore discovery and expected state. Check canonical/top-level agreement
and reject contradictory boolean/null/array combinations. Failed connection, permission, or
query observations refuse rather than producing absence or emptiness. Only supported incomplete
recovery states admit missing tables; ordinary application-database authority remains mandatory.

#### Database creation intent and OID registration

Interrupt after durable creation intent, database creation, OID registration, rebuild intent,
temporary deletion, replacement creation, and each rename. Register only a verified empty
unregistered temporary with valid creation intent; never load before durable OID registration.
Reject populated unregistered databases, wrong owner, unexpected writers, failed emptiness
observation, or mismatched restored OID after promotion. Verify post-success cleanup validates
the registered restored canonical OID while tolerating an already-deleted retired original.

### Backup retention and cleanup

#### Backup provenance after partial migration

Start with selected successful A containing migration 1. Deploy B containing migrations 1, 2, and 3;
commit migration 2, fail migration 3, and leave current at A. Through public deploy retry, scheduled
backup, and restore safety-backup paths, select B's protected installed provenance for the live
prefix rather than attributing migration 2 to A. Verify the stored source ID and actual versions,
and that a partial-prefix backup is not advertised as automatically restorable.
Cover multiple qualifying protected targets in different enumeration orders, preference for a
qualifying current, absent-current first-install recovery, conflicting fingerprints, and no single
eligible record covering the entire prefix. Source choice must be deterministic, must not use an
unrelated installed or newly supplied artifact, and must not change current or successful history.

#### Exact backup attribution and protected references

Multiple backups with identical source/schema do not cause guessed attribution; protect exact
referenced IDs. Scheduled retention and cleanup cannot delete retained unresolved or history-held backups
or required releases, including after target replacement and transport loss.

#### Bounded migration-attempt backups

Exercise more than 64 failed migration attempts and target replacements under one baseline:
retain the original, newest, and three most recent eligible intermediates, without a retry-count
refusal. Include null-baseline first-install recovery, identical timestamps, clock rollback,
independently history/restore-held backups, and unrelated scheduled/manual backups. Interrupt
after new dump publication, protection publication, each retired protection removal, manifest
deletion, and dump deletion. Prove that a transient sixth backup can be inspected and pruned,
failed replacement creation never prunes, original/newest and migration provenance survive,
independent references remain intact, and success records reference only retained protections.
Plans and apply-time checks must bind the exact proposed intermediate deletions.

#### Cleanup while recovery is unfinished

Through the public cleanup command, exercise low backup capacity, an unavailable canonical
database during restore, mismatched physical/successful selection, and unfinished first install.
Eligible unreferenced artifacts can be removed after typed confirmation; all required release
provenance, backup protections, restore bindings, and their referenced material survive.
Inspect/dry-run performs no normalization or managed writes. Cover no eligible targets,
malformed references, target becoming protected after confirmation, interrupted deletion,
and transport uncertainty without invoking database-health or backup-capacity preflight.

### Packaging and failure reporting

#### Isolated packages and scheduled-helper compatibility

Isolated `-I -S` execution of both packages; dependency refresh/no-op/failure/interruption and
an already-started old scheduled process cannot execute unsafe retention after new records.

#### Transport uncertainty and sensitive output

Transport loss and failed reobservation preserve uncertainty and the primary failed boundary;
never report stale unchanged success or leak raw remote output, credentials, or release cookies.

#### Mutation-result contracts and final observations

Round-trip every exact deploy/genesis/restore/cleanup result variant through the packaged helper
and public controller. Reject missing/extra fields, mismatched correlation/operation, invalid
outcome/status pairs, malformed reports, and contradictory unavailable markers. Cover successful
deployment, healthy no-op, pre-mutation refusal, lock contention, partial mutation, failed
verification with its report, and passing verification followed by failed history publication.
Exercise successful and failed final observation, including proved absence versus unavailable
current and empty versus unavailable migrations/protections. No failure may fall back to old
selection/schema facts or default missing mutation evidence to false.

Cover scheduler refresh before failure, provisioning convergence before genesis failure, partial
backup-pair deletion, and multiple cleanup batches followed by refusal or lost transport. Preserve
earlier proved changes and completed targets without claiming unknown later work completed.
Restore cleanup-only success must carry validated completion authority without requiring a new
passing report; cleanup must not inspect database health. Lost/malformed/oversized replies must
not fabricate observations, reports, or completion. Exercise result byte/count/nesting limits with
maximum migration arrays, recovery references, cleanup batches, and bounded verification reports.

## Verification gates

Run the development guide's operations suite, compileall, shell syntax, help/confirmation checks,
and `mix precommit`; check Markdown links, whitespace, and planning-term leakage. Independent scoped
review must inspect migration ordering, reference retention, legacy readers, and the actual public
retry path. Build/packaging changes require clean and dirty identified release builds plus
manifest/hash/cache and packaged-runtime validation. Local tests do not establish real systemd,
backup, or restore safety.

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
Historical interrupted migrations without sufficient fingerprint/protection evidence remain manual;
the observed no-schema-change staging failure does not need fabricated backup records. Local dumps
do not survive host loss. Full destructive recovery acceptance remains separately authorized.

## Next-session checklist

1. Read this complete approved specification and its referenced development constraints before
   implementation; approval does not mean the proposed behavior is already implemented.
2. Read the [approved implementation plan](../plans/2026-09-14-deploy-reconciliation.md)
   (operator approval: 2026-09-14) using the complete design and the development guide's
   ops-specific simplicity/reliability and Python-first rules, with Beads delivery tasks, scoped
   ownership, tests, and an independent verification task. Identify concrete safety reasons for
   nontrivial coordination or persistent state; avoid speculative branches and substantial shell.
   No implementation has begun.
3. Update the readiness handoff and start a clean implementation session by default. Refresh actual
   repository and host state before relying on the recorded baseline.
4. Implement and verify locally; only then continue the already-authorized staging deployment and
   readiness. Do not manually append selection records or repoint current to bypass the controller.
5. Administrator/login acceptance and deployment completion are separate outcomes. Track their
   current acceptance status in the readiness handoff; neither establishes the other.

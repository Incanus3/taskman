# Desired-target deployment reconciliation

Status: proposed specification; design sections approved in conversation, written specification
under operator review. Updated: 2026-09-11. Tracking: `tas-sr4b`.

## Authority and scope

This specification changes existing-host `taskman deploy` from exact-attempt recovery to
reconciliation with the operator's desired release. It is self-contained for planning together with
the [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md), which continues to own
host topology, credentials, archive safety, locking, provisioning, rollback, and restore.
The [runbook](../deployment.md) owns implemented operator instructions; do not document these new
options as available until implemented. The [development guide](../development.md) owns checks.

On approval, this specification supersedes the older design's source-only release identity,
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

## Problem and observed baseline

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

taskman restore ENV BACKUP_ID [--replace-unfinished] [--dry-run] [--json]
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
confirmation. Without it, requesting a different backup while a restore is unfinished refuses
with exit 10 and explains this repair command. The flag is harmless when no unfinished restore
exists or the requested backup already matches; neither case authorizes extra database deletion.

Dry-run resolves and validates the target and observes the host, but does not prompt, refresh the
installed helper, publish records, change services, or mutate the database. It may build a local
artifact. Missing confirmation flags do not make a dry-run fail; its plan reports required
acknowledgments. Incompatible schema or missing required migration policy still refuses.

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
fresh validated backup and protection with no successful-selection baseline. For this backup of an
already partially migrated database, if physical current is absent, select the backup's source
release from previously installed records that prove the
entire live prefix, ordered by full release ID. This is backup provenance, not application selection
or proof of a healthy release. If no such record exists, refuse. This requirement does not apply
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
release selected by the `current` symlink, valid credentials/database authority, safe paths, and
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
Keep the existing 64-KiB message/record bounds as serialization limits, not a 64-attempt recovery
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

Restore planning and execution require valid successful history when present, credentials and
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
service/credential infrastructure, and a safety backup; it does not provision missing host services.
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
live migration prefix of an unfinished deployment through the shared backup capability. It must
also support an unfinished first installation without `current`, using the same validated installed
migration-provenance rules as provisioning. This pre-restore safety backup preserves the database
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
and the chosen restore backup ID, plus the binding's retained replacement recovery backup IDs.
Validate representability before destructive consequences.
Only after durable publication may resolved protection files be removed; history retains their
backup references. A restored release may equal its successful predecessor. No synthetic success
for the failed release is inserted.

Use a restore-specific discovery mode and controller admission so the public command reaches this
procedure without passing strict completed-deployment discovery first. For protocol v3, restore
discovery exposes the same physical/history/schema/protection/scheduler facts as deploy. Restore's
`expected_state` uses deploy's keys except `downgrade_baseline_sha256`, plus `backup_id`, `restore_target_sha256`, and
`restore_database_state`; its `parameters` are exactly `backup_id`,
`credentials_path`, `database`, `verification`, `backup_helper`, and boolean `replace_unfinished`.
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
cluster/admin access through its maintenance database, credentials, filesystem and installed-release
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
| Canonical and retired | Swap has completed; finish release selection, verification, and successful history before removing retired |

Do not infer backup identity from a schema match. Before creating a temporary database, atomically
publish root-owned mode-`0600` `deployments/restore-target.json` with exactly `schema_version: 1`,
`backup_id`, `dump_sha256`, `source_release_id`, `base_selection_id`,
`observed_previous_release_id`, `original_database_oid`, `safety_backup_id`,
`replacement` (initially null), and `replacement_recovery_backup_ids` (initially empty).
The selection ID is the full validated filename, or null when the restore starts before first
successful selection. `observed_previous_release_id` is null only when `current` was proven absent
at that start. The database OID is a positive integer.
All references are validated; the safety backup is already complete and verified. Use the existing
non-link directory, create-once initial publication, atomic replacement, and directory-fsync rules.
Only the explicitly described safety-backup and target-replacement updates may change this record.
This record binds
the selected input and original database; it is not a phase counter or permission to resume without
confirmation. There can be only one unfinished restore per installation.

On an ordinary rerun, require the requested backup and its digest/source to match that binding.
A different backup requires the explicit replacement procedure below; it must never be substituted
silently, even if its schema matches. Validate the original OID under its
canonical or retired name, depending on the arrangement. Names, ownership, or schema alone cannot
substitute for the original identity. While a temporary database exists, keep the application
stopped and rebuild that managed temporary from the exact bound dump before renaming it: an
interruption during dump loading must not promote an incomplete database merely because its
migration table already looks complete. Validate the full restored database through the existing
restore validation before proceeding. Preserve the original/retired database throughout.

The plan reports each database's role and existence, the bound backup, completed selection, retained
safety backup, and remaining consequences. Fresh typed confirmation acknowledges the current plan.
Apply-time revalidation binds the observed OIDs, ownership, migrations, target-record digest, and
selection/protection facts; changed or unobservable authority refuses. Missing canonical state is
reported as absent, never as an empty live schema. Capacity checks cover the remaining work: a
swap already completed does not require free space for another full restore when resuming the same
target; replacing it does require capacity for the new load and any additional safety backups. The completed safety
backup can be reused while its bound original is retained and the application has remained stopped;
if fresh writes cannot be excluded before the swap, take and validate a fresh safety backup and
atomically update only that binding field before further destructive consequences.

The target binding protects its input backup, safety backup, all replacement recovery backups and
their source releases, any pending replacement input, and base/observed
release references from cleanup and scheduled retention. Ensure the compatible scheduled package
before publishing the binding. After verification and durable successful selection, remove the
retired database, then remove the binding with directory fsync. Successful history must retain the
bound backup and safety-backup references. A crash between these steps is recognized through the
binding and exact successful selection references: finish verification/cleanup without restoring
the dump again or adding duplicate success. Do not classify a same-release restore as already
completed merely because the release ID matches; backup references must match as well.
The success record uses the binding's original base and observed release identities, even if a
retry starts after physical selection has changed. After binding removal, a rerun matching that
latest successful restore and physical/schema state verifies the completed outcome without
reapplying the dump. It must not silently discard application writes made after completed restore.

Restore-specific discovery takes the requested `backup_id` in addition to the normal discover
parameters, and returns `restore_target_sha256` (null only when absent) and `restore_database_state`.
The latter contains exactly `canonical`, `temporary`, and `retired`; each is null when proven absent
or has exactly `oid`, `owner`, and `applied_migrations` from direct observation. Use the existing
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
latter, finish only its verification/cleanup without reapplying a dump; then inspect and confirm a
normal new restore with the resulting state as its baseline. Never erase or reinterpret a success
record as an unfinished attempt. If cleanup cannot complete, report that failure without switching.

For an unfinished attempt, hold the lifecycle lock, revalidate the plan, stop the application and
exclude application writes throughout normalization and reload. Preserve the original database by
its bound OID. If writes to the original since its safety backup cannot be excluded, take a fresh
verified safety backup before proceeding and retain the previous safety backup as a replacement
recovery backup. If the failed restored canonical database may have received writes, also take and
verify a safety backup of that database before discarding it. Such copies have the same partial-schema
caveat as other pre-restore safety backups. Backup or provenance failure refuses before deletion.

Before any database deletion or rename, atomically update and fsync the binding with a `replacement`
object containing exactly the new `backup_id`, `dump_sha256`, `source_release_id`, and
`discard_database_oid` (null when there is no temporary or failed restored database to discard).
In the same update, protect the former input backup and all additional safety backups through the
sorted unique `replacement_recovery_backup_ids`. Preserve the original base/observed selection
identities and original database OID. Enforce the existing record and successful-history
representability limits before consequences; never silently omit a recovery reference.

This durable replacement intent authorizes only the exact confirmed non-original database OID:
drop the temporary database when the original is canonical or retired; after a failed completed
swap, drop the failed restored canonical database, never the retired original. If the original is
retired, rename it back to canonical. A replacement in progress additionally permits a retired-only
arrangement, or original-canonical-only after the discard/rename. Validate OIDs and ownership on
every retry; absence of the recorded discard OID is an already-completed step, not permission to
drop another database. Keep the application stopped; do not start the original merely because its
canonical name has been restored.

Once only the original canonical database remains, atomically replace the binding's input fields
with the pending target and clear `replacement`, retaining all safety/recovery references. Then
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

Protocol v3 cleanup keeps its existing exact parameters (`action`, `targets`, `release_retention`,
`backup_retention`) and target mappings (`kind`, `identifier`, `path`). Its expected state is exactly
`selected_release_id`, `last_successful_selection_id`, `backup_protection_sha256`, and
`restore_target_sha256`. An `inspect` request uses empty expected state and an empty target list;
its response supplies those confirmation facts and exact eligible targets from one locked
observation. An `execute` request echoes them; reference changes require replanning, while unrelated
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

Keep controller orchestration and host mutation as separate owners; add no generic workflow engine.
Use host protocol version 3 for the coordinated transient controller/helper change. Retain envelope,
correlation, redaction, collection, and size rules. Old transient protocol requests refuse; the
controller always transfers its matching helper. Persisted legacy readers are not wire-version
compatibility shims.

Version-3 `discover` parameters are exactly `credentials_path`, `database`, and `mode`, with mode
`strict`, `deploy`, `provision`, or `restore`; expected state remains empty. The provision view admits the
unfinished-installation state described above, with nullable current and successful selection;
unknown observations must never be substituted with null. Mode `restore` additionally requires
`backup_id` and uses the database-arrangement observations above. The deploy, provision, and restore views add
`last_successful_selection_id`, `backup_protections`, `backup_protection_sha256`,
`scheduled_backup_sha256`, `backup_timer_enabled`, and `backup_timer_state` to existing discovery
facts. `selected_release_id` means physical current, not successful history. Timer state is
`active`, `inactive`, or `unknown`; enabled is a strict boolean, and unknown enablement refuses.
Deploy and provision views additionally include `downgrade_baseline_sha256` as defined above.

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
and migration-version validators retain their bounds. Timer activation may drift through a normal
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
replace it with an empty report. Keep the original failure primary if follow-up observation fails.
Attempt one bounded safe reobservation under the lock after a partial mutation where possible.
Report physical current, last successful selection, desired target, actual migration versions,
protected backup IDs, database/service/scheduler state, and failed boundary. Distinguish final
observations from the confirmed starting snapshot; never present an old snapshot as current.

Keep public JSON schema 1 and its existing conservative boolean `changed` meaning: true when a
managed mutation is known or may have occurred, false only when no managed mutation occurred or
could have occurred. Add `facts.mutation_state` with `unchanged`, `changed`, or `unknown`; transport
loss after mutation dispatch yields `unknown`, not a claim that a particular mutation completed.
Unavailable final identity/schema/state fields are null or the existing explicit unknown enum,
never empty collections or previous identities masquerading as fresh observations. Human output
must explain possible changes. This follows the separate CLI UX proposal's retained semantics.

Preserve exit categories: 2 invalid input/missing migration declaration, 3 local build prerequisite,
5 SSH/preflight, 6 backup, 7 migration, 8 staging/selection/service lifecycle, 9 verification,
10 unsafe/incompatible state or missing unattended acknowledgment, and 12 lock contention.
Scheduler dependency refresh failures use 8 once its mutation begins; prior unsafe identity uses 10.
Transport loss keeps its transport status and unknown consequence evidence, not an invented
migration failure. Success requires a complete passing report and durable successful history.
Transient service/readiness polling retains a finite budget; do not expand this work into an
unproven startup-race fix. A timeout must retain the failed checks.

## File boundaries and verification

Expected owners, relative to `ops/taskman_ops/`:

- `cli.py`, `workflows/deploy.py`, `workflows/provision.py`: command-specific flags, target intent,
  ordinary confirmation, independent downgrade acknowledgment for deploy/provision, plans, and results.
- `releases/identifiers.py`, `manifests.py`, `build.py`, `artifacts.py`: exact artifact identity,
  clean and frozen-dirty source export, legacy reads, build-after-hash naming, local and installed
  resolution; small source-order helper within `releases/` if needed, not host code that runs Git.
- `host_protocol/`, `workflows/helper.py`: versioned deploy discovery/request/result integration.
- `host_helper/records.py`, `state.py`, `paths.py`: dual-format records and coherent authority;
  a focused `host_helper/backup_protection.py` owns protection publication/reference lifecycle.
- `host_helper/operations/deploy.py`: explicit reconciliation consequence order.
- `workflows/restore.py`, `host_helper/operations/restore.py`: restore-specific admission from an
  unfinished deployment or database swap, typed data-loss confirmation, restore-target binding,
  and verified history/protection resolution.
- `workflows/cleanup.py`, `host_helper/operations/cleanup.py`: capacity-independent cleanup admission,
  read-only inspection, recovery-aware protected targets, and exact confirmed deletion.
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

Acceptance requires focused controller-to-helper scenarios, not helper-only retry tests:

1. Select target, fail verification, rerun the public controller with the same target, and complete
   only after verification; expose the original failed report and real selection on failure.
2. Replace an unhealthy partial target with another target, without starting/verifying the failed
   one first; history links successful outcomes, not an invented success.
3. Lose local artifacts: reuse sufficient installed provenance, or rebuild safely with a new ID;
   explicit artifacts remain exact and legacy records are unchanged.
4. Same-source non-identical archives get distinct IDs; identical bytes reuse identity within the
   same clean-or-dirty provenance class even when irrelevant worktree content differs. Clean and
   dirty artifacts have distinct IDs through the terminal marker. Reject digest/source/manifest
   disagreements and unsafe paths. Exercise real clean build/cache reuse and dirty snapshots with
   tracked changes, deletions, and non-ignored untracked files while proving ignored files stay
   excluded.
5. Fail after each migration commit prefix, backup publication, protection publication, selection,
   start, verification, successful record, and protection removal. Retry or replace from live schema.
6. Multiple backups with identical source/schema do not cause guessed attribution; protect exact
   referenced IDs. Scheduled retention and cleanup cannot delete retained unresolved or history-held backups
   or required releases, including after target replacement and transport loss.
7. Missing/conflicting provenance, malformed protection, unsafe links, absent history/current,
   incompatible downgrade, and nontransactional/unknown database state refuse without repair.
8. Deploy and provision interactive, unattended, JSON, and dry-run confirmation matrices, including
   dirty local builds, implicit acknowledgment from an explicit dirty artifact, redundant
   `--allow-dirty` for a dirty artifact, refusal for a clean artifact, and apply-time drift after
   `--yes`. Both commands cover known/unknown source ordering, lower SemVer, divergent revisions,
   same-source rebuilds, and baseline drift. Provision also covers older replacement after failed
   startup or committed migrations, protected migration targets without current, legacy installed
   provenance before first success, no-baseline fresh installation, and independent ordinary and
   downgrade prompts/flags. Non-interactive known downgrades or unknown ordering against an existing
   baseline without acknowledgment refuse exit 10, including under `--yes` or `--json`. Cover
   missing commit objects, divergent histories, unorderable versions, mixed known/unknown signals,
   identical-source/version rebuilds, and distinct known-downgrade versus uncertainty prompt text.
   Dry-run reports required acknowledgment without demanding it.
9. Legacy and new artifacts/records mixed through verification, scheduled backup, listings,
   rollback, restore, and cleanup. No in-place legacy rewrite or generic adoption.
10. Isolated `-I -S` execution of both packages; dependency refresh/no-op/failure/interruption and
    an already-started old scheduled process cannot execute unsafe retention after new records.
11. Transport loss and failed reobservation preserve uncertainty and the primary failed boundary;
    never report stale unchanged success or leak raw remote output, credentials, or release cookies.
12. Interrupt provisioning during staging, partial migration, selection, start, verification,
    first successful selection publication, and protection removal. Rerun the public provision
    command with changed clean source, dirty source, and different explicit artifacts; lose the
    original archive. Validate reuse or new immutable builds, missing-current recovery, multiple
    installed candidates, fresh protected backups with a null baseline, migration-policy refusal,
    and successful first-history publication. Existing data must survive; incompatible targets
    refuse. Once the first selection is durable, replacement requires deploy, including after
    transport loss. Exercise pausing scheduled backups and waiting for running backups to finish
    before replacing the backup program during provisioning recovery.
13. Through the public controller, deploy from verified A to B, apply migrations, select B, then
    fail startup/verification. Restore the validated pre-deployment backup and A directly, without
    first completing B. Also cover failure before selection with a partial committed migration
    prefix. Assert typed data-loss confirmation, dry-run, unchanged or differing physical/history
    identities, safety backup, corrupt/incompatible backup refusal, apply-time drift, preserved
    protections on failure, verified restored schema, and durable reference transfer on success.
14. Interrupt restore before/after binding publication, during dump loading, after each database
    rename, after release selection, verification, successful history, retired-database deletion,
    and binding removal. Rerun the public command with the same backup, including when canonical
    is absent; complete after renewed confirmation. Reject an unacknowledged different backup with identical
    migration versions, OID/ownership drift, and unknown arrangements. Verify temporary rebuild
    after partial load, preserved original/safety material, retention protection, accurate dry-run,
    capacity checks for remaining work, and no duplicate restore after durable completion.
    Exercise `--replace-unfinished` before loading, during partial loading, between renames, and
    after failed application verification. Preserve the original OID and safety copies, including
    possible new writes to a failed restored database. Interrupt every replacement binding update,
    discard, and rename; resume the pending target or select a third target without requiring the
    abandoned target to run. Cover retired-only inspection, exact discard-OID checks, backup failure
    before deletion, typed confirmation, dry-run, helper/controller parity, retention references,
    and already-successful restores requiring cleanup followed by a separately confirmed new plan.
15. Through the public cleanup command, exercise low backup capacity, an unavailable canonical
    database during restore, mismatched physical/successful selection, and unfinished first install.
    Eligible unreferenced artifacts can be removed after typed confirmation; all required release
    provenance, backup protections, restore bindings, and their referenced material survive.
    Inspect/dry-run performs no normalization or managed writes. Cover no eligible targets,
    malformed references, target becoming protected after confirmation, interrupted deletion,
    and transport uncertainty without invoking database-health or backup-capacity preflight.
16. Provisioning admission is identical with an absent, historical valid, malformed, or symlinked
    marker at the retired path; it neither follows nor modifies that path. Interrupt before the
    first resource mutation and at supported partial-convergence boundaries, then rerun through
    resource inspection and confirmation. Compatible partial state is reusable without a marker;
    a valid marker cannot admit a foreign database, conflicting service/configuration, unsafe
    path, or unrecorded release. Plans identify reused resources and initial database emptiness
    is verified independently of migration-history emptiness.
    Missing accounts, directories, unit files, or credentials in an otherwise safe partial install
    do not by themselves block admission; they are created and validated before use. Missing
    migration provenance for existing populated data still refuses.
17. Exercise more than 64 failed migration attempts and target replacements under one baseline:
    retain the original, newest, and three most recent eligible intermediates, without a retry-count
    refusal. Include null-baseline first-install recovery, identical timestamps, clock rollback,
    independently history/restore-held backups, and unrelated scheduled/manual backups. Interrupt
    after new dump publication, protection publication, each retired protection removal, manifest
    deletion, and dump deletion. Prove that a transient sixth backup can be inspected and pruned,
    failed replacement creation never prunes, original/newest and migration provenance survive,
    independent references remain intact, and success records reference only retained protections.
    Plans and apply-time checks must bind the exact proposed intermediate deletions.
18. Before any successful selection, interrupt provisioning after a complete migration schema,
    capture a validated backup, and exercise a subsequent failed transition. Through the public
    restore command, restore that backup with and without `current` present, using installed
    migration metadata rather than successful history as provenance. Verify safety-backup creation,
    typed data-loss confirmation, null-baseline binding, first-success publication and protection
    transfer, and retry after interrupted swaps or lost completion output. Reject a partial-schema
    backup incompatible with its source release, corrupt/missing metadata, and missing required
    restore infrastructure. Future release replacement must use deploy.

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

1. Complete operator review of this written specification. Resolve review changes here and obtain
   explicit approval of the full amended document before planning.
2. Write and approve an implementation plan from the complete design, with Beads delivery tasks,
   scoped ownership, tests, and an independent verification task. No implementation has begun.
3. Update the readiness handoff and start a clean implementation session by default. Refresh actual
   repository and host state before relying on the recorded baseline.
4. Implement and verify locally; only then continue the already-authorized staging deployment and
   readiness. Do not manually append selection records or repoint current to bypass the controller.
5. Independently, authorized private administrator creation and login acceptance may proceed after
   fresh checks of the release selected by `current`, the running service's executable, database
   identity, and readiness; reconciliation is not their prerequisite. Keep
   deployment completion and login acceptance as separate outcomes in the readiness handoff.

## Specification verification

On 2026-09-09, self-review checked authority, identity-versus-timestamp semantics, live-schema
admission, backup publication/reference ordering, pausing scheduled backups and waiting for running
backups before replacing the backup program, confirmation, and failure
uncertainty. Local relative-link, placeholder, and trailing-whitespace checks passed. Repository
`mix precommit` passed with 805 tests; application source, assets, configuration, tests, `mix.exs`,
and `mix.lock` were unchanged by that check. Dependency compilation emitted warnings. This verifies
the unchanged application baseline and documentation hygiene, not the unimplemented reconciliation.

On 2026-09-10, operator-guided review added environment-neutral dirty-source builds. Follow-up
self-review checked that exact archive bytes and the terminal dirty marker are the only new identity
dimensions, dirty provenance stays visible without a whole-worktree digest, explicit dirty artifacts
imply acknowledgment, and tracked deletions plus non-ignored untracked files enter a stable private
build snapshot while ignored files remain excluded. This amendment is design only; no implementation
or host action has occurred. Follow-up review extended `--yes` and dirty-checkout building to
provision while preserving its then-proposed new-installation and exact-genesis-retry boundary; downgrade
acknowledgment was then deploy-only, superseded by the 2026-09-11 decision below.

Subsequent operator review superseded the exact-artifact restriction before first success:
provision may reconcile an unfinished first installation to a different desired release. The
amendment includes null-baseline backup protection, migration-policy acknowledgment on provision,
missing-current admission with independent installed migration provenance, and the first durable
selection as the command boundary. Full written-spec approval remains pending.

Evidence sources for this change are repository code and the recorded staging observation in
`tas-sr4b`, `tas-q5lo`, and `tas-6dkg`; no new external research or host action was performed while
writing this specification. The dedicated-host design retains technical mechanism references.

On 2026-09-11, operator review extended independent downgrade acknowledgment and
`--allow-downgrade` to provision. An unfinished candidate may have run or changed the database
without successful history. Both commands compare selected/successful releases and protected
migration targets; missing-current first-install recovery also considers installed migration
provenance. This supersedes the earlier deploy-only restriction. Fresh installation without a
baseline needs no downgrade acknowledgment. Full written-spec approval remains pending.

Further operator review on 2026-09-11 requires the same acknowledgment for unknown ordering
against an existing baseline. This supersedes the earlier unknown-ordering exemption. The plan
distinguishes uncertainty from a detected downgrade; fresh installations with no baseline remain
exempt. Ordinary confirmation and migration safety remain independent.

Operator review replaced the 64-unresolved-attempt refusal with bounded attempt retention:
the original recovery backup, the newest, and three recent intermediates per unfinished sequence.
This supersedes blanket retention of every failed-attempt protection. A new backup must be valid
and durably protected before confirmed intermediate pruning; independently referenced backups are
preserved. A temporary sixth backup and interrupted pruning must remain recoverable. Attempt order
is recorded explicitly rather than inferred from timestamps. Full-spec approval remains pending.

Further operator review permits explicit restore before first successful installation when the
backup and installed source release provide validated compatible provenance. This supersedes
restore's successful-history prerequisite. The restore binding permits an absent original
selection/history, and a verified restore may publish the first successful selection. Partial-schema
backups without a compatible source release remain excluded from automatic restore. Full written
approval remains pending.

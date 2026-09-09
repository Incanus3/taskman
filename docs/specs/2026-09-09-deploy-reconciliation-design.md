# Desired-target deployment reconciliation

Status: proposed specification; design sections approved in conversation, written specification
awaiting operator review. Updated: 2026-09-09. Tracking: `tas-sr4b`.

## Authority and scope

This specification changes existing-host `taskman deploy` from exact-attempt recovery to
reconciliation with the operator's desired release. It is self-contained for planning together with
the [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md), which continues to own
host topology, credentials, archive safety, locking, provisioning, rollback, and restore.
The [runbook](../deployment.md) owns implemented operator instructions; do not document these new
options as available until implemented. The [development guide](../development.md) owns checks.

On approval, this specification supersedes the older design's source-only release identity,
artifact-resolution-before-SSH ordering, exact-attempt-only deployment admission, blanket exclusion
of pending records, and absence of unattended deployment confirmation. The exception to completed-only
records is narrowly limited to backup protection. It does not introduce an operation journal.
Other commands retain their existing consequence and confirmation rules.

The [CLI UX proposal](2026-09-09-operations-cli-ux-design.md) remains separate. This work includes
correct deployment failure evidence and the facts needed for reconciliation, coordinated with
`tas-6dkg`; it does not implement that proposal's general progress UI or provisioning redesign.

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

There is one command, with these additional deploy-only options:

```text
taskman deploy ENV [--artifact ARCHIVE] [--migration-policy POLICY]
                   [--yes] [--allow-downgrade] [--dry-run] [--json]
```

Existing options retain their spelling. No `resume`, `redeploy`, generic `--force`, or manual
adoption mode is introduced. Without an explicit artifact, the clean checkout selects the desired
source/build inputs. An explicit artifact selects its exact validated bytes, independently of
checkout cleanliness. A failed existing release need not become healthy before replacement.

`--yes` acknowledges the ordinary deployment plan. `--allow-downgrade` independently acknowledges
a known downgrade; neither flag substitutes for the other or for a migration-policy declaration.
An interactive known downgrade displays both baselines, the target, and the reasons, and requires
a separate explicit `yes` unless the downgrade flag is present. Ordinary confirmation remains
required unless `--yes` is present. Interactive refusal cancels without mutation using the existing
confirmation-cancelled result. Missing required acknowledgment in non-interactive execution is a
safety refusal, exit 10, before upload or managed mutation; it must not wait for input. Flag use on
other commands is an argument error, exit 2. `--json` is never confirmation.

Dry-run resolves and validates the target and observes the host, but does not prompt, refresh the
installed helper, publish records, change services, or mutate the database. It may build a local
artifact. Missing confirmation flags do not make a dry-run fail; its plan reports required
acknowledgments. Incompatible schema or missing required migration policy still refuses.

The plan names physical current, last successful selection, exact desired release and archive
digest, artifact origin, observed schema, pending migrations, migration policy, downgrade evidence,
existing recovery points, and whether the scheduled helper needs refreshing. Ordinary confirmation
binds these material facts. The helper revalidates them under the lifecycle lock. A changed baseline,
schema, target identity, unresolved protection set, or installed helper identity refuses and requires
a new plan, even with `--yes`. Unrelated newly scheduled backups do not invalidate the plan.

## Target resolution and immutable identity

Separate source/build input identity from exact artifact identity. New IDs have this exact form:

```text
<application-version>-<12-hex-source-sha>-ubuntu26.04-amd64-otp<otp-version>-sha256-<64-hex-archive-sha256>
```

The full digest avoids a new truncated-hash collision policy. Full source revision remains in
metadata. Validate the complete ID and filename/path component bounds before use; reject oversized
identities rather than truncating. The archive retains its existing `taskman` top-level layout and
does not embed this external ID. Package to a temporary filename, hash those final bytes, then name
the final archive, manifest, checksum, and private artifact directory. Renaming must not repackage
the archive. Identical bytes with identical logical inputs have the same ID; a non-identical rebuild
gets a different immutable directory. Do not overwrite an installed release.

Detached `built_at` is descriptive, not identity or freshness authority. Two valid manifests for
the same archive may have different build timestamps; compare the archive digest and all source,
target, toolchain, builder, layout, and migration identity fields, not that timestamp, when reusing
installed content. Keep the originally published installed provenance unchanged. Conflicting
identity fields under the same ID still refuse.

New artifact manifests use schema version 3: the existing exact version-2 field set plus
`artifact_sha256`. Validate agreement between this field, the ID suffix, the detached checksum, and
the actual archive. Version-2 artifacts and historical IDs remain readable, with their existing
strict runtime allowlist and checksum rules. Do not rewrite or rename historical artifacts.

New installed release records use schema version 2: the existing `release_id`, `source_revision`,
`artifact_sha256`, and `migrations`, plus `schema_version: 2` and `artifact_manifest` containing the
full validated detached manifest. All duplicated provenance must agree. Publish it with the
immutable release, never retrofit it into a historical directory. Existing four-field records remain
readable and retain their exact historical serialization.

Without `--artifact`, validate the clean checkout and derive exact source, application version,
target, runtime/toolchain, builder tag/digest, and migration fingerprints. Then perform read-only
host observation and resolve in this order:

1. Matching physical installed release with sufficient validated provenance.
2. Matching last-successful installed release with sufficient provenance.
3. Another exact-input installed release, deterministically ordered by full release ID.
4. Verified exact-input local cache artifact, deterministically ordered by full ID and path.
5. A normal fresh build from the identified clean source.

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
confirming an automatically resolved target; source drift requires resolution again.

`build` remains host-independent. Provisioning shares the new build and reader formats but does not
gain existing-host reconciliation, unattended confirmation, or broader first-install admission.

## Observed state and deployment admission

The helper owns one coherent state model with distinct physical selected release, latest successful
selection identity, live migration versions, and unresolved backup protections. Successful history
must be internally valid, but it need not name the physical current release during deploy planning
or execution. A deploy-specific discovery mode exposes this distinction; do not weaken strict
admission for unrelated mutation commands by globally disabling history checks.

Existing-host reconciliation requires a non-empty valid successful history, an exact managed
physical current release, valid credentials/database authority, safe paths, and compatible schema.
Missing current, unmanaged directories, unsafe links, malformed authoritative records, conflicting
fingerprints, unobservable database state, and unrelated schemas refuse. Initial provisioning keeps
its existing separately constrained genesis procedure.

Fresh confirmation authorizes a new desired transition. It need not prove whether an earlier
mismatch arose from a lost success-record write or a trusted operator selecting another installed
release. This is not manual installation adoption: both the physical release and history must be
valid managed authority. Never fabricate a successful entry for the interrupted target.

The existing `verify`, rollback, and restore command admission remains
strict until reconciliation completes. Administrator preflight does not require successful-selection
history to match physical current; preserve its existing host/runtime/database checks. Fresh actual
release identity and readiness checks can establish the baseline for separately authorized private
administrator creation and login acceptance before reconciliation. Such acceptance does not repair
or prove completed deployment history. Listings continue to expose validated records without
claiming deployment success. The shared backup capability and scheduled backup path must support
the validated physical/live view and new protection records, because they may run during an
unfinished deployment. This does not authorize those paths to select or repair releases.

## Database and downgrade safety

The live sorted migration-version sequence, not the last successful release's assumed schema,
determines remaining work. It must be an exact prefix of the desired target's version sequence.
Every applied version must also have consistent filename/hash provenance in relevant installed
records: the last successful release, physical current, or installed migration targets named by
unresolved backup protections. Different hashes for an applied version, missing provenance, removed
versions, or a non-prefix sequence refuse. A desired artifact alone is not evidence of the bytes
that previously ran. This supports partial committed migration prefixes without adopting unrelated
database state. Nontransactional migration side effects not represented in migration history remain
a manual recovery caveat; do not claim this model makes such migrations replay-safe.

No pending versions means `no-change` is the default. Additional versions on an existing database
require explicit `--migration-policy backward-compatible`. `no-change` with pending versions
refuses. `restore-required` with pending versions refuses with the existing restore-required reason;
deploy never performs an implicit restore. A supplied `backward-compatible` declaration remains
acceptable when a retry finds all versions already applied. A `restore-required` declaration with
no pending versions is unnecessary and refuses as a mismatched policy. Missing required policy is
exit 2; contradictory schema or policy is exit 10. Migrations are not reversed.

Compare the desired target against both physical current and the last successful release. A known
downgrade is a lower SemVer precedence or a strict source ancestor of either baseline. Compare valid
SemVer values by numeric core and standard prerelease precedence, ignoring build metadata. Existing
version strings that are not valid SemVer have unknown version ordering, not lexical ordering.
Source ancestry uses locally available full commit objects and bounded read-only checks; do not
fetch, deepen a clone, or contact a remote to infer order. Missing objects or divergent histories
are unknown. Any established downgrade requires acknowledgment even if another signal disagrees.
Identical source/version with different archive bytes is a rebuild, not inherently a downgrade.
Unknown ordering is visible but does not require `--allow-downgrade`. None of these classifications
proves database compatibility or grants permission to restore data.

## Backup protection and successful history

A general journal is unnecessary: fresh confirmation and validated physical/live facts authorize
reconciliation. However, a completed dump alone cannot identify its protecting role after failure,
and ordinary retention can delete an unreferenced pre-migration backup. Use a narrow durable
protection record, published after backup validation and before any migration invocation.

Derive `deployments/backup-protections/` from the installation root; add no configurable root.
Each create-once `backup-<32-hex>.json` contains exactly:

- `schema_version`: integer `1`;
- `backup_id`: the completed backup's exact ID;
- `base_selection_id`: the full `selection-<64-hex>.json` filename of the last successful selection;
- `target_release_id`: the installed immutable target whose migrations may run;
- `created_at`: canonical whole-second UTC timestamp.

The backup record already supplies physical source release, actual pre-migration versions, size,
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

All unresolved protections tied to the current successful-history baseline survive a different
desired target and additional failed attempts. They protect their backups, backup source releases,
base selection, and migration target releases from cleanup. Before further migration, a replacement
adds its own fresh backup/protection; it never silently drops earlier recovery points. Bound the
unresolved set to 64 and respect the existing 64-KiB message/record limits; refuse further migration
before creating an unrepresentable protection, without evicting recovery evidence.

New successful selections use schema version 2 with exactly the legacy fields plus
`schema_version: 2`, `observed_previous_release_id`, and `recovery_backup_ids` (sorted, unique, at
most 64 IDs). `previous_release_id` names the last successful release, never an unverified physical
candidate. `observed_previous_release_id` names physical current at the confirmed start (null only
for genesis). `recovery_backup_ids` includes all unresolved protections being resolved. `backup_id`
continues to identify the most recent pre-migration backup for this reconciliation, or null if the
set is empty; choose by protection `(created_at, backup_id)`, not by unrelated dump timestamps.
Readers preserve legacy four-field selections and their original filename hashes unchanged.

After verification, append the new selection durably before removing any resolved protection files.
Its references preserve protection if cleanup is interrupted. A remaining protection already covered
by that exact successful history is recognized as resolved and can be removed under the lock; never
remove protection first. A new record may name the same release as its last-successful predecessor
when reconciling a different physical selection or resolving outstanding protections; this describes
a freshly verified outcome, not a synthetic intermediate success. An already verified matching
selection with no unresolved protection does not append duplicate history.

Retained selections protect all recovery backup IDs as well as legacy `backup_id`. Ordinary
retention only becomes applicable when those history references are no longer retained under the
existing rules; successful deployment itself never deletes a recovery dump. Rollback continues to
select the immediately preceding successful release and requires exact live-schema compatibility;
a self-predecessor is not a rollback target. Restore still validates the selected backup and source
release. A dump made between partially committed migrations may not match its physical source
release's complete schema and can therefore require manual recovery; retaining it does not falsely
promise that the existing automated restore command can use it.

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
`strict` or `deploy`; expected state remains empty. The deploy view adds
`last_successful_selection_id`, `backup_protections`, `backup_protection_sha256`,
`scheduled_backup_sha256`, `backup_timer_enabled`, and `backup_timer_state` to existing discovery
facts. `selected_release_id` means physical current, not successful history. Timer state is
`active`, `inactive`, or `unknown`; enabled is a strict boolean, and unknown enablement refuses.

Version-3 existing-host deploy has this exact operation-specific shape:

| Object | Exact keys |
| --- | --- |
| `expected_state` | `selected_release_id`, `last_successful_selection_id`, `applied_migrations`, `backup_protection_sha256`, `scheduled_backup_sha256`, `backup_timer_enabled` |
| `parameters` | `target`, `migration_policy`, `credentials_path`, `database`, `verification`, `backup_helper` |
| Uploaded `target` | `kind: "upload"`, `manifest`, `artifact_sha256`, `artifact_path` |
| Installed `target` | `kind: "installed"`, `release_record` |
| `backup_helper` | `sha256`, `upload_path` |

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
oneshot; enablement must not change after confirmation. Genesis retains its separate operation
parameter contract updated only for coordinated protocol/manifest readers.

Record readers/target validation live in shared standard-library modules included explicitly in
each consuming package. Unknown required observation is a refusal, never a substituted checksum or
empty schema. The controller selects acknowledgment policy; the trusted helper independently
enforces target, database, path, and expected-state safety rather than accepting a force boolean.

The mutation sequence is:

1. Lock, observe, validate managed authority and confirmed facts, and resolve only recognizable
   safe temporary files. Refuse relevant drift before consequences.
2. Ensure the compatible scheduled backup executable using the bounded lock-release/revalidation
   sequence above when necessary, then stage or reuse the exact target.
3. If additional migrations are needed, create and protect a fresh backup, then stop Taskman even
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
authority remains mandatory. No broad provisioning, automatic rollback, implicit adoption, or
background recovery is performed. Host reuse remains subject to the same final runtime identity,
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

- `cli.py`, `workflows/deploy.py`: flags, target intent, downgrade acknowledgment, plan and result.
- `releases/identifiers.py`, `manifests.py`, `build.py`, `artifacts.py`: exact artifact identity,
  legacy reads, build-after-hash naming, local and installed resolution; small source-order helper
  within `releases/` if needed, not host code that runs Git.
- `host_protocol/`, `workflows/helper.py`: versioned deploy discovery/request/result integration.
- `host_helper/records.py`, `state.py`, `paths.py`: dual-format records and coherent authority;
  a focused `host_helper/backup_protection.py` owns protection publication/reference lifecycle.
- `host_helper/operations/deploy.py`: explicit reconciliation consequence order.
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
4. Same-source non-identical archives get distinct IDs; identical bytes reuse identity; reject
   digest/source/manifest disagreements and unsafe paths. Exercise real clean build and cache reuse.
5. Fail after each migration commit prefix, backup publication, protection publication, selection,
   start, verification, successful record, and protection removal. Retry or replace from live schema.
6. Multiple backups with identical source/schema do not cause guessed attribution; protect exact
   referenced IDs. Scheduled retention and cleanup cannot delete unresolved or history-held backups
   or required releases, including after target replacement and transport loss.
7. Missing/conflicting provenance, malformed protection, unsafe links, absent history/current,
   incompatible downgrade, and nontransactional/unknown database state refuse without repair.
8. Interactive, unattended, JSON, and dry-run confirmation matrix, known/unknown source ordering,
   lower SemVer, divergent revisions, same-source rebuilds, and drift after `--yes`.
9. Legacy and new artifacts/records mixed through verification, scheduled backup, listings,
   rollback, restore, and cleanup. No in-place legacy rewrite or generic adoption.
10. Isolated `-I -S` execution of both packages; dependency refresh/no-op/failure/interruption and
    an already-started old scheduled process cannot execute unsafe retention after new records.
11. Transport loss and failed reobservation preserve uncertainty and the primary failed boundary;
    never report stale unchanged success or leak raw remote output, credentials, or release cookies.

Run the development guide's operations suite, compileall, shell syntax, help/confirmation checks,
and `mix precommit`; check Markdown links, whitespace, and planning-term leakage. Independent scoped
review must inspect migration ordering, reference retention, legacy readers, and the actual public
retry path. Build/packaging changes require a clean identified release build plus manifest/hash/cache
and packaged-runtime validation. Local tests do not establish real systemd, backup, or restore safety.

## Rejected alternatives and caveats

- Separate resume/redeploy commands or a generic force flag add no useful operator distinction.
- Requiring original local bytes or a healthy partial candidate strands otherwise safe replacement.
- Overwriting source-named immutable directories destroys exact provenance; reproducible builds
  are not assumed and are not required to solve this deployment problem.
- A general operation journal/replay engine is unnecessary. Backup protection records preserve
  recovery material, not execution history or permission to resume an old process.
- Guessing backup identity from timestamps or choosing one matching dump is not authoritative.
- Version ordering alone cannot prove schema compatibility; downgrade acknowledgment cannot restore
  data or bypass migration fingerprints.

This model cannot repair arbitrary manual corruption or infer unrecorded database side effects.
Historical interrupted migrations without sufficient fingerprint/protection evidence remain manual;
the observed no-schema-change staging failure does not need fabricated backup records. Local dumps
do not survive host loss. Full destructive recovery acceptance remains separately authorized.

## Next-session checklist

1. Obtain operator review of this written specification, including the scheduler compatibility
   consequence and the exact new persisted formats. Resolve review changes here, not in a parallel spec.
2. Write and approve an implementation plan from this complete design, with Beads delivery tasks,
   scoped ownership, tests, and an independent verification task. No implementation has begun.
3. Update the readiness handoff and start a clean implementation session by default. Refresh actual
   repository and host state before relying on the recorded baseline.
4. Implement and verify locally; only then continue the already-authorized staging deployment and
   readiness. Do not manually append selection records or repoint current to bypass the controller.
5. Independently, authorized private administrator creation and login acceptance may proceed after
   fresh actual release/database/readiness checks; reconciliation is not their prerequisite. Keep
   deployment completion and login acceptance as separate outcomes in the readiness handoff.

## Specification verification

On 2026-09-09, self-review checked authority, identity-versus-timestamp semantics, live-schema
admission, backup publication/reference ordering, scheduler quiescence, confirmation, and failure
uncertainty. Local relative-link, placeholder, and trailing-whitespace checks passed. Repository
`mix precommit` passed with 805 tests; application source, assets, configuration, tests, `mix.exs`,
and `mix.lock` were unchanged by that check. Dependency compilation emitted warnings. This verifies
the unchanged application baseline and documentation hygiene, not the unimplemented reconciliation.

Evidence sources for this change are repository code and the recorded staging observation in
`tas-sr4b`, `tas-q5lo`, and `tas-6dkg`; no new external research or host action was performed while
writing this specification. The dedicated-host design retains technical mechanism references.

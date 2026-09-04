# Desired-target deployment reconciliation implementation plan

Status: approved by the operator on 2026-09-14; implementation not started. Created: 2026-09-14.

Scoped independent review and focused amendment review completed on 2026-09-14. The manual-backup
admission finding is resolved; no outstanding review blocker. Evidence is recorded in `tas-sr4b`.

**Goal:** Make public deployment, provisioning, restore, and cleanup recover from the approved
interrupted states while preserving exact artifacts, database provenance, recovery material, and
truthful outcomes.

**Architecture:** Keep explicit controller and host procedures. Shared standard-library readers
own persisted authority; the host validates the full reference graph and sends bounded projections.
Use the lifecycle lock, atomic publication, and narrowly scoped backup/restore records for the
specific recovery guarantees. No generic workflow engine or execution journal.

**Technology:** Python 3.12+ controller, standard-library host zipapps, existing pyinfra/SSH,
PostgreSQL tools, systemd, and pinned Ubuntu release builder.

**Specification:** Read the complete [approved reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md)
before executing any task, together with the [baseline design](../specs/2026-09-09-dedicated-host-deployment-design.md)
and [development guide](../development.md). The specification owns exact schemas and accepted
recovery semantics; this plan assigns their implementation and verification.

## Checkpoint, scope, and execution

Planning inspected the GitButler branch `dedicated-host-deployment-automation`, local tip
`0a0c598808b98d8271617263655785b6d65404f2`, with approved documentation changes still uncommitted.
Reconciliation is unimplemented. Specification approval is recorded in `tas-sr4b`, comment 208.
The parent issue's stale approval-pending description/notes were reconciled during resumption.
The [readiness handoff](../handoffs/ops-vps-readiness.md) owns the current continuation state.

The existing failure is reproduced structurally by `workflows/deploy.py` calling strict discovery
before helper reconciliation. Its helper drops a failed verification report and returns old state
on several failures. `host_helper/state.py` caps directory inventories at 4096 entries; discovery
exports whole histories. `cli.py` resolves local deploy artifacts before SSH. Provisioning admission
and convergence read/write the retired marker. These are implementation targets, not new scope.

Plan approval is recorded; begin in a fresh implementation session using the updated handoff. Use the
repository's delegated implementation workflow with a distinct verifier for consequential changes.
Run task-local test cycles and review between tasks. Do not implement from an isolated task excerpt:
each worker receives the complete specification, this plan, and relevant preceding interface changes.
Beads tracks completion; checkboxes below track implementation steps. Commits require separate
operator authorization; do not turn a task's completion into an automatic commit or publication.

All tasks are one coordinated local change. Intermediate states are not deployable controller
versions. Protocol-v3 cutover may temporarily break consumers assigned to subsequent tasks; record
those exact expected gaps, do not hide failures with skipped tests or wire compatibility fallbacks.
Every task must pass its own focused gate; task 11 requires the complete integrated suite. New
host-format writers must not execute before compatible scheduler convergence. Do not test an
intermediate controller on staging.

## Global constraints

- Ubuntu `26.04`, `amd64`; new releases use OTP `29.0.6`, Elixir `1.20.4`, Node `22.22.1`,
  Hex `2.5.1`, Rebar3 `3.24.0`. Preserve the exact historical OTP `27.3.4.6` / Elixir `1.18.3`
  reader allowlist and current pinned builder tag/digest. No dependency or runtime upgrades.
- Protocol version `3`; public JSON schema `1`. Legacy persisted records remain byte-stable.
  No old transient wire-version support and no selection-history listing operation.
- Maximum serialized manifest: 128 KiB; new installed record: 256 KiB; complete request/result:
  1 MiB. Other records and legacy installed records keep 64 KiB. Fingerprints: 256; observed
  versions: 512; unrelated collections: 64; filename components: 255 UTF-8 bytes. Preserve
  existing string/path/nesting rules while admitting each specified nested schema.
- Only two configured roots. Derive backup protections and restore binding beneath deployments.
  Keep credentials, raw command output, and release cookies out of plans, records, and diagnostics.
- Trusted operators; support interruptions, cooperating overlap, scheduled jobs, and plan drift.
  Keep existing path/checksum/permission safeguards. Use Python for substantial host workflows;
  invoke native tools with bounded argv. Do not take on the parked PostgreSQL refactor.
- Ordinary acknowledgment and downgrade/unknown-order acknowledgment are independent. Restore
  and cleanup retain typed confirmations. No flag bypasses schema, authority, or exact-target checks.
- Never publish success before verification and durable history, except the specification's
  authority-validated restore-completion cleanup. Never synthesize success for an interrupted target.
- No automatic rollback, implicit restore, manual current/history repair, host action, push, merge,
  or broader VPS acceptance is part of this local implementation plan.
- Keep CLI UX progress redesign separate. Update the existing human/JSON plans and results only
  as required by reconciliation. Product surfaces use domain language, not task identifiers.

## Delivery order and ownership

| Task | Bead | Depends on | Reviewable outcome |
| --- | --- | --- | --- |
| 1 | `tas-sr4b.1` | — | Compatible exact record readers and package closure |
| 2 | `tas-sr4b.2` | 1 | Frozen builds and target resolution |
| 3 | `tas-sr4b.3` | 1 | Provenance and recovery reference lifecycle |
| 4 | `tas-sr4b.4` | 1, 3 | Bounded wire protocol, discovery, and result contracts |
| 5 | `tas-sr4b.5` | 3, 4 | Safe scheduled-executable refresh |
| 6 | `tas-sr4b.6` | 2, 4, 5 | Public deploy reconciliation |
| 7 | `tas-sr4b.7` | 6 | Resource-based provisioning recovery |
| 8 | `tas-sr4b.8` | 7 | Bound restore retry, completion, and reapply |
| 9 | `tas-sr4b.9` | 8 | Restore replacement and bounded safety retention |
| 10 | `tas-sr4b.10` | 9 | Cleanup during unfinished recovery |
| 11 | `tas-sr4b.11` | 2–10 | Independent acceptance evidence and documentation |

Execute in listed order to avoid concurrent edits to records, state, packaging, protocol, and
workflow helpers. Dependencies describe required interfaces, not permission to run overlapping
writers. `tas-6dkg` is implemented across tasks 4, 6–10 and closes only after task 11 verifies it.

Paths below are repository-relative. Extend existing test support only where it has real consumers;
keep operation-specific fixtures beside their tests. Each task starts with the listed regression,
observes its expected failure, implements the narrow behavior, and reruns the focused group.

## Task 1: Exact artifacts and compatible persisted readers

Files: modify `ops/taskman_ops/releases/{identifiers,manifests}.py`,
`ops/taskman_ops/host_helper/{records,paths,state}.py`, and
`ops/taskman_ops/helper_client/package.py`. Create
`ops/taskman_ops/host_helper/backup_protection.py` and
`ops/taskman_ops/host_helper/restore_target.py` for their exact record data/validation and publication.
Tests: `ops/tests/test_manifests.py`, create `ops/tests/releases/test_identifiers.py`,
`ops/tests/host_helper/{test_records,test_paths,test_state,test_package}.py`; add
`ops/tests/host_helper/{test_backup_protection,test_restore_target}.py`.

Interfaces: preserve `ArtifactManifest.from_mapping`, `ReleaseRecord.from_mapping`,
`SelectionRecord.from_mapping`, `to_mapping`, and `selection_filename`. Add
`BackupProtection.from_mapping(value)` and `RestoreTarget.from_mapping(value)` with `to_mapping()`.
These types validate exact specification fields and serialize only persisted fields. Discovery
adds binding `sha256` separately. Add keyword-only `artifact_sha256` and `source_dirty` to
`build_release_id`; legacy parsing remains available, while new build callers supply the digest.

- [ ] Add identity/legacy tests, including this new-construction contract:

  ```python
  clean = build_release_id("0.2.0", "a" * 40, artifact_sha256="b" * 64, source_dirty=False)
  dirty = build_release_id("0.2.0", "a" * 40, artifact_sha256="b" * 64, source_dirty=True)
  assert clean == "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp29.0.6-" + "b" * 64
  assert dirty == clean + "-dirty"
  assert validate_release_id(clean) == clean
  ```

- [ ] Run those tests red; retain fixtures of actual legacy serialization and selection filename
  hashes. Add strict booleans, duplicate/reference mismatches, digest disagreement, path-length,
  256/257 fingerprints, and per-format byte-limit cases.
- [ ] Implement manifest v3 and installed/selection v2 exactly. Accept differing `built_at` only
  when all identity fields agree; preserve original installed provenance. Add exact protection and
  restore-binding parsing, OID/creation intent validation, safe atomic create/replace/fsync methods,
  and derived paths. No record writer is enabled in operations yet.
- [ ] Update both explicit zipapp allowlists and source mappings for the complete dependency closure.
  Scheduled readers now consume embedded manifests and their error dependencies; do not include
  controller build/SSH/Pydantic or restore mutation code in the scheduled archive.
  Update `ops/tests/test_architecture.py`, `ops/tests/test_simplification_contract.py`, and
  `ops/tests/support/architecture.py` only where their old record assumptions conflict with the
  approved narrow protection/binding owners; preserve guards against generic journals and engines.
- [ ] Run `uv run --project ops pytest ops/tests/test_manifests.py ops/tests/releases
  ops/tests/host_helper/test_records.py ops/tests/host_helper/test_paths.py
  ops/tests/host_helper/test_state.py ops/tests/host_helper/test_package.py
  ops/tests/host_helper/test_backup_protection.py ops/tests/host_helper/test_restore_target.py`.
  Both generated packages must execute under `-I -S`, not merely import from the checkout.

## Task 2: Frozen builds and desired-target resolution

Files: modify `ops/taskman_ops/releases/{build,artifacts,identifiers,manifests}.py`; create
`ops/taskman_ops/releases/source_order.py`. Tests: `ops/tests/{test_build,test_artifacts}.py`,
`ops/tests/releases/test_source_order.py`. Public CLI integration belongs to task 6.

Interfaces: `build_release(repo, output_dir, *, allow_dirty=False)` still returns `VerifiedArtifact`.
Replace archive-only `ArtifactResolution` with `DeploymentTarget` in `releases/artifacts.py`:
`artifact: VerifiedArtifact | None`, `release_record: ReleaseRecord | None`, `source: str`.
Exactly one target representation is present; properties expose validated release ID, digest,
manifest/provenance. Origins are `explicit`, `installed`, `cached`, `built`. Installed is the
wire target kind only when its validated record is used; never fabricate a local archive path.
`resolve_deploy_target(repo, supplied, *, installed_records, selected_release_id,
last_successful_release_id, allow_dirty=False, artifact_root=None)` returns this target.
The controller supplies already validated pages; resolution does not open SSH itself.

- [ ] Add failing build tests using existing builder doubles: final bytes are hashed before naming,
  changed archive bytes yield changed IDs, identical bytes/provenance reuse identity, and timestamp
  variation alone does not invalidate installed identity.
- [ ] Capture tracked edits/deletions and non-ignored untracked regular files into a private stable
  snapshot. Exclude ignored files, metadata, controller state, and secrets. Reuse exporter path/type
  bounds; refuse links, submodules, unsupported members, and relevant drift during capture. Build
  only that snapshot. Do not persist a snapshot digest.
- [ ] Implement final archive naming without repacking and validate prospective manifest, installed
  record, and both target request representations before publishing a reusable artifact. Bounds
  failures are exit 2, not a late staging error. Keep local prerequisites/build failures at exit 3.
- [ ] Implement selected, successful, sorted installed, sorted cache, build resolution. Compare exact
  source/toolchain/builder/layout/migration inputs, not source-only ID or age. Skip incomplete legacy
  provenance for automatic reuse; explicit matching legacy bytes remain valid. Dirty automatic work
  builds before exact-identity reuse; clean `--allow-dirty` follows clean resolution.
- [ ] Add source-order tests for SemVer prereleases/build metadata, invalid legacy versions, ancestor,
  missing/divergent objects, equal full revisions without lookup, conflicting signals, and no baseline.
  Use bounded local ancestry checks only. Return explicit known-downgrade and unknown reasons;
  either requires independent acknowledgment against any applicable baseline.
- [ ] Run `uv run --project ops pytest ops/tests/test_build.py ops/tests/test_artifacts.py
  ops/tests/releases`. Real clean/dirty builder acceptance is repeated on the final implementation
  in task 11; unit doubles do not establish artifact acceptance.

## Task 3: Provenance and recovery references

Files: modify `ops/taskman_ops/host_helper/{state,records,backups,backup_protection,restore_target}.py`,
`ops/taskman_ops/host_helper/operations/{backup,rollback}.py`, and
`ops/taskman_ops/host_helper/scheduled_backup.py`. Tests: corresponding
`ops/tests/host_helper/test_*.py` and `ops/tests/services/test_backups.py`.

Interfaces: `HostState` retains validated physical selection separately from latest/predecessor
successful selection and their filenames, protections, and optional restore binding. Host-only
reference traversal validates complete history; it is not a controller inventory. Add
`select_backup_source(state: HostState) -> ReleaseRecord` in `backups.py`; all backup callers
use it. Protection publication/pruning and successful reference transfer belong to
`backup_protection.py`; restore binding safety-attempt updates belong to `restore_target.py`.

- [ ] Add a regression for selected A with migration 1, protected B with 1/2/3, live 1/2:

  ```python
  source = select_backup_source(state)
  assert source.release_id == protected_target.release_id
  assert state.selected_release_id == selected_release.release_id
  ```

  Build `state`, `protected_target`, and `selected_release` using existing record fixtures with those
  exact migration versions. Assert produced backup metadata records source B and actual versions 1/2.
- [ ] Implement operation-specific eligible provenance, exact live-prefix validation, all relevant
  fingerprint agreement, and deterministic current-first/ascending-ID fallback. A newly supplied
  target or unrelated installed record cannot prove old migrations. Preserve partial-schema caveats.
- [ ] Replace the lifetime selection-count refusal with incremental per-record history validation
  under the operation timeout. Retain latest/predecessor and complete reference protection without
  silently dropping old history. Validate all authority before projecting it.
- [ ] Implement attempt numbering, original/newest/three eligible intermediates, independent
  references, transient sixth protection, exact confirmed prune sets, and provenance preservation.
  Register fresh protection before retirement; retire reference/fsync before manifest/dump deletion.
  Interrupted pruning must finish under renewed confirmation before another fresh backup.
- [ ] Implement success-reference transfer before resolved-protection removal, null-baseline rules,
  same-release successful transitions, no duplicate complete no-op, and rollback self-predecessor
  refusal. Scheduled/manual backup and ordinary retention share the full protection graph but cannot
  retire attempt protections or mutate the restore binding.
- [ ] Run `uv run --project ops pytest ops/tests/host_helper/test_state.py
  ops/tests/host_helper/test_records.py ops/tests/host_helper/test_backup.py
  ops/tests/host_helper/test_backup_protection.py ops/tests/host_helper/test_restore_target.py
  ops/tests/host_helper/test_rollback.py ops/tests/services/test_backups.py`.
  Include 65+ attempts, equal/reversed clocks, fresh-backup failure, every reference-removal/file-delete
  durable state, null baseline, and independently held copies. These are distinct states, not a
  Cartesian product of every command and timing.

## Task 4: Protocol v3, bounded discovery, and result evidence

Files: modify `ops/taskman_ops/host_protocol/{envelope,identifiers,operations,__init__}.py`,
`ops/taskman_ops/host_helper/{__main__,state}.py`,
`ops/taskman_ops/host_helper/operations/discover.py`,
`ops/taskman_ops/helper_client/runner.py`, and
`ops/taskman_ops/workflows/{helper,releases,backups,rollback,verify,backup,verification_results}.py`.
Create `ops/taskman_ops/host_protocol/mutation_results.py` for shared exact result validation
and `ops/taskman_ops/workflows/inventory.py` for bounded controller page collection.
Update package allowlists. Tests: `ops/tests/host_protocol/`,
`ops/tests/host_helper/{test_discovery,test_entrypoint,test_package}.py`,
`ops/tests/test_helper_runner.py`, `ops/tests/workflows/{test_helper,test_discovery,test_helper_read_only,test_backup,test_verification_results}.py`.

Interfaces: `discovery_request(config, *, mode="strict", backup_id=None)` builds the exact
mode-specific request. Release/backup list operations return specification page mappings;
`collect_inventory(remote, config, operation, *, deadline)` returns the complete ordered validated
record entries or raises, never partial success. `validate_mutation_state(operation, outcome, state)`
returns the exact validated result mapping or raises `ProtocolError`. Mutation callers in tasks
6–10 use this shared validator and keep their consequence ordering local.

- [ ] Add codec regressions before raising aggregate limits: 65 and 256 fingerprints round-trip
  inside installed manifests and upload targets; unrelated arrays of 65 still fail. Cover 257,
  512/513 observations, nested record depth, maximum filenames, and exact UTF-8 byte boundaries.
  Validate exceptions by operation and schema path, never a permissive field-name match.
- [ ] Switch controller, helper, and transport bounds together to protocol 3. Reject old transient
  requests. Ensure runner output limits do not clip valid envelopes before the codec sees them.
  Add exact discovery modes and canonical digests for unresolved protections/downgrade baselines.
  Restore's database/binding observations are completed in task 8; never represent unknown as absent.
- [ ] Implement release/backup pages with canonical incremental inventory hashes, byte/count page
  limits, snapshot-first cursor checking, and strict ascending IDs. Collect complete public listings
  within one deadline. Adapt rollback/backup/verify consumers of old discovery inventories now.
  Verify and rollback retain strict completed-selection admission. Public manual backup must reach
  backup-specific authority validation during an unfinished deployment: remove its strict discovery
  current/history equality gate, while retaining valid managed current/history, observable live
  schema, consistent eligible provenance, credentials, paths, and capacity. Use the deploy discovery
  projection for those facts without granting backup any deployment or history-repair authority.
  Scheduled backup uses the same accepted selected/live-prefix/protection authority. More than 4096
  selections must not overflow discovery.
- [ ] Implement exact common mutation fields, operation additions, status/outcome consistency,
  unavailable markers, report validation, and required completion authority. Preserve reports on
  verification failure and on passing verification followed by history failure. Add this aggregation
  rule to controller integration tests:

  ```python
  assert public_result.changed is True
  assert public_result.facts["mutation_state"] == "changed"
  assert public_result.facts["observations"]["selected_release_id"] is None
  assert "selected_release_id" in public_result.facts["unavailable_fields"]
  ```

  Trigger it with proved provisioning/batch mutation followed by a lost mutating helper reply;
  earlier mutation stays proved but final identity is unavailable. A first lost mutating dispatch
  is `unknown`; a read-only transport failure alone is `unchanged`.
- [ ] Update entrypoint operation failure boundaries so affected valid requests never receive the
  former empty state or optional success-only evidence on exceptions. Take at most one safe bounded
  final observation under the lock after the last possible mutation; keep primary error separate.
  Store confirmed `facts.starting_state` locally, not as final observations.
- [ ] Run `uv run --project ops pytest ops/tests/host_protocol ops/tests/test_helper_runner.py
  ops/tests/host_helper/test_discovery.py ops/tests/host_helper/test_entrypoint.py
  ops/tests/host_helper/test_package.py ops/tests/workflows/test_helper.py
  ops/tests/workflows/test_discovery.py ops/tests/workflows/test_helper_read_only.py
  ops/tests/workflows/test_backup.py ops/tests/workflows/test_verification_results.py`.
  Public manual-backup tests must cover current/history mismatch, a selected release that no longer
  covers the partial live prefix but a protected target does, and conflicting-provenance refusal.
  Assert exact backup source/versions without changing current or history.
  Add real encoded request/result round trips,
  invalid cursors, byte-driven pages, drift, malformed replies, and no partial JSON listing.

## Task 5: Scheduled helper compatibility before writes

Files: create `ops/taskman_ops/host_helper/backup_helper.py`; modify
`ops/taskman_ops/host_helper/{services,lock}.py` only where required for bounded coordination,
`ops/taskman_ops/services/backups.py`, `ops/taskman_ops/workflows/helper.py`, and package allowlists.
Tests: `ops/tests/host_helper/test_backup_helper.py`, `ops/tests/services/test_backups.py`,
`ops/tests/host_helper/test_package.py`.

Interfaces: `backup_helper.py` owns scheduler observation and compatible-executable convergence.
Its input is the exact `backup_helper` mapping plus confirmed checksum/enablement. The caller
supplies a locked authority revalidation callback; convergence returns with the lifecycle lock
held, or fails with accumulated mutation evidence. Use one explicit lock owner so context-manager
exit cannot unlock a different acquisition. No timer enablement or unit/schedule convergence here.

- [ ] Add a deterministic concurrency test where the old scheduled process waits for the lifecycle
  lock. Use events/pipes, not sleeps: timer stops, lock releases, old backup completes, lock reacquires,
  authority revalidates, package replaces, checksum verifies, enabled timer starts.
- [ ] Implement that sequence in Python with a single finite command deadline. Validate root-owned
  upload bytes and fixed destination before installation. Never kill the running backup. Enabled
  but inactive timers recover; disabled/inactive remain so; active/disabled refuses refresh.
- [ ] Fail before new-format publication when quiescence, identity, or enablement fails. Restore an
  enabled timer only with a verified old/new executable and preserve restoration failure separately.
  Track pause, replacement, and restart as known/possible managed changes.
- [ ] Run `uv run --project ops pytest ops/tests/host_helper/test_backup_helper.py
  ops/tests/services/test_backups.py ops/tests/host_helper/test_package.py` for no-op, refresh,
  lock reacquisition drift, timeout, interrupted replacement, and restoration failure. Confirm the
  persistent package reads both formats and honors protections without importing mutation workflows.

## Task 6: Public desired-target deployment

Files: modify `ops/taskman_ops/{cli,output}.py`,
`ops/taskman_ops/workflows/{deploy,helper,operational_preflight}.py`, and
`ops/taskman_ops/host_helper/operations/deploy.py`.
Tests: `ops/tests/{test_cli,test_dry_run,test_end_to_end}.py`,
`ops/tests/workflows/{test_deploy,test_deploy_transaction,test_helper_deploy_transaction}.py`,
`ops/tests/host_helper/test_deploy.py`.

Interfaces: public deploy resolves `DeploymentTarget` after read-only host discovery. The workflow
accepts `yes` and `allow_downgrade` keyword booleans, with `allow_dirty` passed to source resolution.
`run_deployment_request` accepts the target and exact confirmed expected state, scheduler payload,
and prune IDs. Helper `converge_deployment` retains explicit `first_release` specialization.
Wire parameters and expected state are exactly the specification tables; no force/confirmation
booleans are added to host requests.

- [ ] Add a public-controller regression that dispatches real helper code against isolated managed
  state: select B, fail verification, then rerun B and complete. A sibling case replaces unhealthy
  B with C. Mock native commands/transport at their boundaries, not discovery or workflow admission.
  Verify history links A to the verified desired outcome and failed B has no synthetic success.
- [ ] Integrate source resolution, full release pages, baseline digest agreement, material-state
  reobservation, target/policy validation, prominent dirty provenance, exact prune plan, and separate
  ordinary/downgrade acknowledgments. Dry-run needs no prompt/ack flags and performs no managed writes.
  Missing policy is exit 2; incompatible policy/schema or missing unattended acknowledgment is 10.
- [ ] Add parser scope tests for all commands. Explicit dirty artifacts imply dirty permission and
  accept redundant `--allow-dirty`; explicit clean artifacts reject it. JSON never supplies consent.
  Equal source/version rebuilds need no downgrade acknowledgment; unknown against any baseline does.
- [ ] Implement the specification's six-step mutation sequence. Ensure scheduler compatibility before
  staging new records. Stop the app before every migration even when current already equals target.
  Protect fresh backup, prune only confirmed intermediates, run missing versions, observe complete
  schema, select/start/verify, then publish history and transfer references. A healthy complete match
  avoids restart and duplicate history. Preserve changed/unknown evidence through all exceptions.
- [ ] Run `uv run --project ops pytest ops/tests/test_cli.py ops/tests/test_dry_run.py
  ops/tests/test_end_to_end.py
  ops/tests/workflows/test_deploy.py ops/tests/workflows/test_deploy_transaction.py
  ops/tests/workflows/test_helper_deploy_transaction.py ops/tests/host_helper/test_deploy.py`.
  Cover each distinct migration prefix/publication/selection/verification interruption, archive loss,
  no-upload installed reuse, drift after `--yes`, malformed authority, scheduler failure, and lost reply.

## Task 7: Resource-based unfinished installation recovery

Files: modify `ops/taskman_ops/host/{facts,acceptance,baseline}.py`,
`ops/taskman_ops/workflows/provision.py`, `ops/taskman_ops/provisioning.py`,
`ops/taskman_ops/host_helper/database.py`, and genesis specialization in
`ops/taskman_ops/host_helper/operations/deploy.py`. Tests: `ops/tests/host/`,
`ops/tests/workflows/test_provision.py`, `ops/tests/test_pyinfra.py`,
`ops/tests/host_helper/test_deploy.py`.

Interfaces: keep `ProvisionCapabilities` as the existing injection boundary; replace marker-based
classification with observed compatible resource facts. Post-convergence genesis uses the same
target/expected-state schema as deploy, but null history/current is permitted only under the
specified unfinished-installation rules. Preserve the original confirmed pre-convergence state in
public results; fresh infrastructure observations do not authorize changed release/schema/protections.

- [ ] Add admission tests with identical resources and marker absent/valid/malformed/symlinked; all
  must make the same decision and perform no read/write/follow at the retired path. Remove active
  marker fields, probes, and writes; leave historical files untouched.
- [ ] Validate existing accounts, units, directories, Caddy/listeners, database and credentials
  individually. Missing prerequisites may be created before use; names alone cannot admit foreign
  resources. Add direct schema-object/data emptiness checks: absent migration table is not emptiness.
- [ ] Integrate missing-current, multiple installed candidates, null-baseline protections, backup
  source fallback, scheduler refresh, source/dirty/explicit target replacement, and both acknowledgments.
  Preserve database and credential contents through convergence. Partial committed migrations need
  explicit backward-compatible policy; a proven empty initial DB retains automatic restore-required.
- [ ] Make first durable successful history the command boundary. After success/lost response,
  changed releases require deploy; only the existing exact completed-genesis replay remains.
  Aggregate convergence mutation before later failure or unknown genesis output.
- [ ] Run `uv run --project ops pytest ops/tests/host ops/tests/workflows/test_provision.py
  ops/tests/test_pyinfra.py ops/tests/host_helper/test_deploy.py`. Public tests interrupt supported
  convergence and release boundaries; assert original data survives and incompatible authority refuses.

## Task 8: Restore identity, retry, completion cleanup, and reapply

Files: modify `ops/taskman_ops/workflows/{restore,operational_preflight,helper}.py`,
`ops/taskman_ops/host_helper/{database,restore_target}.py`,
`ops/taskman_ops/host_helper/operations/{discover,restore}.py`, and `ops/taskman_ops/cli.py`.
Create `ops/taskman_ops/host_helper/restore_database.py` for native database observation,
empty-template proof, OID registration, and bounded load/rename/drop mechanics; workflow decisions
stay in `operations/restore.py`. Update package allowlists. Tests: existing restore suites plus
`ops/tests/host_helper/test_restore_database.py` and
`ops/tests/workflows/test_restore_recovery.py`.

Interfaces: restore discovery accepts `backup_id`, returns flat validated binding plus computed
digest and exact canonical/temporary/retired mappings. `observe_restore_databases(database,
credentials)` returns that validated mapping; no failed observation becomes null. The operation
uses exact specification parameters including `replace_unfinished`/`reapply` booleans, even before
task 9 enables replacement execution. No generic deployment discovery precedes restore inspection.

- [ ] Add public tests from failed A-to-B deployment and before first success, with/without current.
  Assert backup-source compatibility and typed environment/backup confirmation; failed B need not run.
  Inspect cluster/admin authority via maintenance DB when canonical is absent.
- [ ] Implement five recognized arrangements, OID/ownership authority, canonical-only top-level
  migrations, absent/table-missing/table-empty distinctions, and remaining-work capacity checks.
  Bind exact input/digest/source, original OID, safety copy, original baseline/current before temporary
  creation. Register restored OID durably before loading. Unregistered temporary adoption requires
  pending intent and independent empty-template proof; never adopt populated contents or delete it.
- [ ] Implement same-input temporary rebuild with durable pending intent and exact old-OID absence,
  including retired-only recovery. Keep app/workers stopped through load/validation/swap; preserve
  original and protections. Backup partial live prefixes via the shared provenance selector.
- [ ] Transfer exact input/safety/protection references to verified successful history before retiring
  original DB and binding. Retry after durable success validates identities/references and completes
  cleanup without readiness, reload, or duplicate history. Unknown health is separate evidence.
- [ ] Implement explicit `--reapply`: complete old cleanup, freshly inspect/confirm, fresh safety copy,
  new binding and success even for same input. Ordinary completed retry preserves subsequent writes.
  Unfinished reapply refuses and instructs ordinary retry/replacement; flags mutually exclude with exit 2.
- [ ] Run `uv run --project ops pytest ops/tests/host_helper/test_restore.py
  ops/tests/host_helper/test_restore_target.py ops/tests/host_helper/test_restore_database.py
  ops/tests/workflows/test_restore.py ops/tests/workflows/test_restore_recovery.py
  ops/tests/workflows/test_operational_preflight.py ops/tests/test_cli.py`. Cover every durable
  creation/registration/rebuild/rename/success/cleanup state through the public entry path, wrong OIDs,
  writers/unprovable emptiness, partial load, lost response, and full result round trips.

## Task 9: Restore replacement and bounded safety copies

Files: modify `ops/taskman_ops/host_helper/{restore_target,restore_database}.py`,
`ops/taskman_ops/host_helper/operations/restore.py`,
`ops/taskman_ops/workflows/restore.py`; extend restore tests from task 8 and add
`ops/tests/workflows/test_restore_replacement.py`.

Interfaces: replacement is the exact optional binding object from the specification, not a phase
counter. Binding updates retain base/observed/original identity, register safety attempts, and bind
only the exact non-original discard OID. `prune_backup_ids` is a sorted unique confirmed list.
Readers distinguish abandoned input metadata validation from required dump-content validation.

- [ ] Add public replacement tests in each recognized arrangement, including failed restored
  canonical with possible writes. Validate new input completely; preserve original and take all
  required safety copies before recording replacement intent or deleting databases.
- [ ] Implement durable intent, exact discard, original-name normalization, atomic input switch,
  restored-OID clearing/creation intent, then registered fresh temporary load. A pending replacement
  resumes with its flag; a third target normalizes only the pending arrangement, then receives a new
  plan/confirmation. It never loads or starts an abandoned target just to advance recovery.
- [ ] Permit missing/unreadable/corrupt abandoned dump contents only when metadata/binding/source
  remain authoritative and no independent safety role needs those bytes. Apply this in public
  preflight/discovery and shared readers; ordinary retry still validates its dump fully. Preserve
  remaining abandoned files and report their condition.
- [ ] Implement original/newest/three eligible safety attempts, current original-database safety
  copy and independent references outside slots, transient sixth registration, sequential handling
  of multiple fresh copies, reference retirement before pair deletion, and exact confirmed pruning.
  Failed backup/pruning stops before DB consequences. Do not accumulate abandoned input references.
- [ ] Run `uv run --project ops pytest ops/tests/host_helper/test_restore.py
  ops/tests/host_helper/test_restore_target.py ops/tests/host_helper/test_restore_database.py
  ops/tests/workflows/test_restore_recovery.py ops/tests/workflows/test_restore_replacement.py`.
  Include 65+ replacements, clock rollback, pending normalization with unusable abandoned content,
  registration/retirement/deletion interruptions, dry-run without flag, and successful binding cleanup
  followed by a separately confirmed new restore.

## Task 10: Cleanup during unfinished recovery

Files: modify `ops/taskman_ops/workflows/{cleanup,operational_preflight,inventory}.py`,
`ops/taskman_ops/host_helper/operations/cleanup.py`, and shared reference readers only as needed.
Tests: `ops/tests/workflows/test_cleanup.py`, `ops/tests/host_helper/test_cleanup.py`,
`ops/tests/workflows/test_operational_preflight.py`.

Interfaces: controller calls cleanup inspect directly with empty expected state/targets and cursor.
Inspection returns exact confirmation facts plus byte-bounded target pages; execution echoes those
facts and a maximum-64 target batch with null cursor. Ordering is `(kind, identifier, path)` and
cursor `after_id` is its canonical JSON tuple. Result `completed_targets` covers only fully deleted
or safely absent targets in that request; controller accumulates earlier batch completions.

- [ ] Add public tests with low backup capacity, absent/unavailable canonical DB, mismatched
  current/history, and unfinished genesis. Fail if database-health/capacity preflight is called.
  Read-only inspect/dry-run must not normalize partial backup files.
- [ ] Implement filesystem-only admission and full reference protection. Preserve all releases
  during unfinished first install, and conservatively during other unfinished transitions when
  migration irrelevance cannot be proved. No database names or authority-record removal targets.
- [ ] Collect all byte/count bounded pages before typed confirmation. Hash full ordered eligibility
  and confirmation facts; drift refuses. Execute only confirmed batches with fresh protection/path/
  checksum/inode checks. A newly protected target refuses; safe absence remains idempotent.
- [ ] Preserve manifest-before-dump ordering, unknown files, and partial changes/completions. Lost
  later replies cannot reuse earlier final observations. Cleanup never refreshes scheduler code.
- [ ] Run `uv run --project ops pytest ops/tests/workflows/test_cleanup.py
  ops/tests/host_helper/test_cleanup.py ops/tests/workflows/test_operational_preflight.py`.
  Cover byte-driven pages/batches, no eligible targets, malformed references, partial pair deletion,
  changed protection after confirmation, and loss after a proved earlier batch.

## Task 11: Independent verification and implemented documentation

Files: inspect only this implementation and relevant interactions. Update
`docs/specs/2026-09-09-dedicated-host-deployment-design.md`, `docs/deployment.md`, `docs/README.md`,
the reconciliation specification status/checklist, and affected handoffs after evidence supports
implemented behavior. Keep detailed acceptance evidence in `tas-sr4b.11`; durable operator semantics
belong in the design/runbook. Do not create a second architecture narrative.

- [ ] Have a distinct verifier trace the public entry paths and actual record/DB consequences against
  every acceptance family below. Reproduce focused checks independently; identify untested or
  simulated boundaries explicitly. Fix scoped findings, then reverify changed boundaries.
- [ ] Run the required local gates from the repository root:

  ```sh
  uv sync --locked --project ops
  uv run --project ops python -m compileall -q ops/taskman_ops ops/tests
  uv run --project ops pytest ops/tests
  bash -n ops/taskman ops/caddy/render-caddyfile
  mix precommit
  ```

- [ ] Exercise `./ops/taskman build --help`, `deploy --help`, `provision --help`, `restore --help`,
  and `cleanup --help`; assert parser/interactive/unattended/dry-run cases in tests. Inspect actual
  CLI consumers before editing completion/skill surfaces: the application CLI and bundled API skill
  are separate from this operations CLI and must not acquire unrelated operations commands.
- [ ] Build from a clean identified checkout of the final implementation, then build a controlled
  dirty snapshot using `./ops/taskman build --allow-dirty`. Verify actual archive/checksum/manifest,
  full digest ID, source class, toolchain/builder identity, prospective record/request budgets, clean
  exact-input cache reuse, and dirty exclusion of a synthetic ignored canary. Use private temporary
  storage; no real credentials. Do not connect to a host. If obtaining a clean implementation
  revision requires an unapproved commit, report that specific remaining build gate for authorization.
- [ ] Execute both generated packages with `python3 -I -S`; run the packaged terminal test with
  `TASKMAN_TEST_RELEASE=/absolute/path/to/extracted/taskman uv run --project ops pytest
  ops/tests/test_terminal.py`. Inspect native systemd test diagnostics as well as status. Record
  exact implementation revision/content identity and commands, not an inherited historical pass.
- [ ] Update operator examples and superseded baseline sections for source resolution/identity,
  provision resources, discovery/protocol, confirmations, recovery/pruning, restore retry/replacement/
  reapply, cleanup availability, and failure observations. Keep partial-schema and manual legacy
  restore caveats explicit. Preserve remaining external acceptance gates and dated staging evidence.
- [ ] Check local Markdown links/anchors, whitespace, and planning terminology in changed production,
  tests, command output/help, and user-facing docs. Review canonical references for contradictions.
  Close child tasks and `tas-6dkg` only with evidence; retain readiness parent/handoff for the next
  authorized staging action. Local completion does not establish real VPS acceptance.

## Acceptance coverage map

| Specification acceptance family | Owning task(s) | Required evidence |
| --- | --- | --- |
| Artifact loss, exact bytes, dirty snapshots, legacy formats | 1, 2, 6 | Real build plus resolution and isolated mixed-record tests |
| 64/65/256/257 fingerprints, 512/513 versions, aggregate bytes | 1, 2, 4 | Real nested codec/record/target boundary tests before publication |
| 4096+ history, pages, byte-driven batches, drift | 3, 4, 10 | Complete public listing/discovery/cleanup tests; no partial success |
| Retry/replace failed target, each migration/publication state | 3, 5, 6 | Public controller through real helper procedure, native boundary doubles |
| Ordinary/downgrade/dirty/JSON/dry-run rules | 2, 6, 7 | Command-scoped parser, policy, prompts, and baseline drift tests |
| Missing marker/resources, changed genesis, first-success boundary | 7 | Resource admission and public interrupted-provisioning tests |
| Restore before first success and after failed deployment | 3, 8 | Null-baseline/provenance/safety-backup/public restore tests |
| Restore arrangements, incomplete temporary, OID creation intent | 8 | Public retry after every distinct durable database arrangement |
| Completed cleanup without readiness and explicit reapply | 8 | Later writes preserved on ordinary retry; new safety/history on reapply |
| Replacement, unusable input, previews, third target | 9 | Public and helper parity; original OID and required safety preserved |
| Restore digest, DB/table absence versus failed observation | 4, 8 | Strict exact mapping and canonical/top-level agreement tests |
| 65+ restore safety and migration backup attempts | 3, 9 | Original/newest/recent bounds; independent refs; interruption ordering |
| Partial-prefix backup attribution and exact reference protection | 3, 4, 6–9 | Public manual backup, deploy retry, scheduled backup, and restore safety paths |
| Low-space/unavailable-DB cleanup | 10 | No operational DB/capacity preflight; exact protected deletion |
| Scheduler compatibility and old running process | 1, 3, 5 | Deterministic lock coordination and both isolated package executions |
| Exact mutation results, redaction, failed report, transport loss | 4, 6–10 | Packaged round trips and command-level aggregation across prior mutations |

## Approval and remaining uncertainty

This plan does not alter the approved behavior. The main implementation risks are the coordinated
protocol consumer cutover, full reference validation without lifetime inventory limits, and restore
OID/backup ordering across interruption. The task gates above address those boundaries directly.
Native PostgreSQL/systemd behavior and full destructive restore still need separately authorized
host acceptance; local fakes, packages, and container builds cannot establish that evidence.

The operator approved this plan on 2026-09-14. The handoff records the clean-session boundary;
implementation starts after resumption, with task 1 and the complete approved specification.

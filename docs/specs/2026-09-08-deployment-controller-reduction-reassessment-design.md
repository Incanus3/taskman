# Deployment controller reduction reassessment

**Status:** Approved
**Date:** 2026-09-08

## Summary

The deployment-controller reduction implemented and independently reviewed the
first eight tasks of the approved 2026-09-07 design. The ninth task removed the
remaining lifecycle/runtime implementation in a local uncommitted slice, but
the honest production-Python measurement stopped at 15,948 lines: 24.517%
below the recorded 21,128-line baseline and 2,215 lines above the original 35%
gate.

The remaining scheduled-backup asset also consumes the deleted
lifecycle/activation record formats. Its tests can no longer collect once
those Python record modules are removed. The scheduled capability is a
retained product responsibility, so restoring compatibility or deleting the
timer would contradict the intended architecture.

This reassessment preserves the public commands, scheduled backups, runtime
topology, and accepted safety guarantees. It replaces the scheduled asset with
a persistent immutable executable that runs as a short-lived systemd one-shot
process, migrates it to completed records, and authorizes cohesive
simplification of active Python boundaries. It supersedes the unfinished
Task 9–10 direction and the 35% completion rule in the
[2026-09-07 reduction design](2026-09-07-deployment-controller-reduction-design.md).

The revised hard completion floor is a 28% production-Python reduction from
the same baseline, or at most 15,212 lines. The expected range is 28–31%.
This floor applies only after the complete migration and deletion of all
superseded paths; intermediate slices may temporarily grow while consumers
move to shared replacements.

Delivery feature: `tas-deployment-controller-simplification-f00.11`.

## Current repository state

Tasks 1–8 of the existing reduction plan are committed, independently
reviewed, and closed. The applied stack ends at
`a2eae96efdb98cd5687d86592d943e3552cbc553`.

Task 9 has an intentionally uncommitted working-tree slice that:

- deletes `host_helper/lifecycle.py`, `lifecycle_records.py`, `runtime.py`,
  `facts.py`, `legacy_result.py`, and the orphan `operations/legacy_backup.py`;
- removes the temporary internal result bridge and legacy verification path;
- updates helper packaging and dispatch for final `HostRequest -> HostResult`
  operation handlers;
- removes obsolete lifecycle/runtime tests; and
- adds structural deleted-concept guards.

That slice passes 38 focused architecture, simplification, entrypoint,
packaging, and verification tests. The full Python suite cannot collect
because `tests/services/test_backups.py` still imports the removed lifecycle
records. No Task 9 implementation commit exists.

The current uncommitted metric is:

```text
baseline production Python: 21,128
current production Python:  15,948
removed:                     5,180
reduction:                   24.517%
revised maximum:             15,212
additional net reduction:      736
```

The metric counts tracked `taskman_ops/*.py` files. It deliberately excludes
tests, the scheduled-backup shell asset, generated files, and documentation.
The metric boundary is unchanged from the recorded baseline.

## Audit evidence

Three independent read-only audits examined scheduled backups, controller
foundation code, and active host/procedure code.

The audits agreed on these conclusions:

- scheduled backups are an accepted capability and must be migrated rather
  than removed;
- the 843-line shell asset is the only remaining consumer and writer of the
  old lifecycle/activation/backup formats;
- migrating that asset is necessary for architectural completion but does not
  directly reduce the production-Python metric;
- active Python contains genuine duplication and obsolete defensive policy,
  especially in helper invocation, bounded execution, operation primitives,
  host admission, Caddy evidence, PostgreSQL convergence, verification, and
  completed-state parsing;
- those opportunities credibly support the revised 28% floor and an expected
  28–31% final reduction;
- 35% cannot be treated as a guarantee-preserving target on current evidence;
  forcing it would require explicit product or contract reductions, metric
  displacement, or readability-damaging compression.

## Goals

1. Complete deletion of the lifecycle/runtime/result bridge and all
   operation-resumption concepts.
2. Preserve periodic validated local PostgreSQL backups with bounded
   retention.
3. Make manual and scheduled backups write and consume one completed record
   model under one lifecycle lock.
4. Remove duplicate backup authority, validation, publication, and retention
   policy.
5. Consolidate active internal boundaries where the audit demonstrated
   duplication or obsolete policy.
6. Preserve every public command and its principal responsibility.
7. Preserve verified SSH identity, bounded execution and output, recursive
   redaction, authoritative paths, backup-before-consequence, migration
   compatibility, atomic selection, explicit confirmation, and ambiguity
   refusal.
8. Reduce production Python by at least 28% from the 21,128-line baseline
   after all transitional code is deleted.
9. Demonstrate structural simplification through deleted concepts, single
   owners, tests, and metrics rather than line movement.

## Non-goals

- Removing scheduled backups, their timer, or bounded retention.
- Introducing a persistent Taskman daemon or host agent.
- Installing the complete deployment/restore helper as a long-lived
  executable.
- Changing public command names, confirmation behavior, JSON/human output
  contracts, or exit categories.
- Weakening SSH host-key verification, fresh active-session checks, secret
  handling, authoritative-path containment, backup validation, or atomic
  selection.
- Replacing the deleted transaction runtime with another generic workflow
  framework.
- Moving Python behavior into shell, generated files, or another unmeasured
  location to satisfy the metric.
- Preserving compatibility with the never-deployed lifecycle, activation,
  adoption, pending, or old backup record formats.
- Performing a real deployment, host acceptance run, push, merge, or
  publication without separate authorization.

## Architecture

### Persistent capability, short-lived process

Provisioning installs a root-owned immutable scheduled-backup zipapp at:

```text
/usr/local/lib/taskman/taskman-backup.pyz
```

The zipapp persists across timer invocations. The systemd service remains
`Type=oneshot`: each timer event starts one Python process, that process
creates and prunes a backup under the lifecycle lock, and then exits. Nothing
Taskman-owned remains running between invocations.

The installed package contains only the scheduled entrypoint and the
standard-library modules needed for paths, records, state observation,
locking, bounded PostgreSQL commands, backup creation, and retention. It must
not contain deploy, rollback, restore, SSH, controller, secrets-decryption, or
operator-interaction code.

Provisioning builds the zipapp deterministically from the current source,
installs it atomically as root-owned mode `0750`, and validates the installed
checksum. Updating provisioning replaces the immutable executable; it does
not mutate a running process.

### Scheduled entrypoint

The scheduled entrypoint owns only:

- exact non-secret environment parsing;
- conversion into the shared scheduled-backup input;
- invocation of the shared backup and retention capabilities;
- fixed, non-secret journal messages; and
- systemd-facing process status.

Configuration values do not appear in `ExecStart` arguments. The service
reads its root-owned environment file and uses the fixed private credential
path `/etc/taskman/pgpass`.

The adapter maps outcomes as follows:

| Condition | Process status |
| --- | ---: |
| Backup and retention completed | `0` |
| Lifecycle lock unavailable | `12` |
| Recognizable retryable backup failure | `6` |
| Unsafe, contradictory, or manual state | `10` |
| Invalid installed configuration | `2` |

The public workstation command exit model is unchanged.

### Shared backup capability

One helper-owned backup capability serves manual backup, scheduled backup,
deploy, rollback, and restore. Its interface accepts already-validated
managed paths, database connection facts, the private credential path,
purpose, and an observed selected release/database state. It returns a
completed `BackupRecord`; it does not expose operation IDs, pending records,
or recovery instructions.

Under the exclusive lifecycle lock, it:

1. validates authoritative roots and credentials;
2. observes the selected release and actual `schema_migrations`;
3. refuses absent or contradictory selection before database mutation;
4. normalizes only exact deterministic temporary dumps proven unreferenced;
5. obtains database size and verifies capacity with the retained margin;
6. invokes `pg_dump` with argv-only execution and `PGPASSFILE`;
7. validates the dump using `pg_restore --list`;
8. calculates the SHA-256 digest;
9. publishes the dump without replacing an existing final name;
10. atomically publishes one create-once completed manifest;
11. re-observes the completed pair; and
12. returns the record.

An interrupted run leaves only a deterministic temporary dump or a completed
authoritative pair. The next invocation removes/recreates the temporary or
reuses the completed pair when identity is exact.

Final manifest-less dumps are never deleted before selection references and
completed state are observed. A referenced or contradictory orphan is
`manual`, not cleanup material.

### Completed backup record

`BackupRecord` gains one UTC timestamp needed for deterministic retention:

```json
{
  "backup_id": "backup-<32 lowercase hex>",
  "created_at": "YYYY-MM-DDTHH:MM:SSZ",
  "dump_sha256": "<64 lowercase hex>",
  "source_release_id": "<validated release ID>",
  "migration_versions": [20260905120000],
  "source_database_size_bytes": 123456
}
```

The record has exactly these fields, is bounded by the existing record-size
limit, and is stored as:

```text
<backup_root>/backup-<id>.json
```

The matching private dump is:

```text
<backup_root>/backup-<id>.dump
```

Compatibility with the old deployment-root manifest is intentionally absent.
The timestamp is whole-second UTC. Retention sorts by `(created_at,
backup_id)`, so equal timestamps are deterministic and backward clock
movement cannot affect selection-reference protection.

### Retention

The shared retention capability:

- protects every backup referenced by any retained completed
  `SelectionRecord`;
- additionally retains the configured number of newest unprotected completed
  backups;
- validates canonical filenames, regular-file identity, root ownership,
  private modes, manifest identity, size, and checksum before deletion;
- preserves unknown, malformed, redirected, contradictory, or
  checksum-mismatched entries;
- removes the manifest before its dump so interruption leaves a recognizable
  conservative state; and
- re-observes after mutation.

Protected backups do not consume the configured ordinary-retention count.
This matches cleanup's existing safety policy and avoids deleting recovery
material merely because a deployment has long history.

### Systemd authority

The service keeps its existing hardening, `Persistent=true` timer behavior,
calendar validation, loopback PostgreSQL access, and root identity.

Provisioning creates the canonical lifecycle lock file before enabling the
timer. `ReadWritePaths` grants write access only to `backup_root` and the exact
`install_root/lifecycle.lock`; the service no longer writes
`deployment_root/backups` or `/var/lock/taskman`.

The environment drops derived `deployment_root` and `release_root` values.
Only `install_root`, `backup_root`, database host/port/role/name, and retention
remain. All derived paths come from `ManagedPaths`.

## Active-code simplification

### Canonical internal interfaces

The final code has:

- one finite immutable operation-name vocabulary;
- one helper correlation/operation-result validation boundary;
- one direct `HostResult` path with cleanup warnings merged once;
- no dynamically attached dispatch evidence;
- no unused compatibility aliases, speculative configuration properties,
  duplicate fact types, arbitrary mapping result compatibility, or unused
  error serialization.

Public protocol bounds and exact keys remain.

### Shared host capabilities

Small focused modules own repeated behavior currently duplicated among
backup, deploy, rollback, and restore:

- database mapping and migration observation;
- credential-file validation;
- service stop/start;
- atomic current-link selection;
- verification request construction;
- SHA-256 calculation; and
- directory synchronization.

These are capabilities, not configurable workflows. Each operation remains an
explicit readable procedure and retains its own consequence ordering and
ambiguity decisions.

### Bounded execution

Remote execution converges on one bounded implementation with finite default
stdout/stderr limits. It preserves:

- verified known-host construction and fingerprint matching;
- argv validation and safe quoting;
- sensitive stdin;
- simultaneous stdout/stderr draining;
- numeric exit status;
- bounded output and termination;
- private upload and root-owned installation;
- pre- and post-install checksums; and
- exact-path best-effort cleanup warnings.

Marker-specific shell status parsing and duplicated cleanup flag/state
machinery are removed. Simplifying connection setup deadlines is allowed only
when focused tests prove the pinned connector remains forcibly bounded.

Host-side verification reuses the shared bounded command runner instead of
maintaining a second `Popen`/`select` implementation. Each external call owns
one timeout. Readiness retains its explicit polling budget.

### Decision-relevant host evidence

The helper does not repeat immutable controller checks for OS release,
architecture, PID 1, public DNS, and baseline memory merely as
defense-in-depth. The controller continues to validate them before plan and
confirmation.

The helper retains fresh checks whose state or authority can change before a
consequence:

- invoking administrator and active SSH session identity;
- managed path ownership and containment;
- selected release and database state;
- operation-specific capacity;
- service process/release identity;
- loopback/public listener ownership;
- readiness and HSTS behavior.

Caddy evidence retains foreign public-listener refusal, managed marker
authority, and validate-before-activation. It does not reconstruct exhaustive
package/process/cgroup evidence when ordinary declarative convergence can
repair drift.

PostgreSQL convergence retains one-cluster selection, live endpoint identity,
HBA parser validation, SCRAM requirements, role/database least authority, and
secret stdin. Read-only and mutation scripts share predicates rather than
reimplementing the same cluster/HBA policy.

## Migration accounting

The 28% floor applies only to the completed migration.

An intermediate slice may increase production lines when it introduces a
shared replacement before the last old consumer moves. Every transitional
path must have:

- a complete consumer inventory;
- a named later task that deletes it;
- no new consumers after migration begins;
- no runtime selection between competing old and new behavior;
- an architecture test that fails if it survives its deletion task; and
- an updated forecast showing the expected final deletion.

Intermediate reviews assess correctness, safety, migration direction, and
the truthfulness of the forecast. They do not require a per-slice line
reduction.

Measure at:

1. the current 15,948-line partial-deletion checkpoint;
2. completion of scheduled-backup migration;
3. completion of each active-code simplification group;
4. deletion of every transitional/legacy path; and
5. final verification.

No measured checkpoint authorizes compressing expressions, combining
unrelated modules, deleting tests, or moving behavior outside the metric.

## Error handling

The four helper outcomes remain:

- `succeeded` for a verified requested outcome;
- `refused` when input or observed state is unsafe before consequence;
- `retryable` for a recognizable state that a rerun can converge;
- `manual` for contradictory or externally ambiguous state.

Scheduled execution converts those outcomes into the fixed systemd statuses
without serializing secrets or arbitrary exception text to the journal.

Unknown authoritative artifacts are preserved. Lock contention remains
retryable. Unsafe path authority, contradictory completed records, ambiguous
selection, and unmodeled database arrangements remain manual or refused
before unsafe mutation.

## File ownership

The implementation plan may refine filenames, but responsibility must remain
within these boundaries:

- `host_helper/records.py`: exact completed record schemas and atomic
  create-once publication;
- `host_helper/state.py`: bounded coherent observation and contradictions;
- `host_helper/lock.py`: the one lifecycle lock;
- `host_helper/commands.py`: bounded argv-only host subprocess execution;
- one small helper backup module: shared backup creation and retention;
- `scheduled_backup.py`: installed one-shot environment/status adapter only;
- helper-package construction: deterministic allowlisted zipapp assembly;
- `services/backups.py`: systemd assets, non-secret environment, schedule
  validation, and installation contract;
- explicit operation modules: command-specific ordering and results;
- `remote.py`: workstation SSH/pyinfra transport;
- `host/facts.py` and `host/acceptance.py`: workstation host observation and
  admission;
- `host_helper/verification.py`: final host outcome verification.

Production modules must not contain planning-task identifiers or migration
phase names.

## Testing strategy

### Scheduled backup

Tests cover:

- deterministic package contents and installed checksum;
- root ownership, modes, and secret-free systemd environment;
- lexical and native systemd calendar validation;
- unit validation with `systemd-analyze verify`;
- one-shot success and fixed exit mappings;
- shared lifecycle lock exclusion;
- selected release and actual migration provenance;
- private `PGPASSFILE` and secret-canary absence;
- capacity refusal before dumping;
- `pg_restore --list` before publication;
- no-replace dump and create-once manifest publication;
- interruption after dump, validation, dump publication, manifest
  publication, and retention deletion;
- newest-N retention plus all selection references;
- equal/backward timestamps;
- unknown and contradictory artifact preservation.

The legacy 1,300-line shell-contract suite is rewritten around these public
outcomes. It does not reconstruct activation/adoption graphs, inherited lock
descriptors, rollback fingerprints, old record JSON, or checksum-less
compatibility.

### Active simplification

Before extracting shared capabilities, characterization tests preserve the
current public outcome and safety contract. Operation tests continue to cover
consequence-boundary rerun behavior. Implementation-specific marker,
deadline-propagation, and duplicate-parser assertions are removed only after
equivalent boundary behavior is covered.

Required focused contracts include:

- public command names, confirmation, dry-run, human/JSON results, and exit
  categories;
- recursive secret redaction;
- SSH host identity and fresh-session verification;
- bounded transport and subprocess output;
- authoritative paths and exact completed records;
- backup-before-deploy/rollback/restore consequences;
- atomic selection and verification before completed selection;
- rerun convergence and manual ambiguity boundaries;
- declarative provisioning and retained custom-action allowlists; and
- exact artifact-input reuse.

### Complete verification

Final verification includes:

```text
uv sync --locked
python byte compilation
complete operations pytest suite
architecture and simplification contracts
controller metric and deleted-concept inventory
shell syntax and systemd unit validation
mix precommit
diff whitespace checks
pinned release builder
fresh independent safety and architecture review
```

Real-host acceptance remains unresolved and separately authorized.

## Completion criteria

The reassessed reduction is complete only when:

- the scheduled timer uses the completed-record zipapp and the old shell
  lifecycle implementation is gone;
- old lifecycle, activation, adoption, transaction runtime, internal bridge,
  pending/provisional records, durable operation IDs, stage histories,
  residue arrays, and generated recovery programs have no production
  definition or consumer;
- each host capability has one implementation owner;
- all public commands and scheduled backups retain their principal
  responsibility;
- the accepted security and recovery guarantees remain covered;
- every modeled consequential interruption converges on rerun unless state is
  genuinely ambiguous;
- all transitional paths and adapters are deleted;
- production Python is at most 15,212 lines, measured by the unchanged
  controller metric after final deletion;
- the final report explains the measured result, deleted concepts, and any
  estimate variance;
- complete local gates, the pinned builder, and fresh independent review pass;
  and
- no real-host, push, merge, deployment, or publication claim is made without
  separate authorization.

If the completed migration cannot reach 15,212 lines without crossing an
excluded safety or responsibility boundary, work stops for operator review.

## Rejected alternatives

### Keep the 35% gate

The audits could not identify a credible guarantee-preserving path to 13,733
lines. Achieving it would require explicit removal or narrowing of scheduled
backups, helper/SSH/redaction checks, configuration compatibility, build
authority, or other product contracts. A numerical target is not sufficient
authority for those changes.

### Accept the current partial deletion only

This leaves scheduled backups on the old record model, prevents the complete
suite from collecting, and leaves demonstrated active duplication. It is not
architecturally complete.

### Rewrite scheduled backups in POSIX shell

A shorter shell would still duplicate JSON validation, `HostState`, locking,
selection-reference protection, retention, checksums, and atomic publication.
The current shell parser is the source of much of the obsolete complexity.

### Remove scheduled backups

This changes the accepted runtime topology and removes a backup guarantee.
It also does not recover the production-Python gap because the shell and its
tests are outside that metric.

### Install the full helper

A complete persistent helper package would expose deployment and restore code
to a scheduled service that needs only backup capabilities. The narrow
allowlisted zipapp preserves least authority and package auditability.

### Enforce reduction after every slice

Shared replacements may coexist briefly with old consumers. A per-slice gate
would encourage premature deletion or discourage safe migration. Explicit
transition inventories and final deletion guards provide the required control.

## Implementation handoff

The implementation plan must:

1. adopt and review the existing uncommitted Task 9 deletion slice rather than
   silently discarding or broadly committing it;
2. migrate scheduled backups and restore the full Python suite before
   finalizing legacy deletion;
3. sequence active simplification into independently reviewable capability
   groups;
4. name every temporary adapter and its deletion task;
5. keep implementation and independent verification with distinct agents;
6. update Task 9/10 Beads acceptance criteria to this specification; and
7. retain the separately authorized boundary for real-host, push, merge, and
   publication actions.

The next session should create a revised implementation plan from this
specification before changing production code.

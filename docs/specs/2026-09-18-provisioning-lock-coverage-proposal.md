# Operations admission and provisioning recovery design

Status: complete written design awaiting operator approval; independent correctness review complete.
The ownership model and safe refusal/manual recovery after uncertain provisioning are approved;
this complete design and its implementation plan are not. Production convergence remains outside
the lifecycle lock before genesis. No implementation, native host action or publication is authorized.

## Objective and supported boundary

Prevent cooperating Taskman operations from conflicting on one dedicated host, especially a
PostgreSQL configuration/restart during a backup. Preserve established controller-driven pyinfra
convergence instead of moving it into the helper or adding a remote execution session.

Supported topology is one Taskman installation per Ubuntu 26.04 amd64 host, with singleton systemd
units, account and credential paths. Host-owned operations have whole-operation admission;
controller-driven provisioning has durable admission across separate SSH commands. Brief downtime
and explicit manual recovery from uncertain provisioning are accepted. Unrelated administrator
commands, unattended OS maintenance, malicious interference and multi-installation hosting are
outside this coordination boundary. This design adds no lease, workflow journal, background
recovery, automatic rollback or automatic caller-loss termination guarantee.

The [deployment architecture](2026-09-09-dedicated-host-deployment-design.md) owns topology/security;
[operations contracts](2026-09-18-operations-contracts.md) own release, backup, restore, confirmation,
protocol and failure evidence. Those accepted contracts remain current until implementation;
only the explicit prospective amendments below are proposed. The [runbook](../guides/deployment.md)
will own operating/recovery procedures. Delivery order and approval gates remain in the
[workstream handoff](../handoffs/operations-lock-coverage.md#remaining-order-and-gates).

## Evidence and alternatives

Current `workflows/provision.py` confirms and refreshes authority, then invokes
`converge_provisioning` before locked genesis. `provisioning.py` executes a pyinfra deploy, database/
pgpass convergence and runtime environment installation. Those writes are not serialized with
helper lifecycle operations or scheduled backups. Sequential first/repeat native provisioning
passed; its [identified acceptance evidence](../research/2026-09-17-operations-vps-acceptance.md)
does not establish safe overlap or cover future changed source.

The locked pyinfra dependency is 3.10.0. Inspection of its `connectors/ssh.py` and
`connectors/util.py` establishes separate `client.exec_command` channels, closed command stdin,
and an output timeout that kills controller reader greenlets rather than remote commands. The
SSH connector's disconnect clears its file-transfer connection cache. Holding a separate short-
lived lock command therefore does not protect later writes. See the upstream
[connector API](https://docs.pyinfra.com/en/3.x/api/connectors).

The alternatives are rejected for now on cost grounds:

- Moving consequential convergence into the existing helper would require migrating account,
  directory, file, PostgreSQL, systemd and other procedures and preserving rerun/change semantics.
- A host-owned pyinfra execution session would preserve declarations but add command/file-transfer
  framing, cancellation and supervision to the production execution transport.
- An operator convention or a one-time busy check cannot enforce exclusivity atomically.
- A provisioning-only marker checked at lifecycle acquisition cannot reserve an entire deploy/
  restore operation: those helpers intentionally release lifecycle while waiting for scheduler work.
- A durable reservation for every ordinary backup/deploy would add stale recovery to frequently
  executed host-owned procedures without solving process containment.

Use a native whole-operation descriptor lock plus one durable provisioning reservation. This
retains convergence and matching-helper transport. The coordination capability is small; entry-
point, scheduler and recovery integration remain real implementation work.

## Admission paths, authority and lock order

Use fixed host paths, not environment-configurable paths:

| Path | Purpose and authority |
| --- | --- |
| `/etc/taskman/admission.lock` | Root-owned regular file, mode 0600; exclusive descriptor `flock`; never unlink or replace its inode. |
| `/etc/taskman/provision-admission.json` | Root-owned regular file, mode 0600; one durable provisioning owner/recovery binding. |
| `install_root/lifecycle.lock` | Existing lifecycle descriptor lock; unchanged path and state/mutation responsibilities. |

The fixed admission namespace prevents two mistaken install-root configurations from taking
separate locks while changing the same singleton host resources. Admission initialization may
create `/etc/taskman` as root-owned mode 0700 if absent, then create the admission file. Established
baseline convergence may set its usual root:taskman 0750 directory policy later; group/other writes,
links, unsafe file types or ownership refuse. Do not repair contradictory existing authority.
Only executing manual entry points may initialize this namespace, using exclusive no-link creation
and metadata validation. Read-only calls, previews and dry-runs never initialize it. These bounded
control-plane creations are the only shared write exception before admission.
Private per-invocation helper staging is also permitted; it conveys no mutation authority.

Scheduled services open the existing validated admission file read-only (`O_RDONLY`, no
`O_CREAT`/`O_RDWR`) and then take exclusive flock. This works with Linux flock and the existing
ProtectSystem=strict sandbox without adding writable /etc access. A missing admission file refuses
with 10 before backup writes; manual execution initializes it before installing/activating the new
scheduled package. Validate metadata without replacing the inode.

When the admission namespace is absent, read-only initial-install planning may use its existing
lifecycle/authority snapshot without creating files. Open an existing validated lifecycle inode
without creation; when both installation/lifecycle authority are genuinely absent, keep them absent.
Do not reuse the current creating lifecycle-lock helper unchanged for this bootstrap branch. Every
supported new managed writer initializes the permanent admission inode first; older installed
scheduled code remains protected by an existing lifecycle inode. Concurrent legacy manual clients
are outside this protocol upgrade boundary and must be excluded operationally. Check namespace
absence before and after the
snapshot; if it appears, discard the snapshot and retry through ordinary nonblocking admission.
This bootstrap observation is not execution authority and cannot inspect/recover a nonexistent
reservation as though one existed. Existing unsafe namespace metadata refuses. Mutating begin
initializes admission and revalidates the complete confirmed authority under both locks, so a
bootstrap observation never bypasses execution admission. Admission inspection reports free/no
reservation when both fixed files are absent, without creating them; missing-lock/present-record
state is unsafe and refuses.

Lock order is always admission before lifecycle. A host-owned operation holds admission until
its consequential work and final evidence are complete, including lifecycle unlock/wait intervals.
It never reacquires admission through an independent file descriptor inside that scope. Internal
calls receive the existing admission context; they retain existing lifecycle lock ownership rules.
Linux [flock semantics](https://man7.org/linux/man-pages/man2/flock.2.html) support nonblocking
exclusive acquisition; independently opening the same file can contend with the caller's own lock.

Manual admission is nonblocking. If the descriptor is held, return 12 without consequential writes.
After acquisition, inspect the reservation before proceeding. An unrelated reservation also returns
12; a malformed/unsafe record returns 10. Neither PID disappearance, timestamp age, lock-file
existence nor lock availability is evidence that a reservation may be cleared.

Scheduled backups retain a bounded five-second descriptor wait, then return their existing status
12 if unavailable. On acquisition they check the reservation before any backup/retention writes.
A present reservation denies scheduled backups; they never receive owner credentials. The brief
wait allows normal timer activation near the end of a completing manual operation, without
promising catch-up or automatic retry after contention.

## Command conflict rules

| Entry | Admission requirement |
| --- | --- |
| `provision` execution | Durable reservation from post-confirmation admission through finalization. |
| `deploy`, `rollback`, `restore`, manual backup, cleanup execution | Exclusive descriptor for the entire host procedure and its final evidence. |
| Scheduled backup and retention | Same exclusive descriptor and reservation check; retain inner lifecycle lock. |
| Interactive `create-admin` | Privileged foreground wrapper holds admission throughout its existing TTY service invocation. |
| Coherent host discovery, release/backup listing, restore preflight, verification, cleanup inspection and dry-run host observation | Short nonblocking admission acquisition for the complete coherent host observation; refuse an unrelated reservation. |
| Local build, config/secret validation and command help | No host admission. |
| Admission inspection and provisioning recovery | Dedicated rules below; available while provisioning is reserved. |

Read-only helper snapshots are not presented as coherent during unprotected provisioning writes.
Each host-owned snapshot has one short admission scope; a controller's multi-probe preflight is not
one atomic snapshot. Runtime-file comparisons and supplied-pgpass proofs remain separate observations.
Once a snapshot scope ends it is not execution authority; existing confirmed-plan checks still run after
admission, under lifecycle where required. Public mutation flags and data-loss confirmations remain
unchanged. Admission failure does not bypass an earlier local input error or imply that a validated
plan was accepted for execution.

`workflows/helper.py` currently creates managed upload directories before mutating helper entry.
Move release/scheduler payload staging into invocation-private root-owned directories under
`/run/taskman-ops/<correlation_id>` using the existing transient staging capability. Upload before
admission is allowed only there. Change host validators to accept exact bounded filenames in that
validated invocation directory, preserving checksum, mode, non-link and archive-member checks;
do not accept arbitrary `/tmp` files or retain the managed upload directory as a second live path.
One helper-client invocation context owns this directory, helper and payloads. Create it once,
install/checksum-verify `taskman-host.pyz` (root 0500), then stage only `release.tar.gz` and/or
`backup-helper.pyz` (root 0600). Workflow callers pass that prepared context to dispatch; the runner
must not recreate it or independently remove its helper. Ordinary helper calls use the same context
without payloads. After a validated final reply, or proof entry never dispatched, that context removes
only its three known files and directory; failed cleanup is reported. After unknown dispatch retain
all potentially needed helper/payload files, including zipapp source, until explicit quiescence and
safe scoped cleanup. Helpers must not delete other invocations' directories. `/run` persistence
across reboot is not required: transient inputs can be restaged from a newly validated target and
matching package. No retained upload becomes a cache or durable recovery authority. Transport staging
does not count as managed mutation.

The sensitive supplied-pgpass entry remains exit-only with suppressed stdout/stderr, but its
stdin becomes a finite frame: four-byte unsigned big-endian header length (1..4096), that many UTF-8
JSON header bytes, then supplied pgpass bytes to EOF, total bounded by MAX_INPUT_BYTES. Header keys
are exactly `protocol_version`, `correlation_id`, `admission_owner`, `paths`, `database`, and
`database_state`; use protocol 4, the same owner/path/database validators, and ready/absent database
state. Raw pgpass parsing/authentication is unchanged. Args select only the fixed mode; no owner
ID or secret goes in argv/env. Acquire admission around the whole proof and allow only matching
provision ownership (or null when no reservation exists). Return only 0/2/10/12; 12 maps to contention.
Owner-aware proof after successful begin repeats intended runtime-file and supplied credential
validation before convergence writes. Begin itself revalidates its coherent resource/record/database
projection; earlier composite credential observations are not treated as atomically refreshed.

The administrator wrapper is a finite mode of the matching transient helper, with validated
installation root and the fixed create-admin command. It does not accept arbitrary commands or
read JSON from the credential terminal stream. Preserve real-terminal attachment, protected
EnvironmentFile, unprivileged taskman execution, credential handling and session exit propagation.
An admission denial before session entry is public status 12, not a generic release failure.

## Reservation schema and owner propagation

Canonical record/snapshot digests are SHA-256 over sorted-key, ASCII-escaped UTF-8 JSON with
separators `,` and `:`, no whitespace and no trailing newline, excluding response-added digest fields.
File formatting does not alter that digest. Record publication/digest helpers have one host owner;
the controller checks projections/digest agreement, not an independently reconstructed live policy.

One canonical JSON record, schema 1, at the fixed reservation path contains exactly:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer 1. |
| `owner_id` | `provision-` followed by 32 lowercase hexadecimal characters, generated before begin dispatch. |
| `install_root`, `backup_root` | Validated confirmed roots; no overlapping or unsafe roots. |
| `database` | Exact host, port, role and name mapping using existing database validation. |
| `desired_release_id` | Confirmed validated full release identifier. |
| `backup_helper_sha256` | Exact desired compatible scheduled-package SHA-256. |
| `timer_before` | `absent`, `enabled-active`, `enabled-inactive`, or `disabled-inactive`. Active-but-disabled and unknown state refuse. |

Limit the record to 16 KiB. Strictly reject duplicate/unknown keys, wrong types, unsupported schema,
nonfinite/invalid JSON, unsafe metadata and excess bytes. No secrets, recipient names, passwords,
signed links, arbitrary commands, child PID lists, timestamps or workflow stages are persisted.
Publish through the existing protected atomic JSON publication pattern and sync the file/directory
before reporting begin success. A corrupt/uncertain publication blocks automatic mutation.

The owner ID is distinct from request correlation. Correlation remains ephemeral transport identity;
owner ID persists only for this provisioning reservation. Transmit it through protected helper input,
not shell command text, environment or human logs. A matching ID alone supplies neither completion
proof nor permission for unrelated operations.

The new transient request envelope uses protocol 4 and adds exact top-level `admission_owner`,
which is null or a validated owner ID. Keep other request keys and the result envelope, budgets,
operation/correlation matching and redaction. Results use protocol 4. Old transient requests refuse;
the controller ships its matching helper. Existing persisted release/selection/backup/restore formats,
public JSON schema 1 and historical artifact identities do not change. The parked PostgreSQL
proposal must be reconciled to this protocol baseline if later selected; its refactor is not approved.

Only provisioning authority/discovery/preflight, nested genesis, verification, and admission
finish may use an owner ID, and only when their roots/database/desired target agree with the record
where applicable. Unrelated deploy/restore/backup/cleanup/admin requests cannot bypass reservation
with that ID. Owner-context propagation does not skip existing resource/credential/plan checks.

## Provisioning begin and convergence

Prepare plans, artifacts, protected inputs and the compatible backup package locally as today.
Dry-run never creates a reservation, pauses a timer or refreshes an executable. After ordinary
confirmation and required downgrade/migration acknowledgments:

1. Invoke admission begin with the confirmed roots, database, target, desired backup-package hash,
   scheduler observation and fresh owner ID. Acquire admission nonblocking, then lifecycle
   nonblocking. A running conflicting backup, including an older helper holding lifecycle, returns
   12 before reservation or timer mutation; do not kill it or pause its timer.
2. Revalidate confirmed authority. Capture exact timer policy and atomically publish the reservation.
   An existing reservation always refuses begin, even with the same owner ID. Lost begin replies
   require inspection/recovery, not automatic continuation or begin replay.
3. If the timer exists, synchronously disable and stop it. Release lifecycle before draining any
   previously queued/running backup job; retain admission. Establish no executing or queued backup
   work within the existing finite command budget. Terminal failed-with-no-live-job is quiescent;
   `inactive` alone and absence of a PID alone are insufficient. Never kill a backup to expedite this.
4. Reacquire lifecycle and validate the suspended scheduler and confirmed non-scheduler authority.
   An old scheduled process must be quiescent before any convergence write. Reservation retains
   recovery context if suspension, drain or reacquisition is unsuccessful.
5. Release both descriptors and return the validated begin receipt. The durable reservation now
   excludes new cooperating operations while pyinfra uses its ordinary SSH execution path.

Known suspension is not accidental plan drift: observe and report physical timer disabled/inactive,
and validate its intended resume policy from the exact owner record. Compare every other material
confirmed field as today; do not normalize unknown changes or invent a virtual unchanged snapshot.

Run existing convergence with the owner context. Generic systemd creation may install/enable the
necessary unit resources but must not enable/start the backup timer while reserved: it remains
disabled/inactive, including first install. Existing scheduler refresh retains its stop/unlock/wait/
reacquire/revalidate/checksum contract, but its restart and error-restoration branches defer to
provisioning finalization. Ordinary deploy continues to use its existing enabled=>active policy.
Installed old scheduled helpers cannot honor new admission, so they stay suspended and quiescent
until genesis atomically publishes the exact compatible package. This is a supported scheduler
upgrade, not deletion/conversion of old releases or recovery records. Unsupported schema/authority
still refuses through existing preflight rules before managed payload upload or mutation.

No convergence migration into a new helper procedure is required. Include all baseline package,
account/directory/unattended-upgrade/UFW, Caddy repository/package/config/service, PostgreSQL package/
HBA/restart/role/database/pgpass and runtime/systemd changes in the reservation interval. The
reservation does not serialize unrelated apt/unattended-upgrade activity; package-manager lock
failures retain native truthful failure and the provisioning recovery boundary.

## Known completion and failure

Genesis retains fresh lifecycle-locked admission, migration/backup/reference checks and complete
verification/durable successful history. It acquires host admission with the matching owner context
and keeps the backup timer suspended. An owner ID does not make an unsafe genesis acceptable.

After every dispatched operational command has returned and validated genesis succeeds, invoke
admission finish. Under admission then lifecycle, match the exact reservation, validate the selected
release/successful history against its desired target, database authority, protected inputs and
exact installed scheduled-package checksum. Establish no outstanding backup/service-manager jobs
from this operation, and use the existing verification boundary with the explicit owned scheduler
suspension exception. Do not interpret service-manager job submission as completed work.

Restore timer policy: an absent-before timer becomes enabled/active for a completed first install;
enabled-active and enabled-inactive become enabled/active; disabled-inactive remains disabled/inactive.
Enabled-inactive repair is visible in the confirmed plan. A current controller must checksum-verify
the admission-compatible installed backup package before enabling. Restore synchronously while
holding admission, remove only the matching record after successful restoration and sync its parent,
then release admission. A compatible scheduled start may wait briefly or report status 12; it cannot
write while the reservation remains. Never delete the descriptor lock file.

Any failure after reservation publication keeps that record and backup suspension unless this
explicit finish boundary succeeds. This conservative rule includes known convergence failures,
not only SSH uncertainty, because partial infrastructure state may not be safe for backups. It
adds explicit recovery before ordinary provisioning retry. A failed begin before record publication
reports no reservation; if publication may have happened, inspection/recovery is required.

On SSH loss, lost reply, timeout or cancellation, report unknown dispatch consequences rather than
unchanged. Preserve earlier known mutations, applicable native status and a recovery next action.
Do not expire admission, clear by PID, restart the timer from a finally block or treat record presence
as proof that provisioning is still running. Say “provisioning admission is retained; active or
interrupted work must be inspected.” If finish's reply is lost, inspect current record/timer/authority;
absence of the record is not permission to replay the finished provisioning command blindly.

Operational commands remain foreground and awaited in normal execution; intended Taskman,
PostgreSQL and Caddy target services remain running. Automatic termination of every remote command
after provisioning caller loss is explicitly not required. `systemd-run --wait` creates service-
manager-owned work; killing its waiting client does not establish job termination. Name transient migration and administrator units `taskman-migrate-<correlation_id>.service` and
`taskman-create-admin-<correlation_id>.service` so known managed jobs can be inspected without PID
journals or arbitrary process scanning. Check those prefixes, taskman/backup/Caddy units and the
validated selected PostgreSQL cluster's jobs at finish/recovery. A named unit supplies observability,
not automatic cancellation. Existing helper subprocess timeout cleanup also does not establish
owner-death containment. See upstream
[systemd-run documentation](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd-run.xml).
This design does not promise new containment after abrupt death of other host-owned helpers; their
existing failure/reconciliation contracts remain, and leftover native work needs explicit inspection.

## Inspection and explicit recovery command

Proposed operator interfaces:

```text
./ops/taskman inspect-admission ENV [--json]
./ops/taskman recover-provision ENV OWNER_ID [--keep-backups-suspended] [--dry-run] [--json]
./ops/taskman resume-backups ENV [--dry-run] [--json]
```

Inspection validates fixed control-plane metadata, reports transient admission contention and the
safe reservation projection, and distinguishes a retained reservation from a proved-running command.
It does not take a lifecycle snapshot while a provision owner writes or assert that a host is idle
based on a momentary free lock. It remains usable without decrypting application credentials;
SSH host-key/privilege checks remain required. Owner IDs are bounded recovery identifiers, not secrets.

Recovery uses the matching helper and never automatically kills commands, cancels database sessions,
deletes releases/backups/restore bindings, repairs infrastructure or reruns migrations. Before apply,
the operator must inspect and finish/stop outstanding pyinfra commands and transient systemd jobs,
coordinate OS maintenance and establish quiescence through the runbook. The helper can check known
systemd backup/jobs and ordinary authority but cannot prove absence of every administrator subprocess.
This trusted-operator attestation is the accepted manual boundary, not an automated safety proof.

Validate the requested owner against the current record and configuration, acquire admission then
lifecycle nonblocking, require observed known jobs quiescent, and display a fresh redacted recovery
plan. Require a real-terminal typed confirmation of `ENV OWNER_ID` before apply; no `--yes`, generic
`--force`, automatic stale clearance or noninteractive apply. Dry-run validates and displays without
mutation or confirmation. Changed owner/material recovery facts require a new plan/confirmation.

Default recovery validates a currently completed supported selected release, successful history,
credentials/database authority, installed compatible scheduled package and complete verification
with owned suspension accounted for. It restores the record's timer policy, then removes the exact
record using finish ordering. It need not select the record's desired release: a still-valid previous
completed selection can be the safe endpoint, with actual versus attempted identities reported.

With `--keep-backups-suspended`, recovery can release a partial first installation or failed release
without requiring application readiness. Require the exact valid record/configuration, quiescent
known jobs, protected path/record authority and a disabled/inactive or wholly absent backup timer;
refuse contradictory managed identity. Preserve incomplete releases, migration protections and
restore recovery bindings. Leave backups disabled/inactive, remove only the reservation and direct
the operator to the appropriate existing provision/deploy/restore recovery command using fresh
observed successful-selection authority. Restoring an unready application is not this command's job.
An older scheduled package may remain only while its timer stays disabled and jobs quiescent;
refresh to a compatible package before any later activation.

The keep-suspended result must prominently report that automatic backups remain disabled. After
subsequent verified application recovery, explicit operator scheduler activation is required if
ordinary convergence preserves the now-disabled policy. The narrow `resume-backups` command is that admitted maintenance procedure; unrelated administrator
starts remain outside automated coordination. This is an
explicit recovery choice, never a silent loss of the former backup policy. Record the former policy
in the recovery result before removing its record; the operator retains that result for restoration.

`resume-backups` acquires ordinary admission, refuses any reservation, and validates a completed
supported selected release/successful history, credentials/database authority, full verification
with the stopped-scheduler exception, and exact compatible installed backup package before enabling/
starting the existing timer. It does not install packages, edit timer schedule/units, select releases,
repair failed readiness or infer old policy. Its explicit invocation requests enabled/active policy;
a safely enabled/active timer is a verified no-op. Dry-run displays the plan without mutation;
execution uses ordinary interactive plan confirmation with fresh apply-time revalidation and no
`--yes`/force bypass. This is an ops maintenance entry, not general scheduler configuration.

A missing reservation is an exit-10 recovery refusal, not a successful unlock. Mismatched owner,
unsafe/corrupt record or changed installation configuration is also 10 with inspection guidance.
No command deletes `/etc/taskman/admission.lock` or `lifecycle.lock` to recover.

## Helper/API and result changes

Protocol 4 adds finite operation `admission`. `paths` always contains the exact configured roots;
all action validators reject unknown keys. Database/verification mappings use existing exact
`database_settings`/`verification_settings` schemas, not arbitrary flags. The action contracts are:

| Action | Exact parameters | Exact expected_state | admission_owner |
| --- | --- | --- | --- |
| inspect | `action: inspect` | empty mapping | null |
| begin | `action: begin`, `owner_id`, `desired_release_id`, `backup_helper_sha256`, `authority_settings` | Existing exact 19-field provision_authority state | null |
| finish | `action: finish`, `database`, `verification` | `reservation_sha256` | Matching record owner |
| recover preview | `action: recover`, `owner_id`, strict boolean `keep_backups_suspended`, `database`, `verification`, `dry_run: true` | empty mapping | null |
| recover apply | Same keys, `dry_run: false` | `reservation_sha256`, `recovery_snapshot_sha256` | null |
| resume_backups preview | `action: resume_backups`, `database`, `verification`, `backup_helper_sha256`, `dry_run: true` | empty mapping | null |
| resume_backups apply | Same keys, `dry_run: false` | `recovery_snapshot_sha256` | null |

Begin authority_settings has exactly `database`, `postgres_package_track` and `resource_digests`;
the digest mapping has exactly `taskman_service`, `backup_environment`, `backup_service`, and
`backup_timer`, all validated SHA-256 values, matching existing provision_authority input. Reuse the
host's underlying locked observer, not a nested independently locking helper invocation. Its state
has exactly `authority`, `initial_database_empty`, `selected_release_id`,
`last_successful_selection_id`, `last_successful_selection`, `previous_successful_selection`,
`applied_migrations`, `service_state`, `database_state`, `backup_protections`,
`independently_held_backup_ids`, `backup_protection_sha256`, `scheduled_backup_sha256`,
`backup_timer_enabled`, `backup_timer_state`, `downgrade_baseline_sha256`, `installed_release_count`,
`installed_release_sha256`, and `scheduler_resources`, with existing validators/budgets. Compare the
confirmed state under begin's locks before record/scheduler mutation. No opaque action language,
controller-supplied completion receipt or successful-boolean replaces host verification.

A recovery snapshot is a closed mapping containing exactly `selected_release_id`,
`last_successful_selection_id`, `applied_migrations`, `database_state`, `timer_policy`,
`scheduled_backup_sha256`, `restore_target_sha256`, `backup_protection_sha256`,
`managed_jobs_sha256`, `state_authority`, and `unavailable_fields`. Identity/digest fields use existing
validators or null; migrations are a validated sorted version array or null; database_state is
ready/absent/unknown; timer_policy uses the observation enum below; state_authority is validated/
partial/unsafe/unknown. unavailable_fields is a sorted unique list of unavailable snapshot keys;
failed inspection is never a proved absent identity. managed_jobs_sha256 hashes a freshly observed
mapping with exactly `queued_jobs` and `executions`. queued_jobs is a sorted array of exact
`(unit, job_type, job_state)` objects. executions is a sorted array of exact `(unit, active_state,
sub_state, live_work)` objects for backup and the finite migration/admin unit prefixes specified
above; live_work is absent/present/unknown from bounded native unit/cgroup inspection. Include
active units even when no queued manager job exists. Quiescence requires no queued job, each
execution terminal inactive/failed, and live_work absent; unknown/refused inspection fails closed.
Long-running target services are inspected for authority but excluded from this operational-work
quiescence predicate. Missing transient units require confirmed native absence, not PID absence.
These observations establish only the known managed set, not absence of all OS processes.
Snapshot is limited to 16 KiB and uses existing migration bounds. Keep-suspended may accept partial/unknown database
readiness because it does not start backups or mutate database/application state; unsafe managed
path/record identity still refuses. Default recovery/resume requires complete validated authority.
Compare both exact digest-bound preview facts again under apply locks before any mutation.

Admission responses retain HostResult and use the existing common mutation-evidence fields exactly:
`mutation_state`, `exit_code`, `failed_boundary`, `observations`, `unavailable_fields`,
`inspection_error`, and `report`, plus strict boolean `recovery_required`. This shape also applies to
inspection/preview, with unchanged mutation. failed_boundary is null on success or input/admission/
authority/scheduler/verification/release/inspection on failure. report is null or the existing
validated verification report. Observation keys are exactly:

| Observation | Type/meaning |
| --- | --- |
| admission_state | free/busy/reserved/unknown; informational observation, never execution authority |
| reservation | Valid schema-1 record, or null when absent/unavailable |
| reservation_sha256 | Canonical record digest or null when absent/unavailable |
| backup_policy | enabled-active/enabled-inactive/disabled-inactive/absent/unknown; physical observed policy |
| former_timer_policy | Record's saved timer_before enum, or null if none/unavailable; retain in reply after clearance |
| actual_selected_release_id | Fresh actual selected release or null, with unavailability explicit |
| attempted_release_id | Record's desired_release_id, retained in reply after clearance, or null |
| recovery_snapshot | Exact snapshot above for recovery/resume preview/apply, otherwise null |
| recovery_snapshot_sha256 | Canonical snapshot digest or null |

Top-level unavailable_fields lists unavailable observation keys; defined not-applicable nulls are
not unavailable. Unknown record presence is distinct from proved absence. recovery_required is true
when a reservation remains or its possible publication/removal is uncertain; transient busy alone
does not invent a retained provision owner. Failed follow-up inspection never replaces the primary
failure. Reuse exact internal evidence/encoding fallback with this new observation schema.

Outcome/status agreement: succeeded=>0; ordinary contention=>retryable/12; safety/invalid authority=>
refused/10; input error=>refused/2; SSH errors retain 5 at the transport boundary; scheduler write/
restore failures=>retryable/8; verification failure=>retryable/9. Unknown mutation is never unchanged.
Admission failures of existing mutation operations retain their exact operation-specific result
shape, using existing failed boundary `lock`, exit 12 and unavailable final fields where necessary.
Extend internal failure/encoding recovery to the new admission shape without weakening validation.

Public JSON stays schema 1. Expose coarse admission/recovery facts and actual backup suspension;
redact raw commands/exceptions and preserve aggregate mutation evidence. Reservation publication,
backup policy changes and uncertain managed dispatch count as known/possible mutation; successful
reservation removal does not erase earlier evidence. A verified overall unchanged provisioning run
may still report changed because it temporarily changed scheduler policy/admission state; tests
must distinguish unchanged resource convergence from whole-command coordination mutations.

These are ops-controller maintenance commands, not application-domain capabilities. No application
browser/API or application Bash/Fish completion/bundled skill expansion is required. Add ops command
help, parser validation, runbook examples and focused CLI/JSON tests; keep production names independent
of planning identifiers. Current application CLI surfaces remain unchanged.

## File ownership and implementation boundary

- New focused `host_helper/admission.py` owns fixed control-plane authority, descriptor admission,
  strict reservation I/O and context; it is not a generic workflow engine.
- New focused `host_helper/operations/admission.py` owns begin/finish/recovery policies and scheduler
  ordering; reuse existing state/verification/record and backup-helper capabilities.
- `host_protocol/envelope.py`, `operations.py`, `mutation_results.py` and helper dispatch own protocol
  4 owner fields, finite action/result validation and encoding/failure evidence.
- `helper_client/runner.py`, `workflows/helper.py` and focused private-staging support own owner
  propagation and validated invocation-private release/scheduler payloads.
- `workflows/provision.py` owns reserve/converge/genesis/finish ordering and aggregate evidence.
  Existing `provisioning.py`, baseline/firewall/Caddy/PostgreSQL convergence remains the live path.
- Host mutating/read-only entry points, scheduled backup and administrator launcher own their finite
  admission scopes. `backup_helper.py` and `services/systemd.py` defer timer policy while reserved.
- New focused controller recovery workflow and `cli.py` own inspection/recovery interaction, typed
  confirmation and public results, including narrow resume-backups. Host admission does not duplicate CLI consent.
- Focused admission/workflow/protocol/scheduler/TTY tests cover the production consumers. Update
  runbook, current contract/architecture references and documentation indexes with implementation;
  do not promote this proposed protocol/behavior to implemented status now.

The parked PostgreSQL Python and CLI UX proposals are not implementation dependencies. Reconcile
protocol/admission overlap in their canonical designs before their later approval/execution; do not
absorb their refactors or presentation changes into this increment.

## Verification and native acceptance

Test distinct supported states and consequential boundaries rather than every equivalent timing:

1. Conflicting manual procedures fail 12 before managed mutation, including during lifecycle unlock/
   scheduler waits, different workstation/configured-root invocations and interactive admin entry.
2. Reservation begin versus helper/scheduled admission is atomic; owner mismatch and unrelated-owner
   use refuse; internal nested calls do not self-contend.
3. Active older backup returns busy before begin mutation; queued older backup drains without
   deadlock; compatible scheduled refusal/terminal failed state is recognized as quiescent.
4. Old installed helper upgrade stays suspended until compatible checksum-proved publication;
   no generic/new-timer, unchanged-helper or error-restoration path prematurely activates backups.
   Scheduled admission opens existing authority read-only under ProtectSystem=strict.
5. Begin reply loss, failure during timer suspension, convergence failure, SSH timeout/disconnect,
   cancellation, malformed reply and finish reply loss retain truthful mutation/recovery evidence.
   Exercise active remote work and service-manager jobs, not only normal context-manager exit.
   An active operational unit with no queued job still refuses recovery; unknown live work refuses.
6. Persistent reservation and disabled timer survive reboot; no automatic expiry/PID-clearance.
7. Known completion restores enabled policy or preserves disabled policy; restoration/removal
   failures preserve recovery context and actual state.
8. Default recovery accepts a valid safe completed endpoint; partial first install and failed
   readiness require explicit keep-suspended; record absence/mismatch/unsafe state refuse.
9. Recovery/resume dry-run and fresh-host bootstrap never create control-plane/lifecycle files;
   namespace appearance invalidates a bootstrap observation. Typed confirmation/drift/TTY rules hold; no backup/release/
   protection/restore material is deleted. Resource-unchanged reruns remain distinct from timer/
   admission changes. Exact legacy mutation-result and encoding behavior stays covered.
10. Admitted resume-backups verifies readiness/compatible package, refuses retained reservation and
    binds preview/apply drift; enabled no-op does not rewrite resources.
11. Invocation-private staging preserves checksum/permission/isolation/unknown-dispatch retention;
    create-admin preserves real-terminal behavior and admission status propagation.

Run the [operations verification gates](../guides/development.md#operations-verification), relevant
ops command help, Markdown link/whitespace and product-surface planning-language checks, plus
`mix precommit`. Verify both actual isolated helper packages and pinned-runtime terminal behavior
where packaging/admin paths change; source/controller tests alone do not cover deployed consumers.

Native acceptance requires separate operator authority. Exercise real SSH, systemd PostgreSQL/
backup overlap refusal, old scheduled-process quiescence/upgrade, first/repeat provisioning,
interrupted reservation recovery, disabled policy, reboot suspension and compatible timer restoration.
Use disposable-host/recovery preservation requirements from the runbook/inventory. Container/mock
results do not establish native guarantees. Refresh final artifact/source/CI bindings for the actual
delta; existing acceptance remains qualified to its old source/database identities.

## Approval and continuation

Approve this complete written design only after independent scoped review resolves material
correctness gaps. Then create/review a Beads-backed implementation plan. Implementation remains a
dedicated post-merge workstream on a new branch, with plan approval and the clean-session boundary.
Lock coverage does not block readiness/consolidation/merge of the current operations branch; its
absence remains a known implementation limit. Native acceptance, final publication, exact-head CI,
workstream completion and merge of the new branch retain their separate gates. Task state and
execution sequence belong to the handoff/tracker, not a delivery ledger in this specification.

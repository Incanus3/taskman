# Operations admission and provisioning recovery design

Status: Not Planned. On 2026-11-06 the operator parked this work because the implementation and
operational burden exceed the currently demonstrated need. This document preserves the proposal
for reconsideration; it is not an active delivery commitment or an implemented operating contract.

No admission lock, provisioning reservation, inspection/recovery command or implementation plan
was produced. The detailed design was revalidated against source `8754d594` on 2026-10-31 and passed
scoped independent correctness/planning review. That review is historical evidence about the
proposal, not approval of implementation or native acceptance. The former review companion and
active handoff are retired; their useful design and continuation context is consolidated here.

## Why this work is not planned

A host-wide guard could prevent cooperating commands from overlapping provisioning, but it reaches
well beyond a small lock-coverage fix: it changes helper protocols, nested ownership, private
staging, scheduler lifecycle, first-install observation and manual recovery. The actual need for
that breadth has not been established sufficiently to justify the cost now.

The conservative recovery policy is itself an operational downside. Known failures and
proxy-pending outcomes can leave provisioning reserved and automatic backups suspended until
explicit inspection and recovery. That burden, and the risk of prolonged backup suspension, are
reasons to defer the capability rather than incidental implementation details.

The accepted present-day limit remains: lifecycle locks protect individual supported scopes,
not the entire controller-driven provision command. Operators must coordinate exclusive
maintenance and inspect actual timer/jobs after uncertain interruption, as described in the
[runbook](../guides/deployment.md). Deferral does not claim this gap is fixed.

Repository-relative Task 32, resolved through the owning repository's registered Project and
validated by `Source: taskman/br/tas-bbry`, records the Not Planned disposition. Earlier transfer
from `tas-561x.9` is historical provenance. CLI UX remains a separate workstream.

## Decisions and inherited constraints

| ID | Status | Choice | Concrete cost or constraint | Rationale |
| --- | --- | --- | --- | --- |
| D3 | Proposed | Extend the matching transient protocol to v4 and propagate the owner through the finite live provisioning call graph | Update PostgreSQL, Caddy and firewall constructors, validators and failure projections together | [Owner propagation](#reservation-schema-and-owner-propagation), [Result changes](#helperapi-and-result-changes) |
| D4 | Proposed | Stage release/scheduler payloads in the existing invocation-private `/run` namespace | Refactor helper lifetime ownership and consumer paths; retained inputs do not survive reboot | [Private staging](#invocation-private-staging) |
| D5 | Proposed | Persistently suspend and drain backups until verified finish or explicit recovery | Backup availability can remain reduced after a known convergence failure; scheduler upgrade needs native verification | [Begin and convergence](#provisioning-begin-and-convergence) |
| D6 | Proposed | Admit each coherent host observation; preserve noncreating fresh-host observation | More observation entry points need admission scopes; multiple probes still are not atomic | [Lock order and bootstrap](#admission-paths-authority-and-lock-order) |
| D7 | Proposed | Add inspection, typed provisioning recovery and narrowly admitted backup resumption | Operators must establish quiescence and retain recovery results; no automatic cleanup or restart | [Recovery commands](#inspection-and-explicit-recovery-command) |
| D8 | Proposed | Bind nested PostgreSQL requests to a small frozen selected-target projection in the reservation | One additional bounded record field; bootstrap retains null pre-creation cluster identity | [Selected-target binding](#postgresql-integration) |
| D1 | Accepted | One fixed host-wide admission flock plus a provisioning-only durable owner reservation; admission precedes lifecycle | Permanent control-plane authority and changes at every cooperating host entry point | [Admission paths](#admission-paths-authority-and-lock-order), [Alternatives](#evidence-and-alternatives) |
| D2 | Accepted | Retain uncertain provisioning and backup suspension for manual recovery; owner ID is not completion proof | No expiry or automatic caller-loss termination; operator inspection can prolong downtime | [Failure boundary](#known-completion-and-failure) |
| D9 | Accepted | Park this capability as Not Planned; retain this proposal without an active handoff or review companion | Whole-provision coordination remains an operator responsibility; reconsideration requires a fresh need assessment | [Deferral rationale](#why-this-work-is-not-planned) |

D1 and D2 retain the previously accepted direction. D9 suspends its delivery: D3–D8 are unapproved
design options, and no earlier direction authorizes implementation if work is reconsidered. The
requirements below describe the retained proposal. Inherited accepted constraints:

- [Deployment architecture](2026-09-09-dedicated-host-deployment-design.md): one dedicated host,
  controller-driven pyinfra, fixed service topology and existing lifecycle authority.
- [PostgreSQL ownership and native Python configuration](2026-10-07-shared-postgresql-design.md):
  existing clusters are resource-only by default; selected-cluster override, independent recovery
  capture and bounded configuration/cancellation semantics are already implemented.
- [Upload staging](2026-10-30-upload-staging-design.md#simplified-upload-checks): current inputs use
  `install_root/uploads`, ordinary metadata/containment checks and existing transfer machinery.
  D4 proposes replacing that active payload path; it does not reinstate superseded retained-ancestor
  traversal/copy machinery or authorize cleanup of old retained inputs.
- [Operations contracts](2026-09-18-operations-contracts.md): exact authority, failure evidence,
  artifact/backup/restore formats, confirmation and scoped destructive behavior remain binding.

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
would own implemented operating/recovery procedures if this capability is selected. Conditional
delivery requirements are retained in [Approval and continuation](#approval-and-continuation).

## Evidence and alternatives

Source inspection at `8754d594` confirms that [`workflows/provision.py`](../../ops/taskman_ops/workflows/provision.py)
confirms and refreshes authority, then invokes
`converge_provisioning` before locked genesis. `provisioning.py` executes a pyinfra deploy, database/
pgpass convergence and runtime environment installation. PostgreSQL native inspect/configure now
run through the transient helper with their own lifecycle scopes; recovery capture is a separate
helper call. Package/HBA staging, resource convergence and other pyinfra writes are still outside
one whole-provision scope, and capture/configure are separate intervals. Those inner locks do not
serialize the complete provisioning workflow against scheduled backups. Sequential first/repeat
native provisioning passed in privately recorded acceptance; this does not establish safe overlap or cover future
changed source. The [native acceptance guide](../guides/native-acceptance.md) defines portable criteria.

The lockfile still pins pyinfra 3.10.0. Earlier inspection of its `connectors/ssh.py` and
`connectors/util.py` establishes separate `client.exec_command` channels, closed command stdin,
and an output timeout that kills controller reader greenlets rather than remote commands. The
SSH connector's disconnect clears its file-transfer connection cache. Holding a separate short-
lived lock command therefore does not protect later writes. See the upstream
[connector API](https://docs.pyinfra.com/en/3.x/api/connectors).

The alternatives are rejected for now on cost grounds:

- Moving all consequential convergence into the helper would still require migrating package,
  account/directory, resource, systemd and other procedures and preserving pyinfra rerun/change
  semantics. The already implemented native PostgreSQL configuration subset does not eliminate
  that remaining migration.
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

### Provisioning scenarios on the refreshed baseline

| Situation | Proposed observable outcome |
| --- | --- |
| Existing compatible shared PostgreSQL, no override | Reserve provisioning, converge only owned resources, preserve global PostgreSQL configuration, then verified finish restores backup policy |
| Explicit existing-cluster override | Bind the selected target, retain configuration copies before needed native changes, and carry that independent mutation evidence through finish or failure |
| Competing manual or scheduled operation during convergence | Refuse with status 12 even between nested helper calls, because the durable reservation remains |
| Caddy needs operator activation | Preserve proxy-pending status 13 and preparation receipts; report suspended backups and required recovery before another provision attempt |
| Configure reply is lost after recovery capture | Preserve capture evidence and unknown configuration consequences; keep reservation/backups suspended for inspection instead of automatically retrying |

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

The current `_validate_managed_resources` observer expects an established `/etc/taskman` to be
root:taskman 0750 and resolves the taskman group. Extract its locked observation with one explicit
bootstrap exception: root:root 0700 is valid control-plane-only authority when its entries are
limited to the validated permanent `admission.lock` and, where the operation permits it, the exact
valid matching `provision-admission.json`. It need not resolve a not-yet-created taskman account or
group. Before begin publication no reservation is allowed; the post-suspension recheck and partial
bootstrap inspection/recovery may recognize that owner's valid record. This also permits retry or
read-only observation after an earlier initialization stopped before publication. No credentials,
runtime/scheduler inputs, unrelated entries, unsafe metadata or contradictory established managed
resource identity qualify for the exception. Other present resources retain their exact checks;
normal baseline convergence may establish root:taskman 0750 after account creation.

Apply this rule to both noncreating planning observations and begin's fresh locked revalidation.
The known directory/lock initialization is the stated control-plane exception, not confirmation
drift or proof of an established installation. Do not reuse the current observer unchanged, rewrite
existing contradictory metadata, or ignore arbitrary differences to make the confirmed plan match.

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

### Invocation-private staging

Current [`workflows/helper.py`](../../ops/taskman_ops/workflows/helper.py) uses
[`uploads.py`](../../ops/taskman_ops/uploads.py) to prepare `install_root/uploads` before mutating
helper entry. This is the accepted shared upload path, separate from durable deployment records;
its preparation/upload evidence is currently aggregated with helper results. The helper runner
separately stages its executable beneath `/run/taskman-ops/<correlation_id>`. Its current failure
cleanup can remove the helper even after unknown dispatch, while payload workflows retain uploads;
the shared context below must replace that split lifetime, not merely change payload filenames.
Move release/scheduler payload staging into invocation-private root-owned directories under
`/run/taskman-ops/<correlation_id>` using the existing transient staging capability. Upload before
admission is allowed only there. Change host validators to accept exact bounded filenames in that
validated invocation directory, preserving checksum, mode, non-link and archive-member checks;
do not accept arbitrary `/tmp` files or retain `install_root/uploads` as a second live payload path.
Existing retained upload files remain recovery material for explicit operator disposition; no
automatic migration or deletion is added.
One helper-client invocation context owns this directory, helper and payloads. Create it once,
install/checksum-verify `taskman-host.pyz` (root 0500), then stage only `release.tar.gz` and/or
`backup-helper.pyz` (root 0600). Workflow callers pass that prepared context to dispatch; the runner
must not recreate it or independently remove its helper. Ordinary helper calls use the same context
without payloads. After a validated final reply, or proof entry never dispatched, that context removes
only its known files and directory; failed cleanup is reported. The ordinary payload invocation's
allowlist is exactly `taskman-host.pyz`, `release.tar.gz` and `backup-helper.pyz`. The separate
sensitive pgpass invocation also owns its existing bounded `pgpass.probe` file (root 0600);
that mode's context must clean or retain it with the helper, rather than treating it as an
unknown file or allowing arbitrary additional payloads. After unknown dispatch retain
all potentially needed helper/payload files, including zipapp source, until explicit quiescence and
safe scoped cleanup. Helpers must not delete other invocations' directories. `/run` persistence
across reboot is not required: transient inputs can be restaged from a newly validated target and
matching package. No retained upload becomes a cache or durable recovery authority. Transport staging
does not count as managed mutation. Preserve `UploadDispatchResult` and aggregation of prior
known/possible managed effects; relocating private staging is not permission to discard earlier
convergence, reservation or scheduler mutation evidence. Reuse ordinary metadata/containment,
no-follow file-open, same-opened-archive, checksum and framing checks from the accepted upload
contract. No generic retained-directory/copy framework is required for this private namespace.

The sensitive supplied-pgpass entry remains exit-only with suppressed stdout/stderr, but its
stdin becomes a finite frame: four-byte unsigned big-endian header length (1..4096), that many UTF-8
JSON header bytes, then supplied pgpass bytes to EOF, total bounded by MAX_INPUT_BYTES. Header keys
are exactly `protocol_version`, `correlation_id`, `admission_owner`, `paths`, `database`,
`database_state`, and `authentication_mode`; use protocol 4, the same owner/path/database
validators, ready/absent database state, and exactly `authenticate` or `defer-for-configuration`. Raw pgpass parsing/authentication
is unchanged. Args select only the fixed mode; no owner ID or secret goes in argv/env. Acquire admission around the whole proof and allow only matching
provision ownership (or null when no reservation exists). Preserve exits 0/2/5/10 and add 12 for
contention; 5 retains the existing SCRAM/client failure classification rather than becoming a credential mismatch. Derive deferred authentication only
from validated explicit `configure-existing` authority, as today. Deferral does not skip exact
pgpass parsing, retained-file authority or selected-resource checks, and post-convergence readiness
still requires the ordinary application authentication proof.
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
| `postgresql_target` | Frozen branch/package-track/selected-cluster projection; exact contract in [PostgreSQL integration](#postgresql-integration). |
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
public JSON schema 1 and historical artifact identities do not change. Extend the implemented
PostgreSQL configuration/recovery validators and packaging to v4; do not remove or reimplement
that accepted capability.

The finite owner allowlist is the current provisioning call graph below, not every helper
operation. Each call takes short descriptor admission, validates its matching reservation, then
retains its existing inner lifecycle responsibilities. Internal calls share an already acquired
admission context rather than opening a second descriptor.

| Nested entry | Allowed owner use and binding |
| --- | --- |
| `discover` in provision mode; `provision_authority`; sensitive pgpass proof | Match confirmed roots/database; preserve override/access context and all current authority checks |
| `inspect_postgresql`, `configure_postgresql`, `postgresql_config_recovery` | Match roots and the frozen PostgreSQL target described below; configuration additionally matches database |
| `firewall` inspect/converge | Match reservation roots and database port where supplied; retain exact port policy, administrator identity and snapshot checks |
| `caddy` inspect/guard-install/prepare/activate | Match reservation roots; retain frozen ownership, site/receipt and public-probe checks; activation remains inside provisioning before successful completion |
| `prepare_genesis`, `genesis`, `verify`; admission finish | Match roots/database/release where supplied; preserve preparation, completion and verification checks |

Unrelated discovery modes, deploy/rollback/restore/backup/cleanup/admin and standalone recovery or
resume requests cannot bypass a reservation with an owner ID. Null-owner calls still refuse a
present reservation. Carry owner context through `ProvisioningInputs`, pyinfra's existing host/state
adapter, Caddy/firewall request builders and request reconstructions used to validate receipts.
Do not store owner IDs in global environment or command text. Firewall/Caddy v4 requests now carry
exact `install_root` and `backup_root` paths, including null-owner calls, replacing their current
empty-path schemas. This is a deliberate paired constructor/validator change, not a permissive
acceptance of either shape.

### PostgreSQL integration

Add exactly one `postgresql_target` field to the proposed schema-1 reservation. It is a closed
mapping with `branch`, `package_track` and `cluster`, derived at begin from the freshly validated
PostgreSQL authority and confirmed package-track input, not arbitrary controller completion data.
`branch` is `existing`, `bootstrap` or `configure-existing`; `package_track` uses the current
nullable numeric-track validator; `cluster` is null only for bootstrap, otherwise the exact current
six-field selected cluster (`version`, `name`, `data_directory`, `config_file`, `hba_file`, `port`).
Validate it through the existing branch-aware `validate_postgresql_authority` rules and configured
database-port consistency checks, using branch-derived force context. In particular, the existing
resource-only branch may retain a nonstandard already validated HBA path; do not apply the stricter
`validate_postgresql_cluster` override-only native-file policy to every stored cluster.
No owner request can change that frozen projection.

Existing/resource-only provisioning does not stage global HBA or invoke native configuration.
Bootstrap may inspect/configure with null pre-creation cluster and its frozen package-track/database
binding; the existing native bootstrap observer still selects and validates the created target.
Configure-existing inspect/configure requests must equal the frozen selected cluster and package
track; their database must equal the record's database. Recovery capture is allowed only for
configure-existing and matches its exact cluster plus roots; its current request has no database
field, so the saved target supplies the missing binding. Retain separate capture and configuration
operations: capture success is independently recorded mutation, even if configure later fails.

`--force-postgresql-config` remains explicit configuration authority, never target selection or a
safety bypass. Begin validates its strict boolean context and current `postgresql` projection,
including override `access=unproven`. Changes in role/database/access after convergence are expected;
the reservation freezes native target intent, not a requirement to reproduce the pre-convergence
resource snapshot. Do not route native configuration through the ordinary already-configured
endpoint observer: bootstrap must retain its old-port inspection policy.

Acquire admission before the configuration helper's existing lifecycle scope. Give the separate
recovery-capture helper an admission scope too; do not claim its current implementation already
holds lifecycle. Its exact protected-copy procedure and independently retained UUID directories
remain unchanged. Native handled-signal cancellation/reaping and bounded HBA restoration stay with
the configuration capability; they neither prove remote caller-loss quiescence nor authorize
reservation clearance, full configuration rollback or deletion of retained recovery files.

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

Run existing convergence with the owner context, including the nested PostgreSQL, firewall and
Caddy helpers above. Generic systemd creation may install/enable the
necessary unit resources but must not enable/start the backup timer while reserved: it remains
disabled/inactive, including first install. Existing scheduler refresh retains its stop/unlock/wait/
reacquire/revalidate/checksum contract, but its restart and error-restoration branches defer to
provisioning finalization. Ordinary deploy continues to use its existing enabled=>active policy.
This changes all three current `declare_systemd` cases: an entirely absent scheduler currently
starts its new timer, a newly completed partial scheduler enables without starting, and existing
scheduler policy is left to genesis. While reserved, none may enable/start the timer. Retain
per-resource creation authority and defer activation to finish.
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

The existing Caddy `application-provisioned-proxy-pending` outcome remains status 13 and is not
successful admission finish. Preserve its candidate/preparation/Caddy receipts, set
`recovery_required=true`, and explicitly report retained reservation and suspended backups. Replace
its unconditional rerun advice with: inspect/complete the operator-owned Caddy setup, establish
quiescence, run explicit provisioning recovery (keep suspended if no verified completed endpoint
exists), then replan provisioning from refreshed authority. Do not automatically finalize or grant
same-owner replay through this pending branch.

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
| begin | `action: begin`, `owner_id`, `desired_release_id`, `backup_helper_sha256`, `authority_settings` | Existing exact 20-field provision_authority state | null |
| finish | `action: finish`, `database`, `verification` | `reservation_sha256` | Matching record owner |
| recover preview | `action: recover`, `owner_id`, strict boolean `keep_backups_suspended`, `database`, `verification`, `dry_run: true` | empty mapping | null |
| recover apply | Same keys, `dry_run: false` | `reservation_sha256`, `recovery_snapshot_sha256` | null |
| resume_backups preview | `action: resume_backups`, `database`, `verification`, `backup_helper_sha256`, `dry_run: true` | empty mapping | null |
| resume_backups apply | Same keys, `dry_run: false` | `recovery_snapshot_sha256` | null |

Begin authority_settings has exactly `database`, `postgres_package_track`, strict boolean
`force_postgresql_config`, and `resource_digests`;
the digest mapping has exactly `taskman_service`, `backup_environment`, `backup_service`, and
`backup_timer`, all validated SHA-256 values, matching existing provision_authority input. Reuse the
host's underlying locked observer, not a nested independently locking helper invocation. Its state
has exactly `authority`, `postgresql`, `initial_database_empty`, `selected_release_id`,
`last_successful_selection_id`, `last_successful_selection`, `previous_successful_selection`,
`applied_migrations`, `service_state`, `database_state`, `backup_protections`,
`independently_held_backup_ids`, `backup_protection_sha256`, `scheduled_backup_sha256`,
`backup_timer_enabled`, `backup_timer_state`, `downgrade_baseline_sha256`, `installed_release_count`,
`installed_release_sha256`, and `scheduler_resources`, with existing validators/budgets. Compare the
confirmed state under begin's locks before record/scheduler mutation. Preserve the existing
`validate_postgresql_authority` and schema-consistency checks, including configured port/track,
force context, compatible database versus ready state, and initial-empty/migration consistency.
The source owners are [`discover.provision_authority`](../../ops/taskman_ops/host_helper/operations/discover.py)
and [`validate_existing_authority`](../../ops/taskman_ops/provisioning.py); these currently require
four input settings and the closed 20-field projection. No opaque action language,
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
Preserve operation-specific result shapes instead of forcing every denial into lifecycle's
`failed_boundary=lock` schema. Add only the finite v4 denial variants below. A pre-entry denial
proves this call's managed mutation unchanged; the controller must still aggregate earlier known
or possible mutation. Unobserved host state is unavailable, not absent or a reusable authority.

| Entry | Busy/reserved denial | Unsafe admission authority |
| --- | --- | --- |
| Deploy/genesis/preparation/restore/cleanup mutation operations | Existing lock-boundary failure, retryable/12 and unavailable final fields | Refused/10 through existing authority-failure projection |
| Backup/rollback and discovery/list/preflight/verify observations | Existing locked observation response, retryable; controller maps it to 12 | Existing refused observation response, mapped to 10 |
| PostgreSQL inspect/configure | Existing `lock_unavailable`, `failed_boundary=postgresql`, `locked=true`, retryable/12, `changed=false`, `mutation_state=unchanged` | Existing `unsafe_state`, refused/10, same PostgreSQL boundary, unchanged mutation |
| PostgreSQL recovery capture | Extend its closed failure codes with retryable/12; `failed_boundary=postgresql`, unchanged mutation, null `recovery_directory` and `cluster` | Existing refused/10 capture shape with the same null/unavailable capture evidence |
| Caddy | Extend boundary enum with `lock`, reason with `admission-unavailable`, retryable/12; unchanged mutation, unavailable authority, no new receipt/validation/reload/recovery effects | Existing `unsafe-authority`, refused/10; do not claim a new authority observation |
| Firewall | Extend reason enum with `admission_unavailable`, retryable; existing eight fields, both change flags false, null snapshot/administrator identity and empty exceptions/proposed/completed; controller maps to 12 | Existing `unsupported_state`, refused and unchanged; controller maps to 10 |

For Caddy denials retain an already supplied validated receipt, while `validation` and `reload`
are `not-attempted` and recovery is `not-needed`/runtime-certain for this call. This does not erase
earlier Caddy mutation held by the controller. Firewall's current reason-based public mapping must
recognize its new contention reason rather than collapse it to generic remote status 5. Extend
PostgreSQL recovery's wire validator, failure projection and controller mapping together; it
currently admits only 5/8/10. Keep all non-admission result variants, strict encoding/failure
fallback and transport/protocol uncertainty semantics intact. The new admission operation has its
own observation shape above; native PostgreSQL failures remain PostgreSQL failures, never admission
success or a reason to clear the reservation.

Public JSON stays schema 1. Expose coarse admission/recovery facts and actual backup suspension;
redact raw commands/exceptions and preserve aggregate mutation evidence. Construct maintenance
failures through explicit `WorkflowResult` projection: the current generic `OpsError` rendering
drops state/facts/warnings and cannot carry retained-reservation/suspension evidence by itself.
Reservation publication,
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
- `workflows/provision.py` owns reserve/converge/prepare/activate/complete/finish ordering, the
  proxy-pending recovery result and aggregate evidence.
  Existing `provisioning.py`, baseline/firewall/Caddy/PostgreSQL convergence remains the live path.
  `services/postgresql.py`, `services/caddy.py`, `host/firewall.py` and `ProvisioningInputs` own
  propagation into current nested calls and receipt validation; do not rebuild those capabilities.
- Host mutating/read-only entry points, scheduled backup and administrator launcher own their finite
  admission scopes. `helper_client/package.py` and scheduled-package member lists include the new
  shared admission capability with the appropriate least-authority operation set. `backup_helper.py`
  and `services/systemd.py` defer timer policy while reserved.
- New focused controller recovery workflow and `cli.py` own inspection/recovery interaction, typed
  confirmation and public results, including narrow resume-backups. Host admission does not duplicate CLI consent.
- `host_protocol/postgresql.py`, `caddy.py`, `firewall.py` and their operation/controller adapters
  own the exact new request/denial variants while preserving native failure projections.
- Focused admission/workflow/protocol/scheduler/TTY tests cover the production consumers. Update
  runbook, current contract/architecture references and documentation indexes with implementation;
  do not promote this proposed protocol/behavior to implemented status now.

PostgreSQL native Python configuration and shared upload staging are implemented dependencies of
this source baseline, with accepted ownership and recovery boundaries to preserve. The parked CLI
UX proposal remains separate; do not absorb its presentation changes into this increment.

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
12. Cover all three PostgreSQL ownership branches, strict force context and 20-field begin authority;
    matching and mismatched frozen target across inspect/capture/configure; independent capture
    evidence after configure/transport/encoding failure; pgpass deferred-auth plus status-5 mapping.
    Verify existing package isolation and cancellation/HBA-recovery cases remain valid under v4.
13. Exercise owner propagation through production Caddy/firewall pyinfra adapters and all receipt
    reconstructions, including null-owner contention and their dedicated denial/result mappings.
    Proxy-pending status 13 retains reservation/suspension and requires explicit recovery before retry.

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

There is no active implementation or pending approval request. Reconsider this work only through
an explicit operator decision after assessing a concrete need, such as recurring command overlap,
multiple independent controllers, or a demonstrated unsafe provisioning/backup interaction.
Before reviving the full design, compare its cost with a narrower provisioning/backup guard or an
operator maintenance procedure. These alternatives reduce enforcement guarantees; moving the
whole procedure into one host-owned execution scope instead changes transport and supervision.
The [original alternatives](#evidence-and-alternatives) remain useful design context, not permanent
rejections that a future assessment must adopt.

If the operator selects this capability again, the remaining sequence is:

1. Resolve Task 32 in the registered Project and obtain explicit authority to reopen/reassign it.
   Refresh source, task/thread/blockers, service/package assumptions and actual coordination needs.
   Revalidate this complete proposal against the then-current baseline; full-design approval is
   required, including the previously unapproved choices and the two design rulings below.
2. Write and independently review a bounded implementation plan. Create execution tasks only after
   plan approval; preserve the approved-plan clean-session implementation boundary.
3. Implement on an appropriate fresh branch, perform scoped independent correctness review and
   local operations/package/runtime/TTY verification, then update the canonical contracts, runbook
   and indexes to describe the actual resulting behavior.
4. Obtain separate authority for native host acceptance. Exercise overlap refusal, interruption and
   recovery, legacy scheduling, first/repeat provisioning and reboot behavior. Preserve private
   recovery resources and refresh source/artifact/acceptance bindings; historical evidence is not
   acceptance of a new source or installation.
5. Refresh target/remote/review and final-head CI, and obtain separate publication, merge and
   operator workstream-completion authority. Selecting this proposal does not grant host access,
   deployment or merge permission.

Two design rulings were proposed before deferral; neither is an accepted implementation decision:

- R1 / D8: [freeze the PostgreSQL target](#postgresql-integration) in the reservation because
  roots-only recovery requests cannot otherwise establish that selected-cluster binding. Cost if
  unnecessary: extra record validation; risk if insufficient: wrong-cluster mutation.
- R2 / D6: [recognize control-plane-only bootstrap authority](#admission-paths-authority-and-lock-order)
  before account creation, including the matching record after publication. Reusing the established
  observer unchanged blocks fresh begin; an overly broad exception could admit contradictory
  installation state. All other resource checks remain strict in the retained proposal.

## Revalidation scope and approval questions

This revision compares source at `8754d594` with the proposal; it does not establish runtime or
native acceptance. The lockfile still pins pyinfra 3.10.0 and the transient envelope remains v3.
The accepted PostgreSQL replacement's [local/native evidence limits](2026-10-07-shared-postgresql-design.md#implementation-boundaries-and-verification)
remain separate from this design refresh. Revalidation checks the actual production call graph,
closed validators, scheduler branches and staging consumers; future implementation must execute
the gates above. No admission implementation or native overlap/interruption/recovery acceptance
was performed. Earlier application test results and documentation/desktop checks do not establish
those guarantees; transient environment/setup failures are not requirements of this proposal.

| Changed contract | Reason and approval impact |
| --- | --- |
| [Current baseline](#evidence-and-alternatives) and [file boundaries](#file-ownership-and-implementation-boundary) | PostgreSQL native configuration and upload relocation have landed; removed the parked-refactor and Beads/new-branch assumptions. D1/D2 direction is unchanged. |
| [Owner propagation and PostgreSQL binding](#reservation-schema-and-owner-propagation) | Cover the implemented PostgreSQL/Caddy/firewall call graph, paired path-schema changes and proposed D8 target field. These detailed choices require approval. |
| [Private staging and pgpass](#invocation-private-staging) | Start from `install_root/uploads`; retain existing safety/evidence and `pgpass.probe` lifetime; preserve deferred authentication and native status 5. D4 remains proposed. |
| [Begin authority and results](#helperapi-and-result-changes) | Preserve strict force context and the current 20-field PostgreSQL-aware projection; specify finite per-operation contention variants. D3 remains proposed. |
| [Completion/failure](#known-completion-and-failure) and [scheduler ordering](#provisioning-begin-and-convergence) | Proxy-pending status 13 retains reservation/backups; fresh and partial scheduler enablement defers to finish. D5/D7 remain proposed. |

If reconsidered, complete-design approval must explicitly accept or revise these retained choices:

1. [D3/D8](#reservation-schema-and-owner-propagation): accept the finite owner propagation,
   frozen PostgreSQL target and paired v4 schema/failure extensions as the planning basis.
2. [D4](#invocation-private-staging): accept moving payloads from the current shared upload path
   into one invocation-owned `/run` lifetime, with uncertain-dispatch retention and restaging after reboot.
3. [D5–D7](#known-completion-and-failure): accept persistent backup suspension through failures
   and proxy-pending outcomes, noncreating admitted observations, and explicit typed recovery/resumption.

The former ownership/manual-recovery direction remains historical context. Deferral neither
approves the detailed contracts nor promises future delivery. Any future approval would cover
only the explicitly reviewed scope, under [Approval and continuation](#approval-and-continuation).

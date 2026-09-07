# Deployment controller reduction

**Status:** Approved
**Date:** 2026-09-07

## Summary

Taskman's first deployment-controller simplification successfully consolidated remote behavior
behind one transient helper and one bounded protocol, but production Python grew from 16,259 to
21,128 lines. Much of the remaining implementation is devoted to exact crash resumption,
provisional lifecycle publication, detailed recovery evidence, hostile-state classification, and
fine-grained timing behavior.

This design preserves every public operations command and its principal responsibility while
replacing the helper's transaction core with a smaller replayable-convergence model. A rerun
observes authoritative host state, repeats safe work, repairs recognizable partial state, requests
confirmation for newly required dangerous actions, and proceeds automatically whenever it needs no
new operator input.

The internal lifecycle, recovery, and backup metadata formats have never been deployed and may be
replaced without compatibility behavior. Completion requires at least a 35% reduction from the
21,128-line production Python baseline as well as deletion of the obsolete concepts that caused the
complexity. The percentage is a guard against another architectural rearrangement, not permission
to compress readable code or weaken the safety floor.

Delivery feature: `tas-deployment-controller-simplification-f00.11`.

## Context

The consolidated implementation established the correct outer boundaries:

- the workstation controller owns operator interaction, secrets, artifact building, SSH trust,
  pyinfra provisioning, helper transport, and result presentation;
- one transient standard-library helper owns state-dependent host operations;
- one bounded versioned protocol connects them; and
- only `install_root` and `backup_root` are configurable.

Those boundaries remain. The next reduction targets the machinery inside them. The largest current
modules are:

- `host_helper/lifecycle.py`, 1,507 lines;
- `host_helper/operations/deploy.py`, 1,229 lines;
- `host_helper/operations/restore.py`, 1,217 lines;
- `remote.py`, 1,071 lines;
- `host_helper/operations/backup.py`, 946 lines;
- `host_helper/operations/cleanup.py`, 935 lines; and
- `helper_runner.py`, 675 lines.

These modules contain substantial handling for instruction-boundary interruption, pending and
provisional records, exact retry identity, publication reconciliation, residue inventories,
fine-grained deadline propagation, and distinctions among many partial failure forms. This design
retains automatic recovery on operator rerun but does not retain seamless continuation of the same
transaction.

## Goals

1. Preserve all current public commands and each command's principal outcome.
2. Preserve the agreed security, privilege, backup, release-selection, and recoverability floor.
3. Make mutating commands automatically recover and converge on rerun whenever the required state
   is observable and no new operator information is needed.
4. Replace exact transaction resumption with replayable convergence from observed host state.
5. Persist only completed facts needed by later commands.
6. Replace operation-specific evidence and failure taxonomies with a small common result model.
7. Remove defensive handling whose complexity is disproportionate to realistic dedicated-host
   risk.
8. Reduce production Python by at least 35% from the recorded 21,128-line baseline.
9. Record the concepts, types, paths, and tests deleted so the result demonstrates actual
   simplification rather than code movement.
10. Prefer pyinfra declarative built-ins over custom provisioning convergence whenever the desired
    state is stable, secret-free, independently observable, and safely repeatable.
11. Make plain `deploy` reuse an exactly matching verified local artifact and otherwise perform the
    same build capability as `build` before connecting to the host.

## Non-goals

- Removing, renaming, or narrowing the principal responsibility of any public command.
- Changing the dedicated-host runtime topology, OTP release format, systemd ownership, loopback
  Phoenix binding, local PostgreSQL, Caddy, or workstation-driven authorization model.
- Removing the transient helper or merging stateful release and database transactions into
  pyinfra.
- Maximizing pyinfra usage for its own sake or disguising custom shell procedures as pyinfra
  operations without reducing bespoke policy.
- Adding a generic workflow language, transaction framework, persistent host agent, or third-party
  host dependency.
- Preserving compatibility with the current never-deployed lifecycle, recovery, or backup metadata
  formats.
- Seamlessly continuing the exact interrupted transaction or retaining its operation identity.
- Automatically recovering genuinely ambiguous state by guessing.
- Performing a deployment, real-host acceptance run, push, merge, or publication without separate
  authorization.

## Preserved public surface

The commands remain:

```text
build
provision
deploy
verify
releases
backups
backup
create-admin
cleanup
rollback
restore
```

Command names, plan-before-mutation behavior, confirmation prompts, human and JSON reporting, and
public exit categories remain stable unless implementation work demonstrates that an exact report
field has no external consumer and exists only to expose deleted internal machinery. Any such field
removal must be explicit in the implementation plan and documentation.

For artifact selection, `deploy --artifact PATH` continues to mean “verify and use this explicit
artifact.” Without `--artifact`, `deploy` resolves the current clean source and searches the managed
local artifact directory for a verified exact-input match. A match has the current source revision,
application version, supported target, pinned toolchain, and pinned builder identity. If no match
exists, `deploy` invokes the same build capability as `build` and deploys the resulting artifact.
Artifact age is not part of freshness: an immutable exact-input match remains reusable regardless of
its `built_at` value. Invalid and nonmatching cached artifacts are ignored, not repaired or trusted.

## Safety floor

The reduction must preserve these guarantees:

1. Secrets do not appear in logs, command arguments, helper protocol payloads, exceptions, or
   rendered reports.
2. SSH uses verified host identity.
3. Destructive actions require explicit operator confirmation.
4. Privileged writes and deletions remain confined to exact authoritative Taskman paths.
5. Migrations and restores retain a validated backup created before the consequential database
   change.
6. Release selection remains atomic.
7. Failures preserve a recoverable state when reasonably possible and report a truthful final
   state, even when that report is coarse.
8. Helper input, output, execution, and lifecycle remain bounded and transient.

Checks that directly establish this floor remain strict. Ordinary owned-file drift, redundant
metadata validation, exhaustive hostile-filesystem classification, exact timeout attribution, and
instruction-boundary recovery are not part of the floor.

## Deliberately relaxed behavior

The new core does not preserve:

- exact crash resumption or durable operation IDs;
- pending, provisional, finalization, or recovery publication protocols;
- detailed changed-stage histories;
- exhaustive residue-path and recovery-action arrays;
- exact classification of which instruction timed out or whether a transport failure occurred
  before or after a particular remote mutation;
- automatic reconstruction of arbitrary operator-created or corrupt filesystem states;
- bespoke refusal for low-risk ownership, mode, package, file, or service drift that standard
  convergence can safely repair;
- race handling beyond one lifecycle lock, atomic filesystem operations, authoritative-path checks,
  and a small confirmation-relevance check; or
- compatibility aliases or migrators for current internal metadata.

Unknown non-authoritative files may be ignored or reported as warnings. Ambiguous authoritative
state remains a refusal rather than being silently repaired.

## Target architecture

### Controller

The controller continues to own:

- command and environment parsing;
- local validation and secret decryption;
- release construction and local artifact verification;
- strict host-key SSH establishment;
- declarative pyinfra convergence;
- plan presentation and operator confirmation;
- transient helper packaging and invocation;
- request/result correlation; and
- public result and exit-category rendering.

Controller workflows describe operator intent and translate one helper result. They do not
reconstruct remote transaction stages or validate a duplicate model of host lifecycle state.

### Pyinfra boundary

Provisioning follows a built-in-first rule. Use pyinfra's declarative operations for stable host
state whose ordinary convergence and failure behavior satisfy the safety floor, including packages,
repositories, users, directories, non-secret configuration files, file ownership and modes,
systemd unit installation, and ordinary service enablement or reloads. Delete custom probes,
refusal logic, and conditional-convergence wrappers when a built-in can safely detect and repair
that drift on rerun.

Retain a small custom action when the built-in does not preserve a material availability, access,
or secret boundary. Expected examples are validating Caddy configuration before activation, safely
establishing firewall rules without losing SSH access, PostgreSQL cluster and HBA transitions, and
installing runtime or database credentials without exposing secret bytes. PostgreSQL role or
database built-ins may be used only where their actual convergence semantics cover the required
existing-state changes and secret handling.

Command-specific release, migration, backup, restore, cleanup, and post-transaction verification
remain transient-helper responsibilities. They depend on immediately preceding mutable state,
confirmation, secrets, or coherent observation under the lifecycle lock. Moving their shell code
into a generic pyinfra shell operation would relocate custom logic rather than simplify it.

### Observed host state

One immutable `HostState`-style value is built under the lifecycle lock from physical facts and
completed records. It contains only what commands consume:

- selected release;
- installed release manifests;
- successful release-selection history;
- validated backup manifests;
- applied database migrations and relevant database identities;
- service and readiness state; and
- recognizable temporary releases, dumps, and databases.

Observation rejects ambiguity in authoritative paths or identities. It does not classify every
unknown filesystem entry or attach recovery evidence to each fact.

### Minimal durable records

The helper persists only completed facts required by later commands:

- a release manifest within each immutable installed release;
- a backup manifest beside each validated backup dump; and
- a simple successful deployment or selection history containing the releases and migration
  relationship needed to assess rollback compatibility.

There are no pending operation records, provisional records, recovery records, publication
markers, or durable operation identities. Successful records use atomic file replacement where an
update is required. History may instead use create-once files when that is simpler.

### Locking and confirmation relevance

All mutating helper operations use one exclusive lifecycle lock. Read-only commands may use the
same lock briefly or rely on atomic completed records when that produces an equally coherent
snapshot with less machinery.

The helper does not revalidate an exhaustive controller snapshot after confirmation. It checks only
facts that materially define the confirmed destructive action, such as the current release, target
release, selected backup, or exact cleanup targets. A material change returns a replan/refusal;
irrelevant drift does not invalidate confirmation.

### Replayable convergence

A mutating command follows one direct procedure:

```text
acquire lock
observe authoritative state
normalize recognizable incomplete artifacts
validate the requested outcome against current state
repeat or skip idempotent work
perform consequential steps in safe order
verify the requested final outcome
record completed facts
return the final observed state
```

Commands do not share a configurable workflow engine. They reuse small capabilities for locking,
observation, artifact installation, backup creation, service control, verification, and atomic
selection.

## Command responsibilities

### `build`

Build and locally verify a release artifact. Keep provenance, archive integrity, and manifest
validation. Publish successful output into the managed local artifact directory so a later plain
`deploy` can reuse it. Do not introduce host lifecycle concepts.

### `provision`

Run the single programmatic pyinfra deploy for stable host state, then use the same deployment
procedure as `deploy` for the first release. Do not retain a separate genesis transaction model.

### `deploy`

Resolve an artifact before connecting to the host: honor an explicit `--artifact`; otherwise reuse
a verified exact-input local artifact or run the same build capability as `build`. Then validate and
install the artifact, create a validated backup when migrations require one, stop the service,
migrate, switch `current` atomically, restart, verify, and append one successful selection record.
On rerun, observe which outcomes hold and repeat the remaining safe steps.

### `verify`

Read service, release, database, listener, and health state and return a bounded pass/fail summary.
Do not reconstruct transaction history or mutate host state.

### `releases` and `backups`

List installed releases and validated backups from their manifests and successful selection
history. Unknown non-authoritative entries produce a warning or are ignored.

### `backup`

Dump to a deterministic temporary path, validate the dump, atomically publish it, and write its
completed manifest. On rerun, remove, replace, or reuse recognizable incomplete output without a
pending publication record.

### `rollback`

Confirm the target, use successful history to check migration compatibility, create a fresh
validated backup, select the target atomically, restart, verify, and record success. Do not perform
reverse migrations.

### `restore`

Validate the chosen backup, create a fresh safety backup, restore through a deterministic temporary
database, swap databases, select the compatible release, restart, verify, and record success. A
rerun observes the database arrangement and completes or restarts the procedure when it can do so
without guessing.

### `cleanup`

Plan exact recognized stale releases, backups, and temporary artifacts; confirm; then delete only
those paths. Do not retain tree digests, per-target recoverability vectors, or exhaustive
mutation-race narration unless a specific check is necessary for authoritative-path containment.

### `create-admin`

Preserve administrator creation and secret handling using ordinary bounded command execution. It
does not participate in release lifecycle transactions.

## Result and error model

The helper uses four internal outcomes:

- `succeeded`: the requested final outcome was verified;
- `refused`: the request or observed state is unsafe and no consequential mutation occurred;
- `retryable`: the helper stopped in a recognizable recoverable state and the operator should
  rerun the command; and
- `manual`: state is ambiguous or external information or action is required.

The result contains the operation, correlation ID, outcome, concise message, warnings, and final
observed release/database/service state relevant to that operation. It does not contain detailed
stage histories, exact residue inventories, or generated recovery procedures.

The controller maps the small internal model to the existing public exit categories. Operation
code raises a small common set of errors rather than distinct exceptions for each partial effect.

## Recovery behavior

- The running command makes a best-effort attempt to leave the service usable but does not execute
  a complex compensation state machine.
- Rerunning the same command is the normal automatic recovery path.
- The rerun observes physical state and repeats already completed work when repetition is safe.
- When forward completion is unsafe but a derivable safe repair exists, the tool performs it after
  any newly required confirmation.
- Deterministic temporary names make incomplete releases, dumps, and databases recognizable without
  durable journals.
- The helper stops with `manual` only when it cannot identify the authoritative release, database,
  backup, or safe next transition from available facts.

Automatic recovery means the operator reruns the command, confirms newly presented dangerous steps
when required, and the tool does the remaining work. It does not mean background recovery,
unprompted rollback, or seamless continuation of the original process.

## Timing and transport

- Use one lifecycle-lock timeout and a simple active-operation refusal.
- Retain bounded SSH connection, helper execution, subprocess, and output limits.
- Prefer one timeout at each external call boundary over propagated nested deadline budgets.
- Treat a lost helper transport result as unknown remote outcome and direct the operator to inspect
  or rerun; do not infer the last completed instruction.
- Keep checksum verification and root-owned transient helper execution because they directly
  establish the safety floor.
- Simplify cleanup reporting to a warning when the transient helper or upload cannot be removed;
  the next invocation may clean recognizable Taskman-owned temporary paths.

## Testing strategy

Tests protect observable command outcomes and the safety floor rather than reproducing every
internal transition.

Each command retains focused coverage for:

- success and no-op rerun;
- invalid or unsafe input refusal;
- interruption at each consequential boundary rather than every instruction;
- automatic convergence when rerun after those interruptions;
- secret redaction, authoritative-path containment, required validated backups, atomic release
  selection, and confirmation relevance; and
- genuinely ambiguous states that require manual intervention.

Artifact-resolution coverage additionally proves that plain `deploy` reuses a verified exact-input
artifact, rebuilds on a cache miss or input mismatch, ignores artifact age, rejects an unclean or
unidentified source when it would need implicit selection or building, and always honors an
explicit verified `--artifact` without substituting a cached artifact.

Tests whose only purpose is preserving the following are deleted rather than ported:

- exact internal stage labels or changed-stage sequences;
- operation-ID replay;
- pending or provisional record reconciliation;
- detailed residue and recovery-action arrays;
- exact timeout-stage attribution;
- arbitrary hostile filesystem combinations unrelated to authoritative-path containment; and
- recovery from corruption that cannot result from a normal interrupted command.

The final gates remain the complete operations suite, locked dependency validation, byte
compilation, shell checks, documentation checks, `mix precommit`, the pinned release builder, and
independent review. Real-host acceptance remains separately authorized.

## Migration strategy

1. Freeze the reduced public outcome and safety-floor contract. Explicitly identify tests that
   protect behavior this design removes.
2. Introduce one local artifact resolver shared by `build` output and plain `deploy`; keep explicit
   `--artifact` selection separate and authoritative.
3. Audit provisioning against the pyinfra boundary: replace eligible custom convergence with
   declarative built-ins, retain only the small custom actions justified above, and record why each
   retained action cannot safely use a built-in.
4. Add the minimal completed-record formats and observed-state model.
5. Move `verify`, `releases`, and `backups` to the new state model.
6. Replace backup and cleanup with replayable convergent procedures.
7. Replace deployment and provisioning genesis with one deployment procedure.
8. Replace rollback and restore.
9. Delete the old lifecycle, provisional-publication, recovery-evidence, and operation-resumption
   machinery as soon as its last production consumer moves.
10. Simplify controller transport and result translation after the new helper result proves what
   remains necessary.
11. Update the runbook and measure production and test lines, largest modules, and deleted concepts.
12. Run complete local gates, the pinned builder, and fresh independent review.

Production must not dynamically choose between the old and new implementations. Transitional code
may exist only within a migration slice and is deleted before that slice closes.

## Completion criteria

- Every public command remains and fulfills its principal responsibility.
- The safety floor is covered by focused tests.
- Every modeled interruption at a consequential boundary automatically converges on rerun unless
  the resulting state is genuinely ambiguous.
- Internal lifecycle and result models contain no durable operation identity, pending publication,
  exact stage journal, exhaustive residue list, or generated recovery-action sequence.
- There is one implementation for each host capability and one deployment procedure shared by
  `deploy` and provisioning genesis.
- Stable, secret-free, independently observable, safely repeatable provisioning uses pyinfra
  declarative built-ins; every remaining custom provisioning action has a recorded safety or
  semantic justification.
- Plain `deploy` reuses only a verified artifact matching the current clean build inputs and
  otherwise runs the shared build capability; freshness has no time limit.
- Production Python is reduced by at least 35% from the 21,128-line baseline.
- The final report names deleted concepts, types, compatibility paths, and obsolete tests; line
  movement or file splitting does not count as simplification.
- The complete local verification gates, pinned builder, and fresh independent review pass.
- Real-host acceptance remains explicitly unresolved until separately authorized.

If the implementation cannot meet the percentage reduction while preserving the accepted command
responsibilities and safety floor, work stops for design reassessment rather than declaring a
smaller rearrangement complete.

## Risks and mitigations

- **Incomplete state is harder to classify without journals.** Use deterministic temporary names
  and derive recovery only from a deliberately small set of observable physical states.
- **Reduced validation could cross a privilege boundary.** Retain strict exact-path, owner, secret,
  SSH identity, backup, and atomic-selection checks; relax only unrelated defensive detail.
- **A generic convergence abstraction could recreate complexity.** Keep command procedures explicit
  and share only small host capabilities.
- **Tests could preserve the discarded design indirectly.** Review tests against public outcomes
  and the safety floor before porting them.
- **Code could move rather than disappear.** Enforce the percentage gate and record deleted
  concepts in addition to physical lines.
- **Forward recovery could be unsafe after a migration.** Require a validated pre-migration backup,
  observe applied migrations, and stop when compatibility cannot be derived.
- **Simpler reporting could obscure operator action.** Always report the final selected release,
  database and service state, plus whether rerun or manual action is required.

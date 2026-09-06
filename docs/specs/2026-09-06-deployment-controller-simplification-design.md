# Deployment controller simplification

**Status:** Approved
**Date:** 2026-09-06

## Summary

Taskman's dedicated-host automation is complete and locally verified, but its controller owns
16,259 lines of production Python. Important safety behavior is spread across embedded host-side
programs and duplicated controller-side parsing and validation. Several modules combine transport,
protocol, state, policy, and recovery responsibilities; the largest release transaction module is
1,450 lines.

This design preserves the existing operator interface and core deployment guarantees while
replacing the embedded remote programs with one transient, checksum-verified,
standard-library-only host helper. It also replaces four independently configurable filesystem
roots with one installation root and one backup root. Deploy, rollback, restore, and provisioning
remain distinct workflows, but share one bounded host protocol and transaction execution boundary.

The simplification preserves consequential behavior except for the approved configuration schema
change. It may replace bespoke low-consequence drift refusal, change reporting, or recovery details
with standard safe pyinfra behavior when exact parity would require substantial code for only a
marginal safety improvement. It does not authorize a deployment, a real-host acceptance run, or any
other external mutation.

Delivery feature: `tas-deployment-controller-simplification-f00`.

## Current repository and environment state

The implementation being simplified is on branch
`dedicated-host-deployment-automation`. Its completed delivery milestone is
`6f5937d4dff1fea8e615fa2ea87879d3e3d03888`.

The current controller has:

- 16,259 physical production Python lines across 44 files;
- 17,835 Python test lines;
- approximately 2,670 non-documentation lines inside multiline executable strings;
- 4,820 workflow-orchestration lines;
- 3,287 release-state and release-primitive lines;
- 3,516 host-discovery and service-convergence lines;
- 4,636 controller-foundation, configuration, build, and output lines; and
- 1,005 installation-verification lines.

Although the original automation design selected pyinfra as the convergence engine, the production
provisioning path currently uses it only for SSH connection management, shell-command execution,
and SFTP transfer. Declarative adapters for `apt`, files, users, systemd, PostgreSQL, Caddy, and UFW
exist, but production call tracing shows that `provision` bypasses them in favor of parallel
custom-shell implementations. Those adapters are reached only by tests. Removing this
adapter/direct-script duplication and restoring a real pyinfra convergence path is part of this
refactor.

The largest concentrations are:

- `ops/taskman_ops/workflows/deploy_transaction.py`, 1,450 lines;
- `ops/taskman_ops/releases/records.py`, 1,230 lines;
- `ops/taskman_ops/verification.py`, 1,005 lines;
- `ops/taskman_ops/host/facts.py`, 960 lines; and
- `ops/taskman_ops/services/postgresql.py`, 901 lines.

The completed baseline passes 673 operations tests with four controlled-host skips,
`mix precommit` with 802 tests, shell and documentation checks, and the pinned Ubuntu 26.04
`linux/amd64` builder. The builder reports known dependency advisories for Ash 3.32.3 and Mint
1.9.3; dependency upgrades are outside this refactor.

No real environment configuration, SOPS private identity, production credential, or authorized
disposable Ubuntu host is present. No compatibility migrator is required for operator-created
environment files because the automation has not been merged or deployed.

## Goals

1. Preserve the approved core deployment, provisioning, backup, rollback, restore, cleanup,
   verification, redaction, failure-evidence, and operator-control guarantees.
2. Replace substantial embedded executable programs with one versioned transient host helper.
3. Establish one canonical, bounded request/result protocol shared by controller and helper.
4. Centralize helper packaging, strict transfer, invocation, result decoding, and cleanup.
5. Centralize canonical locking, under-lock discovery, stage execution, and transaction evidence.
6. Keep operation-specific policy explicit instead of creating a generic workflow-definition
   framework.
7. Reduce filesystem configuration to `install_root` and `backup_root`; derive all subordinate
   paths.
8. Split oversized modules by stable responsibility and remove obsolete duplicate validation.
9. Make pyinfra the actual engine for stable declarative host convergence rather than only an SSH
   transport.
10. Measure and materially reduce production code and duplication without code golfing or weakening
   evidence.
11. Keep a real Ubuntu-host acceptance run separately authorized.

## Non-goals

- Changing the OTP release, systemd, loopback Phoenix, local PostgreSQL, or Caddy runtime topology.
- Changing the workstation-driven authorization model.
- Adding a resident agent, daemon, listener, scheduler, or privileged service for the host helper.
- Adding third-party Python dependencies to the host.
- Replacing pyinfra for declarative host convergence.
- Changing release identity, artifact provenance, lifecycle record formats, migration declarations,
  backup formats, or public exit categories unless required by the root-schema change.
- Adding zero-downtime deployment, automatic deployment, or automatic rollback.
- Adding provider, DNS, Resend, off-host backup, or VPS lifecycle automation.
- Fixing the existing Ash or Mint dependency advisories.
- Producing a legacy configuration migration utility.
- Treating a line-count target as more important than understandable, verified code.

## Accepted decisions

### Transient host helper

The controller builds a deterministic Python zipapp named `taskman-host.pyz`. The helper:

- uses only the Python standard library available through Ubuntu's `python3-minimal`;
- is uploaded for one controller invocation;
- is installed into a unique root-owned directory under `/run/taskman-ops/`;
- is addressed by its absolute path rather than through `PATH`;
- is verified against the controller-computed SHA-256 before execution;
- opens no network listener and runs no background process;
- reads one bounded JSON request from standard input;
- emits one bounded JSON result on standard output; and
- is removed when the invocation finishes.

This deliberately replaces the earlier assumption that controller Python must never reach the
host. The durable guarantee is narrower: no controller package, source tree, dependency
environment, or host agent persists after successful execution.

Transience is selected because it produces the simpler total lifecycle, not because a durable
helper is prohibited or considered intrinsically unsafe. Every invocation uses the helper bundled
with that exact controller checkout, so there is no independently installed helper version to
bootstrap, upgrade, negotiate with, roll back, or garbage-collect.

### Two configurable roots

The environment schema exposes:

```yaml
install_root: /opt/taskman
backup_root: /var/backups/taskman
```

The controller derives:

```text
<install_root>/releases
<install_root>/deployments
<install_root>/current
```

`release_root`, `deployment_root`, and `managed_root` are removed as independent configuration
fields. Both accepted roots must be normalized absolute POSIX paths. They must be distinct,
non-overlapping, and outside reserved Taskman configuration, state, lock, installed-program, and
temporary-helper roots. Derived paths are never accepted from remote data or operator
configuration.

The default paths and existing lifecycle records remain unchanged. The earlier root fields never
became part of a merged or deployed interface, so the new schema has no compatibility aliases,
migration behavior, or field-specific legacy diagnostics.

### Shared protocol, separate policies

The protocol, transport runner, and transaction mechanics are shared. Deploy, genesis deployment,
rollback, restore, backup, cleanup, adoption, and verification retain separate operation policy.
They may reuse focused stage functions, but are not converted into a declarative workflow language
or a configurable state-machine framework.

### Proportionate safety and pyinfra-first convergence

Built-in pyinfra operations are the default for ordinary declarative convergence. The refactor does
not reimplement a built-in merely to preserve every stricter refusal, drift classification, or
changed/no-change detail of the current custom shell path.

Custom pyinfra operations or helper-owned actions require a concrete material risk that a built-in
cannot reasonably control. Material risks include:

- credential disclosure;
- SSH lockout or loss of administrative access;
- crossing an ownership or privilege boundary;
- destructive mutation outside an exact authoritative path or database;
- migration without the required validated backup;
- non-atomic or ambiguous release selection; and
- false, incomplete, or misleading recovery evidence after a consequential failure.

For ordinary files, packages, directories, accounts, and service state, standard pyinfra behavior
is acceptable when the operation is low-risk, reversible, visible in dry-run output, and safely
rerunnable. A migration task that intentionally changes an old behavior records the difference and
why it does not materially weaken confidentiality, integrity, availability, privilege isolation,
recoverability, or blast-radius control. When both designs reasonably protect those properties,
the simpler design wins.

### Existing interfaces remain stable

The `./ops/taskman` commands, confirmation behavior, dry-run behavior, human and JSON reports,
public exit categories, release artifact format, lifecycle record format, backup format, and
core recovery guarantees remain stable except where this specification explicitly changes
filesystem configuration. Provisioning may adopt pyinfra's standard drift repair and change
reporting where the proportional-safety rule permits it.

### Readable and immutable builder base

The builder references the official Ubuntu base image using both its dated release tag and matching
content digest:

```dockerfile
FROM ubuntu:resolute-20260811.1@sha256:<matching-manifest-digest>
```

The dated tag communicates the selected Ubuntu image release to reviewers. The digest retains exact
base-image identity if a registry tag is reassigned. An update changes and verifies the tag and
digest as one reviewed pair; neither value is advanced independently.

The release manifest records both the human-readable base-image tag and the resolved digest. This
does not claim complete bit-for-bit build reproducibility because Ubuntu package indexes remain
external inputs, even though installed Erlang, Elixir, Node, npm, Hex, and Rebar versions remain
explicitly constrained.

### Real pyinfra convergence

Provisioning uses one real programmatic pyinfra deploy for stable desired-state configuration.
Built-in pyinfra operations own package installation, the Taskman system account, managed
directories, non-secret configuration and unit files, Caddy's repository and package, PostgreSQL
package installation, systemd daemon reload and enablement, and unattended-upgrades configuration.

This replaces the current dual implementation in which declarative adapters exist but the
production workflow invokes separate rendered shell programs. There is one production convergence
path and one test seam for each capability.

Small custom pyinfra operations are permitted only when a provisioning action is still declarative
but no built-in operation reasonably preserves a material safety or ordering requirement. Exact
parity with a bespoke refusal contract is not sufficient justification. Custom operations must use
pyinfra's operation lifecycle and change reporting; they must not recreate an alternate
general-purpose convergence layer.

Stateful release and database transactions remain outside pyinfra. Wrapping the transient helper in
an imperative pyinfra shell operation would add prepare/execute machinery without providing
declarative convergence, so direct helper invocation remains the simpler boundary.

## Architecture

### Controller responsibilities

The workstation controller remains the only operator-facing program. It owns:

- parsing commands and environment configuration;
- local cross-field and path validation;
- SOPS decryption and redaction registration;
- release building and artifact verification;
- strict pinned-host-key SSH establishment;
- declarative pyinfra host convergence;
- presenting redacted plans and obtaining confirmation;
- packaging and invoking the transient helper;
- validating helper request correlation and required result invariants;
- translating validated results into stable `WorkflowResult` and `OpsError` values; and
- rendering bounded human or JSON output.

The controller must not reconstruct host mutations through operation-specific embedded Python
programs. Small fixed shell commands passed as argument arrays remain acceptable for bootstrapping
the helper, private file transfer, and declarative pyinfra operations.

### Helper responsibilities

The helper owns host-local behavior whose correctness depends on immediately preceding state:

- host and lifecycle snapshot discovery;
- canonical lifecycle locking;
- root-owned lifecycle record access;
- manual-installation adoption;
- release staging and immutable installation;
- database backup creation and validation;
- release activation and service transitions;
- deployment and genesis transactions;
- rollback;
- guarded restore and database swap;
- exact cleanup;
- transaction-local readiness and topology verification; and
- structured success, failure, residue, and recovery evidence.

The helper does not own SOPS decryption, artifact building, SSH trust establishment, plan
presentation, operator confirmation, provider resources, DNS changes, or off-host backups.

### Declarative convergence

pyinfra remains responsible for stable desired-state convergence of packages, users, directories,
ordinary non-secret files, package repositories, systemd units, backup units, and service
enablement. The helper does not become a second general-purpose provisioner.

The controller constructs and executes one programmatic pyinfra deploy for provisioning. The deploy
uses pyinfra's built-in `apt`, `files`, `server`, and `systemd` operations wherever they reasonably
protect Taskman's material invariants. Exact parity with every bespoke ownership, refusal, or
change-reporting detail is unnecessary. It does not branch during pyinfra's prepare phase on
mutable facts such as file existence, installed packages, or service state. Built-in operations
own their normal fact/diff behavior; execution-time conditions are reserved for the small number of
ordered custom operations that genuinely require them.

The following remain outside declarative pyinfra convergence:

- strict host-key establishment and controller protocol transport;
- hostile-state preflight and coherent lifecycle discovery;
- secret-bearing runtime and PostgreSQL inputs where pyinfra command or logging behavior cannot
  prove the existing redaction contract;
- UFW activation and mandatory fresh-SSH verification;
- lifecycle locking, staging, backup, migration, activation, rollback, restore, and cleanup; and
- transaction-local verification, failure evidence, and recovery reporting.

The implementation plan must examine UFW, PostgreSQL, Caddy validation, and protected runtime-file
installation individually. A built-in pyinfra operation is preferred unless analysis identifies a
concrete material risk it cannot reasonably control. A small custom operation or the transient
helper owns the capability only when that risk justifies the additional code. This decision is
recorded explicitly in the task that migrates the capability; it is not an invitation to retain
parallel production paths.

Provisioning invokes the same helper transaction used by deployment for the first immutable release.
It does not retain a provisioning-specific embedded activation implementation.

## Helper packaging and lifecycle

### Deterministic package

The package builder selects an explicit allowlist of helper modules and writes a deterministic
zipapp:

- file order is lexical;
- archive paths are relative and normalized;
- timestamps and permissions are fixed;
- source bytes are not generated from environment-specific values;
- the entry point is fixed;
- the archive contains no controller configuration, secrets, tests, caches, bytecode, repository
  metadata, or unrelated application source; and
- repeated builds from identical helper sources produce identical bytes and SHA-256.

The helper version is derived from a protocol version plus the archive SHA-256. It is not inferred
from a mutable filename.

### Transfer and installation

For each helper invocation, the controller:

1. generates a cryptographically random operation identifier satisfying a narrow allowlist;
2. builds the helper and computes its SHA-256 locally;
3. creates a unique administrator-owned transfer directory with mode `0700`;
4. uploads the helper with mode `0600`;
5. verifies the uploaded checksum before privilege escalation;
6. creates `/run/taskman-ops/<operation-id>` as `root:root` mode `0700`;
7. installs the verified helper there as `root:root` mode `0500`;
8. removes the administrator-owned transfer copy;
9. verifies the installed root-owned helper checksum;
10. invokes it as `sudo -- python3 <absolute-helper-path>` with the request on stdin; and
11. removes the root-owned invocation directory in a final cleanup attempt.

Existing unexpected entries, symlinks, non-root ownership, mismatched modes, or checksum changes
under the selected `/run/taskman-ops/<operation-id>` path are safety refusals. The controller never
recursively removes a path that it cannot prove belongs to the current operation.

If preparation fails before the helper can run, the controller reports whether the transfer or
root-owned directory may remain. If final cleanup fails, a successful operation remains successful
but includes exact residue and a fixed recovery action. A primary operation failure is never
replaced or hidden by cleanup failure.

### Secret inputs

The protocol contains no credential values. When an operation needs secret material, the controller
uses the existing private-transfer boundary to install a unique root-owned mode-`0600` input file.
The request contains only its validated absolute path and expected purpose. The helper opens it
without following symlinks, verifies ownership and mode, reads it only for the selected operation,
and removes it at the earliest safe point.

Raw helper stderr and malformed stdout are sensitive diagnostic material. They are bounded and
discarded or reduced to a fixed redacted error category; they are never echoed to the operator.

## Versioned request and result protocol

### Common envelope

The standard-library-only protocol module is imported by the controller and bundled unchanged in
the helper. It defines strict parsing, serialization, and bounds for:

```text
HostRequest
  protocol_version
  operation
  operation_id
  expected_state
  paths
  parameters

HostResult
  protocol_version
  operation
  operation_id
  outcome
  stage
  changed_stages
  lifecycle
  runtime_state
  verification
  residue_paths
  recovery_actions
  warnings
```

Every mapping uses an exact key set. Identifiers, paths, strings, sequences, nesting depth, input
bytes, output bytes, and diagnostic bytes have explicit upper bounds. The parser rejects booleans
where integers are required, duplicate identifiers, unknown enum values, non-absolute paths,
unexpected derived paths, and values that do not satisfy the existing narrow identifier patterns.

The controller verifies that protocol version, operation, operation identifier, and expected
result variant match the request. Mismatch, truncation, invalid UTF-8, invalid JSON, extra output,
or incomplete evidence is a safety refusal.

### Operation payloads

Operation-specific request and result types live beside the common envelope. They use stable domain
names rather than task, phase, milestone, or implementation-plan terminology. Each operation
declares:

- required expected-state evidence;
- exact non-secret inputs;
- possible success, no-op, refusal, and failed-stage results;
- stage names and legal ordering;
- state that must be rediscovered under the lock;
- residue that may remain;
- recovery actions that are truthful for each terminal state; and
- whether the operation is read-only or mutating.

The protocol module validates structure. Operation policy validates whether the structurally valid
evidence is legal for that operation and the observed state.

## Transaction execution

One helper runtime provides:

- canonical lock acquisition and holder evidence;
- fresh state discovery after acquiring the lock;
- ordered stage execution;
- changed-stage tracking;
- bounded warnings;
- primary-error retention;
- cleanup registration;
- residue reporting;
- recovery-action construction; and
- final result serialization.

This runtime is deliberately small. It does not interpret an arbitrary workflow description.
Operation modules call explicit Python functions in explicit order.

Deploy, genesis deployment, rollback, and restore continue to encode their distinct safety rules:

- deployment confirms the expected current release and migration declaration;
- genesis requires an empty lifecycle and represents the predecessor as absent;
- rollback proves every crossed activation edge is backward-compatible;
- restore validates the exact dump before confirmation and again under lock, restores into a
  temporary database, and preserves recoverable database state on failure.

Backup and cleanup use the same lock and result machinery but retain their narrower policies.
Read-only discovery and verification use the common protocol without acquiring a mutation lock
unless reading a coherent snapshot requires the existing shared lock contract.

## Safety and recovery guarantees

The refactor must preserve these non-negotiable behaviors:

- No mutation occurs before required validation and confirmation.
- Dry-runs perform applicable validation and discovery but no remote mutation and no confirmation.
- A mutating operation revalidates confirmed state under the canonical lock.
- No existing immutable release directory is edited in place.
- A pre-activation backup is created and validated where currently required.
- Migration, activation, startup, readiness, rollback, and restore failures report the selected
  release, service state, database state, backup, changed stages, residue, and next safe action.
- Genesis failures never invent a predecessor or report an unstarted service as running.
- Unknown state remains unknown; absence of evidence is never converted to a successful fact.
- Restore validation occurs before confirmation and repeats under lock.
- Cleanup deletes only exact, eligible, tool-owned entries under derived authoritative roots.
- Helper transport, protocol, and cleanup errors use existing stable exit categories.
- Redaction applies recursively to every result, exception, warning, plan, and rendered output.

The controller validates that helper results match the request and satisfy the operation's required
result invariants before presenting success. It does not independently reproduce host discovery or
transaction policy already enforced by the helper.

The following details are candidates for deliberate simplification rather than compatibility
requirements:

- refusal of ordinary owned-file or package drift that pyinfra can safely repair;
- bespoke changed/no-change markers when pyinfra already reports convergence accurately;
- low-consequence internal stage names;
- exact preservation of recoverable ordinary service state; and
- duplicate fact, ownership, or topology checks already supplied by a built-in operation.

Additional hardening is retained only when it materially changes confidentiality, integrity,
availability, privilege isolation, recoverability, or the blast radius of a failure.

## Repository boundaries

The implementation plan will refine exact names, but the intended ownership is:

```text
ops/
  taskman_ops/
    host_protocol/
      envelope.py
      identifiers.py
      operations/
    host_helper/
      __main__.py
      runtime.py
      paths.py
      facts.py
      lifecycle.py
      verification.py
      operations/
    helper_package.py
    helper_runner.py
    workflows/
    host/
    services/
  tests/
    host_protocol/
    host_helper/
    workflows/
```

- `host_protocol/` is standard-library-only code shared byte-for-byte by controller and helper.
- `host_helper/` contains host-local mechanics and operation policies.
- `helper_package.py` owns deterministic allowlisted zipapp construction.
- `helper_runner.py` owns transfer, checksum verification, bounded execution, cleanup, and
  controller-side protocol validation.
- `workflows/` owns operator-facing orchestration, plans, confirmation, and result translation.
- `host/` and `services/` define one programmatic pyinfra provisioning deploy and only the
  declarative capabilities that genuinely belong to it.

The refactor removes the full embedded deployment transaction and remote snapshot/adoption/locking
programs. Large lifecycle models, local storage, remote transport, and rollback analysis are split
by responsibility. Fact collection is separated from host-acceptance policy. Unused pyinfra
adapters and their parallel direct-shell implementations are replaced by one production
convergence path rather than retained as compatibility layers.

No new module should combine helper source text, SSH transport, protocol parsing, operation policy,
and operator-result rendering.

## Migration sequence

Migration proceeds in independently reviewable, core-guarantee-preserving slices:

1. Record baseline metrics and add missing characterization coverage for public CLI, configuration,
   lifecycle, result, and failure contracts.
2. Replace the four-root schema with the approved two-root model and derived paths.
3. Replace the unused-adapter/direct-script provisioning split with one programmatic pyinfra deploy,
   migrating stable built-in operations before bounded custom operations.
4. Add the shared protocol, deterministic helper package, and bounded runner.
5. Move read-only host and lifecycle discovery plus verification through the helper.
6. Move locking, lifecycle storage, staging, activation, and manual adoption.
7. Move existing-host deployment and clean-host genesis transactions.
8. Move backup, rollback, restore, and cleanup.
9. Integrate provisioning with the shared genesis path.
10. Remove obsolete embedded programs, duplicate parsers, duplicate transaction plumbing, and
    superseded convergence code.
11. Rebalance remaining modules, update documentation, measure the result, and run final review.

Each slice begins with a failing or characterization test, keeps the applicable operations suite
green after intentionally superseded assertions are updated or removed, and receives a scoped
independent review. Compatibility shims exist only while required by a later migration slice and
are removed before completion.

## Testing and verification

### Characterization baseline

Before structural changes, tests identify:

- command names, options, help, confirmations, and exit categories;
- human and JSON result shapes;
- dry-run discovery and non-mutation;
- configuration failures and the authoritative two-root schema;
- release, activation, adoption, and backup record compatibility;
- existing injected failure boundaries and whether each protects a core guarantee or only bespoke
  behavior;
- redaction of canary values in output, exceptions, commands, and residue;
- genesis, deployment, rollback, restore, and cleanup recovery evidence; and
- two-root derivation and overlap rejection.

Characterization records the current system so differences are deliberate; it does not make every
observed behavior a permanent requirement. Tests for intentionally relaxed low-consequence
behavior are replaced with tests for the selected pyinfra operation's material safety properties.

### Protocol and packaging

Focused tests prove:

- deterministic zipapp bytes and digest;
- allowlisted contents and absence of secrets, tests, caches, bytecode, and repository metadata;
- execution with Ubuntu's supported Python version and no third-party imports;
- exact-key parsing and every input/output bound;
- protocol, operation, and operation-ID matching;
- rejection of malformed, truncated, oversized, repeated, or trailing output;
- checksum verification before and after root-owned installation;
- safe preparation and final cleanup; and
- truthful residue when cleanup is refused or uncertain.

### Pyinfra convergence

Focused tests execute the real programmatic provisioning deploy and prove:

- built-in operations converge packages, accounts, directories, ordinary files, and systemd state;
- a converged second run is a no-op;
- ordinary owned drift is repaired using standard pyinfra behavior, while incompatible ownership,
  type, symlink, or topology is refused only where replacement would create a material risk;
- dry-run reports the same applicable convergence without mutation;
- operation order does not depend on mutable prepare-phase facts;
- custom operations, if any, report truthful changed/no-change results; and
- no production capability retains both a pyinfra adapter and a direct-shell implementation.

Tests that instantiate otherwise-unused pyinfra adapters are removed rather than counted as
production coverage.

### Operation parity

Each migrated operation runs through the real helper entry point in isolated temporary roots. Tests
cover success, no-op, refusal, injected failure at every state-changing boundary, and rerun behavior.
Existing shell-backed and controlled-Ubuntu-adapter tests remain the behavioral oracle.

Legacy and helper implementations may coexist only long enough to establish parity for the current
slice. Production does not dynamically choose between them. The old path is deleted once the new
path passes its focused and full gates.

### Repository gates

Completion requires:

- the complete operations suite;
- Python byte compilation and locked dependency validation;
- `mix precommit`;
- shell syntax and every documented help surface;
- local Markdown links;
- secret-canary and planning-identifier scans;
- exact-range whitespace validation;
- the pinned Ubuntu 26.04 `linux/amd64` release builder;
- scoped independent review after every migration slice; and
- one final fresh full-branch review.

A real disposable Ubuntu 26.04 `amd64` acceptance run remains unresolved until separately
authorized. Local completion must not claim systemd PID 1, UFW, ACME, public DNS, email, reboot, or
full real-PostgreSQL restore evidence that was not exercised.

## Simplification evidence

The final report records the same measurements used for the baseline:

- physical production and test lines;
- multiline executable-string lines;
- lines by controller, protocol, helper, workflow, release, host, and service area;
- largest modules;
- repeated transaction/evidence helpers removed; and
- unused pyinfra adapters and parallel direct convergence programs removed; and
- remaining compatibility or duplication debt.

The expected result is a material reduction, approximately 20–30 percent of production Python,
with no substantial executable program embedded in controller strings. This is a design target,
not permission to compress readable code, merge unrelated responsibilities, weaken validation, or
delete meaningful tests. If the implementation misses the target, final review must either
demonstrate that the resulting boundaries are nevertheless simpler or explain why a larger
reduction could not reasonably be achieved because the remaining behavior has inherent complexity
rather than avoidable duplication.

## Documentation changes

Implementation updates:

- `ops/environments/example.yaml` for `install_root` and `backup_root`;
- `docs/deployment.md` for helper prerequisites, lifecycle, residue recovery, and the two-root
  schema;
- the original dedicated-host automation specification's supersession notice;
- `docs/README.md` indexes;
- CLI help and examples containing removed root names; and
- the builder base reference and artifact metadata for the paired Ubuntu tag and digest; and
- the active workstream handoff and Beads feature.

Documentation describes the host helper as transient execution infrastructure, not as an installed
agent or service.

## Rejected alternatives

### Persistent installed helper

A persistent, versioned helper avoids repeated upload cost but creates helper activation,
compatibility, rollback, cleanup, and bootstrap state. The helper is small relative to release
artifacts, and deployments are infrequent, so avoiding those durable states is more valuable than
the transfer optimization. A durable helper remains an acceptable future design if measured
transfer or startup costs justify its additional lifecycle; it is not rejected as a security
boundary.

### Reorganized embedded programs

Moving embedded strings into more controller modules would improve navigation but preserve the
dual implementation and protocol duplication. It does not satisfy the simplification objective.

### Generic workflow engine

Encoding stages and compensations as data could reduce repeated syntax but would obscure the
different database and release guarantees of deploy, rollback, and restore. Shared mechanics plus
explicit policies is the smaller understandable boundary.

### Canonical paths with no configurability

Hard-coded paths are simplest, but one installation root and one separate backup root preserve
useful relocatability at modest cost. Independently configurable subordinate roots are not retained.

### Replacing pyinfra with Ansible

Ansible could reduce some project-owned convergence code, but it would change the accepted
automation stack and would not remove the release transaction protocol. This refactor first fixes
the demonstrated controller/host boundary without combining it with a provisioner migration.

## External evidence

The revised boundaries rely on:

- [pyinfra's declarative and imperative operation model](https://docs.pyinfra.com/en/3.x/using-operations.html);
- [pyinfra's prepare and execute phases](https://docs.pyinfra.com/en/3.x/deploy-process.html);
- [pyinfra's built-in operation catalog](https://docs.pyinfra.com/en/3.x/operations.html);
- [pyinfra package operations](https://docs.pyinfra.com/en/3.x/operations/apt.html);
- [pyinfra file operations](https://docs.pyinfra.com/en/3.x/operations/files.html);
- [pyinfra system-account operations](https://docs.pyinfra.com/en/3.x/operations/server.html);
- [pyinfra systemd operations](https://docs.pyinfra.com/en/3.x/operations/systemd.html);
- [the official Ubuntu image's supported dated tags](https://hub.docker.com/_/ubuntu); and
- [Docker's distinction between mutable tags and immutable digests](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest).

pyinfra deploy code runs during a prepare phase before mutating operations execute. Therefore,
mutable facts observed in ordinary deploy-code branches cannot safely represent the effects of
earlier operations. The implementation uses built-in operation diffing or explicit execution-time
conditions for declarative convergence and keeps immediately state-dependent transactions in the
helper.

## Risks and mitigations

- **Protocol drift:** one shared standard-library module is imported by the controller and bundled
  unchanged in the helper; version and operation identifiers are checked both ways.
- **Helper tampering:** verify SHA-256 before privileged installation and again at the absolute
  root-owned execution path.
- **Temporary privilege residue:** use unique paths, exact ownership/mode checks, conservative
  cleanup, and explicit residue evidence.
- **Refactor regressions:** migrate one operation at a time under characterization and
  failure-injection tests; never retain an unverified fallback.
- **Over-generalization:** prohibit arbitrary workflow descriptions and keep operation policy
  explicit.
- **Pyinfra phase errors:** use built-in operation diffing, avoid branching on mutable facts during
  prepare, and exercise the real programmatic deploy in convergence and dry-run tests.
- **False pyinfra coverage:** require production call tracing and tests through the actual
  `provision` capability path; adapter-only unit tests are insufficient.
- **Marginal hardening recreates the old complexity:** default to built-in pyinfra behavior and
  require a written material-risk justification for custom convergence or helper ownership.
- **Hidden code relocation:** record before/after metrics and require removal, not mere movement, of
  duplicated embedded programs.
- **Root-schema ambiguity:** derive every subordinate path in one model and expose no legacy root
  aliases.
- **False real-host confidence:** retain the external acceptance gate and report only locally
  exercised evidence.

## Completion checklist

- The two-root configuration is canonical and independently placed subordinate roots are rejected.
- The builder identifies Ubuntu through a reviewed dated tag and matching immutable digest.
- Provisioning runs one real programmatic pyinfra deploy for stable desired state.
- No declarative capability retains unused adapters beside a separate live shell implementation.
- The deterministic transient helper is the only substantial imperative host program.
- Controller and helper share one bounded, versioned protocol implementation.
- One runner owns helper transfer, verification, execution, decoding, and cleanup.
- One helper runtime owns canonical locking and common transaction evidence.
- Operation policies remain explicit and independently testable.
- Obsolete embedded programs and compatibility shims are removed.
- Lifecycle and artifact formats remain compatible.
- Core observable and recovery guarantees remain stable; the root-schema change and approved
  low-consequence pyinfra convergence differences are documented.
- Before/after simplification measurements are recorded.
- All local, builder, scoped-review, and full-branch-review gates pass.
- Real-host acceptance is still explicitly unresolved and separately authorized.
- Canonical documentation, Beads, indexes, and the short-lived handoff agree.

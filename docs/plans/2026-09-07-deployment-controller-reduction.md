# Deployment Controller Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or
> executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Preserve every deployment command and its principal responsibility while replacing the
consolidated controller's evidence-heavy transaction machinery with a substantially smaller,
replayable-convergence implementation.

**Architecture:** Keep the CLI, strict SSH transport, programmatic pyinfra provisioning, bounded
transient helper, and two authoritative roots. Replace journals and provisional records with one
observed `HostState`, completed-only manifests and selection records, explicit command procedures,
and four coarse outcomes; operator rerun is the normal recovery mechanism. Plain `deploy` resolves
an exact-input local artifact or invokes the same builder used by `build` before connecting.

**Tech Stack:** Python 3.12, pyinfra 3.x, Pydantic 2.x, pytest 8.x, Ubuntu 26.04 host Python standard
library, PostgreSQL, systemd, Caddy, UFW, OpenSSH, SOPS, Docker Buildx.

**Spec:** `docs/specs/2026-09-07-deployment-controller-reduction-design.md`

## Global Constraints

- Read the complete specification before implementing any task.
- Preserve all public commands: `build`, `provision`, `deploy`, `verify`, `releases`, `backups`,
  `backup`, `create-admin`, `cleanup`, `rollback`, and `restore`.
- Preserve the public exit categories unless a task explicitly documents a translation from the
  new four-outcome helper model.
- Use TDD: add a focused failing test, run it and observe the expected failure, implement the
  smallest coherent change, and rerun focused plus affected tests.
- Keep secrets out of logs, argv, helper payloads, exceptions, and reports. Secret-bearing files
  continue through bounded sensitive stdin and restrictive atomic installation.
- Preserve strict pinned-host-key SSH, destructive confirmation, authoritative-path containment,
  validated backup before migration or restore, atomic release selection, truthful coarse state,
  and the bounded transient helper.
- Recovery is replayable convergence: rerun, reconfirm newly dangerous work, automatically repeat
  or repair recognizable state, and return `manual` only for genuine ambiguity.
- Do not preserve compatibility with the never-deployed lifecycle, recovery, backup, or protocol
  metadata. Do not dynamically select between old and new production implementations.
- Use pyinfra declarative built-ins for stable, secret-free, independently observable, safely
  repeatable provisioning. Retain custom actions only for a documented availability, access,
  secret, or transaction boundary; wrapping custom shell in pyinfra is not simplification.
- An implicit deploy artifact is fresh only when verification proves an exact current clean source
  revision, application version, supported target, pinned toolchain, and builder identity match.
  Artifact age is not a freshness input. Explicit `--artifact` remains authoritative.
- The final tracked production-Python count must be reduced by at least 35% from the recorded
  21,128-line baseline. Do not meet the gate through minification, compressed expressions, or
  moving Python behavior into shell strings.
- Delete tests that enforce operation IDs, provisional records, exact stage histories, residue
  arrays, generated recovery actions, exact timeout stages, or unrealistic hostile state outside
  the retained safety floor.
- Keep each task green and independently reviewable. Request a scoped review after each task and a
  fresh whole-branch review after the final gates.
- Use repository-local Beads for task state. Use `but` for commits and other version-control
  mutations. Inspect `but diff` before each commit and pass selected file or hunk IDs whenever
  unrelated workspace changes exist. Do not run a real-host operation, deploy, push, merge, or
  publication without separate authorization.

---

## Intended File Ownership

The implementation should converge on the following ownership. Existing paths may remain when they
already have this single responsibility; do not rename a small cohesive file solely for symmetry.

```text
ops/taskman_ops/
  artifacts.py                       managed artifact discovery and exact-input resolution
  build.py                           immutable release construction and verification
  cli.py                             parsing, dispatch, and public result construction
  provisioning.py                    one programmatic pyinfra deployment
  remote.py                          strict SSH and bounded argv/file transport
  helper_runner.py                   transient helper upload, invocation, decode, cleanup
  host_protocol/
    envelope.py                      bounded request/result codecs and four outcomes
    operations.py                    finite helper operation vocabulary
  host_helper/
    __main__.py                      one-request dispatch
    paths.py                         two-root derivation and authoritative-path checks
    records.py                       completed release/backup/selection record codecs and writes
    state.py                         coherent HostState observation and ambiguity detection
    lock.py                          one lifecycle lock
    commands.py                      bounded subprocess and service/database capabilities
    verification.py                 service/release/database/readiness observation
    operations/
      discover.py                    verify, releases, and backups projections
      deploy.py                      deploy and genesis convergence
      backup.py                      validated backup convergence
      cleanup.py                     confirmed recognized-target deletion
      rollback.py                    compatible release selection
      restore.py                     validated temporary-database restore and swap
  workflows/
    helper.py                        common helper request and four-outcome translation
    deploy.py                        deploy planning, confirmation, public result
    backup.py                        backup public workflow
    cleanup.py                       cleanup planning and confirmation
    rollback.py                      rollback planning and confirmation
    restore.py                       restore planning and confirmation
    verify.py                        verification public result
    releases.py / backups.py         list projections
```

Delete `host_helper/lifecycle.py`, `host_helper/lifecycle_records.py`, and
`host_helper/runtime.py` after their final consumers move. Delete `workflows/helper_recovery.py`,
`workflows/helper_results.py`, `workflows/helper_deploy.py`, and
`workflows/helper_deploy_results.py` after `workflows/helper.py` owns the surviving behavior. Delete
`taskman_ops/pyinfra.py` only if the provisioning audit leaves no justified caller.

### Stable interfaces

The tasks below use these names consistently:

```python
@dataclass(frozen=True)
class ArtifactResolution:
    artifact: VerifiedArtifact
    source: Literal["explicit", "cached", "built"]

def resolve_deploy_artifact(
    repo: Path,
    supplied: Path | None,
    *,
    artifact_root: Path | None = None,
    builder: Callable[[Path, Path], VerifiedArtifact] = build_release,
) -> ArtifactResolution: ...

@dataclass(frozen=True)
class ReleaseRecord:
    release_id: str
    source_revision: str
    artifact_sha256: str
    migrations: tuple[Mapping[str, object], ...]

@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    dump_sha256: str
    source_release_id: str
    migration_versions: tuple[int, ...]
    source_database_size_bytes: int

@dataclass(frozen=True)
class SelectionRecord:
    release_id: str
    previous_release_id: str | None
    backup_id: str | None
    selected_at: datetime

@dataclass(frozen=True)
class HostState:
    selected_release_id: str | None
    releases: tuple[ReleaseRecord, ...]
    backups: tuple[BackupRecord, ...]
    selections: tuple[SelectionRecord, ...]
    applied_migrations: tuple[int, ...]
    service_state: Literal["running", "stopped", "failed", "unknown"]
    database_state: Literal["ready", "absent", "unknown"]
    temporary_paths: tuple[PurePosixPath, ...]
    warnings: tuple[str, ...]

def observe_host_state(
    paths: ManagedPaths,
    *,
    database: Mapping[str, object] | None = None,
    include_runtime: bool = False,
) -> HostState: ...

@dataclass(frozen=True)
class HostRequest:
    protocol_version: int
    operation: str
    correlation_id: str
    expected_state: Mapping[str, object]
    paths: Mapping[str, str]
    parameters: Mapping[str, object]

@dataclass(frozen=True)
class HostResult:
    protocol_version: int
    operation: str
    correlation_id: str
    outcome: Literal["succeeded", "refused", "retryable", "manual"]
    message: str
    state: Mapping[str, object]
    warnings: tuple[str, ...]

def run_request(remote: Remote, request: HostRequest) -> HostResult: ...
def result_error(result: HostResult) -> OpsError: ...
```

`correlation_id` is ephemeral transport correlation only. It is never persisted or used to name a
release, backup, temporary database, record, or recovery action.

---

### Task 1: Share build output and resolve fresh deploy artifacts

**Files:**

- Create: `ops/taskman_ops/artifacts.py`
- Create: `ops/tests/test_artifacts.py`
- Modify: `ops/taskman_ops/cli.py`
- Modify: `ops/taskman_ops/build.py`
- Modify: `ops/tests/test_cli.py`
- Modify: `ops/tests/test_build.py`

**Interfaces:**

- Produces: `ArtifactResolution` and `resolve_deploy_artifact(...)` as declared above.
- Consumes: `build_release`, `read_repository_state`, `read_application_version`,
  `verify_artifact`, and the pinned manifest constants.

- [ ] **Step 1: Write failing exact-input artifact-resolution tests**

  Add tests that create verified artifact triplets and assert cached reuse, cache miss building,
  source/application/target/toolchain/builder mismatch building, corrupt candidates being ignored,
  age being ignored, explicit artifact priority, and implicit resolution refusing an unclean or
  unidentified checkout. The primary test should follow this shape:

  ```python
  resolution = resolve_deploy_artifact(
      repo,
      None,
      artifact_root=artifact_root,
      builder=unexpected_builder,
  )

  assert resolution.source == "cached"
  assert resolution.artifact.manifest.source_revision == revision
  ```

- [ ] **Step 2: Run the artifact tests and observe the missing module failure**

  ```bash
  cd ops
  uv run pytest tests/test_artifacts.py -q
  ```

  Expected: collection fails because `taskman_ops.artifacts` does not exist.

- [ ] **Step 3: Implement the managed artifact resolver**

  Implement `ArtifactResolution` and `resolve_deploy_artifact`. For implicit selection, validate the
  checkout first, derive the expected release identity, scan only direct managed artifact
  directories, call `verify_artifact` for each complete archive/manifest/checksum triplet, and accept
  the first deterministic exact-input match. Verification already enforces pinned target, toolchain,
  and builder identity; compare source revision and application version explicitly. Ignore invalid
  cache entries without deleting them. Call `builder(repo, root)` only after no match remains.

- [ ] **Step 4: Route both public commands through the shared artifact root**

  Keep `build` calling `build_release` directly. Replace the deploy dispatch's local conditional
  with:

  ```python
  resolution = resolve_deploy_artifact(repo, invocation.artifact)
  artifact = resolution.artifact
  ```

  Include `artifact_source` (`explicit`, `cached`, or `built`) in the deploy workflow facts without
  changing its exit category. Do not connect to SSH until resolution succeeds.

- [ ] **Step 5: Run focused and complete operations tests**

  ```bash
  cd ops
  uv run pytest tests/test_artifacts.py tests/test_build.py tests/test_cli.py \
    tests/workflows/test_deploy.py -q
  uv run pytest -q
  ```

- [ ] **Step 6: Review and commit the artifact slice**

  Inspect `but diff`, request scoped review, address findings, and commit only this task's files:

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Reuse fresh deployment artifacts"
  ```

---

### Task 2: Replace lifecycle storage with completed records and one observed state

**Files:**

- Create: `ops/taskman_ops/host_helper/records.py`
- Create: `ops/taskman_ops/host_helper/state.py`
- Create: `ops/taskman_ops/host_helper/lock.py`
- Create: `ops/tests/host_helper/test_records.py`
- Create: `ops/tests/host_helper/test_state.py`
- Modify: `ops/taskman_ops/host_helper/paths.py`
- Modify: `ops/tests/host_helper/test_lifecycle.py`

**Interfaces:**

- Produces: `ReleaseRecord`, `BackupRecord`, `SelectionRecord`, `HostState`, and
  `observe_host_state(...)` as declared above; also:

  ```python
  @contextmanager
  def lifecycle_lock(paths: ManagedPaths, timeout_seconds: float) -> Iterator[None]: ...

  def write_release_manifest(paths: ManagedPaths, record: ReleaseRecord) -> None: ...
  def write_backup_manifest(paths: ManagedPaths, record: BackupRecord) -> None: ...
  def append_selection(paths: ManagedPaths, record: SelectionRecord) -> None: ...
  ```

- Consumes: `ManagedPaths` and the existing identifier, manifest, path-containment, service, and
  PostgreSQL observation primitives.

- [ ] **Step 1: Write failing completed-record codec tests**

  Test exact schemas, bounded JSON size, identifier validation, authoritative derived locations,
  restrictive ownership/modes where they establish path authority, and atomic completed writes.
  Explicitly assert there are no pending, provisional, finalization, recovery, or operation-ID
  fields.

- [ ] **Step 2: Write failing HostState observation tests**

  Cover an empty host, one selected release, multiple completed releases/backups/selections,
  recognizable deterministic temporary artifacts, warnings for irrelevant unknown entries, and
  refusal for ambiguous authoritative symlinks, duplicate identities, or contradictory selection
  records. Do not reproduce arbitrary hostile-tree combinations.

- [ ] **Step 3: Run the new state tests and observe missing-interface failures**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_records.py tests/host_helper/test_state.py -q
  ```

- [ ] **Step 4: Implement completed record codecs and atomic writes**

  Keep schemas flat and operation-independent. A release manifest lives in its immutable release;
  a backup manifest lives beside its validated dump; each successful selection is a create-once
  record. Use same-directory temporary files plus `os.replace` only where replacement is required.
  Do not add version migration or compatibility readers.

- [ ] **Step 5: Implement one lifecycle lock and HostState observer**

  Use one exclusive lock file derived from `install_root`. Observation occurs while locked for
  mutations and returns only fields consumed by commands. Map ordinary unknown non-authoritative
  entries to bounded warnings; raise one state-ambiguity error for authoritative contradictions.

- [ ] **Step 6: Remove superseded lifecycle tests without deleting production consumers yet**

  Reduce `test_lifecycle.py` to the retained lock contention, path authority, completed-write, and
  rollback-compatibility expectations. Delete tests dedicated to journals, operation replay,
  provisional publication, finalization effects, tree digests, or exhaustive residue evidence.

- [ ] **Step 7: Run state, path, and architecture tests**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_records.py tests/host_helper/test_state.py \
    tests/host_helper/test_lifecycle.py tests/host_helper/test_discovery.py \
    tests/test_simplification_contract.py -q
  uv run pytest -q
  ```

- [ ] **Step 8: Review and commit the completed-state slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Add completed deployment state"
  ```

---

### Task 3: Collapse the helper protocol and controller translation

**Files:**

- Rewrite: `ops/taskman_ops/host_protocol/envelope.py`
- Modify: `ops/taskman_ops/host_protocol/__init__.py`
- Modify: `ops/taskman_ops/host_protocol/identifiers.py`
- Modify: `ops/taskman_ops/host_protocol/operations.py`
- Modify: `ops/taskman_ops/host_helper/__main__.py`
- Create: `ops/taskman_ops/workflows/helper.py`
- Simplify: `ops/taskman_ops/helper_runner.py`
- Simplify: `ops/taskman_ops/remote.py`
- Delete: `ops/taskman_ops/workflows/helper_recovery.py`
- Delete: `ops/taskman_ops/workflows/helper_results.py`
- Delete: `ops/taskman_ops/workflows/helper_deploy.py`
- Delete: `ops/taskman_ops/workflows/helper_deploy_results.py`
- Rewrite: `ops/tests/host_protocol/test_envelope.py`
- Modify: `ops/tests/host_protocol/test_operations.py`
- Modify: `ops/tests/host_helper/test_entrypoint.py`
- Rewrite: `ops/tests/test_helper_runner.py`
- Modify: `ops/tests/test_remote.py`
- Modify: `ops/tests/test_dry_run.py`
- Modify: `ops/tests/test_simplification_contract.py`
- Modify: `ops/tests/workflows/test_backup.py`
- Modify: `ops/tests/workflows/test_cleanup.py`
- Modify: `ops/tests/workflows/test_deploy.py`
- Modify: `ops/tests/workflows/test_deploy_transaction.py`
- Modify: `ops/tests/workflows/test_discovery.py`
- Modify: `ops/tests/workflows/test_helper_deploy_transaction.py`
- Modify: `ops/tests/workflows/test_helper_read_only.py`
- Modify: `ops/tests/workflows/test_restore.py`
- Modify: `ops/tests/workflows/test_rollback.py`

**Interfaces:**

- Produces: the final `HostRequest`, `HostResult`, `run_request`, and `result_error` interfaces
  declared above for every subsequent command slice.
- Consumes: the finite helper operation vocabulary and the `HostState` projections introduced in
  Task 2.

- [ ] **Step 1: Write the final bounded protocol tests**

  Require exactly request fields `protocol_version`, `operation`, `correlation_id`, `expected_state`,
  `paths`, and `parameters`; require exactly result fields `protocol_version`, `operation`,
  `correlation_id`, `outcome`, `message`, `state`, and `warnings`. Test only `succeeded`, `refused`,
  `retryable`, and `manual`; bounded bytes, nesting, collections, UTF-8, duplicate keys, finite
  operations, and exact correlation remain strict.

- [ ] **Step 2: Run protocol tests and observe old-envelope failures**

  ```bash
  cd ops
  uv run pytest tests/host_protocol tests/host_helper/test_entrypoint.py \
    tests/test_helper_runner.py -q
  ```

- [ ] **Step 3: Replace the protocol without a compatibility decoder**

  Increment `PROTOCOL_VERSION`, rename transport-only `operation_id` to `correlation_id`, replace
  evidence fields with `message`, `state`, and `warnings`, and enforce the four outcomes. Do not
  accept the old schema or aliases. Update every operation constructor in the same slice; retain its
  current business procedure until the command-specific task replaces it.

- [ ] **Step 4: Replace workflow helper modules with one translator**

  `run_request` packages/invokes the helper and validates protocol, operation, and correlation.
  `result_error` maps `refused`, `retryable`, and `manual` using the operation and concise final
  state; it never rebuilds transaction stages. Keep every existing `ExitStatus` member. Use
  `LOCKED` only when state says the lifecycle lock was unavailable, `BACKUP` for backup failures,
  `RESTORE` for restore failures, `READINESS` for failed verification, `SAFETY` for refusals and
  cleanup ambiguity, and `RELEASE` for other deployment/rollback/genesis failures. Use `MIGRATION`
  only when the observed applied-migration state directly identifies migration as the failed
  consequential boundary. Local prerequisite, secret, and remote-preflight failures continue to
  arise at their controller-owned boundaries. Each public workflow validates only the state fields
  it actually exposes.

- [ ] **Step 5: Reduce helper runner and remote transport**

  Keep strict host identity, argv validation, sensitive stdin, bounded stdout/stderr, upload
  checksum, root-owned transient execution, and best-effort cleanup warnings. Remove exact remote
  instruction attribution, retained operation artifacts, residue aggregation, generated recovery
  commands, and nested deadline budgets not required at an external-call boundary.

- [ ] **Step 6: Delete obsolete translators and update fixtures**

  Remove old helper modules only after `rg` finds no production imports. Rewrite the listed fixture
  factories to construct the final result shape. Delete assertions whose sole expectation is an old
  evidence field or exact internal stage.

- [ ] **Step 7: Run protocol, transport, workflow, redaction, and full tests**

  ```bash
  cd ops
  uv run pytest tests/host_protocol tests/host_helper/test_entrypoint.py \
    tests/test_helper_runner.py tests/test_remote.py tests/test_secrets.py \
    tests/workflows -q
  uv run pytest -q
  ```

- [ ] **Step 8: Review and commit the protocol/controller slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Reduce helper outcomes and transport"
  ```

---

### Task 4: Migrate verification and listing commands to HostState

**Files:**

- Modify: `ops/taskman_ops/host_helper/operations/discover.py`
- Modify: `ops/taskman_ops/host_helper/verification.py`
- Modify: `ops/taskman_ops/workflows/verify.py`
- Modify: `ops/taskman_ops/workflows/releases.py`
- Modify: `ops/taskman_ops/workflows/backups.py`
- Rewrite: `ops/tests/host_helper/test_discovery.py`
- Modify: `ops/tests/host_helper/test_verification.py`
- Rewrite: `ops/tests/workflows/test_helper_read_only.py`
- Modify: `ops/tests/workflows/test_verify.py`
- Modify: `ops/tests/workflows/test_discovery.py`

**Interfaces:**

- Consumes: `observe_host_state(...)`, `HostState`, and completed records from Task 2.
- Produces: read-only helper results containing concise `state` projections used by `verify`,
  `releases`, and `backups`.

- [ ] **Step 1: Replace read-only fixtures with HostState outcomes**

  Write failing tests for empty/healthy/failed verification, release rows, backup rows, bounded
  warnings, lock contention, and authoritative ambiguity. Assert observable rows and checks, not
  internal lifecycle categories or exact stage names.

- [ ] **Step 2: Run the read-only tests and observe old lifecycle-shape failures**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_discovery.py \
    tests/host_helper/test_verification.py tests/workflows/test_helper_read_only.py \
    tests/workflows/test_verify.py tests/workflows/test_discovery.py -q
  ```

- [ ] **Step 3: Project verify, releases, and backups directly from HostState**

  Acquire the shared lock only long enough to obtain a coherent snapshot. Keep actual health,
  listener, selected-release, and database checks, but remove lifecycle-record validation,
  operation-recovery interpretation, inventory residue, and exact timeout-stage reporting.

- [ ] **Step 4: Simplify controller-side read result validation**

  Validate required row/check fields and bounded types. Do not reconstruct helper lifecycle state or
  reject harmless additional warnings. Preserve existing public `WorkflowResult` facts and exit
  categories.

- [ ] **Step 5: Run focused and full tests**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_discovery.py \
    tests/host_helper/test_verification.py tests/workflows/test_helper_read_only.py \
    tests/workflows/test_verify.py tests/workflows/test_discovery.py -q
  uv run pytest -q
  ```

- [ ] **Step 6: Review and commit the read-only slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Read deployment state directly"
  ```

---

### Task 5: Audit provisioning and prefer pyinfra built-ins

**Files:**

- Modify: `ops/taskman_ops/provisioning.py`
- Modify: `ops/taskman_ops/host/baseline.py`
- Modify: `ops/taskman_ops/host/firewall.py`
- Modify: `ops/taskman_ops/services/caddy.py`
- Modify: `ops/taskman_ops/services/postgresql.py`
- Modify: `ops/taskman_ops/services/systemd.py`
- Delete if unused: `ops/taskman_ops/pyinfra.py`
- Modify: `ops/tests/test_pyinfra.py`
- Modify: `ops/tests/host/test_baseline.py`
- Modify: `ops/tests/host/test_firewall.py`
- Modify: `ops/tests/services/test_caddy.py`
- Modify: `ops/tests/services/test_postgresql.py`
- Modify: `ops/tests/services/test_systemd.py`
- Modify: `ops/tests/workflows/test_provision.py`

**Interfaces:**

- Produces: the existing `taskman_provisioning(*, inputs)` and
  `converge_provisioning(remote, inputs) -> ChangeSet` with less custom policy.
- Consumes: pyinfra `apt`, `files`, `server`, `systemd`, and eligible `postgres` operations.

- [ ] **Step 1: Add tests that state the approved pyinfra boundary**

  Assert ordinary package, repository, user, directory, non-secret file, ownership/mode, unit,
  daemon-reload, enablement, and service convergence uses built-ins. Assert retained custom actions
  are limited to Caddy pre-activation validation, UFW plus fresh strict SSH verification,
  PostgreSQL cluster/HBA transitions not represented safely by built-ins, and secret-bearing
  credential installation.

- [ ] **Step 2: Run provisioning tests and record current custom callers**

  ```bash
  cd ops
  uv run pytest tests/test_pyinfra.py tests/host tests/services \
    tests/workflows/test_provision.py -q
  rg -n "conditional_convergence|@operation|server\.shell" taskman_ops/host \
    taskman_ops/services taskman_ops/provisioning.py
  ```

- [ ] **Step 3: Replace eligible probes and conditional wrappers with built-ins**

  Let built-ins repair ordinary drift. Remove duplicate ownership/mode/package/service probes and
  bespoke changed-marker parsing. Use `systemd.service` and `files.put` result predicates only where
  a later built-in genuinely depends on a changed unit or validated configuration.

- [ ] **Step 4: Minimize and document the remaining custom actions**

  Keep Caddy validate-before-install, UFW ordered enablement followed by a fresh pinned-host-key
  connection, PostgreSQL native cluster/HBA selection, and sensitive-stdin credential writes.
  Reduce each to one direct action with ordinary failure; do not retain a general custom convergence
  framework solely to share marker parsing. Use `postgres.database` or `postgres.role` only when
  their behavior covers the existing-state and secret requirements.

- [ ] **Step 5: Delete the generic wrapper if no justified action needs it**

  Remove `taskman_ops/pyinfra.py` and its wrapper-specific tests when direct local actions suffice.
  If one caller remains, record its exact safety justification in the Beads task and keep the module
  limited to that capability rather than a generic policy engine.

- [ ] **Step 6: Run focused, full, and executable-string measurements**

  ```bash
  cd ops
  uv run pytest tests/test_pyinfra.py tests/host tests/services \
    tests/workflows/test_provision.py -q
  uv run python scripts/measure_controller.py ..
  uv run pytest -q
  ```

- [ ] **Step 7: Review and commit the provisioning slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Prefer pyinfra provisioning primitives"
  ```

---

### Task 6: Replace backup and cleanup journals with replayable procedures

**Files:**

- Create: `ops/taskman_ops/host_helper/commands.py`
- Create: `ops/tests/host_helper/test_commands.py`
- Rewrite: `ops/taskman_ops/host_helper/operations/backup.py`
- Rewrite: `ops/taskman_ops/host_helper/operations/cleanup.py`
- Modify: `ops/taskman_ops/workflows/backup.py`
- Modify: `ops/taskman_ops/workflows/cleanup.py`
- Rewrite: `ops/tests/host_helper/test_backup.py`
- Rewrite: `ops/tests/host_helper/test_cleanup.py`
- Modify: `ops/tests/workflows/test_backup.py`
- Modify: `ops/tests/workflows/test_cleanup.py`

**Interfaces:**

- Consumes: `HostState`, completed records, lifecycle lock, deterministic Taskman-owned temporary
  names, bounded commands, and authoritative-path validation.
- Produces:

  ```python
  def run_command(
      argv: tuple[str, ...],
      *,
      stdin: bytes | None = None,
      env: Mapping[str, str] | None = None,
      timeout_seconds: float,
  ) -> subprocess.CompletedProcess[bytes]: ...

  def create_validated_backup(
      state: HostState,
      paths: ManagedPaths,
      database: Mapping[str, object],
      credentials: Path,
      *,
      purpose: str,
  ) -> BackupRecord: ...

  def backup(request: HostRequest) -> HostResult: ...
  def cleanup(request: HostRequest) -> HostResult: ...
  ```

- [ ] **Step 1: Write bounded command and consequence-boundary interruption tests**

  Cover argv-only execution, optional protected stdin, one subprocess timeout, bounded captured
  output, and redacted failure. For backup, interrupt after dump creation, validation, publication,
  and manifest write; rerun must remove/repeat or reuse recognizable work and finish automatically.
  For cleanup, interrupt between confirmed target deletions; rerun must replan from remaining
  recognized targets and require a new confirmation only when the dangerous target set changed.

- [ ] **Step 2: Add retained safety tests**

  Cover secret-safe PostgreSQL invocation, dump validation before publication, same-root atomic
  publication, exact cleanup containment, selected/in-use artifact protection, and refusal for an
  ambiguous authoritative target. Remove pending-publication, residue-array, tree-digest,
  per-target-recoverability, and generated-recovery-action assertions.

- [ ] **Step 3: Run backup and cleanup tests and observe failures against old machinery**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_commands.py tests/host_helper/test_backup.py \
    tests/host_helper/test_cleanup.py tests/workflows/test_backup.py \
    tests/workflows/test_cleanup.py -q
  ```

- [ ] **Step 4: Implement the direct backup procedure**

  Under the one lock: observe, normalize the deterministic incomplete dump, dump, validate, atomically
  publish, write the completed manifest, re-observe, and return success. Treat a recognizable
  interruption as `retryable`; return `manual` only when an authoritative dump/manifest identity is
  contradictory. Do not persist pending state.

- [ ] **Step 5: Implement the direct cleanup procedure**

  Inspection returns exact recognized stale targets. Execution rechecks only current selection and
  exact target identity, deletes confined targets, tolerates already-absent targets, and returns the
  final observed state. Do not compute tree digests or narrate individual residue recovery.

- [ ] **Step 6: Simplify public workflow translation and run all tests**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_commands.py tests/host_helper/test_backup.py \
    tests/host_helper/test_cleanup.py tests/workflows/test_backup.py \
    tests/workflows/test_cleanup.py -q
  uv run pytest -q
  ```

- [ ] **Step 7: Review and commit the backup/cleanup slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Replay backup and cleanup safely"
  ```

---

### Task 7: Replace deploy and genesis with one convergent procedure

**Files:**

- Rewrite: `ops/taskman_ops/host_helper/operations/deploy.py`
- Modify: `ops/taskman_ops/host_helper/verification.py`
- Modify: `ops/taskman_ops/workflows/deploy.py`
- Modify: `ops/taskman_ops/workflows/provision.py`
- Rewrite: `ops/tests/host_helper/test_deploy.py`
- Rewrite: `ops/tests/workflows/test_deploy_transaction.py`
- Modify: `ops/tests/workflows/test_deploy.py`
- Modify: `ops/tests/workflows/test_provision.py`
- Modify: `ops/tests/test_dry_run.py`

**Interfaces:**

- Consumes: `HostState`, completed records, `create_validated_backup`, lifecycle lock, atomic symlink
  selection, artifact manifest, and verification capability.
- Produces:

  ```python
  def converge_deployment(request: HostRequest, *, first_release: bool = False) -> HostResult: ...
  def deploy(request: HostRequest) -> HostResult: ...
  def genesis(request: HostRequest) -> HostResult: ...
  ```

- [ ] **Step 1: Write failing outcome and rerun-convergence tests**

  Cover success, already-selected no-op, first release, artifact staging interruption, backup
  interruption, migration interruption, selection interruption, restart/readiness interruption,
  and lost-result rerun. Every recognizable state must converge when the same command is rerun, even
  if staging, backup, migration checks, or service actions repeat.

- [ ] **Step 2: Add migration safety and ambiguity tests**

  Prove a validated pre-migration backup exists before migration, applied migration versions are
  observed after lost output, the same candidate proceeds forward when compatible, an older current
  release is not restarted against a known incompatible migrated schema, and contradictory
  release/schema identity returns `manual` without guessing.

- [ ] **Step 3: Run deploy tests and observe old journal/stage expectations fail**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_deploy.py tests/workflows/test_deploy.py \
    tests/workflows/test_deploy_transaction.py tests/workflows/test_provision.py \
    tests/test_dry_run.py -q
  ```

- [ ] **Step 4: Implement one explicit deployment procedure**

  Under the lock: observe and normalize the deterministic staging directory; validate/extract or
  reuse the immutable release; create a validated backup when migrations change; stop; run the
  candidate's forward migrations; observe applied versions; atomically replace `current`; start;
  verify; append one successful selection; return final state. Skip satisfied outcomes and repeat
  safe work. Do not use a configurable runtime, stage journal, operation ID, provisional record, or
  compensation state machine.

- [ ] **Step 5: Make genesis a parameterized entry into the same procedure**

  `genesis(request)` calls `converge_deployment(request, first_release=True)`. Keep its empty-host
  precondition and restore-required migration policy, but do not maintain separate transaction
  stages or records.

- [ ] **Step 6: Simplify deploy/provision controller workflows**

  Keep plan presentation, confirmation, migration-policy validation, dry-run, and public facts.
  Remove parsing of changed stages, resume planes, provisional publication, residue, and generated
  recovery actions. Provisioning genesis must use the same release procedure after pyinfra and
  secret/database convergence.

- [ ] **Step 7: Run focused, full, and metric gates**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_deploy.py tests/workflows/test_deploy.py \
    tests/workflows/test_deploy_transaction.py tests/workflows/test_provision.py \
    tests/test_dry_run.py -q
  uv run python scripts/measure_controller.py ..
  uv run pytest -q
  ```

- [ ] **Step 8: Review and commit the deployment slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Converge deployments on rerun"
  ```

---

### Task 8: Replace rollback and restore with observed-state procedures

**Files:**

- Rewrite: `ops/taskman_ops/host_helper/operations/rollback.py`
- Rewrite: `ops/taskman_ops/host_helper/operations/restore.py`
- Modify: `ops/taskman_ops/workflows/rollback.py`
- Modify: `ops/taskman_ops/workflows/restore.py`
- Rewrite: `ops/tests/host_helper/test_rollback.py`
- Rewrite: `ops/tests/host_helper/test_restore.py`
- Modify: `ops/tests/workflows/test_rollback.py`
- Modify: `ops/tests/workflows/test_restore.py`

**Interfaces:**

- Consumes: `HostState`, selection history, validated backup records, lifecycle lock,
  `create_validated_backup`, deterministic temporary database naming independent of correlation ID,
  atomic selection, and verification.
- Produces: direct `rollback(request) -> HostResult` and `restore(request) -> HostResult` procedures.

- [ ] **Step 1: Write rollback consequence-boundary tests**

  Cover plan/confirmation, validated safety backup, compatible target selection, interruption before
  and after selection, restart/verification interruption, already-completed rerun, and refusal when
  successful selection history cannot prove compatibility. Do not test reverse migrations because
  rollback never performs them.

- [ ] **Step 2: Write restore consequence-boundary tests**

  Cover source dump validation, fresh safety backup, deterministic temporary database restore,
  restored-database validation, database swap, compatible release selection, restart/verification,
  and rerun after each consequential boundary. Recognizable source/temp/live arrangements converge;
  contradictory identities return `manual`.

- [ ] **Step 3: Run rollback/restore tests and observe failures against recovery records**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_rollback.py tests/host_helper/test_restore.py \
    tests/workflows/test_rollback.py tests/workflows/test_restore.py -q
  ```

- [ ] **Step 4: Implement direct rollback**

  Observe, validate target and successful-history compatibility, ensure a fresh validated backup,
  stop, atomically select, start, verify, append selection, and return final state. On rerun, tolerate
  the target already selected and repeat safe service/verification work. Remove selection recovery
  directories and recovery-action synthesis.

- [ ] **Step 5: Implement direct restore**

  Observe, validate source backup and capacity, create the safety backup, restore and validate a
  deterministic temporary database, swap using a small explicit set of recognizable database
  arrangements, select the compatible release, start, verify, append selection, and remove safe
  temporary state. Do not write a restore journal. Return `manual` when database identity is not one
  of the modeled interruption states.

- [ ] **Step 6: Simplify controller plans and outcome translation**

  Retain exact target/backup confirmation and the public result's target, selected release,
  database, service, and next-action facts. Remove recovery IDs, exact changed stages, residue paths,
  recoverability vectors, and generated shell instructions.

- [ ] **Step 7: Run focused, full, and metric gates**

  ```bash
  cd ops
  uv run pytest tests/host_helper/test_rollback.py tests/host_helper/test_restore.py \
    tests/workflows/test_rollback.py tests/workflows/test_restore.py -q
  uv run python scripts/measure_controller.py ..
  uv run pytest -q
  ```

- [ ] **Step 8: Review and commit the rollback/restore slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Replay rollback and restore safely"
  ```

---

### Task 9: Delete the old transaction core and enforce the reduction

**Files:**

- Delete: `ops/taskman_ops/host_helper/lifecycle.py`
- Delete: `ops/taskman_ops/host_helper/lifecycle_records.py`
- Delete: `ops/taskman_ops/host_helper/runtime.py`
- Delete: `ops/taskman_ops/host_helper/facts.py`
- Modify: `ops/taskman_ops/host_helper/__init__.py`
- Modify: `ops/tests/test_architecture.py`
- Rewrite: `ops/tests/test_simplification_contract.py`
- Delete: obsolete portions of `ops/tests/host_helper/test_lifecycle.py`
- Delete: obsolete operation-ID/stage/residue tests across `ops/tests/`
- Modify: `ops/scripts/measure_controller.py` only if reporting needs a non-behavioral correction

**Interfaces:**

- Consumes: all final interfaces from Tasks 1–8.
- Produces: one implementation per capability and at least a 35% production-Python reduction from
  the 21,128-line baseline.

- [ ] **Step 1: Add architecture tests for deleted concepts**

  Fail on production imports or identifiers for `LifecycleStore`, `LifecycleRecords`,
  `TransactionRuntime`, pending/provisional/finalization/recovery records, durable operation IDs,
  changed-stage histories, residue arrays, and generated recovery actions. Keep scans specific
  enough not to reject documentation or ordinary words in user-facing messages.

- [ ] **Step 2: Run architecture tests and observe legacy-module failures**

  ```bash
  cd ops
  uv run pytest tests/test_architecture.py tests/test_simplification_contract.py -q
  ```

- [ ] **Step 3: Delete all legacy production modules and imports**

  Use `rg` before each deletion. Fold only still-used small safety capabilities into `records.py`,
  `state.py`, `lock.py`, `commands.py`, or the owning operation. Do not preserve unused adapters for
  old tests and do not replace the deleted framework with another generic workflow abstraction.

- [ ] **Step 4: Delete obsolete tests and strengthen public behavior coverage**

  The simplification contract must cover command names, exit categories, confirmation, dry-run,
  recursive redaction, authoritative paths, backup-before-consequence, atomic selection, the four
  outcomes, exact-input artifact reuse, and rerun convergence at consequential boundaries. It must
  not require detailed terminal recovery evidence.

- [ ] **Step 5: Measure and enforce the percentage gate**

  ```bash
  cd ops
  uv run python scripts/measure_controller.py ..
  ```

  Calculate the reduction from the recorded 21,128-line baseline and confirm it is at least 35%.
  If it is smaller, list the largest remaining modules and remove avoidable policy, compatibility,
  duplicate validation, or evidence machinery. If reaching the gate would weaken the safety floor
  or a command's main responsibility, stop and return to design rather than compressing code.

- [ ] **Step 6: Run complete local Python gates**

  ```bash
  cd ops
  uv sync --locked
  uv run python -m compileall -q taskman_ops tests
  uv run pytest -q
  uv run python scripts/measure_controller.py ..
  ```

- [ ] **Step 7: Review and commit the deletion slice**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Delete deployment transaction machinery"
  ```

---

### Task 10: Update operator documentation and run final verification

**Files:**

- Modify: `docs/deployment.md`
- Modify: `docs/README.md`
- Modify: `README.md` if command behavior is summarized there
- Modify: `ops/environments/example.yaml` only if a documented field changed
- Modify: `docs/handoffs/deployment-controller-simplification.md`
- Modify: `.beads/issues.jsonl` through `br` only

**Interfaces:**

- Consumes: the complete implementation and all prior verification evidence.
- Produces: accurate runbook, final reduction report, fresh review evidence, and delivery state ready
  for separately authorized real-host acceptance.

- [ ] **Step 1: Update the deployment runbook**

  Document exact-input artifact reuse, implicit build on cache miss, explicit artifact priority,
  pyinfra's built-in-first boundary and justified custom actions, completed-only state, rerun-based
  automatic recovery, coarse outcomes, and the circumstances that require manual intervention.
  Remove operator instructions for operation IDs, journals, residue arrays, or generated recovery
  commands.

- [ ] **Step 2: Run documentation and repository checks**

  Verify all local Markdown links resolve, all public command/help examples match parser output, and
  no removed internal terminology remains in current operator documentation. Use `rg` searches with
  an explicit allowlist for historical specs and plans.

- [ ] **Step 3: Run complete local gates**

  ```bash
  cd ops
  uv sync --locked
  uv run python -m compileall -q taskman_ops tests
  uv run pytest -q
  uv run python scripts/measure_controller.py ..
  cd ..
  bash -n ops/taskman ops/backup/taskman-backup ops/caddy/render-caddyfile
  mix precommit
  git diff --check
  ```

- [ ] **Step 4: Run the pinned release builder**

  Build and verify the `linux/amd64` artifact. Confirm its manifest records builder tag
  `ubuntu:resolute-20260811.1` and digest
  `sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b`.
  Confirm a second plain deploy resolution reuses the exact-input artifact without rebuilding; do
  not connect to or mutate a real host during this local check.

- [ ] **Step 5: Record final evidence in Beads**

  Record before/after production and test lines, percentage reduction, executable-string count,
  largest remaining modules, deleted concepts/files/tests, retained custom provisioning actions and
  their justifications, focused/full test counts, builder identity, and review outcome. State
  explicitly that real-host acceptance remains unperformed unless separately authorized.

- [ ] **Step 6: Request fresh whole-branch review**

  Require the reviewer to map every public command and safety-floor item to tests, inspect the
  replayable failure cases, verify the pyinfra boundary, reproduce the line metric, and report
  Critical and Important findings. Resolve findings and rerun affected plus complete gates.

- [ ] **Step 7: Update the handoff and Beads statuses**

  Close completed child tasks. Keep the parent delivery feature open until the operator accepts the
  intended branch state and separately authorizes any real-host acceptance or integration action.

- [ ] **Step 8: Commit the verified documentation and evidence**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Document reduced deployment controller"
  ```

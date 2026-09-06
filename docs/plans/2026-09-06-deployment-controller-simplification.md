# Deployment Controller Simplification Implementation Plan

**Status:** Approved

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or
> executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Replace Taskman's duplicated controller-side host programs with one bounded transient
helper, make pyinfra the real provisioning engine, and reduce configuration and implementation
complexity while preserving material deployment guarantees.

**Architecture:** The workstation controller retains operator interaction, secrets, artifact
building, SSH trust, and pyinfra convergence. A deterministic standard-library zipapp owns coherent
host discovery and stateful lifecycle/database transactions through one versioned JSON protocol.
Operation policies remain explicit; only transport, locking, discovery, and transaction evidence
mechanics are shared.

**Tech Stack:** Python 3.12 controller, Ubuntu 26.04 host Python, pyinfra 3.x, Pydantic 2.x, pytest
8.x, SOPS, OpenSSH, systemd, PostgreSQL, Caddy, Docker Buildx.

**Spec:** `docs/specs/2026-09-06-deployment-controller-simplification-design.md`

## Global Constraints

- Read the complete specification before implementing any task.
- Use TDD: demonstrate a focused failing test before changing production behavior.
- Built-in pyinfra operations are the default for ordinary convergence. Custom operations require
  a written concrete material-risk justification in the task's review notes.
- Preserve credential redaction, strict pinned-host-key SSH, confirmation-before-mutation,
  under-lock revalidation, backup-before-migration, immutable releases, exact cleanup authority,
  and truthful recovery evidence.
- The helper uses only the Ubuntu standard library, opens no listener, and leaves no installed
  agent or controller package after successful execution.
- Controller validation checks request correlation and required result invariants; it must not
  duplicate helper discovery or transaction policy.
- Only `install_root` and `backup_root` are configurable. There are no compatibility aliases or
  migration behavior for removed root fields.
- Do not dynamically select between old and new production implementations. Temporary parity
  seams are deleted in the task that completes their replacement.
- Do not use pyinfra prepare-time branches on facts changed by earlier operations in the same
  deploy.
- Keep secrets out of helper JSON, command strings, logs, exceptions, and rendered output.
- A real-host run, deployment, push, force-push, merge, or publication requires separate operator
  authorization.
- Run scoped independent review after every task. Run the full operations suite at each task
  boundary where the branch is expected to be green.

---

## Intended File Ownership

The final implementation should converge on these responsibilities:

```text
ops/taskman_ops/
  config.py                         validated operator configuration and derived paths
  manifests.py                      release artifact schema and provenance
  provisioning.py                   one packaged programmatic pyinfra deploy
  remote.py                         strict SSH connector and bounded argv/file transport
  helper_package.py                 deterministic allowlisted zipapp construction
  helper_runner.py                  upload, checksum, invoke, decode, and cleanup
  host_protocol/
    __init__.py                     public protocol exports
    envelope.py                     bounded HostRequest/HostResult parsing and encoding
    identifiers.py                  protocol identifiers and absolute-path validation
    operations.py                   operation-specific payload validation
  host_helper/
    __main__.py                     bounded stdin/stdout entry point and dispatch
    runtime.py                      lock, stages, cleanup, and terminal evidence
    paths.py                        derived-root and exact-authority checks
    facts.py                        coherent host and lifecycle discovery
    lifecycle.py                    record storage, adoption, staging, and activation
    verification.py                 local service/release verification
    operations/
      discover.py
      deploy.py
      backup.py
      rollback.py
      restore.py
      cleanup.py
  workflows/                        plans, confirmation, helper requests, result translation
```

Delete superseded modules only after their consumers have moved. A module may remain under its
current path when it already has one clear responsibility; the target tree is an ownership map,
not a requirement to rename code without benefit.

### Stable interfaces introduced by this plan

```python
@dataclass(frozen=True)
class HostRequest:
    protocol_version: int
    operation: str
    operation_id: str
    expected_state: Mapping[str, object]
    paths: Mapping[str, str]
    parameters: Mapping[str, object]

@dataclass(frozen=True)
class HostResult:
    protocol_version: int
    operation: str
    operation_id: str
    outcome: str
    stage: str
    changed_stages: tuple[str, ...]
    lifecycle: Mapping[str, object]
    runtime_state: Mapping[str, object]
    verification: Mapping[str, object]
    residue_paths: tuple[str, ...]
    recovery_actions: tuple[str, ...]
    warnings: tuple[str, ...]

def encode_request(request: HostRequest) -> bytes: ...
def decode_request(payload: bytes) -> HostRequest: ...
def encode_result(result: HostResult) -> bytes: ...
def decode_result(payload: bytes) -> HostResult: ...

@dataclass(frozen=True)
class HelperPackage:
    path: Path
    sha256: str
    protocol_version: int

def build_helper_package(destination: Path) -> HelperPackage: ...

@dataclass(frozen=True)
class HelperInvocation:
    result: HostResult
    cleanup_warning: str | None = None

def invoke_helper(
    remote: Remote,
    package: HelperPackage,
    request: HostRequest,
) -> HelperInvocation: ...

@dataclass(frozen=True)
class ProvisioningInputs:
    config: EnvironmentConfig
    runtime_environment: bytes
    pgpass: bytes

def converge_provisioning(
    remote: PyinfraRemote,
    inputs: ProvisioningInputs,
    *,
    dry_run: bool = False,
) -> ChangeSet: ...

def summarize_deploy(state: State) -> ChangeSet: ...
```

## Task 1: Freeze the Baseline and Classify Guarantees

**Files:**

- Create: `ops/tests/test_simplification_contract.py`
- Create: `ops/scripts/measure_controller.py`
- Modify: `docs/handoffs/deployment-controller-simplification.md`
- Test: existing CLI, configuration, workflow, redaction, and failure-injection suites

**Interfaces:**

- Consumes: current public CLI and `WorkflowResult` behavior.
- Produces: reproducible baseline metrics and a test matrix distinguishing material guarantees
  from intentionally relaxable implementation details.

- [ ] **Step 1: Add a baseline contract test**

  Add parameterized assertions covering command names, exit categories, confirmation cancellation,
  dry-run non-mutation, recursive redaction, and required terminal evidence for deploy, genesis,
  rollback, restore, and cleanup. Reference existing fixtures rather than copying remote programs.

  ```python
  @pytest.mark.parametrize(
      ("command", "required_facts"),
      [
          ("deploy", {"release_id", "service_state", "database_state"}),
          ("rollback", {"release_id", "service_state", "database_state"}),
          ("restore", {"backup_id", "service_state", "database_state"}),
      ],
  )
  def test_failed_mutation_retains_required_recovery_facts(command, required_facts):
      result = load_recorded_failure_fixture(command)
      assert required_facts <= result.facts.keys()
      assert result.next_action
  ```

  Define `load_recorded_failure_fixture(command: str) -> WorkflowResult` in the same test module by
  invoking the existing workflow failure fixtures; it must not construct an expected result by
  duplicating production policy.

- [ ] **Step 2: Run the focused contract test and confirm any missing fixture fails**

  Run from `ops/`:

  ```bash
  uv run pytest tests/test_simplification_contract.py -q
  ```

  Expected: the new test initially fails only for deliberately absent fixture wiring, not because
  a material current guarantee is unknown.

- [ ] **Step 3: Implement the measurement script**

  `measure_controller.py` must count production/test physical lines, multiline executable-string
  lines, lines by the areas named in the specification, and the ten largest modules. Its only
  inputs are the repository root and tracked Python files; output is stable JSON with sorted keys.

  ```python
  def measure(repository: Path) -> dict[str, object]: ...
  def executable_string_lines(source: str) -> int: ...
  def main(argv: Sequence[str] | None = None) -> int: ...
  ```

- [ ] **Step 4: Verify and record the baseline**

  ```bash
  uv run pytest tests/test_simplification_contract.py -q
  uv run python scripts/measure_controller.py ..
  uv run pytest -q
  ```

  Expected baseline: 16,259 production Python lines, 17,835 test lines, and approximately 2,670
  executable-string lines. Investigate material deviation before continuing.

- [ ] **Step 5: Review and commit**

  Confirm that characterization has not converted ordinary drift refusal or private internal stage
  naming into permanent requirements. Commit with GitButler:

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Record deployment simplification baseline"
  ```

## Task 2: Adopt Two Roots and the Readable Builder Identity

**Files:**

- Modify: `ops/taskman_ops/config.py`
- Modify: `ops/environments/example.yaml`
- Modify: `ops/taskman_ops/host/baseline.py`
- Modify: `ops/taskman_ops/host/facts.py`
- Modify: `ops/taskman_ops/releases/identifiers.py`
- Modify: all workflow call sites that pass `managed_root`, `release_root`, or `deployment_root`
- Modify: `ops/builder/Containerfile`
- Modify: `ops/taskman_ops/manifests.py`
- Modify: `ops/tests/test_config.py`
- Modify: `ops/tests/test_build.py`
- Modify: `ops/tests/test_manifests.py`
- Modify: affected host/release/workflow tests

**Interfaces:**

- Produces: `EnvironmentConfig.install_root`, derived read-only `release_root`,
  `deployment_root`, and `current_link`, plus builder tag/digest manifest fields.
- Consumes: no new interfaces.

- [ ] **Step 1: Write failing two-root schema tests**

  Assert defaults and custom derivation:

  ```python
  assert config.install_root == PurePosixPath("/opt/taskman")
  assert config.release_root == PurePosixPath("/opt/taskman/releases")
  assert config.deployment_root == PurePosixPath("/opt/taskman/deployments")
  assert config.current_link == PurePosixPath("/opt/taskman/current")
  ```

  Assert that `install_root` and `backup_root` must be absolute, normalized, non-overlapping, and
  outside reserved roots. Do not add tests for legacy-field diagnostics.

- [ ] **Step 2: Replace the configuration fields**

  Store only `install_root` and `backup_root` in the Pydantic model. Implement derived properties:

  ```python
  @property
  def release_root(self) -> PurePosixPath:
      return self.install_root / "releases"

  @property
  def deployment_root(self) -> PurePosixPath:
      return self.install_root / "deployments"

  @property
  def current_link(self) -> PurePosixPath:
      return self.install_root / "current"
  ```

  Remove aliases and validation branches for `managed_root`, configured `release_root`, and
  configured `deployment_root`.

- [ ] **Step 3: Update every path consumer and fixture**

  Replace `managed_root` with `install_root`; continue using the derived properties where a
  subordinate path is required. Search until production and user-facing configuration contain no
  removed field:

  ```bash
  rg -n "managed_root|release_root:|deployment_root:" taskman_ops environments ../docs/deployment.md
  ```

  Expected: no configuration key or `managed_root` access; `release_root` and `deployment_root`
  remain only as derived property accesses or domain names.

- [ ] **Step 4: Add readable builder provenance tests**

  Pin the exact base reference:

  ```dockerfile
  FROM ubuntu:resolute-20260811.1@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b AS build
  ```

  Add `builder_base_tag` and `builder_base_digest` to `ArtifactManifest`, increment its schema
  version, and require exact values during parsing. The digest is the OCI index digest resolved for
  that official dated tag; it is the digest already used by the current builder.

- [ ] **Step 5: Run focused and full verification**

  ```bash
  uv run pytest tests/test_config.py tests/test_build.py tests/test_manifests.py \
    tests/host tests/releases tests/workflows -q
  uv run pytest -q
  ```

- [ ] **Step 6: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Use two deployment roots and readable builder provenance"
  ```

## Task 3: Make pyinfra the Real Provisioning Engine

**Files:**

- Create: `ops/taskman_ops/provisioning.py`
- Modify: `ops/taskman_ops/remote.py`
- Modify: `ops/taskman_ops/host/baseline.py`
- Modify: `ops/taskman_ops/host/firewall.py`
- Modify: `ops/taskman_ops/services/caddy.py`
- Modify: `ops/taskman_ops/services/postgresql.py`
- Modify: `ops/taskman_ops/services/systemd.py`
- Modify: `ops/taskman_ops/workflows/provision.py`
- Replace: `ops/tests/test_pyinfra.py`
- Modify: relevant host/service/provision tests

**Interfaces:**

- Produces: `ProvisioningInputs` and `converge_provisioning`.
- Consumes: validated `EnvironmentConfig`, already rendered secret bytes, and the connected
  `PyinfraRemote`.

- [ ] **Step 1: Write a failing production-path trace test**

  Exercise `_default_capabilities()` through `provision()` and assert one pyinfra deploy is added
  and executed. Fail if production calls `converge_baseline_host`, `apply_caddy_install`,
  `apply_postgresql_native_configuration`, or `apply_systemd_assets`.

- [ ] **Step 2: Add the real programmatic lifecycle**

  Make `PyinfraRemote` retain its `Inventory`, `State`, and host created by `connect()`. Implement a
  method used by `converge_provisioning` that follows pyinfra's API lifecycle:

  ```python
  add_deploy(state, taskman_provisioning, inputs=inputs)
  run_ops(state)
  return summarize_deploy(state)
  ```

  Implement `summarize_deploy(state: State) -> ChangeSet` from pyinfra's supported state results;
  do not parse human console output. Preserve strict host-key construction and bounded connector
  behavior in `remote.py`.

- [ ] **Step 3: Package one provisioning deploy**

  Define:

  ```python
  @deploy("Converge Taskman host")
  def taskman_provisioning(*, inputs: ProvisioningInputs) -> None:
      declare_baseline(inputs.config)
      declare_caddy(inputs.config)
      declare_postgresql(inputs.config)
      declare_systemd(inputs)
  ```

  Built-ins own apt packages, the Taskman account, directories, ordinary files, repositories,
  systemd reload/enablement, and unattended upgrades. Do not branch on facts that earlier
  operations mutate.

- [ ] **Step 4: Apply the proportional-safety decision capability by capability**

  Keep only these justified custom boundaries:

  - UFW rule activation plus a new strict pinned-host-key SSH connection, because lockout is
    material.
  - PostgreSQL cluster/HBA validation immediately before restart, database/role mutation, and
    connection verification, because destructive database ambiguity and credential exposure are
    material.
  - Runtime and pgpass installation where pyinfra logging cannot prove byte secrecy.
  - Caddy validation immediately before replacing its live configuration when invalid
    configuration could remove public availability.

  Prefer built-ins for the surrounding files, packages, accounts, directories, and services.
  Delete duplicate direct-shell paths as each capability moves.

- [ ] **Step 5: Prove convergence through the live path**

  Tests must demonstrate first-run change, second-run no-op, dry-run non-mutation, ordinary drift
  repair, prepare/execute ordering, and absence of adapter-only production coverage. Retain strict
  refusal tests only for the four material boundaries above.

  ```bash
  uv run pytest tests/test_pyinfra.py tests/host tests/services tests/workflows/test_provision.py -q
  uv run pytest -q
  ```

- [ ] **Step 6: Review and commit**

  The review records why each surviving custom operation controls a material risk and verifies that
  no capability has both a live pyinfra and live direct-shell implementation.

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Run provisioning through pyinfra"
  ```

## Task 4: Introduce the Bounded Protocol and Deterministic Helper Package

**Files:**

- Create: `ops/taskman_ops/host_protocol/__init__.py`
- Create: `ops/taskman_ops/host_protocol/identifiers.py`
- Create: `ops/taskman_ops/host_protocol/envelope.py`
- Create: `ops/taskman_ops/host_protocol/operations.py`
- Create: `ops/taskman_ops/host_helper/__init__.py`
- Create: `ops/taskman_ops/host_helper/__main__.py`
- Create: `ops/taskman_ops/helper_package.py`
- Create: `ops/tests/host_protocol/test_envelope.py`
- Create: `ops/tests/host_protocol/test_operations.py`
- Create: `ops/tests/host_helper/test_package.py`
- Create: `ops/tests/host_helper/test_entrypoint.py`

**Interfaces:**

- Produces: `HostRequest`, `HostResult`, their four codecs, `HelperPackage`, and
  `build_helper_package`.
- Consumes: standard library only inside `host_protocol` and `host_helper`.

- [ ] **Step 1: Write failing strict-codec tests**

  Cover exact keys, type checks that reject booleans as integers, enum values, absolute normalized
  paths, identifier patterns, byte limits, sequence limits, nesting limits, invalid UTF-8,
  truncation, trailing bytes, and round trips for every operation.

- [ ] **Step 2: Implement shared envelope and operation validation**

  Use frozen dataclasses and explicit parsers. Define `PROTOCOL_VERSION = 1`; JSON encoding uses
  UTF-8, sorted keys, and compact separators. A result variant must contain all common fields even
  when mappings or sequences are empty.

- [ ] **Step 3: Write failing deterministic-package tests**

  Assert identical bytes and digest across two builds, lexical member order, fixed timestamp and
  mode, an explicit module allowlist, and absence of configuration, secrets, tests, caches,
  bytecode, and repository metadata.

- [ ] **Step 4: Build the zipapp**

  Package only `host_protocol`, `host_helper`, and the fixed `__main__` entry point. Use a fixed
  archive timestamp and permission bits; derive the helper identity from protocol version plus
  archive SHA-256.

- [ ] **Step 5: Implement the bounded entry point**

  Read at most the protocol input limit plus one byte, reject overflow, decode one request, dispatch
  by exact operation name, and emit one encoded result. Reduce unexpected exceptions to a fixed
  failure result; never echo stdin, raw exception text, or raw stderr.

- [ ] **Step 6: Verify without controller dependencies**

  ```bash
  uv run pytest tests/host_protocol tests/host_helper/test_package.py \
    tests/host_helper/test_entrypoint.py -q
  ```

  `test_entrypoint.py` must launch the built artifact with `python3 -I <generated-zipapp>` and a
  serialized `HostRequest`, proving that it succeeds using only the interpreter and zipapp.

- [ ] **Step 7: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Add bounded transient host helper protocol"
  ```

## Task 5: Add the Single Helper Runner

**Files:**

- Create: `ops/taskman_ops/helper_runner.py`
- Modify: `ops/taskman_ops/remote.py`
- Create: `ops/tests/test_helper_runner.py`
- Modify: `ops/tests/fakes.py`

**Interfaces:**

- Produces: `HelperInvocation` and `invoke_helper`.
- Consumes: `Remote`, `HelperPackage`, `HostRequest`, and `decode_result`.

- [ ] **Step 1: Write the transfer/invocation failure matrix**

  Test unique operation IDs; private upload; local and remote checksum agreement; root-owned
  `/run/taskman-ops/<operation-id>` mode `0700`; helper mode `0500`; absolute
  `sudo -- python3 ...`; bounded stdin/stdout/stderr; correlation validation; final cleanup; primary
  error retention; and truthful residue when cleanup is uncertain.

- [ ] **Step 2: Implement `invoke_helper`**

  Use a cryptographically random allowlisted operation ID and exact argument arrays. Validate
  ownership, mode, type, and checksum before privileged execution. Remove only the generated
  transfer file and exact invocation directory after proving their authority.

- [ ] **Step 3: Validate only controller-owned invariants**

  Reject protocol-version, operation, operation-ID, result-variant, and required-success-field
  mismatch. Do not reread host lifecycle state or duplicate operation policy after a valid result.

- [ ] **Step 4: Run focused transport and redaction tests**

  ```bash
  uv run pytest tests/test_helper_runner.py tests/test_remote.py tests/test_secrets.py \
    tests/test_output.py -q
  ```

- [ ] **Step 5: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Transfer and invoke the transient host helper"
  ```

## Task 6: Move Discovery, Verification, and Lifecycle Primitives

**Files:**

- Create: `ops/taskman_ops/host_helper/paths.py`
- Create: `ops/taskman_ops/host_helper/facts.py`
- Create: `ops/taskman_ops/host_helper/verification.py`
- Create: `ops/taskman_ops/host_helper/lifecycle.py`
- Create: `ops/taskman_ops/host_helper/operations/discover.py`
- Modify: `ops/taskman_ops/workflows/releases.py`
- Modify: `ops/taskman_ops/workflows/backups.py`
- Modify: `ops/taskman_ops/workflows/verify.py`
- Create: `ops/tests/host_helper/test_discovery.py`
- Create: `ops/tests/host_helper/test_lifecycle.py`
- Create: `ops/tests/host_helper/test_verification.py`
- Modify: related existing discovery/lifecycle/verification tests

**Interfaces:**

- Produces: helper operations `discover`, `list_releases`, `list_backups`, and `verify`; canonical
  lifecycle record readers/writers; exact derived-path authority.
- Consumes: helper protocol and runner.

- [ ] **Step 1: Add helper-entry-point characterization**

  Execute the real zipapp against isolated temporary roots. Cover empty, manually installed,
  managed, malformed, symlinked, permission-incompatible, and partially failed lifecycle state.

- [ ] **Step 2: Move coherent discovery**

  Port parsing and policy as ordinary Python modules, not generated source strings. Keep fact
  collection separate from host-acceptance policy. Return protocol mappings; do not import
  Pydantic, pyinfra, or controller output classes.

- [ ] **Step 3: Move lifecycle storage and adoption**

  Centralize exact record schemas, atomic root-owned writes, current-link validation, manual
  adoption, immutable staging authority, and rollback eligibility. Split record types from storage
  mechanics if either file exceeds a focused responsibility.

- [ ] **Step 4: Move local verification**

  Implement service, process, listener, readiness, release, and database checks once in
  `host_helper/verification.py`. Controller workflows translate validated result mappings into
  existing `WorkflowResult` facts without rechecking host state.

- [ ] **Step 5: Delete superseded read-only remote programs**

  Remove `remote_snapshot.py`, `remote_adoption.py`, and read-only embedded source sections once no
  production import or call remains. Keep controller-side pure artifact and rollback-policy models
  only where they are not host-state implementations.

- [ ] **Step 6: Verify**

  ```bash
  uv run pytest tests/host_helper/test_discovery.py tests/host_helper/test_lifecycle.py \
    tests/host_helper/test_verification.py tests/workflows/test_discovery.py \
    tests/test_verification.py tests/workflows/test_verify.py -q
  uv run pytest -q
  ```

- [ ] **Step 7: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Move host discovery and lifecycle state into the helper"
  ```

## Task 7: Move Locking, Deployment, and Genesis

**Files:**

- Create: `ops/taskman_ops/host_helper/runtime.py`
- Create: `ops/taskman_ops/host_helper/operations/deploy.py`
- Modify: `ops/taskman_ops/host_helper/lifecycle.py`
- Modify: `ops/taskman_ops/workflows/deploy.py`
- Modify: `ops/taskman_ops/workflows/deploy_transaction.py`
- Modify: `ops/taskman_ops/workflows/provision.py`
- Create: `ops/tests/host_helper/test_runtime.py`
- Create: `ops/tests/host_helper/test_deploy.py`
- Modify: existing deploy/provision/end-to-end tests

**Interfaces:**

- Produces: canonical helper transaction runtime and `deploy`/`genesis` operations.
- Consumes: lifecycle/discovery/verification modules and helper runner.

- [ ] **Step 1: Test the transaction runtime independently**

  Cover lock acquisition and holder evidence, under-lock rediscovery, legal stage order,
  changed-stage tracking, cleanup registration, primary-error retention, warnings, residue, and
  recovery actions. Use injected stage callables; do not introduce a workflow description format.

- [ ] **Step 2: Implement explicit deployment operations**

  `deploy()` and `genesis()` call explicit functions in explicit order. Both validate confirmed
  expected state under the lock, stage an immutable release, create and validate required backups,
  apply migration policy, switch the current symlink atomically, start/restart, and verify.
  Genesis represents the predecessor as absent and never invents prior running state.

- [ ] **Step 3: Reduce controller workflows**

  Keep artifact transfer, redacted plan, confirmation, one `HostRequest`, `invoke_helper`, and
  result translation. Delete embedded deployment/genesis programs and duplicated evidence
  validators. Retain only correlation and required success/failure invariant checks.

- [ ] **Step 4: Exercise every state-changing failure boundary**

  Run success, no-op, refusal, rerun, and injected failures before/after backup, migration,
  activation, startup, readiness, and cleanup through the real helper entry point.

  ```bash
  uv run pytest tests/host_helper/test_runtime.py tests/host_helper/test_deploy.py \
    tests/workflows/test_deploy.py tests/workflows/test_deploy_transaction.py \
    tests/workflows/test_provision.py tests/test_end_to_end.py -q
  uv run pytest -q
  ```

- [ ] **Step 5: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Run deployment and genesis in the host helper"
  ```

## Task 8: Move Backup, Rollback, Restore, and Cleanup

**Files:**

- Create: `ops/taskman_ops/host_helper/operations/backup.py`
- Create: `ops/taskman_ops/host_helper/operations/rollback.py`
- Create: `ops/taskman_ops/host_helper/operations/restore.py`
- Create: `ops/taskman_ops/host_helper/operations/cleanup.py`
- Modify: `ops/taskman_ops/workflows/backup.py`
- Modify: `ops/taskman_ops/workflows/rollback.py`
- Modify: `ops/taskman_ops/workflows/restore.py`
- Modify: `ops/taskman_ops/workflows/cleanup.py`
- Create: matching `ops/tests/host_helper/test_*.py`
- Modify: matching existing workflow tests

**Interfaces:**

- Produces: four helper operations with explicit policy.
- Consumes: common helper runtime, lifecycle, paths, verification, and protocol.

- [ ] **Step 1: Add operation matrices through the zipapp**

  For each operation cover success, no-op, refusal, rerun, lock contention, stale confirmation,
  every mutation failure, cleanup failure, and bounded evidence.

- [ ] **Step 2: Move backup**

  Create and validate PostgreSQL custom-format backups before atomic publication. Prune only exact
  eligible recognized artifacts under `backup_root`; keep credential values outside JSON.

- [ ] **Step 3: Move rollback**

  Revalidate the exact target and every crossed activation edge under lock. Refuse an
  incompatible backward migration; on failure report selected release, service/database state,
  backup, residue, and next safe action.

- [ ] **Step 4: Move restore**

  Validate the selected dump before controller confirmation and again under lock. Restore into a
  temporary database, swap only after validation, and retain recoverable source/database state when
  an inverse action cannot be proven safe.

- [ ] **Step 5: Move cleanup**

  Delete only protocol-specified, under-lock-revalidated, tool-owned exact entries below derived
  roots. Never accept a wildcard, relative path, configured subordinate root, or unproved recovery
  path.

- [ ] **Step 6: Reduce controller workflows and delete embedded programs**

  Each workflow becomes plan/confirmation/request/result translation. Remove the large source
  strings and their controller-side duplicate JSON parsers once the helper tests pass.

- [ ] **Step 7: Verify**

  ```bash
  uv run pytest tests/host_helper/test_backup.py tests/host_helper/test_rollback.py \
    tests/host_helper/test_restore.py tests/host_helper/test_cleanup.py \
    tests/workflows/test_backup.py tests/workflows/test_rollback.py \
    tests/workflows/test_restore.py tests/workflows/test_cleanup.py -q
  uv run pytest -q
  ```

- [ ] **Step 8: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Run recovery operations in the host helper"
  ```

## Task 9: Remove Transitional Architecture and Rebalance Modules

**Files:**

- Delete: `ops/taskman_ops/pyinfra.py` if no justified custom operation uses it
- Delete: superseded `ops/taskman_ops/releases/remote_*.py`
- Delete or reduce: `ops/taskman_ops/workflows/deploy_transaction.py`
- Modify or split: `ops/taskman_ops/releases/records.py`
- Modify or split: `ops/taskman_ops/host/facts.py`
- Modify or split: `ops/taskman_ops/verification.py`
- Modify or split: remaining files over approximately 600 focused lines
- Delete or rewrite: tests that instantiate unused adapters or superseded production paths

**Interfaces:**

- Produces: one production implementation per capability and focused module ownership.
- Consumes: all prior task interfaces.

- [ ] **Step 1: Add architecture scans**

  Tests or repository checks must fail on substantial executable controller strings, imports of
  superseded remote modules, duplicate production convergence paths, helper imports of third-party
  packages, and planning terminology in production surfaces.

- [ ] **Step 2: Delete every transitional production path**

  Remove adapters that only tests call, direct-shell convergence superseded by pyinfra, controller
  parsers superseded by `host_protocol`, and embedded transaction programs superseded by
  `host_helper`.

- [ ] **Step 3: Split remaining mixed responsibilities**

  Keep lifecycle dataclasses/serialization separate from local storage and policy; keep fact
  collection separate from acceptance; keep workflow result translation separate from helper
  invocation. Do not split small cohesive modules merely to reduce line counts.

- [ ] **Step 4: Re-run metrics and inspect largest modules**

  ```bash
  uv run python scripts/measure_controller.py ..
  uv run pytest -q
  ```

  Aim for a 20–30 percent production reduction and no substantial embedded executable program. If
  the target is missed, record evidence that boundaries are simpler or that remaining behavior has
  inherent complexity rather than avoidable duplication.

- [ ] **Step 5: Review and commit**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Remove duplicate deployment implementations"
  ```

## Task 10: Documentation and Final Verification

**Files:**

- Modify: `docs/deployment.md`
- Modify: `docs/README.md`
- Modify: `README.md` if its command/configuration examples changed
- Modify: `ops/environments/example.yaml`
- Modify: `docs/handoffs/deployment-controller-simplification.md`
- Modify: `.beads/issues.jsonl` through `br` only

**Interfaces:**

- Produces: final operator documentation, before/after evidence, and implementation-ready branch.
- Consumes: completed implementation.

- [ ] **Step 1: Update the runbook**

  Document the two roots, transient helper lifecycle, checksum behavior, exact residue recovery,
  pyinfra provisioning boundary, readable builder tag plus digest, unchanged authorization model,
  and separately authorized real-host acceptance.

- [ ] **Step 2: Run complete local gates**

  ```bash
  cd ops
  uv sync --locked
  uv run python -m compileall -q taskman_ops tests
  uv run pytest -q
  cd ..
  mix precommit
  bash -n ops/taskman ops/backup/taskman-backup ops/caddy/render-caddyfile
  git diff --check
  ```

  Validate every documented help surface, all local Markdown links, secret-canary scans, and
  planning-identifier scans using the same commands recorded by the original delivery plan.

- [ ] **Step 3: Run the pinned builder**

  Build for `linux/amd64`, verify the release archive and manifest, and assert the manifest records:

  ```text
  ubuntu:resolute-20260811.1
  sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b
  ```

- [ ] **Step 4: Record simplification evidence**

  Add the before/after line metrics, executable-string count, largest modules, duplicate paths
  removed, remaining compatibility debt, and any justified target shortfall to the Beads feature
  and final handoff. Do not claim real systemd, UFW, ACME, public DNS, email, reboot, or complete
  PostgreSQL restore evidence unless a separately authorized host run occurred.

- [ ] **Step 5: Run final reviews**

  Request a fresh full-branch review after all scoped findings are resolved. The reviewer must map
  the final tree to the specification's completion checklist and reproduce material verification.

- [ ] **Step 6: Commit the verified result**

  ```bash
  but commit -b dedicated-host-deployment-automation -m "Complete deployment controller simplification"
  ```

  Do not push, force-push, merge, deploy, or close the feature without explicit operator
  authorization.

## Plan Self-Review

- Every specification migration slice maps to Tasks 1–10.
- The protocol, helper package, runner, transaction runtime, separate policies, two roots, builder
  identity, real pyinfra deploy, proportional safety rule, result-validation boundary, metrics, and
  external acceptance gate have explicit owners and verification.
- Interfaces use the same names and types throughout the plan.
- Transitional implementations are permitted only within their migration task and are removed
  before that task completes.
- No unresolved placeholder or unspecified compatibility behavior remains.

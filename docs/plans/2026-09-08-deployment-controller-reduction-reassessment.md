# Deployment Controller Reduction Reassessment Implementation Plan

> **For implementation:** Read the complete
> [approved reassessment design](../specs/2026-09-08-deployment-controller-reduction-reassessment-design.md)
> before changing production code. Execute this plan in a fresh session with
> subagent-driven development and an independent review after each task.

**Goal:** Finish the deployment-controller simplification without removing a
public command, scheduled backups, or an accepted safety guarantee, and reduce
tracked production Python from the 21,128-line baseline to at most 15,212
lines after every superseded path is deleted.

**Architecture:** Replace the scheduled-backup shell lifecycle with a narrow,
immutable standard-library zipapp that invokes the same completed-record,
observed-state, lock, backup, and retention capabilities as manual operations.
Then remove the partial legacy transaction slice deliberately and simplify
active code around canonical interfaces, shared host capabilities, one bounded
transport, decision-relevant evidence, and focused Caddy/PostgreSQL ownership.
Temporary growth is allowed; the reduction gate applies only to the completed
migration.

**Tech stack:** Python 3.12, pyinfra 3.x, Pydantic 2.x, pytest 8.x, systemd,
PostgreSQL, Caddy, OpenSSH, and GitButler.

**Current checkpoint:** Tasks 1–8 of the superseded reduction plan are applied
through `a2eae96efdb98cd5687d86592d943e3552cbc553`. An intentionally
uncommitted Task 9 slice deletes the old lifecycle/runtime implementation and
passes 38 focused tests, but the full suite cannot collect until scheduled
backups stop importing those records. Preserve and adopt that slice; do not
discard or broadly commit it.

## Global execution rules

- Use repository-local Beads through `br`. Revise the blocked Task 9 and old
  Task 10 acceptance criteria to point to this plan, then create ordered child
  tasks for the slices below before implementation.
- Before each version-control operation run `but status --json`. Select only
  the intended file or hunk IDs; unrelated dirty changes are user-owned.
- For every behavior change, first add or adjust a focused test, run it to
  observe the expected failure, implement the smallest coherent change, and
  rerun focused and affected tests.
- Preserve strict pinned-host-key SSH, recursive redaction, bounded execution
  and output, authoritative paths, backup-before-consequence, actual migration
  observation, atomic verified selection, destructive confirmation, and
  ambiguity refusal.
- Preserve public commands, public output/exit categories, and scheduled
  backup behavior. Do not add a persistent daemon or install the full helper.
- A transitional implementation must list every old consumer, name its
  deletion task, reject new consumers with an architecture test, and avoid a
  runtime old/new selector.
- Record the production-Python count after every task, but enforce the
  15,212-line maximum only in Task 9 after all legacy and transitional paths
  are gone. Do not minify, combine unrelated responsibilities, or move Python
  behavior outside the metric.
- No real-host operation, deployment, push, merge, or publication is
  authorized by this plan.

---

### Task 1: Migrate scheduled backups to completed records

**Files:**

- Create: `ops/taskman_ops/scheduled_backup.py`
- Create: `ops/taskman_ops/host_helper/backups.py`
- Modify: `ops/taskman_ops/host_helper/records.py`
- Modify: `ops/taskman_ops/host_helper/state.py`
- Modify: `ops/taskman_ops/host_helper/operations/backup.py`
- Modify: `ops/taskman_ops/host_helper/operations/cleanup.py`
- Modify: `ops/taskman_ops/helper_package.py`
- Modify: `ops/taskman_ops/services/backups.py`
- Modify: `ops/systemd/taskman-backup.service`
- Modify: `ops/systemd/taskman-backup.timer`
- Rewrite: `ops/tests/services/test_backups.py`
- Modify: `ops/tests/host_helper/test_records.py`
- Modify: `ops/tests/host_helper/test_backup.py`
- Modify: `ops/tests/host_helper/test_cleanup.py`
- Modify: `ops/tests/host_helper/test_package.py`
- Delete: `ops/backup/taskman-backup`

**Interfaces:**

- `BackupRecord` has exactly `backup_id`, `created_at`, `dump_sha256`,
  `source_release_id`, `migration_versions`, and
  `source_database_size_bytes`.
- One shared backup capability accepts validated paths, database facts,
  credential path, purpose, and observed selected state, and returns a
  completed `BackupRecord`.
- The installed `/usr/local/lib/taskman/taskman-backup.pyz` is persistent;
  each `Type=oneshot` timer invocation is short-lived.

- [ ] **Step 1: Characterize the shared backup and retention contract**

  Add failing tests for whole-second UTC timestamps; exact record keys;
  selected-release and actual-migration provenance; capacity refusal;
  `PGPASSFILE`; `pg_restore --list`; no-replace dump and create-once manifest
  publication; interruption recovery; and retention of every
  selection-referenced backup plus newest N unprotected backups sorted by
  `(created_at, backup_id)`.

- [ ] **Step 2: Characterize the installed one-shot contract**

  Replace tests of shell internals with failing black-box tests for the
  allowlisted zipapp, deterministic bytes/checksum, root ownership and `0750`
  mode, fixed non-secret environment, `/etc/taskman/pgpass`, exact writable
  paths, systemd hardening, calendar validation, `Persistent=true`, lifecycle
  lock exclusion, secret-canary absence, and statuses `0`, `2`, `6`, `10`,
  and `12`.

- [ ] **Step 3: Run the focused tests and observe migration failures**

  ```bash
  cd ops
  uv run pytest tests/services/test_backups.py \
    tests/host_helper/test_records.py tests/host_helper/test_backup.py \
    tests/host_helper/test_cleanup.py tests/host_helper/test_package.py -q
  ```

- [ ] **Step 4: Implement the completed backup and retention capabilities**

  Keep mutation under `install_root/lifecycle.lock`. Validate roots and
  credentials, observe selection and migrations, normalize only safe
  deterministic temporary dumps, publish without replacement, re-observe,
  and preserve unknown, malformed, redirected, contradictory, referenced, or
  checksum-mismatched artifacts.

- [ ] **Step 5: Build and provision the narrow zipapp**

  Package only the scheduled adapter and the standard-library modules needed
  for paths, records, state, locking, commands, backup, and retention.
  Reject third-party imports from this package.
  Provision the lock before enabling the timer. Remove derived roots and
  secrets from service arguments/environment. Atomically install and verify
  the checksum.

- [ ] **Step 6: Delete the shell implementation and legacy tests**

  Delete `ops/backup/taskman-backup` and assertions for activation/adoption
  graphs, inherited lock descriptors, rollback fingerprints, old JSON, and
  checksum-less compatibility. Add an architecture guard that rejects the
  shell and legacy record imports.

- [ ] **Step 7: Verify and record the transitional count**

  ```bash
  cd ops
  uv run pytest tests/services/test_backups.py tests/host_helper -q
  uv run pytest -q
  uv run python scripts/measure_controller.py ..
  ```

  Record any temporary increase and the exact deletion owners in Tasks 2–8.

- [ ] **Step 8: Request scoped safety review and commit**

  Review package least authority, interruption states, selection-protected
  retention, secret handling, and systemd confinement before committing only
  this slice.

---

### Task 2: Finalize deletion of the old transaction core

**Files:**

- Adopt existing modifications: `ops/taskman_ops/helper_package.py`
- Adopt existing modifications: `ops/taskman_ops/host_helper/__init__.py`
- Adopt existing modifications: `ops/taskman_ops/host_helper/__main__.py`
- Adopt existing modifications: `ops/taskman_ops/host_helper/verification.py`
- Delete: `ops/taskman_ops/host_helper/lifecycle.py`
- Delete: `ops/taskman_ops/host_helper/lifecycle_records.py`
- Delete: `ops/taskman_ops/host_helper/runtime.py`
- Delete: `ops/taskman_ops/host_helper/facts.py`
- Delete: `ops/taskman_ops/host_helper/legacy_result.py`
- Delete: `ops/taskman_ops/host_helper/operations/legacy_backup.py`
- Adopt/delete corresponding tests under `ops/tests/host_helper/`
- Modify: `ops/tests/test_architecture.py`
- Modify: `ops/tests/test_simplification_contract.py`

- [ ] **Step 1: Inventory the existing dirty slice**

  Compare every dirty file with the applied stack and the Task 1 result.
  Classify each hunk as required legacy deletion, scheduled-backup conflict,
  or unrelated. Preserve unrelated work and resolve overlaps without reviving
  old record formats.

- [ ] **Step 2: Strengthen deletion guards**

  Fail on production definitions, imports, and consumers of lifecycle stores,
  transaction runtimes, operation IDs, pending/provisional records, stage
  histories, residue arrays, generated recovery actions, legacy result
  bridges, and legacy scheduled-backup formats.

- [ ] **Step 3: Run guards and the complete suite**

  ```bash
  cd ops
  uv run pytest tests/test_architecture.py tests/test_simplification_contract.py \
    tests/host_helper/test_entrypoint.py tests/host_helper/test_package.py \
    tests/host_helper/test_verification.py -q
  uv run pytest -q
  uv run python scripts/measure_controller.py ..
  ```

- [ ] **Step 4: Review and commit the deliberate deletion**

  Require review of the dirty-slice provenance and confirm scheduled backups
  now use only completed records. Commit only the adopted Task 2 files.

---

### Task 3: Canonicalize internal controller interfaces

**Files:**

- Modify: `ops/taskman_ops/host_protocol/operations.py`
- Modify: `ops/taskman_ops/host_protocol/envelope.py`
- Modify: `ops/taskman_ops/helper_runner.py`
- Modify: `ops/taskman_ops/workflows/helper.py`
- Modify: `ops/taskman_ops/errors.py`
- Modify: relevant protocol, runner, workflow, and architecture tests

- [ ] Add characterization tests for the finite operation vocabulary, exact
  bounded request/result keys, correlation validation, four outcomes,
  cleanup-warning merge, recursive redaction, and public exit translation.
- [ ] Remove duplicate operation names, correlation/result checks, dynamic
  dispatch evidence, arbitrary mapping compatibility, unused aliases,
  speculative properties, and unused error serialization.
- [ ] Prove there is one direct `HostResult` path and cleanup warnings merge
  once; add architecture guards against deleted compatibility boundaries.
- [ ] Run focused protocol/runner/workflow tests, the full suite, and the
  controller metric; request scoped review and commit.

---

### Task 4: Share focused host-operation capabilities

**Files:**

- Modify/create focused modules under `ops/taskman_ops/host_helper/`
- Modify: `ops/taskman_ops/host_helper/operations/backup.py`
- Modify: `ops/taskman_ops/host_helper/operations/deploy.py`
- Modify: `ops/taskman_ops/host_helper/operations/rollback.py`
- Modify: `ops/taskman_ops/host_helper/operations/restore.py`
- Modify: corresponding host-helper tests

- [ ] Characterize operation outcomes and consequence ordering before
  extraction, including backup-before-migration/restore and rerun ambiguity.
- [ ] Give one small owner each to database mapping/migration observation,
  credential validation, service stop/start, atomic current-link selection,
  verification request construction, SHA-256, and directory synchronization.
- [ ] Migrate every inventoried consumer without introducing a configurable
  workflow abstraction or runtime selector.
- [ ] Delete superseded local helpers and guard against new duplicates.
- [ ] Run affected operation tests, the full suite, and the metric; request
  scoped safety/readability review and commit.

---

### Task 5: Use one bounded remote execution path

**Files:**

- Modify: `ops/taskman_ops/remote.py`
- Modify: `ops/taskman_ops/helper_runner.py`
- Modify: `ops/taskman_ops/host_helper/commands.py`
- Modify: `ops/taskman_ops/host_helper/verification.py`
- Modify: `ops/tests/test_remote.py`
- Modify: `ops/tests/test_helper_runner.py`
- Modify: `ops/tests/host_helper/test_commands.py`
- Modify: `ops/tests/host_helper/test_verification.py`

- [ ] Characterize pinned host identity, argv validation/quoting, sensitive
  stdin, simultaneous bounded stdout/stderr, numeric status, forced timeout
  termination, private upload, checksums, exact cleanup, and readiness budget.
- [ ] Consolidate workstation execution on one finite-default bounded path;
  remove marker parsing and duplicate cleanup flags/state.
- [ ] Reuse the host command runner from verification so each external call
  owns one timeout while readiness retains its polling budget.
- [ ] Delete the second `Popen`/`select` implementation and add structural
  guards. Simplify connector setup deadlines only if tests prove they remain
  forcibly bounded.
- [ ] Run focused transport/verification tests, full tests, and the metric;
  request security review and commit.

---

### Task 6: Keep only decision-relevant host admission and verification

**Files:**

- Modify: `ops/taskman_ops/host/facts.py`
- Modify: `ops/taskman_ops/host/acceptance.py`
- Modify: `ops/taskman_ops/workflows/operational_preflight.py`
- Modify: `ops/taskman_ops/host_helper/state.py`
- Modify: `ops/taskman_ops/host_helper/verification.py`
- Modify: corresponding host, workflow, state, and verification tests

- [ ] Characterize controller admission before plan/confirmation and fresh
  helper checks at consequence boundaries.
- [ ] Remove helper repeats of immutable OS, architecture, PID 1, public DNS,
  and baseline-memory admission already proven by the controller.
- [ ] Retain fresh administrator/session identity, path authority, selected
  release/database state, capacity, service identity, listener ownership,
  readiness, and HSTS evidence.
- [ ] Consolidate completed-state parsing and delete duplicate fact types
  without weakening contradiction handling.
- [ ] Run focused admission/state/verification tests, full tests, and metric;
  request safety review and commit.

---

### Task 7: Simplify Caddy evidence

**Files:**

- Modify: `ops/taskman_ops/services/caddy.py`
- Modify: `ops/taskman_ops/provisioning.py`
- Modify: `ops/tests/services/test_caddy.py`
- Modify: relevant provisioning and architecture tests

- [ ] Characterize foreign public-listener refusal, managed-marker authority,
  declarative convergence, and validate-before-activation.
- [ ] Remove exhaustive package/process/cgroup reconstruction where ordinary
  declarative convergence repairs drift.
- [ ] Keep the direct pre-activation validation action and its documented
  authority boundary; remove duplicate predicates and evidence types.
- [ ] Run focused Caddy/provisioning tests, full tests, and metric; request
  service-safety review and commit.

---

### Task 8: Simplify PostgreSQL convergence

**Files:**

- Modify: `ops/taskman_ops/services/postgresql.py`
- Modify: `ops/taskman_ops/provisioning.py`
- Modify: `ops/tests/services/test_postgresql.py`
- Modify: relevant provisioning and architecture tests

- [ ] Characterize one-cluster selection, live endpoint identity, HBA parser
  validation, SCRAM, least-authority role/database ownership, and secret stdin.
- [ ] Extract shared read/mutation predicates so observation and convergence
  do not reimplement cluster/HBA policy.
- [ ] Preserve the justified direct cluster/HBA transition and
  secret-sensitive write boundaries; delete duplicate script/evidence logic.
- [ ] Run focused PostgreSQL/provisioning tests, full tests, and metric;
  request database-safety review and commit.

---

### Task 9: Enforce final structure, metric, documentation, and verification

**Files:**

- Modify: `ops/tests/test_architecture.py`
- Modify: `ops/tests/test_simplification_contract.py`
- Modify: `docs/deployment.md`
- Modify: `docs/README.md`
- Modify: `README.md` only if its public behavior summary changed
- Modify: `docs/handoffs/deployment-controller-simplification.md`
- Modify: `.beads/issues.jsonl` through `br` only

- [ ] **Step 1: Delete every remaining transitional path**

  Reconcile the consumer/deletion inventories from Tasks 1–8. Fail
  architecture tests if an adapter, duplicate owner, old scheduled asset, old
  record concept, or planning-task identifier remains in production.

- [ ] **Step 2: Enforce the final metric**

  ```bash
  cd ops
  uv run python scripts/measure_controller.py ..
  ```

  The unchanged metric must report at most 15,212 tracked production-Python
  lines, a reduction of at least 28% from 21,128. Record the count after each
  prior task and explain estimate variance. If the floor cannot be reached
  without crossing a protected responsibility or safety boundary, stop for
  operator review.

- [ ] **Step 3: Update operator documentation**

  Document the persistent zipapp/short-lived process distinction, fixed
  scheduled statuses, completed backup records, selection-aware retention,
  rerun convergence, manual ambiguity, exact-input artifacts, and retained
  custom provisioning boundaries. Remove current instructions for old
  lifecycle or recovery concepts.

- [ ] **Step 4: Run complete local verification**

  ```bash
  cd ops
  uv sync --locked
  uv run python -m compileall -q taskman_ops tests
  uv run pytest -q
  uv run python scripts/measure_controller.py ..
  cd ..
  bash -n ops/taskman ops/caddy/render-caddyfile
  systemd-analyze verify ops/systemd/taskman-backup.service \
    ops/systemd/taskman-backup.timer
  mix precommit
  git diff --check
  ```

- [ ] **Step 5: Run the pinned local builder**

  Build and verify `linux/amd64` with builder tag
  `ubuntu:resolute-20260811.1` and digest
  `sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b`.
  Prove exact-input reuse without connecting to or mutating a real host.

- [ ] **Step 6: Request fresh independent whole-workstream review**

  Have a fresh reviewer reproduce the metric and map every public command,
  scheduled backups, protected guarantee, interruption boundary, retained
  custom action, and deleted concept to implementation and tests. Resolve
  Critical and Important findings and rerun affected plus complete gates.

- [ ] **Step 7: Persist delivery evidence**

  Through `br`, record final counts, percentage, deleted concepts, retained
  responsibilities, test evidence, builder identity, review outcome, and the
  still-unperformed real-host acceptance. Update the handoff and indexes,
  close completed child tasks, and leave integration/external actions for
  explicit operator authorization.

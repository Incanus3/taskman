# Dedicated-host Deployment Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide safe workstation-driven commands for provisioning a clean supported VPS and for
building, deploying, inspecting, backing up, rolling back, restoring, and cleaning up Taskman
releases on an existing host.

**Architecture:** A small typed Python package coordinates a locked pyinfra controller environment,
strict SSH operations, immutable release artifacts, root-owned lifecycle records, and explicit
workflow state machines. Phoenix exposes a deliberately minimal database-backed readiness endpoint;
the deployed runtime remains a bare-metal OTP release supervised by systemd behind loopback Caddy
with local PostgreSQL.

**Tech Stack:** Python 3.12+, uv, pyinfra 3.x, Pydantic 2.x, PyYAML 6.x, pytest, SOPS with age,
Docker BuildKit, Ubuntu 26.04 amd64, Erlang/OTP 27.3.4.6, Elixir 1.18.3, Node.js 22.22.1,
Phoenix 1.8, Ecto/PostgreSQL, systemd, UFW, Caddy, ExUnit.

**Spec:** `docs/specs/2026-09-04-dedicated-host-deployment-automation-design.md`

**Status:** Approved

**Delivery tracking:** Feature `tas-dedicated-host-deployment-automation-moq`

| Task | Beads issue |
| --- | --- |
| 1 | `tas-dedicated-host-deployment-automation-moq.1` |
| 2 | `tas-dedicated-host-deployment-automation-moq.2` |
| 3 | `tas-dedicated-host-deployment-automation-moq.3` |
| 4 | `tas-dedicated-host-deployment-automation-moq.4` |
| 5 | `tas-dedicated-host-deployment-automation-moq.5` |
| 6 | `tas-dedicated-host-deployment-automation-moq.6` |
| 7 | `tas-dedicated-host-deployment-automation-moq.7` |
| 8 | `tas-dedicated-host-deployment-automation-moq.8` |
| 9 | `tas-dedicated-host-deployment-automation-moq.9` |
| 10 | `tas-dedicated-host-deployment-automation-moq.10` |
| 11 | `tas-dedicated-host-deployment-automation-moq.11` |
| 12 | `tas-dedicated-host-deployment-automation-moq.12` |
| 13 | `tas-dedicated-host-deployment-automation-moq.13` |
| 14 | `tas-dedicated-host-deployment-automation-moq.14` |

## Global Constraints

- Read the complete approved specification, `AGENTS.md`, and `docs/development.md` before starting
  any task.
- Support only Ubuntu `26.04`, Linux `amd64`/`x86_64`, one SSH host, local PostgreSQL, systemd,
  and direct Caddy HTTPS in this increment.
- Preserve the existing public boundary: Caddy owns ports 80/443, Phoenix binds only to loopback,
  PostgreSQL binds only to loopback, and Erlang distribution stays fixed and loopback-only.
- Production receives an immutable OTP release, not source, Mix, Node, Python, pyinfra, SOPS, age,
  or a container runtime.
- Keep `provision` and `deploy` separate. Deployment must not upgrade packages, change the
  firewall, or reconverge unrelated host state.
- Store no plaintext secret in the repository or a persistent workstation file. Never include
  secrets in arguments, logs, previews, exceptions, manifests, JSON output, or retained temporary
  files.
- Use strict SSH host-key verification and refuse unsupported, ambiguous, contradictory, or
  unrecognized state rather than overwriting it.
- Every non-no-op activation and every rollback receives a fresh validated backup. Never
  automatically roll code back after migrations may have changed the database.
- Release IDs use
  `<application-version>-<source-short-sha>-ubuntu26.04-amd64-otp<otp-version>`; build time is
  manifest provenance and SHA-256 distinguishes exact artifact bytes.
- Authoritative release and backup IDs come from validated metadata, never directory-name
  inference. `releases`, `backups`, and `verify` remain read-only.
- Preserve the stable exit statuses `0` and `2` through `12` defined by the specification.
- Mutating commands require the specified confirmations. Do not add a generic `--force` or
  unattended deployment mode.
- The local backup feature is not disaster recovery. Documentation must require independently
  arranged and tested off-host copies.
- Do not create or mutate a VPS, DNS, provider firewall, Resend account, production secret, or real
  deployment without separate operator authorization.
- Use `Req` for any Elixir HTTP request. Do not add HTTPoison, Tesla, or `:httpc`.
- Use `but` for version-control mutations. Each task ends with focused checks and an independently
  reviewable commit; do not push, merge, publish, or deploy without separate authorization.
- Each implementation task receives independent verification from an agent that did not implement
  it. Finish repository work with `mix precommit`.

---

## File and Interface Map

### Phoenix readiness contract

- `lib/taskman/health.ex` owns the bounded database readiness check.
- `lib/taskman_web/controllers/health_controller.ex` maps readiness to fixed, secret-free HTTP
  responses.
- `lib/taskman_web/router.ex` exposes unauthenticated `GET /healthz`.
- `test/taskman/health_test.exs` and `test/taskman_web/controllers/health_controller_test.exs`
  verify database and HTTP behavior.

```elixir
@spec Taskman.Health.check(keyword()) :: :ready | :unavailable
@spec TaskmanWeb.HealthController.show(Plug.Conn.t(), map()) :: Plug.Conn.t()
```

### Controller package and stable types

- `ops/taskman` is the executable launcher and runs the locked uv environment.
- `ops/pyproject.toml` and `ops/uv.lock` own Python and dependency constraints.
- `ops/taskman_ops/cli.py` parses commands, selects workflows, and returns stable exit statuses.
- `ops/taskman_ops/errors.py` defines `ExitStatus` and secret-free `OpsError`.
- `ops/taskman_ops/output.py` owns redaction, human tables, JSON schema version `1`, plans, and
  final reports.
- `ops/taskman_ops/config.py` owns validated non-secret environment data.
- `ops/taskman_ops/secrets.py` owns SOPS decryption, secret validation, and protected file
  rendering.
- `ops/taskman_ops/remote.py` owns the strict-SSH/pyinfra adapter behind a testable protocol.

```python
class ExitStatus(IntEnum):
    OK = 0
    INVALID = 2
    LOCAL_PREREQUISITE = 3
    SECRET = 4
    REMOTE_PREFLIGHT = 5
    BACKUP = 6
    MIGRATION = 7
    RELEASE = 8
    READINESS = 9
    SAFETY = 10
    RESTORE = 11
    LOCKED = 12

@dataclass(frozen=True)
class WorkflowResult:
    command: str
    environment: str
    changed: bool
    stage: str
    facts: Mapping[str, object]
    warnings: tuple[str, ...] = ()
    next_action: str | None = None

class Remote(Protocol):
    def facts(self) -> HostFacts: ...
    def run(self, argv: Sequence[str], *, sudo: bool = False,
            stdin: bytes | None = None, sensitive: bool = False) -> CommandResult: ...
    def put(self, source: Path, destination: PurePosixPath, *,
            mode: int, sensitive: bool = False) -> None: ...
    def converge(self, deploy: Callable[..., None], data: Mapping[str, object]) -> ChangeSet: ...
```

### Artifacts and lifecycle state

- `ops/builder/Containerfile` pins the target build environment.
- `ops/taskman_ops/build.py` owns clean-source checks and BuildKit invocation.
- `ops/taskman_ops/manifests.py` owns artifact manifest creation and verification.
- `ops/taskman_ops/releases/identifiers.py` owns all narrow identifiers and managed-path
  construction.
- `ops/taskman_ops/releases/records.py` owns schema-versioned release, activation, backup, and
  retained-database records.
- `ops/taskman_ops/releases/locking.py`, `staging.py`, `activation.py`, and `cleanup.py` own their
  bounded lifecycle capabilities.

```python
@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: Literal[1]
    application: Literal["taskman"]
    application_version: str
    source_revision: str
    release_id: str
    built_at: datetime
    target_os: Literal["ubuntu26.04"]
    architecture: Literal["amd64"]
    otp_version: str
    elixir_version: str
    node_version: str
    migrations: tuple[MigrationFingerprint, ...]
    top_level: Literal["taskman"]

@dataclass(frozen=True)
class ReleaseRecord:
    schema_version: Literal[1]
    release_id: str
    artifact_sha256: str | None
    installed_at: datetime
    activated_at: datetime | None
    previous_release_id: str | None
    backup_id: str | None
    migration_policy: Literal["no-change", "backward-compatible", "restore-required", "adopted"]

@dataclass(frozen=True)
class BackupRecord:
    schema_version: Literal[1]
    backup_id: str
    created_at: datetime
    size_bytes: int
    database: str
    current_release_id: str | None
    candidate_release_id: str | None
    reason: Literal["scheduled", "pre-deploy", "pre-rollback", "pre-restore"]
    validated: bool
    dump_path: PurePosixPath
```

### Host capabilities and workflows

- `ops/taskman_ops/host/facts.py`, `baseline.py`, and `firewall.py` own supported-host discovery
  and convergent baseline state.
- `ops/taskman_ops/services/postgresql.py`, `caddy.py`, `systemd.py`, and `backups.py` own service
  convergence and native validation.
- `ops/taskman_ops/verification.py` owns local/public health, listener, process-path, unit, and log
  evidence.
- `ops/taskman_ops/workflows/` contains one focused module per operator command; it sequences
  capabilities but does not duplicate them.
- `ops/backup/`, `ops/caddy/`, and `ops/systemd/` contain server assets installed by provisioning.
- `ops/environments/example.yaml` and `example.secrets.sops.yaml` document complete schemas without
  usable credentials.
- `ops/tests/` mirrors package boundaries and uses fake remotes plus controlled integration
  fixtures.

---

### Task 1: Add the database-backed readiness endpoint

**Files:**

- Create: `lib/taskman/health.ex`
- Create: `lib/taskman_web/controllers/health_controller.ex`
- Modify: `lib/taskman_web/router.ex`
- Create: `test/taskman/health_test.exs`
- Create: `test/taskman_web/controllers/health_controller_test.exs`

**Interfaces:**

- Consumes: `Taskman.Repo.query/3`, the existing endpoint, and controller test support.
- Produces: `Taskman.Health.check/1` and unauthenticated `GET /healthz`.

- [ ] **Step 1: Write failing capability tests**

Use an injected `query` function so ready, database error, exception, and timeout-shaped results
are deterministic:

```elixir
test "reports ready only for a successful SELECT 1" do
  assert :ready = Health.check(query: fn "SELECT 1", [], [timeout: 750] -> {:ok, %{}} end)
end

for result <- [{:error, :timeout}, {:error, :closed}] do
  test "maps #{inspect(result)} to unavailable" do
    result = unquote(Macro.escape(result))
    assert :unavailable = Health.check(query: fn _, _, _ -> result end)
  end
end
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `mix test test/taskman/health_test.exs`

Expected: compilation fails because `Taskman.Health` does not exist.

- [ ] **Step 3: Implement the bounded core capability**

Implement `check/1` with defaults `query: &Repo.query/3` and `timeout: 750`; call exactly
`query.("SELECT 1", [], timeout: timeout)`, return only `:ready | :unavailable`, and rescue/catch
unexpected failures after logging a fixed message without inspected exception data.

- [ ] **Step 4: Write failing controller tests**

Assert `GET /healthz` returns status `200`, body `ready`, content type `text/plain`,
`cache-control: no-store`, and no release/database details. Temporarily set a health implementation
in application configuration for a `503 unavailable` test, and assert `POST /healthz` is not routed.

- [ ] **Step 5: Implement and route the controller**

Add `get "/healthz", HealthController, :show` in the unauthenticated browser scope before the
authenticated LiveView session. The controller calls the configured health module and emits only
the two fixed bodies and statuses.

- [ ] **Step 6: Verify and commit**

Run:

```sh
mix format
mix test test/taskman/health_test.exs test/taskman_web/controllers/health_controller_test.exs
mix test test/taskman_web/authenticated_hosted_access_test.exs
```

Expected: all focused tests pass and existing authentication remains required everywhere except
the intentional health route.

Commit: `Add deployment readiness endpoint`

---

### Task 2: Establish the locked controller, CLI, results, and redaction boundary

**Files:**

- Create: `ops/pyproject.toml`
- Create: `ops/uv.lock`
- Create: `ops/taskman`
- Create: `ops/taskman_ops/__init__.py`
- Create: `ops/taskman_ops/cli.py`
- Create: `ops/taskman_ops/errors.py`
- Create: `ops/taskman_ops/output.py`
- Create: `ops/tests/test_cli.py`
- Create: `ops/tests/test_output.py`

**Interfaces:**

- Consumes: workstation Python 3.12+, uv, and standard input/output.
- Produces: `ExitStatus`, `OpsError`, `WorkflowResult`, all command parsers, redacted human output,
  and versioned JSON output.

- [ ] **Step 1: Create the project contract and failing CLI tests**

Declare Python `>=3.12`, runtime dependencies `pyinfra>=3,<4`, `pydantic>=2,<3`, and
`PyYAML>=6,<7`, plus pytest as a development dependency. Tests must parse every approved command,
reject missing or extra identifiers with status `2`, reject unknown commands, and prove that
`--json` and `--dry-run` reach the invocation object.

- [ ] **Step 2: Lock dependencies and record the baseline**

Run:

```sh
cd ops
uv lock
uv run pytest tests/test_cli.py -q
```

Expected: dependency lock succeeds; tests fail because command dispatch is absent.

- [ ] **Step 3: Implement the launcher and typed command parser**

`ops/taskman` must use `#!/bin/sh`, `set -eu`, resolve its own directory, and execute
`uv run --frozen --project "$ops_dir" python -m taskman_ops.cli "$@"`. Use `argparse` subparsers for
`build`, `provision`, `deploy`, `verify`, `releases`, `backups`, `backup`, `rollback`, `restore`,
`create-admin`, and `cleanup`; do not add aliases or `--force`.

- [ ] **Step 4: Implement stable errors, reports, and recursive redaction**

Make `OpsError` carry `status`, `stage`, `message`, `changed`, and `next_action`. Redaction replaces
registered canary values in strings nested in mappings, sequences, dataclasses, exceptions, human
tables, and JSON. JSON output always has:

```json
{"schema_version":1,"command":"verify","environment":"production","status":"ok",
 "changed":false,"stage":"complete","facts":{},"warnings":[],"next_action":null}
```

- [ ] **Step 5: Verify output non-disclosure and exit mapping**

Run: `cd ops && uv run pytest tests/test_cli.py tests/test_output.py -q`

Expected: all commands parse, every `ExitStatus` maps exactly to `0,2..12`, and canary secrets are
absent from captured stdout, stderr, JSON, and exception rendering.

- [ ] **Step 6: Commit**

Commit: `Add deployment controller command shell`

---

### Task 3: Validate environment configuration and decrypt secrets safely

**Files:**

- Create: `ops/taskman_ops/config.py`
- Create: `ops/taskman_ops/secrets.py`
- Create: `ops/environments/example.yaml`
- Create: `ops/environments/example.secrets.sops.yaml`
- Create: `.sops.yaml`
- Create: `ops/tests/test_config.py`
- Create: `ops/tests/test_secrets.py`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: environment name, repository YAML, `sops decrypt --output-type yaml`, and an external
  age identity.
- Produces: frozen `EnvironmentConfig`, `SecretConfig`, `load_environment(name)`,
  `decrypt_secrets(name, runner)`, and `render_runtime_environment(config, secrets) -> bytes`.

- [ ] **Step 1: Write failing schema and allowlist tests**

Cover valid complete input and refusal of path-like environment names, reserved/local hostnames,
wrong OS/architecture, invalid SSH/application/distribution ports, overlapping ports, invalid
database identifiers, unsafe managed roots, nonpositive retention/timeouts, and malformed pinned
host-key fingerprints.

- [ ] **Step 2: Implement strict Pydantic models**

Use `extra="forbid"` and frozen models. Canonicalize architecture aliases to `amd64`; retain the
exact configured SSH port. Permit only environment names matching
`[a-z][a-z0-9-]{0,31}` and PostgreSQL identifiers matching `[a-z_][a-z0-9_]{0,62}`.

- [ ] **Step 3: Write failing secret and rendering tests**

Use canaries for database password, `SECRET_KEY_BASE`, Ash signing secret, and Resend key. Assert
both signing secrets are distinct and at least 64 bytes, all required fields exist, malformed SOPS
output maps to status `4`, and rendered systemd/pgpass bytes are correctly quoted without touching
disk.

- [ ] **Step 4: Implement in-memory SOPS handling**

Invoke SOPS without a plaintext output path, parse only captured stdout, register secrets with the
redactor immediately, clear captured buffers after model construction, and expose no mapping
method or repr containing values. `.sops.yaml` contains only a documented example public recipient
rule; the example encrypted file contains obviously unusable `ENC[...]` samples.

- [ ] **Step 5: Verify and commit**

Run: `cd ops && uv run pytest tests/test_config.py tests/test_secrets.py tests/test_output.py -q`

Expected: schema failures return `2`, secret failures return `4`, and no canary survives any output.

Commit: `Validate deployment configuration and secrets`

---

### Task 4: Build and verify target-compatible release artifacts

**Files:**

- Create: `ops/builder/Containerfile`
- Create: `ops/taskman_ops/build.py`
- Create: `ops/taskman_ops/manifests.py`
- Create: `ops/taskman_ops/releases/__init__.py`
- Create: `ops/taskman_ops/releases/identifiers.py`
- Create: `ops/tests/test_build.py`
- Create: `ops/tests/test_manifests.py`
- Create: `ops/tests/fixtures/artifacts/`

**Interfaces:**

- Consumes: a clean identified repository revision, Docker BuildKit, Mix version `0.2.0`, and
  migrations under `priv/repo/migrations`.
- Produces: `build_release(repo, output_dir) -> VerifiedArtifact`,
  `verify_artifact(archive, manifest, checksum) -> VerifiedArtifact`, and validated managed IDs.

- [ ] **Step 1: Write failing identifier and manifest tests**

Assert the release ID `0.2.0-<12-hex>-ubuntu26.04-amd64-otp27.3.4.6`, full SHA validation,
UTC timestamp parsing, stable migration filename/content hashes, exact schema fields, safe
top-level layout, and rejection of slashes, traversal, malformed checksums, wrong target, unknown
fields, absolute archive entries, escaping links, and multiple top-level roots.

- [ ] **Step 2: Implement immutable manifest models and archive inspection**

Calculate SHA-256 by streaming bytes. Inspect tar members before extraction; reject device nodes,
absolute paths, `..`, and symlink/hardlink targets outside `taskman/`. Keep the checksum detached
and require all expected release directories and launchers.

- [ ] **Step 3: Write failing build-runner tests**

Inject command and repository-state readers. Assert dirty/unidentified source returns status `3`,
the exact revision is passed as build metadata, `linux/amd64` is mandatory, and a failed build
reports the retained artifact directory without inventing a new ID.

- [ ] **Step 4: Add the pinned Ubuntu builder**

Use base image
`ubuntu@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b`.
Install the recorded Ubuntu 26.04 toolchain versions:

```text
erlang-base 1:27.3.4.6+dfsg-1
elixir 1.18.3.dfsg-1build1
nodejs 22.22.1+dfsg+~cs22.19.15-1ubuntu1
npm 9.2.0~ds3-1
```

Run `mix deps.get --only prod`, `mix compile --warnings-as-errors`, `mix assets.deploy`, and
`mix release --overwrite`. Export the archive, manifest inputs, and tool versions through a
BuildKit output directory; never bake source credentials or controller secrets into a layer.

- [ ] **Step 5: Implement build packaging and exact retry behavior**

Read `mix.exs` application version without evaluating arbitrary code, build in a unique mode-0700
local directory, generate the manifest after container output, create the archive and detached
checksum, chmod them `0600`, and retain the exact paths on failure.

- [ ] **Step 6: Verify unit fixtures and perform the local container proof**

Run:

```sh
cd ops
uv run pytest tests/test_build.py tests/test_manifests.py -q
./taskman build
```

Expected: tests pass; BuildKit produces an amd64 archive whose manifest reports Ubuntu 26.04,
OTP 27.3.4.6, the current clean SHA, and the exact archive checksum.

- [ ] **Step 7: Commit**

Commit: `Build verified Ubuntu release artifacts`

---

### Task 5: Add strict remote execution and supported-host preflight

**Files:**

- Create: `ops/taskman_ops/remote.py`
- Create: `ops/taskman_ops/host/__init__.py`
- Create: `ops/taskman_ops/host/facts.py`
- Create: `ops/tests/fakes.py`
- Create: `ops/tests/test_remote.py`
- Create: `ops/tests/host/test_facts.py`

**Interfaces:**

- Consumes: validated `EnvironmentConfig`, OpenSSH host-key policy, sudo, and pyinfra's
  programmatic API.
- Produces: `Remote`, `PyinfraRemote`, `HostFacts`, `connect(config)`, and
  `validate_supported_host(remote, config)`.

- [ ] **Step 1: Write failing transport-boundary tests**

Assert argument quoting, no shell interpolation from unvalidated values, unique private upload
paths, mode enforcement, sensitive stdin suppression, timeout mapping, strict known-host
fingerprint use, and statuses `5` versus `12`.

- [ ] **Step 2: Implement the adapter and reusable fake**

Keep pyinfra objects inside `PyinfraRemote`; workflows depend only on `Remote`. Use OpenSSH
`StrictHostKeyChecking=yes` with a unique generated known-hosts file containing only the
out-of-band verified key, then remove that non-secret temporary file. Never enable accept-new.

- [ ] **Step 3: Write failing host-fact tests**

Cover exact Ubuntu `26.04`, `x86_64` normalization, systemd PID 1, sudo capability, active SSH
connection port, memory/disk minimums, DNS/address match, and conflict detection for listeners,
paths, units, accounts, and databases.

- [ ] **Step 4: Implement immutable fact collection and refusal checks**

Gather all facts before mutation. Return structured facts, not raw command output. Make unsupported
targets status `2`; connection, key, or sudo failures status `5`; ambiguous managed state status
`10`. Do not adopt anything in this task.

- [ ] **Step 5: Verify and commit**

Run: `cd ops && uv run pytest tests/test_remote.py tests/host/test_facts.py -q`

Commit: `Add strict host preflight`

---

### Task 6: Model lifecycle records, locking, adoption, and discovery

**Files:**

- Create: `ops/taskman_ops/releases/records.py`
- Create: `ops/taskman_ops/releases/locking.py`
- Create: `ops/taskman_ops/releases/adoption.py`
- Create: `ops/taskman_ops/workflows/__init__.py`
- Create: `ops/taskman_ops/workflows/releases.py`
- Create: `ops/taskman_ops/workflows/backups.py`
- Create: `ops/tests/releases/test_records.py`
- Create: `ops/tests/releases/test_locking.py`
- Create: `ops/tests/releases/test_adoption.py`
- Create: `ops/tests/workflows/test_discovery.py`

**Interfaces:**

- Consumes: managed roots, `Remote`, installed manifests, current symlink, and root-owned JSON
  records.
- Produces: atomic schema-versioned records, shared/exclusive lifecycle locks,
  `adopt_current_release`, `list_releases`, and `list_backups`.

- [ ] **Step 1: Write failing record-validation tests**

Cover exact schemas, UTC dates, immutable IDs, target paths under managed roots, activation-edge
chains, current/previous labels, cross-edge rollback eligibility, stale backup files, contradictory
records, unknown schema versions, and unknown storage entries.

- [ ] **Step 2: Implement record parsing and atomic writes**

Write to a unique root-owned mode-0600 sibling, fsync, then rename. Reject symlink records,
non-regular files, wrong owners/modes, broken activation chains, duplicate IDs, and a current
symlink inconsistent with records.

- [ ] **Step 3: Write and implement lock tests**

Use `flock` on one root-owned lock: exclusive for mutating commands and shared for discovery.
Acquisition is bounded and returns status `12` with the recorded operation, PID, and start time;
tests synchronize fake contenders with events, never sleeps.

- [ ] **Step 4: Write and implement manual-release adoption**

Test layout, permissions, immutable content digest, application version, migration fingerprints,
current symlink containment, runtime health, and topology. Record unavailable source/artifact
fields as `unknown`; require explicit confirmation; never modify adopted release contents.

- [ ] **Step 5: Implement human and JSON discovery**

Sort releases by activation and backups by creation time. Emit all specification fields and
warnings, empty arrays with status `0`, and status `10` for contradictions. Confirm backup dump
paths are regular files without running `pg_restore --list`.

- [ ] **Step 6: Verify and commit**

Run:

```sh
cd ops
uv run pytest tests/releases tests/workflows/test_discovery.py -q
```

Commit: `Track and list deployment lifecycle state`

---

### Task 7: Add validated local backups and retention

**Files:**

- Create: `ops/backup/taskman-backup`
- Create: `ops/systemd/taskman-backup.service`
- Create: `ops/systemd/taskman-backup.timer`
- Create: `ops/taskman_ops/services/backups.py`
- Create: `ops/taskman_ops/workflows/backup.py`
- Create: `ops/tests/services/test_backups.py`
- Create: `ops/tests/workflows/test_backup.py`

**Interfaces:**

- Consumes: local PostgreSQL, `/etc/taskman/pgpass`, managed backup root, and lifecycle records.
- Produces: `create_backup(remote, context, reason) -> BackupRecord` and bounded recognized-file
  retention.

- [ ] **Step 1: Write failing backup-contract tests**

Assert restrictive temporary creation, custom-format `pg_dump`, fresh `pg_restore --list`,
fsync/atomic rename, metadata only after validation, cleanup of private partial files, statuses
`6`, and no password in argv/output.

- [ ] **Step 2: Implement the root-controlled backup command**

Use non-secret `--host`, `--username`, and `--dbname` arguments with
`PGPASSFILE=/etc/taskman/pgpass`. Accept only validated reason/release IDs, generate the backup ID
internally, create mode-0600 output, and write its `BackupRecord` atomically after validation.

- [ ] **Step 3: Implement timer convergence and safe pruning**

Install and validate the service/timer. Prune only files referenced by valid records beyond
retention; preserve the newest matching pre-deploy backup and warn about unrecognized entries.

- [ ] **Step 4: Wire the explicit backup workflow**

Acquire the exclusive lifecycle lock, preflight database health and capacity, create a
`scheduled`-reason backup, report its exact ID/path/size without secrets, and leave application
state untouched.

- [ ] **Step 5: Verify and commit**

Run: `cd ops && uv run pytest tests/services/test_backups.py tests/workflows/test_backup.py -q`

Commit: `Add validated local database backups`

---

### Task 8: Implement deployment verification as an independent capability

**Files:**

- Create: `ops/taskman_ops/verification.py`
- Create: `ops/taskman_ops/workflows/verify.py`
- Create: `ops/tests/test_verification.py`
- Create: `ops/tests/workflows/test_verify.py`

**Interfaces:**

- Consumes: `Remote`, environment config, selected manifest, local curl, public HTTPS, systemd,
  sockets, and journal evidence.
- Produces: `verify_installation(remote, config, expected_release_id=None) -> VerificationReport`.

- [ ] **Step 1: Write failing verification tests**

Cover service active/process path, exact local and public `ready`, HSTS, Caddy active, Phoenix and
distribution loopback-only, PostgreSQL non-public, no EPMD listener, bounded polling, startup log
failure, wrong running release, and public failure after local success.

- [ ] **Step 2: Implement structured checks**

Never parse secrets or dump raw environment/journal content. Each check records
`name`, `status`, and fixed summary. Map startup/lifecycle failures to `8` and readiness/public
failures to `9`; preserve the report and next safe action.

- [ ] **Step 3: Wire read-only `verify`**

Run configuration validation, strict preflight, shared lifecycle lock, state validation, and full
verification without sudo mutation. Human and JSON reports contain the same facts.

- [ ] **Step 4: Verify and commit**

Run: `cd ops && uv run pytest tests/test_verification.py tests/workflows/test_verify.py -q`

Commit: `Verify deployed Taskman topology`

---

### Task 9: Converge supported host and service capabilities

**Files:**

- Create: `ops/taskman_ops/host/baseline.py`
- Create: `ops/taskman_ops/host/firewall.py`
- Create: `ops/taskman_ops/services/postgresql.py`
- Create: `ops/taskman_ops/services/caddy.py`
- Create: `ops/taskman_ops/services/systemd.py`
- Modify: `ops/caddy/Caddyfile`
- Modify: `ops/caddy/render-caddyfile`
- Modify: `ops/systemd/taskman.service`
- Create: `ops/tests/host/test_baseline.py`
- Create: `ops/tests/host/test_firewall.py`
- Create: `ops/tests/services/test_postgresql.py`
- Create: `ops/tests/services/test_caddy.py`
- Create: `ops/tests/services/test_systemd.py`

**Interfaces:**

- Consumes: validated config/secrets, pyinfra desired-state operations, and Tasks 5–8.
- Produces: independently callable, idempotent baseline and service convergence capabilities.

- [ ] **Step 1: Write failing baseline and firewall tests**

Assert minimal packages, unattended security updates without reboot, `taskman` nologin account,
exact owners/modes, SSH allow-before-enable ordering, ports 80/443, denial of public
4000/5432/6789/4369, and fresh SSH verification after UFW changes.

- [ ] **Step 2: Implement declarative baseline capabilities**

Use pyinfra operations for stable desired state. Do not branch during prepare on facts changed by
earlier operations. Do not alter SSH authentication, authorized keys, distribution release, Ubuntu
Pro, or automatic reboot policy.

- [ ] **Step 3: Write failing PostgreSQL convergence tests**

Assert loopback binding, SCRAM, least-authority role/database creation, password through sensitive
stdin rather than argv, root mode-0600 pgpass, idempotent reruns, and refusal to replace existing
incompatible role/database state.

- [ ] **Step 4: Implement PostgreSQL and backup-service convergence**

Install Ubuntu 26.04 PostgreSQL packages, validate native configuration before restart, create the
role/database through protected stdin, verify with the rendered application connection, and
install/enable Task 7's timer.

- [ ] **Step 5: Write and implement Caddy/systemd convergence**

Test official authenticated Caddy repository configuration, rendered-host validation, staged
`caddy validate`, `systemd-analyze verify`, runtime env mode `0600`, service unit installation,
daemon reload only on change, and no service start before the first release transaction.

- [ ] **Step 6: Prove convergence in a controlled Ubuntu container**

Run unit tests, then a container fixture capable of package/user/file operations twice. First run
must report changes; second must report none; deliberate owned-file drift must be repaired.
Explicitly skip and report systemd/UFW/ACME checks that require a VM.

- [ ] **Step 7: Commit**

Commit: `Converge Taskman host services`

---

### Task 10: Stage, migrate, activate, and verify a release

**Files:**

- Create: `ops/taskman_ops/releases/staging.py`
- Create: `ops/taskman_ops/releases/activation.py`
- Create: `ops/taskman_ops/workflows/deploy.py`
- Create: `ops/tests/releases/test_staging.py`
- Create: `ops/tests/releases/test_activation.py`
- Create: `ops/tests/workflows/test_deploy.py`

**Interfaces:**

- Consumes: verified artifact, current/adopted release, exclusive lock, backup and verification
  capabilities.
- Produces: `stage_release`, `activate_release`, migration-edge records, and `deploy(invocation)`.

- [ ] **Step 1: Write failing secure-staging tests**

Cover unique mode-0600 upload, remote checksum, archive reinspection, restrictive extraction,
complete-directory atomic rename, root/taskman permissions, cleanup, resume of matching complete
content, and refusal of partial or same-ID/different-checksum content.

- [ ] **Step 2: Implement immutable staging**

Use exact validated paths only. Never mutate an existing final release. Remove only the operation's
known private upload/staging path after verifying containment; report unexpected residue.

- [ ] **Step 3: Write failing migration/activation state-machine tests**

Cover exact-current healthy no-op, adoption, pre-deploy backup, no-change policy, required explicit
policy for changed migrations, stop/migrate/symlink/start order, idempotent `ExecStartPre`,
readiness, and activation-edge record content.

- [ ] **Step 4: Implement the deployment transaction**

Acquire the exclusive lock; preflight capacity/state; stage; create and validate backup; stop;
run candidate `bin/migrate` using protected systemd environment; atomically replace `current`;
start; verify; then finalize the activation record. Keep Caddy running.

- [ ] **Step 5: Implement fail-safe reports**

On migration failure, leave old `current` selected and Taskman stopped. On activation/start/readiness
failure, leave the observed selection and database untouched, do not silently start old code, and
report changed stages, service state, backup ID, release IDs, and exact next safe commands.

- [ ] **Step 6: Verify failure injection and commit**

Run:

```sh
cd ops
uv run pytest tests/releases/test_staging.py tests/releases/test_activation.py \
  tests/workflows/test_deploy.py -q
```

Commit: `Deploy immutable Taskman releases`

---

### Task 11: Compose the complete provisioning workflow

**Files:**

- Create: `ops/taskman_ops/workflows/provision.py`
- Create: `ops/tests/workflows/test_provision.py`

**Interfaces:**

- Consumes: config, secrets, build/artifact, strict preflight, host convergence, shared release
  transaction, and verification from Tasks 3–10.
- Produces: `provision(invocation)`.

- [ ] **Step 1: Write failing orchestration tests**

Assert local validation/build and redacted plan precede confirmation and SSH; immutable discovery
precedes mutation; baseline precedes firewall reconnect; PostgreSQL/backups precede Caddy/systemd;
and the first release transaction occurs only after every prerequisite validates.

- [ ] **Step 2: Implement the ordered state machine**

Compose existing capabilities without duplicating deployment logic. A pre-release failure reports
safely rerunnable partial convergence and does not undo packages, firewall, database, or secrets.
Once the first release transaction starts, preserve the exact deployment failure behavior.

- [ ] **Step 3: Test idempotence and external acceptance reporting**

With a stateful fake remote, the first run reports exact changes and the second reports no
convergent changes. Both perform procedural verification. The success report lists interactive
administrator, HTTPS sign-in, Resend invitation, API-key, LiveView, and off-host-copy acceptance
steps without claiming they were automated.

- [ ] **Step 4: Verify and commit**

Run: `cd ops && uv run pytest tests/workflows/test_provision.py -q`

Commit: `Compose clean-host provisioning`

---

### Task 12: Add compatibility-gated rollback

**Files:**

- Create: `ops/taskman_ops/workflows/rollback.py`
- Create: `ops/tests/workflows/test_rollback.py`

**Interfaces:**

- Consumes: activation-edge graph, installed releases, fresh backup, activation, and verification.
- Produces: `rollback(invocation, release_id)`.

- [ ] **Step 1: Write failing rollback tests**

Cover unknown/current/incomplete target refusal, target outside managed root, missing edge, direct
and multi-edge compatible paths, any intervening `restore-required` edge, confirmation, backup
failure, activation failure, and readiness failure.

- [ ] **Step 2: Implement graph-based eligibility**

Traverse the exact current-to-target activation chain and refuse status `10` if it is incomplete or
contains `restore-required`. There is no bypass flag.

- [ ] **Step 3: Implement the rollback transaction**

After confirmation and exclusive lock, verify healthy database, create `pre-rollback` backup, stop,
atomically select target, start without reverse migrations, verify, and append a new activation
edge that preserves history.

- [ ] **Step 4: Verify and commit**

Run: `cd ops && uv run pytest tests/workflows/test_rollback.py -q`

Commit: `Add compatibility-gated release rollback`

---

### Task 13: Add guarded restore and exact cleanup

**Files:**

- Create: `ops/taskman_ops/workflows/restore.py`
- Create: `ops/taskman_ops/releases/cleanup.py`
- Create: `ops/taskman_ops/workflows/cleanup.py`
- Create: `ops/tests/workflows/test_restore.py`
- Create: `ops/tests/releases/test_cleanup.py`
- Create: `ops/tests/workflows/test_cleanup.py`

**Interfaces:**

- Consumes: exact backup/release records, local PostgreSQL, exclusive lock, typed confirmation, and
  verification.
- Produces: `restore(invocation, backup_id)`, `cleanup_plan`, and `cleanup(invocation)`.

- [ ] **Step 1: Write failing restore tests**

Cover exact-ID-only lookup, stale/missing dump refusal, fresh `pg_restore --list`, capacity,
pre-restore backup, temporary database ownership, schema/migration validation, session termination,
two-step rename, intended release selection, and full readiness.

- [ ] **Step 2: Implement typed confirmation and restore transaction**

Require the exact environment and backup ID. Restore into a unique validated temporary database;
retain the old canonical database under a recorded recovery ID after swapping. Never accept a path
or glob from the CLI.

- [ ] **Step 3: Implement every restore failure boundary**

Before swap, remove only the verified tool-created temporary database and restart unchanged state
where safe. On partial swap, attempt only the exact inverse rename. After swap, retain both
databases, leave Taskman stopped on uncertainty, and report exact manual recovery commands without
secrets. Map failures to `11`.

- [ ] **Step 4: Write failing cleanup eligibility tests**

Cover retention, current/previous/rollback-required releases, newest pre-deploy backup, known
completed staging files, retained database IDs, symlinks, unrecognized objects, wildcard/path
escape, and typed target confirmation.

- [ ] **Step 5: Implement plan-first exact cleanup**

Return an immutable list of exact typed targets. Revalidate each immediately before deletion under
the exclusive lock; abort the complete cleanup if state changed. Report what was removed and
whether another retained artifact can recover it.

- [ ] **Step 6: Verify and commit**

Run:

```sh
cd ops
uv run pytest tests/workflows/test_restore.py tests/releases/test_cleanup.py \
  tests/workflows/test_cleanup.py -q
```

Commit: `Add guarded restore and cleanup`

---

### Task 14: Complete interactive administration, dry runs, end-to-end contracts, and operations docs

**Files:**

- Create: `ops/taskman_ops/workflows/create_admin.py`
- Modify: `ops/taskman_ops/cli.py`
- Modify: `ops/taskman_ops/output.py`
- Create: `ops/tests/workflows/test_create_admin.py`
- Create: `ops/tests/test_dry_run.py`
- Create: `ops/tests/test_end_to_end.py`
- Modify: `docs/deployment.md`
- Modify: `README.md`
- Modify: `docs/README.md`

**Interfaces:**

- Consumes: all completed capabilities and workflows.
- Produces: complete operator CLI, interactive TTY bridge, consistent dry-run behavior, runbook,
  and repository verification evidence.

- [ ] **Step 1: Write and implement the interactive administrator bridge**

Tests require a real local TTY, exact constrained `systemd-run` properties, no email/password in
argv/environment/result, refusal without a TTY, and propagation of the remote command status.
Invoke only `/opt/taskman/current/bin/create-admin` as `taskman` with the root-only environment
file.

- [ ] **Step 2: Write and implement cross-command dry-run tests**

For every mutating command, assert local schema/secrets/artifact checks, SSH/facts, and planned
operations occur while remote mutation, service changes, database changes, and confirmations do
not. `build` may create a local artifact; read-only commands remain read-only with or without
`--dry-run`.

- [ ] **Step 3: Add controller-level failure-injection scenarios**

Drive fake remote state through corrupt checksums, capacity failure, interrupted upload, invalid
dump, missing compatibility declaration, migration/start/readiness/public failures, lock
contention, contradictory discovery, rollback refusal, restore swap failures, and cleanup races.
For each case assert selected release, service state, database preservation, backup retention,
exit status, redaction, and next action.

- [ ] **Step 4: Rewrite the deployment runbook around automation**

Document workstation prerequisites, SOPS/age setup, environment creation, build/provision/deploy,
`releases`/`backups`, backup, rollback, restore, cleanup, exit statuses, state-specific recovery,
and first-admin acceptance. Retain standalone manual recovery and release-command trust warnings.
State plainly that VPS creation, DNS, provider firewall, Resend, and off-host backup are external.

- [ ] **Step 5: Update discoverability and validate all examples**

Add the plan to `docs/README.md` and the smallest hosted-operation link to `README.md`. Run every
`--help` example and validate all local Markdown links. Search product surfaces for planning terms
and all repository files for example canary secrets.

- [ ] **Step 6: Run the complete repository gate**

Run:

```sh
cd ops
uv run pytest -q
cd ..
mix precommit
docker buildx build --platform linux/amd64 --file ops/builder/Containerfile .
```

Expected: all Python and Elixir tests pass, formatting is clean, production compilation has no
warnings, and the pinned builder completes.

- [ ] **Step 7: Record the unresolved external acceptance gate**

Do not run it without authorization. Record that full readiness still requires an authorized
disposable Ubuntu 26.04 amd64 VPS run covering first/second provision, HTTPS/HSTS, interactive
admin, email, API key, LiveView, deploy, rollback, migration failure, restore, firewall/listeners,
and secret leakage inspection.

- [ ] **Step 8: Commit**

Commit: `Document and verify deployment automation`

---

## Plan Self-review

- Every goal, command, safety refusal, exit category, trust boundary, and repository boundary in
  the approved specification maps to at least one task above.
- The health, controller core, artifacts, remote boundary, records, backups, verification,
  provisioning, deployment, rollback, restore/cleanup, and final integration gates are separately
  reviewable.
- Shared types are defined once in the file/interface map and consumed consistently by later
  tasks.
- No task authorizes a real VPS, DNS, provider firewall, Resend, production-secret, push, merge, or
  deployment mutation.
- The disposable-host acceptance remains explicit unresolved evidence until separately authorized.

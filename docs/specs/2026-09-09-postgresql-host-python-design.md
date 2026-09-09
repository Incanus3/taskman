# PostgreSQL host-side Python configuration

Status: parked proposal; scope and invocation boundary approved, written review pending.
Updated: 2026-09-09.

Workstream task: `tas-sidn`. Resume state: [PostgreSQL host-side Python](../handoffs/postgresql-host-python.md).

The operator chose to finish VPS provisioning using the verified current implementation before
this refactor. This proposal is not a provisioning prerequisite. Resume design approval and
planning later against the observed running-host baseline.

## Purpose and authority

Replace the substantial embedded PostgreSQL inspection/configuration shell workflow with a
focused host-side Python procedure. Preserve supported behavior, safety refusals, native tools,
and recovery consequences. This is not a general shell-removal project.

The [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md) remains the canonical
deployment architecture. This document specifies a bounded extension to its helper vocabulary
and the implementation of its PostgreSQL custom operation. Until implemented and verified, it
does not describe the running product. The [runbook](../deployment.md) owns operator recovery;
the [development guide](../development.md) owns verification commands.

The operator approved PostgreSQL-first extraction, reuse of the existing transient helper and
authenticated connection, two narrow inspect/configure operations, unchanged protected password
handling, and preservation of native-HBA validation and its accepted crash window.

## Starting state and evidence

The corrections baseline is local commit `8266656` on
`dedicated-host-deployment-automation`. Prior verification recorded 886 operations tests and 805
Elixir tests through `mix precommit`, plus pinned-runtime configuration checks. These are historical
baseline results, not verification of this refactor. No refactor code exists at specification time.

`services/postgresql.py` currently owns desired-state dataclasses, pyinfra package/staged-file
declarations, generated shell inspection and configuration, and separate protected role/database
convergence. The shell uses `pg_lsclusters`, `pg_conftool`, `pg_ctlcluster`, `postgres -C`, and
peer-authenticated `psql`, with filesystem parsing and shell traps around HBA replacement.

The existing `PyinfraRemote(host, config, state=state)` adapter can use the currently authenticated
pyinfra SSH connector. `helper_client/runner.py` already provides bounded, checksum-verified,
private transient helper transfer and correlated JSON results. Reuse those mechanisms; do not
create another SSH connection, upload implementation, or arbitrary-code executor.

The VPS readiness work remains paused separately in
[its handoff](../handoffs/ops-vps-readiness.md), tracked by `tas-b7kd`. Its failed immutable release
must not be edited or retried; replacement build and exact recoverable retirement remain separate
work. This design authorizes neither host mutation nor release retirement, push, merge, or publication.

## Scope and exclusions

Move cluster/runtime observation, desired-state comparison, configuration sequencing, HBA
backup/replacement/restoration, and post-restart validation to standard-library host Python.
Keep pyinfra as the owner of provisioning order and change reporting.

Keep unchanged:

- Package selection, root/configuration inputs, staged HBA content and mode, and native topology.
- Role/database adoption policy, least privilege, password creation, protected pgpass installation,
  and application connection verification after declarative provisioning.
- Caddy, UFW, systemd, release procedures, builder/toolchain, and unrelated short shell commands.
- Public CLI commands, confirmations, dry-run semantics, output schema, and error categories.

Do not add a resident helper, temporary PostgreSQL instance, alternate HBA path, generic workflow
engine, new controller/host dependency, configurable command/path escape hatch, automatic recovery
of ambiguous artifacts, or blanket configuration rollback.

## Architecture and file ownership

Paths below are relative to `ops/taskman_ops/`.

| Owner | Change |
| --- | --- |
| `services/postgresql.py` | Retain plans, pyinfra declarations and protected database work; replace shell generation with request/result integration |
| `host_helper/postgresql.py` | Own selected-cluster observation, native command policy, desired-state checks, and the explicit configuration/recovery procedure |
| `host_helper/operations/postgresql.py` | Validate narrow requests, take lifecycle lock, invoke the capability and project bounded final results |
| `host_helper/commands.py` | Add opt-in cancellation for PostgreSQL; preserve defaults and behavior of existing consumers |
| `host_protocol/operations.py`, `host_helper/__main__.py` | Add exact operation names and dispatch |
| `helper_client/package.py` | Include the new modules only in the transient package; preserve scheduled-backup least authority |
| `provisioning.py` | Pass validated non-secret configuration to the operation adapter where needed; retain declaration/consequence order |

Use small private dataclasses for observed cluster identity and in-flight HBA restoration state.
Keep them local to PostgreSQL; do not add generic state machines or durable journals. Extract a
separate filesystem module only if the cohesive HBA transition becomes independently substantial.
Reuse `host_helper/commands.py` for bounded argv subprocess execution.

The pyinfra operation receives validated configuration alongside its plan. During execution its
inspection invokes the existing helper using a `PyinfraRemote` wrapping that same host/state.
If configuration is required, its `FunctionCommand` invokes configuration through the same
adapter. Do not call `run_deploy` recursively. No helper is invoked during prepare or dry-run.
Packages and staged HBA must execute first. An unchanged inspection yields no mutation operation;
configuration always reobserves authority and never trusts an earlier inspection snapshot.

Keep pyinfra's existing execution-based change semantics. A converged inspection skips the
`FunctionCommand` and reports no change. Once yielded and executed, that command counts as
changed in pyinfra even if another actor converged state between inspection and configuration,
and the helper consequently reports `changed: false`. The helper boolean describes its own
mutation; it does not override pyinfra's completed-operation accounting. Accept this conservative
race result rather than mutating private operation metadata or running the mutation inside the
inspection generator merely to change accounting. Test the distinction explicitly.

## Request and result contract

Add `inspect_postgresql` and `configure_postgresql` to the finite transient vocabulary. Keep the
version-2 envelope and bounds: this is an additive operation extension, not an envelope redesign.
The matching helper is packaged and transferred for each invocation; old helpers reject the new
names. There is no installed transient-helper compatibility fallback.

Both requests require empty `expected_state`, the existing exact two-root `paths` mapping, and
these exact `parameters` fields:

- `package_track`: null or the validated numeric PostgreSQL package track.
- `database`: exact `host`, `port`, `role`, and `name` fields, with existing loopback, integer-port,
  and safe identifier constraints; host-side validation must not import controller dependencies.
- `hba_sha256`: lowercase 64-hex digest of the desired staged HBA bytes.

The staged file is fixed at `/etc/taskman/pg_hba.conf.staged`; the final file and executable paths
are derived from the validated selected Ubuntu cluster. SCRAM is fixed. No HBA text, credentials,
arbitrary settings, commands, owner overrides, or final-path overrides cross the protocol.
Controller test-only path overrides become local capability test seams, not privileged wire inputs.

Successful inspection state is exactly `changed: false` and `configuration_required: <boolean>`.
It reports required configuration for safe drift or an unprovable desired-state predicate, matching
the current probe; it does not certify that mutation is safe. Malformed input, transport failure,
or inability to acquire the lock is an error, not ordinary drift.

Successful configuration state is exactly `changed: <boolean>`. Failures carry `changed`,
`failed_boundary: "postgresql"`, and a fixed `reason` selected from `invalid_request`,
`unsafe_state`, `command_failed`, `recovery_required`, or `lock_unavailable`; only lock failure
also carries `locked: true`. Outcomes are `refused` for invalid/unsafe authority, `manual` when
recovery material needs inspection, and `retryable` for command failure without such material or
lock failure. Messages and
warnings are fixed and contain no raw subprocess output, configuration contents, or exceptions.
Validate exact operation-specific successful state at the controller boundary.

Preserve status `10` for cluster/runtime safety refusal and the existing provisioning failure
translation (status `5`) for ordinary command/transport errors, including restart/postcheck command
failure even when recovery material remains; lock contention maps to `12`. `manual` with
`recovery_required` maps to `10` when preexisting evidence prevents safe action or restoration
cannot be completed (failed restoration takes precedence over the original command error).
On other native command failures, retain `reason: command_failed` and status `5`
even when the outcome is `manual` because evidence requires inspection. Preserve
pyinfra's categorized-error propagation. Never use helper process status `0` as proof of success:
decode and validate the correlated final result. Remove the PostgreSQL shell status-marker parser.

Known earlier changes remain reported after failure. A dispatched configuration helper with a
lost or invalid result means possible mutation and must not become `changed: false`; inspection
transport loss alone does not prove a PostgreSQL mutation. The enclosing deploy retains its
existing conservative possible-change reporting once execution has begun. Preserve cleanup warnings through
pyinfra and the public error boundary. Transient upload cleanup is not a managed configuration change.

## Observation and native command semantics

Use the current executable behavior, not weaker unused parser helpers, as the compatibility
baseline. Select exactly one `pg_lsclusters --no-header` row in the requested track (or across all
tracks when unspecified). Validate numeric version/port, safe cluster name, online/down status,
and `postgres` owner. Refuse zero or multiple candidates. Do not simply choose the first row.

Read the data directory with `pg_conftool -s VERSION CLUSTER show data_directory`; retain its
absolute, canonical, restricted-path check. Validate native configuration directory and regular
non-link configuration/HBA files without changing parent metadata. Parse the eight-line
`postmaster.pid`, including PID, data directory, start epoch, live port, socket and ready status.
Require `pg_ctlcluster VERSION CLUSTER status` and cross-check live SQL port/data/start identity.

Administrative SQL uses `runuser -u postgres -- psql --no-psqlrc --tuples-only --no-align
--field-separator | --host /var/run/postgresql --port LIVE_PORT --username postgres
--dbname=postgres --command SQL` as individual argv elements. Use the observed old live port
before restart and the desired port afterward. Verify `SHOW config_file`, `SHOW hba_file`, and
`SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL` against the exact selected cluster.
An unsuccessful query is never an empty successful result.

Inspect staged digest, final bytes and `postgres:postgres:0640` metadata, absence of recovery
material, configured settings, and native effective settings. Use the versioned `postgres`
binary with `--config-file=... -C SETTING` as postgres. Retain all four effective settings:
`hba_file`, `listen_addresses`, `port`, and `password_encryption`.

Retain `pg_conftool` for setting configuration. Python replaces only the narrowly recognized
unquoted `127.0.0.1` assignment emitted by that utility, preserving comments, other content and
file metadata. Do not normalize IPv6 or introduce a PostgreSQL configuration-file rewriter.

Use finite subprocess output and timeout limits through the existing runner. Give each helper
procedure a 600-second total budget, including a 5-second lock acquisition allowance and a
reserved 30-second bounded cleanup/restoration allowance; individual commands use at most
60 seconds and the remaining work budget. The existing 660-second transport allowance encloses
that budget. Signal handling must terminate/reap active command groups before bounded restoration;
do not leave a parser/restart child racing the restored file. Add an optional cancellation event
to `run_command`, defaulting to no cancellation. With that event supplied, bounded waiting must
notice cancellation, abort/reap the existing process group and join its I/O workers before raising
a fixed cancellation error. PostgreSQL's temporary HUP/INT/TERM handlers only set the event;
the procedure checks it between commands and enters bounded cleanup after runner termination.
Restore previous handlers on exit. Restoration uses its reserved deadline without the already-set
work cancellation event; repeated handled signals must not restart or interrupt restoration.
Default calls retain their current behavior. Test cancellation and unchanged existing consumers;
this optional seam is the only shared-runner extension authorized by this design.

## Configuration and recovery procedure

Both operations use the existing lifecycle lock for coherent observation and configuration.
Baseline directories precede invocation; no outer lock is held across the helper call. Recheck
all authority under the configure lock. Do not claim that package installation or all provisioning
has become one transaction.

1. Prove a reachable online selected cluster, PID/SQL identity, native active config/HBA paths,
   current parser success, staged digest, and absence of any existing recovery path or symlink.
2. Compare configuration and HBA bytes/metadata. Set only differing settings with `pg_conftool`,
   normalize the exact IPv4 assignment if necessary, and validate native effective settings.
   These configuration changes may survive later failure; there is no whole-file rollback.
3. If HBA replacement is needed, create the exact sibling `pg_hba.conf.taskman-backup` directory
   privately as root, mode `0700`. Preserve the old bytes in `pg_hba.conf`, mode `0600`, and old
   numeric `uid:gid:mode` plus newline in `metadata`, mode `0600`. Never overwrite existing evidence.
4. Create a sibling candidate, assign postgres ownership and mode `0640`, and atomically replace
   the native HBA. Query the existing live server's rules view before any reload/restart.
5. On parser rejection, query failure, or handled HUP/INT/TERM before successful validation,
   atomically restore the old bytes and metadata. Remove only owned temporary files and the
   completed restoration's backup. If restoration fails, preserve available evidence and fail.
6. After candidate parser success, do not restore the old HBA automatically. Restart only when
   required. On restart or postcheck failure retain the recovery directory and refuse a later
   mutation until the operator resolves it. This differs deliberately from validation failure.
7. Reobserve runtime PID, desired port, SQL identity, native config/HBA paths, and parser success.
   Only then clean this invocation's recovery material and report success.

SIGKILL, power loss, or controller transport termination can bypass cleanup. The accepted live-path
disk window remains; Python does not eliminate it. Existing recovery artifacts, even recognizable
ones, require manual inspection rather than automatic replay. Retain their current names/content
so runbook recovery remains applicable. Do not delete unrelated native or legacy-HBA residue.

## Alternatives and rationale

Moving the shell to an external `.sh` file would improve navigation but retain quoting, parsing,
trap, and state-management complexity. A standalone Python upload/invocation path would duplicate
the established helper transport and result contract. Reusing the transient helper adds two
explicit operations and a small pyinfra integration seam while keeping host-local consequences
together. Replacing every shell snippet would expand scope without demonstrated benefit.

The native-HBA decision is unchanged: the existing accepted design records VPS evidence that
`hba_file` is startup-only and the rules view reads current disk contents. Source provenance is
the PostgreSQL 18 [file-location documentation](https://www.postgresql.org/docs/18/runtime-config-file-locations.html)
and [rules-view documentation](https://www.postgresql.org/docs/18/view-pg-hba-file-rules.html).
No new external experiment is claimed by this specification.

## Verification and acceptance

Start from the observable scenarios in `ops/tests/services/test_postgresql.py`. Characterize
behavior before removing shell execution; migrate assertions to the Python capability and real
helper entry point, not merely generated source-text expectations. Keep unrelated role/pgpass
tests intact. Do not mechanically preserve obsolete shell implementation assertions.

Required coverage:

- Default/custom ports, requested track, zero/multiple/malformed clusters, stopped/unreachable
  server, contradictory PID/SQL/config/HBA identities, and unavailable parser.
- Desired-state no-op, metadata drift, IPv4 normalization, IPv6 preservation, exact staged digest,
  unchanged native parent metadata, and recovery-residue refusal.
- Configuration/native-validation failures before restart; candidate parser failure and signals
  restore exact bytes/metadata; successful transition cleans evidence; restart/postcheck failure
  preserves evidence and blocks retry; restoration failure never reports success.
- Old live socket before restart and desired socket afterward, including interrupted custom-port
  transitions; explicit argv and bounded output/timeout/process cleanup.
- Exact request/result schemas, invalid or extra parameters, forbidden path/secret input, malformed
  or mismatched results, transport loss, cleanup warnings, and partial-change aggregation.
- Real programmatic pyinfra prepare/execute, first change, converged second run, drift and dry-run;
  connection reuse and no nested deploy or prepare-time host mutation.
- Both deterministic zipapps execute with `python3 -I -S`; transient package includes new code
  without third-party imports, scheduled package excludes PostgreSQL provisioning capabilities.

Update architecture guards to remove the PostgreSQL substantial-shell exemption, not loosen the
guard globally. Remove unused internal shell renderers/status parsing once consumers migrate.
Run focused tests, then the full operations recipe and `mix precommit` from the development guide.
Use independent review of the changed safety boundary and its tests, not a full-branch review.

Local fakes and subprocess tests do not prove real PostgreSQL/systemd behavior. A separately
authorized supported-host acceptance check must exercise native parsing, ownership, first and
second convergence and custom-port behavior; report its absence as uncertainty. Do not inject
failure, change ports, or restart the existing VPS merely to satisfy this design's test matrix.

## Next-session checklist

Design verification on 2026-09-09: independent scoped review approved the corrected pyinfra
accounting, cancellation seam and error precedence. Local links, placeholders and whitespace
checks passed; `mix precommit` passed 805 tests. No refactor implementation or host acceptance
has been performed. These checks do not replace the operator's written-spec approval.

1. Obtain written-spec approval, then write and review the implementation plan; create bounded
   repository-local Beads work through `br` linked to `tas-b7kd` before implementation.
2. Update the active handoff and use the approved-plan clean-session boundary. No implementation
   begins in this design session unless the operator explicitly asks to continue here.
3. Recheck the actual checkout and applicable guidance, read this entire spec and the canonical
   deployment design, then execute the plan with characterization, implementation and review.
4. Preserve the VPS/artifact checkpoint. After verification, update the canonical deployment
   vocabulary/ownership description and runbook only where behavior or diagnostics require it;
   mark this proposal implemented with evidence rather than leaving competing current guidance.

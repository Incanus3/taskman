# Operations test parallelism measurements

## Scope and inputs

Investigation for `tas-sr4b.13`: preserve all 1,462 operations tests and their assertions while
measuring bounded process parallelism against the recorded 219.61-second serial baseline.
Source checkpoint for the initial investigation: `8f1918614123fcba78fd874d45c20d1c9d51cfb4`, with the previously verified
restore-retention fixture optimization and the deterministic parametrization correction below.
No production code, durable dependency, default scheduling, or host state changed during those
trials. Subsequent dependency adoption is recorded below.

Workstation: Intel Core i9-10980XE, 18 physical cores / 36 logical CPUs, Python 3.12.12,
pytest 8.4.2, pytest-xdist 3.8.0, execnet 2.1.2. Runs were sequential, without concurrent
application verification. Times below are pytest-reported elapsed wall times; dependency
installation and outer `uv` startup are excluded. This is local evidence, not a CI timing guarantee.

## Parallel-safety audit

- Filesystem fixtures and synthetic Git repositories use private `tmp_path` roots. Packaged helper
  archives and runtime-state files are private to each test. Source/architecture tests inspect
  checkout files; mutation tests modify synthetic repositories rather than the checkout.
- Remote SSH, Docker build, PostgreSQL, UFW, and service mutations are substituted. Local pyinfra
  file operations remap destinations into private roots; native systemd inspection uses `--root`
  and checks diagnostics. Configuration addresses and fixed managed paths are generally test data,
  not actual network listeners or host destinations.
- The real socket listener is an AF_UNIX socket inside `tmp_path`; PTYs are newly allocated per
  process. The release prompt test reads an optional extracted release or compiles sources in a
  separate Elixir VM. Administrator bridge tests run `mix run --no-start` and therefore share
  repository Mix build output: tested successfully with the existing compiled workstation state,
  but cold-build parallel safety is not established. Avoid concurrent source edits/application
  builds during these trials.
- Environment, cwd, secret registry, and pyinfra monkeypatches are process-local. Test-level
  scheduling keeps an individual test's lifecycle sequence on one worker. Isolated helper calls
  still use fresh `python -I -S` processes and retain fresh authority discovery.
- The isolated helper runner kills and drains timed-out children and closes readiness pipes.
  PTY tests kill/wait on failure and close descriptors; subprocess timeout/descendant termination
  coverage uses private markers. Strict timing assertions remain unchanged and may fail under
  resource contention; passing trials do not prove universal scheduling safety.

The initial two-worker trial refused collection (exit 1, 1.52 seconds, no tests executed).
`test_each_declared_operation_round_trips_through_both_codecs` parametrized directly from the
immutable `OPERATION_NAMES` frozenset. Per-process hash randomization changed case order.
Sorting only the parametrization input fixed worker agreement while preserving every case and
the production frozenset. Focused two-worker verification passed all 14 tests in 0.55 seconds.

## Reproduction and results

Supply xdist temporarily, without editing `ops/pyproject.toml` or `ops/uv.lock`:

```sh
uv run --project ops --with pytest-xdist==3.8.0 pytest ops/tests -n 2 --dist worksteal --max-worker-restart=0 --durations=10
uv run --project ops --with pytest-xdist==3.8.0 pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0 --durations=10
```

Repeat trials used `--durations=5`; this changes only the printed duration report.
[pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
describes work stealing for tests of differing duration. File/scope scheduling was avoided because
it would concentrate expensive public integration tests. Worker restart was disabled so a crash
would remain visible.

| Execution | Tests passed | Failures | Seconds | Warnings |
| --- | ---: | ---: | ---: | ---: |
| Recorded serial baseline | 1,462 | 0 | 219.61 | Not recorded |
| Two workers, first trial | 1,462 | 0 | 114.77 | 156 |
| Two workers, repeat | 1,462 | 0 | 113.27 | 156 |
| Four workers, first trial | 1,462 | 0 | 55.47 | 158 |
| Four workers, repeat | 1,462 | 0 | 59.54 | 158 |

Warnings report Python's multi-threaded `fork`/`forkpty` deprecation through pyinfra/gevent
and the administrator PTY bridge. They were not suppressed. No worker crash or test exclusion
occurred. These warnings and shared Mix output remain limitations, rather than proof of an
xdist-specific defect or permission to redesign production workflows.

## Decision boundary

Four workers give substantial headroom below the 120-second local target; two workers gave
much less headroom. Recommend an explicit, bounded four-worker operations command after operator
acceptance: add `pytest-xdist>=3.8,<4` to the ops dev dependency group, refresh the lock, and
document `uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0`
as the bounded parallel gate without changing pytest's default addopts.
Keep `uv run --project ops pytest ops/tests` as the serial path. Durable xdist dependency
or default-command changes were unaccepted at the measurement checkpoint. The operator subsequently
accepted the xdist dev dependency and explicit bounded gate on 2026-09-16: the ops dev group and
lock now include pytest-xdist 3.8.0 / execnet 2.1.2, and the development guide documents the parallel
command and serial alternative. Default pytest addopts remain unchanged.

The operator also requested investigating the remaining three candidates despite meeting the
timing target. `tas-sr4b.14` owns package reuse, child startup, and history fixture construction;
their active list remains in the workstream handoff until each has an explicit disposition.
Production simplification is a separate later increment.

After adoption, locked synchronization and a final uninstrumented four-worker suite passed:
1,462 tests, 158 warnings, 63.11 seconds. Thus below-120 local timing is established, while
below-60 is not a consistent guarantee. No existing locked dependency was upgraded.

Final local checks passed: locked dependency synchronization, compileall, Bash syntax, focused
serial codec tests (14 passed), Markdown path/whitespace and changed-test terminology inspection,
and `mix precommit` (805 passed). The full serial timing is the preceding recorded baseline;
this session did not repeat a full serial run. External host acceptance remains out of scope.

## Remaining optimization candidates: measurements

Follow-up owner: `tas-sr4b.14`. The operator requested pursuing all three remaining candidates.
These measurements do not mark their optimization complete. Temporary pytest instrumentation
wrapped package construction/import validation and the collected integration module's history
builder/helper runner; nothing from the probe was added to repository test or production code.
Worker metrics were written separately and summed after sequential complete runs.

### Immutable integration packages

Two instrumented four-worker suites passed all 1,462 tests, with 158 warnings each, in 59.91 and
62.83 seconds. Each constructed 243 archives: 109 transient helpers and 134 scheduled helpers.
The first run measured 38.14 aggregate worker-seconds of construction, including 27.47 seconds
of import validation. The second measured 40.23 seconds, with these consumer groups:

| Consumer | Builds | Aggregate seconds |
| --- | ---: | ---: |
| `_install_public_controller` | 28 | 5.16 |
| `_install_public_restore_controller` | 43 | 9.58 |
| `scheduled_backup_helper` | 114 | 12.16 |
| `temporary_helper_package` | 24 | 5.76 |
| Package fixtures and other direct tests | 34 | 7.57 |

Construction timing includes instrumentation overhead; aggregate worker time is not parallel
wall time and is not an established saving. The two named integration installers are a bounded
reuse seam. Scheduled-helper consumers require a separate boundary audit before caching: that
count spans production service-plan construction and focused packaging consumers. Do not cache
production package construction or blanket-patch the suite. Keep builder, mutation, allowlist,
checksum, determinism, earlier-package, and isolated-package tests on fresh construction.

Recommended next design: a test-support session fixture builds each required immutable archive
once per worker, captures bytes/checksum/protocol/mode, and materializes private copies for the
two integration installers. Pass it explicitly through their consumer fixtures; no shared mutable
archive path or discovery state. Verify identity/mode parity, the actual copied archive under
`-I -S`, and complete test counts, then compare uninstrumented full-suite runs.

The operator accepted this bounded design on 2026-09-16 and requested implementation in a fresh
session. Approval was satisfied at that checkpoint; the resumed implementation and results are recorded below. Expected ownership is a focused
`ops/tests/support/` package fixture, `ops/tests/test_end_to_end.py` integration installers, and
their direct consumer fixtures/tests. Import the shared fixture explicitly in its consumers.
Keep production package builders and their contracts unchanged. Do not extend caching to all
scheduled-helper/service-plan consumers merely because their aggregate count is large.

### Child archive startup

The unchanged large-history test passed with per-child cProfile injection in 19.54 seconds.
All 14 fresh processes still ran `python -I -S` and the actual archive entrypoint, with profile
files written through an exit hook outside the archive. Summed child profile time was 16.19 seconds;
parent helper-runner wall time was 17.07 seconds. CProfile materially slows this test, so absolute
times are not directly comparable to unprofiled timings.

- Real `observe_host_state`: 11 calls, 12.74 cumulative seconds.
- Selection reading: 9.00 seconds; history validation: 3.65 seconds. These are contained in
  observation time, not additional startup time.
- Archive source compilation: 1,120 calls, 2.24 cumulative seconds. Builtin compilation alone
  took 2.22 seconds; zip decompression took 0.10 seconds. These figures overlap import totals.

Startup is a real cost, but most profiled time is required observation of 4,097 records.
Decompression is a poor standalone target. No evidence yet justifies process reuse, cached
authority, a bytecode packaging contract, or abandoning archive execution. Keep this candidate
open for a bounded import/compilation design after package reuse; do not label the overhead
irreducible. Production import/state simplification requires its own scoped behavior-preserving
review rather than weakening this regression test.

### Large-history fixture construction

Current construction took 1.03 seconds in the child-profile run (preceding historical profile:
2.35 seconds). A temporary probe replaced only `append_selection` during the fixture's construction
with private create-only canonical record writes and mode 0600. It retained `SelectionRecord`,
`selection_filename`, 4,097 valid files, oldest-only backup references, and every public outcome
assertion. The complete test passed in 13.37 seconds; construction took 0.39 seconds.

The observed construction reduction is about 0.64 seconds, while total test times vary and do not
establish a suite-wide saving. Focused record tests already exercise publication, create-once
refusal, and filename contracts. A direct fixture-writing change is feasible and narrow, but lower
priority than package reuse. The temporary probe is not the final implementation; a durable helper
should express valid history construction directly rather than temporarily monkeypatching production
publication. Keep this candidate visible until that change is accepted or explicitly deferred.

### Follow-up boundary audit

The resumed investigation inspected `host_helper/__main__.py`, its operation imports and
`test_end_to_end.py`'s isolated harness. The entrypoint eagerly imports all operation families
before decoding a request. Lazy selected-handler imports could reduce ordinary cold-start work
without changing the archive allowlist or fresh authority checks. However, the integration harness
also pre-imports deploy, restore, cleanup, discover, preflight and observation modules to substitute
native effects. An entrypoint-only change therefore cannot be credited with eliminating the
measured integration compilation cost. No new timing or startup implementation is established.

Keep startup open for a separately accepted design: first inventory per-operation harness seams,
then measure a throwaway operation-selective import probe with the same fresh `python -I -S`
children, actual archive entrypoint, readiness/timeout cleanup and complete public assertions.
Consider production lazy imports during the later production-simplification increment rather
than introducing an indirect dispatcher solely for an unmeasured test saving. Process reuse,
authority caching, bytecode packaging and allowlist changes remain outside this proposal.

The next bounded history-fixture proposal is to change only
`_replace_with_large_successful_history`: retain validated `SelectionRecord` construction and
`selection_filename`, serialize canonical compact sorted JSON with a trailing newline, and create
private files exclusively with mode 0600. Avoid production atomic-publication and fsync mechanics
for synthetic fixture setup. Keep all 4,097 records, timestamp/predecessor relationships,
oldest-only backup references and existing public success/refusal/projection assertions unchanged.
`ops/tests/host_helper/test_records.py::test_selection_records_are_create_once_and_atomic`
continues to own publication, duplicate refusal, filename, permissions and record round-trip
coverage. Verify that focused owner and the unchanged large-history regression, then repeat the
complete operations gate. This proposal has not yet been accepted or implemented.

The intended coverage boundary is explicit: the integration regression tests reading and validating
large history and applying its backup authority through public deploy/restore consumers. Its setup
currently calls `append_selection` 4,097 times, repeating managed-path validation, temporary writes,
file/directory fsync and hard-link publication. Those durability mechanics are exercised by the
focused record-publication test; repeating them in this fixture is not its acceptance criterion.
Direct setup trades that incidental integration-level publication exercise for a small amount of
explicit test serialization. The serializer must match production canonical bytes, including ASCII
escaping, sorted keys, compact separators, no NaN and a trailing newline, so derived filenames
remain valid. Exclusive creation and explicit 0600 permissions prevent accidental replacement or
unsafe fixture files. The real observer and packaged public consumers must validate the resulting
records; do not substitute parsed history or observation results. Expected gain is modest: about
0.64 seconds of construction in the earlier probe, with suite-wide savings unproven. Approval here
would cover only this fixture change and its verification; startup remains a separate decision.

### Package reuse implementation and disposition

Implemented the accepted seam in `ops/tests/support/integration_packages.py`: an explicitly
imported session fixture builds both archive variants once per worker, captures immutable bytes,
checksum, protocol and mode, and writes private copies for each installer invocation. Only the
two public integration installers and their 34 direct consumers opt in. Production builders,
packaging/source-mutation tests and service-plan construction remain uncached and unchanged.

A new focused contract test hashes both copied archives against their reported checksum, checks
protocol/mode parity and independent mutation, and executes each surviving private copy with
`python3 -I -S`. Independent task review approved compliance and quality after correcting the
initial missing copied-byte hash assertion. No existing behavioral assertions were removed.

Two sequential uninstrumented complete gates, with captured exit status 0, used:

```sh
uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0
```

| Run | Tests passed | Failures | Seconds | Warnings |
| --- | ---: | ---: | ---: | ---: |
| Latest pre-reuse baseline | 1,462 | 0 | 63.11 | 158 |
| Private package reuse, first | 1,463 | 0 | 55.01 | 158 |
| Private package reuse, repeat | 1,463 | 0 | 55.20 | 158 |

The additional case is the fixture contract test. Compared with the recorded baseline, reductions
are 8.10 and 7.91 seconds (about 12.5–12.8%). These are workstation observations across sequential
runs, not a controlled attribution of every second or a CI/sub-60 guarantee. No instrumented
post-change build-count measurement was performed; shared construction is explicit in fixture
scope rather than inferred from timing. The accepted package-reuse candidate is complete and
retained. Startup and history construction remain unresolved as described above.

Focused serial diagnostics produced JUnit evidence of 76 cases with no failures/errors/skips in
163.269 seconds, but the implementer did not retain the outer timeout command's exit status;
this is not a completed verification gate. The complete parallel runs cover those consumers.
The targeted copy contract recheck passed with exit status 0. Locked dependency synchronization,
compileall and Bash syntax passed. `mix precommit` completed with exit status 0 and 805 passing tests in 42.5 seconds. Changed Markdown link/whitespace and new test-support terminology checks passed; Beads synchronization was in sync. No clean release rebuild is required for this test-only change; no production packaging contract changed.


### Configurable-limit alternative

The operator suggested lowering a configurable 4,096 limit for the integration regression.
Inspection found that successful selection history has no count limit: `_selection_entries` scans
all entries under a deadline. `MAX_INVENTORY_ENTRIES = 4096` applies to other bounded inventory
paths, not that history scanner. The 4,097-record tests protect against accidentally reapplying
that inventory guard to lifetime history; changing the constant would not reduce their actual
history work unless test counts were also changed, and parent monkeypatches do not automatically
reach fresh packaged children.

A smaller integration fixture while retaining real >4,096 focused observer regressions is a
separate possible coverage redistribution. It could save more than direct publication setup,
but would relax the currently accepted requirement that public packaged deploy/restore consumers
exercise all 4,097 records. That decision is open; no configurable limit or test-count change
is approved. Avoid adding a production history count cap or configuration only for test speed.


### Accepted history coverage split

The operator approved the coverage split on 2026-09-16 after considering the configurable-limit
alternative. This supersedes the unaccepted direct canonical fixture-writing proposal: retain
production `append_selection` for setup and use three successful selections in the packaged
public deploy/restore regression. The first alone references the old backup; the final two form
the latest/predecessor projection, so the public test still distinguishes full backup authority
from projected history. Preserve all existing public planned/refused, backup retention, bounded
response and no-history-export assertions and fresh `python -I -S` packaged execution.

Keep both real >4096 focused observer regressions unchanged: complete scanning/old references,
and refusal of malformed history beyond the old inventory boundary. No production limit,
configuration or serialization helper is introduced. Accepted risk: a size-dependent defect
specific to the combined packaged-consumer path no longer has a direct >4096 integration case.
The canonical reconciliation specification now reflects this allocation of coverage. Implementation and verification are recorded below.

### History split focused verification

The approved split is implemented only in the integration helper and its sole test in
`ops/tests/test_end_to_end.py`. They now describe historical backup authority and publish three
records with the existing production setup. No observer or production code changed; all public
behavioral assertions remain. The two actual >4096 observer regressions remain unchanged.

Sequential focused commands, each with captured exit status 0:

```sh
# Before the count change:
uv run --project ops pytest ops/tests/test_end_to_end.py::test_public_deploy_and_restore_validate_large_history_without_exporting_it
# After:
uv run --project ops pytest ops/tests/test_end_to_end.py::test_public_deploy_and_restore_respect_historical_backup_authority_without_exporting_it
uv run --project ops pytest ops/tests/host_helper/test_state.py::test_observe_validates_more_than_4096_selections_and_retains_old_references ops/tests/host_helper/test_state.py::test_observe_validates_authority_beyond_the_4096th_selection
```

The before integration case passed in 14.42 seconds; after passed in 4.96 seconds. The two observer
cases passed in 1.23 seconds. This demonstrates a local focused reduction of 9.46 seconds (about
66%), rather than an established full-suite wall-time reduction. Keep the earlier large-history
profile as historical evidence: it no longer describes the public integration case after this
accepted split. Startup investigation must choose a current workload before further profiling.


### History split disposition and complete operations results

Independent task review passed compliance and quality with no findings. Two sequential complete
uninstrumented four-worker gates, using the documented command with captured exit status 0,
passed 1,463 tests and emitted the unchanged 158 warnings in 55.21 and 54.65 seconds.
The preceding package-reuse checkpoint passed in 55.01 and 55.20 seconds: these results do not
establish a meaningful additional full-suite wall-time improvement under four-worker scheduling.

Retain the approved split: the integration case itself is about 9.46 seconds faster in the local
focused comparison, with unchanged semantic assertions and lower fixture cost. No configurable
limit, serializer or production abstraction was added. The actual >4096 observer regressions
retain the large-history invariant; the accepted coverage trade-off remains explicit above.
History fixture optimization is complete. Startup remains open and must be measured on a current
workload, without assuming the former 4,097-record integration profile still represents this test.
Locked synchronization, compileall and Bash syntax passed. `mix precommit` completed with
exit status 0 and 805 passing tests in 43.2 seconds. Changed Markdown references/whitespace
and integration test terminology checks passed; Beads synchronization was in sync.

### Current archive startup investigation

The startup follow-up uses the current three-record historical-authority case, plus selected
unverified-release retry and successful scheduler-quiescing restore. The latter pair exercises
actual packaged mutations; the historical-authority case dispatches only reads and preflight.
All commands below completed with exit status 0, sequentially without concurrent application
builds. Temporary instrumentation and profiles lived outside the repository; archive bytes,
entrypoint, fresh children, `-I -S`, readiness/timeout cleanup and consumer assertions were unchanged.

```sh
uv run --project ops pytest ops/tests/test_end_to_end.py::test_public_deploy_and_restore_respect_historical_backup_authority_without_exporting_it
uv run --project ops pytest ops/tests/test_end_to_end.py::test_public_controller_retries_a_selected_unverified_release_without_synthetic_success 'ops/tests/test_end_to_end.py::test_public_restore_runs_old_scheduler_to_quiescence_before_packaged_binding[False]'
```

A temporary pytest plugin wrapped `_run_isolated_helper`, prepending cProfile enablement and an
atexit profile-file hook to its existing harness. Parent timings encompassed the complete original
runner. Summed profile values are aggregate child time, not parallel suite wall time; compilation
and observation are contained in total profile time. Instrumentation changes timing.

| Workload | Uninstrumented test seconds | Profiled test seconds | Children | Runner seconds | Profile seconds | Source compile seconds | Host observation seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Historical backup authority | 4.67 | 5.00 | 14 | 4.27 | 3.58 | 2.09 | 0.043 |
| Failed deploy retry and successful restore | 8.38 | 9.65 | 28 | 8.78 | 7.41 | 4.37 | 0.148 |

The first workload dispatched eight discover, three list_releases and three restore_preflight
requests. The second dispatched 15 discover, seven list_releases, two deploy, two restore_preflight,
one list_backups and one restore. Unlike the historical 4,097-record profile, observation is now a
small share. Source compilation remains a demonstrated cost.

#### Native-effect seam inventory

The isolated harness imports the entrypoint first, then installs all substitutions unconditionally.
The entrypoint itself eagerly imports every operation family and builds its direct callable map.
Changing either side alone therefore does not avoid the broad graph.

| Owner | Harness substitutions and retained effects |
| --- | --- |
| Shared state/services | Service observation and native commands use private JSON state; real filesystem and record observation remain. |
| Discover/list/provision authority | Database observations, credential/resource authority and scheduler facts are substituted; inventory, history and backup-reference validation remain real. |
| Deploy/genesis | Database/credential/preflight commands, safety backup, migration/service commands, verification and scheduler facts are substituted; selection publication and protection lifecycle remain real, with event wrappers. |
| Backup protection and scheduler | Event wrappers retain real publication, retirement and deletion; native timer/wait/upload/executable effects use private state. |
| Restore preflight | Credentials, PostgreSQL queries and statvfs are substituted and recorded. Patching `preflight_module.os.statvfs` changes the shared process-local `os` module and must be accounted for when separating setup. |
| Restore/database replacement | OID observation, creation, load, rename/drop, capacity, connections, backup, service and verification effects are substituted; real binding/registration/protection operations retain interruption and lost-reply wrappers. |
| Cleanup | A deletion-count/failure wrapper retains the real `_delete_target` operation. |

Rollback, standalone backup and standalone verify do not have complete dedicated substitutions
in this harness. Do not assume it supports their native mutation paths merely because imports
succeed. Any selective setup must enumerate existing consumers rather than generalize this runner.

#### Import-only diagnostic and proposed disposition

Thirty fresh `python -I -S` children imported either the unchanged entrypoint or one unchanged
operation module from the same private integration archive, five times per choice. Each child
inserted only that archive into its path and timed `importlib.import_module` with `perf_counter`.
No entrypoint request or native operation ran. These are import-closure diagnostics, not acceptance
tests or a selective-import implementation.

| Imported module | Median seconds | Range seconds | Loaded taskman_ops modules |
| --- | ---: | --- | ---: |
| host_helper.__main__ | 0.1838 | 0.1814–0.2075 | 39 |
| operations.discover | 0.1270 | 0.1165–0.1426 | 27 |
| operations.preflight | 0.1020 | 0.0807–0.1282 | 25 |
| operations.deploy | 0.1680 | 0.1513–0.1904 | 34 |
| operations.restore | 0.2039 | 0.1651–0.2315 | 34 |
| operations.cleanup | 0.1232 | 0.1114–0.1387 | 27 |

Selected discovery/preflight imports have a smaller closure; deploy and restore retain most of
it. Differences of these medians do not establish end-to-end savings: common entrypoint/failure
imports and harness substitutions must still run, and trials show timing variability.

The operator accepted the measured disposition on 2026-09-16: finish the step-2 startup
investigation and defer import changes to production simplification in step 3. Consider plain
selected-handler imports there only when they simplify the production boundary; do not introduce
a generic dispatcher or harness framework solely for an unmeasured saving. Fresh processes,
actual archive execution, fresh authority and all assertions remain required.

The unselected selective-import experiment would have been bounded to temporary archive copies
and test-support instrumentation: defer operation imports on both sides, select setup using the
parent's already-known operation without consuming stdin, and retain every shared and relevant
native-effect substitution above. Preserve the readiness marker after setup and real request
decoding/dispatch through `runpy.run_path`; compare the three current cases uninstrumented before
and after, then inspect consumer parity. Failure-result observation must retain its shared imports.
No persistent bytecode contract, process reuse, cached authority, allowlist change or production
rewrite is approved. This alternative is retained as rationale, not an unfinished step-2 commitment.
Any production import change needs a bounded design and verification in step 3.

Final local verification: the three focused cases passed as recorded above; Markdown paths and
whitespace passed. `mix precommit` initially refused because local PostgreSQL was stopped; after
starting the existing development container and confirming readiness, it passed 805 tests in
44.5 seconds with exit status 0. No production/test source changed, so the complete operations
suite was not repeated; its last full result remains the history-split checkpoint. The workstream
advances to step 3; history consolidation and real-VPS acceptance retain their authorization gates.

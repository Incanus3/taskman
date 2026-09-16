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
session. Approval is satisfied; implementation has not begun. Expected ownership is a focused
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

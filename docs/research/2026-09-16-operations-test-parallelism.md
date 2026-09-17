# Operations test parallelism measurements

Status: completed historical research; decisions implemented or explicitly skipped.
Updated: 2026-09-18. Owners: `tas-sr4b.13`, `tas-sr4b.14`, `tas-561x.1`, `tas-561x.2` (closed).

## Scope and authority

This report preserves the evidence behind bounded parallel execution, immutable integration
package reuse, the accepted history-coverage split and the skipped import optimization.
The [development guide](../guides/development.md#operations-verification)
owns current commands and concurrency precautions. The
[reconciliation specification](../specs/2026-09-18-operations-contracts.md#growing-inventories-and-bounded-responses)
owns coverage requirements, and the
[deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md#implemented-simplification-decisions)
owns architecture decisions. The [acceptance report](2026-09-17-operations-vps-acceptance.md)
owns later identified readiness evidence; these dated measurements are not current readiness claims.

Initial source: `8f1918614123fcba78fd874d45c20d1c9d51cfb4`, with the preceding
restore-retention fixture optimization and the collection-order correction below.
Workstation: Intel Core i9-10980XE, 18 physical cores / 36 logical CPUs, Python 3.12.12,
pytest 8.4.2, pytest-xdist 3.8.0, execnet 2.1.2. Trials were sequential without concurrent
application verification. Times are pytest wall times, excluding dependency installation and
outer uv startup unless stated otherwise. Instrumented aggregate times are not parallel wall time.
No timing is a CI guarantee or controlled attribution of every second saved.

## Parallel-safety evidence and limits

The audit found private temporary filesystem/repository/archive roots, process-local environment,
cwd, secret registries and monkeypatches, private AF_UNIX sockets and newly allocated PTYs.
Native host/network/build mutations were substituted; local pyinfra file operations used private
roots and systemd inspection used isolated roots with diagnostics checked. Isolated helper calls
retained fresh `python -I -S` children and real archive entrypoints. Timeout cleanup kills/drains
children and closes readiness pipes; PTY failure paths kill/wait and close descriptors.

Administrator bridge tests invoke `mix run --no-start` and share repository Mix build output.
The trials used an already compiled checkout; cold-build parallel safety was not established.
Avoid concurrent source edits/application builds during operations tests. Strict timing assertions
can fail under resource contention; passing trials do not prove universal scheduling safety.
Observed multi-threaded fork/forkpty deprecation warnings through pyinfra/gevent and the PTY
bridge were retained, not suppressed. They do not establish an xdist-specific defect.

Frozenset iteration can parametrize operation cases in different hash-randomized orders across
workers and cause collection refusal before any tests execute. Sorting only the parametrization
input preserves all 14 cases and the production frozenset. Current
`host_protocol/test_operations.py` retains that sorting.

## Parallel execution decision

| Execution at initial checkpoint | Passed | Failures | Seconds | Warnings |
| --- | ---: | ---: | ---: | ---: |
| Recorded serial baseline | 1,462 | 0 | 219.61 | Not recorded |
| Two workers, first | 1,462 | 0 | 114.77 | 156 |
| Two workers, repeat | 1,462 | 0 | 113.27 | 156 |
| Four workers, first | 1,462 | 0 | 55.47 | 158 |
| Four workers, repeat | 1,462 | 0 | 59.54 | 158 |
| Four workers, adopted locked environment | 1,462 | 0 | 63.11 | 158 |

The supported gate uses four explicit worksteal workers and dev dependency
`pytest-xdist>=3.8,<4`. Four workers gave more headroom under the selected
120-second local operations-suite budget. File/scope scheduling was avoided because it would
concentrate expensive integration tests; worker restart was disabled to expose crashes.
The original scheduling reference was the
[pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).
The lock retains xdist 3.8.0 / execnet 2.1.2; default pytest addopts remain `-q`, so the default
is serial. No worker crashed or test was excluded in passing trials. Below 60 seconds was
not consistently established. The serial baseline was not rerun after adoption.

Reproduce the supported gate using the development guide's locked environment:

```sh
uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0
```

For profiling, add `--durations=30` and optionally `--junitxml=/private/path/report.xml`.
Current test counts and timings can differ from these historical tables.

## Immutable integration packages

Two instrumented four-worker runs passed 1,462 tests in 59.91/62.83 seconds, constructing
243 archives each (109 transient, 134 scheduled). Construction consumed 38.14/40.23 aggregate
worker-seconds; the first included 27.47 seconds of import validation. These instrumented
figures identified a reuse seam, not an established wall-time saving.

The implementation in `ops/tests/support/integration_packages.py` builds both archive
variants once per pytest worker in an explicitly imported session fixture. It captures immutable
bytes, checksum, protocol and mode, then materializes independent private copies for opted-in
public integration controllers. Production builders, source-mutation/allowlist/determinism tests
and service-plan construction remain uncached. Replacement-workflow consumers also opt in.
The fixture contract test checks copied-byte hashes, protocol/modes, independent mutation and
actual surviving copies under `python3 -I -S`. Discovery state is not cached.

| Uninstrumented execution | Passed | Failures | Seconds | Warnings |
| --- | ---: | ---: | ---: | ---: |
| Latest pre-reuse baseline | 1,462 | 0 | 63.11 | 158 |
| Private package reuse, first | 1,463 | 0 | 55.01 | 158 |
| Private package reuse, repeat | 1,463 | 0 | 55.20 | 158 |

The additional case was the fixture contract test. Observed reductions were 8.10/7.91 seconds
(about 12.5–12.8%); sequential observations do not establish universal savings. No instrumented
post-change construction-count measurement was retained.

## Accepted history coverage split

Coverage is split as follows:

- Public packaged deploy/restore historical-backup-authority coverage uses three valid selections,
  published through production `append_selection`; only the oldest holds the old backup reference.
  Real observation and public refusal when that dump disappears remain exercised.
- Two focused observer regressions retain real >4,096-record histories, full-history validation
  and old-reference protection beyond the bounded latest/predecessor projection.

Accepted risk: a size-dependent defect specific to the combined packaged-consumer path no longer
has a direct >4,096 integration case. The reconciliation specification records that allocation.
`MAX_INVENTORY_ENTRIES = 4096` governs other inventory paths, not lifetime successful-selection
history; lowering it would not shorten those scans. No production history count cap, configurable
limit or direct test serializer was introduced.

The original unchanged 4,097-record test's child cProfile run took 19.54 seconds: 14 children,
16.19 aggregate profile seconds, 12.74 cumulative observation seconds and 2.24 source compilation
seconds. Contained/overlapping metrics cannot be added as separate costs. A temporary direct-write
setup probe reduced construction from 1.03 to 0.39 seconds, but supplied no suite-wide saving
and was not adopted. These profiles describe the old workload, not the current integration test.

Focused uninstrumented before/after integration checks passed in 14.42/4.96 seconds; the two
unchanged observer regressions passed in 1.23 seconds. Two complete post-split gates passed
1,463 tests in 55.21/54.65 seconds with 158 warnings. This established a focused 9.46-second
reduction, but no meaningful additional full-suite improvement over package reuse's 55.01/55.20.

## Current archive startup investigation

This heading is retained for existing citations; “current” refers to the three-record workload
at the 2026-09-16 measurement checkpoint, not a fresh profile of today's source.
Temporary profiling retained actual archive bytes/entrypoints, fresh isolated children,
readiness/timeout cleanup and consumer assertions.

| Historical workload | Uninstrumented seconds | Profiled seconds | Children | Runner seconds | Profile seconds | Compilation seconds | Observation seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Three-record historical backup authority | 4.67 | 5.00 | 14 | 4.27 | 3.58 | 2.09 | 0.043 |
| Failed deploy retry and successful restore | 8.38 | 9.65 | 28 | 8.78 | 7.41 | 4.37 | 0.148 |

Observation became a small share; source compilation remained a demonstrated cost. Both the
entrypoint and isolated harness imported all operation families; changing only one side could
not avoid that graph. Five fresh archive imports per module produced these import-only medians:

| Module | Median seconds | Range seconds | Loaded taskman_ops modules |
| --- | ---: | --- | ---: |
| host_helper.__main__ | 0.1838 | 0.1814–0.2075 | 39 |
| operations.discover | 0.1270 | 0.1165–0.1426 | 27 |
| operations.preflight | 0.1020 | 0.0807–0.1282 | 25 |
| operations.deploy | 0.1680 | 0.1513–0.1904 | 34 |
| operations.restore | 0.2039 | 0.1651–0.2315 | 34 |
| operations.cleanup | 0.1232 | 0.1114–0.1387 | 27 |

These are closure diagnostics: no request/native operation ran, and differences do not establish
end-to-end savings. Read-operation closures were smaller; mutations retained most dependencies.
Selected-handler imports were skipped: these diagnostics do not establish an end-to-end benefit.
Fresh processes, actual isolated archive execution, fresh authority and consequential assertions
remain required;
process reuse, authority caching, bytecode packaging or a generic dispatcher/harness are not
justified by these measurements.

The harness substitutes native service/database/credential/scheduler effects while retaining real
filesystem/history/binding/protection operations and interruption wrappers. Its preflight statvfs
patch affects the process-local shared `os` module. The original audit did not establish complete
native substitutions for rollback, standalone backup or standalone verify; successful imports
alone must not be treated as safe native-path coverage. Any future harness change needs a fresh
consumer/effect inventory rather than treating the old inventory as current certification.

## Post-acceptance profiling and fixture correction

The 2026-09-18 profile measured source `cd92dacf372448ca04e014fb536256826a4e9d7a`
against consolidation baseline `f1052417e651`, retaining the four-worker command and coverage.
Native PostgreSQL opt-in variables were absent. No source edits or Mix builds overlapped runs.
Private capture files were pre-created 0600 under a 0700 receipt directory; the test process's
umask was controlled separately.

| Historical run | Passed | Failed | Skipped | Warnings | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial private wrapper, umask 077 | 1,621 | 1 | 3 | 158 | 62.60 |
| Normal test subprocess, umask 022 | 1,622 | 0 | 3 | 158 | 66.46 |
| Explicit fixture chmod | 1,622 | 0 | 3 | 158 | 65.65 |

The passing 022 run took 67.11 seconds including outer uv/process time; 1,625 JUnit cases summed
to 214.025 aggregate testcase seconds. Packaged workflows dominated (133.164 seconds end-to-end,
32.521 restore replacement); PostgreSQL, provisioning and entrypoint groups were 4.675, 4.598 and
3.394. Newly named test functions contributed 1.933 aggregate seconds, excluding costs added to
existing cases; this is not an elapsed-time estimate. Review of the slow repeated operations
found distinct lost-genesis replay/changed-target refusals and different predecessor/protection
semantics, with no justified removable request or assertion. No new runtime optimization was selected.

The lifecycle-lock fixture explicitly applies chmod(0750) after mkdir because umask 077 would
otherwise produce 0700; production correctly preserves an existing root's permissions. Recorded
checks passed all three lifecycle-lock cases under both 077 and 022. This precondition improves verification
reliability without changing production behavior or assertions; it is not a speed optimization.

Raw profiling receipts were retained privately under
`/tmp/taskman-vps-acceptance.89wn5h8s/premerge-repeat`; they are provenance, not a permanent artifact
availability guarantee. Identified acceptance findings belong to the acceptance report.

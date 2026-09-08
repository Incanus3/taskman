# Deployment controller simplification handoff

**Status:** reassessment design and implementation plan approved; ready for clean-session execution
**Updated:** 2026-09-08
**Resume:** `$resume deployment-controller-simplification`

## Objective

Replace duplicated dedicated-host controller implementations with one bounded
transient helper protocol and declarative pyinfra convergence while preserving
the material safety, recovery, and operator-control guarantees.

## Durable references

- [Simplification design](../specs/2026-09-06-deployment-controller-simplification-design.md)
- [Reduction design](../specs/2026-09-07-deployment-controller-reduction-design.md)
- [Reduction reassessment](../specs/2026-09-08-deployment-controller-reduction-reassessment-design.md)
- [Reassessment implementation plan](../plans/2026-09-08-deployment-controller-reduction-reassessment.md)
- [Reduction implementation plan](../plans/2026-09-07-deployment-controller-reduction.md)
- [Implementation plan](../plans/2026-09-06-deployment-controller-simplification.md)
- [Current deployment runbook](../deployment.md)

Delivery feature: `tas-deployment-controller-simplification-f00.11`, under parent
`tas-deployment-controller-simplification-f00`.

Approved continuation tasks are
`tas-deployment-controller-simplification-f00.11.9` through `.11.19`.
Task `.11.9` is the next ready task; `.11.18` and `.11.19` are the terminal
read-only production and test audits.

## Current checkpoint

Tasks 1–8 of the reduction plan are implemented, independently reviewed, and
closed. The applied stack now includes commit
`a2eae96efdb98cd5687d86592d943e3552cbc553`.

The implementation has a verified artifact resolver, completed immutable
records and one observed `HostState`, the final bounded four-outcome helper
protocol, read-only workflows projected from observed state, and built-in-first
pyinfra provisioning. Backup and cleanup now use bounded subprocesses and
replayable observed-state procedures without journals, residue arrays, or
generated recovery actions. Deploy and genesis now share one convergent
procedure with validated backup-before-migration, atomic verified selection,
and completed selection records. The only retained direct provisioning actions are Caddy
pre-activation validation, ordered UFW activation with a fresh pinned-host-key
SSH proof, PostgreSQL cluster/HBA transition, and secret-sensitive writes.

Task 9 is blocked as
`tas-deployment-controller-simplification-f00.11.9`. Its uncommitted working
tree deletes the legacy lifecycle/runtime core, temporary result bridge, and
orphan legacy backup operation; focused architecture/simplification coverage
passes. The honest production count is 15,948 lines, or 24.517% below the
21,128-line baseline. It is 736 lines above the approved revised final maximum
of 15,212, before the scheduled-backup migration and active simplification.

The complete suite also cannot collect because the installed scheduled-backup
asset's black-box tests import the deleted lifecycle records. That asset still
writes the old activation/lifecycle format. The approved reassessment migrates
that retained capability before adopting the legacy deletion slice.

A fresh three-part read-only audit found that scheduled backups are a retained
product capability and should migrate to the completed-record model rather than
be removed. The recommended shape is a narrow installed standard-library
zipapp reusing the backup, state, record, lock, command, and retention
capabilities; the old 843-line shell lifecycle graph and most of its 1,300-line
legacy test suite can then be deleted. This migration is architecturally
necessary but likely adds roughly 140–220 measured production-Python lines.

The approved reassessment design records the resulting architecture. The
active-code audit found credible cohesive reductions in internal interface
duplication, bounded transport, shared helper primitives, host admission,
Caddy evidence, PostgreSQL convergence, verification timing/commands, and
completed-state parsing. After overlap and scheduled-backup migration, the
honest expected final reduction is approximately 28–31%, not 35%. Reaching 35%
would require explicit product or guarantee changes such as removing scheduled
backups, weakening helper/SSH/redaction checks, or narrowing configuration and
build contracts; none is recommended merely to satisfy the metric.

## Active decisions and evidence

- The final wire protocol is authoritative. One private one-way helper bridge
  remains only until the last legacy internal consumer is migrated; Task 9 must
  delete it.
- Deterministic temporary names remain
  `.release-<version>-<revision>-<target>.tmp` and
  `.backup-<backup-id>.dump.tmp`; they carry no operation ID or pending record.
- Task 4 also migrated restore discovery to the final selected-release, backup
  source-release, and bounded migration projections.
- Task 5 passed its focused provisioning suite, architecture and executable
  guards, controller metric, full operations suite, and two independent fix
  reviews. Ruff was unavailable in the locked environment.
- Task 6 passed 34 focused tests, the full operations suite,
  architecture/contracts, compileall, the controller metric, and
  `mix precommit` with 802 tests. Independent review confirmed that cleanup
  protects all selection-referenced backups before any mutation.
- Task 7 passed 45 focused tests, 53 architecture/protocol contracts, the full
  operations suite, compileall, the controller metric, and `mix precommit` with
  802 tests. Independent review confirmed unowned genesis schema state is
  refused before mutation.
- Task 8 passed 25 focused helper/dispatch/workflow tests, the full operations
  suite, architecture/contracts, compileall, metric, and `mix precommit` with
  802 tests. Independent review approved same-release and interrupted restore
  replay plus exact fresh-backup provenance.
- After uncommitted Task 9 deletions, the measured production count is 15,948
  lines. The approved revised final maximum is 15,212; intermediate slices
  are not subject to a per-phase reduction gate.
- No banned legacy production references remain in the partial Task 9 tree.
- The largest remaining modules are active SSH transport, PostgreSQL
  convergence, deploy/restore procedures, host facts, verification,
  configuration, output, and build code; no further 2,215-line obsolete
  transaction inventory has been demonstrated.
- The metric scope is correct: it counts tracked `taskman_ops/*.py` only, not
  the scheduled shell asset or tests.
- No real-host action, deployment, push, merge, or publication has been
  performed.

## Next action

Begin the scheduled-backup migration
(`tas-deployment-controller-simplification-f00.11.9`) in a fresh session with:

```text
$resume deployment-controller-simplification
```

Default to subagent-driven execution with an independent reviewer for each
slice. Every implementation and review subagent must first read the complete
approved reassessment design and its assigned plan task. Start with the
scheduled-backup migration and preserve the existing uncommitted Task 9
deletion slice until it can be adopted deliberately.

After the verified reduction baseline, perform two read-only terminal audits:
first inspect production operations code for further simplification that
preserves all functionality, core guarantees, readability, understandability,
and organization; then inspect tests for genuinely duplicated coverage and
fixture/helper machinery. Record and independently review findings, but do not
implement them without separate operator approval.

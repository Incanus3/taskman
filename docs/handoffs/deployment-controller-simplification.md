# Deployment controller simplification handoff

**Status:** active; reduction design approved, implementation plan ready for execution
**Updated:** 2026-09-07
**Resume:** `$resume deployment-controller-simplification`

## Objective

Replace duplicated dedicated-host controller implementations with one bounded
transient helper protocol and declarative pyinfra convergence while preserving
the material safety, recovery, and operator-control guarantees.

## Durable references

- [Simplification design](../specs/2026-09-06-deployment-controller-simplification-design.md)
- [Reduction design](../specs/2026-09-07-deployment-controller-reduction-design.md)
- [Reduction implementation plan](../plans/2026-09-07-deployment-controller-reduction.md)
- [Implementation plan](../plans/2026-09-06-deployment-controller-simplification.md)
- [Current deployment runbook](../deployment.md)

Delivery feature: `tas-deployment-controller-simplification-f00.11`, under parent
`tas-deployment-controller-simplification-f00`.

## Current checkpoint

Tasks 1–10 are implemented, independently reviewed, and closed. The final
whole-branch review approved head
`b9253eab2d05cb4f36ed5d7701b2029e84e391f0` with no Critical or Important
findings after four correction rounds.

The completed architecture uses the documented two-root model, one bounded
transient standard-library helper protocol, and one programmatic pyinfra
provisioning path. The root-owned `ops/backup/taskman-backup` remains
intentionally as the systemd timer target installed by pyinfra. It is a
scheduled host capability, not a parallel workstation controller.

Delivery feature `tas-deployment-controller-simplification-f00` remains open
while a follow-on reduction is assessed before operator-controlled integration
and real-host acceptance. All ten implementation tasks are closed in Beads.

The follow-on direction has been approved section by section and captured in
the proposed reduction design. It preserves every public command and its main
responsibility, but replaces exact crash continuation with replayable
convergence: after a failure, the operator reruns the command, confirms any
newly dangerous step, and the tool automatically repairs or repeats work unless
the observed state is genuinely ambiguous. Compatibility with the never-deployed
lifecycle, recovery, and backup metadata is not required. The design now also
uses a built-in-first pyinfra boundary: stable, secret-free, independently
observable, safely repeatable provisioning should use declarative built-ins;
small custom actions remain only for material availability, access, secret, or
transaction boundaries. Wrapping custom shell in pyinfra does not qualify as
simplification.

Plain `deploy` must resolve its artifact before connecting to the host. An
explicit `--artifact` remains authoritative after verification. Otherwise it
reuses a verified artifact only when its source revision, application version,
target, pinned toolchain, and builder identity exactly match the current clean
checkout; on no match it invokes the same build capability as `build`. Artifact
age is irrelevant, and invalid or nonmatching cache entries are ignored.

The retained safety floor covers secret non-disclosure, verified SSH host
identity, destructive confirmation, privileged mutation confined to
authoritative Taskman paths, validated backups before migrations or restores,
atomic release selection, truthful recoverability, and the bounded transient
helper. Exact operation journals, provisional state, exhaustive residue and
stage evidence, seamless continuation, and low-value timing/hostile-filesystem
edge machinery are eligible for deletion. The required result is at least a
35% reduction from the 21,128-line production-Python baseline; there is no
absolute line-count target.

## Latest evidence and constraints

Independent final review passed a 16-case adversarial matrix, 237 focused
deployment/recovery/SSH tests, the cache-cleared operations suite, 30
architecture and contract tests, locked dependency synchronization,
compileall, shell and diff checks, and `mix precommit` with 802 tests.

The latest builder-relevant source commit
`6b4717e486203f497bdc8cd37eb2df7e17de94c5` produced and independently
verified release
`0.2.0-6b4717e48620-ubuntu26.04-amd64-otp27.3.4.6`, archive SHA-256
`d56f122fef1ae336c0d22a262e99e3b1721c6e9dcfe3f1aeeabc8508b6d03518`,
from
`ubuntu:resolute-20260811.1@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b`.
Later changes are controller-only Python and do not enter the Phoenix release
artifact.

Final-head metric: 21,128 production lines, 19,895 test lines, 354 multiline
executable-string lines, and 114 tracked Python files. The nonbinding 20–30%
production-line reduction target was not met; the final report records the
deleted duplicate paths and the cohesive safety-critical boundaries retained.

No real-host action, deployment, push, merge, or publication was performed.
Those actions remain separately authorized.

## Next action

Start a clean session and execute the ten ordered child tasks
`tas-deployment-controller-simplification-f00.11.1` through `.11.10` using the
selected subagent-driven workflow. Begin with the artifact resolver, then the
completed-state foundation and coarse protocol before migrating commands. No
implementation agent has yet been spawned and no production code has changed.
Keep the parent feature open until the operator accepts the intended delivery
state; real-host acceptance remains separately authorized.

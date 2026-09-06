# Deployment controller simplification handoff

**Status:** locally complete; awaiting operator-controlled integration and real-host acceptance
**Updated:** 2026-09-07
**Resume:** `$resume deployment-controller-simplification`

## Objective

Replace duplicated dedicated-host controller implementations with one bounded
transient helper protocol and declarative pyinfra convergence while preserving
the material safety, recovery, and operator-control guarantees.

## Durable references

- [Simplification design](../specs/2026-09-06-deployment-controller-simplification-design.md)
- [Implementation plan](../plans/2026-09-06-deployment-controller-simplification.md)
- [Current deployment runbook](../deployment.md)

Delivery feature: `tas-deployment-controller-simplification-f00`.

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
for operator-controlled integration and real-host acceptance. All ten child
tasks are closed.

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

The operator chooses whether to keep, merge, or publish the branch and
separately authorizes any real-host acceptance run. Close the parent feature
only after the operator accepts the intended delivery state.

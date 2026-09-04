# Dedicated-host deployment automation handoff

**Status:** active  
**Updated:** 2026-09-04  
**Resume:** `$resume dedicated-host-deployment-automation`

## Objective

Add workstation-driven automation for repeat Taskman release deployments and complete provisioning
of a clean SSH-accessible Ubuntu VPS while preserving the existing OTP release, systemd, loopback
Phoenix, local PostgreSQL, and Caddy topology.

## Durable references

- [Approved design](../specs/2026-09-04-dedicated-host-deployment-automation-design.md)
- [Approved implementation plan](../plans/2026-09-04-dedicated-host-deployment-automation.md)
- [Existing deployment runbook](../deployment.md)
- [Hosted-access foundation](../specs/2026-09-02-authenticated-hosted-access-design.md)

Delivery feature: `tas-dedicated-host-deployment-automation-moq`

## Current checkpoint

The complete specification and 14-task implementation plan are approved. Tasks 1–3 are implemented
and independently verified through commit `03e4742`. Task 4,
`tas-dedicated-host-deployment-automation-moq.4` (verified release artifacts), is the next delivery
gate.

The selected stack is pyinfra 3.x, SOPS with age, a pinned Ubuntu 26.04 amd64 build container, local
PostgreSQL, Caddy, systemd, explicit migration compatibility, local backup retention, explicit
rollback/restore, and brief deployment downtime.

## Next actions

1. Continue subagent-driven development with Task 4
   (`tas-dedicated-host-deployment-automation-moq.4`).
2. Independently verify Task 4 before closing it and proceeding through the dependency graph.

## Constraints and unresolved evidence

- A real Ubuntu 26.04 amd64 VPS acceptance run requires separate authorization.
- Local backups do not provide disaster recovery until the operator copies them off-host.
- DNS, provider firewall, VPS lifecycle, and Resend provisioning remain external.
- Preserve the existing release-command trust boundary and never expose Phoenix, PostgreSQL, or
  Erlang distribution publicly.
- No real environment file, SOPS private identity, or production secret is present yet.

## Latest verification

- Task 1 fresh checks passed: 15 health/controller tests and 2 hosted-access tests.
- Task 2 scoped review approved both fixes with no new breakage; fresh locked checks passed:
  28 Python tests and `uv lock --check`.
- Task 3 passed two security fix rounds and scoped re-review; fresh locked checks passed:
  89 configuration/secrets/output tests and `uv lock --check`.

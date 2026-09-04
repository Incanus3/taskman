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

The complete specification and 14-task implementation plan are approved. The Beads dependency
graph exists and is ready for implementation. Tasks
`tas-dedicated-host-deployment-automation-moq.1` (health endpoint) and
`tas-dedicated-host-deployment-automation-moq.2` (controller shell) are the initial unblocked
delivery gates.

The selected stack is pyinfra 3.x, SOPS with age, a pinned Ubuntu 26.04 amd64 build container, local
PostgreSQL, Caddy, systemd, explicit migration compatibility, local backup retention, explicit
rollback/restore, and brief deployment downtime.

## Next actions

1. Read the complete approved specification and plan.
2. Use subagent-driven development by default, beginning with Task 1
   (`tas-dedicated-host-deployment-automation-moq.1`).
3. Independently verify Task 1 before closing it and proceeding through the dependency graph.

## Constraints and unresolved evidence

- A real Ubuntu 26.04 amd64 VPS acceptance run requires separate authorization.
- Local backups do not provide disaster recovery until the operator copies them off-host.
- DNS, provider firewall, VPS lifecycle, and Resend provisioning remain external.
- Preserve the existing release-command trust boundary and never expose Phoenix, PostgreSQL, or
  Erlang distribution publicly.
- No real environment file, SOPS private identity, or production secret is present yet.

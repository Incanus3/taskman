# Dedicated-host deployment automation handoff

**Status:** active  
**Updated:** 2026-09-05  
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

The complete specification and 14-task implementation plan are approved. Tasks 1–5 are implemented
and independently verified through commit `7146aec`. Task 6,
`tas-dedicated-host-deployment-automation-moq.6` (lifecycle records, locking, adoption, and
discovery), is the next delivery gate.

The selected stack is pyinfra 3.x, SOPS with age, a pinned Ubuntu 26.04 amd64 build container, local
PostgreSQL, Caddy, systemd, explicit migration compatibility, local backup retention, explicit
rollback/restore, and brief deployment downtime.

## Next actions

1. Continue subagent-driven development with Task 6
   (`tas-dedicated-host-deployment-automation-moq.6`).
2. Independently verify Task 6 before closing it and proceeding through the dependency graph.

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
- Task 4 passed two artifact-hardening rounds and scoped re-review; fresh checks passed:
  56 build/manifest tests and `uv lock --check`.
- Clean-clone BuildKit proof produced and revalidated
  `0.2.0-59bc20ec625b-ubuntu26.04-amd64-otp27.3.4.6` for Ubuntu 26.04 amd64,
  OTP 27.3.4.6. The artifact checksum is recorded in the Task 4 implementation report.
- Task 5 passed two preflight-hardening rounds and scoped re-review; fresh checks passed:
  37 transport/host-fact tests and `uv lock --check`.

# Dedicated-host deployment automation handoff

**Status:** active  
**Updated:** 2026-09-04  
**Resume:** `$resume dedicated-host-deployment-automation`

## Objective

Add workstation-driven automation for repeat Taskman release deployments and complete provisioning
of a clean SSH-accessible Ubuntu VPS while preserving the existing OTP release, systemd, loopback
Phoenix, local PostgreSQL, and Caddy topology.

## Durable references

- [Approved design awaiting written review](../specs/2026-09-04-dedicated-host-deployment-automation-design.md)
- [Existing deployment runbook](../deployment.md)
- [Hosted-access foundation](../specs/2026-09-02-authenticated-hosted-access-design.md)

No Beads delivery issue exists yet; create it from the approved implementation plan rather than
from the design alone.

## Current checkpoint

The in-chat architecture, provisioning flow, deployment transaction, operator interface, safety
model, and verification boundaries are approved. The target was corrected to Ubuntu 26.04 LTS on
amd64, including Intel 64. The self-contained specification is written and now needs operator
review.

The selected stack is pyinfra 3.x, SOPS with age, a pinned Ubuntu 26.04 amd64 build container, local
PostgreSQL, Caddy, systemd, explicit migration compatibility, local backup retention, explicit
rollback/restore, and brief deployment downtime.

## Next actions

1. Receive operator review of the written specification and make any requested corrections.
2. After explicit approval, create the implementation plan under `docs/plans/`.
3. Create and verify the repository-local Beads issue graph.
4. Refresh this handoff with the plan, issue IDs, and first implementation action.
5. End the design/planning session and resume implementation in a clean session.

## Constraints and unresolved evidence

- A real Ubuntu 26.04 amd64 VPS acceptance run requires separate authorization.
- Local backups do not provide disaster recovery until the operator copies them off-host.
- DNS, provider firewall, VPS lifecycle, and Resend provisioning remain external.
- Preserve the existing release-command trust boundary and never expose Phoenix, PostgreSQL, or
  Erlang distribution publicly.
- No real environment file, SOPS private identity, or production secret is present yet.

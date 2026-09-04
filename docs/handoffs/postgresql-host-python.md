# PostgreSQL host-side Python refactor

Status: parked. Updated: 2026-09-09. Resume: `$resume postgresql-host-python`.

## Objective and authority

Replace the substantial embedded PostgreSQL inspection/configuration shell with focused host-side
Python while preserving native tools and safety/recovery behavior. Task: `tas-sidn` (deferred).
The [proposed specification](../specs/2026-09-09-postgresql-host-python-design.md) owns scope,
trade-offs, file boundaries, request/error semantics, and verification requirements.

## Current checkpoint

- The operator approved PostgreSQL-first scope and narrow inspect/configure operations through
  the existing transient helper and authenticated pyinfra connection.
- Independent specification review approved the clarified change accounting, cancellation seam,
  and error precedence. Operator written-spec approval is still pending; no implementation plan
  or refactor code exists.
- The operator parked this work to finish VPS provisioning, then prioritized
  [operations CLI UX](operations-cli-ux.md). Provisioning/readiness now work with the existing
  shell implementation; this refactor is not required for the UX or host acceptance work.
- Keep the selected cluster's native HBA path, parser-backed validation, protected password
  boundary, recovery copy, and accepted brief on-disk crash window. Do not broaden to a generic
  remote executor, daemon, blanket shell conversion, or unsolicited live-host experiments.

## Next actions when selected

1. Recheck the current controller baseline and the complete specification against intervening
   changes. The [VPS readiness workstream](ops-vps-readiness.md) owns current host/artifact evidence.
2. Obtain operator written-spec approval, then prepare a reviewed implementation plan and bounded
   implementation tasks under `tas-sidn`.
3. Update this handoff at the approved-plan clean-session boundary before implementation.

Design review is not implementation verification or permission to alter the live PostgreSQL
cluster. Existing test/build evidence and detailed historical findings remain in the specification
and `tas-b7kd`; do not repeat completed VPS repairs.

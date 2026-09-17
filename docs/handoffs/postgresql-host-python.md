# PostgreSQL host-side Python refactor

Status: parked. Updated: 2026-09-18. Resume: `$resume postgresql-host-python`.

Resume only after the current operations branch is merged and this workstream is selected.
Use its own dedicated branch from refreshed main; retain the written-spec review and plan gates below.

## Objective and authority

Replace the substantial embedded PostgreSQL inspection/configuration shell with focused host-side
Python while preserving native tools and safety/recovery behavior. Task: `tas-sidn` (deferred).
The [proposed specification](../specs/2026-09-09-postgresql-host-python-design.md) owns scope,
trade-offs, file boundaries, request/error semantics, and verification requirements.

## Current checkpoint

PostgreSQL-first scope and narrow inspect/configure operations through the existing transient
helper and authenticated pyinfra connection are approved. Written-spec approval is pending;
no implementation plan or refactor exists. Earlier independent review covered change accounting,
cancellation and error precedence; the revised protocol-v3 failure/encoding and SQL port/data/
parent-PID contract still needs scoped review.

The existing shell implementation supports readiness; this refactor is not a prerequisite for
CLI UX or host acceptance. The proposal owns current four-operation recovery, ready/absent pgpass
authority, native-HBA recovery and pyinfra change semantics. Preserve the selected cluster's native
HBA path, parser validation, protected password boundary, recovery copy and brief on-disk crash
window. No generic executor, daemon, blanket shell conversion or live-host experiments.

## Next actions when selected after the operations merge

1. Recheck the current controller baseline and review the refreshed protocol-v3 failure/encoding
   and corrected SQL parent-PID contract before approval. The [VPS acceptance report](../research/2026-09-17-operations-vps-acceptance.md) owns identified host/artifact evidence.
2. Obtain operator written-spec approval, then prepare a reviewed implementation plan and bounded
   repository-local implementation tasks through `br` under `tas-sidn`. `tas-b7kd` is historical
   correction provenance, not this refactor's delivery owner.
3. Update this handoff at the approved-plan clean-session boundary before implementation.
   Continue in the design session only if the operator explicitly asks.
4. Recheck the actual checkout and applicable guidance, read the complete specification and
   canonical deployment design, then execute the plan with characterization, implementation
   and review. Preserve the VPS/artifact checkpoint; no live-host changes are authorized here.
5. After verification, update canonical deployment vocabulary/ownership and runbook only where
   behavior or diagnostics require it; mark the proposal implemented with evidence rather than
   leaving competing current guidance. Native acceptance requires separate host authority.

Design review is not implementation verification or permission to alter the live PostgreSQL
cluster. Identified test/build/native evidence belongs to the acceptance report and historical
correction provenance to `tas-b7kd`; do not repeat completed VPS repairs.

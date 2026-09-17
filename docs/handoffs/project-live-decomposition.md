# ProjectLive decomposition

- Status: parked
- Updated: 2026-09-18
- Resume: `$resume project-live-decomposition`

Resume only after the current operations branch is merged and this workstream is selected.
Use its own dedicated branch from refreshed main; retain the approved-plan execution gates below.

## Objective

Split `TaskmanWeb.ProjectLive` into explicit workflow modules without changing its routes, socket
ownership, streams, events, or user-visible behavior.

## Durable references

- Design: [ProjectLive workflow decomposition](../specs/2026-09-03-project-live-decomposition-design.md)
- Plan: [ProjectLive workflow decomposition implementation](../plans/2026-09-04-project-live-decomposition.md)
- Beads feature: `tas-1tq`; implementation tasks: `tas-1tq.1` through `tas-1tq.9`

## Current checkpoint

Design and nine-task implementation plan are approved; no decomposition has started. `tas-1tq`
and `.1`–`.9` remain deferred until selection. Small private workflow `State` modules are nested
in their owning files and limited to pure transformations. Select/refresh the isolated target/base
before using the plan's `project-live-decomposition` commit commands; do not infer old branches.

If directory removal runs first, preserve its accepted form/navigation behavior; neither
workstream requires the other. Missing-location extraction clears only listing results and
preserves creation/movement drafts. The separately gated behavior follow-up `tas-1tq.10` depends
on `.9`; keep it in this workstream until resolved. Explicit operator completion confirmation is
required before handoff retirement. Use the existing clean-session boundary; do not request a
second boundary solely for document refresh.

## Next actions when selected after the operations merge

1. Select and refresh the isolated implementation target/base; resume through the existing
   clean-session boundary and read the complete design and plan.
2. Use subagent-driven development by default, with separate implementer and verifier agents.
3. Start `tas-1tq.1`, then execute the remaining tasks in plan order with a passing checkpoint and
   commit after each.
4. After the extraction sequence, review and separately scope the
   [missing-location behavior follow-up](../specs/2026-09-03-project-live-decomposition-design.md#missing-location-behavior-follow-up).
   Invalidate actions tied to a disappeared location while preserving recoverable input; specify
   recovery UX, reproduce active creation/detail/movement and stale/pending actions, then obtain
   behavior-design approval before regression coverage and a fix. Do not implement List deletion.
   This agreed follow-up is not part of the behavior-preserving extraction or an approved fix.

## Pending decision

Implementation target selection is required before execution. The approved design direction and
nine-task dependency order remain intact. Do not begin implementation in this review session.

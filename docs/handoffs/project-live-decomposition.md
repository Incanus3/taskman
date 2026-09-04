# ProjectLive decomposition

- Status: active
- Updated: 2026-09-04
- Resume: `$resume project-live-decomposition`

## Objective

Split `TaskmanWeb.ProjectLive` into explicit workflow modules without changing its routes, socket
ownership, streams, events, or user-visible behavior.

## Durable references

- Design: [ProjectLive workflow decomposition](../specs/2026-09-03-project-live-decomposition-design.md)
- Plan: [ProjectLive workflow decomposition implementation](../plans/2026-09-04-project-live-decomposition.md)
- Beads feature: `tas-1tq`; implementation tasks: `tas-1tq.1` through `tas-1tq.9`

## Current checkpoint

The design and implementation plan are approved. Workflow-owned `State` modules will be nested in
their containing workflow source files under the repository's narrow exception for small, private
state modules with pure transformations. Planning artifacts and implementation tracking are
complete on the `project-live-decomposition` branch, stacked above
`authenticated-hosted-access`; no production-code changes have been started.

## Next actions

1. Resume in a fresh session and read the complete design and plan.
2. Use subagent-driven development by default, with separate implementer and verifier agents.
3. Start `tas-1tq.1`, then execute the remaining tasks in plan order with a passing checkpoint and
   commit after each.

## Pending decision

None. Do not begin implementation in this planning session.

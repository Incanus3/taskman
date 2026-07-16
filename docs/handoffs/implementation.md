# Implementation handoff

**Status:** Active planning context
**Updated:** 2026-07-16

## Current position

Taskman is still in planning; no implementation has started in this repository. The Phoenix
application skeleton will be generated locally by Jakub when implementation begins.

The high-level technology direction is settled. The first implementation slice is **Projects and
basic Tasks**: create a Project, create and manage Tasks in it, and view them in the default
list-first screen.

## Next step

When implementation begins:

1. Generate the Phoenix LiveView application skeleton and configure local PostgreSQL.
2. Establish the normal local run, formatting, and test workflow.
3. Refine the minimum architecture needed for the Projects and basic Tasks slice.
4. Implement that slice as the first real MVP milestone.

Use [the MVP roadmap](../planning/roadmap.md) for the high-level sequence. Use [the development
guide](../development.md) for durable project-wide working rules and technology direction.

## Planning notes

- Do not begin implementation in the planning phase without explicit direction.
- Do not resolve the whole architecture or create the entire implementation backlog upfront.
- Before each slice, make the minimum justified architecture and abstraction decisions that the slice
  needs.
- The product documents under `docs/product/` are the authoritative current behavior.

## Relevant documents

- [MVP specification](../product/mvp-spec.md)
- [Domain model](../product/domain.md)
- [Task relationships](../product/relationships.md)
- [Agent Sessions](../product/agent-sessions.md)
- [MVP roadmap](../planning/roadmap.md)
- [Development guide](../development.md)

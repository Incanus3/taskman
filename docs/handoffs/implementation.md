# Implementation handoff

**Status:** Ready to refine Projects and basic Tasks
**Updated:** 2026-07-18

## Current position

The application foundation is complete. The Phoenix LiveView skeleton runs locally against
PostgreSQL, and the application currently has only the stock root page; no product domain,
persistence, or Projects-and-Tasks UI work has started.

`./run_postgres.sh` is the standard local path for starting PostgreSQL. It is optional when a
compatible PostgreSQL instance is already running on `localhost:5432` with the `postgres` user and
password.

The high-level technology direction is settled. The first product implementation slice remains
**Projects and basic Tasks**: create a Project, create and manage Tasks in it, and view them in the
default list-first screen.

The normal `mix precommit` workflow passed on 2026-07-18 with five tests. The `mix setup` workflow
has passed, and PostgreSQL, `mix phx.server`, and the landing page at `http://localhost:4000` have
been verified.

## Next step

Refine the minimum architecture and immediate implementation work for the first product slice:

1. Define only the Project and Task persistence, validation, and list-first LiveView decisions
   required for the initial slice.
2. Record the immediate implementation work in the repository-local Beads store.
3. Implement the first thin vertical slice with focused context and LiveView tests.

Use [the MVP roadmap](../planning/roadmap.md) for the high-level sequence. Use [the development
guide](../development.md) for durable project-wide working rules and technology direction.

## Planning notes

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

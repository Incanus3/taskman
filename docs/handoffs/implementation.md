# Implementation handoff

**Status:** First Projects and basic Tasks slice approved and ready for implementation
**Updated:** 2026-07-19

## Current position

The application foundation is complete and verified. PostgreSQL, `mix setup`, `mix phx.server`, the
stock landing page, and the normal `mix precommit` workflow have all been exercised successfully.
No product-domain persistence or UI has been implemented yet.

The first thin vertical slice has completed design and plan review. Its approved design is
`docs/specs/2026-07-18-projects-basic-tasks-first-slice-design.md`; its decision-complete execution
plan is `docs/plans/2026-07-18-projects-basic-tasks-first-slice.md`. Treat those documents as the
source of truth rather than redesigning the slice during implementation.

Project-wide implementation rules remain canonical in `AGENTS.md` and `docs/development.md`. In
particular, use their current version-control, application-boundary, Phoenix, testing, and UI
component guidance rather than copying those rules into slice-specific work.

## Work tracking

The repository-local Beads epic is `tas-x38` (**Projects and basic Tasks: first vertical slice**).
Its five child tasks follow the implementation-plan order through blocking dependencies.

The only ready task is `tas-x38.1` (**Add Project persistence boundary**). It corresponds to Task 1
of the implementation plan. Claim it when implementation begins, follow its red-green steps, and do
not begin `tas-x38.2` until its dependency is closed.

## Resume sequence

1. Use `gpt-5.6-terra` for implementation.
2. Read `AGENTS.md`, this handoff, `docs/development.md`, the approved design, and the approved plan.
3. Run `but status --format json` before any version-control operation and preserve unrelated work.
4. Inspect and claim `tas-x38.1` in the repository-local Beads store.
5. Execute Task 1 from the plan test-first, run its focused verification, and commit only that task's
   changes using `but`.
6. Update and close the Bead only after its acceptance criteria are verified; then continue with the
   next unblocked child task.

## Local runtime

`./run_postgres.sh` is the standard local PostgreSQL startup path. It is optional when a compatible
PostgreSQL instance is already running on `localhost:5432` with the `postgres` user and password.
Run Phoenix directly with `mix phx.server`.

The product documents under `docs/product/` remain authoritative for behavior outside decisions
already fixed by the approved slice design.

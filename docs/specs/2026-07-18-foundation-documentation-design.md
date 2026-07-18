# Foundation documentation update design

**Date:** 2026-07-18
**Status:** Approved design

## Goal

Bring the repository documentation in line with the generated Phoenix application and make the
repository-provided PostgreSQL helper the standard, optional local-development path.

## Scope

- Replace the generated Phoenix README with a short Taskman introduction and first-run workflow.
- Document `./run_postgres.sh` as the standard way to start the expected local PostgreSQL service,
  while allowing an already-running compatible PostgreSQL instance.
- Mark the application-foundation roadmap slice as in progress and state what still needs
  verification.
- Replace the stale planning handoff with the actual repository state and next actions.

## Content model

The root README will be the entry point: start PostgreSQL, set up dependencies/database/assets, and
run the server. It will link to the documentation index for product and planning detail.

The roadmap remains high-level. Its foundation slice will distinguish completed scaffolding from the
remaining database, workflow, and boot verification rather than claiming the slice is complete.

The handoff will state that the Phoenix skeleton exists, is still the only implemented application
surface, and that no Projects-and-Tasks work has begun. Its next step will be to verify and close the
foundation before refining the first product slice.

## Acceptance criteria

- No document says that the Phoenix skeleton has yet to be generated.
- The documented default PostgreSQL setup uses `./run_postgres.sh`.
- The README identifies Taskman rather than Phoenix boilerplate.
- The roadmap and handoff agree on the implementation stage.
- Existing `run_postgres.sh` content is not changed.

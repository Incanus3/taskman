# Implementation handoff

**Status:** Projects and basic Tasks first increment complete; roadmap slice remains in progress
**Updated:** 2026-07-19

## Current position

The application foundation remains complete and verified. The approved first Projects and basic
Tasks increment is now implemented: a user can create a Project with a required local directory,
select it from the URL-backed sidebar, and see its direct Task list. From that list, Add task opens
a URL-backed, keyboard-focused modal; invalid Task input stays in the modal, while a valid Task
closes it and appears in the direct list with the default Pending and None badges. Unknown Project
URLs show a recoverable main-panel not-found state while the sidebar remains usable.

The completed increment is defined by
`docs/specs/2026-07-18-projects-basic-tasks-first-slice-design.md` and
`docs/plans/2026-07-18-projects-basic-tasks-first-slice.md`. It completes Project
creation/selection and default Task creation/direct listing only; it does not complete the broader
Projects and basic Tasks roadmap slice.

Project-wide implementation rules remain canonical in `AGENTS.md` and `docs/development.md`. In
particular, use their current version-control, application-boundary, Phoenix, testing, and UI
component guidance rather than copying those rules into slice-specific work.

## Work tracking

The repository-local Beads epic is `tas-x38` (**Projects and basic Tasks: first vertical slice**).
Its planned persistence, Project workspace, Task modal, and slice-acceptance work has been
delivered. Keep future implementation work in the repository-local Beads store.

The next thin increment is Task editing and human-controlled lifecycle controls. Refine that
increment immediately before implementation; do not treat the completed creation/listing behavior
as authorization to expand the remaining roadmap scope.

## Verification evidence

- `mix precommit` passed with 18 tests and zero failures.
- The web-boundary proof found no `Taskman.Repo`, `Repo.`, or `Ecto.Query` references in
  `lib/taskman_web`.
- Browser smoke acceptance covered empty and invalid Project states, Project creation and
  URL-backed selection, the focused Task modal, invalid and valid Task creation, recoverable
  not-found selection, and responsive layout/focus behavior.
- Headless Chromium screenshots verified a usable stacked 390 × 844 layout with the Task modal and
  controls visible, and a usable 1440 × 1000 two-column workspace. A fresh embedded-browser modal
  session confirmed visible dialog/form state, focus on Close dialog, and no error console messages.

## Resume sequence

1. Use `gpt-5.6-terra` for implementation.
2. Read `AGENTS.md`, this handoff, `docs/development.md`, and the product documents relevant to
   Task editing and lifecycle behavior.
3. Run `but status --format json` before any version-control operation and preserve unrelated work.
4. Inspect the repository-local Beads store and create or claim the next scoped Task editing and
   lifecycle work item.
5. Refine the minimum design and test plan for editing and explicit human-controlled status changes
   before implementation; do not add automation or broader Task-detail scope.
6. Implement test-first, run focused verification plus `mix precommit`, and commit only the scoped
   change using `but`.

## Local runtime

`./run_postgres.sh` is the standard local PostgreSQL startup path. It is optional when a compatible
PostgreSQL instance is already running on `localhost:5432` with the `postgres` user and password.
Run Phoenix directly with `mix phx.server`.

The product documents under `docs/product/` remain authoritative for behavior outside decisions
already fixed by the approved slice design.

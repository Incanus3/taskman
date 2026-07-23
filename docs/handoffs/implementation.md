# Implementation handoff

**Status:** Ready to refine Task editing and lifecycle controls
**Updated:** 2026-07-23

## Current state

The first Projects and basic Tasks increment is complete:

- Projects can be created for existing local directories and selected through URL-backed sidebar
  navigation.
- A selected Project shows only its direct Tasks.
- New Tasks can be created through a URL-backed modal. Invalid input remains in the modal; valid
  input creates a Pending Task with None priority and updates the list.
- Unknown Project URLs keep the sidebar usable and isolate the not-found state to the main panel.

The completed increment is recorded in the [approved design](../specs/2026-07-18-projects-basic-tasks-first-slice-design.md),
the [archived implementation plan](../archive/plans/2026-07-18-projects-basic-tasks-first-slice.md),
and closed Beads epic `tas-x38`.

The broader Projects and basic Tasks roadmap slice remains in progress.

## What remains

The persistence model already contains `title`, `description`, `status`, `priority`, and `due_at`,
including the fixed enum values and database constraints. The current application does not yet
expose the rest of that model:

- `Taskman.Tasks` has no Task lookup or update API.
- The Task form supports creation only and exposes only the title.
- Task rows render fixed Pending and None badges rather than persisted values.
- There is no edit route, edit form state, update event, or stream refresh for an edited Task.
- A person cannot explicitly change Task lifecycle state.

The next increment is tracked by Beads epic `tas-task-editing-lifecycle-nr1`.

## Constraints for the next increment

The next increment should finish Task editing and explicit human-controlled lifecycle behavior
without pulling in the later Task-detail slice.

- Editable fields in this slice are title, description, priority, and optional local due date-time.
- Every status change is initiated by a person. Agent activity, due dates, and other application
  events never change status automatically.
- The normal forward lifecycle is Icebox → Pending → In Progress → In Review → Done. In Review may
  return to Pending or In Progress, and Will Not Do is the other terminal state. Do not hard-gate
  other human-initiated transitions.
- The unresolved-blocker warning for Done belongs with Task relationships and is not required before
  relationship persistence exists.
- Keep editing in the existing list-first context. Do not add the later Task-detail hierarchy,
  Activity/Sessions rail, relationships, checklists, deletion, Lists, or Agent Sessions.

## Execution sequence

1. `tas-task-editing-lifecycle-nr1.1` — refine the URL-backed editing and lifecycle design.
2. `tas-task-editing-lifecycle-nr1.2` — add Project-scoped Task lookup and update APIs.
3. `tas-task-editing-lifecycle-nr1.3` — add the URL-backed Task editing flow.
4. `tas-task-editing-lifecycle-nr1.4` — add human lifecycle controls and persisted row badges.
5. `tas-task-editing-lifecycle-nr1.5` — run complete acceptance and update delivery status.

The current automated baseline is `mix precommit` with 19 passing tests.

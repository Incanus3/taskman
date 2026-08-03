# Task editing and human-controlled lifecycle design

**Date:** 2026-07-31  
**Status:** Approved design  
**Delivery issue:** `tas-task-editing-lifecycle-nr1.1`

> **Superseded in part:** Task creation now exposes the complete editable field set as defined by
> the [Full Task creation form design](2026-08-03-full-task-creation-form-design.md). The Task
> editing and lifecycle decisions in this document stay authoritative.

## Goal

Finish Task editing and explicit human-controlled lifecycle behavior in the existing list-first
Project context.

This increment establishes the canonical URL-backed Task modal and makes the Task's persisted title,
description, status, priority, and optional local due date-time editable. The modal deliberately
starts narrow and will later grow into the fuller Task surface without introducing a separate edit
view or replacing its URL.

## Current baseline

The application currently supports:

- creating and selecting Projects through URL-backed navigation;
- listing a selected Project's direct Tasks;
- opening Task creation over the preserved Project list at
  `/projects/:project_id/tasks/new`;
- creating a title-only Task with Pending status and None priority;
- retaining the sidebar and isolating unknown Project URLs to a main-panel not-found state; and
- Project and Task LiveView streams.

The Task schema already contains `title`, `description`, `status`, `priority`, and `due_at`, including
the fixed enum values and database constraints. `Taskman.Tasks` does not yet expose Project-scoped
Task lookup or update APIs. Task rows render fixed Pending and None badges, and there is no route or
interaction for opening and editing an existing Task.

The verified baseline recorded by the current handoff is `mix precommit` with 19 passing tests.

## Scope

Included:

- Project-scoped Task lookup and update boundaries.
- A canonical Task URL and modal over the preserved Project list.
- Editing title, plain-text description, status, priority, and optional local due date-time.
- Server-managed, field-targeted autosave.
- Human selection of every fixed lifecycle state without automatic transitions or hard gates.
- Persisted title, status, and priority values in Task rows.
- A database invariant that Task descriptions are non-null strings.
- Focused context and LiveView coverage.

Excluded:

- A separate Task edit route or edit-only view.
- Parent-child hierarchy and nesting guides.
- Activity and Agent Sessions content.
- Relationships, checklists, deletion, Lists, and Task moves.
- Rich-text editing controls.
- Inline row editing or quick lifecycle controls.
- Automatic lifecycle transitions and hard transition gates.
- Relationship-dependent warnings when moving a Task to Done.
- Sorting, filtering, search, and pagination.

## Product decisions

### One canonical Task surface

The canonical Task URL is:

```text
/projects/:project_id/tasks/:task_id
```

It renders the selected Project's direct list with the Task modal open. Closing the modal patches
back to `/projects/:project_id`. Direct navigation to the canonical URL produces the same state as
opening it from a row.

The modal is both the Task's current editing surface and the foundation of its later full detail
surface. This increment renders only the editable Task fields. Later hierarchy, relationship,
Activity, and Agent Session capabilities extend the same route and modal.

An `/edit` suffix was rejected because it would create a temporary URL and imply separate view and
edit surfaces that the product does not need.

### Human-controlled lifecycle

The modal exposes every persisted status:

- Icebox
- Pending
- In Progress
- In Review
- Done
- Will Not Do

Selecting a status is an explicit human action and persists immediately. Agent activity, due dates,
checklist state, and other application events never change status. The UI does not hard-gate status
choices. The normal lifecycle remains guidance rather than an enforced transition graph.

Lifecycle controls live in the modal for this increment. Direct row controls were rejected for now
because they would add a second editing interaction before its value is demonstrated. The row
structure preserves space for later inline controls without requiring a redesign.

### Immediately editable autosave

The modal opens with form controls ready for editing. There is no view/edit toggle and no global
Save button.

- Title and description database writes use a 300 ms server-managed debounce.
- Status, priority, and due date-time persist immediately after a change.
- The complete form draft remains visible while each targeted field is validated and saved
  independently.
- An invalid field never prevents an unrelated valid field from saving.
- Closing, browser navigation, or selecting another Task flushes every valid dirty field before
  modal state is cleared.
- Invalid dirty fields remain at their last valid persisted values and their drafts are discarded
  when the modal closes.

Client-side form debounce was rejected because browser navigation can remove the modal before an
unsent event reaches the server. Whole-form autosave was rejected because one invalid field would
block unrelated valid changes. A client-managed JavaScript draft was rejected because the LiveView
can own the draft and persistence schedule without a second state system.

### String and optional-value semantics

There is no domain distinction between an absent description and an empty description.

- `description` is a non-null string with a schema and database default of `""`.
- Clearing the description persists `""`.
- Existing `NULL` descriptions are backfilled to `""` before the column becomes non-null.
- `title` remains non-null and must contain non-whitespace text.
- `due_at` remains nullable because no due date is a meaningful absence.
- Clearing the due date persists `nil`.

The description uses a plain multiline textarea in this increment. Rich-text editing controls are
deferred to the fuller Task modal.

## Context and persistence boundaries

`Taskman.Tasks` continues to own Task persistence. It adds:

```elixir
Tasks.get_task_for_project(project, task_id)
Tasks.update_task(project, task, attrs)
```

`get_task_for_project/2` returns the Task only when its `project_id` matches the supplied Project.
It returns `nil` for missing, malformed, and cross-Project IDs, matching the existing
`Projects.get_project/1` lookup convention.

`update_task/3` accepts a Project and a Task already obtained through the scoped lookup, verifies
that their IDs still match, casts only editable Task attributes, and never accepts ownership
changes. It returns `{:ok, task}` after a valid update, `{:error, changeset}` after validation or
constraint failure, and `{:error, :not_found}` for a mismatched Project and Task rather than
updating or leaking the other Project's Task.

The web layer calls only these public context APIs. It does not call `Taskman.Repo` or construct
Ecto queries.

The migration for `description` performs these operations in order:

1. Backfill existing `NULL` values to `""`.
2. Set the database default to `""`.
3. Add the `NOT NULL` constraint.

The `Taskman.Tasks.Task` schema sets `description` to `default: ""`. Normal changeset empty-value
handling then resolves a cleared description to `""` while a cleared `due_at` resolves to `nil`.

## LiveView state and data flow

`TaskmanWeb.ProjectLive` remains the owner of the list-first screen and modal. It does not introduce
a stateful LiveComponent.

While the canonical Task route is active, the LiveView holds:

- `selected_task`, the last persisted Task;
- `task_draft`, the complete current form parameters;
- `task_form`, a changeset-backed `to_form` built from the persisted Task and current draft;
- dirty-field metadata with a revision token for each debounced text field; and
- an autosave state used for accessible feedback.

The regular `TaskmanWeb.TaskForm` function component is extended for the canonical Task modal. Task
creation may continue to use the same component with its existing title-only configuration, while
the canonical Task route supplies the full editing configuration.

### Loading a Task

When `handle_params/3` receives the canonical Task route:

1. Load the Project through `Taskman.Projects`.
2. Load and stream that Project's direct Tasks.
3. Look up the Task through `Tasks.get_task_for_project/2`.
4. When found, initialize the persisted Task, draft, form, dirty metadata, and idle autosave state.
5. When not found, preserve the Project and Task list but render the modal-level not-found state.

A missing Project retains the existing main-panel Project-not-found behavior and does not render a
Task modal.

### Processing a form change

The editing form sends `autosave_task` changes without client-side debounce. Every event contains
the complete form params and `_target`.

For each event, the LiveView:

1. Resolve `_target` to one editable field and reject targets outside the editable whitelist.
2. Replace the complete draft and rebuild the full validation form.
3. For title or description, replace that field's pending revision token and schedule a save
   message after 300 ms.
4. For status, priority, or due date-time, attempt the targeted save immediately.
5. Persist only the targeted field through `Tasks.update_task/3`.
6. On success, replace `selected_task`, clear that field's dirty state, rebuild the form against the
   new persisted Task while retaining other drafts, and re-stream the Task.
7. Ignore scheduled messages whose revision token is no longer current.

The LiveView mailbox serializes draft events, scheduled saves, and route changes. Before clearing or
replacing modal state, route handling flushes all valid dirty fields from the latest server-side
draft. This preserves valid text typed immediately before Close, browser Back, or Task navigation.

## Modal and row behavior

The canonical Task modal contains:

- a title input;
- a plain multiline description input;
- a status select with all six lifecycle values;
- a priority select with None, Low, Medium, High, and Urgent;
- a local `datetime-local` due date-time input with minute precision;
- a Close action; and
- an `aria-live` autosave status region.

The status region communicates:

- `Couldn’t save changes` after a persistence failure;
- otherwise, `Not saved` while validation errors are present;
- otherwise, `Saving…` while valid dirty fields await persistence; and
- otherwise, `Saved` after at least one edit has persisted and no dirty fields remain.

Validation errors render beside their fields. A persistence failure retains the draft for correction
or retry. The precedence above ensures an invalid draft is not described as saved merely because an
unrelated valid field persisted. The modal does not use global flash messages for field validation.

Task rows render persisted title, status, and priority values. Human-readable labels are derived
from the fixed enum values in one project-owned presentation boundary rather than repeated as
hard-coded row content.

Each row has a stretched navigation link named by the Task title. The link covers the row's
non-interactive surface but does not contain the status, priority, or future inline controls.
Badges and later controls occupy independent interaction layers above the link. This produces a
large pointer target and a normal keyboard-focusable link without nesting interactive elements or
turning the row into a button.

## Invalid and not-found behavior

An invalid draft remains visible with inline errors while the database and Task row retain the last
valid value. Other valid targeted fields continue to save. Closing the modal discards only invalid
dirty drafts.

For a valid Project and a missing, malformed, or cross-Project Task ID:

- keep the requested canonical URL unchanged;
- preserve the selected Project and its direct Task list;
- show a non-editable Task-not-found state inside the modal; and
- provide a Close action back to the Project URL.

Cross-Project lookup is indistinguishable from a missing Task and does not reveal Task data from the
other Project.

## Testing strategy

### Context and migration tests

Cover:

- Project-scoped lookup of an existing Task.
- Missing, malformed, and cross-Project lookup results.
- Updates to title, description, status, priority, and due date-time.
- Every fixed status and priority value.
- Rejection of Project ownership changes and mismatched Project/Task updates.
- Required non-whitespace title.
- Empty description persistence as `""`.
- Clearing `due_at` to `nil`.
- The description database default and non-null constraint.

### LiveView tests

Cover:

- Opening the canonical Task URL from a row while preserving the Project list.
- Direct navigation to the same modal.
- Closing back to the selected Project.
- Modal-level not-found behavior for missing, malformed, and cross-Project Task IDs.
- Debounced title and description persistence.
- Immediate status, priority, and due date-time persistence.
- Persisted title, status, and priority row refresh through the Task stream.
- An invalid title remaining visible without changing persistence.
- A valid field saving while another field has an invalid draft.
- Closing or navigating away flushing valid dirty fields and discarding invalid drafts.
- Stale scheduled save messages not overwriting newer values.
- Stable DOM IDs, complete selector options, accessible autosave feedback, and keyboard-focusable
  Task navigation.

The autosave delay is application-configurable: normal environments use 300 ms and the test
environment uses 0 ms. Tests synchronize with the LiveView mailbox instead of sleeping. LiveView
assertions use stable selectors and user-visible outcomes rather than raw HTML or styling details.

### Final verification

Run focused context and LiveView tests during implementation, then run `mix precommit`. Complete a
responsive browser smoke test covering desktop and narrow layouts, row navigation, modal editing,
autosave feedback, validation, direct URLs, and not-found recovery.

## Acceptance criteria

- A Task row opens `/projects/:project_id/tasks/:task_id` in a modal over the preserved Project
  list.
- The same canonical URL works through direct navigation.
- The modal edits title, plain-text description, status, priority, and optional local due date-time
  without a separate edit mode or Save button.
- Valid title and description changes persist after a 300 ms server-managed debounce.
- Status, priority, and due date-time changes persist immediately.
- Every lifecycle state is human-selectable, with no automatic transition or hard gate.
- Invalid drafts do not overwrite persisted values or block unrelated valid fields from saving.
- Closing or navigating away flushes valid dirty fields and discards invalid drafts.
- Task rows reflect persisted title, status, and priority updates.
- Empty descriptions persist as `""`; the database rejects `NULL` descriptions.
- Missing, malformed, and cross-Project Task IDs preserve the Project list and render the same
  modal-level not-found state.
- Modules in `TaskmanWeb` use public contexts rather than direct Repo calls or Ecto queries.
- Focused tests and `mix precommit` pass, and responsive browser behavior is verified.

## Implementation boundaries

Expected files include:

- a generated migration altering `tasks.description`;
- `lib/taskman/tasks/task.ex`;
- `lib/taskman/tasks.ex`;
- `lib/taskman_web/router.ex`;
- `lib/taskman_web/live/project_live.ex`;
- `lib/taskman_web/live/project_live.html.heex`;
- `lib/taskman_web/components/task_form.ex`;
- focused context and LiveView test files; and
- delivery documentation updated after acceptance.

No new dependency, stateful LiveComponent, or custom JavaScript hook is expected.

## Next-session checklist

1. Read this specification before planning or implementation.
2. Confirm the current handoff and Beads issue state.
3. Generate the description migration with `mix ecto.gen.migration`.
4. Plan implementation in the existing delivery sequence:
   Project-scoped context APIs, canonical modal/autosave flow, persisted row values, then acceptance.
5. Preserve unrelated workspace changes and use repository-approved version-control tooling.

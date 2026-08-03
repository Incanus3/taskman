# Full Task creation form design

**Date:** 2026-08-03  
**Status:** Approved design

## Goal

Let a user provide all currently editable Task details while creating a Task, while preserving an
explicit creation boundary.

Task creation and editing use the same form controls, but they retain different persistence
semantics: creation writes the complete Task only after the user selects **Create task**, whereas
editing continues to autosave targeted fields after the Task exists.

## Current baseline

The application has one list-first `TaskmanWeb.ProjectLive` with URL-backed Task modals:

- `/projects/:project_id/tasks/new` opens Task creation over the selected Project's direct Task
  list.
- `/projects/:project_id/tasks/:task_id` opens the canonical Task editing modal over the same list.

Both modals render the `TaskmanWeb.TaskForm` function component. Its `:new` mode currently renders
only the title and explicit Cancel and Create task actions. Its `:edit` mode renders title,
description, status, priority, and due date-time and uses field-targeted autosave.

`Taskman.Tasks.create_task/2` and the Task changeset already accept every editable field. A new
`Task` supplies the persisted defaults of Pending status, None priority, an empty description, and
no due date.

The current new-Task form is built from a Task without its server-owned Project association. That
changeset is internally invalid even when every visible value is valid, because `project_id` is
required. Creation currently tolerates this because `Tasks.create_task/2` assigns ownership only at
submission time. The expanded form needs a Project-scoped form changeset so its validity can
reliably control whether Create task is enabled.

This design supersedes only the title-only Task creation decisions in:

- [Projects and basic Tasks first-slice design](2026-07-18-projects-basic-tasks-first-slice-design.md)
- [Task editing and human-controlled lifecycle design](2026-07-31-task-editing-lifecycle-design.md)

Their remaining decisions stay authoritative.

## Scope

Included:

- Show title, description, status, priority, and optional local due date-time during Task creation.
- Use the same project-owned form controls and option sources for creation and editing.
- Preserve complete-form validation before creation.
- Disable Create task while the current form fails any change-verifiable validation.
- Create the Task in one explicit submission.
- Close the modal and return to the selected Project after successful creation.
- Preserve entered values and show inline errors when submission-time validation or constraints
  reject a form that passed change-time validation.
- Cover the expanded creation flow with focused LiveView tests.

Excluded:

- Autosaving an unpersisted Task.
- Creating placeholder or draft Task records when the modal opens or a field changes.
- Changing the canonical Task editing route or its autosave behavior.
- Keeping the newly created Task modal open after creation.
- Adding fields beyond those already supported by Task editing.
- Schema, database, migration, or route changes.

## Product decisions

### One field set with two persistence modes

Creation and editing expose the same five controls:

- title;
- plain-text description;
- status;
- priority; and
- optional local due date-time with minute precision.

The controls use the same labels, DOM IDs, status options, priority options, and input types in both
flows. This makes the Task surface consistent and prevents the creation and editing field sets from
drifting.

The persistence boundary remains mode-specific:

- Creation validates changes in memory and writes all fields together only when the user selects
  **Create task**.
- Editing keeps the existing field-targeted autosave behavior.

An approach that created a placeholder Task and immediately switched to editing autosave was
rejected. Opening the modal or typing into it must not create persistent data before explicit user
confirmation.

Separate create and edit form components were also rejected. Their fields are currently identical,
and separate components would duplicate presentation or introduce an unnecessary abstraction.

### Creation defaults

Opening Task creation builds the existing changeset-backed form from a new `Task`. The controls
therefore display the existing domain defaults:

- status: Pending;
- priority: None;
- description: empty; and
- due date-time: empty.

The user may override any default before submitting. The application does not introduce a second
set of web-layer defaults.

The form changeset also receives the selected Project through a public `Taskman.Tasks` boundary.
This makes the server-owned association available to validation without accepting `project_id` from
form parameters.

### Validity-gated creation

Create task is disabled whenever the latest server-validated form changeset is invalid. The initial
blank form is invalid because title is required, so its Create task action is disabled. Each
`validate_task` change refreshes the full form and its action state.

This disabled state prevents the normal user interface from submitting known-invalid values. It is
not the persistence authority: the server still validates every submitted parameter and the
database still enforces its constraints. A submission can fail after change-time validation
because of a database-only rule, a race with other state, or a client that bypasses or submits stale
UI state. Such a failure keeps the modal open, preserves every entered value, and renders the
returned errors inline when field errors are available.

### Completion and cancellation

A successful submission creates one Task with the complete valid form state, inserts it into the
selected Project's Task stream, and patches back to `/projects/:project_id`. The creation modal
closes. The user can open the new Task from its row if they want to make later autosaved changes.

Cancel patches back to the selected Project without persisting a Task. Browser and modal
cancellation retain the existing route behavior.

Keeping the new Task open after creation was rejected because the creation form already captures
all current details and the agreed flow should close on success.

## Component and LiveView design

`TaskmanWeb.TaskForm` remains a regular function component with `:new` and `:edit` modes.

The five field controls render for both modes. Mode continues to determine behavior around that
shared field set:

- `:new` uses `validate_task` on change, `save_task` on submit, and renders Cancel and Create task
  actions. The LiveView derives a plain creation-enabled boolean from the changeset's validity and
  passes it to the form component.
- `:edit` uses `autosave_task` on change, retains its hidden submit control for navigation-safe
  flushing, renders no creation footer, and continues to show the modal-level autosave status.

The form remains changeset-backed and assigned through `to_form/1`; templates do not access
changesets or `Phoenix.HTML.Form.source` directly.

No stateful LiveComponent is introduced. `TaskmanWeb.ProjectLive` remains the owner of creation,
editing, route transitions, and the Task stream.

`Taskman.Tasks` adds a Project-scoped form boundary, using the existing `change_task` name or an
equivalently focused API:

```elixir
Tasks.change_task(project, attrs)
```

It builds a new Task with `project_id` assigned from the trusted Project struct before applying the
regular Task changeset. The parameter map cannot replace ownership because `project_id` is not a
cast field. The existing Task-based `change_task(task, attrs)` behavior remains available for
editing.

## Data flow

### Opening creation

When the new-Task route is active:

1. Load the selected Project and its direct Tasks through the existing context APIs.
2. Build `task_form` from the Project-scoped Task form boundary.
3. Render all five controls using values and defaults from that form.
4. Assign the changeset's validity to a plain creation-enabled boolean.
5. Render Create task disabled because the required title is empty.
6. Do not create or update any Task.

### Validating creation

On `validate_task`:

1. Receive the complete `"task"` parameter map.
2. Build a Project-scoped changeset for a new Task from all submitted fields.
3. Mark it for validation and assign its `to_form` representation.
4. Assign the changeset's validity to the plain creation-enabled boolean.
5. Render any errors inline while preserving the complete draft.
6. Enable Create task only when that boolean is true.
7. Perform no persistence call.

Validation remains whole-form for creation because the eventual operation is one complete insert.
The field-targeted isolation required by editing autosave does not apply to an unpersisted Task.

### Submitting creation

On `save_task`:

1. Pass the complete `"task"` parameter map and selected Project to
   `Tasks.create_task/2`.
2. On success, insert the returned Task into the stream, mark the stream non-empty, and patch back
   to the selected Project URL.
3. On a validation or constraint error returned as a changeset, assign that changeset as
   `task_form`, set the creation-enabled boolean to false, retain the modal, preserve the submitted
   values, and render available inline errors.

Project ownership remains assigned inside `Tasks.create_task/2`; it is never accepted from form
parameters.

## Error and navigation behavior

- A missing Project keeps the existing Project-not-found behavior and does not render Task
  creation.
- Known-invalid Task input keeps Create task disabled.
- Errors render beside the affected controls through the shared input component.
- If a stale or bypassed client submits known-invalid input, server validation rejects it, keeps the
  creation URL and modal open, and persists no partial Task.
- Submission-time constraint errors keep the creation URL and modal open, preserve the complete
  submitted draft, and render field errors when available.
- Cancel and modal close discard the in-memory form draft.
- Editing persistence failures and navigation-safe dirty-field flushing are unchanged.

## Testing and verification

Focused LiveView tests should verify:

- the creation modal renders all five controls;
- a new form displays Pending and None defaults, empty optional values, and a disabled Create task
  action;
- entering a valid title enables Create task, while making a change-verifiable field invalid
  disables it again;
- submitting non-default values persists title, description, status, priority, and due date-time;
- successful creation adds the Task row, closes the modal, and returns to the Project URL;
- directly submitted invalid parameters keep the modal open, preserve submitted values, show inline
  errors, and create no Task;
- Cancel closes the modal and creates no Task; and
- the canonical editing modal still exposes the same controls and retains autosave behavior.

Existing context tests establish that `Tasks.create_task/2` persists the supported fields and
defaults. Focused context coverage should establish that the new Project-scoped form boundary
assigns trusted ownership, accepts all editable attributes, and does not allow parameters to replace
the Project association. Do not duplicate unrelated creation coverage solely because the fields
become visible in a second UI flow.

There is no current Task constraint that passes change-time validation but predictably fails at
insert time. Do not introduce an artificial rule solely to exercise that path. If such a rule is
added later, its creation-flow coverage must verify preservation of the complete submitted draft
and its returned field errors.

Final verification for implementation is:

```text
mix precommit
```

The implementation should also be inspected for planning terminology leaked into production code,
tests, user-facing text, command output, or APIs.

## Expected file boundaries

The implementation is expected to change:

- `lib/taskman/tasks.ex`
- `lib/taskman_web/live/project_live.ex`
- `lib/taskman_web/live/project_live.html.heex`
- `lib/taskman_web/components/task_form.ex`
- `test/taskman/tasks_test.exs`
- `test/taskman_web/live/project_live_test.exs`

Changes to `lib/taskman/tasks/task.ex`, routes, migrations, or assets are not expected unless
implementation uncovers a concrete mismatch with this specification.

## Next-session checklist

1. Create or select the repository-local Beads delivery issue for this increment.
2. Write an implementation plan from this specification.
3. Implement the shared creation field set with test-driven development.
4. Run focused LiveView tests and `mix precommit`.
5. Record verification evidence in the delivery issue and retire the active handoff when the
   increment is complete.

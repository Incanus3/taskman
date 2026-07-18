# Projects and basic Tasks first-slice design

**Date:** 2026-07-18
**Status:** Approved design

## Goal

Deliver the first thin product workflow: a user can create a Project for a local directory, select
that Project, create a Task with the product defaults, and see the Task in the Project's direct Task
list.

This slice establishes the smallest useful persistence and LiveView boundaries for later Project and
Task work. It does not implement the complete Projects and basic Tasks roadmap slice.

## Scope

Included:

- Create and persist a Project with a name and primary local directory.
- List and select Projects in the left sidebar.
- Show the selected Project's direct Task list in the main panel.
- Open Task creation in a modal over the preserved Project list.
- Create a title-only Task with the fixed Pending status and None priority defaults.
- Keep an unknown Project URL usable by retaining the Project sidebar and showing a not-found state
  in the main panel.

Excluded:

- Project or Task editing and deletion.
- Task lifecycle controls.
- Lists and Task moves.
- Task detail, relationships, checklists, and Agent Sessions.
- Sorting, filtering, search, and pagination.

## Context boundaries

Domain capabilities, rather than the ownership tree, define context boundaries.

`Taskman.Projects` owns Project creation, lookup, and listing. It is the only application layer in
this slice that calls `Taskman.Repo` for Projects.

`Taskman.Tasks` owns the Task schema, Task defaults, creation, and Project-scoped Task queries. It is
the only application layer in this slice that calls `Taskman.Repo` for Tasks. Its public API accepts
Project structs so that ownership is assigned by application code rather than user-controlled
parameters:

- `Tasks.create_task(project, attrs)`
- `Tasks.list_tasks_for_project(project)`

The LiveView calls only public context APIs and never calls `Taskman.Repo` directly. A later Lists
slice may introduce a separate `Taskman.Lists` context; neither Tasks nor future Lists need to be
placed in the Projects context.

## Persistence model

### Project

`Taskman.Projects.Project` has:

- `name`, a required string.
- `primary_directory`, a required string containing a normalized absolute path.
- Normal timestamps.

Project creation trims the name, expands the submitted path to an absolute path, and verifies with
`File.dir?/1` that it names an existing local directory. Invalid input is returned as changeset
errors. Multiple Projects may reference the same directory because the product specification does
not require uniqueness.

### Task

`Taskman.Tasks.Task` has:

- `project_id`, a required foreign key assigned internally.
- `title`, a required string.
- `description`, an optional string backed by a text column.
- `status`, an `Ecto.Enum` with `:icebox`, `:pending`, `:in_progress`, `:in_review`, `:done`, and
  `:will_not_do`.
- `priority`, an `Ecto.Enum` with `:none`, `:low`, `:medium`, `:high`, and `:urgent`.
- `due_at`, an optional local `:naive_datetime`.
- Normal timestamps.

New Tasks default to `:pending` and `:none`. The initial creation form exposes only `title`; the
other confirmed fields are present now so the next Task-management increment does not require an
artificial intermediate data model.

Required columns, enum check constraints, and the Project foreign key are enforced in PostgreSQL as
well as in changesets. The Task table does not contain `list_id`; the Lists slice will introduce
nullable List ownership when its behavior is designed.

Queries use a stable oldest-first ordering with `id` as a tie-breaker. The first slice contains only
direct Project Tasks, so `Tasks.list_tasks_for_project/1` returns every Task owned by that Project.

## LiveView routes and state

One `TaskmanWeb.ProjectLive` owns the list-first screen:

- `/` renders the Project sidebar and a main-panel prompt to select or create a Project.
- `/projects/:project_id` renders the selected Project's direct Task list.
- `/projects/:project_id/tasks/new` renders the same selected Project list with the Task creation
  modal open.

Project links use patches so selection is represented in the URL without replacing the LiveView.
Creating a Project navigates to its Project URL. Opening and cancelling Task creation patch between
the selected Project and new-Task routes, preserving the list behind the modal.

The Project creation control and form live in the sidebar. The selected Project is visibly
highlighted. The main panel contains the selected Project heading, an Add task button, and its Task
list. The initial Task rows show title, Pending status, and None priority.

Projects and Tasks use LiveView streams. Selecting a Project resets the Task stream using
`Tasks.list_tasks_for_project/1` and refreshes the Project stream so selection styling is updated.
Creating a Task inserts the new record into the Task stream and patches back to the selected Project
route.

The Task modal uses the shared core modal and a regular function component driven by a `to_form/2`
assign. It does not introduce a stateful LiveComponent. Its DOM and form boundary are reusable by a
later edit route and edit changeset without implementing editing in this slice.

## Empty, invalid, and error states

With no Projects, the sidebar and main panel explain how to create the first Project. A selected
Project with no Tasks shows an empty state alongside the Add task action.

Project and Task validation errors render inline through the standard input components. An invalid
Task submission keeps the modal open. Inputs expose submitting states so duplicate submissions are
not encouraged.

`Projects.get_project/1` returns `nil` for a missing or unparseable ID rather than raising. The
LiveView then keeps the Project sidebar populated and interactive, displays a Project-not-found state
in the main panel, hides Project-only actions, leaves the requested URL unchanged, and resets the
Task stream to empty. Patching to a valid Project recovers normally without reloading the page.

## Testing

Context tests cover:

- Required Project attributes.
- Primary-directory normalization and rejection of paths that are not existing directories.
- Required Task title, Project ownership, and Pending/None defaults.
- Project-scoped Task queries that do not leak Tasks from another Project.

LiveView tests cover:

- The empty root state and stable key DOM IDs.
- Project creation, navigation, and selected-sidebar styling.
- Direct Task-list isolation between Projects.
- Opening and cancelling the new-Task modal while preserving the selected Project list.
- Invalid Task submission remaining in the modal with errors.
- Successful Task creation closing the modal and adding the row.
- An unknown Project URL retaining a working sidebar and recovering after a valid Project is
  selected.

Tests use `Phoenix.LiveViewTest` selectors against stable DOM IDs and assert user-visible outcomes
rather than raw HTML or internal function calls. Filesystem validation tests use temporary
directories owned by the test.

## Acceptance criteria

- A user can create and select a Project whose primary directory exists locally.
- Project selection is reflected in the URL and highlighted in the sidebar.
- The selected Project's main panel shows only its direct Tasks.
- Add task opens a URL-backed modal over the preserved Project list.
- Submitting a title creates a Task owned by the selected Project with Pending status and None
  priority, closes the modal, and displays the Task in the list.
- Invalid Project and Task input produces inline errors without losing the surrounding screen.
- An unknown Project URL preserves a functional Project sidebar and shows the not-found state only
  in the main panel.
- LiveView modules do not call `Taskman.Repo` directly.
- Focused context and LiveView tests pass through the normal `mix precommit` workflow.

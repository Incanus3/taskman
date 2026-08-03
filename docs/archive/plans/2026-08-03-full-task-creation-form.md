# Full Task creation form implementation plan

**Status:** Completed — verified on 2026-08-03

**Verification:** `mix precommit` exited 0 with 47 tests and zero failures; the focused context,
LiveView, and autosave suites exited 0 with 13, 19, and 7 tests respectively. Both architecture
and terminology boundary searches exited 1 with no matches. The final implementation review of
`a2bd792..76725f8` was clean with no findings.

> Execute this plan task by task. Each implementation task starts with a failing focused test and
> ends with focused verification and a selective commit before the next task begins.

**Goal:** Let users provide every currently editable Task field during explicit creation, with
Create task enabled only for a valid server-side draft.

**Architecture:** `Taskman.Tasks` supplies a Project-scoped new-Task changeset so form validity
includes trusted ownership without accepting `project_id` from parameters. `TaskmanWeb.ProjectLive`
continues to own the URL-backed creation flow, while the existing stateless `TaskForm` renders one
shared field set with explicit-submit creation behavior and unchanged autosave editing behavior.

**Tech stack:** Elixir 1.17+, Phoenix 1.8, LiveView 1.2, Ecto/PostgreSQL, HEEx, Tailwind CSS, ExUnit,
and Phoenix.LiveViewTest.

**Required design:** Read
[`docs/specs/2026-08-03-full-task-creation-form-design.md`](../../specs/2026-08-03-full-task-creation-form-design.md)
completely before implementation.

**Delivery issue:** `tas-full-task-creation-form-atf`

## Global constraints

- Creation persists nothing until the user explicitly submits Create task.
- Creation closes and returns to `/projects/:project_id` after a successful insert.
- Creation and editing expose title, description, status, priority, and optional local due
  date-time through the same project-owned controls.
- Create task is disabled whenever the latest server-validated creation form is invalid.
- The server validates every submitted parameter even though the normal UI prevents a known-invalid
  submission.
- A rejected submission keeps the modal open, preserves the complete draft, and renders available
  inline field errors.
- Project ownership comes only from the selected trusted `Project`; never cast `project_id`.
- Preserve the canonical Task editing route and its existing field-targeted autosave behavior.
- Keep Repo calls, Ecto queries, schemas, and changesets out of template code.
- Use the existing `<.input>`, `<.modal>`, `to_form/1`, LiveView stream, and Task option helpers.
- Add no dependency, migration, route, stateful LiveComponent, or JavaScript hook.
- Preserve unrelated workspace changes.
- At each version-control checkpoint, use the repository-approved tooling to inspect the diff and
  commit only the task's listed files.

---

## File map

- `lib/taskman/tasks.ex` — constructs both Project-scoped new-Task changesets and existing
  Task-scoped editing changesets.
- `lib/taskman_web/live/project_live.ex` — supplies the selected Project to new-Task form creation
  and validation and exposes changeset validity as a plain component assign.
- `lib/taskman_web/live/project_live.html.heex` — passes the plain creation-enabled state to the
  shared Task form.
- `lib/taskman_web/components/task_form.ex` — renders the shared five-field Task controls and the
  validity-gated creation action.
- `test/taskman/tasks_test.exs` — verifies trusted ownership and editable attributes in the
  Project-scoped form boundary.
- `test/taskman_web/live/project_live_test.exs` — verifies creation fields, defaults, validity
  gating, complete persistence, error preservation, cancellation, and close-after-success.
- `docs/archive/plans/2026-08-03-full-task-creation-form.md`, `docs/handoffs/INDEX.md`,
  `docs/README.md`, and the Beads delivery issue — track implementation and final verification
  without duplicating the specification.

---

### Task 1: Add the Project-scoped new-Task form boundary

**Files:**

- Modify: `lib/taskman/tasks.ex`
- Test: `test/taskman/tasks_test.exs`

**Interfaces:**

- Consumes: `%Taskman.Projects.Project{id: project_id}` and editable Task attributes.
- Produces:
  `Tasks.change_task(project, attrs) :: Ecto.Changeset.t()` for an unpersisted Task whose
  `project_id` comes from the Project.
- Preserves:
  `Tasks.change_task(task, attrs) :: Ecto.Changeset.t()` for an existing or explicitly constructed
  Task.
- Guarantees: an attribute named `project_id` cannot replace the trusted Project association.

- [x] **Step 1: Write the failing Project-scoped changeset test**

Add this test after the existing `create_task/2` ownership tests in
`test/taskman/tasks_test.exs`:

```elixir
test "change_task/2 builds a valid Project-owned creation changeset" do
  project = project_fixture(%{})
  other_project = project_fixture(%{})

  changeset =
    Tasks.change_task(project, %{
      title: "  Planned task  ",
      description: "Creation details",
      status: :in_progress,
      priority: :high,
      due_at: ~N[2026-08-03 16:00:00],
      project_id: other_project.id
    })

  assert changeset.valid?
  assert Ecto.Changeset.get_field(changeset, :project_id) == project.id
  assert Ecto.Changeset.get_field(changeset, :title) == "Planned task"
  assert Ecto.Changeset.get_field(changeset, :description) == "Creation details"
  assert Ecto.Changeset.get_field(changeset, :status) == :in_progress
  assert Ecto.Changeset.get_field(changeset, :priority) == :high
  assert Ecto.Changeset.get_field(changeset, :due_at) == ~N[2026-08-03 16:00:00]
end
```

- [x] **Step 2: Run the focused context test and verify the missing boundary**

Run:

```text
mix test test/taskman/tasks_test.exs
```

Expected: the new test fails with a function-clause error because `Tasks.change_task/2` currently
accepts only a `Task`.

- [x] **Step 3: Overload `change_task/2` for trusted Project ownership**

Replace the existing `change_task/2` definition in `lib/taskman/tasks.ex` with:

```elixir
def change_task(owner, attrs \\ %{})

def change_task(%Project{id: project_id}, attrs) do
  %Task{project_id: project_id}
  |> Task.changeset(attrs)
end

def change_task(%Task{} = task, attrs) do
  Task.changeset(task, attrs)
end
```

Keep `project_id` out of `Task.changeset/2`'s cast list. Do not change `create_task/2` or
`update_task/3`.

- [x] **Step 4: Run the focused context suite**

Run:

```text
mix test test/taskman/tasks_test.exs
```

Expected: every Task context test passes, including the new Project-scoped changeset test.

- [x] **Step 5: Commit only the context boundary and its test**

Inspect the workspace diff with the repository-approved version-control tooling, select only
`lib/taskman/tasks.ex` and `test/taskman/tasks_test.exs`, then create a commit on the implementation
branch with message:

```text
add Project-scoped Task creation form
```

Expected: the commit contains only those two files; handoff, plan, tracker, and unrelated changes
remain outside it.

---

### Task 2: Reuse the full Task form for validity-gated creation

**Files:**

- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `lib/taskman_web/components/task_form.ex`
- Test: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: `Tasks.change_task(project, attrs)` from Task 1.
- Produces: `#task-form` with `#task-title`, `#task-description`, `#task-status`,
  `#task-priority`, and `#task-due-at` in both `:new` and `:edit` modes.
- Produces: `#create-task[disabled]` when the creation changeset is invalid and an enabled
  `#create-task` when it is valid.
- Produces: `task_create_enabled? :: boolean()` in `ProjectLive` and
  `create_enabled? :: boolean()` in `TaskForm`; templates never inspect a changeset or
  `Phoenix.HTML.Form.source`.
- Preserves: creation uses `validate_task` and `save_task`; editing uses `autosave_task` and
  `submit_task_edit`.

- [x] **Step 1: Add a failing rendering and validity-gating test**

Add this test near the existing new-Task modal tests in
`test/taskman_web/live/project_live_test.exs`:

```elixir
test "new Task form exposes every editable field and gates creation on validity", %{conn: conn} do
  project = project_fixture(%{})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

  assert has_element?(view, "#task-title")
  assert has_element?(view, "#task-description")
  assert has_element?(view, "#task-status option[selected][value='pending']")
  assert has_element?(view, "#task-priority option[selected][value='none']")
  assert has_element?(view, "#task-due-at[value='']")
  assert has_element?(view, "#create-task[disabled]")

  valid_params = %{
    title: "Ready to create",
    description: "",
    status: "pending",
    priority: "none",
    due_at: ""
  }

  view
  |> form("#task-form", task: valid_params)
  |> render_change()

  refute has_element?(view, "#create-task[disabled]")

  view
  |> form("#task-form", task: Map.put(valid_params, :title, ""))
  |> render_change()

  assert has_element?(view, "#create-task[disabled]")
  assert has_element?(view, "#task-form [data-role='field-error']")
end
```

- [x] **Step 2: Expand the existing creation test to cover the complete transaction**

Replace the current test named
`"successful Task hides the empty state and persists its defaults after validation errors"` with:

```elixir
test "creates every Task field explicitly and closes the modal", %{conn: conn} do
  project = project_fixture(%{})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

  task_params = %{
    title: "Ship complete creation",
    description: "All details supplied up front",
    status: "in_progress",
    priority: "urgent",
    due_at: "2026-08-03T16:00"
  }

  view
  |> form("#task-form", task: task_params)
  |> render_change()

  refute has_element?(view, "#create-task[disabled]")

  view
  |> form("#task-form", task: task_params)
  |> render_submit()

  assert_patch(view, ~p"/projects/#{project.id}")
  refute has_element?(view, "#task-modal")

  [task] = Tasks.list_tasks_for_project(project)
  assert task.title == "Ship complete creation"
  assert task.description == "All details supplied up front"
  assert task.status == :in_progress
  assert task.priority == :urgent
  assert task.due_at == ~N[2026-08-03 16:00:00]
  assert has_element?(view, "#task-#{task.id}")
  assert has_element?(view, "#task-status-#{task.id}", "In Progress")
  assert has_element?(view, "#task-priority-#{task.id}", "Urgent")
  assert has_element?(view, "#tasks-empty.hidden.only\\:block")
end
```

The test module already aliases `Taskman.Tasks`; do not add a second alias.

- [x] **Step 3: Add a failing defensive-submission preservation test**

Add:

```elixir
test "direct invalid submission preserves the complete draft", %{conn: conn} do
  project = project_fixture(%{})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

  invalid_params = %{
    title: "",
    description: "Keep this draft",
    status: "in_review",
    priority: "high",
    due_at: "2026-08-04T09:30"
  }

  view
  |> form("#task-form", task: invalid_params)
  |> render_submit()

  assert has_element?(view, "#task-modal")
  assert has_element?(view, "#create-task[disabled]")
  assert has_element?(view, "#task-form [data-role='field-error']")
  assert has_element?(view, "#task-description", "Keep this draft")
  assert has_element?(view, "#task-status option[selected][value='in_review']")
  assert has_element?(view, "#task-priority option[selected][value='high']")
  assert has_element?(view, "#task-due-at[value='2026-08-04T09:30']")
  assert Tasks.list_tasks_for_project(project) == []
end
```

This directly invokes the server submission path. It does not represent the normal browser
interaction, where the disabled Create task button prevents the submission.

- [x] **Step 4: Run the LiveView suite and verify the missing creation controls**

Run:

```text
mix test test/taskman_web/live/project_live_test.exs
```

Expected: the new tests fail because creation currently hides four fields, has no `#create-task`
ID or validity-driven disabled state, and builds validation changesets without trusted Project
ownership.

- [x] **Step 5: Make creation form construction Project-scoped**

In `mount/3` in `lib/taskman_web/live/project_live.ex`, initialize the plain validity assign:

```elixir
|> assign(:task_create_enabled?, false)
```

In the `:new_task` branch of `apply_route/2`, construct the changeset once and use it for both the
form and the boolean:

```elixir
changeset = Tasks.change_task(project)

socket
|> clear_task_modal_state()
|> assign_project_state(project, false, Tasks.list_tasks_for_project(project))
|> assign(:task_form, to_form(changeset))
|> assign(:task_create_enabled?, changeset.valid?)
```

Replace `handle_event("validate_task", ...)` with:

```elixir
def handle_event("validate_task", %{"task" => task_params}, socket) do
  changeset =
    socket.assigns.selected_project
    |> Tasks.change_task(task_params)
    |> Map.put(:action, :validate)

  {:noreply,
   socket
   |> assign(:task_form, to_form(changeset))
   |> assign(:task_create_enabled?, changeset.valid?)}
end
```

In the error branch of `handle_event("save_task", ...)`, retain the returned form and disable
creation:

```elixir
{:error, changeset} ->
  {:noreply,
   socket
   |> assign(:task_form, to_form(changeset))
   |> assign(:task_create_enabled?, false)}
```

Reset `task_create_enabled?` in `clear_task_modal_state/1`:

```elixir
|> assign(:task_create_enabled?, false)
```

Keep the successful `save_task` branch unchanged so `Tasks.create_task/2` remains the persistence
authority.

- [x] **Step 6: Render one shared field set and gate Create task**

At the new-Task `TaskForm.form` call in `lib/taskman_web/live/project_live.html.heex`, pass:

```heex
create_enabled?={@task_create_enabled?}
```

Add the boolean attribute in `lib/taskman_web/components/task_form.ex`:

```elixir
attr :create_enabled?, :boolean, default: false
```

In `lib/taskman_web/components/task_form.ex`, keep the title input in place and remove
`:if={@mode == :edit}` from the wrapper containing description, status, priority, and due date-time:

```heex
<div class="mt-4 space-y-4">
  <.input
    field={@form[:description]}
    id="task-description"
    type="textarea"
    label="Description"
    rows="6"
    class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
    error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
  />
  <.input
    field={@form[:status]}
    id="task-status"
    type="select"
    label="Status"
    options={TaskComponents.status_options()}
  />
  <.input
    field={@form[:priority]}
    id="task-priority"
    type="select"
    label="Priority"
    options={TaskComponents.priority_options()}
  />
  <.input
    field={@form[:due_at]}
    id="task-due-at"
    type="datetime-local"
    label="Due date and time"
    step="60"
  />
</div>
```

Add a stable ID and changeset-driven disabled attribute to the existing creation button:

```heex
<button
  id="create-task"
  type="submit"
  disabled={!@create_enabled?}
  phx-disable-with="Creating…"
  class="rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/30 transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60"
>
  Create task
</button>
```

Do not add a second form, duplicate the fields, or change edit-mode events.

- [x] **Step 7: Run focused context, LiveView, and autosave suites**

Run:

```text
mix test test/taskman/tasks_test.exs
mix test test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/live/project_live_autosave_test.exs
```

Expected: all three commands pass. The autosave suite establishes that sharing the fields did not
change editing persistence or navigation-safe flush behavior.

- [x] **Step 8: Commit only the LiveView creation flow and its tests**

Inspect the workspace diff with the repository-approved version-control tooling and select only
`lib/taskman_web/live/project_live.ex`, `lib/taskman_web/live/project_live.html.heex`,
`lib/taskman_web/components/task_form.ex`, and `test/taskman_web/live/project_live_test.exs`. Create
a commit on the implementation branch with message:

```text
reuse full Task form for creation
```

Expected: the commit contains only those four files and is stacked after Task 1.

---

### Task 3: Verify and record the completed increment

**Files:**

- Move after all checks pass:
  `docs/plans/2026-08-03-full-task-creation-form.md` to
  `docs/archive/plans/2026-08-03-full-task-creation-form.md`
- Modify: `docs/README.md`
- Delete after all checks pass: `docs/handoffs/full-task-creation-form.md`
- Modify: `docs/handoffs/INDEX.md`
- Update: Beads issue `tas-full-task-creation-form-atf`

**Interfaces:**

- Consumes: the two implementation commits and all acceptance criteria in the approved design.
- Produces: fresh automated and architecture evidence, an archived completed plan, a retired
  handoff, and a closed delivery issue.

- [x] **Step 1: Run the complete repository verification**

Run:

```text
mix precommit
```

Expected: compilation with warnings treated as errors succeeds, formatting produces no unresolved
changes, unused dependency checks pass, and the complete test suite reports zero failures.

- [x] **Step 2: Verify architectural and terminology boundaries**

Run:

```text
rg -n "Taskman\\.Repo|Ecto\\.Query|Repo\\." lib/taskman_web
rg -n "tas-full-task-creation-form-atf|implementation plan|milestone|bead" \
  lib/taskman lib/taskman_web test
```

Expected: both commands produce no matches. Context modules own changesets and persistence; planning
language does not leak into implementation-facing files.

- [x] **Step 3: Inspect the implementation diff against the approved design**

Inspect the workspace diff and the two implementation commits with the repository-approved
version-control tooling. Confirm:

- every creation field is present and edit controls are not duplicated;
- only `Tasks.change_task(project, attrs)` supplies creation ownership;
- Create task derives disabled state from the latest form changeset;
- `save_task` still performs one explicit `Tasks.create_task/2` call;
- success still inserts the stream row and patches to the Project URL; and
- editing events, routes, and autosave state remain unchanged.

Expected: every design requirement maps to code or a focused test, with no unrelated implementation
changes.

- [x] **Step 4: Close durable delivery state**

After Steps 1–3 pass:

1. Mark every checkbox in this plan complete and add a completed status with the verification date.
2. Move this plan into `docs/archive/plans/`.
3. Update `docs/README.md` so the completed plan is linked under Archive rather than active plans.
4. Remove `docs/handoffs/full-task-creation-form.md` and restore
   `docs/handoffs/INDEX.md` to its no-current-handoffs state.
5. Add the focused-suite and `mix precommit` evidence to Beads issue
   `tas-full-task-creation-form-atf`, then close it with a concise outcome-based reason.

Expected: the specification remains the canonical behavior document, the archived plan retains
execution evidence, no stale handoff remains, and the delivery issue records the implementation
outcome.

- [x] **Step 5: Commit only completion documentation and tracker export**

Inspect the workspace diff and select only the archived plan, documentation index, handoff
retirement, and repository task-tracker export. Create a commit on the implementation branch with
message:

```text
record full Task creation verification
```

Expected: the final commit contains only completion records; unrelated workspace changes remain
uncommitted.

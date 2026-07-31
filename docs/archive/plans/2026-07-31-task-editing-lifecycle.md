# Task editing and human-controlled lifecycle implementation plan

**Status:** Completed and archived on 2026-07-31.

> Execute this plan task-by-task. Each task ends with focused verification and a selective
> commit before the next task begins.

**Goal:** Add one canonical URL-backed Task modal with Project-scoped, field-targeted autosave and
explicit human lifecycle selection while preserving the existing Project list.

**Architecture:** `Taskman.Tasks` owns scoped lookup, validation, and updates.
`TaskmanWeb.ProjectLive` owns the canonical modal, server-side draft, targeted saves, debounce
revisions, and Task stream refresh. Stateless Task components own labels, row navigation, and form
markup.

**Tech stack:** Elixir 1.17+, Phoenix 1.8, LiveView 1.2, Ecto/PostgreSQL, HEEx, Tailwind CSS, ExUnit,
Phoenix.LiveViewTest, LazyHTML.

**Required design:** Read
[`docs/specs/2026-07-31-task-editing-lifecycle-design.md`](../../specs/2026-07-31-task-editing-lifecycle-design.md)
completely before implementation.

## Global constraints

- Keep the canonical Task URL exactly `/projects/:project_id/tasks/:task_id`; do not add `/edit`.
- Keep Task creation title-only with Pending and None defaults.
- Keep Task editing inside the canonical modal; do not create a separate edit surface.
- Persist `description` as a non-null string and clear it to `""`; keep `due_at` nullable.
- Every lifecycle transition is initiated by a person. Do not add automatic transitions or hard
  transition gates.
- Keep hierarchy, relationships, checklists, deletion, Lists, Agent Sessions, rich-text controls,
  inline row editing, and relationship-dependent Done warnings out of scope.
- Keep Repo calls and Ecto queries out of `TaskmanWeb`.
- Use LiveView streams for Task rows and re-stream a Task after every successful update.
- Use existing `<.input>`, `<.modal>`, `<.icon>`, `to_form/2`, and `<Layouts.app>` foundations.
- Add no dependency, stateful LiveComponent, or custom JavaScript hook.
- Preserve all unrelated workspace changes.
- Do not incorporate the superseded remote design branch. It contains no implementation code, and
  its compatible decisions are already represented by the required design.
- At each commit checkpoint, inspect the workspace diff and commit only that task's listed files
  with the repository-approved version-control tooling.

---

## File map

- `priv/repo/migrations/*_make_task_descriptions_non_null.exs` — backfill descriptions and enforce
  the database default and non-null invariant. Generate the exact timestamped path with the required
  Mix task.
- `lib/taskman/tasks/task.ex` — schema defaults, editable-field changeset, and fixed enum lists.
- `lib/taskman/tasks.ex` — Project-scoped lookup and update APIs.
- `lib/taskman_web/router.ex` — canonical Task route.
- `lib/taskman_web/live/project_live.ex` — modal state, draft validation, targeted persistence,
  debounce scheduling, route-change flushing, and stream refresh.
- `lib/taskman_web/live/project_live.html.heex` — canonical modal and preserved-list states.
- `lib/taskman_web/components/task_form.ex` — creation and editing form variants.
- `lib/taskman_web/components/task_components.ex` — Task labels, badge presentation, and stretched
  row navigation.
- `config/config.exs` and `config/test.exs` — normal and deterministic-test autosave delays.
- `test/taskman/tasks_test.exs` — persistence and context behavior.
- `test/taskman_web/live/project_live_test.exs` — canonical route, autosave, row, and recovery
  behavior.
- `docs/planning/roadmap.md`, `docs/handoffs/task-editing-lifecycle.md`, and repository-local task
  state — acceptance evidence and delivery state after the implementation passes.

---

### Task 1: Enforce non-null Task descriptions

**Files:**

- Create: generated `priv/repo/migrations/*_make_task_descriptions_non_null.exs`
- Modify: `lib/taskman/tasks/task.ex`
- Modify: `test/taskman/tasks_test.exs`

**Interfaces:**

- Produces: every newly constructed and persisted `Task.description` defaults to `""`.
- Produces: PostgreSQL rejects `NULL` descriptions and defaults omitted descriptions to `""`.
- Consumes: existing `Tasks.create_task/2` and `Task.changeset/2`.

- [x] **Step 1: Add a failing context assertion for the description default**

Extend the existing `"create_task/2 assigns ownership and product defaults"` test:

```elixir
assert task.description == ""
```

Add a focused clearing test:

```elixir
test "create_task/2 persists an explicitly empty description as an empty string" do
  project = project_fixture(%{})

  assert {:ok, task} = Tasks.create_task(project, %{title: "Empty description", description: ""})
  assert task.description == ""
end
```

- [x] **Step 2: Run the focused tests and confirm the current nullable behavior**

Run:

```text
mix test test/taskman/tasks_test.exs
```

Expected: at least the default-description assertion fails because the current schema default is
`nil`.

- [x] **Step 3: Generate the migration with the repository-required command**

Run:

```text
mix ecto.gen.migration make_task_descriptions_non_null
```

Expected: Mix prints the exact generated timestamped path under `priv/repo/migrations/`. Use that
reported path for the next step; do not rename it or invent a timestamp.

- [x] **Step 4: Implement a reversible backfill and constraint migration**

Replace the generated module body with:

```elixir
def up do
  execute("UPDATE tasks SET description = '' WHERE description IS NULL")

  alter table(:tasks) do
    modify :description, :text, null: false, default: ""
  end
end

def down do
  alter table(:tasks) do
    modify :description, :text, null: true, default: nil
  end
end
```

Keep the generated migration module name unchanged.

- [x] **Step 5: Set the schema default**

Change the schema field in `Taskman.Tasks.Task`:

```elixir
field :description, :string, default: ""
```

Keep `description` in the existing cast list. Ecto will resolve a cleared string to the schema
default while resolving an empty `due_at` to `nil`.

- [x] **Step 6: Migrate and run focused tests**

Run:

```text
mix ecto.migrate
mix test test/taskman/tasks_test.exs
```

Expected: migration succeeds and all Task context tests pass.

- [x] **Step 7: Commit only the migration, schema, and context-test changes**

Inspect the workspace diff and create a selective commit named
`enforce non-null Task descriptions` containing only the three files in this task.

Expected: one new commit exists and all unrelated workspace changes remain uncommitted.

---

### Task 2: Add Project-scoped Task lookup and update APIs

**Files:**

- Modify: `lib/taskman/tasks.ex`
- Modify: `lib/taskman/tasks/task.ex`
- Modify: `test/taskman/tasks_test.exs`

**Interfaces:**

- Produces: `Tasks.get_task_for_project(project, task_id) :: Task.t() | nil`.
- Produces:
  `Tasks.update_task(project, task, attrs) :: {:ok, Task.t()} | {:error, Ecto.Changeset.t()} | {:error, :not_found}`.
- Produces: `Task.statuses/0` and `Task.priorities/0` for one fixed source of enum values.
- Guarantees: submitted `project_id` never moves a Task.

- [x] **Step 1: Add failing scoped-lookup tests**

Add:

```elixir
test "get_task_for_project/2 returns only a Task owned by the Project" do
  project = project_fixture(%{})
  other_project = project_fixture(%{})
  task = task_fixture(project, %{title: "Owned"})
  other_task = task_fixture(other_project, %{title: "Other"})

  assert Tasks.get_task_for_project(project, task.id) == task
  assert Tasks.get_task_for_project(project, Integer.to_string(task.id)) == task
  assert Tasks.get_task_for_project(project, other_task.id) == nil
  assert Tasks.get_task_for_project(project, "not-an-id") == nil
  assert Tasks.get_task_for_project(project, -1) == nil
  assert Tasks.get_task_for_project(project, 999_999_999) == nil
end
```

Import `Taskman.TasksFixtures` at the top of the test module.

- [x] **Step 2: Add failing update tests**

Add:

```elixir
test "update_task/3 persists every editable field" do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Before"})
  due_at = ~N[2026-08-03 16:00:00]

  attrs = %{
    title: "  After  ",
    description: "Updated",
    status: :in_review,
    priority: :urgent,
    due_at: due_at
  }

  assert {:ok, updated} = Tasks.update_task(project, task, attrs)
  assert updated.title == "After"
  assert updated.description == "Updated"
  assert updated.status == :in_review
  assert updated.priority == :urgent
  assert updated.due_at == due_at
end

test "update_task/3 keeps ownership immutable and rejects a mismatched Project" do
  project = project_fixture(%{})
  other_project = project_fixture(%{})
  task = task_fixture(project, %{title: "Owned"})

  assert {:ok, updated} =
           Tasks.update_task(project, task, %{title: "Still owned", project_id: other_project.id})

  assert updated.project_id == project.id
  assert {:error, :not_found} = Tasks.update_task(other_project, task, %{title: "Leaked"})
  assert Tasks.get_task_for_project(project, task.id).title == "Still owned"
end
```

Add one table-driven test that iterates over `Task.statuses/0`, persists each status with
`Tasks.update_task/3`, and asserts the result:

```elixir
test "update_task/3 accepts every fixed lifecycle status" do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Lifecycle"})

  Enum.reduce(Task.statuses(), task, fn status, current ->
    assert {:ok, updated} = Tasks.update_task(project, current, %{status: status})
    assert updated.status == status
    updated
  end)
end

test "update_task/3 accepts every fixed priority" do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Priority"})

  Enum.reduce(Task.priorities(), task, fn priority, current ->
    assert {:ok, updated} = Tasks.update_task(project, current, %{priority: priority})
    assert updated.priority == priority
    updated
  end)
end
```

Alias `Taskman.Tasks.Task` in the test module.

- [x] **Step 3: Run the context tests and verify missing APIs fail**

Run:

```text
mix test test/taskman/tasks_test.exs
```

Expected: compilation or test failure reports that the new context functions do not exist.

- [x] **Step 4: Expose the fixed enum lists**

In `Taskman.Tasks.Task`, add:

```elixir
def statuses, do: @statuses
def priorities, do: @priorities
```

Do not expose mutable or user-derived atoms.

- [x] **Step 5: Implement the scoped lookup**

Add to `Taskman.Tasks`:

```elixir
def get_task_for_project(%Project{id: project_id}, id) when is_integer(id) and id > 0 do
  Repo.get_by(Task, id: id, project_id: project_id)
end

def get_task_for_project(%Project{} = project, id) when is_binary(id) do
  case Integer.parse(id) do
    {parsed, ""} -> get_task_for_project(project, parsed)
    _invalid -> nil
  end
end

def get_task_for_project(%Project{}, _id), do: nil
```

- [x] **Step 6: Implement the ownership-checked update**

Add:

```elixir
def update_task(
      %Project{id: project_id},
      %Task{project_id: project_id} = task,
      attrs
    ) do
  task
  |> Task.changeset(attrs)
  |> Repo.update()
end

def update_task(%Project{}, %Task{}, _attrs), do: {:error, :not_found}
```

Keep `project_id` absent from `Task.changeset/2`'s cast list.

- [x] **Step 7: Run focused tests**

Run:

```text
mix test test/taskman/tasks_test.exs
```

Expected: all lookup, ownership, field-update, status, and priority tests pass.

- [x] **Step 8: Commit the context boundary**

Inspect the workspace diff and create a selective commit named
`add Project-scoped Task updates` containing only this task's context, schema, and test files.

---

### Task 3: Add the canonical Task modal and targeted autosave

**Files:**

- Create: `lib/taskman_web/components/task_components.ex`
- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `lib/taskman_web/components/task_form.ex`
- Modify: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: `Tasks.get_task_for_project/2`, `Tasks.update_task/3`, `Task.statuses/0`, and
  `Task.priorities/0`.
- Produces: route `/projects/:project_id/tasks/:task_id` with live action `:show_task`.
- Produces: `TaskComponents.status_options/0`, `priority_options/0`, `status_label/1`,
  `priority_label/1`, and `row/1`.
- Produces: `autosave_task` LiveView event that persists only `_target`.
- At this checkpoint, all fields save immediately. Task 4 changes only title and description to
  scheduled persistence.

- [x] **Step 1: Add failing route and modal tests**

Add tests with stable selectors:

```elixir
test "opens the canonical Task modal from the row and preserves the list", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Editable", description: "Context"})
  sibling = task_fixture(project, %{title: "Sibling"})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

  view |> element("#open-task-#{task.id}") |> render_click()

  assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
  assert has_element?(view, "#task-modal")
  assert has_element?(view, "#task-form")
  assert has_element?(view, "#task-title[value='Editable']")
  assert has_element?(view, "#task-description")
  assert has_element?(view, "#task-#{sibling.id}")
end

test "direct canonical Task URL renders the same modal", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Direct"})

  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

  assert has_element?(view, "#task-modal")
  assert has_element?(view, "#task-form")
  assert has_element?(view, "#task-#{task.id}")
end
```

- [x] **Step 2: Add failing not-found isolation tests**

Cover malformed, missing, and cross-Project IDs:

```elixir
test "invalid Task URLs preserve the selected Project list in a modal not-found state", %{
  conn: conn
} do
  project = project_fixture(%{})
  visible = task_fixture(project, %{title: "Visible"})
  other_project = project_fixture(%{})
  other = task_fixture(other_project, %{title: "Secret"})

  for task_id <- ["not-an-id", "999999999", Integer.to_string(other.id)] do
    {:ok, view, _html} = live(conn, "/projects/#{project.id}/tasks/#{task_id}")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-not-found")
    assert has_element?(view, "#task-#{visible.id}")
    refute has_element?(view, "#task-form")
    refute has_element?(view, "#task-#{other.id}")
  end
end
```

- [x] **Step 3: Add failing targeted-autosave tests**

Add tests for immediate targeted updates:

```elixir
test "autosaves the targeted Task field and refreshes the streamed row", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Before", status: :pending, priority: :none})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

  view
  |> form("#task-form", task: %{title: "After"})
  |> render_change(%{"_target" => ["task", "title"]})

  assert Tasks.get_task_for_project(project, task.id).title == "After"
  assert has_element?(view, "#task-#{task.id}", "After")

  view
  |> form("#task-form", task: %{status: "in_review"})
  |> render_change(%{"_target" => ["task", "status"]})

  assert Tasks.get_task_for_project(project, task.id).status == :in_review
  assert has_element?(view, "#task-status-#{task.id}", "In Review")
end
```

Alias `Taskman.Tasks` in the LiveView test module.

Add a second targeted-save test that submits an empty title, asserts
`#task-form [data-role='field-error']` and unchanged title persistence, then changes status to
`in_review`. Assert the status persisted while the title did not, and
`#task-save-status[data-state='not_saved']` remains.

```elixir
test "an invalid draft does not block another field from saving", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Valid", status: :pending})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

  view
  |> form("#task-form", task: %{title: ""})
  |> render_change(%{"_target" => ["task", "title"]})

  assert has_element?(view, "#task-form [data-role='field-error']")
  assert Tasks.get_task_for_project(project, task.id).title == "Valid"

  view
  |> form("#task-form", task: %{status: "in_review"})
  |> render_change(%{"_target" => ["task", "status"]})

  updated = Tasks.get_task_for_project(project, task.id)
  assert updated.title == "Valid"
  assert updated.status == :in_review
  assert has_element?(view, "#task-save-status[data-state='not_saved']")
end
```

- [x] **Step 4: Add the canonical route**

In the browser scope, add:

```elixir
live "/projects/:project_id/tasks/:task_id", ProjectLive, :show_task
```

Keep it alongside the existing Project and new-Task routes.

- [x] **Step 5: Create the Task presentation component**

Create `TaskmanWeb.TaskComponents` with:

```elixir
defmodule TaskmanWeb.TaskComponents do
  use TaskmanWeb, :html

  alias Taskman.Tasks.Task

  @status_labels %{
    icebox: "Icebox",
    pending: "Pending",
    in_progress: "In Progress",
    in_review: "In Review",
    done: "Done",
    will_not_do: "Will Not Do"
  }

  @priority_labels %{
    none: "None",
    low: "Low",
    medium: "Medium",
    high: "High",
    urgent: "Urgent"
  }

  def status_options, do: Enum.map(Task.statuses(), &{status_label(&1), &1})
  def priority_options, do: Enum.map(Task.priorities(), &{priority_label(&1), &1})
  def status_label(status), do: Map.fetch!(@status_labels, status)
  def priority_label(priority), do: Map.fetch!(@priority_labels, priority)

  attr :task, Task, required: true
  attr :project_id, :integer, required: true

  def row(assigns) do
    ~H"""
    <article
      id={"task-row-#{@task.id}"}
      class="relative grid gap-3 border-b border-slate-800 px-3 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_5.5rem_5.5rem] sm:items-center"
    >
      <.link
        id={"open-task-#{@task.id}"}
        patch={~p"/projects/#{@project_id}/tasks/#{@task.id}"}
        class="absolute inset-0 z-0 rounded-xl outline-none transition hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
      >
        <span class="sr-only">Open {@task.title}</span>
      </.link>
      <p
        id={"task-#{@task.id}"}
        class="pointer-events-none relative z-10 truncate text-sm font-medium text-slate-100"
      >
        {@task.title}
      </p>
      <span
        id={"task-status-#{@task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-amber-400/10 px-2.5 py-1 text-xs font-semibold text-amber-300 sm:justify-self-center"
      >
        {status_label(@task.status)}
      </span>
      <span
        id={"task-priority-#{@task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-slate-800 px-2.5 py-1 text-xs font-semibold text-slate-300 sm:justify-self-center"
      >
        {priority_label(@task.priority)}
      </span>
    </article>
    """
  end
end
```

Keep color refinements local to this component. Do not test color classes.

- [x] **Step 6: Extend the Task form component for creation and editing**

Add attrs:

```elixir
attr :mode, :atom, values: [:new, :edit], required: true
attr :change, :string, required: true
attr :submit, :string, default: nil
attr :cancel, :string, required: true
```

Alias `TaskmanWeb.TaskComponents` in the component module. Render the form with
`phx-change={@change}` and `phx-submit={@submit}`. Always render the title.
When `@mode == :edit`, additionally render:

```heex
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
```

Keep the existing Create and Cancel actions only for `:new`. For `:edit`, render an explicit
`#close-task` patch link and no submit button.

- [x] **Step 7: Add canonical modal state to the LiveView**

In `mount/3`, initialize:

```elixir
|> assign(:selected_task, nil)
|> assign(:task_not_found?, false)
|> assign(:task_draft, %{})
|> assign(:task_dirty_fields, MapSet.new())
|> assign(:task_revisions, %{})
|> assign(:task_save_failed?, false)
|> assign(:task_saved?, false)
|> assign(:task_save_state, :idle)
```

Add a `:show_task` `handle_params/3` clause that:

1. loads the Project;
2. streams its direct Tasks;
3. calls `Tasks.get_task_for_project/2`;
4. assigns `selected_task` and `to_form(Tasks.change_task(task))` when found; and
5. assigns `task_not_found?: true` and no form when not found.

Add a private `clear_task_modal_state/1` and call it from root, Project, and new-Task route handling
so stale Task state never survives navigation.

- [x] **Step 8: Implement immediate field-targeted autosave**

Define:

```elixir
@editable_task_fields ~w(title description status priority due_at)
```

Add:

```elixir
def handle_event(
      "autosave_task",
      %{"_target" => ["task", field], "task" => task_params},
      socket
    )
    when field in @editable_task_fields do
  socket =
    socket
    |> assign(:task_draft, task_params)
    |> assign_task_form(task_params)
    |> mark_task_field_dirty(field)
    |> persist_task_field(field)

  {:noreply, socket}
end
```

Implement helpers so `persist_task_field/2`:

1. validates `%{field => Map.get(task_draft, field)}` through `Tasks.change_task/2`;
2. leaves an invalid field dirty and displays the full-draft errors;
3. calls `Tasks.update_task/3` with only the targeted string-keyed field when valid;
4. on success assigns the updated Task, removes that field from the dirty set, re-streams the Task,
   and rebuilds the full form against the updated Task while retaining the other draft params;
5. on `{:error, changeset}` retains the draft and marks persistence failure; and
6. on `{:error, :not_found}` replaces the form with the modal not-found state.

Use these helper shapes:

```elixir
defp assign_task_form(socket, params) do
  form =
    socket.assigns.selected_task
    |> Tasks.change_task(params)
    |> Map.put(:action, :validate)
    |> to_form()

  assign(socket, :task_form, form)
end

defp mark_task_field_dirty(socket, field) do
  assign(
    socket,
    :task_dirty_fields,
    MapSet.put(socket.assigns.task_dirty_fields, field)
  )
end

defp persist_task_field(socket, field) do
  value = Map.get(socket.assigns.task_draft, field)
  field_changeset = Tasks.change_task(socket.assigns.selected_task, %{field => value})

  if field_changeset.valid? do
    save_task_field(socket, field, value)
  else
    refresh_task_save_state(socket)
  end
end

defp save_task_field(socket, field, value) do
  case Tasks.update_task(
         socket.assigns.selected_project,
         socket.assigns.selected_task,
         %{field => value}
       ) do
    {:ok, task} ->
      socket
      |> assign(:selected_task, task)
      |> assign(:task_dirty_fields, MapSet.delete(socket.assigns.task_dirty_fields, field))
      |> assign(:task_save_failed?, false)
      |> assign(:task_saved?, true)
      |> stream_insert(:tasks, task)
      |> assign_task_form(socket.assigns.task_draft)
      |> refresh_task_save_state()

    {:error, %Ecto.Changeset{}} ->
      socket
      |> assign(:task_save_failed?, true)
      |> refresh_task_save_state()

    {:error, :not_found} ->
      socket
      |> assign(:selected_task, nil)
      |> assign(:task_form, nil)
      |> assign(:task_not_found?, true)
  end
end
```

Set `task_save_failed?` to `false` when processing a new browser change so a prior persistence
failure can recover. `refresh_task_save_state/1` assigns the derived state rather than computing it
inside the template.

After every transition, derive `task_save_state` with this precedence:

```elixir
cond do
  socket.assigns.task_save_failed? -> :failed
  !socket.assigns.task_form.source.valid? -> :not_saved
  MapSet.size(socket.assigns.task_dirty_fields) > 0 -> :saving
  socket.assigns.task_saved? -> :saved
  true -> :idle
end
```

Add exact presentation text:

```elixir
defp task_save_message(:idle), do: "Autosaves changes"
defp task_save_message(:saving), do: "Saving…"
defp task_save_message(:saved), do: "Saved"
defp task_save_message(:not_saved), do: "Not saved"
defp task_save_message(:failed), do: "Couldn’t save changes"
```

- [x] **Step 9: Render rows and the canonical modal**

Replace each inline Task `<article>` with:

```heex
<TaskComponents.row task={task} project_id={@selected_project.id} />
```

Render the found Task modal:

```heex
<.modal
  :if={@live_action == :show_task && @selected_project && @selected_task}
  id="task-modal"
  show
  on_cancel={JS.patch(~p"/projects/#{@selected_project.id}")}
>
  <h2 id="task-modal-title">Task</h2>
  <TaskForm.form
    form={@task_form}
    mode={:edit}
    change="autosave_task"
    cancel={~p"/projects/#{@selected_project.id}"}
  />
  <p id="task-save-status" aria-live="polite" data-state={@task_save_state}>
    {task_save_message(@task_save_state)}
  </p>
</.modal>
```

Render a second `#task-modal` branch for `task_not_found?` containing `#task-not-found` and a Close
patch link. Keep the selected Project and `#tasks` stream visible behind both branches.

Update the new-Task component invocation with `mode={:new}`, `change="validate_task"`, and
`submit="save_task"`.

- [x] **Step 10: Run focused LiveView tests**

Run:

```text
mix test test/taskman_web/live/project_live_test.exs
```

Expected: canonical navigation, direct loading, not-found isolation, immediate targeted save, new
Task behavior, and existing Project behavior all pass.

- [x] **Step 11: Commit the canonical modal**

Inspect the workspace diff and create a selective commit named
`add canonical Task autosave modal` containing only this task's component, router, LiveView,
template, and test files.

---

### Task 4: Add server-managed debounce and safe route flushing

**Files:**

- Modify: `config/config.exs`
- Modify: `config/test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `test/taskman_web/live/project_live_test.exs`
- Create: `test/taskman_web/live/project_live_autosave_test.exs`

**Interfaces:**

- Consumes: the Task 3 draft, dirty-field, target-persistence, and modal-state helpers.
- Produces: 300 ms normal delay and 0 ms test delay under
  `config :taskman, :task_autosave_delay_ms`.
- Produces: `{:autosave_task_field, task_id, field, revision}` internal messages.
- Guarantees: stale scheduled messages cannot overwrite newer edits.
- Guarantees: route changes flush valid dirty fields and discard invalid drafts.

- [x] **Step 1: Add a failing debounce, stale-message, and flush test**

Create `TaskmanWeb.ProjectLiveAutosaveTest` in
`test/taskman_web/live/project_live_autosave_test.exs` with
`use TaskmanWeb.ConnCase, async: false`. Import `Phoenix.LiveViewTest`, Project fixtures, and Task
fixtures; alias `Taskman.Tasks`.

Add a setup callback that isolates the application delay:

```elixir
setup do
  previous = Application.get_env(:taskman, :task_autosave_delay_ms)
  Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

  on_exit(fn ->
    case previous do
      nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
      value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
    end
  end)

  :ok
end
```

Add:

```elixir
test "debounces text, ignores stale messages, and flushes the final value on close", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Before"})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

  view
  |> form("#task-form", task: %{title: "First"})
  |> render_change(%{"_target" => ["task", "title"]})

  view
  |> form("#task-form", task: %{title: "Final"})
  |> render_change(%{"_target" => ["task", "title"]})

  assert Tasks.get_task_for_project(project, task.id).title == "Before"

  send(view.pid, {:autosave_task_field, task.id, "title", 1})
  _ = :sys.get_state(view.pid)

  assert Tasks.get_task_for_project(project, task.id).title == "Before"

  view |> element("#close-task") |> render_click()

  assert_patch(view, ~p"/projects/#{project.id}")
  assert Tasks.get_task_for_project(project, task.id).title == "Final"
end
```

- [x] **Step 2: Run the new test and verify immediate persistence fails it**

Run:

```text
mix test test/taskman_web/live/project_live_autosave_test.exs
```

Expected: failure because Task 3 persists text immediately instead of retaining `"Before"` until a
timer or route flush.

- [x] **Step 3: Configure normal and test delays**

Add to `config/config.exs`:

```elixir
config :taskman, :task_autosave_delay_ms, 300
```

Add to `config/test.exs`:

```elixir
config :taskman, :task_autosave_delay_ms, 0
```

- [x] **Step 4: Schedule text saves by revision**

Add:

```elixir
@debounced_task_fields ~w(title description)
```

For title and description targets:

1. increment `task_revisions[field]`;
2. retain the field in `task_dirty_fields`;
3. validate the target;
4. when valid, call:

```elixir
Process.send_after(
  self(),
  {:autosave_task_field, socket.assigns.selected_task.id, field, revision},
  Application.get_env(:taskman, :task_autosave_delay_ms, 300)
)
```

5. do not schedule invalid values; incrementing the revision still invalidates any older message.

Keep status, priority, and due date-time on the immediate `persist_task_field/2` path.

- [x] **Step 5: Handle current and stale save messages**

Add:

```elixir
def handle_info(
      {:autosave_task_field, task_id, field, revision},
      %{assigns: %{selected_task: %{id: task_id}, task_revisions: revisions}} = socket
    ) do
  if Map.get(revisions, field) == revision do
    {:noreply, persist_task_field(socket, field)}
  else
    {:noreply, socket}
  end
end

def handle_info({:autosave_task_field, _task_id, _field, _revision}, socket) do
  {:noreply, socket}
end
```

- [x] **Step 6: Flush valid dirty fields before route state changes**

At the beginning of every `handle_params/3` path, call:

```elixir
socket = flush_dirty_task_fields(socket)
```

Implement it as an `Enum.reduce/3` over `task_dirty_fields`, calling the existing
`persist_task_field/2` for each field. The helper must be a no-op when no Task modal is active.
After flushing, the route-specific state assignment may clear the modal and discard any fields that
remain invalid.

Refactor the duplicate route clauses into one `handle_params/3` dispatcher so this flush cannot be
accidentally skipped by a future live action.

- [x] **Step 7: Run autosave and full LiveView tests**

Run:

```text
mix test test/taskman_web/live/project_live_autosave_test.exs
mix test test/taskman_web/live/project_live_test.exs
```

Expected: no sleeps, no stale overwrite, safe close flush, invalid-field independence, and all
existing LiveView tests pass.

- [x] **Step 8: Commit debounce safety**

Inspect the workspace diff and create a selective commit named
`make Task autosave navigation-safe` containing only this task's config, LiveView, and test files.

---

### Task 5: Complete acceptance and delivery state

**Files:**

- Modify: `docs/planning/roadmap.md`
- Modify: `docs/README.md`
- Modify: repository-local task state
- Delete: `docs/handoffs/task-editing-lifecycle.md`
- Modify: `docs/handoffs/INDEX.md`

**Interfaces:**

- Consumes: all prior tasks and the approved design acceptance criteria.
- Produces: verified Task editing and lifecycle behavior.
- Produces: current roadmap and repository-local delivery state.
- Produces: no live handoff after the workstream is genuinely complete.

- [x] **Step 1: Run focused context and LiveView tests**

Run:

```text
mix test test/taskman/tasks_test.exs
mix test test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/live/project_live_autosave_test.exs
```

Expected: all focused tests pass.

- [x] **Step 2: Run the complete repository gate**

Run:

```text
mix precommit
```

Expected: compilation with warnings treated as errors, dependency unlock check, formatting, and the
full test suite all pass.

- [x] **Step 3: Inspect implementation files for leaked planning terminology**

Run:

```text
rg -n "nr1|bead|phase|milestone|implementation plan" lib test
```

Expected: no product code, test name, DOM copy, command output, or API contains internal planning
terminology.

- [x] **Step 4: Perform responsive browser acceptance**

Start PostgreSQL through the repository's documented local workflow, run `mix phx.server`, and use
the embedded browser for the active workspace.

Verify at desktop and narrow widths:

1. the Task row's non-control area opens the canonical modal;
2. direct canonical URLs recover on reload;
3. title and description show Saving then Saved;
4. all six statuses and five priorities persist;
5. due date-time sets and clears;
6. invalid title stays visible and does not block a valid status save;
7. Close, backdrop, Escape, browser Back, and switching Tasks flush valid drafts;
8. row title, status, and priority reflect persisted values;
9. malformed, missing, and cross-Project Task URLs preserve the Project list without leaking data;
   and
10. existing Task creation and unknown-Project recovery still work.

Record only concise acceptance evidence in the canonical delivery documentation.

- [x] **Step 5: Update authoritative delivery state**

After all verification succeeds:

1. Confirm design issue `tas-task-editing-lifecycle-nr1.1` is complete, then mark
   `tas-task-editing-lifecycle-nr1.2`, `.3`, `.4`, and `.5` complete in dependency order in the
   repository-local task tracker.
2. Mark epic `tas-task-editing-lifecycle-nr1` complete only after every child is complete.
3. Update `docs/planning/roadmap.md` to state that Projects and basic Tasks are complete and name the
   verified canonical Task modal outcome.
4. Remove the completed implementation handoff and its `task-editing-lifecycle` index entry in the
   same change.
5. Remove the obsolete current-handoff link from `docs/README.md`; remove the Handoffs section when
   it has no remaining entries.
6. Do not include routine command logs or copy the specification into the roadmap.

- [x] **Step 6: Commit acceptance tests and delivery documentation selectively**

Inspect the workspace diff and exclude unrelated pre-existing changes unless they have become the
canonical file versions intentionally integrated during this task. Create a selective commit named
`complete Task editing lifecycle increment` containing only accepted implementation, test, and
delivery-documentation files.

Expected: the branch contains the implementation and durable evidence; no push, merge, publication,
or remote-branch deletion occurs without separate operator authorization.

---

## Plan self-review checklist

- Every acceptance criterion in the approved design maps to Tasks 1–5.
- The plan establishes the canonical route directly and never introduces `/edit`.
- The plan keeps creation behavior unchanged.
- The description migration and schema share the `""` invariant.
- Lookup and update semantics match the approved return values.
- Targeted autosave allows valid fields to save around invalid drafts.
- Debounce messages carry Task ID, field, and revision and are safe after navigation.
- Route changes flush valid server-side drafts without a JavaScript hook.
- Row navigation stays separate from future inline controls.
- Tests use selectors, streams, and mailbox synchronization rather than sleeps or styling
  assertions.
- Final delivery updates occur only after verification.

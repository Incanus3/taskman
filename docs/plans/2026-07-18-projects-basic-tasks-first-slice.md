# Projects and Basic Tasks First Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create and select a Project, open a URL-backed modal to create a default Task,
and see that Task in the selected Project's direct list.

**Architecture:** `Taskman.Projects` and `Taskman.Tasks` are separate application contexts. Each
exposes a domain-focused public API and uses Ecto internally for persistence. One
`TaskmanWeb.ProjectLive` orchestrates those public APIs, represents Project selection and Task-modal
state in routes, and renders Project and Task collections with LiveView streams.

**Tech Stack:** Elixir 1.17+, Phoenix 1.8, LiveView 1.2, Ecto SQL, PostgreSQL, HEEx, Tailwind CSS v4,
ExUnit, Phoenix.LiveViewTest, and LazyHTML.

## Global Constraints

- Follow the project-wide architecture and working rules in `docs/development.md`, including its
  application-boundary rules.
- Project ownership is assigned from a `%Project{}` argument, never cast from Task form params.
- Begin every LiveView template with `<Layouts.app flash={@flash} current_scope={nil}>`.
- Use LiveView streams for Projects and Tasks, with stable parent and child DOM IDs.
- Drive forms with `to_form/2`, `<.form for={@form}>`, and `<.input field={@form[:field]}>`.
- Use `<.icon>` for every icon.
- Do not add editing, deletion, lifecycle controls, Lists, Task detail, relationships, sorting,
  filtering, search, or pagination.
- Every task follows red-green-refactor and ends with focused tests plus its listed commit step.

---

### Task 1: Project Persistence Boundary

**Files:**

- Generate: `priv/repo/migrations/*_create_projects.exs` using the migration command below.
- Create: `lib/taskman/projects/project.ex`
- Create: `lib/taskman/projects.ex`
- Create: `test/support/fixtures/projects_fixtures.ex`
- Create: `test/taskman/projects_test.exs`

**Interfaces:**

- Produces: `Projects.list_projects/0 :: [Project.t()]`
- Produces: `Projects.get_project/1 :: Project.t() | nil`
- Produces: `Projects.create_project/1 :: {:ok, Project.t()} | {:error, Ecto.Changeset.t()}`
- Produces: `Projects.change_project/2 :: Ecto.Changeset.t()`
- Produces: `ProjectsFixtures.project_fixture/1 :: Project.t()` for later tests.

- [ ] **Step 1: Write failing context tests**

Create `test/taskman/projects_test.exs` with these focused tests:

```elixir
defmodule Taskman.ProjectsTest do
  use Taskman.DataCase, async: true

  alias Taskman.Projects

  @tag :tmp_dir
  test "create_project/1 normalizes and persists a valid directory", %{tmp_dir: tmp_dir} do
    relative_path = Path.relative_to(tmp_dir, File.cwd!())

    assert {:ok, project} =
             Projects.create_project(%{name: "  Taskman  ", primary_directory: relative_path})

    assert project.name == "Taskman"
    assert project.primary_directory == Path.expand(relative_path)
  end

  test "create_project/1 rejects missing fields and a non-directory path" do
    assert {:error, changeset} = Projects.create_project(%{})
    assert %{name: [_], primary_directory: [_]} = errors_on(changeset)

    assert {:error, changeset} =
             Projects.create_project(%{name: "Taskman", primary_directory: "/not/a/taskman/dir"})

    assert %{primary_directory: ["must be an existing directory"]} = errors_on(changeset)
  end

  @tag :tmp_dir
  test "list_projects/0 is stable and get_project/1 handles invalid IDs", %{tmp_dir: tmp_dir} do
    assert {:ok, first} =
             Projects.create_project(%{name: "First", primary_directory: tmp_dir})

    assert {:ok, second} =
             Projects.create_project(%{name: "Second", primary_directory: tmp_dir})

    assert Projects.list_projects() == [first, second]
    assert Projects.get_project(Integer.to_string(first.id)) == first
    assert Projects.get_project("not-an-id") == nil
    assert Projects.get_project(-1) == nil
  end
end
```

- [ ] **Step 2: Run the test and confirm the expected failure**

Run: `mix test test/taskman/projects_test.exs`

Expected: compilation fails because `Taskman.Projects` does not exist.

- [ ] **Step 3: Generate and implement the Project migration**

Run: `mix ecto.gen.migration create_projects`

Expected: Mix prints the exact generated `priv/repo/migrations/*_create_projects.exs` path.

Replace that generated module's `change/0` body with:

```elixir
create table(:projects) do
  add :name, :string, null: false
  add :primary_directory, :string, null: false

  timestamps(type: :utc_datetime)
end
```

Run: `mix ecto.migrate`

Expected: the `CreateProjects` migration runs successfully.

- [ ] **Step 4: Implement the Project schema and context**

Create `Taskman.Projects.Project` as:

```elixir
defmodule Taskman.Projects.Project do
  use Ecto.Schema
  import Ecto.Changeset

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          name: String.t() | nil,
          primary_directory: String.t() | nil
        }

  schema "projects" do
    field :name, :string
    field :primary_directory, :string

    timestamps(type: :utc_datetime)
  end

  def changeset(project, attrs) do
    project
    |> cast(attrs, [:name, :primary_directory])
    |> update_change(:name, &String.trim/1)
    |> validate_required([:name, :primary_directory])
  end
end
```

Implement `Taskman.Projects` with the following public functions:

```elixir
defmodule Taskman.Projects do
  import Ecto.Query

  alias Taskman.Projects.Project
  alias Taskman.Repo

def list_projects do
  Project
  |> order_by([project], asc: project.inserted_at, asc: project.id)
  |> Repo.all()
end

def get_project(id) when is_integer(id) and id > 0, do: Repo.get(Project, id)

def get_project(id) when is_binary(id) do
  case Integer.parse(id) do
    {parsed, ""} -> get_project(parsed)
    _invalid -> nil
  end
end

def get_project(_id), do: nil

def create_project(attrs \\ %{}) do
  %Project{}
  |> change_project(attrs)
  |> Repo.insert()
end

def change_project(%Project{} = project, attrs \\ %{}) do
  attrs = normalize_primary_directory(attrs)

  project
  |> Project.changeset(attrs)
  |> validate_primary_directory()
end

defp normalize_primary_directory(attrs) do
  key =
    cond do
      Map.has_key?(attrs, :primary_directory) -> :primary_directory
      Map.has_key?(attrs, "primary_directory") -> "primary_directory"
      true -> nil
    end

  case key && Map.fetch(attrs, key) do
    {:ok, path} when is_binary(path) ->
      normalized =
        case String.trim(path) do
          "" -> ""
          trimmed -> Path.expand(trimmed)
        end

      Map.put(attrs, key, normalized)

    _missing_or_invalid ->
      attrs
  end
end

defp validate_primary_directory(changeset) do
  case Ecto.Changeset.get_field(changeset, :primary_directory) do
    path when is_binary(path) and path != "" ->
      if File.dir?(path) do
        changeset
      else
        Ecto.Changeset.add_error(changeset, :primary_directory, "must be an existing directory")
      end

    _blank ->
      changeset
  end
end
end
```

`normalize_primary_directory/1` must support atom or string keys, trim a binary path, and replace it
with `Path.expand/1`. `validate_primary_directory/1` must read the field with
`Ecto.Changeset.get_field/2`, skip blank values already handled by `validate_required/3`, and add
`"must be an existing directory"` when `File.dir?/1` is false.

- [ ] **Step 5: Add the Project fixture and rerun tests**

Create `Taskman.ProjectsFixtures.project_fixture/1`. Give every fixture a unique default name and
use a caller-provided existing directory:

```elixir
defmodule Taskman.ProjectsFixtures do
def project_fixture(attrs) do
  unique = System.unique_integer([:positive])

  attrs =
    Map.merge(
      %{name: "Project #{unique}", primary_directory: File.cwd!()},
      Map.new(attrs)
    )

  {:ok, project} = Taskman.Projects.create_project(attrs)
  project
end
end
```

Run: `mix test test/taskman/projects_test.exs`

Expected: all Project context tests pass with zero failures.

- [ ] **Step 6: Commit the Project boundary**

Run: `but status --format json`, confirm only Task 1 files are uncommitted, then run:

```bash
but commit mvp --message "add Project persistence boundary"
```

Expected: one new commit on `mvp` and a clean workspace.

---

### Task 2: Task Persistence Boundary

**Files:**

- Generate: `priv/repo/migrations/*_create_tasks.exs` using the migration command below.
- Create: `lib/taskman/tasks/task.ex`
- Create: `lib/taskman/tasks.ex`
- Create: `test/support/fixtures/tasks_fixtures.ex`
- Create: `test/taskman/tasks_test.exs`

**Interfaces:**

- Consumes: `Taskman.Projects.Project.t()` from Task 1.
- Produces: `Tasks.list_tasks_for_project/1 :: [Task.t()]`
- Produces: `Tasks.create_task/2 :: {:ok, Task.t()} | {:error, Ecto.Changeset.t()}`
- Produces: `Tasks.change_task/2 :: Ecto.Changeset.t()`
- Produces: `TasksFixtures.task_fixture/2 :: Task.t()` for LiveView tests.

- [ ] **Step 1: Write failing Task context tests**

Create tests that prove ownership, defaults, and Project isolation:

```elixir
defmodule Taskman.TasksTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures

  alias Taskman.Tasks

  test "create_task/2 assigns ownership and product defaults" do
    project = project_fixture(%{})

    assert {:ok, task} = Tasks.create_task(project, %{title: "First task"})
    assert task.project_id == project.id
    assert task.title == "First task"
    assert task.status == :pending
    assert task.priority == :none
  end

  test "create_task/2 requires a title and ignores user-owned project IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})

    assert {:error, changeset} = Tasks.create_task(project, %{title: ""})
    assert %{title: [_]} = errors_on(changeset)

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Owned safely", project_id: other_project.id})

    assert task.project_id == project.id
  end

  test "list_tasks_for_project/1 returns only that Project's tasks in stable order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    {:ok, first} = Tasks.create_task(project, %{title: "First"})
    {:ok, second} = Tasks.create_task(project, %{title: "Second"})
    {:ok, _other} = Tasks.create_task(other_project, %{title: "Other"})

    assert Tasks.list_tasks_for_project(project) == [first, second]
  end
end
```

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run: `mix test test/taskman/tasks_test.exs`

Expected: compilation fails because `Taskman.Tasks` does not exist.

- [ ] **Step 3: Generate and implement the Task migration**

Run: `mix ecto.gen.migration create_tasks`

Expected: Mix prints the exact generated `priv/repo/migrations/*_create_tasks.exs` path.

Implement this migration shape:

```elixir
create table(:tasks) do
  add :project_id, references(:projects, on_delete: :delete_all), null: false
  add :title, :string, null: false
  add :description, :text
  add :status, :string, null: false, default: "pending"
  add :priority, :string, null: false, default: "none"
  add :due_at, :naive_datetime

  timestamps(type: :utc_datetime)
end

create index(:tasks, [:project_id])

create constraint(:tasks, :tasks_status_check,
         check: "status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'will_not_do')"
       )

create constraint(:tasks, :tasks_priority_check,
         check: "priority IN ('none', 'low', 'medium', 'high', 'urgent')"
       )
```

Run: `mix ecto.migrate`

Expected: the `CreateTasks` migration runs successfully.

- [ ] **Step 4: Implement the Task schema and context**

Define `Taskman.Tasks.Task` as:

```elixir
defmodule Taskman.Tasks.Task do
  use Ecto.Schema
  import Ecto.Changeset

  @statuses [:icebox, :pending, :in_progress, :in_review, :done, :will_not_do]
  @priorities [:none, :low, :medium, :high, :urgent]

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          project_id: pos_integer() | nil,
          title: String.t() | nil,
          description: String.t() | nil,
          status: atom(),
          priority: atom(),
          due_at: NaiveDateTime.t() | nil
        }

  schema "tasks" do
    field :title, :string
    field :description, :string
    field :status, Ecto.Enum, values: @statuses, default: :pending
    field :priority, Ecto.Enum, values: @priorities, default: :none
    field :due_at, :naive_datetime

    belongs_to :project, Taskman.Projects.Project

    timestamps(type: :utc_datetime)
  end

  def changeset(task, attrs) do
    task
    |> cast(attrs, [:title, :description, :status, :priority, :due_at])
    |> update_change(:title, &String.trim/1)
    |> validate_required([:project_id, :title, :status, :priority])
    |> foreign_key_constraint(:project_id)
    |> check_constraint(:status, name: :tasks_status_check)
    |> check_constraint(:priority, name: :tasks_priority_check)
  end
end
```

Use the exact public context API below:

```elixir
defmodule Taskman.Tasks do
  import Ecto.Query

  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task

def list_tasks_for_project(%Project{id: project_id}) do
  Task
  |> where([task], task.project_id == ^project_id)
  |> order_by([task], asc: task.inserted_at, asc: task.id)
  |> Repo.all()
end

def create_task(%Project{id: project_id}, attrs \\ %{}) do
  %Task{project_id: project_id}
  |> Task.changeset(attrs)
  |> Repo.insert()
end

def change_task(%Task{} = task, attrs \\ %{}) do
  Task.changeset(task, attrs)
end
end
```

- [ ] **Step 5: Add Task fixtures and rerun focused tests**

Create `Taskman.TasksFixtures.task_fixture/2` as:

```elixir
defmodule Taskman.TasksFixtures do
  alias Taskman.Tasks

  def task_fixture(project, attrs \\ %{}) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))

    {:ok, task} = Tasks.create_task(project, attrs)
    task
  end
end
```

Run: `mix test test/taskman/projects_test.exs test/taskman/tasks_test.exs`

Expected: all Project and Task context tests pass with zero failures.

- [ ] **Step 6: Commit the Task boundary**

Run: `but status --format json`, confirm only Task 2 files are uncommitted, then run:

```bash
but commit mvp --message "add Task persistence boundary"
```

Expected: one new commit on `mvp` and a clean workspace.

---

### Task 3: List-First Project LiveView

**Files:**

- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/components/layouts.ex`
- Modify: `lib/taskman_web/components/layouts/root.html.heex`
- Create: `lib/taskman_web/live/project_live.ex`
- Create: `lib/taskman_web/live/project_live.html.heex`
- Delete: `lib/taskman_web/controllers/page_controller.ex`
- Delete: `lib/taskman_web/controllers/page_html.ex`
- Delete: `lib/taskman_web/controllers/page_html/home.html.heex`
- Delete: `test/taskman_web/controllers/page_controller_test.exs`
- Create: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: all `Projects` APIs from Task 1.
- Consumes: `Tasks.list_tasks_for_project/1` from Task 2.
- Produces routes: `/` and `/projects/:project_id` handled by `TaskmanWeb.ProjectLive`.
- Produces stable IDs: `#project-sidebar`, `#project-form`, `#projects`, `#main-panel`,
  `#project-not-found`, `#tasks`, and `#add-task`.

- [ ] **Step 1: Write failing LiveView tests for the shell and Project flow**

Create `test/taskman_web/live/project_live_test.exs`, import `Phoenix.LiveViewTest`,
`Taskman.ProjectsFixtures`, and `Taskman.TasksFixtures`, then add these outcomes:

```elixir
defmodule TaskmanWeb.ProjectLiveTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

test "root renders the list-first empty state", %{conn: conn} do
  {:ok, view, _html} = live(conn, ~p"/")

  assert has_element?(view, "#project-sidebar")
  assert has_element?(view, "#project-form")
  assert has_element?(view, "#main-panel[data-state='no-selection']")
end

test "creates a Project and selects it in the URL", %{conn: conn} do
  {:ok, view, _html} = live(conn, ~p"/")

  view
  |> form("#project-form", project: %{name: "Taskman", primary_directory: File.cwd!()})
  |> render_submit()

  assert [project] = Taskman.Projects.list_projects()
  assert_patch(view, ~p"/projects/#{project.id}")
  assert has_element?(view, "#project-#{project.id}[aria-current='page']")
  assert has_element?(view, "#tasks")
end

test "unparseable Project ID keeps the sidebar usable", %{conn: conn} do
  project = project_fixture(%{})
  {:ok, view, _html} = live(conn, "/projects/not-an-id")

  assert has_element?(view, "#project-sidebar")
  assert has_element?(view, "#project-not-found")
  refute has_element?(view, "#add-task")

  view |> element("#project-#{project.id}") |> render_click()
  assert_patch(view, ~p"/projects/#{project.id}")
  assert has_element?(view, "#tasks")
end

test "selected Project shows only its direct Tasks", %{conn: conn} do
  selected = project_fixture(%{})
  other = project_fixture(%{})
  selected_task = task_fixture(selected, %{title: "Visible"})
  other_task = task_fixture(other, %{title: "Hidden"})

  {:ok, view, _html} = live(conn, ~p"/projects/#{selected.id}")

  assert has_element?(view, "#task-#{selected_task.id}")
  refute has_element?(view, "#task-#{other_task.id}")
end
end
```

- [ ] **Step 2: Run the LiveView test and confirm the expected failure**

Run: `mix test test/taskman_web/live/project_live_test.exs`

Expected: the root route is still a controller route and the LiveView module does not exist.

- [ ] **Step 3: Replace the stock route with LiveView routes**

Inside the existing browser scope, replace `get "/", PageController, :home` with:

```elixir
live "/", ProjectLive, :index
live "/projects/:project_id", ProjectLive, :show
```

Delete the now-unused stock PageController, PageHTML, home template, and controller test listed in
this task.

- [ ] **Step 4: Implement LiveView state and events**

`ProjectLive.mount/3` initializes `selected_project: nil`, `project_not_found?: false`,
`project_form: to_form(Projects.change_project(%Project{}))`, and empty Project and Task streams.
On connected and disconnected mounts, load `Projects.list_projects/0` into the Project stream.

`handle_params/3` performs these exact state transitions:

- for `:index`, clear selection and reset the Task stream;
- for `:show`, call `Projects.get_project/1`, load its Tasks when present, or assign the main-panel
  not-found state when absent;
- reset the Project stream whenever selection changes so `aria-current` styling updates.

Implement `validate_project` and `save_project` events. Validation sets the changeset action to
`:validate`. Successful save resets the Project form and uses
`push_patch(socket, to: ~p"/projects/#{project.id}")`; failed save reassigns the form.

- [ ] **Step 5: Implement the list-first HEEx shell**

Create `project_live.html.heex` with this complete first-stage structure:

```heex
<Layouts.app flash={@flash} current_scope={nil}>
  <div id="taskman-workspace" class="min-h-screen bg-stone-50 text-slate-950">
    <div class="mx-auto grid min-h-screen max-w-[1600px] lg:grid-cols-[19rem_minmax(0,1fr)]">
      <aside id="project-sidebar" class="border-r border-slate-200 bg-slate-950 text-slate-100">
        <div class="flex h-full flex-col px-5 py-6">
          <div class="mb-8 flex items-center gap-3">
            <div class="grid size-10 place-items-center rounded-xl bg-indigo-500 text-white shadow-lg shadow-indigo-950/30">
              <.icon name="hero-check-circle" class="size-6" />
            </div>
            <div>
              <p class="text-base font-semibold tracking-tight">Taskman</p>
              <p class="text-xs text-slate-400">Local project workspace</p>
            </div>
          </div>

          <nav id="projects" phx-update="stream" aria-label="Projects" class="space-y-1">
            <div id="projects-empty" class="hidden rounded-xl border border-dashed border-slate-700 px-3 py-4 text-sm text-slate-400 only:block">
              Create your first Project below.
            </div>
            <div :for={{dom_id, project} <- @streams.projects} id={dom_id}>
              <.link
                id={"project-#{project.id}"}
                patch={~p"/projects/#{project.id}"}
                aria-current={@selected_project && @selected_project.id == project.id && "page"}
                class={[
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
                  @selected_project && @selected_project.id == project.id && "bg-white/12 text-white",
                  (!@selected_project || @selected_project.id != project.id) && "text-slate-300 hover:bg-white/7 hover:text-white"
                ]}
              >
                <.icon name="hero-folder" class="size-4 shrink-0" />
                <span class="truncate">{project.name}</span>
              </.link>
            </div>
          </nav>

          <.form for={@project_form} id="project-form" phx-change="validate_project" phx-submit="save_project" class="mt-auto space-y-3 border-t border-white/10 pt-5">
            <p class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">New Project</p>
            <.input
              field={@project_form[:name]}
              type="text"
              label="Name"
              autocomplete="off"
              class="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/10"
              error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/10"
            />
            <.input
              field={@project_form[:primary_directory]}
              type="text"
              label="Primary directory"
              autocomplete="off"
              class="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2.5 font-mono text-xs text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/10"
              error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/10"
            />
            <button type="submit" phx-disable-with="Creating…" class="w-full rounded-xl bg-indigo-500 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-400 disabled:cursor-wait disabled:opacity-60">
              Create Project
            </button>
          </.form>
        </div>
      </aside>

      <main
        id="main-panel"
        data-state={if(@project_not_found?, do: "not-found", else: if(@selected_project, do: "selected", else: "no-selection"))}
        class="min-w-0 bg-white"
      >
        <div :if={!@selected_project && !@project_not_found?} class="grid min-h-screen place-items-center px-6 py-16 text-center">
          <div class="max-w-md">
            <.icon name="hero-arrow-left" class="mx-auto mb-5 size-8 text-indigo-500 lg:hidden" />
            <h1 class="text-2xl font-semibold tracking-tight text-slate-950">Choose a Project</h1>
            <p class="mt-3 text-sm leading-6 text-slate-500">Select a Project from the sidebar or create one to begin organizing work.</p>
          </div>
        </div>

        <section :if={@project_not_found?} id="project-not-found" class="grid min-h-screen place-items-center px-6 py-16 text-center">
          <div class="max-w-md">
            <.icon name="hero-folder-minus" class="mx-auto mb-5 size-10 text-slate-300" />
            <h1 class="text-2xl font-semibold tracking-tight text-slate-950">Project not found</h1>
            <p class="mt-3 text-sm leading-6 text-slate-500">This Project may have been removed. Choose another Project from the sidebar.</p>
          </div>
        </section>

        <section :if={@selected_project} class="px-5 py-8 sm:px-8 lg:px-12 lg:py-10">
          <header class="mb-8 flex items-center justify-between gap-4">
            <div class="min-w-0">
              <p class="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Direct list</p>
              <h1 class="mt-1 truncate text-2xl font-semibold tracking-tight text-slate-950">{@selected_project.name}</h1>
              <p class="mt-1 truncate font-mono text-xs text-slate-400">{@selected_project.primary_directory}</p>
            </div>
            <button id="add-task" type="button" disabled class="rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white opacity-60 shadow-sm">
              Add task
            </button>
          </header>

          <div id="tasks" phx-update="stream" class="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div id="tasks-empty" class="hidden px-6 py-16 text-center only:block">
              <.icon name="hero-clipboard-document-list" class="mx-auto size-9 text-slate-300" />
              <p class="mt-3 text-sm font-medium text-slate-700">No direct Tasks yet</p>
            </div>
            <article :for={{dom_id, task} <- @streams.tasks} id={dom_id} class="grid gap-3 border-b border-slate-100 px-5 py-4 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center">
              <p id={"task-#{task.id}"} class="truncate text-sm font-medium text-slate-900">{task.title}</p>
              <span class="w-fit rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700">Pending</span>
              <span class="w-fit rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">None</span>
            </article>
          </div>
        </section>
      </main>
    </div>
  </div>
</Layouts.app>
```

Update `Layouts.app/1` to be a neutral full-width application frame without the generated Phoenix
navigation. Keep `<Layouts.flash_group>` inside `layouts.ex`. Update the root title suffix to
`" · Taskman"`, remove the generated marketing identity, and remove the generated inline theme
script because this product shell does not expose that theme control.

- [ ] **Step 6: Run focused tests and format**

Run:

```bash
mix format
mix test test/taskman_web/live/project_live_test.exs
```

Expected: all Project shell, selection, isolation, and not-found tests pass with zero failures.

- [ ] **Step 7: Commit the Project LiveView**

Run: `but status --format json`, confirm only Task 3 files are uncommitted, then run:

```bash
but commit mvp --message "add list-first Project workspace"
```

Expected: one new commit on `mvp` and a clean workspace.

---

### Task 4: URL-Backed Task Creation Modal

**Files:**

- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/components/core_components.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Create: `lib/taskman_web/components/task_form.ex`
- Modify: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: `Tasks.change_task/2` and `Tasks.create_task/2` from Task 2.
- Produces route: `/projects/:project_id/tasks/new` with live action `:new_task`.
- Produces reusable component: `<TaskForm.form form={@task_form} cancel={path} />`.
- Produces stable IDs: `#task-modal`, `#task-form`, `#task-title`, and `#cancel-task`.

- [ ] **Step 1: Write failing modal interaction tests**

Add these tests to `project_live_test.exs`:

```elixir
test "invalid Project input renders inline errors", %{conn: conn} do
  {:ok, view, _html} = live(conn, ~p"/")

  view
  |> form("#project-form", project: %{name: "Taskman", primary_directory: "/not/a/taskman/dir"})
  |> render_submit()

  assert has_element?(view, "#project-form [data-role='field-error']")
end

test "opens and cancels the new Task modal over the selected list", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Existing"})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

  view |> element("#add-task") |> render_click()
  assert_patch(view, ~p"/projects/#{project.id}/tasks/new")
  assert has_element?(view, "#task-modal")
  assert has_element?(view, "#task-#{task.id}")

  view |> element("#cancel-task") |> render_click()
  assert_patch(view, ~p"/projects/#{project.id}")
  refute has_element?(view, "#task-modal")
end

test "invalid Task stays in the modal and successful Task appears in the list", %{conn: conn} do
  project = project_fixture(%{})
  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

  view |> form("#task-form", task: %{title: ""}) |> render_submit()
  assert has_element?(view, "#task-modal")
  assert has_element?(view, "#task-form [data-role='field-error']")

  view |> form("#task-form", task: %{title: "Ship first slice"}) |> render_submit()
  assert_patch(view, ~p"/projects/#{project.id}")
  refute has_element?(view, "#task-modal")

  [task] = Taskman.Tasks.list_tasks_for_project(project)
  assert task.status == :pending
  assert task.priority == :none
  assert has_element?(view, "#task-#{task.id}")
end
```

- [ ] **Step 2: Run the modal tests and confirm the expected failure**

Run: `mix test test/taskman_web/live/project_live_test.exs`

Expected: navigation to `/tasks/new` fails because the route and modal do not exist.

- [ ] **Step 3: Add the modal route and accessible core component**

Add this route after the selected Project route:

```elixir
live "/projects/:project_id/tasks/new", ProjectLive, :new_task
```

Add a project-owned `<.modal>` to `CoreComponents`. It must render a fixed backdrop and centered
`role="dialog"` container with `aria-modal="true"`, `aria-labelledby`, `phx-click-away`, and Escape
handling. Its public attributes are `id`, `show`, and `on_cancel`; its inner slot contains the form.
Use `JS.push_focus/1`, `JS.focus_first/2`, `JS.pop_focus/1`, and Tailwind transition commands so
opening focuses the first control and closing restores focus. The close button uses
`<.icon name="hero-x-mark">` and has an explicit accessible label.

Also update the private field-error component to include `data-role="field-error"` while preserving
its translated message and icon. Do not call `<.flash_group>` from this component.

- [ ] **Step 4: Add the reusable Task form component**

Create `TaskmanWeb.TaskForm` with `use TaskmanWeb, :html`, a required
`Phoenix.HTML.Form` attribute named `form`, and a required string attribute named `cancel`.
Implement:

```heex
<.form for={@form} id="task-form" phx-change="validate_task" phx-submit="save_task">
  <.input
    field={@form[:title]}
    id="task-title"
    type="text"
    label="Task title"
    autocomplete="off"
    class="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-3 text-sm text-slate-950 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/10"
    error_class="border-rose-400 focus:border-rose-500 focus:ring-rose-500/10"
  />
  <div class="mt-6 flex justify-end gap-3">
    <.link id="cancel-task" patch={@cancel} class="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-950">
      Cancel
    </.link>
    <button type="submit" phx-disable-with="Creating…" class="rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60">
      Create task
    </button>
  </div>
</.form>
```

- [ ] **Step 5: Implement modal state and Task events in ProjectLive**

For `:new_task`, load the Project exactly like `:show`; when valid, assign
`task_form: to_form(Tasks.change_task(%Task{}))`. When the Project is unknown, render the existing
main-panel not-found state and do not open a modal.

Implement:

```elixir
def handle_event("validate_task", %{"task" => task_params}, socket) do
  form =
    %Task{}
    |> Tasks.change_task(task_params)
    |> Map.put(:action, :validate)
    |> to_form()

  {:noreply, assign(socket, :task_form, form)}
end

def handle_event("save_task", %{"task" => task_params}, socket) do
  case Tasks.create_task(socket.assigns.selected_project, task_params) do
    {:ok, task} ->
      {:noreply,
       socket
       |> stream_insert(:tasks, task)
       |> push_patch(to: ~p"/projects/#{socket.assigns.selected_project.id}")}

    {:error, changeset} ->
      {:noreply, assign(socket, :task_form, to_form(changeset))}
  end
end
```

Render `<.modal>` only when `@live_action == :new_task` and `@selected_project` is present. Place a
heading with a stable ID inside it, render `<TaskForm.form>`, and patch cancel actions back to the
selected Project URL. Replace Task 3's disabled Add task control with a patch link to the new route.

- [ ] **Step 6: Run focused tests and format**

Run:

```bash
mix format
mix test test/taskman_web/live/project_live_test.exs
```

Expected: all modal, validation, creation, isolation, and not-found tests pass with zero failures.

- [ ] **Step 7: Commit Task creation**

Run: `but status --format json`, confirm only Task 4 files are uncommitted, then run:

```bash
but commit mvp --message "add modal Task creation flow"
```

Expected: one new commit on `mvp` and a clean workspace.

---

### Task 5: Slice Acceptance and Project Status

**Files:**

- Modify: `docs/planning/roadmap.md`
- Modify: `docs/handoffs/implementation.md`
- Verify: all files changed by Tasks 1 through 4.

**Interfaces:**

- Consumes: the complete running first slice.
- Produces: current roadmap and handoff documentation naming Task editing and lifecycle controls as
  the next increment.

- [ ] **Step 1: Run the complete automated gate**

Run:

```bash
mix precommit
if rg -n 'Taskman\.Repo|Repo\.|Ecto\.Query' lib/taskman_web; then exit 1; else test $? -eq 1; fi
```

Expected: compilation succeeds with warnings treated as errors, formatting makes no further changes,
the full test suite reports zero failures, and the web layer contains no direct Repo calls or Ecto
queries.

- [ ] **Step 2: Perform the browser smoke test**

Run: `mix phx.server` and verify at `http://localhost:4000`:

1. The empty root has a functional Project sidebar.
2. Invalid Project input shows inline errors.
3. A valid Project is created, selected, highlighted, and represented in the URL.
4. Add task opens a focused modal over the Project's existing direct list.
5. Invalid Task input stays in the modal.
6. A valid Task closes the modal and appears with Pending and None badges.
7. An unknown Project ID shows not-found content only in the main panel; selecting a sidebar Project
   recovers without a full reload.
8. The layout remains usable at narrow and wide browser widths, with visible keyboard focus.

Expected: every acceptance path works without browser-console or server errors.

- [ ] **Step 3: Update delivery documentation**

In `docs/planning/roadmap.md`, keep the overall Projects and basic Tasks slice in progress and record
that Project creation/selection plus default Task creation/direct listing are complete. State that
Task editing and human-controlled lifecycle changes remain.

In `docs/handoffs/implementation.md`, replace the pre-slice position with the implemented behavior,
verification evidence, and the next thin increment: Task editing and lifecycle controls. Do not
claim the full Projects and basic Tasks roadmap slice is complete.

- [ ] **Step 4: Re-run documentation and workspace checks**

Run:

```bash
rg -n "Projects and basic Tasks|Task editing|lifecycle|mix precommit" docs/planning/roadmap.md docs/handoffs/implementation.md
but diff --format agent
but status --format json
```

Expected: the documents agree on current state, the diff contains only Task 5 documentation, and no
unrelated workspace changes exist.

- [ ] **Step 5: Commit verified slice status**

Run:

```bash
but commit mvp --message "record first Projects and Tasks slice"
```

Expected: one documentation commit on `mvp` and a clean workspace.

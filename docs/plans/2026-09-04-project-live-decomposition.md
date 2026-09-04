# ProjectLive Workflow Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `TaskmanWeb.ProjectLive` into cohesive workflow modules while preserving every
route, event, stream, subscription, DOM, persistence, conflict, and error contract.

**Architecture:** `TaskmanWeb.ProjectLive` remains the sole LiveView and explicitly coordinates
route application and callback dispatch. Ordinary modules own workspace, listing, creation,
editing, parent-selection, movement, and reconciliation workflows; each workflow-specific state
structure is a nested `State` module containing only data and pure transformations.

**Tech Stack:** Elixir 1.17+, Phoenix 1.8.9, LiveView 1.2, Ecto/PostgreSQL, ExUnit, LazyHTML.

**Spec:** `docs/specs/2026-09-03-project-live-decomposition-design.md`

**Status:** Approved

**Delivery tracking:** `tas-1tq`

| Task | Beads issue |
| --- | --- |
| 1 | `tas-1tq.1` |
| 2 | `tas-1tq.2` |
| 3 | `tas-1tq.3` |
| 4 | `tas-1tq.4` |
| 5 | `tas-1tq.5` |
| 6 | `tas-1tq.6` |
| 7 | `tas-1tq.7` |
| 8 | `tas-1tq.8` |
| 9 | `tas-1tq.9` |

## Global Constraints

- Read the complete approved specification, `AGENTS.md`, and `docs/development.md` before starting
  any task.
- Preserve the existing routes, event names and payloads, stream names and DOM IDs, PubSub topics,
  error text, persistence behavior, and public Project/List/Task context APIs.
- Keep `TaskmanWeb.ProjectLive` as the only LiveView, socket owner, route target, and renderer.
- Keep `mount/3`, `handle_params/3`, cross-workflow route ordering, stream configuration, callback
  dispatch, and rendering in `ProjectLive`.
- Keep `current_scope`, `current_user`, `flash`, `live_action`, and LiveView streams outside
  workflow state structures.
- State transitions accept and return structures; they must not receive sockets or call contexts,
  persistence, PubSub, timers, or navigation.
- Define `Workspace.State`, `Listing.State`, `Creation.State`, and `Editing.State` inside their
  owning workflow files. Extract one only if it gains independent consumers, external
  coordination, persistence, or substantial behavior.
- Do not introduce LiveComponents, lifecycle hooks, callback-injection macros, a shared generic
  helpers module, or a second route coordinator.
- Workflow modules may use LiveView socket APIs, verified routes through `Paths`, existing
  socket-free state modules, and public Taskman context APIs.
- Preserve malformed-event no-op clauses and do not add an unknown-event catch-all.
- Update template expressions mechanically to grouped state fields without changing rendered
  markup or component boundaries.
- Use selector-based LiveView assertions and stable DOM IDs; do not assert raw application HTML or
  styling details.
- Use `but` for version-control mutations. Each task ends with focused verification and an
  independently reviewable commit; do not push, merge, publish, or deploy without separate
  authorization.

---

## File and Interface Map

- `lib/taskman_web/live/project_live.ex` remains the coordinator. At completion it contains
  `mount/3`, `handle_params/3`, route composition, workflow event lists, thin callback delegation,
  stream configuration, and shared aliases only.
- `lib/taskman_web/live/project_live.html.heex` renders grouped assigns:
  `@workspace`, `@listing`, `@creation`, and `@editing`. It calls `Paths` for routes and workflow
  presentation helpers for derived copy.
- `lib/taskman_web/live/project_live/paths.ex` owns pure verified-route construction, query
  preservation, and canonical selected-route comparisons.
- `lib/taskman_web/live/project_live/workspace.ex` owns Project/List forms, selected location,
  navigation streams, subscriptions, and workspace reconciliation. Its nested `State` owns:
  `selected_project`, `subscribed_project_id`, `project_not_found?`, `selected_list`,
  `include_children?`, `location_not_found?`, `location_path`, `project_form`,
  `expanded_node_ids`, and `list_edit`.
- `lib/taskman_web/live/project_live/tasks/listing.ex` owns Task filtering, sorting, querying, and
  stream refresh. Its nested `State` owns: `visible_statuses`, `status_filter_form`,
  `filter_open?`, `sort`, `tasks_empty?`, and `tasks_filtered_empty?`.
- `lib/taskman_web/live/project_live/tasks/creation.ex` owns new-Task route state, validation,
  persistence, and location refresh. Its nested `State` owns: `form`, `enabled?`, and `location`.
- `lib/taskman_web/live/project_live/tasks/editing.ex` owns selected Task detail, hierarchy,
  autosave, conflict handling, flushes, and persisted-Task reconciliation. Its nested `State` owns:
  `selected_task`, `not_found?`, `detail_open?`, `autosave`, and `hierarchy`.
- `lib/taskman_web/live/project_live/tasks/parent_selection.ex` owns browser orchestration for the
  existing `ParentPicker` state shared by creation and editing.
- `lib/taskman_web/live/project_live/tasks/movement.ex` owns browser and route orchestration for
  the existing `Move` state.
- `lib/taskman_web/live/project_live/reconciliation.ex` validates and orders autosave and
  workspace-change messages without duplicating workflow rules.
- New direct tests live beside the existing state tests:
  `test/taskman_web/live/project_live/paths_test.exs`,
  `test/taskman_web/live/project_live/workspace_test.exs`,
  `test/taskman_web/live/project_live/tasks/listing_test.exs`,
  `test/taskman_web/live/project_live/tasks/creation_test.exs`, and
  `test/taskman_web/live/project_live/tasks/editing_test.exs`.
- Existing behavior suites under `test/taskman_web/live/project_live/` remain authoritative for
  LiveView outcomes and are not reorganized again.

The workflow interfaces are internal application APIs:

```elixir
@spec Paths.browse_path(Project.t(), TaskList.t() | nil, boolean()) :: String.t()
@spec Paths.new_task_path(Project.t(), TaskList.t() | nil, boolean(), pos_integer() | nil) ::
        String.t()
@spec Paths.task_detail_path(Project.t(), TaskList.t() | nil, Task.t(), boolean()) ::
        String.t()
@spec Paths.selected_task_route?(map(), Project.t(), TaskList.t() | nil, Task.t(), boolean()) ::
        boolean()

@spec Workspace.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Workspace.resolve_location(map()) ::
        {:ok, Project.t(), TaskList.t() | nil}
        | {:error, :project_not_found}
        | {:error, :location_not_found, Project.t()}
@spec Workspace.location_path(Project.t(), TaskList.t() | nil) :: [TaskList.t()]
@spec Workspace.subscribe(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Workspace.refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Workspace.reconcile(Phoenix.LiveView.Socket.t(), ChangeNotifications.Event.t()) ::
        {Phoenix.LiveView.Socket.t(),
         :unchanged | {:location_changed, [TaskList.t()]} | :location_missing}

@spec Listing.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Listing.refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Listing.clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()

@spec Creation.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Creation.apply_route(Phoenix.LiveView.Socket.t(), map()) :: Phoenix.LiveView.Socket.t()
@spec Creation.refresh_location(Phoenix.LiveView.Socket.t(), [TaskList.t()]) ::
        Phoenix.LiveView.Socket.t()
@spec Creation.clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()

@spec Editing.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Editing.apply_route(Phoenix.LiveView.Socket.t(), Project.t(), Task.t()) ::
        Phoenix.LiveView.Socket.t()
@spec Editing.handle_autosave_info(tuple(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Editing.flush(Phoenix.LiveView.Socket.t()) ::
        {:ok, Phoenix.LiveView.Socket.t()} | {:error, Phoenix.LiveView.Socket.t()}
@spec Editing.reconcile(Phoenix.LiveView.Socket.t(), ChangeNotifications.Event.t()) ::
        Phoenix.LiveView.Socket.t()
@spec Editing.sync_persisted_task(Phoenix.LiveView.Socket.t(), Task.t()) ::
        Phoenix.LiveView.Socket.t()
@spec Editing.reload_hierarchy(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Editing.clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()

@spec ParentSelection.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec ParentSelection.open_edit(Phoenix.LiveView.Socket.t(), Project.t(), Task.t()) ::
        Phoenix.LiveView.Socket.t()
@spec ParentSelection.sync(Phoenix.LiveView.Socket.t(), Task.t()) ::
        Phoenix.LiveView.Socket.t()
@spec ParentSelection.refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec ParentSelection.clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()

@spec Movement.handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
@spec Movement.refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Movement.reconcile(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
@spec Movement.clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()

@spec Reconciliation.handle_info(term(), Phoenix.LiveView.Socket.t()) ::
        {:noreply, Phoenix.LiveView.Socket.t()}
```

### Task 1: Extract pure route construction

**Files:**

- Create: `lib/taskman_web/live/project_live/paths.ex`
- Create: `test/taskman_web/live/project_live/paths_test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`

**Interfaces:**

- Consumes: `Taskman.Projects.Project`, `Taskman.Lists.TaskList`, `Taskman.Tasks.Task`, and Phoenix
  verified routes.
- Produces: `Paths.browse_path/3`, `new_task_path/4`, `task_detail_path/4`, and
  `selected_task_route?/5` for every later workflow.

- [ ] **Step 1: Add failing direct route tests**

Use ordinary structs so these pure tests need no database:

```elixir
defmodule TaskmanWeb.ProjectLive.PathsTest do
  use ExUnit.Case, async: true

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths

  test "builds browse, creation, and detail routes with stable query ordering" do
    project = %Project{id: 11}
    task_list = %TaskList{id: 22}
    task = %Task{id: 33}

    assert Paths.browse_path(project, nil, false) == "/projects/11"
    assert Paths.browse_path(project, task_list, true) ==
             "/projects/11/lists/22?include_children=true"

    assert Paths.new_task_path(project, task_list, true, 33) ==
             "/projects/11/lists/22/tasks/new?include_children=true&parent_task_id=33"

    assert Paths.task_detail_path(project, nil, task, true) ==
             "/projects/11/tasks/33?include_children=true"
  end

  test "recognizes only the exact selected Task route" do
    project = %Project{id: 11}
    task_list = %TaskList{id: 22}
    task = %Task{id: 33}
    params = %{"project_id" => "11", "list_id" => "22", "task_id" => "33"}

    assert Paths.selected_task_route?(params, project, task_list, task, false)
    refute Paths.selected_task_route?(params, project, nil, task, false)
    refute Paths.selected_task_route?(
             Map.put(params, "include_children", "true"),
             project,
             task_list,
             task,
             false
           )
  end
end
```

- [ ] **Step 2: Verify the new test fails for the missing module**

Run:

```sh
mix test test/taskman_web/live/project_live/paths_test.exs
```

Expected: compilation fails because `TaskmanWeb.ProjectLive.Paths` is undefined.

- [ ] **Step 3: Move the route helpers without changing their clauses**

Define `Paths` with `use TaskmanWeb, :verified_routes`. Move `browse_path/3`,
`new_task_path/4`, `task_detail_path/4`, `selected_task_route?/5`,
`selected_list_route?/2`, `canonical_query_route?/2`, `append_include_children/2`, and
`append_parent_task_id/2`. Keep the latter four private and retain the current query ordering.

- [ ] **Step 4: Replace root and template calls with `Paths` calls**

Alias `TaskmanWeb.ProjectLive.Paths` in the root module. Replace every route helper invocation,
including modal cancellation, descendant toggling, subtask links, successful creation, failed
autosave route restoration, and movement route restoration. Remove the extracted root helpers.

- [ ] **Step 5: Verify route and navigation behavior**

Run:

```sh
mix test test/taskman_web/live/project_live/paths_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs \
  test/taskman_web/live/project_live/lists_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs \
  test/taskman_web/live/project_live/move_task_test.exs
```

Expected: all tests pass with unchanged patches and navigation.

- [ ] **Step 6: Commit the route extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive route paths"
```

### Task 2: Group workspace state

**Files:**

- Create: `lib/taskman_web/live/project_live/workspace.ex`
- Create: `test/taskman_web/live/project_live/workspace_test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`

**Interfaces:**

- Consumes: `ListEdit`, Project/List structs, and an initialized Project form.
- Produces: nested `Workspace.State`, `Workspace.panel_state/1`,
  `Workspace.selected_location/1`, and grouped `@workspace` access for later workflows.

- [ ] **Step 1: Add failing state-invariant tests**

```elixir
defmodule TaskmanWeb.ProjectLive.WorkspaceTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias TaskmanWeb.ProjectLive.ListEdit
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Workspace.State

  test "location transitions keep found and not-found state mutually consistent" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = State.new(:project_form)

    selected = State.select_location(state, project, task_list, true, [task_list])
    assert selected.selected_project == project
    assert selected.selected_list == task_list
    assert selected.include_children?
    refute selected.project_not_found?
    refute selected.location_not_found?

    missing = State.location_not_found(selected, project)
    assert missing.selected_project == project
    assert missing.selected_list == nil
    assert missing.location_path == []
    assert missing.location_not_found?
    refute missing.project_not_found?

    assert State.project_not_found(missing).project_not_found?
  end

  test "node and List-edit transitions remain inside workspace state" do
    project = project_fixture(%{})
    state = State.new(:project_form)
    list_edit = ListEdit.open_new(project, nil)

    state = state |> State.toggle_node({:project, project.id}) |> State.put_list_edit(list_edit)
    assert MapSet.member?(state.expanded_node_ids, {:project, project.id})
    assert state.list_edit == list_edit
    assert State.clear_list_edit(state).list_edit == ListEdit.empty()
  end
end
```

- [ ] **Step 2: Verify the test fails for the missing state module**

Run:

```sh
mix test test/taskman_web/live/project_live/workspace_test.exs
```

Expected: compilation fails because `Workspace.State` is undefined.

- [ ] **Step 3: Define the nested state and pure transitions**

In `workspace.ex`, define `State` before the containing module functions:

```elixir
defmodule State do
  alias TaskmanWeb.ProjectLive.ListEdit

  defstruct selected_project: nil,
            subscribed_project_id: nil,
            project_not_found?: false,
            selected_list: nil,
            include_children?: false,
            location_not_found?: false,
            location_path: [],
            project_form: nil,
            expanded_node_ids: MapSet.new(),
            list_edit: ListEdit.empty()
end
```

Implement `new/1`, `select_location/5`, `project_not_found/1`,
`location_not_found/2`, `toggle_node/2`, `put_list_edit/2`, `clear_list_edit/1`, and
`put_subscription/2`. Implement `Workspace.panel_state/1` and `selected_location/1` against the
structure, not a socket.

- [ ] **Step 4: Replace workspace scalar assigns with `@workspace`**

Initialize one `Workspace.State` in `mount/3`. Update all root logic and HEEx expressions for the
ten workspace fields listed in the file map. Keep existing event handlers and socket orchestration
in `ProjectLive` temporarily, but every state change must assign a complete updated
`Workspace.State`; do not retain compatibility scalar assigns.

Use this socket update shape:

```elixir
workspace =
  socket.assigns.workspace
  |> Workspace.State.select_location(project, task_list, include_children?, location_path)

assign(socket, :workspace, workspace)
```

- [ ] **Step 5: Verify grouped state across workspace behavior**

Run:

```sh
mix test test/taskman_web/live/project_live/workspace_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs \
  test/taskman_web/live/project_live/lists_test.exs \
  test/taskman_web/live/project_live/workspace_updates_test.exs \
  test/taskman_web/live/project_live/external_updates_test.exs
```

Expected: all tests pass and the template has no references to the superseded workspace scalars.

- [ ] **Step 6: Commit the workspace-state migration**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Group ProjectLive workspace state"
```

### Task 3: Extract Task listing

**Files:**

- Create: `lib/taskman_web/live/project_live/tasks/listing.ex`
- Create: `test/taskman_web/live/project_live/tasks/listing_test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`

**Interfaces:**

- Consumes: `socket.assigns.workspace`, the Tasks context, and the `:tasks` stream.
- Produces: nested `Listing.State`, `handle_event/3`, `refresh/1`, and `clear/1`. Creation,
  editing, movement, workspace, and reconciliation call `refresh/1`.

- [ ] **Step 1: Add failing sorting, filtering, and result-state tests**

```elixir
defmodule TaskmanWeb.ProjectLive.Tasks.ListingTest do
  use ExUnit.Case, async: true

  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State

  test "normalizes visible statuses and cycles sort directions" do
    state = State.new(Task.statuses() -- [:will_not_do])
    state = State.apply_statuses(state, ["done", "unknown", "pending"])

    assert state.visible_statuses == [:pending, :done]
    assert state.status_filter_form[:statuses].value == ["pending", "done"]

    assert state |> State.sort_by(:title) |> Map.fetch!(:sort) == {:title, :asc}
    assert state |> State.sort_by(:status) |> Map.fetch!(:sort) == {:status, :desc}
    assert state |> State.sort_by(:title) |> State.sort_by(:title) |> Map.fetch!(:sort) ==
             {:title, :desc}
  end

  test "clears an unavailable location sort and updates both empty flags together" do
    state =
      Task.statuses()
      |> State.new()
      |> State.sort_by(:location)
      |> State.available_sort(false)
      |> State.put_results([], true)

    assert state.sort == nil
    assert state.tasks_empty?
    assert state.tasks_filtered_empty?
    assert State.clear_results(state).tasks_empty?
    refute State.clear_results(state).tasks_filtered_empty?
  end
end
```

- [ ] **Step 2: Verify the new test fails**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/listing_test.exs
```

Expected: compilation fails because `Listing.State` is undefined.

- [ ] **Step 3: Define listing state and move pure transitions**

Define nested `State` with:

```elixir
defstruct visible_statuses: [],
          status_filter_form: nil,
          filter_open?: false,
          sort: nil,
          tasks_empty?: true,
          tasks_filtered_empty?: false
```

Move status normalization, form construction, filter toggling/closing, status application, sort
cycling, location-sort availability, and coupled result flags into `State`. Keep the current Task
status order and descending initial direction for status and priority.

- [ ] **Step 4: Move listing events and stream orchestration**

Move the five filtering/sorting event families, `list_tasks_for_location/5`,
`refresh_task_stream/1`, and `tasks_filtered_empty?/5` into `Listing`. Rename the public stream
operation to `refresh/1`, add `clear/1`, and preserve every malformed-payload fallback. Add the
listing event attribute and one guarded delegation clause to `ProjectLive`.

- [ ] **Step 5: Migrate mount, route, and template listing accesses**

Initialize `@listing` once in `mount/3`. Change route application to call
`State.available_sort/2` and `Listing.refresh/1`. Update the template to read the six listing
fields and remove all corresponding scalar assigns and helpers from `ProjectLive`.

- [ ] **Step 6: Verify direct and LiveView listing behavior**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/listing_test.exs \
  test/taskman_web/live/project_live/task_table_test.exs \
  test/taskman_web/live/project_live/lists_test.exs \
  test/taskman_web/live/project_live/task_updates_test.exs
```

Expected: all tests pass, including location-sort clearing and filtered empty states.

- [ ] **Step 7: Commit the listing extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive Task listing"
```

### Task 4: Extract Task creation

**Files:**

- Create: `lib/taskman_web/live/project_live/tasks/creation.ex`
- Create: `test/taskman_web/live/project_live/tasks/creation_test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`

**Interfaces:**

- Consumes: `Workspace.State`, `Listing.refresh/1`, `ParentPicker`, Tasks and Lists contexts, and
  `Paths`.
- Produces: nested `Creation.State`, `handle_event/3`, `apply_route/2`,
  `refresh_location/2`, `clear/1`, and `location_copy/2`.

- [ ] **Step 1: Add failing creation-state tests**

```elixir
defmodule TaskmanWeb.ProjectLive.Tasks.CreationTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Tasks.Creation
  alias TaskmanWeb.ProjectLive.Tasks.Creation.State

  test "an enabled form always retains its target location" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    state = State.empty() |> State.open(valid_form, task_list)
    assert state.enabled?
    assert state.location == task_list
    assert Creation.location_copy(project, state) == "Create this Task in List #{task_list.name}."

    assert State.clear(state) == State.empty()
  end

  test "validation preserves location and derives enabled state from the form" do
    project = project_fixture(%{})
    state = State.empty() |> State.open(Tasks.change_task(project) |> Phoenix.Component.to_form(), nil)
    valid_form = project |> Tasks.change_task(%{"title" => "Ship"}) |> Phoenix.Component.to_form()

    validated = State.validate(state, valid_form)
    assert validated.enabled?
    assert validated.location == nil
  end
end
```

- [ ] **Step 2: Verify the new test fails**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/creation_test.exs
```

Expected: compilation fails because `Creation.State` is undefined.

- [ ] **Step 3: Define creation state and pure transitions**

Define nested `State`:

```elixir
defstruct form: nil, enabled?: false, location: nil
```

Implement `empty/0`, `open/3`, `validate/2`, `refresh_location/2`, and `clear/1`. `open/3`
accepts the prior state, form, and location; `validate/2` preserves the captured location;
`refresh_location/2` replaces a matching List with its canonical value and otherwise preserves the
current state.

- [ ] **Step 4: Move route setup, validation, and save orchestration**

Move `apply_action(:new_task, ...)`, `validate_task`, `save_task`, `task_create_state/3`,
`task_create_parent/2`, `task_location/2`, and location-copy clauses into `Creation`. Expose them
through `apply_route/2`, `handle_event/3`, and `location_copy/2`. On successful creation call
`Listing.refresh/1` before patching through `Paths.browse_path/3`; preserve parent-not-found and
changeset errors exactly.

- [ ] **Step 5: Migrate mount, route, and template creation accesses**

Initialize `@creation` with `Creation.State.empty/0`, delegate the two creation events, call
`Creation.apply_route/2` for `:new_task`, and use `Creation.clear/1` for other actions. Update the
modal to read `@creation.form`, `@creation.enabled?`, and
`Creation.location_copy(@workspace.selected_project, @creation)`.

- [ ] **Step 6: Verify direct and end-to-end creation behavior**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/creation_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs \
  test/taskman_web/live/project_live/lists_test.exs
```

Expected: all tests pass, including preselected parents, invalid drafts, captured locations, and
successful route restoration.

- [ ] **Step 7: Commit the creation extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive Task creation"
```

### Task 5: Extract Task editing and autosave orchestration

**Files:**

- Create: `lib/taskman_web/live/project_live/tasks/editing.ex`
- Create: `test/taskman_web/live/project_live/tasks/editing_test.exs`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`

**Interfaces:**

- Consumes: `Workspace.State`, `Listing.refresh/1`, `Paths`, `Autosave`, `Hierarchy`, and Tasks.
- Produces: nested `Editing.State`, `handle_event/3`, `apply_route/3`,
  `handle_autosave_info/2`, `flush/1`, `reconcile/2`, `sync_persisted_task/2`,
  `reload_hierarchy/1`, and `clear/1`. Parent selection and movement depend on these APIs.

- [ ] **Step 1: Add failing editing-state tests**

```elixir
defmodule TaskmanWeb.ProjectLive.Tasks.EditingTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Editing, Hierarchy}
  alias TaskmanWeb.ProjectLive.Tasks.Editing.State

  test "opening and clearing detail keeps autosave and hierarchy aligned with the Task" do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, hierarchy} = Tasks.get_task_hierarchy(project, task)

    state = State.empty() |> State.open(task, hierarchy)
    assert state.selected_task == task
    assert state.detail_open?
    assert state.autosave.task_id == task.id
    assert state.hierarchy.hierarchy.selected_task_id == task.id

    cleared = State.clear(state)
    assert cleared.selected_task == nil
    refute cleared.detail_open?
    assert cleared.autosave == Autosave.empty()
    assert cleared.hierarchy == Hierarchy.empty()
  end

  test "not-found state removes incompatible detail data" do
    state = State.empty() |> State.not_found()
    assert state.not_found?
    assert state.selected_task == nil
    refute state.detail_open?
  end
end
```

- [ ] **Step 2: Verify the new test fails**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/editing_test.exs
```

Expected: compilation fails because `Editing.State` is undefined.

- [ ] **Step 3: Define editing state and pure transitions**

Define nested `State`:

```elixir
defstruct selected_task: nil,
          not_found?: false,
          detail_open?: false,
          autosave: Autosave.empty(),
          hierarchy: Hierarchy.empty()
```

Implement `empty/0`, `open/3`, `not_found/1`, `put_autosave/2`, `put_hierarchy/2`,
`clear_transient/1`, and `clear/1`. `open/3` loads a fresh unsaved autosave baseline and the
matching hierarchy; `not_found/1` clears selected Task, autosave, and hierarchy before setting the
not-found flag.

- [ ] **Step 4: Move detail, hierarchy, and autosave logic**

Move the hierarchy-toggle event, autosave event, edit submission, ordinary conflict resolution,
scheduled autosave handling, result application, autosave synchronization and scheduling,
detail loading/not-found handling, hierarchy reload, flush, failed-route restoration, and
persisted-Task reconciliation into `Editing`. Replace private names with the interfaces listed
above. Keep `Process.send_after/3`, every tagged Autosave result, and failure behavior unchanged.

- [ ] **Step 5: Migrate grouped editing state and callback dispatch**

Initialize `@editing`, delegate the four editing event families, and route scheduled autosave
messages to `Editing.handle_autosave_info/2`. Change `handle_params/3` to call `Editing.flush/1`.
For `:show_task`, resolve the scoped Task in the root route flow and call
`Editing.apply_route/3`; use `Editing.clear/1` for non-detail actions. Update the template and
component arguments to read `@editing.selected_task`, `@editing.autosave`,
`@editing.hierarchy`, and the detail/not-found flags.

- [ ] **Step 6: Verify editing, hierarchy, and autosave behavior**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/editing_test.exs \
  test/taskman_web/live/project_live/tasks/autosave_test.exs \
  test/taskman_web/live/project_live/tasks/hierarchy_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs
```

Expected: all tests pass, including timer debouncing, flush failures, conflicts, missing Tasks,
hierarchy navigation, and modal clearing.

- [ ] **Step 7: Commit the editing extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive Task editing"
```

### Task 6: Extract parent-selection orchestration

**Files:**

- Create: `lib/taskman_web/live/project_live/tasks/parent_selection.ex`
- Modify: `lib/taskman_web/live/project_live.ex`

**Interfaces:**

- Consumes: `ParentPicker`, `Editing.sync_persisted_task/2`,
  `Editing.reload_hierarchy/1`, `Listing.refresh/1`, and `Workspace.State`.
- Produces: `handle_event/3`, `open_edit/3`, `sync/2`, `refresh/1`, and `clear/1` for root route
  setup and reconciliation.

- [ ] **Step 1: Record the focused parent-selection baseline**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/parent_picker_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs
```

Expected: all tests pass before moving socket orchestration.

- [ ] **Step 2: Move all eight parent event families**

Move opening, toggling, closing, searching, keyboard handling, selecting, clearing, and parent
conflict resolution into `ParentSelection.handle_event/3`, including every malformed-payload
fallback. Keep `@task_parent_picker` as its own top-level assign because creation and editing share
it.

- [ ] **Step 3: Move parent persistence and synchronization**

Move `update_task_parent_picker/3`, `save_task_parent_picker/4`,
`sync_task_parent_picker/3`, and shared conflict-resolution parsing. On `{:ok, picker, task}` and
`{:conflict, picker, task}`, call `Editing.sync_persisted_task/2`,
`Editing.reload_hierarchy/1`, and `Listing.refresh/1` in the current order. On errors assign only
the returned picker.

- [ ] **Step 4: Add parent event delegation and route synchronization**

Add the parent-selection event attribute and guarded root delegation. Move edit-route picker
initialization to `open_edit/3`; creation route setup continues to initialize its picker alongside
the parent-derived creation location. Implement `sync/2` for an external persisted-Task update,
`refresh/1` to rebuild open create/edit candidates after workspace changes, and `clear/1` for
modal clearing; none may reset an unrelated editing draft.

- [ ] **Step 5: Verify parent and autosave interaction**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/parent_picker_test.exs \
  test/taskman_web/live/project_live/project_live_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs \
  test/taskman_web/live/project_live/external_updates_test.exs
```

Expected: all tests pass, including stale candidates, parent conflicts, dirty ordinary fields, and
external parent changes.

- [ ] **Step 6: Commit the parent-selection extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive parent selection"
```

### Task 7: Extract Task movement orchestration

**Files:**

- Create: `lib/taskman_web/live/project_live/tasks/movement.ex`
- Modify: `lib/taskman_web/live/project_live.ex`

**Interfaces:**

- Consumes: `Move`, `Editing.flush/1`, `Editing.sync_persisted_task/2`,
  `Listing.refresh/1`, Tasks, and `Workspace.State`.
- Produces: `handle_event/3`, `refresh/1`, `reconcile/1`, and `clear/1`.

- [ ] **Step 1: Record the focused movement baseline**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/move_test.exs \
  test/taskman_web/live/project_live/move_task_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs
```

Expected: all tests pass before moving socket and route orchestration.

- [ ] **Step 2: Move all six movement event families**

Move opening a Task, opening/searching/selecting destinations, cancellation, and submission into
`Movement.handle_event/3`, including malformed-payload fallbacks. Preserve row-versus-detail
origin detection and keep `@task_move` as its own top-level assign.

- [ ] **Step 3: Move movement refresh and flush coordination**

Move active-state refresh, destination refresh, detail autosave gating, selected-Task refresh,
active-row reinsertion, and success/error handling. Use `Editing.flush/1` for detail-origin
movement and `Editing.sync_persisted_task/2` after a successful selected-Task move. Use
`Listing.refresh/1` for stream changes.

- [ ] **Step 4: Delegate events and expose reconciliation helpers**

Add the movement event attribute and guarded root delegation. Implement `refresh/1` for open
destination surfaces, `reconcile/1` for external workspace changes without forcing options open,
and `clear/1` for modal transitions.

- [ ] **Step 5: Verify movement and editing interaction**

Run:

```sh
mix test test/taskman_web/live/project_live/tasks/move_test.exs \
  test/taskman_web/live/project_live/move_task_test.exs \
  test/taskman_web/live/project_live/autosave_test.exs \
  test/taskman_web/live/project_live/task_updates_test.exs
```

Expected: all tests pass, including failed flushes, stale destinations, row reinsertion, route
preservation, and external moves.

- [ ] **Step 6: Commit the movement extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive Task movement"
```

### Task 8: Complete workspace workflow extraction

**Files:**

- Modify: `lib/taskman_web/live/project_live/workspace.ex`
- Modify: `lib/taskman_web/live/project_live.ex`

**Interfaces:**

- Consumes: `Workspace.State`, `ListEdit`, `Listing`, `Movement`, Projects, Lists,
  ChangeNotifications, and the `:projects` and `:navigation_nodes` streams.
- Produces: final `handle_event/3`, `resolve_location/1`, `location_path/2`, `subscribe/1`,
  `refresh/1`, and `reconcile/2`.

- [ ] **Step 1: Record the focused Project/List baseline**

Run:

```sh
mix test test/taskman_web/live/project_live/workspace_test.exs \
  test/taskman_web/live/project_live/list_edit_test.exs \
  test/taskman_web/live/project_live/lists_test.exs \
  test/taskman_web/live/project_live/workspace_updates_test.exs
```

Expected: all tests pass before moving the remaining workflow functions.

- [ ] **Step 2: Move Project and navigation event handling**

Move Project validation/creation, navigation identity parsing and expansion, and Project form
construction into `Workspace`. Preserve invalid-node and foreign-node no-ops. Successful Project
creation still patches to the selected Project through `Paths.browse_path/3`.

Move Project/List route lookup to `resolve_location/1` and List breadcrumb construction to
`location_path/2`. The root route coordinator calls these APIs before ordering state assignment,
listing refresh, and modal action setup.

- [ ] **Step 3: Move List editing and downstream refreshes**

Move List form open/cancel/validate/save, action-Project resolution, parent/List resolution,
List-edit errors, and selected-path refresh. After a successful rename, call
`Listing.refresh/1` and `Movement.refresh/1` in the existing order. Keep all changeset and
not-found outcomes unchanged.

- [ ] **Step 4: Move navigation streams and subscriptions**

Move workspace snapshots, navigation-node streaming, selected-location identity, workspace
subscription, Project-specific subscription switching, and Project ID normalization. Store the
subscription ID through `Workspace.State.put_subscription/2`; preserve connected-socket checks and
unsubscribe-before-subscribe ordering.

- [ ] **Step 5: Make reconciliation return an explicit outcome**

Move canonical Project/List reconciliation into `Workspace.reconcile/2`. Return:

```elixir
{socket, :unchanged}
{socket, {:location_changed, task_lists}}
{socket, :location_missing}
```

The function updates only workspace-owned state and navigation/Project streams. It must not call
creation, listing, editing, parent-selection, or movement reconciliation; the top-level
`Reconciliation` module will order those downstream operations.

- [ ] **Step 6: Replace root handlers with workspace delegation**

Add the workspace event attribute and guarded delegation, initialize through `Workspace.State`,
call `Workspace.refresh/1` and `Workspace.subscribe/1` from `mount/3`, and remove the extracted
helpers from `ProjectLive`.

- [ ] **Step 7: Verify Project/List workflows**

Run:

```sh
mix test test/taskman_web/live/project_live/workspace_test.exs \
  test/taskman_web/live/project_live/list_edit_test.exs \
  test/taskman_web/live/project_live/lists_test.exs \
  test/taskman_web/live/project_live/workspace_updates_test.exs \
  test/taskman_web/live/project_live/external_updates_test.exs
```

Expected: all tests pass, including List rename refreshes, missing selected Lists, subscriptions,
and navigation expansion.

- [ ] **Step 8: Commit the workspace extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Extract ProjectLive workspace workflow"
```

### Task 9: Extract reconciliation and finish the coordinator

**Files:**

- Create: `lib/taskman_web/live/project_live/reconciliation.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `docs/handoffs/project-live-decomposition.md`
- Modify: `.beads/issues.jsonl` through `br`

**Interfaces:**

- Consumes: all completed workflow interfaces and well-formed `ChangeNotifications.Event`
  envelopes.
- Produces: `Reconciliation.handle_info/2` and the final thin `ProjectLive` coordinator.

- [ ] **Step 1: Record the external-update and coordinator baseline**

Run:

```sh
mix test test/taskman_web/live/project_live/external_updates_test.exs \
  test/taskman_web/live/project_live/task_updates_test.exs \
  test/taskman_web/live/project_live/workspace_updates_test.exs
```

Expected: all tests pass before moving `handle_info/2`.

- [ ] **Step 2: Move message validation and autosave dispatch**

Move all `handle_info/2` clauses, well-formed workspace/Task event predicates, and hierarchy-impact
classification into `Reconciliation`. Route scheduled autosaves to
`Editing.handle_autosave_info/2`. Keep irrelevant entities, malformed fields, stale project IDs,
and inactive detail messages as no-ops.

- [ ] **Step 3: Implement the preserved reconciliation order**

For Project/List events, call `Workspace.reconcile/2` first. For
`{:location_changed, task_lists}`, then call `Creation.refresh_location/2`,
`Listing.refresh/1`, `Movement.reconcile/1`, `ParentSelection.refresh/1`, and
`Editing.reload_hierarchy/1`; for `:location_missing`, clear creation/listing/movement state while
retaining the current not-found route behavior.

For Task events in the selected Project, call `Editing.reconcile/2`, then synchronize the parent
picker from the resulting `socket.assigns.editing.selected_task` when one remains selected:

```elixir
socket
|> Listing.refresh()
|> Editing.reconcile(event)
|> sync_parent_selection_from_editing()
|> Movement.reconcile()
```

Call `Editing.reload_hierarchy/1` only when the event operation or fields affect hierarchy, exactly
matching the current predicate.

- [ ] **Step 4: Reduce root callbacks to explicit dispatch**

Keep one `handle_event/3` clause per event-owner attribute and one `handle_info/2` delegation:

```elixir
def handle_event(event, params, socket) when event in @workspace_events,
  do: Workspace.handle_event(event, params, socket)

def handle_info(message, socket),
  do: Reconciliation.handle_info(message, socket)
```

Retain no unknown-event catch-all. Keep route resolution and cross-workflow action ordering in
`handle_params/3` and private route-composition functions only. Remove all duplicated extracted
functions and unused aliases.

- [ ] **Step 5: Run the complete focused web suite**

Run:

```sh
mix format --check-formatted
mix test test/taskman_web/live/project_live \
  test/taskman_web/components/tasks \
  test/taskman_web/components/workspace_navigation_test.exs
```

Expected: all focused tests pass with zero failures.

- [ ] **Step 6: Check structural and terminology contracts**

Run:

```sh
rg -n 'defp (refresh_task_stream|refresh_navigation_stream|flush_task_autosave|browse_path|new_task_path|task_detail_path|update_task_parent_picker|refresh_move_surface)' \
  lib/taskman_web/live/project_live.ex

rg -n '@(selected_project|subscribed_project_id|project_not_found|selected_list|include_children|visible_task_statuses|status_filter_form|task_status_filter_open|task_sort|location_not_found|location_path|project_form|task_create_form|task_create_enabled|task_create_location|tasks_empty|selected_task|task_not_found|task_detail_open|task_autosave|task_hierarchy|expanded_node_ids|list_edit|tasks_filtered_empty)' \
  lib/taskman_web/live/project_live.html.heex

rg -n 'Task [0-9]|phase|milestone|bead|ticket' \
  lib/taskman_web/live/project_live.ex \
  lib/taskman_web/live/project_live.html.heex \
  lib/taskman_web/live/project_live
```

Expected: all three searches return no matches.

- [ ] **Step 7: Run full repository verification**

Run:

```sh
mix precommit
```

Expected: formatting, compilation, static checks, and the complete test suite pass.

- [ ] **Step 8: Record completion in canonical state**

Update `tas-1tq` through `br` with the implemented module boundaries and fresh verification
evidence, then close it with a concise outcome. Retire
`docs/handoffs/project-live-decomposition.md` and remove its line from
`docs/handoffs/INDEX.md` because no continuation remains.

- [ ] **Step 9: Commit the final coordinator extraction**

Inspect `but diff`, confirm it contains only this task, then run:

```sh
but commit -b authenticated-hosted-access -m "Complete ProjectLive workflow decomposition"
```

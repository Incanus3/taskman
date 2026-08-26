# Lists and Nested Organization Implementation Plan

**Status:** Completed and archived — 2026-08-28

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add nested Lists, URL-backed Project/List browsing, descendant Task views, List-owned
Task creation, and explicit same-Project Task movement.

**Architecture:** `Taskman.Lists` owns the adjacency-list hierarchy, validation, paths, and
navigation projections. `Taskman.Tasks` owns Task location, recursive descendant queries, listed
Task projections, creation, and movement. `TaskmanWeb.ProjectLive` remains the route and interaction
orchestrator while focused stateless function components render navigation and movement.

**Tech Stack:** Elixir 1.17+, Phoenix 1.8, LiveView 1.2, HEEx, Ecto SQL 3.13, PostgreSQL 15+,
Tailwind CSS 4, ExUnit, LazyHTML, and Phoenix.LiveViewTest.

**Spec:** Read
[`docs/specs/2026-08-26-lists-nested-organization-design.md`](../../specs/2026-08-26-lists-nested-organization-design.md)
completely before implementation.

**Delivery work:**

- `tas-lists-nested-organization-8qe` — parent delivery feature.
- `tas-lists-nested-organization-8qe.1` — Task 1, TaskList persistence and Lists context.
- `tas-lists-nested-organization-8qe.2` — Task 2, Task locations, queries, and movement; blocked by
  `.1`.
- `tas-lists-nested-organization-8qe.3` — Task 3, canonical routes and Task views; blocked by `.2`.
- `tas-lists-nested-organization-8qe.4` — Task 4, streamed navigation and List forms; blocked by
  `.3`.
- `tas-lists-nested-organization-8qe.5` — Task 5, explicit movement UI; blocked by `.4`.
- `tas-lists-nested-organization-8qe.6` — Task 6, final verification and reconciliation; blocked by
  `.5`.

## Global Constraints

- Use `Taskman.Lists.TaskList` for the Elixir schema, `Taskman.Lists` for the context, and `lists`
  for the table; product copy remains **List**.
- Require PostgreSQL 15 or newer and use one case-insensitive sibling index with
  `NULLS NOT DISTINCT`.
- Keep `project_id`, `parent_list_id`, and `list_id` out of ordinary user-parameter `cast/3` calls;
  context functions set ownership fields.
- Enforce same-Project parentage and Task location with composite database foreign keys.
- Keep List parentage immutable; add no List deletion, reparenting, reordering, or movement API.
- Add no drag-and-drop, cross-Project Task movement, authentication, dependency, or stateful
  LiveComponent.
- Keep `TaskmanWeb` free of Repo calls and Ecto queries.
- Preserve existing Task editing, autosave, lifecycle, responsive detail, and recoverable
  not-found behavior.
- Use streams for Task and navigation collections, `to_form/2` for every form, and stable unique
  DOM IDs for tested interactions.
- Use `mix ecto.gen.migration add_lists_and_task_locations` to create the migration file.

---

## File Map

- The migration emitted by `mix ecto.gen.migration add_lists_and_task_locations` under
  `priv/repo/migrations/` — creates `lists`, both composite ownership constraints, the sibling-name
  index, and nullable `tasks.list_id`.
- `lib/taskman/lists/task_list.ex` — persisted TaskList schema and create/rename changeset.
- `lib/taskman/lists.ex` — Project-scoped List lookup, mutation, ordering, tree/path helpers, and
  navigation projection.
- `lib/taskman/lists/navigation_node.ex` — stable flattened tree item consumed by the navigation
  stream.
- `lib/taskman/tasks/task_with_location.ex` — Task plus its root-to-owner TaskList path.
- `lib/taskman/tasks.ex` and `lib/taskman/tasks/task.ex` — location-aware creation, recursive
  listing, explicit movement, and `list_id` persistence.
- `lib/taskman_web/components/workspace_navigation.ex` — semantic tree and List create/rename
  popovers.
- `lib/taskman_web/components/move_task.ex` — shared row/detail destination combobox and submit
  surface.
- `lib/taskman_web/components/task_components.ex` and
  `lib/taskman_web/components/task_detail.ex` — location display and move entry points.
- `lib/taskman_web/router.ex`, `lib/taskman_web/live/project_live.ex`, and
  `lib/taskman_web/live/project_live.html.heex` — canonical routes, URL state, streams, forms, and
  event orchestration.
- `test/support/fixtures/lists_fixtures.ex` and `test/support/fixtures/tasks_fixtures.ex` —
  context-backed fixtures.
- `test/taskman/lists_test.exs`, `test/taskman/tasks_test.exs`, and
  `test/taskman/lists_and_task_locations_migration_test.exs` — context and database invariants.
- `test/taskman_web/components/workspace_navigation_test.exs`,
  `test/taskman_web/components/move_task_test.exs`,
  `test/taskman_web/live/project_live_lists_test.exs`, and
  `test/taskman_web/live/project_live_move_task_test.exs` — focused component and interaction
  coverage.
- Existing ProjectLive and autosave tests — regression coverage and selector updates only where
  the product surface intentionally changes.
- `docs/planning/roadmap.md`, this plan, and the active handoff — delivery state and verification
  evidence.

---

### Task 1: Persist TaskLists and establish the Lists context

**Delivery issue:** `tas-lists-nested-organization-8qe.1`

**Files:**

- Create: generated `priv/repo/migrations/*_add_lists_and_task_locations.exs`
- Create: `lib/taskman/lists/task_list.ex`
- Create: `lib/taskman/lists/navigation_node.ex`
- Create: `lib/taskman/lists.ex`
- Create: `test/support/fixtures/lists_fixtures.ex`
- Create: `test/taskman/lists_test.exs`
- Create: `test/taskman/lists_and_task_locations_migration_test.exs`

**Interfaces:**

- Produces:
  - `Lists.list_lists_for_project(%Project{}) :: [TaskList.t()]`
  - `Lists.get_list_for_project(%Project{}, id) :: TaskList.t() | nil`
  - `Lists.create_list(%Project{}, TaskList.t() | nil, map())`
  - `Lists.change_list(TaskList.t(), map()) :: Ecto.Changeset.t()`
  - `Lists.rename_list(%Project{}, TaskList.t(), map())`
  - `Lists.path_for([TaskList.t()], TaskList.t() | nil) :: [TaskList.t()]`
  - `Lists.navigation_nodes([Project.t()], %{pos_integer() => [TaskList.t()]},
    selected_location, MapSet.t()) :: [NavigationNode.t()]`
- `selected_location` is `nil`, `{:project, project_id}`, or `{:list, list_id}`. Expanded node IDs
  use the same tagged tuples.
- Guarantees one ordered Project query for tree assembly, root-to-node paths, immutable ownership,
  and controlled `:not_found` returns for mismatched parents and renames.

- [x] **Step 1: Read the migration task help, generate the migration, and add failing tests**

Run:

```text
mix help ecto.gen.migration
mix ecto.gen.migration add_lists_and_task_locations
```

Add focused tests equivalent to:

```elixir
test "creates arbitrarily nested Lists and returns stable root-to-node paths" do
  project = project_fixture()
  root = list_fixture(project, nil, %{name: "Planning"})
  child = list_fixture(project, root, %{name: "Launch"})
  leaf = list_fixture(project, child, %{name: "Copy"})

  assert Lists.list_lists_for_project(project) == [root, child, leaf]
  assert Lists.path_for(Lists.list_lists_for_project(project), leaf) == [root, child, leaf]
end

test "rejects duplicate sibling names without regard to case" do
  project = project_fixture()
  _existing = list_fixture(project, nil, %{name: "Planning"})

  assert {:error, changeset} = Lists.create_list(project, nil, %{name: "planning"})
  assert %{name: [_]} = errors_on(changeset)
end

test "rejects a parent from another Project" do
  project = project_fixture()
  other_parent = list_fixture(project_fixture(), nil)

  assert {:error, :not_found} = Lists.create_list(project, other_parent, %{name: "Child"})
end
```

In the migration test, execute direct SQL inserts inside savepoints and assert PostgreSQL reports
`foreign_key_violation` for `(parent_list_id, project_id)` crossing Projects and
`unique_violation` for both duplicate root and duplicate nested sibling names differing only by
case.

- [x] **Step 2: Run the new tests and confirm the missing persistence boundary**

Run:

```text
mix test test/taskman/lists_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
```

Expected: compilation or relation failures because the schema, context, fixture, and migration are
not implemented.

- [x] **Step 3: Implement the migration constraints**

In the generated migration:

```elixir
create table(:lists) do
  add :project_id, references(:projects, on_delete: :delete_all), null: false
  add :parent_list_id, :bigint
  add :name, :string, null: false
  timestamps(type: :utc_datetime)
end

create unique_index(:lists, [:id, :project_id], name: :lists_id_project_id_index)

create unique_index(
         :lists,
         [:project_id, :parent_list_id, "lower(name)"],
         name: :lists_sibling_name_index,
         nulls_distinct: false
       )

alter table(:lists) do
  modify :parent_list_id,
         references(:lists,
           with: [project_id: :project_id],
           on_delete: :nothing,
           name: :lists_parent_list_id_project_id_fkey
         ),
         from: :bigint
end

alter table(:tasks) do
  add :list_id,
      references(:lists,
        with: [project_id: :project_id],
        on_delete: :nothing,
        name: :tasks_list_id_project_id_fkey
      )
end

create index(:tasks, [:project_id, :list_id])
```

Keep existing Task rows direct by leaving `list_id` nullable with no rewrite.

- [x] **Step 4: Implement TaskList validation and Project-scoped context functions**

Use a schema whose public changeset casts only `:name`, trims it, requires it, limits it to 255
characters, and maps `:lists_sibling_name_index` onto `:name`:

```elixir
def changeset(task_list, attrs) do
  task_list
  |> cast(attrs, [:name])
  |> update_change(:name, &String.trim/1)
  |> validate_required([:project_id, :name])
  |> validate_length(:name, max: 255)
  |> foreign_key_constraint(:project_id)
  |> foreign_key_constraint(:parent_list_id,
    name: :lists_parent_list_id_project_id_fkey
  )
  |> unique_constraint(:name, name: :lists_sibling_name_index)
end
```

Build List ownership before the changeset:

```elixir
def create_list(%Project{id: project_id}, nil, attrs) do
  %TaskList{project_id: project_id}
  |> TaskList.changeset(attrs)
  |> Repo.insert()
end

def create_list(
      %Project{id: project_id},
      %TaskList{project_id: project_id, id: parent_id},
      attrs
    ) do
  %TaskList{project_id: project_id, parent_list_id: parent_id}
  |> TaskList.changeset(attrs)
  |> Repo.insert()
end

def create_list(%Project{}, %TaskList{}, _attrs), do: {:error, :not_found}
```

Order by `inserted_at`, then `id`; parse IDs exactly like the existing Project-scoped Task lookup.
Build `path_for/2` from a single `id => TaskList` map, stopping at `nil`. Build a
`project.id => ordered TaskLists` map with one `list_lists_for_project/1` call per Project, then
build flattened `NavigationNode` structs depth-first; use stable identities
`"project-#{id}"` and `"list-#{id}"`, `depth` starting at 1, and force selected ancestors open
without changing the transient expansion set.

Define the projection explicitly:

```elixir
@enforce_keys [:dom_id, :kind, :depth, :project, :expanded?, :expandable?, :selected?]
defstruct [
  :dom_id,
  :kind,
  :depth,
  :project,
  :task_list,
  :expanded?,
  :expandable?,
  :selected?
]
```

`kind` is `:project` or `:list`; `task_list` is `nil` for Project nodes.

- [x] **Step 5: Verify the Lists boundary**

Run:

```text
mix format priv/repo/migrations lib/taskman/lists.ex lib/taskman/lists \
  test/support/fixtures/lists_fixtures.ex test/taskman/lists_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
mix test test/taskman/lists_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
```

Expected: all focused tests pass, including database-level cross-Project and case-insensitive
uniqueness assertions.

- [x] **Step 6: Review and commit Task 1**

Inspect the focused diff, verify no delete or parent-update API exists, then selectively commit only
Task 1 files with message:

```text
add nested List persistence and context
```

---

### Task 2: Add Task ownership projections, recursive queries, creation, and movement

**Delivery issue:** `tas-lists-nested-organization-8qe.2`

**Files:**

- Create: `lib/taskman/tasks/task_with_location.ex`
- Modify: `lib/taskman/tasks/task.ex`
- Modify: `lib/taskman/tasks.ex`
- Modify: `test/support/fixtures/tasks_fixtures.ex`
- Modify: `test/taskman/tasks_test.exs`
- Modify: `test/taskman/lists_and_task_locations_migration_test.exs`

**Interfaces:**

- Consumes Task 1's TaskList lookup, ordering, and path helpers.
- Produces:
  - `%TaskWithLocation{task: Task.t(), location_path: [TaskList.t()]}`
  - `Tasks.create_task(%Project{}, TaskList.t() | nil, map())`
  - `Tasks.list_tasks_for_location(%Project{}, TaskList.t() | nil,
    include_descendants: boolean())`
  - `Tasks.move_task(%Project{}, Task.t(), TaskList.t() | nil)`
- Keeps existing `Tasks.create_task(project, attrs)` as a direct-Project compatibility wrapper.

- [x] **Step 1: Write failing ownership, listing, and movement tests**

Cover direct creation, List creation, direct Project listing, direct List listing, Project
descendants, List descendants, full paths, stable Task order, valid moves, unchanged moves, stale
Tasks, and cross-Project destinations. Representative assertions:

```elixir
assert {:ok, listed} =
         Tasks.list_tasks_for_location(project, root, include_descendants: true)

assert Enum.map(listed, & &1.task.id) == [direct_in_root.id, nested.id]
assert Enum.map(hd(Enum.drop(listed, 1)).location_path, & &1.name) ==
         ["Planning", "Launch"]

assert {:ok, moved} = Tasks.move_task(project, task, destination)
assert moved.list_id == destination.id
assert {:error, :unchanged_location} = Tasks.move_task(project, moved, destination)
assert {:error, :not_found} = Tasks.move_task(project, moved, foreign_list)
```

Add a direct SQL assertion that the composite Task/List foreign key rejects a `list_id` belonging
to another Project. Assert `list_id` supplied in ordinary create/update attrs is ignored.

- [x] **Step 2: Run focused tests and verify they fail**

Run:

```text
mix test test/taskman/tasks_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
```

Expected: failures because Task has no `list_id`, `TaskWithLocation` does not exist, and the new
context arities are missing.

- [x] **Step 3: Add the Task schema field and listed projection**

Add `belongs_to :list, Taskman.Lists.TaskList` without adding `:list_id` to `cast/3`. Define one
module per file:

```elixir
defmodule Taskman.Tasks.TaskWithLocation do
  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.Task

  @enforce_keys [:task, :location_path]
  defstruct [:task, :location_path]

  @type t :: %__MODULE__{task: Task.t(), location_path: [TaskList.t()]}
end
```

Map the composite foreign-key constraint in the Task changeset:

```elixir
foreign_key_constraint(:list_id, name: :tasks_list_id_project_id_fkey)
```

- [x] **Step 4: Implement location-aware creation and movement**

Pattern-match both Project IDs before constructing a changeset:

```elixir
def create_task(%Project{id: project_id}, %TaskList{project_id: project_id, id: list_id}, attrs) do
  %Task{project_id: project_id, list_id: list_id}
  |> Task.changeset(attrs)
  |> Repo.insert()
end

def create_task(%Project{}, %TaskList{}, _attrs), do: {:error, :not_found}

def move_task(
      %Project{id: project_id},
      %Task{project_id: project_id} = task,
      %TaskList{project_id: project_id, id: destination_id}
    ) do
  persist_location_change(task, destination_id)
end
```

Provide the `nil` direct-Project clauses, reject mismatches with `:not_found`, and compare the
persisted `task.list_id` before `Ecto.Changeset.change(task, list_id: destination_id)`.

- [x] **Step 5: Implement recursive descendant listing**

Use an Ecto recursive CTE scoped by `project_id`. For a selected List, seed its ID; for a selected
Project, seed root Lists and union direct Project Tasks separately. The recursive member joins
`list.parent_list_id == parent.id`:

```elixir
seed =
  from task_list in TaskList,
    where:
      task_list.project_id == ^project_id and
        task_list.id in ^root_ids,
    select: %{id: task_list.id}

recursive_member =
  from task_list in TaskList,
    join: parent in "descendant_lists",
    on: task_list.parent_list_id == parent.id,
    where: task_list.project_id == ^project_id,
    select: %{id: task_list.id}

descendant_lists = union_all(seed, ^recursive_member)

Task
|> recursive_ctes(true)
|> with_cte("descendant_lists", as: ^descendant_lists)
|> where([task], task.project_id == ^project_id)
|> where(
  [task],
  task.list_id in subquery(from item in "descendant_lists", select: item.id)
)
```

For the Project case, OR the descendant predicate with `is_nil(task.list_id)`. Apply the resulting
IDs to a Task query, order all Tasks by `inserted_at` and `id`, then build `TaskWithLocation`
values from one ordered List query and its path map. Return `{:error, :not_found}` before querying
when a supplied TaskList belongs to another Project.

Keep this query in `Taskman.Tasks`; do not expose Ecto queryables to `TaskmanWeb`.

- [x] **Step 6: Verify and commit Task 2**

Run:

```text
mix format lib/taskman/tasks.ex lib/taskman/tasks \
  test/support/fixtures/tasks_fixtures.ex test/taskman/tasks_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
mix test test/taskman/tasks_test.exs test/taskman/lists_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs
```

Expected: all context and constraint tests pass. Selectively commit Task 2 files with:

```text
add Task location queries and movement
```

---

### Task 3: Introduce canonical Project/List routes and URL-backed Task views

**Delivery issue:** `tas-lists-nested-organization-8qe.3`

**Files:**

- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Create: `test/taskman_web/live/project_live_lists_test.exs`
- Modify: `test/taskman_web/live/project_live_test.exs`
- Modify: `test/taskman_web/live/project_live_autosave_test.exs`

**Interfaces:**

- Consumes `Tasks.list_tasks_for_location/3` and the Task 1 List lookups.
- Produces LiveView assigns `selected_list`, `include_children?`, `location_not_found?`,
  `location_path`, and a `TaskWithLocation` stream configured with DOM IDs derived from `task.id`.
- Produces canonical route helpers for the six approved Project/List routes while preserving only
  `include_children=true`.

- [x] **Step 1: Add failing route and browsing-state LiveView tests**

Test all List routes, malformed and cross-Project List IDs, direct versus descendant results, URL
canonicalization, history-preserving patches, List-owned creation, and Task detail over both
contexts. Use selectors such as:

```elixir
assert has_element?(view, "#location-heading", "Launch")
refute has_element?(view, "#task-#{nested_task.id}")

view |> element("#include-child-lists") |> render_click()
assert_patch(view, ~p"/projects/#{project.id}/lists/#{list.id}?include_children=true")
assert has_element?(view, "#task-#{nested_task.id}")

view |> element("#add-task") |> render_click()
assert_patch(
  view,
  ~p"/projects/#{project.id}/lists/#{list.id}/tasks/new?include_children=true"
)
```

Assert that `include_children=1` behaves as false and the next generated patch omits it. Assert a
valid Project sidebar remains present when `#location-not-found` renders.

- [x] **Step 2: Run the focused LiveView tests and confirm route failures**

Run:

```text
mix test test/taskman_web/live/project_live_lists_test.exs \
  test/taskman_web/live/project_live_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: new List-route tests fail because the router and location assigns do not exist; existing
tests remain useful regression evidence.

- [x] **Step 3: Add the three canonical List routes**

Under the existing browser scope add:

```elixir
live "/projects/:project_id/lists/:list_id", ProjectLive, :show
live "/projects/:project_id/lists/:list_id/tasks/new", ProjectLive, :new_task
live "/projects/:project_id/lists/:list_id/tasks/:task_id", ProjectLive, :show_task
```

- [x] **Step 4: Centralize route interpretation and URL generation**

Replace action-specific duplication with helpers that:

1. resolve Project, then optional Project-scoped List;
2. set `include_children?` only when the raw value is `"true"`;
3. query `TaskWithLocation` results for the resolved location;
4. preserve location and recognized query in new/detail/close/cancel patches; and
5. load Task detail by Project ownership, not background membership.

Configure the Task stream once in `mount/3`:

```elixir
stream_configure(socket, :tasks,
  dom_id: fn %TaskWithLocation{task: task} -> "tasks-#{task.id}" end
)
```

Use three exact helpers rather than hardcoding Project-only paths throughout event handlers:

```elixir
browse_path(project, task_list_or_nil, include_children?)
new_task_path(project, task_list_or_nil, include_children?)
task_detail_path(project, task_list_or_nil, task, include_children?)
```

Each helper emits `include_children=true` only when enabled. Update dirty-autosave restoration to
restore the exact `task_detail_path/4`.

- [x] **Step 5: Render truthful location state and location-aware creation**

Change the heading, Add Task link, empty state, modal copy, cancel path, detail open path, and
not-found surface based on the selected location. Add:

```heex
<.link
  id="include-child-lists"
  patch={browse_path(@selected_project, @selected_list, !@include_children?)}
  aria-pressed={to_string(@include_children?)}
>
  Include child Lists
</.link>
```

On save, call `Tasks.create_task(selected_project, selected_list, task_params)`, requery/reset the
stream, and patch back to the same browsing context.

- [x] **Step 6: Verify and commit Task 3**

Run:

```text
mix format lib/taskman_web/router.ex lib/taskman_web/live/project_live.ex \
  lib/taskman_web/live/project_live.html.heex test/taskman_web/live
mix test test/taskman_web/live/project_live_lists_test.exs \
  test/taskman_web/live/project_live_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: canonical URL, creation, recovery, Task detail, and autosave regression tests pass.
Selectively commit Task 3 files with:

```text
add URL-backed List Task views
```

---

### Task 4: Render streamed workspace navigation and List management popovers

**Delivery issue:** `tas-lists-nested-organization-8qe.4`

**Files:**

- Create: `lib/taskman_web/components/workspace_navigation.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Create: `test/taskman_web/components/workspace_navigation_test.exs`
- Modify: `test/taskman_web/live/project_live_lists_test.exs`

**Interfaces:**

- Consumes `Lists.navigation_nodes/3`, `Lists.create_list/3`, `Lists.rename_list/3`, and the
  canonical route helpers from Task 3.
- Produces `WorkspaceNavigation.tree/1`, the `:navigation_nodes` stream, transient
  `expanded_node_ids`, and one active List form identified by `{:new, parent_or_nil}` or
  `{:rename, list}`.

- [x] **Step 1: Write failing semantic component and interaction tests**

Render a deep flattened tree and assert `role="tree"`, `role="treeitem"`, `aria-level`,
`aria-current`, separately labelled selection/expansion/action controls, and no deletion control.
In LiveView tests:

- expand/collapse without changing the URL;
- ensure selected ancestors stay visible;
- create a root List and a child List;
- keep selection unchanged after creation;
- rename a List while preserving expansion and selection;
- keep popovers open with inline blank, overlong, and duplicate-name errors; and
- verify unique IDs including `#list-create-form-root`, `#list-create-form-<parent-id>`, and
  `#list-rename-form-<list-id>`.

- [x] **Step 2: Run focused tests and verify the missing component**

Run:

```text
mix test test/taskman_web/components/workspace_navigation_test.exs \
  test/taskman_web/live/project_live_lists_test.exs
```

Expected: component compilation and selector failures.

- [x] **Step 3: Implement the stateless navigation component**

Declare explicit attributes for stream items, selection, and active form state. Render the stream
parent with `id="workspace-tree"` and `phx-update="stream"`, and each supplied DOM ID on a
`role="treeitem"` wrapper. Selection links use canonical patches and `aria-current="page"`;
expansion buttons send `"toggle_navigation_node"` and expose `aria-expanded`; actions send
`"open_list_form"`. Render forms with `<.form for={@list_form}>` and the existing `<.input>`.

Do not enumerate a stream or introduce nested assigned collections.

- [x] **Step 4: Add navigation stream and List form orchestration**

Initialize `expanded_node_ids`, `active_list_form`, and `list_form`; reset
`:navigation_nodes` whenever Projects, Lists, selection, expansion, creation, or rename changes.
Use these events:

```text
toggle_navigation_node
open_list_form
cancel_list_form
validate_list
save_list
```

Resolve every submitted parent/List ID through `Lists.get_list_for_project/2`. On successful
creation, add the parent identity to the expansion set but do not navigate. On successful rename,
requery Lists and reset navigation and Task streams so every derived path updates; if a move
popover is open, rebuild its destination labels from the same refreshed Lists. Keep the changeset
form and local error open on validation or uniqueness failure.

- [x] **Step 5: Verify and commit Task 4**

Run:

```text
mix format lib/taskman_web/components/workspace_navigation.ex \
  lib/taskman_web/live/project_live.ex lib/taskman_web/live/project_live.html.heex \
  test/taskman_web/components/workspace_navigation_test.exs \
  test/taskman_web/live/project_live_lists_test.exs
mix test test/taskman_web/components/workspace_navigation_test.exs \
  test/taskman_web/live/project_live_lists_test.exs
```

Expected: semantic navigation and all List management tests pass. Selectively commit with:

```text
add streamed List workspace navigation
```

---

### Task 5: Add location-aware Task rows and explicit MoveTask interaction

**Delivery issue:** `tas-lists-nested-organization-8qe.5`

**Files:**

- Create: `lib/taskman_web/components/move_task.ex`
- Modify: `lib/taskman_web/components/task_components.ex`
- Modify: `lib/taskman_web/components/task_detail.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Create: `test/taskman_web/components/move_task_test.exs`
- Create: `test/taskman_web/live/project_live_move_task_test.exs`
- Modify: `test/taskman_web/live/project_live_autosave_test.exs`

**Interfaces:**

- Consumes `TaskWithLocation`, List paths, `Tasks.move_task/3`, and location-aware stream refresh.
- Produces `MoveTask.popover/1` and one transient move state:
  `active_move_task`, `move_query`, `move_destination`, `move_options`, and `move_error`.
- Uses server events `open_move_task`, `search_move_destinations`, `select_move_destination`,
  `submit_move_task`, and `cancel_move_task`.

- [x] **Step 1: Write failing component contract tests**

Render the component with Project and deep List destinations. Assert a labelled combobox, full path
labels, current-location indication, no-results state, explicit cancel, local error region, and a
submit button disabled until a different destination is selected. Use IDs:

```text
move-task-<task-id>
move-task-search-<task-id>
move-task-options-<task-id>
move-task-option-project-<project-id>
move-task-option-list-<list-id>
move-task-submit-<task-id>
move-task-error-<task-id>
```

- [x] **Step 2: Write failing row/detail movement LiveView tests**

Cover case-insensitive substring search over complete paths, stable tree ordering, row-initiated
movement, detail-initiated movement, unchanged/stale/cross-Project rejection, one open surface,
cancel clearing transient state, row disappearance, retained descendant row path updates, and
detail staying open after a move out of the result set.

Add an autosave regression that enters an invalid blank title, opens movement from detail, submits
a valid destination, and asserts both that the Task location is unchanged and the Task save error
remains visible.

- [x] **Step 3: Run focused tests and verify missing behavior**

Run:

```text
mix test test/taskman_web/components/move_task_test.exs \
  test/taskman_web/live/project_live_move_task_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: failures because the component, row/detail actions, and movement events do not exist.

- [x] **Step 4: Render location data and the reusable MoveTask component**

Change `TaskComponents.row/1` to consume `task_with_location`, canonical `task_path`, and movement
assigns.
When descendants are enabled, render **Project** for an empty path or join List names with
`" / "`. Add a separately clickable **Move Task** button above the row overlay.

Add the same entry point to `TaskDetail.detail/1`. Keep editing inside the existing Task-detail
component. `MoveTask.popover/1` renders filtered options passed by ProjectLive, ordinary LiveView
events, `phx-click-away`, and keyboard dismissal; add no hook unless browser acceptance proves
focus cannot be made accessible with these primitives.

- [x] **Step 5: Implement searchable transient state and explicit submission**

Build destination values as `"project"` and `"list:#{id}"`; never convert user input to atoms.
Filter with:

```elixir
String.contains?(
  String.downcase(option.label),
  String.downcase(socket.assigns.move_query)
)
```

On submit, re-fetch the Task through `Tasks.get_task_for_project/2`, re-fetch a List destination
through `Lists.get_list_for_project/2`, and call `Tasks.move_task/3`. For detail-originated moves,
call `flush_dirty_task_fields/1` first and stop on `{:error, socket}`. On success:

1. re-fetch the selected Task when detail is open;
2. re-query and reset the entire Task stream;
3. keep selected Project/List and `include_children?`;
4. clear move state; and
5. keep the current detail route, even if the Task no longer belongs to the background result.

Map `:unchanged_location`, `:not_found`, changeset failures, and autosave failures to specific local
messages while keeping the popover open and persistence unchanged.

- [x] **Step 6: Verify and commit Task 5**

Run:

```text
mix format lib/taskman_web/components/move_task.ex \
  lib/taskman_web/components/task_components.ex lib/taskman_web/components/task_detail.ex \
  lib/taskman_web/live/project_live.ex lib/taskman_web/live/project_live.html.heex \
  test/taskman_web/components/move_task_test.exs \
  test/taskman_web/live/project_live_move_task_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
mix test test/taskman_web/components/move_task_test.exs \
  test/taskman_web/live/project_live_move_task_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: component, row/detail movement, stream refresh, error, and autosave-blocking tests pass.
Selectively commit with:

```text
add explicit same-Project Task movement
```

---

### Task 6: Reconcile regressions, run acceptance, and close the slice

**Delivery issue:** `tas-lists-nested-organization-8qe.6`

**Files:**

- Modify only as failures require: existing focused source and test files from Tasks 1–5
- Modify: `docs/planning/roadmap.md`
- Modify: `docs/archive/plans/2026-08-26-lists-nested-organization.md`
- Modify: `docs/handoffs/lists-nested-organization.md`
- Modify: `docs/handoffs/INDEX.md`
- Mutate through `br`: repository-local delivery issues created after plan approval

**Interfaces:**

- Consumes the complete approved specification and Tasks 1–5.
- Produces verified responsive behavior, a completed roadmap slice, closed Beads with evidence, and
  retired handoff state.

- [x] **Step 1: Run all focused tests together**

Run:

```text
mix test test/taskman/lists_test.exs \
  test/taskman/tasks_test.exs \
  test/taskman/lists_and_task_locations_migration_test.exs \
  test/taskman_web/components/workspace_navigation_test.exs \
  test/taskman_web/components/move_task_test.exs \
  test/taskman_web/live/project_live_lists_test.exs \
  test/taskman_web/live/project_live_move_task_test.exs \
  test/taskman_web/live/project_live_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: all focused tests pass with no unexpected warnings.

- [x] **Step 2: Run structural scope checks**

Run:

```text
rg -n "Taskman\\.Repo|Ecto\\.Query" lib/taskman_web
rg -n "delete_list|Delete List|move_list|reparent|drag" lib test
rg -n "slice 3|bead|milestone|implementation phase" lib test
```

Expected: the first and third searches return no matches; the second returns only explicit
absence assertions or unrelated established wording, with no List delete/reparent/drag product
surface.

- [x] **Step 3: Compile assets and run the repository gate**

Run:

```text
mix assets.build
mix precommit
```

Expected: both commands exit 0. Investigate and fix only regressions attributable to this slice;
do not broaden into unrelated cleanup.

- [x] **Step 4: Perform responsive browser acceptance**

Start the app using the repository's documented local PostgreSQL and Phoenix workflow. At wide and
narrow viewports verify:

- deep tree expansion, selected ancestors, long labels, and semantic keyboard focus;
- root/child create and rename success plus inline duplicate-name failure;
- Project/List direct and descendant tables, Location labels, empty states, and refreshed URLs;
- Task creation in each selected location;
- move search, no results, unchanged destination, cancel, row disappearance, retained-row path
  update, and open detail retention;
- dirty invalid Task detail blocking movement;
- malformed and cross-Project List/Task recovery; and
- existing responsive Task-detail hierarchy and autosave behavior.

Record concrete pass/fail evidence in this plan's status section rather than relying on screenshots
alone.

- [x] **Step 5: Obtain independent implementation verification**

Use the repository's review workflow to compare the complete implementation directly against the
approved specification. Resolve every correctness, security, architecture, or scope finding, then
rerun the affected focused tests and `mix precommit`.

- [x] **Step 6: Reconcile durable delivery state**

Mark the plan completed with the exact test, asset, precommit, browser, and review evidence. Update
the roadmap to mark nested organization delivered and identify the next roadmap slice. Close the
associated Beads through `br`, verify each with `br show`, then run:

```text
br sync --status
```

Retire `docs/handoffs/lists-nested-organization.md` and its index entry because nothing remains to
resume.

- [x] **Step 7: Commit only final reconciliation**

Inspect the focused documentation and Beads export diff, then selectively commit it with:

```text
complete nested List organization slice
```

Do not push, merge, publish, or otherwise alter shared state without separate operator
authorization.

### Task 6 completion record

All checklist steps in Tasks 1–6 are complete. The final verification evidence is:

- The combined focused command from Step 1 passed 105 tests. The final exact-source gate
  `ERL_FLAGS='+S 4' mix precommit` exited 0 with 124 tests and no database warnings.
- Step 2 boundary searches found no `Taskman.Repo`/`Ecto.Query`, List deletion/reparenting/drag,
  or planning-terminology matches in `lib/taskman_web`, `lib`, or `test` as applicable. The final
  planning-terminology search was rerun before reconciliation.
- `mix assets.build` exited 0 with Tailwind v4.3.0, daisyUI 5.5.20, and esbuild output.
- Wide browser acceptance passed four-level hierarchy expansion, canonical List routes, direct and
  descendant Task tables with full Location paths, create/rename and duplicate validation, Task
  creation, move search/current/unchanged/cancel/error behavior, row/detail retention, malformed
  and cross-Project recovery, and existing Task-detail/autosave behavior. The isolated acceptance
  server and `taskman_testacceptance` database were stopped and dropped.
- Raw narrow acceptance measured `390x844`. An initial `scrollWidth=412` exposed base-grid
  min-content overflow; `cde55d6d24f07faf7f7f113a5415ae560ce2282b` added
  `grid-cols-[minmax(0,1fr)]`, after which measured `width=390`, `height=844`, and
  `scrollWidth=377` showed no horizontal overflow. Descendant Location cells/full paths, child
  duplicate error retention, move-popover current marker and disabled submit, and Task detail
  `/projects/1/lists/3/tasks/4` were operable at that viewport. Earlier iPhone emulation reported
  `390x844` while page-context dimensions were `1125x1622`; raw measurement is the responsive
  acceptance evidence.
- Whole-branch review of `7c8028b0550f880d8b9fa56bd852ca9fbf6d4247` through
  `0c979baa4a55cd358440015859520d01e9a81375` returned With fixes. Commit
  `d61257d9230184d702e49017ecfa776c4724011e` addressed ancestor treeitem click propagation,
  stale move refreshes, and the missing Location column, plus exact `Project · name`, valid
  dirty-save-before-move, and cancel/reopen state tests. The measured browser rerun and
  `cde55d6d24f07faf7f7f113a5415ae560ce2282b` then resolved the narrow acceptance finding.
  Scoped re-review was APPROVED with no new breakage; seven affected suites passed 77 tests.
- Child-duplicate LiveView coverage was added in `0c979baa4a55cd358440015859520d01e9a81375` and
  passed its focused line test. The earlier acceptance-harness connection and sandbox limits were
  isolated as environment resource constraints, not application regressions.
- The local development database marked migration
  `20260826162407_add_lists_and_task_locations` applied while its generated file was still empty.
  Fresh test and acceptance databases validate the committed migration. No destructive local
  database repair was authorized or attempted; reconcile that local database before relying on it
  for development data involving Lists or Task locations.
- The parent and Task 6 Beads issues were closed with bounded verification evidence, the roadmap
  marks this slice delivered with Task relationships as the next MVP slice, and the completed
  handoff was retired from `docs/handoffs/` and its index.

---

## Execution Boundary (historical)

This plan was approved and its delivery Beads existed. Before implementation:

1. Resume from the active handoff in a clean session.
2. Claim the parent feature and Task 1 delivery issue.
3. Default to `subagent-driven-development` after resumption unless the operator explicitly chooses
   another execution approach.

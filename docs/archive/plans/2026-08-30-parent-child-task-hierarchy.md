# Parent-Child Task Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver safe parent-child Task hierarchy across persistence, browser, API, CLI, help,
completions, onboarding, and the bundled agent skill.

**Architecture:** Store each Task's optional immediate parent as a composite same-Project
self-reference. `Taskman.Tasks` remains the public persistence boundary and delegates recursive
query/projection work to focused hierarchy modules. LiveView composes dedicated parent-picker and
tree-expansion state while API and CLI remain equal adapters over the same context operations.

**Tech Stack:** Elixir, Ecto, PostgreSQL recursive CTEs and row locks, Phoenix LiveView/HEEx,
Tailwind CSS, Req, ExUnit, LazyHTML.

**Spec:** `docs/specs/2026-08-30-parent-child-task-hierarchy-design.md`

**Status:** Completed and archived — 2026-08-31

## Global Constraints

- Read the complete approved specification before implementing any task.
- Use `but` for version-control writes; never run `jj` or raw Git write commands.
- Generate the migration with `mix ecto.gen.migration add_parent_task_hierarchy`; do not invent its
  timestamp.
- Keep `parent_task_id` out of the ordinary Task `cast/3` list; resolve parent identity before
  programmatically changing the foreign key.
- Keep persistence and Ecto queries out of `TaskmanWeb`.
- Parent-child is same-Project, acyclic, single-parent, List-ownership-independent, and
  lifecycle-independent.
- Do not add Blocks, Relates to, deletion behavior, drag-and-drop, bulk operations, or a generalized
  relationship table.
- Every meaningful browser operation/query ships with API, CLI, help, Bash/Fish completion, bundled
  skill, and focused verification parity.
- Use LiveView structural IDs and selector-based tests; do not assert styling details.
- Use `start_supervised!/1` for supervised test processes and monitors or `_ = :sys.get_state/1`
  instead of sleeps.
- Run the focused tests named by each task before committing that task.

---

## File and interface map

### Domain

- Generated migration: nullable self-reference, composite key/FK, check constraint, lookup index.
- `lib/taskman/tasks/task.ex`: schema association and constraint mappings only.
- `lib/taskman/tasks.ex`: public create/update options, transaction/Project-lock coordination,
  candidate search, and hierarchy facade.
- `lib/taskman/tasks/hierarchy.ex`: recursive ancestor/descendant queries, cycle check, candidate
  query, connected-tree assembly, and `%Taskman.Tasks.Hierarchy{}`.
- `lib/taskman/tasks/hierarchy_node.ex`: `%Taskman.Tasks.HierarchyNode{task, location_path,
  children}`.

The public signatures are:

```elixir
@spec create_task(Project.t(), TaskList.t() | nil, map(), keyword()) ::
        {:ok, Task.t()} | {:error, Ecto.Changeset.t() | :not_found}
def create_task(project, location, attrs, opts)

@spec update_task(Project.t(), Task.t(), map(), keyword()) ::
        {:ok, Task.t()} | {:error, Ecto.Changeset.t() | :not_found}
def update_task(project, task, attrs, opts)

@spec search_parent_candidates(Project.t(), Task.t() | nil, String.t(), keyword()) ::
        [TaskWithLocation.t()]
def search_parent_candidates(project, current_task, query, opts \\ [])

@spec get_task_hierarchy(Project.t(), Task.t()) ::
        {:ok, Hierarchy.t()} | {:error, :not_found}
def get_task_hierarchy(project, task)
```

Existing create/update arities delegate to these functions. Create defaults to `parent: nil`;
update omits the option to mean unchanged. `parent: nil` clears on update and
`parent: %Task{}` sets/replaces.

### Web

- `lib/taskman_web/live/task_parent_picker.ex`: picker state and domain-operation orchestration.
- `lib/taskman_web/components/task_parent_picker.ex`: accessible shared combobox/listbox.
- `lib/taskman_web/live/task_hierarchy.ex`: modal/connected-tree expansion state.
- `lib/taskman_web/components/task_form.ex`: composes the picker in create and edit modes.
- `lib/taskman_web/components/task_detail.ex`: renders the hierarchy projection recursively.
- `lib/taskman_web/components/task_components.ex`: Add subtask row action.
- `lib/taskman_web/live/project_live.ex` and `.html.heex`: route/event coordination only.

The picker state exposes:

```elixir
empty()
open_create(state, project, selected_parent)
open_edit(state, project, task)
open_options(state, project)
search(state, project, query)
select_draft(state, project, parent_id)
clear_draft(state)
reject_draft(state, message)
save_edit(state, project, task)
selected_parent(state)
```

The shared picker component emits exactly:

```text
open_task_parent_options
search_task_parents
select_task_parent
clear_task_parent
```

`ProjectLive` dispatches those events according to `live_action` (`:new_task` or `:show_task`).

The hierarchy state exposes:

```elixir
empty()
load(state, %Taskman.Tasks.Hierarchy{})
toggle(state, task_id)
expanded?(state, task_id)
clear(state)
```

Hierarchy disclosures emit `toggle_task_hierarchy_node` with `task-id`.

### API and CLI

- Task JSON gains nullable `parent_task_id`.
- Create/update accept `parent_task_id`; update omission means unchanged and JSON `null` clears.
- `GET /api/v1/projects/:project_id/tasks/:task_id/hierarchy` returns
  `%{selected_task_id: id, root: nested_node}`.
- CLI create gains `--parent`; update gains mutually exclusive `--parent`/`--no-parent`;
  `tasks hierarchy` renders the connected tree.

---

### Task 1: Persist parentage and enforce atomic hierarchy mutations

**Files:**

- Create: migration emitted by `mix ecto.gen.migration add_parent_task_hierarchy`
- Create: `lib/taskman/tasks/hierarchy.ex`
- Create: `test/taskman/parent_task_hierarchy_migration_test.exs`
- Modify: `lib/taskman/tasks/task.ex`
- Modify: `lib/taskman/tasks.ex`
- Modify: `test/taskman/tasks_test.exs`
- Modify: `test/support/fixtures/tasks_fixtures.ex`

**Interfaces:**

- Consumes: existing `Tasks.create_task/2-3`, `Tasks.update_task/3`, Project/List ownership rules.
- Produces: the four-argument create/update functions from the file map and persisted
  `Task.parent_task_id`; internal
  `Taskman.Tasks.Hierarchy.validate_parent/3 :: :ok | {:error, :cycle}`.

- [ ] **Step 1: Write failing migration and context tests**

Add tests that directly establish the database and public-context contract:

```elixir
test "database enforces same-Project non-self parentage" do
  project = project_fixture(%{})
  other_project = project_fixture(%{})
  task = task_fixture(project, %{})
  parent = task_fixture(project, %{})
  foreign = task_fixture(other_project, %{})

  assert {:ok, child} = Tasks.update_task(project, task, %{}, parent: parent)
  assert child.parent_task_id == parent.id

  foreign_changeset =
    task
    |> Task.changeset(%{})
    |> Ecto.Changeset.put_change(:parent_task_id, foreign.id)

  assert {:error, foreign_changeset} = Repo.update(foreign_changeset)
  assert "does not exist" in errors_on(foreign_changeset).parent_task_id

  self_changeset =
    task
    |> Task.changeset(%{})
    |> Ecto.Changeset.put_change(:parent_task_id, task.id)

  assert {:error, self_changeset} = Repo.update(self_changeset)
  assert "cannot be its own parent" in errors_on(self_changeset).parent_task_id
end
```

Add migration cases for nullable `parent_task_id`, both named indexes, and `ON DELETE NO ACTION`.
Add context cases for create with parent, update omission, replace, clear, idempotent set/clear,
foreign parent, unchanged List ownership, combined ordinary-field atomicity, direct self-parent,
deep cycles, and two concurrent opposite reparent attempts.

- [ ] **Step 2: Run the tests and confirm the missing schema/arity failures**

Run:

```bash
ERL_FLAGS='+S 4' mix test test/taskman/parent_task_hierarchy_migration_test.exs test/taskman/tasks_test.exs
```

Expected: FAIL because `parent_task_id` and four-argument create/update do not exist.

- [ ] **Step 3: Generate and implement the migration**

First run:

```bash
mix help ecto.gen.migration
mix ecto.gen.migration add_parent_task_hierarchy
```

Implement the generated migration with these exact objects:

```elixir
create unique_index(:tasks, [:id, :project_id], name: :tasks_id_project_id_index)

alter table(:tasks) do
  add :parent_task_id, :bigint
end

create constraint(:tasks, :tasks_parent_not_self_check,
  check: "parent_task_id IS NULL OR parent_task_id <> id"
)

alter table(:tasks) do
  modify :parent_task_id,
         references(:tasks,
           with: [project_id: :project_id],
           on_delete: :nothing,
           name: :tasks_parent_task_id_project_id_fkey
         ),
         from: :bigint
end

create index(:tasks, [:project_id, :parent_task_id],
  name: :tasks_project_id_parent_task_id_index
)
```

- [ ] **Step 4: Add the schema association without widening generic casting**

Add `belongs_to :parent_task, __MODULE__` and type information to `Task`. Map the composite foreign
key and self check in the changeset, but leave `parent_task_id` out of `cast/3`. Use field messages
`does not exist` for `tasks_parent_task_id_project_id_fkey` and `cannot be its own parent` for
`tasks_parent_not_self_check`, matching the tests.

- [ ] **Step 5: Implement atomic create/update parent options**

Keep old arities as delegators. When `:parent` is present, wrap mutation in `Repo.transaction/1`,
lock the owning Project row with `FOR UPDATE`, reload scoped Task/parent records, validate, then
programmatically apply:

```elixir
changeset
|> Ecto.Changeset.put_change(:parent_task_id, parent && parent.id)
|> Repo.update()
```

Implement `Hierarchy.validate_parent/3` with a recursive CTE that walks the proposed parent's
ancestor chain and stops at a root or the Task. Return a changeset error:

```elixir
Ecto.Changeset.add_error(changeset, :parent_task_id, "would create a cycle")
```

Return `{:error, :not_found}` for stale/foreign operands. Ensure a mixed title/parent update uses
one changeset and transaction.

- [ ] **Step 6: Run focused tests**

Run the command from Step 2 until all focused migration/context tests pass.

- [ ] **Step 7: Commit only Task 1 files**

Run `but diff`, copy the exact IDs for the files listed in Task 1, and commit them on
`parent-child-task-hierarchy` with message:

```text
Persist safe parent-child Task relationships
```

---

### Task 2: Add hierarchy projection and parent-candidate queries

**Files:**

- Modify: `lib/taskman/tasks/hierarchy.ex`
- Create: `lib/taskman/tasks/hierarchy_node.ex`
- Create: `test/taskman/tasks/hierarchy_test.exs`
- Modify: `lib/taskman/tasks.ex`
- Modify: `test/taskman/tasks_test.exs`

**Interfaces:**

- Consumes: persisted `parent_task_id`, Project-scoped Tasks, `Lists.path_for/2`.
- Produces: `Tasks.search_parent_candidates/4`, `Tasks.get_task_hierarchy/2`,
  `%Hierarchy{selected_task_id, root}`, and nested `%HierarchyNode{}`.

- [ ] **Step 1: Write failing hierarchy and search tests**

Cover empty query limit/order, case-insensitive title search, exact positive-ID priority,
Project scoping, location paths, current Task/descendant exclusions, disconnected Tasks, topmost
root, full connected tree, and sibling order.

Use an assertion shaped like:

```elixir
assert {:ok,
        %Hierarchy{
          selected_task_id: ^child_id,
          root: %HierarchyNode{
            task: %{id: ^root_id},
            children: [
              %HierarchyNode{task: %{id: ^child_id}, location_path: [^planning]}
            ]
          }
        }} = Tasks.get_task_hierarchy(project, child)
```

- [ ] **Step 2: Run the focused tests and confirm missing-module failures**

```bash
ERL_FLAGS='+S 4' mix test test/taskman/tasks/hierarchy_test.exs test/taskman/tasks_test.exs
```

Expected: FAIL because hierarchy structs and public functions do not exist.

- [ ] **Step 3: Implement recursive query/projection modules**

Use recursive CTEs to:

- walk selected Task ancestors to the topmost root;
- walk descendants from that root for the complete connected component;
- calculate descendants of the edited Task for candidate exclusion; and
- keep all predicates Project-scoped.

Build `children_by_parent`, recursively create `%HierarchyNode{}`, and preserve sibling order by
`inserted_at`, then `id`. Attach full List paths without exposing Ecto queries to web code.

- [ ] **Step 4: Implement candidate search**

Normalize with `String.trim/1`. An empty query returns the first 20 eligible Tasks. A positive
integer exact ID match is first; remaining title matches are case-insensitive and stable. Apply
`Keyword.get(opts, :limit, 20)`. Return `TaskWithLocation` values.

- [ ] **Step 5: Run focused tests**

Run the Step 2 command until all hierarchy/search tests pass.

- [ ] **Step 6: Commit only Task 2 files**

Use `but diff` IDs for Task 2 and commit with:

```text
Query Task hierarchies and parent candidates
```

---

### Task 3: Extend the JSON API contract

**Files:**

- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/controllers/api/task_controller.ex`
- Modify: `lib/taskman_web/controllers/api/representation.ex`
- Modify: `test/taskman_web/controllers/api/task_controller_test.exs`

**Interfaces:**

- Consumes: Task 1 parent-aware mutations and Task 2 hierarchy projection.
- Produces: Task JSON `parent_task_id`, create/update parent semantics, hierarchy endpoint.

- [ ] **Step 1: Write failing API tests**

Add tests for:

```elixir
post(conn, ~p"/api/v1/projects/#{project.id}/tasks",
  task: %{title: "Child", parent_task_id: parent.id}
)

patch(conn, ~p"/api/v1/projects/#{project.id}/tasks/#{task.id}",
  task: %{title: "Atomic", parent_task_id: parent.id}
)

get(conn, ~p"/api/v1/projects/#{project.id}/tasks/#{task.id}/hierarchy")
```

Assert omission/positive/null behavior, every Task representation containing `parent_task_id`,
nested hierarchy shape/order, malformed `400`, stale/foreign `404`, cycle/self `422` with
`fields.parent_task_id`, and combined-update atomicity.

- [ ] **Step 2: Run the controller tests and confirm contract failures**

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/controllers/api/task_controller_test.exs
```

- [ ] **Step 3: Implement parent resolution and hierarchy serialization**

In create/update, pop `parent_task_id` before ordinary attrs. Resolve positive IDs through
`Tasks.get_task_for_project/2`; treat update omission as unchanged, `nil` as clear, and create
omission/`nil` as root. Pass resolved `%Task{}`/`nil` through context options.

Add:

```elixir
get "/projects/:project_id/tasks/:task_id/hierarchy", TaskController, :hierarchy
```

Serialize each hierarchy node as `%{task: Representation.task(...), children: [...]}` and use the
standard `%{data: value}` envelope.

- [ ] **Step 4: Run focused API tests**

Run Step 2 until passing, then run:

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/controllers/api
```

- [ ] **Step 5: Commit only Task 3 files**

Commit the Task 3 diff with:

```text
Expose Task hierarchy through the JSON API
```

---

### Task 4: Add complete CLI and bundled-skill parity

**Files:**

- Modify: `lib/taskman/cli/registry.ex`
- Modify: `lib/taskman/cli/commands/tasks.ex`
- Modify: `lib/taskman/cli/client.ex`
- Modify: `lib/taskman/cli/output.ex`
- Modify: `lib/taskman/cli/onboarding.ex`
- Modify: `priv/taskman_cli_skill/SKILL.md`
- Modify: `test/taskman/cli/parser_test.exs`
- Modify: `test/taskman/cli/commands/tasks_test.exs`
- Modify: `test/taskman/cli/client_test.exs`
- Modify: `test/taskman/cli/output_test.exs`
- Modify: `test/taskman/cli/help_test.exs`
- Modify: `test/taskman/cli/completions_test.exs`
- Modify: `test/taskman/cli/onboarding_test.exs`
- Modify: `test/taskman/cli/end_to_end_test.exs`
- Modify: `test/taskman/cli/skill/bundle_test.exs`

**Interfaces:**

- Consumes: Task 3 HTTP contract.
- Produces: `--parent`, `--no-parent`, `tasks hierarchy`, recursive response validation, readable
  tree output, distribution documentation parity.

- [ ] **Step 1: Write failing registry/parser/help/completion tests**

Require:

```text
taskman tasks create --project 7 --title Child --parent 42
taskman tasks update --project 7 51 --parent 42
taskman tasks update --project 7 51 --no-parent
taskman tasks hierarchy --project 7 51
```

Assert `--parent`/`--no-parent` mutual exclusion, either satisfying update's at-least-one
constraint, exact help usage, and Bash/Fish completion coverage.

- [ ] **Step 2: Write failing handler/client/output/end-to-end tests**

Assert request bodies contain `parent_task_id`, hierarchy uses the exact endpoint, Task response
validation requires nullable positive `parent_task_id`, hierarchy validation recursively checks
`task` and `children`, readable collections add `PARENT`, and tree output marks `[selected]`.

- [ ] **Step 3: Run focused CLI tests and confirm failures**

```bash
ERL_FLAGS='+S 4' mix test test/taskman/cli test/taskman/cli_test.exs
```

- [ ] **Step 4: Implement registry, request, validation, and rendering**

Add `:hierarchy` dispatch in `Taskman.CLI.Commands.Tasks`. Add a hierarchy success shape to
`Client`, with recursive node validation. Ensure `Output.success/3` detects the hierarchy command
before generic map rendering and emits deterministic indentation without relying on color.

- [ ] **Step 5: Update onboarding and bundled skill**

Add one parent-update and one hierarchy-inspection example. Document exact ID scoping, inspection
before mutation, `--parent`, `--no-parent`, and `tasks hierarchy`. Regenerate no checked-in
artifact manually; keep bundle tests authoritative.

- [ ] **Step 6: Run all focused CLI tests**

Run Step 3 until passing.

- [ ] **Step 7: Commit only Task 4 files**

Commit with:

```text
Add parent-child hierarchy CLI parity
```

---

### Task 5: Build reusable parent-picker state and component

**Files:**

- Create: `lib/taskman_web/live/task_parent_picker.ex`
- Create: `lib/taskman_web/components/task_parent_picker.ex`
- Create: `test/taskman_web/live/task_parent_picker_test.exs`
- Create: `test/taskman_web/components/task_parent_picker_test.exs`

**Interfaces:**

- Consumes: Task 2 candidate search and Task 1 parent-aware update.
- Produces: picker state/component and the API named in the file map.

- [ ] **Step 1: Write failing state tests**

Test create/edit initialization, empty/exact/title search, open/close, selection, No parent,
candidate exclusions, successful edit save, stale/cycle failure, and draft-preserving error state.

The failure assertion must require:

```elixir
assert failed.options_open? == false
assert failed.selected_parent.id == rejected_parent.id
assert failed.error == "That parent would create a cycle."
assert persisted.parent_task_id == original_parent.id
```

Reopening keeps the error; changing/clearing the draft removes it.

- [ ] **Step 2: Write failing component tests**

Render the component and assert `#task-parent-picker`, combobox/listbox roles, stable option IDs,
No parent, `#task-parent-error`, `aria-invalid`, `aria-describedby`, and draft label after failure.

- [ ] **Step 3: Run focused tests and confirm missing modules**

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/live/task_parent_picker_test.exs test/taskman_web/components/task_parent_picker_test.exs
```

- [ ] **Step 4: Implement state transitions and persistence mapping**

Use `%TaskWithLocation{}` options and preserve a rejected `%Task{}` as draft. Map controlled errors
to helpful copy:

```elixir
:not_found -> "That parent Task is no longer available."
cycle changeset -> "That parent would create a cycle."
other changeset -> "Couldn’t save the parent. Please try again."
```

Only actual draft changes clear an error; opening options alone does not.

- [ ] **Step 5: Implement the shared HEEx component**

Use `<.input>` for the search field where its contract fits; otherwise preserve full equivalent
styling and accessible semantics. Tasks 6 and 7 compose the finished component into the shared
Task form for create and edit respectively.

- [ ] **Step 6: Run focused tests and commit**

Run Step 3 until passing. Commit Task 5 files with:

```text
Build the shared Task parent picker
```

---

### Task 6: Integrate normal creation and Add subtask

**Files:**

- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `lib/taskman_web/components/task_components.ex`
- Modify: `lib/taskman_web/components/task_form.ex`
- Modify: `test/taskman_web/live/project_live_test.exs`
- Modify: `test/taskman_web/live/project_live_lists_test.exs`

**Interfaces:**

- Consumes: Task 5 picker, Task 1 parent-aware create, existing route/path helpers.
- Produces: normal optional-parent creation and row-level `add_subtask`.

- [ ] **Step 1: Write failing LiveView creation tests**

Cover:

- normal New Task starts with no parent and current browse location;
- selecting a Project-wide parent persists it without changing current location;
- `#add-subtask-<id>` opens the normal modal with parent preselected;
- the shortcut captures the parent's List/root location while keeping the background route;
- changing/clearing that parent does not change the captured location;
- stale/foreign `parent_task_id` query input is recoverable and does not leak identity;
- success refreshes only views that contain the new Task.

Use `element/2`, `form/2`, and `render_click/1`; never assert raw HTML.

- [ ] **Step 2: Run focused LiveView tests**

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/live/project_live_test.exs test/taskman_web/live/project_live_lists_test.exs
```

- [ ] **Step 3: Add route-backed shortcut state**

`add_subtask` patches to the current location's existing new-Task route with
`parent_task_id=<source_id>`. `apply_action(:new_task, params, socket)` resolves that parent and
captures its current `list_id` separately from `selected_list`; normal New Task uses
`selected_list`.

Add assigns for `task_create_location` and `task_parent_picker`. Clear them in
`clear_task_modal_state/1`. The modal copy must name the captured Project/List.

Compose the picker into `TaskForm.form/1` with a temporary `parent_picker` default of `nil` so the
not-yet-integrated edit caller remains valid. Render it whenever picker state is present.

- [ ] **Step 4: Submit through the shared context**

Resolve the picker's selected parent and call:

```elixir
Tasks.create_task(project, task_create_location, task_params,
  parent: TaskParentPicker.selected_parent(task_parent_picker)
)
```

Preserve form/picker/location on validation failure. Patch back to the original browse path after
success and refresh the stream. When the selected parent becomes stale, use
`TaskParentPicker.reject_draft/2` to close the listbox, retain the cached draft, and show
`That parent Task is no longer available.` below the combobox.

- [ ] **Step 5: Run focused tests and commit**

Run Step 2 until passing. Commit Task 6 files with:

```text
Create Tasks and subtasks with optional parents
```

---

### Task 7: Integrate parent editing with autosave

**Files:**

- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/components/task_detail.ex`
- Modify: `lib/taskman_web/components/task_form.ex`
- Modify: `test/taskman_web/live/project_live_autosave_test.exs`
- Modify: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: Task 5 picker save operation and current `TaskAutosave`.
- Produces: edit initialization, immediate parent save/clear, draft-preserving failure recovery.

- [ ] **Step 1: Write failing edit tests**

Verify current parent initialization, immediate set/replace/clear, idempotence, streamed state,
ordinary dirty autosave drafts surviving a parent save, and failure behavior:

```elixir
assert has_element?(view, "#task-parent-search[aria-invalid='true']")
assert has_element?(view, "#task-parent-error", "would create a cycle")
refute has_element?(view, "#task-parent-results")
assert has_element?(view, "#task-parent-search[value='Rejected parent']")
```

Reopen without changing and assert the error remains. Change/clear draft and assert it clears.

- [ ] **Step 2: Run focused autosave/LiveView tests**

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/live/project_live_autosave_test.exs test/taskman_web/live/project_live_test.exs
```

- [ ] **Step 3: Add picker events and refresh rules**

Initialize edit picker when `apply_action(:show_task, ...)` resolves a Task. Search/open/select/clear
events delegate to `TaskParentPicker`. On success assign the updated Task, preserve ordinary
`TaskAutosave` draft/dirty state, and refresh relevant streamed rows. Task 8 extends this success
path to reload the hierarchy once hierarchy state exists.

Pass edit picker state through `TaskDetail.detail/1` into `TaskForm.form/1`. Once create and edit
both supply it, make the Task form's `parent_picker` attr required and remove the temporary nil
branch introduced by Task 6.

On failure keep persisted `selected_task`, close options, retain the rejected draft/error, focus
`#task-parent-search`, and leave the existing hierarchy placeholder unchanged.

- [ ] **Step 4: Ensure navigation/close discards invalid parent draft**

`clear_task_modal_state/1` clears picker state. Existing ordinary autosave flush behavior remains
authoritative when leaving the modal; an invalid parent draft has no pending persistence operation.

- [ ] **Step 5: Run focused tests and commit**

Run Step 2 until passing. Commit Task 7 files with:

```text
Autosave Task parent edits
```

---

### Task 8: Render and navigate the progressive hierarchy

**Files:**

- Create: `lib/taskman_web/live/task_hierarchy.ex`
- Create: `test/taskman_web/live/task_hierarchy_test.exs`
- Modify: `lib/taskman_web/components/task_detail.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `assets/css/app.css`
- Modify: `test/taskman_web/components/task_detail_test.exs`
- Modify: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes: Task 2 hierarchy projection, Task 7 parent-save success path, existing Task-detail
  layout hook and route helpers.
- Produces: modal-scoped expansion state, semantic recursive tree, context-preserving links.

- [ ] **Step 1: Write failing hierarchy-state tests**

Build two connected trees and verify:

- first load expands ancestors and selected node;
- same-root navigation unions the new required path with manual expansions;
- toggle expands/collapses non-required branches;
- different-root load resets manual expansions; and
- `clear/1` resets state for modal close.

- [ ] **Step 2: Write failing component/LiveView tests**

Assert empty truth, nested `role="group"`, disclosure `aria-expanded`, current node,
ancestor/sibling/direct-child initial visibility, collapsed descendants appearing after disclosure,
same-hierarchy state survival, modal-close reset, different-tree reset, and hierarchy links
preserving Project/List plus `include_children=true`.

- [ ] **Step 3: Run focused tests**

```bash
ERL_FLAGS='+S 4' mix test test/taskman_web/live/task_hierarchy_test.exs test/taskman_web/components/task_detail_test.exs test/taskman_web/live/project_live_test.exs
```

- [ ] **Step 4: Implement expansion state and recursive HEEx**

Load hierarchy during Task-detail resolution. Use root ID as connected-tree identity. Render stable
IDs:

```text
#task-hierarchy-node-<id>
#task-hierarchy-disclosure-<id>
#task-hierarchy-link-<id>
```

Disclosure events update only LiveView state. Links patch through the existing
`task_detail_path/4`, preserving the selected browse location and descendant query.

Extend Task 7's successful parent-save branch to reload and assign the hierarchy immediately.
Failed parent saves retain the previous hierarchy.

- [ ] **Step 5: Make route clearing preserve only same-modal hierarchy state**

The current `apply_route/2` unconditionally calls `clear_task_modal_state/1`. Split that behavior so
`:show_task` to `:show_task` patches carry the previous `TaskHierarchy` state into
`TaskHierarchy.load/2`; its root-ID check then preserves same-tree expansions or resets a
different tree. Routes that close Task detail or open creation call `TaskHierarchy.clear/1`.
Continue clearing picker, move, and ordinary modal state on every Task change.

- [ ] **Step 6: Preserve existing shell behavior**

Set `data-has-hierarchy` from the projection while retaining `.TaskDetailLayout` ownership of only
the whole-panel open/collapsed preference. Keep narrow overlay/Escape behavior, independent tree
scrolling, reduced motion, and wide pushed layout. Add CSS only for nesting guides, disclosure
alignment, focus, truncation, and current-node clarity.

- [ ] **Step 7: Run focused tests and commit**

Run Step 3 until passing. Commit Task 8 files with:

```text
Render navigable Task hierarchies
```

---

### Task 9: Verify parity, browser behavior, and delivery readiness

**Files:**

- Modify: tests or implementation files only for defects found by verification
- Modify: `docs/planning/roadmap.md` only when recording the verified delivered state
- Modify: `docs/handoffs/parent-child-task-hierarchy.md`
- Modify: `docs/handoffs/INDEX.md` when retiring the completed handoff

**Interfaces:**

- Consumes: Tasks 1-8.
- Produces: verified slice with canonical evidence and no leaked planning terminology.

- [ ] **Step 1: Run focused surface suites**

```bash
ERL_FLAGS='+S 4' mix test test/taskman/tasks_test.exs test/taskman/tasks/hierarchy_test.exs test/taskman_web/controllers/api/task_controller_test.exs
ERL_FLAGS='+S 4' mix test test/taskman/cli test/taskman/cli_test.exs
ERL_FLAGS='+S 4' mix test test/taskman_web/live test/taskman_web/components
```

Expected: all pass with zero failures.

- [ ] **Step 2: Run the complete repository gate**

```bash
ERL_FLAGS='+S 4' mix precommit
```

Expected: formatting, compilation, and all tests pass.

- [ ] **Step 3: Scan implementation surfaces**

```bash
rg -n \"Task [1-9]|phase|milestone|bead|ticket|implementation plan\" lib test priv/taskman_cli_skill
rg -n \"parent_task_id|tasks hierarchy|--parent|--no-parent\" lib test priv/taskman_cli_skill
```

The first command must find no planning terminology leaks. The second must demonstrate parity
across context/API/CLI/help/completions/skill/tests.

- [ ] **Step 4: Perform responsive browser acceptance**

Start PostgreSQL and Phoenix through the repository's documented workflow. At wide and 390×844
viewports verify:

1. normal create with optional parent;
2. Add subtask and inherited List/root location copy;
3. edit set/replace/clear;
4. draft-preserving validation error below a closed picker;
5. ancestor/sibling/direct-child visibility and branch expansion;
6. hierarchy navigation with a target row absent from the current stream;
7. modal close and different-tree expansion reset;
8. keyboard/focus/combobox/disclosure behavior; and
9. narrow overlay/Escape and wide pushed layout.

- [ ] **Step 5: Obtain independent verification**

Dispatch a distinct verifier that did not implement the changes. Require direct inspection against
the approved specification, reproduction of focused/full gates where feasible, and a written list
of defects or explicit approval. Route defects back to the responsible task implementer and repeat
the affected focused and full gates.

- [ ] **Step 6: Record evidence and commit verification fixes**

Update the roadmap only after technical verification establishes delivery. Update/retire the
handoff according to repository policy. Use `but diff` IDs to commit only intentional changes with:

```text
Complete parent-child Task hierarchy
```

Do not merge, push, publish, or alter external/shared state without explicit operator
authorization.

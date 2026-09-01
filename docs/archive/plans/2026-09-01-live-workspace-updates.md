# Immediate Cooperative Workspace Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successful Project, List, and Task mutations immediately visible in connected
Project views while preserving navigation, open forms, and unsaved human Task edits.

**Architecture:** A context-neutral notification boundary publishes workspace-wide Project/List
invalidations and Project-scoped Task invalidations only after persistence succeeds. Task mutation
persistence moves into a focused module that applies Ecto optimistic locking, retries one disjoint
stale write, and returns structured same-field conflicts. `ProjectLive` refetches canonical
projections without navigation, while `TaskAutosave` and `TaskParentPicker` reconcile canonical
state with local drafts.

**Tech Stack:** Elixir, Ecto optimistic locking, PostgreSQL, Phoenix PubSub, Phoenix LiveView/HEEx,
Req-backed JSON API and CLI, ExUnit, LazyHTML.

**Spec:** `docs/specs/2026-09-01-live-task-updates-design.md`

**Status:** Completed

## Global Constraints

- Read the complete approved specification before implementing any task.
- Follow `AGENTS.md` and `docs/development.md`; keep persistence and Ecto queries out of
  `TaskmanWeb`.
- Use `but` for version-control writes; never run `jj` or raw Git write commands.
- Generate the migration with `mix ecto.gen.migration add_lock_version_to_tasks`; do not invent its
  timestamp.
- Keep `lock_version` internal: do not cast it from input or add it to public Project/List/Task JSON.
- Publish only after a database operation or enclosing transaction has returned success.
- Treat PubSub messages as invalidations; event handlers refetch through public contexts.
- Preserve current filters, sorting, descendant inclusion, navigation expansion, URLs, focus, and
  open Project/List/Task drafts during external reconciliation.
- Task writes retry at most once after the initial stale write. Project and List writes retain
  existing last-write-wins behavior.
- Do not add authentication, presence, polling, durable event delivery, deletion, Project editing,
  List movement/reparenting, or public event APIs.
- Use stable DOM IDs and selector-based LiveView tests. Do not assert styling details.
- Use `start_supervised!/1` for supervised test processes and monitors or
  `_ = :sys.get_state/1` instead of sleeps.
- Each implementation task receives an independent verification pass from an agent that did not
  implement that task.
- Run each task's focused tests before committing it. Do not push, merge, publish, or deploy without
  separate operator authorization.

---

## File and Interface Map

### Notification boundary

- `lib/taskman/change_notifications.ex` owns PubSub topics, subscription functions, event
  construction, and `broadcast_from/4`.
- `lib/taskman/change_notifications/event.ex` defines the internal invalidation value.
- `Taskman.Projects`, `Taskman.Lists`, and `Taskman.Tasks` publish through this boundary after
  successful context mutations.

The event and public notification functions are:

```elixir
defmodule Taskman.ChangeNotifications.Event do
  @enforce_keys [:entity, :operation, :project_id, :entity_id, :fields]
  defstruct [:entity, :operation, :project_id, :entity_id, :lock_version, :fields]
end

@spec subscribe_workspace() :: :ok | {:error, term()}
def subscribe_workspace()

@spec subscribe_project(Project.t() | pos_integer()) :: :ok | {:error, term()}
def subscribe_project(project_or_id)

@spec unsubscribe_project(Project.t() | pos_integer()) :: :ok
def unsubscribe_project(project_or_id)

@spec publish_project(Project.t(), :created, [atom()]) :: :ok | {:error, term()}
def publish_project(project, operation, fields)

@spec publish_list(TaskList.t(), :created | :updated, [atom()]) ::
        :ok | {:error, term()}
def publish_list(task_list, operation, fields)

@spec publish_task(Task.t(), :created | :updated | :moved, [atom()]) ::
        :ok | {:error, term()}
def publish_task(task, operation, fields)
```

Use topic names `workspace:changes` and `projects:<project_id>:tasks`. Each publisher normalizes
fields to a unique, stable atom list and calls `Phoenix.PubSub.broadcast_from/4` with `self()` as
the sender.

### Task mutation and conflict boundary

- The generated migration adds `tasks.lock_version`.
- `lib/taskman/tasks/task.ex` owns only the schema field/default and changeset constraints.
- `lib/taskman/tasks/conflict.ex` defines the context result shared by web adapters.
- `lib/taskman/tasks/mutations.ex` owns Task create/update/move persistence, optimistic-lock retry,
  parent transaction coordination, and changed-field calculation.
- `lib/taskman/tasks.ex` remains the public facade and publishes successful mutation metadata.

The conflict and internal mutation results are:

```elixir
defmodule Taskman.Tasks.Conflict do
  @enforce_keys [:task, :fields]
  defstruct [:task, :fields]

  @type t :: %__MODULE__{task: Task.t(), fields: [atom()]}
end

@type mutation_result ::
        {:ok, Task.t(), [atom()]}
        | {:error, Conflict.t() | Ecto.Changeset.t() | :not_found | :unchanged_location}
```

Existing public `Tasks.create_task`, `Tasks.update_task`, and `Tasks.move_task` signatures remain
source-compatible. Successful public calls still return `{:ok, task}`. Updates and moves may now
return `{:error, %Taskman.Tasks.Conflict{}}`.

### Task draft reconciliation

- `lib/taskman_web/live/task_autosave.ex` owns ordinary editable-field baselines, drafts, dirty
  fields, conflicts, retry resolution, and save-state copy.
- `lib/taskman_web/live/task_parent_picker.ex` owns parent-selection reconciliation and parent-field
  conflict resolution.
- `lib/taskman_web/components/task_form.ex` and
  `lib/taskman_web/components/task_parent_picker.ex` render accessible inline conflict notices.

`TaskAutosave` gains `baseline: Task.t() | nil` and
`conflicts: %{optional(String.t()) => term()}` and exposes:

```elixir
@spec reconcile(t(), Task.t()) :: t()
def reconcile(autosave, persisted_task)

@spec resolve_conflict(t(), Project.t(), Task.t(), String.t(), :use_latest | :keep_mine) ::
        {:ok, t(), Task.t()} | {:conflict, t(), Task.t()} | {:not_found, t()}
def resolve_conflict(autosave, project, persisted_task, field, resolution)

@spec conflict_value(t(), String.t()) :: term() | nil
def conflict_value(autosave, field)
```

`TaskParentPicker` gains a nullable conflict value and exposes:

```elixir
@spec reconcile(t(), Project.t(), Task.t()) :: t()
def reconcile(picker, project, persisted_task)

@spec resolve_conflict(t(), Project.t(), :use_latest | :keep_mine) ::
        {:ok, t(), Task.t()} | {:error, t(), term()}
def resolve_conflict(picker, project, resolution)
```

### LiveView coordination

- `lib/taskman_web/live/project_live.ex` owns subscription lifecycle and orchestration only.
- `test/taskman_web/live/project_live_task_updates_test.exs` covers Project-scoped Task events.
- `test/taskman_web/live/project_live_workspace_updates_test.exs` covers workspace navigation
  events.
- Existing focused state modules refresh their own canonical projections.

`ProjectLive` stores `:subscribed_project_id`, subscribes to the workspace topic whenever connected,
and changes only its Task subscription when the selected Project changes.

## Delivery Graph

The parent feature is `tas-live-task-updates-egu`.

| Plan task | Beads issue | Dependency gate |
| --- | --- | --- |
| 1. Notification boundary and workspace publishers | `tas-live-task-updates-egu.1` | Ready |
| 2. Optimistic Task mutations and events | `tas-live-task-updates-egu.2` | Task 1 |
| 3. API and CLI conflict contract | `tas-live-task-updates-egu.3` | Task 2 |
| 4. Draft reconciliation and inline resolution | `tas-live-task-updates-egu.4` | Task 2 |
| 5. Project-scoped Task reconciliation | `tas-live-task-updates-egu.5` | Tasks 1, 2, 4 |
| 6. Workspace navigation reconciliation | `tas-live-task-updates-egu.6` | Task 1 |
| 7. Integrated propagation gate | `tas-live-task-updates-egu.7` | Tasks 3, 5, 6 |

---

### Task 1: Add the notification boundary and workspace publishers

**Files:**

- Create: `lib/taskman/change_notifications.ex`
- Create: `lib/taskman/change_notifications/event.ex`
- Create: `test/taskman/change_notifications_test.exs`
- Modify: `lib/taskman/projects.ex`
- Modify: `lib/taskman/lists.ex`
- Modify: `test/taskman/projects_test.exs`
- Modify: `test/taskman/lists_test.exs`

**Interfaces:**

- Consumes: `Taskman.PubSub`, successful `Projects.create_project/1`,
  `Lists.create_list/3`, and `Lists.rename_list/3`.
- Produces: the notification functions and `%Event{}` from the file map; workspace events for
  Project creation and List creation/rename.

- [ ] **Step 1: Write failing notification contract tests**

In `change_notifications_test.exs`, subscribe the test process to the workspace topic, start a
second supervised subscriber that forwards received messages to the test, and assert:

```elixir
assert :ok = ChangeNotifications.subscribe_workspace()
assert :ok = ChangeNotifications.publish_project(project, :created, [:primary_directory, :name])

refute_receive %Event{}

assert_receive {:forwarded,
                %Event{
                  entity: :project,
                  operation: :created,
                  project_id: project_id,
                  entity_id: project_id,
                  lock_version: nil,
                  fields: [:name, :primary_directory]
                }}
```

Cover workspace topic isolation from a Project Task subscription, stable field normalization,
Project IDs, and List owning/entity IDs. Configure the notification module to target a missing
PubSub server in a non-async test and assert publication returns an error without raising; context
callers must ignore that observation failure after persistence succeeds.

- [ ] **Step 2: Write failing Project/List publication tests**

Extend context tests to assert that:

- successful Project creation emits one `:project/:created` workspace event with
  `[:name, :primary_directory]`;
- root and child List creation emit `:list/:created` with
  `[:name, :project_id, :parent_list_id]`;
- a changed List name emits `:list/:updated` with `[:name]`;
- invalid Project/List changesets and foreign parents emit nothing;
- a rename whose normalized name is unchanged returns the existing success result without an
  event.

Use a forwarding subscriber because `broadcast_from/4` excludes the calling process.

- [ ] **Step 3: Run the focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman/change_notifications_test.exs \
  test/taskman/projects_test.exs \
  test/taskman/lists_test.exs
```

Expected: FAIL because the notification modules and context publication do not exist.

- [ ] **Step 4: Implement event construction and subscription**

Implement the exact interfaces from the file map. Keep topic construction private. Reject invalid
non-positive Project IDs through function clauses rather than constructing malformed topics.
Publish the `%Event{}` itself as the PubSub message; do not wrap it in a web-specific tuple.

- [ ] **Step 5: Publish only successful Project/List changes**

In each context:

1. Build the changeset and capture its actual changed fields before persistence.
2. Persist through the existing Repo call.
3. After `{:ok, entity}`, publish when fields are non-empty.
4. Return the existing public result unchanged.

Project creation always publishes both presentation fields. List creation adds ownership fields
programmatically because they are not cast. Do not publish from controllers or LiveView handlers.

- [ ] **Step 6: Run focused tests**

Run the Step 3 command until it reports zero failures.

- [ ] **Step 7: Independently verify Task 1**

A verifier that did not implement the task inspects the event payload, sender exclusion, context
boundaries, and no-event failure cases, then reruns the Step 3 command.

- [ ] **Step 8: Commit only Task 1 files**

Use `but diff`, copy the exact file/hunk IDs for Task 1, and commit on `live-task-updates` with:

```text
Publish Project and List workspace changes
```

---

### Task 2: Add optimistic Task mutations and Task events

**Files:**

- Create via generator: `priv/repo/migrations/*_add_lock_version_to_tasks.exs`
- Create: `lib/taskman/tasks/conflict.ex`
- Create: `lib/taskman/tasks/mutations.ex`
- Create: `test/taskman/task_lock_version_migration_test.exs`
- Modify: `lib/taskman/tasks/task.ex`
- Modify: `lib/taskman/tasks.ex`
- Modify: `test/taskman/tasks_test.exs`
- Modify: `test/support/fixtures/tasks_fixtures.ex`

**Interfaces:**

- Consumes: `ChangeNotifications.publish_task/3`, current Task hierarchy validation, Project row
  locking for parent mutations, and current public Task context arities.
- Produces: `Task.lock_version`, `%Taskman.Tasks.Conflict{}`, internal mutation results, bounded
  disjoint retry, and Task created/updated/moved events.

- [ ] **Step 1: Write failing migration and schema tests**

Assert that newly inserted Tasks default to `lock_version == 1`, a successful update increments it,
the database column is non-null with default `1`, and `Tasks.change_task/2` ignores input
`lock_version`.

- [ ] **Step 2: Write failing stale-write context tests**

Use two separately loaded baselines and sequential writes to establish deterministic concurrency:

```elixir
first_baseline = Tasks.get_task_for_project(project, task.id)
second_baseline = Tasks.get_task_for_project(project, task.id)

assert {:ok, titled} = Tasks.update_task(project, first_baseline, %{title: "Changed"})
assert {:ok, merged} = Tasks.update_task(project, second_baseline, %{status: :done})

assert merged.title == "Changed"
assert merged.status == :done
assert merged.lock_version == titled.lock_version + 1
```

Add cases for:

- stale same-title updates returning `%Conflict{task: current, fields: [:title]}` without data loss;
- a requested value already equal to current persistence returning the current Task without a new
  write;
- mixed intended fields failing atomically when any intended field conflicts;
- stale parent changes retrying disjoint ordinary fields, revalidating hierarchy, and conflicting
  on `:parent_task_id`;
- stale moves retrying changes disjoint from `:list_id`, conflicting on `:list_id`, preserving
  destination validation, and retaining `:unchanged_location`;
- a second stale race stopping after one retry rather than looping;
- conflict and unchanged results emitting no event.

Retain and rerun the existing concurrent opposite-reparent test to prove Project locking still
prevents cycles.

- [ ] **Step 3: Write failing Task publication tests**

Assert exact Project-topic events:

- create:
  `[:description, :due_at, :list_id, :parent_task_id, :priority, :project_id, :status, :title]`;
- ordinary update: the actual changed Task fields;
- parent update: `[:parent_task_id]` plus any ordinary fields in the same atomic mutation;
- movement: `[:list_id]`;
- each event carries the persisted `lock_version`;
- sender exclusion, validation failure, transaction rollback, conflict, and unchanged movement
  produce no event.

- [ ] **Step 4: Run the focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman/task_lock_version_migration_test.exs \
  test/taskman/tasks_test.exs \
  test/taskman/change_notifications_test.exs
```

Expected: FAIL because the migration, conflict type, optimistic mutation module, and Task events do
not exist.

- [ ] **Step 5: Generate and implement the lock migration**

First inspect and invoke the required generator:

```bash
mix help ecto.gen.migration
mix ecto.gen.migration add_lock_version_to_tasks
```

Implement:

```elixir
alter table(:tasks) do
  add :lock_version, :integer, null: false, default: 1
end
```

Add `field :lock_version, :integer, default: 1` and its type entry to `Task`, but do not add it to
`cast/3`.

- [ ] **Step 6: Implement normalized intended-field classification**

In `Taskman.Tasks.Mutations`, derive intended values from the validated changeset's normalized
`changes`, then add programmatic `parent_task_id` or `list_id` when applicable. On a stale write,
load the current Project-scoped Task and classify every intended field:

```text
current == requested  -> already satisfied; remove it from retry work
current == baseline   -> disjoint; safe to reapply
otherwise             -> same-field conflict
```

If any field conflicts, return one `%Conflict{}` containing all conflicting fields and persist
nothing. If every field is already satisfied, return the current Task with an empty changed-field
list. Otherwise rebuild and revalidate against the current Task, retry once with its latest
`lock_version`, and turn another stale result into `%Conflict{}` rather than recurring.

- [ ] **Step 7: Preserve parent and movement invariants**

Keep parent mutation inside the existing Project-row-locked transaction. On retry, reload the Task
and proposed parent, rerun same-Project and cycle validation, then apply `optimistic_lock/3`.
Movement must validate the destination against the owning Project on both attempts and must no
longer replace the caller's baseline with an unconditional pre-update reload.

Catch only `Ecto.StaleEntryError` from the optimistic Repo update. Let validation changesets,
not-found outcomes, transaction rollbacks, and unexpected failures retain their existing result
semantics.

- [ ] **Step 8: Publish successful Task results from the facade**

Have `Taskman.Tasks` translate `{:ok, task, fields}` back to `{:ok, task}` and publish only when
`fields != []`. Use `:created`, `:updated`, and `:moved` operations exactly. Publication occurs
after any transaction has returned `{:ok, ...}`.

- [ ] **Step 9: Run focused tests**

Run the Step 4 command until it reports zero failures.

- [ ] **Step 10: Independently verify Task 2**

The verifier inspects retry bounds, normalized values, mixed-field atomicity, parent/move
revalidation, internal-only `lock_version`, and publish-after-commit ordering, then reruns Step 4.

- [ ] **Step 11: Commit only Task 2 files**

Use `but diff` and commit the exact Task 2 changes with:

```text
Protect concurrent Task mutations
```

---

### Task 3: Expose Task conflicts through the API and CLI

**Files:**

- Modify: `lib/taskman_web/controllers/api/fallback_controller.ex`
- Modify: `lib/taskman/cli/client.ex`
- Modify: `test/taskman_web/controllers/api/task_controller_test.exs`
- Modify: `test/taskman/cli/client_test.exs`
- Modify: `test/taskman/cli/output_test.exs`

**Interfaces:**

- Consumes: `{:error, %Taskman.Tasks.Conflict{task, fields}}`.
- Produces: HTTP `409` with code `concurrent_update`, a stable `fields` map, and CLI exit status
  `3` in both human and JSON modes.

- [ ] **Step 1: Write failing API fallback tests**

Pass a real `%Conflict{}` to the fallback boundary and assert:

```elixir
assert %{
         "error" => %{
           "code" => "concurrent_update",
           "message" => "Task changed concurrently",
           "fields" => %{
             "status" => ["changed concurrently"],
             "title" => ["changed concurrently"]
           }
         }
       } = json_response(conn, 409)
```

Also assert `lock_version` and the current Task are absent from the public envelope. Existing
`unchanged_location` remains a separate `409` contract.

- [ ] **Step 2: Write failing CLI classification and rendering tests**

Add `Req.Test` responses for both `409` codes. Assert `concurrent_update` validates the fields map,
maps to status `3`, renders the named fields in readable mode, and preserves the exact API envelope
plus trailing newline in JSON mode. A mismatched `409` code or malformed fields value remains
`invalid_response` status `5`.

- [ ] **Step 3: Run focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman_web/controllers/api/task_controller_test.exs \
  test/taskman/cli/client_test.exs \
  test/taskman/cli/output_test.exs
```

Expected: FAIL because the fallback and CLI client recognize only `unchanged_location`.

- [ ] **Step 4: Implement the conflict envelope**

Add a fallback clause before the generic changeset clause. Sort and map atom fields into:

```elixir
Map.new(fields, &{Atom.to_string(&1), ["changed concurrently"]})
```

Do not serialize `Conflict.task`. Preserve all existing API success representations and keep
`lock_version` absent.

- [ ] **Step 5: Permit both stable 409 error codes in the CLI**

Change the client contract so status `409` accepts `unchanged_location` or `concurrent_update`, each
with exit status `3`. Reuse the existing generic error renderer; do not add command-specific retry
logic.

- [ ] **Step 6: Run focused tests**

Run the Step 3 command until it reports zero failures.

- [ ] **Step 7: Independently verify Task 3**

The verifier checks HTTP status/code/field stability, public representation unchangedness, CLI
human/JSON parity, and no same-field retry in the CLI, then reruns Step 3.

- [ ] **Step 8: Commit only Task 3 files**

Use `but diff` and commit with:

```text
Report concurrent Task updates to API clients
```

---

### Task 4: Reconcile Task drafts and render inline conflict resolution

**Files:**

- Modify: `lib/taskman_web/live/task_autosave.ex`
- Modify: `lib/taskman_web/live/task_parent_picker.ex`
- Modify: `lib/taskman_web/components/task_form.ex`
- Modify: `lib/taskman_web/components/task_parent_picker.ex`
- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `test/taskman_web/live/task_autosave_test.exs`
- Modify: `test/taskman_web/live/task_parent_picker_test.exs`
- Create: `test/taskman_web/components/task_form_test.exs`
- Modify: `test/taskman_web/components/task_parent_picker_test.exs`
- Modify: `test/taskman_web/live/project_live_autosave_test.exs`

**Interfaces:**

- Consumes: current canonical Tasks and `%Conflict{}` from Task context writes.
- Produces: the reconciliation and resolution functions from the file map, `:conflicted`
  autosave status, and LiveView events `resolve_task_conflict` and
  `resolve_task_parent_conflict`.

- [ ] **Step 1: Write failing ordinary-field reconciliation tests**

Cover:

- `load/3` stores the canonical baseline;
- an external clean-field change updates baseline and form;
- an external dirty-field change preserves the local draft, records the latest canonical value,
  and deletes that field's pending revision;
- an unrelated external field updates while a dirty field remains visible;
- repeated external changes replace the conflict's latest value;
- a scheduled save with an invalidated revision is ignored;
- unresolved conflicts make `flush/3` return an error without persisting the local value;
- `save_state == :conflicted` and message `"Resolve conflicting changes"`.

Use a title draft plus external status change for the clean merge, then an external title change
for the same-field conflict.

- [ ] **Step 2: Write failing resolution tests**

For `:use_latest`, assert the draft, dirty flag, revision, and conflict clear without a write and
the form displays the persisted value. For `:keep_mine`, assert only that field is retried against
the conflict's latest Task; success clears it while a further context conflict keeps the local
draft and updates the latest value.

Assert that a conflict returned directly from `persist_field` takes the same reconciliation path as
a PubSub-delivered external update.

- [ ] **Step 3: Write failing parent-picker conflict tests**

Start with two stale Task baselines and different parent selections. Assert a parent conflict:

- retains the locally selected parent;
- stores the latest Task and latest persisted parent;
- renders Use latest and Keep mine choices;
- Use latest adopts the canonical parent without a write;
- Keep mine retries against the latest Task;
- a further race remains conflicted.

Ordinary external Task reconciliation refreshes the picker `current_task` and canonical selected
parent while preserving its open/search interaction state.

- [ ] **Step 4: Write failing component and LiveView interaction tests**

Render conflict notices with these stable IDs:

```text
task-title-conflict
use-latest-title
keep-mine-title
task-parent-conflict
use-latest-parent_task_id
keep-mine-parent_task_id
```

Each notice has `role="alert"`, includes a human-readable latest persisted value, and emits the
corresponding resolution event and field. Add LiveView tests proving a same-title race preserves
the input, blocks the old timer, changes save status, and resolves without closing
`#task-modal`.

- [ ] **Step 5: Run focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman_web/live/task_autosave_test.exs \
  test/taskman_web/live/task_parent_picker_test.exs \
  test/taskman_web/components/task_form_test.exs \
  test/taskman_web/components/task_parent_picker_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
```

Expected: FAIL because baselines, conflict state, resolution functions, DOM notices, and handlers
do not exist.

- [ ] **Step 6: Implement baseline-aware ordinary-field reconciliation**

Store the canonical Task in `baseline`. On reconcile, compare atom fields on `baseline` and the new
Task. Remove clean fields from the draft before rebuilding the form so stale full-form params
cannot mask canonical values. Keep only dirty field params as overrides.

For a dirty field changed from baseline, store its latest persisted value in `conflicts`, remove its
revision entry, and retain its draft. Rebuild the form from the latest Task plus dirty overrides,
then replace the baseline.

On every form change, retain prior dirty-field overrides and copy only the targeted field from the
submitted full-form params. After a successful field save, remove that field from both
`dirty_fields` and `draft` before rebuilding the form. This prevents clean values captured in an
older browser form submission from becoming accidental local overrides.

- [ ] **Step 7: Implement ordinary-field resolution and save semantics**

Reject unsupported fields and resolutions without atomizing user input. Use the existing fixed
editable-field list to map string fields to existing atoms. `:use_latest` clears state locally.
`:keep_mine` calls `Tasks.update_task/3` with exactly one field and the latest canonical Task.
Route context conflicts back through `reconcile/2`.

Make conflict state higher priority than failed, invalid, dirty, or saved state in
`refresh_save_state/1`. `flush/3` must not silently persist unresolved fields.

- [ ] **Step 8: Implement parent-picker reconciliation and resolution**

Store parent conflict state separately from ordinary autosave conflicts because parent selection
has its own immediate-save state machine. Fetch the latest persisted parent through
`Tasks.get_task_for_project/2`; never query Repo from the web module. Preserve search text,
options-open state, and local selection while conflicted.

- [ ] **Step 9: Render and handle conflict actions**

Add a focused conflict-notice helper in `TaskForm` after each ordinary editable input and a parent
notice in `TaskParentPickerComponent`. Use the stable IDs from Step 4. Format status/priority through
existing labels and format due dates consistently with the input; do not expose raw structs.

In `ProjectLive`, validate resolution strings against the two accepted literals, call the state
module, update selected canonical Task state, and keep the modal URL and unrelated dirty fields.

- [ ] **Step 10: Run focused tests**

Run the Step 5 command until it reports zero failures.

- [ ] **Step 11: Independently verify Task 4**

The verifier checks dirty/clean merging, timer invalidation, repeated conflicts, parent behavior,
accessible DOM contracts, no dynamic atom creation, and modal preservation, then reruns Step 5.

- [ ] **Step 12: Commit only Task 4 files**

Use `but diff` and commit with:

```text
Resolve concurrent Task drafts inline
```

---

### Task 5: Reconcile Project views after Project-scoped Task events

**Files:**

- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/task_move.ex`
- Modify: `lib/taskman_web/live/task_parent_picker.ex`
- Create: `test/taskman_web/live/project_live_task_updates_test.exs`
- Modify: `test/taskman_web/live/task_move_test.exs`
- Modify: `test/taskman_web/live/task_parent_picker_test.exs`

**Interfaces:**

- Consumes: Project Task topics, `%Event{entity: :task}`, `TaskAutosave.reconcile/2`,
  `TaskParentPicker.reconcile/3`, and existing canonical list/hierarchy/move queries.
- Produces: selected-Project subscription lifecycle and navigation-free Task projection refresh.

- [ ] **Step 1: Write failing subscription lifecycle tests**

Start connected views on the index, Project A, and Project B. Assert:

- the index has no Task subscription;
- entering Project A subscribes once;
- patching within Project A does not duplicate it;
- switching to Project B unsubscribes A before subscribing B;
- returning to the index unsubscribes B;
- a Task event for another Project or a malformed event changes no assigns or DOM.

Use forwarded PubSub events or direct `%Event{}` sends and `_ = :sys.get_state(view.pid)`; do not
sleep.

- [ ] **Step 2: Write failing canonical Task-stream tests**

From another process/context call, cover Task create, status membership change, title/status/priority
sort reordering, parent change, and move into/out of the visible location. Assert current status
filters, sort direction, descendant inclusion, and empty-state semantics remain selected while the
stream resets from canonical persistence.

- [ ] **Step 3: Write failing open-modal reconciliation tests**

Cover:

- unrelated external fields update a clean detail form without closing it;
- dirty ordinary fields reconcile through Task 4 state;
- title/status/parent/creation events refresh the open hierarchy while retaining expansion;
- the selected Task remains open when moved out of the visible slice;
- active move destinations refresh and retain a still-valid selected destination;
- a Task-creation modal retains form, captured location, parent draft, focus-target IDs, and URL
  while the table behind it refreshes;
- duplicate and out-of-order events converge on current persistence.

- [ ] **Step 4: Run focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman_web/live/project_live_task_updates_test.exs \
  test/taskman_web/live/task_move_test.exs \
  test/taskman_web/live/task_parent_picker_test.exs
```

Expected: FAIL because subscriptions and Task event handlers do not exist.

- [ ] **Step 5: Implement selected-Project subscription state**

Initialize `:subscribed_project_id` in `mount/3`. Add one private synchronization helper that:

1. does nothing while disconnected;
2. does nothing when the desired ID equals the current ID;
3. unsubscribes the old Project topic when present;
4. subscribes the new valid Project topic when present;
5. stores the resulting ID.

Call it from route-state assignment without changing route semantics.

- [ ] **Step 6: Implement Task event reconciliation**

Pattern-match only well-formed `%Event{entity: :task}` values. Ignore mismatched Project IDs.
For matching events:

1. refetch and reset the visible Task stream through the existing filter/sort/location helper;
2. refresh active move state;
3. if detail is open, refetch the selected Task and reconcile autosave plus parent picker;
4. refresh hierarchy when event operation/fields can affect title, status, parentage, creation, or
   movement;
5. preserve modal, URL, filters, sorting, navigation, and unrelated local state.

Do not call `handle_params/3`, `push_patch/2`, or a helper that clears modal state.

- [ ] **Step 7: Make active projections refreshable without resetting interaction**

Extend `TaskMove.refresh/2` and `TaskParentPicker.reconcile/3` to replace canonical Tasks, Lists,
paths, and options while preserving active query, open, and still-valid selection state.

- [ ] **Step 8: Run focused tests**

Run the Step 4 command until it reports zero failures.

- [ ] **Step 9: Independently verify Task 5**

The verifier checks topic transitions, cross-Project isolation, canonical refetch rather than event
interpretation, filters/sorts/empty states, modal preservation, and duplicate/out-of-order
idempotence, then reruns Step 4.

- [ ] **Step 10: Commit only Task 5 files**

Use `but diff` and commit with:

```text
Refresh connected views after Task changes
```

---

### Task 6: Reconcile workspace navigation after Project and List events

**Files:**

- Modify: `lib/taskman_web/live/project_live.ex`
- Modify: `lib/taskman_web/live/list_edit.ex`
- Create: `test/taskman_web/live/project_live_workspace_updates_test.exs`
- Modify: `test/taskman_web/live/list_edit_test.exs`

**Interfaces:**

- Consumes: workspace `%Event{entity: :project | :list}`, existing Project/List contexts,
  `Lists.navigation_nodes/4`, current expansion state, and Task 1 publication.
- Produces: one connected workspace subscription and state-preserving canonical navigation
  refresh.

- [ ] **Step 1: Write failing workspace subscription tests**

Assert every connected `ProjectLive`, including `/`, subscribes once to workspace changes and keeps
that subscription while patching between Projects. Task subscription changes must not alter the
workspace subscription.

- [ ] **Step 2: Write failing Project/List projection tests**

Cover:

- external Project creation appears on an already-connected index and selected-Project sidebar;
- external root/child List creation appears under the correct Project;
- List rename updates navigation labels, selected breadcrumb/location heading, Task location cells,
  and location-sort order;
- List events for a non-selected Project update only workspace navigation;
- duplicate/delayed events rebuild the latest canonical navigation.

- [ ] **Step 3: Write failing state-preservation tests**

Before an external workspace event, expand navigation and enter drafts into:

- `#project-form`;
- a root/child List creation form;
- a List rename form;
- an open Task create/detail modal.

After the event, assert the same expansion IDs, form values, active inline List form DOM ID, modal,
and URL remain. LiveView tests establish DOM/state preservation; the final browser acceptance step
checks actual focus.

- [ ] **Step 4: Run focused tests and confirm the red state**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman_web/live/project_live_workspace_updates_test.exs \
  test/taskman_web/live/list_edit_test.exs
```

Expected: FAIL because workspace subscription and event reconciliation do not exist.

- [ ] **Step 5: Subscribe once and rebuild canonical workspace projections**

When connected, subscribe in `mount/3` to the workspace topic. For well-formed Project/List events,
refetch `Projects.list_projects/0`, then `Lists.list_lists_for_project/1` for each Project. Reset the
`:projects` and `:navigation_nodes` streams from those values while reusing
`:expanded_node_ids` and `selected_location/1`.

Do not call `assign_project_state/8`, because it clears List-edit and modal interaction state.

- [ ] **Step 6: Refresh selected-location projections for owning-Project List events**

When `event.project_id` matches the selected Project:

1. refetch the selected Project and selected List by ID;
2. preserve a nil selected List for a Project-root route;
3. recompute `:location_path`;
4. refetch the Task stream with current filters, sorting, and descendant inclusion;
5. refresh hierarchy, move, and open picker projections that display List paths.

Project creation requires only workspace stream refresh. Unknown/malformed events are ignored;
canonical rebuilding handles an event whose entity is unexpectedly absent.

- [ ] **Step 7: Preserve inline List edit identity and drafts**

Add a `ListEdit` reconciliation helper only if the navigation stream reset would otherwise replace
the form-bearing node with stale entity data. Keep the same form ID and form params. Refresh
underlying Project/List structs used for later target validation without replacing submitted draft
values. Concurrent List renames remain last-write-wins.

- [ ] **Step 8: Run focused tests**

Run the Step 4 command until it reports zero failures.

- [ ] **Step 9: Independently verify Task 6**

The verifier checks global subscription lifetime, canonical all-Project navigation, selected List
paths/order, form and expansion preservation, no navigation, and unchanged List concurrency
semantics, then reruns Step 4.

- [ ] **Step 10: Commit only Task 6 files**

Use `but diff` and commit with:

```text
Refresh workspace navigation after changes
```

---

### Task 7: Verify integrated browser, API, and CLI propagation

**Files:**

- Create: `test/taskman_web/live/project_live_external_updates_test.exs`
- Modify: `test/taskman/cli/end_to_end_test.exs`
- Modify: `docs/handoffs/live-task-updates.md`
- Modify through `br`: `tas-live-task-updates-egu` and child issue state/evidence

**Interfaces:**

- Consumes: all prior task interfaces and existing loopback Bandit CLI test setup.
- Produces: integrated adapter evidence, completion-gate evidence, and accurate durable resume
  state.

- [ ] **Step 1: Write integrated API-to-LiveView tests**

Open a connected LiveView, then use Phoenix controller requests from another process/connection to
create a Project, create/rename a List, and create/update/parent/move a Task. After each request,
use `_ = :sys.get_state(view.pid)` and assert the appropriate navigation, row, selected detail,
hierarchy, and membership projection changed without a patch or modal closure.

- [ ] **Step 2: Extend loopback CLI acceptance**

Start Bandit with `start_supervised!/1`, open a connected LiveView in the same non-async sandbox,
then invoke real `Taskman.CLI.run/1` commands against its loopback URL for:

- Project create;
- List create and rename;
- Task create, update, parent change, and move.

Assert status `0`, unchanged response shapes, and connected-view projection updates. Keep conflict
transport coverage in Task 3's deterministic client tests; do not add a timing-dependent HTTP race.

- [ ] **Step 3: Run all feature-focused tests**

Run:

```bash
ERL_FLAGS='+S 4' mix test \
  test/taskman/change_notifications_test.exs \
  test/taskman/task_lock_version_migration_test.exs \
  test/taskman/projects_test.exs \
  test/taskman/lists_test.exs \
  test/taskman/tasks_test.exs \
  test/taskman_web/controllers/api/task_controller_test.exs \
  test/taskman/cli/client_test.exs \
  test/taskman/cli/output_test.exs \
  test/taskman/cli/end_to_end_test.exs \
  test/taskman_web/live/task_autosave_test.exs \
  test/taskman_web/live/task_parent_picker_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs \
  test/taskman_web/live/project_live_task_updates_test.exs \
  test/taskman_web/live/project_live_workspace_updates_test.exs \
  test/taskman_web/live/project_live_external_updates_test.exs
```

Expected: all focused tests pass with zero failures.

- [ ] **Step 4: Search implementation surfaces for leaked planning language**

Run:

```bash
rg -n 'tas-live-task-updates|Task [1-7]|phase|milestone|implementation plan' \
  lib test priv README.md
```

Expected: no planning identifiers or internal sequencing terminology in production code, tests,
public API errors, CLI output, or user-facing documentation.

- [ ] **Step 5: Run the repository completion gate**

Run:

```bash
mix precommit
```

Expected: formatting, compilation, and the complete test suite pass with zero failures.

- [ ] **Step 6: Perform browser acceptance**

With the app running locally, use two browser tabs plus the real CLI and verify:

1. Project creation and List creation/rename update both sidebars immediately.
2. Task create/update/parent/move updates the other tab's current filtered/sorted projection.
3. A clean open Task field adopts an external value without navigation.
4. A dirty same field preserves the draft, displays latest persistence, and supports Use latest
   and Keep mine.
5. Project/List/Task forms, navigation expansion, modal URL, and focused control remain stable.
6. A disconnected/reconnected view reconstructs current state without replay.

Record observed failures precisely; do not weaken automated assertions to match a browser defect.

- [ ] **Step 7: Obtain independent feature verification**

A verifier that did not implement the feature reads the approved specification and plan, inspects
the complete feature diff, maps evidence to each success criterion, reruns relevant focused tests
and `mix precommit`, and reports residual uncertainty. This is bounded feature verification, not a
full-branch review.

- [ ] **Step 8: Update Beads and handoff state**

Use `br` only. Add concise verification evidence to the child issues, close each child after its
acceptance criteria pass, then close `tas-live-task-updates-egu` only when no implementation or
verification work remains. Retire the active handoff and synchronize `docs/handoffs/INDEX.md` when
the workstream is complete.

- [ ] **Step 9: Commit only integrated verification and durable-state files**

Use `but diff` and commit with:

```text
Verify cooperative workspace updates
```

Do not push or merge without explicit operator authorization.

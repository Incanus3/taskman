# ProjectLive Workflow Decomposition Design

## Status

Approved.

## Context

`TaskmanWeb.ProjectLive` is the single LiveView behind the workspace routes. It owns Project and
List navigation, Task listing and filtering, Task creation and editing, parent selection, autosave,
Task movement, and reconciliation of external workspace changes.

The module is currently about 1,800 lines. Its state-only collaborators have already been grouped
under:

- `TaskmanWeb.ProjectLive.ListEdit`
- `TaskmanWeb.ProjectLive.Tasks.Autosave`
- `TaskmanWeb.ProjectLive.Tasks.Hierarchy`
- `TaskmanWeb.ProjectLive.Tasks.Move`
- `TaskmanWeb.ProjectLive.Tasks.ParentPicker`

Those modules isolate transient state and domain transitions, but the LiveView still contains the
event handlers and socket orchestration for every workflow. The result is difficult to navigate and
makes otherwise independent changes collide in one file.

The existing LiveView behavior is well covered by the tests under
`test/taskman_web/live/project_live/`. The repository-wide baseline at the start of this design is
758 passing tests.

## Goals

- Keep `TaskmanWeb.ProjectLive` as the only LiveView, socket owner, and route target.
- Make each workspace workflow discoverable in a purpose-named module.
- Let each workflow own its related state through one cohesive structure or an existing focused
  state structure.
- Keep framework state, streams, workflow-level assigns, and callback dispatch visible in
  `ProjectLive`.
- Preserve routes, event names, assign meaning, streams, subscriptions, DOM contracts, and
  user-visible behavior.
- Establish an acyclic dependency direction between extracted modules.
- Make each extraction independently verifiable with the existing focused test suites.

The expected result is a `ProjectLive` coordinator of roughly 250–400 lines. This range is a
navigation target, not a reason to create artificial abstractions.

## Non-goals

- Splitting the workspace across multiple LiveViews or stateful LiveComponents.
- Changing the DOM or component boundaries in `project_live.html.heex`. Expressions may change
  mechanically to read fields from grouped workflow state.
- Changing URLs, navigation behavior, stream identifiers, PubSub topics, event payloads, or error
  messages.
- Changing persistence behavior or the public APIs of the Projects, Lists, and Tasks contexts.
- Redesigning the existing socket-free state modules.
- Introducing macros that inject callback clauses or LiveView lifecycle hooks.
- Refactoring code solely to meet a line-count limit.

## Chosen approach

`ProjectLive` remains an explicit coordinator. It owns `mount/3`, `handle_params/3`, framework
assigns, streams, one top-level assign per workflow, callback dispatch, and rendering. Ordinary
modules own cohesive workflows and receive the socket explicitly.

Workflow-local scalar assigns are replaced by state structures. State transformations are pure:
they accept a structure and return a structure without receiving or mutating a LiveView socket.
Workflow orchestration explicitly assigns the returned value:

```elixir
listing =
  socket.assigns.listing
  |> Listing.State.apply_statuses(statuses)

socket
|> assign(:listing, listing)
|> Listing.refresh()
```

State functions encode meaningful transitions and invariants. They must not exist merely to wrap
`Map.put/3`.

Known browser events are grouped in module attributes and delegated by `ProjectLive`:

```elixir
def handle_event(event, params, socket) when event in @workspace_events,
  do: Workspace.handle_event(event, params, socket)

def handle_event(event, params, socket) when event in @task_editing_events,
  do: Editing.handle_event(event, params, socket)

def handle_info(message, socket),
  do: Reconciliation.handle_info(message, socket)
```

The event lists make ownership searchable without retaining business logic in the root module.
Each workflow module handles malformed payload fallbacks for the events it owns. An unknown event
is not silently accepted by a catch-all clause.

## Target structure

```text
lib/taskman_web/live/
  project_live.ex
  project_live.html.heex
  project_live/
    paths.ex
    workspace.ex
    reconciliation.ex
    list_edit.ex
    tasks/
      listing.ex
      creation.ex
      editing.ex
      parent_selection.ex
      movement.ex
      autosave.ex
      hierarchy.ex
      move.ex
      parent_picker.ex
```

The matching tests remain under `test/taskman_web/live/project_live/`. Existing behavior-oriented
test files continue to be the primary owners of observable contracts.

## Workflow state model

The socket stores four grouped workflow states plus the existing focused parent-picker and movement
states:

| Socket assign | Structure | Owned fields |
| --- | --- | --- |
| `workspace` | `TaskmanWeb.ProjectLive.Workspace.State` | selected Project and List, descendant mode, location path and not-found flags, subscription identity, Project form, expanded navigation nodes, and `ListEdit` |
| `listing` | `TaskmanWeb.ProjectLive.Tasks.Listing.State` | visible statuses, filter form and open state, sorting, and Task empty-state flags |
| `creation` | `TaskmanWeb.ProjectLive.Tasks.Creation.State` | Task creation form, enabled state, and target location used for persistence and derived copy |
| `editing` | `TaskmanWeb.ProjectLive.Tasks.Editing.State` | selected Task, detail and not-found state, `Autosave`, and `Hierarchy` |
| `task_parent_picker` | `TaskmanWeb.ProjectLive.Tasks.ParentPicker` | existing parent-selection state |
| `task_move` | `TaskmanWeb.ProjectLive.Tasks.Move` | existing movement state |

`current_scope`, `current_user`, `flash`, and `live_action` remain framework-level assigns. LiveView
streams remain separate under `streams`; no collection is copied into a workflow structure.

The grouped states deliberately make ownership more important than retaining the existing scalar
assign names. The template reads values such as `@workspace.selected_project`,
`@listing.visible_statuses`, `@creation.form`, and `@editing.autosave`. These access changes must
not alter rendered markup.

Changing any field marks its containing top-level workflow assign as changed for LiveView diff
tracking. This is accepted because each structure corresponds to one cohesive UI surface and the
potentially large collections remain streams. A structure should be split only if measurement
shows a material rendering cost, not preemptively.

State modules may depend on existing socket-free state modules and deterministic Phoenix form
construction, but they must not depend on sockets, contexts, persistence, PubSub, timers, or
navigation.

Each workflow state is defined as a nested module in its owning workflow's source file, for example
`TaskmanWeb.ProjectLive.Tasks.Listing.State` inside `tasks/listing.ex`. This keeps a private,
one-consumer state model beside the workflow whose invariants it represents. The repository's
one-module-per-file convention permits this narrowly for small workflow-owned `State` modules
containing data and pure transformations. A state module must move to its own file if it gains an
independent consumer, persistence or external coordination, or substantial behavior.

## Module responsibilities

### `TaskmanWeb.ProjectLive`

The root module owns:

- the LiveView declaration and template;
- `mount/3`, including stream configuration, framework assigns, and one initialized assign per
  workflow;
- `handle_params/3` and cross-workflow route composition;
- event-name groups and thin `handle_event/3` delegation;
- thin `handle_info/2` delegation;
- aliases and constants that are genuinely shared by the coordinator.

It must not retain workflow-local scalar initialization, event-specific validation, persistence,
conflict resolution, movement, filtering, or reconciliation logic after the corresponding workflow
is extracted. A contributor should be able to open `ProjectLive` and see the workflow boundaries
without requiring the root module to enumerate each workflow's internal fields.

### `TaskmanWeb.ProjectLive.Paths`

`Paths` owns pure route construction and route-comparison helpers:

- Project and List browse paths;
- Task creation paths;
- Task detail paths;
- preservation of the `include_children` query;
- optional parent Task query parameters;
- canonical selected-Project, selected-List, and selected-Task route predicates.

It may use verified routes, but it must not read persistence, mutate a socket, or push navigation.
Workflow modules decide when to navigate and call `Paths` to construct the target.

### `TaskmanWeb.ProjectLive.Workspace`

`Workspace` owns Project, List, and sidebar workflows:

- Project validation and creation events;
- navigation-node expansion events;
- opening, cancelling, validating, and saving List forms;
- workspace subscription and snapshot loading;
- Project/List navigation stream refresh;
- selected Project/List reconciliation after external changes;
- clearing a selected location that no longer exists.

`ListEdit` remains a socket-free state value. `Workspace` coordinates that value with LiveView
assigns, contexts, streams, and navigation.

`Workspace.State` groups workspace-local assigns. Its transformations include:

- `new/1` for the initial Project form and empty navigation state;
- `select_location/5` for a consistent Project, List, descendant mode, path, and found state;
- `project_not_found/1` and `location_not_found/2` for mutually consistent failure states;
- `toggle_node/2` for navigation expansion;
- transitions for List edit and subscription identity.

Public internal entry points are:

- `handle_event/3` for owned events;
- `subscribe/1` for the workspace subscription;
- `refresh/1` for a complete navigation refresh;
- `reconcile/2` for a well-formed Project or List notification.

These functions return normal LiveView callback tuples or an updated socket as appropriate. Their
specifications must make the return shape explicit.

### `TaskmanWeb.ProjectLive.Tasks.Listing`

`Listing` owns the visible Task collection:

- opening and closing the status filter;
- applying and restoring visible statuses;
- cycling Task sort fields and directions;
- querying Tasks for the current Project/List and descendant setting;
- resetting the Task stream;
- maintaining `tasks_empty?` and `tasks_filtered_empty?`;
- making location sort unavailable when descendants are hidden.

Its main internal APIs are `handle_event/3`, `refresh/1`, and `clear/1`.

`Listing.State` groups filter, sort, and empty-state values. Its transformations include `new/1`,
`toggle_filter/1`, `close_filter/1`, `apply_statuses/2`, `sort_by/2`, and a result transition that
updates both empty-state flags together. Status normalization and sort cycling remain state
invariants rather than ad hoc socket updates.

### `TaskmanWeb.ProjectLive.Tasks.Creation`

`Creation` owns the new-Task workflow:

- entering the `:new_task` route;
- validating the create form;
- deriving the selected creation location;
- creating a Task with the selected parent;
- preserving the current descendant filter in the resulting route;
- refreshing or clearing creation state when the selected List changes or disappears.

Its main internal APIs are `handle_event/3`, `apply_route/2`, `refresh_location/2`, and `clear/1`.
Parent-picker interaction itself belongs to `ParentSelection`.

`Creation.State` groups the form, enabled state, and target location. Its transformations include
`empty/0`, `open/3`, `validate/2`, `refresh_location/2`, and `clear/1`. A state must not contain an
enabled form that lacks a valid target location.

### `TaskmanWeb.ProjectLive.Tasks.ParentSelection`

`ParentSelection` owns parent-picker orchestration shared by Task creation and editing:

- opening, toggling, closing, and searching options;
- keyboard movement and selection;
- selecting or clearing a draft parent;
- saving an edited parent;
- resolving parent conflicts;
- reconciling picker state with the latest persisted Task.

The existing `ParentPicker` module remains the socket-free state and transition owner.
`ParentSelection` translates browser events and persisted results into socket updates.

Its main internal APIs are `handle_event/3`, `open_edit/3`, `sync/2`, `refresh/1`, and `clear/1`.

### `TaskmanWeb.ProjectLive.Tasks.Editing`

`Editing` owns the Task detail and ordinary editable-field lifecycle:

- entering and clearing Task detail state;
- loading and clearing Task hierarchy state;
- toggling hierarchy disclosure;
- scheduling, executing, and flushing autosaves;
- applying successful, ignored, conflicted, missing, and failed autosave results;
- submitting pending edits;
- resolving ordinary editable-field conflicts;
- reconciling an open Task with the latest persisted Task.

The existing `Autosave` and `Hierarchy` modules remain socket-free. `Editing` coordinates them with
the socket, timers, Tasks context, and routes.

Its main internal APIs are `handle_event/3`, `apply_route/3`, `handle_autosave_info/2`, `flush/1`,
`reconcile/2`, `reload_hierarchy/1`, and `clear/1`.

`Editing.State` groups the selected Task, detail visibility, not-found state, autosave state, and
hierarchy state. Its transformations include `empty/0`, `open/3`, `not_found/1`,
`put_autosave/2`, `put_hierarchy/2`, `clear_transient/1`, and `clear/1`. `open/3` establishes a
saved autosave baseline and matching hierarchy for the same selected Task; `not_found/1` removes
incompatible detail state.

### `TaskmanWeb.ProjectLive.Tasks.Movement`

`Movement` owns moving a Task between locations:

- opening a move surface from a row or detail view;
- loading, searching, selecting, and closing destinations;
- flushing detail edits before a move;
- submitting a move;
- preserving or restoring the correct route after success or failure;
- refreshing the active move surface after workspace changes;
- reinserting an active row when stream state changes.

The existing `Move` module remains the socket-free movement state. `Movement` owns socket and route
orchestration around it.

Its main internal APIs are `handle_event/3`, `refresh/1`, `reconcile/1`, and `clear/1`.
`Movement` may depend on `Editing.flush/1` and `Listing.refresh/1`; neither module may depend back on
`Movement`.

### `TaskmanWeb.ProjectLive.Reconciliation`

`Reconciliation` owns `handle_info` processing:

- scheduled autosave messages;
- well-formed Project, List, and Task notifications;
- rejection of stale, malformed, or irrelevant notifications;
- ordering calls to the appropriate workflow reconciliation APIs.

It must not duplicate workflow rules. For example, open-detail reconciliation belongs to `Editing`,
active-move reconciliation belongs to `Movement`, and navigation reconciliation belongs to
`Workspace`.

`handle_info/2` returns a standard LiveView callback tuple. Unrelated well-formed notification types
continue to leave the socket unchanged, matching current behavior.

## Event ownership

| Owner | Events |
| --- | --- |
| `Workspace` | `validate_project`, `save_project`, `toggle_navigation_node`, `open_list_form`, `cancel_list_form`, `validate_list`, `save_list` |
| `Listing` | `toggle_task_status_filter`, `close_task_status_filter`, `filter_task_statuses`, `restore_task_statuses`, `sort_tasks` |
| `Creation` | `validate_task`, `save_task` |
| `ParentSelection` | `open_task_parent_options`, `toggle_task_parent_options`, `close_task_parent_options`, `search_task_parents`, `task_parent_keydown`, `select_task_parent`, `clear_task_parent`, `resolve_task_parent_conflict` |
| `Editing` | `toggle_task_hierarchy_node`, `autosave_task`, `submit_task_edit`, `resolve_task_conflict` |
| `Movement` | `open_move_task`, `open_move_destinations`, `search_move_destinations`, `select_move_destination`, `cancel_move_task`, `submit_move_task` |

Event strings and payload shapes are unchanged. Where an event currently has a malformed-payload
fallback, that fallback moves with the event.

## Dependency direction

Dependencies must respect these layers:

```text
ProjectLive
  ├── Workspace
  ├── Tasks.Creation
  ├── Tasks.ParentSelection
  ├── Tasks.Editing
  ├── Tasks.Movement
  ├── Tasks.Listing
  └── Reconciliation

Reconciliation
  ├── Workspace
  ├── Tasks.Listing
  ├── Tasks.ParentSelection
  ├── Tasks.Editing
  └── Tasks.Movement

Tasks.Movement
  ├── Tasks.Editing
  └── Tasks.Listing

Tasks.ParentSelection
  ├── Tasks.Editing
  └── Tasks.Listing

Workspace
  ├── Tasks.Listing
  └── Tasks.Movement

All workflow modules
  ├── Paths
  ├── existing socket-free state modules
  └── public Taskman context APIs
```

The diagram expresses allowed direction, not a requirement that every listed dependency exist.
`Paths` and the socket-free state modules must never depend on a workflow module.
`Reconciliation` is a top-level coordinator and no workflow module may depend on it.

Workflow modules must not call private functions in `ProjectLive`. Any operation needed by more than
one workflow belongs to the lowest cohesive owner shown above. A generic shared-helpers module is
not part of this design.

## Socket and callback contracts

- Workflow event handlers accept `(event, params, socket)` and return `{:noreply, socket}`.
- `Reconciliation.handle_info/2` returns `{:noreply, socket}`.
- Lower-level workflow operations return an updated socket or the existing tagged result when the
  caller must decide whether to navigate.
- Workflow `State` modules accept and return structures without receiving a socket.
- Workflow orchestration modules use LiveView socket APIs directly and assign complete state
  structures returned by pure transitions.
- Scalar assign names may become fields of their owning workflow state; their meaning remains
  unchanged.
- Stream configuration remains in `ProjectLive.mount/3`; stream resetting and insertion move to the
  workflow that owns the collection change.
- Subscription ownership moves to `Workspace`, but subscription timing remains the same.

Functions are internal application APIs even though Elixir requires them to be public across
modules. They should have focused specs and documentation sufficient to explain their inputs,
outputs, and ownership without advertising them as general-purpose web APIs.

## Route flow

`ProjectLive.handle_params/3` remains the route coordinator:

1. Resolve the requested Project, optional List, and optional Task using public context APIs.
2. Assign canonical Project and List state through `Workspace`.
3. Refresh the visible Task collection through `Listing`.
4. Apply the current action:
   - `:show` needs no modal workflow;
   - `:new_task` delegates creation state to `Creation`;
   - `:show_task` delegates detail state to `Editing` and parent state to `ParentSelection`.
5. Clear transient modal state owned by workflows that are not active.
6. Use `Paths` for any canonical redirect or patch.

The coordinator retains this ordering because route application crosses several workflow
boundaries. Extracting it wholesale would create a second god module rather than a useful boundary.

## Reconciliation flow

For external notifications:

1. `Reconciliation` validates the event envelope exactly as today.
2. Project/List events first reconcile `Workspace`.
3. Any resulting location change refreshes `Creation`, `Listing`, and `Movement` in that order.
4. Task events refresh `Listing`, then reconcile the open detail through `Editing` and
   `ParentSelection`, then reconcile the move surface through `Movement`.
5. Missing or stale records preserve the current route-recovery and not-found behavior.

The implementation must preserve ordering where later operations consume assigns or streams
updated by earlier operations.

## Error and conflict behavior

This refactor does not introduce new error semantics.

- Invalid identifiers continue to produce the existing not-found state or route recovery.
- List validation and persistence errors remain attached to `ListEdit`.
- Task creation validation remains on the creation form.
- Autosave success, ignored updates, conflicts, missing Tasks, and persistence failures preserve
  their existing state transitions and messages.
- Parent conflicts preserve both `use_latest` and `keep_mine` behavior.
- Move failures preserve the active move state, destination errors, edit flushing, and route
  restoration behavior.
- Malformed browser-event payloads keep their current no-op behavior.
- Malformed or irrelevant external events remain no-ops.

Moving a clause to another module must not broaden exception handling or convert a currently visible
failure into a silent fallback.

## Implementation sequence

The refactor proceeds in dependency order:

1. Extract `Paths`.
2. Define `Workspace.State` inside the new `Workspace` module and migrate the grouped assign.
3. Define `Listing.State` inside the new `Listing` module and extract Task listing.
4. Define `Creation.State` inside the new `Creation` module and extract Task creation.
5. Define `Editing.State` inside the new `Editing` module and extract Task editing.
6. Extract `ParentSelection`, which can then reuse Editing reconciliation without duplication.
7. Extract `Movement`.
8. Complete the `Workspace` orchestration extraction.
9. Extract `Reconciliation` and reduce `ProjectLive` to initialization, route composition, and
   callback dispatch.

After each step, compile and run the focused tests owned by the moved workflow. Do not move several
workflow groups before establishing a passing checkpoint. Namespace and source-path assertions are
updated in the same step as the corresponding extraction.

## Testing and verification

Existing tests are the behavior-preservation baseline:

- `project_live/project_live_test.exs` covers primary routing, creation, and detail behavior.
- `project_live/lists_test.exs`, `list_edit_test.exs`, and
  `workspace_updates_test.exs` cover `Workspace`.
- `project_live/task_table_test.exs` covers `Listing`.
- `project_live/autosave_test.exs` and `tasks/autosave_test.exs` cover `Editing` and autosave state.
- `project_live/tasks/parent_picker_test.exs` and component tests cover `ParentSelection`.
- `project_live/move_task_test.exs` and `tasks/move_test.exs` cover `Movement`.
- `project_live/task_updates_test.exs` and `external_updates_test.exs` cover `Reconciliation`.

Tests should continue to assert observable LiveView outcomes. New direct unit tests are warranted
for pure `Paths` behavior, workflow-state invariants, or a newly explicit tagged-result contract,
but the refactor must not replace application-level coverage with implementation-detail
assertions.

Required final verification:

```bash
mix format --check-formatted
mix test test/taskman_web/live/project_live test/taskman_web/components/tasks \
  test/taskman_web/components/workspace_navigation_test.exs
mix precommit
```

A final stale-reference scan must confirm that no extracted function remains duplicated in
`project_live.ex` and no source or test references the superseded module locations.

## Rejected alternatives

### LiveView lifecycle hooks

Attaching per-feature `handle_event` or `handle_info` hooks could remove dispatch clauses from the
root module. It was rejected because event ownership and ordering would become dependent on hook
registration and `:halt`/`:cont` behavior. Explicit delegation is easier to search, review, and
debug.

### Multiple LiveViews or stateful LiveComponents

Splitting the workspace into separate lifecycle owners would provide stronger isolation, but it
would also change socket state, subscriptions, navigation transitions, and component messaging.
Those changes add risk without serving the organization-only goal.

### Callback-injecting macros

Macros could make `ProjectLive` look smaller by injecting callback clauses at compile time. They
would hide rather than reduce complexity and make event ownership harder to discover.

### Callback-type modules

Files such as `events.ex`, `params.ex`, and `info.ex` would group code by Phoenix callback rather
than by product responsibility. Each workflow would remain scattered across several files, so this
does not solve the navigation problem.

### One generic helpers module

A shared `ProjectLive.Helpers` module would become a new accumulation point with no stable
responsibility. Shared operations instead belong to `Paths` or the lowest workflow that owns the
behavior.

## Acceptance criteria

- `TaskmanWeb.ProjectLive` remains the only workspace LiveView and router target.
- Its framework assigns, stream configuration, workflow-level assigns, route coordination, and
  callback dispatch remain visible in the root module.
- Workflow-local scalar assigns are grouped into the documented pure state structures.
- Workflow state transformations do not receive a socket and encode meaningful invariants.
- All event-specific business logic is owned by the workflow named in the event-ownership table.
- External notification handling is delegated to `Reconciliation`.
- Existing state modules remain socket-free.
- Module dependencies follow the documented direction without cycles or a generic helpers module.
- Routes, events, payloads, assign meaning, streams, PubSub behavior, DOM output, and user-visible
  errors remain unchanged.
- The existing focused suites and `mix precommit` pass.
- Canonical documentation and tests reference the resulting module locations.

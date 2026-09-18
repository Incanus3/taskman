# ProjectLive Workflow Decomposition Design

## Status

Implemented and verified; the missing-location recovery workstream remains separately gated.
Updated: 2026-09-19.

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
`test/taskman_web/live/project_live/`. The
[archived implementation plan](../archive/plans/2026-09-04-project-live-decomposition.md) records
the completed extraction sequence.
If the separately designed directory removal runs first, preserve its then-current Project
form/navigation contract; neither workstream requires the other.

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
`sync/2` consumes the persisted selected Task from Editing's successful reconciliation outcome,
without another selected-Task lookup. Reconciliation calls it exactly once on success and skips it
when Editing reports `:unchanged`, including when the selected Task lookup fails.

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
`reconcile/2`, `sync_persisted_task/2`, `refresh_after_move/2`, `reload_hierarchy/1`, and `clear/1`.
`reconcile/2` returns `{socket, {:task_reconciled, persisted_task}}` after a successful selected-Task
lookup or `{socket, :unchanged}` otherwise. It updates editing state only; the coordinator owns
external picker synchronization. Scheduled autosave synchronization retains its picker updates.

`Editing.State` groups the selected Task, detail visibility, not-found state, autosave state, and
hierarchy state. Its transformations include `empty/0`, `open/4`, `not_found/1`,
`put_autosave/2`, `put_hierarchy/2`, `clear_transient/1`, and `clear/1`. `open/4` accepts the prior
state, selected Task, prebuilt `Autosave`, and matching domain `Taskman.Tasks.Hierarchy`.
It loads UI `Tasks.Hierarchy` state internally through pure `Hierarchy.load/2`.
The orchestration module builds the context-dependent autosave form and preserves the existing
detail-opening `saved?: false`
and idle save indicator; its baseline is the persisted Task. `not_found/1` removes incompatible
detail state.

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
`Movement` may depend on `Editing.flush/1`, `Editing.refresh_after_move/2`, and `Listing.refresh/1`;
`Editing.refresh_after_move/2` reloads a matching selected Task after successful movement with
`Autosave.load(..., saved?: true)`, preserving the post-move saved indicator. Ordinary persisted
Task synchronization retains pending edits through `Editing.sync_persisted_task/2`. Neither Editing
nor Listing may depend back on `Movement`.

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
  ├── Tasks.Creation
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

Tasks.Creation
  └── Tasks.Listing

Tasks.Editing
  ├── Tasks.Creation (clear/1 for missing-detail cleanup only)
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
Creation and Editing may refresh Listing after their successful mutations; Listing must not depend
on either workflow. Editing may call `Creation.clear/1` only to preserve all-modal cleanup
when detail or its hierarchy is missing, including late creation-validation events on a detail
route. Creation must not depend on Editing. These edges preserve the approved plan's refresh calls
without introducing cycles.
`Paths` and the socket-free state modules must never depend on a workflow module.
`Reconciliation` is a top-level coordinator and no workflow module may depend on it.

Workflow modules must not call private functions in `ProjectLive`. Any operation needed by more than
one workflow belongs to the lowest cohesive owner shown above. A generic shared-helpers module is
not part of this design.

## Socket and callback contracts

- Workflow event handlers accept `(event, params, socket)` and return `{:noreply, socket}`.
- `Reconciliation.handle_info/2` returns `{:noreply, socket}`.
- Lower-level workflow operations return an updated socket or an explicit tagged result when the
  caller must coordinate downstream work or navigation.
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
3. `Workspace.reconcile/2` returns `{socket, :unchanged}`,
   `{socket, {:location_changed, task_lists}}`, or `{socket, {:location_missing, task_lists}}`.
   A found location refreshes `Creation`, `Listing`, `Movement`, `ParentSelection`, and Task hierarchy
   in that order. A missing location refreshes only the creation location from the already loaded
   Lists, then clears `Listing`; creation, editing, parent-selection, and movement drafts remain.
   Carrying the Lists preserves canonicalization when creation targets a different surviving List
   through a selected parent Task, without another context lookup.
4. Task events refresh `Listing`, reconcile the move surface through `Movement`, reconcile the
   open detail through `Editing`, and synchronize `ParentSelection` exactly once from the explicit
   `{:task_reconciled, persisted_task}` outcome in that order. An `:unchanged` outcome skips picker
   synchronization, preserving its state when the selected Task disappears. Only hierarchy-affecting
   operations or fields then reload hierarchy.
5. Missing or stale records preserve the current route-recovery and not-found behavior.

The implementation must preserve ordering where later operations consume assigns or streams
updated by earlier operations.

## Error and conflict behavior

This refactor does not introduce new error semantics.

### Missing-location behavior follow-up

The proposed detailed contract now lives in the [Missing List recovery design](2026-09-18-missing-list-recovery-design.md),
with a [draft implementation plan](../plans/2026-09-18-missing-list-recovery.md) and a separate
[workstream handoff](../handoffs/missing-list-recovery.md). Direction is approved; these written
artifacts still require review and implementation approval. The evidence below remains the
extraction baseline and does not describe implemented recovery.

On 2026-09-18 the operator agreed to track a separate recovery workstream.
Current external List reconciliation marks a missing selected location, hides its Task creation/
detail surfaces, and clears the Task stream and empty flags; it retains transient creation and
movement state. Existing route lookup has a different boundary: an unavailable route clears modal
state. Extraction must describe these existing boundaries accurately, without interpreting draft
retention as proof that the behavior is correct.

The recovery workstream reviews missing-location handling as a separately scoped behavior
increment. Its preferred direction is to invalidate actions tied to the missing location, explain
what disappeared, and preserve recoverable user input. It specifies whether and how drafts remain
accessible, how the human chooses a new location or discards input, and which pending actions must
stop. It must not silently redirect a save into the Project root or erase drafts as a convenience.

Reproduce external disappearance with creation, detail and movement active, including stale browser
events or pending work after the surface hides. Creation currently retains a List struct while
persistence enforces its foreign-key boundary; movement refetches Task/destination authority on
submit. Those guards do not establish a complete recovery UX. List deletion is not currently a
supported product mutation, so controlled disappearance fixtures must not expand this follow-up
into implementing deletion. Add outcome-focused regression coverage for the agreed behavior before
fixing it. A bounded behavior design and operator approval remain required; the preferred direction
does not authorize implementation or alter the approved nine-task extraction dependency order.

#### Missing-location investigation evidence

A 2026-09-18 read-only investigation used three temporary LiveView probes against
`MIX_ENV=test`, database `taskman_test`, with `Ecto.Adapters.SQL.Sandbox`. All three probes
passed and rolled their synthetic fixtures back. The temporary command was
`MIX_ENV=test mix test /tmp/taskman_missing_location_probe_test.exs --trace`; the temporary
probe is not a repository regression suite or an approved behavior contract.

| Active workflow | Observed disappearance outcome | Observed stale or pending action |
| --- | --- | --- |
| Creation with a parent in another surviving List | Modal hides; form and parent-derived location survive and are canonicalized. | Stale `validate_task` followed by `save_task` creates the Task in the surviving List and patches away from the missing route. |
| Detail with dirty title and a scheduled revision | Modal hides; selected Task and dirty draft remain after the synthetic Task disappears. | `{:autosave_task_field, task_id, "title", 1}` reaches persistence `:not_found`, clears draft/dirty state, and removes selected Task while recovery remains hidden. |
| Row movement with a chosen destination | Popover hides; active Task and destination remain. | Stale `submit_move_task` retains the inaccessible move state and error `This Task is no longer available.` |

These observations establish that persistence authority checks alone do not invalidate actions
at location loss or provide recoverable input. Creation can still persist through a surviving
parent-derived location; pending detail work can destroy input after its surface hides.

The synthetic disappearance removed Task fixtures before their List because the composite
`tasks_list_id_project_id_fkey` rejects deleting a referenced List. A well-formed List update
notification then triggered reconciliation. This reproduces the post-disappearance boundary,
not an implemented deletion operation or a guarantee about external notification ordering.
Source inspection also finds unguarded conflict, parent-picker, and movement search/cancel
handlers, but those variants were not separately reproduced. A future approved specification
must define its event boundary and cover those actions explicitly.

The recovery direction is approved: stop location-bound actions, show retained input in an
accessible recovery surface, and require explicit destination selection before resuming creation,
with copying/discarding input available. This authorizes detailed design and planning. Recovery
storage lifetime, parent relationships, navigation, detail recovery when its Task survives or is
gone, and explicit movement reopening must be specified and approved before implementation.
No persistence across reload, automatic save redirection, or List deletion is authorized by this investigation.

### Preserved extraction outcomes

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

### Final implementation decisions

The implemented extraction resolved several illustrative-plan details while preserving the design:

- `Editing.State.open/4` receives the prior state, selected Task, prebuilt `Autosave`, and domain
  hierarchy. It preserves the persisted Task as the baseline while opening with `saved?: false`, an
  idle indicator, and the existing autosave sequence.
- `Editing` may call `Creation.clear/1` only for missing-detail and missing-hierarchy cleanup. This
  minimal acyclic dependency preserves the existing all-modal cleanup contract.
- `Editing.State` loads its UI hierarchy state from the domain hierarchy through the pure
  `Hierarchy.load/2` transition, preserving disclosure state.
- Resolving a parent conflict synchronizes the persisted picker without adding the hierarchy and
  listing refreshes used by an ordinary successful parent save. This preserves an existing
  limitation: surrounding hierarchy or listing presentation may remain unchanged until another
  normal refresh. Any improvement requires a separately scoped behavior change rather than being
  folded into the behavior-preserving extraction.
- `Editing.refresh_after_move/2` performs a scoped selected-Task refetch and reloads autosave with a
  saved indicator while retaining its sequence. General persisted-Task synchronization continues
  to preserve pending edits.
- Missing-location reconciliation returns the already loaded Lists with
  `{:location_missing, task_lists}`. Creation can therefore canonicalize a surviving parent-derived
  List before Listing clears, without a duplicate query or reverse workflow dependency.
- Task notifications run Listing, Movement, Editing, and then ParentSelection in that order.
  `Editing.reconcile/2` returns an explicit successful persisted-Task outcome or `:unchanged`, so
  ParentSelection synchronizes exactly once after a successful lookup and remains unchanged when
  the selected Task is missing. Scheduled-autosave picker behavior remains independent.

Final independent review and fix re-review found no remaining extraction defects. The verified
baseline passed 264 focused tests, structural and formatting checks, 829 full `mix precommit`
tests, and PR CI without compiler warnings. Expected negative-path runtime logs remain nonblocking.

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

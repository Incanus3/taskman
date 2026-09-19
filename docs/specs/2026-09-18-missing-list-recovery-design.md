# Missing List recovery design

Status: Parked draft. Recovery direction approved; detailed design and implementation plan are not
yet approved. Resume on a dedicated `missing-list-recovery` branch from refreshed `main`. Beads
owner: `tas-1tq.10`.

## Context and baseline

The verified ProjectLive extraction and recovery drafts are published in PR #17. The extraction
has clean independent review, 264 focused tests and 829 passing precommit tests. No recovery
behavior is implemented. Recovery is a parked workstream; when selected, it starts on a dedicated
branch from refreshed `main` rather than continuing on the decomposition branch.

Read the [decomposition design](2026-09-03-project-live-decomposition-design.md), especially its
[disappearance evidence](2026-09-03-project-live-decomposition-design.md#missing-location-investigation-evidence),
and the [Missing List recovery handoff](../handoffs/missing-list-recovery.md) for the continuing
sequence and gates.
The [domain](../product/domain.md) and [relationships](../product/relationships.md) remain product
constraints. There is no supported List deletion operation.

Current `Workspace.reconcile/2` returns `{socket, {:location_missing, task_lists}}` when a selected
List is absent after a validated List notification. Reconciliation canonicalizes creation location
and clears Listing; the template hides Task controls but leaves their actionable state intact.
Three rolled-back test-database probes demonstrated a stale creation saving into a surviving
parent-derived List, a queued title autosave clearing a hidden draft after Task loss, and a hidden
move error. These are observed baseline defects, not desired behavior.

## Scope and invariants

- ProjectLive remains the sole workspace LiveView and renderer; no new routes or LiveComponents.
- Recovery starts at observed loss of the selected List after an already accepted workspace event.
- Suspend location-bound Task actions before handling another browser event or scheduled save.
- Preserve input from the active creation, detail, or movement workflow in accessible recovery UI.
- Resuming is explicit and never creates, updates, or moves a Task by itself.
- Creation requires explicit same-Project destination selection; Project root is an explicit option.
- Persistence stays behind existing Projects, Lists and Tasks contexts; no Repo calls in web code.
- No List deletion, deletion event vocabulary, schema change, dependency, or draft persistence.
- Recovery data lives in the current LiveView process. Reload, disconnect/reconnect that replaces
  the process, or leaving the LiveView loses it; the UI states this limit. Same-LiveView patches
  retain it. Do not imply durable storage or promise preservation of input never sent to the server.

The boundary is event processing: writes completed before the loss notification remain committed.
This design does not undo them. Initial navigation to an unavailable List without an active draft
retains the existing not-found page. Losing a Project and Task disappearance independent of List
loss are separate behavior increments; do not broaden this fix into those cases.

## Approach and alternatives

Use a socket-local Recovery workflow with a private pure State module. It captures recoverable
input and clears the ordinary actionable workflow states. This provides an explicit lifecycle
without teaching every workflow how to own the others' input.

Keeping hidden workflows live with scattered flags was rejected: every timer, conflict and parent
path would need to honor the flag, and ordinary route clearing could still erase drafts. Copy-only
recovery is simpler but does not satisfy the approved explicit creation-resumption direction.
Persistent draft storage would survive reloads but adds an unapproved data lifecycle and schema.

## State and ownership

Create `TaskmanWeb.ProjectLive.Recovery` in
`lib/taskman_web/live/project_live/recovery.ex`, with nested `State`. Root mount assigns
`:recovery = Recovery.State.empty()`.

`State` holds `snapshot`, a monotonically increasing `sequence`, `destination`, `pending`, and
`error`. A snapshot contains its `id`, source Project ID/name, disappeared List ID/name,
`include_children?`, `mode` (`:creation`, `:detail`, or `:movement`), and the captured workflow data:
Creation.State plus ParentPicker for creation; Editing.State plus ParentPicker and optional Move
for detail; Move for row movement. Snapshot data is inert and is never passed directly to mutations.
Capture only the active route's input, not stray state from an inactive modal. Normal UI has one
main Task form; a detail move belongs to the detail snapshot. With no active workflow, leave
snapshot nil and retain the existing not-found page.

State functions consume ordinary data, never sockets or context calls:
`empty/0`, `active?/1`, `capture/2`, `choose_destination/2`, `clear_parent/1`,
`prepare/2`, `put_error/2`, and `discard/1`. `capture/2` increments sequence and is a no-op when
already active, so repeated notifications cannot overwrite the first draft or pending intent.
`discard/1` clears snapshot, pending, destination and error but retains sequence.

Recovery orchestration owns snapshot capture, readonly presentation, scoped destination/Task
resolution, and pending route intent. Creation and Editing own rebuilding their own active forms;
Movement owns rebuilding its active move. Reconciliation invokes Recovery on the missing outcome.
ProjectLive keeps route composition, guarded dispatch and renderer ownership. Dependency direction:
root/Reconciliation -> Recovery -> workflow owners -> contexts/pure helpers. No workflow owner
calls Recovery. A project-owned `Tasks.Recovery` HEEx component renders the recovery panel only.

## Capture and suspension

Before `Workspace.reconcile/2`, Reconciliation saves the previous workspace value. On
`:location_missing`, pass that previous value to `Recovery.enter/2` so the lost List's identity is
available even though Workspace has set selected_list to nil. Capture active input before clearing
it, then call Creation.clear, ParentSelection.clear, Movement.clear, Editing.clear and Listing.clear.
Editing.clear preserves Autosave.sequence and removes revisions; old timer tuples cannot match.
Repeated notifications preserve snapshot and never recapture empty active state.

`Recovery.blocked?/1` is true for an active snapshot, a not-found Project/location, or no selected
Project. Root places one explicit guard before its existing creation/editing/parent/movement event
clauses: known Task workflow events return `{:noreply, socket}` while blocked. Unknown events keep
existing behavior; do not add a general catch-all. Recovery's own events are dispatched separately.
Scheduled autosave dispatch checks the same boundary; blocked timers return unchanged state.
Task notifications may refresh workspace/listing navigation but skip Movement, Editing, parent and
hierarchy work while a snapshot exists. They never mutate the snapshot. Existing validators,
subscriptions and notification vocabulary remain unchanged.

## Recovery UI and lifetime

Render `#task-recovery` within Layouts.app whenever a snapshot exists, including after navigation
to another valid location. Ordinary Task modals and movement controls remain unavailable until
recovery is resolved. Sidebar browsing and existing listing filters remain usable; no new Task
creation, editing or move may replace the suspended input.

Explain: “This List is no longer available. Your input is kept here until you resume or discard it.
Reloading or leaving this page will lose this recovery.” Show the captured source label and readonly
fields, including invalid values. Creation shows all submitted editable fields. Detail shows dirty
values and both sides of existing conflicts, clearly marked unsaved; persisted fields can supply
context. Movement shows query and the previous destination as an unconfirmed suggestion.

`#task-recovery-copy` copies a plain-text field-labelled representation through a project JS hook
in app.js; no inline scripts. An always-visible readonly textarea `#task-recovery-text` provides
manual copying when clipboard permission fails; show `#task-recovery-copy-status` with aria-live.
No external scripts or dependencies. `#task-recovery-discard` explicitly discards the snapshot,
clears active Task modal state, and patches to the current valid browse location, or the source
Project root if the current location is missing. That route fallback is browsing, never saving.

Browsing patches retain the snapshot and skip ordinary modal initialization and autosave flush.
A new-task/detail route requested while recovery is active may update workspace navigation, but
must not initialize its Task action. The panel explains that recovery must be resolved first.
Discard and resume are the only operations that end the active recovery lifetime.

## Explicit creation resume

The recovery form `#task-recovery-form` offers unselected destination options from fresh Lists in
the captured Project: `project` and `list:<positive-id>`. Events:
`choose_recovery_destination` (`destination` string), `clear_recovery_parent`,
`resume_task_recovery`, and `discard_task_recovery`. Recovery events include `recovery_id`
(the positive snapshot ID as a string); malformed, missing, inactive or mismatched IDs are no-ops.
The selection event updates only inert recovery state. Resume refetches the Project and destination.

`Projects.get_project/1`, `Lists.get_list_for_project/2` and `Tasks.get_task_for_project/2` return
struct or nil for integer/string IDs; validate positive canonical destination IDs before lookup.
Never trust IDs from cached options, foreign Projects or captured structs. Unknown destination:
“That destination is no longer available.” Missing source Project: “This Project is no longer
available. Copy your input or discard it.” Retain the full snapshot on either error.

A captured parent is refetched in the source Project. Existing relationships permit parent and
child Tasks in different Lists, including a root child with a List-owned parent. Retain that rule;
parent choice must not override the explicitly chosen creation location. Missing parent blocks
resume with “That parent Task is no longer available. Clear the parent to resume, or copy your input.”;
`clear_recovery_parent` explicitly removes that relationship from the inert snapshot while retaining
field input. Do not silently clear it, substitute another parent, or let it override the selected
location. A valid same-Project parent survives, and ordinary parent selection works after resume.

Resume stores a pending intent with mode, canonical route params and snapshot ID, then push_patch
uses Paths.new_task_path/4 with parent_task_id nil, avoiding parent-derived route location. Root
skips flush while recovery is active, resolves workspace normally while skipping Task apply_action,
and always calls `Recovery.complete_route/2` while recovery is active, including failed workspace
resolution. That function checks pending Project/List/action/query identity before restoring.
Inactive or no-pending recovery is unchanged. Revalidate destination/parent at completion. Restore
through `Creation.restore/3` using captured form input and a fresh parent; rebuild a changeset via
Tasks.change_task/2 and to_form/2, preserving invalid user values. Only successful restoration
consumes snapshot. No Tasks.create_task call occurs until the user submits the resumed form.

Wrong pending routes clear pending, retain snapshot and show “Recovery was interrupted. Choose
resume again or copy your input.” Failed resolution clears pending and retains snapshot with the
Project/destination-unavailable error above. An unexpected owner restoration error uses “Couldn’t
restore your input. Try resuming again or copy it.” A destination
that disappears between preparation and route handling cannot consume input or fall back to root.

## Explicit detail and movement resume

Detail recovery has `#task-recovery-resume` labelled “Reopen Task with unsaved input”. Refetch its
Task in the captured Project and resolve its actual current List (or Project root). Do not move it
to a user-selected location. Missing Task: “This Task is no longer available. Copy your unsaved
input or discard it.” Missing actual List uses the destination-unavailable error; retain snapshot.
Use Paths.task_detail_path/4 and the same prepare/complete-route protocol.

`Editing.restore/3` receives captured Editing.State and ParentPicker; it refetches the Task/hierarchy
from the resolved Project and rebuilds normal detail. `Autosave.resume/2` reconciles the captured
baseline/dirty draft against fresh persisted Task, clears revisions, and preserves a sequence at
least as high as both captured and live sequences. External changes to dirty fields produce the
existing conflict UI. It schedules and persists nothing. Dirty nonconflicted fields display
:not_saved; conflicted fields :conflicted. The user edits or explicitly saves after reopening.
Existing parent conflicts remain explicit and are refreshed against fresh parent authority; do not
persist parent choices on resume. Old scheduled tuples stay ignored even after resume.

Row movement reopens the surviving Task in its actual-location detail route and opens a fresh detail
move surface. `Movement.restore/2` rebuilds via Move.open/4 and Move.search/3, preserving query and
retaining a destination only if fresh options allow it and it is not the current location. If the
previous destination is gone, preserve its label in the recovery text and require a new selection.
Never call Move.submit/2 on resume. With a detail move, restore detail input first, then move state;
ordinary later movement retains its existing edit-flush/conflict gate. A missing Task leaves recovery
accessible. Recovery text explains that reopening does not complete a move.

## File and interface contract

| Owner | Proposed interface and responsibility |
| --- | --- |
| Recovery | `events/0`; `enter(socket, previous_workspace) -> socket`; `blocked?(socket) -> boolean`; `handle_event(event, params, socket) -> {:noreply, socket}`; `complete_route(socket, params) -> socket`; `view(socket) -> map` for fresh readonly/options presentation. |
| Creation | `restore(socket, captured_creation, fresh_parent_or_nil) -> {:ok, socket} | {:error, socket}`; location comes from resolved workspace, never cached creation.location. |
| Editing | `restore(socket, captured_editing, captured_picker) -> {:ok, socket} | {:error, socket}`; refetch scoped Task/hierarchy and restore reconciled inert draft. |
| Autosave | `resume(captured_autosave, fresh_task) -> autosave`; clears revisions without persistence/scheduling; caller supplies max sequence first. |
| Movement | `restore(socket, captured_move) -> {:ok, socket} | {:error, socket}`; reopen in detail with fresh authority and no submit. |
| Tasks.Recovery component | `panel/1` accepts readonly presentation, form and available controls; owns IDs and accessible markup. |

Root handle_params skips Editing.flush while recovery is active, applies its normal workspace
route resolution and clears ordinary Task state, then calls Recovery.complete_route. Root skips
Task apply_action for every active recovery; complete_route delegates form reconstruction to the
workflow owners only for a matching validated pending intent. No callback injection or alternate
route resolver is introduced.
Readonly view computation uses source Project identity even when browsing another Project.

## Test and acceptance strategy

Use real LiveView selectors for UI outcomes and public context lookups for persisted results.
Controlled synthetic disappearance is test-only: verify SQL Sandbox, remove/relocate referenced
synthetic Tasks and child Lists before deleting the List, then send a valid List update Event.
The FK `tasks_list_id_project_id_fkey` prevents deleting a referenced List; this fixture ordering
models post-disappearance, not an implemented external producer sequence. Never add product deletion.

Cover active creation with a parent in a surviving List; dirty detail with old timer; row/detail move;
all known stale workflow events; repeated workspace/Task notifications; browsing while suspended;
copy fallback; discard; resume without persistence; invalid values; parent disappearance/cross-List relationships and
explicit clearing; foreign/malformed destinations; destination loss during pending route; missing
and surviving Task; external ordinary-field and parent conflicts; old timer after resume; sequence
monotonicity; detail move's normal later flush gate; and current subscription/order regressions.

Run each changed suite and final:

```sh
mix format --check-formatted
mix test test/taskman_web/live/project_live test/taskman_web/components/tasks \
  test/taskman_web/components/workspace_navigation_test.exs
mix precommit
```

Use a browser smoke check for clipboard failure fallback, keyboard/focus and navigation retention.
Core component tests may exercise JS contracts, never styling. No sleeps for synchronization.

Implementation sequence: [Missing List recovery plan](../plans/2026-09-18-missing-list-recovery.md),
tracked by `tas-1tq.10.1` through `.3`.

## Next-session checklist and gates

1. Review this complete draft and its linked implementation plan; reconcile any requested changes.
2. Obtain explicit detailed design and implementation-plan approval before implementation.
3. The extraction already resumed through its clean-session boundary. Do not require another clean
   session merely for these documents; offer a new implementation session only once after approving
   this new plan if that boundary has not already been taken.
4. Execute tracked increments with bounded independent reviews, then covering suites and precommit.
5. Update current product recovery behavior only when implemented; retain all active handoff rulings.
6. Refresh publication target before any approved push; same-name historical upstream requires an
   explicit decision. Merge/publication and workstream completion remain separate gates.

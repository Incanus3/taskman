# Missing List recovery design

Status: Implemented, independently reviewed, browser accepted, and confirmed complete by the
operator on 2026-09-23. Delivery was tracked by `tas-1tq.10` and its child issues. PR #18 remains
the merge vehicle; publication and merge are separate from workstream completion.

## Context and baseline

The verified ProjectLive extraction is merged from PR #17. This design defines ordinary editable
creation through location loss, automatic continuation of surviving dirty detail, exceptional
recovery when fresh authority cannot be resolved, and movement continuity. An earlier two-step
creation recovery and a surviving-detail dialog were rejected after browser review; their accepted
replacements are the behavior specified below.

The [decomposition design](2026-09-03-project-live-decomposition-design.md) records the
[pre-implementation disappearance evidence](2026-09-03-project-live-decomposition-design.md#missing-location-investigation-evidence).
The [domain](../product/domain.md) and [relationships](../product/relationships.md) remain product
constraints. There is no supported List deletion operation.

`Workspace.reconcile/2` returns `{socket, {:location_missing, task_lists}}` when a selected List is
absent after a validated List notification. Explicit creation location and fresh submit validation
keep that form editable; queued detail autosaves and active moves require their own stale-action
guards and fresh authority checks. A surviving Task's dirty detail continues without another user
decision.

## Scope and invariants

- ProjectLive remains the sole workspace LiveView and renderer; no new routes or LiveComponents.
- Recovery starts at observed loss of the selected List after an already accepted workspace event.
- Creation always exposes an explicit same-Project location field, with Project root as an option.
- Keep an active creation form editable when its selected location or backdrop disappears; invalidate
  only an unavailable location and prevent submission until the user selects a valid one.
- Preserve unsaved detail input and suspend its stale location-bound actions before handling another
  browser event or scheduled save.
- When fresh Project, Task and actual-location authority resolve, reconcile the captured draft,
  automatically restore its ordinary detail editor without moving the Task, and restart ordinary
  autosave for every valid, nonconflicted dirty field under that fresh authority.
- Report `Saving…`, `Saved`, `Not saved`, and save failure beside each changed input independently;
  do not use one form-wide save-status message as the only feedback for multiple fields.
- Show Copy and Discard recovery UI only when the Project, Task, or Task's current List/root cannot
  be freshly resolved. Offer Try again for unavailable Project/location or restoration failure;
  confirmed missing-Task recovery has no retry button. Retrying performs fresh lookups and never
  writes by itself.
- Keep an active move usable when its destination, source List, or row anchor disappears; movement
  continuity uses the existing popover rather than the detail recovery snapshot.
- Persistence stays behind existing Projects, Lists and Tasks contexts; no Repo calls in web code.
- No List deletion, deletion event vocabulary, schema change, dependency, or draft persistence.
- Creation and recovery data live in the current LiveView process. Reload, disconnect/reconnect
  that replaces the process, or leaving the LiveView loses them. Same-LiveView patches retain them.
  Do not imply durable storage or promise preservation of input never sent to the server.

The boundary is event processing: writes completed before the loss notification remain committed.
This design does not undo them. Initial navigation to an unavailable List without an active draft
retains the existing not-found page. Losing a Project and Task disappearance independent of List
loss are separate behavior increments; do not broaden this fix into those cases.

## Approach and alternatives

Keep creation in its existing `Creation` workflow. Location becomes explicit creation-form state,
is shown on every Create Task dialog, and is refreshed and revalidated independently of the browse
route. Selected-List loss invalidates location without replacing, freezing or copying the form.

Use the socket-local Recovery workflow only for dirty detail state, where normal editing includes
autosave revisions, conflicts and a persisted Task authority that must be re-established before the
form can become actionable. Treat the snapshot as a transient no-write route handoff when authority
resolves, not as a reason to interrupt the user. Render its recovery shell only if the Project,
Task, or Task's actual location cannot be resolved. Movement continues through its existing popover
rather than snapshot recovery. The reconciliation phase itself remains write-free. Once owner
reconstruction succeeds, Editing restarts normal autosave under the freshly resolved authority.

The implemented two-step creation snapshot was rejected after browser review because it turns one
creation intent into readonly recovery followed by a second ordinary form without adding a safety
property that fresh submit-time location validation cannot provide. Persistent draft storage still
adds an unapproved data lifecycle and schema.

The implemented explicit surviving-detail dialog is rejected for the same interaction reason: a
Task moving between Lists invalidates stale persistence authority, not its captured field values.
Automatic fresh reconciliation supplies the necessary safety. Unconditionally relocating the
backdrop was rejected because a moved Task can remain visible from the user's current location.
Deriving that decision from the rendered listing was rejected because status filters must not
change location scope. Duplicating Creation's direct/descendant predicates was rejected because the
two workflows would drift.

## State and ownership

`TaskmanWeb.ProjectLive.Recovery` remains in
`lib/taskman_web/live/project_live/recovery.ex`, with nested `State`. Root mount assigns
`:recovery = Recovery.State.empty()`.

`Recovery.State` holds a detail `snapshot`, a monotonically increasing `sequence`, `pending`, and
`error`. A snapshot contains its ID, source Project/List identity, `include_children?`, Editing.State,
ParentPicker and optional Move. Snapshot data is inert and is never passed directly to mutations.
Capture only active detail input, not stray state from an inactive modal. With no active detail
workflow, leave snapshot nil and retain the existing not-found page.

State functions consume ordinary data, never sockets or context calls: `empty/0`, `active?/1`,
`capture/2`, `prepare/2`, `put_error/2`, and `discard/1`. `capture/2` increments sequence and is a
no-op when already active, so repeated notifications cannot overwrite the first draft or pending
intent. `discard/1` clears snapshot, pending and error but retains sequence.

Creation owns its editable form, location options, invalid-location state, submit-time authority
checks and post-create navigation. A shared pure `Tasks.LocationScope` policy owns direct and
transitive-descendant visibility plus the safe fallback backdrop. Recovery orchestration owns detail
snapshot capture, exceptional recovery presentation, scoped Task/location resolution, and pending
route intent. Editing owns rebuilding normal detail; Movement owns continuity of an active row move
outside the snapshot workflow.
Reconciliation handles active creation before considering detail Recovery, after Movement has
established that no surviving move should relocate to a fresh Task detail route.
ProjectLive keeps route composition, guarded dispatch and renderer ownership. Dependency direction:
root/Reconciliation -> Creation or Recovery -> workflow owners -> contexts/pure helpers. No workflow
owner calls Recovery. A project-owned `Tasks.Recovery` HEEx component renders the detail shell only.

## Capture and suspension

Before calling `Workspace.reconcile/2`, the `ProjectLive.Reconciliation` workflow retains the
current `Workspace.State` in a local variable. If reconciliation reports `:location_missing`, that
state supplies the lost List identity for either creation invalidation or detail recovery after
Workspace has set `selected_list` to nil. A surviving row move is reconciled before either path.

For an active creation route, Reconciliation keeps `Creation.State`, refreshes its location options
from the reconciled List set, marks a disappeared selected location invalid, and clears only the
invalid derived listing. It does not call `Recovery.enter/2`, clear the creation form or parent
picker, or replace the ordinary modal. For active detail, `Recovery.enter/2` captures detail state
before clearing actionable ParentSelection, Movement and Editing copies, then clears Listing and
immediately attempts fresh automatic continuation.
`Editing.clear/1` preserves `Autosave.sequence` and removes revisions; old timer tuples cannot match.
With no active detail or creation workflow, the snapshot remains nil and the existing not-found
result remains. Repeated notifications preserve an existing detail snapshot.

`Recovery.blocked?/1` is true while a detail snapshot is unresolved, for a not-found Project, or for
no selected Project. A missing browse List does not block the still-active creation form. Root places an explicit
guard before editing/parent/movement event clauses; known blocked events return
`{:noreply, socket}`. Creation validation, parent selection, location selection, submission and
cancel remain active while their ordinary modal is present. Unknown events keep existing behavior;
do not add a general catch-all. Recovery's own events are dispatched separately.
Scheduled autosave dispatch checks the same boundary; blocked timers return unchanged state.
This guard is a server-side backstop, not the user-facing disabled state. Templates must not expose
an enabled action control whose event the guard would suppress. Active recovery renders its
explanation with action controls absent or semantically disabled; unavailable workspace states keep
their existing not-found or empty presentation. Directly delivered stale or raced events remain
silent no-ops because the current UI already communicates the blocking state.
Task notifications may refresh workspace/listing navigation but skip Movement, Editing, parent and
hierarchy work while a snapshot exists. They never mutate the snapshot. Existing validators,
subscriptions and notification vocabulary remain unchanged.

## Exceptional recovery UI and lifetime

The **recovery shell** is the fallback shown only when automatic continuation cannot freshly resolve
the Project, Task, or Task's current List/root. It is a small amount of detail-recovery framing around
the captured detail surface: one reason-specific heading, one concise explanation of Copy/Discard
and the temporary lifetime, plus Copy and Discard controls. Other recoverable lookup failures also
offer Try again. It is not a second Task editor or a standalone readonly representation of the
workflow.

Whenever an unresolved snapshot exists, render `#task-recovery` in ProjectLive's existing
Task-operation/modal slot inside Layouts.app, including after navigation to another valid location.
The shell replaces
the ordinary active-operation rendering and wraps the reused detail fields, conflicts, and parent
picker. Do not render a separate
recovery panel or duplicate the operation UI. Render those existing components in an explicit
recovery-display mode: values remain visible and selectable, while their normal mutation, picker,
conflict, movement, cancel, and autosave controls are disabled. Ordinary Task surfaces outside the
shell remain unavailable until recovery is resolved. The recovery shell is modal: its backdrop
blocks the sidebar, listing filters, and all other background controls until recovery is resolved.
Those surfaces may remain visible behind the modal, but they are inert; no new Task creation or
editing may replace the retained input. When the recovery shell mounts, move focus to its dialog
container so assistive technology announces the recovery context before the user reaches its
controls.

Ordinary Task modals delegate initial focus to the form's title hook. That hook waits for the
modal root's visibility event, focuses the title once, and places the caret at the end of an
existing title. Other modal callers retain the shared modal's ordinary focus behavior.

In recovery-display mode, reuse the detail component's field rendering but do not render the
actionable `#task-form`. Detail fields use recovery-owned non-actionable markup. This gives
stale-event tests an exact distinction between recovered fields and a reopened ordinary Task form.

Explain the unavailable authority in a single muted orange heading. For a confirmed missing Task,
use “This task is no longer available” and the subtitle “Copy your unsaved input or discard it. This
recovery state is temporary. Reloading, reconnecting, or leaving Taskman may lose it.” Do not show a stale
source Project/List path, duplicate alert, Task-number heading, activity/sessions sidebar, or field
save lifecycle messages in recovery; there is no writable Task authority. Keep the captured detail
values visible and selectable, including dirty values and both sides of existing conflicts;
persisted values can supply context. Other lookup or restoration failures use the same simple
structure and offer Try again. Do not present ordinary Task relocation as an error when automatic
continuation succeeds or label the shell as “suspended.”

`#task-recovery-copy` copies a human-readable plain-text representation in which each visible
recoverable Task value is preceded by its field label. During every server render with an active
snapshot, derive the current text from that snapshot's presentation and place it in
`#task-recovery`'s `data-copy-value`; render no copy value without a snapshot. This derived text is
not stored as another source of truth.

The recovery component defines a colocated `.CopyRecovery` hook. Its uniquely identified
`phx-update="ignore"` controls region owns only `#task-recovery-copy` and
`#task-recovery-copy-status`; on click it reads the current value from the closest LiveView-managed
`#task-recovery`, calls `navigator.clipboard.writeText`, and reports success or failure through the
status element with `aria-live`. Failure leaves the visible, selectable recovery fields and snapshot
unchanged and permits retry; do not reveal a fallback textarea. Do not add an `app.js` hook,
external script or dependency.
`#task-recovery-discard` explicitly discards the snapshot,
clears active Task modal state, and patches to the current valid browse location, or the source
Project root if the current location is missing. Give Discard a visible rose-tinted border and
background at rest, with stronger emphasis on hover, so it reads as a destructive button before
interaction. That route fallback is browsing, never saving.

If browser history or another route transition changes the Project or List while an unresolved
recovery shell is active, the patch retains the snapshot and skips ordinary modal initialization
and autosave flush. A
new-task or Task-detail route without the matching pending recovery intent must not remain as the
canonical URL or initialize its Task action. Resolve its valid Project/List backdrop, then
`push_patch` to the corresponding `Paths.browse_path` with `replace: true` while retaining the
recovery shell. Only a Task-action route with the matching pending intent may proceed through
`Recovery.complete_route/2`. The panel explains that recovery must be resolved first.
While the unresolved shell is active, it is non-dismissible. Ordinary modal close or cancel,
backdrop-click dismissal, and captured popover close or cancel controls are disabled or ignored.
Same-LiveView navigation may change the backdrop but never removes the shell. Only successful
retry where offered or explicit discard clears the snapshot and removes the recovery UI; failed,
interrupted, or mismatched retry attempts retain both. Reloading, leaving the LiveView, or
replacement of its process remains the stated destructive lifetime boundary.

## Ordinary creation location and List-loss handling

Every Create Task dialog renders an explicit `#task-location` select inside the ordinary
`#task-form`. Its values are canonical same-Project keys: `project` and `list:<positive-id>`. Opening
from a Project or List preselects that location. “Add subtask” preselects the parent and its current
location. After opening, location and parent are independent: selecting or clearing a parent never
changes location, and choosing a location never changes the parent.

`Creation.State` owns the form, selected location key, fresh options, an unavailable-location label
when needed, and its location error. Form validity and location validity jointly control
`#create-task`. The browse route and selected location are separate authorities: changing the form
location never patches the URL or changes the backdrop.

On every accepted workspace List notification, refresh options from the source Project. If the
selected key still resolves, keep it and refresh its label. If it no longer resolves, retain the
missing List's last-known label as an invalid selected value, set “This List is no longer available.
Choose another location.”, focus or associate the error with `#task-location`, and disable Create.
All other Task fields and the parent picker remain editable. Choosing a fresh valid location clears
only the location error. Repeated notifications do not replace form or parent input.

Submission refetches the Project and resolves the canonical location key through
`Lists.get_list_for_project/2`; cached option structs are never mutation authority. Malformed,
foreign or disappeared locations retain all form input, mark location invalid, and do not call
`Tasks.create_task`. Parent authority remains the existing independent create-time check. A valid
parent may reside anywhere in the same Project, including a different List or Project root.

After successful creation, keep the current browse route when the new Task is visible by location:
it targets the current Project/List directly, or **Include child Lists** is enabled and its target is
a transitive descendant of the current Project/List. Status filters do not participate in this
decision. Patch to the created Task's chosen Project/List location only when the previous selected
List disappeared or the new Task is outside the current location scope, preserving the current
Include child Lists setting. Changing the location never navigates before submission. Cancel returns
to the current valid backdrop, or to the source Project root when the backdrop List disappeared.

The standard create modal remains rendered over a missing-List backdrop while its active form
exists. It has no recovery title, readonly fields, Resume, Copy or Discard controls, and never
creates a `Recovery.State` snapshot. Reloading or leaving still loses unsaved creation input under
the ordinary LiveView form lifetime; no separate recovery-lifetime promise is needed.

## Automatic detail continuation

After capture, refetch the Task in the captured Project and resolve its actual current List or
Project root. Do not move it to a selected or previous location. Choose the backdrop with the same
location-scope rule as creation:

- keep the previous backdrop when it still exists and the Task is directly in that location;
- keep it when the Task's actual List is a transitive child and **Include child Lists** is enabled;
- otherwise use the Task's actual List or Project root; and
- never consult status filters when deciding visibility.

Preserve the current Include child Lists setting whether the backdrop stays or changes. A previous
backdrop that disappeared is never restored. `Tasks.LocationScope.visible?/4` makes the direct and
transitive-child decision from the selected backdrop, actual location, Include child Lists setting,
and fresh Project Lists. `Tasks.LocationScope.backdrop/3` receives the workspace, actual location,
and fresh Project Lists, then returns the surviving selected backdrop when visible or the actual
location otherwise. Creation and detail both use these helpers.

Use `Paths.task_detail_path/4` and the existing prepare/complete-route protocol when route correction
is required. Revalidate the Project, Task, actual location, and chosen backdrop at completion. A
racing change converges through a replacement patch to the newly valid detail route; it never saves
through stale authority or leaves the URL at a missing List.

`Editing.restore/3` receives captured Editing.State and ParentPicker; it refetches the Task/hierarchy
from the resolved Project and rebuilds normal detail. `Autosave.resume/2` reconciles the captured
baseline/dirty draft against the fresh persisted Task, clears revisions, and preserves a sequence at
least as high as both captured and live sequences. External changes to dirty fields produce the
existing conflict UI. Reconciliation schedules and persists nothing. After the reconciled owners
are installed, Editing restarts autosave for each valid, nonconflicted dirty field with a fresh
revision. Debounced fields use the normal delay; other eligible fields use their existing immediate
save path. Old scheduled tuples remain invalid and ignored.

Autosave owns a per-field lifecycle for changed editable fields. A valid pending field reports
`Saving…`; a successful field reports `Saved`; an invalid dirty field reports `Not saved`; and a
field-specific persistence failure reports `Couldn’t save changes`. Place each message at the
right end of its input's label line with the same typography and control spacing as the label, its
own stable DOM ID, and a polite live region. Keep an empty invisible slot mounted for untouched or
conflicted fields so lifecycle transitions do not move the surrounding form. Untouched fields show
no visible status. Use muted amber for `Saving…`, emerald for `Saved`, and rose for `Not saved` and
`Couldn’t save changes`; the text remains the non-color state indicator.
One field completing must not erase or mislabel another field's pending, invalid, failed, or
conflicted state. Conflicted fields retain the existing field-local conflict notice and are not
automatically saved until the user resolves them. The form-wide save-status footer is removed.
For the current open editor, `Saved` remains beside that field until the same field changes again;
opening or reloading another detail editor clears these presentation states. They are not persisted.
Existing parent conflicts remain explicit and are refreshed against fresh parent authority; do not
persist parent choices.

Ordinary Task detail identifies the Task's current location in its header. From the small-display
breakpoint upward, retain at least the containing List and `Task #ID`; an extra-small display may
show only the Task. Wide layouts show the complete List path when it fits. When it does not, remove
whole leading List segments from the left and show an ellipsis while retaining the deepest
remaining path and Task; truncate the containing segment only when overflow remains after all
optional ancestors have been removed. A Project-root Task uses the Project name as its navigable
location instead of a static “Project root” label. Project and List segments use ordinary patch
navigation, so the existing route-transition gate flushes pending detail changes before leaving.
Refresh this path immediately after movement so the changed location is visible without a
transient notice. Keep the path left-aligned, use the same type size and weight for location and
Task segments, and distinguish ancestors by muted color rather than scale. Present Move Task as a
compact secondary button. Field-local save lifecycle messages remain right-aligned on their label
lines without changing field height.

Successful reconstruction consumes the transient snapshot and renders the ordinary actionable
detail editor without first displaying recovery UI. If the Project, Task, or actual location cannot
be resolved, retain the inert snapshot and display the exceptional recovery shell. Where offered,
Try again reruns this same fresh automatic-continuation policy; it is not a distinct destination
choice.

With a captured detail move, restore detail input first, then restore the existing move popover with
fresh Task and destination authority. Ordinary later movement retains its existing
edit-flush/conflict gate. Never call `Move.submit/2` during automatic continuation or retry.

## Movement continuity

After a successful move, choose the backdrop through the same fresh `Tasks.LocationScope.backdrop/3`
policy as creation and detail continuation. Keep a surviving selected backdrop when the Task is
directly visible there, or within an included transitive child List. Otherwise patch to the Task's
actual destination, retaining the Include child Lists preference. Status filters do not decide the
route. A row-origin move opens the resulting browse route; a detail-origin move keeps the same Task
editor open over the resulting backdrop. Preserve field-local save feedback while that same editor
is open. This supersedes the fixed-backdrop rule in the earlier Lists design.

Movement does not use a standalone recovery shell merely because a List changes. Refresh the moving
Task, List structure, destination options, and current listing whenever an accepted List or Task
notification can affect an active move, before another move event is accepted. The same authority
check runs at the next move interaction if no notification was observed; there is no polling.
The shared move popover has one Cancel button in the footer beside Move Task. Its resting border and
fill make it recognizable before hover in ordinary movement and after destination loss. Full-width
horizontal dividers separate its heading, destination field, and action footer. The destination
options list floats beneath the input; expanding or filtering it does not move the error or footer.
In Task detail, the popover is a compact control anchored to Move Task rather than spanning the
underlying form; constrain its width to the available detail area on narrow screens.
Clicking elsewhere in the move popover closes only the options while preserving the query,
destination, error, and active move. Clicking outside the popover retains its ordinary cancel action.

When the chosen destination disappears, keep the existing row- or detail-anchored move popover open.
Clear the invalid destination, retain its label/query as context, show “That destination is no longer
available. Choose another destination.”, and disable Move Task until a fresh valid destination is
selected. No copy, discard, or reopen controls are needed.

When the source List or current row anchor disappears, do not endanger or restart the move. If the
moving Task still appears in the freshly rendered listing—such as beneath a surviving parent with
Include child Lists—keep the current backdrop and row-anchored popover. Otherwise refetch the Task,
resolve its actual current List or Project root, immediately patch to that fresh detail route, change
the move origin from row to detail, and render the same active popover in the existing detail surface.
The freshly resolved actual List or Project root becomes the detail modal's backdrop; the disappeared
List is never restored as a backdrop. This route correction performs no Task write. Cancelling the
move keeps the detail modal open, and closing the detail modal returns to that resolved backdrop. Any
still-valid destination and query survive; simultaneous destination loss uses the inline rule above.
If another actor moves the Task into the selected destination, keep that destination visible,
disable Move Task, and show a short muted-orange status explaining that the Task is already there.
If the row anchor is lost, detail and its backdrop follow that actual destination as above; no
second move is submitted. Choosing a different destination or a later move away clears the status.

If the Task itself no longer exists, clear the impossible row move and show the standard application
error flash “This Task is no longer available.” Remain on a still-valid backdrop; if the selected
List also disappeared, patch to the source Project root and carry the flash across the patch. When
an active detail draft contains unsaved input, detail recovery takes precedence and keeps that input
accessible instead of reducing the result to a flash.
The error flash uses a muted dark red with legible pale text; this flash styling does not change
field-level error colors.

## File and interface contract

| Owner | Interface and responsibility |
| --- | --- |
| Recovery | `events/0`; `enter(socket, previous_workspace) -> socket`; `blocked?(socket) -> boolean`; `handle_event(event, params, socket) -> {:noreply, socket}`; `complete_route(socket, params) -> socket`; `view(socket) -> map` only when the Project, Task, or Task location cannot be freshly resolved. Entry attempts automatic continuation; Reopen retries it. |
| Tasks.LocationScope | `visible?(selected_list, actual_list, include_children?, task_lists) -> boolean`; `backdrop(workspace, actual_list, task_lists) -> TaskList.t() | nil`; shared pure visibility/backdrop policy independent of status filters. |
| Creation | State owns `form`, canonical `location`, fresh `location_options`, `location_error`, and combined `enabled?`; `refresh_locations(socket, task_lists) -> socket` preserves form/parent state and invalidates only a missing location; `post_create_path(project, workspace, destination, task_lists) -> String.t()` uses `Tasks.LocationScope` after fresh submission authority succeeds. |
| Editing | `restore(socket, captured_editing, captured_picker) -> {:ok, socket} | {:error, socket}`; refetch scoped Task/hierarchy, install the reconciled draft, then restart eligible field saves under fresh authority. |
| Autosave | `resume(captured, fresh_task) -> autosave` performs write-free reconciliation; `restart(autosave, project, fresh_task) -> {:ok, autosave, task, schedules} | {:not_found, autosave}` immediately handles eligible nondebounced fields and returns fresh `{delay_ms, message}` schedules for eligible debounced fields; `field_state(autosave, field)` exposes `:saving | :saved | :not_saved | :failed | nil`. Conflicts remain in the existing conflict map. |
| Movement | Reconcile an active move against fresh Task, listing and destination authority; keep its anchor, relocate it to fresh detail, invalidate only its target, or report a missing Task without submitting. |
| Tasks.Recovery component | `shell/1` frames the reused detail component in recovery-display mode and owns recovery notices, controls, IDs and accessible markup. |

Root handle_params skips Editing.flush while a captured detail handoff is active, applies its normal
workspace route resolution and clears ordinary Task state, then calls `Recovery.complete_route/2`.
Root skips ordinary Task apply_action until that handoff resolves. Without a matching pending intent,
it canonicalizes a Task-action route to the resolved browse path through
`push_patch(..., replace: true)`; with a matching validated intent, complete_route delegates detail
reconstruction to Editing. A successful automatic handoff therefore renders ordinary detail, while
an unresolved one renders the exceptional shell. Creation
remains the ordinary `:new_task` route even when its browse List becomes unavailable. No callback
injection or alternate route resolver is introduced.
Readonly view computation uses source Project identity even when browsing another Project.

## Test and acceptance strategy

Use real LiveView selectors for UI outcomes and public context lookups for persisted results.
Controlled synthetic disappearance is test-only: verify SQL Sandbox, remove/relocate referenced
synthetic Tasks and child Lists before deleting the List, then send a valid List update Event.
The FK `tasks_list_id_project_id_fkey` prevents deleting a referenced List; this fixture ordering
models post-disappearance, not an implemented external producer sequence. Never add product deletion.

Cover creation location defaults from Project, List and Add subtask; independent location/parent
changes; selected location loss with editable fields; repeated notifications; malformed, foreign or
raced-away submit locations; direct, descendant and out-of-scope post-create navigation; missing
backdrop cancellation and creation; and status-filter independence. Cover automatic dirty-detail
continuation in place for direct and included-descendant locations, relocation for excluded
descendants, unrelated locations and missing backdrops, Project-root equivalents, preserved Include
child Lists, and status-filter independence. Cover automatic post-reconciliation save for every
valid nonconflicted dirty field; independent field-local `Saving…`, `Saved`, `Not saved`, failure,
and conflict outcomes; untouched fields without status; and mixed multi-field lifecycles. Also cover
dirty detail with old timer; row/detail move;
all known stale workflow events; browsing while fresh Project, Task, or Task-location lookup fails; clipboard
success/failure status and retry; discard; retry without persistence when offered; invalid values; parent
disappearance and conflicts; destination loss during pending detail routing; Task-action URL
canonicalization without a matching intent; missing and surviving Task; move-target disappearance;
source/anchor loss with a retained row or
automatic detail relocation; missing moving Task flash/fallback; external ordinary-field and parent
conflicts; old timer after continuation; sequence monotonicity; detail move's normal later flush gate; and
current subscription/order regressions.

Run each changed suite and final:

```sh
mix format --check-formatted
mix test test/taskman_web/live/project_live test/taskman_web/components/tasks \
  test/taskman_web/components/workspace_navigation_test.exs
mix precommit
```

The ordinary creation, automatic detail continuation, mixed field lifecycle, direct and included
descendant, exceptional missing-Task, and movement browser cases were accepted on 2026-09-23. The
full branch review passed after a breadcrumb width-measurement cleanup. Focused component tests
(5), the asset build, and `mix precommit` (906 tests) passed after that cleanup; both PR #18 Build
and test checks passed at published implementation head `4ba789e2`. These checks establish the
local and published baselines, not authorization to merge.

The completed execution sequence is retained for provenance in the
[archived implementation plan](../archive/plans/2026-09-18-missing-list-recovery.md).

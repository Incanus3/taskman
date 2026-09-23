# Missing List Recovery Implementation Plan

Historical implementation plan. The recovery workstream is complete, independently reviewed,
browser accepted, and verified. This plan preserves execution provenance, not remaining work.
Current behavior belongs to the
[accepted design](../../specs/2026-09-18-missing-list-recovery-design.md),
[product specification](../../product/mvp-spec.md), and implemented code.

**Goal:** Preserve ordinary Task creation and surviving detail editing across List-location changes
while rejecting stale writes and retaining exceptional recovery plus movement continuity.

**Architecture:** ProjectLive remains the lifecycle and route owner. Creation owns its explicit
location field and submit authority. `Tasks.LocationScope` supplies a shared pure direct/descendant
visibility and backdrop policy. Recovery captures dirty detail before stale actions can run, then
automatically reconstructs ordinary detail against fresh authority; its shell appears only when the
Project, Task, or actual location cannot be resolved. Movement retains inline continuity and uses
the shared location-scope policy for successful post-move navigation. Tasks 1–4 establish the
implemented baseline; Task 5 replaces the rejected surviving-detail interruption.

**Tech stack:** Existing Phoenix LiveView, Elixir, context APIs, HEEx and Phoenix colocated hooks;
no dependencies.

**Spec:** [Missing List recovery](../../specs/2026-09-18-missing-list-recovery-design.md).

**Status:** Completed and archived on 2026-09-23 after workstream completion confirmation and
acknowledgement of all 14 workstream rulings. PR #18 remains the delivery vehicle; publication and
merge have separate authorization gates.

## Global constraints

- ProjectLive remains the sole workspace LiveView and renderer; no new routes or LiveComponents.
- Recovery starts at observed loss of the selected List after an already accepted workspace event.
- Suspend location-bound Task actions before handling another browser event or scheduled save.
- Keep creation in its ordinary editable modal; always expose an explicit same-Project location with
  Project root as an option, and never enter snapshot recovery for creation.
- Preserve active detail input in a transient snapshot, reject stale autosaves, and automatically
  restore ordinary detail against fresh authority without an update or move.
- Reserve Copy, Reopen and Discard UI for a Project, Task, or current Task location that cannot be
  freshly resolved; Reopen retries the same no-write lookups.
- Keep parent and creation location independent after their initial values are established.
- Reuse existing detail, parent-picker and move-popover components for retained detail input;
  recovery-specific UI is limited to detail framing, notices and controls.
- Keep an active move usable across target, source-List and row-anchor loss whenever its Task survives.
- Persistence stays behind existing Projects, Lists and Tasks contexts; no Repo calls in web code.
- No List deletion, deletion event vocabulary, schema change, dependency, or draft persistence.
- Post-create navigation depends only on backdrop validity and location scope; status filters do not
  participate.
- Detail continuation uses that same location scope and preserves Include child Lists. Relocate only
  when the previous backdrop disappeared or the Task is not visible there.
- Successful movement follows that same location scope: row moves browse the resulting backdrop,
  and detail moves keep the same Task editor open there.

## Review focus

- A destination disappearing between the last form change and submit retains every other field and
  performs no Task write.
- Project-root and nested-List visibility calculations handle direct, transitive-descendant and
  unrelated destinations without consulting status filters.
- An Add subtask parent and its initial location can later change independently without one silently
  overwriting the other.
- Repeated List notifications preserve invalid ordinary field values, parent-picker state and the
  last-known missing-location label.
- Removing creation branches from Recovery does not weaken dirty-detail capture, old-timer rejection,
  clipboard behavior or movement continuity.
- Automatic detail continuation never writes, preserves dirty/conflicted values, rejects old timer
  revisions, and does not flash the exceptional shell on its successful path.
- Detail and creation use one location-scope policy; status filters cannot trigger relocation.
- Row and detail moves use the same scope decision after submission, preserve Include child Lists,
  and keep field-local save feedback in the continuing detail editor.
- A concurrent move to the selected destination keeps the disabled Move Task control and explains
  that the Task is already there, including after row-anchor relocation into detail.

Delivery and documentation gates:

- Use repository-local Beads through `br` and GitButler for local commits.
- Never push, merge, rewrite upstream, close the feature, or retire its handoff without the
  corresponding operator gate.
- Preserve all rulings in the active handoff before deleting execution scratch.
- Keep planning identifiers out of production modules, test names, DOM, events and user-facing
  product documentation.

## File boundaries and shared interfaces

- `lib/taskman_web/live/project_live/tasks/creation.ex`: ordinary creation form state, fresh location
  options and validation, disappearance reconciliation, submission authority, visibility decision
  and resulting browse path.
- `lib/taskman_web/live/project_live/tasks/location_scope.ex`: shared pure location visibility and
  backdrop selection for creation and surviving-detail continuation.
- `lib/taskman_web/components/tasks/form.ex`: always-visible new-Task location select and accessible
  invalid-location feedback inside `#task-form`.
- Root `project_live.ex` and `.html.heex`: allow the ordinary creation owner to remain actionable over
  a missing-List backdrop, choose a safe cancel route, and keep detail recovery modal-only.
- `reconciliation.ex`: route location-missing outcomes to Creation or detail Recovery without
  weakening movement precedence.
- `recovery.ex` and `components/tasks/recovery.ex`: retain detail capture and exceptional fallback;
  successful automatic continuation bypasses recovery presentation.
- `test/taskman_web/live/project_live/creation_location_test.exs`: replace the superseded
  `creation_recovery_test.exs` contract with ordinary creation/location behavior.
- Existing Creation, workspace-update, root, recovery component, detail and movement suites provide
  regression coverage.
- Modify `docs/product/mvp-spec.md` only after Task 4 is implemented to describe current UX.

Shared APIs are exact:

```elixir
Recovery.enter(socket, previous_workspace) # -> socket; detail only
Recovery.blocked?(socket) # -> boolean; missing List alone does not block active creation
Recovery.events() # -> ["resume_task_recovery", "discard_task_recovery"]
Recovery.handle_event(event, params, socket) # -> {:noreply, socket}
Recovery.complete_route(socket, params) # -> socket; detail only
Recovery.view(socket) # -> detail presentation map
Creation.refresh_locations(socket, task_lists) # -> socket
Creation.post_create_path(project, workspace, destination, task_lists) # -> String.t()
Tasks.LocationScope.visible?(selected_list, actual_list, include_children?, task_lists) # -> boolean
Tasks.LocationScope.backdrop(workspace, actual_list, task_lists) # -> TaskList.t() | nil
Editing.restore(socket, captured_editing, captured_picker) # -> {:ok, socket} | {:error, socket}
Autosave.resume(captured_autosave, fresh_task) # -> autosave
Movement.reconcile(socket) # -> {:anchored, socket} | {:relocate, socket, task} | {:missing, socket}
```

State contract:

- `Recovery.State` has `snapshot`, `sequence`, `pending` and `error`.
- Pure `capture/2` receives an ordinary map with source Project/List, `include_children`, Editing,
  ParentPicker and optional Move state—never a socket.
- Recovery snapshots are detail-only. An empty snapshot means inactive.
- Capture increments `sequence` for `snapshot.id` and retains the first snapshot when already active.
- `prepare/2` receives a validated pending-intent map; `put_error/2` clears pending intent;
  `discard/1` retains sequence.

Recovery events:

- Event names are `resume_task_recovery` and `discard_task_recovery`.
- Each requires a matching `recovery_id`.
- Copying is client-side and has no server mutation event.
- Normal Task workflow events remain unchanged and are guarded while recovery is active.

Movement reconciliation outcomes after fresh Task, List, destination and listing resolution:

- keep the current anchor;
- keep the anchor with an invalidated destination and inline error;
- relocate the surviving Task to its actual-location detail route with move origin changed to
  detail; or
- clear a missing Task and request the standard error flash/fallback route.

Apply any movement route outcome before accepting another move event.

## Task 1: Capture, suspend and expose recoverable input

**Beads:** `tas-1tq.10.1`.

**Files:**

- Create Recovery workflow/State and Tasks.Recovery component with colocated clipboard hook.
- Create `test/taskman_web/live/project_live/recovery_test.exs`.
- Create `test/taskman_web/live/project_live/tasks/recovery_state_test.exs`.
- Create `test/taskman_web/components/tasks/recovery_test.exs`.
- Modify the ProjectLive root/template and Reconciliation.

**Consumes:** Existing workflow clear APIs, Workspace.reconcile tagged outcomes and accepted Event.
**Produces:** All shared Recovery APIs and State, reused disabled operation UI with recovery
framing/copy/discard, and route retention. Resume preparation remains rejected without losing the
snapshot until its behavior is supplied by Tasks 2/3.

- [x] **Add RED capture and recovery coverage.**

  - Cover creation capture, no stale save, repeated notification retention, navigation and discard.
  - Use `ConnCase`, `log_in_user(user_fixture())`, and existing fixtures/imports.
  - Exercise root dispatch rather than raw HTML. Start with this concrete test:

  ```elixir
  test "List loss exposes draft and rejects the hidden creation submission", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")
    view |> form("#task-form", task: %{title: "Keep this"}) |> render_change()
    Taskman.Repo.delete!(lost)
    send(view.pid, %Taskman.ChangeNotifications.Event{
      entity: :list, operation: :updated, project_id: project.id,
      entity_id: lost.id, lock_version: nil, fields: [:name]
    })
    render(view)
    assert has_element?(view, "#task-recovery #task-title[value='Keep this']")
    refute has_element?(view, "#task-form")
    render_submit(view, "save_task", %{"task" => %{"title" => "Stale"}})
    assert {:ok, []} = Taskman.Tasks.list_tasks_for_location(project, nil)
    assert has_element?(view, "#task-recovery #task-title[value='Keep this']")
  end
  ```

  **Additional coverage and fixture constraints:**

  - Build synthetic fixtures only in SQL Sandbox.
  - Before deleting a referenced List, relocate or delete its synthetic Tasks and child Lists.
  - Add direct State tests for first capture, repeated capture, ID monotonicity, discard and invalid
    recovery IDs.
  - Add a dirty-detail test that delivers its queued title tuple after disappearance. Assert that
    recovery text retains input and no hidden `:not_found` clearing occurs.
- [x] **Confirm the focused tests are RED.** Run
  `mix test test/taskman_web/live/project_live/recovery_test.exs
  test/taskman_web/live/project_live/tasks/recovery_state_test.exs`.
- [x] **Implement capture ordering and guarded dispatch.**

  **Capture and reconciliation:**

  - `ProjectLive.Reconciliation` retains the current `Workspace.State` before calling
    `Workspace.reconcile/2` and passes it to `Recovery.enter/2` for `:location_missing`.
  - Reconcile a surviving row move before entering this path.
  - `Recovery.enter/2` assigns the result of `State.capture/2` to `socket.assigns.recovery` before
    clearing actionable owner states and the invalid derived listing. Clearing socket assigns must
    not alter the immutable captured values.
  - With no active creation or detail workflow, retain a nil snapshot and the existing not-found
    result.
  - Keep existing validators and subscriptions.

  **Guarded root-dispatch shape:**

  ```elixir
  @task_workflow_events @creation_events ++ @editing_events ++
                          @parent_selection_events ++ @movement_events
  # Place before the existing owner clauses; do not catch unknown events.
  def handle_event(event, _params, %{assigns: %{recovery: %{snapshot: snapshot}}} = socket)
      when event in @task_workflow_events and not is_nil(snapshot),
      do: {:noreply, socket}
  ```

  **Blocking behavior:**

  - Existing owner clauses also use `Recovery.blocked?/1` for an unavailable location or no Project.
  - Treat the guard as a server backstop. No template may expose an enabled action control whose
    event it suppresses.
  - Active recovery supplies the explanation and absent or semantically disabled action controls.
    Unavailable/no-Project states retain their existing not-found or empty presentation.
  - Scheduled autosave dispatch returns unchanged state when blocked.
  - During active recovery, skip Movement, Editing, picker and hierarchy notification work while
    allowing workspace navigation to continue.

  **Route behavior:**

  - Normal root route resolution continues without flush and skips Task `apply_action` while a
    snapshot exists.
  - Ordinary Project/List browsing patches retain the shell and snapshot.
  - A new-task or Task-detail route without matching pending recovery intent resolves its valid
    backdrop and uses `push_patch(..., replace: true)` to the corresponding browse path. It must not
    remain as a mismatched canonical URL.
  - Matching pending routes continue to `complete_route`.
  - Preserve the snapshot across all same-LiveView patches.
- [x] **Render the recovery shell through the existing operation components.**

  **Shell ownership and placement:**

  - Render `task-recovery`, `task-recovery-copy`, `task-recovery-copy-status` and
    `task-recovery-discard` IDs.
  - The shell is the recovery explanation, lifetime/error text and controls around the existing
    creation or detail surface—not another Task editor.
  - Place it in ProjectLive's existing Task-operation/modal slot, replacing ordinary active-operation
    rendering. Do not add a separate panel or duplicate the operation UI.

  **Reused component behavior:**

  - Add recovery-display mode to the existing Form, Detail, ParentPicker and MovePopover composition.
  - Keep captured invalid values and conflicts visible and selectable.
  - Disable mutation, picker, conflict, move, cancel and autosave controls.
  - Reuse the components' field rendering without emitting actionable `#task-form`. Creation fields
    and destination selection render within `#task-recovery-form`; the existing detail component
    renders its same shared fields through a non-actionable recovery wrapper. Do not copy field
    markup into `Tasks.Recovery`. This prevents nested forms and reserves `#task-form` for an
    ordinary resumed workflow in templates and tests.

  **Lifetime and dismissal:**

  - Disable or ignore ordinary modal close/cancel, backdrop dismissal, and captured popover
    close/cancel.
  - Same-LiveView navigation may change the backdrop but must retain the shell.
  - Only successful restoration or explicit discard removes the shell.
  - State “Recovery is temporary. Reloading, reconnecting, or leaving Taskman may lose it.” in the
    shell, never as a permanent application alert.
  - Do not call the active recovery UI or its operation “suspended.”
  - Discard clears owner states, preserves sequence, then browses the current valid location or source
    Project root. Invalid or inactive recovery events no-op.
- [x] **Implement clipboard behavior without another state owner.**

  - Derive field-labelled copy text only while a snapshot is active.
  - On every server render, refresh `#task-recovery[data-copy-value]` from the current snapshot
    presentation. Never store it as separate authoritative state.
  - Define a recovery-specific colocated `.CopyRecovery` hook in `Tasks.Recovery`.
  - Its uniquely identified `phx-update="ignore"` controls region reads the latest value from the
    closest LiveView-managed shell, calls `navigator.clipboard.writeText`, reports success or failure
    through `task-recovery-copy-status` with `aria-live`, and permits retry.
  - After failure, keep existing fields visible and selectable. Do not reveal a fallback textarea or
    add an `app.js` hook.
  - Cover the low-level hook and changing-copy-value contracts without style assertions.
- [x] **Extend the Task 1 acceptance table.** Cover:

  - stale validate, save, autosave, submit, conflict, parent and move events as no-ops;
  - absent or semantically disabled action controls while the recovery explanation is present;
  - direct stale-event delivery as a server no-op, without an enabled dead control in the UX;
  - the shell occupying the ordinary operation slot without a parallel or duplicate Task surface;
  - unchanged behavior for malformed/unrelated notifications and root unknown callbacks;
  - close, cancel and backdrop-dismissal attempts that cannot hide active recovery;
  - navigation to another Project retaining the shell and original source identity;
  - ordinary new-task/detail routes being history-replaced with their resolved browse path, never a
    mismatched action URL;
  - no-active-workflow loss retaining the old not-found page;
  - an initial missing route creating no snapshot; and
  - explicit discard with a stale repeat as a no-op.
- [x] **Verify, review and commit Task 1.**

  - Run changed tests plus `workspace_updates_test`, `external_updates_test`, `autosave_test` and
    `reconciliation_test`.
  - Format changed files and review the diff.
  - Obtain scoped independent review.
  - Commit only reviewed file IDs through
    `but commit -b missing-list-recovery -m "Preserve input when a List disappears"`.
  - Record evidence and rulings; close the issue only after the review gate.

## Task 2: Explicit creation restoration

**Beads:** `tas-1tq.10.2`; depends on Task 1.

**Final-state note:** This completed increment records the implemented baseline. Task 4 supersedes
its creation snapshot, Resume flow, creation-specific Recovery events and `Creation.restore/3`.

**Files:**

- Modify Recovery workflow, Tasks.Recovery component, Creation and root route integration.
- Create `test/taskman_web/live/project_live/creation_recovery_test.exs`.

**Consumes:** Recovery snapshot/IDs/guards, root workspace resolution, complete_route boundary.
**Produces:** Creation.restore/3 and complete-route pending-intent machinery reused in Task 3.

- [x] **Add RED creation-restoration coverage.**

  **Core scenario:**

  - Capture a parent in a different surviving List.
  - Select an explicit destination and resume without creating a Task.
  - Submit the ordinary restored form and assert both explicit `list_id` and preserved
    `parent_task_id`.
  - Add a Project-root destination with a List-owned parent. Never impose parent/List co-location.
  - Use these concrete DOM contracts:

  ```elixir
  view |> form("#task-recovery-form", destination: "list:#{destination.id}") |> render_change()
  view |> element("#task-recovery-resume") |> render_click()
  assert_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/new")
  assert has_element?(view, "#task-form #task-title[value='Keep this']")
  assert {:ok, []} = Tasks.list_tasks_for_location(project, destination)
  view |> form("#task-form", task: %{title: "Keep this"}) |> render_submit()
  assert {:ok, [%{task: created}]} = Tasks.list_tasks_for_location(project, destination)
  assert created.parent_task_id == parent.id
  ```

  **Setup constraints:**

  - Reuse Task 1's logged-in synthetic creation flow.
  - Put the parent in a surviving List and use an empty selected/destination List.
  - Initialize the parent through existing parent query/picker controls.
- [x] **Confirm the creation-recovery test is RED.** Run
  `mix test test/taskman_web/live/project_live/creation_recovery_test.exs`.
- [x] **Implement fresh destination and parent validation.**

  - Build source-Project options fresh, with no default destination.
  - The choose event updates only snapshot selection.
  - Resume refetches the source Project, chosen List and parent.
  - Use this canonical destination parser:

  ```elixir
  case destination do
    "project" -> {:ok, nil}
    "list:" <> id ->
      case Integer.parse(id) do
        {value, ""} when value > 0 ->
          if Integer.to_string(value) == id do
            case Lists.get_list_for_project(project, value) do
              nil -> {:error, :destination_not_found}
              task_list -> {:ok, task_list}
            end
          else
            {:error, :destination_not_found}
          end
        _ -> {:error, :destination_not_found}
      end
    _ -> {:error, :destination_not_found}
  end
  ```

  **Parent rules:**

  - A fresh parent may reside anywhere in the source Project.
  - A missing parent retains the snapshot and blocks resume until explicit
    `clear_recovery_parent`; field values survive clearing.
  - Use the exact errors from the specification.
- [x] **Implement pending route intent and creation restoration.**

  **Prepare and route:**

  - Store pending mode, path identity and snapshot ID.
  - Call `push_patch` with `Paths.new_task_path(project, destination, include_children?, nil)`.
  - Root resolves workspace, skips Task `apply_action`, and always calls `complete_route` while
    recovery is active, including failed resolution.

  **Complete and restore:**

  - `complete_route` checks `live_action` and canonical Project/List/query params, revalidates
    authority, and delegates to `Creation.restore/3`.
  - Restore uses resolved workspace location, captured `form.params`, `Tasks.change_task/2`,
    `to_form/2` and `ParentPicker.open_create`; it performs no mutation.
  - On success, clear the snapshot only after installing restored owner states.
  - On error, clear pending intent and retain the input and error.
- [x] **Add creation-restoration edge-case coverage.** Cover:

  - invalid `title` and `due_at` restoration;
  - malformed, foreign or gone destinations;
  - duplicate or stale `recovery_id`;
  - parent disappearance and explicit parent clearing;
  - pending destination loss; and
  - wrong patch identity.

  For a wrong pending Task-action route, clear pending intent with the specified interruption error,
  history-replace the action URL with its valid browse path, and assert there is no Back-loop entry.
  Drive the completion race deterministically through direct root callbacks and synthetic deletion
  between prepare and complete; never sleep. Assert no automatic root fallback or Task write.
- [x] **Verify, review and commit Task 2.**

  - Run `creation_recovery_test`, `recovery_test`, `project_live_test` and
    `workspace_updates_test`.
  - Format and review the changes.
  - Obtain scoped independent review.
  - Commit locally with message
    `Resume recovered creation drafts at an explicit location`.
  - Record evidence and close the issue only after review.

## Task 3: Restore detail drafts and preserve active movement

**Beads:** `tas-1tq.10.3`; depends on Task 2.

**Final-state note:** This completed increment records the implemented explicit detail Resume
baseline. Task 5 supersedes only the successful surviving-Task interaction: the same capture,
reconciliation, conflict, timer, failed-lookup fallback, and movement reconstruction remain,
but successful restoration becomes automatic and does not display the recovery shell.

**Files:**

- Modify Recovery/component, Editing, Autosave and Movement.
- Create `test/taskman_web/live/project_live/detail_recovery_test.exs`.
- Create `test/taskman_web/live/project_live/movement_recovery_test.exs`.
- Extend `test/taskman_web/live/project_live/tasks/autosave_test.exs`.
- Update the product MVP recovery description in the final increment.

**Consumes:** Pending intent/complete-route protocol and freeze lifetime from Tasks 1/2.
**Produces:** Editing.restore/3, Autosave.resume/2, detail-move restoration, row-move continuity and
final recovery behavior.

- [x] **Add RED Autosave and detail-restoration coverage.**

  **Pure Autosave coverage:**

  - Use existing Autosave direct-test helpers for a prebuilt dirty state.
  - Assert retained dirty values, fresh clean values, conflicted external dirty values, cleared
    revisions, retained sequence and non-saving status.
  - Use this required core expectation:

  ```elixir
  resumed = Autosave.resume(captured, fresh_task)
  assert resumed.draft["title"] == "Mine"
  assert resumed.revisions == %{}
  assert resumed.sequence == captured.sequence
  assert resumed.save_state == :not_saved
  assert resumed.form[:title].value == "Mine"
  ```

  **Conflict and LiveView coverage:**

  - Add a conflict variant asserting `:conflicted` and the latest value, never `:saving`.
  - Add real LiveView detail loss with a dirty title.
  - Move the synthetic Task to a surviving List before deleting its old List.
  - Explicitly reopen it and assert its actual-location route plus unchanged persisted title.
  - Deliver the captured old autosave tuple after reopening, cross a render barrier, then assert the
    persisted title remains unchanged and the draft remains present.
  - Assert that a new edit schedules a higher revision and normal explicit save works.
- [x] **Add RED movement-continuity coverage for both invalidation axes.**

  **Destination loss:**

  - Keep the existing popover anchored.
  - Clear only the selected destination and retain its label/query as context.
  - Show “That destination is no longer available. Choose another destination.”
  - Disable submission until a fresh destination is selected.

  **Source-List or row-anchor loss:**

  - If the Task remains visible under the current Include child Lists result, retain the row and
    popover.
  - Otherwise immediately patch a surviving Task to its actual-location detail route, change the
    move origin to detail, and retain the query plus any valid destination.
  - Assert that relocation performs no move.

  **Missing Task and precedence:**

  - If the Task is gone, clear a row move and show “This Task is no longer available.” as the
    standard error flash.
  - Remain on a valid backdrop, or patch to the source Project root when the selected List is gone.
  - Add the precedence case where a dirty detail draft retains its recovery instead of collapsing
    to the row-move flash behavior.
- [x] **Confirm Task 3's initial tests are RED.** Run `detail_recovery_test`,
  `movement_recovery_test` and `tasks/autosave_test`.
- [x] **Implement Autosave and detail restoration.**

  **Autosave:**

  - Implement `Autosave.resume` through `Autosave.reconcile`.
  - Reset revisions and derive status first from conflicts, then dirty fields.
  - Do not call `change`, `flush`, `scheduled_save` or persistence.
  - Keep old timer revisions absent and allow future sequence advancement.

  **Editing:**

  - `Editing.restore` refetches the Task and hierarchy, uses the maximum live/captured sequence,
    reconciles the original baseline, and opens detail with the restored form.
  - Refresh captured parent conflict against scoped parent authority without writes.
  - A missing Task or List retains the snapshot and uses the exact specification error.
  - Do not duplicate a Task or move it to the selected location as a recovery fallback.
- [x] **Implement detail routing and movement reconstruction.**

  **Route and snapshot lifecycle:**

  - Resolve the Task's actual List/root before prepare and again at completion.
  - Use `Paths.task_detail_path/4` for detail resume.
  - Consume the snapshot only after successful owner restoration.
  - For a captured detail move, restore editing first and then rebuild the existing popover with
    fresh authority and no submit.

  **Row-movement reconciliation:**

  - Run active row-movement reconciliation on accepted List/Task notifications before the next move
    event.
  - If no notification arrived, run it at the next authority-refreshing move event.
  - If the anchor is lost, use `Paths.task_detail_path/4` for immediate relocation without a
    recovery snapshot.

  **No-write owner composition:**

  ```elixir
  move = Move.open(Move.empty(), project, fresh_task, :detail)
  {:ok, move, _} = Move.search(move, project, captured_move.query)
  # Retain destination only when its fresh option exists and is not current.
  # Assign this rebuilt Move; never call Move.submit here.
  ```

  **Destination reconstruction:**

  - Implement the option check explicitly.
  - Retain a valid captured destination.
  - If it is unavailable, retain its label/query only as context, show the inline
    destination-unavailable error, clear the selection, and disable submission until the user
    chooses a valid destination.
  - If rebuilding a captured detail move returns `:task_not_found`, keep the detail snapshot rather
    than accepting a cleared Move as successful restoration.
- [x] **Complete Task 3's acceptance coverage.** Cover:

  - notification or destination loss during pending completion;
  - simultaneous source and target loss;
  - parent Include child Lists anchor retention;
  - automatic detail relocation onto the freshly resolved List or Project-root backdrop;
  - cancel after relocation with detail still open;
  - closing detail back to that backdrop;
  - ordinary-field conflict and captured-parent conflict;
  - browser-navigation retention;
  - explicit discard; and
  - ID mismatch.

  Map every acceptance row in the specification to a test or the stated browser smoke check. Do not
  add style tests.
- [x] **Update durable product and workstream documentation.**

  - Update the product MVP with implemented recovery controls and the process-lifetime limit.
  - Keep planning IDs and internal task language out of product copy.
  - Update plan checkboxes, task state, indexes and handoff without removing rulings or remaining
    publication/completion gates.
- [x] **Run final automated and browser verification.**

  **Automated checks:**

  - Run changed suites, then `mix format --check-formatted`, the complete focused command from the
    specification, and `mix precommit`.
  - Check for planning terminology, module duplicates and broken local documentation links.
  - Inspect the final diff.

  **Browser smoke checks:**

  - keyboard copying;
  - clipboard success/failure status and retry;
  - focus on recovery entry and resume;
  - readonly invalid values;
  - navigation retention; and
  - browser Back after Task-action URL canonicalization without a history loop.

  Record uncertainty if browser tooling prevents a check; do not claim that check passed.
- [x] **Review, commit and preserve the remaining gates.**

  - Obtain scoped independent review.
  - Commit locally with message `Restore recovered Task edits and movement`.
  - Close execution issues only on accepted evidence.
  - Leave the feature and handoff open until operator completion confirmation.
  - Publication and merge require separate authorization and refreshed target state.

## Task 4: Keep creation ordinary across location loss

**Beads:** `tas-1tq.10.4`; depends on the implemented Tasks 1–3 baseline.

**Files:**

- Modify `lib/taskman_web/live/project_live/tasks/creation.ex`.
- Modify `lib/taskman_web/components/tasks/form.ex`.
- Modify `lib/taskman_web/live/project_live/reconciliation.ex`.
- Modify `lib/taskman_web/live/project_live.ex`.
- Modify `lib/taskman_web/live/project_live.html.heex`.
- Modify `lib/taskman_web/live/project_live/recovery.ex`.
- Modify `lib/taskman_web/components/tasks/recovery.ex`.
- Move and rewrite `test/taskman_web/live/project_live/creation_recovery_test.exs` as
  `test/taskman_web/live/project_live/creation_location_test.exs`.
- Modify `test/taskman_web/live/project_live/tasks/creation_test.exs`.
- Modify `test/taskman_web/live/project_live/recovery_test.exs`.
- Modify `test/taskman_web/live/project_live/workspace_updates_test.exs`.
- Modify `test/taskman_web/components/tasks/recovery_test.exs`.
- Modify `docs/product/mvp-spec.md`, this plan, the design, documentation indexes and the active
  handoff after implementation.

**Interfaces:**

- Consumes: `Workspace.reconcile/2` location outcomes, `Lists.list_lists_for_project/1`,
  `Lists.tree_order/1`, `Lists.path_for/2`, `Lists.get_list_for_project/2`, `Tasks.create_task/4`,
  `ParentPicker.selected_parent/1`, and existing `Paths.browse_path/3`.
- Produces: ordinary Creation state with `location`, `location_label`, `location_options`,
  `location_error` and combined `enabled?`; `Creation.refresh_locations/2`; detail-only Recovery;
  and deterministic post-create browse paths.

- [x] **Step 1: Add failing ordinary-location form coverage.**

  In `creation_location_test.exs`, assert that every new-Task route renders one ordinary form with a
  selected location:

  ```elixir
  test "creation exposes an explicit location without changing its backdrop", %{conn: conn} do
    project = project_fixture(%{})
    current = list_fixture(project, nil, %{name: "Current"})
    other = list_fixture(project, nil, %{name: "Other"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{current.id}/tasks/new")

    assert has_element?(view, "#task-form #task-location")
    assert has_element?(view, "#task-location option[value='project']", "Project #{project.name}")
    assert has_element?(view, "#task-location option[value='list:#{current.id}'][selected]")

    view
    |> form("#task-form", location: "list:#{other.id}", task: %{title: "Elsewhere"})
    |> render_change()

    assert has_element?(view, "#task-location option[value='list:#{other.id}'][selected]")
  end
  ```

  Add Project-root and Add-subtask variants. For Add subtask, assert that the parent and its current
  location are initial defaults, then change location and prove the parent remains selected. Change
  or clear the parent and prove location remains unchanged. A browser assertion owns the separate
  requirement that changing this select does not change the URL before submission.

- [x] **Step 2: Add failing selected-location disappearance coverage.**

  Replace creation snapshot expectations with the ordinary form contract:

  ```elixir
  test "List loss keeps creation editable and requires another location", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project, nil, %{name: "Lost"})
    destination = list_fixture(project, nil, %{name: "Destination"})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/new")

    view
    |> form("#task-form", location: "list:#{lost.id}", task: %{
      title: "Keep this",
      description: "Still editable"
    })
    |> render_change()

    Taskman.Repo.delete!(lost)
    send(view.pid, list_event(project.id, lost.id))
    render(view)

    assert has_element?(view, "#task-form #task-title[value='Keep this']")
    assert has_element?(view, "#task-description", "Still editable")
    assert has_element?(view, "#task-location[aria-invalid='true']")
    assert has_element?(view, "#task-location-error", "This List is no longer available")
    assert has_element?(view, "#create-task[disabled]")
    refute has_element?(view, "#task-recovery")

    view
    |> form("#task-form", location: "list:#{destination.id}", task: %{
      title: "Keep this",
      description: "Edited after loss"
    })
    |> render_change()

    refute has_element?(view, "#task-location-error")
    assert has_element?(view, "#create-task:not([disabled])")
  end
  ```

  Assert repeated notifications preserve form parameters, invalid ordinary values, selected parent
  and the last-known missing label. Assert Cancel patches to Project root when the backdrop List is
  missing and to the unchanged backdrop when it remains valid.

- [x] **Step 3: Add failing fresh-submit and post-create navigation coverage.**

  Cover canonical Project-root and List keys, malformed keys, foreign Lists, and a destination
  deleted after `render_change` but before `render_submit`. Every invalid case retains the ordinary
  form, marks location invalid and leaves the Project's Task count unchanged.

  Build a root List, child, grandchild and unrelated List, then exercise these location-only rules:

  ```elixir
  # Direct destination: stay on the current backdrop.
  assert_patch(view, Paths.browse_path(project, current, false))

  # Descendant destination with Include child Lists: stay on the current backdrop.
  assert_patch(view, Paths.browse_path(project, current, true))

  # Descendant while Include child Lists is off: browse destination and preserve `false`.
  assert_patch(view, Paths.browse_path(project, destination, false))

  # Unrelated destination or missing backdrop: browse destination and preserve the active setting.
  assert_patch(view, Paths.browse_path(project, destination, true))
  ```

  Add Project-root equivalents: a root Task is directly visible; a List Task is visible only when
  Include child Lists is enabled. Add an active status filter that excludes the created Task and
  assert it does not change the otherwise applicable location decision.

- [x] **Step 4: Run the new tests and confirm they fail for the superseded behavior.**

  Run:

  ```sh
  mix test test/taskman_web/live/project_live/creation_location_test.exs \
    test/taskman_web/live/project_live/tasks/creation_test.exs \
    test/taskman_web/live/project_live/workspace_updates_test.exs
  ```

  Expected: failures show the absent `#task-location`, creation entering `#task-recovery`, stale
  location authority, or unconditional current-backdrop navigation. Investigate any unrelated
  failure before implementation.

- [x] **Step 5: Make location explicit ordinary Creation state.**

  Replace the cached `%TaskList{} | nil` mutation authority with presentation state:

  ```elixir
  defstruct form: nil,
            enabled?: false,
            location: nil,
            location_label: nil,
            location_options: [],
            location_error: nil
  ```

  Use canonical strings (`"project"`, `"list:<positive-id>"`). Build options from Project root plus
  `Lists.tree_order/1`. Use the exact pure state contracts
  `State.open(state, form, location, location_label, location_options)`,
  `State.validate(state, form)`, `State.choose_location(state, location, location_options)` and
  `State.refresh_locations(state, location_options)`. Each constructor or transition derives the
  final `enabled?` from both `form.source.valid?` and whether `location` is currently available. A
  missing selected option retains its key and last-known label as an invalid, disabled option; a
  fresh selection clears only `location_error`.

  Render `#task-location` near the top of `#task-form` with label “Location”. Associate
  `#task-location-error` through `aria-describedby`, set `aria-invalid="true"` when invalid, and use
  the exact copy “This List is no longer available. Choose another location.” Keep the standard
  title-focus hook and every other input editable.

- [x] **Step 6: Resolve fresh authority and the correct post-create path.**

  Make both `validate_task` and `save_task` accept top-level `location` beside nested `task` params.
  Parse canonical positive IDs; resolve Lists through `Lists.get_list_for_project/2` immediately
  before `Tasks.create_task/4`. Never use option or stale display structs as mutation authority.

  Add `Creation.post_create_path(project, workspace, destination, task_lists)`. After success, it
  computes location visibility from the valid current workspace and fresh Project Lists:

  ```elixir
  cond do
    workspace.location_not_found? ->
      Paths.browse_path(project, destination, workspace.include_children?)
    direct_location?(workspace.selected_list, destination) ->
      Paths.browse_path(project, workspace.selected_list, workspace.include_children?)
    workspace.include_children? and
        descendant_location?(workspace.selected_list, destination, task_lists) ->
      Paths.browse_path(project, workspace.selected_list, workspace.include_children?)
    true -> Paths.browse_path(project, destination, workspace.include_children?)
  end
  ```

  Define `direct_location?(selected_list, destination)` as equality between two canonical
  locations, where `nil` represents Project root. Define
  `descendant_location?(selected_list, destination, task_lists)` to return false for a root
  destination, true for any List destination when `selected_list` is root, and otherwise to use
  `Lists.path_for(task_lists, destination)` to establish that the selected List occurs before the
  destination in its fresh ancestry path. The caller invokes this helper only when Include child
  Lists is enabled. Do not inspect status filters. On invalid authority, retain Task params and
  ParentPicker state, assign the location error, and perform no write or patch.

- [x] **Step 7: Reconcile List loss without entering creation recovery.**

  In Reconciliation, preserve Movement precedence. For `:location_missing`, route active
  `:new_task` state through `Creation.refresh_locations/2` and `Listing.clear/1`; do not call
  `Recovery.enter/2`, `Creation.clear/1` or `ParentSelection.clear/1`. Continue to use detail
  Recovery for `:show_task` with unsaved input.

  Render the ordinary create modal whenever `@live_action == :new_task`, the Project remains valid,
  and `@creation.form` exists—even if `@workspace.location_not_found?`. Remove the explanatory
  `#task-create-location` paragraph because `#task-location` is authoritative. Use a Creation-owned
  cancel-path helper so a missing backdrop closes to Project root. Adjust event blocking so a missing
  List alone does not suppress the visible Creation and ParentPicker controls.

- [x] **Step 8: Remove creation-only Recovery branches.**

  Delete creation capture and presentation, `State.destination`, `choose_destination/2`,
  `clear_parent/1`, creation pending intents, `Creation.restore/3`, and the
  `choose_recovery_destination` / `clear_recovery_parent` events. `resume_task_recovery` becomes
  detail-only. Simplify `Tasks.Recovery.shell/1` and the modal size contract to detail only.

  Rewrite Recovery state/component tests to assert no creation mode remains. Retain detail copy,
  discard, focus, interrupted-route, missing-Task, conflict and captured-move behavior unchanged.
  Remove obsolete creation-stale-event assertions only where the ordinary editable form now owns the
  event; do not weaken blocked detail-event coverage.

- [x] **Step 9: Run focused and covering tests.**

  Run:

  ```sh
  mix test test/taskman_web/live/project_live/creation_location_test.exs \
    test/taskman_web/live/project_live/tasks/creation_test.exs \
    test/taskman_web/live/project_live/recovery_test.exs \
    test/taskman_web/live/project_live/detail_recovery_test.exs \
    test/taskman_web/live/project_live/movement_recovery_test.exs \
    test/taskman_web/live/project_live/workspace_updates_test.exs \
    test/taskman_web/live/project_live/project_live_test.exs \
    test/taskman_web/components/tasks/recovery_test.exs
  ```

  Expected: all pass. Then run `mix format --check-formatted` and the complete focused command from
  the specification.

- [x] **Step 10: Update durable creation behavior and verify it in the browser.**

  Update `docs/product/mvp-spec.md` only after the code passes: describe the always-visible creation
  location, editable invalidation on List loss, fresh submit check and location-scope navigation;
  reserve Copy/Discard/Reopen and temporary recovery wording for failed fresh Project, Task, or
  Task-location lookup.

  In the Orca Taskman tab, rebuild the synthetic creation case. Confirm the same ordinary dialog
  remains editable after its List disappears, the missing location is invalid, Create is disabled,
  selecting another location permits creation, and the resulting route follows the visibility rule.
  The ordinary creation scenario passed browser review on 2026-09-22. Its resulting Task was
  created at the selected destination with title and description retained and the visibility-based
  route selected. The subsequent dirty-detail dialog was rejected; its replacement and the
  remaining movement simulations move to Task 5.

- [x] **Step 11: Complete verification, review and the local commit gate.**

  Run `mix precommit`, scan implementation-facing files for leaked planning terminology, inspect the
  Task 4 diff, and obtain independent scoped review plus fix re-review if needed. Update
  `tas-1tq.10.4`, the documentation index and active handoff with exact evidence and remaining browser,
  publication, merge, ruling and completion gates.

  Commit the reviewed bounded change through GitButler with message:

  ```text
  Keep Task creation editable across List loss
  ```

  The scoped review, two task fix re-reviews, native recovery-background inertness re-review,
  focused checks and `mix precommit` passed. The bounded implementation and subsequent browser
  checkpoints are committed locally. Nothing is pushed. Do not merge, close the parent workstream,
  retire the handoff or skip Task 5 and the remaining browser simulations without their gates.

## Task 5: Continue surviving detail drafts automatically

**Beads:** `tas-1tq.10.5`; depends on completed Task 4.

**Files:**

- Create `lib/taskman_web/live/project_live/tasks/location_scope.ex`.
- Create `test/taskman_web/live/project_live/tasks/location_scope_test.exs`.
- Modify `lib/taskman_web/live/project_live/tasks/creation.ex`.
- Modify `lib/taskman_web/live/project_live/reconciliation.ex`.
- Modify `lib/taskman_web/live/project_live/recovery.ex`.
- Modify `lib/taskman_web/live/project_live/tasks/editing.ex` only if its existing restoration return
  contract cannot distinguish failed fresh lookups from a route race.
- Modify root route integration in `lib/taskman_web/live/project_live.ex` and
  `lib/taskman_web/live/project_live.html.heex` only as required to bypass the exceptional shell on
  successful automatic continuation.
- Modify `test/taskman_web/live/project_live/creation_location_test.exs`,
  `detail_recovery_test.exs`, `movement_recovery_test.exs`, and focused owner tests.
- Update `docs/product/mvp-spec.md`, this plan, the design, indexes, Beads, and the active handoff only
  after behavior is implemented and verified.

**Consumes:** the implemented detail snapshot, `Editing.restore/3`, `Autosave.resume/2`,
`Paths.task_detail_path/4`, workspace location state, fresh scoped Project/List/Task lookups, and
Creation's current direct/descendant behavior.

**Produces:** one shared location-scope policy; automatic no-write continuation to ordinary detail;
visibility-based backdrop retention or replacement; and exceptional recovery only for unresolved
authority.

- [x] **Step 1: Add RED pure location-scope coverage.**

  In `location_scope_test.exs`, build root, child, grandchild and unrelated locations and cover:

  - Project root and identical List locations are directly visible;
  - a List destination is visible from Project root only with Include child Lists;
  - a child or transitive grandchild is visible from an ancestor List only with Include child Lists;
  - an ancestor, sibling, unrelated List, and Project-root Task are not descendants of a selected
    List;
  - a missing previous backdrop selects the actual Task location;
  - a visible Task retains the previous backdrop; an out-of-scope Task selects its actual location;
  - Project-root and List fallbacks preserve the caller's Include child Lists value in route
    composition; and
  - no status-filter input exists in this API.

  Use exact contracts:

  ```elixir
  LocationScope.visible?(selected_list, actual_list, include_children?, task_lists)
  LocationScope.backdrop(workspace, actual_list, task_lists)
  ```

  Run `mix test test/taskman_web/live/project_live/tasks/location_scope_test.exs` and confirm the
  missing module/API is the only expected failure.

- [x] **Step 2: Implement the policy and refactor Creation without changing behavior.**

  Move Creation's private direct and descendant predicates into `Tasks.LocationScope`. Represent
  Project root as `nil`, use fresh `Lists.path_for/2` ancestry, and keep every function pure.
  `backdrop/3` must return the current selected List/root only when it still exists and `visible?/4`
  succeeds; otherwise return the actual Task location. Do not inspect Listing state or status
  filters.

  Refactor `Creation.post_create_path/4` to use this policy. Run the new unit suite plus
  `creation_location_test.exs` and `tasks/creation_test.exs`; all existing creation route assertions
  must remain unchanged.

- [x] **Step 3: Add RED automatic detail-continuation coverage.**

  Extend `detail_recovery_test.exs` with isolated LiveView outcomes:

  - a dirty Task still directly located in the current backdrop returns immediately to ordinary
    `#task-form` on the same route;
  - a Task moved to a child or transitive grandchild keeps the backdrop only when Include child
    Lists is enabled;
  - the same descendant with Include child Lists disabled relocates to its actual List;
  - an unrelated actual List relocates to that List;
  - a disappeared previous backdrop relocates to the actual List or Project root;
  - Project-root direct and descendant equivalents follow the same rule;
  - an active status filter that excludes the Task does not change the route decision; and
  - every retained or changed route preserves the current Include child Lists setting.

  Each successful case must assert ordinary actionable detail, dirty values retained, fresh
  persisted values reconciled into existing conflicts, no `#task-recovery`, and unchanged persisted
  Task fields. Deliver the previously queued autosave tuple after continuation and prove that it
  performs no write.

  Add route-race cases in which the Task moves again or the chosen backdrop disappears between
  prepare and completion. Assert replacement patch convergence to the freshly valid route, no
  history loop, no stale editor, and no write.

  Run `mix test test/taskman_web/live/project_live/detail_recovery_test.exs`; failures must describe
  the still-visible explicit recovery shell or wrong backdrop, not fixture/setup errors.

- [x] **Step 4: Add RED exceptional fallback and captured-move coverage.**

  Preserve recovery-shell assertions only when the Project, Task, or Task's current List/root still
  cannot be resolved after fresh lookups:

  - missing Project, missing Task, and unresolved actual List retain the inert snapshot;
  - Copy and Discard retain their existing contracts;
  - Try again retries the same automatic policy for unavailable Project/location or restoration
    failure and keeps the snapshot on failure; confirmed missing-Task recovery offers only Copy and
    Discard;
  - if authority later resolves, a retry restores ordinary detail without writing; and
  - mismatched or stale recovery IDs remain no-ops.

  For captured detail movement, assert that successful automatic continuation first restores the
  draft, then reconstructs the existing move popover with fresh destination authority, query and any
  still-valid destination. It must not call `Move.submit/2`; the ordinary later movement gate still
  owns conflict/flush behavior.

- [x] **Step 5: Implement automatic continuation and exceptional fallback.**

  Keep `Recovery.enter/2` capture-first: invalidate actionable Editing, ParentPicker and Movement
  copies and stale autosave revisions before resolution. Resolve the scoped Project, fresh Task,
  actual List/root and `LocationScope.backdrop/3`, then:

  - restore immediately in place when the existing route already has the chosen backdrop;
  - otherwise prepare a validated internal intent and replacement-patch to
    `Paths.task_detail_path/4` for that backdrop;
  - revalidate all authority in `Recovery.complete_route/2` before `Editing.restore/3`;
  - consume the snapshot only after successful owner reconstruction;
  - render no recovery shell on that successful path; and
  - retain the inert snapshot plus a precise error when the Project, Task, or Task location cannot
    be resolved.

  `resume_task_recovery` becomes a retry of this same resolver where the UI offers Try again. It
  offers no destination and performs no save or move. Preserve maximum autosave sequence, clear
  captured revisions, retain dirty and conflict state, and ignore old scheduled tuples. Restore
  captured Movement only after Editing.

- [x] **Step 6: Run focused and full verification.**

  Automated verification completed on 2026-09-22. After the scoped review and its fix wave, the
  focused command passed with 80 tests, formatting passed, and `mix precommit` passed with 890
  tests. Scoped re-review found the route-interruption, capture-order, and observable-matrix
  findings resolved.

  Run:

  ```sh
  mix test test/taskman_web/live/project_live/tasks/location_scope_test.exs \
    test/taskman_web/live/project_live/creation_location_test.exs \
    test/taskman_web/live/project_live/detail_recovery_test.exs \
    test/taskman_web/live/project_live/movement_recovery_test.exs \
    test/taskman_web/live/project_live/recovery_test.exs \
    test/taskman_web/live/project_live/tasks/autosave_test.exs \
    test/taskman_web/live/project_live/tasks/creation_test.exs
  mix format --check-formatted
  mix test test/taskman_web/live/project_live test/taskman_web/components/tasks \
    test/taskman_web/components/workspace_navigation_test.exs
  mix precommit
  ```

  Inspect persistence in the tests rather than inferring no-write behavior from UI. Scan production
  and test names for planning terminology. Inspect the bounded diff and obtain scoped independent
  review plus fix re-review for any finding.

- [x] **Step 7: Inspect the first revised browser surface and capture the product correction.**

  The relocation case returned to ordinary Task 34 detail with both draft values and the correct
  route, but exposed one form-wide `Not saved` message and left the draft inert until another edit.
  Product review rejected that behavior: successful reconciliation must restart autosave, and every
  changed input must own its `Saving…`, `Saved`, or `Not saved` feedback independently. Task 6
  implements this amendment before the remaining browser matrix continues.

- [x] **Step 8: Update durable state and commit the bounded increment after Task 6 acceptance.**

  Update product documentation only to implemented behavior. Record commands and browser evidence
  in the plan, Bead and active handoff; retain publication, merge, ruling-acknowledgement and
  completion gates. Commit through GitButler with a concise behavior-focused message. Do not push,
  merge, close the parent workstream, or retire the handoff without their separate operator gates.

## Task 6: Restart reconciled saves with field-local lifecycle feedback

**Beads:** `tas-1tq.10.5`; amends Task 5 after its first browser inspection.

**Files:**

- Modify `lib/taskman_web/live/project_live/tasks/autosave.ex`.
- Modify `lib/taskman_web/live/project_live/tasks/editing.ex`.
- Modify `lib/taskman_web/components/tasks/form.ex`.
- Modify `lib/taskman_web/components/tasks/detail.ex`.
- Extend `test/taskman_web/live/project_live/tasks/autosave_test.exs`.
- Extend `test/taskman_web/live/project_live/detail_recovery_test.exs`.
- Extend `test/taskman_web/components/tasks/detail_test.exs` and focused form-component coverage.

**Consumes:** Task 5's fresh-authority reconstruction, captured dirty fields, conflict map,
monotonic autosave sequence, ordinary debounced/immediate save paths, and stable input IDs.

**Produces:** automatic post-reconciliation saving through fresh revisions and an independent
save lifecycle in a stable right-aligned slot on each changed editable input's label line.

- [x] **Step 1: Add RED Autosave lifecycle tests.**

  Add focused owner tests proving:

  - reconciliation itself performs no write and invalidates every captured revision;
  - after successful owner restoration, each valid nonconflicted dirty field receives a fresh
    normal save action with a revision greater than all captured/live revisions;
  - debounced title and description fields can be pending simultaneously and save independently;
  - valid non-debounced fields use their existing immediate persistence path after restoration;
  - an invalid dirty field remains dirty with field state `:not_saved` and receives no save action;
  - a conflicted field receives no save action and retains its conflict notice;
  - a persistence failure marks only the affected field `:failed`; and
  - saving or completing one field does not overwrite another field's state.

  Run the Autosave owner suite and confirm failures describe the missing field lifecycle and restart
  API, not fixture setup.

- [x] **Step 2: Implement independent field state and restart actions.**

  Replace form-wide lifecycle as the rendering authority with a per-field state map for changed
  editable fields. Preserve aggregate state only where workflow control still needs it. Update the
  ordinary change, scheduling, persistence, failure, conflict-resolution, clear, reconcile, and
  reload transitions so each operation changes only its own field entry. Keep a successful field's
  `:saved` presentation state until that same field changes again or another detail editor loads.

  Keep `Autosave.resume/2` pure and write-free. Add
  `Autosave.restart(autosave, project, fresh_task)`, returning
  `{:ok, autosave, task, [{delay_ms, message}]}` or `{:not_found, autosave}`. It immediately handles
  eligible nondebounced fields through their existing path and returns fresh schedules for eligible
  debounced fields. Add `Autosave.field_state(autosave, field)` returning
  `:saving | :saved | :not_saved | :failed | nil`; conflicts continue through the conflict map.
  Editing performs restart only after successful fresh reconciliation, installs the returned owners,
  and schedules every returned message. Never reuse a captured revision or queued tuple.

- [x] **Step 3: Add RED component and LiveView outcome tests.**

  Component tests must assert stable selectors such as
  `#task-title-save-status`, `#task-description-save-status`,
  `#task-status-save-status`, `#task-priority-save-status`, and
  `#task-due-at-save-status` directly after their respective controls. Each rendered message is a
  polite live region with its own `data-state`. Untouched fields render no status, and the old
  `#task-save-status` form footer is absent.

  In `detail_recovery_test.exs`, restore title and description together after relocation. Assert
  both fields report `Saving…`, deliver their fresh tuples independently, and verify each transitions
  to `Saved` only when its own persisted value changes. Add mixed invalid/conflicted cases proving
  their field-local `Not saved`/conflict UI remains while an unrelated valid field saves. Deliver
  the old captured tuple and prove it remains ignored.

- [x] **Step 4: Render field-local lifecycle feedback.**

  Pass the field-state map from Detail into Form. Render a small reusable field-status component at
  the right end of each input's label line and before its conflict notice. Keep its empty slot
  mounted and invisible so lifecycle transitions do not move the form. Match the label typography
  and input gap. Use concise existing copy: `Saving…`, `Saved`, `Not saved`, and `Couldn’t save
  changes`; distinguish them with muted amber, emerald, and rose while retaining text as the
  non-color indicator. Untouched and conflicted fields show no visible lifecycle text; the existing
  conflict notice remains authoritative. Remove the form-wide save-status footer.

- [x] **Step 5: Verify, review, and commit the amendment.**

  Run the changed owner, component, and recovery suites; run the complete ProjectLive/component
  command and `mix precommit`. Inspect persistence rather than inferring it from rendered status.
  Obtain scoped independent review and fix-wave re-review for any finding. Update product docs,
  Beads, this plan, and the active handoff only to verified behavior, then commit locally through
  GitButler. Do not push or merge.

  Implemented through TDD in commits `d0df3923`, `5b4ec0c8`, and `5a715cd2`. The scoped review
  found a malformed due-date status ID, same-editor movement clearing saved presentation, and two
  coverage gaps; two fix re-reviews found every item addressed. Fresh controller verification
  passed 331 focused ProjectLive/component tests and `mix precommit` with 897 tests.

- [x] **Step 6: Resume browser acceptance one materially different surface at a time.**

  Rebuild Task 34's relocation case with a long delay. Confirm title and description independently
  show `Saving…`, then `Saved`, and that fresh persistence contains both values. Inspect an invalid
  or conflicted field beside a valid field, then continue the direct, included-descendant,
  exceptional-fallback, and movement cases from the handoff. Pause for operator inspection at each
  materially different surface.

  The first amended relocation surface passed on 2026-09-22 using Task 35 and a 60-second delay.
  The browser replacement-patched from removed List 18 to surviving List 12, retained both draft
  values, showed independent `Saving…` then `Saved` states, and rendered no recovery shell. A direct
  read confirmed both values persisted at List 12 while List 18 remained absent. The surface is
  paused for operator inspection before the remaining matrix continues. Operator inspection found
  the behavior mostly correct but withheld acceptance: lifecycle text must be right-aligned, and
  sufficiently wide Task headers should display the selected Task's containing List path so a move
  visibly changes its context. The approved responsive breadcrumb amendment is implemented in
  commits `091bb705` and `1b333c6b`: Project/List segments navigate through the existing flush
  gate; small displays retain the containing location; wide overflow removes whole leading
  segments; Project-root Tasks use the navigable Project name; movement refreshes the path; and
  lifecycle text is right-aligned. Its scoped review and fix re-review are clean, 901 tests pass,
  and browser checks cover route-flush persistence plus extra-small, small, wide-fit and
  wide-overflow behavior. Default-viewport refinement then left-aligned the path, equalized segment
  typography while muting ancestors, made Move Task a compact secondary button, reduced the
  header-to-form gap, and moved lifecycle feedback onto a stable label-line slot. Browser
  measurements confirmed identical downstream control positions before and after `Saved`, exact
  12px/18px label/status typography with a 4px control gap, and amber/rose/emerald states. The
  operator accepted this surface. Continue the remaining matrix one materially different case at
  a time.

  The mixed invalid/valid case is now verified in the browser on Task 36. Its whitespace-only title
  stayed invalid with `Not saved` and `can't be blank`, while the valid description restarted at
  `Saving…` and reached `Saved` after the Task moved from removed List 19 to surviving List 12.
  A direct context read confirmed the new description persisted, the original title remained
  stored, and List 19 was absent. The operator accepted the outcome and requested equal bottom and
  side padding in the detail panel. The final fieldset now drops its extra bottom spacing through a
  structural `:last-child` selector; the browser measures 28px on both edges. `mix assets.build`
  and fresh `mix precommit` pass with 902 tests.

  The operator accepted the direct-location case on Task 38.
  Its original title was confirmed persisted before source child List 21 was removed. With Include
  child Lists enabled on surviving List 12, the Task moved directly into that List; the route and
  browse setting stayed unchanged, the ordinary editor retained the unsaved title without a
  recovery shell, and its field status went from `Saving…` to `Saved`. A fresh context read confirmed
  the new title persisted at List 12 and List 21 was absent. List 12 is named `Recovery destination
  · trigger 3`; it appeared in both breadcrumb and backdrop because the Task ended directly there.

  The operator accepted the included-descendant case on Task 39.
  It began in child List 22 with an unsaved title; a direct context read confirmed the original
  persisted title. After Task 39 moved to sibling child List 23 and List 22 was removed, the List 12
  backdrop and Include child Lists setting stayed unchanged, while the breadcrumb named List 23.
  Ordinary detail retained the draft with no recovery shell, advanced `Saving…` to `Saved`, and a
  fresh context read confirmed the new title persisted in List 23 while List 22 was absent.

  The first exceptional fallback surface on Task 40 was browser verified, then rejected by the
  operator during inspection.
  Its unsaved title was present only in the browser before Task 40 and temporary List 24 were
  removed and the accepted List update notification was published. Fresh reads found neither
  record. The shell retained the title, but repeated recovery titles and stale source path, showed
  `Saving…` for a deleted Task, and offered a misleading Reopen action. The revised shell has one
  missing-Task heading and one temporary-recovery explanation, retains the fields without ordinary
  Task header/activity/save status, and offers Copy and Discard only. A fresh browser fixture on
  Task 43, removed with temporary List 27 after its title edit, showed the first revised surface
  for operator inspection. A direct read confirmed the title remained unsaved before removal; fresh
  reads confirmed Task and List absent afterward. Copy reported `Copied.`. Movement remains next
  after acceptance.

  The operator found the revised structure much better and requested a muted red or orange heading
  for the exceptional case. The heading now uses Tailwind orange-300. After the development reload,
  Task 44 was rebuilt in temporary List 28 with an unsaved title before both records were removed.
  Fresh reads confirmed them absent, the browser retained the draft with no save/reopen cue, and
  computed style confirmed the orange heading. The operator found the color mostly good but asked
  for Discard to look like a button before hover; the revised recovery control has a resting rose
  border and fill. A fresh Task 45 fixture in temporary List 29 retained its unsaved title after
  both records were removed; computed style confirmed the resting border and fill. Inspect this
  revised control before continuing movement. The operator accepted Task 45's recovery surface.
  The first movement case is paused on Task 46 in surviving List 30: the row popover selected
  temporary List 31, which was then removed. The accepted notification left the Task in List 30,
  retained the chosen label and popover, showed the inline unavailable-destination error, and
  disabled Move Task. Operator review found two borderless Cancel actions, also present during
  ordinary movement. The shared popover now retains only the footer Cancel beside Move Task and
  gives it a visible resting border and fill. After the development reload, Task 46's case was
  rebuilt with temporary destination List 32, then that List was removed. The inline error and
  disabled Move Task remain visible with one styled Cancel. Inspect this surface before the
  source/row-anchor-loss case.

  The operator found the revised Cancel treatment much better and requested horizontal dividers
  around destination selection and a floating options list. The shared popover now separates
  header, field group, and footer with full-width rules; its bordered options list is anchored
  beneath the search input. Browser measurements on Task 46's row popover confirmed that expanding
  four options leaves the popover and Cancel button at the same coordinates. The same list remains
  visible inside the detail modal without clipping. The row popover is left expanded for inspection.
  Operator review then found that clicking inside the dialog but outside the options did not dismiss
  the list. A click-away owner around the input/list now closes only destination options, preserving
  the active move and query. Browser interaction on Task 46 confirmed clicking the heading collapses
  the list while leaving the dialog open; the list is expanded again for inspection.

  The operator accepted the remaining movement cases: successful row and detail moves followed the
  Task's visible destination; a lost source or row anchor relocated the active popover to Task
  detail; a concurrent move into the selected destination retained its label, disabled submission,
  and explained why in muted orange. Missing moving Tasks closed the popover and showed the standard
  error, staying on a surviving List or routing to the Project root when that List also disappeared.
  The standard error flash uses a muted red with readable pale text. The final browser case and
  `mix precommit` with 906 tests passed on 2026-09-23. All agreed browser surfaces are accepted;
  workstream ruling acknowledgement, closure, publication, and merge remain separate gates.

## Preflight coverage and execution gate

Tasks 1–6 and the agreed browser matrix are the verified local implementation baseline. Task 6's
automatic restart and field-local lifecycle amendment passed scoped review and two fix re-reviews;
the later responsive location, movement, and exceptional UI refinements passed focused and browser
verification. The fresh `mix precommit` gate passes with 906 tests. Ruling acknowledgement, task
closure, publication, merge, completion confirmation, and handoff retirement remain separately
gated in the active handoff.

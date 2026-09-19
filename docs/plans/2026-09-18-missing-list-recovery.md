# Missing List Recovery Implementation Plan

Execute sequentially with fresh implementation and independent scoped verification per increment.
Steps use checkbox syntax for tracking. Read the complete linked specification before executing.

**Goal:** Stop Task actions at selected List loss and provide explicit recovery without losing input.

**Architecture:** ProjectLive remains the lifecycle and route owner. Recovery stores inert input in
socket-local State, clears actionable workflows, and delegates explicit restoration to their owners.
A pending intent bridges recovery events and root-owned route resolution without performing writes.

**Tech stack:** Existing Phoenix LiveView, Elixir, context APIs, HEEx and app.js; no dependencies.

**Spec:** [Missing List recovery](../specs/2026-09-18-missing-list-recovery-design.md).

**Status:** Parked draft. Direction approved; detailed specification and this plan are not approved
for implementation. Owner `tas-1tq.10`; all three execution issues are deferred. When selected,
refresh `main` and open the dedicated `missing-list-recovery` branch before continuing through the
[Missing List recovery handoff](../handoffs/missing-list-recovery.md).

## Global constraints

- ProjectLive remains the sole workspace LiveView and renderer; no new routes or LiveComponents.
- Recovery starts at observed loss of the selected List after an already accepted workspace event.
- Suspend location-bound Task actions before handling another browser event or scheduled save.
- Preserve input from the active creation, detail, or movement workflow in accessible recovery UI.
- Resuming is explicit and never creates, updates, or moves a Task by itself.
- Creation requires explicit same-Project destination selection; Project root is an explicit option.
- Persistence stays behind existing Projects, Lists and Tasks contexts; no Repo calls in web code.
- No List deletion, deletion event vocabulary, schema change, dependency, or draft persistence.

Use repository-local Beads through br and GitButler for local commits. Never push, merge, rewrite
upstream, or close the feature/retire its handoff without their separate operator gates. Preserve
all rulings in the active handoff before deleting execution scratch. No planning identifiers in
production modules, test names, DOM, events or user-facing product documentation.

## File boundaries and shared interfaces

- New `lib/taskman_web/live/project_live/recovery.ex`: workflow plus private pure State. State data,
  active snapshot rules, errors and route intents are defined in the specification.
- New `lib/taskman_web/components/tasks/recovery.ex`: readonly recovery panel and recovery controls.
- Modify root `project_live.ex` and `.html.heex`: initialization, known-event guards, route integration,
  panel and suppressed ordinary Task controls. Root remains the route resolver.
- Modify `reconciliation.ex`: capture old workspace, missing outcome, timer and notification guards.
- Modify `tasks/creation.ex`, `editing.ex`, `autosave.ex`, `movement.ex`: restoration owner APIs only.
- Modify `assets/js/app.js`: imported clipboard hook from new `assets/js/recovery_clipboard.js`.
- New scoped tests under `test/taskman_web/live/project_live/` and
  `test/taskman_web/live/project_live/tasks/`; reusable component tests under components/tasks.
- Modify `docs/product/mvp-spec.md` only in the final implemented increment to describe current UX.

Shared APIs are exact:

```elixir
Recovery.enter(socket, previous_workspace) # -> socket
Recovery.blocked?(socket) # -> boolean
Recovery.events() # -> list of event strings
Recovery.handle_event(event, params, socket) # -> {:noreply, socket}
Recovery.complete_route(socket, params) # -> socket
Recovery.view(socket) # -> presentation map
Creation.restore(socket, captured_creation, parent_or_nil) # -> {:ok, socket} | {:error, socket}
Editing.restore(socket, captured_editing, captured_picker) # -> {:ok, socket} | {:error, socket}
Autosave.resume(captured_autosave, fresh_task) # -> autosave
Movement.restore(socket, captured_move) # -> {:ok, socket} | {:error, socket}
```

Recovery.State has snapshot, sequence, destination, pending and error. Its pure `capture/2` receives
an ordinary map with source Project/List, include_children, mode and captured owner states; never
socket. Empty snapshot means inactive. Capture increments sequence for snapshot.id and retains the
first snapshot when already active. Pure choose_destination/2 and clear_parent/1 update inert input;
prepare/2 receives a validated pending intent map; put_error/2 clears pending; discard/1 keeps sequence.

Recovery event names are choose_recovery_destination, clear_recovery_parent, resume_task_recovery,
discard_task_recovery. Each requires matching recovery_id. Copying is client-side and has no server
mutation event. Normal Task workflow events are unchanged and guarded while suspended.

## Task 1: Capture, suspend and expose recoverable input

**Beads:** `tas-1tq.10.1`.

**Files:** Create Recovery workflow/State, Tasks.Recovery component, clipboard hook,
`test/taskman_web/live/project_live/recovery_test.exs`,
`test/taskman_web/live/project_live/tasks/recovery_state_test.exs`,
`test/taskman_web/components/tasks/recovery_test.exs`. Modify root/template, Reconciliation and app.js.

**Consumes:** Existing workflow clear APIs, Workspace.reconcile tagged outcomes and accepted Event.
**Produces:** All shared Recovery APIs and State, readonly/copy/discard UI, route retention. Resume
preparation remains rejected without losing snapshot until its behavior is supplied by Tasks 2/3.

- [ ] Add RED LiveView coverage for creation capture, no stale save, repeated notification retention,
  navigation and discard. Use ConnCase, log_in_user(user_fixture()), and existing fixtures/imports.
  This concrete first test exercises root dispatch, not raw HTML:

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
  assert has_element?(view, "#task-recovery-text", "Keep this")
  refute has_element?(view, "#task-form")
  render_submit(view, "save_task", %{"task" => %{"title" => "Stale"}})
  assert {:ok, []} = Taskman.Tasks.list_tasks_for_location(project, nil)
  assert has_element?(view, "#task-recovery-text", "Keep this")
end
```

  Build synthetic fixtures only in SQL Sandbox. For referenced Lists relocate/delete synthetic Tasks
  and child Lists first. Add direct State tests for first capture, repeated capture, ID monotonicity,
  discard and invalid recovery IDs. Add a dirty-detail test delivering its queued title tuple after
  disappearance; assert recovery text retains input and no hidden :not_found clearing occurs.
- [ ] Run `mix test test/taskman_web/live/project_live/recovery_test.exs
  test/taskman_web/live/project_live/tasks/recovery_state_test.exs` and confirm outcome RED.
- [ ] Implement State capture and Recovery.enter with capture-before-clear ownership. Reconciliation
  must pass the pre-reconcile Workspace.State. Keep existing validators and subscriptions.
  Root's guarded dispatch shape is:

```elixir
@task_workflow_events @creation_events ++ @editing_events ++
                        @parent_selection_events ++ @movement_events
# Place before the existing owner clauses; do not catch unknown events.
def handle_event(event, _params, %{assigns: %{recovery: %{snapshot: snapshot}}} = socket)
    when event in @task_workflow_events and not is_nil(snapshot),
    do: {:noreply, socket}
```

  Existing owner clauses additionally use Recovery.blocked?/1 for unavailable location/no Project.
  Scheduled autosave dispatch returns unchanged state when blocked. During active recovery skip
  Movement/Editing/picker/hierarchy notification work; workspace navigation continues. Normal root
  route resolution continues without flush, skip Task apply_action while snapshot exists, then
  complete_route (inactive/no pending is unchanged). Preserve snapshot across all same-LiveView patches.
- [ ] Render the panel with task-recovery, task-recovery-text, task-recovery-copy,
  task-recovery-copy-status, task-recovery-discard IDs. Add aria labels/live status, readonly invalid
  values/conflicts, source label and process-lifetime warning. Hide ordinary Task forms/move controls
  until resolution. Discard clears owner states, preserves sequence, then browses valid current
  location or source Project root. Invalid/inactive recovery events no-op.
- [ ] Add clipboard hook with unique ID and phx-update ignore when it manages DOM. The hook invokes
  navigator.clipboard.writeText on user click, reports failure, and leaves readonly manual-copy text
  available. Imports stay in app.js. Cover low-level hook contract without style assertions.
- [ ] Extend the test table: stale validate/save/autosave/submit/conflict/parent/move events no-op;
  malformed/unrelated notifications retain existing behavior; root unknown callbacks unchanged;
  browsing to another Project keeps original source identity; no-active-workflow loss uses the old
  not-found page; initial missing route has no snapshot; discard is explicit and stale repeat no-op.
- [ ] Run changed tests plus workspace_updates_test, external_updates_test, autosave_test and
  reconciliation_test; format changed files, review diff, obtain scoped independent review and commit
  through `but commit -b missing-list-recovery -m "Preserve input when a List disappears"`
  using only reviewed file IDs. Record evidence/rulings and close issue only after the review gate.

## Task 2: Explicit creation restoration

**Beads:** `tas-1tq.10.2`; depends on Task 1.

**Files:** Recovery workflow, Tasks.Recovery component, Creation, root route integration;
new `test/taskman_web/live/project_live/creation_recovery_test.exs`.

**Consumes:** Recovery snapshot/IDs/guards, root workspace resolution, complete_route boundary.
**Produces:** Creation.restore/3 and complete-route pending-intent machinery reused in Task 3.

- [ ] Add RED creation recovery tests with captured parent in a different surviving List. Select an
  explicit destination, resume without creation, then submit ordinary form and assert both explicit
  list_id and preserved parent_task_id. Add root destination with List-owned parent. Never impose
  parent/list co-location. The action sequence uses these concrete DOM contracts:

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

  The setup is Task 1's logged-in synthetic creation flow, with parent in a surviving List and an
  empty selected/destination List. Initialize the parent via existing parent query/picker controls.
- [ ] Run `mix test test/taskman_web/live/project_live/creation_recovery_test.exs`; confirm RED.
- [ ] Implement fresh source-Project options with no default destination. choose event only updates
  snapshot selection; resume refetches source Project, chosen List and parent. Canonical parser:

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

  Fresh parent may reside anywhere in source Project. Missing parent retains snapshot and blocks
  resume until explicit clear_recovery_parent; field values survive clearing. Use exact spec errors.
- [ ] Store pending mode/path identity/snapshot ID and push_patch with Paths.new_task_path(project,
  destination, include_children?, nil). Root resolves workspace, skips Task apply_action, and always calls
  complete_route while recovery is active, even on failed resolution. complete_route checks live_action and canonical Project/List/query params, revalidates authority
  and delegates Creation.restore/3. Restore uses resolved workspace location, captured form.params,
  Tasks.change_task/2, to_form/2 and ParentPicker.open_create; no mutation. On success clear snapshot
  only after installing restored owner states. On error clear pending and retain input/error.
- [ ] Add invalid title/due_at restoration, malformed/foreign/gone destination, duplicate/stale
  recovery_id, parent disappearance/explicit clear, pending destination loss and wrong patch identity
  tests. Drive completion race deterministically with direct root callbacks and synthetic deletion
  between prepare and complete, never sleeps. Assert no automatic root fallback or Task write.
- [ ] Run creation_recovery_test, recovery_test, project_live_test and workspace_updates_test; format,
  review, independent scoped review and local commit with message "Resume recovered creation drafts
  at an explicit location". Record evidence and close issue after review.

## Task 3: Restore detail drafts and reopen movement

**Beads:** `tas-1tq.10.3`; depends on Task 2.

**Files:** Recovery/component, Editing, Autosave, Movement; new
`test/taskman_web/live/project_live/detail_recovery_test.exs`,
`test/taskman_web/live/project_live/movement_recovery_test.exs`; extend
`test/taskman_web/live/project_live/tasks/autosave_test.exs`; product MVP recovery description.

**Consumes:** Pending intent/complete-route protocol and freeze lifetime from Tasks 1/2.
**Produces:** Editing.restore/3, Autosave.resume/2, Movement.restore/2 and final recovery behavior.

- [ ] Add RED pure Autosave test showing retained dirty values, fresh clean values, conflicted external
  dirty values, cleared revisions, retained sequence and non-saving status. Use existing Autosave
  direct test helpers for a prebuilt dirty state. Required core expectation:

```elixir
resumed = Autosave.resume(captured, fresh_task)
assert resumed.draft["title"] == "Mine"
assert resumed.revisions == %{}
assert resumed.sequence == captured.sequence
assert resumed.save_state == :not_saved
assert resumed.form[:title].value == "Mine"
```

  Add conflict variant asserting :conflicted and latest value, never :saving. Add real LiveView
  detail loss with dirty title, move the synthetic Task to a surviving List before deleting old List,
  explicitly reopen and assert its actual-location route plus unchanged persisted title. Deliver
  the captured old autosave tuple after reopening; render barrier, then assert persisted title still
  unchanged and draft retained. New edit schedules a higher revision and normal explicit save works.
- [ ] Add RED movement cases: row move to a surviving hidden/filtered Task reopens in detail; detail
  move preserves dirty ordinary fields and later flush/conflict gate; missing Task keeps accessible
  recovery; disappeared destination requires reselect; explicit reopening performs no move.
- [ ] Run detail_recovery_test, movement_recovery_test and tasks/autosave_test; confirm RED.
- [ ] Implement Autosave.resume by Autosave.reconcile, revisions reset and status derived from conflicts
  then dirty fields. Do not call change, flush, scheduled_save or persistence. Editing.restore refetches
  Task/hierarchy, uses maximum live/captured sequence, reconciles original baseline and opens detail
  with restored form. Refresh captured parent conflict against scoped parent authority without writes.
  Missing Task/List keeps snapshot and exact spec error. Old timer revisions stay absent; future
  sequence advances. No Task duplication or selected-location movement as recovery fallback.
- [ ] Resolve Task's actual List/root before prepare and again at completion; detail resume uses
  Paths.task_detail_path/4. After successful owner restoration consume snapshot. Movement-only resume
  initializes surviving Task detail with idle autosave, then Movement.restore opens Move with :detail,
  restores query and only fresh valid noncurrent destination. Detail move restores editing first.
  These owner-level transformations are the intended no-write composition:

```elixir
move = Move.open(Move.empty(), project, fresh_task, :detail)
{:ok, move, _} = Move.search(move, project, captured_move.query)
# Retain destination only when its fresh option exists and is not current.
# Assign this rebuilt Move; never call Move.submit here.
```

  Implement the option check explicitly and retain the readonly captured destination label until
  successful resume. If rebuilding returns :task_not_found, keep snapshot rather than accepting a
  cleared Move as successful restoration.
- [ ] Add notification/destination-loss during pending completion, ordinary-field conflict, captured
  parent conflict, browser navigation retention, explicit discard and ID mismatch coverage. Map every
  acceptance row in the spec to a test or the stated browser smoke check; do not add style tests.
- [ ] Update product MVP with implemented recovery controls and process-lifetime limit. Keep planning
  IDs and internal task language out of product copy. Update plan checkboxes, task state, indexes
  and handoff without removing rulings or remaining publication/completion gates.
- [ ] Run changed suites, then `mix format --check-formatted`, the complete focused command from the
  spec and `mix precommit`. Check planning terminology/module duplicates/local doc links and inspect
  diff. Browser smoke: keyboard copying with clipboard failure, focus on recovery entry/resume,
  readonly invalid values and navigation retention. Record uncertainty if browser tooling prevents
  a particular check; do not claim it passed.
- [ ] Obtain scoped independent review; local commit "Restore recovered Task edits and movement".
  Close execution issues only on accepted evidence. Leave feature and handoff open until operator
  completion confirmation; publication/merge require separate authorization and target refresh.

## Preflight coverage and execution gate

All spec requirements map to Task 1 (capture/stop/lifetime/UI), Task 2 (creation/route authority), or
Task 3 (detail/movement/conflicts and final acceptance). State and workflow interfaces above are
shared across tasks; no later task introduces a competing signature. Draft proposals are concrete
and self-contained, but require operator review. Do not execute any unchecked task until the
written specification and plan are approved. Default approved execution is delegated; inline
execution is available if explicitly chosen. No clean-session prompt while approval is pending.

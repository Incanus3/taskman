# Additional Task States Implementation Plan

**Status:** Operator approved; All deliverables independently accepted; final integration review and operator acceptance pending.

**Execution:** Implement the checked tasks in dependency order, with independent scoped review
before advancing. Follow repository execution and version-control guidance. Read the complete
specification before implementation; this plan does not replace its contracts.

**Goal:** Add Not Planned and contextual additional-state selectors without selector-time discovery
queries, while making counting and blocker resolution independent.

**Architecture:** A compile-time state catalogue supplies domain semantics and presentation tokens.
A page-local Project location model owns List metadata and prepared availability sets. Local
mutation results and PubSub events extend those sets using the Task state/location values returned
by successful database operations, subject to the accepted outer-transaction limitation. Existing
controls gain a reveal action and delayed descriptions while retaining their native value bridge.

**Tech stack:** Elixir 1.20.4, Erlang/OTP 29, Phoenix 1.8/LiveView, Ecto/PostgreSQL, existing
JavaScript hooks and Tailwind CSS. Use existing dependencies.

**Spec:** [Additional Task states and Not Planned](../specs/2026-11-01-additional-task-states-design.md)
(operator approved, D1–D13).

**Tracker:** Workstream Task 71, with description marker
`Source: Incanus3/taskman:additional-task-states`. Task IDs in this plan are relative to this repository's registered Taskman
Project; every child below includes its exact description marker. The sibling dependency chain
follows the plan’s execution order; current progress is recorded in the tracker and workstream handoff.

## Global constraints

- Canonical order: `icebox`, `pending`, `in_progress`, `in_review`, `done`, `not_planned`, `will_not_do`.
- Not Planned is additional, initially unchecked only in the main listing, closed for counting,
  and unresolved as a blocker. Only Done resolves blockers; Will Not Do remains closed.
- Hierarchy selection starts with all supported states checked and retains its existing modal lifetime.
- Additional-state observations survive within a mounted page; reload/remount rebuilds from current
  Tasks. Real states remain valid API/CLI inputs regardless of UI visibility.
- No availability query/Task scan/subtree aggregation on picker opening or normal List navigation;
  no per-client Task lookup to interpret an availability event.
- Reveal is an action row in the enhanced picker. It preserves the value and keeps the popup open;
  it persists across Task navigation within an edit dialog. New creation forms start unrevealed.
- Native fallback offers all real states. State descriptions use a 450 ms hover/keyboard delay in
  affected controls and a 450 ms hover delay on table state badges.
- Keep ephemeral notifications and the accepted enclosing-transaction limitation. No replay,
  last-instance counts, new metadata endpoint, polling or generalized event-driven UI.
- Keep live-update conflict handling, recovery, exact counts, direct-Task presence, required tree
  ancestry, selection and popup dismissal contracts. Do not implement the separate notification
  read-optimization research tracked outside this feature.
- Use the recorded workstream parent and children; task creation and plan approval are complete.
  No implementation in this planning session without the operator's explicit choice to continue
  here. Pushing/merging/deployment require separate authority.

## Review focus

| Input or condition | Required outcome | Owner |
|---|---|---|
| An absent additional state is remembered as checked, then another checkbox changes | Preserve its preference; an explicit empty URL snapshot still replaces everything | Task 4 |
| A List event arrives after a Task snapshot referring to it | Retain the pending observation without a Task query, then materialize it under the correct ancestry | Tasks 2–3 |
| Last additional-state Task changes state or moves; Lists are then reparented | Retain direct observations but recompute subtree membership from current topology; never treat observations as exact counts | Tasks 3–4 |
| Reveal removes the keyboard-active action row during a server patch | Keep a valid active/focus target, open popup, unchanged native value and idle autosave; preserve reveal on Task navigation | Task 5 |
| Tooltip source is replaced, removed or covered by an open dialog/popover | Cancel stale tooltips, preserve accessible descriptions and click-through badge navigation, and display new tooltips above their owner | Task 6 |

## File ownership and public seams

| Boundary | Files and responsibility |
|---|---|
| Domain state definitions | New `lib/taskman/tasks/status.ex`; existing Task schema, counts, mutations, SQL sorting and web Status presenter |
| Database/bootstrap observations | New `lib/taskman/tasks/availability_persistence.ex`; Tasks context facade; generated state-check migration |
| Notifications | `lib/taskman/change_notifications.ex` and `change_notifications/event.ex`; matching producer/consumer fixtures |
| Shared locations | New `lib/taskman_web/live/project_live/locations.ex`; Workspace state/bootstrap/navigation and route coordination |
| Scope/filter rules | New `lib/taskman_web/live/project_live/tasks/state_options.ex`; listing/hierarchy state, reconciliation, creation/edit/move results and preference hook |
| Reveal control | Task Form/Detail components, creation/edit state and generic SelectPicker HEEx/JavaScript; picker probe support |
| Descriptions | Shared tooltip JavaScript/CSS, picker metadata, filter rows and table badges |
| External parity/authority | CLI registry/help/completions/skill, API tests, product contracts and affected specifications |

All names below are implementation-plan decisions. Change a seam only after reconciling its
consumers and recording a material deviation; do not let workers silently invent incompatible APIs.

## Task 1: Establish the state catalogue and persisted lifecycle behavior

**Tracker:** Task 72, marker `Source: Incanus3/taskman:additional-task-states:state-catalogue`.

**Dependencies:** None.

**Files:** Create `lib/taskman/tasks/status.ex`,
`test/taskman/tasks/status_test.exs` and
`test/taskman/repo/migrations/task_statuses_test.exs`. Generate the migration with
`mix ecto.gen.migration add_not_planned_task_status`; its timestamped filename is determined by
that command, and its module is `Taskman.Repo.Migrations.AddNotPlannedTaskStatus`.
Modify `lib/taskman/tasks/{task,counts,mutations}.ex`, `lib/taskman/tasks.ex`,
`lib/taskman_web/components/tasks/status.ex`,
`lib/taskman_web/live/project_live/tasks/hierarchy.ex`,
`lib/taskman_web/live/project_live/task_table_preferences.ex`,
`lib/taskman_web/live/project_live.html.heex`, `assets/js/project_live_hooks.js` and affected
count/query/hierarchy/Done-confirmation tests.

**Interfaces produced — `Taskman.Tasks.Status`:**

- `values/0`, `default_values/0`, `additional_values/0`, `initial_listing_values/0`,
  `open_values/0` return ordered lists of state atoms.
- `definition(state)` returns `%{label: string, description: string, color: atom,
  additional?: boolean, initially_selected?: boolean, open?: boolean, resolves_blocker?: boolean}`.
- `rank(state) :: non_neg_integer()` follows `values/0`;
  `resolves_blocker?(state) :: boolean()` is true only for Done.
- Keep `Task.statuses/0` as the existing facade over `Status.values/0`.

- [x] **Write failing contracts.** Catalogue tests assert the exact seven-state order and four
  initial listing values; retain Pending as the new-Task default. Use these assertions:

  ```elixir
  assert Status.additional_values() == [:not_planned]
  assert Status.definition(:not_planned).label == "Not Planned"
  assert Status.definition(:not_planned).description ==
           "Work we currently consider unnecessary, retained for reconsideration if the need arises."
  refute Status.definition(:not_planned).open?
  refute Status.resolves_blocker?(:will_not_do)
  assert Status.resolves_blocker?(:done)
  ```

  Add database acceptance and ascending/descending sort tests using one Task per state.
  Test that selected Not Planned participates in visible/eligible counts, and unselected Not
  Planned does not become open work. Update Done-warning expectations: both Not Planned and
  Will Not Do require the existing confirmation, including the API's HTTP 409 contract.
- [x] **Run the red tests.** `mix test test/taskman/tasks/status_test.exs test/taskman/tasks/counts_test.exs test/taskman/tasks/done_confirmation_test.exs test/taskman/tasks/queries_test.exs test/taskman_web/live/project_live/tasks/hierarchy_test.exs`.
  Expect missing catalogue/new-state support or old blocker/order assertions, not fixture errors.
- [x] **Implement the catalogue, migration and consumers.** Preserve existing descriptions/colors;
  use color tokens `sky`, `amber`, `red`, `orange`, `emerald`, `stone`, `slate` in canonical order,
  with the web palette preserving existing styles and a subdued stone style for Not Planned.
  Derive SQL sorting from the catalogue's ordered string array, using PostgreSQL
  `array_position(?::text[], ?::text)` rather than another hand-maintained state ranking.
  Freeze the seven allowed strings in the historical migration; do not make past migrations
  depend on a future mutable catalogue. Down migration reinstates the old constraint and refuses
  transactionally if Not Planned rows still exist. Use an isolated-schema migration test following
  `project_identity_test.exs`, including refusal without row loss and successful down after rows
  are explicitly changed. Derive server defaults and a `data-default-task-statuses` browser input
  from `initial_listing_values/0`; browser fallback must not accidentally include Not Planned.
- [x] **Run the green gate.** Run the red command plus the new migration test,
  `test/taskman_web/live/project_live/task_table_preferences_test.exs`,
  `test/taskman_web/live/project_live/done_warning_test.exs` and
  `test/taskman_web/controllers/api/task_controller_test.exs`. Inspect both sort directions and
  unchanged unknown-state validation. Expected: all pass.
- [x] **Review the owned diff/privacy and commit:** `Add shared task state definitions and Not Planned lifecycle`.

## Task 2: Publish compact observations and provide one bootstrap query

**Tracker:** Task 73, marker `Source: Incanus3/taskman:additional-task-states:availability-events`.

**Dependencies:** Task 1.

**Files:** Create `lib/taskman/tasks/availability_persistence.ex` and
`test/taskman/tasks/availability_test.exs`. Modify `lib/taskman/tasks.ex`,
`lib/taskman/change_notifications.ex`, `lib/taskman/change_notifications/event.ex`,
`test/taskman/change_notifications_test.exs`, `test/taskman/tasks/notifications_test.exs` and
the existing test factories that construct Task `%Event{}` values.

**Interfaces produced:**

- `AvailabilityPersistence.list_location_states(project_id, additional_states)` returns distinct
  `{list_id | nil, state_atom}` rows scoped to that Project.
- `Tasks.additional_states_by_location(%Project{}) :: %{(pos_integer() | nil) => MapSet.t(atom())}`
  delegates that storage operation and groups rows; it takes no UI status filter.
- Task `%Event{}` gains `status` and `list_id`. Every create/update/move publisher uses the
  returned persisted Task, preserving identity/revision/changed-field metadata and self-exclusion.

- [x] **Write failing tests.** Seed direct-Project, direct-List, child-List and foreign-Project
  Not Planned Tasks alongside default-state Tasks. Assert only the correct additional observations
  are returned. Extend publish tests with exact final values, including `list_id: nil`:

  ```elixir
  assert_receive %Event{entity: :task, entity_id: id, status: :not_planned, list_id: nil}
  assert id == task.id
  assert Tasks.additional_states_by_location(project)[nil] == MapSet.new([:not_planned])
  ```

  Verify create, status update, ordinary title update, move and failed mutation behavior;
  failed/rolled-back supported operations still emit no success event.
- [x] **Run red:** `mix test test/taskman/tasks/availability_test.exs test/taskman/change_notifications_test.exs test/taskman/tasks/notifications_test.exs`.
  Expect absent snapshot fields/bootstrap API.
- [x] **Implement only the new payload and grouped query.** Do not fetch a Task again inside
  publication, add old-state payloads, alter other event types or add delivery infrastructure.
  Update in-repository Task event fixtures from the Task they represent; malformed-snapshot tests
  deliberately retain missing/invalid values.
- [x] **Run green:** the red command plus
  `mix test test/taskman_web/live/project_live/reconciliation_test.exs test/taskman_web/live/project_live/external_updates_test.exs`.
  Expected: payload/query contracts and existing invalidation consumers pass.
- [x] **Review and commit:** `Publish task state observations for availability`.

## Task 3: Give navigation and availability one location owner

**Tracker:** Task 74, marker `Source: Incanus3/taskman:additional-task-states:shared-locations`.

**Dependencies:** Tasks 1–2.

**Files:** Create `lib/taskman_web/live/project_live/locations.ex` and
`test/taskman_web/live/project_live/locations_test.exs`. Modify
`lib/taskman_web/live/project_live/workspace.ex`, `lib/taskman_web/live/project_live.ex`,
creation's location-option plumbing, and existing workspace/navigation/List-recovery tests.

**Interfaces produced — pure `TaskmanWeb.ProjectLive.Locations`:**

- `new/0`; `loaded?(model, project_id)`.
- `load(model, project_id, lists, observed_by_location)` merges retained direct observations with
  a fresh snapshot and rebuilds prepared summaries.
- `reconcile_lists(model, project_id, lists)` updates canonical metadata/topology and materializes
  pending observations; authoritative removals prune invalid keys.
- `observe(model, project_id, list_id_or_nil, state)` adds only supported additional-state use;
  unknown List IDs remain pending within that Project until topology arrives.
- `retain_projects(model, project_ids)` prunes removed Project caches.
- `lists(model, project_id)` returns canonical List records in existing insertion/ID order.
- `available(model, project_id, location_id_or_nil, :direct | :subtree)` returns canonical ordered
  default-plus-observed state atoms from the prepared entry; no child traversal.

Store the model in `Workspace.State.locations`. Each Project holds one location entry per List,
plus `nil` for the Project root; entries hold the List record and direct/subtree sets. Pending
observations have no fabricated List records. The root subtree supplies the whole-Project set.
Counts and current direct-Task presence keep their existing independent sources.

**Workspace seams:** `ensure_locations(socket, project)`, `resnapshot_locations(socket)` and
`lists(socket)` own context reads; `location_path(socket, project, task_list)` replaces the old
querying two-argument path helper. Navigation consumes the model rather than loading another tree.

- [x] **Write failing model/workspace tests.** Assert direct/subtree/root distinctions, no removal
  when an observed state ceases to be current, Project isolation, reparented subtree changes,
  invalid-key pruning and pending observations. Representative assertions after fixture setup:

  ```elixir
  refute :not_planned in Locations.available(model, project.id, parent.id, :direct)
  assert :not_planned in Locations.available(model, project.id, parent.id, :subtree)
  assert :not_planned in Locations.available(model, project.id, nil, :subtree)
  assert Locations.lists(model, project.id) == task_lists
  ```

  Observe a Task for a newly created List before reconciling that List, then assert its direct
  state and ancestor/root summaries appear without a Task read when metadata arrives.
- [x] **Run red:** `mix test test/taskman_web/live/project_live/locations_test.exs test/taskman_web/live/project_live/workspace_test.exs`.
- [x] **Implement the pure model and Workspace integration.** Subscribe before bootstrap so queued
  observations can merge afterward. Bootstrap only on first Project entry or return after changing
  Projects; known same-Project navigation reuses prepared metadata/sets. Workspace notifications
  reconcile one authoritative List snapshot into the owner before navigation/forms consume it.
  Keep existing authoritative route and mutation scope validation. If route authority exposes
  previously unknown/changed List metadata, reconcile that topology through the same owner; do
  not turn selector opening into a query or discover Task states on ordinary List navigation.
  Supply creation location options and selection paths from the owner; retain current missing-List
  recovery behavior and exact navigation presence/count semantics.
- [x] **Run green:** the red command plus
  `mix test test/taskman_web/live/project_live/workspace_updates_test.exs test/taskman_web/live/project_live/lists_test.exs test/taskman_web/live/project_live/creation_location_test.exs test/taskman_web/live/project_live/detail_recovery_test.exs test/taskman_web/components/workspace_navigation_test.exs`.
  Use Repo telemetry like `reconciliation_test.exs` to prove repeated navigation rendering does not
  reload a second List tree or run availability bootstrap again. Expected: all pass.
- [x] **Review and commit:** `Share project locations between navigation and state availability`.

## Task 4: Maintain observations and filter only offered choices

**Tracker:** Task 75, marker `Source: Incanus3/taskman:additional-task-states:availability-filters`.

**Dependencies:** Task 3.

**Files:** Create `lib/taskman_web/live/project_live/tasks/state_options.ex`,
`test/taskman_web/live/project_live/tasks/state_options_test.exs` and
`test/taskman_web/live/project_live/additional_states_test.exs`. Modify Workspace,
Reconciliation, ProjectLive, creation/editing/movement result paths, listing/hierarchy state,
TaskTablePreferences, Task Table/Detail components, ProjectLive HEEx, `assets/css/app.css` and
`assets/js/project_live_hooks.js`. Extend existing preference/hierarchy/filter/update tests.

**Interfaces produced:**

- `Workspace.observe_task(socket, %Task{})` handles successful local results;
  `observe_task_event(socket, %Event{})` validates and merges snapshots without persistence reads.
- `StateOptions.merge_selection(current, offered, submitted) :: [atom]` canonicalizes supported
  atom/string inputs and changes only offered states. Explicit route/hydration replacement remains
  owned by existing TaskTablePreferences functions.
- `StateOptions.for_form(locations, project_id, owning_list_id_or_nil, current_state, revealed?)`
  returns `%{values: [atom], hidden?: boolean}`. A List uses direct scope; no owning List uses the
  Project root's subtree. Retain a supported current value even when it is not otherwise offered.
- Hierarchy state gains `available_statuses`, computed in `Hierarchy.load/2` from its full tree;
  checkbox submissions update only that offered set. `Table.status_filter/1` accepts offered values.

- [x] **Write failing preference/scope tests.** Use named tests for remembered absent state,
  empty checkbox submission, empty URL replacement, direct Project versus whole Project, List
  direct versus subtree, filtered-empty listing and all-checked hierarchy initialization:

  ```elixir
  assert StateOptions.merge_selection([:pending, :not_planned], [:pending], []) == [:not_planned]
  assert StateOptions.merge_selection([:not_planned], [:pending], ["pending"]) ==
           [:pending, :not_planned]
  assert Preferences.apply_route(saved, %{"statuses" => ""}).statuses == []
  ```

  Also test local create/status/move, external snapshots, duplicate/out-of-order snapshots, foreign
  and malformed inputs, unchanged availability on unrelated fields, last-use retention, Project
  return, conservative reconnect refresh, remount reset and current hierarchy projection refresh.
- [x] **Run red:** `mix test test/taskman_web/live/project_live/tasks/state_options_test.exs test/taskman_web/live/project_live/additional_states_test.exs test/taskman_web/live/project_live/hierarchy_status_filter_test.exs`.
- [x] **Wire updates and UI scopes.** Observe valid matching Task create/move/status/List changes
  before presentation refreshes. Local create must retain the returned Task currently discarded
  by `Creation.create_task`; editing and movement observe their persisted results directly because
  broadcasts exclude the caller. Preserve existing authoritative listing/detail/graph reads.
  Observe successful persistence results, not scheduled or rejected autosave drafts. Prepare
  hierarchy options during tree load, not filter toggle; render additional filter rows with the
  agreed muted background. Add `resnapshot_task_locations` to Workspace events; its handler
  validates a supplied Project ID against the currently selected/subscribed Project and ignores
  absent/stale/invalid contexts. Put that ID in `workspace-content`'s server-owned `data-project-id`.
  The TaskTablePreferences hook's `reconnected()` sends it; do not key recovery to browser query
  text, which preference handling can clean without updating server route metadata. Workspace
  merges a fresh snapshot without resetting forms/recovery; remount naturally bootstraps.
  Do not add missed-event detection, polling or per-event Task lookup. Keep the browser's allowed
  states and persisted/share-link selections complete even when rendered options are contextual.
- [x] **Run green:** the red command plus
  `mix test test/taskman_web/live/project_live/task_table_preferences_test.exs test/taskman_web/live/project_live/task_counts_test.exs test/taskman_web/live/project_live/external_updates_test.exs test/taskman_web/live/project_live/reconciliation_test.exs test/taskman_web/live/project_live/hierarchy_sort_test.exs`.
  Add focused telemetry assertions around observation helpers and selector toggles: zero
  availability queries, zero hierarchy traversal on filter open, and no Task reads from snapshot
  observation. Do not misclassify existing listing refresh queries as new availability work.
  Test stale/valid reconnect Project IDs and preserve refresh after share-link query cleanup.
- [x] **Review and commit:** `Apply contextual task state availability without selector discovery`.

## Task 5: Reveal additional states inside the shared picker

**Tracker:** Task 76, marker `Source: Incanus3/taskman:additional-task-states:picker-reveal`.

**Dependencies:** Task 4.

**Files:** Modify `lib/taskman_web/components/select_picker.ex`, Task Form/Detail components,
ProjectLive HEEx, creation/editing state and events, `assets/js/select_picker.js`, scoped CSS,
`test/support/form_picker_probe.ex`, existing generic picker tests, and additional-state
LiveView tests. Create `test/taskman_web/live/project_live/additional_states_browser_test.exs`.

**Interfaces produced:**

- Creation/edit state each has `additional_states_revealed?: false`. Creation resets when a new
  form starts; Editing preserves it in `State.open/5` and resets it only in dialog `State.clear/1`.
- Creation event `reveal_task_creation_states` and Editing event `reveal_task_edit_states` set only
  that flag through existing workflow/recovery guards. No changeset, Task write or autosave schedule.
- Generic SelectPicker keeps `options` as all native label/value pairs. Optional `visible_values`
  restricts enhanced rows (default: all); `option_metadata` maps string values to
  `%{description: string, additional: boolean}`; optional
  `action: %{label: string, event: string}` adds one action footer.
  Task callers supply “Show additional states”; generic controls contain no Task-specific defaults.
- ProjectLive computes the form subset from `StateOptions.for_form/5` using the actual form's
  status field value (including an unsaved draft). Thread `status_values` and optional
  `reveal_states_event` through Detail/Form; components do not receive or query a second List model.
- Stable IDs: retain real option/native/trigger IDs; action is `ID-reveal`. Keep `ID-options` as the
  listbox and use a new `ID-popup` top-layer wrapper containing the listbox and semantic action button.

- [x] **Write failing interaction tests.** Before observation, the enhanced form omits Not Planned,
  the native select contains all seven real states, and the action exists. After reveal assert
  canonical insertion, absent action, same native value/Task lock version, no ordinary change event
  or autosave, and an open popup. Check conditional absence when all additional states are already
  offered, retained current draft values, disabled recovery, validation/autosave retention, owning
  List changes, Task navigation in one dialog, and close/reopen/new-creation reset. Example:

  ```elixir
  assert has_element?(view, "#task-status-picker")
  render_click(view, "reveal_task_edit_states", %{})
  assert Tasks.get_task_for_project(project, task.id).status == :pending
  ```

  The direct event exercises the hook-to-server contract. Exercise the actual action-row ID,
  rendering/focus and popup visibility in browser tests, not raw LiveView HTML. The generic probe
  records hook `pushEvent` calls and simulates the metadata patch; the application browser test
  proves the real LiveView round trip.
- [x] **Run red:** `mix test test/taskman_web/live/project_live/additional_states_test.exs test/taskman_web/components/form_pickers_test.exs`; then
  `mix test.browser test/taskman_web/live/project_live/additional_states_browser_test.exs`.
- [x] **Implement the separate action path.** Task Form passes all native values plus the scoped
  enhanced subset supplied by its ProjectLive caller. Render additional rows with the agreed muted
  background and keep real-state order independent of the final action. Do not route activation
  through the existing `choose()` path, which writes the input and closes the popup.
  ArrowDown after the last state reaches the action button; ArrowUp from it returns to the last
  state. Enter/Space activates the action; normal Tab/Escape dismissal remains intact. After the
  patch removes the action, focus the listbox and activate the first newly revealed state without
  selecting it. Maintain popup position/open state during this reveal update; existing Task-context
  changes may close the popup while the server's dialog reveal flag remains true. Do not attach
  state colors/descriptions or a fabricated native value to the action.
- [x] **Run green:** the red commands plus
  `mix test test/taskman_web/components/tasks/form_test.exs test/taskman_web/live/project_live/autosave_test.exs test/taskman_web/live/project_live/detail_recovery_test.exs` and
  `mix test.browser test/taskman_web/components/form_pickers_browser_test.exs`.
  Use the shared browser probe to prove other select instances still isolate values/change events.
- [x] **Review and commit:** `Reveal additional task states from the open picker`.

## Task 6: Display delayed descriptions without breaking popup or row interaction

**Tracker:** Task 77, marker `Source: Incanus3/taskman:additional-task-states:state-descriptions`.

**Dependencies:** Task 5.

**Files:** Modify `assets/js/tooltips.js`, `assets/js/select_picker.js`, `assets/css/app.css`,
SelectPicker, Task Table/Status components, additional-state browser tests and existing table/picker
browser tests. Keep existing non-state tooltip consumers unchanged.

**Interfaces produced:** Export `requestDescriptionTooltip(target: HTMLElement): void` (fixed
450 ms delay) and `dismissTooltip(): void` from the shared tooltip module. Use opt-in
`data-tooltip-mode="description"` for state sources; generic existing tooltip paths retain their
current hover delay and immediate keyboard-focus behavior. Real enhanced option metadata supplies
the text; accessible descriptions exist independently of the delayed visual popup.

- [x] **Write failing browser assertions.** State options and checkbox rows describe all states;
  keyboard-active picker rows show the corresponding description after the delay without changing
  selection. Hover a table state badge, verify the correct description, then click that same area
  and verify Task opening. Add stale-source cancellation, Escape precedence, hover over tooltip,
  changed badge state and no extra badge tab stops. Verify generic action tooltips still behave as
  before and description popups actually appear above the open picker/filter/dialog.
- [x] **Run red:** `mix test.browser test/taskman_web/live/project_live/additional_states_browser_test.exs`.
- [x] **Implement opt-in scheduling, presentation and accessibility.** Use a manual top-layer
  popover for description tooltips, bounded to the viewport, plain text only, with no focusable
  controls. Hovering its trigger/tooltip keeps it open. Dismiss on the specification's source,
  lifecycle, scroll, selection and state-change boundaries; Escape must not consume the owning
  control's existing action. The picker explicitly schedules the newly keyboard-active row after
  rendering. Supply stable accessible description IDs/text before visual timing completes.
  Preserve badge pointer passthrough: opt-in row hover hit testing against the badge rectangle
  can identify a `data-tooltip-pass-through` source without changing the underlying Task link.
  Use a generic `data-tooltip-scope` on the row rather than coupling the shared module to Task IDs.
- [x] **Run green:** the red command plus
  `mix test.browser test/taskman_web/live/project_live/task_table_browser_test.exs test/taskman_web/live/project_live/task_detail_browser_test.exs test/taskman_web/components/form_pickers_browser_test.exs`.
  Use condition-based browser waits, not process sleeps. Inspect actual 1440×900, 390×844 and
  320×400 CSS viewports for clipping, focus, muted readability and horizontal overflow. Record
  native fallback inspection and assistive-technology limits honestly.
- [x] **Review and commit:** `Describe task states in delayed accessible tooltips`.

## Task 7: Complete external parity, canonical documentation and the integrated gate

**Tracker:** Task 78, marker `Source: Incanus3/taskman:additional-task-states:external-parity-verification`.

**Dependencies:** Tasks 1–6; public CLI work depends on Task 1, but final verification waits for all.

**Files:** Modify `lib/taskman/cli/registry.ex`,
`test/taskman/cli/commands/tasks_test.exs`, `test/taskman/cli/execution/parser_test.exs`,
`test/taskman/cli/presentation/{help,completions}_test.exs`,
`test/taskman/cli/skill/bundle_test.exs`, `test/taskman/cli/escript_test.exs`,
`priv/taskman_cli_skill/SKILL.md`, API Task tests and
`docs/product/{domain,mvp-spec,relationships}.md`. Add supersession references in the existing
task-editing lifecycle, blocking, live-update, task-count, hierarchy-filter and shared-controls
specifications linked by the approved design. Existing help/completion renderers consume Registry.
Update `docs/README.md` and the workstream handoff; retain this plan until delivery is complete
and its durable conclusions have been harvested under repository documentation policy.

**Interfaces consumed:** `Status.values/0` supplies Registry string values and help syntax;
existing API/CLI commands and JSON shapes are preserved. The UI-only availability/reveal model
adds no public state-metadata endpoint or CLI operation.

- [x] **Write failing parity tests.** API create/update/list and CLI parser/request/help/completion
  tests accept `not_planned`; unknown values retain existing failures. Add a boundary test for a
  Will Not Do blocker returning HTTP 409/`unresolved_blockers` and succeeding only through existing
  confirmation. Pin the exact state vocabulary in tests, including Bash/Fish output, instead of
  deriving every expected value from the implementation under test.
- [x] **Run red:** `mix test test/taskman/cli test/taskman_web/controllers/api/task_controller_test.exs`.
- [x] **Implement parity and documentation.** Derive Registry values/help alternatives from the
  domain catalogue; update bundled skill definitions, main defaults and only-Done blocker rules.
  Mark superseded rules in earlier specs and point to the accepted current contract. Document the
  payload extension, shared location ownership, native fallback and reveal/tooltip lifetimes.
  Check local links/privacy and search changed implementation-facing files for leaked planning
  terminology. Do not implement the separate optimization research or change its tracker status.
- [x] **Run the integrated gate sequentially, with source edits stopped:**

  ```sh
  mix test test/taskman/tasks test/taskman_web/live/project_live test/taskman_web/components
  mix test test/taskman_web/controllers/api
  mix test test/taskman/cli
  mix precommit
  mix escript.build
  TASKMAN_TEST_ESCRIPT="$PWD/taskman" mix test test/taskman/cli/escript_test.exs
  mix test.browser test/taskman_web/live/project_live/additional_states_browser_test.exs test/taskman_web/live/project_live/task_table_browser_test.exs test/taskman_web/live/project_live/task_detail_browser_test.exs test/taskman_web/components/form_pickers_browser_test.exs
  ```

  Expected: all applicable tests pass, all three rebuilt-escript smoke tests execute, and browser
  evidence covers the new contracts plus existing popup/recovery/value-bridge behavior. Keep the
  generated escript untracked and use disposable test configuration. If stale compressed assets
  are demonstrated, rebuild with `MIX_ENV=test mix do assets.build + phx.digest` before retrying.
  Follow the development guide for a working display/Xvfb when headed tests require it.
- [x] **Review the session's implementation and commit the verified checkpoint.** Record exact
  evidence, unresolved limits and autonomous rulings in their canonical owners/handoff. Present
  the feature for operator acceptance. Do not treat tests, commits or independent child review as
  authority to mark the whole workstream Done, retire its handoff, push, merge or deploy.

## Plan review and continuation

Author self-review covered specification requirements, step clarity, interface/type consistency,
all five Review Focus cases and plan proportion. Independent readiness review found no material
gaps. Scoped reviewers verify each implemented deliverable directly; a full-branch review is not
part of this plan.

The operator has approved the plan and the Product workstream grouping above has been created
and verified. Continue from the [workstream handoff](../handoffs/additional-task-states.md) in a
fresh implementation session by default, using the existing Task IDs rather than recreating them.
Default to delegated implementation after resumption; an explicit operator choice to continue here
or implement inline takes precedence. Plan approval does not authorize publication.

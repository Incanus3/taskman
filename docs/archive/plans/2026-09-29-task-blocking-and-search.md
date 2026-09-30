# Task blocking relationships and global search implementation plan

**Status:** Completed and archived

This historical plan records the delivered implementation sequence for
[PR #23](https://github.com/Incanus3/taskman/pull/23), tracked by closed Beads issue `tas-gtjx`.
The operator confirmed workstream completion on 2026-09-30. Checked execution items describe
completed work; the specifications below remain the current behavior and architecture contracts.
Merge and deployment require separate authorization.

**Goal:** Deliver directed cross-Project Blocks / Blocked by links, deliberate Done confirmation
with unresolved direct blockers, and global Task jump search with browser, API, CLI, help,
completion, and bundled skill parity.

**Approved specifications:** [Task blocking relationships and Done warning](../../specs/2026-09-27-task-blocking-relationships-design.md)
and [global Task jump search](../../specs/2026-09-29-global-task-search-design.md). Read both in full
before implementing any task. The [relationship product contract](../../product/relationships.md),
[MVP specification](../../product/mvp-spec.md), and [development guide](../../guides/development.md)
remain authoritative.

**Architecture:** `Taskman.Tasks.Search` owns one literal whitespace-term ID/title matcher used
by global search, the link picker, and the existing parent picker. `Taskman.Tasks.Blocking` owns
directed-edge operations, backed by a narrow persistence module and one PostgreSQL transaction
advisory lock shared with status and parent writes. LiveView and API adapters call the Tasks
context; the CLI uses the API. Search and relationship results are compact Task summaries with
priority and current location.

**Planning baseline (historical):** Branch `Incanus3/task-blocking-relationships` at design-approval
commit `62d37156` was clean when planning began. `but status --json` reported `setup_required` in this linked
worktree; do not run `but setup`. Use the repository's raw Git fallback here. `br sync --status`
reported in sync on 2026-09-29. The open `tas-i5t` parent hierarchy coverage issue is separate;
do not mutate it or unrelated Beads state. The Orca setup hook was skipped, but Mix dependencies
were present. This snapshot describes the historical planning environment.

## Global constraints

- Keep Tasks in the existing Ecto context. Web modules use `Taskman.Tasks`, not `Repo`; Ash remains
  in Accounts. Preserve existing optimistic field conflicts and URL-backed Task detail behavior.
- Generate the link migration with `mix ecto.gen.migration add_task_blocking_links` and use its
  generated timestamp. Links store only Task IDs, never Project IDs or a cached resolution flag.
- Require the graph lock for link, parentage, status, and later cascading Task deletion writes.
  Acquire it before Project/Task row locks. Reads and ordinary field/List-location writes do not
  acquire it. Do not add Task, List, or Project deletion endpoints in this increment.
- The only relationship mutation API/CLI direction is blocker-first. The Task detail UI may add
  or remove from either displayed group by passing the corresponding ordered pair to the context.
- Done warning covers an existing non-Done to Done transition with unresolved **direct** blockers.
  Confirmation succeeds if every current blocker ID was confirmed, even when the current set has
  diminished or emptied. Case-specific force allows one request; neither mode bypasses ordinary
  validation or optimistic conflicts. Relationships never change Task status automatically.
- Preserve `Relates to` and prerequisite discovery as future work. Do not add a search index
  without measured latency evidence or a general-purpose relationship framework.
- Keep browser tests minimal: use focused context, controller, CLI, and LiveView tests first. Add
  one browser test only for behavior those tests cannot exercise; inspect narrow layout directly.
- For each bounded task: write a failing behavioral test, run it, make the smallest production
  change, rerun the focused tests, inspect the diff, and commit verified work locally. Do not push,
  merge, deploy, or begin implementation before this plan is approved.

## File and interface map

| Owner | Responsibilities and intended interface |
| --- | --- |
| `lib/taskman/tasks/search.ex` | `filter_by_terms(query, raw_query)` applies the inclusive ID/title OR for every whitespace term, with literal special characters; `search_tasks(raw_query, project_or_nil)` and `blocking_candidates(project, selected_task, raw_query)` apply their own scope, blank behavior, limit, order, and location projection. |
| `lib/taskman/tasks/hierarchy.ex`, `tasks.ex` | The existing parent-candidate query delegates nonblank matching to `Search.filter_by_terms/2`; existing exclusions, blank results, exact-ID promotion, and ordering remain. `Tasks.search_tasks/2` and `Tasks.search_blocking_candidates/3` expose global and picker reads. |
| Generated migration, `lib/taskman/tasks/blocking_link.ex`, `blocking_persistence.ex` | Directed edge table/schema, database constraints, endpoint reads, recursive reachability, and stored-edge insert/delete. Persistence owns Ecto queries and row mechanics. |
| `lib/taskman/tasks/graph_lock.ex`, `blocking.ex`, `mutations.ex`, `tasks.ex` | `GraphLock.acquire!()` holds the fixed `{724_150, 1}` advisory lock within the caller's transaction. `Tasks.list_blocking/2`, `add_block/3`, and `remove_block/3` return scoped, stable domain results. `Tasks.update_task/4` accepts `done_confirmation: {:ids, [pos_integer()]} | :force` for a Done request; warned updates return `{:error, {:unresolved_blockers, summaries}}`. |
| `lib/taskman/change_notifications*`, `lib/taskman_web/live/project_live/reconciliation.ex` | Relationship invalidations after the context transaction, addressed once to each endpoint Project with sorted local `task_ids`; open detail refetches links without treating foreign Task events as local edits. |
| `lib/taskman_web/components/layouts.ex`, `lib/taskman_web/components/global_task_search.ex`, `lib/taskman_web/live/project_live/` | App-wide dropdown/panel, result navigation using fresh Task location and existing `LocationScope.backdrop/3`, Related Tasks component/picker, Done warning state, and responsive placement. |
| `lib/taskman_web/controllers/api/`, `router.ex` | Search, relationship, and Done confirmation routes, strict request validation, exact summary/edge/error shapes. |
| `lib/taskman/cli/`, `priv/taskman_cli_skill/SKILL.md` | HTTP commands, strict response validation, readable/JSON output, registry-driven help and Bash/Fish completion, and version-matched skill guidance. |

The exact private helper names may change while implementing; keep the public context signatures,
API paths, command syntax, and result contracts above and in the approved specifications stable.

## Review focus

These cases need explicit tests in their owning tasks:

1. A reverse or cross-Project edge inserted concurrently cannot complete a cycle (Task 7).
2. A Done PATCH that also changes title writes **no fields** when a newly unconfirmed blocker
   appears, while equal, diminished, and empty blocker sets succeed (Tasks 8 and 9).
3. `%`, `_`, mixed ID/title terms, and an exact numeric ID remain literal and deterministic in
   all search callers (Tasks 1 and 2).
4. A Task moved or deleted after appearing in global results opens its current location or the
   existing not-found state, never a stale route (Task 4).
5. Reparenting into an existing parent-to-child block is rejected, including a link/reparent race,
   without persisting unrelated fields (Tasks 6 and 7).

## Execution sequence

### Task 1: Shared matcher and existing parent picker

**Files:** Create `lib/taskman/tasks/search.ex` and
`test/taskman/tasks/search_test.exs`; modify `lib/taskman/tasks/hierarchy.ex` and
`test/taskman/tasks/hierarchy_test.exs`.

- [x] Add query tests where `42 publish` matches Task 42 titled `Publish site`, each term may
  match ID and title together, other mixed-field matches work, `%` and `_` are literal, title
  matching ignores case, and an exact ID is first. Retain parent tests for empty query, Project
  scope, descendant exclusion, List paths, 20-result cap, and stable non-exact order.
- [x] Run `mix test test/taskman/tasks/search_test.exs test/taskman/tasks/hierarchy_test.exs`;
  confirm the new matcher assertions fail before implementation.
- [x] Implement `Search.filter_by_terms/2` with bound literal substring values and decimal Task ID
  matching, then call it from the existing parent query. Use PostgreSQL `strpos` on lowercased
  titles and decimal IDs so `%` and `_` remain literal without `LIKE` escaping. Combine the two
  fields with inclusive OR per term and the terms with AND. Keep parent eligibility and sorting
  in `Hierarchy`; never interpolate raw search terms into SQL source text.
- [x] Rerun those files; inspect generated query behavior for special characters and exact IDs.
  Commit the matcher and parent-picker adaptation.

### Task 2: Global search context and authenticated API

**Files:** Extend `search.ex` and `tasks.ex`; create
`lib/taskman_web/controllers/api/task_search_controller.ex`,
`test/taskman_web/controllers/api/task_search_controller_test.exs`; modify `router.ex` and
`test/taskman/tasks/search_test.exs`.

- [x] Test all-Project results, optional Project restriction, all statuses, exact-ID priority,
  case-insensitive title/Project ordering, stable ID tie, 20-result cap, priority and full owning
  List path, and blank input. Test that the query filters in PostgreSQL rather than loading every
  Task or issuing one query per Project.
- [x] Test `GET /api/v1/tasks/search?q=...` with and without `project_id`: exact summary fields,
  `200` empty `data`, `400 invalid_request` for missing/blank/repeated `q`, repeated or malformed
  `project_id`, unsupported keys, `404 not_found` for an unknown Project, and existing API
  authentication behavior. Register the route before any catch-all Task route.
- [x] Run the two focused files and confirm the new tests fail. Implement
  `Tasks.search_tasks(raw_query, project_or_nil)` and a controller that validates the raw query
  parameters before calling it. Project-root location uses `kind: "project"`, `list_id: nil`, and
  an empty path; listed Tasks use the full current path. Return exactly the seven summary fields
  in the approved design.
- [x] Rerun the focused files, inspect the query plan on representative seeded data, and record
  latency evidence. Add no index unless the measured result warrants it. Commit the context and
  API slice.

### Task 3: Global search CLI, help, completion, and skill

**Files:** Modify `lib/taskman/cli/registry.ex`, `commands/tasks.ex`,
`presentation/output.ex`, `client.ex` or its response validator,
`priv/taskman_cli_skill/SKILL.md`, and focused files under `test/taskman/cli/`.

- [x] Add Req.Test coverage for `taskman tasks search "42 publish"` and `--project 9`: URI/query,
  optional Project dispatch before ordinary scoped Task commands, readable
  `ID TITLE STATUS PRIORITY PROJECT LOCATION` columns, zero-match message, and exact JSON
  envelope. Test local exit 2 for missing/blank query and malformed Project without an HTTP call,
  exit 3 for unknown Project, and exit 5 for a malformed success summary.
- [x] Add registry/help/Bash/Fish completion assertions for the new command and optional Project.
  Test installed skill content includes a search example followed by `tasks show --project ...`
  using result IDs and explains summary versus full detail.
- [x] Run `mix test test/taskman/cli`; confirm the new assertions fail. Add the command record,
  dispatch branch, search-summary validator, and command-specific output. Keep existing `tasks
  list` formatting and its required Project option unchanged.
- [x] Rerun focused CLI and skill-install tests; commit the programmatic search surface.

### Task 4: Global navbar search and Task navigation

**Files:** Create `lib/taskman_web/components/global_task_search.ex` and focused
`test/taskman_web/live/global_task_search_test.exs`; modify `layouts.ex`,
`project_live.html.heex`, `project_live/paths.ex`, relevant authenticated LiveView templates,
and `project_live/tasks/location_scope.ex` only if a pure route helper is needed.

- [x] Write LiveView tests for the centered desktop control and narrow-panel trigger, blank
  prompt/no rows, loading, no-match and retry states, status/priority/location in rows, selected
  result navigation, and no Task mutation. Test same-Project visible backdrop retention versus
  owning List/root fallback, cross-Project fallback, and a result moved or deleted after display.
- [x] Run the focused LiveView file and confirm failure. Implement one layout-owned stateful
  LiveComponent with an accessible combobox/listbox, arrow/Enter/Escape/pointer behavior, focus
  restoration, and a capped query through `Tasks.search_tasks/2`. Re-resolve the selected Task,
  Project, and List through contexts before building the route. Pass the current Project workspace
  to the layout only from `ProjectLive`; other pages use the owning location. Modal layering keeps
  the search unavailable while Task detail is open.
- [x] Rerun focused LiveView tests and inspect wide/narrow rendering directly. Add at most one
  browser test if actual keyboard focus or viewport behavior cannot be established through
  LiveView and direct inspection; document the specific gap before adding it. Commit the browser
  search slice.

### Task 5: Directed edge migration and storage primitives

**Files:** Generate a migration under `priv/repo/migrations/`; create
`lib/taskman/tasks/blocking_link.ex`, `blocking_persistence.ex`, and
`test/taskman/tasks/blocking_persistence_test.exs`.

- [x] Add tests for one ordered pair across Projects, unique-pair and non-self constraints,
  required endpoints, endpoint lookup indexes, UTC timestamps, and `ON DELETE CASCADE` from
  either Task to its incident edges. Verify deleting a link leaves both Tasks intact.
- [x] Read `mix help ecto.gen.migration`, then generate the migration with
  `mix ecto.gen.migration add_task_blocking_links`. Define the
  bigint primary key, two bigint Task foreign keys, unique ordered-pair index, non-self check,
  and indexes usable for both incoming/outgoing lookup. Map constraints in the schema.
- [x] Run `mix test test/taskman/tasks/blocking_persistence_test.exs` with the repository's
  normal test-database preparation. Inspect the migration up/down behavior and commit the
  storage slice.

### Task 6: Shared graph lock and parent/status write participation

**Files:** Create `lib/taskman/tasks/graph_lock.ex`; modify `tasks/mutations.ex`,
`tasks/hierarchy.ex`, `test/taskman/tasks/parentage_test.exs`, and
`test/taskman/tasks/mutations_test.exs`.

- [x] Add tests that parent assignment/change and every persisted status change use one
  transaction-held graph lock before the existing Project row lock; ordinary title, priority,
  description, due-date, and List-location edits keep their existing path. Preserve optimistic
  same-field conflict behavior and atomic mixed-field updates.
- [x] Run the two focused files and confirm the new lock-order assertions fail. Implement
  `GraphLock.acquire!()` with a descriptively named `{724_150, 1}` attribute and
  `pg_advisory_xact_lock($1, $2)`. Integrate it inside the existing parent transactions and a
  transactional status-update path, before reloading Tasks. A combined parent/status PATCH uses
  one lock and one transaction. Keep the no-parent create path outside the lock.
- [x] Rerun focused parentage/mutation/conflict tests. Commit the lock integration before adding
  link writes, so every graph participant uses the same ordering.

### Task 7: Blocking context, graph validation, and relationship reads

**Files:** Create `lib/taskman/tasks/blocking.ex`, extend `blocking_persistence.ex`,
`mutations.ex`, and `tasks.ex`; create `test/taskman/tasks/blocking_test.exs` and
`test/taskman/tasks/blocking_concurrency_test.exs`, and extend the parentage tests.

- [x] Test both ordered perspectives and summary ordering/fields; cross-Project adds/removes;
  duplicate, self, reverse, and longer cycle rejection; child-blocks-parent allowed;
  parent-blocks-child forbidden; missing/stale endpoints; missing-edge removal; and no status
  changes when adding an unresolved blocker to an already Done Task.
- [x] Add deterministic concurrency tests for opposite edge adds and link/reparent races using
  separate sandbox connections and a gate/barrier rather than sleeps. Check one loser returns a
  domain validation error and the persisted graph remains valid. Test a parent update that also
  changes title rolls back fully when the existing link would become forbidden.
- [x] Run the focused files and confirm failures. Implement
  `Tasks.list_blocking(project, selected_task)`, `Tasks.add_block(blocker_project, blocker,
  blocked)`, and `Tasks.remove_block(blocker_project, blocker, blocked)`. Each mutation starts a
  transaction, acquires the graph lock, reloads both endpoints, validates under the lock, and
  returns named endpoint summaries. Use recursive reachability for cycle detection and
  `target_task_id` changeset errors with cause-specific messages. Validate parent changes against
  existing direct edges under the same lock.
- [x] Rerun the context and concurrency files plus parentage tests; commit the graph domain.

### Task 8: Atomic Done warning and confirmation in the Tasks context

**Files:** Modify `tasks/mutations.ex`; retain the existing option-forwarding wrapper in `tasks.ex`; create
`test/taskman/tasks/done_confirmation_test.exs` and extend conflict/concurrency tests.

- [x] Test direct unresolved blockers versus resolved (`done`, `will_not_do`) and indirect ones;
  non-Done to Done warning; already-Done no-op; non-Done status updates; adding a blocker to an
  already Done Task; and no automatic dependent-status change. Test first-request confirmed IDs,
  equal/diminished/empty current sets, a newly unconfirmed ID, and force during a changed blocker
  set. Include a warned title-plus-status update and assert **neither** field persisted.
- [x] Test force and ID confirmation against optimistic conflicts and validation failures. Use
  deterministic barriers to interleave link/status changes before the Done transaction acquires
  the lock; assert the current set determines the result.
- [x] Run the focused file and confirm failures. Extend `Tasks.update_task/4` with
  `done_confirmation: {:ids, ids} | :force`, and return
  `{:error, {:unresolved_blockers, blocker_summaries}}` on a warning. Under the graph lock,
  reload current Task and incoming blocker statuses, compare `C` against submitted `S` using
  `MapSet.subset?/2`, and persist the whole update only when allowed. Preserve the current
  optimistic retry/conflict rules. A no-confirmation Done request with an empty `C` succeeds.
- [x] Rerun focused status/conflict/concurrency tests; commit Done domain semantics.

### Task 9: Relationship API and Done confirmation API

**Files:** Create `lib/taskman_web/controllers/api/task_blocking_controller.ex` and
`test/taskman_web/controllers/api/task_blocking_controller_test.exs`; modify `router.ex`,
`controllers/api/task_controller.ex`, `fallback_controller.ex`, `representation.ex`, and
`test/taskman_web/controllers/api/task_controller_test.exs`, plus the cross-Project target lookup
in `lib/taskman/tasks.ex`.

- [x] Test authenticated `GET .../blocking` with both Task-summary arrays; blocker-first
  `POST`/`DELETE .../blocks/:target_task_id` with no body and `201`/`200` named endpoint
  summaries. Cover cross-Project targets and reading an incoming link from the blocked Task.
  Test malformed IDs/body/query keys (`400`), missing route Project/endpoints/edge (`404`), and
  duplicate/self/cycle/parent-direction errors (`422` with `fields.target_task_id`).
- [x] Test `PATCH` warning `409 unresolved_blockers` with full priority-bearing blocker
  summaries, valid first-request/retry IDs, diminished/equal/empty sets, one-request force, and
  atomic multi-field refusal. Test empty/malformed/duplicate IDs, both confirmation fields,
  force other than literal `true`, confirmation on non-Done, and ordinary `409
  concurrent_update` precedence and Project scope.
- [x] Run the focused controller files and confirm failure. Add routes and strict request
  parsing. Translate context summaries to the exact JSON fields. Keep confirmation separate from
  editable Task attributes; never cast it onto the Task schema. Add the warning fallback branch
  without changing existing error envelopes.
- [x] Rerun focused controller and authentication tests; commit the API contract.

### Task 10: Relationship CLI, Done options, completion, and skill

**Files:** Modify `lib/taskman/cli/registry.ex`, `commands/tasks.ex`,
`presentation/output.ex`, `execution/parser.ex`, the response validator in `client.ex`, and
`priv/taskman_cli_skill/SKILL.md`; extend focused `test/taskman/cli/` files.

- [x] Use Req.Test to pin `tasks blocking show` and `tasks blocks add|remove` routes, exact
  blocker-first IDs, read sections, edge output, JSON passthrough, malformed response rejection,
  missing edge/validation exit 3, and local invalid-ID exit 2. Include an incoming blocker example
  whose source Project differs from the blocked Task's Project.
- [x] Test `tasks update --status done` warning on stderr/exit 3, first-run and retry
  `--confirm-unresolved-blockers 12,15`, refreshed warning on a new ID, and
  `--force-done-with-unresolved-blockers` in a single request. Test mutual exclusion, malformed
  comma list, non-Done use, readable/JSON success, and no HTTP request on local exit-2 errors.
- [x] Test registry help, Bash/Fish completion, and installed skill content for each command and
  option. The skill must explain human lifecycle authority and the case-specific force override.
- [x] Run `mix test test/taskman/cli`; confirm failures. Add registry records/constraints, request
  bodies, strict success/error validation, and dedicated readable rendering. Preserve existing
  exit conventions and all unrelated Task command output.
- [x] Rerun CLI, help, completion, and skill-install files; commit parity material.

### Task 11: Relationship invalidations and open-detail reconciliation

**Files:** Modify `lib/taskman/change_notifications.ex`, `event.ex`, `tasks.ex`,
`lib/taskman_web/live/project_live/reconciliation.ex`, `project_live/tasks/editing.ex`, and
focused notification/reconciliation tests.

- [x] Test one post-commit relationship invalidation per distinct endpoint Project after add or
  remove, including same-Project deduplication. Test linked Task title/status/priority/location
  changes prompt a relationship refetch for an open linked Task in either the same or another
  Project, with no foreign Task payload mistaken for a local Task update. Test that existing
  workspace events for Project and List name changes refresh an open Related Tasks display,
  including a renamed ancestor List in the owning path. No event should publish for a rejected
  transaction.
- [x] Run focused notification/reconciliation tests and confirm failures. Add a distinct
  `Event` entity/operation for relationship invalidation that carries sorted local `task_ids`.
  Publish after the context returns a successful transaction. This follows the existing top-level
  context convention, not an after-commit hook for a caller's outer transaction. Reconciliation
  refetches only open detail whose selected ID is in the event; route entry also loads current
  relationships. Existing workspace events with a changed Project or List `name` refresh Related
  Tasks whenever detail is open.
- [x] Rerun focused tests and commit the invalidation path.

### Task 12: Related Tasks and Done warning in Task detail

**Files:** Create `lib/taskman_web/components/tasks/related_tasks.ex` and a focused picker state
module under `lib/taskman_web/live/project_live/tasks/`; modify `components/tasks/detail.ex`,
`project_live/tasks/editing.ex`, `project_live/tasks/autosave.ex`, `project_live.ex`,
`project_live.html.heex`, and focused
LiveView tests under `test/taskman_web/live/project_live/`.

- [x] Test Blocks and Blocked by groups, counts, empty states, status/priority/Project in rows,
  in-Project and cross-Project navigation, Project-first candidate search (blank and multi-term),
  both add directions through the context, one-click remove, stale candidate refusal, and local
  error display. Test narrow DOM ordering: main fields, Related Tasks, Activity/Sessions.
- [x] Test selecting Done with unresolved direct blockers leaves persistence unchanged and keeps
  the Done draft; Keep current status cancels; Mark Done anyway sends displayed IDs; a diminished
  set succeeds; a new unconfirmed blocker refreshes the warning. Closing detail or choosing another
  status discards the pending confirmation. Other autosave fields and existing conflict handling
  remain usable.
- [x] Run focused LiveView/autosave files and confirm failures. Add component and state/event
  handling using `Tasks.list_blocking/2`, `search_blocking_candidates/3`, `add_block/3`,
  `remove_block/3`, and `update_task/4`. Render the section below main fields and before
  Activity/Sessions on narrow displays. Do not call the JSON API or `Repo` from LiveView.
- [x] Rerun focused LiveView tests, inspect narrow and wide layouts and accessible interaction
  controls directly, then commit the Task detail slice. Physical browser input delivery remains the
  recorded verification limit. Add a browser test only for a concrete behavior
  that LiveView tests and direct inspection cannot cover.

### Task 13: End-to-end verification and implementation handoff

**Files:** Update `docs/handoffs/task-relationships.md` and its index, plus any canonical
documentation whose guidance changed during implementation. Do not retire the handoff without
explicit operator workstream-completion confirmation.

- [x] Run focused context, controller, CLI, LiveView, migration, and concurrency suites. Run
  `ERL_FLAGS='+S 4' mix precommit`. If one targeted browser test was justified, run it through
  `mix test.browser` with Chromium/ChromeDriver; otherwise record direct responsive inspection.
- [x] Scan implementation-facing files for planning terminology, stale help, and missing
  UI/API/CLI/skill parity. Inspect the actual generated migration, advisory-lock order, and
  duplicate/foreign Task route handling. Obtain an independent specialist review of graph and
  concurrency correctness.
- [x] Record verified results, unresolved risks, and the exact next approval or delivery gate in
  the handoff. Commit verified documentation locally. Do not push, merge, deploy, or treat passing
  tests as authorization for external actions.

## Completion

Implementation, scoped reviews, graph specialist verification, and publication are complete.
The operator confirmed workstream completion. The current specifications preserve behavior,
architecture decisions, and verification limits; the Beads issue records delivery evidence.
Merge and deployment require separate authorization.

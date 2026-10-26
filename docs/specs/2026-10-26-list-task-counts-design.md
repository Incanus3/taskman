# Task counts in List navigation and headers

Status: implemented, independently reviewed, and operator accepted. D1–D6 are accepted.

Show the size of each List under the active filters, and show how much work the current view
represents relative to its broader task population. This is a small UI addition to the existing
Phoenix LiveView workspace; it needs no schema migration or dependency.

## Decisions for review

| ID | Status | Choice | Cost or constraint | Details |
|---|---|---|---|---|
| D1 | Accepted | Apply the current filters to every sidebar count; header uses visible / eligible counts | Denominator changes when closed statuses are selected | [Counting contract](#counting-contract) |
| D2 | Accepted | Return every List's ID, parent ID, and direct visible/eligible counts, plus Project tasks, in one query; roll up child Lists from those rows | Query must retain empty Lists; counts must be recomputed for changed filters | [Query approach](#query-approach) |
| D3 | Accepted | Show counts including zero in badges; align sidebar badges right, replacing them with actions on hover or keyboard focus; show both on touch | Hover/focus temporarily hides sidebar counts; touch needs room for both counts and actions | [Presentation](#presentation) |
| D4 | Accepted | Keep aggregate counts internal to the browser feature; no new API or CLI operation | API/CLI consumers continue deriving counts from existing task queries | [Scope](#scope-and-inherited-constraints) |
| D5 | Accepted | Exclude Done as well as Will Not Do from the default status selection | Completed tasks require explicitly enabling Done | [Default selection](#default-status-selection) |
| D6 | Accepted | Preserve saved status selections and explicit shared-link filters when changing the default | Existing browsers with Done saved remain opted into Done | [Default selection](#default-status-selection) |

## Counting contract

For a location, let `S` be the selected statuses and `U` be Icebox, Pending, In Progress, and
In Review. Closed statuses are Done and Will Not Do.

- **Visible (`a`)** counts tasks whose status belongs to `S`.
- **Eligible (`b`)** counts tasks whose status belongs to `U` plus whichever closed statuses
  belong to `S`: `U ∪ (S ∩ {Done, Will Not Do})`.
- Both use identical location scope. With Include child Lists off, count only tasks owned directly
  by that List, or directly by the Project for “Project tasks”. With it on, add every descendant
  List; “Project tasks” then covers the entire Project.
- Each Task counts once in a location's population. Parent-child Task relationships do not alter
  owning location or cause traversal, exclusions, or double counting. Nested Lists roll up even
  when their sidebar branches are collapsed.
- Every sidebar row uses the current global status and child-list filters, regardless of
  selection. The selected location's sidebar number equals the header numerator.
- An empty status selection gives `a = 0` and `b = count(U)`. An empty population shows `0 / 0`.
  The set rule ensures `0 ≤ a ≤ b`; this is not a completed / total progress indicator.
- The new default status selection is `U`, excluding both Done and Will Not Do. The default view
  therefore counts all unclosed tasks in both numerator and denominator.

For a synthetic location with counts Icebox 2, Pending 3, In Progress 4, In Review 1, Done 5,
and Will Not Do 6, keeping the location scope fixed:

| Selected statuses | Sidebar / numerator | Header |
|---|---|---|
| Default (all unclosed statuses) | 10 | 10 / 10 |
| In Review | 1 | 1 / 10 |
| In Review, Done | 6 | 6 / 15 |
| Will Not Do | 6 | 6 / 16 |
| Done, Will Not Do | 11 | 11 / 21 |
| All statuses | 21 | 21 / 21 |
| None | 0 | 0 / 10 |

## Default status selection

D5 changes the fallback status selection to Icebox, Pending, In Progress, and In Review.
Done remains available in the filter, and selecting it still expands both counts according to
the counting contract. Include child Lists continues to default to off.

D6 retains the existing precedence: explicit URL status filters override saved browser
selections, which override the default. Preserve saved selections, including Done or an empty
selection; do not migrate browser storage or rewrite shared links. This respects existing choices
and link snapshots, but users with Done already saved will not automatically see it deselected.
Removing Done from saved selections was rejected because it would change previously chosen
filters and require a separate migration rule.

## Presentation

Use muted, noninteractive badges to visually distinguish counts from names. Show zero explicitly
rather than hiding it. Sidebar badges share a common right edge regardless of List nesting,
including the “Project tasks” row. Keep badges readable with long names.

On devices with hover, the row's action buttons replace its badge when the row is hovered or
contains keyboard focus. This also applies to the “Project tasks” add-root-List action, which
becomes hidden until hover or keyboard focus. Preserve keyboard access to all actions and existing
navigation disclosure. Use the same trailing area for the badge and actions so the swap does not
shift the name or row. On touch devices, show the badge and action buttons side by side, with
badges aligned consistently across rows; both must remain available without hover.

Place `a / b` in a single badge in the main location header immediately before Share, on the right
with the header controls at desktop widths. This badge stays visible during hover and focus.
Use 14px header text with 8px padding on every side, giving the badge roughly the Share
button’s height; keep sidebar badges compact. Permit wrapping on small screens without clipping
counts or controls.
Use tabular numerals and an accessible description explaining that the first number matches the
filters and the second includes all unclosed tasks plus selected closed statuses. Provide a
short styled tooltip (“Matching / eligible tasks” for the header, “Matching tasks” for the sidebar)
as well as the full accessible description. Use the existing action-button tooltip system for
the header controls too; Include child Lists says “Include tasks from child Lists.” Sidebar
counts should be identified as matching tasks without replacing the existing navigation labels.
Do not make every count an announcing live region.

## Query approach

D2 uses one Project-scoped query returning a self-contained accounting result:

- One row for every List in the Project, including Lists with no direct eligible tasks. Each row
  contains `list_id`, `parent_list_id`, `visible_count`, and `eligible_count`.
- One Project-tasks row, including when its direct counts are zero. Its `list_id` and
  `parent_list_id` are null; its counts represent direct Project tasks.

Top-level Lists have null `parent_list_id`, identifying the Project-tasks row as their rollup
destination. The null-ID row is the terminal root, not its own child. No List names, Task records,
or other hierarchy lookup are required for count calculation.

Let `E = U ∪ (S ∩ {Done, Will Not Do})`. Restrict the aggregate's input to the Project and statuses
in `E`. Count all remaining tasks in each group for the eligible count, and conditionally count
those with status in `S` for the visible count. This is valid because `S` is a subset of `E`.
An empty `S` produces zero visible counts while still counting all unclosed tasks as eligible.
The task aggregate groups only by `list_id`. In the same SQL statement, left-join all Project
Lists to those aggregates and include the Project-tasks row, coalescing missing counts to zero.
Keep the eligibility restriction inside the task aggregate so it cannot remove empty Lists from
the final result. Simply joining parent IDs onto task aggregate rows is insufficient: it would
omit empty intermediate ancestors needed to reach higher Lists and the Project.

Accumulate both counts from children to parents when enabled, using only the returned parent
IDs. With inclusion off, use the direct counts. Keep aggregation proportional to the number of
Lists, including empty ancestors with nonempty descendants. No separate hierarchy query, Task
hierarchy traversal, persisted counters, or cache are needed for this accounting. Navigation may
still load its own List data. Filter changes recompute the query; do not retain per-status buckets.
Elixir owns the status sets and constructs the query; PostgreSQL executes its filtering and
aggregation. No stored procedure or persisted database business logic is introduced.

Returning hierarchy alongside the counts requires the List join and explicit Project row in the
SQL statement and returns zero-count Lists too. A task-only aggregate can be smaller and its query
is simpler, but its caller must obtain the complete hierarchy separately. The accepted shape makes
accounting independent of navigation's loaded data and reads its parent links and counts together.
In the current implementation, `Workspace.refresh/1` consumes the hierarchy to build navigation
without retaining it for `Listing.refresh/1`; task-table queries also load Lists internally.
Reusing those reads would require passing the hierarchy through the refresh paths or retaining
shared hierarchy state. The extra List join keeps this feature independent of that coordination.

Grouping by both List and status and fetching every status was also considered. The accepted
direct count pair returns at most one row per owning location and removes application-side status
summation. Ineligible closed tasks need not enter the aggregate. Per-status buckets could derive
new status combinations without another aggregate query if retained, but this feature has no
requirement to cache them. These structural savings do not establish a measured database
latency gain.

Separate count queries for each location were also considered: initially simpler, but query count
would grow with the sidebar. Loading full Task records just to count them wastes work. The revised
grouped approach keeps database query count independent of the number of Lists, at the cost of
explicit in-memory List aggregation.

Expected ownership:

- `Taskman.Tasks` exposes the internal count use case. A focused module under
  `lib/taskman/tasks/` owns count calculation, with storage queries in a focused persistence
  module as required by the development guide. No Repo calls or Ecto queries enter the web layer.
- `ProjectLive.Tasks.Listing` owns the current count results alongside listing state. Use one
  calculation result for the sidebar map and selected header pair; clear stale state when the
  Project disappears or changes. An unavailable location has no header count.
- `ProjectLive.Workspace` and the listing coordinator ensure updated counts reach navigation
  stream items. Updating a separate assign alone does not rerender existing streamed rows:
  re-stream affected navigation items, preserving disclosure and editing state.
- `WorkspaceNavigation` and `project_live.html.heex` render counts with stable element IDs.
- `TaskTablePreferences.defaults/0` and the fallback in `assets/js/project_live_hooks.js` must
  agree on the new default so browser hydration cannot re-enable Done inadvertently.

Refresh on route and Project changes, browser preference hydration, either filter change, local
task creation/status changes/moves/deletions, List membership changes, and the corresponding
validated external notifications. A Task change in an unselected List must still refresh its
sidebar count and affected ancestors. Expanding a branch renders current counts. Sorting does
not affect values. Failed mutations reflect persisted state rather than optimistic count edits.
Reuse existing reconciliation and reconnect behavior; do not add polling or a second event bus.

The accounting query reads parent links and direct counts in one statement snapshot. Navigation
and task rows are read separately, so this feature does not introduce a cross-query snapshot
guarantee for the complete UI; existing validated notifications and reloads reconcile subsequent
state. Sidebar and header count values within a refresh must use the same calculated result.

## Scope and inherited constraints

The [domain model](../product/domain.md) owns location and status semantics. The
[active Project selector specification](2026-09-22-active-project-selector-design.md) owns global
filter preferences and shared-view snapshots. D5 replaces its previous default of all statuses
except Will Not Do; D6 addresses saved-selection handling. This feature adds no preference or URL
parameter. The [development guide](../guides/development.md) owns web/context
boundaries, persistence ownership, and verification policy.

D4 is an accepted exception to API/CLI parity for new query operations: these counts
summarize existing task queries for browser navigation, without introducing new persisted data or
task-selection capability. Existing API/CLI task retrieval remains unchanged. No new command,
help, completion, or bundled-skill surface is included.

No progress bar, Project selector totals, task-detail relationship counts, pagination, deployment,
or unrelated refactoring is included. Implementation details such as
internal function names and the precise count-state representation remain the implementer's choice.

## Evidence and verification

Repository inspection on 2026-10-26 established:

- At the design baseline, `TaskTablePreferences.defaults/0` and JavaScript hydration fallback
  selected every status except Will Not Do. The implemented fallback now uses D5 in both places.
- `Tasks.list_tasks_for_location/3` filters direct or recursive List ownership; empty statuses
  match nothing. Its current recursive queries provide the scope to reproduce.
- `WorkspaceNavigation.tree/1` streams List rows; the Project row is outside that stream.
- `Listing.refresh/1` refreshes the table, while `Workspace.refresh/1` refreshes navigation.
  `Reconciliation` invokes both for validated task notifications; filter changes currently
  refresh the listing. Count integration must cover both paths.

The implementation now covers the following verification responsibilities through focused context,
LiveView, preference, and browser tests:

- Context tests cover the status examples above, empty Lists, direct Project tasks, nested Lists,
  sibling exclusion, Project isolation, and Task hierarchy ownership. Assert agreement with
  existing location queries over representative scopes and verify bounded aggregate query count.
  Verify the accounting result includes every List with correct parent IDs and the Project-tasks
  row, including empty Projects and Lists containing only ineligible closed tasks. Cover multiple
  empty intermediate ancestors whose descendant contributes eligible tasks. Verify direct
  visible/eligible pairs and final rollups separately, and that counting needs no extra hierarchy
  query.
- LiveView tests use stable count IDs to prove sidebar/header agreement, global filters affecting
  unselected Lists, zero display, preference application, navigation expansion, mutation refresh,
  and external changes affecting another List. Include a move updating source/destination and
  ancestor counts, and deletion clearing stale location counts.
- Verify server and browser defaults exclude both closed statuses, fresh browsers apply that
  default, and stored selections and explicit shared-link snapshots retain D6's existing precedence.
- Inspect desktop and narrow layouts, including long names, aligned badges, the hover/focus swap
  on both List and Project rows, and simultaneous badges/actions on touch. Verify actions remain
  keyboard-accessible and swapping does not shift names. Browser inspection should record actual
  viewport width and horizontal scroll width. Add a focused Wallaby test only where needed to
  establish browser-only interaction behavior; do not duplicate server-owned count tests.
- Run focused context and LiveView tests, then `mix precommit`. Check implementation-facing files
  for planning terminology and audit changed files for privacy before committing.

Browser interaction coverage checks computed visibility, real focus and Tab reachability on
hover-capable Chromium. Responsive and non-hover acceptance uses measured browser capabilities,
geometry, screenshots, and touch input. Touch acceptance uses Chromium emulation rather than a
physical device; hover tests require headed Chromium with a display in the verification environment.
Task and List deletion recovery is tested through persisted fixture removal and existing validated
notifications because there is no supported deletion UI; no deletion operation is added.
The unavailable-preference-storage check denies
localStorage reads/writes while keeping sessionStorage available for Phoenix history and verifies
connection plus hydration. Blanket sessionStorage denial prevents the framework from starting and
is outside this preference fallback contract.

## Approval scope

The accepted scope covers the counting, presentation, query, refresh, and verification contracts
in this specification. Merging and deployment remain separate operator decisions.

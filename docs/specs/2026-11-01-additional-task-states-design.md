# Additional Task states and Not Planned

**Status:** Behavioral decisions accepted in discussion; full design awaiting approval.
**Date:** 2026-11-01

Keep ordinary Task selectors compact as the supported state vocabulary grows. Introduce
**Not Planned** for work currently considered unnecessary but retained for reconsideration.
Additional states remain valid everywhere; contextual visibility only limits UI options.

## Decisions and approval scope

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Default states plus additional states observed in the relevant scope | List options can remain available after their last matching Task changes state | [Scopes](#option-availability-uses-the-unfiltered-task-population) |
| D2 | Accepted | Preserve filter preferences independently of available options | Checkbox submissions must preserve absent states | [Preferences](#filter-preferences-survive-absence-and-navigation) |
| D3 | Accepted | One-way reveal for the lifetime of a create/edit form; retain its selected state | Reveal state must reset at the correct form boundary | [Forms](#forms-can-reveal-all-states-without-changing-the-selected-value) |
| D4 | Accepted | One canonical order for selectors and Task status sorting; additional options have a muted background | Existing SQL/hierarchy sort ranks change; muted backgrounds must preserve focus/selection readability | [Presentation](#selector-order-and-presentation) |
| D5 | Accepted | Not Planned is additional, closed, initially excluded only in the main listing | Closed work remains available through explicit filtering | [Definitions](#state-definitions) |
| D6 | Accepted | Only Done resolves blockers; Will Not Do and Not Planned remain unresolved | Previously non-warning Done transitions may now require confirmation | [Blocking](#closed-work-can-still-block-other-tasks) |
| D7 | Accepted | Centralize state definitions including a color token; translate tokens into styles in the web layer | Existing hardcoded state lists, colors and semantic checks must be reconciled | [Ownership](#ownership-and-implementation-boundaries) |
| D8 | Accepted | Show state descriptions in delayed tooltips on options and table state badges | Pickers require active-option/top-layer support; badge hover must preserve row navigation | [Descriptions](#descriptions-appear-in-delayed-option-tooltips) |
| D9 | Accepted | Precompute List availability and retain observed states for the open page; reload rebuilds from current Tasks | Retained states may have zero current Tasks; PubSub invalidations may require a single-Task read | [Updates](#availability-is-prepared-before-controls-open) |

D1–D9 reflect explicit operator decisions. The complete written specification still requires design
approval before implementation planning. Approval covers this feature and the Will Not Do
blocker correction; it does not authorize implementation, pushing, merging or deployment.

## Existing contracts and observed baseline

Inspected source on branch `feature/additional_states`, starting at revision
`8754d59462f300d056c3da17d956405575f95027`; no product changes have been made for this feature.

- [Domain model](../product/domain.md) owns current lifecycle and count semantics.
- [Hierarchy filter](2026-10-28-hierarchy-status-filter-design.md) owns modal-local preferences,
  ancestry preservation, required paths, expansion and dismissal behavior.
- [Shared controls](2026-10-28-form-controls-design.md) owns the native-select bridge and enhanced
  picker behavior. [Live updates](2026-09-01-live-task-updates-design.md) owns reconciliation.
- `Task.statuses/0` currently returns six Ecto enum values. The initial migration also restricts
  the string column with `tasks_status_check`.
- `TaskTablePreferences` and `assets/js/project_live_hooks.js` persist a full inclusion list in
  browser local storage. Explicit URL snapshots take precedence. Defaults omit Done and Will Not Do.
- Hierarchy state starts with every supported state checked, retains choices within an open
  dialog, and resets on close. Its complete source tree is available before projection.
- `Tasks.Counts` derives eligible counts from all open states plus explicitly selected closed
  states. `Mutations.unresolved_blocker_summaries/1` currently excludes Done and Will Not Do.
- Selectors use `Task.statuses/0`, while SQL and hierarchy status sorting have a separate existing
  rank: Will Not Do, Icebox, Pending, In Progress, In Review, Done in ascending order.
- `assets/js/tooltips.js` uses a 450 ms hover delay and immediate keyboard-focus display.
  Its tooltip is appended to the document body and marked `aria-hidden`. The enhanced select
  instead focuses its listbox and tracks the active option with `aria-activedescendant`, while
  both the select options and hierarchy filter can use top-layer popovers. Existing tooltip
  behavior therefore needs bounded extensions for these description tooltips.
- Table state badges currently disable pointer events so clicking their area reaches the row's
  task-opening link. Adding hover descriptions must preserve that navigation behavior.
- `ChangeNotifications.Event` carries Task IDs and changed-field names, not state/location
  snapshots. Task notifications target the active Project subscription and use `broadcast_from`,
  excluding the publishing LiveView. `Workspace.refresh/1` loads List navigation; listing count
  queries exclude unchecked closed states and cannot themselves discover all additional states.

This specification replaces the blocker-resolution rule in the domain, relationships, MVP and
blocking specification when implemented. Other lifecycle, ownership, recovery and confirmation
contracts remain authoritative.

## State definitions

| Identifier | Label | Visibility | Initially included in main listing | Counts as open | Resolves blockers |
|---|---|---|---|---|---|
| `icebox` | Icebox | Default | Yes | Yes | No |
| `pending` | Pending | Default | Yes | Yes | No |
| `in_progress` | In Progress | Default | Yes | Yes | No |
| `in_review` | In Review | Default | Yes | Yes | No |
| `done` | Done | Default | No | No | Yes |
| `not_planned` | Not Planned | Additional | No | No | No |
| `will_not_do` | Will Not Do | Default | No | No | No |

Identifiers are stable storage/API values, independent of labels. Keep every existing state
default-visible. Pending remains the single initial Task state; hierarchy initialization remains
the general rule “all supported states checked,” not per-state metadata.

Descriptions explain intended usage. Preserve existing definitions for the six existing states.
Not Planned means “Work we currently consider unnecessary, retained for reconsideration if the
need arises.” Icebox means deferred work expected to remain actionable; Will Not Do means
explicitly abandoned or declined work. Human acceptance of completed work remains specific to Done.
Closed, completed and blocker-resolving are distinct concepts; no configurable completion engine
is needed for the existing Done-specific behavior.

Descriptions and a color token belong in the shared state definitions and product/agent
documentation. The web layer maps the token to concrete text/background styles rather than
maintaining an independent state-to-color assignment. Preserve existing state colors; choose an
accessible subdued color for Not Planned. Exact shade/token is an implementation choice.
The additional-option background treatment remains separate from each state's identifying color.

## Option availability uses the unfiltered Task population

Every selector includes all default states. Bootstrap additional-state availability from the
following unfiltered populations. List/Project scopes then retain additional states observed in
use for the open page, even after their last matching Task changes state or moves away. This
session-local retention replaces the earlier draft's requirement to remove unused List states.
The hierarchy continues to derive availability from its complete currently loaded tree.

| Selector | Population for initial/current state observation |
|---|---|
| Main listing, List, child Lists off | Tasks owned directly by that List |
| Main listing, List, child Lists on | Tasks owned by that List or any descendant List |
| Main listing, Project, child Lists off | Tasks owned directly by the Project, without an owning List |
| Main listing, Project, child Lists on | All Tasks in that Project |
| Hierarchy sidebar | Entire connected Task hierarchy loaded for the selected Task |
| Create/edit form, owning List selected | Tasks owned directly by that List |
| Create/edit form, no owning List | All Tasks in the form's Project |

List ancestry and Task parentage are different. Main-listing contextual ancestors and expanded
Task descendants outside the owning-location population do not enlarge that selector's scope.
Hierarchy scope includes every node even when filtering or collapsing conceals it.

No state-filter predicate may restrict discovery. Main listing and form selectors share the
same observed direct-location sets, with the scope differences in the table above. Merely
revealing options or selecting an unsaved draft state does not mark that state used in a List.

## Availability is prepared before controls open

Keep a small availability index in LiveView state alongside navigation data, keyed by Project
and owning List (including the direct-Project location). Store observed additional states for
each direct location, plus prepared subtree and whole-Project unions using current List ancestry.
Do not duplicate default states in every entry or maintain per-state reference counts. Build
the index for a Project when it is loaded; do not bootstrap every Project in the workspace.

| Trigger | Availability work |
|---|---|
| Initial Project load | One grouped/distinct `(list_id, status)` snapshot of current additional-state Tasks, unfiltered by status preferences; prepare all direct and aggregate entries for that Project |
| Relevant external Task event | On create, move, or a change to status/owning List, read the authoritative Task as needed and union its additional state into its current direct-location entry; update affected aggregate entries |
| Successful local create/edit/move | Apply the same observation using the returned persisted Task; do not wait for a self-excluded PubSub event |
| Task leaves an additional state, moves away, or is removed | Retain the source location's observed state; no last-instance query or decrement |
| List create/reparent/remove reconciliation | Update valid location keys and rebuild aggregate unions from retained direct entries and current List ancestry; never discover Task states by scanning the listing |
| Navigate between Lists, change Include child Lists, change form ownership | Read the appropriate prepared entry; no discovery query or subtree aggregation |
| Open/close a filter or state picker | Read already prepared options; no availability query, Task scan or aggregate rebuild |
| Hierarchy load/refresh | Derive its state set once from the complete loaded tree before rendering its filter; opening that filter performs no traversal |
| Return to a Project or reconnect | Refresh the current database snapshot to cover periods without a subscription; merge with observations retained by the same mounted page and rebuild aggregates |
| Full reload/new LiveView mount | Discard observed availability and bootstrap from current Tasks |

Keep observed direct entries for previously visited Projects within that mounted page. Switching
Projects must refresh the selected Project on return because only the active Project's Task
events are subscribed. Drop invalid List/Project keys when reconciling authoritative topology.
Reparenting a List changes subtree unions according to its current ancestry; it does not record
permanent history in its former ancestors. Closing a task form resets its explicit reveal flag,
but does not reset the page's observed availability. A transient connection loss on the same
LiveView preserves observations; a replacement process/remount starts fresh.

Title, description, priority, due-date, comment, and blocking-link-only updates do not trigger
availability discovery. Parent-Task changes can refresh hierarchy availability through the
existing tree reload but do not affect List ownership. Filter checkbox changes do not rebuild
the availability index. Any existing listing/count refreshes remain separate from availability
maintenance; opening a form must not introduce extra availability work.

Current notifications are invalidations, so the minimal external-event path may read one Task
through the Tasks context; do not requery every List or the whole Project for each event. Reuse
an authoritative Task already fetched during reconciliation when practical. Observations use
persisted snapshots actually seen by the page; the index is not an audit log and does not
reconstruct intermediate states overwritten before reconciliation reads them. Invalid or foreign
Project events must not change the index.

This eager session index avoids selector latency and last-instance bookkeeping. A database usage
history would survive reload but is outside the accepted lifetime. Requerying upon selector open
adds latency; refreshing exact per-state counts after every update adds unnecessary work when
state availability is intentionally retained.

## Filter preferences survive absence and navigation

The main listing continues to store selections across Lists and Projects in the existing browser
preference key. Availability does not alter that full supported-state inclusion list. Rendering
uses the contextual subset; querying uses remembered selection. An absent state has no matching
Tasks in that population, so retaining its preference has no filtering effect there.

When a checkbox form submits, update only the states offered by that selector. Preserve all
other supported-state preferences. Validate against the server's current offered states rather
than accepting arbitrary client-provided availability. An empty offered selection unchecks the
offered options while retaining absent options. Explicit full URL snapshots and hydration remain
full replacements, including an explicitly empty selection; unknown state values follow existing
normalization. Do not let contextual option lists become the browser's allowed-state vocabulary.

Not Planned starts unchecked with fresh defaults. Existing stored arrays and URL snapshots
that omit `not_planned` also leave it unchecked. No preference reset or storage migration is
required. A stored explicit inclusion of `not_planned` is retained even when it is absent locally.

Hierarchy filtering remains independent and modal-local. Start with all supported states checked,
render only default and represented additional states, and preserve absent preferences when
navigating to another tree inside the same dialog. Closing the dialog resets the selection.
Required ancestry may remain visible despite unchecked states, as in the existing contract.

| Scenario | Expected result |
|---|---|
| Check Not Planned in List A; navigate to B where it has not been observed | Option is hidden in B; remembered inclusion survives |
| Change Pending in B; return to A | Not Planned is still checked |
| Uncheck Not Planned while its Tasks exist | Option stays visible and unchecked |
| Last Not Planned Task in A becomes Pending | Not Planned remains offered in A until a full reload, including after closing/reopening forms |
| Reload with no remaining Not Planned Tasks in A | A's option is hidden again; browser-stored filter preference survives |
| All Tasks are Not Planned with fresh main defaults | Filtered-empty listing; Not Planned remains offered unchecked |
| Open a hierarchy containing Not Planned | Option appears checked; matching nodes are eligible normally |
| Will Not Do is the only blocker | Done transition requires the existing unresolved-blocker confirmation |

## Forms can reveal all states without changing the selected value

Create/edit status controls normally offer default states, observed additional states, and
the current selected value. This last exception preserves an existing Task or draft when its
ownership changes or the last other Task using that state disappears.

Provide a small keyboard-accessible “Show additional states” button beside/below the status
control when unrevealed options remain. It reveals all supported additional states, changes no
field value, and is not submitted as a state option. Remove the button once everything is offered.
It is a one-way action for that form's lifetime, without browser storage or a reverse toggle.

Keep reveal state across validation errors, autosaves, ownership changes and ordinary live
refreshes of the same form. Reset on closing/reopening, starting a new creation form, or navigating
to a different Task's editing form inside the same detail dialog. Existing disabled recovery
controls remain disabled and retain the displayed value. Opening the picker must never mark a
Task changed. A current or recoverable draft value must not be lost through option filtering.

## Selector order and presentation

Use the ordered sequence in the state-definition table for both filters and create/edit options.
Insert newly available options at their logical positions; do not append them, group them, or
add separators. Give additional options a subtly duller background while preserving contrast,
hover, focus, checked and selected affordances. Apply this to additional options in all affected
selectors. State badges retain their ordinary state-specific presentation.

Use the existing enhanced picker and native form bridge. Additional metadata must not break
priority or other picker consumers. Native fallback must offer the same values and order; exact
native option backgrounds may be platform-controlled. Verify the enhanced option distinction
visually rather than asserting CSS class strings.

Selectors and Task status sorting share one canonical order:
**Icebox, Pending, In Progress, In Review, Done, Not Planned, Will Not Do**.
Ascending status sort follows that sequence; descending reverses it. Apply the same ranking in
both listing SQL and hierarchy sorting, preserving existing tie-breakers and selected directions.
This replaces the existing separate ranking described in the baseline. The earlier draft's
proposal to preserve that ranking was rejected: consistent ordering across surfaces takes precedence.

## Descriptions appear in delayed option tooltips

Show the shared description for every state option, default or additional, in the enhanced
create/edit state picker and both status filters. Use a 450 ms delay on pointer hover or keyboard
focus/active-option changes. This matches the existing hover delay for consistent pacing;
the delay is not configurable per state. In the enhanced picker, follow its keyboard-active
option without moving DOM focus or changing the selected value. For filters, follow the focused
checkbox as well as hover over its option row.

Show the same state description on the main table's state badges after 450 ms of pointer hover,
including contextual and expanded rows. Apply to every state. Badges remain noninteractive labels
without extra keyboard tab stops; expose their descriptions to assistive technology independently
of visual hover. Preserve clicking/tapping the badge area to open its Task and the existing row
hover treatment. Account for the current pointer-event passthrough rather than simply attaching
tooltip attributes to an element that cannot receive hover events.

Moving to another option or badge restarts the delay and removes the previous tooltip. Cancel
pending and visible tooltips on selection, picker/filter close, scroll, navigation, disconnected
or removed options/badges, changed badge state, and form/dialog changes. Keep the tooltip visible
while the pointer remains over its trigger or tooltip; Escape dismisses it while retaining existing
picker/filter/dialog dismissal precedence. Tooltip handling must not consume Escape in place of
the control's existing action.

Render above the owning dialog/popover without clipping, constrain to the viewport, and never
cover the option being described when space allows. Tooltips contain plain descriptive text,
with no focusable controls. Descriptions must also be available to assistive technology through
the option's accessible description independently of visual timing; the current globally
`aria-hidden` tooltip alone is insufficient. Touch users can select immediately; do not introduce
a long-press requirement or delay selection. Native select tooltip presentation is platform-owned;
custom timing applies to the enhanced picker and the application-owned checkbox filters.

Reuse/extend the existing tooltip infrastructure with opt-in description/active-option support,
including a top-layer-capable presentation path. Preserve existing non-state tooltip behavior,
including immediate keyboard-focus display. Description tooltips cover state options and table
state badges; other badge locations, priority badges and other picker types are outside this scope.

## Closed work can still block other Tasks

Not Planned is excluded from open counts. Preserve the established count contract: when any
closed state is explicitly selected, its Tasks participate in visible and eligible counts.
Changing Will Not Do's blocker semantics does not change its closed classification.

Only Done resolves an existing blocking relationship. Not Planned and Will Not Do remain
unresolved even though they are closed. No link is removed or changed by assigning a state.
An operator may revisit the blocking Task or explicitly remove the relationship when it no
longer represents a dependency.

Preserve the existing warning boundary: unresolved blockers warn on a transition to Done;
they do not newly prohibit entering In Progress or other states. Preserve explicit ID-based
confirmation, force confirmation, concurrent rechecks, and human-controlled lifecycle behavior.
API failure remains HTTP 409, `error.code = "unresolved_blockers"`, with current blocker summaries
and message “Review unresolved direct blockers before marking Done”. Not Planned and Will Not Do
blockers now appear in those summaries. No new error type or confirmation flag is introduced.

## Ownership and implementation boundaries

Use a small compile-time domain state catalogue under `lib/taskman/tasks/`, consumed by the
Task schema, preference defaults, counting, validation and blocker resolution. It owns identifiers,
labels, descriptions, color tokens, order and semantic flags. Keep concrete style mappings in the
web layer, keyed by color token rather than assigning colors independently by state. Avoid
database-backed state definitions, per-Project configuration, user-created states and a generic
state-machine framework.

Approaches considered:

- **Remember preferences separately from contextual options (selected):** retains navigation
  intent and compact UI, but requires partial checkbox updates. Resetting unavailable preferences
  loses choices; retaining every selected option visibly accumulates clutter.
- **Central domain definitions (selected):** current consumers need independent visibility,
  count and blocker properties. Extending scattered hardcoded lists is initially smaller but
  risks reintroducing the incorrect assumption that every closed state resolves blockers.

Expected boundaries:

- Tasks context/persistence owns project-scoped represented-state discovery and List scope
  validation. Provide grouped bootstrap discovery and bounded single-Task observation reads
  through existing context boundaries; do not load entire Projects solely to discover a handful
  of state values. Missing or cross-Project List scopes preserve existing `{:error, :not_found}`
  handling, never broaden to the whole Project.
- Listing state/preferences, creation/editing state, hierarchy projection and reconciliation own
  availability and lifetime. A focused page-local index owns direct observations and prepared
  rollups, shared by navigation/listing/forms; hierarchy availability is prepared on tree load.
- `components/tasks/{table,form,status}.ex`, `select_picker.ex`, `assets/js/select_picker.js`
  and relevant CSS own option rendering, metadata and picker synchronization. `assets/js/tooltips.js`
  owns shared tooltip scheduling/presentation extensions; opt-in state handling must preserve
  other tooltip consumers. No web-layer queries.
- `Tasks.Counts`, mutations/blocking checks and SQL/hierarchy sort expressions consume the new
  definitions/rank without introducing unrelated query or workflow refactors.
- Generate a new Ecto migration to extend `tasks_status_check`; keep existing rows unchanged.
  Rollback must refuse while `not_planned` rows exist rather than deleting or silently reclassifying
  them. Older application code cannot read the new enum value: revert such rows explicitly before
  rolling back application/migration. No deployment is part of this work.
- Existing API create/update/list and CLI `--status` accept `not_planned`, even when UI options
  hide it. Update registry validation, help, Bash/Fish completions, bundled CLI skill and focused
  tests. Keep existing JSON shapes; successful create/update remain 201/200. Invalid values retain
  existing validation/error contracts. No state-metadata endpoint is required for this UI feature.

## Verification and next steps

Design evidence is source inspection, not executed feature tests. No visual prototype or browser
acceptance has been performed. Implementation planning must cover:

1. Metadata, schema/database acceptance, count classification, and only-Done blocker resolution,
   including Will Not Do regression coverage and existing confirmation behavior. Verify selectors
   and ascending/descending listing and hierarchy status sorts against the same canonical sequence.
2. Scoped discovery for direct Project, direct List, descendant Lists and whole Project, including
   cross-Project isolation, empty populations and state filters that exclude all matching Tasks.
   Verify monotonic direct observations, local/external updates, topology rollups, Project return,
   reconnect/remount behavior and reload reset. Verify selector opening adds no availability
   queries or tree traversal, and unrelated events do not trigger discovery.
3. Main/hierarchy checkbox preference retention across absent states, navigation, live changes,
   empty submissions, explicit URL snapshots and existing browser storage.
4. Form auto-availability, selected draft retention, one-way reveal, validation/autosave lifetime,
   Task switching and close/reopen reset; independent hierarchy lifetime.
5. API/CLI create/update/list parity for `not_planned`, help/completion/skill consistency and the
   rebuilt executable gate from the development guide.
6. Focused browser checks for stored preferences across reload, enhanced picker option updates,
   keyboard reveal/selection/dismissal and muted option readability. Verify delayed description
   display for hover and keyboard-active options, cancellation and stale-tooltip removal, actual
   visibility above dialogs/popovers, viewport placement, accessible descriptions and preserved
   ordinary tooltip behavior. Cover table state-badge hover delay, correct descriptions after live
   updates, and preserved click/tap navigation without added tab stops. Preserve existing popup
   placement, native bridge, focus and recovery behavior. Run `mix precommit` after implementation.

At implementation completion, update the domain, MVP, relationships and affected historical
specification rules to identify this specification's replacements; do not publish contradictory
current contracts. Audit changed files for privacy and leaked planning terminology.

Next: obtain full design approval, then write and review an implementation plan. Plan approval is
the clean-session boundary unless the operator explicitly chooses to implement in that session.

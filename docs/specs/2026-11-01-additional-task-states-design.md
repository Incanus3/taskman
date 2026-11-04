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
| D3 | Accepted | One-way reveal action inside the open create/edit state picker; retain its selected state | Action rows need distinct activation semantics and the existing form lifetime | [Forms](#forms-can-reveal-all-states-without-changing-the-selected-value) |
| D4 | Accepted | One canonical order for selectors and Task status sorting; additional options have a muted background | Existing SQL/hierarchy sort ranks change; muted backgrounds must preserve focus/selection readability | [Presentation](#selector-order-and-presentation) |
| D5 | Accepted | Not Planned is additional, closed, initially excluded only in the main listing | Closed work remains available through explicit filtering | [Definitions](#state-definitions) |
| D6 | Accepted | Only Done resolves blockers; Will Not Do and Not Planned remain unresolved | Previously non-warning Done transitions may now require confirmation | [Blocking](#closed-work-can-still-block-other-tasks) |
| D7 | Accepted | Centralize state definitions including a color token; translate tokens into styles in the web layer | Existing hardcoded state lists, colors and semantic checks must be reconciled | [Ownership](#ownership-and-implementation-boundaries) |
| D8 | Accepted | Show state descriptions in delayed tooltips on options and table state badges | Pickers require active-option/top-layer support; badge hover must preserve row navigation | [Descriptions](#descriptions-appear-in-delayed-option-tooltips) |
| D9 | Accepted | Precompute page-local availability; update from event state/location snapshots without per-client Task reads | Events gain a compact payload; retained states may have zero current Tasks | [Updates](#availability-is-prepared-before-controls-open) |
| D10 | Accepted | Share one Project location model between navigation and selectors; Project root is a location entry | Retains List metadata currently loaded for navigation; direct/subtree sets are derived caches | [Shared state](#availability-is-prepared-before-controls-open) |
| D11 | Accepted | Retain ephemeral PubSub delivery and current-state resnapshots | Missed events are not replayed; transient historical state use may be lost | [Delivery limits](#accepted-delivery-and-publication-limitations) |
| D12 | Accepted | Retain the existing enclosing-transaction publication limitation | An outer rollback can leave an already-delivered availability observation for an uncommitted change | [Publication limits](#accepted-delivery-and-publication-limitations) |
| D13 | Accepted | Native select fallback offers all real states directly, without a reveal pseudo-option | Contextual decluttering applies to the enhanced picker; native fallback may show unused additional states | [Fallback](#presentation-and-fallback-constraints) |

D1–D13 reflect explicit operator decisions. The complete written specification still requires design
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
- `Workspace.refresh/1` currently reloads Lists and emits `NavigationNode` rows into a LiveView
  stream; `Workspace.State` does not retain a reusable complete List index. The shared location
  model below introduces that owner rather than duplicating an existing retained collection.

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

Keep one page-local Project location model shared by navigation and selectors. Each List entry
owns its List record (including its parent relationship), observed direct additional-state set
and prepared subtree additional-state set. Use one canonical collection of List metadata/topology,
not a separate availability-only List tree. Selection paths and streamed navigation rows are
derived projections of that owner, not independently refreshed collections. Exact module/type
names are implementation choices.

Represent the Project root as another location entry. Its direct set covers Tasks without an
owning List; its subtree set covers the whole Project, including all Lists. Do not store a separate
third whole-Project set. A subtree set is the union of that entry's direct set and every child
location's subtree set: each additional state appears at most once in the result. Compute these
summaries at bootstrap and on relevant updates, never by traversing children when opening controls.

For example, if only child List B has observed Not Planned:

| Location | Direct additional states | Subtree additional states |
|---|---|---|
| Project root | None | Not Planned |
| List A, a root List | None | Not Planned |
| List B, child of A | Not Planned | Not Planned |

Repeated state identifiers in direct/subtree summaries are deliberate caching for immediate
lookups; List records and topology have one owner. Do not duplicate default states in entries or
maintain per-state reference counts. Populate the model when a Project is loaded; do not bootstrap
every Project in the workspace. Navigation rendering reuses the retained List records rather than
loading another collection solely to render the same topology. Preserve authoritative refreshes
on relevant workspace notifications and subscription-gap resnapshots.

Existing exact Task counts and current direct-Task presence have independent refresh semantics.
They may remain separately derived values; do not infer either from observed additional states,
which can remain after all matching Tasks leave. This shared owner does not require the broader
count-delta or notification optimizations recorded in the separate research.

| Trigger | Availability work |
|---|---|
| Initial Project load | One grouped/distinct `(list_id, status)` snapshot of current additional-state Tasks, unfiltered by status preferences; prepare all direct and aggregate entries for that Project |
| Relevant external Task event | On create, move, or a change to status/owning List, merge the event's persisted state/location snapshot into its direct-location entry and update affected aggregates; no Task read |
| Successful local create/edit/move | Apply the same observation using the returned persisted Task; do not wait for a self-excluded PubSub event |
| Task leaves an additional state, moves away, or is removed | Retain the source location's observed state; no last-instance query or decrement |
| List create/reparent/remove reconciliation | Update valid location keys and rebuild aggregate unions from retained direct entries and current List ancestry; never discover Task states by scanning the listing |
| Navigate between Lists, change Include child Lists, change form ownership | Read the appropriate prepared entry; no discovery query or subtree aggregation |
| Open/close a filter or state picker | Read already prepared options; no availability query, Task scan or aggregate rebuild |
| Hierarchy load/refresh | Derive its state set once from the complete loaded tree before rendering its filter; opening that filter performs no traversal |
| Return to a Project or re-establish the connection | Conservatively refresh current database state whether or not any events were missed; merge surviving page observations and rebuild aggregates |
| Full reload/new LiveView mount | Discard observed availability and bootstrap from current Tasks |

### Page lifetime and topology

| Boundary | What survives or changes |
|---|---|
| Switch Projects | Retain previously visited Projects' direct observations in the mounted page. Refresh the selected Project on return because only the active Project's Task events are subscribed. |
| Reconcile topology | Drop invalid List/Project keys. Reparenting changes subtree unions using current ancestry; former ancestors do not retain permanent usage history. |
| Close a Task form | Reset its explicit reveal flag; retain the page's observed availability. |
| Lose and restore the connection | If the same LiveView survives, preserve observations and merge a fresh snapshot; a replacement process/remount bootstraps from current Tasks. |

### Changes that do not discover availability

- Title, description, priority, due-date, comment and blocking-link-only changes do not trigger
  availability discovery.
- Parent-Task changes can refresh hierarchy availability through the existing tree reload, but
  do not affect List ownership.
- Filter checkbox changes do not rebuild the availability index.

Existing listing/count refreshes remain independent of availability maintenance. Opening a form
must not introduce extra availability work.

### Task events carry the persisted state and location

Extend every Task create/update/move event with the final `status` and `list_id` from the persisted
Task already passed to `ChangeNotifications.publish_task/3`, including ordinary updates.
Consumers still use the trigger rules above to decide whether availability work is relevant.

| Payload | Contract |
|---|---|
| `status` | Supported persisted Task state |
| `list_id` | Positive owning List ID; `list_id: nil` explicitly means direct Project ownership |
| Existing metadata | Retain `project_id`, `entity_id`, `lock_version` and changed `fields` |
| Other event types | Do not carry Task state/location data |
| Excluded data | No drafts, full Task structs or extra descriptive data |

The Task context publishes after successful persistence, as before. Local callers observe the
same persisted result directly. Update all in-repository producers and event fixtures together;
no public event schema or legacy producer compatibility layer is introduced.

### Consumers merge observations without Task reads

1. Structurally validate the supported state, nil/positive owning List ID and valid matching Project.
2. Merge valid observations using set unions and prepared rollup updates.
3. For a List not yet present in navigation, retain the observation by ID until topology
   reconciliation supplies its record. Do not query to validate each notification's ownership.
4. Invalid or missing snapshots do not trigger a fallback Task read.

The snapshot records the successful Task mutation at publication time, subject to the accepted
outer-transaction limitation below. Duplicate or out-of-order events can add a previously used state
and never remove one. No per-Task version cache or old-state/location payload is needed for
availability; existing revision metadata remains available to other consumers.

### Event guarantees and limits

| Concern | Boundary |
|---|---|
| Current Task presentation | Listing, counts, detail and hierarchy retain their authoritative invalidation/refetch paths; these two fields do not replace those reads. |
| Database savings | Eliminate additional availability reads per client, not every read in existing event handling. |
| Existing event contract | Supersede the live-updates design's metadata-only restriction only for Task availability observations. |
| Delivery and publication | Retain the explicitly accepted limitations below. |
| Recovery | Refresh at known potential gaps: Project return, connection re-establishment or remount. No individual missed-event detector; the cache is not an audit log. |
| Scope | No outbox or broader event-driven view rewrite. |

#### Accepted delivery and publication limitations

| Limitation | Meaning | Effect on availability |
|---|---|---|
| Ephemeral delivery | PubSub does not store or replay messages for disconnected subscribers. Reconnection reloads current database state. | States used only transiently while disconnected cannot be reconstructed. |
| Enclosing transaction | The Task context publishes after its own operation succeeds. A caller's outer transaction may still be pending and can subsequently roll back. | An already-delivered snapshot can add an additional state even though that outer transaction never committed the change; the observation can remain until reload/remount. |

Recovery is triggered by those lifecycle boundaries, not by evidence that a particular event was
lost. There are no sequence-gap checks, delivery acknowledgements or replay mechanism. If a
publication fails while a client remains connected, the client cannot detect that loss; current
state is repaired at its next resnapshot boundary. Transient historical state use remains
unrecoverable after a gap/remount.

Current supported UI/API Task mutation paths call the contexts at the top level and avoid the
enclosing-transaction case. Future callers composing these mutations inside a larger transaction
must address publication timing before relying on outer-transaction atomicity. This feature does
not add an after-commit mechanism or prohibit nesting; both limitations are accepted as documented.

The eager session model avoids selector latency and last-instance bookkeeping. Persistent database
usage history would exceed the accepted lifetime. Selector-time queries add latency; exact per-state
counts add unnecessary update work when observed availability is intentionally retained.

## Filter preferences survive absence and navigation

### Main listing remembers the complete selection

Keep the existing browser preference key across Lists and Projects. Render contextual options,
but query using the full remembered supported-state inclusion list. An absent state has no matching
Tasks in that population, so retaining its preference has no filtering effect there.

| Input | How it changes remembered selection |
|---|---|
| Checkbox submission | Update only currently offered states; preserve preferences for other supported states. Validate availability against the server's offered set. |
| Empty offered selection | Uncheck offered states while retaining absent states. |
| Explicit URL snapshot or hydration | Replace the full selection, including an explicitly empty selection; retain existing unknown-value normalization. |
| Browser allowed-state vocabulary | Continue to include all supported states; never reduce it to contextual options. |

Not Planned starts unchecked with fresh defaults. Existing stored arrays or URL snapshots that omit
`not_planned` also leave it unchecked; no reset or storage migration is required. Explicit stored
inclusion survives local absence.

### Hierarchy selection lasts for one dialog

- Start with **all supported states checked**; offer default and represented additional states.
- Keep selection independent of the main listing and preserve absent preferences when navigating
  to another tree inside the same dialog.
- Closing the dialog resets selection. Required ancestry may remain visible despite unchecked
  states, following the existing hierarchy contract.

### Navigation and empty-result examples

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

### Offer the selected value and a one-way reveal

Enhanced create/edit controls offer default states, observed additional states and the
**current selected value**. Preserve that value for persisted Tasks and drafts even when ownership
changes or the last other Task using that state disappears.

| Reveal action | Contract |
|---|---|
| Label and placement | “Show additional states” as a visually distinctive final action row inside the open enhanced picker popup; no beside/below button in the enhanced control |
| Visibility | Offer only when at least one supported additional state is still hidden after including observed states and the current selected value; omit when all additional states are already offered |
| Activation | Remove the action row and insert all hidden additional states at their canonical positions; keep the popup open and the selected value unchanged |
| Form semantics | This is an action, not a Task state. Never assign or submit it through the native select, validate a fake state, trigger an autosave or mark the form changed |
| Direction and storage | One-way for the form's lifetime; no reverse toggle or browser storage |

The action row is outside the canonical ordering of real states. Its styling and accessible
semantics must distinguish revealing options from selecting a state. Keyboard users can reach
and activate it with Enter/Space; do not describe it as the selected Task state or attach a state
description/color to it. Exact DOM structure may separate the semantic action from the listbox
while keeping it visually within the same options popup.

After activation, keep a valid keyboard focus/active-option target within the open picker and
make the revealed options reachable. The native input retains its original state until a real
state is selected. Repeated activation is harmless and never produces a form value change.

The native fallback offers every real supported state in canonical order, with no pseudo-option
or separate reveal control. Its form bridge contains only valid Task state values. The enhanced
picker receives its contextual option subset and reveal action separately from that full native
vocabulary; rendering hidden native options does not mark them observed in a List.

### Reveal lifetime and value protection

| Event | Reveal/value behavior |
|---|---|
| Validation error, autosave, ownership change, ordinary live refresh of the same form | Preserve reveal state |
| Close/reopen, start another creation form, navigate to a different Task's editing form in the same detail dialog | Reset reveal state |
| Recovery disables controls | Keep them disabled and retain the displayed value |
| Open the picker | Do not mark the Task changed |
| Filter available options | Never discard a current or recoverable draft value |

## Selector order and presentation

### One canonical order serves selectors and sorting

**Icebox, Pending, In Progress, In Review, Done, Not Planned, Will Not Do**.

- Filters and create/edit options use this sequence, matching the state-definition table.
- Insert newly available options at their logical position; do not append, group or separate them.
- Ascending Task status sorting follows the sequence; descending reverses it. Listing SQL and
  hierarchy sorting share the ranking, preserving existing tie-breakers and selected directions.

This replaces the baseline's separate sort ranking. The earlier proposal to preserve that ranking
was rejected in favor of consistency across surfaces.

### Presentation and fallback constraints

| Surface | Requirement |
|---|---|
| Additional options in every affected selector | Subtly duller background with readable contrast and intact hover, focus, checked and selected affordances |
| State badges | Retain ordinary state-specific presentation |
| Enhanced picker | Reuse the existing picker and native form bridge; metadata must not break priority or other consumers |
| Native fallback | Offer all real supported states in canonical order, with no reveal pseudo-option; exact option backgrounds may be platform-controlled |
| Visual verification | Inspect the enhanced distinction visually instead of asserting CSS class strings |

## Descriptions appear in delayed option tooltips

### Surfaces, triggers and delay

Use shared descriptions for every state, default or additional. The **450 ms** delay matches the
existing hover delay and is not configurable per state.

| Surface | Trigger | Interaction constraints |
|---|---|---|
| Enhanced create/edit state picker | Hover or keyboard-active option after 450 ms | Follow the active option without moving DOM focus or changing selection |
| Main and hierarchy filters | Hover over the row or keyboard focus on its checkbox after 450 ms | Describe the corresponding state |
| Main-table state badges, including contextual/expanded rows | Pointer hover after 450 ms | Keep badges noninteractive with no extra tab stops; preserve click/tap Task opening and row hover |

Badge hover must work with the current pointer-event passthrough; attaching tooltip attributes to
an element that cannot receive hover events is insufficient. Expose badge descriptions to assistive
technology independently of hover.

### Tooltip lifetime and dismissal

- Moving to another option/badge removes the old tooltip and restarts the delay.
- Cancel pending and visible tooltips on selection, picker/filter close, scroll, navigation,
  disconnected/removed options or badges, changed badge state, and form/dialog changes.
- Keep the tooltip visible while the pointer remains over its trigger or the tooltip.
- Escape dismisses the tooltip while preserving existing picker/filter/dialog dismissal precedence;
  tooltip handling must not consume Escape in place of the control's existing action.

### Placement, accessibility and integration

| Concern | Contract |
|---|---|
| Placement | Render above the owning dialog/popover without clipping, constrain to the viewport, and avoid covering the described option when space allows |
| Content | Plain descriptive text with no focusable controls |
| Accessible description | Available through the option's accessible description independently of visual timing; the current globally `aria-hidden` tooltip alone is insufficient |
| Touch | Immediate selection; no long-press requirement or selection delay |
| Native select | Tooltip presentation is platform-owned; custom timing applies to the enhanced picker and application-owned checkbox filters |
| Shared infrastructure | Reuse/extend existing tooltips with opt-in description/active-option support and a top-layer-capable presentation path |
| Other consumers | Preserve non-state tooltip behavior, including immediate keyboard-focus display |
| Scope | State options and table state badges only; other badge locations, priority badges and other picker types remain outside scope |

## Closed work can still block other Tasks

### Counting and blocker resolution are independent

- Not Planned is excluded from open counts. Preserve the existing rule that explicitly selected
  closed states participate in visible and eligible counts.
- Will Not Do retains its closed classification despite the changed blocker semantics.
- **Only Done resolves a blocking relationship.** Not Planned and Will Not Do remain unresolved.
- Assigning a state never removes or changes a link. Revisit the blocking Task or explicitly remove
  the relationship when it no longer represents a dependency.

### Preserve the existing Done-confirmation boundary

Unresolved blockers warn on transition to Done; they do not newly prohibit In Progress or other
states. Keep explicit ID-based confirmation, force confirmation, concurrent rechecks and
human-controlled lifecycle behavior.

| API outcome | Contract |
|---|---|
| Status/code | HTTP 409; `error.code = "unresolved_blockers"` |
| Message | “Review unresolved direct blockers before marking Done” |
| Evidence | Current blocker summaries now include Not Planned and Will Not Do blockers |
| Compatibility | No new error type or confirmation flag |

## Ownership and implementation boundaries

### Definitions and selected approaches

Use a small compile-time state catalogue under `lib/taskman/tasks/` for identifiers, labels,
descriptions, color tokens, order and semantic flags. The Task schema, preference defaults,
counting, validation and blocker resolution consume it. Concrete styles remain in the web layer,
keyed by color token. Database-backed definitions, per-Project configuration, user-created states
and a generic state-machine framework are outside scope.

| Selected approach | Benefit and trade-off | Alternatives considered |
|---|---|---|
| Remember preferences independently of contextual options | Retains navigation intent and compact UI; requires partial checkbox updates | Resetting unavailable preferences loses choices; displaying every selected option accumulates clutter |
| Central domain definitions | Keeps visibility, counting and blocker semantics independent | Scattered hardcoded lists are initially smaller but risk treating all closed states as blocker-resolving |

### Responsibility map

| Owner | Responsibility |
|---|---|
| Tasks context/persistence | Grouped bootstrap discovery and List-scope validation; avoid loading full Projects merely to discover states. Missing/cross-Project List scopes keep `{:error, :not_found}` and never broaden to the Project. |
| `ChangeNotifications` and Event struct | Compact persisted Task snapshot supplied by the Tasks context; event observations require no Task reads. |
| Shared page-local Project location model | Canonical List records/topology, direct observations and subtree summaries; Project-root subtree is the whole-Project summary. Navigation streams, listing and forms consume it. |
| Listing/preferences, creation/editing and reconciliation | Workflow selection, reveal/availability lifetime and coordinated model updates. |
| Hierarchy projection | Prepare its separate availability on tree load. |
| `components/tasks/{table,form,status}.ex`, `select_picker.ex`, `assets/js/select_picker.js` and relevant CSS | State/action row rendering, metadata and picker synchronization; reveal activation bypasses ordinary value-selection behavior. No web-layer queries. |
| `assets/js/tooltips.js` | Shared tooltip scheduling/presentation extensions that preserve other consumers. |
| `Tasks.Counts`, mutations/blocking checks and SQL/hierarchy sort expressions | Consume the definitions/rank without unrelated query or workflow refactors. |

### Persistence and rollback

- Generate a new Ecto migration extending `tasks_status_check`; preserve existing rows.
- Rollback refuses while `not_planned` rows exist; never delete or silently reclassify them.
- Older application code cannot read the new enum value. Explicitly revert those rows before
  rolling back application/migration. Deployment is outside this work.

### API, CLI and documentation parity

- Existing API create/update/list and CLI `--status` accept `not_planned`, including when UI options
  hide it. Successful create/update remain HTTP 201/200 with existing JSON shapes.
- Preserve invalid-value validation/error contracts. No state-metadata endpoint is required.
- Update CLI registry validation, help, Bash/Fish completions, bundled CLI skill and focused tests.

## Verification and next steps

### Evidence available now

Source inspection establishes the design context. Feature tests, visual prototypes and browser
acceptance have not been performed.

### Required implementation evidence

| Area | Coverage |
|---|---|
| State definitions and persistence | Metadata, schema/database acceptance, count classification, and one canonical sequence for selectors plus ascending/descending listing/hierarchy status sorts |
| Blockers | Only-Done resolution, Will Not Do regression coverage and existing confirmation behavior |
| Scope isolation | Direct Project, direct List, descendant Lists and whole Project; cross-Project isolation, empty populations and filters excluding every matching Task |
| Availability updates | Monotonic direct observations, local/external updates, topology rollups, Project return, reconnect/remount and reload reset; unrelated events do not discover states |
| Event contract | Publisher values after create/update/move, nil owning List, duplicate/out-of-order delivery, not-yet-loaded Lists and malformed/foreign snapshots |
| No added interactive reads | Selector opening performs no availability queries or tree traversal; external observation performs no Task read |
| Shared location model | Navigation projections, direct/subtree selection and Project-root whole-Project summary; preserve exact counts/current direct-Task presence |
| Preferences | Main/hierarchy independence, absent-state retention, navigation/live changes, empty submissions, explicit URL snapshots and existing browser storage |
| Forms | Automatic availability, selected draft retention, conditional reveal-action visibility, validation/autosave lifetime, Task switching, close/reopen reset, independent hierarchy lifetime and all-real-state native fallback |
| API/CLI | Create/update/list parity, help/completion/skill consistency and the development guide's rebuilt-executable gate |
| Picker reveal (browser) | Click/keyboard activation removes the action and inserts states in order; popup stays open, focus remains valid, selected/native value stays unchanged and autosave stays idle; action appears only while states remain hidden |
| Browser behavior | Stored preferences across reload; enhanced option updates; selection/dismissal; muted option readability; delayed hover/keyboard-active descriptions; cancellation/stale removal; visibility above dialogs/popovers; viewport placement and accessible descriptions |
| Browser regressions | Ordinary tooltip behavior, badge hover delay, correct descriptions after live updates, click/tap navigation without extra tab stops, popup placement, native bridge, focus and recovery |

Run `mix precommit` after implementation.

### Documentation completion and next gate

At implementation completion:

- Update domain, MVP, relationships and affected historical specification rules to identify this
  specification's replacements; keep current contracts consistent.
- Include the bounded Task snapshot extension in the live-updates contract.
- Audit changed files for privacy and leaked planning terminology.

Next: obtain full design approval, then write and review an implementation plan. Plan approval is
the clean-session boundary unless the operator explicitly chooses to implement in that session.

# Additional Task states and Not Planned

**Status:** Behavioral decisions accepted in discussion; full design awaiting approval.
**Date:** 2026-11-01

Keep ordinary Task selectors compact as the supported state vocabulary grows. Introduce
**Not Planned** for work currently considered unnecessary but retained for reconsideration.
Additional states remain valid everywhere; contextual visibility only limits UI options.

## Decisions and approval scope

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D7 | Proposed | Centralize domain state definitions; keep Tailwind presentation in the web layer | Existing hardcoded state lists and semantic checks must be reconciled | [Ownership](#ownership-and-implementation-boundaries) |
| D8 | Proposed | Add descriptions as metadata; retain existing interaction and warning boundaries | Descriptions do not introduce a new tooltip or transition engine | [Definitions](#state-definitions) |
| D1 | Accepted | Default states plus contextually represented additional states in selectors | Options change with location and live updates | [Scopes](#option-availability-uses-the-unfiltered-task-population) |
| D2 | Accepted | Preserve filter preferences independently of available options | Checkbox submissions must preserve absent states | [Preferences](#filter-preferences-survive-absence-and-navigation) |
| D3 | Accepted | One-way reveal for the lifetime of a create/edit form; retain its selected state | Reveal state must reset at the correct form boundary | [Forms](#forms-can-reveal-all-states-without-changing-the-selected-value) |
| D4 | Accepted | One logical selector order; additional options have a muted background | Background distinction must remain readable alongside focus/selection | [Presentation](#selector-order-and-presentation) |
| D5 | Accepted | Not Planned is additional, closed, initially excluded only in the main listing | Closed work remains available through explicit filtering | [Definitions](#state-definitions) |
| D6 | Accepted | Only Done resolves blockers; Will Not Do and Not Planned remain unresolved | Previously non-warning Done transitions may now require confirmation | [Blocking](#closed-work-can-still-block-other-tasks) |

D1–D6 reflect explicit operator decisions. D7–D8 and the complete contract below require design
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

Descriptions belong in domain metadata and product/agent documentation. Exact visual exposure
is left to later UI work; this feature does not require tooltip interactions. Color is web
presentation metadata. Choose an accessible subdued color for Not Planned without altering
existing state colors; exact shade is an implementation choice.

## Option availability uses the unfiltered Task population

Every selector includes all default states. Add each additional state represented by at least
one persisted Task in the following population, regardless of checked states or collapsed branches.

| Selector | Population for represented additional states |
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

Recompute availability on relevant navigation, Include child Lists changes, form ownership
changes, and reconciled Task create/update/move/delete or List topology changes. Unchecking the
last represented state does not remove its option; changing or deleting the last Task in that
state may remove it. No state-filter predicate may restrict discovery of represented states.

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
| Check Not Planned in List A; navigate to B with no such Tasks | Option disappears in B; remembered inclusion survives |
| Change Pending in B; return to A | Not Planned is still checked |
| Uncheck Not Planned while its Tasks exist | Option stays visible and unchecked |
| All Tasks are Not Planned with fresh main defaults | Filtered-empty listing; Not Planned remains offered unchecked |
| Open a hierarchy containing Not Planned | Option appears checked; matching nodes are eligible normally |
| Will Not Do is the only blocker | Done transition requires the existing unresolved-blocker confirmation |

## Forms can reveal all states without changing the selected value

Create/edit status controls normally offer default states, represented additional states, and
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

Status sorting of Task rows retains the existing six states' relative ranks. As an implementation
choice, insert Not Planned immediately after Will Not Do in ascending status sort, so descending
sort starts Done, In Review, In Progress, Pending, Icebox, Not Planned, Will Not Do. Both listing
and hierarchy sorting use the same ranking. Selector order and existing work-priority sort are
different concerns; a broader sort redesign is outside this feature.

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

Prefer a small compile-time domain state catalogue under `lib/taskman/tasks/`, consumed by the
Task schema, preference defaults, counting, validation and blocker resolution. It owns identifiers,
labels, descriptions, order and semantic flags. Keep Tailwind classes in the existing web Status
module, keyed by the same identifiers. Avoid database-backed state definitions, per-Project
configuration, user-created states and a generic state-machine framework.

Approaches considered:

- **Remember preferences separately from contextual options (selected):** retains navigation
  intent and compact UI, but requires partial checkbox updates. Resetting unavailable preferences
  loses choices; retaining every selected option visibly accumulates clutter.
- **Central domain definitions (recommended):** current consumers need independent visibility,
  count and blocker properties. Extending scattered hardcoded lists is initially smaller but
  risks reintroducing the incorrect assumption that every closed state resolves blockers.

Expected boundaries:

- Tasks context/persistence owns project-scoped represented-state discovery and List scope
  validation. Prefer distinct state queries or reuse suitable already-loaded data; do not load
  entire Projects solely to discover a handful of state values. Missing or cross-Project List
  scopes preserve existing `{:error, :not_found}` handling, never broaden to the whole Project.
- Listing state/preferences, creation/editing state, hierarchy projection and reconciliation own
  availability and lifetime. Derive hierarchy availability from its existing complete tree.
- `components/tasks/{table,form,status}.ex`, `select_picker.ex`, `assets/js/select_picker.js`
  and relevant CSS own option rendering, metadata and picker synchronization. No web-layer queries.
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
   including Will Not Do regression coverage and existing confirmation behavior.
2. Scoped discovery for direct Project, direct List, descendant Lists and whole Project, including
   cross-Project isolation, empty populations and state filters that exclude all matching Tasks.
3. Main/hierarchy checkbox preference retention across absent states, navigation, live changes,
   empty submissions, explicit URL snapshots and existing browser storage.
4. Form auto-availability, selected draft retention, one-way reveal, validation/autosave lifetime,
   Task switching and close/reopen reset; independent hierarchy lifetime.
5. API/CLI create/update/list parity for `not_planned`, help/completion/skill consistency and the
   rebuilt executable gate from the development guide.
6. Focused browser checks for stored preferences across reload, enhanced picker option updates,
   keyboard reveal/selection/dismissal and muted option readability. Preserve existing popup
   placement, native bridge, focus and recovery behavior. Run `mix precommit` after implementation.

At implementation completion, update the domain, MVP, relationships and affected historical
specification rules to identify this specification's replacements; do not publish contradictory
current contracts. Audit changed files for privacy and leaked planning terminology.

Next: obtain full design approval, then write and review an implementation plan. Plan approval is
the clean-session boundary unless the operator explicitly chooses to implement in that session.

# Modal-local hierarchy status filter

**Status:** Implemented, verified and operator accepted.
**Date:** 2026-10-28

Reduce unrelated branches in Task detail without losing the selected Task's context or
matching descendants. Add a status dropdown at the right of the Task hierarchy heading,
matching the existing main-listing status filter's checkbox presentation.

The [Task detail layout contract](2026-10-28-task-detail-layout-design.md) retains authority
for responsive layout, selection, manual expansion, recovery and focus. The
[comment contract](2026-09-26-task-comments-design.md) retains departure/draft behavior.

## Decisions

| ID | Status | Choice | Downside or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Filter the loaded tree in LiveView presentation state | Requires a recursive visible-tree projection | [Projection](#projection-and-ownership) |
| D2 | Accepted | Keep matching Tasks, ancestors of matching Tasks, and the root-to-selected path | Some Tasks with unchecked statuses remain visible | [Visibility](#visibility-preserves-the-tree) |
| D3 | Accepted | Independent modal-local selection; all statuses initially checked | Closing the dialog discards filter choices | [Lifecycle](#choices-last-for-one-open-dialog) |

D1–D3 and the full written design are operator approved.

## Visibility preserves the tree

Keep a node when any of the following is true:

- Its status is checked.
- Any descendant, at any depth, has a checked status.
- It is on the inclusive path from the root to the currently selected Task.

Otherwise omit it and its subtree. Evaluate descendants in the full loaded tree, including
collapsed branches. Preserve parentage, sibling order, indentation, status labels and current-Task
highlight; do not promote descendants to another parent. Render disclosure controls and connector
endpoints from the remaining children. Preserve existing manual expansion choices: filtering does
not automatically expand optional branches, while the current path remains required-open.

| Example | Result |
|---|---|
| Done unchecked; unrelated Done leaf | Hidden |
| Done unchecked; Done parent has a Pending descendant; Pending checked | Parent and connecting ancestors retained; existing expansion choices apply |
| All statuses unchecked | Only the inclusive root-to-selected path remains |
| Navigate to another Task in the same open dialog | Recompute the protected path using the retained filter |
| A live status or parentage update changes a branch's eligibility | Recompute the projection without resetting filter or manual expansion choices |

## Choices last for one open dialog

All six statuses start checked. Changes apply immediately and independently of the main
listing. Preserve choices during Task navigation, including a different connected tree, and
while hiding/reopening the hierarchy panel within that dialog. Close resets the selection;
reopening starts with all statuses checked. No URL parameters, browser storage or persisted
preferences are added. Preserve existing recovery, editing and comment departure behavior.

Use the existing status filter's labels, checkboxes, outside-click dismissal and Escape behavior.
Give the hierarchy instance unique IDs and events so it cannot affect the listing filter.
Keep the control within the expanded hierarchy header, using a compact funnel button when
needed to keep the heading and collapse control reachable. Provide an accessible name and
expanded state. The popup must remain reachable within the clipped sidebar and short viewports;
Escape closes the popup before collapsing the narrow hierarchy overlay or dismissing the dialog.
The popup also closes when comment departure makes its owning modal inert, preserving the
confirmation focus and unsent draft.

## Projection and ownership

Source inspection establishes that `Tasks.Hierarchy` loads the connected tree
with statuses, and `ProjectLive.Tasks.Hierarchy` already tracks the selected path and manual
expansion. `Editing.State.clear/1` clears hierarchy state on dialog close. The listing filter
in `components/tasks/table.ex` currently hardcodes its IDs and event names.

The accepted approach is a pure recursive projection in the existing LiveView hierarchy
state boundary, retaining the complete source tree for navigation and refresh. Extend editing
events/state for modal-local filter selection. Reuse or minimally parameterize the existing
filter presentation; do not duplicate its behavior or bind hierarchy state to listing preferences.
`components/tasks/detail.ex` renders the projected tree and header control. CSS and client
changes are limited to popup placement, responsive sizing and Escape/focus integration as needed.

Client-only hiding would duplicate tree/path logic and leave server-rendered disclosures out of
sync. Database filtering would discard ancestry needed to evaluate the rules and add queries
to a purely presentational operation. Neither is appropriate here. View-local preferences were
also feasible, but the operator chose modal-local lifetime after comparing both scopes.

No domain, persistence, API or CLI operations change; this filter applies only to the dialog's
presentation. No migration, new dependency, listing-filter change, pagination or hierarchy
loading redesign is in scope. Preserve the sidebar expansion height fix in the baseline.

## Verification and approval

Pure tests cover recursive pruning, a matching grandchild below unchecked ancestors, collapsed
branches, all/none selected, protected-path changes and unchanged input structure. Focused
LiveView tests cover independent filters, immediate changes, navigation and refresh retention,
close/reopen reset, projected disclosures and preserved expansion choices. Browser verification
covers popup clipping/reachability, keyboard dismissal precedence and focus at desktop, narrow
and short viewports, plus the existing hierarchy expansion regression. Run `mix precommit`.

Implementation uses `Hierarchy.visible_root/1` for the projection, hierarchy-owned status
selection and editing events, and the shared `Table.status_filter/1` with instance-specific IDs
and events. The existing layout hook positions the native top-layer popup and closes it before
overlay/dialog dismissal or departure confirmation.

Verification passed: 82 focused server/component tests; `mix precommit` with 1,373 passed and
3 existing skips; 15 affected Task detail/listing browser tests; and a fresh 12-test Task detail
browser run after the departure-confirmation correction. Independent scoped review is clean and
independently reproduced 14 hierarchy unit/LiveView tests. Chromium checks cover actual
1440 × 900, 390 × 844 and 320 × 400 CSS viewports for popup reachability, focus and dismissal.
Other browser engines and assistive-technology sessions remain unverified. The operator accepted
the delivered result within these limits. Publication, merging and deployment require their
own authorization.

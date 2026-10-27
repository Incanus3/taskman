# Expandable Task listing

**Status:** Implemented and operator accepted

**Date:** 2026-10-27

The main workspace Task table shows filtered roots initially. Expanding a root reveals its entire
work-breakdown subtree, including Tasks outside the selected location or statuses. This reduces
repeated hierarchy entries while retaining access to work whose ancestors are all filtered out.

## Decisions for review

The baseline decisions and D14 expansion-memory refinement are approved and implemented.
Superseded decisions retain their rationale.

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D2 | Accepted | Expand every recursive descendant, bypassing all listing filters | Expanded groups may be large and include closed Tasks | [Expansion](#expansion-shows-the-complete-subtree) |
| D3 | Accepted | Use a browser-table projection rather than changing shared flat retrieval | Adds a distinct projection and its tests | [Ownership](#approaches-and-implementation-ownership) |
| D5 | Accepted | Keep expansion through updates and detail navigation; reset on location changes | Expansion is temporary and not part of shared links | [State](#expansion-state-and-refresh) |
| D6 | Accepted | Keep badges counting filter matches independently of expansion | Badge values can differ from displayed row counts | [Counts](#counts-and-empty-states) |
| D7 | Accepted | Apply the selected column sort to roots and each sibling group; use Task ID to break ties | Descendants remain grouped under parents rather than globally sorted | [Ordering](#ordering-and-overlap) |
| D8 | Accepted | Keep the existing conditional Location column | Out-of-scope descendants have no Location cell when Include child Lists is off | [Rows](#rows-and-accessible-disclosure) |
| D9 | Accepted | Exempt the browser projection from API/CLI parity | External clients keep flat retrieval and existing hierarchy queries | [Ownership](#approaches-and-implementation-ownership) |
| D10 | Accepted | Use a dedicated expansion column before ID with an always-visible chevron | Consumes additional horizontal space | [Disclosure](#rows-and-accessible-disclosure) |
| D11 | Accepted | A matching Task is a contextual root only when none of its ancestors matches the filters | Root selection needs ancestor links across filtered gaps | [Roots](#roots-come-from-the-complete-filtered-result) |
| D12 | Accepted | Close Move on deliberate row hiding, but preserve external-update recovery into detail | Row loss has different outcomes depending on its cause | [Move integration](#approaches-and-implementation-ownership) |
| D13 | Accepted | One automated browser test for disclosure visibility and independent activation; inspect responsive layout, geometry, and touch directly | Layout and touch regressions rely on direct acceptance rather than new automated cases | [Verification](#verification-and-delivery-boundaries) |
| D14 | Implemented | Remember expanded branches across status and Include child Lists changes, including returning ancestors | Up to one Project-sized set of Task IDs; expanding an ancestor reveals its entire subtree again | [Expansion memory](#remembered-branches-survive-filter-changes) |
| D1 | Superseded | Immediate-parent visibility alone determined roots | Produced separate roots across filtered gaps; replaced by D11 | [Roots](#roots-come-from-the-complete-filtered-result) |
| D4 | Superseded | Overlapping roots moved into an expanded ancestor group | Required duplicate ownership and dormant expansion state; eliminated by D11 | [Overlap](#ordering-and-overlap) |

## Roots come from the complete filtered result

Let `F` be the existing flat result after applying the selected Project/location, Include child
Lists, status selection, and any future listing filters. A Task `t` in `F` is a contextual root
exactly when none of its ancestors is a member of `F`. A Task with no parent therefore qualifies.
Check the entire ancestor chain, including through intermediate Tasks that do not match filters.

Ancestor visibility means membership in `F`, not whether its row is currently rendered or whether
it is collapsed. A matching ancestor suppresses the descendant as a root even when collapsed.
On a fresh connection or location entry, every root starts collapsed; its descendants appear only
through expansion. D14 covers subsequent filter changes.

D11 supersedes D1's immediate-parent-only rule. The former rule made a matching grandchild into a
separate root whenever its intermediate parent was filtered out, despite a matching ancestor above
it. The accepted ancestor rule keeps that work in one subtree. It requires ancestor links outside
`F`, but removes the separate overlapping-root behavior described under [ordering](#ordering-and-overlap).

Location semantics remain those of the existing listing: direct owning location with Include child
Lists off; that location and its List descendants with it on. Project tasks with inclusion off
means direct Project ownership, and with inclusion on means the whole Project.

| Situation | Initial result | Expanding the root |
|---|---|---|
| Matching parent A and child B | A | A, then indented B |
| B matches; all ancestors, including parent A, are outside the location scope | B as a contextual root | B and all its descendants; omit A |
| A is in a child List with inclusion off; B belongs directly to the selected List; no higher ancestor matches | B as a contextual root | Include B's descendants across Lists |
| A is Done and excluded; B is Pending and included; no higher ancestor matches | B as a contextual root | Include all descendants of B, including Done |
| A and grandchild C match; intermediate B does not | A only, even when collapsed | Expanding A shows A, B, C with depths 0, 1, 2 |
| C matches; all ancestors are filtered out | C as a contextual root | C and its descendants; omit ancestors |
| No Tasks match, including an empty status selection | Existing empty state | No expansion is available |

The operator explicitly extended invisibility to status and any future filter. Adding future
filters must change `F` before root selection; expansion must continue bypassing those filters.

## Expansion shows the complete subtree

A disclosure operates on a contextual root with at least one direct child anywhere in its Project.
Expansion inserts every recursive descendant immediately beneath that root in hierarchy order.
It does not reveal ancestors or siblings outside that subtree. Parent-child relationships are
Project-scoped, so expansion never crosses Projects.

Descendant rows bypass location, status, and future listing filters. They retain their actual Task
fields, owning location, and normal actions. Expansion never mutates ownership, parentage, or status.
Nested rows are not independently collapsible: this interaction opens or closes the entire root's
subtree. Leaf roots retain their ID and have no disclosure.

## Ordering and overlap

Apply the selected column sort to contextual roots and independently to every sibling group in
expanded subtrees, using the existing field semantics and direction. Break equal column values by
Task ID in the selected direction, matching existing column sorts. With no explicit column sort,
use the existing default Task order (`inserted_at` ascending, then `id` ascending) at every level.

Render depth-first preorder after sorting each sibling group: a child is followed immediately by
its own descendants before the next sibling. Expanded descendants stay contiguous with their root;
the table is not globally sorted across hierarchy levels. For example, a high-priority grandchild
stays beneath its low-priority parent rather than moving ahead of that parent's siblings.

D7 accepts sibling sorting by the selected column rather than fixed creation order. This makes the
sort control consistent across roots and subtrees, at the cost of rearranging siblings when sorting
changes. It requires applying the existing comparator at each level, without changing hierarchy
traversal or introducing a new ordering preference.

D11 guarantees that no contextual root is an ancestor of another contextual root. Because each
Task has at most one parent, different root subtrees cannot overlap: render each Task once without
a separate duplicate-ownership pass. Collapsing A hides all its descendants; a matching grandchild
C does not reappear as a standalone root while A still matches.

D4's earlier overlap handling is superseded. Under the immediate-parent rule, expansion could move
C's standalone row beneath A, with a dormant expansion choice restored on collapse. Ancestor-based
root selection eliminates that situation and its duplicate-ownership mechanisms. D14 adds
branch memory for filter changes without restoring overlapping roots or row reownership.

## Rows and accessible disclosure

Reuse the current Task row, including status, priority, Task detail link, Add subtask, and Move.
Indent only the title column according to hierarchy depth; contextual roots have depth zero even
when they have real ancestors outside the group. Preserve truncation and usable title width at
narrow viewports and deep nesting; an implementation may cap visual indentation while exposing the
true depth through an accessible description. No additional tree connector artwork is required.

D10 adds a narrow, dedicated expansion column before ID. An expandable contextual root always
shows a right-pointing chevron when collapsed and a downward chevron when expanded, including
without row hover or keyboard focus. Keep its Task ID visible in the adjacent ID column. Leaf
roots and descendants inside an expanded group have an empty expansion cell; descendants have no
independent disclosure because the entire subtree is already shown. Reserve the same column space
for every row, including when no roots have children, to preserve alignment. Use an accessible
column label such as “Expand subtasks” without requiring a visible header label.

The disclosure is a real button with an accessible Expand/Collapse label identifying the Task,
`aria-expanded`, and Enter/Space activation. Clicking it must not open Task detail. Mouse, keyboard,
and non-hover/touch devices use the same visible control. Keep title and column positions stable
when expanding, collapsing, hovering, or focusing; the additional column must fit the narrow layout.

The earlier hover-only ID replacement is superseded: it saved column space but concealed whether a
root had children until interaction. A small separate child indicator beside the ID was considered,
but combining indication and disclosure in one always-visible button is clearer and keeps IDs
readable. The accepted dedicated column costs horizontal space in exchange for discoverability.

Use the existing `<.icon>` component for chevrons. Preserve `tasks-<id>`, `open-task-<id>`, and existing action IDs;
add `toggle-task-subtasks-<id>` for root disclosure buttons. Provide depth metadata on rows for
structural verification. Keep the table/list semantics; a new ARIA treegrid and arrow-key navigation
model are outside scope.

D8 retains the current rule: show the Location column only when Include child Lists is on,
including while subtrees are expanded.
An expanded out-of-scope row still opens correct Task detail in the current browse context. Always
showing Location for expanded groups would add clarity but also change table columns on expansion;
that extra layout change is deferred. Actual location remains available in Task detail and Move.

## Expansion state and refresh

Store expansion in LiveView listing state, separately from the Task-detail hierarchy's independent
branch controls. Reset it when the selected Project or List identity changes, or when the listing
is cleared during missing-location recovery. A new connection/page reload starts collapsed. Do not
add URL parameters, browser storage, database state, or shared-link fields.

D14 replaces the baseline root-only memory rule, which pruned expansion whenever a Task stopped
being a contextual root. That simpler rule lost visible descendants on promotion and forgot
returning ancestors; branch memory now preserves expansion across those transitions.

Only accept disclosure events for current expandable contextual roots.
Malformed, stale, missing, foreign-Project, leaf, or non-root IDs are ignored without mutation or a
new user-facing error. Normal Task lookup remains Project-scoped, so opening a descendant outside
current filters continues to work. Refresh after local changes and existing validated external
notifications; reuse current reconciliation and reconnect behavior rather than adding polling.

### Remembered branches survive filter changes

D14 extends the existing expansion-ID set to remember non-leaf Tasks whose complete subtrees have
been revealed in the current browse location. Status selection, Include child Lists, sorting,
preference hydration, detail navigation and ordinary refreshes preserve this memory even when a
Task becomes hidden or stops being a root. Only contextual roots have disclosure controls; their
membership in the remembered set determines whether they render expanded.

Expanding a root remembers it and every non-leaf descendant revealed beneath it. Every subsequent
projection also remembers non-leaf Tasks revealed beneath an expanded root, including new children
or a branch brought into that subtree by an update. Filtering out an expanded ancestor therefore
promotes its matching descendants already expanded; restoring the ancestor restores its expansion.
Unexplored roots start collapsed. Memory does not make a filtered-out Task into a root or reveal
ancestors outside an expanded group's existing full-subtree contract.

Collapsing a root forgets it and its entire current descendant subtree, leaving unrelated branches
and hidden ancestors' memory intact. Descendant memory cannot partially collapse an expanded
ancestor: its full subtree always renders. Restoring a remembered ancestor therefore reveals and
remembers its non-leaf descendants again, even if one was collapsed while acting as a root.

| Starting state and action | Result |
|---|---|
| Expand A in A → B → C | A, B, C visible; remember A and B |
| Exclude A's status while B still matches | B is an expanded root; C stays visible; retain A's memory |
| Restore A's status | A returns expanded, showing A, B, C |
| A belongs to a child List; disable Include child Lists while B remains in scope | B becomes an expanded root; C stays visible |
| Re-enable Include child Lists | A returns expanded with its complete subtree |
| Collapse B while A is excluded, then restore A | B's branch memory clears; A remains remembered and returns showing its complete subtree |
| Collapse A, then filter A out | B starts collapsed because collapsing A forgot its entire branch |
| Select no matching statuses, then restore the prior selection | Empty state is truthful; remembered branches return expanded |
| Change Project/List, reload, reconnect with a new LiveView, or clear for missing-location recovery | All expansion memory resets |

Use one `MapSet` of Task IDs in listing state, extending the existing set rather than storing Tasks,
rows, ancestor maps or per-filter histories. An expanded branch needs one ID per non-leaf Task;
worst-case retained state is O(N) IDs for the Project. Reuse the existing batch-loaded Project
hierarchy for branch marking, collapse and validation; this refinement adds no database queries,
persistence, URL fields or browser-storage entries. Exact private interfaces remain planning choices.

On a nonempty projection, prune remembered IDs for deleted, foreign-Project or now-leaf Tasks, but
retain valid hidden/non-root branches. An empty matching result preserves the ID set and existing
empty-state queries without loading the whole Project solely to prune memory; stale IDs are removed
on the next nonempty projection. Thus empty results can temporarily retain IDs from the last
validated Project snapshot without accumulating new history. A branch pruned after losing its last
child starts collapsed if children are added later, unless revealed by an expanded ancestor.

Apply explicit disclosure actions only after validating their target against fresh expandable
contextual roots in the same batch projection. Invalid targets leave remembered intent unchanged;
normal refresh may still prune invalid IDs. A collapse must remove descendants before rebuilding
rows, so visible rows cannot immediately re-add the collapsed branch's memory. Keep existing
count, ordering, full-subtree, Move and stream-reset contracts.

Root-only memory is simpler but loses the visible subtree on promotion and forgets returning
ancestors. Remembering only previously expanded roots would restore A but still collapse B. The
selected branch-memory approach covers both directions with a single ID set; its cost is retaining
hidden branch IDs and allowing a restored ancestor to reveal a previously collapsed descendant.
The implementation and the MVP product contract use this branch-memory contract.

## Counts and empty states

D6 preserves the existing [counting contract](2026-10-26-list-task-counts-design.md):
badges count Tasks matching location/status filters, including children hidden in collapsed groups.
Expansion does not change those counts, include extra out-of-scope descendants in them, or trigger
count recomputation merely because a disclosure changed. The current tooltip already describes
matching Tasks. This avoids badges jumping when exploring hierarchy, at the cost of no longer
representing the number of rendered rows. Counting rendered rows instead would conflate matching
work with the expanded context and change sidebar/header accounting.

Derive empty and filtered-empty states from matching Tasks, not from an expansion cache or stale
rendered rows. In a valid finite acyclic hierarchy, a nonempty `F` always has a contextual root.
Retain the current empty-state wording and status-filter recovery.

## Approaches and implementation ownership

Repository inspection found the following existing seams:

- `Taskman.Tasks.list_tasks_for_location/3` returns flat `TaskWithLocation` records and applies
  location/status/sort options. It serves existing retrieval behavior beyond this table.
- `ProjectLive.Tasks.Listing` coordinates listing state, filters, sorting, count refreshes, and
  resetting the Task stream. `Reconciliation` already refreshes it for relevant notifications.
- `TaskmanWeb.Tasks.Table.row/1` renders normal rows, and `project_live.html.heex` consumes the
  `:tasks` stream with stable per-Task IDs.
- `Taskman.Tasks.Hierarchy` already projects full connected Task trees with location paths and
  deterministic ordering, but its public operation loads the selected Task's ancestors as well.

The agreed approach adds a dedicated browser-table projection. Persistence and hierarchy data
retrieval belong behind a `Taskman.Tasks` context entry point, using focused capability/persistence
modules consistent with the [development guide](../guides/development.md). LiveView owns expansion
state and event handling; the component owns disclosure and indentation. The projection may use a
small row wrapper carrying Task/location plus depth and disclosure state. Preserve existing shared
flat retrieval and `TaskWithLocation` consumers rather than adding unrelated presentation fields
to every consumer. Exact function names and the split between pure projection and data retrieval
are implementation choices.

Load needed hierarchy/location data in batches, including ancestor links through filtered-out Tasks:
no per-row, per-ancestor, or per-descendant database lookup, and no separate full connected-hierarchy
call for every root. A bounded set of Project-scoped reads may
load compact hierarchy data or the necessary Tasks for this initial unpaginated table. Do not
introduce an unbounded cache. Keep full Task descriptions off the LiveView state when unnecessary;
retain the existing stream memory model. Reuse ancestor membership results within a projection
where useful to avoid repeatedly walking shared ancestor chains. Expansion cost necessarily grows
with the subtree shown.
This design adds no pagination, virtualization, background loading, or cross-query snapshot
isolation; existing notification refresh handles concurrent changes.

Keep stream child IDs unique and reset/re-stream affected rows when depth, expansion, or root
membership changes. Updating a separate assign alone does not reliably update already streamed
row content.
Disclosure and sorting refresh the table only, leaving counts/navigation untouched where their
inputs have not changed. Preserve normal row actions for descendants outside `F`.

`Tasks.Movement` currently checks row anchoring against the flat filtered listing and reinserts
`TaskWithLocation` directly into the stream. Adapt both consumers to the rendered projection:
a Move popover remains anchored while its Task row is rendered, including an out-of-filter descendant.
D12 distinguishes deliberate hiding from external changes. If the user collapses or filters away
the row, clear its Move popover without navigating or reinserting a stale row. If a validated
external update removes a surviving Task's row, preserve existing recovery: reopen Move in Task
detail at its fresh location without moving the Task. A descendant still rendered under an expanded
root remains row-anchored even after changing location or status. Preserve missing-Task errors and
missing-List recovery. Reinsertion must preserve depth, group position, and disclosure state.
Successful moves keep the existing destination/backdrop navigation behavior; do not silently
introduce a different Move workflow for expanded rows.

Changing shared retrieval into a tree was rejected because it would alter API/CLI behavior for a
browser presentation need. Client-only hiding of flat rows was rejected because the filtered result
cannot contain descendants excluded by filters and therefore cannot implement full expansion.

D9 is an accepted exception to new-query API/CLI parity: the projection composes existing
Task retrieval and hierarchy capabilities for browser rows, introducing no new persisted operation
or external retrieval capability. No API response, CLI, help, completion, or bundled skill change
is included.

## Verification and delivery boundaries

Focused tests must establish:

- Root selection for direct Project Tasks, selected Lists, child inclusion on/off, outside parents,
  status-hidden ancestors, empty filters, deep filtered gaps, and Project isolation. A matching
  ancestor suppresses a descendant root regardless of intermediate matches and expansion state.
- Full recursion through excluded intermediate Tasks, actual depth/location/status preservation,
  selected-column sibling ordering and tie-breaking at multiple depths, default creation order,
  and disjoint root subtrees with unique rows in either root sort direction.
- Collapse keeps matching grandchildren hidden beneath a matching ancestor; ancestor filter changes
  promote/demote roots under D14 with status and Include child Lists round trips preserving
  expanded descendants and returning ancestors.
  Verify explicit whole-branch collapse (including a collapsed promoted root followed by ancestor
  restoration), multiple sibling branches, empty-filter round trips, deleted/foreign/leaf pruning,
  newly revealed branches, and bounded ID-only memory without additional queries. Cover location resets,
  sorting/filter preservation, and stale-event no-ops.
- LiveView disclosure round trips, `aria-expanded`, normal detail/action paths for out-of-scope
  descendants, Move anchoring and reinsertion on nested rows, closure on deliberate hiding,
  external-update recovery into detail, unchanged badge values, and truthful empty states.
- Existing local mutations and validated external updates refresh visible expanded groups, including
  parent changes, moved children, status changes, and removed Tasks. Use existing fixture/persistence
  deletion techniques: this work does not add a deletion UI.
- Data-access query count stays bounded as roots/descendants increase; no web-layer persistence.

Use pure/context tests for projection rules and LiveView tests for server interactions, rendered
outcomes, and accessibility attributes. D13 limits new automated browser coverage to one focused
test for computed chevron visibility before hover/focus and real mouse/keyboard activation independent
of the row's covering Task-detail link. It must establish Tab reachability, Enter/Space activation,
and that disclosure clicks are not intercepted by the row link. LiveView tests cannot prove CSS
visibility, browser hit testing, or native focus/activation behavior; do not duplicate their
expansion, filtering, ordering, or state matrices in the browser.

Perform responsive layout, geometry, and touch acceptance by direct browser inspection, without
adding automated cases for those checks. Inspect desktop and narrow layouts, readable IDs,
indentation, empty-cell alignment, stable column positions, and non-hover/touch operation. Record
actual viewport and horizontal scroll widths and identify emulation limits. Do not add CSS styling
assertions to server tests. Run focused tests and `mix precommit` for implementation, plus privacy
and planning-terminology checks before committing.
Independent implementation verification remains scoped to this feature and relevant interactions.

The browser listing projection, temporary expansion state, accessible disclosure, and Move integration
are implemented. The [MVP specification](../product/mvp-spec.md#4-work-organization-and-navigation)
owns the current product contract. The [relationship contract](../product/relationships.md) remains
authoritative for Project-scoped acyclic parentage and independent owning location; the Task-detail
hierarchy is unchanged. No external research was needed for this design.

The complete delivery, including D14 and the responsive thresholds, is operator accepted. Completed
implementation plans and the workstream handoff are retired. Verification and acceptance evidence
is recorded under repository-relative Task `52`, with description marker
`Source: taskman/expandable-task-listing`.

Acceptance uses Chromium mouse/keyboard disclosure coverage, direct responsive geometry inspection
and Chromium touch emulation. It does not establish physical-device or other-engine acceptance.
Disclosure re-streaming releases keyboard focus to the page body; the browser test re-enters with
Tab before the next keyboard activation. At a 312-pixel viewport, inspection retained 45 pixels
for a depth-nine title, with the complete title available through its accessible row link.
Whole-Project loading remains an accepted transient read; query bounds do not establish
large-Project latency or cross-query snapshot isolation. These limits do not add implementation
work to this completed scope. Deployment and merging retain separate authorization gates.

# Task tree guides in the main listing

**Status:** Implemented and operator accepted

**Date:** 2026-10-27

Make parentage and sibling groups easier to follow without changing the aligned Task table.
The operator approved the written design and requested implementation without a separate plan.

## Decisions

| ID | Status | Choice | Cost or constraint | Rationale |
|---|---|---|---|---|
| D1 | Accepted | Tree guides inside the Task column | Requires branch-ending metadata alongside depth | [Connectors](#connectors-show-parentage) |
| D2 | Accepted | Softer separators within families; stronger separators between roots | Internal row boundaries become less prominent | [Separators](#separators-support-both-reading-directions) |
| D4 | Accepted | Preserve the six-level indentation limit and compress deeper branches into the last guide lane | Deep branches cannot be distinguished by horizontal position alone | [Narrow layouts](#narrow-layouts-and-deep-trees) |
| D5 | Accepted | Short title-only connectors below the desktop table breakpoint | Loses continuous rails on stacked rows; preserves compact metadata placement | [Responsive treatment](#stacked-rows-use-short-connectors) |
| D6 | Accepted | Highlight all connectors in a displayed root family when any row is hovered or focused | Requires browser event coordination across streamed sibling rows | [Interaction](#interaction-and-accessibility) |
| D3 | Superseded | Highlight only the hovered or focused row's guides | Simpler CSS, but fragmented a family's visual feedback; replaced by D6 at operator request | [Interaction](#interaction-and-accessibility) |

## Connectors show parentage

The component, `lib/taskman_web/components/tasks/table.ex`, indents only the title, capped
at six levels. IDs and other columns use a shared desktop subgrid.
The listing projection already emits each complete subtree in depth-first sibling order.
These are source-inspection findings, not new layout measurements.

In the desktop table, draw 2-pixel vertical rails and short horizontal elbows in the title's
indentation area. Use a 20-pixel depth step, position each rail 4 pixels right of its parent
label's start, and use an 8-pixel horizontal arm, leaving 8 pixels before the child label.
Start outgoing trunks 8 pixels below the bottom of the 20-pixel title line. These dimensions
preserve row height and cost up to 36 more pixels of desktop title indentation than the
original 14-pixel step. Stacked rows retain that
original step and their compact connector geometry. Each child gets an elbow from its
parent's lane. A sibling lane continues through earlier
siblings and their descendants, ending at the final sibling's elbow. An ancestor lane is
present only when that ancestor has a following sibling. Roots have no incoming connector;
an expanded root starts its child rail below its title.

For A with children B and D, and B with child C, B connects down to D; C connects to B,
and the B-to-D rail continues beside C. D terminates A's child rail. A following root E
has no connection to A's family. Sorting and filter-driven contextual roots use the same
rules relative to the displayed tree; never draw a connector to a hidden parent.

Add presentation metadata to `ListingRow` through `ListingProjection`, with no additional
database queries. Metadata must remain correct after sorting, filtering, expansion,
reparenting and row reinsertion for Move. Keep the shared flat retrieval unchanged.
The row component owns decorative markup, and `assets/css/app.css` owns rail geometry.
The LiveView template passes the metadata to the component.

## Separators support both reading directions

Replace uniform bottom borders with faint internal separators and a stronger full-width
separator before each root except the first. Give the final Task the same stronger bottom
border, regardless of the hidden empty-state element's position in the stream. Reserve
scrollbar width only when content needs vertical scrolling; expansion may therefore change
available column width when a scrollbar appears. Desktop connectors cross row boundaries continuously
inside the title area, without gaps caused by row padding. Keep the header border.
IDs, Location, Status, Priority and Actions retain their current columns and alignment.
Preserve row height, click targets, disclosure placement and the existing responsive layouts.

Background shading alone was considered but does not identify parents within a nested family.
Keeping current border strength would make the horizontal grid compete with the tree. The
selected treatment preserves horizontal tracking while giving the tree greater visual weight.

## Interaction and accessibility

Use muted indigo guides, stronger than the internal separators. Hovering any row highlights
all connectors in that displayed root's family, including the root, siblings and deeper
descendants. Keyboard focus within any row activates the same family. Separate hovered and
focused families may both be highlighted. Leaving the family clears pointer highlighting;
leaving keyboard focus clears focus highlighting. Unrelated families retain their normal color.

Keep family membership correct after stream replacement, sorting, filtering, reparenting and
collapse. Membership follows rendered contextual roots, not hidden ancestors. Coordinate this
with a small client controller observing existing row depth metadata; do not add persisted
state, queries or server round trips for hover. Keep stream content managed by LiveView and
disconnect browser listeners/observers when the controller is destroyed. Row-local CSS was
simpler but produced fragmented feedback; wrapping each family would require restructuring
the existing Task stream solely for this decoration.

Guides are hidden from accessibility APIs and never intercept clicks. Retain accessible depth
text, Task links and disclosure semantics. Transition guide colors briefly, respecting reduced motion.

## Narrow layouts and deep trees

On desktop, stretch guide layers across the complete row. On stacked rows, draw only
short elbows beside the title, as specified below. Preserve the six-level indentation cap
to bound the width taken from titles. At deeper levels, coalesce overlapping rails into the final
lane and retain actual depth in accessible text. Deep hierarchy is consequently less explicit
visually; do not suggest a new root or connect across families. Larger indentation would
make depth clearer but reduce already limited title space.

## Stacked rows use short connectors

Below the desktop table breakpoint (80rem / 1280 CSS pixels), hide ancestor rails and outgoing
child trunks. Each child retains a short elbow confined to the title line, including children
whose deep guide lane is shared. Metadata keeps its current position and rows stay compact.
At and above the breakpoint, show continuous rails with the full sibling/ancestor semantics.

Browser inspection established that full-height guides intersect Location text and status
badges on stacked rows. Reserving a guide gutter beside metadata would avoid intersections
but make narrow rows taller. The operator selected short connectors to preserve the existing
metadata placement and row-height budget, accepting reduced visual continuity below desktop.

## Verification and authorization

Add focused projection tests for siblings, nested last children, ancestor continuations,
multiple roots, reordered siblings and contextual roots. These test the metadata used to
draw meaningful connections, not CSS classes. Exercise existing expansion and Move tests.
Inspect rendered desktop, intermediate and narrow layouts, long titles, variable row heights,
deep nesting, hover/focus and click targets. Record actual viewport and scroll widths.
Run focused tests, asset build and `mix precommit`; audit changes for privacy before committing.

This is a browser presentation refinement under the existing
[expandable listing contract](2026-10-27-expandable-task-listing-design.md).
No persistence, API, CLI, sorting, filtering or disclosure behavior changes are included.

Approval covers the current decisions and current-session implementation, including the
operator-requested geometry and family-wide highlighting refinement. The operator waived
a separate implementation plan for this bounded presentation change. Local commits are
authorized by repository policy; publication, merging and deployment require separate authorization.

Implementation verification established:

- `mix precommit`: 1,350 passed, 3 skipped; browser tests excluded from that gate.
- Focused Chromium tests cover disclosure, table edges, and whole-family pointer/focus
  highlighting, including actual root/sibling reordering, collapse/re-expansion and contextual
  root promotion after filtering an ancestor.
- A temporary responsive probe checked desktop stroke/step/label-gap/trunk-gap geometry,
  title-only short stems below desktop, hidden ancestor rails/trunks, readable depth-nine
  titles and no horizontal overflow at actual CSS widths 1440, 1280, 1279, 1024, 640, 390 and 312.
- Rendered screenshots were inspected at those widths. At 312 pixels, deep titles retained
  about 46 pixels of width. Independent scoped review found no blocking defects.

A focused permanent browser regression checks unused scrollbar space and the final-row border
at 1440, 1024, 390 and 312 CSS pixels, before and after expanding the final root. The broader
responsive probe uses isolated synthetic fixtures and is not a new permanent browser suite.
These results establish Chromium behavior, not physical-device or other-engine acceptance.

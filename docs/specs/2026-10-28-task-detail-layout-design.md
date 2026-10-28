# Task detail layout, hierarchy and responsive sizing

**Status:** Implemented and operator accepted within the recorded verification limits.

**Date:** 2026-10-28

Keep Task detail readable and usable across narrow and wide viewports: connected hierarchy,
dedicated header, bounded editor and readable discussion. Preserve editing and discussion behavior. Adopt the visual direction from
[issue 25](https://github.com/Incanus3/taskman/issues/25), with the operator's revised depth
target and the review refinements below. This specification governs repository adoption;
the current accepted decisions below were approved by the operator.

## Accepted decisions

Decision IDs retain their meaning within the hierarchy and sizing groups. This specification
consolidates the hierarchy/header and wide-layout contracts.

### Hierarchy and header decisions

| ID | Status | Choice | Downside or constraint | Rationale |
|---|---|---|---|---|
| D1 | Accepted | Wrapping titles, connected branches, ID/status metadata, current-task highlight and dedicated Close header | Taller rows show fewer Tasks at once | [Presentation](#titles-and-metadata-stay-readable) |
| D2 | Accepted | Clean presentation through four levels at all supported widths; five where straightforward | Deeper hierarchies receive an access guarantee rather than polished layout | [Depth](#four-levels-are-the-primary-depth-target) |
| D3 | Accepted | Start with three columns at 1280 CSS px, two at 1024 px, overlay below; validate actual available width | Thresholds may need adjustment during browser acceptance | [Responsive layout](#responsive-layout-keeps-the-editor-usable) |
| D4 | Accepted | Use contained horizontal overflow only when indentation would leave less than 180 px for title and metadata | Exceptional deep branches require horizontal scrolling | [Depth](#four-levels-are-the-primary-depth-target) |
| D5 | Accepted | Native nested lists, links and disclosure buttons instead of ARIA tree roles | More Tab stops than a composite tree widget | [Accessibility](#nested-lists-match-the-keyboard-contract) |
| D6 | Accepted | Required-open branches get a noninteractive expanded marker in the disclosure slot | Expanded indicators have two distinct interaction treatments | [Expansion](#expansion-and-navigation-preserve-current-state) |
| D8 | Accepted | Use the public rendered canvas for frame comparison, with the saved source and overview preserving the reference | Canvas comparison requires browser access; individual PNGs remain optional conveniences | [Visual references](#visual-references-and-their-authority) |
| D7 | Superseded | Require individual native frame exports before visual implementation; replaced by D8 | Authenticated export unnecessarily blocked access to an already public rendered reference | [Visual references](#visual-references-and-their-authority) |

D1–D6 and D8 record the operator's accepted choices and trade-offs; D8 replaces D7's export prerequisite.

### Wide sizing decisions

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Grow above 1440 CSS px to a 2100 px dialog and 448 px hierarchy | A 4K viewport still has outer space once the cap is reached | [Sizing](#wide-sizing-grows-from-the-existing-layout) |
| D2 | Accepted | Keep existing form padding, cap the editor panel at 800 px, and give surplus width to discussion | Discussion becomes wider when hierarchy collapses | [Allocation](#discussion-receives-the-space-outside-the-form) |
| D3 | Accepted | Comment text fills its available content width with symmetric insets | Longer lines on wide discussion panels | [Comment width](#comment-text-uses-the-available-width) |
| D4 | Accepted | Discussion has at least 320 px inner width when beside the editor (369 px total) | Reduces editor width by up to 81 px near the three-column boundary | [Allocation](#discussion-receives-the-space-outside-the-form) |

The sizing choices replace the former 2240 px dialog and 1016 px editor proposal with a
2100 px dialog, 800 px editor panel and 320 px inner discussion minimum. Equal comment insets
remain required. The hierarchy and sizing choices are implemented, independently reviewed and
operator accepted within the recorded verification limits.

## Baseline findings and inherited constraints

Source inspection at repository baseline `8b35b184e0a4a1443de4fd0ad3b241c50a05001f`
established the following. These are code findings, not rendered-layout measurements:

- `lib/taskman_web/components/tasks/detail.ex` renders single-line truncated titles, nested
  lists with ARIA tree roles, separate disclosure buttons, and current-task links. Required-open
  branches then had no disclosure button. The hierarchy omits ID/status metadata.
- `assets/css/app.css` allocates 240 px to the expanded hierarchy from 768 px, overlays it
  below that width, and places discussion beside the editor from 1280 px. Continuous left
  borders do not terminate at the final child's elbow.
- `core_components.ex` positions modal Close absolutely at top right. The wide modal is
  capped at 1280 px and has outer margins, so viewport width is not content width.
- `ProjectLive.Tasks.Hierarchy` already preserves manual expansion inside a connected tree
  and forces open the selected Task and its ancestors. The workspace's then-colocated
  `TaskDetailLayout` hook owns the panel preference, overlay handling and narrow Escape behavior.
- `assets/js/project_live_hooks.js` intercepts hierarchy-link departures when a comment draft
  is dirty. Close uses `request_task_departure` through the modal's deferred cancellation path.

The [MVP contract](../product/mvp-spec.md#4-work-organization-and-navigation),
[relationship semantics](../product/relationships.md),
[parent-child design](2026-08-30-parent-child-task-hierarchy-design.md), and
[comments design](2026-09-26-task-comments-design.md) retain authority for domain behavior,
autosave, comments and departure protection. This approved design replaces the corresponding
older design sections for hierarchy presentation, responsive treatment and semantic markup.
The older domain/API/CLI contracts remain in force. The product documents describe the delivered
behavior; the recorded verification limits remain explicit below.

This work adds no search, drag-and-drop, reordering, bulk actions, tree status editing,
reparenting gestures, hierarchy loading model, domain operations or API/CLI changes. Parentage
remains editable through the existing Parent Task field. Task List location remains independent
of parentage. The recent main-listing tree-guide design is a separate surface; its indentation
cap and family highlighting do not automatically apply to this dialog.

## Visual references and their authority

The source is the designer's [shared canvas](https://app.pen.dev/s/nGke-NUwM9_vdg_J-jOSdofWvseFjbOqc7uwVM0Sxx0),
described as v2.1, dated 2026-10-01. Implementation references are:

| Frame | Use |
|---|---|
| 01 Desktop task detail · Frozen v2 | Hierarchy rows and full desktop dialog composition |
| 02 Narrow hierarchy open · Frozen v2.1 | Header and hierarchy controls reachable together |
| 03 Narrow task detail · Handover reference | Closed hierarchy; editor followed by discussion |

Frame 00 is a feature banner, not an application screen. The archive contains rejected earlier
explorations, including immediate-family-only navigation; do not implement those alternatives.
The mock's editor, breadcrumbs, sharing, Move, Related Tasks and discussion provide composition
context rather than permission to replace current behavior or omit existing controls.

A dated [source snapshot](../prototypes/task-detail-hierarchy/2026-10-28/taskman.pen),
[canvas overview](../prototypes/task-detail-hierarchy/2026-10-28/canvas-overview.png) and
[SHA-256 manifest](../prototypes/task-detail-hierarchy/2026-10-28/SHA256SUMS) preserve the reviewed
reference locally. The downloaded source matched the public share manifest's SHA-256
`7aa0b4ce71eab6d529189621bd2161683cb7ef6e7d5c59c3349cec41a7665834`.
The repository copy removes only the top-level service `fileToken`; its own checksum is in
the local manifest. No signed download URLs, account metadata or browser chrome are retained.

The overview is a low-resolution whole-canvas preview, not a native frame export. Use the public
canvas at a readable zoom to inspect frames 01–03 and compare them with the rendered application.
Move the pointer away from the inspected frame and distinguish canvas hover/selection outlines
from design borders; editor overlays are not application styling.
Before comparison, verify that the public share's source checksum still matches the recorded
original hash above. If it differs, retain the saved source as authority and resolve the reference
difference rather than silently adopting a later revision. The manifest checksum was rechecked
and still matched when the operator accepted this comparison method.

D8 replaces the mandatory native-export prerequisite in D7. Individual PNGs offer a convenient
stable reference at known resolution, but add no design information beyond the rendered canvas.
They may be captured or exported when useful; native export and a pen.dev account are not required
for implementation. Preserve the local source and overview checksums. Any new reference images
must identify their source and update the manifest without claiming native-export fidelity for
browser screenshots. This Markdown governs behavior where source annotations retain superseded
responsive, depth or workflow assumptions.

## Titles and metadata stay readable

Use the application font stack and semantic theme colors. Reference dimensions guide visual
matching; readability, contrast and reachable controls take precedence over exact pixels.

| Element | Reference treatment |
|---|---|
| Expanded desktop panel | 336 px through 1440 CSS px; wider allocation follows the [wide sizing contract](#wide-sizing-grows-from-the-existing-layout) |
| Title | 14 px, approximately 1.35 line height; selected title semibold |
| Metadata | 11 px reference; increase if rendered readability requires it |
| Metadata ordering | Task ID, 10 px gap, then a 5 px status dot and 4 px gap before status text |
| Row | Approximately 9 px vertical and 8 px horizontal padding; 5 px title/metadata gap |
| Indentation | Approximately 15 px first nested offset, 20 px thereafter |
| Connectors | 2 px, matching the main listing's normal desktop connectors; muted slate `#33445F` |
| Selected row | Muted indigo surface `#232C4D`, border `#485888`, 6 px radius |

Titles wrap naturally; long unbroken strings must break. Do not normally ellipsize titles or
clamp their line count. Metadata stays beneath the title and may wrap when necessary.
The dot indicates status, never priority; retain visible status text. Reuse the canonical table
mapping: Icebox sky, Pending amber, In Progress red, In Review orange, Done emerald, Will Not Do
slate. The mock's yellow Pending tint does not replace canonical amber.

Draw vertical rails and short elbows from the nested branch structure. Each sibling rail ends
at its last child's elbow, aligned near the first title line; ancestor continuations must remain
correct beside descendants. Rails grow with wrapped content and do not continue into unused
space, cross unrelated roots or intercept interaction. Use layout-driven geometry rather than
fixed heights or per-row measurement/fetch loops. Recompute correctly after expansion,
reparenting, title/status updates and viewport changes.

For an expanded, unselected parent, continue its child rail upward within the parent row, leaving
the same small clearance below the parent chevron as the child arm leaves beside a child chevron
(3 px at the reference font size). A selected parent retains the connection below its highlighted
row rather than extending through that surface. This remains layout-driven for wrapped titles.
Anchor the continuation outside the row's selection border so border rounding at fractional
device scales cannot offset it horizontally from the child rail.
For an unselected leaf child, extend the horizontal arm through the empty chevron slot, leaving
the same title clearance a chevron would leave. Selected leaves and branches keep their shorter arms.
Compensate for the shallower first branch by shifting only the root row's chevron and Task pill
together. Preserve their internal spacing, all connector coordinates and the compressed narrow
layout's existing relative alignment.

The current-task surface follows the Task shown in the editor. Hover and keyboard focus remain
distinguishable from selection. Do not introduce main-listing family-wide highlighting here.
Keeping truncation would preserve density but fail the identification problem; immediate-family
presentation would reduce clutter but lose sibling and cousin navigation.

## The header owns dialog dismissal

Place one header above the hierarchy, editor and discussion, containing Task identity, Share
and Close. Preserve the existing actual-location breadcrumbs and Move control. Give Close
reserved layout space rather than floating it over Activity/Sessions or editor controls.
The header remains reachable while content scrolls, including narrow-panel and short-height
states. Discussion tabs stay inside their own region below the header.

Move the control, not its cancellation semantics: use the existing deferred modal dismissal
path and `request_task_departure`. Keep Go back, Submit comment and close, and Close and discard
draft; a failed submission retains the draft and error feedback. Preserve validation, autosave
conflicts, page-exit warnings, tab-switch draft retention and Sessions' truthful empty state.
Inspect all shared modal callers before introducing a header slot or Close-placement option;
other dialogs must retain a single working Close control and correct Escape/click-away behavior.

## Responsive layout keeps the editor usable

D3 accepts the issue's thresholds as initial CSS layout values, with layout verification determined
by actual dialog space rather than viewport labels:

| Viewport width | Composition |
|---|---|
| At least 1280 CSS px | Hierarchy, editor, discussion in three columns |
| 1024–1279 CSS px | Hierarchy beside editor; discussion below editor |
| Below 1024 CSS px | Hierarchy overlays editor content; discussion follows editor when panel is hidden |

At a 1280 px viewport, the dialog is 1232 px before subtracting its two borders.
With 336 px hierarchy and the final 369 px discussion minimum, the editor receives 525 px
including padding. The main Task table changes arrangements at 640 and 1280 px. Share its upper
breakpoint but use 1024 px for the dialog's lower boundary: the dialog must fit an editable form
beside the hierarchy. A 640 or 768 px side-by-side threshold would squeeze the widened editor.
Actual dialog space and rendered controls determine usability, not viewport labels alone.

Measure at 1280/1279 and 1024/1023 as well as the acceptance viewports. The title, Parent Task,
status/priority, breadcrumbs and Move must remain usable. The implementer may adjust CSS
thresholds or panel width to satisfy these same compositions and readability constraints,
recording measured values and rationale; changing composition or hiding controls requires
renewed design review. Do not add a second persistent preference for breakpoint-dependent modes.

Keep the dialog header outside the narrow hierarchy overlay. Both Close and the hierarchy hide
control remain reachable. A closed panel provides a labeled Task hierarchy trigger. Selecting
a Task leaves panel openness unchanged; hiding the panel returns to the editor without closing
the dialog. Preserve contextual initial openness and workspace-LiveView-session preference;
do not add browser-persistent storage.

Keep the hierarchy heading and hide control outside its independently scrolling body. On opening
or navigation, reveal the selected row with minimal scrolling only if it is outside the visible
region; do not reset an already useful viewport or steal editing focus. Contain scrolling within
the dialog/panel, including at 200% zoom. Discussion scroll behavior and composer access remain
intact at the revised layout boundaries.

## Wide sizing grows from the existing layout

All measurements below are browser CSS pixels at the normal 16 px root font size.
Physical display resolution alone does not establish the available viewport.

For viewport width V above 1440, define excess X = V − 1440:

- Dialog outer width M = min(V − 48, 1280 + 0.9X, 2100).
- Expanded hierarchy column H = min(336 + 0.1X, 448).
- Minimum discussion column D = max(369, min(288 + 0.32X, 640)).

Retain the 48 px desktop outer clearance and the dialog's existing two 1 px borders.
Use CSS sizing, preferably shared custom properties scoped to the normal Task detail dialog.
Express equivalent dimensions in rem where appropriate to preserve the existing sizing convention.
There is no widening jump at 1440 px.

Alternatives considered: keeping the current cap wastes available width; original B leaves
surplus space around a centered form; C grows to 3200 px but was not selected.
Revised B bounds overall width while prioritizing useful sidebar space.

## Discussion receives the space outside the form

At widths from 1280 px, cap the editor panel at 800 px total, including its existing 28 px
padding on each side: 744 px maximum form content with current production border ownership.
Do not add a centered, maximum-width inner form surrounded by surplus gutters.
Discussion receives all remaining available width after its minimum and the editor cap.
Discussion reserves at least 320 px of inner content width, excluding 24 px padding on each
side and its existing 1 px left divider: 369 px total. This outer width is a floor on the
existing fluid formula; wider-screen growth remains unchanged once that formula exceeds 369 px.
The inner width is inside the discussion panel, before comment-card padding. Stacked discussion
below 1280 px has no new minimum.

For the expanded hierarchy, editor width E = min(800, M − 2 − H − D);
discussion width R = M − 2 − H − E. Fractional widths may round within 1 px.
When hierarchy is collapsed, substitute its existing 48 px rail for H and apply the same
allocation. Its released width therefore goes to discussion after the editor reaches its cap.
Through 1440 px, use the existing dialog width M = min(V − 48, 1280), H = 336 and D = 369.
Applying the cap from 1280 px avoids a collapsed-editor width jump at 1440 px. Preserve the
full editor width when discussion is stacked below 1280 px.

| Viewport | Dialog | Expanded hierarchy | Editor | Discussion |
|---|---:|---:|---:|---:|
| 1440 | 1280 | 336 | 573 | 369 |
| 1920 | 1712 | 384 | 800 | 526 |
| 2560 | 2100 | 448 | 800 | 850 |
| 3840 | 2100 | 448 | 800 | 850 |

The comparison mockup placed a 1 px divider inside the editor; production places it on
discussion. Keep that border ownership and the existing padding when applying the panel cap.

## Comment text uses the available width

Keep discussion's existing 24 px panel padding and comment-card decoration/padding.
Text fills its card's available width; left/right insets remain symmetric.
Do not add a paragraph-specific character-width cap or an extra right gutter.
Keep wrapping and overflow protection for long unbroken text, timestamps and author names.
Tabs, comments, composer and Post remain within the panel with their current scroll behavior.

The visual comparison's plain paragraphs illustrate allocation, not permission to remove
production comment cards or alter comment semantics. It was checked at 1440, 1920, 2560 and
3840 px, including symmetric 24 px paragraph insets in the simplified mockup.
These observations do not establish production acceptance.

## Four levels are the primary depth target

Count the root as level one. One through four levels must remain clear and usable at every
supported width, including 320 CSS px. Test four levels with wrapped and unbroken titles before
adding exceptional-depth machinery. Five levels are desirable where achievable with the same
layout; do not add special modes or a larger feature solely to polish that case.

D4 retains truthful indentation and enables horizontal scrolling within the
hierarchy body when less than approximately 180 px would remain for title/metadata content,
excluding the disclosure slot and indentation. Apply this minimum through layout sizing, not
depth-specific server logic. At four levels, tune narrow-panel width, padding and indentation
to keep titles comfortably readable without horizontal scrolling as the normal treatment.
Five or more levels may use the contained fallback. Header and panel controls do not scroll
horizontally, and no tree causes page-wide overflow or silently hides Tasks.

An indentation cap is simpler but conflates different parent levels. Full indentation with no
minimum content width preserves structure but eventually reduces titles to a few characters.
The fallback preserves access without promising a polished layout for arbitrary depth. Eight
levels are not a visual acceptance criterion or a product depth limit. Revisit only if real
usage shows deeper trees are common; do not add limits, pagination or virtualization in this work.

## Expansion and navigation preserve current state

- Show the connected tree from the selected Task's topmost ancestor in existing sibling order.
  A disconnected Task is the selected root with no disclosure; retain the truthful empty-state copy.
- The selected Task and all ancestors stay expanded. D6 uses a decorative downward marker
  in their reserved disclosure slot, with no button role, tab stop, hover affordance or click
  handler. Leaves reserve alignment space but have no expanded marker or disclosure.
  Interactive and fixed chevrons share the same icon alignment, so changing selection does not
  shift a branch's chevron vertically when its interaction treatment changes.
- Only off-path branches with children get interactive disclosure buttons. A disclosure changes
  expansion only, never selection. Required-open markers avoid the false affordance of disabled
  lookalike buttons; omitting all markers is simpler but loses the reference's branch-state cue.
- Navigating within the same connected tree keeps manual choices and unions in the selected path.
  Opening another connected tree starts with its required path. Closing the dialog clears manual
  expansion; hiding the panel does not.
- Task links use existing URL-backed navigation and draft protection. Preserve Project/List
  backdrop, active filters, Include child Lists and panel preference even when the target is absent
  from the visible table. Breadcrumbs describe the selected Task's actual location. Re-selecting
  the current Task is idempotent.
- Parent changes keep existing same-Project, single-parent and cycle validation/autosave behavior.
  Successful updates refresh the hierarchy; rejected changes retain existing form/picker feedback
  and leave persisted hierarchy intact. Existing not-found/authorization handling must never show
  stale editable details under a new Task identity.

## Nested lists match the keyboard contract

D5 uses a labeled navigation region containing native nested `ul`/`li` elements, Task
links with `aria-current="true"` for the selected Task, and named disclosure buttons with
`aria-expanded` and `aria-controls` pointing to stable child-group IDs. Remove the hierarchy's
`tree`, `treeitem`, `group` and tree-specific `aria-level` attributes rather than promising a
composite widget interaction that this increment does not supply. Preserve existing stable IDs.

This is an application of the W3C
[disclosure navigation pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation/),
whose native list structure conveys hierarchy. The alternative
[tree-view pattern](https://www.w3.org/WAI/ARIA/apg/patterns/treeview/) includes arrow-key and
Home/End behavior, adding a keyboard model outside the agreed scope. Tab/Shift+Tab navigate
ordinary controls; Enter activates links, and Enter/Space activates buttons. The cost is more
Tab stops in a broad tree. Rails, dots and fixed expanded markers are decorative and hidden from
assistive technology; status text remains exposed. Use distinguishable focus indicators,
readable contrast and reduced-motion treatment.

The modal must retain its accessible name, focus containment, Escape handling and focus return
to the opener, consistent with the W3C
[modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).
Inspection found `role="dialog"` and push/pop-focus calls but did not establish an existing
Tab focus trap; do not treat the external proposal's preservation claim as verification.
The implementation uses the framework focus wrapper for the Task dialog and departure confirmation.
Forward and reverse Tab remain usefully contained. Reverse Tab may reach the existing Activity
region before its descendants; an exact final-button target is not required.

Opening the overlay moves focus to its hide control. Hiding it while focus is inside restores
focus to the labeled hierarchy trigger; hidden/covered editor controls must not remain tabbable.
The dialog header remains available. Escape in the open narrow overlay hides that panel first
and consumes the event; a later Escape follows existing picker/Move/dialog dismissal handling
and draft safeguards. Panel selection retains openness. These are panel focus rules, not a new
tree keyboard model. Verify focus after LiveView navigation and breakpoint changes.
Overlay inertness applies only while a real hierarchy panel is present and recovery is inactive;
retained recovery inputs must remain focusable and selectable without losing the workspace preference.

## Observable scenarios

| Situation | Expected outcome |
|---|---|
| Select a cousin Task outside the visible List table | Editor/URL/current highlight update; backdrop and manual expansion survive |
| Click an optional branch's chevron | Children toggle; editor, URL and comment draft do not change |
| Selected Task is at level four on a 320 px viewport | Wrapping title and status readable; Close and panel controls reachable |
| Open a much deeper branch | All Tasks remain reachable through contained overflow; no new depth cap |
| Close or select another Task with an unsent comment | Existing departure confirmation runs; Go back preserves draft and current Task |
| Submit comment and close fails | Draft and error remain available; no silent dismissal |
| Reparent succeeds or another user updates title/status | Branches, labels, selected path and connectors reflect the refreshed hierarchy |
| Target Task disappears before navigation completes | Existing recoverable not-found handling; no stale editable Task under new identity |

## File ownership and implementation boundaries

| Owner | Responsibility |
|---|---|
| `lib/taskman_web/components/tasks/detail.ex` | Header composition, nested rows, metadata, accessible navigation and decorative markers |
| `lib/taskman_web/components/core_components.ex` | Reusable modal header/Close composition and modal focus behavior without breaking callers |
| `assets/css/app.css` | Responsive columns, wrapping, branch geometry, scroll containment, focus/selection styles |
| `lib/taskman_web/live/project_live.html.heex` | Modal integration, existing departure path and workspace hook attachment |
| `assets/js/task_detail_layout.js` | Workspace-lifetime hierarchy preference, overlay/focus behavior and selected-row visibility |
| `assets/js/task_detail_hooks.js` | Registers the layout hook with the existing Task detail hooks |
| `assets/js/project_live_hooks.js` | Existing draft interception and discussion scrolling; inspect breakpoint coupling before changing |
| `lib/taskman_web/live/project_live/tasks/hierarchy.ex` | Preserve expansion state rules; no presentation geometry |
| `lib/taskman_web/components/tasks/status.ex` | Shared canonical status labels and colors; table and hierarchy delegate here |
| `lib/taskman/tasks/hierarchy.ex` | Existing connected-tree projection; no per-row fetches or domain changes expected |

Wide sizing uses the existing normal-dialog selector
`#task-modal-content:has(> #task-dialog-header)`. Recovery retains its 1280 px cap; creation,
not-found and other dialogs keep their existing sizes (creation/not-found are 512 px).
Scope width allocation to `assets/css/app.css`; the normal header attachment and panel/card
structure remain in the owners above. Do not change the shared modal API, hook preference,
height, dialog naming, Share/Move, comments, recovery input or departure guards to widen it.

Prefer project-owned components and existing LiveView/CSS composition. No new dependencies,
database migrations or persistence access from the web layer are expected. Inspect focused
hierarchy, modal, comment departure and browser tests before planning edits. Do not convert
existing functional tests into CSS-string snapshots or expand verification to a full-branch review.

## Verification and acceptance evidence

The implementation retains D3's 1280/1024 CSS px boundaries and a 336 px panel through 1440 px.
The [wide sizing contract](#wide-sizing-grows-from-the-existing-layout) owns final allocation.
The hierarchy measurements below establish panel/title space and responsive composition;
the final editor/discussion allocations are recorded in [rendered sizing evidence](#rendered-sizing-evidence). Chromium
rendered measurements established the available space below the dedicated header:

| CSS viewport | Dialog width | Panel width | Level-four title width | Discussion |
|---|---:|---:|---:|---|
| 1440 × 900 | 1280 px | 336 px | 205 px | Beside editor |
| 1280 × 900 | 1232 px | 336 px | 205 px | Beside editor |
| 1279 × 900 | 1231 px | 336 px | 205 px | Below editor |
| 1024 × 768 | 976 px | 336 px | 205 px | Below editor |
| 1023 × 768 | 975 px | 336 px overlay | 231 px | Below editor |
| 390 × 844 | 358 px | 336 px overlay | 231 px | Below editor |
| 320 × 720 | 288 px | 286 px overlay | 181 px | Below editor |

Four levels with 120-character wrapped titles and a 64-character unbroken title fit without
hierarchy horizontal overflow or page overflow. Narrow padding and indentation are reduced while
preserving distinct depth. At 320 px, five and six levels retain 180 px title space through
contained overflow. The Parent Task selector and native date field allow their grid items to
shrink; the existing location/Move row wraps on phone widths so both controls remain reachable.
These are sizing refinements within D2–D4, not new layout modes or hidden controls.

Browser checks exercise header/panel scroll bounds, control reachability, focus/inertness across
1024 px, selection and reparenting visibility, reading-position retention, draft departures,
discussion access and Share fallback. Reduced-motion emulation resolves panel/link transitions to
zero. Chromium's accessibility tree exposes native lists, list items, Task links with status and
named disclosure buttons. This is browser semantic evidence, not a screen-reader session.

Actual 200% browser zoom remains unverified: WebDriver shortcut and profile-preference probes
left the viewport and device-pixel ratio unchanged, while headed Chromium could not resize under
the available desktop window manager. No CSS scaling is counted as zoom acceptance. Other browser
engines, physical devices and assistive-technology interpretation remain untested. Independent
scoped review is complete. The operator accepted completion with these limits recorded.

The native Status/Priority popup presentation is superseded by the
[field picker contract](2026-10-28-form-controls-design.md#select-picker): matching Parent Task menus
preserve the existing form/lifecycle behavior. The Due popup follows the separate
[staged date/time contract](2026-10-28-form-controls-design.md#date-time-picker), including its explicit day-first
text format. Due text and the remaining native fallback controls retain
explicit dark backgrounds, light text, borders and focus treatment to avoid
pale text on inherited white controls. The original Chromium inspection at 1440 and 320 px
verified background/text matching; it was not a full application contrast audit.

Acceptance requires the following evidence and remaining checks:

1. Focused component/LiveView coverage of ID/status rendering, native list/disclosure semantics,
   current Task, required-open constraints, optional toggles, retained expansion and backdrop,
   not-found handling, and safe Close/task-selection departures. Exercise existing autosave,
   parent picker, comment draft/tab and shared modal cases affected by composition changes.
2. Browser checks at 1440 × 900, 1280 and 1279 px widths, 1024 × 768, 1023 px width,
   390 × 844, 320 × 720, short-height layouts and 200% zoom. Record actual viewport, dialog,
   panel/content widths and page overflow; verify controls and all form fields remain reachable.
3. Visual inspection against frames 01–03: all six statuses, single root, four levels, broad
   siblings, final-child elbows and ancestor rails, 120-character titles and 64-character
   unbroken strings. Probe five levels; use a deeper smoke case only to confirm access and
   containment, without imposing eight-level visual polish.
4. Keyboard and accessibility review of dialog Tab containment, overlay focus entry/return,
   Escape precedence, selection versus focus, hidden controls, disclosure names/states and
   status text; verify native-list interpretation with assistive technology and state any
   untested engine/device combinations.
5. Focused tests, asset build and required `mix precommit`; implementation-facing terminology
   and privacy checks; independent scoped verification. The final implementation check passed
   1,352 tests with 3 existing skips and 42 browser tests excluded. Browser checks ran separately:
   19 affected tests, followed by 5 focused browser and 37 recovery tests after the recovery correction.

## Rendered sizing evidence

Chromium inspection used the real application with a normal 16 px root font and actual CSS
viewport widths. The baseline at 2560 px measured a 1280 px dialog with 336/654/288 px
hierarchy/editor/discussion columns. With the 800 px editor cap, 2100 px dialog cap and
320 px inner discussion floor, the layout measured:

| CSS viewport width | Dialog | Expanded hierarchy | Editor | Discussion |
|---|---:|---:|---:|---:|
| 1440 | 1280 | 336 | 573 | 369 |
| 1441 | 1280.89 | 336.09 | 573.80 | 369 |
| 1920 | 1712 | 384 | 800 | 526 |
| 2560 | 2100 | 448 | 800 | 850 |
| 3840 | 2100 | 448 | 800 | 850 |

With hierarchy collapsed at 2560 and 3840 px, the rail is 48 px, editor 800 px and discussion
1250 px. At 1280, 1440 and 1441 px, the collapsed editor is also capped at 800 px, with
discussion measuring 382, 430 and 430.89 px respectively. Maximum form content is 744 px.
Editor padding remains 28 px; discussion padding remains 24 px. Comment-card insets
are equal at 17 px including the existing 1 px border, and paragraphs fill card content width.
At 2560 px, recovery retains its 1280 px cap and accessible retained input; creation and
not-found retain their 512 px caps.

These measurements establish CSS allocation, not actual browser zoom, other browser engines,
physical-device or assistive-technology acceptance.

## Sizing verification

The final 800 px editor cap, 2100 px dialog cap and 320 px inner discussion floor pass the
temporary rendered matrix (4 checks) and all 5 existing Task detail browser tests after
rebuilding assets/digests. A direct screenshot inspection also checked long unbroken comments and reachable controls. The temporary styling harness is
outside the durable test suite. The final `mix precommit` gate reports 1,352 passed,
3 skipped and 42 browser exclusions. The first run failed the recovery assertion in
`test/taskman_web/live/global_task_search_test.exs:364`; that server-only test passed unchanged
with the original seed, and the full gate passed on retry. The intermittent cause remains
unconfirmed; no recovery code or test was changed.

The original layout passed 51 focused Project LiveView tests and an independent browser matrix
reproduction. Those checks preceded the sizing corrections; final independent source/log/screenshot
re-review approves the sizing corrections with no findings. The operator accepted the delivered result.

## Completion and acceptance limits

Implementation is complete. This specification preserves its lasting decisions and verification
limits; the temporary implementation plan has been retired.
The operator acknowledged the focus, test-ownership and narrow-wrapping decisions, and replaced
the native-control deferral with the matching-background correction described above. The completed
handoffs and implementation plans are retired. Independent acceptance was scoped to the implementation; no full-branch review
is implied. Browser semantic evidence does not establish assistive-technology or actual zoom
acceptance. Merge and deployment remain separate operator decisions.

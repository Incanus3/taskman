# Browser test ownership review

Status: Conservative reductions implemented and operator accepted.

Reviewed: 2026-11-08, branch `task-activity-history`, source revision `cd123ccc`.
Scope: all 15 `*browser_test.exs` modules and relevant ordinary tests and hooks.
The review findings below refer to that source revision. Conservative reductions
and the ordinary tooltip correction are implemented on this branch.
Independent scoped review approved both spec compliance and test quality after
that correction; no findings remain.
Product behavior and browser-mode configuration remain unchanged. Runtime savings
were not measured.

## Current outcome

- Browser cases reduced from 88 to 85: retired the visual table-edge case and
  removed one repeated comment-snapshot case and one Task-link departure permutation.
- Ordinary tests now cover missing relationship searches, native status vocabulary,
  tooltip wiring and date-form draft behavior before their browser counterparts
  were trimmed. Most other reductions shorten existing browser scenarios.
- The combined Back/capture/comment regression, real delayed-WebSocket test and
  location/same-Task interception flow remain. Conditional migrations in section 6
  are retained opportunities, not unfinished implementation commitments.
- The existing hierarchy scroll fixture now explicitly confirms its external Done
  update through `done_confirmation: :force`, preserving its intended patch and
  all navigation/scroll assertions. This accepted fixture decision assumes Done
  was already confirmed; dedicated tests own rejection and confirmation policy.
- The operator-approved CLI activity test change inlines a single-use fixture helper.
- The operator-approved consolidation-test change only wraps existing assertions.

## Headless execution

Twelve browser modules use Wallaby's default or explicit headless mode. Three
modules explicitly require a headed browser for desktop hover: additional states,
Task expansion and count badges. A plain-headless, `DISPLAY`-unset probe of the
existing Task expansion case failed its unchanged `(hover: hover)` assertion.
No flags, input emulation or assertion weakening were added; the other two suites
were not redundantly probed under the same unavailable capability.

The editor/capture tests pass headlessly with `DISPLAY` unset. A mixed-mode run
reproduced an unchanged editor undo failure with a desktop display in the environment;
the focused rerun with only `DISPLAY` unset passed. The
[development guide](../guides/development.md#verification-expectations) now explains
that separation and the virtual-display option for headed tests.

## Conclusion

Most existing browser cases exercise actual browser behavior. There is one strong
whole-test retirement candidate and several opportunities to shorten retained cases.
Do not replace browser history, focus, popover hit-testing, scrolling, clipboard,
Leaf selection/undo, or native resizing assertions with simulated LiveView events.

The [development guide](../guides/development.md#verification-expectations) owns
the policy: server behavior belongs in ordinary tests; browser cases must prove
behavior that lower layers cannot, and styling-only assertions should be avoided.

## Recommended reductions

### 1. Retire the table-edge visual regression case

`test/taskman_web/live/project_live/task_expansion_browser_test.exs:86`, with helper
`assert_table_edges/3` at line 305, checks unused scrollbar gutter, bottom-border
width/color, and lack of vertical overflow for a short table at four widths.
These are visual presentation checks, without an inaccessible-control assertion.
Retire this case in favor of direct visual acceptance. Do not reproduce border or
gutter checks in LiveView tests. Retain the other expansion cases proving independent
disclosure activation and pointer/keyboard family highlighting.

### 2. Remove the icon-rendering inventory from the table scenario

`task_table_browser_test.exs:642-663` opens Project editing to inspect eight icon
mask images and checks List/folder masks. This is styling inventory inside a test
nominally about filters, sharing, and Project memory. Remove those checks and their
editor navigation. Keep the selector dismissal and keyboard disclosure/focus checks.
This shortens a case; it does not remove a browser case.

### 3. Reduce capture matrices while retaining native integration boundaries

`description_capture_browser_test.exs:96` repeats the same unsent-comment snapshot
path for blank and nonempty initial comments. Keep the nonempty-to-newer snapshot
browser case, including exact whitespace retention. Ordinary
`description_capture_test.exs:633,714` already varies initial comments and verifies
retry/current snapshot handling. Proposed reduction: two browser cases to one.

`description_editor_browser_test.exs:87` expands link/Back departure and comment
presence into four cases. Consider removing only the no-comment Task-link variant:
the link-plus-comment case retains link interception, while Back without a comment
retains final-input capture and Forward restoration. Keep Back with a comment:
it combines native history, unsent description capture, and the departure guard.
Ordinary `description_capture_test.exs` and `comment_departure_test.exs` cover server
state combinations but cannot establish that combined native behavior. Proposed
four-to-three reduction still trades away one integration combination; it is not
proof that all combinations are equivalent.

### 4. Trim repeated server-result matrices from retained interaction tests

- `related_tasks_browser_test.exs:75-88` repeats empty/matching search results
  before and after Enter across two widths and two relationship directions.
  `related_tasks_test.exs:80` covers search; add an explicit no-match assertion
  there before removing browser empty-result assertions. Retain representative search
  patches to prove focus/popover anchoring survives them, and Enter does not reload
  the document; remove repeated result checks unrelated to those browser contracts.
- `additional_states_browser_test.exs:80-84,146-148` includes state vocabulary and
  no-write outcomes. `additional_states_test.exs:449-495` covers no-write outcomes
  and selected option metadata, not the complete native vocabulary. Add the full
  vocabulary assertion to an ordinary rendered-form/component test before removing
  it from the browser case. Keep native reveal focus, keyboard operation, popup
  state and event isolation.
- `task_detail_browser_test.exs:95` can leave the valid/invalid/leap-date matrix
  to `components/date_time_input_test.exs` and ordinary Task form tests. Keep an
  invalid focused draft surviving a patch and representative text/calendar bridge
  behavior. Do not drop popup event isolation or preserved seconds on unchanged
  Apply: those exercise the JavaScript calendar adapter.

### 5. Shorten generic tooltip and Share repetition carefully

`task_table_browser_test.exs:489` loops over five tooltip labels after already
proving delayed pointer display, focus display, dismissal and viewport bounds.
Move static label wiring assertions to ordinary rendered-component/LiveView tests
before removing the loop. Keep representative native pointer/focus behavior.

`task_table_browser_test.exs:699,840` repeats Share route/filter snapshots and
clipboard feedback. `task_table_test.exs:15,88,160` already owns rendered Share
attributes, empty statuses and duplicate-filter handling. Trim repeated server
assertions and repeated toast-expiry waits; retain clipboard success/failure,
fallback focus/reachability, Task-target override, query/fragment preservation,
local storage and Back/Forward/reload behavior. The hooks independently implement
URL parsing and URL construction, so Elixir tests alone do not cover those paths.

### 6. Conditional whole-case migrations need replacement coverage first

`description_editor_browser_test.exs:142` primarily checks rejection of delayed
editor changes after reopening or starting a new draft. Ordinary
`description_editor_test.exs:84,132,146` covers stale editor identity, but some
tests inject internal messages directly. Add an edit-to-new-creation case through
the public `content_changed` component boundary before considering retirement.
Removing the browser case also gives up its real delayed-WebSocket-frame proof;
retain it if transport-level identity preservation is the intended regression.

`comment_departure_browser_test.exs:60` combines location-link, same-Task-link and
Back protection. Stronger native cancellation cases already exist at lines 201
and 242, and ordinary same-Task state coverage exists in
`comment_departure_test.exs:49`. Consolidate only after retaining actual link
interception and same-Task exemption in another browser flow. Ordinary state
tests do not execute those JavaScript decisions.

### 7. Trim failed Retry setup from successful Retry cases

`live/global_task_search_browser_test.exs:398-421` repeats a failed keyboard Retry,
Escape and reopening inside successful Retry cases. Ordinary
`global_task_search_test.exs:156-198` owns failed-retry state; dedicated browser
cases at line 324 own Escape from Retry. Retain successful keyboard and pointer
activation on both surfaces, including focus/results visibility. If failed Retry
focus restoration is required, keep a focused browser proof rather than duplicating
the longer sequence.

## Coverage that should remain in browsers

- Global search: real focus/blur, keyboard result visibility, Retry interaction,
  modal inertness, mobile dismissal and native click/keyboard event ordering.
- Shared form pickers: generated option IDs, active-descendant resolution, focus,
  native popovers and isolation between instances. Static markup alone is insufficient.
- Counts and preferences: local storage hydration, malformed/empty/saved data and
  denied storage. Detailed count arithmetic belongs to ordinary tests, but those
  tests do not execute the JavaScript storage reader.
- Task detail/table/relationships: reachable controls, clipping, scroll position,
  responsive composition, focus restoration and layered Escape handling.
- Comments/departure: Ctrl+Enter versus newline, scroll follow/preservation, native
  history cancellation, reload warning and focus trapping/restoration.
- Description editor/capture/replacement/resize/block menu: actual Leaf conversion,
  selections, caret, undo history, paste, frozen final input, native resizing and
  menu clipping/backdrop targeting. Ordinary hook-message tests cannot replace these.

No standalone JavaScript test harness currently exists in `assets/`. Pure URL or
editor conversion logic could be tested outside Chromium if separately extracted,
but that requires additional implementation and is not an immediate LiveView migration.

## Evidence and limits

Replacement ordinary coverage passed 93 tests across the four changed LiveView
modules before browser assertions were removed. Final `mix precommit` passed:
1,664 tests passed, three existing executable smoke tests skipped, 85 browser
cases excluded, and no Credo findings. The final tooltip correction also passed
all 20 Task table tests and another complete precommit gate with those same counts.

All 59 cases in the eight affected browser modules passed in their intended
modes: global Task search; ProjectLive additional states, description capture,
description editor, related Tasks, Task detail, Task expansion and Task table.
Editor/capture ran with `env -u DISPLAY mix test.browser`; the remaining modules
used `mix test.browser` with a working desktop display. Runs were sequential.

The initial mixed run had two failures: the confirmed editor environment mismatch
and the stale ancestor Done fixture. Corrected proof sets passed all 59 affected
cases. The plain-headless hover probe intentionally established a mode limitation;
it is not a passing hover-suite claim. Unchanged browser modules were not rerun,
and runtime savings remain unmeasured.

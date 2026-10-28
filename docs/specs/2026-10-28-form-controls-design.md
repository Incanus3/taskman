# Shared date/time and select form controls

**Status:** Implemented, independently reviewed and accepted.
**Date:** 2026-10-28

Reusable date/time formatting and pickers live outside Tasks. Task creation and editing supply
Due, Status and Priority details explicitly. Selects match Parent Task's trigger and dark menu;
date/time fields use day-first text and a staged calendar/time popup. Existing form submission,
autosave, conflicts, recovery, focus and local-value semantics remain authoritative.

This specification consolidates the shared-controls, Due-picker and field-picker contracts.
Decision IDs retain their original meaning within each named group below. The
[MVP](../product/mvp-spec.md), [editing lifecycle](2026-07-31-task-editing-lifecycle-design.md)
and [Task detail layout](2026-10-28-task-detail-layout-design.md) own domain behavior, persistence
and dialog composition. No domain migration or new API/CLI operation is introduced.

## Accepted decisions

### Shared ownership decisions

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Extract generic controls, parameterize Task details, move out of Tasks | Requires updating HEEx, hooks, CSS and tests together | [Interfaces](#shared-interfaces) |
| D2 | Accepted | Keep separate shared modules and small explicit attributes | Callers supply their own labels, context and field key | [Interfaces](#shared-interfaces) |

Keep three small modules with explicit caller parameters instead of a configurable widget
framework or a second set of controls. A namespace-only move would leave Due IDs, messages and
parameter keys coupled to Tasks. Caller configuration owns labels, field keys, recovery policy
and the context whose navigation should discard a popup draft.

### Select decisions

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Match Parent Task's trigger chevron and dark option list for Status and Priority | These short lists do not need Parent Task's search or location metadata | [Presentation](#presentation-matches-parent-task) |
| D2 | Accepted | Enhance the existing native selects with one shared custom picker for create/edit forms | Client code must maintain keyboard, focus and server-value synchronization | [Integration](#existing-form-controls-remain-the-value-bridge) |

Changing only the chevron leaves platform-dependent option lists. The shared enhancement matches
both requested surfaces while retaining native fields; separate creation/edit controls would
duplicate behavior in an already shared form. Parent Task's search and navigation are unchanged.

### Date/time decisions

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Use Vanilla Calendar Pro if free and reasonably small | One additional bundled JavaScript library | [Dependency](#dependency-is-free-pinned-and-locally-bundled) |
| D2 | Accepted | Apply date and time together; Cancel leaves the form value unchanged | Committing a popup edit requires Apply | [Commit](#popup-edits-remain-private-until-apply) |
| D5 | Accepted | Use 24-hour minute controls; an empty value starts with no date and visible 00:00 | A different intended time must be selected before Apply | [Values](#date-and-time-remain-local-values) |
| D6 | Accepted | An incoming due-value change closes the popup without saving, with a notice | Staged popup changes must be selected again | [Synchronization](#server-updates-cannot-silently-overwrite-or-submit-popup-drafts) |
| D7 | Accepted | Use plain text typing outside the library's safe 1000–9999 calendar range | Unsupported years have no calendar popup | [Bounds](#dependency-is-free-pinned-and-locally-bundled) |
| D8 | Accepted | Display and type `DD.MM.YYYY HH:mm`, with 24-hour time | Fixed day-first display rather than browser locale | [Values](#date-and-time-remain-local-values) |
| D9 | Accepted | One visible text field, normalized at web-form boundaries | Adds explicit UI parsing; native segmented editing and OS picker fallback are replaced | [Integration](#the-day-first-field-remains-the-form-and-typing-interface) |
| D10 | Accepted | Manage the published Calendar package through npm with an exact pin and lockfile | Asset builds require Node/npm and a package-install step | [Dependency](#dependency-is-free-pinned-and-locally-bundled) |
| D3 | Superseded | Vendor published Calendar ESM directly | Manual retrieval/update maintenance; replaced by D10 | [Dependency](#dependency-is-free-pinned-and-locally-bundled) |
| D4 | Superseded | Visible datetime-local input with native fallback | Browser controls the display order, which cannot satisfy D8 consistently | [Replacement D9](#the-day-first-field-remains-the-form-and-typing-interface) |

All current choices and implementation rulings are accepted. Superseded choices preserve their
rationale and replacement links. Publication, merge and deployment require separate authorization.

## Shared interfaces

- `TaskmanWeb.DateTimeInput` lives at `lib/taskman_web/components/date_time_input.ex`.
  Expose `canonical/1` and `format/1`, plus
  `normalize_params(params, field_key)`; no Task-specific default key. It updates only a supplied
  key when present, preserves unrelated fields and retains malformed nonblank values. The three
  Task web entry points pass `"due_at"` explicitly. Formatting remains project-wide
  `DD.MM.YYYY HH:mm`, with the existing optional seconds/blank/ISO compatibility semantics.
- `TaskmanWeb.DateTimePicker.date_time_picker/1` lives at
  `lib/taskman_web/components/date_time_picker.ex`. Accept `field`, explicit `id`, `label`,
  optional `trigger_label`, optional `changed_message`, optional `context_id`, and `readonly`.
  Defaults for optional text are generic date/time wording. Child IDs and ARIA references derive
  from `id`; no global Task selector. `label` also names the popup. The Task form passes its
  existing ID/labels/changed-message and recovery as `readonly`, retaining current user text.
- `TaskmanWeb.SelectPicker.select_picker/1` lives at
  `lib/taskman_web/components/select_picker.ex`. Preserve `field`, `id`, `label`, `options` and
  accept `disabled` and optional `context_id`. The Task form passes recovery as `disabled` and its
  current record identity explicitly. Do not infer Task records inside either shared component.
- Hooks live in `assets/js/date_time_picker.js` and `assets/js/select_picker.js`, named
  `DateTimePicker` and `SelectPicker`. Register in `assets/js/app.js`, independently of
  `task_detail_hooks.js`. Get the native/text field from `data-input-id` and controls from the
  current hook root using generic data roles or derived IDs. Server-patched `data-context-id`
  replaces implicit record identity; the caller chooses its semantics. Read the changed notice
  from caller data. Generic default wording contains neither Task nor Due.
- Use the mutually exclusive popup event `form-picker-open` in both hooks. Opening one
  still dismisses any other select/date-time popup without saving. Use picker-owned CSS
  classes named generic `select-picker` / `date-time-picker` names, keeping rules and palette intact.

No configurable formats, calendar engines, theme system, shared popup superclass, new library,
new production route, domain change or persistence migration is needed. The pinned npm package,
MIT attribution and app.js/app.css pipeline follow the dependency contract below.

## Multiple instances and lifecycle

Two date-time controls with unrelated IDs, fields and labels must operate independently. Each
Apply writes only its own field and emits one input; Cancel writes nothing. Two select controls
must preserve independent values and one-change selection. ARIA references must resolve uniquely.
Cross-control dismissal remains intentional; ownership, draft values, notices and focus stay local.
Option values containing whitespace must not introduce whitespace into generated ARIA IDs; encode
unsafe suffixes while preserving existing Task enum option IDs.

Preserve read-only/disabled rendering, invalid-text/low-year fallback, no-JavaScript presentation,
focused manual input across patches, explicit context-change dismissal, incoming-value notices,
Escape/Enter behavior, outside focus, positioning, disconnect/reconnect and teardown. The Task
caller keeps current IDs, so application tests continue exercising its original form contract.

## Select picker

The Task form supplies IDs `task-status` and `task-priority`, field-derived names, and canonical
option order from `Task.statuses/0` and `Task.priorities/0`: statuses `icebox`, `pending`,
`in_progress`, `in_review`, `done`, `will_not_do`; priorities `none`, `low`, `medium`, `high`,
`urgent`. Recovery disables the controls and can retain an unknown captured value.

### Presentation matches Parent Task

Use Parent Task's existing trigger dimensions, border/background, text, hover and focus treatment,
and its `hero-chevron-down` icon at `size-4` with slate-400 color. The popup uses the same rounded-xl
slate-700 border, slate-950 background, 4 px padding, 8 px gap and dark shadow, with a 240 px
scrolling maximum. Rows use rounded-lg corners, 12/8 px horizontal/vertical padding, 14 px text,
slate hover, indigo selection and a `hero-check` marker.

Keep the current labels, option order and values. Do not add search, metadata, status editing
elsewhere or new colors/badges. Location behavior remains unchanged; the date/time contract is specified below. Parent Task's
search/navigation behavior is unchanged.

### Existing form controls remain the value bridge

Keep the actual native selects and their IDs/names under LiveView ownership. A mounted enhancement
hides them from display and accessibility, then exposes a named custom trigger/listbox. Without
the enhancement, the native control remains usable. Do not expose duplicate accessible controls.
Hook-managed custom markup uses a separate stable ID and `phx-update="ignore"`; native fields and
lifecycle messages remain outside that ignored subtree.

Choosing a different option updates the real select and dispatches the existing form change path
once. Choosing the current value is idempotent. Existing server validation, autosave, errors, conflict
resolution, Done/blocker confirmation and cancellation remain authoritative. Server updates must
resynchronize displayed labels and selected options, including updates to another Task. Recovery
keeps disabled controls and captured values; no stale editable popup survives it.

No new domain operation, API/CLI contract, dependency, persistence or server event protocol is needed.

### Keyboard, dismissal and placement

Expose the visible field label, expanded state, associated listbox and selected/active options.
Enter/Space or Arrow keys open the picker, initially activating the selected value. ArrowUp/Down,
Home/End and label typeahead navigate; Enter/Space commits. Navigation alone does not save.
Escape closes only the picker and returns focus to its trigger. Tab closes and continues ordinary
focus navigation. Pointer selection closes and returns focus; an outside click closes without
stealing focus from the clicked target. At most one Status/Priority list is open.

Menus must remain usable near the editor's lower edge and at a 320 px viewport. Fit the popup
above/below its trigger inside the viewport and avoid clipping to the editor scroll container.
Close/clean listeners and popups on component removal, Task navigation, recovery and disconnection.
Do not introduce another modal or interfere with hierarchy Escape, draft departure or dialog focus.

## Date-time picker

Cally is a smaller calendar building block but requires separate time controls. Vanilla supplies
time selection and daisyUI styling, reducing behavior owned by Taskman. Keeping the native popup
would retain the platform-dependent appearance that prompted this control.

### Dependency is free, pinned and locally bundled

Use Vanilla Calendar Pro **3.4.0**, MIT licensed, with no runtime dependencies. Manage the exact
published package through `assets/package.json` and `assets/package-lock.json`; import Calendar
and its time extension through a small project-owned legal wrapper. The lockfile records the
public npm tarball and integrity. Do not copy upstream ESM into version control.

[Phoenix's supported npm setup](https://phoenix.hexdocs.pm/asset_management.html#third-party-js-packages)
keeps the existing esbuild wrapper: package resolution works from `assets/node_modules`, and
`npm ci` belongs in asset build/deploy aliases. `mix setup` reaches installation through
`assets.build`; `assets.setup` continues installing the standalone build tools. The Alpine CI job
installs Node/npm and builds assets. The release builder already includes Node/npm and invokes
`mix assets.deploy`. Retain Heroicons/daisyUI as Mix-managed Git dependencies; their repositories
ship directly consumable SVG/plugin bundles. This is not a bundler or toolchain-version migration.

Authoritative package identity:

- [Published metadata](https://registry.npmjs.org/vanilla-calendar-pro/3.4.0)
- [Published tarball](https://registry.npmjs.org/vanilla-calendar-pro/-/vanilla-calendar-pro-3.4.0.tgz)
- [Pinned upstream license](https://raw.githubusercontent.com/uvarov-frontend/vanilla-calendar-pro/v3.4.0/LICENSE)
- Upstream commit/npm gitHead `0947da2f3d5435f92b3de0124e081e0e0f8cccb1`.
- npm integrity `sha512-HLWjzHOWvMIrjI77tXK5bYzp0s5TeBSrZC247STjcTAdWI7kqEwWT6Xvu20vqdwzF498H1Da8l4WpdgVIGtc1Q==`.
- Published `index.mjs` SHA-256 `e85bbcc14e85bfc01298803c28aa22db05e9350203b99e76691f134c1dcb3531`;
  full MIT license SHA-256 `b95f659670e7f7af29dedc3e69c1deaeee1267757729ad391318aba94108774d`.

`assets/vendor/vanilla-calendar-pro.js` is a project-owned re-export from the npm package, with
the complete MIT legal comment retained in the shipped minified app.js. It contains no copied
library implementation. Verify its notice against the installed package license when changing
the package pin. Do not restore a copied implementation, duplicate license or manual download/update recipe. Keep app.js/app.css bundles and local runtime delivery; no CDN.

This replaces the original vendoring choice: npm now owns package retrieval, exact artifact
integrity and dependency installation. The concrete cost is Node/npm during development and
asset builds, including a CI package-install step. Existing calendar behavior, local-value
semantics, runtime dependency count and MIT obligations remain unchanged.

Local daisyUI 5.7.50 includes matching `.vc` date/time selectors. Start with those styles rather
than upstream CSS. Ensure a literal `vc` class is visible to Tailwind's existing source scan.
Scope dark theme/semantic colors and any refinements to this popup, matching the existing slate
and indigo controls without changing global theme values.

The library defaults limit dates to 1970–2470; do not inherit that as a Task restriction.
Configure its calendar view for 1000-01-01 through 9999-12-31 and jump to the selected date.
Exact 3.4.0 source inspection found unpadded-year and JavaScript Date construction defects below
1000. Keep unsupported/invalid existing values in the editable text field without a calendar popup;
do not initialize, clamp, clear or rewrite them through the custom calendar. This is a calendar
capability boundary, not a schema/API/domain restriction. Test an ordinary pre-1970 value and
an existing low-year value without changing it. Keep the published artifact unmodified.

### The day-first field remains the form and typing interface

The operator rejected both month-first and ISO-looking display and explicitly selected
`28.10.2026 17:45`. Use one visible `<.input type="text">` retaining `task-due-at`, `task[due_at]`,
errors and read-only recovery. Its placeholder and accessible format hint specify
`DD.MM.YYYY HH:mm`. Optional `:ss[.fraction]` retains nonzero seconds/fractions in presentation
and unchanged popup selections. Existing `:naive_datetime` storage precision remains seconds; this correction does not
add microsecond persistence or change domain casting. Empty or whitespace-only input means no
due date and clears the value; a nonblank malformed value is invalid, not a clear. Surrounding
whitespace may be ignored for a valid date/time. Native segmented editing and native popup
fallback are superseded; plain text editing remains available without the popup or JavaScript.

`TaskmanWeb.DateTimeInput` formats canonical local values. Creation validate/save and Editing
autosave call `normalize_params(params, "due_at")` explicitly to normalize valid day-first input
to ISO. Caller parameters follow [shared interfaces](#shared-interfaces). Domain changesets,
Autosave state, storage, API and CLI retain their existing contracts. Existing ISO form submissions
(with `T` or a space separator) remain accepted and normalize to the canonical `T` separator for
compatibility with existing consumers. Invalid or incomplete nonblank text passes through
unchanged to validation: it must never become empty, a previous valid value, or an unintended save.
Use real calendar validation, including leap days, and 24-hour bounds; no Date UTC conversion.

The [native control documentation](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/datetime-local)
establishes that browser/OS locale owns native display order. A language attribute cannot promise
the chosen format across Chromium and Gecko. A hidden canonical field plus an ignored display
field could retain an ISO browser payload but adds two-field synchronization and invalid-draft
ownership. Split day/month/year controls add focus and validation complexity. One text field is
the smallest reliable option; browser form payloads are now day-first text while server/domain
values stay canonical. No new dependency or browser-specific indicator masking is needed.

The shared `TaskmanWeb.DateTimePicker` component and `DateTimePicker` hook own the popup.
The Task form supplies its current IDs, Due labels/messages, record context and read-only policy. The
hook-managed subtree has a stable ID and `phx-update="ignore"`; the text input and server
lifecycle/error messages stay outside it. Server-patched data attributes expose canonical values
or retained invalid text. Do not overwrite focused manual edits on ordinary patches. Parse display
text when opening; Apply writes the formatted text once and dispatches one bubbling `input`
event, which existing `phx-change` handling consumes.
Incoming canonical changes retain the existing close/notice rule. There is only one calendar
trigger; unsupported/invalid values disable it while leaving text editing available. Recovery has
no editable trigger or popup. No new server event, persistent field or API/CLI operation is needed.

Instantiate the library inline in a dedicated owned child of a manual top-layer popup, rather
than its automatic inputMode/body popup. Fit it above/below the trigger within the viewport,
including 320 CSS px and the scrolling editor's lower edge. Keep Apply/Cancel reachable at short
heights. The library's normal minimum calendar width is 286 px; avoid extra padding that forces
page overflow. Other Task menus remain unchanged.

### Popup edits remain private until Apply

Opening copies the current form value into a private date/time draft. Calendar/time interactions
must not emit Task form input/change/submit events; isolate those events inside the popup.
The library's date/time callbacks update only that draft. Do not close on selecting a day.

- **Apply:** Commit one complete valid date/time, or an explicit cleared draft, through one bubbling
  `input` event on the real text field. Web-form normalization precedes existing creation validation
  or edit autosave.
  An unchanged value closes without dispatching a form event.
- **Cancel, Escape, outside dismissal or focus leaving the popup:** Discard the popup draft without
  changing the real field. Escape closes this picker before outer dialog/hierarchy handlers.
- **Clear:** Stage an empty due value; Apply performs the clear. Cancel restores the original value.
- **Invalid/incomplete draft:** Disable Apply. A new empty field starts with no selected day and
  visible 00:00; no value is assigned merely by opening. Date selection does not toggle off the
  selected date; explicit Clear owns that action.

Calendar keyboard navigation and time editing remain library-owned. Tab must reach date/time and
footer controls; Enter in a time text field must not accidentally submit the surrounding Task form.
Apply/Cancel return focus to the calendar trigger. Outside dismissal preserves the clicked target's
focus. Coordinate with the other field pickers so opening one closes the other without a save.

### Date and time remain local values

Use the library's runtime `context.selectedDates[0]` and 24-hour `context.selectedTime`, not stale
constructor options. Keep calendar draft context as `YYYY-MM-DDTHH:mm`; Apply displays `DD.MM.YYYY HH:mm` and the
web-form boundary normalizes it back to ISO. Clearing sends an empty value. Do not use
`Date.toISOString()`, append `Z`, shift timezones or derive the date through UTC conversion.

Honor `onChangeTime`'s error flag: invalid visible time must not apply a previously valid hidden
time. Keep existing input values unchanged on open/Cancel or an unchanged minute-level selection,
including any pre-existing seconds; explicit changed picker values use minute precision. The text
bridge emits one `input` event rather than a native-field `change`: LiveView can deduplicate a
synthetic `change` after a previous text `input`, while `input` matches ordinary text editing.
Never emit both events for one Apply.

### Server updates cannot silently overwrite or submit popup drafts

Keep popup edits private across patches where the due value and Task identity are unchanged.
Do not reset the library on every form patch. If an incoming authoritative due value changes while
the popup is open, close/discard the popup without dispatching a save and announce that the due
value changed and the picker must be reopened. This avoids silently applying an older private
selection over a newer value. Own Apply closes before its server round trip and must not produce
that notice. Existing form-field conflict resolution and errors remain server-owned.

Task navigation, recovery, disconnection and component destruction close/discard the popup.
Reconnect starts from the latest form value. Guard library cleanup: destroy runs once after init
and can restore its original element, so use a dedicated library-owned child inside the stable
ignored boundary. Remove listeners/observers/frames and avoid stale editable controls.

## File ownership and verification

| Owner | Responsibility |
|---|---|
| `lib/taskman_web/components/date_time_input.ex` | Shared parsing, formatting and explicit-key normalization |
| `lib/taskman_web/components/date_time_picker.ex` | Generic date/time component and caller attributes |
| `lib/taskman_web/components/select_picker.ex` | Generic select component and native value bridge |
| `assets/js/date_time_picker.js`, `assets/js/select_picker.js` | Picker hooks registered in `assets/js/app.js` |
| `assets/css/app.css` | Scoped `date-time-picker` and `select-picker` presentation |
| `assets/package.json`, `assets/package-lock.json`, `assets/vendor/vanilla-calendar-pro.js` | Pinned dependency and full-MIT re-export |
| `mix.exs`, `.github/workflows/elixir.yml` | Locked installation and asset build/deploy wiring |
| `lib/taskman_web/components/tasks/form.ex` | Task labels, fields, IDs, options, recovery and record context |
| `project_live/tasks/creation.ex`, `tasks/editing.ex` | Due normalization at create validate/save and edit autosave |

Keep focused formatter/component/form/lifecycle tests for arbitrary field keys, IDs, labels,
messages, multiple instances, independent values and unique ARIA references. Exercise creation,
autosave, Done/blocker warnings and cancellation, conflict resolution and retained recovery.
Test public behavior rather than CSS strings or deleted module names. Do not add production
fixture routes, a generic popup framework or unrelated Parent Task refactors.

Browser checks own keyboard navigation without saves, one-event commit, unchanged-value
idempotency, pointer selection, server-value synchronization, Escape/Tab/outside dismissal,
focus retention, incoming patches, context/navigation/recovery/disconnect/reconnect cleanup,
popup placement and actual control visibility. Date/time cases include invalid visible time,
manual typing and correction, local ISO values without UTC shifts, seconds retention, staged
Clear, empty/00:00 and unsupported-year text editing. Inspect all select controls at 1440 and
320 CSS px and the date popup at short heights and the editor's lower edge; measure actual
viewport and page overflow. No CSS scaling counts as browser zoom evidence.

Build/digest assets, verify emitted `.vc` styles and full shipped MIT, compare same-settings
minified/gzip size, run `mix precommit`, check links/privacy/whitespace and obtain independent
scoped review. Packaging changes also require the clean pinned release-build and artifact/cache
gates in the [development guide](../guides/development.md#verification-expectations).

## Verification evidence and limits

Focused select/form/Project LiveView/autosave/Done/conflict/recovery checks passed 127 tests;
the day-first parser/form/editing suite passed 101; shared arbitrary-field/instance/ARIA checks
passed 91. These are scoped suites, not additive totals. The final affected Chromium batch
passed 11 tests: nine actual Task detail LiveView tests and two generic component/hook probes.
The probes use a test-only static Plug with manually delivered hook lifecycle callbacks;
Task tests establish real LiveView patching and persistence. Where probe geometry covers a
physical trigger, cross-popup coordination uses a direct DOM click; Apply/Cancel/select still
use browser interactions. Native select fallback is established by markup/CSS inspection,
not a separate JavaScript-disabled browser session.

The selected last Status option is fully visible on open. Direct Parent Task/Status/Priority
screenshots at 1440 and 320 CSS px show matching controls without page overflow. A separate
headed Chromium probe asserted `(hover: hover)` and verified the selected row keeps its indigo
surface when hovered. Regular interaction and screenshot evidence is headless Chromium.
Server tests establish dedicated conflict resolution; broadcast and Done-cancel browser checks
establish the shared displayed-value synchronization path.

Date/time Apply tests use a real text `input` event and await the exact canonical saved value.
A synthetic `change` alone can be deduplicated after a previous text input by LiveView;
that simulation does not establish real-user Apply data loss. One changed Apply emits one input;
unchanged Apply, including existing seconds, and Cancel emit none.

Vanilla Calendar Pro 3.4.0 now resolves from the exact npm lockfile through the existing esbuild
pipeline. The installed ESM and license match the former vendored artifacts byte for byte.
The complete 1,068-byte MIT notice remains in minified, digested and compressed JavaScript.
Matching daisyUI calendar/time styles are emitted.

The minified app measures 253,268 bytes / 75,930 gzip bytes against the same-settings
185,413 / 54,996 baseline: **67,855 raw / 20,934 gzip bytes added**, including the adapter and
legal notice. This is the JavaScript-only delta, excluding CSS. Relative to the extracted-controls
baseline, npm resolution adds 71 raw / 38 gzip bytes solely through legal-comment placement;
executable bytes are identical after removing legal comments. Fresh installation, build/deploy
and digest checks pass. Independent scoped review found no issues.

Clean-source release verification at `f06fd2fd41dc867ded379633234d2dcdedef7cc5` passes with
Elixir 1.20.4 / OTP 29.0.6 / Node 22.22.1 and the pinned builder digest. Archive, manifest and
checksum validation, isolated no-network release evaluation, full MIT in all four packaged
JavaScript variants, and exact-input artifact cache reuse pass without contacting a host.
This release-build receipt does not establish actual Alpine CI execution.

Final sequential `mix precommit` passes: **1,366 passed, 3 skipped, 48 browser exclusions**.
Focused and browser checks preceded it, with no concurrent Mix builds. A prior layout-only
recovery assertion at `global_task_search_test.exs:364` passed exact-seed replay and retry
unchanged; its intermittent cause remains unconfirmed and no recovery code/test was changed.

Actual headless Chromium measurements and direct screenshots establish:

| CSS viewport | Page width | Popup left/right | Popup top/bottom | Footer |
|---|---:|---|---|---|
| 1440 × 900 | 1440 | 637 / 941 | 358 / 749 | Apply visible and hit-testable |
| 320 × 720 | 320 | 8 / 312 | 270 / 661 | Apply visible and hit-testable |
| 320 × 400 | 320 | 8 / 312 | 8 / 341 | Scrollable calendar/time area; footer remains reachable |

The full displayed `08.07.2032 11:22` and single calendar trigger remain readable at both widths.
Static snapshots of the actual application DOM were also rendered in headless Zen/Gecko at
1440 and 320 CSS px. The 320 px page has no horizontal overflow; the 178 px text field displays
the complete date/time with one trigger. This verifies Gecko appearance, not live hook or
persistence interactions. Live cross-engine interactions, physical devices, actual browser zoom,
headed pointer and assistive-technology acceptance remain unverified.

Independent scoped review approved source contracts, final gate receipts, bundle identity/notice
and browser/visual evidence with no open findings. Registry signatures/attestations were not
independently cryptographically verified. The 1000–9999 calendar boundary retains plain text
editing outside the library's safe range; no domain restriction or value clamping is introduced.
No full-branch review or publication is implied.

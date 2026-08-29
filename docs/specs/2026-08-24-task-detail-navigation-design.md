# Task detail and navigation design

**Status:** Approved  
**Date:** 2026-08-24

## Goal

Extend Taskman's existing URL-backed Task editing modal into the MVP Task-detail surface without
losing the selected Project's list context. The surface adds the structural parent-child hierarchy
sidebar and the Activity and Sessions rail required by the product specification while keeping
their initial states truthful: Task hierarchy, activity history, and Agent Session persistence do
not exist yet.

This increment establishes layout, responsive behavior, accessibility, and the smallest useful
browser interaction boundary. It preserves the existing Task URL, editing, autosave, lifecycle,
recovery, and context boundaries.

## Authoritative references

- [MVP product specification](../product/mvp-spec.md#4-work-organization-and-navigation)
- [MVP roadmap](../planning/roadmap.md#2-task-detail-and-navigation)
- [Existing Task editing design](2026-07-31-task-editing-lifecycle-design.md)
- [Navigation and MVP visual guidance](../prototypes/navigation.html)
- [Development guide](../development.md)

## Current baseline

The application currently provides:

- a list-first Project workspace with a Project sidebar and direct Task table;
- the canonical `/projects/:project_id/tasks/:task_id` route;
- a modal over the preserved Project Task list for both row navigation and direct URLs;
- immediate editing of title, description, status, priority, and optional local due date-time;
- server-managed, field-level autosave with navigation-safe dirty-field flushing;
- modal-level recovery for missing, malformed, and cross-Project Task IDs; and
- focused LiveView coverage plus responsive browser acceptance for this existing behavior.

`TaskmanWeb.ProjectLive` owns the list, route, selected Task, form, and autosave state.
`TaskmanWeb.TaskForm` renders the existing editing controls. `TaskmanWeb.CoreComponents.modal/1`
currently uses one compact maximum width for every modal.

The navigation prototype in `docs/prototypes/navigation.html` establishes the intended broad
three-region Task-detail shape. This design refines its initial empty states and responsive behavior
for the implemented application rather than copying prototype-only relationship or Session data.

## Scope

### Included

- A wider Task-detail modal on the existing canonical Task route.
- A collapsible left parent-child hierarchy sidebar inside the modal.
- The current Task rendered as a single hierarchy node.
- A truthful no-parent/no-children hierarchy state.
- A central editable Task-detail region using the existing form and autosave behavior.
- Separate Activity and Sessions sections with truthful empty states.
- Wide, narrow, and phone layout behavior.
- A session-scoped hierarchy expansion preference.
- Narrow-screen hierarchy overlay dismissal and Escape ordering.
- Stable structural DOM IDs and accessible hierarchy controls.

### Excluded

- Parent-child relationship storage, editing, or navigation.
- Blocks, Blocked by, Relates to, or a Related Tasks table.
- Activity event storage or generated history.
- Agent Session records, launch, attachment, resume, provider selection, or disabled action
  placeholders.
- Lists, nested List navigation, checklists, Task deletion, or a docked detail layout.
- Server-stored or browser-storage-backed hierarchy preferences.
- Changes to Task lifecycle or autosave semantics.

## Product behavior

### Canonical surface and list context

The canonical Task URL remains:

```text
/projects/:project_id/tasks/:task_id
```

Opening the URL renders the selected Project's direct Task list with the Task-detail modal above it.
Closing the modal patches to `/projects/:project_id`. Direct navigation and Task-row navigation
produce the same Task-detail surface.

There is no separate view mode, edit route, or full-page Task route.

### Three-region Task detail

For a found Task, the modal contains:

1. a collapsible parent-child hierarchy sidebar;
2. the existing editable Task detail; and
3. separate Activity and Sessions sections.

All three are parts of the rendered detail surface. The hierarchy is the only collapsible region.
At wide widths, the regions appear as left sidebar, central detail, and right rail. At narrower
widths, the hierarchy remains an internal modal sidebar and Activity and Sessions move below the
editable detail.

The missing-Task modal does not render this shell. It retains the current compact not-found
presentation and recovery action.

### Hierarchy content

Because parent-child persistence is not present, every current Task has an empty hierarchy. When
expanded, the hierarchy renders:

- the selected Task as a single tree node; and
- the message `No parent or child Tasks`.

The single node identifies what the hierarchy is centered on without implying a relationship that
does not exist. No fake ancestors, children, counts, disclosure affordances, relationship actions,
or navigation links are rendered.

The component boundary accepts whether the Task has hierarchy content so later parent-child work
can supply real data without replacing the layout contract. This increment always supplies false.

### Activity and Sessions

Activity and Sessions remain distinct sections because they represent different future data and
operations.

The Activity section renders:

```text
No activity has been recorded for this Task.
```

The Sessions section renders:

```text
No Agent Sessions are associated with this Task.
```

The UI does not synthesize activity from `inserted_at`, `updated_at`, status, or autosave changes.
It does not render sample Sessions, Session counts, provider details, future-operation copy,
disabled buttons, or Launch and Attach controls.

### Hierarchy default and preference

The hierarchy presentation has two sources of state:

- a contextual default; and
- an explicit user preference for the current LiveView browser lifetime.

Before the user toggles the hierarchy:

- a Task with neither parent nor children opens collapsed; and
- a Task with a parent or at least one child opens expanded.

After any explicit expand or collapse action, that state becomes the preference for every
subsequently rendered Task, regardless of whether its hierarchy is empty or populated. Toggling an
empty hierarchy updates the same preference as toggling a populated hierarchy.

The preference survives LiveView patches, closing one Task, and opening another Task while the
workspace LiveView remains mounted. A full browser reload creates a new hook instance and restores
the contextual defaults.

The preference is not stored in the database, LiveView session, cookie, `localStorage`, or
`sessionStorage`. A later server-stored preference may initialize the same presentation boundary
without changing the Task-detail component contract.

## Responsive layout and scrolling

### Wide layout

At Tailwind's `xl` breakpoint and above, the modal uses the three-column layout:

```text
expanded hierarchy: 15rem | minmax(0, 1fr) Task detail | 18rem Activity/Sessions
collapsed hierarchy: 3rem  | minmax(0, 1fr) Task detail | 18rem Activity/Sessions
```

The opt-in wide modal has a maximum width of `80rem` (`max-w-7xl`) and remains constrained by the
viewport padding. Expanding or collapsing the hierarchy changes the first column width and pushes
or releases the central detail; it does not overlay the detail at this width.

### Narrow layout

Below the `xl` breakpoint:

- the base layout uses a `3rem` collapsed hierarchy edge and a minimum-width-zero content column;
- Activity follows Task detail in the content column;
- Sessions follows Activity;
- expanding the hierarchy opens it as a drawer over the Task detail rather than shrinking the
  editing column; and
- the drawer width is `min(20rem, calc(100% - 3rem))`, leaving an outside dismissal target even
  when the modal is narrower than `20rem`.

A localized overlay between the drawer and Task content visually separates the layers. Clicking
that overlay collapses the hierarchy without closing the Task modal.

When the narrow hierarchy drawer is open, the first Escape key press collapses it and prevents that
key event from closing the modal. A subsequent Escape closes the modal through the existing modal
behavior. When the hierarchy is not a narrow overlay, Escape retains the existing modal-close
behavior.

### Scroll ownership

The Task-detail modal is content-sized and has a maximum height bounded by the visible viewport. It
does not stretch to fill the viewport when its content is shorter than that maximum.

- The hierarchy sidebar owns an independent vertical scroll area in both wide pushed mode and
  narrow overlay mode.
- Task detail, Activity, and Sessions share one vertical scroll area.
- Activity and Sessions remain in the same document and keyboard order whether they render beside
  or below the form.
- The narrow drawer does not move with the main content scroll.

This avoids a strange coupling between an overlay drawer and the obscured form while avoiding
independent nested scroll areas for Activity and Sessions.

## Browser and server boundaries

### LiveView ownership

`TaskmanWeb.ProjectLive` continues to own:

- Project and Task lookup;
- canonical route handling;
- selected Task and not-found state;
- form draft and validation;
- dirty-field metadata;
- autosave persistence and feedback; and
- navigation-safe flushing.

No new assign, event, context API, schema, table, or query is needed for hierarchy presentation.
The server renders that the current hierarchy is empty and supplies the current Task to the detail
component.

### Client-owned presentation

A narrowly scoped colocated LiveView hook owns only hierarchy presentation. It attaches to the
stable `#taskman-workspace` element so its in-memory preference survives removal and reinsertion of
the conditional Task modal.

The hook:

1. detects insertion or update of the Task-detail layout;
2. reads whether that layout reports hierarchy content;
3. applies the contextual default when no explicit preference exists;
4. applies the remembered preference after any user toggle;
5. synchronizes the toggle label, `aria-expanded`, and layout data state;
6. handles localized outside-click dismissal in narrow overlay mode; and
7. intercepts Escape only while the narrow overlay is expanded.

CSS and Tailwind responsive rules decide whether expanded hierarchy is pushed or overlaid. The hook
does not calculate column geometry, send LiveView events, mutate Task fields, or duplicate form
state. No general client-state framework is introduced.

## Components and DOM contract

### Shared modal

`TaskmanWeb.CoreComponents.modal/1` gains an opt-in size variant:

- `:default` preserves the current compact `max-w-lg` content;
- `:wide` provides the Task-detail maximum width and a content-sized, viewport-bounded maximum
  height.

The new Task creation and Task-not-found modals keep `:default`. Only the found Task-detail modal
uses `:wide`.

### Task-detail component

A project-owned `TaskmanWeb.TaskDetail` function component composes the hierarchy, existing
`TaskForm`, Activity, Sessions, and autosave status. It receives the cohesive
`%TaskmanWeb.TaskAutosave{}` interaction state rather than separate form, state, and message
values. It does not own state or persistence.

Stable IDs include:

- `#task-detail-layout`
- `#task-hierarchy`
- `#task-hierarchy-toggle`
- `#task-detail-content`
- `#task-activity`
- `#task-sessions`
- the existing `#task-form`
- the existing `#task-save-status`

The hierarchy toggle is a native button. It has a state-specific accessible label,
`aria-expanded`, and `aria-controls="task-hierarchy"`. The hierarchy region has an accessible
heading and uses tree semantics only for the actual current-Task node. Collapsing the sidebar hides
its content without removing the labeled toggle.

The narrow overlay keeps focus on the same toggle as it changes position. Outside dismissal or
Escape returns the layout to its collapsed state without moving focus outside the modal.

### Expected file boundaries

Expected implementation files are:

- `lib/taskman_web/components/core_components.ex` for the opt-in modal size;
- `lib/taskman_web/components/task_detail.ex` for the three-region function component;
- `lib/taskman_web/live/project_live.html.heex` for composition and the stable colocated hook;
- `assets/css/app.css` only for focused stateful or responsive rules that are not clear as HEEx
  utility composition;
- `test/taskman_web/live/project_live_test.exs` for application behavior and structure;
- `test/taskman_web/live/project_live_autosave_test.exs` only for regression coverage affected by
  composition; and
- a focused component test when a low-level modal or accessibility contract cannot be exercised
  through LiveView.

`lib/taskman_web/live/project_live.ex`, Task contexts, schemas, migrations, and router behavior
should remain unchanged unless implementation discovers a concrete incompatibility with this
design.

## Error and recovery behavior

Existing Task lookup and edit recovery remains authoritative:

- A missing Project retains the current main-panel Project-not-found state and renders no Task
  modal.
- A missing, malformed, or cross-Project Task retains the requested URL, preserved Project list,
  and compact modal-level Task-not-found state.
- Invalid form drafts remain visible while unrelated valid fields can persist.
- Persistence failures retain their draft and current accessible save feedback.
- Closing or navigating flushes all valid dirty fields before modal state is cleared or replaced.

Hierarchy layout interaction never writes domain state and cannot change lifecycle status. A layout
hook failure must not remove the form or prevent Task editing; server-rendered markup starts from
the empty hierarchy's collapsed contextual state.

## Testing strategy

### LiveView and component tests

Application tests cover:

- the canonical found-Task modal rendering `#task-detail-layout`;
- the preserved Task list and existing form inside the wider modal;
- the current Task appearing as the sole hierarchy node;
- the hierarchy toggle's accessible name, `aria-controls`, and initial empty-state metadata;
- separate Activity and Sessions sections and their exact truthful empty-state messages;
- absence of Launch, Attach, disabled Session actions, fabricated activity, and fabricated Session
  rows;
- the Task-not-found modal omitting the Task-detail shell;
- unchanged row and direct-URL navigation;
- unchanged editing, autosave status, and navigation-safe persistence outcomes; and
- the shared modal's default versus opt-in wide contract when that contract is best verified at the
  component level.

LiveView tests assert observable behavior and stable DOM structure rather than responsive utility
classes, spacing, colors, or layout implementation details.

### Browser acceptance

Responsive browser verification covers:

1. Open an empty Task and confirm the hierarchy starts collapsed.
2. Expand it and confirm the current Task single-node tree and empty message.
3. Close the Task, open another Task without reloading, and confirm the explicit expanded
   preference remains.
4. Collapse it, switch Tasks again, and confirm the collapsed preference remains.
5. Reload and confirm the empty hierarchy returns to its collapsed contextual default.
6. At `xl` width or above, confirm expanded hierarchy pushes the Task detail and Activity/Sessions
   remain in the right rail.
7. Below `xl`, confirm expanded hierarchy overlays the detail, the form remains readable, and
   Activity then Sessions appear below the form.
8. Confirm localized outside click collapses the narrow drawer without closing the modal.
9. Confirm first Escape collapses the narrow drawer and second Escape closes the modal.
10. Confirm hierarchy and main content scroll independently without page or form clipping.
11. Confirm keyboard focus remains visible and the hierarchy toggle is operable by keyboard.

Verify representative desktop, tablet, and phone widths.

### Commands

During implementation, run focused tests first:

```bash
mix test test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/live/project_live_autosave_test.exs
mix assets.build
```

Run any new focused component test directly. Finish with:

```bash
mix precommit
```

PostgreSQL must be running for the test and precommit aliases. The most recent design checkpoint
could not run the full repository gate because PostgreSQL was stopped and the environment could not
start its existing Docker container due to unsupported bridge-network creation. This is an
implementation-verification prerequisite, not a design blocker.

## Acceptance criteria

- The canonical Task URL still opens a modal above the preserved selected-Project Task list.
- A found Task uses the wider three-region detail surface.
- The existing Task fields, validation, autosave, lifecycle, and recovery behavior remain
  unchanged.
- The hierarchy is an internal collapsible sidebar with its own scrolling.
- An expanded empty hierarchy shows the current Task as its only node and truthfully reports no
  parent or child Tasks.
- Empty hierarchies default collapsed and future populated hierarchies default expanded until the
  user explicitly toggles.
- Any explicit toggle becomes the preference for subsequent Tasks until reload.
- Wide expansion pushes Task detail; narrow expansion overlays it without compressing the form.
- Narrow outside click and first Escape collapse the hierarchy without closing the modal.
- Activity and Sessions have separate truthful empty states and no unavailable controls or
  fabricated data.
- Activity and Sessions render in a right rail at wide widths and below Task detail at narrower
  widths.
- Missing or invalid Tasks retain the existing compact not-found recovery behavior.
- No parent-child, activity, or Agent Session persistence or operations are introduced.
- Focused tests, asset compilation, `mix precommit`, and responsive browser acceptance pass with
  PostgreSQL available.

## Rejected alternatives and trade-offs

### Context-only collapse state

Always deriving collapse state from hierarchy content was rejected because it discards an explicit
user choice each time Task context changes.

### Remembering only populated-hierarchy toggles

Keeping a preference only for populated hierarchies was rejected because a deliberate toggle on an
empty hierarchy is still a user preference and should affect the next Task.

### Server-owned MVP preference

A LiveView assign plus browser viewport coordination was rejected for this increment because it
splits a purely presentational interaction across server and client, adds round trips, and still
requires browser logic for overlay behavior. Durable server storage remains a possible later
enhancement.

### Browser storage

`localStorage` and `sessionStorage` were rejected because the approved MVP preference ends on full
reload. Hook-instance memory matches the intended lifetime.

### CSS-only disclosure

A native disclosure or CSS-only state was rejected because it cannot reliably preserve the
explicit preference across Task patches while also providing contextual defaults and ordered
Escape handling.

### Always overlaying or always pushing

Always overlaying was rejected because wide screens can show hierarchy and detail together without
obstruction. Always pushing was rejected because it makes the form wrap heavily on narrow screens.
The accepted design pushes when the three columns fit and overlays below `xl`.

### Independent scrolling for every region

Giving hierarchy, Task detail, Activity, and Sessions separate scroll areas was rejected as
unnecessary nested-scroll complexity. Coupling hierarchy to the main scroll was also rejected
because the narrow drawer must remain stable above the obscured form. The accepted design gives
hierarchy its own scroll and keeps all other content in one scroll area.

### Disabled Session actions

Disabled Launch or Attach controls were rejected because Agent Session operations do not exist yet;
disabled controls would imply a temporarily unavailable implemented capability.

## Next-session checklist

1. Read this specification and the existing Task editing design before planning implementation.
2. Confirm PostgreSQL availability before promising the final test gate.
3. Write the bounded implementation plan in `docs/plans/`.
4. Create repository-local Beads delivery work only after the plan defines independently verifiable
   units.
5. Update the active handoff and stop at the clean implementation-session boundary after the plan
   and Beads work are approved.
